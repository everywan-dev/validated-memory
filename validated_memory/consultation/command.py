"""Opt-in consultation operations over canonical, read-only adopter inputs."""

import argparse
import os
import uuid
from collections import deque
from pathlib import Path

from . import checks, inputs, model as m, store
from . import lifecycle_cli, lifecycle_state as ls
from .state import State


class _SingleValue(argparse.Action):
    """Reject duplicate singleton options after argparse resolves their spelling."""

    def __call__(self, parser, namespace, values, option_string=None):
        seen = getattr(namespace, '_consultation_singletons', set())
        if self.dest in seen:
            parser.error(f'{option_string} may be supplied only once')
        seen.add(self.dest)
        namespace._consultation_singletons = seen
        setattr(namespace, self.dest, values)


def parser(parent):
    parent.add_argument('--store', action=_SingleValue, required=True, metavar='PATH')
    commands = parent.add_subparsers(dest='operation', required=True)
    for operation in ('register', 'checkpoint', 'relocate', 'bind', 'review-support',
                      'conflict', 'choose', 'read', 'record-use', 'check-use', 'show', 'recover'):
        sub = commands.add_parser(operation)
        if operation in ('register', 'relocate'):
            sub.add_argument('alias')
            sub.add_argument('root_path', metavar='ROOT')
        elif operation == 'checkpoint':
            sub.add_argument('alias')
        elif operation in ('bind', 'review-support', 'read', 'record-use'):
            sub.add_argument('target', metavar='ALIAS:ID')
        elif operation in ('choose', 'show', 'check-use'):
            sub.add_argument('handle', metavar='HANDLE')
        if operation == 'register':
            sub.add_argument('--source', action=_SingleValue, required=True)
        if operation == 'relocate':
            sub.add_argument('--checkpoint', action=_SingleValue, required=True)
        if operation in ('bind', 'review-support'):
            sub.add_argument('--support', action='append', required=True)
            sub.add_argument('--reference', action='append', default=[])
            sub.add_argument('--authority', action=_SingleValue, required=True)
        if operation == 'review-support':
            sub.add_argument('--prior', action=_SingleValue, required=True)
        if operation == 'conflict':
            sub.add_argument('--candidate', action='append', required=True)
            sub.add_argument('--prior', action=_SingleValue)
            sub.add_argument('--replacement', action='append', default=[])
        if operation == 'choose':
            sub.add_argument('--candidate', action=_SingleValue, required=True)
        if operation in ('bind', 'review-support', 'conflict', 'choose', 'read'):
            sub.add_argument('--scope', action='append', required=True)
        if operation in ('checkpoint', 'relocate', 'bind', 'review-support', 'conflict', 'choose'):
            sub.add_argument('--actor', action=_SingleValue, required=True)
            sub.add_argument('--reason', action=_SingleValue, required=True)
        if operation == 'read':
            sub.add_argument('--max-bytes', action=_SingleValue, type=int, default=65536)
        if operation == 'record-use':
            sub.add_argument('--receipt', action=_SingleValue, required=True)
        sub.set_defaults(consultation_parser=sub)
    lifecycle_cli.parser(commands, _SingleValue)


def normalize(args):
    if args.operation in lifecycle_cli.OPERATIONS:
        lifecycle_cli.normalize(args)
    if hasattr(args, 'scope'):
        scope = {}
        for pair in args.scope:
            k, sep, v = pair.partition('=')
            m.require(sep and k not in scope, 'scope requires unique KEY=VALUE pairs')
            scope[k] = v
        m.scope(scope)
        args.scope = scope
    for field, limit in (('actor', 256), ('reason', 4096)):
        if hasattr(args, field):
            m.text(getattr(args, field), limit)
    for field in ('alias', 'source', 'authority'):
        if hasattr(args, field):
            m.name(getattr(args, field))
    for field, maximum in (('support', 64), ('reference', 64), ('replacement', 64)):
        if hasattr(args, field):
            values = getattr(args, field)
            m.require(len(values) <= maximum and len(set(values)) == len(values),
                      f'--{field} must be unique and at most {maximum}')
    if hasattr(args, 'support'):
        for value in args.support:
            m.path(value)
    if args.operation == 'conflict':
        m.require(2 <= len(args.candidate) <= 64 and len(set(args.candidate)) == len(args.candidate),
                  '--candidate requires 2..64 distinct identities')
        m.require(args.prior or not args.replacement, '--replacement requires --prior')
    if args.operation == 'read':
        m.integer(args.max_bytes, 2048, 1048576)
    for field in ('prior', 'checkpoint', 'receipt', 'handle'):
        if hasattr(args, field) and getattr(args, field) is not None:
            m.sha(getattr(args, field))
    qualified = []
    for field in ('target', 'reference'):
        value = getattr(args, field, [])
        qualified.extend(value if isinstance(value, list) else [value])
    if hasattr(args, 'candidate'):
        value = args.candidate
        qualified.extend(value if isinstance(value, list) else [value])
    for replacement in getattr(args, 'replacement', []):
        parts = replacement.split('=')
        m.require(len(parts) == 2, '--replacement requires OLD=NEW')
        qualified.extend(parts)
    for value in qualified:
        pieces = value.split(':')
        m.require(len(pieces) == 2, 'qualified identity requires ALIAS:ID')
        m.name(pieces[0])
        m.pattern(pieces[1], m.ID_PATTERN.pattern)


def capture(state, **kwargs):
    projects = inputs.capture(state, **kwargs)
    snapshot = dict(version=1, workspace=state.workspace,
                    projects=[projects[p]['inventory'] for p in sorted(projects)], heads=state.heads())
    return projects, snapshot


def recapture(state, before, **kwargs):
    store.fault('before-recapture')
    projects, after = capture(state, **kwargs)
    ensure_snapshot(before, after)
    return projects


def ensure_snapshot(before, after):
    if before == after:
        return
    changed = []
    old = {p['project']: p for p in before['projects']}
    new = {p['project']: p for p in after['projects']}
    for project in sorted(set(old) | set(new)):
        a, b = old.get(project), new.get(project)
        if a == b:
            continue
        if not a or not b:
            changed.append(project + ' membership')
            continue
        for field in ('root', 'root_identity', 'config', 'schema'):
            if a[field] != b[field]:
                changed.append(project + ':' + field)
        for role in ('knowledge', 'support'):
            left = {f['path']: f for f in a[role]}
            right = {f['path']: f for f in b[role]}
            changed.extend(project + ':' + path for path in sorted(set(left) | set(right))
                           if left.get(path) != right.get(path))
    for field in before['heads']:
        if before['heads'][field] != after['heads'][field]:
            changed.append('semantic ' + field)
    raise m.Refusal('snapshot changed/stale: ' + ', '.join(changed[:16]) +
                    '; inspect changes, review-support or bind successors, then reconsult')


def attribution(args):
    return dict(actor=args.actor, reason=args.reason)


def emit(stream, text):
    raw = getattr(getattr(stream, 'buffer', None), 'raw', None)
    if raw is None:
        stream.write(text)
    else:
        remaining = memoryview(text.encode('utf-8'))
        while remaining:
            count = raw.write(remaining)
            if not count:
                raise OSError('output delivery made no progress')
            remaining = remaining[count:]
    stream.flush()


def response(args, event, **extra):
    status = {'record-use': 'checked-use recorded', 'check-use': 'current'}.get(args.operation, 'recorded')
    return m.line(args.operation, status, id=event['id'], **extra)


def registration(args, database):
    state = database.state
    root, node = inputs.root_identity(args.root_path)
    registrations = {p: e['payload'] for p, e in state.registrations.items()}
    if args.operation == 'register':
        existing = state.registrations.get(state.aliases.get(args.alias))
        if existing:
            old = existing['payload']
            m.require(old['root'] == root and old['source'] == args.source,
                      f'{args.alias}: alias collision; choose a distinct alias or relocate explicitly')
            projects, snapshot = capture(state)
            recapture(state, snapshot)
            return existing, dict(project_id=old['project'], alias=args.alias)
        project = str(uuid.uuid4())
        prior = None
        p = dict(version=1, alias=args.alias, project=project, root=root,
                 source=args.source, root_identity=node, inventory_sha256='0' * 64)
    else:
        project = state.project(args.alias)
        head = state.registrations[project]
        old = head['payload']
        checkpoint = state.event(args.checkpoint, ('checkpoint',))
        if head['kind'] == 'relocation' and old['root'] == root and old['checkpoint'] == args.checkpoint:
            projects, snapshot = capture(state)
            m.require(m.digest(m.neutral(projects[project]['inventory'])) == checkpoint['payload']['inventory_sha256'],
                      'relocation retry content changed; inspect and checkpoint current content')
            recapture(state, snapshot)
            return head, dict(project_id=project, alias=args.alias)
        m.require(state.checkpoints.get(head['id']) == checkpoint,
                  'relocation requires latest checkpoint for current registration; restore root and checkpoint')
        m.require(not os.path.lexists(old['root']), 'old root still available; move it before explicit relocation')
        prior = head['id']
        p = dict(version=1, alias=args.alias, project=project, root=root,
                 source=old['source'], root_identity=node, inventory_sha256='0' * 64,
                 checkpoint=args.checkpoint, **attribution(args))
    m.require(not m.overlaps(root, database.path), 'store overlaps adopter; select an outside store')
    for other_id, other in registrations.items():
        if other_id != project:
            m.require(not m.overlaps(root, other['root']) and node != other['root_identity'],
                      'duplicate/nested root; use the registered project')
    m.require(project in registrations or len(registrations) < 16, '16-project limit; narrow enrollment')
    registrations[project] = p
    projects, snapshot = capture(state, registrations=registrations)
    p['inventory_sha256'] = m.digest(m.neutral(projects[project]['inventory']))
    if args.operation == 'relocate':
        m.require(p['inventory_sha256'] == checkpoint['payload']['inventory_sha256'],
                  'relocation destination differs from checkpoint; restore/inspect original content')
    recapture(state, snapshot, registrations=registrations)
    return database.append(args.operation == 'register' and 'registration' or 'relocation', p, prior), dict(project_id=project, alias=args.alias)


def checkpoint(args, database):
    state = database.state
    project = state.project(args.alias)
    projects, snapshot = capture(state)
    registration = state.registrations[project]['id']
    inv = m.neutral(projects[project]['inventory'])
    p = dict(version=1, project=project, registration=registration, inventory=inv,
             inventory_sha256=m.digest(inv), **attribution(args))
    previous = state.checkpoints.get(registration)
    recapture(state, snapshot)
    if previous and m.semantic(previous['payload']) == m.semantic(p):
        return previous, dict(project_id=project)
    return database.append('checkpoint', p, previous['id'] if previous else None), dict(project_id=project)


def bind(args, database):
    state = database.state
    identity = state.resolve(args.target)
    head = state.bindings.get(m.key(identity))
    overrides = {m.key(identity): sorted(args.support)}
    projects, snapshot = capture(state, support_overrides=overrides)
    current = checks.unit(projects, identity)
    m.require(current['state'] == 'active', f'{identity}: superseded; bind its successor')
    if args.operation == 'review-support':
        m.require(head is not None and (head['id'] == args.prior or head['prior'] == args.prior),
                  'review-support prior is stale; inspect current binding')
        m.require(current['file'] == head['payload']['unit'],
                  'review-support cannot change canonical claim; author a successor')
    references = []
    for ref in args.reference:
        target = state.resolve(ref)
        observed = checks.unit(projects, target)
        m.require(observed['state'] == 'active', f'{ref}: reference superseded; select successor')
        references.append(dict(identity=target, unit_sha256=observed['file']['sha256']))
    p = dict(version=1, identity=identity, unit=current['file'], authority=args.authority,
             scope=args.scope, support=[projects[identity['project']]['files'][path] for path in sorted(args.support)],
             references=sorted(references, key=lambda x: m.key(x['identity'])), **attribution(args))
    m.require(args.authority == state.registrations[identity['project']]['payload']['source'],
              'authority differs from registered source; inspect declaration')
    recapture(state, snapshot, support_overrides=overrides)
    if head and m.semantic(head['payload']) == m.semantic(p):
        return head, dict(root=identity)
    if args.operation == 'bind':
        m.require(head is None, 'binding already exists with changed inputs; inspect and review-support or author successor')
    else:
        m.require(head['id'] == args.prior, 'review-support prior is stale; inspect current binding')
    return database.append('binding' if args.operation == 'bind' else 'support-review', p,
                           head['id'] if head else None), dict(root=identity)


def candidate(state, projects, identity, scope):
    event = checks.binding(state, projects, identity, scope)
    return dict(identity=identity, binding=event['id'], unit_sha256=event['payload']['unit']['sha256'])


def lineage(projects, old, new):
    m.require(old['identity']['project'] == new['identity']['project'], 'cross-project supersession is forbidden')
    project = projects[new['identity']['project']]
    start, goal = new['identity']['unit'], old['identity']['unit']
    queue = deque([[start]])
    seen = {start}
    bound_reached = False
    while queue:
        path = queue.popleft()
        if path[-1] == goal:
            proof = [project['units'][u]['file'] for u in path]
            m.require(proof[-1]['sha256'] == old['unit_sha256'], 'old conflict endpoint bytes changed; restore canonical predecessor')
            return proof
        if len(path) == 128:
            bound_reached = True
            continue
        for target in sorted(project['units'][path[-1]]['data'].get('supersedes', [])):
            if target not in seen:
                seen.add(target)
                queue.append(path + [target])
    m.require(not bound_reached, '128-document lineage bound; narrow successor chain')
    raise m.Refusal('replacement lacks canonical supersession lineage; author a same-project successor')


def conflict(args, database):
    state = database.state
    projects, snapshot = capture(state)
    candidates = sorted([candidate(state, projects, state.resolve(value), args.scope)
                         for value in args.candidate], key=lambda x: m.key(x['identity']))
    p = dict(version=1, scope=args.scope, candidates=candidates, lineage=[], **attribution(args))
    if args.prior:
        previous = state.event(args.prior, ('conflict',))
        old = {m.key(x['identity']): x for x in previous['payload']['candidates']}
        new = {m.key(x['identity']): x for x in candidates}
        for replacement in args.replacement:
            a, b = replacement.split('=')
            before, after = state.resolve(a), state.resolve(b)
            m.require(m.key(before) in old and m.key(after) in new, 'replacement must name prior/current candidates')
            p['lineage'].append(dict(before=before, after=after,
                                     proof=lineage(projects, old[m.key(before)], new[m.key(after)])))
        p['lineage'].sort(key=lambda x: m.key(x['before']))
    recapture(state, snapshot)
    for event in state.conflicts.values():
        if event['prior'] == args.prior and m.semantic(event['payload']) == m.semantic(p):
            return event, {}
    return database.append('conflict', p, args.prior), {}


def choose(args, database):
    state = database.state
    m.require(args.handle in state.conflicts, 'conflict is obsolete; inspect current conflict successor')
    conflict = state.conflicts[args.handle]['payload']
    m.require(args.scope == conflict['scope'], 'choice scope must equal conflict scope')
    projects, snapshot = capture(state)
    for item in conflict['candidates']:
        m.require(candidate(state, projects, item['identity'], args.scope) == item,
                  'stale conflict candidate; declare conflict successor then choose')
    selection = candidate(state, projects, state.resolve(args.candidate), args.scope)
    m.require(selection in conflict['candidates'], 'choice must select a conflict candidate')
    p = dict(version=1, conflict=args.handle, candidate=selection, scope=args.scope, **attribution(args))
    head = state.choices.get(args.handle)
    recapture(state, snapshot)
    if head and m.semantic(head['payload']) == m.semantic(p):
        return head, {}
    return database.append('choice', p, head['id'] if head else None), {}


def read(args, database, stdout):
    state = database.state
    root = state.resolve(args.target)
    projects, snapshot = capture(state)
    content = checks.content(state, projects, root, args.scope)
    ls.gate(state, content, args.scope)
    inspection = m.line('read', 'inspected', root=root, scope=args.scope, content=content, limitation=m.LIMITATION)
    p = dict(version=1, root=root, scope=args.scope, max_bytes=args.max_bytes,
             snapshot=snapshot, snapshot_sha256=m.digest(snapshot), content=content,
             content_sha256=m.digest(content), inspection_text=inspection,
             inspection_sha256=m.byte_digest(inspection.encode('utf-8')))
    if database.version == 2:
        p.update(version=2, review_frontier=ls.frontier(state, content, args.scope))
    handle = m.event_id('receipt', None, p)
    final = m.line('read', 'receipt recorded', id=handle)
    m.require(len((inspection + final).encode('utf-8')) <= args.max_bytes,
              'complete acquisition exceeds --max-bytes; increase bound or narrow declared closure')
    store.fault('before-inspection-output')
    emit(stdout, inspection)
    store.fault('after-inspection-output')
    recapture(state, snapshot)
    event = database.append('receipt', p)
    database.commit(event['id'])
    store.fault('before-handle-output')
    emit(stdout, final)


def use(args, database):
    state = database.state
    if args.operation == 'check-use':
        event = state.event(args.handle, ('use',))
        receipt_id = event['payload']['receipt']
        root = event['payload']['root']
    else:
        receipt_id = args.receipt
        root = state.resolve(args.target)
    receipt = state.event(receipt_id, ('receipt',))['payload']
    m.require(root == receipt['root'], f"{root}: receipt belongs to a different consumer {receipt['root']}; read the exact conclusion")
    projects, snapshot = capture(state)
    ls.gate(state, receipt['content'], receipt['scope'], receipt.get('review_frontier', []))
    ensure_snapshot(receipt['snapshot'], snapshot)
    content = checks.content(state, projects, root, receipt['scope'])
    m.require(content == receipt['content'], 'receipt content changed; inspect and reconsult')
    recapture(state, snapshot)
    if args.operation == 'check-use':
        return event, {}
    p = dict(version=1, root=root, receipt=receipt_id, snapshot_sha256=receipt['snapshot_sha256'],
             content_sha256=receipt['content_sha256'], scope=receipt['scope'])
    return database.append('use', p), dict(root=root)


def run(args, stdout, stderr):
    try:
        normalize(args)
    except (m.Refusal, ValueError, UnicodeError) as error:
        args.consultation_parser.error(str(error))
    database = None
    try:
        store_path = store.location(args.store)
        missing_store = not Path(store_path).exists()
        if args.operation == 'register' and missing_store:
            root, node = inputs.root_identity(args.root_path)
            m.require(not m.overlaps(root, store_path), 'store overlaps adopter; select an outside store')
            temporary = State(str(uuid.uuid4()), store_path)
            project = str(uuid.uuid4())
            inputs.capture(temporary, registrations={project: dict(project=project, root=root,
                           root_identity=node, alias=args.alias, source=args.source)})
        readonly = args.operation in ('show', 'check-use', 'reconcile')
        database = store.Store(store_path, writable=not readonly,
                               create=args.operation == 'register' and missing_store, recover=args.operation == 'recover')
        with database:
            if args.operation in lifecycle_cli.OPERATIONS:
                from .lifecycle_command import run as lifecycle_run
                return lifecycle_run(args, database, stdout)
            if args.operation == 'read':
                read(args, database, stdout)
                return 0
            if args.operation == 'show':
                event = database.state.event(args.handle)
                emit(stdout, m.line('show', 'historical; not a current eligibility check', id=event['id'], artifact=event))
                return 0
            if args.operation == 'recover':
                database.commit()
                emit(stdout, m.line('recover', 'recovered'))
                return 0
            operations = {'register': registration, 'relocate': registration, 'checkpoint': checkpoint,
                          'bind': bind, 'review-support': bind, 'conflict': conflict, 'choose': choose,
                          'record-use': use, 'check-use': use}
            event, extra = operations[args.operation](args, database)
            if not readonly:
                database.commit(event['id'])
            emit(stdout, response(args, event, **extra))
            return 0
    except Exception as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        message = store.storage_error(error)
        if database and database.committed_id:
            message += '; committed artifact id=' + database.committed_id + '; retry validates this artifact'
        try:
            print('consultation: ' + message, file=stderr, flush=True)
        except OSError:
            pass
        return 1
