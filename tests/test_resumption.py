"""Black-box task resumption over exact uses and retained foreign history."""

import json
import sqlite3

import pytest

from test_transfer_contract import ATTR, SCOPE, Workspace, simple_transfer, tree


def resume(workspace, use, code=0, scope=SCOPE, extra=()):
    before = workspace.store.read_bytes(), tree(workspace.root)
    result = workspace.call('resume-use', use, *scope, *extra, code=code)
    assert (workspace.store.read_bytes(), tree(workspace.root)) == before
    rows = result.stdout.splitlines()
    assert len(rows) == 1
    report = json.loads(rows[0])
    assert report['operation'] == 'resume-use' and report['schema_version'] == 1
    assert report['id'] == use
    return report


def test_current_empty_report_is_deterministic_and_preserves_check_use_wire(tmp_path, run_cli):
    w = Workspace(tmp_path, run_cli, 'local')
    w.install('rule')
    w.bind('rule')
    receipt, use = w.use('rule')
    before = w.call('check-use', use).stdout
    report = resume(w, use)
    assert report == resume(w, use)
    assert report['receipt'] == receipt
    assert report['root']['identity'] == {'project': w.project, 'unit': 'rule'}
    assert report['historical_check'] == {'status': 'current', 'diagnostic': None}
    assert report['origin_analysis'] == {'complete': True, 'failures': [], 'scope': {'exercise': 'dispatch'}}
    assert report['origins'] == report['actions'] == []
    assert w.call('check-use', use).stdout == before


def test_two_origins_correction_missing_root_and_unrelated_import(tmp_path, run_cli):
    sources = []
    destination = Workspace(tmp_path, run_cli, 'destination')
    for name in ('alpha', 'beta', 'unrelated'):
        source = Workspace(tmp_path, run_cli, name)
        source.install('rule')
        source.bind('rule')
        receipt = source.read('rule')
        imported = destination.import_(source.export(receipt, name + '.json'))
        sources.append((source, receipt, imported))
    proposal, candidate = destination.proposal('conclusion')
    links = [destination.link(proposal, imported, source, 'rule') for source, _, imported in sources[:2]]
    destination.incorporate(proposal, candidate, 'conclusion')
    _, use = destination.use('conclusion')
    assert {r['link']['id'] for r in resume(destination, use)['origins']} == set(links)
    alpha, receipt, _ = sources[0]
    alpha.challenge('rule')
    destination.import_(alpha.export(receipt, 'correction.json'))
    report = resume(destination, use, 1)
    assert len(report['origins']) == 2
    assert all(r['live_origin'] == 'not-checked' and r['membership'] == 'undetermined' for r in report['origins'])
    assert 'review-required' in next(r for r in report['origins'] if r['link']['id'] == links[0])['statuses']
    destination.root.rename(tmp_path / 'offline')
    missing = resume(destination, use, 1)
    assert missing['origin_analysis']['complete']
    assert missing['origins'] == report['origins']
    assert missing['historical_check']['diagnostic'] != report['historical_check']['diagnostic']


def test_transitive_correction_actions_are_dependency_first(tmp_path, run_cli):
    a, b, receipt, _, b_link = simple_transfer(tmp_path, run_cli)
    c = Workspace(tmp_path, run_cli, 'third')
    imported = c.import_(b.export(b.read('local-rule'), 'middle.json'))
    proposal, candidate = c.proposal('third-rule')
    c_link = c.link(proposal, imported, b, 'local-rule')
    c.incorporate(proposal, candidate, 'third-rule')
    _, use = c.use('third-rule')
    a.challenge('rule')
    c.import_(a.export(receipt, 'ancestor.json'))
    report = resume(c, use, 1)
    rows = {r['link']['id']: r for r in report['origins']}
    assert set(rows) == {b_link, c_link}
    assert 'review-required' in rows[b_link]['statuses']
    actions = [a['links'][0]['id'] for a in report['actions'] if a['action'] == 'inspect-origin']
    assert actions.index(b_link) < actions.index(c_link)


def test_independence_preserves_historical_path_without_new_upstream_gate(tmp_path, run_cli):
    a, b, receipt, _, link = simple_transfer(tmp_path, run_cli)
    b.install('intermediate', ('local-rule',))
    proposal, candidate = b.proposal('independent', supersedes=('intermediate',))
    detached = b.artifact('detach-transfer', proposal, '--from', link, *ATTR)
    b.incorporate(proposal, candidate, 'independent')
    _, use = b.use('independent')
    a.challenge('rule')
    b.import_(a.export(receipt, 'update.json'))
    report = resume(b, use)
    rows = {r['link']['id']: r for r in report['origins']}
    assert rows[link]['membership'] == 'historical'
    assert rows[detached]['membership'] == 'current'
    assert rows[detached]['disposition'] == 'independent'
    assert 'origin-review-stale' not in rows[detached]['statuses']
    assert report['actions'] == []


def test_requested_narrow_scope_reports_its_challenge_while_historical_check_passes(tmp_path, run_cli):
    a = Workspace(tmp_path, run_cli, 'source')
    a.install('rule')
    a.bind('rule')
    receipt = a.read('rule')
    statement = tmp_path / 'east.txt'
    statement.write_text('Review only the east region.\n')
    challenge = a.artifact('challenge', 'source:rule', '--statement', str(statement), '--kind', 'factual',
                           *SCOPE, '--scope', 'region=east', *ATTR)
    a.accept(challenge)
    b = Workspace(tmp_path, run_cli, 'destination')
    imported = b.import_(a.export(receipt, 'narrow.json'))
    proposal, candidate = b.proposal('local')
    b.link(proposal, imported, a, 'rule')
    b.incorporate(proposal, candidate, 'local')
    _, use = b.use('local')
    report = resume(b, use, 1, (*SCOPE, '--scope', 'region=east'))
    assert report['historical_check']['status'] == 'current'
    assert not report['scope']['matches']
    assert 'review-required' in report['origins'][0]['statuses']
    assert report['origins'][0]['membership'] == 'undetermined'
    assert any(a['action'] == 'read-for-requested-scope' for a in report['actions'])


@pytest.mark.parametrize('arguments', [(), ('--scope', 'bad'), (*SCOPE, *SCOPE),
    (*SCOPE, '--max-bytes', '2047'), (*SCOPE, '--max-bytes', '1048577'),
    (*SCOPE, '--max-bytes', '4096', '--max-bytes', '4096')])
def test_usage_errors_are_nonmutating(tmp_path, run_cli, arguments):
    w = Workspace(tmp_path, run_cli, 'local')
    before = w.store.read_bytes(), tree(w.root)
    result = w.call('resume-use', '0' * 64, *arguments, code=2)
    assert not result.stdout
    assert (w.store.read_bytes(), tree(w.root)) == before


def test_wrong_kind_missing_handle_and_overflow_emit_no_report(tmp_path, run_cli):
    a, b, receipt, imported, _ = simple_transfer(tmp_path, run_cli)
    _, use = b.use('local-rule')
    for handle in (imported, '0' * 64):
        result = b.call('resume-use', handle, *SCOPE, code=1)
        assert not result.stdout
    a.challenge('rule')
    b.import_(a.export(receipt, 'correction.json'))
    # A blocked report includes complete diagnostics and advisory actions.
    report = resume(b, use, 1)
    assert len(json.dumps(report).encode()) > 2048
    before = b.store.read_bytes(), tree(b.root)
    result = b.call('resume-use', use, *SCOPE, '--max-bytes', '2048', code=1)
    assert not result.stdout and 'exceeds --max-bytes' in result.stderr
    assert (b.store.read_bytes(), tree(b.root)) == before


def legacy_schema(w, version):
    kinds = "'registration','relocation','checkpoint','binding','support-review','conflict','choice','receipt','use'"
    if version == 2:
        kinds += ",'proposal','challenge','inspection','decision','incorporation','resolution','address','publication','reflection'"
    # Construct the historical legacy SQLite schema before acquiring a legacy use.
    with sqlite3.connect(w.store) as db:
        rows = db.execute('SELECT * FROM events ORDER BY sequence').fetchall()
        workspace_id = db.execute('SELECT workspace_id FROM workspace').fetchone()[0]
        db.execute('DROP TABLE events')
        db.execute('DROP TABLE workspace')
        db.execute('CREATE TABLE workspace (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), '
                   f'schema_version INTEGER NOT NULL CHECK (schema_version = {version}), workspace_id TEXT NOT NULL)')
        db.execute('CREATE TABLE events (sequence INTEGER PRIMARY KEY CHECK (sequence >= 1), '
                   'id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK (kind IN (' + kinds + ')), '
                   'prior TEXT REFERENCES events(id), payload TEXT NOT NULL, created_at TEXT NOT NULL)')
        db.execute('INSERT INTO workspace VALUES (1,?,?)', (version, workspace_id))
        db.executemany('INSERT INTO events VALUES (?,?,?,?,?,?)', rows)


@pytest.mark.parametrize('version', [1, 2])
def test_legacy_missing_ancestry_is_incomplete_without_changing_check_use(tmp_path, run_cli, version):
    w = Workspace(tmp_path, run_cli, 'legacy')
    w.install('old')
    w.install('new', ('old',))
    w.bind('new')
    legacy_schema(w, version)
    _, use = w.use('new')
    before = w.call('check-use', use).stdout
    report = resume(w, use, 1)
    assert report['historical_check']['status'] == 'current'
    assert not report['origin_analysis']['complete']
    assert all('ancest' in row['diagnostic'] for row in report['origin_analysis']['failures'])
    assert w.call('check-use', use).stdout == before
    w.call('upgrade')
    assert not resume(w, use, 1)['origin_analysis']['complete']
    w.call('check-use', use)


def test_independent_reference_does_not_hide_continuing_sibling(tmp_path, run_cli):
    a, b, receipt, imported, first_link = simple_transfer(tmp_path, run_cli)
    proposal, candidate = b.proposal('other')
    other_link = b.link(proposal, imported, a, 'rule')
    b.incorporate(proposal, candidate, 'other')
    proposal, candidate = b.proposal('independent', supersedes=('local-rule',))
    detached = b.artifact('detach-transfer', proposal, '--from', first_link, *ATTR)
    b.incorporate(proposal, candidate, 'independent')
    b.install('conclusion')
    b.bind('conclusion', ('independent', 'other'))
    _, use = b.use('conclusion')
    report = resume(b, use)
    rows = {r['link']['id']: r for r in report['origins']}
    assert rows[other_link]['membership'] == rows[detached]['membership'] == 'current'
    assert rows[first_link]['membership'] == 'historical'
    a.challenge('rule')
    b.import_(a.export(receipt, 'sibling-correction.json'))
    report = resume(b, use, 1)
    rows = {r['link']['id']: r for r in report['origins']}
    assert rows[other_link]['membership'] == rows[detached]['membership'] == 'undetermined'
    assert rows[first_link]['membership'] == 'historical'
    assert [a['links'][0]['id'] for a in report['actions'] if a['action'] == 'inspect-origin'] == [other_link]


def test_sequential_update_refusal_preserves_committed_correction_and_retry(tmp_path, run_cli):
    a, b, receipt, _, _ = simple_transfer(tmp_path, run_cli)
    _, use = b.use('local-rule')
    fork = Workspace(tmp_path, run_cli, 'fork')
    fork.store.write_bytes(a.store.read_bytes())
    # A distinct valid history after the same prefix is a fork, not a second origin.
    fork.artifact('inspect', a.show(receipt)['payload']['content']['units'][0]['binding'])
    fork_capsule = fork.export(receipt, 'fork.json')
    a.challenge('rule')
    correction = a.export(receipt, 'update.json')
    imported = b.import_(correction)
    before = b.store.read_bytes()
    refused = b.call('import-transfer', str(fork_capsule), *ATTR, code=1)
    assert 'fork' in refused.stderr
    assert b.store.read_bytes() == before
    assert b.import_(correction) == imported and b.store.read_bytes() == before
    assert any('review-required' in row['statuses'] for row in resume(b, use, 1)['origins'])


def test_shared_analysis_budget_reports_exhaustion_without_changing_legacy_use(tmp_path, run_cli):
    w = Workspace(tmp_path, run_cli, 'dense')
    legacy_schema(w, 1)
    for index in range(33):
        w.install(f'n{index:02}', tuple(f'n{old:02}' for old in range(index)))
        w.bind(f'n{index:02}')
    _, use = w.use('n32')
    report = resume(w, use, 1)
    assert report['historical_check']['status'] == 'current'
    assert not report['origin_analysis']['complete']
    assert any('512' in row['diagnostic'] for row in report['origin_analysis']['failures'])
    w.call('check-use', use)


def test_incomplete_legacy_branch_preserves_separate_corrected_origin(tmp_path, run_cli):
    w = Workspace(tmp_path, run_cli, 'destination')
    legacy_schema(w, 2)
    w.install('old')
    w.install('conclusion', ('old',))
    original_proposal, candidate = w.proposal('other')
    w.install('other')
    w.bind('other')
    w.bind('conclusion', ('other',))
    _, use = w.use('conclusion')
    w.call('upgrade')
    source = Workspace(tmp_path, run_cli, 'source')
    source.install('rule')
    source.bind('rule')
    receipt = source.read('rule')
    imported = w.import_(source.export(receipt, 'first-origin.json'))
    proposal = w.artifact('renew', original_proposal, *ATTR)
    link = w.link(proposal, imported, source, 'rule')
    w.incorporate(proposal, candidate, 'other')
    source.challenge('rule')
    w.import_(source.export(receipt, 'known-correction.json'))
    report = resume(w, use, 1)
    assert not report['origin_analysis']['complete']
    assert any('ancest' in row['diagnostic'] for row in report['origin_analysis']['failures'])
    row = next(row for row in report['origins'] if row['link']['id'] == link)
    assert row['identity'] == {'project': source.project, 'unit': 'rule'}
    assert 'review-required' in row['statuses']
    assert row['membership'] == 'undetermined'
    action = next(a for a in report['actions'] if a['action'] == 'inspect-origin')
    assert action['links'] == [row['link']]
    assert 'ordering unavailable' in action['diagnostic']


def test_foreign_analysis_failure_qualifies_the_parent_link_workspace(tmp_path, run_cli):
    source = Workspace(tmp_path, run_cli, 'source')
    for index in range(126):
        source.install(f'n{index:03}', (f'n{index - 1:03}',) if index else ())
    source.bind('n125')
    receipt = source.read('n125')
    destination = Workspace(tmp_path, run_cli, 'destination')
    imported = destination.import_(source.export(receipt, 'origin.json'))
    proposal, candidate = destination.proposal('local')
    retained = destination.link(proposal, imported, source, 'n125', origin_only=('n124',))
    destination.incorporate(proposal, candidate, 'local')
    proposal, candidate = destination.proposal('independent', supersedes=('local',))
    independent = destination.artifact('detach-transfer', proposal, '--from', retained, *ATTR)
    destination.incorporate(proposal, candidate, 'independent')
    # The independent disposition permits checked use; historical origin analysis
    # additionally visits 126 foreign units and exhausts its shared 128-node bound.
    destination.install('aaa')
    destination.bind('aaa')
    destination.install('consumer')
    destination.bind('consumer', ('aaa', 'independent'))
    _, use = destination.use('consumer')
    report = resume(destination, use, 1)
    assert report['historical_check'] == {'status': 'current', 'diagnostic': None}
    assert not report['origin_analysis']['complete']
    failure, = report['origin_analysis']['failures']
    assert '128' in failure['diagnostic']
    assert failure['workspace'] == report['workspace']
    assert failure['link'] == independent
    assert destination.show(failure['link'])['kind'] == 'transfer-link'
    assert not source.call('show', failure['link'], code=1).stdout
