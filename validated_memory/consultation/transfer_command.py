"""Bounded capsule transport and reviewed local correspondence; no adopter writes."""

import os

from . import model as m, store, transfer_values as tv, origin_history as oh
from . import transfer_projection as tp, transfer_links as tl
from .transfer_cli import parse_origin
from .command import emit, attribution, recapture
from .lifecycle_command import material, proposal_capture


def inventory(state, import_id):
    cap = state.event(import_id, ('transfer-import',))['payload']['capsule']
    source = state.origins.validate(cap)
    receipt = source.event(cap['receipt'])['payload']
    latest = state.origins.state(cap['workspace'])
    observations = oh.canonical_observations(latest)
    units, recovery = [], []
    for item in receipt['content']['units']:
        binding = source.event(item['binding'])['payload']
        bindings = oh.closure(source, receipt, item['identity'])
        statuses = oh.source_statuses(latest, bindings, receipt['scope'])
        try:
            projection = tp.Projection(state)
            projection.origin_heads[latest.workspace] = state.origins.head(latest.workspace)
            tree = tp.retained_tree(latest)
            for b in bindings:
                tree[m.key(b['payload']['identity'])] = b['payload']['unit']
            for b in bindings:
                projection.visit(latest, b['payload']['identity'], tree, receipt['scope'], b['payload'])
        except m.Refusal as error:
            statuses.extend(status for status in tv.STATUS if status in str(error) and status not in statuses)
            if not statuses:
                statuses.append('origin-review-stale')
        predecessors = []
        for name in m.frontmatter(binding['unit']['text']).get('supersedes', []):
            identity = dict(project=item['identity']['project'], unit=name)
            revisions = sorted(observations.get(m.key(identity), {}))
            predecessors.append(dict(identity=identity, revisions=revisions, availability='complete' if len(revisions) == 1 else ('unavailable' if not revisions else 'inconsistent')))
        units.append(dict(identity=item['identity'], binding=item['binding'], unit_sha256=item['sha256'],
                          support_sha256=sorted({f['sha256'] for f in binding['support']}), dependencies=binding['references'],
                          predecessors=sorted(predecessors, key=lambda row: m.key(row['identity'])),
                          statuses=[status for status in tv.STATUS if status in statuses] or ['historical']))
    links = []
    for key, event in sorted(state.transfer_links.items()):
        p = event['payload']
        reached = {p['origin']['workspace'], *[row['workspace'] for row in p['observed']['origins']]}
        try:
            reached.update(row['workspace'] for row in tl.source_material(state, p, state.origins)['units'])
        except m.Refusal:
            pass  # Retained observed heads still identify affected blocked history.
        if cap['workspace'] in reached:
            proposal = state.event(p['proposal'])['payload']
            statuses = []
            tree = tp.retained_tree(state)
            retired = any(key[0] == proposal['identity']['project'] and proposal['identity']['unit'] in m.frontmatter(file['text']).get('supersedes', []) for key, file in tree.items())
            try:
                if p['mode'] == 'retain' and not retired:
                    tp.proposal_gate(state, p['proposal'], 'use')
            except m.Refusal as error:
                statuses = [status for status in tv.STATUS if status in str(error)] or ['origin-review-stale']
            links.append(dict(local=proposal['identity'], unit_sha256=proposal['unit']['sha256'], origin=p['origin'], link=event['id'], statuses=statuses or ['historical']))
    # A deterministic topological listing makes prerequisite work visible.
    pending = {m.key(row['identity']): row for row in units}
    while pending:
        ready = [key for key, row in pending.items() if all(m.key(ref['identity']) not in pending for ref in row['dependencies'])]
        m.require(ready, 'origin dependency cycle')
        for key in sorted(ready):
            row = pending.pop(key)
            recovery.append(f"{key[0]}:{key[1]}: inspect origin status; map and incorporate dependencies first, then link, inspect and accept the local proposal")
    return dict(origin=dict(workspace=cap['workspace'], head=state.origins.head(cap['workspace']), receipt=cap['receipt']),
                scope=receipt['scope'], live_origin='not-checked', units=sorted(units, key=lambda row: m.key(row['identity'])),
                local_links=links, recovery=recovery, disclosure=tv.DISCLOSURE,
                limits=dict(capsule_bytes=tv.CAP, output_bytes=tv.WIRE, origins=16, events=10000, depth=4))


def export(args, database, stdout):
    state = database.state
    receipt = state.event(args.handle, ('receipt',))['payload']
    cap = dict(format='validated-memory-transfer', version=1, workspace=state.workspace,
               source_store_path=database.path, storage_version=database.version, receipt=args.handle, events=list(state.events.values()))
    cap['sha256'] = m.digest(cap)
    wire = m.canonical(cap) + '\n'
    size = len(wire.encode('utf-8'))
    refusals = []
    if size > args.max_bytes:
        refusals.append('complete workspace history exceeds export bound; smaller receipt selection cannot remove history')
    try:
        oh.Registry(None).preflight(cap)
    except m.Refusal as error:
        reason = str(error)
        if reason not in refusals:
            refusals.append(reason)
    if args.assess:
        emit(stdout, m.line('export-transfer', 'assessed', bytes=size, max_bytes=args.max_bytes, supported=not refusals, refusals=refusals,
                          history_events=len(state.events), projects=len(state.registrations), origins=1 + len(state.origins.states),
                          selected=dict(receipt=args.handle, units=len(receipt['content']['units']),
                                        dependencies=sum(len(state.event(u['binding'])['payload']['references']) for u in receipt['content']['units']),
                                        support=len(receipt['content']['support'])), disclosure=tv.DISCLOSURE))
    else:
        m.require(not refusals, '; '.join(refusals))
        emit(stdout, wire)


def import_(args, database, stdout):
    state = database.state
    captured = material(state, args.capsule_file, tv.CAP, outside=True)
    cap = m.decode(captured['text'])
    m.require(m.canonical(cap) + '\n' == captured['text'], 'capsule must be canonical JSON plus one newline; use export-transfer output')
    tv.capsule(cap)
    existing = next((event for event in state.events.values() if event['kind'] == 'transfer-import' and event['payload']['capsule'] == cap), None)
    if existing:
        state.origins.ingest(cap)
        event = existing
    else:
        event = database.append('transfer-import', dict(version=1, capsule=cap, **attribution(args)))
    wire = m.line('import-transfer', 'recorded', id=event['id'], inventory=inventory(state, event['id']))
    m.require(len(wire.encode('utf-8')) <= tv.WIRE, 'import inventory exceeds complete output bound')
    store.fault('before-recapture')
    m.require(material(state, args.capsule_file, tv.CAP, outside=True) == captured, 'capsule changed during import; retry')
    database.commit(event['id'])
    emit(stdout, wire)


def mapped_incorporation(state, identity, file):
    candidates = [e for e in state.events.values() if e['kind'] == 'incorporation'
                  and state.event(e['payload']['binding'])['payload']['identity'] == identity
                  and state.event(e['payload']['binding'])['payload']['unit'] == file]
    m.require(candidates, 'mapped dependency requires an incorporated local revision')
    return candidates[-1]['id']


def link(args, database, stdout):
    state = database.state
    proposal = state.event(args.handle, ('proposal',))['payload']
    root = state.registrations[proposal['identity']['project']]['payload']['root']
    installed = os.path.lexists(os.path.join(root, proposal['unit']['path']))
    projects, snapshot, kwargs = proposal_capture(state, proposal['identity'], proposal['unit'], [f['path'] for f in proposal['support']], installed)
    tree = tp.tree_from_projects(projects)
    if args.operation == 'link-transfer':
        source, receipt, origin = oh.selected(state, args.import_id, args.origin)
        p = dict(version=1, mode='retain', proposal=args.handle, origin=origin, **{'import': args.import_id, 'from': None}, dependencies=[], predecessors=[], **attribution(args))
        for item in args.dependency:
            old, new = item.split('=')
            identity = state.resolve(new)
            m.require(m.key(identity) in tree, 'mapped local dependency unavailable')
            file = tree[m.key(identity)]
            p['dependencies'].append(dict(origin=parse_origin(old), local=identity, unit_sha256=file['sha256'], incorporation=mapped_incorporation(state, identity, file)))
        for item in args.predecessor:
            old, new = item.split('=')
            identity = state.resolve(new)
            m.require(m.key(identity) in tree, 'mapped local predecessor unavailable')
            file = oh.predecessor(state.origins.state(origin['workspace']), parse_origin(old))
            p['predecessors'].append(dict(origin=parse_origin(old), unit_sha256=file['sha256'], mode='mapped', local=identity, local_sha256=tree[m.key(identity)]['sha256']))
        for item in args.origin_only_predecessor:
            identity = parse_origin(item)
            file = oh.predecessor(state.origins.state(origin['workspace']), identity)
            p['predecessors'].append(dict(origin=identity, unit_sha256=file['sha256'], mode='origin-only', local=None, local_sha256=None))
        upstream = tp.Projection(state)
        upstream.upstream(state, p)
        p['observed'] = upstream.frontier()
    else:
        ancestor = state.event(args.from_link, ('transfer-link',))['payload']
        p = dict(version=1, mode='independent', proposal=args.handle, origin=ancestor['origin'],
                 **{'import': None, 'from': args.from_link}, dependencies=[], predecessors=[], **attribution(args))
        origins = {row['workspace']: state.origins.head(row['workspace']) for row in ancestor['observed']['origins']}
        origins[p['origin']['workspace']] = state.origins.head(p['origin']['workspace'])
        p['observed'] = dict(origins=[dict(workspace=k, head=origins[k]) for k in sorted(origins)], links=ancestor['observed']['links'])
    p['dependencies'].sort(key=lambda row: m.key(row['origin']))
    p['predecessors'].sort(key=lambda row: (*m.key(row['origin']), m.key(row['local']) if row['local'] else ('', '')))
    p['lineage'] = tp.ancestors(tree, [proposal['identity'], *[row['local'] for row in p['dependencies']]])
    wire = tl.validate(state, p, tree)
    p.update(max_bytes=args.max_bytes, inspection_text=wire, inspection_sha256=m.byte_digest(wire.encode('utf-8')))
    tv.payload('transfer-link', p)
    head = state.transfer_links.get(tv.link_key(state, p))
    same = head and tv.semantic(head['payload']) == tv.semantic(p)
    m.require(same or args.prior == (head['id'] if head else None), 'transfer link prior is stale; name current link')
    if same:
        # Repeat the original attributed review rather than claiming a new actor.
        p = head['payload']
        wire = tl.validate(state, p, tree)
    handle = head['id'] if same else m.event_id('transfer-link', args.prior, p)
    final = m.line(args.operation, 'recorded', id=handle)
    m.require(len((wire + final).encode('utf-8')) <= args.max_bytes, 'complete transfer inspection exceeds --max-bytes')
    store.fault('before-inspection-output')
    emit(stdout, wire)
    store.fault('after-inspection-output')
    recapture(state, snapshot, **kwargs)
    event = head if same else database.append('transfer-link', p, args.prior)
    database.commit(event['id'])
    store.fault('before-handle-output')
    emit(stdout, final)


def run(args, database, stdout):
    if args.operation == 'export-transfer':
        export(args, database, stdout)
    elif args.operation == 'show-transfer':
        wire = m.line('show-transfer', 'historical; live origin not checked', id=args.handle, inventory=inventory(database.state, args.handle))
        m.require(len(wire.encode('utf-8')) <= args.max_bytes, 'complete transfer inventory exceeds --max-bytes')
        emit(stdout, wire)
    else:
        m.require(database.version == 3, 'transfer writes require schema 3; run consultation upgrade explicitly')
        if args.operation == 'import-transfer':
            import_(args, database, stdout)
        else:
            link(args, database, stdout)
    return 0
