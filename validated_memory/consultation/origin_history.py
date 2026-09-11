"""Bounded foreign history replay, separate from destination operational state."""

from . import model as m, transfer_values as tv


class Registry:
    def __init__(self, owner, cache=None):
        self.owner = owner
        self.capsules = {}
        self.states = {}
        self.cache = cache if cache is not None else {}

    def preflight(self, capsule):
        prospective = dict(self.capsules)
        pending = [(capsule, 1, (self.owner,))]
        packages = []
        while pending:
            item, depth, ancestors = pending.pop()
            m.require(depth <= 4, 'transfer nesting exceeds 4 origin levels')
            tv.capsule(item)
            workspace = item['workspace']
            m.require(workspace not in ancestors, 'self/cyclic workspace import refused')
            existing = prospective.get(workspace)
            if existing:
                a, b = existing['events'], item['events']
                m.require(a[:min(len(a), len(b))] == b[:min(len(a), len(b))], 'origin history fork; inspect both histories without choosing a branch')
            if not existing or len(item['events']) > len(existing['events']):
                prospective[workspace] = item
            m.require(len(prospective) <= 16 and sum(len(c['events']) for c in prospective.values()) <= 10000,
                      'origin registry exceeds 16 workspaces/10000 distinct rows')
            packages.append(item)
            for event in item['events']:
                if event.get('kind') == 'transfer-import':
                    p = event.get('payload')
                    m.require(type(p) is dict and 'capsule' in p, 'invalid nested import')
                    pending.append((p['capsule'], depth + 1, (*ancestors, workspace)))
        return prospective, packages

    def ingest(self, capsule):
        prospective, packages = self.preflight(capsule)
        for item in reversed(packages):
            self.validate(item)
        self.capsules = prospective
        self.states = {workspace: self.validate(item) for workspace, item in prospective.items()}

    def validate(self, capsule):
        key = (capsule['workspace'], capsule['storage_version'], capsule['source_store_path'], m.digest(capsule['events']))
        if key not in self.cache:
            from .state import State
            state = State(capsule['workspace'], capsule['source_store_path'], capsule['storage_version'], origin_cache=self.cache)
            for event in capsule['events']:
                state.add(event)
            self.cache[key] = state
        state = self.cache[key]
        state.event(capsule['receipt'], ('receipt',))
        return state

    def state(self, workspace):
        m.require(workspace in self.states, 'required origin history unavailable; import complete origin package')
        return self.states[workspace]

    def head(self, workspace):
        return next(reversed(self.state(workspace).events))


def selected(state, import_id, identity):
    event = state.event(import_id, ('transfer-import',))
    capsule = event['payload']['capsule']
    source = state.origins.validate(capsule)
    receipt = source.event(capsule['receipt'], ('receipt',))['payload']
    found = [item for item in receipt['content']['units'] if item['identity'] == identity]
    m.require(len(found) == 1, 'origin unit absent from selected receipt closure')
    unit = found[0]
    origin = dict(workspace=capsule['workspace'], identity=identity, unit_sha256=unit['sha256'], binding=unit['binding'])
    return source, receipt, origin


def closure(source, receipt, root):
    units = {m.key(item['identity']): item for item in receipt['content']['units']}
    pending = [root]
    found = {}
    edges = 0
    while pending:
        identity = pending.pop()
        key = m.key(identity)
        if key in found:
            continue
        m.require(key in units, 'selected receipt omits origin dependency')
        item = units[key]
        binding = source.event(item['binding'], ('binding', 'support-review'))
        found[key] = binding
        edges += len(binding['payload']['references'])
        m.require(len(found) <= 128 and edges <= 512, 'origin closure bound exceeded')
        pending.extend(ref['identity'] for ref in binding['payload']['references'])
    return [found[key] for key in sorted(found)]


def canonical_observations(state):
    """Positive installed observations only; proposed text is not installation."""
    found = {}

    def add(identity, file):
        found.setdefault(m.key(identity), {})[file['sha256']] = file

    for event in state.events.values():
        kind, p = event['kind'], event['payload']
        if kind in ('binding', 'support-review'):
            add(p['identity'], p['unit'])
        elif kind == 'receipt':
            for item in p['content']['units']:
                add(item['identity'], dict(path=item['path'], sha256=item['sha256'], text=item['text'], size=len(item['text'].encode('utf-8'))))
            for item in p.get('transfer', {}).get('lineage', []):
                add(item['identity'], item['file'])
        elif kind == 'conflict':
            for edge in p['lineage']:
                for file in edge['proof']:
                    add(dict(project=edge['after']['project'], unit=m.frontmatter(file['text'])['id']), file)
        elif kind == 'resolution' and p['remedy']['kind'] == 'incorporation':
            binding = state.event(p['remedy']['binding'])['payload']
            for file in p['remedy']['proof']:
                add(dict(project=binding['identity']['project'], unit=m.frontmatter(file['text'])['id']), file)
    return found


def predecessor(state, identity):
    revisions = canonical_observations(state).get(m.key(identity), {})
    m.require(len(revisions) == 1, 'origin predecessor unavailable or inconsistent; inspect retained revisions')
    return next(iter(revisions.values()))


def known_tree(state):
    observations = canonical_observations(state)
    return {key: next(iter(files.values())) for key, files in observations.items() if len(files) == 1}


def source_statuses(state, bindings, scope):
    from . import lifecycle_state as ls
    statuses = set()
    observations = canonical_observations(state)
    for binding in bindings:
        p = binding['payload']
        identity = p['identity']
        receipt = dict(scope=scope, content=dict(units=[dict(identity=identity, sha256=p['unit']['sha256'])]))
        for handle, decision in state.decisions.items():
            submission = state.events[handle]
            if submission['kind'] == 'challenge' and decision['payload']['outcome'] == 'accept' and decision['id'] not in state.resolutions and ls.affected(submission['payload'], receipt):
                statuses.add('review-required')
        if any(identity['unit'] in m.frontmatter(file['text']).get('supersedes', [])
               for key, files in observations.items() if key[0] == identity['project'] for file in files.values()):
            statuses.add('known-superseded')
        if state.bindings.get(m.key(identity), {}).get('id') != binding['id']:
            statuses.add('binding-changed')
        incorporations = [e for e in state.events.values() if e['kind'] == 'incorporation'
                          and state.event(e['payload']['binding'])['payload']['identity'] == identity
                          and state.event(e['payload']['binding'])['payload']['unit']['sha256'] == p['unit']['sha256']]
        if incorporations:
            try:
                latest = incorporations[-1]['payload']
                ls.accepted(state, latest['proposal'], latest['decision'])
            except m.Refusal:
                statuses.add('acceptance-withdrawn')
        for handle, event in state.conflicts.items():
            cp = event['payload']
            if m.applies(cp['scope'], scope) and any(c['identity'] == identity for c in cp['candidates']):
                choice = state.choices.get(handle)
                if not choice or choice['payload']['candidate']['identity'] != identity:
                    statuses.add('conflict')
                for candidate in cp['candidates']:
                    try:
                        state.check_candidate(candidate)
                    except m.Refusal:
                        statuses.add('conflict')
    return [status for status in tv.STATUS if status in statuses]
