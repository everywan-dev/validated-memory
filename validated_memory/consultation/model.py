"""Closed consultation values and deterministic encoding."""

import datetime
import hashlib
import json
import re
import uuid
from pathlib import PurePosixPath

from ..contract import ID_PATTERN
from ..frontmatter import FrontmatterError, parse

LIMITATION = ('Inspection is not checked use. Hashes do not prove entailment; '
              'anchor verdicts are not checked.')
KINDS = ('registration', 'relocation', 'checkpoint', 'binding', 'support-review',
         'conflict', 'choice', 'receipt', 'use')
LIFECYCLE_KINDS = ('proposal', 'challenge', 'inspection', 'decision', 'incorporation',
                   'resolution', 'address', 'publication', 'reflection')


class Refusal(Exception):
    """A gating refusal with an affected identity and recovery instruction."""


def require(condition, message):
    if not condition:
        raise Refusal(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return byte_digest(canonical(value).encode('utf-8'))


def byte_digest(value):
    return hashlib.sha256(value).hexdigest()


def decode(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f'duplicate JSON key {key}; restore intact history')
            result[key] = value
        return result
    try:
        value = json.loads(text, object_pairs_hook=pairs,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        canonical(value).encode('utf-8')
        return value
    except (ValueError, UnicodeError, RecursionError) as error:
        raise Refusal('invalid JSON payload; restore intact history') from error


def obj(value, fields):
    require(type(value) is dict and set(value) == set(fields.split()),
            f'invalid object fields, expected {fields}; inspect input or restore intact history')


def integer(value, low, high=None):
    require(type(value) is int and value >= low and (high is None or value <= high),
            f'invalid integer bound {low}..{high}; inspect input')


def text(value, maximum=None):
    require(type(value) is str and bool(value.strip()), 'nonblank text required; inspect input')
    try:
        size = len(value.encode('utf-8'))
    except UnicodeError as error:
        raise Refusal('invalid Unicode text; inspect input') from error
    require(maximum is None or size <= maximum, f'text exceeds {maximum} bytes; narrow input')


def pattern(value, regex):
    require(type(value) is str and re.fullmatch(regex, value) is not None,
            'invalid identifier spelling; inspect input')


def name(value):
    pattern(value, r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')


def sha(value):
    pattern(value, '[0-9a-f]{64}')


def uid(value):
    try:
        parsed = uuid.UUID(value) if type(value) is str else None
    except ValueError:
        parsed = None
    require(parsed is not None and parsed.version == 4 and str(parsed) == value,
            'invalid workspace/project UUID; restore intact history')


def identity(value):
    obj(value, 'project unit')
    uid(value['project'])
    pattern(value['unit'], ID_PATTERN.pattern)


def key(value):
    return value['project'], value['unit']


def ident(pair):
    return dict(zip(('project', 'unit'), pair))


def scope(value):
    require(type(value) is dict, 'scope must be a mapping; supply --scope KEY=VALUE')
    integer(len(value), 1, 32)
    for field, val in value.items():
        name(field)
        text(val, 256)


def applies(declaration, requested):
    return all(requested.get(k) == v for k, v in declaration.items())


def path(value, root=False):
    text(value, 4096)
    require('\x00' not in value and '\\' not in value,
            'invalid path; use a confined POSIX path')
    pieces = value.split('/')[1:] if root else value.split('/')
    require((value.startswith('/') if root else not value.startswith('/'))
            and all(p and p not in ('.', '..') for p in pieces),
            f'invalid path {value}; use a canonical confined path')


def node(value):
    obj(value, 'device inode')
    integer(value['device'], 0)
    integer(value['inode'], 0)


def file(value, captured=False):
    obj(value, 'path sha256 size text' if captured else 'path sha256 size')
    path(value['path'])
    sha(value['sha256'])
    integer(value['size'], 0, 1048576)
    if captured:
        require(type(value['text']) is str, 'captured text missing; restore intact history')
        raw = value['text'].encode('utf-8')
        require(len(raw) == value['size'] and byte_digest(raw) == value['sha256'],
                f"captured bytes disagree: {value['path']}; restore intact history")


def revision(value):
    return {k: value[k] for k in ('path', 'sha256', 'size')}


def array(value, minimum=0, maximum=None, sort=None):
    require(type(value) is list, 'array required; inspect input')
    integer(len(value), minimum, maximum)
    if sort:
        keys = [sort(item) for item in value]
        require(keys == sorted(keys) and len(keys) == len(set(keys)),
                'array must be sorted and unique; inspect input or restore history')


def inventory(value, project=False):
    obj(value, 'version config schema knowledge support' +
        (' project root root_identity' if project else ''))
    require(type(value['version']) is int and value['version'] == 1, 'unsupported inventory version')
    if project:
        uid(value['project'])
        path(value['root'], True)
        node(value['root_identity'])
    file(value['config'])
    require(value['config']['path'] == 'validated-memory.md', 'invalid configuration inventory path')
    if value['schema'] is not None:
        file(value['schema'])
    seen = {value['config']['path']: value['config']}
    if value['schema']:
        schema = value['schema']
        require(seen.setdefault(schema['path'], schema) == schema,
                'file inventory roles disagree; restore intact history')
    for field in ('knowledge', 'support'):
        array(value[field], maximum=4096)
        for member in value[field]:
            file(member)
            if field == 'knowledge':
                require(member['path'].startswith('knowledge/') and member['path'].endswith('.md'),
                        'invalid knowledge inventory member')
            previous = seen.setdefault(member['path'], member)
            require(previous == member, 'file inventory roles disagree; restore intact history')
        array(value[field], sort=lambda x: x['path'])
    require(len(seen) <= 4096, '4096 distinct inventory path limit exceeded; restore intact history')
    require(sum(member['size'] for member in seen.values()) <= 16 * 1024 * 1024,
            '16 MiB aggregate inventory limit exceeded; restore intact history')


def neutral(value):
    return {k: value[k] for k in ('version', 'config', 'schema', 'knowledge', 'support')}


def candidate(value):
    obj(value, 'identity binding unit_sha256')
    identity(value['identity'])
    sha(value['binding'])
    sha(value['unit_sha256'])


def timestamp(value):
    pattern(value, r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z')
    try:
        datetime.datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError as error:
        raise Refusal('invalid event timestamp; restore intact history') from error


def frontmatter(value):
    try:
        return parse(value)
    except FrontmatterError as error:
        raise Refusal('invalid retained canonical frontmatter; restore intact history') from error


def payload(kind, value):
    if kind in LIFECYCLE_KINDS:
        from .lifecycle_model import payload as lifecycle_payload
        lifecycle_payload(kind, value)
        return
    fields = {
        'registration': 'alias project root source root_identity inventory_sha256',
        'relocation': 'alias project root source root_identity inventory_sha256 checkpoint actor reason',
        'checkpoint': 'project registration inventory inventory_sha256 actor reason',
        'binding': 'identity unit authority scope actor reason support references',
        'support-review': 'identity unit authority scope actor reason support references',
        'conflict': 'scope candidates lineage actor reason',
        'choice': 'conflict candidate scope actor reason',
        'receipt': 'root scope max_bytes snapshot snapshot_sha256 content content_sha256 inspection_text inspection_sha256',
        'use': 'root receipt snapshot_sha256 content_sha256 scope',
    }
    require(kind in fields, 'unknown event kind; upgrade deliberately or restore history')
    extra = ' review_frontier' if kind == 'receipt' and value.get('version') == 2 else ''
    obj(value, 'version ' + fields[kind] + extra)
    require(type(value['version']) is int and value['version'] in ((1, 2) if kind == 'receipt' else (1,)), 'unsupported payload version')
    if extra:
        array(value['review_frontier'], maximum=10000, sort=lambda x: x)
        for handle in value['review_frontier']:
            sha(handle)
    for field in ('actor', 'reason'):
        if field in value:
            text(value[field], 256 if field == 'actor' else 4096)
    for field in ('alias', 'source', 'authority'):
        if field in value:
            name(value[field])
    for field in ('inventory_sha256', 'snapshot_sha256', 'content_sha256', 'inspection_sha256',
                  'checkpoint', 'registration', 'conflict', 'receipt'):
        if field in value:
            sha(value[field])
    if 'project' in value:
        uid(value['project'])
    if 'root_identity' in value:
        node(value['root_identity'])
        path(value['root'], True)
    elif 'root' in value:
        identity(value['root'])
    if 'scope' in value:
        scope(value['scope'])
    if kind == 'checkpoint':
        inventory(value['inventory'])
        require(digest(value['inventory']) == value['inventory_sha256'], 'checkpoint inventory hash mismatch')
    if kind in ('binding', 'support-review'):
        identity(value['identity'])
        file(value['unit'], True)
        data = frontmatter(value['unit']['text'])
        require(data.get('id') == value['identity']['unit'] and data.get('evidence') in
                ('measured', 'verifiable', 'hypothesis'), 'binding canonical identity/evidence mismatch')
        require(value['unit']['path'].startswith('knowledge/') and value['unit']['path'].endswith('.md'),
                'binding unit must be canonical knowledge')
        array(value['support'], 1, 64)
        for support in value['support']:
            file(support, True)
        array(value['support'], sort=lambda x: x['path'])
        array(value['references'], maximum=64)
        for ref in value['references']:
            obj(ref, 'identity unit_sha256')
            identity(ref['identity'])
            sha(ref['unit_sha256'])
        array(value['references'], sort=lambda x: key(x['identity']))
    if kind == 'conflict':
        array(value['candidates'], 2, 64)
        for item in value['candidates']:
            candidate(item)
        array(value['candidates'], sort=lambda x: key(x['identity']))
        array(value['lineage'], maximum=64)
        for edge in value['lineage']:
            obj(edge, 'before after proof')
            identity(edge['before'])
            identity(edge['after'])
            array(edge['proof'], 2, 128)
            for member in edge['proof']:
                file(member, True)
        array(value['lineage'], sort=lambda x: key(x['before']))
    if kind == 'choice':
        candidate(value['candidate'])
    if kind == 'receipt':
        integer(value['max_bytes'], 2048, 1048576)
        snap = value['snapshot']
        obj(snap, 'version workspace projects heads')
        require(type(snap['version']) is int and snap['version'] == 1, 'unsupported snapshot version')
        uid(snap['workspace'])
        array(snap['projects'], 1, 16)
        for item in snap['projects']:
            inventory(item, True)
        array(snap['projects'], sort=lambda x: x['project'])
        obj(snap['heads'], 'registrations bindings conflicts choices')
        for ids in snap['heads'].values():
            array(ids, maximum=10000)
            for handle in ids:
                sha(handle)
            array(ids, sort=lambda x: x)
        obj(value['content'], 'units support')
        array(value['content']['units'], 1, 128)
        for unit in value['content']['units']:
            obj(unit, 'identity binding path sha256 text')
            identity(unit['identity'])
            sha(unit['binding'])
            file({'size': len(unit['text'].encode('utf-8')), **{k: unit[k] for k in ('path', 'sha256', 'text')}}, True)
        array(value['content']['units'], sort=lambda x: key(x['identity']))
        array(value['content']['support'], maximum=4096)
        for support in value['content']['support']:
            obj(support, 'project path sha256 text')
            uid(support['project'])
            file({'size': len(support['text'].encode('utf-8')), **{k: support[k] for k in ('path', 'sha256', 'text')}}, True)
        array(value['content']['support'], sort=lambda x: (x['project'], x['path']))
        require(digest(snap) == value['snapshot_sha256'] and digest(value['content']) == value['content_sha256'],
                'receipt content/snapshot digest mismatch; restore intact history')
        require(type(value['inspection_text']) is str and
                byte_digest(value['inspection_text'].encode('utf-8')) == value['inspection_sha256'],
                'receipt inspection digest mismatch')
        require(value['inspection_text'] == line('read', 'inspected', root=value['root'],
                scope=value['scope'], content=value['content'], limitation=LIMITATION),
                'receipt inspection differs from captured content')


def line(operation, status, **fields):
    return canonical(dict(schema_version=1, operation=operation, status=status, **fields)) + '\n'


def event_id(kind, prior, value):
    return digest(dict(kind=kind, prior=prior, payload=value))


def semantic(value):
    return {k: v for k, v in value.items() if k not in ('actor', 'reason')}


def overlaps(left, right):
    a, b = PurePosixPath(left), PurePosixPath(right)
    return a == b or a in b.parents or b in a.parents
