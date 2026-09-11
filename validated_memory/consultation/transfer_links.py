"""Exact correspondence validation and retained source review material."""

from . import model as m, transfer_values as tv, lifecycle_state as ls
from . import origin_history as oh, transfer_projection as tp


def source_material(state, p, registry, siblings=(), budget=None):
    budget = budget or tp.WorkBudget()
    pending = [(state, p), *[(state, state.event(row['link'])['payload']) for row in siblings]]
    seen = set()
    units, supports, bindings_out, predecessors, corrections = {}, {}, {}, {}, {}

    def retain(target, key, value):
        m.require(key not in target or target[key] == value, 'conflicting qualified source material revisions')
        target[key] = value

    while pending:
        owner, payload = pending.pop()
        marker = (owner.workspace, m.digest(payload))
        if marker in seen:
            continue
        seen.add(marker)
        source, receipt, bindings = tp.material_source(owner, payload)
        proposal = owner.event(payload['proposal'])['payload']
        local_node = budget.node(owner.workspace, proposal['identity'], proposal['unit'])
        budget.edge('transfer', local_node, (payload['origin']['workspace'], *m.key(payload['origin']['identity']), payload['origin']['unit_sha256']))
        latest = registry.state(payload['origin']['workspace'])
        workspace = latest.workspace
        identities = {m.key(b['payload']['identity']) for b in bindings}
        tree = tp.retained_tree(latest)
        for binding in bindings:
            value = binding['payload']
            identity = value['identity']
            key = (workspace, *m.key(identity))
            budget.node(workspace, identity, value['unit'])
            retain(units, key, dict(workspace=workspace, identity=identity, file=value['unit']))
            bindings_out[(workspace, binding['sequence'])] = dict(workspace=workspace, event=binding)
            tree[m.key(identity)] = value['unit']
            for file in value['support']:
                retain(supports, (workspace, identity['project'], file['path']), dict(workspace=workspace, project=identity['project'], **file))
            for name in m.frontmatter(value['unit']['text']).get('supersedes', []):
                predecessor = dict(project=identity['project'], unit=name)
                retain(predecessors, (workspace, *m.key(predecessor)), dict(workspace=workspace, identity=predecessor, file=oh.predecessor(latest, predecessor)))
        for binding in bindings:
            bp = binding['payload']
            start = budget.node(workspace, bp['identity'], bp['unit'])
            for reference in bp['references']:
                target = tree[m.key(reference['identity'])]
                budget.edge('reference', start, budget.node(workspace, reference['identity'], target))
            for events in tp.effective(latest, bp['identity'], tree, budget=budget).values():
                for event in events.values():
                    corrections[(workspace, event['sequence'])] = dict(workspace=workspace, event=event)
                    if event['payload']['mode'] == 'retain':
                        pending.append((latest, event['payload']))
        related = set()
        for event in latest.events.values():
            kind, value = event['kind'], event['payload']
            include = False
            if kind == 'challenge':
                include = m.key(value['target']['identity']) in identities
            elif kind == 'proposal':
                include = m.key(value['identity']) in identities
            elif kind == 'incorporation':
                include = m.key(latest.event(value['binding'])['payload']['identity']) in identities
            elif kind == 'conflict':
                include = any(m.key(c['identity']) in identities for c in value['candidates'])
            elif kind in ('decision', 'resolution', 'choice'):
                include = any(value.get(field) in related for field in ('submission', 'challenge', 'conflict'))
            if include:
                corrections[(workspace, event['sequence'])] = dict(workspace=workspace, event=event)
                related.add(event['id'])
    return dict(units=[units[k] for k in sorted(units)], support=[supports[k] for k in sorted(supports)],
                bindings=[bindings_out[k] for k in sorted(bindings_out)],
                predecessor_files=[predecessors[k] for k in sorted(predecessors)],
                corrections=[corrections[k] for k in sorted(corrections)])


def inherited(state, proposal, tree, budget=None):
    result = {}
    for name in m.frontmatter(proposal['unit']['text']).get('supersedes', []):
        identity = dict(project=proposal['identity']['project'], unit=name)
        for events in tp.effective(state, identity, tree, budget=budget).values():
            for event in events.values():
                result[event['id']] = dict(workspace=state.workspace, link=event['id'], origin=event['payload']['origin'])
    return [result[key] for key in sorted(result)]


def active_counterparts(state, origin, tree, proposal):
    # The virtual insertion must not retire a counterpart before it is mapped.
    before = dict(tree)
    if proposal['installed_binding'] is None:
        before.pop(m.key(proposal['identity']), None)
    retired = {(key[0], name) for key, file in before.items() for name in m.frontmatter(file['text']).get('supersedes', [])}
    result = {}
    for key, file in before.items():
        if key[0] != proposal['identity']['project'] or key in retired:
            continue
        obligations = tp.effective(state, dict(project=key[0], unit=key[1]), before)
        for event in obligations.get(origin, {}).values():
            if event['payload']['mode'] == 'retain':
                result[key] = file
    return result


def validate(state, p, tree, registry=None):
    registry = registry or state.origins
    proposal = state.event(p['proposal'], ('proposal',))['payload']
    roots = [proposal['identity']]
    for row in p['dependencies']:
        roots.append(row['local'])
        binding = ls.incorporation_binding(state, row['incorporation'])['payload']
        tree[m.key(row['local'])] = binding['unit']
    tree[m.key(proposal['identity'])] = proposal['unit']
    budget = tp.WorkBudget()
    expected_lineage = tp.ancestors(tree, roots, state.workspace, budget)
    local_node = budget.node(state.workspace, proposal['identity'], proposal['unit'])
    for reference in proposal['references']:
        budget.edge('reference', local_node, budget.node(state.workspace, reference['identity'], tree[m.key(reference['identity'])]))
    m.require(expected_lineage == p['lineage'], 'transfer lineage is not complete canonical ancestry')
    inventories = {item['project']: item for item in proposal['expected']['projects']}
    for item in p['lineage']:
        inv = inventories.get(item['identity']['project'])
        m.require(inv and m.revision(item['file']) in inv['knowledge'], 'transfer lineage differs from proposal inventory; renew proposal')
    inherited_rows = inherited(state, proposal, tree, budget)
    material = source_material(state, p, registry, inherited_rows if p['mode'] == 'independent' else (), budget)
    source, receipt, bindings = tp.material_source(state, p)
    upstream = tp.Projection(state, registry, budget=budget)
    if p['mode'] == 'retain':
        m.require(proposal['scope'] == receipt['scope'], 'scope differs from selected origin receipt')
        root = next(b['payload'] for b in bindings if b['payload']['identity'] == p['origin']['identity'])
        m.require({f['sha256'] for f in root['support']} <= {f['sha256'] for f in proposal['support']}, 'local support omits source evidence hashes')
        refs = {m.key(r['identity']): r for r in root['references']}
        m.require(set(refs) == {m.key(row['origin']) for row in p['dependencies']}, 'every immediate origin dependency requires one explicit mapping')
        for row in p['dependencies']:
            m.require(any(ref['identity'] == row['local'] and ref['unit_sha256'] == row['unit_sha256'] for ref in proposal['references']), 'proposal references omit mapped local revision')
            binding = ls.incorporation_binding(state, row['incorporation'])['payload']
            m.require(binding['identity'] == row['local'] and binding['unit']['sha256'] == row['unit_sha256'], 'mapped incorporation revision mismatch')
            links = tp.direct(state, row['local'], binding['unit'])
            key = (p['origin']['workspace'], *m.key(row['origin']))
            linked = links.get(key)
            m.require(linked and linked['payload']['mode'] == 'retain' and linked['payload']['origin']['unit_sha256'] == refs[m.key(row['origin'])]['unit_sha256'],
                      'mapped dependency lacks exact retained origin revision')
        direct_predecessors = set(m.frontmatter(root['unit']['text']).get('supersedes', []))
        predecessors = {m.key(item['identity']): item['file'] for item in material['predecessor_files']
                        if item['workspace'] == p['origin']['workspace'] and item['identity']['project'] == root['identity']['project']
                        and item['identity']['unit'] in direct_predecessors}
        m.require(set(predecessors) == {m.key(row['origin']) for row in p['predecessors']}, 'every origin predecessor requires an explicit disposition')
        for origin_key, file in predecessors.items():
            rows = [row for row in p['predecessors'] if m.key(row['origin']) == origin_key]
            m.require(all(row['unit_sha256'] == file['sha256'] for row in rows), 'predecessor origin revision mismatch')
            counterparts = active_counterparts(state, (p['origin']['workspace'], *origin_key), tree, proposal)
            mapped = {m.key(row['local']) for row in rows if row['mode'] == 'mapped'}
            if any(row['mode'] == 'origin-only' for row in rows):
                m.require(len(rows) == 1 and not counterparts, 'origin-only predecessor has active local counterparts')
            else:
                m.require(set(counterparts) <= mapped, 'map every active retaining local predecessor')
            for row in rows:
                if row['mode'] != 'mapped':
                    continue
                local = next((item for item in proposal['predecessors'] if item['identity'] == row['local'] and item['unit']['sha256'] == row['local_sha256']), None)
                m.require(local, 'proposal supersedes/predecessors omit mapped local revision')
                obligations = tp.effective(state, row['local'], tree)
                events = obligations.get((p['origin']['workspace'], *origin_key), {})
                m.require(any(e['payload']['mode'] == 'retain' and e['payload']['origin']['unit_sha256'] == file['sha256'] for e in events.values()),
                          'local predecessor lacks exact retained origin revision')
        upstream.upstream(state, p)
    else:
        ancestor_link = state.event(p['from'], ('transfer-link',))
        m.require(p['origin'] == ancestor_link['payload']['origin'], 'independence changed inherited origin identity')
        ancestor = state.event(ancestor_link['payload']['proposal'])['payload']
        m.require(any(item['identity'] == ancestor['identity'] and item['file'] == ancestor['unit'] for item in p['lineage']),
                  'independence requires strict canonical successor, not identical revision')
        m.require(not p['dependencies'] and not p['predecessors'], 'independence has no continuing mapping')
        # Freeze known upstream context, even when the inherited claim is blocked.
        upstream.origin_heads[p['origin']['workspace']] = registry.head(p['origin']['workspace'])
        for row in ancestor_link['payload']['observed']['origins']:
            upstream.origin_heads[row['workspace']] = registry.head(row['workspace'])
        for row in ancestor_link['payload']['observed']['links']:
            upstream.links[(row['workspace'], row['link'])] = None
    m.require(upstream.frontier() == p['observed'], 'origin-review-stale: observed upstream context changed')
    review = {key: p[key] for key in ('mode', 'proposal', 'origin', 'import', 'from', 'dependencies', 'predecessors', 'observed', 'actor', 'reason')}
    review.update(material=material, inherited=dict(links=inherited_rows, lineage=p['lineage']), limitation=tv.LIMITATION)
    operation = 'link-transfer' if p['mode'] == 'retain' else 'detach-transfer'
    return m.line(operation, 'inspected', review=review)


def add(state, event):
    p = event['payload']
    if event['kind'] == 'transfer-import':
        m.require(event['prior'] is None, 'transfer import cannot have prior')
        state.origins.ingest(p['capsule'])
        return
    key = tv.link_key(state, p)
    head = state.transfer_links.get(key)
    m.require(event['prior'] == (head['id'] if head else None), 'transfer link prior is not current head')
    m.require(not head or tv.semantic(head['payload']) != tv.semantic(p), 'redundant transfer link')
    if not any(e['kind'] == 'transfer-link' and e['payload']['proposal'] == p['proposal'] for e in state.events.values()):
        m.require(not any(e['kind'] == 'decision' and e['payload']['submission'] == p['proposal'] and e['payload']['outcome'] == 'accept' for e in state.events.values()),
                  'first transfer link must precede proposal acceptance; renew proposal')
    tree = tp.retained_tree(state, p['lineage'])
    wire = validate(state, p, tree)
    m.require(wire == p['inspection_text'], 'transfer inspection differs from exact material/mapping')
    final = m.line('link-transfer' if p['mode'] == 'retain' else 'detach-transfer', 'recorded', id=event['id'])
    m.require(len((wire + final).encode('utf-8')) <= p['max_bytes'], 'transfer inspection exceeds bound')
    state.transfer_links[key] = event
