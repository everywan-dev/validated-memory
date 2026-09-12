"""Complete selected historical material without changing retained inspections."""

from collections import defaultdict, deque

from . import model as m, origin_history as oh, transfer_projection as tp


class Material:
    def __init__(self, state, maximum):
        self.state = state
        self.maximum = maximum
        self.budget = tp.WorkBudget()
        self.indexes = {}
        self.files = {}
        self.support = {}
        self.bindings = {}
        self.corrections = {}
        self.links = {}
        self.closures = {}
        self.contexts = {}
        self.pending = deque()
        self.visited = set()
        self.identities = set()
        self.historical_ancestors = set()
        self.successors = set()
        self.ancestry = defaultdict(set)
        self.bytes = 0

    def account(self, value):
        self.bytes += len(m.canonical(value).encode('utf-8'))
        m.require(self.bytes <= self.maximum, 'complete selected material exceeds --max-bytes')

    def index(self, workspace):
        if workspace in self.indexes:
            return self.indexes[workspace]
        owner = self.state.origins.state(workspace)
        canonical, bindings, successors, related, referring = (defaultdict(list) for _ in range(5))

        def observe(identity, file):
            if file not in canonical[m.key(identity)]:
                canonical[m.key(identity)].append(file)
            for parent in m.frontmatter(file['text']).get('supersedes', []):
                row = (identity, file)
                if row not in successors[(identity['project'], parent)]:
                    successors[(identity['project'], parent)].append(row)

        for event in owner.events.values():
            kind, p = event['kind'], event['payload']
            if kind in ('binding', 'support-review'):
                bindings[m.key(p['identity'])].append(event)
                observe(p['identity'], p['unit'])
            elif kind == 'receipt':
                for item in p['content']['units']:
                    observe(item['identity'], dict(path=item['path'], sha256=item['sha256'], text=item['text'], size=len(item['text'].encode('utf-8'))))
                for item in p.get('transfer', {}).get('lineage', []):
                    observe(item['identity'], item['file'])
            elif kind == 'transfer-link':
                for item in p['lineage']:
                    observe(item['identity'], item['file'])
            elif kind == 'conflict':
                for edge in p['lineage']:
                    for file in edge['proof']:
                        observe(dict(project=edge['after']['project'], unit=m.frontmatter(file['text'])['id']), file)
            elif kind == 'resolution' and p['remedy']['kind'] == 'incorporation':
                project = owner.event(p['remedy']['binding'])['payload']['identity']['project']
                for file in p['remedy']['proof']:
                    observe(dict(project=project, unit=m.frontmatter(file['text'])['id']), file)
            if kind == 'proposal':
                related[m.key(p['identity'])].append(event)
            elif kind == 'challenge':
                related[m.key(p['target']['identity'])].append(event)
            elif kind == 'incorporation':
                related[m.key(owner.event(p['binding'])['payload']['identity'])].append(event)
            elif kind == 'conflict':
                for candidate in p['candidates']:
                    related[m.key(candidate['identity'])].append(event)
            if kind in ('decision', 'resolution', 'choice'):
                for field in ('submission', 'challenge', 'conflict'):
                    if field in p:
                        referring[p[field]].append(event)
        result = dict(owner=owner, canonical=canonical, bindings=bindings, successors=successors,
                      related=related, referring=referring)
        self.indexes[workspace] = result
        return result

    def schedule(self, kind, workspace, value, context):
        marker = (kind, workspace, m.digest(value), context)
        if marker not in self.visited:
            self.visited.add(marker)
            self.pending.append((kind, workspace, value, context))

    def file(self, workspace, identity, file, role, context):
        self.budget.node(workspace, identity, file)
        key = (workspace, *m.key(identity), file['sha256'], file['path'])
        if key not in self.files:
            self.account(file)
            self.files[key] = dict(workspace=workspace, identity=identity, file=file, roles=[])
        roles = self.files[key]['roles']
        if role not in roles:
            roles.append(role)
            roles.sort()
        self.schedule('file', workspace, dict(identity=identity, file=file), context)

    def binding(self, workspace, event, role, context):
        key = (workspace, event['id'])
        if key not in self.bindings:
            self.account(event)
            self.bindings[key] = dict(workspace=workspace, event=event)
        p = event['payload']
        self.file(workspace, p['identity'], p['unit'], role, context)
        for file in p['support']:
            key = (workspace, p['identity']['project'], file['path'], file['sha256'])
            if key not in self.support:
                self.account(file)
                self.support[key] = dict(workspace=workspace, project=p['identity']['project'], file=file)
        self.schedule('binding', workspace, event, context)

    def exact_bindings(self, workspace, identity, sha):
        events = [e for e in self.index(workspace)['bindings'][m.key(identity)] if e['payload']['unit']['sha256'] == sha]
        m.require(events, f'{workspace}:{identity}: required canonical revision lacks retained binding/support declaration; retain its evidence and export again')
        return events

    def select(self, owner, import_id, identity, via=None):
        source, receipt, origin = oh.selected(owner, import_id, identity)
        capsule = owner.event(import_id)['payload']['capsule']
        bindings = oh.closure(source, receipt, identity)
        workspace = origin['workspace']
        key = (workspace, capsule['receipt'], *m.key(identity))
        if key not in self.closures:
            self.closures[key] = dict(workspace=workspace, receipt=capsule['receipt'], root=identity,
                                      scope=receipt['scope'], bindings=sorted(e['id'] for e in bindings), via_links=[])
            choices = {m.key(item['identity']): item['file'] for item in receipt.get('transfer', {}).get('lineage', [])}
            for item in receipt['content']['units']:
                choices[m.key(item['identity'])] = source.event(item['binding'])['payload']['unit']
            self.contexts[key] = choices
        if via and via not in self.closures[key]['via_links']:
            self.closures[key]['via_links'].append(via)
            self.closures[key]['via_links'].sort(key=lambda row: (row['workspace'], row['id']))
        pending = [(e['payload']['identity'], e['payload']['unit']) for e in bindings]
        seen = set()
        while pending:
            current, file = pending.pop()
            marker = (*m.key(current), file['sha256'], file['path'])
            if marker in seen:
                continue
            seen.add(marker)
            for parent, predecessor in self.predecessors(workspace, current, file, key):
                self.historical_ancestors.add((workspace, *m.key(parent), predecessor['sha256'], predecessor['path']))
                self.file(workspace, parent, predecessor, 'predecessor', key)
                pending.append((parent, predecessor))
        for event in bindings:
            role = 'selected' if via is None and event['payload']['identity'] == identity else ('transitive' if via else 'dependency')
            self.binding(workspace, event, role, key)
        return dict(workspace=workspace, receipt=capsule['receipt'], identity=identity,
                    unit_sha256=origin['unit_sha256'], binding=origin['binding'], scope=receipt['scope'])

    def correction(self, workspace, event, context):
        key = (workspace, event['id'])
        if key not in self.corrections:
            self.account(event)
            self.corrections[key] = dict(workspace=workspace, event=event)
        self.schedule('correction', workspace, event, context)

    def predecessors(self, workspace, identity, file, context):
        index = self.index(workspace)
        start = self.budget.node(workspace, identity, file)
        for parent in m.frontmatter(file['text']).get('supersedes', []):
            parent_identity = dict(project=identity['project'], unit=parent)
            parent_key = m.key(parent_identity)
            selected = self.contexts[context].get(parent_key)
            candidates = index['canonical'][parent_key]
            if selected:
                candidates = [f for f in candidates if f == selected]
            m.require(candidates and len({f['sha256'] for f in candidates}) == 1,
                      f'{workspace}:{parent_identity}: predecessor unavailable or ambiguous; retain exact canonical proof and export again')
            for predecessor in candidates:
                end = self.budget.node(workspace, parent_identity, predecessor)
                pending, seen = [end], set()
                while pending:
                    node = pending.pop()
                    m.require(node != start, 'cyclic exact canonical ancestry; inspect retained proof')
                    if node not in seen:
                        seen.add(node)
                        pending.extend(self.ancestry[node])
                self.ancestry[start].add(end)
                self.budget.edge('ancestry', start, end)
                yield parent_identity, predecessor

    def successor(self, workspace, identity, file, context):
        key = (workspace, *m.key(identity), file['sha256'], file['path'])
        self.successors.add(key)
        self.file(workspace, identity, file, 'successor', context)
        # Follow retained material before deciding whether a declaration is
        # required: a deeper exact selection can prove original ancestry.
        for event in self.index(workspace)['bindings'][m.key(identity)]:
            if event['payload']['unit']['sha256'] == file['sha256']:
                self.binding(workspace, event, 'successor', context)

    def process_file(self, workspace, value, context):
        identity, file = value['identity'], value['file']
        index = self.index(workspace)
        key = m.key(identity)
        for parent_identity, predecessor in self.predecessors(workspace, identity, file, context):
            self.file(workspace, parent_identity, predecessor, 'predecessor', context)
        identity_key = (workspace, *key, context)
        if identity_key not in self.identities:
            self.identities.add(identity_key)
            for event in index['bindings'][key]:
                if (workspace, event['id']) not in self.bindings:
                    self.binding(workspace, event, 'context-binding', context)
                else:
                    self.schedule('binding', workspace, event, context)
            for successor_identity, successor in index['successors'][key]:
                self.successor(workspace, successor_identity, successor, context)
            for event in index['related'][key]:
                self.correction(workspace, event, context)
        for event in tp.direct(index['owner'], identity, file).values():
            event_key = (workspace, event['id'])
            if event_key not in self.links:
                self.account(event)
                self.links[event_key] = dict(workspace=workspace, event=event)
            self.schedule('link', workspace, event, context)

    def process_binding(self, workspace, event, context):
        p = event['payload']
        start = self.budget.node(workspace, p['identity'], p['unit'])
        for reference in p['references']:
            events = self.exact_bindings(workspace, reference['identity'], reference['unit_sha256'])
            for dependency in events:
                self.budget.edge('reference', start, self.budget.node(workspace, reference['identity'], dependency['payload']['unit']))
                self.binding(workspace, dependency, 'dependency', context)

    def process_correction(self, workspace, event, context):
        index = self.index(workspace)
        for referring in index['referring'][event['id']]:
            self.correction(workspace, referring, context)
        if event['kind'] == 'resolution':
            remedy = event['payload']['remedy']
            binding = index['owner'].event(remedy['binding'], ('binding', 'support-review'))
            self.binding(workspace, binding, 'context-binding', context)
            if remedy['kind'] == 'incorporation':
                self.correction(workspace, index['owner'].event(remedy['incorporation']), context)
                project = binding['payload']['identity']['project']
                for file in remedy['proof']:
                    identity = dict(project=project, unit=m.frontmatter(file['text'])['id'])
                    self.successor(workspace, identity, file, context)

    def process_link(self, workspace, event, context):
        owner = self.index(workspace)['owner']
        p = event['payload']
        proposal = owner.event(p['proposal'])['payload']
        start = self.budget.node(workspace, proposal['identity'], proposal['unit'])
        self.budget.edge('transfer', start, (p['origin']['workspace'], *m.key(p['origin']['identity']), p['origin']['unit_sha256']))
        if p['mode'] == 'retain':
            self.select(owner, p['import'], p['origin']['identity'], dict(workspace=workspace, id=event['id']))
        else:
            inherited = owner.event(p['from'], ('transfer-link',))
            key = (workspace, inherited['id'])
            if key not in self.links:
                self.account(inherited)
                self.links[key] = dict(workspace=workspace, event=inherited)
            self.schedule('link', workspace, inherited, context)

    def collect(self):
        while self.pending:
            kind, workspace, value, context = self.pending.popleft()
            getattr(self, 'process_' + kind)(workspace, value, context)
        for key in sorted(self.successors):
            row = self.files[key]
            if key in self.historical_ancestors:
                row['roles'].remove('successor')
                if 'predecessor' not in row['roles']:
                    row['roles'].append('predecessor')
                    row['roles'].sort()
            else:
                self.exact_bindings(row['workspace'], row['identity'], row['file']['sha256'])
        selected_bindings = {(row['workspace'], handle) for row in self.closures.values() for handle in row['bindings']}
        for key, row in self.bindings.items():
            if key not in selected_bindings:
                p = row['event']['payload']
                file_key = (row['workspace'], *m.key(p['identity']), p['unit']['sha256'], p['unit']['path'])
                roles = self.files[file_key]['roles']
                if 'context-binding' not in roles:
                    roles.append('context-binding')
                    roles.sort()
        units, predecessors = [], []
        for key, row in sorted(self.files.items()):
            bound = any(e['payload']['unit']['sha256'] == row['file']['sha256'] for e in self.index(row['workspace'])['bindings'][m.key(row['identity'])])
            (units if bound else predecessors).append(row)
        return dict(units=units, predecessor_files=predecessors,
                    support=[self.support[k] for k in sorted(self.support)],
                    bindings=[self.bindings[k] for k in sorted(self.bindings, key=lambda k: (k[0], self.bindings[k]['event']['sequence']))],
                    corrections=[self.corrections[k] for k in sorted(self.corrections, key=lambda k: (k[0], self.corrections[k]['event']['sequence']))],
                    links=[self.links[k] for k in sorted(self.links, key=lambda k: (k[0], self.links[k]['event']['sequence']))])


def report(args, state):
    selected = Material(state, args.max_bytes)
    selection = selected.select(state, args.handle, args.origin)
    material = selected.collect()
    wire = m.line('show-transfer', 'historical', id=args.handle, selection=selection,
                  selection_closures=[selected.closures[k] for k in sorted(selected.closures)],
                  known_heads=[dict(workspace=k, head=state.origins.head(k)) for k in sorted(selected.indexes)],
                  material=material,
                  audit_reference_policy='Historical prior pointers, inspections, snapshots, registrations and relocations remain qualified audit references. Unselected conflict candidates retain declarations, not inspected full evidence. This is not a self-contained replay capsule.',
                  limitations=['Historical material; external freshness is not checked.',
                               'This view does not authenticate origins or establish semantic correctness.',
                               'Complete link, inspection and read protocols remain required; this view authorizes no checked use.',
                               'Successors through reached predecessors may include sibling successors.'])
    m.require(len(wire.encode('utf-8')) <= args.max_bytes, 'complete selected material exceeds --max-bytes')
    return wire
