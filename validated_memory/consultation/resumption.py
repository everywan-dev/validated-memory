"""Read-only task resumption over retained origins and the existing use check."""

from . import model as m, origin_history as oh, transfer_projection as tp, transfer_values as tv
from .command import validate_use


class Origins:
    def __init__(self, state, scope):
        self.state = state
        self.scope = scope
        self.budget = tp.WorkBudget()
        self.rows = {}
        self.failures = {}
        self.order = []
        self.visited = set()

    def failure(self, workspace, link, error):
        row = dict(workspace=workspace, link=link, diagnostic=str(error))
        self.failures[(workspace or '', link or '', str(error))] = row

    def exhausted(self):
        return len(self.budget.nodes) > 128 or len(self.budget.edges) > 512

    def roots(self, owner, declarations, tree, historical=False, context=None):
        failure_workspace, failure_link = context or (owner.workspace, None)
        pending_declarations = {m.key(d['identity']): d for d in declarations}
        ordered = []
        while pending_declarations:
            ready = sorted(key for key, d in pending_declarations.items()
                           if all(m.key(r['identity']) not in pending_declarations for r in d['references']))
            if not ready:
                self.failure(failure_workspace, failure_link, 'dependency ordering unavailable; retained reference cycle')
                ready = sorted(pending_declarations)
            ordered.extend(pending_declarations.pop(key) for key in ready)
        for declaration in ordered:
            if self.exhausted():
                return
            identity = declaration['identity']
            try:
                start = self.budget.node(owner.workspace, identity, declaration['unit'])
                for reference in declaration['references']:
                    ref_key = m.key(reference['identity'])
                    m.require(ref_key in tree, 'referenced canonical proof unavailable; retain complete lineage')
                    m.require(tree[ref_key]['sha256'] == reference['unit_sha256'], 'referenced canonical revision changed; inspect retained declarations')
                    self.budget.edge('reference', start, self.budget.node(owner.workspace, reference['identity'], tree[ref_key]))
                effective = tp.effective(owner, identity, tree, budget=self.budget)
                active = {event['id'] for events in effective.values() for event in events.values()}
            except m.Refusal as error:
                self.failure(failure_workspace, failure_link, error)
                active = None
            pending, seen = [identity], set()
            while pending and not self.exhausted():
                current = pending.pop()
                key = m.key(current)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    m.require(key in tree, 'canonical ancestor unavailable; retain complete lineage')
                    file = tree[key]
                    self.budget.node(owner.workspace, current, file)
                    for event in tp.direct(owner, current, file).values():
                        self.link(owner, event, historical or (active is not None and event['id'] not in active))
                    pending.extend(dict(project=current['project'], unit=name)
                                   for name in reversed(m.frontmatter(file['text']).get('supersedes', [])))
                except m.Refusal as error:
                    self.failure(failure_workspace, failure_link, error)

    def link(self, owner, event, historical):
        key = (owner.workspace, event['id'])
        if (key, historical) in self.visited or self.exhausted():
            return
        self.visited.add((key, historical))
        p = event['payload']
        origin = p['origin']
        row = self.rows.get(key)
        if row is None:
            row = dict(workspace=origin['workspace'], identity=origin['identity'],
                       unit_sha256=origin['unit_sha256'], link=dict(workspace=key[0], id=key[1]),
                       disposition=p['mode'], membership='historical' if historical else 'undetermined',
                       reviewed_head=next((r['head'] for r in p['observed']['origins']
                                           if r['workspace'] == origin['workspace']), None),
                       known_head=None, statuses=['historical'], live_origin='not-checked')
            self.rows[key] = row
        elif not historical:
            row['membership'] = 'undetermined'
        try:
            latest = self.state.origins.state(origin['workspace'])
            row['known_head'] = self.state.origins.head(origin['workspace'])
            _, _, bindings = tp.material_source(owner, p)
            statuses = oh.source_statuses(latest, bindings, self.scope)
            if p['mode'] == 'retain' and any(self.state.origins.head(r['workspace']) != r['head']
                                            for r in p['observed']['origins']):
                statuses.append('origin-review-stale')
            row['statuses'] = [status for status in tv.STATUS if status in statuses] or ['historical']
            proposal = owner.event(p['proposal'])['payload']
            start = self.budget.node(owner.workspace, proposal['identity'], proposal['unit'])
            end = (origin['workspace'], *m.key(origin['identity']), origin['unit_sha256'])
            self.budget.edge('transfer', start, end)
            tree = tp.retained_tree(latest)
            declarations = [b['payload'] for b in bindings]
            for declaration in declarations:
                tree[m.key(declaration['identity'])] = declaration['unit']
            self.roots(latest, declarations, tree, historical or p['mode'] == 'independent', (owner.workspace, event['id']))
        except m.Refusal as error:
            self.failure(owner.workspace, event['id'], error)
        if key not in self.order:
            self.order.append(key)


def report(args, state):
    use = state.event(args.handle, ('use',))['payload']
    receipt = state.event(use['receipt'], ('receipt',))['payload']
    origins = Origins(state, args.scope)
    tree = tp.retained_tree(state, receipt.get('transfer', {}).get('lineage', []))
    declarations = []
    for item in receipt['content']['units']:
        declaration = state.event(item['binding'], ('binding', 'support-review'))['payload']
        declarations.append(declaration)
        tree[m.key(item['identity'])] = declaration['unit']
    origins.roots(state, declarations, tree)
    check = dict(status='current', diagnostic=None)
    try:
        validate_use(state, use['root'], receipt)
    except (m.Refusal, OSError) as error:
        check = dict(status='blocked', diagnostic=str(error))
    matches = args.scope == receipt['scope']
    complete = not origins.failures
    if matches and check['status'] == 'current' and complete:
        for row in origins.rows.values():
            if row['membership'] != 'historical':
                row['membership'] = 'current'
    actions = []
    for key in origins.order:
        row = origins.rows[key]
        if row['statuses'] != ['historical'] and row['disposition'] == 'retain' and row['membership'] != 'historical':
            diagnostic = ', '.join(row['statuses'])
            if not complete:
                diagnostic += '; dependency ordering unavailable because retained analysis is incomplete'
            actions.append(dict(action='inspect-origin', links=[row['link']], requires_semantic_judgment=True,
                                diagnostic=diagnostic))
    if not matches:
        actions.append(dict(action='read-for-requested-scope', links=[], requires_semantic_judgment=True,
                            diagnostic='Inspect a complete consumer read at the requested scope and record a new use.'))
    if check['status'] != 'current' or not complete:
        actions.append(dict(action='inspect-and-reconsult', links=[], requires_semantic_judgment=True,
                            diagnostic=check['diagnostic'] or 'Inspect unavailable retained ancestry before reconsulting.'))
    current = matches and check['status'] == 'current' and complete
    root = next(item for item in receipt['content']['units'] if item['identity'] == use['root'])
    wire = m.line('resume-use', 'current' if current else 'blocked', workspace=state.workspace, id=args.handle,
                  receipt=use['receipt'], root=dict(identity=use['root'], unit_sha256=root['sha256']),
                  scope=dict(historical=receipt['scope'], requested=args.scope, matches=matches), historical_check=check,
                  origin_analysis=dict(complete=complete, failures=[origins.failures[k] for k in sorted(origins.failures)], scope=args.scope),
                  origins=[origins.rows[k] for k in sorted(origins.rows)], actions=actions,
                  limitations=['External freshness is not checked; supplied history does not prove live origin freshness.',
                               'This report is not a consultation receipt and does not attest that all update routes were checked.',
                               'Subsequent operations revalidate their inputs.'])
    m.require(len(wire.encode('utf-8')) <= args.max_bytes, 'complete resumption report exceeds --max-bytes')
    return wire, 0 if current else 1
