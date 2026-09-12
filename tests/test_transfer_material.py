"""Complete selected material exercised through public subprocess commands."""

import hashlib
import json

import pytest

from test_transfer_contract import ATTR, SCOPE, Workspace, simple_transfer, tree


def view(destination, imported, source, name, *, code=0, extra=()):
    before = destination.store.read_bytes(), tree(destination.root)
    result = destination.call('show-transfer', imported, '--origin', f'{source.project}:{name}',
                              '--material', *extra, code=code)
    assert (destination.store.read_bytes(), tree(destination.root)) == before
    if code:
        assert not result.stdout
        return result
    assert len(result.stdout.splitlines()) == 1
    report = json.loads(result.stdout)
    assert report['schema_version'] == 1 and report['operation'] == 'show-transfer'
    assert report['status'] == 'historical' and report['id'] == imported
    for row in report['material']['units'] + report['material']['predecessor_files'] + report['material']['support']:
        file = row['file']
        assert hashlib.sha256(file['text'].encode()).hexdigest() == file['sha256']
        assert len(file['text'].encode()) == file['size']
    return report


def review(source, name, prior):
    return source.artifact('review-support', f'{source.name}:{name}', '--prior', prior,
                           '--support', 'sources/evidence.txt', '--authority', source.name, *SCOPE, *ATTR)


def test_exact_initial_selection_keeps_both_same_path_support_revisions(tmp_path, run_cli):
    source = Workspace(tmp_path, run_cli, 'source')
    source.install('dependency')
    old = source.bind('dependency')
    source.install('root')
    root = source.bind('root', ('dependency',))
    receipt = source.read('root')
    destination = Workspace(tmp_path, run_cli, 'destination')
    initial = source.export(receipt, 'original.json')
    imported = destination.import_(initial)
    (source.root / 'sources/evidence.txt').write_text('Changed support, identical canonical dependency.\n')
    newer = review(source, 'dependency', old)
    destination.import_(source.export(receipt, 'newer.json'))
    source.root.rename(tmp_path / 'source-offline')
    report = view(destination, imported, source, 'root')
    assert report['selection']['binding'] == root
    assert report['selection_closures'] == [dict(workspace=report['selection']['workspace'], receipt=receipt,
        root={'project': source.project, 'unit': 'root'}, scope={'exercise': 'dispatch'},
        bindings=sorted([root, old]), via_links=[])]
    events = {row['event']['id']: row['event'] for row in report['material']['bindings']}
    assert set(events) == {root, old, newer}
    assert events[old]['payload']['unit'] == events[newer]['payload']['unit']
    support = report['material']['support']
    assert len(support) == 2
    assert {r['file']['path'] for r in support} == {'sources/evidence.txt'}
    assert len({r['file']['sha256'] for r in support}) == 2
    assert 'context-binding' in next(row for row in report['material']['units'] if row['identity']['unit'] == 'dependency')['roles']


def test_recursive_predecessors_bound_and_canonical_only_and_different_id_successor(tmp_path, run_cli):
    source = Workspace(tmp_path, run_cli, 'source')
    source.install('oldest')
    source.install('middle', ('oldest',))
    middle = source.bind('middle')
    source.install('root', ('middle',))
    source.bind('root')
    receipt = source.read('root')
    source.install('next', ('root',))
    successor = source.bind('next')
    destination = Workspace(tmp_path, run_cli, 'destination')
    imported = destination.import_(source.export(receipt, 'successor.json'))
    report = view(destination, imported, source, 'root')
    units = {row['identity']['unit']: row for row in report['material']['units']}
    assert set(units) == {'middle', 'root', 'next'}
    assert 'predecessor' in units['middle']['roles']
    assert 'successor' in units['next']['roles']
    predecessors = report['material']['predecessor_files']
    assert [row['identity']['unit'] for row in predecessors] == ['oldest']
    assert predecessors[0]['file']['text'] == (source.root / 'knowledge/oldest.md').read_text()
    assert {middle, successor} <= {row['event']['id'] for row in report['material']['bindings']}


def test_unbound_known_successor_refuses_without_partial_output(tmp_path, run_cli):
    source = Workspace(tmp_path, run_cli, 'source')
    source.install('root')
    source.bind('root')
    receipt = source.read('root')
    source.install('unbound', ('root',))
    source.install('later', ('unbound',))
    source.bind('later')
    source.read('later')  # Retains the unbound canonical intermediate in lineage.
    destination = Workspace(tmp_path, run_cli, 'destination')
    imported = destination.import_(source.export(receipt, 'unbound.json'))
    result = view(destination, imported, source, 'root', code=1)
    assert 'lacks retained binding/support declaration' in result.stderr


def test_transitive_selection_membership_and_new_ancestor_support(tmp_path, run_cli):
    a, b, a_receipt, _, link = simple_transfer(tmp_path, run_cli)
    binding = a.show(a_receipt)['payload']['content']['units'][0]['binding']
    b_receipt = b.read('local-rule')
    c = Workspace(tmp_path, run_cli, 'third')
    imported = c.import_(b.export(b_receipt, 'middle.json'))
    (a.root / 'sources/evidence.txt').write_text('Revised ancestor justification, unchanged claim.\n')
    newer = review(a, 'rule', binding)
    c.import_(a.export(a_receipt, 'ancestor-update.json'))
    frozen_link = b.show(link)['payload']['inspection_text']
    report = view(c, imported, b, 'local-rule')
    ancestor = next(row for row in report['selection_closures'] if row['root']['project'] == a.project)
    assert ancestor['bindings'] == [binding]
    assert ancestor['receipt'] == a_receipt
    assert ancestor['via_links'] == [{'workspace': report['selection']['workspace'], 'id': link}]
    events = {row['event']['id'] for row in report['material']['bindings']}
    assert {binding, newer} <= events
    assert [row['event']['id'] for row in report['material']['links']] == [link]
    assert b.show(link)['payload']['inspection_text'] == frozen_link


@pytest.mark.parametrize('args', [('--material',), ('--origin', 'bad'),
    ('--origin', 'bad', '--material'), ('--material', '--material'),
    ('--max-bytes', '2047'), ('--max-bytes', '1048577'), ('--max-bytes', '4096', '--max-bytes', '4096')])
def test_invalid_material_flags(tmp_path, run_cli, args):
    w = Workspace(tmp_path, run_cli, 'destination')
    before = w.store.read_bytes(), tree(w.root)
    result = w.call('show-transfer', '0' * 64, *args, code=2)
    assert not result.stdout
    assert (w.store.read_bytes(), tree(w.root)) == before


def test_stale_selector_wrong_handle_and_byte_bound(tmp_path, run_cli):
    a, b, receipt, imported, _ = simple_transfer(tmp_path, run_cli)
    view(b, imported, a, 'missing', code=1)
    view(b, '0' * 64, a, 'rule', code=1)
    view(b, b.read('local-rule'), a, 'rule', code=1)
    a.challenge('rule')
    b.import_(a.export(receipt, 'correction.json'))
    report = view(b, imported, a, 'rule')
    assert {row['event']['kind'] for row in report['material']['corrections']} >= {'challenge', 'decision'}
    result = view(b, imported, a, 'rule', code=1, extra=('--max-bytes', '2048'))
    assert 'exceeds --max-bytes' in result.stderr


def test_two_unbound_historical_predecessors_are_not_new_unsupported_corrections(tmp_path, run_cli):
    a = Workspace(tmp_path, run_cli, 'source')
    a.install('oldest')
    a.install('middle', ('oldest',))
    a.install('root', ('middle',))
    binding = a.bind('root')
    receipt = a.read('root')
    b = Workspace(tmp_path, run_cli, 'destination')
    imported = b.import_(a.export(receipt, 'ancestry.json'))
    report = view(b, imported, a, 'root')
    assert [row['event']['id'] for row in report['material']['bindings']] == [binding]
    assert {row['identity']['unit'] for row in report['material']['predecessor_files']} == {'oldest', 'middle'}


def test_selected_material_measurement_excludes_unrelated_history_and_preserves_inventory(tmp_path, run_cli):
    a = Workspace(tmp_path, run_cli, 'source')
    a.install('root')
    binding = a.bind('root')
    receipt = a.read('root')
    unrelated = a.install('unrelated')
    sentinel = 'UNRELATED-CANONICAL-SENTINEL'
    with unrelated.open('a') as stream:
        stream.write((sentinel + '\n') * 1500)
    a.bind('unrelated')
    capsule = a.export(receipt, 'whole.json')
    b = Workspace(tmp_path, run_cli, 'destination')
    imported = b.import_(capsule)
    inventory = b.call('show-transfer', imported).stdout
    report = view(b, imported, a, 'root')
    selected = b.call('show-transfer', imported, '--origin', f'{a.project}:root', '--material').stdout
    whole = b.call('show', imported).stdout
    assert b.call('show-transfer', imported).stdout == inventory
    assert sentinel in whole and sentinel in capsule.read_text() and sentinel not in selected
    assert [r['event']['id'] for r in report['material']['bindings']] == [binding]
    counts = {key: len(value) for key, value in report['material'].items()}
    assert counts == dict(units=1, support=1, bindings=1, predecessor_files=0, corrections=0, links=0)
    assert report['material']['units'][0]['file']['text'] == (a.root / 'knowledge/root.md').read_text()
    measurement = dict(whole_show_bytes=len(whole.encode()), capsule_bytes=capsule.stat().st_size,
                       selected_material_bytes=len(selected.encode()), categories=counts)
    assert measurement['selected_material_bytes'] < measurement['capsule_bytes'] < measurement['whole_show_bytes']
    (tmp_path / 'material-measurement.json').write_text(json.dumps(measurement, sort_keys=True) + '\n')
    print(json.dumps(measurement, sort_keys=True))


def test_proposal_challenge_resolution_and_unselected_conflict_are_explicit_context(tmp_path, run_cli):
    from test_transfer_contract import doc

    a = Workspace(tmp_path, run_cli, 'source')
    candidate = tmp_path / 'candidate.md'
    candidate.write_text(doc('root') + '\nPROPOSED-ONLY-TEXT\n')
    proposal = a.artifact('submit', a.name, str(candidate), '--path', 'knowledge/root.md',
                          '--support', 'sources/evidence.txt', '--authority', a.name, *SCOPE, *ATTR)
    a.install('root')
    binding = a.bind('root')
    receipt = a.read('root')
    challenge, decision = a.challenge('root')
    inspection = a.artifact('inspect', binding)
    resolution = a.artifact('resolve', challenge, '--decision', decision, '--review', binding,
                            '--inspection', inspection, *ATTR)
    a.install('competitor')
    competitor_support = a.root / 'sources/competitor.txt'
    competitor_support.write_text('UNSELECTED-COMPETITOR-EVIDENCE\n')
    a.artifact('bind', 'source:competitor', '--support', 'sources/competitor.txt', '--authority', a.name, *SCOPE, *ATTR)
    conflict = a.artifact('conflict', '--candidate', 'source:root', '--candidate', 'source:competitor', *SCOPE, *ATTR)
    choice = a.artifact('choose', conflict, '--candidate', 'source:root', *SCOPE, *ATTR)
    b = Workspace(tmp_path, run_cli, 'destination')
    imported = b.import_(a.export(receipt, 'context.json'))
    report = view(b, imported, a, 'root')
    events = {row['event']['id']: row['event'] for row in report['material']['corrections']}
    assert set(events) == {proposal, challenge, decision, resolution, conflict, choice}
    assert events[proposal] == a.show(proposal)
    assert 'PROPOSED-ONLY-TEXT' in events[proposal]['payload']['unit']['text']
    assert all('PROPOSED-ONLY-TEXT' not in row['file']['text'] for row in report['material']['units'])
    assert 'UNSELECTED-COMPETITOR-EVIDENCE' not in json.dumps(report)
    assert {r['identity']['unit'] for r in report['material']['units']} == {'root'}
    assert binding in {r['event']['id'] for r in report['material']['bindings']}
    assert 'Unselected conflict candidates' in report['audit_reference_policy']


@pytest.mark.parametrize('count,dense,diagnostic', [(129, False, '128'), (33, True, '512')])
def test_correction_family_shares_node_and_edge_bounds(tmp_path, run_cli, count, dense, diagnostic):
    a = Workspace(tmp_path, run_cli, 'source')
    a.install('n000')
    a.bind('n000')
    receipt = a.read('n000')
    for index in range(1, count):
        parents = tuple(f'n{old:03}' for old in (range(index) if dense else (index - 1,)))
        a.install(f'n{index:03}', parents)
        a.bind(f'n{index:03}')
    b = Workspace(tmp_path, run_cli, 'destination')
    imported = b.import_(a.export(receipt, 'large-family.json'))
    result = view(b, imported, a, 'n000', code=1)
    assert diagnostic in result.stderr


def test_multiple_origins_keep_same_unit_path_and_support_path_qualified(tmp_path, run_cli):
    sources = []
    b = Workspace(tmp_path, run_cli, 'destination')
    for name in ('alpha', 'beta'):
        a = Workspace(tmp_path, run_cli, name)
        a.install('root')
        a.bind('root')
        receipt = a.read('root')
        sources.append((a, b.import_(a.export(receipt, name + '.json'))))
    proposal, candidate = b.proposal('root')
    for a, imported in sources:
        b.link(proposal, imported, a, 'root')
    b.incorporate(proposal, candidate, 'root')
    c = Workspace(tmp_path, run_cli, 'third')
    imported = c.import_(b.export(b.read('root'), 'both.json'))
    report = view(c, imported, b, 'root')
    assert len(report['selection_closures']) == 3
    units = report['material']['units']
    assert {row['identity']['unit'] for row in units} == {'root'}
    assert len({row['workspace'] for row in units}) == 3
    assert len({row['identity']['project'] for row in units}) == 3
    assert len(report['material']['support']) == 3
    assert {row['file']['path'] for row in report['material']['support']} == {'sources/evidence.txt'}


def test_replayable_later_reference_cycle_remains_complete_historical_context(tmp_path, run_cli):
    a = Workspace(tmp_path, run_cli, 'source')
    a.install('first')
    first = a.bind('first')
    a.install('second')
    second = a.bind('second', ('first',))
    receipt = a.read('second')
    later = a.artifact('review-support', 'source:first', '--prior', first, '--support', 'sources/evidence.txt',
                       '--reference', 'source:second', '--authority', a.name, *SCOPE, *ATTR)
    refused = a.call('read', 'source:second', *SCOPE, code=1)
    assert 'cycle' in refused.stderr
    b = Workspace(tmp_path, run_cli, 'destination')
    imported = b.import_(a.export(receipt, 'cyclic-context.json'))
    report = view(b, imported, a, 'second')
    assert report['selection_closures'][0]['bindings'] == sorted([first, second])
    assert {row['event']['id'] for row in report['material']['bindings']} == {first, second, later}
    assert {row['identity']['unit'] for row in report['material']['units']} == {'first', 'second'}


def test_later_dependency_without_retained_evidence_refuses_complete_view(tmp_path, run_cli):
    a = Workspace(tmp_path, run_cli, 'source')
    a.install('root')
    binding = a.bind('root')
    receipt = a.read('root')
    a.install('unbound')
    a.artifact('review-support', 'source:root', '--prior', binding, '--support', 'sources/evidence.txt',
                '--reference', 'source:unbound', '--authority', a.name, *SCOPE, *ATTR)
    b = Workspace(tmp_path, run_cli, 'destination')
    imported = b.import_(a.export(receipt, 'missing-evidence.json'))
    refused = view(b, imported, a, 'root', code=1)
    assert 'lacks retained binding/support declaration' in refused.stderr and 'unbound' in refused.stderr


def test_exact_receipt_predecessor_selection_resolves_other_retained_revisions(tmp_path, run_cli):
    a = Workspace(tmp_path, run_cli, 'source')
    old = a.install('old')
    a.install('root', ('old',))
    a.bind('root')
    receipt = a.read('root')
    original = old.read_text()
    old.write_text(original + '\nLater canonical-only observation.\n')
    a.read('root')
    b = Workspace(tmp_path, run_cli, 'destination')
    imported = b.import_(a.export(receipt, 'selected-predecessor.json'))
    report = view(b, imported, a, 'root')
    assert report['material']['predecessor_files'][0]['file']['text'] == original


def test_independent_link_retains_transitive_history_without_eligibility_gate(tmp_path, run_cli):
    a, b, receipt, _, retaining = simple_transfer(tmp_path, run_cli)
    proposal, candidate = b.proposal('independent', supersedes=('local-rule',))
    independent = b.artifact('detach-transfer', proposal, '--from', retaining, *ATTR)
    b.incorporate(proposal, candidate, 'independent')
    b_receipt = b.read('independent')
    a.challenge('rule')
    b.import_(a.export(receipt, 'known-correction.json'))
    c = Workspace(tmp_path, run_cli, 'third')
    imported = c.import_(b.export(b_receipt, 'independent.json'))
    report = view(c, imported, b, 'independent')
    assert {row['event']['id'] for row in report['material']['links']} == {retaining, independent}
    assert {row['event']['kind'] for row in report['material']['corrections']} >= {'challenge', 'decision'}
    assert any(row['root']['project'] == a.project for row in report['selection_closures'])


def test_duplicate_selection_flags_and_corrupt_capsule_refuse_without_mutation(tmp_path, run_cli):
    a, b, receipt, imported, _ = simple_transfer(tmp_path, run_cli)
    origin = f'{a.project}:rule'
    before = b.store.read_bytes(), tree(b.root)
    for flags in [('--origin', origin, '--origin', origin, '--material'),
                  ('--origin', origin, '--material', '--material')]:
        assert not b.call('show-transfer', imported, *flags, code=2).stdout
    capsule = a.export(receipt, 'corrupt.json')
    value = json.loads(capsule.read_text())
    value['events'][0]['prior'] = value['events'][0]['id']
    payload = {key: item for key, item in value.items() if key != 'sha256'}
    canonical = lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    value['sha256'] = hashlib.sha256(canonical(payload).encode()).hexdigest()
    capsule.write_text(canonical(value) + '\n')
    assert not b.call('import-transfer', str(capsule), *ATTR, code=1).stdout
    assert (b.store.read_bytes(), tree(b.root)) == before


def test_new_correction_with_ambiguous_canonical_predecessor_refuses(tmp_path, run_cli):
    a = Workspace(tmp_path, run_cli, 'source')
    old = a.install('old')
    a.install('branch', ('old',))
    a.bind('branch')
    a.read('branch')
    old.write_text(old.read_text() + '\nA second retained canonical-only revision.\n')
    a.read('branch')
    a.install('root')
    a.bind('root')
    receipt = a.read('root')
    a.install('merged', ('root', 'branch'))
    a.bind('merged')
    b = Workspace(tmp_path, run_cli, 'destination')
    imported = b.import_(a.export(receipt, 'ambiguous-predecessor.json'))
    refused = view(b, imported, a, 'root', code=1)
    assert 'predecessor unavailable or ambiguous' in refused.stderr


def test_complete_everyday_handoff_material_matches_nested_history_and_measures_wire(tmp_path):
    from test_everyday_workflow import run_case

    case = run_case(tmp_path / 'everyday-material')
    imported = case.handles['final_import']['id']
    capsule_path = case.base / 'final-planning-transfer.json'
    capsule = json.loads(capsule_path.read_text())
    original_events = {event['id']: event for event in capsule['events']}
    receipt = original_events[capsule['receipt']]['payload']
    root = receipt['root']
    before = case.snapshot(), {name: (case.base / f'{name}.sqlite').read_bytes() for name in ('a', 'b', 'c')}
    report = case.call('c', 'show-transfer', imported, '--origin', f'{root["project"]}:{root["unit"]}', '--material')[0]
    selected_bytes = case.records[-1]['stdout_bytes']
    case.call('c', 'show', imported)
    whole_bytes = case.records[-1]['stdout_bytes']
    assert before == (case.snapshot(), {name: (case.base / f'{name}.sqlite').read_bytes() for name in ('a', 'b', 'c')})
    counts = {key: len(value) for key, value in report['material'].items()}
    assert counts == dict(units=6, support=5, bindings=6, corrections=15, links=2, predecessor_files=0)
    histories = {}
    pending = [capsule]
    while pending:
        current = pending.pop()
        previous = histories.get(current['workspace'])
        if previous is None or len(previous['events']) < len(current['events']):
            histories[current['workspace']] = current
        pending.extend(e['payload']['capsule'] for e in current['events'] if e['kind'] == 'transfer-import')
    events = {workspace: {event['id']: event for event in cap['events']} for workspace, cap in histories.items()}
    for category in ('bindings', 'corrections', 'links'):
        for row in report['material'][category]:
            assert row['event'] == events[row['workspace']][row['event']['id']]
    for row in report['material']['units']:
        assert any(event['kind'] in ('binding', 'support-review') and event['payload']['identity'] == row['identity']
                   and event['payload']['unit'] == row['file'] for event in events[row['workspace']].values())
    for row in report['material']['support']:
        assert any(event['kind'] in ('binding', 'support-review') and event['payload']['identity']['project'] == row['project']
                   and row['file'] in event['payload']['support'] for event in events[row['workspace']].values())
    expected_bindings = {'source_original_binding', 'source_successor_binding', 'local_original_binding',
                         'local_successor_binding', 'old_plan_binding', 'new_plan_binding'}
    assert {r['event']['id'] for r in report['material']['bindings']} == {case.handles[key]['id'] for key in expected_bindings}
    expected_corrections = {'source_challenge', 'source_challenge_decision', 'source_successor_proposal',
        'source_successor_decision', 'source_successor_incorporation', 'source_resolution', 'local_challenge',
        'local_challenge_decision', 'local_successor_proposal', 'local_successor_decision',
        'local_successor_incorporation', 'local_resolution', 'local_original_proposal',
        'local_original_decision', 'local_original_incorporation'}
    assert {r['event']['id'] for r in report['material']['corrections']} == {case.handles[key]['id'] for key in expected_corrections}
    assert len(report['selection_closures']) == 3
    for closure in report['selection_closures']:
        chosen = events[closure['workspace']][closure['receipt']]['payload']
        units = {item['identity']['unit']: item for item in chosen['content']['units']}
        pending_names, expected = [closure['root']['unit']], set()
        while pending_names:
            item = units[pending_names.pop()]
            if item['binding'] in expected:
                continue
            expected.add(item['binding'])
            binding = events[closure['workspace']][item['binding']]['payload']
            pending_names.extend(ref['identity']['unit'] for ref in binding['references'])
        assert closure['bindings'] == sorted(expected)
        assert closure['scope'] == chosen['scope']
    measurement = dict(whole_show_bytes=whole_bytes, capsule_bytes=capsule_path.stat().st_size,
                       selected_material_bytes=selected_bytes, categories=counts, selection_closures=3)
    assert selected_bytes < measurement['capsule_bytes'] < whole_bytes
    (case.base / 'material-measurement.json').write_text(json.dumps(measurement, sort_keys=True) + '\n')
    print(json.dumps(measurement, sort_keys=True))


def test_incorporation_remedy_preserves_original_unbound_ancestor_without_inventing_binding(tmp_path, run_cli):
    a = Workspace(tmp_path, run_cli, 'source')
    a.install('old')
    old_binding = a.bind('old')
    challenge, decision = a.challenge('old')
    middle = a.install('middle', ('old',))
    proposal, candidate = a.proposal('root', supersedes=('middle',))
    incorporation = a.incorporate(proposal, candidate, 'root')
    root_binding = a.show(incorporation)['payload']['binding']
    inspection = a.artifact('inspect', root_binding)
    resolution = a.artifact('resolve', challenge, '--decision', decision, '--incorporation', incorporation,
                            '--inspection', inspection, *ATTR)
    receipt = a.read('root')
    b = Workspace(tmp_path, run_cli, 'destination')
    imported = b.import_(a.export(receipt, 'resolved-ancestry.json'))
    report = view(b, imported, a, 'root')
    assert {row['event']['id'] for row in report['material']['bindings']} == {old_binding, root_binding}
    assert {row['identity']['unit'] for row in report['material']['units']} == {'old', 'root'}
    predecessor, = report['material']['predecessor_files']
    assert predecessor['identity'] == {'project': a.project, 'unit': 'middle'}
    assert predecessor['file']['text'] == middle.read_text()
    assert predecessor['roles'] == ['predecessor']
    assert next(row['event'] for row in report['material']['corrections'] if row['event']['id'] == resolution) == a.show(resolution)


def test_deeper_exact_selection_proves_unbound_ancestor_before_successor_verdict(tmp_path, run_cli):
    a, d, old_a, _, old_link = simple_transfer(tmp_path, run_cli)
    b = Workspace(tmp_path, run_cli, 'bridge')
    direct = b.import_(a.export(old_a, 'old-a.json'))
    indirect = b.import_(d.export(d.read('local-rule'), 'old-d.json'))
    for name, imported, source, origin in (
        ('aaa-direct', direct, a, 'rule'),
        ('zzz-indirect', indirect, d, 'local-rule'),
    ):
        proposal, candidate = b.proposal(name)
        b.link(proposal, imported, source, origin)
        b.incorporate(proposal, candidate, name)
    b.install('root')
    b.bind('root', ('aaa-direct', 'zzz-indirect'))
    historical = b.read('root')

    # A's middle has canonical proof but no declaration. Only the deeper
    # B -> D -> A selection establishes it as original receipt ancestry.
    a.install('middle', ('rule',))
    a.install('new-root', ('middle',))
    new_binding = a.bind('new-root')
    new_a = a.read('new-root')
    updated = d.import_(a.export(new_a, 'new-a.json'))
    proposal, candidate = d.proposal('new-local', supersedes=('local-rule',))
    d.artifact('detach-transfer', proposal, '--from', old_link, *ATTR)
    d.link(proposal, updated, a, 'new-root', origin_only=('middle',))
    d.incorporate(proposal, candidate, 'new-local')
    b.import_(d.export(d.read('new-local'), 'new-d.json'))
    c = Workspace(tmp_path, run_cli, 'final')
    imported = c.import_(b.export(historical, 'final-b.json'))
    before = [(tree(w.root), w.store.read_bytes()) for w in (a, b, c, d)]
    complete = view(c, imported, b, 'root')
    branch = view(c, imported, b, 'zzz-indirect')
    assert [(tree(w.root), w.store.read_bytes()) for w in (a, b, c, d)] == before
    for report in (complete, branch):
        closure = next(row for row in report['selection_closures']
                       if row['root'] == {'project': a.project, 'unit': 'new-root'})
        assert closure['receipt'] == new_a
        assert closure['bindings'] == [new_binding]
        assert closure['scope'] == {'exercise': 'dispatch'}
        middle = next(row for row in report['material']['predecessor_files']
                      if row['identity'] == {'project': a.project, 'unit': 'middle'})
        assert middle['workspace'] == closure['workspace']
        assert middle['file']['text'] == (a.root / 'knowledge/middle.md').read_text()
        assert middle['roles'] == ['predecessor']
        assert all(row['event']['payload']['identity'] != middle['identity']
                   for row in report['material']['bindings'])
    assert {row['root']['unit'] for row in complete['selection_closures']} >= {
        'root', 'rule', 'local-rule', 'new-root'}
