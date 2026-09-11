"""Portable-history failure boundaries exercised only through subprocess CLI."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from test_transfer_contract import Workspace, ATTR, SCOPE, simple_transfer, tree

REPO = Path(__file__).resolve().parents[1]


def raw(workspace, *args, env=None, stdout=None, timeout=20):
    return subprocess.run([sys.executable, '-P', '-m', 'validated_memory', 'consultation', '--store', str(workspace.store), *args],
                          cwd=workspace.base, env=dict(os.environ, PYTHONPATH=str(REPO), **(env or {})),
                          stdout=stdout if stdout is not None else subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def seal(value):
    value.pop('sha256', None)
    value['sha256'] = hashlib.sha256(encoded(value).encode()).hexdigest()
    return encoded(value) + '\n'


def bare_source(tmp_path, run_cli, name='source'):
    source = Workspace(tmp_path, run_cli, name)
    source.install('rule')
    binding = source.bind('rule')
    receipt = source.read('rule')
    return source, binding, receipt


def test_dense_ancestry_dag_is_bounded_by_nodes_and_edges_not_paths(tmp_path, run_cli):
    w = Workspace(tmp_path, run_cli, 'dense')
    for index in range(30):
        w.install(f'n{index:02}', tuple(f'n{old:02}' for old in range(index)))
    w.bind('n29')
    result = raw(w, 'read', 'dense:n29', *SCOPE, timeout=10)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout.splitlines()[-1])['id']
    assert len(w.show(receipt)['payload']['transfer']['lineage']) == 29
    # 33 fully connected ancestors need528edges, beyond the supported512.
    for index in range(30, 33):
        w.install(f'n{index:02}', tuple(f'n{old:02}' for old in range(index)))
    w.bind('n32')
    result = raw(w, 'read', 'dense:n32', *SCOPE, timeout=10)
    assert result.returncode == 1 and '512' in result.stderr and not result.stdout


def test_capsule_reader_accepts_more_than_one_mib_without_raising_source_file_bound(tmp_path, run_cli):
    source, _binding, receipt = bare_source(tmp_path, run_cli)
    statement = tmp_path / 'large-statement.txt'
    statement.write_text('s' * 60000)
    for index in range(18):
        source.artifact('challenge', 'source:rule', '--statement', str(statement), '--kind', 'policy', *SCOPE,
                        '--actor', str(index), '--reason', 'Distinct retained request')
    cap = source.export(receipt, 'large-capsule.json')
    assert 1048576 < cap.stat().st_size < 8388608
    destination = Workspace(tmp_path, run_cli, 'destination')
    destination.import_(cap)


@pytest.mark.parametrize('point,committed', [('before-inspection-output', False), ('after-inspection-output', False), ('before-commit', False), ('after-commit', True)])
def test_link_output_and_commit_failures_preserve_adopter_and_report_committed_handle(tmp_path, run_cli, point, committed):
    source, _binding, receipt = bare_source(tmp_path, run_cli)
    destination = Workspace(tmp_path, run_cli, 'destination')
    imported = destination.import_(source.export(receipt, 'capsule.json'))
    proposal, _candidate = destination.proposal('local')
    before = destination.store.read_bytes(), tree(destination.root)
    args = ('link-transfer', proposal, '--import', imported, '--origin', f'{source.project}:rule', *ATTR)
    result = raw(destination, *args, env={'VALIDATED_MEMORY_CONSULTATION_FAULT': point})
    assert result.returncode == 1 and tree(destination.root) == before[1]
    if committed:
        assert 'committed artifact id=' in result.stderr
        assert destination.store.read_bytes() != before[0]
    else:
        assert destination.store.read_bytes() == before[0]
    destination.call(*args)


def test_failed_export_or_link_delivery_never_changes_history(tmp_path, run_cli):
    source, _binding, receipt = bare_source(tmp_path, run_cli)
    destination = Workspace(tmp_path, run_cli, 'destination')
    imported = destination.import_(source.export(receipt, 'capsule.json'))
    proposal, _ = destination.proposal('local')
    for workspace, args in ((source, ('export-transfer', receipt, '--include-workspace-history')),
                            (destination, ('link-transfer', proposal, '--import', imported, '--origin', f'{source.project}:rule', *ATTR))):
        before = workspace.store.read_bytes()
        with open('/dev/full', 'w') as output:
            result = raw(workspace, *args, stdout=output)
        assert result.returncode == 1 and workspace.store.read_bytes() == before


@pytest.mark.parametrize('kind', ['pretty', 'second_newline', 'digest', 'unknown_field', 'bad_sequence', 'future_schema'])
def test_capsule_shape_encoding_and_history_failures_are_atomic(tmp_path, run_cli, kind):
    source, _binding, receipt = bare_source(tmp_path, run_cli)
    path = source.export(receipt, 'capsule.json')
    cap = json.loads(path.read_text())
    if kind == 'pretty':
        text = json.dumps(cap, indent=2) + '\n'
    elif kind == 'second_newline':
        text = path.read_text() + '\n'
    elif kind == 'digest':
        cap['sha256'] = '0' * 64
        text = encoded(cap) + '\n'
    elif kind == 'unknown_field':
        cap['unexpected'] = True
        text = seal(cap)
    elif kind == 'bad_sequence':
        cap['events'][0]['sequence'] = 2
        text = seal(cap)
    else:
        cap['storage_version'] = 4
        text = seal(cap)
    path.write_text(text)
    destination = Workspace(tmp_path, run_cli, 'destination')
    before = destination.store.read_bytes(), tree(destination.root)
    result = raw(destination, 'import-transfer', str(path), *ATTR)
    assert result.returncode == 1 and not result.stdout
    assert (destination.store.read_bytes(), tree(destination.root)) == before


def test_nested_self_import_and_depth_are_explicit(tmp_path, run_cli):
    source, _binding, receipt = bare_source(tmp_path, run_cli, 'level0')
    cap = source.export(receipt, 'level0.json')
    for index in range(1, 4):
        next_source, _binding, next_receipt = bare_source(tmp_path, run_cli, f'level{index}')
        next_source.import_(cap)
        cap = next_source.export(next_receipt, f'level{index}.json')
    destination, _binding, receipt = bare_source(tmp_path, run_cli, 'destination')
    destination.import_(cap)
    before = destination.store.read_bytes()
    result = raw(destination, 'export-transfer', receipt, '--include-workspace-history', '--assess')
    summary = json.loads(result.stdout)
    assert result.returncode == 0 and not summary['supported']
    assert any('4 origin levels' in reason for reason in summary['refusals'])
    result = raw(destination, 'export-transfer', receipt, '--include-workspace-history')
    assert result.returncode == 1 and '4 origin levels' in result.stderr and not result.stdout
    assert destination.store.read_bytes() == before
    # A returning copy cannot create a cycle of origin authority.
    result = raw(source, 'import-transfer', str(tmp_path / 'level1.json'), *ATTR)
    assert result.returncode == 1 and 'self/cyclic' in result.stderr


def test_shorter_compatible_import_cannot_restore_eligibility(tmp_path, run_cli):
    source, destination, receipt, _imported, _link = simple_transfer(tmp_path, run_cli)
    _local_receipt, use = destination.use('local-rule')
    older = source.export(receipt, 'older.json')
    source.challenge('rule')
    destination.import_(source.export(receipt, 'newer.json'))
    destination.call('check-use', use, code=1)
    destination.import_(older)
    destination.call('check-use', use, code=1)


def test_unrelated_import_does_not_stale_existing_use(tmp_path, run_cli):
    source, _binding, receipt = bare_source(tmp_path, run_cli)
    destination, _binding, _receipt = bare_source(tmp_path, run_cli, 'destination')
    _, use = destination.use('rule')
    destination.import_(source.export(receipt, 'capsule.json'))
    destination.call('check-use', use)


def test_mapped_support_cannot_be_removed_by_later_support_review(tmp_path, run_cli):
    source, destination, _receipt, _imported, _link = simple_transfer(tmp_path, run_cli)
    destination.use('local-rule')
    prior = next(event['id'] for event in _events(destination) if event['kind'] == 'binding')
    (destination.root / 'different.txt').write_text('Different justification\n')
    destination.artifact('review-support', 'destination:local-rule', '--prior', prior, '--support', 'different.txt',
                         '--authority', 'destination', *SCOPE, *ATTR)
    result = raw(destination, 'read', 'destination:local-rule', *SCOPE)
    assert result.returncode == 1 and 'evidence' in result.stderr


def _events(workspace):
    with sqlite3.connect(workspace.store) as db:
        return [dict(id=row[0], kind=row[1], payload=json.loads(row[2])) for row in db.execute('SELECT id,kind,payload FROM events ORDER BY sequence')]


def test_link_requires_current_source_scope_and_all_support(tmp_path, run_cli):
    source, _binding, receipt = bare_source(tmp_path, run_cli)
    destination = Workspace(tmp_path, run_cli, 'destination')
    imported = destination.import_(source.export(receipt, 'capsule.json'))
    (destination.root / 'sources/evidence.txt').write_text('Unrelated local evidence\n')
    proposal, _ = destination.proposal('local')
    before = destination.store.read_bytes()
    result = raw(destination, 'link-transfer', proposal, '--import', imported, '--origin', f'{source.project}:rule', *ATTR)
    assert result.returncode == 1 and 'support' in result.stderr
    assert destination.store.read_bytes() == before


def test_accepted_source_challenge_prevents_link_without_partial_inspection(tmp_path, run_cli):
    source, _binding, receipt = bare_source(tmp_path, run_cli)
    source.challenge('rule')
    destination = Workspace(tmp_path, run_cli, 'destination')
    imported = destination.import_(source.export(receipt, 'capsule.json'))
    proposal, _ = destination.proposal('local')
    before = destination.store.read_bytes()
    result = raw(destination, 'link-transfer', proposal, '--import', imported, '--origin', f'{source.project}:rule', *ATTR)
    assert result.returncode == 1 and 'review-required' in result.stderr and not result.stdout
    assert destination.store.read_bytes() == before


def test_ancestor_only_inventory_names_affected_local_descendant(tmp_path, run_cli):
    source, middle, receipt, _imported, _link = simple_transfer(tmp_path, run_cli)
    destination = Workspace(tmp_path, run_cli, 'third')
    imported = destination.import_(middle.export(middle.read('local-rule'), 'middle.json'))
    proposal, candidate = destination.proposal('third-rule')
    linked = destination.link(proposal, imported, middle, 'local-rule')
    destination.incorporate(proposal, candidate, 'third-rule')
    source.challenge('rule')
    ancestor_import = destination.import_(source.export(receipt, 'ancestor.json'))
    output = json.loads(destination.call('show-transfer', ancestor_import).stdout)
    row = next(row for row in output['inventory']['local_links'] if row['link'] == linked)
    assert row['local']['unit'] == 'third-rule'
    assert 'review-required' in row['statuses']


@pytest.mark.parametrize('version', [1, 2])
def test_legacy_unbound_ancestor_upgrade_preserves_exact_receipts_and_uses(tmp_path, run_cli, version):
    w = Workspace(tmp_path, run_cli, 'legacy')
    w.install('old')
    w.install('new', ('old',))
    w.bind('new')
    kinds = "'registration','relocation','checkpoint','binding','support-review','conflict','choice','receipt','use'"
    if version == 2:
        kinds += ",'proposal','challenge','inspection','decision','incorporation','resolution','address','publication','reflection'"
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
    receipt, use = w.use('new')
    assert w.show(receipt)['payload']['version'] == version
    with sqlite3.connect(w.store) as db:
        old_rows = db.execute('SELECT * FROM events ORDER BY sequence').fetchall()
    w.call('upgrade')
    with sqlite3.connect(w.store) as db:
        assert db.execute('SELECT * FROM events ORDER BY sequence').fetchall() == old_rows
        assert db.execute('SELECT schema_version,workspace_id FROM workspace').fetchone() == (3, workspace_id)
    w.show(receipt)
    w.call('check-use', use)
    assert w.show(w.read('new'))['payload']['version'] == 3


def test_independent_branch_does_not_cancel_retaining_diamond_sibling(tmp_path, run_cli):
    _source, destination, _receipt, _imported, link = simple_transfer(tmp_path, run_cli)
    proposal, candidate = destination.proposal('left', supersedes=('local-rule',))
    destination.artifact('detach-transfer', proposal, '--from', link, *ATTR)
    destination.incorporate(proposal, candidate, 'left')
    destination.install('right', ('local-rule',))
    destination.install('joined', ('left', 'right'))
    destination.bind('joined')
    result = raw(destination, 'read', 'destination:joined', *SCOPE)
    assert result.returncode == 1 and 'explicit retain or independent disposition' in result.stderr
    assert not result.stdout


@pytest.mark.parametrize('remedy', ['review', 'incorporation'])
@pytest.mark.parametrize('resolved_before', [False, True])
def test_resolution_checks_current_foreign_origin_on_creation_and_retry(tmp_path, run_cli, remedy, resolved_before):
    source, destination, receipt, imported, _link = simple_transfer(tmp_path, run_cli)
    target = 'local-rule'
    if remedy == 'incorporation':
        proposal, candidate = destination.proposal('successor', supersedes=(target,))
        destination.link(proposal, imported, source, 'rule')
        incorporation = destination.incorporate(proposal, candidate, 'successor')
        binding = destination.show(incorporation)['payload']['binding']
        remedy_args = ('--incorporation', incorporation)
    else:
        binding = next(e['id'] for e in _events(destination) if e['kind'] == 'binding')
        remedy_args = ('--review', binding)
    challenge, decision = destination.challenge(target)
    inspection = destination.artifact('inspect', binding)
    args = ('resolve', challenge, '--decision', decision, '--inspection', inspection, *remedy_args, *ATTR)
    if resolved_before:
        resolution = destination.artifact(*args)
    source.challenge('rule')
    destination.import_(source.export(receipt, 'blocked-origin.json'))
    before = destination.store.read_bytes(), tree(destination.root)
    result = raw(destination, *args)
    assert result.returncode == 1 and 'review-required' in result.stderr and not result.stdout
    assert (destination.store.read_bytes(), tree(destination.root)) == before
    if resolved_before:
        destination.show(resolution)  # The original pre-import assertion remains historical.
    else:
        payload = dict(version=1, challenge=challenge, decision=decision, inspection=inspection,
                       remedy=dict(kind='review', binding=binding), actor='fixture-reviewer', reason='Forged later assertion.')
        if remedy == 'review':
            handle = hashlib.sha256(encoded(dict(kind='resolution', prior=None, payload=payload)).encode()).hexdigest()
            with sqlite3.connect(destination.store) as db:
                sequence = db.execute('SELECT max(sequence)+1 FROM events').fetchone()[0]
                db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',
                           (sequence, handle, 'resolution', None, encoded(payload), '2026-09-11T00:00:00.000000Z'))
            result = raw(destination, 'show', handle)
            assert result.returncode == 1 and 'review-required' in result.stderr and not result.stdout


@pytest.mark.parametrize('levels', [1, 2])
def test_narrow_foreign_challenge_applies_to_requested_scope_transitively(tmp_path, run_cli, levels):
    source, _binding, receipt = bare_source(tmp_path, run_cli)
    statement = tmp_path / 'narrow.txt'
    statement.write_text('Only the east region requires correction.\n')
    challenge = source.artifact('challenge', 'source:rule', '--statement', str(statement), '--kind', 'factual',
                                *SCOPE, '--scope', 'region=east', *ATTR)
    source.accept(challenge)
    previous, previous_receipt, name = source, receipt, 'rule'
    for level in range(levels):
        destination = Workspace(tmp_path, run_cli, f'destination{level}')
        imported = destination.import_(previous.export(previous_receipt, f'level{level}.json'))
        proposal, candidate = destination.proposal('local-rule')
        destination.link(proposal, imported, previous, name)
        destination.incorporate(proposal, candidate, 'local-rule')
        previous_receipt = destination.read('local-rule')
        previous, name = destination, 'local-rule'
    before = destination.store.read_bytes()
    result = raw(destination, 'read', f'{destination.name}:local-rule', *SCOPE, '--scope', 'region=east')
    assert result.returncode == 1 and 'review-required' in result.stderr and not result.stdout
    assert destination.store.read_bytes() == before
    destination.call('read', f'{destination.name}:local-rule', *SCOPE, '--scope', 'region=west')


def ancestry_workspace(tmp_path, run_cli, name, count, edges=None):
    workspace = Workspace(tmp_path, run_cli, name)
    parents = {index: [index - 1] if index else [] for index in range(count)}
    remaining = (edges if edges is not None else count - 1) - (count - 1)
    for index in range(count):
        for parent in range(index - 1):
            if remaining:
                parents[index].append(parent)
                remaining -= 1
    assert remaining == 0
    for index in range(count):
        workspace.install(f'n{index:02}', tuple(f'n{old:02}' for old in sorted(parents[index])))
    return workspace, parents


@pytest.mark.parametrize('local_count,source_edges,allowed', [(64, 63, True), (65, 63, False),
                                                            (64, 448, True), (64, 449, False)])
def test_operation_wide_foreign_and_local_ancestry_budget(tmp_path, run_cli, local_count, source_edges, allowed):
    source, parents = ancestry_workspace(tmp_path, run_cli, 'source', 64, source_edges)
    source.bind('n63')
    receipt = source.read('n63')
    destination, _parents = ancestry_workspace(tmp_path, run_cli, 'destination', local_count - 1)
    imported = destination.import_(source.export(receipt, 'bounded-source.json'))
    proposal, _candidate = destination.proposal('local', supersedes=(f'n{local_count - 2:02}',))
    args = ['link-transfer', proposal, '--import', imported, '--origin', f'{source.project}:n63', *ATTR]
    for index in parents[63]:
        args.extend(('--origin-only-predecessor', f'{source.project}:n{index:02}'))
    before = destination.store.read_bytes(), tree(destination.root)
    result = raw(destination, *args)
    assert result.returncode == (0 if allowed else 1), result.stderr
    if not allowed:
        assert ('128' if local_count == 65 else '512') in result.stderr and not result.stdout
        assert (destination.store.read_bytes(), tree(destination.root)) == before


def test_requested_scope_reaches_mapped_dependency_corrections(tmp_path, run_cli):
    source = Workspace(tmp_path, run_cli, 'source')
    source.install('basis')
    source.bind('basis')
    source.install('rule')
    source.bind('rule', ('basis',))
    receipt = source.read('rule')
    statement = tmp_path / 'dependency-challenge.txt'
    statement.write_text('The dependency is disputed for east only.\n')
    challenge = source.artifact('challenge', 'source:basis', '--statement', str(statement), '--kind', 'factual',
                                *SCOPE, '--scope', 'region=east', *ATTR)
    source.accept(challenge)
    destination = Workspace(tmp_path, run_cli, 'destination')
    imported = destination.import_(source.export(receipt, 'dependency-origin.json'))
    basis, candidate = destination.proposal('local-basis')
    destination.link(basis, imported, source, 'basis')
    destination.incorporate(basis, candidate, 'local-basis')
    rule, candidate = destination.proposal('local-rule', references=('local-basis',))
    destination.link(rule, imported, source, 'rule', dependencies=(('basis', 'local-basis'),))
    destination.incorporate(rule, candidate, 'local-rule', ('local-basis',))
    before = destination.store.read_bytes()
    result = raw(destination, 'read', 'destination:local-rule', *SCOPE, '--scope', 'region=east')
    assert result.returncode == 1 and 'review-required' in result.stderr and not result.stdout
    assert destination.store.read_bytes() == before
    destination.call('read', 'destination:local-rule', *SCOPE, '--scope', 'region=west')


def remove_last_artifact(workspace, handle):
    event = workspace.show(handle)
    with sqlite3.connect(workspace.store) as db:
        assert db.execute('SELECT id FROM events ORDER BY sequence DESC LIMIT 1').fetchone()[0] == handle
        db.execute('DELETE FROM events WHERE id=?', (handle,))
    return event


def append_forged_artifact(workspace, event):
    with sqlite3.connect(workspace.store) as db:
        sequence = db.execute('SELECT max(sequence)+1 FROM events').fetchone()[0]
        db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',
                   (sequence, event['id'], event['kind'], event['prior'], encoded(event['payload']), event['created_at']))


def consumer_reference(destination, depth):
    reference = 'local-rule'
    for index in range(depth - 1):
        name = f'bridge{index}'
        destination.install(name)
        destination.bind(name, (reference,))
        reference = name
    return reference


@pytest.mark.parametrize('depth', [1, 2])
@pytest.mark.parametrize('remedy', ['review', 'incorporation'])
@pytest.mark.parametrize('retry', [False, True])
def test_resolution_traverses_declared_consumers_live_and_replay(tmp_path, run_cli, depth, remedy, retry):
    source, destination, receipt, _imported, _link = simple_transfer(tmp_path, run_cli)
    reference = consumer_reference(destination, depth)
    destination.install('consumer')
    binding = destination.bind('consumer', (reference,))
    if remedy == 'incorporation':
        proposal, candidate = destination.proposal('consumer-new', references=(reference,), supersedes=('consumer',))
        incorporation = destination.incorporate(proposal, candidate, 'consumer-new', (reference,))
        binding = destination.show(incorporation)['payload']['binding']
        remedy_args = ('--incorporation', incorporation)
    else:
        remedy_args = ('--review', binding)
    challenge, decision = destination.challenge('consumer')
    inspection = destination.artifact('inspect', binding)
    args = ('resolve', challenge, '--decision', decision, '--inspection', inspection, *remedy_args, *ATTR)
    resolved = destination.artifact(*args)  # Equivalent eligible dependency closure succeeds.
    if not retry:
        forged = remove_last_artifact(destination, resolved)
    source.challenge('rule')
    destination.import_(source.export(receipt, 'blocked-consumer.json'))
    before = destination.store.read_bytes(), tree(destination.root)
    result = raw(destination, *args)
    assert result.returncode == 1 and 'review-required' in result.stderr and not result.stdout
    assert (destination.store.read_bytes(), tree(destination.root)) == before
    if retry:
        destination.show(resolved)
    else:
        append_forged_artifact(destination, forged)
        result = raw(destination, 'show', challenge)
        assert result.returncode == 1 and 'review-required' in result.stderr and not result.stdout


@pytest.mark.parametrize('depth', [1, 2])
@pytest.mark.parametrize('operation', ['accept', 'incorporate'])
def test_proposal_traverses_declared_consumers_live_and_replay(tmp_path, run_cli, depth, operation):
    source, destination, receipt, _imported, _link = simple_transfer(tmp_path, run_cli)
    reference = consumer_reference(destination, depth)
    proposal, candidate = destination.proposal('consumer', references=(reference,))
    inspection = destination.artifact('inspect', proposal)
    decision = destination.accept(proposal, inspection)
    if operation == 'accept':
        handle = decision
        args = ('decide', proposal, '--inspection', inspection, '--outcome', 'accept', *ATTR)
    else:
        (destination.root / 'knowledge/consumer.md').write_bytes(candidate.read_bytes())
        destination.bind('consumer', (reference,))
        args = ('incorporate', proposal, '--decision', decision, *ATTR)
        handle = destination.artifact(*args)
    forged = remove_last_artifact(destination, handle)
    source.challenge('rule')
    destination.import_(source.export(receipt, 'blocked-proposal.json'))
    before = destination.store.read_bytes(), tree(destination.root)
    result = raw(destination, *args)
    assert result.returncode == 1 and 'review-required' in result.stderr and not result.stdout
    assert (destination.store.read_bytes(), tree(destination.root)) == before
    append_forged_artifact(destination, forged)
    result = raw(destination, 'show', proposal)
    assert result.returncode == 1 and 'review-required' in result.stderr and not result.stdout


@pytest.mark.parametrize('retained', [False, True])
@pytest.mark.parametrize('operation', ['accept', 'resolve'])
def test_unbound_dependency_requires_replayable_canonical_proof_without_binding_requirement(tmp_path, run_cli, retained, operation):
    source, _binding, receipt = bare_source(tmp_path, run_cli)
    destination = Workspace(tmp_path, run_cli, 'destination')
    destination.install('old-leaf')
    leaf = destination.install('leaf', ('old-leaf',))
    leaf.write_text(leaf.read_text().replace('evidence: verifiable', 'evidence: hypothesis'))
    imported = destination.import_(source.export(receipt, 'proof-source.json'))
    carrier, _candidate = destination.proposal('carrier', supersedes=('leaf',) if retained else ())
    destination.link(carrier, imported, source, 'rule')
    if operation == 'accept':
        proposal, _candidate = destination.proposal('consumer', references=('leaf',))
        inspection = destination.artifact('inspect', proposal)
        args = ('decide', proposal, '--inspection', inspection, '--outcome', 'accept', *ATTR)
    else:
        destination.install('consumer')
        binding = destination.bind('consumer', ('leaf',))
        challenge, decision = destination.challenge('consumer')
        inspection = destination.artifact('inspect', binding)
        args = ('resolve', challenge, '--decision', decision, '--inspection', inspection, '--review', binding, *ATTR)
    before = destination.store.read_bytes(), tree(destination.root)
    result = raw(destination, *args)
    assert result.returncode == (0 if retained else 1), result.stderr
    if retained:
        handle = json.loads(result.stdout.splitlines()[-1])['id']
        destination.show(handle)
        assert all(e['payload']['identity']['unit'] != 'leaf' for e in _events(destination) if e['kind'] == 'binding')
    else:
        assert 'retain a reviewed binding or supported canonical lineage' in result.stderr and not result.stdout
        assert (destination.store.read_bytes(), tree(destination.root)) == before


def test_pending_proposal_exception_does_not_exempt_ordinary_linked_dependency(tmp_path, run_cli):
    source, _binding, receipt = bare_source(tmp_path, run_cli)
    destination = Workspace(tmp_path, run_cli, 'destination')
    imported = destination.import_(source.export(receipt, 'pending-dependency.json'))
    dependency, candidate = destination.proposal('dependency')
    destination.link(dependency, imported, source, 'rule')
    destination.accept(dependency)
    (destination.root / 'knowledge/dependency.md').write_bytes(candidate.read_bytes())
    destination.bind('dependency')
    consumer, _candidate = destination.proposal('consumer', references=('dependency',))
    inspection = destination.artifact('inspect', consumer)
    before = destination.store.read_bytes(), tree(destination.root)
    result = raw(destination, 'decide', consumer, '--inspection', inspection, '--outcome', 'accept', *ATTR)
    assert result.returncode == 1 and 'local-incorporation-pending' in result.stderr and not result.stdout
    assert (destination.store.read_bytes(), tree(destination.root)) == before


@pytest.mark.parametrize('operation', ['accept', 'resolve'])
def test_mismatching_bound_dependency_cannot_be_treated_as_unbound(tmp_path, run_cli, operation):
    source, destination, _receipt, imported, _link = simple_transfer(tmp_path, run_cli)
    bridge = destination.install('bridge')
    original = bridge.read_bytes()
    carrier, _candidate = destination.proposal('carrier', supersedes=('bridge',))
    destination.link(carrier, imported, source, 'rule')  # Retains the old unbound bridge bytes.
    bridge.write_bytes(original + b'Additional bound statement.\n')
    destination.bind('bridge', ('local-rule',))
    bridge.write_bytes(original)
    if operation == 'accept':
        proposal, _candidate = destination.proposal('consumer', references=('bridge',))
        inspection = destination.artifact('inspect', proposal)
        args = ('decide', proposal, '--inspection', inspection, '--outcome', 'accept', *ATTR)
    else:
        destination.install('consumer')
        binding = destination.bind('consumer', ('bridge',))
        challenge, decision = destination.challenge('consumer')
        inspection = destination.artifact('inspect', binding)
        args = ('resolve', challenge, '--decision', decision, '--inspection', inspection, '--review', binding, *ATTR)
    before = destination.store.read_bytes(), tree(destination.root)
    result = raw(destination, *args)
    assert result.returncode == 1 and 'referenced binding revision differs' in result.stderr and not result.stdout
    assert (destination.store.read_bytes(), tree(destination.root)) == before
