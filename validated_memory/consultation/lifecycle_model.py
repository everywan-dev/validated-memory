"""Closed retained values for contribution and review operations."""

from . import model as m

FIELDS = {
    'proposal': 'identity unit authority scope support references predecessors expected installed_binding actor reason',
    'challenge': 'target kind statement scope actor reason',
    'inspection': 'submission observed_head max_bytes inspection_text inspection_sha256',
    'decision': 'submission inspection outcome actor reason',
    'incorporation': 'proposal decision binding actor reason',
    'resolution': 'challenge decision inspection remedy actor reason',
    'address': 'challenge decision resolution old_use new_use mode consumer_proof actor reason',
    'publication': 'challenge project use file actor reason',
    'reflection': 'publication address file actor reason',
}
DECLARATION = ('identity', 'unit', 'authority', 'scope', 'support', 'references')


def declaration(payload):
    return {key: payload[key] for key in DECLARATION}


def canonical_file(value):
    m.file(value, True)
    m.require(value['path'].startswith('knowledge/') and value['path'].endswith('.md'),
              'proof requires canonical knowledge paths')
    data = m.frontmatter(value['text'])
    m.pattern(data.get('id'), m.ID_PATTERN.pattern)
    return data


def snapshot(value):
    m.obj(value, 'version workspace projects heads')
    m.require(type(value['version']) is int and value['version'] == 1, 'unsupported snapshot version')
    m.uid(value['workspace'])
    m.array(value['projects'], 1, 16, sort=lambda item: item['project'])
    total = count = 0
    for inv in value['projects']:
        m.inventory(inv, True)
        files = {f['path']: f for f in [inv['config'], inv['schema'], *inv['knowledge'], *inv['support']] if f}
        count += len(files)
        total += sum(f['size'] for f in files.values())
    m.require(count <= 4096 and total <= 16777216, 'prospective inventory bound exceeded')
    m.obj(value['heads'], 'registrations bindings conflicts choices')
    for values in value['heads'].values():
        m.array(values, maximum=10000, sort=lambda x: x)
        for handle in values:
            m.sha(handle)


def payload(kind, value):
    m.obj(value, 'version ' + FIELDS[kind])
    m.require(type(value['version']) is int and value['version'] == 1, 'unsupported lifecycle payload version')
    for field in ('actor', 'reason'):
        if field in value:
            m.text(value[field], 256 if field == 'actor' else 4096)
    for field in ('submission', 'observed_head', 'inspection_sha256', 'inspection', 'proposal',
                  'decision', 'binding', 'challenge', 'resolution', 'old_use', 'new_use',
                  'use', 'publication', 'address'):
        if field in value:
            m.sha(value[field])
    if 'scope' in value:
        m.scope(value['scope'])
    if 'file' in value:
        m.file(value['file'], True)
    if kind == 'proposal':
        m.payload('binding', dict(version=1, **declaration(value), actor=value['actor'], reason=value['reason']))
        m.array(value['predecessors'], maximum=4096, sort=lambda x: m.key(x['identity']))
        ids = []
        for predecessor in value['predecessors']:
            m.obj(predecessor, 'identity unit')
            m.identity(predecessor['identity'])
            data = canonical_file(predecessor['unit'])
            m.require(predecessor['identity']['project'] == value['identity']['project']
                      and data['id'] == predecessor['identity']['unit'], 'predecessor identity mismatch')
            ids.append(data['id'])
        m.require(sorted(ids) == sorted(m.frontmatter(value['unit']['text']).get('supersedes', [])),
                  'proposal predecessor mapping mismatch')
        snapshot(value['expected'])
        if value['installed_binding'] is not None:
            m.sha(value['installed_binding'])
    elif kind == 'challenge':
        m.obj(value['target'], 'identity binding unit')
        m.identity(value['target']['identity'])
        m.sha(value['target']['binding'])
        data = canonical_file(value['target']['unit'])
        m.require(data['id'] == value['target']['identity']['unit'], 'challenge target identity mismatch')
        m.require(value['kind'] in ('factual', 'policy'), 'unknown challenge kind')
        statement = value['statement']
        m.obj(statement, 'sha256 size text')
        m.sha(statement['sha256'])
        m.integer(statement['size'], 1, 65536)
        m.text(statement['text'], 65536)
        raw = statement['text'].encode('utf-8')
        m.require(len(raw) == statement['size'] and m.byte_digest(raw) == statement['sha256'], 'statement bytes mismatch')
    elif kind == 'inspection':
        m.integer(value['max_bytes'], 2048, 1048576)
        m.require(type(value['inspection_text']) is str, 'inspection text must be text')
        m.require(m.byte_digest(value['inspection_text'].encode('utf-8')) == value['inspection_sha256'], 'inspection digest mismatch')
    elif kind == 'decision':
        m.require(value['outcome'] in ('accept', 'reject', 'defer'), 'unknown decision outcome')
    elif kind == 'resolution':
        remedy = value['remedy']
        m.require(type(remedy) is dict and remedy.get('kind') in ('review', 'incorporation'), 'unknown remedy')
        m.obj(remedy, 'kind binding' + (' incorporation proof' if remedy['kind'] == 'incorporation' else ''))
        m.sha(remedy['binding'])
        if remedy['kind'] == 'incorporation':
            m.sha(remedy['incorporation'])
            proof_files(remedy['proof'])
    elif kind == 'address':
        m.require(value['mode'] in ('replacement', 'dependency-removed'), 'unknown address mode')
        if value['consumer_proof']:
            proof_files(value['consumer_proof'])
        else:
            m.array(value['consumer_proof'])
    elif kind == 'publication':
        m.uid(value['project'])
        m.require(value['file']['path'].split('/')[0] not in ('knowledge', 'memory', '.git', '.validated-memory')
                  and value['file']['path'] != 'validated-memory.md', 'publication names excluded canonical/private path')


def proof_files(proof):
    m.array(proof, 2, 128)
    for file in proof:
        canonical_file(file)


def proof(proof, newer, older):
    """Validate retained same-project supersession, permitting retired path moves."""
    proof_files(proof)
    m.require(newer['identity']['project'] == older['identity']['project'], 'cross-project successor forbidden')
    ids = [m.frontmatter(f['text'])['id'] for f in proof]
    m.require(len(ids) == len(set(ids)) and ids[0] == newer['identity']['unit']
              and ids[-1] == older['identity']['unit'] and proof[0] == newer['unit']
              and all(proof[-1][key] == older['unit'][key] for key in ('sha256', 'size', 'text')),
              'successor proof endpoint mismatch')
    for index, file in enumerate(proof[:-1]):
        m.require(ids[index + 1] in m.frontmatter(file['text']).get('supersedes', []), 'proof lacks canonical supersession')


def inspection_line(event):
    return m.line('inspect', 'historical material; not a current evidence check', id=event['id'], artifact=event)
