"""Explicit retained contribution workflow over read-only adopter inputs."""

import copy
import os

from . import checks, inputs, model as m, store
from . import lifecycle_model as lm, lifecycle_state as ls
from .command import attribution, capture, recapture, ensure_snapshot, emit, lineage


def current_use(state, handle, projects, snapshot):
    receipt = ls.receipt(state, handle)
    ls.gate(state, receipt['content'], receipt['scope'], receipt.get('review_frontier', []))
    ensure_snapshot(receipt['snapshot'], snapshot)
    content = checks.content(state, projects, receipt['root'], receipt['scope'])
    m.require(content == receipt['content'], 'receipt content changed; reconsult')
    return receipt


def current_resolution(state, event, projects):
    binding = ls.resolution_binding(state, event)
    challenge = state.event(event['payload']['challenge'], ('challenge',))['payload']
    checks.binding(state, projects, binding['payload']['identity'], challenge['scope'])
    return binding


def current_address(state, event, projects, snapshot):
    p = event['payload']
    ls.validate_address(state, p)
    current_resolution(state, state.event(p['resolution'], ('resolution',)), projects)
    current_use(state, p['new_use'], projects, snapshot)


def material(state, path, maximum=1048576, outside=False):
    absolute, value = inputs.external(path, maximum)
    if outside:
        m.require(all(not m.overlaps(absolute, reg['payload']['root']) for reg in state.registrations.values()),
                  'candidate must be outside enrolled roots')
    return value


def declaration(args, state, projects, identity, unit):
    references = []
    for ref in args.reference:
        target = state.resolve(ref)
        observed = checks.unit(projects, target)
        m.require(observed['state'] == 'active', 'proposal reference is superseded; select successor')
        references.append(dict(identity=target, unit_sha256=observed['file']['sha256']))
    return dict(identity=identity, unit=unit, authority=state.registrations[identity['project']]['payload']['source'],
                scope=args.scope, support=[projects[identity['project']]['files'][path] for path in sorted(args.support)],
                references=sorted(references, key=lambda item: m.key(item['identity'])))


def proposal_capture(state, identity, unit, paths, installed=False):
    kwargs = dict(support_overrides={m.key(identity): paths})
    if not installed:
        kwargs['insertion'] = dict(identity=identity, unit=unit)
    projects, snapshot = capture(state, **kwargs)
    return projects, snapshot, kwargs


def submit(args, database):
    state = database.state
    old = state.event(args.handle, ('proposal',)) if args.operation == 'renew' else None
    installed = False
    if old:
        previous = old['payload']
        identity, unit = previous['identity'], previous['unit']
        root = state.registrations[identity['project']]['payload']['root']
        installed = os.path.lexists(os.path.join(root, unit['path']))
        paths = args.support if args.explicit_declaration else [f['path'] for f in previous['support']]
    else:
        unit = dict(material(state, args.candidate_file, outside=True), path=args.path)
        identity = dict(project=state.project(args.alias), unit=m.frontmatter(unit['text']).get('id'))
        m.identity(identity)
        m.require(args.authority == state.registrations[identity['project']]['payload']['source'], 'authority differs from registered source')
        paths = args.support
    projects, snapshot, kwargs = proposal_capture(state, identity, unit, paths, installed)
    observed = checks.unit(projects, identity)
    m.require(observed['state'] == 'active' and observed['file'] == unit, 'renewal candidate bytes/path/ID changed; retain proposal and author legitimate successor')
    if old and not args.explicit_declaration:
        dec = lm.declaration(previous)
        m.require(all(projects[identity['project']]['files'].get(f['path']) == f for f in previous['support']),
                  'renewal support drift; use explicit complete --support --scope declaration')
        for ref in previous['references']:
            target = checks.unit(projects, ref['identity'])
            m.require(target['state'] == 'active' and target['file']['sha256'] == ref['unit_sha256'],
                      'renewal reference drift; supply complete new declaration')
    else:
        dec = declaration(args, state, projects, identity, unit)
    predecessors = []
    for predecessor_id in m.frontmatter(unit['text']).get('supersedes', []):
        predecessor = dict(project=identity['project'], unit=predecessor_id)
        file = checks.unit(projects, predecessor)['file']
        if not old:
            before = projects[identity['project']]['before_states'].get(predecessor_id)
            m.require(before and before[1] == 'active', 'proposal predecessor must be active before insertion')
        predecessors.append(dict(identity=predecessor, unit=file))
    predecessors.sort(key=lambda item: m.key(item['identity']))
    if old:
        m.require(predecessors == previous['predecessors'], 'renewal predecessor bytes/path changed; inspect retained original')
    binding_id = None
    if installed:
        binding = checks.binding(state, projects, identity, dec['scope'])
        m.require(lm.declaration(binding['payload']) == dec, 'installed binding differs from renewed declaration; review-support first')
        binding_id = binding['id']
    p = dict(version=1, **dec, predecessors=predecessors, expected=snapshot,
             installed_binding=binding_id, **attribution(args))
    lm.payload('proposal', p)
    recapture(state, snapshot, **kwargs)
    if not old:
        m.require(dict(material(state, args.candidate_file, outside=True), path=args.path) == unit, 'candidate changed during submission')
    prior = old['id'] if old else None
    # Bound the exact future inspection envelope before retaining material.
    preview = dict(sequence=len(state.events) + 1, id=m.event_id('proposal', prior, p), kind='proposal', prior=prior,
                   payload=p, created_at='2000-01-01T00:00:00.000000Z')
    wire = lm.inspection_line(preview) + m.line('inspect', 'inspection recorded', id='0' * 64)
    m.require(len(wire.encode('utf-8')) <= 1048576, 'proposal exceeds complete inspection bound; narrow submitted material')
    return database.append('proposal', p, prior)


def challenge(args, database):
    state = database.state
    identity = state.resolve(args.target)
    binding = state.binding(identity)
    statement = material(state, args.statement, 65536)
    p = dict(version=1, target=dict(identity=identity, binding=binding['id'], unit=binding['payload']['unit']),
             kind=args.kind, statement={k: statement[k] for k in ('sha256', 'size', 'text')},
             scope=args.scope, **attribution(args))
    m.require(material(state, args.statement, 65536) == statement, 'statement changed; retry')
    return database.append('challenge', p)


def inspect(args, database, stdout):
    state = database.state
    event = state.event(args.handle, ('proposal', 'challenge', 'binding', 'support-review'))
    wire = lm.inspection_line(event)
    p = dict(version=1, submission=event['id'], observed_head=next(reversed(state.events)), max_bytes=args.max_bytes,
             inspection_text=wire, inspection_sha256=m.byte_digest(wire.encode('utf-8')))
    handle = m.event_id('inspection', None, p)
    final = m.line('inspect', 'inspection recorded', id=handle)
    m.require(len((wire + final).encode('utf-8')) <= args.max_bytes, 'complete inspection exceeds --max-bytes; increase bound')
    store.fault('before-inspection-output')
    emit(stdout, wire)
    store.fault('after-inspection-output')
    event = database.append('inspection', p)
    database.commit(event['id'])
    store.fault('before-handle-output')
    emit(stdout, final)


def decide(args, database):
    state = database.state
    state.event(args.handle, ('proposal', 'challenge'))
    p = dict(version=1, submission=args.handle, inspection=args.inspection, outcome=args.outcome, **attribution(args))
    head = state.decisions.get(args.handle)
    if head and head['payload'] == p:
        return head
    return database.append('decision', p, args.prior)


def incorporate(args, database):
    state = database.state
    proposal = state.event(args.handle, ('proposal',))['payload']
    ls.accepted(state, args.handle, args.decision)
    projects, snapshot = capture(state)
    binding = checks.binding(state, projects, proposal['identity'], proposal['scope'])
    expected = copy.deepcopy(proposal['expected'])
    if proposal['installed_binding'] is None:
        expected['heads']['bindings'] = sorted([*expected['heads']['bindings'], binding['id']])
    ensure_snapshot(expected, snapshot)
    m.require(lm.declaration(binding['payload']) == lm.declaration(proposal), 'incorporation declaration differs from accepted proposal; renew and accept')
    recapture(state, snapshot)
    return database.append('incorporation', dict(version=1, proposal=args.handle, decision=args.decision,
                                                binding=binding['id'], **attribution(args)))


def successor_proof(projects, newer, older):
    return lineage(projects, dict(identity=older['identity'], unit_sha256=older['unit']['sha256']),
                   dict(identity=newer['identity'], unit_sha256=newer['unit']['sha256']))


def resolve(args, database):
    state = database.state
    challenge = state.event(args.handle, ('challenge',))['payload']
    ls.accepted(state, args.handle, args.decision)
    projects, snapshot = capture(state)
    if args.incorporation:
        binding = ls.incorporation_binding(state, args.incorporation)
        remedy = dict(kind='incorporation', incorporation=args.incorporation, binding=binding['id'],
                      proof=successor_proof(projects, binding['payload'], challenge['target']))
    else:
        binding = state.event(args.review, ('binding', 'support-review'))
        remedy = dict(kind='review', binding=binding['id'])
    m.require(checks.binding(state, projects, binding['payload']['identity'], challenge['scope']) == binding,
              'resolution binding is not current; inspect current binding')
    inspected = state.event(args.inspection, ('inspection',))
    decision = state.event(args.decision, ('decision',))
    m.require(inspected['payload']['submission'] == binding['id'] and inspected['sequence'] > decision['sequence'],
              'resolution requires exact binding inspection after acceptance')
    p = dict(version=1, challenge=args.handle, decision=args.decision, inspection=args.inspection,
             remedy=remedy, **attribution(args))
    recapture(state, snapshot)
    return database.append('resolution', p)


def address(args, database):
    state = database.state
    ls.accepted(state, args.handle, args.decision)
    resolution = state.resolutions.get(args.decision)
    m.require(resolution, 'accepted challenge requires explicit resolution before addressing uses')
    projects, snapshot = capture(state)
    current_resolution(state, resolution, projects)
    new = current_use(state, args.new_use, projects, snapshot)
    old = ls.receipt(state, args.old_use)
    a, b = ls.root_revision(new), ls.root_revision(old)
    proof = [] if a == b else successor_proof(projects, a, b)
    p = dict(version=1, challenge=args.handle, decision=args.decision, resolution=resolution['id'],
             old_use=args.old_use, new_use=args.new_use, mode=args.mode, consumer_proof=proof, **attribution(args))
    ls.validate_address(state, p)
    recapture(state, snapshot)
    return database.append('address', p)


def track(args, database):
    state = database.state
    state.event(args.handle, ('challenge',))
    project = state.project(args.project)
    projects, snapshot = capture(state)
    file = inputs.publication(state, project, args.path, projects)
    recapture(state, snapshot)
    m.require(inputs.publication(state, project, args.path, projects) == file, 'publication changed during capture; retry')
    return database.append('publication', dict(version=1, challenge=args.handle, project=project, use=args.use,
                                              file=file, **attribution(args)))


def reflect(args, database):
    state = database.state
    publication = state.event(args.handle, ('publication',))['payload']
    address = state.event(args.address, ('address',))
    projects, snapshot = capture(state)
    current_address(state, address, projects, snapshot)
    file = inputs.publication(state, publication['project'], publication['file']['path'], projects)
    p = dict(version=1, publication=args.handle, address=args.address, file=file, **attribution(args))
    m.require(file['sha256'] != publication['file']['sha256'], 'reflection requires changed publication bytes')
    recapture(state, snapshot)
    m.require(inputs.publication(state, publication['project'], file['path'], projects) == file, 'publication changed during capture; retry')
    return database.append('reflection', p)


def run(args, database, stdout):
    if args.operation == 'upgrade':
        database.upgrade()
        database.commit()
        emit(stdout, m.line('upgrade', 'upgraded', storage_version=2))
        return 0
    m.require(database.version == 2, 'lifecycle requires schema 2; run consultation --store PATH upgrade explicitly')
    if args.operation == 'inspect':
        inspect(args, database, stdout)
    elif args.operation == 'reconcile':
        from .reconciliation import report
        emit(stdout, report(args, database.state))
    else:
        handlers = {'submit': submit, 'renew': submit, 'challenge': challenge, 'decide': decide,
                    'incorporate': incorporate, 'resolve': resolve, 'address': address,
                    'track-publication': track, 'reflect': reflect}
        event = handlers[args.operation](args, database)
        database.commit(event['id'])
        emit(stdout, m.line(args.operation, 'recorded', id=event['id']))
    return 0
