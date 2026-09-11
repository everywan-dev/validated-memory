"""The same binding and closure checks serve acquisition and historical replay."""

from . import model as m


def unit(projects, identity):
    project = projects.get(identity['project'])
    value = project['units'].get(identity['unit']) if project else None
    m.require(value is not None, f'{identity}: unit unavailable; inspect source and author or reconsult')
    return value


def binding(state, projects, identity, scope, historical=False):
    current = unit(projects, identity)
    m.require(current['state'] == 'active', f'{identity}: superseded unit; bind and consult its successor')
    event = state.binding(identity)
    p = event['payload']
    m.require(current['file']['sha256'] == p['unit']['sha256'] and
              current['file']['path'] == p['unit']['path'],
              f'{identity}: canonical revision changed; author a successor and bind it')
    m.require(m.applies(p['scope'], scope), f'{identity}: scope is not applicable; inspect declared scope')
    project = projects[identity['project']]
    if historical:
        canonical = dict(path=current['file']['path'], sha256=current['file']['sha256'],
                         size=len(current['file']['text'].encode('utf-8')))
        m.require(canonical in project['inventory']['knowledge'],
                  'receipt canonical revision missing from inventory')
    for support in p['support']:
        observed = project['files'].get(support['path'])
        m.require(observed is not None and m.revision(observed) == m.revision(support),
                  f"{identity} {support['path']}: support changed/unavailable; inspect and review-support")
        if historical:
            m.require(m.revision(support) in project['inventory']['support'], 'receipt omits declared support inventory')
    for ref in p['references']:
        target = unit(projects, ref['identity'])
        m.require(target['file']['sha256'] == ref['unit_sha256'],
                  f"{ref['identity']}: reference revision changed; inspect and review-support")
    return event


def content(state, projects, root, scope, historical=False):
    visiting, done = set(), set()
    units, supports = [], {}
    edges = 0

    def visit(identity):
        nonlocal edges
        pair = m.key(identity)
        m.require(pair not in visiting, f'{identity}: dependency cycle; inspect declarations')
        if pair in done:
            return
        m.require(len(done) + len(visiting) < 128, '128-unit closure limit; narrow declared dependencies')
        visiting.add(pair)
        event = binding(state, projects, identity, scope, historical)
        p = event['payload']
        observed = unit(projects, identity)
        m.require(observed['data'].get('evidence') in ('measured', 'verifiable'),
                  f'{identity}: hypothesis cannot acquire checked use; investigate evidence')
        edges += len(p['references'])
        m.require(edges <= 512, '512-edge closure limit; narrow dependencies')
        for ref in p['references']:
            visit(ref['identity'])
        for conflict_id, conflict in state.conflicts.items():
            cp = conflict['payload']
            if not m.applies(cp['scope'], scope) or pair not in {m.key(c['identity']) for c in cp['candidates']}:
                continue
            for candidate in cp['candidates']:
                state.check_candidate(candidate)
                if not historical:
                    binding(state, projects, candidate['identity'], cp['scope'])
                    candidate_unit = unit(projects, candidate['identity'])
                    m.require(candidate_unit['state'] == 'active' and
                              candidate_unit['file']['sha256'] == candidate['unit_sha256'],
                              f'{conflict_id}: stale conflict; declare successor and choose')
            choice = state.choices.get(conflict_id)
            m.require(choice and m.key(choice['payload']['candidate']['identity']) == pair,
                      f'{conflict_id}: conflict blocks {identity}; record an explicit choice')
        units.append(dict(identity=identity, binding=event['id'],
                          **{k: observed['file'][k] for k in ('path', 'sha256', 'text')}))
        for support in p['support']:
            supports[(identity['project'], support['path'])] = dict(project=identity['project'],
                            **{k: support[k] for k in ('path', 'sha256', 'text')})
        visiting.remove(pair)
        done.add(pair)

    visit(root)
    return dict(units=sorted(units, key=lambda x: m.key(x['identity'])),
                support=[supports[pair] for pair in sorted(supports)])
