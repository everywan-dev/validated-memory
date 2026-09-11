"""Subprocess contracts for the immutable consultation store and failure boundaries."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCOPE = ['--scope', 'task=planning']
ATTR = ['--actor', 'fixture agent', '--reason', 'inspected declared support']


class Workspace:
    def __init__(self, root, run_cli):
        self.root = root
        self.store = root / 'workspace.sqlite3'
        self.project = root / 'project'
        self.project.mkdir()
        result = run_cli('init', cwd=self.project)
        assert result.returncode == 0, result.stderr
        self.unit('kb-a')
        (self.project / 'source.txt').write_text('retained evidence\n')

    def unit(self, name, evidence='verifiable', supersedes=''):
        (self.project / 'knowledge' / (name + '.md')).write_text(
            f'---\nid: {name}\nevidence: {evidence}\nanchors: []\n{supersedes}---\n\nClaim {name}.\n')

    def call(self, *args, env=None):
        return subprocess.run([sys.executable, '-P', '-m', 'validated_memory', 'consultation',
                               '--store', str(self.store), *args], cwd=self.root,
                              env=dict(os.environ, PYTHONPATH=str(REPO), **(env or {})),
                              capture_output=True, text=True, timeout=20)

    def ok(self, *args):
        result = self.call(*args)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout.splitlines()[-1])

    def enroll(self):
        return self.ok('register', 'project', str(self.project), '--source', 'policy')

    def bind(self, name='kb-a', *refs):
        return self.ok('bind', 'project:' + name, '--support', 'source.txt', '--authority', 'policy',
                       *SCOPE, *ATTR, *sum((['--reference', r] for r in refs), []))

    def events(self):
        with sqlite3.connect(self.store) as db:
            return db.execute('SELECT id,kind,payload FROM events ORDER BY sequence').fetchall()


@pytest.fixture
def workspace(tmp_path, run_cli):
    return Workspace(tmp_path, run_cli)


def tree(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_missing_store_read_operations_and_invalid_register_create_nothing(workspace):
    w = workspace
    before = tree(w.root)
    for command in ('show', 'check-use'):
        result = w.call(command, 'a' * 64)
        assert result.returncode == 1
        assert 'unavailable' in result.stderr
    result = w.call('register', 'bad alias', str(w.project), '--source', 'policy')
    assert result.returncode == 2
    assert tree(w.root) == before


def test_initial_preflight_and_incomplete_initialization_recovery(workspace):
    w = workspace
    old = (w.project / 'validated-memory.md').read_bytes()
    (w.project / 'validated-memory.md').write_text('invalid configuration')
    assert w.call('register', 'project', str(w.project), '--source', 'policy').returncode == 1
    assert not w.store.exists()
    (w.project / 'validated-memory.md').write_bytes(old)
    failed = w.call('register', 'project', str(w.project), '--source', 'policy',
                    env={'VALIDATED_MEMORY_CONSULTATION_FAULT': 'after-insert'})
    assert failed.returncode == 1
    assert w.store.exists()
    assert w.call('show', 'a' * 64).returncode == 1
    assert w.call('register', 'project', str(w.project), '--source', 'policy').returncode == 1
    result = w.ok('recover')
    assert result['status'] == 'recovered' and 'id' not in result
    assert w.events() == []
    w.enroll()


def test_show_is_historical_and_works_after_source_disappears(workspace):
    w = workspace
    registration = w.enroll()
    binding = w.bind()
    w.project.rename(w.root / 'moved')
    before = tree(w.root)
    result = w.ok('show', binding['id'])
    assert result['artifact']['payload']['support'][0]['text'] == 'retained evidence\n'
    assert 'not a current' in result['status']
    assert w.ok('show', registration['id'])['id'] == registration['id']
    assert tree(w.root) == before


@pytest.mark.parametrize('mutation', ['unknown_payload', 'wrong_type', 'duplicate_key', 'bad_hash',
                                     'sequence_gap', 'unknown_kind', 'extra_table', 'extra_trigger',
                                     'metadata_version', 'partial_schema', 'receipt_closure'])
def test_store_corruption_refuses_entire_history_without_writes(workspace, mutation):
    w = workspace
    w.enroll()
    binding = w.bind()
    receipt = w.ok('read', 'project:kb-a', *SCOPE)
    with sqlite3.connect(w.store) as db:
        db.execute('PRAGMA ignore_check_constraints=ON')
        if mutation in ('unknown_payload', 'wrong_type', 'duplicate_key', 'bad_hash', 'receipt_closure'):
            target = receipt['id'] if mutation == 'receipt_closure' else binding['id']
            kind, prior, payload = db.execute('SELECT kind,prior,payload FROM events WHERE id=?', (target,)).fetchone()
            value = json.loads(payload)
            if mutation == 'unknown_payload':
                value['extra'] = True
            elif mutation == 'wrong_type':
                value['unit']['size'] = True
            elif mutation == 'receipt_closure':
                value['content']['support'] = []
            encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
            if mutation == 'duplicate_key':
                encoded = encoded[:-1] + ',"version":1}'
            handle = hashlib.sha256(json.dumps(dict(kind=kind, prior=prior, payload=value),
                        sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            db.execute('UPDATE events SET payload=?,id=? WHERE id=?',
                       (encoded, 'f' * 64 if mutation == 'bad_hash' else handle, target))
        elif mutation == 'sequence_gap':
            db.execute('UPDATE events SET sequence=20 WHERE sequence=3')
        elif mutation == 'unknown_kind':
            db.execute("UPDATE events SET kind='future' WHERE sequence=3")
        elif mutation == 'extra_table':
            db.execute('CREATE TABLE foreign_data (id INTEGER)')
        elif mutation == 'extra_trigger':
            db.execute('CREATE TRIGGER foreign_trigger BEFORE DELETE ON events BEGIN SELECT 1; END')
        elif mutation == 'metadata_version':
            db.execute('UPDATE workspace SET schema_version=3')
        elif mutation == 'partial_schema':
            db.execute('DROP TABLE events')
    before = tree(w.root)
    for command in ('show', 'check-use'):
        result = w.call(command, binding['id'])
        assert result.returncode == 1
        assert not result.stdout
    assert w.call('recover').returncode == 1
    assert tree(w.root) == before


def test_sqlite_wal_foreign_schema_and_hardlink_refuse(workspace):
    w = workspace
    w.enroll()
    with sqlite3.connect(w.store) as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
    before = tree(w.root)
    assert w.call('recover').returncode == 1
    assert tree(w.root) == before
    with sqlite3.connect(w.store) as db:
        db.execute('PRAGMA journal_mode=DELETE')
    os.link(w.store, w.project / 'canonical-alias.sqlite3')
    before = tree(w.root)
    result = w.call('recover')
    assert result.returncode == 1 and 'hardlinked' in result.stderr
    assert tree(w.root) == before


def test_live_sqlite_lock_times_out_without_breaking_it(workspace):
    w = workspace
    w.enroll()
    with sqlite3.connect(w.store) as db:
        db.execute('BEGIN IMMEDIATE')
        result = w.call('checkpoint', 'project', *ATTR)
        assert result.returncode == 1 and 'busy' in result.stderr
        assert db.in_transaction
    assert len(w.events()) == 1


def test_precommit_and_postcommit_receipt_failures_and_retry(workspace):
    w = workspace
    w.enroll()
    w.bind()
    for point in ('before-inspection-output', 'after-inspection-output', 'before-commit'):
        before = w.events()
        result = w.call('read', 'project:kb-a', *SCOPE,
                        env={'VALIDATED_MEMORY_CONSULTATION_FAULT': point})
        assert result.returncode == 1
        assert w.events() == before
        assert 'receipt recorded' not in result.stdout
    result = w.call('read', 'project:kb-a', *SCOPE,
                    env={'VALIDATED_MEMORY_CONSULTATION_FAULT': 'before-handle-output'})
    assert result.returncode == 1
    assert 'committed artifact id=' in result.stderr
    assert len(result.stdout.splitlines()) == 1
    handle = w.events()[-1][0]
    assert handle in result.stderr
    assert w.ok('read', 'project:kb-a', *SCOPE)['id'] == handle


def test_race_after_content_output_refuses_receipt(workspace):
    w = workspace
    w.enroll()
    w.bind()
    before = w.events()
    ready, release = w.root / 'ready', w.root / 'release'
    env = dict(os.environ, PYTHONPATH=str(REPO), VALIDATED_MEMORY_CONSULTATION_FAULT='after-inspection-output',
               VALIDATED_MEMORY_CONSULTATION_FAULT_ACTION='pause',
               VALIDATED_MEMORY_CONSULTATION_READY=str(ready), VALIDATED_MEMORY_CONSULTATION_RELEASE=str(release))
    proc = subprocess.Popen([sys.executable, '-P', '-m', 'validated_memory', 'consultation',
                             '--store', str(w.store), 'read', 'project:kb-a', *SCOPE],
                            env=env, cwd=w.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        until = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < until:
            time.sleep(0.02)
        assert ready.exists()
        w.unit('kb-new')
        release.write_text('continue')
        stdout, stderr = proc.communicate(timeout=10)
        assert proc.returncode == 1
        assert 'snapshot changed' in stderr
        assert len(stdout.splitlines()) == 1
        assert w.events() == before
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_true_hot_journal_requires_explicit_recovery(workspace):
    w = workspace
    w.enroll()
    before = w.events()
    script = '''import os,sqlite3,sys
c=sqlite3.connect(sys.argv[1])
c.execute("PRAGMA cache_size=1")
c.execute("BEGIN IMMEDIATE")
c.execute("UPDATE events SET payload=?", ("x"*5000000,))
os._exit(71)
'''
    result = subprocess.run([sys.executable, '-c', script, str(w.store)])
    assert result.returncode == 71
    assert Path(str(w.store) + '-journal').exists()
    frozen = tree(w.root)
    refusal = w.call('show', before[0][0])
    assert refusal.returncode == 1 and 'recover' in refusal.stderr
    assert tree(w.root) == frozen
    assert w.ok('recover')['status'] == 'recovered'
    assert w.events() == before


def test_hypothesis_missing_receipt_and_wrong_scope_refuse(workspace):
    w = workspace
    w.enroll()
    w.unit('kb-a', evidence='hypothesis')
    w.bind()
    before = w.events()
    for args in [('read', 'project:kb-a', *SCOPE),
                 ('read', 'project:kb-a', '--scope', 'task=elsewhere'),
                 ('record-use', 'project:kb-a', '--receipt', 'a' * 64)]:
        result = w.call(*args)
        assert result.returncode == 1
        assert not result.stdout
        assert w.events() == before


def test_unchosen_conflict_candidate_support_drift_blocks_reacquisition(workspace):
    w = workspace
    w.enroll()
    w.unit('kb-b')
    (w.project / 'other.txt').write_text('alternative support')
    w.bind()
    w.ok('bind', 'project:kb-b', '--support', 'other.txt', '--authority', 'policy', *SCOPE, *ATTR)
    conflict = w.ok('conflict', '--candidate', 'project:kb-a', '--candidate', 'project:kb-b', *SCOPE, *ATTR)
    w.ok('choose', conflict['id'], '--candidate', 'project:kb-a', *SCOPE, *ATTR)
    w.ok('read', 'project:kb-a', *SCOPE)
    (w.project / 'other.txt').write_text('changed alternative support')
    before = w.events()
    for args in [('read', 'project:kb-a', *SCOPE),
                 ('choose', conflict['id'], '--candidate', 'project:kb-a', *SCOPE, *ATTR)]:
        result = w.call(*args)
        assert result.returncode == 1 and 'support changed' in result.stderr
    assert w.events() == before


def test_concurrent_initial_registration_serializes(workspace, run_cli):
    w = workspace
    second = w.root / 'second'
    second.mkdir()
    assert run_cli('init', cwd=second).returncode == 0
    commands = [('project', w.project), ('second', second)]
    processes = [subprocess.Popen([sys.executable, '-P', '-m', 'validated_memory', 'consultation',
                    '--store', str(w.store), 'register', alias, str(root), '--source', 'policy'],
                    cwd=w.root, env=dict(os.environ, PYTHONPATH=str(REPO)),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for alias, root in commands]
    for proc in processes:
        output, error = proc.communicate(timeout=15)
        assert proc.returncode == 0, error
        assert json.loads(output)['id']
    rows = w.events()
    assert len(rows) == 2 and all(row[1] == 'registration' for row in rows)
    assert len({json.loads(row[2])['project'] for row in rows}) == 2


def test_successor_conflict_requires_complete_replacement_and_explicit_choice(workspace):
    w = workspace
    w.enroll()
    w.unit('kb-b')
    w.bind()
    w.bind('kb-b')
    conflict = w.ok('conflict', '--candidate', 'project:kb-a', '--candidate', 'project:kb-b', *SCOPE, *ATTR)
    w.ok('choose', conflict['id'], '--candidate', 'project:kb-a', *SCOPE, *ATTR)
    w.unit('kb-new', supersedes='supersedes:\n  - kb-a\n')
    w.bind('kb-new')
    before = w.events()
    incomplete = w.call('conflict', '--prior', conflict['id'], '--candidate', 'project:kb-new',
                        '--candidate', 'project:kb-b', *SCOPE, *ATTR)
    assert incomplete.returncode == 1 and w.events() == before
    successor = w.ok('conflict', '--prior', conflict['id'], '--candidate', 'project:kb-new',
                     '--candidate', 'project:kb-b', '--replacement', 'project:kb-a=project:kb-new', *SCOPE, *ATTR)
    assert w.call('read', 'project:kb-new', *SCOPE).returncode == 1
    w.ok('choose', successor['id'], '--candidate', 'project:kb-new', *SCOPE, *ATTR)
    w.ok('read', 'project:kb-new', *SCOPE)
    assert w.ok('show', conflict['id'])['artifact']['payload']['candidates'][0]


def test_duplicate_options_are_usage_errors_before_creation(workspace):
    w = workspace
    before = tree(w.root)
    result = w.call('read', 'project:kb-a', '--scope', 'task=a', '--scope', 'task=b')
    assert result.returncode == 2
    result = w.call('bind', 'project:kb-a', '--support', 'source.txt', '--support', 'source.txt',
                    '--authority', 'policy', *SCOPE, *ATTR)
    assert result.returncode == 2
    assert tree(w.root) == before


def test_root_inode_replacement_cannot_silently_reuse_registration(workspace):
    w = workspace
    w.enroll()
    w.bind()
    old = w.root / 'retained-root'
    w.project.rename(old)
    w.project.mkdir()
    before = tree(w.root)
    result = w.call('read', 'project:kb-a', *SCOPE)
    assert result.returncode == 1 and 'identity changed' in result.stderr
    assert tree(w.root) == before


def test_metadata_table_constraints_are_part_of_storage_contract(workspace):
    w = workspace
    registration = w.enroll()
    with sqlite3.connect(w.store) as db:
        row = db.execute('SELECT * FROM workspace').fetchone()
        db.execute('DROP TABLE workspace')
        db.execute('CREATE TABLE workspace (singleton INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, workspace_id TEXT NOT NULL)')
        db.execute('INSERT INTO workspace VALUES (?,?,?)', row)
    before = tree(w.root)
    result = w.call('show', registration['id'])
    assert result.returncode == 1 and 'constraints mismatch' in result.stderr
    assert tree(w.root) == before


def test_replay_rejects_support_review_fork_even_with_recomputed_hash(workspace):
    w = workspace
    w.enroll()
    binding = w.bind()
    (w.project / 'source.txt').write_text('reviewed evidence')
    review = w.ok('review-support', 'project:kb-a', '--prior', binding['id'], '--support', 'source.txt',
                  '--authority', 'policy', *SCOPE, *ATTR)
    with sqlite3.connect(w.store) as db:
        sequence, payload, created_at = db.execute('SELECT sequence,payload,created_at FROM events WHERE id=?',
                                                  (review['id'],)).fetchone()
        value = json.loads(payload)
        value['reason'] = 'a forged fork with the earlier prior'
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        handle = hashlib.sha256(json.dumps(dict(kind='support-review', prior=binding['id'], payload=value),
                    sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',
                   (sequence + 1, handle, 'support-review', binding['id'], encoded, created_at))
    before = tree(w.root)
    result = w.call('show', binding['id'])
    assert result.returncode == 1 and 'prior is not binding head' in result.stderr
    assert tree(w.root) == before


@pytest.mark.parametrize('limit', ['units', 'edges'])
def test_dependency_graph_work_limits_refuse_without_receipt(workspace, limit):
    w = workspace
    w.enroll()
    if limit == 'units':
        names = [f'kb-chain-{i:03}' for i in range(129)]
        for name in names:
            w.unit(name)
        for index, name in enumerate(names):
            refs = ['project:' + names[index + 1]] if index + 1 < len(names) else []
            w.bind(name, *refs)
        w.ok('read', 'project:' + names[1], *SCOPE, '--max-bytes', '1048576')
        target, diagnostic = names[0], '128-unit'
    else:
        leaves = [f'kb-leaf-{i}' for i in range(8)]
        branches = [f'kb-branch-{i:02}' for i in range(63)]
        for name in leaves + branches:
            w.unit(name)
        for name in leaves:
            w.bind(name)
        heads = {}
        for index, name in enumerate(branches):
            selected = leaves if index < 56 else leaves[:1] if index == 56 else []
            heads[name] = w.bind(name, *['project:' + leaf for leaf in selected])
        w.bind('kb-a', *['project:' + branch for branch in branches])
        w.ok('read', 'project:kb-a', *SCOPE, '--max-bytes', '1048576')
        w.ok('review-support', 'project:' + branches[56], '--prior', heads[branches[56]]['id'],
             '--support', 'source.txt', '--authority', 'policy', *SCOPE, *ATTR,
             '--reference', 'project:' + leaves[0], '--reference', 'project:' + leaves[1])
        target, diagnostic = 'kb-a', '512-edge'
    before = w.events()
    result = w.call('read', 'project:' + target, *SCOPE, '--max-bytes', '1048576')
    assert result.returncode == 1 and diagnostic in result.stderr
    assert not result.stdout
    assert w.events() == before


@pytest.mark.parametrize('operation,arguments', [
    ('register', ['project', 'PROJECT', '--source', 'policy', '--sou=alternate']),
    ('bind', ['project:kb-a', '--support', 'source.txt', '--authority', 'policy',
              '--auth=alternate', *SCOPE, *ATTR]),
    ('checkpoint', ['project', *ATTR, '--act=someone else']),
    ('checkpoint', ['project', *ATTR, '--reason=another reason']),
    ('record-use', ['project:kb-a', '--receipt', 'a' * 64, '--rec=' + 'b' * 64]),
    ('review-support', ['project:kb-a', '--support', 'source.txt', '--authority', 'policy',
                       *SCOPE, *ATTR, '--prior', 'a' * 64, '--pri=' + 'b' * 64]),
    ('read', ['project:kb-a', *SCOPE, '--max-bytes', '65536', '--max=1048576']),
    ('choose', ['a' * 64, '--candidate', 'project:kb-a', '--cand=project:kb-b', *SCOPE, *ATTR]),
    ('relocate', ['project', 'PROJECT', '--checkpoint', 'a' * 64, '--check=' + 'b' * 64, *ATTR]),
])
def test_singleton_duplicates_are_usage_errors_before_storage(workspace, operation, arguments):
    w = workspace
    arguments = [str(w.project) if arg == 'PROJECT' else arg for arg in arguments]
    before = tree(w.root)
    result = w.call(operation, *arguments)
    assert result.returncode == 2 and 'only once' in result.stderr
    assert tree(w.root) == before
    assert not w.store.exists()


def test_duplicate_store_abbreviation_and_equal_forms_refuse_before_opening(workspace):
    w = workspace
    foreign = w.root / 'must-not-be-created.sqlite3'
    before = tree(w.root)
    result = w.call('--sto=' + str(foreign), 'register', 'project', str(w.project), '--source', 'policy')
    assert result.returncode == 2 and '--store' in result.stderr and 'only once' in result.stderr
    assert tree(w.root) == before
    assert not w.store.exists() and not foreign.exists()


def replace_checkpoint_payload(database, handle, payload):
    payload['inventory_sha256'] = hashlib.sha256(json.dumps(payload['inventory'], sort_keys=True,
                    ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    with sqlite3.connect(database) as db:
        kind, prior = db.execute('SELECT kind,prior FROM events WHERE id=?', (handle,)).fetchone()
        new_handle = hashlib.sha256(json.dumps(dict(kind=kind, prior=prior, payload=payload), sort_keys=True,
                                    ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        db.execute('UPDATE events SET payload=?,id=? WHERE id=?', (encoded, new_handle, handle))
    return new_handle


@pytest.mark.parametrize('corruption', ['aggregate_bytes', 'aggregate_paths', 'config_schema', 'knowledge_support'])
def test_checkpoint_replay_enforces_distinct_inventory_bounds_and_roles(workspace, corruption):
    w = workspace
    w.enroll()
    checkpoint = w.ok('checkpoint', 'project', *ATTR)
    payload = json.loads(w.events()[-1][2])
    inventory = payload['inventory']
    if corruption == 'aggregate_bytes':
        inventory['support'] = [dict(path=f'support-{i:02}.txt', sha256='a' * 64, size=1048576)
                                for i in range(17)]
        diagnostic = '16 MiB'
    elif corruption == 'aggregate_paths':
        inventory['support'] = [dict(path=f'support-{i:04}.txt', sha256='a' * 64, size=0)
                                for i in range(4096)]
        diagnostic = '4096'
    elif corruption == 'config_schema':
        inventory['schema'] = {**inventory['config'], 'sha256': 'b' * 64}
        diagnostic = 'roles disagree'
    else:
        inventory['support'] = [{**inventory['knowledge'][0], 'size': 0}]
        diagnostic = 'roles disagree'
    handle = replace_checkpoint_payload(w.store, checkpoint['id'], payload)
    before = tree(w.root)
    for operation in ('show', 'check-use'):
        result = w.call(operation, handle)
        assert result.returncode == 1 and diagnostic in result.stderr
        assert not result.stdout
    assert tree(w.root) == before


def test_checkpoint_replay_counts_consistent_cross_role_paths_once(workspace):
    w = workspace
    w.enroll()
    checkpoint = w.ok('checkpoint', 'project', *ATTR)
    payload = json.loads(w.events()[-1][2])
    inventory = payload['inventory']
    inventory['schema'] = dict(inventory['config'])
    remaining = 16 * 1024 * 1024 - inventory['config']['size'] - sum(item['size'] for item in inventory['knowledge'])
    unique = []
    for index in range(4094):
        size = min(1048576, remaining)
        unique.append(dict(path=f'support-{index:04}.txt', size=size, sha256='a' * 64))
        remaining -= size
    assert remaining == 0
    inventory['support'] = sorted([dict(inventory['config']), *inventory['knowledge'], *unique],
                                  key=lambda item: item['path'])
    handle = replace_checkpoint_payload(w.store, checkpoint['id'], payload)
    before = tree(w.root)
    assert w.ok('show', handle)['artifact']['payload']['inventory'] == inventory
    assert tree(w.root) == before
