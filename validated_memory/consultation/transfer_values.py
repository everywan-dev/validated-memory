"""Closed portable history values and deterministic review wire encoding."""

from . import model as m, lifecycle_model as lm

CAP = 8388608
WIRE = 1048576
DISCLOSURE = ('Complete workspace history includes unrelated projects, absolute paths, '
              'retained publications and nested imports. Live origin is not checked.')
LIMITATION = 'Historical material; live origin not checked; attributed correspondence is not semantic proof.'
STATUS = ('review-required', 'known-superseded', 'binding-changed', 'acceptance-withdrawn',
          'conflict', 'origin-review-stale', 'local-incorporation-pending', 'historical')


def qualified(value):
    m.obj(value, 'workspace identity')
    m.uid(value['workspace'])
    m.identity(value['identity'])


def origin(value):
    m.obj(value, 'workspace identity unit_sha256 binding')
    m.uid(value['workspace'])
    m.identity(value['identity'])
    m.sha(value['unit_sha256'])
    m.sha(value['binding'])


def origin_key(value):
    return (value['workspace'], *m.key(value['identity']))


def frontier(value):
    m.obj(value, 'origins links')
    m.array(value['origins'], maximum=16, sort=lambda item: item['workspace'])
    for row in value['origins']:
        m.obj(row, 'workspace head')
        m.uid(row['workspace'])
        m.sha(row['head'])
    m.array(value['links'], maximum=512, sort=lambda item: (item['workspace'], item['link']))
    for row in value['links']:
        m.obj(row, 'workspace link')
        m.uid(row['workspace'])
        m.sha(row['link'])


def lineage(value):
    m.array(value, maximum=128, sort=lambda item: m.key(item['identity']))
    for item in value:
        m.obj(item, 'identity file')
        m.identity(item['identity'])
        data = lm.canonical_file(item['file'])
        m.require(data['id'] == item['identity']['unit'], 'lineage identity mismatch')


def transfer(value):
    m.obj(value, 'lineage frontier origins')
    lineage(value['lineage'])
    frontier(value['frontier'])
    m.array(value['origins'], maximum=512, sort=lambda row: (row['local']['workspace'], *m.key(row['local']['identity']), *origin_key(row['origin'])))
    for row in value['origins']:
        m.obj(row, 'local mode origin link head scope evidence_sha256 actor reason live_origin')
        qualified(row['local'])
        m.require(row['mode'] in ('retain', 'independent'), 'unknown origin disposition')
        origin(row['origin'])
        m.obj(row['link'], 'workspace id')
        m.uid(row['link']['workspace'])
        m.sha(row['link']['id'])
        m.sha(row['head'])
        m.scope(row['scope'])
        m.array(row['evidence_sha256'], maximum=64, sort=lambda x: x)
        for sha in row['evidence_sha256']:
            m.sha(sha)
        m.text(row['actor'], 256)
        m.text(row['reason'], 4096)
        m.require(row['live_origin'] == 'not-checked', 'origin freshness cannot be asserted')


def capsule(value):
    m.obj(value, 'format version workspace source_store_path storage_version receipt events sha256')
    m.require(value['format'] == 'validated-memory-transfer' and type(value['version']) is int and value['version'] == 1,
              'unsupported transfer format/version')
    m.uid(value['workspace'])
    m.path(value['source_store_path'], root=True)
    m.require(type(value['storage_version']) is int and value['storage_version'] in (1, 2, 3), 'unsupported source storage version')
    m.sha(value['receipt'])
    m.sha(value['sha256'])
    m.array(value['events'], 1, 10000)
    m.require(m.digest({k: v for k, v in value.items() if k != 'sha256'}) == value['sha256'], 'capsule digest mismatch')
    m.require(len((m.canonical(value) + '\n').encode('utf-8')) <= CAP, 'capsule exceeds 8 MiB limit')
    m.require(sum(len(m.canonical(e.get('payload')).encode('utf-8')) for e in value['events']) <= 33554432,
              'source history exceeds 32 MiB')


def payload(kind, p):
    if kind == 'transfer-import':
        m.obj(p, 'version capsule actor reason')
        capsule(p['capsule'])
    else:
        m.obj(p, 'version mode proposal origin import from dependencies predecessors observed lineage max_bytes inspection_text inspection_sha256 actor reason')
        m.require(p['mode'] in ('retain', 'independent'), 'unknown transfer mode')
        m.sha(p['proposal'])
        origin(p['origin'])
        m.require((p['import'] is None) == (p['mode'] == 'independent') and
                  (p['from'] is None) == (p['mode'] == 'retain'), 'transfer mode handles mismatch')
        for field in ('import', 'from'):
            if p[field] is not None:
                m.sha(p[field])
        m.array(p['dependencies'], maximum=64, sort=lambda item: m.key(item['origin']))
        locals_seen = set()
        for row in p['dependencies']:
            m.obj(row, 'origin local unit_sha256 incorporation')
            m.identity(row['origin'])
            m.identity(row['local'])
            m.sha(row['unit_sha256'])
            m.sha(row['incorporation'])
            m.require(m.key(row['local']) not in locals_seen, 'dependency targets must be one-to-one')
            locals_seen.add(m.key(row['local']))
        m.array(p['predecessors'], maximum=128, sort=lambda item: (*m.key(item['origin']), m.key(item['local']) if item['local'] else ('', '')))
        for row in p['predecessors']:
            m.obj(row, 'origin unit_sha256 mode local local_sha256')
            m.identity(row['origin'])
            m.sha(row['unit_sha256'])
            m.require(row['mode'] in ('mapped', 'origin-only'), 'unknown predecessor disposition')
            if row['mode'] == 'mapped':
                m.identity(row['local'])
                m.sha(row['local_sha256'])
            else:
                m.require(row['local'] is None and row['local_sha256'] is None, 'origin-only cannot name local counterpart')
        frontier(p['observed'])
        lineage(p['lineage'])
        m.integer(p['max_bytes'], 2048, WIRE)
        m.require(type(p['inspection_text']) is str and m.byte_digest(p['inspection_text'].encode('utf-8')) == p['inspection_sha256'], 'transfer inspection digest mismatch')
    m.require(type(p['version']) is int and p['version'] == 1, 'unsupported transfer payload version')
    m.text(p['actor'], 256)
    m.text(p['reason'], 4096)


def link_key(state, p):
    proposal = state.event(p['proposal'], ('proposal',))['payload']
    return (*m.key(proposal['identity']), proposal['unit']['sha256'], *origin_key(p['origin']))


def semantic(p):
    return {k: v for k, v in p.items() if k not in ('actor', 'reason', 'max_bytes', 'inspection_text', 'inspection_sha256')}
