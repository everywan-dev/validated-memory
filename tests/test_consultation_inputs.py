"""Captured-input boundaries exercised only through consultation subprocesses."""

import hashlib
import json

import pytest


class Inputs:
    def __init__(self, directory, cli):
        self.directory = directory
        self.cli = cli
        self.store = directory / 'capture.sqlite'

    def command(self, *args, code=0):
        result = self.cli('consultation', '--store', str(self.store), *args,
                          cwd=self.directory)
        assert result.returncode == code, (args, result.stdout, result.stderr)
        assert 'Traceback' not in result.stderr
        return result

    def project(self, alias='sample'):
        root = self.directory / alias
        root.mkdir()
        (root / 'knowledge').mkdir()
        (root / 'validated-memory.md').write_text('---\nid_prefix: unusual\n---\n', encoding='utf-8')
        self.unit(root, 'unusual-one')
        return root

    def unit(self, root, identity, body='A bounded fixture claim.\n', fields=''):
        path = root / 'knowledge' / (identity + '.md')
        path.write_text(f'---\nid: {identity}\nevidence: verifiable\n{fields}---\n{body}', encoding='utf-8')
        return path

    def register(self, root, code=0):
        return self.command('register', root.name, str(root), '--source', root.name, code=code)

    def bind(self, root, identity='unusual-one', support=('evidence.txt',), prior=None, code=0):
        args = ['review-support' if prior else 'bind', f'{root.name}:{identity}',
                '--authority', root.name, '--scope', 'exercise=capture',
                '--actor', 'fixture-agent', '--reason', 'Inspected supporting inputs.']
        if prior:
            args.extend(('--prior', prior))
        for path in support:
            args.extend(('--support', path))
        return self.command(*args, code=code)

    def read(self, root, code=0):
        return self.command('read', f'{root.name}:unusual-one', '--scope', 'exercise=capture', code=code)


@pytest.fixture
def inputs(tmp_path, run_cli):
    return Inputs(tmp_path, run_cli)


def test_configuration_is_validated_even_without_extension(inputs):
    root = inputs.project()
    (root / 'validated-memory.md').write_text('---\nid_prefix: unusual\nunknown_setting: no\n---\n', encoding='utf-8')
    result = inputs.register(root, code=1)
    assert 'unknown' in result.stderr
    assert not inputs.store.exists()


def test_nondefault_schema_and_id_prefix_are_captured_and_validated(inputs):
    root = inputs.project()
    (root / 'schemas').mkdir()
    schema = root / 'schemas' / 'local.md'
    schema.write_text('---\nfields:\n  - name: category\n    type: enum\n    values:\n      - operational\n---\n', encoding='utf-8')
    (root / 'validated-memory.md').write_text('---\nid_prefix: unusual\nextension:\n  schema: schemas/local.md\n  version: "1"\n---\n', encoding='utf-8')
    inputs.unit(root, 'unusual-one', fields='category: operational\n')
    inputs.register(root)
    (root / 'evidence.txt').write_bytes(b'External fixture evidence.\n')
    inputs.bind(root)
    lines = inputs.read(root).stdout.splitlines()
    receipt = json.loads(lines[1])['id']
    artifact = json.loads(inputs.command('show', receipt).stdout)['artifact']['payload']
    inventory = artifact['snapshot']['projects'][0]
    assert inventory['schema']['path'] == 'schemas/local.md'
    assert inventory['schema']['sha256'] == hashlib.sha256(schema.read_bytes()).hexdigest()
    inputs.unit(root, 'unusual-one', fields='category: unsupported\n')
    frozen = inputs.store.read_bytes()
    result = inputs.read(root, code=1)
    assert 'category' in result.stderr
    assert inputs.store.read_bytes() == frozen


def test_support_keeps_crlf_utf8_and_empty_content_exactly(inputs):
    root = inputs.project()
    unit = root / 'knowledge' / 'unusual-one.md'
    unit_bytes = unit.read_bytes().replace(b'\n', b'\r\n')
    unit.write_bytes(unit_bytes)
    inputs.register(root)
    content = 'First line\r\nCafé evidence\r\n'.encode('utf-8')
    (root / 'evidence.txt').write_bytes(content)
    (root / 'empty.txt').write_bytes(b'')
    binding = json.loads(inputs.bind(root, support=('evidence.txt', 'empty.txt')).stdout)['id']
    payload = json.loads(inputs.command('show', binding).stdout)['artifact']['payload']
    assert payload['unit']['text'].encode('utf-8') == unit_bytes
    by_path = {item['path']: item for item in payload['support']}
    for path, raw in [('evidence.txt', content), ('empty.txt', b'')]:
        assert by_path[path]['text'].encode('utf-8') == raw
        assert by_path[path]['size'] == len(raw)
        assert by_path[path]['sha256'] == hashlib.sha256(raw).hexdigest()
    inspected = json.loads(inputs.read(root).stdout.splitlines()[0])
    assert {item['path']: item['text'].encode('utf-8') for item in inspected['content']['support']} == {
        'evidence.txt': content, 'empty.txt': b''}


@pytest.mark.parametrize('kind', ['knowledge-file', 'knowledge-directory', 'support-file', 'support-directory'])
def test_consumed_symlink_files_and_directory_ancestors_refuse(inputs, kind):
    root = inputs.project()
    external = inputs.directory / 'external'
    external.mkdir()
    if kind == 'knowledge-file':
        original = root / 'knowledge' / 'unusual-one.md'
        original.rename(external / original.name)
        original.symlink_to(external / original.name)
    elif kind == 'knowledge-directory':
        inputs.unit(root, 'unusual-two').rename(external / 'unusual-two.md')
        (root / 'knowledge' / 'linked').symlink_to(external, target_is_directory=True)
    else:
        inputs.register(root)
        (external / 'evidence.txt').write_bytes(b'evidence\n')
        if kind == 'support-file':
            (root / 'evidence.txt').symlink_to(external / 'evidence.txt')
            path = 'evidence.txt'
        else:
            (root / 'linked').symlink_to(external, target_is_directory=True)
            path = 'linked/evidence.txt'
        frozen = inputs.store.read_bytes()
        inputs.bind(root, support=(path,), code=1)
        assert inputs.store.read_bytes() == frozen
        return
    inputs.register(root, code=1)
    assert not inputs.store.exists()


def test_per_file_limit_refuses_before_registration_store_creation(inputs):
    root = inputs.project()
    inputs.unit(root, 'unusual-one', body='x' * 1048576)
    result = inputs.register(root, code=1)
    assert '1 MiB' in result.stderr
    assert not inputs.store.exists()


@pytest.mark.parametrize('bound', ['file-count', 'aggregate-bytes'])
def test_capture_bounds_apply_across_all_registered_projects(inputs, bound):
    first, second = inputs.project('first'), inputs.project('second')
    for root in (first, second):
        count, size = (2050, 1) if bound == 'file-count' else (9, 1000000)
        for number in range(count):
            inputs.unit(root, f'unusual-{number}', body='x' * size)
    inputs.register(first)
    frozen = inputs.store.read_bytes()
    result = inputs.register(second, code=1)
    assert ('4096' if bound == 'file-count' else '16 MiB') in result.stderr
    assert inputs.store.read_bytes() == frozen


def test_review_overlay_cannot_excuse_another_active_missing_support(inputs):
    root = inputs.project()
    inputs.unit(root, 'unusual-two')
    (root / 'evidence.txt').write_bytes(b'Shared supporting content.\n')
    inputs.register(root)
    first = json.loads(inputs.bind(root).stdout)['id']
    inputs.bind(root, 'unusual-two')
    (root / 'evidence.txt').rename(root / 'replacement.txt')
    frozen = inputs.store.read_bytes()
    result = inputs.bind(root, support=('replacement.txt',), prior=first, code=1)
    assert 'evidence.txt' in result.stderr
    assert inputs.store.read_bytes() == frozen
    inputs.read(root, code=1)


def test_seventeenth_project_exceeds_enrolled_scope_limit(inputs):
    for number in range(16):
        inputs.register(inputs.project(f'project{number}'))
    frozen = inputs.store.read_bytes()
    result = inputs.register(inputs.project('project16'), code=1)
    assert '16' in result.stderr
    assert inputs.store.read_bytes() == frozen


def test_external_declared_schema_refuses_before_store_creation(inputs):
    root = inputs.project()
    (inputs.directory / 'external.md').write_text('---\nfields: []\n---\n', encoding='utf-8')
    (root / 'validated-memory.md').write_text('---\nextension:\n  schema: ../external.md\n  version: "1"\n---\n', encoding='utf-8')
    inputs.register(root, code=1)
    assert not inputs.store.exists()


@pytest.mark.parametrize('kind', ['canonical', 'support'])
def test_invalid_utf8_is_refused_without_publishing(inputs, kind):
    root = inputs.project()
    if kind == 'canonical':
        (root / 'knowledge' / 'unusual-one.md').write_bytes(b'\xff\xfe')
        inputs.register(root, code=1)
        assert not inputs.store.exists()
    else:
        inputs.register(root)
        (root / 'evidence.txt').write_bytes(b'\xff\xfe')
        frozen = inputs.store.read_bytes()
        inputs.bind(root, code=1)
        assert inputs.store.read_bytes() == frozen


@pytest.mark.parametrize('schema_spelling', ['./knowledge-extension.md', '././knowledge-extension.md',
                                              'schemas/../knowledge-extension.md', 'absolute'])
def test_supported_schema_spellings_capture_canonical_inventory(inputs, schema_spelling):
    root = inputs.project()
    (root / 'schemas').mkdir()
    schema = root / 'knowledge-extension.md'
    schema.write_text('---\nfields:\n  - name: domain\n    type: string\n---\n', encoding='utf-8')
    declaration = str(schema) if schema_spelling == 'absolute' else schema_spelling
    (root / 'validated-memory.md').write_text(
        f'---\nextension:\n  schema: {declaration}\n  version: "1"\nid_prefix: unusual\n---\n', encoding='utf-8')
    inputs.unit(root, 'unusual-one', fields='domain: operations\n')
    (root / 'evidence.txt').write_text('evidence')
    baseline = inputs.cli('validate', cwd=root)
    assert baseline.returncode == 0, baseline.stderr
    inputs.register(root)
    inputs.bind(root)
    receipt = json.loads(inputs.read(root).stdout.splitlines()[-1])
    history = json.loads(inputs.command('show', receipt['id']).stdout)['artifact']['payload']
    assert history['snapshot']['projects'][0]['schema']['path'] == 'knowledge-extension.md'


@pytest.mark.parametrize('unsafe', ['outside', 'symlink', 'symlink_parent'])
def test_schema_normalization_preserves_confinement_and_nofollow(inputs, unsafe):
    root = inputs.project()
    schema = root / 'knowledge-extension.md'
    schema.write_text('---\nfields: []\n---\n')
    if unsafe == 'outside':
        outside = inputs.directory / 'outside.md'
        outside.write_text('---\nfields: []\n---\n')
        declaration = './../outside.md'
    elif unsafe == 'symlink':
        (root / 'linked.md').symlink_to(schema)
        declaration = './linked.md'
    else:
        (root / 'linked').symlink_to(root, target_is_directory=True)
        declaration = 'linked/../knowledge-extension.md'
    (root / 'validated-memory.md').write_text(
        f'---\nextension:\n  schema: {declaration}\n  version: "1"\n---\n')
    inputs.register(root, code=1)
    assert not inputs.store.exists()
