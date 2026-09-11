"""One bounded projection for live and chronological transfer obligations."""

from . import model as m, lifecycle_state as ls, transfer_values as tv
from . import origin_history as oh


def tree_from_projects(projects):
    return {(project, unit): observed['file'] for project, value in projects.items() for unit, observed in value['units'].items()}


def retained_tree(state, extra=()):
    tree = oh.known_tree(state)
    for event in state.events.values():
        if event['kind'] == 'transfer-link':
            for item in event['payload']['lineage']:
                tree[m.key(item['identity'])] = item['file']
    for item in extra:
        tree[m.key(item['identity'])] = item['file']
    return tree


class WorkBudget:
    def __init__(self):
        self.nodes = set()
        self.edges = set()

    def node(self, workspace, identity, file):
        key = (workspace, *m.key(identity), file['sha256'])
        self.nodes.add(key)
        m.require(len(self.nodes) <= 128, 'transfer provenance exceeds 128 nodes')
        return key

    def edge(self, kind, start, end):
        self.edges.add((kind, start, end))
        m.require(len(self.edges) <= 512, 'transfer provenance exceeds 512 edges')


def ancestors(tree, roots, workspace=None, budget=None):
    budget = budget or WorkBudget()
    found, active = {}, set()
    edges = 0
    root_keys = {m.key(identity) for identity in roots}

    def visit(key):
        nonlocal edges
        m.require(key not in active, 'canonical ancestry cycle; inspect supersession')
        if key in found:
            return
        m.require(key in tree, 'canonical ancestor unavailable; retain complete lineage')
        m.require(len(found) < 128, 'canonical ancestry exceeds 128 units')
        active.add(key)
        found[key] = tree[key]
        node = budget.node(workspace, dict(project=key[0], unit=key[1]), tree[key])
        for parent in m.frontmatter(tree[key]['text']).get('supersedes', []):
            edges += 1
            m.require(edges <= 512, 'canonical ancestry exceeds 512 edges')
            parent_key = (key[0], parent)
            visit(parent_key)
            budget.edge('ancestry', node, budget.node(workspace, dict(project=key[0], unit=parent), tree[parent_key]))
        active.remove(key)
    for key in sorted(root_keys):
        visit(key)
    return [dict(identity=dict(project=key[0], unit=key[1]), file=found[key]) for key in sorted(found) if key not in root_keys]


def direct(state, identity, file):
    prefix = (*m.key(identity), file['sha256'])
    return {tv.origin_key(e['payload']['origin']): e for key, e in state.transfer_links.items() if key[:3] == prefix}


def effective(state, identity, tree, active=None, memo=None, budget=None):
    """Union retaining siblings; a direct disposition overrides only its origin."""
    active = set() if active is None else active
    memo = {} if memo is None else memo
    budget = WorkBudget() if budget is None else budget
    key = m.key(identity)
    m.require(key in tree and key not in active, 'canonical ancestry unavailable or cyclic')
    if key in memo:
        return {origin: dict(events) for origin, events in memo[key].items()}
    parents = m.frontmatter(tree[key]['text']).get('supersedes', [])
    node = budget.node(state.workspace, identity, tree[key])
    active.add(key)
    inherited = {}
    heads = direct(state, identity, tree[key])
    for parent in parents:
        parent_identity = dict(project=key[0], unit=parent)
        branch = effective(state, parent_identity, tree, active, memo, budget)
        budget.edge('ancestry', node, budget.node(state.workspace, parent_identity, tree[m.key(parent_identity)]))
        for replacement in heads.values():
            rp = replacement['payload']
            if rp['mode'] != 'retain':
                continue
            for mapping in rp['predecessors']:
                if mapping['mode'] != 'mapped' or mapping['local'] != parent_identity or mapping['local_sha256'] != tree[m.key(parent_identity)]['sha256']:
                    continue
                old_origin = (rp['origin']['workspace'], *m.key(mapping['origin']))
                survivors = {handle: event for handle, event in branch.get(old_origin, {}).items()
                             if event['payload']['mode'] != 'retain' or event['payload']['origin']['unit_sha256'] != mapping['unit_sha256']}
                if survivors:
                    branch[old_origin] = survivors
                else:
                    branch.pop(old_origin, None)
        for origin, events in branch.items():
            inherited.setdefault(origin, {}).update(events)
    for origin, event in heads.items():
        inherited[origin] = {event['id']: event}
    active.remove(key)
    memo[key] = inherited
    return {origin: dict(events) for origin, events in inherited.items()}


def material_source(state, payload):
    depth = 0
    while payload['mode'] == 'independent':
        depth += 1
        m.require(depth <= 128, 'source inspection provenance bound exceeded')
        payload = state.event(payload['from'], ('transfer-link',))['payload']
    source, receipt, origin = oh.selected(state, payload['import'], payload['origin']['identity'])
    m.require(origin == payload['origin'], 'transfer origin differs from selected receipt')
    return source, receipt, oh.closure(source, receipt, origin['identity'])


def local_incorporation(state, identity, file, links, declaration=None, pending=None, phase='use', inspection=None):
    """A renewed proposal may cover all effective links of one canonical revision."""
    def accepted_proposal(proposal_id, decision):
        p = state.event(proposal_id, ('proposal',))['payload']
        if p['identity'] != identity or p['unit'] != file:
            return False
        try:
            ls.accepted(state, proposal_id, decision)
            inspected = state.event(state.event(decision)['payload']['inspection'], ('inspection',))
            return all(inspected['sequence'] > e['sequence'] for e in links)
        except m.Refusal:
            return False

    if pending:
        p = state.event(pending, ('proposal',))['payload']
        if p['identity'] == identity and p['unit'] == file:
            if phase == 'accept':
                inspected = state.event(inspection, ('inspection',))
                m.require(inspected['payload']['submission'] == pending and all(inspected['sequence'] > e['sequence'] for e in links),
                          'local-incorporation-pending: inspect proposal after every effective transfer link')
                return None
            if phase == 'incorporate':
                decision = state.decisions.get(pending)
                m.require(decision and accepted_proposal(pending, decision['id']), 'local-incorporation-pending: current post-link acceptance required')
                return None
    candidates = []
    for event in state.events.values():
        if event['kind'] == 'incorporation':
            binding = state.event(event['payload']['binding'])['payload']
            if binding['identity'] == identity and binding['unit'] == file:
                candidates.append(event)
    m.require(candidates, 'local-incorporation-pending: accept and incorporate linked proposal')
    latest = candidates[-1]
    m.require(accepted_proposal(latest['payload']['proposal'], latest['payload']['decision']),
              'local-incorporation-pending: current acceptance must follow all links')
    binding = ls.incorporation_binding(state, latest['id'])
    if declaration is not None:
        m.require(binding['payload']['unit'] == declaration['unit'], 'local-incorporation-pending: incorporated revision changed')
    return latest


class Projection:
    def __init__(self, state, registry=None, pending=None, phase='use', inspection=None, budget=None):
        self.state = state
        self.registry = registry or state.origins
        self.pending = pending
        self.phase = phase
        self.inspection = inspection
        self.origin_heads = {}
        self.links = {}
        self.rows = {}
        self.visiting = set()
        self.nodes = set()
        self.budget = budget or WorkBudget()

    def frontier(self):
        return dict(origins=[dict(workspace=k, head=self.origin_heads[k]) for k in sorted(self.origin_heads)],
                    links=[dict(workspace=k[0], link=k[1]) for k in sorted(self.links)])

    def merge(self, other):
        self.origin_heads.update(other.origin_heads)
        self.links.update(other.links)
        self.rows.update(other.rows)
        self.nodes.update(other.nodes)

    def upstream(self, owner, p, scope=None):
        scope = owner.event(p['proposal'])['payload']['scope'] if scope is None else scope
        source, receipt, bindings = material_source(owner, p)
        latest = self.registry.state(p['origin']['workspace'])
        self.origin_heads[latest.workspace] = self.registry.head(latest.workspace)
        statuses = oh.source_statuses(latest, bindings, scope)
        m.require(not statuses, f"{p['origin']['identity']}: " + ', '.join(statuses) + '; inspect latest origin and review dependency-first')
        tree = retained_tree(latest)
        for binding in bindings:
            bp = binding['payload']
            tree[m.key(bp['identity'])] = bp['unit']
        for binding in bindings:
            self.visit(latest, binding['payload']['identity'], tree, scope, binding['payload'])
        for dependency in p['dependencies']:
            original = owner.event(dependency['incorporation'], ('incorporation',))
            bp = ls.incorporation_binding(owner, original['id'])['payload']
            m.require(bp['identity'] == dependency['local'] and bp['unit']['sha256'] == dependency['unit_sha256'], 'mapped dependency revision changed')
            tree = retained_tree(owner)
            tree[m.key(bp['identity'])] = bp['unit']
            self.visit(owner, bp['identity'], tree, scope, bp)
        return source, receipt, bindings

    def visit(self, owner, identity, tree, scope, declaration=None):
        key = (owner.workspace, *m.key(identity), tree[m.key(identity)]['sha256'])
        m.require(key not in self.visiting, 'transfer provenance cycle; inspect origin mappings')
        if key in self.nodes:
            return
        m.require(len(self.nodes) < 128, 'transfer provenance exceeds 128 units')
        self.nodes.add(key)
        self.visiting.add(key)
        obligations = effective(owner, identity, tree, budget=self.budget)
        if declaration is not None:
            for reference in declaration['references']:
                target = m.key(reference['identity'])
                m.require(target in tree, 'referenced canonical proof unavailable; retain a reviewed binding or supported canonical lineage, then retry')
                m.require(tree[target]['sha256'] == reference['unit_sha256'],
                          'referenced canonical revision changed; review the declaration and retry')
                self.budget.edge('reference', key, self.budget.node(owner.workspace, reference['identity'], tree[target]))
                binding = owner.bindings.get(target)
                m.require(binding is None or binding['payload']['unit']['sha256'] == reference['unit_sha256'],
                          'referenced binding revision differs; review the declaration and retry')
                dependency = binding['payload'] if binding else None
                self.visit(owner, reference['identity'], tree, scope, dependency)
        direct_heads = direct(owner, identity, tree[m.key(identity)])
        for origin, events in sorted(obligations.items()):
            retaining = [e for e in events.values() if e['payload']['mode'] == 'retain']
            m.require(not retaining or origin in direct_heads, 'origin-review-stale: local successor needs explicit retain or independent disposition')
            for event in sorted(events.values(), key=lambda e: e['id']):
                p = event['payload']
                link_proposal = owner.event(p['proposal'], ('proposal',))['payload']
                local_identity = link_proposal['identity']
                local_file = link_proposal['unit']
                local_node = self.budget.node(owner.workspace, local_identity, local_file)
                origin_node = (p['origin']['workspace'], *m.key(p['origin']['identity']), p['origin']['unit_sha256'])
                self.budget.edge('transfer', local_node, origin_node)
                active_links = list(direct(owner, local_identity, local_file).values())
                local_incorporation(owner, local_identity, local_file, active_links, declaration,
                                    self.pending if owner is self.state else None, self.phase, self.inspection)
                self.links[(owner.workspace, event['id'])] = event
                source, receipt, bindings = material_source(owner, p)
                if p['mode'] == 'retain':
                    sub = Projection(self.state, self.registry, self.pending, self.phase, self.inspection, self.budget)
                    sub.visiting = set(self.visiting)
                    sub.upstream(owner, p, scope)
                    m.require(sub.frontier() == p['observed'], 'origin-review-stale: link reviewed older upstream heads; re-link then inspect and accept')
                    self.merge(sub)
                    bp = declaration if local_identity == identity and declaration is not None else owner.binding(local_identity)['payload']
                    required = {f['sha256'] for f in bindings[0]['payload']['support']} if len(bindings) == 1 else {
                        f['sha256'] for b in bindings if b['payload']['identity'] == p['origin']['identity'] for f in b['payload']['support']}
                    m.require(required <= {f['sha256'] for f in bp['support']}, 'binding-changed: local support review dropped mapped evidence')
                    m.require(all(any(r['identity'] == d['local'] and r['unit_sha256'] == d['unit_sha256'] for r in bp['references']) for d in p['dependencies']),
                              'binding-changed: local support review dropped mapped dependency')
                    head = self.registry.head(p['origin']['workspace'])
                else:
                    head = next(row['head'] for row in p['observed']['origins'] if row['workspace'] == p['origin']['workspace'])
                row = dict(local=dict(workspace=owner.workspace, identity=local_identity), mode=p['mode'], origin=p['origin'],
                           link=dict(workspace=owner.workspace, id=event['id']), head=head, scope=receipt['scope'],
                           evidence_sha256=sorted({f['sha256'] for b in bindings if b['payload']['identity'] == p['origin']['identity'] for f in b['payload']['support']}),
                           actor=p['actor'], reason=p['reason'], live_origin='not-checked')
                row_key = (owner.workspace, *m.key(local_identity), *origin, event['id'])
                self.rows[row_key] = row
        self.visiting.remove(key)


def project(state, content, scope, tree, pending=None, phase='use', inspection=None, registry=None):
    roots = [item['identity'] for item in content['units']]
    budget = WorkBudget()
    lineage = ancestors(tree, roots, state.workspace, budget)
    projection = Projection(state, registry, pending, phase, inspection, budget)
    for item in content['units']:
        if pending and state.event(pending)['payload']['identity'] == item['identity']:
            declaration = state.event(pending)['payload']
        else:
            declaration = state.event(item['binding'], ('binding', 'support-review'))['payload']
        projection.visit(state, item['identity'], tree, scope, declaration)
    result = dict(lineage=lineage, frontier=projection.frontier(), origins=[projection.rows[k] for k in sorted(projection.rows)])
    tv.transfer(result)
    return result


def check_receipt(state, receipt, projects=None):
    if state.version < 3:
        return
    if receipt['version'] < 3 and projects is None and not state.transfer_links:
        return
    for item in receipt.get('transfer', {}).get('lineage', []):
        inv = next((v for v in receipt['snapshot']['projects'] if v['project'] == item['identity']['project']), None)
        m.require(inv and m.revision(item['file']) in inv['knowledge'], 'receipt lineage revision missing from snapshot')
    tree = tree_from_projects(projects) if projects is not None else retained_tree(state, receipt.get('transfer', {}).get('lineage', []))
    for item in receipt['content']['units']:
        tree[m.key(item['identity'])] = dict(path=item['path'], sha256=item['sha256'], text=item['text'], size=len(item['text'].encode('utf-8')))
    try:
        result = project(state, receipt['content'], receipt['scope'], tree)
    except m.Refusal as error:
        if receipt['version'] < 3 and projects is None and 'ancestor' in str(error):
            raise m.Refusal('reconsult to obtain receipt3 lineage before recording a new assertion over legacy history') from error
        raise
    retained = receipt.get('transfer', dict(lineage=[], frontier=dict(origins=[], links=[]), origins=[]))
    if receipt['version'] == 3:
        m.require(result == retained, 'origin-review-stale: transfer frontier or ancestry changed; reconsult after review')
    else:
        m.require(not result['frontier']['origins'] and not result['frontier']['links'], 'origin-review-stale: old receipt lacks current transfer obligations')


def proposal_gate(state, proposal_id, phase, inspection=None, projects=None):
    if state.version < 3:
        return
    p = state.event(proposal_id, ('proposal',))['payload']
    tree = tree_from_projects(projects) if projects is not None else retained_tree(state)
    tree[m.key(p['identity'])] = p['unit']
    for item in p['predecessors']:
        tree[m.key(item['identity'])] = item['unit']
    # Do not alter ordinary unlinked E1 behavior when no ancestry is retained.
    if not state.transfer_links:
        return
    units = [dict(identity=p['identity'], binding='0' * 64)]
    project(state, dict(units=units), p['scope'], tree, proposal_id, phase, inspection)
    if projects is not None:
        proposal_gate(state, proposal_id, phase, inspection)


def binding_gate(state, binding, scope, projects=None):
    if state.version < 3 or not state.transfer_links:
        return
    payload = binding['payload']
    tree = tree_from_projects(projects) if projects is not None else retained_tree(state)
    tree[m.key(payload['identity'])] = payload['unit']
    project(state, dict(units=[dict(identity=payload['identity'], binding=binding['id'])]), scope, tree)
    if projects is not None:
        binding_gate(state, binding, scope)
