"""Bounded historical consequence enumeration with separate live observations."""

from . import inputs, model as m, lifecycle_state as ls
from .command import capture, recapture
from .lifecycle_command import current_use, current_address


def observation(action):
    try:
        action()
        return dict(status='current', reason='')
    except (m.Refusal, OSError) as error:
        reason = str(error)
        if 'review required' in reason:
            status = 'review required'
        elif 'snapshot changed/stale' in reason or 'receipt content changed' in reason or 'publication bytes changed' in reason:
            status = 'stale snapshot'
        elif any(word in reason for word in ('unavailable', 'capture failed', 'root identity', 'input metadata')):
            status = 'unavailable'
        else:
            status = 'conflict/invalid binding'
        return dict(status=status, reason=reason)


def report(args, state):
    challenge = state.event(args.handle, ('challenge',))['payload']
    uses = sorted((e for e in state.events.values() if e['kind'] == 'use' and ls.affected(challenge, ls.receipt(state, e['id']))), key=lambda e: e['id'])
    publications = sorted((e for e in state.events.values() if e['kind'] == 'publication' and e['payload']['challenge'] == args.handle), key=lambda e: e['id'])
    m.require(len(uses) <= 128 and len(publications) <= 128, 'reconcile exceeds 128-use/publication bound; inspect retained artifacts individually')
    decision = state.decisions.get(args.handle)
    disposition = decision['payload']['outcome'] if decision else 'none'
    review = 'not-open' if disposition != 'accept' else ('resolved' if decision['id'] in state.resolutions else 'open')
    resolutions = sorted(e['id'] for e in state.events.values() if e['kind'] == 'resolution' and e['payload']['challenge'] == args.handle)
    failure = None
    try:
        projects, snapshot = capture(state)
    except (m.Refusal, OSError) as error:
        failure = str(error)
        projects = snapshot = None
    observed_files = []

    def observe(action):
        return dict(status='unavailable', reason=failure) if failure else observation(action)

    def file_check(publication, expected):
        actual = inputs.publication(state, publication['project'], expected['path'], projects)
        observed_files.append((publication['project'], actual))
        m.require(actual == expected, 'publication bytes changed; inspect and record reflection')

    def reflection_check(event):
        p = event['payload']
        publication = state.event(p['publication'], ('publication',))['payload']
        current_address(state, state.event(p['address'], ('address',)), projects, snapshot)
        file_check(publication, p['file'])

    rows = []
    for use in uses:
        addresses = sorted((e for e in state.events.values() if e['kind'] == 'address' and e['payload']['challenge'] == args.handle and e['payload']['old_use'] == use['id']), key=lambda e: e['id'])
        pubs = []
        for publication in publications:
            if publication['payload']['use'] != use['id']:
                continue
            reflections = sorted((e for e in state.events.values() if e['kind'] == 'reflection' and e['payload']['publication'] == publication['id']), key=lambda e: e['id'])
            pubs.append(dict(id=publication['id'], observation=observe(lambda p=publication['payload']: file_check(p, p['file'])),
                             reflections=[dict(id=e['id'], observation=observe(lambda e=e: reflection_check(e))) for e in reflections]))
        rows.append(dict(id=use['id'], dependence='declared', observation=observe(lambda: current_use(state, use['id'], projects, snapshot)),
                         addresses=[dict(id=e['id'], new_use=e['payload']['new_use'], observation=observe(lambda e=e: current_address(state, e, projects, snapshot))) for e in addresses],
                         publications=pubs))
    if not failure:
        try:
            recapture(state, snapshot)
            for project, file in observed_files:
                m.require(inputs.publication(state, project, file['path'], projects) == file, 'publication changed during report; retry')
        except (m.Refusal, OSError) as error:
            failure = str(error)
    if failure:
        for row in rows:
            row['observation'] = dict(status='unavailable', reason=failure)
            for address in row['addresses']:
                address['observation'] = dict(status='unavailable', reason=failure)
            for publication in row['publications']:
                publication['observation'] = dict(status='unavailable', reason=failure)
                for reflection in publication['reflections']:
                    reflection['observation'] = dict(status='unavailable', reason=failure)
    wire = m.line('reconcile', 'reported', id=args.handle, decision=decision['id'] if decision else None,
                  disposition=disposition, review=review, resolutions=resolutions, uses=rows)
    m.require(len(wire.encode('utf-8')) <= 1048576, 'reconcile exceeds 1 MiB complete output bound; inspect retained artifacts individually')
    return wire
