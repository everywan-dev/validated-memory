"""Chronological incorporation relationships and review eligibility."""

from . import model as m
from . import lifecycle_model as lm


def affected(challenge, receipt):
    target = challenge['target']
    return m.applies(challenge['scope'], receipt['scope']) and any(
        item['identity'] == target['identity'] and item['sha256'] == target['unit']['sha256']
        for item in receipt['content']['units'])


def frontier(state, content, scope):
    receipt = dict(content=content, scope=scope)
    return sorted(event['id'] for event in state.events.values()
                  if event['kind'] == 'decision' and event['payload']['outcome'] == 'accept'
                  and state.events[event['payload']['submission']]['kind'] == 'challenge'
                  and affected(state.events[event['payload']['submission']]['payload'], receipt))


def gate(state, content, scope, retained=None):
    receipt = dict(content=content, scope=scope)
    for handle, decision in state.decisions.items():
        submission = state.events[handle]
        if submission['kind'] == 'challenge' and decision['payload']['outcome'] == 'accept' and affected(submission['payload'], receipt):
            m.require(decision['id'] in state.resolutions, f'{handle}: review required; inspect accepted challenge and resolve')
    if retained is not None:
        m.require(retained == frontier(state, content, scope), 'review required: acceptance history changed; reconsult after review')


def accepted(state, submission, decision):
    event = state.event(decision, ('decision',))
    m.require(event['payload']['submission'] == submission and event['payload']['outcome'] == 'accept'
              and state.decisions.get(submission) == event, 'decision is not the current acceptance; inspect disposition')
    return event


def descendant(state, newer, older):
    current = state.event(newer, ('binding', 'support-review'))
    original = state.event(older, ('binding', 'support-review'))
    m.require(current['payload']['identity'] == original['payload']['identity']
              and current['payload']['unit'] == original['payload']['unit'], 'binding changed canonical incorporated revision')
    walk = current
    while walk['id'] != older:
        m.require(walk['kind'] == 'support-review' and walk['prior'] is not None, 'binding is not a support-review descendant')
        walk = state.event(walk['prior'], ('binding', 'support-review'))
    m.require(state.binding(current['payload']['identity']) == current, 'resolved binding is not current; inspect current binding')
    return current


def incorporation_binding(state, handle, binding_id=None):
    event = state.event(handle, ('incorporation',))
    p = event['payload']
    accepted(state, p['proposal'], p['decision'])
    original = state.event(p['binding'], ('binding', 'support-review'))
    current = state.binding(original['payload']['identity'])
    return descendant(state, binding_id or current['id'], p['binding'])


def resolution_binding(state, event):
    p = event['payload']
    accepted(state, p['challenge'], p['decision'])
    m.require(state.resolutions.get(p['decision']) == event, 'resolution is not current for acceptance')
    remedy = p['remedy']
    if remedy['kind'] == 'incorporation':
        incorporation_binding(state, remedy['incorporation'])
    original = state.event(remedy['binding'], ('binding', 'support-review'))
    current = state.binding(original['payload']['identity'])
    return descendant(state, current['id'], remedy['binding'])


def receipt(state, use_id):
    use = state.event(use_id, ('use',))
    return state.event(use['payload']['receipt'], ('receipt',))['payload']


def root_revision(receipt):
    item = next(item for item in receipt['content']['units'] if item['identity'] == receipt['root'])
    return dict(identity=item['identity'], unit=dict(path=item['path'], sha256=item['sha256'],
                                                   text=item['text'], size=len(item['text'].encode('utf-8'))))


def validate_address(state, p):
    challenge = state.event(p['challenge'], ('challenge',))['payload']
    accepted(state, p['challenge'], p['decision'])
    resolution = state.event(p['resolution'], ('resolution',))
    m.require(resolution['payload']['challenge'] == p['challenge'] and resolution['payload']['decision'] == p['decision'],
              'address resolution differs from challenge acceptance')
    binding = resolution_binding(state, resolution)
    old, new = receipt(state, p['old_use']), receipt(state, p['new_use'])
    m.require(affected(challenge, old), 'old use is not affected by challenge')
    m.require(old['scope'] == new['scope'], 'address scope must equal old use scope')
    a, b = root_revision(new), root_revision(old)
    if a == b:
        m.require(not p['consumer_proof'], 'identical consumer must have empty proof')
    else:
        lm.proof(p['consumer_proof'], a, b)
    if p['mode'] == 'dependency-removed':
        m.require(not affected(challenge, new), 'dependency-removed still includes challenged revision')
    else:
        target = binding['payload']
        matching = [u for u in new['content']['units'] if u['identity'] == target['identity']
                    and u['sha256'] == target['unit']['sha256']]
        m.require(len(matching) == 1, 'replacement use omits resolved source revision')
        descendant(state, matching[0]['binding'], resolution['payload']['remedy']['binding'])
    m.require(new['snapshot']['heads'] == state.heads(), 'snapshot changed/stale: new use semantic heads; reconsult')
    gate(state, new['content'], new['scope'], new.get('review_frontier', []))


def add(state, event):
    kind, p, prior = event['kind'], event['payload'], event['prior']
    m.require(kind in ('proposal', 'decision') or prior is None, 'lifecycle artifact cannot have prior')
    if kind == 'proposal':
        identity = p['identity']
        registration = state.registrations.get(identity['project'])
        m.require(registration and p['authority'] == registration['payload']['source'], 'proposal authority mismatch')
        if prior:
            previous = state.event(prior, ('proposal',))['payload']
            m.require(all(p[k] == previous[k] for k in ('identity', 'unit', 'authority', 'predecessors')),
                      'renewal changes immutable candidate or predecessors')
        else:
            m.require(p['installed_binding'] is None and m.key(identity) not in state.bindings, 'initial proposal already bound')
        expected = p['expected']
        m.require(expected['workspace'] == state.workspace and expected['heads'] == state.heads(), 'proposal context heads mismatch')
        m.require({inv['project'] for inv in expected['projects']} == set(state.registrations), 'proposal project membership mismatch')
        for inv in expected['projects']:
            reg = state.registrations[inv['project']]['payload']
            m.require(inv['root'] == reg['root'] and inv['root_identity'] == reg['root_identity'], 'proposal registration mismatch')
        inv = next(inv for inv in expected['projects'] if inv['project'] == identity['project'])
        m.require(m.revision(p['unit']) in inv['knowledge'] and all(m.revision(f) in inv['support'] for f in p['support']),
                  'proposal inventory omits accepted material')
        for predecessor in p['predecessors']:
            m.require(m.revision(predecessor['unit']) in inv['knowledge'], 'proposal inventory omits predecessor')
        for ref in p['references']:
            m.require(ref['identity']['project'] in state.registrations, 'proposal reference project not enrolled')
        if p['installed_binding']:
            binding = state.event(p['installed_binding'], ('binding', 'support-review'))
            m.require(state.binding(identity) == binding and lm.declaration(binding['payload']) == lm.declaration(p),
                      'installed renewal declaration mismatch')
    elif kind == 'challenge':
        target = p['target']
        binding = state.event(target['binding'], ('binding', 'support-review'))
        m.require(state.binding(target['identity']) == binding and binding['payload']['unit'] == target['unit'], 'challenge target binding mismatch')
        m.require(m.applies(binding['payload']['scope'], p['scope']), 'challenge scope outside binding scope')
    elif kind == 'inspection':
        submission = state.event(p['submission'], ('proposal', 'challenge', 'binding', 'support-review'))
        m.require(p['observed_head'] == next(reversed(state.events)), 'inspection occurrence does not follow observed head')
        m.require(p['inspection_text'] == lm.inspection_line(submission), 'inspection material differs from submission')
        final = m.line('inspect', 'inspection recorded', id=event['id'])
        m.require(len((p['inspection_text'] + final).encode('utf-8')) <= p['max_bytes'], 'inspection output bound exceeded')
    elif kind == 'decision':
        state.event(p['submission'], ('proposal', 'challenge'))
        inspection = state.event(p['inspection'], ('inspection',))
        m.require(inspection['payload']['submission'] == p['submission'], 'decision inspection names different submission')
        head = state.decisions.get(p['submission'])
        m.require(prior == (head['id'] if head else None), 'decision prior is not current disposition')
        m.require(not head or head['payload'] != p, 'redundant disposition')
        state.decisions[p['submission']] = event
    elif kind == 'incorporation':
        proposal = state.event(p['proposal'], ('proposal',))['payload']
        accepted(state, p['proposal'], p['decision'])
        binding = state.event(p['binding'], ('binding', 'support-review'))
        m.require(state.binding(proposal['identity']) == binding and lm.declaration(binding['payload']) == lm.declaration(proposal),
                  'incorporation binding declaration mismatch')
        expected = {key: list(value) for key, value in proposal['expected']['heads'].items()}
        if proposal['installed_binding'] is None:
            expected['bindings'] = sorted([*expected['bindings'], binding['id']])
        else:
            m.require(proposal['installed_binding'] == binding['id'], 'incorporation installed binding changed')
        m.require(expected == state.heads(), 'incorporation context changed; renew proposal and accept fresh context')
    elif kind == 'resolution':
        challenge = state.event(p['challenge'], ('challenge',))['payload']
        decision = accepted(state, p['challenge'], p['decision'])
        m.require(p['decision'] not in state.resolutions, 'acceptance already resolved; new assertion needs new decision')
        remedy = p['remedy']
        binding = state.event(remedy['binding'], ('binding', 'support-review'))
        m.require(state.binding(binding['payload']['identity']) == binding, 'resolution binding is not current')
        inspection = state.event(p['inspection'], ('inspection',))
        m.require(inspection['payload']['submission'] == binding['id'] and inspection['sequence'] > decision['sequence'],
                  'resolution requires exact binding inspection after acceptance')
        if remedy['kind'] == 'incorporation':
            incorporation_binding(state, remedy['incorporation'], binding['id'])
            lm.proof(remedy['proof'], binding['payload'], challenge['target'])
        else:
            m.require(binding['payload']['identity'] == challenge['target']['identity']
                      and binding['payload']['unit'] == challenge['target']['unit'], 'review changes challenged canonical claim')
        m.require(m.applies(binding['payload']['scope'], challenge['scope']), 'resolution scope mismatch')
        state.resolutions[p['decision']] = event
    elif kind == 'address':
        validate_address(state, p)
    elif kind == 'publication':
        challenge = state.event(p['challenge'], ('challenge',))['payload']
        use_receipt = receipt(state, p['use'])
        m.require(affected(challenge, use_receipt) and use_receipt['root']['project'] == p['project'], 'publication use/project mismatch')
        m.require(p['project'] in state.registrations, 'publication project not registered')
    elif kind == 'reflection':
        publication = state.event(p['publication'], ('publication',))['payload']
        address = state.event(p['address'], ('address',))['payload']
        m.require(address['challenge'] == publication['challenge'] and address['old_use'] == publication['use'], 'reflection address mismatch')
        m.require(p['file']['path'] == publication['file']['path'] and p['file']['sha256'] != publication['file']['sha256'], 'reflection must change original tracked bytes')
        validate_address(state, address)
