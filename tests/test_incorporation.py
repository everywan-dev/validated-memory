"""Subprocess enforcement for immutable contribution decisions and recovery."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ATTR = ('--actor', 'test author', '--reason', 'Reviewed fixture evidence')
SCOPE = ('--scope', 'task=planning')
LEGACY_KINDS = "'registration','relocation','checkpoint','binding','support-review','conflict','choice','receipt','use'"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class Workspace:
    def __init__(self, root, run_cli):
        self.root = root
        self.project = root / 'project'
        self.project.mkdir()
        assert run_cli('init', cwd=self.project).returncode == 0
        self.store = root / 'workspace.sqlite'
        self.unit('old')
        (self.project / 'evidence.txt').write_text('Stable fixture evidence\n')
        self.ok('register', 'p', str(self.project), '--source', 'source')
        self.binding = self.ok('bind', 'p:old', '--support', 'evidence.txt', '--authority', 'source', *SCOPE, *ATTR)['id']
        self.statement = root / 'statement.txt'
        self.statement.write_text('Please reconsider this exact finding.\n')

    def unit(self, name, supersedes=()):
        text = f'---\nid: {name}\nevidence: verifiable\nsupersedes: [{", ".join(supersedes)}]\n---\nFinding {name}.\n'
        path = self.project / 'knowledge' / (name + '.md')
        path.write_text(text)
        return path

    def call(self, *args, env=None, stdout=None):
        return subprocess.run([sys.executable, '-P', '-m', 'validated_memory', 'consultation', '--store', str(self.store), *args],
                              env=dict(os.environ, PYTHONPATH=str(REPO), **(env or {})), cwd=self.root,
                              stdout=stdout if stdout is not None else subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=25)

    def ok(self, *args):
        result = self.call(*args)
        assert result.returncode == 0, (args, result.stdout, result.stderr)
        return json.loads(result.stdout.splitlines()[-1])

    def event(self, handle):
        return self.ok('show', handle)['artifact']

    def challenge(self):
        return self.ok('challenge', 'p:old', '--statement', str(self.statement), '--kind', 'factual', *SCOPE, *ATTR)['id']

    def inspect(self, handle):
        return self.ok('inspect', handle, '--max-bytes', '1048576')['id']

    def accept(self, handle):
        return self.ok('decide', handle, '--inspection', self.inspect(handle), '--outcome', 'accept', *ATTR)['id']

    def rows(self):
        with sqlite3.connect(self.store) as db:
            return db.execute('SELECT sequence,id,kind,prior,payload,created_at FROM events ORDER BY sequence').fetchall()

    def legacy(self):
        """Construct the independently frozen exact v1 table schema over legacy-only rows."""
        rows = self.rows()
        assert all(row[2] in ('registration', 'binding') for row in rows)
        with sqlite3.connect(self.store) as db:
            uuid = db.execute('SELECT workspace_id FROM workspace').fetchone()[0]
            db.execute('DROP TABLE events')
            db.execute('DROP TABLE workspace')
            db.execute('CREATE TABLE workspace (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), schema_version INTEGER NOT NULL CHECK (schema_version = 1), workspace_id TEXT NOT NULL)')
            db.execute('CREATE TABLE events (sequence INTEGER PRIMARY KEY CHECK (sequence >= 1), id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK (kind IN (' + LEGACY_KINDS + ')), prior TEXT REFERENCES events(id), payload TEXT NOT NULL, created_at TEXT NOT NULL)')
            db.execute('INSERT INTO workspace VALUES (1,1,?)', (uuid,))
            db.executemany('INSERT INTO events VALUES (?,?,?,?,?,?)', rows)
        return uuid, rows


@pytest.fixture
def workspace(tmp_path, run_cli):
    return Workspace(tmp_path, run_cli)


def test_decision_exact_inspection_prior_and_attribution(workspace):
    w = workspace
    first, other = w.challenge(), w.challenge()
    assert first == other
    inspected = w.inspect(first)
    second_inspection = w.inspect(first)
    assert inspected != second_inspection
    rows = w.rows()
    accepted = w.ok('decide', first, '--inspection', inspected, '--outcome', 'accept', *ATTR)['id']
    assert w.ok('decide', first, '--inspection', inspected, '--outcome', 'accept', *ATTR)['id'] == accepted
    assert len(w.rows()) == len(rows) + 1
    args = ('decide', first, '--inspection', second_inspection, '--outcome', 'accept', *ATTR)
    assert w.call(*args).returncode == 1
    new = w.ok(*args, '--prior', accepted)['id']
    assert new != accepted and w.event(new)['prior'] == accepted
    changed = w.ok('challenge', 'p:old', '--statement', str(w.statement), '--kind', 'factual', *SCOPE,
                   '--actor', 'other contributor', '--reason', 'Independent attributed submission')['id']
    assert changed != first
    assert w.call('decide', changed, '--inspection', inspected, '--outcome', 'accept', *ATTR).returncode == 1


@pytest.mark.parametrize('point,version', [('before-upgrade-rebuild', 1), ('after-upgrade-rebuild', 1), ('after-commit', 3)])
def test_upgrade_crash_preserves_complete_schema_and_exact_history(workspace, point, version):
    w = workspace
    uuid, rows = w.legacy()
    result = w.call('upgrade', env={'VALIDATED_MEMORY_CONSULTATION_FAULT': point, 'VALIDATED_MEMORY_CONSULTATION_FAULT_ACTION': 'crash'})
    assert result.returncode == 71
    assert w.ok('recover')['status'] == 'recovered'
    with sqlite3.connect(w.store) as db:
        assert db.execute('SELECT schema_version,workspace_id FROM workspace').fetchone() == (version, uuid)
    assert w.rows() == rows
    w.ok('upgrade')
    assert w.rows() == rows
    w.ok('upgrade')
    assert w.rows() == rows


def test_legacy_commands_do_not_upgrade_and_lifecycle_refuses_without_write(workspace):
    w = workspace
    uuid, rows = w.legacy()
    before = w.store.read_bytes()
    assert w.event(w.binding)['kind'] == 'binding'
    result = w.call('challenge', 'p:old', '--statement', str(w.statement), '--kind', 'factual', *SCOPE, *ATTR)
    assert result.returncode == 1 and 'upgrade' in result.stderr
    assert w.store.read_bytes() == before and w.rows() == rows
    receipt = w.ok('read', 'p:old', *SCOPE)['id']
    assert w.event(receipt)['payload']['version'] == 1
    w.ok('upgrade')
    modern = w.ok('read', 'p:old', *SCOPE)['id']
    assert modern != receipt and w.event(modern)['payload']['review_frontier'] == []


@pytest.mark.parametrize('operation', ['inspect', 'upgrade'])
def test_missing_workspace_operations_create_nothing(tmp_path, operation):
    before = list(tmp_path.iterdir())
    args = [sys.executable, '-P', '-m', 'validated_memory', 'consultation', '--store', str(tmp_path / 'missing.sqlite'), operation]
    if operation == 'inspect':
        args += ['a' * 64]
    result = subprocess.run(args, cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(REPO)), capture_output=True)
    assert result.returncode == 1 and list(tmp_path.iterdir()) == before


def test_inspection_exact_wire_bound_and_failed_delivery_commit_nothing(workspace):
    w = workspace
    challenge = w.challenge()
    result = w.call('inspect', challenge, '--max-bytes', '1048576')
    assert result.returncode == 0
    size = len(result.stdout.encode())
    before = w.rows()
    assert w.call('inspect', challenge, '--max-bytes', str(max(2048, size - 1))).returncode == (1 if size > 2048 else 0)
    if size > 2048:
        assert w.rows() == before
    before = w.rows()
    with open('/dev/full', 'w') as output:
        result = w.call('inspect', challenge, stdout=output)
    assert result.returncode == 1 and w.rows() == before
    result = w.call('inspect', challenge, env={'VALIDATED_MEMORY_CONSULTATION_FAULT': 'after-commit'})
    assert result.returncode == 1 and 'committed artifact id=' in result.stderr
    assert len(w.rows()) == len(before) + 1


@pytest.mark.parametrize('field', ['observed_head', 'inspection_text', 'submission'])
def test_rehashed_inspection_corruption_refuses_complete_history(workspace, field):
    w = workspace
    challenge = w.challenge()
    inspected = w.inspect(challenge)
    with sqlite3.connect(w.store) as db:
        sequence, kind, prior, payload = db.execute('SELECT sequence,kind,prior,payload FROM events WHERE id=?', (inspected,)).fetchone()
        value = json.loads(payload)
        if field == 'observed_head':
            value[field] = w.binding
        elif field == 'inspection_text':
            value[field] = '{}\n'
            value['inspection_sha256'] = hashlib.sha256(value[field].encode()).hexdigest()
        else:
            value[field] = w.binding
        handle = hashlib.sha256(canonical(dict(kind=kind, prior=prior, payload=value)).encode()).hexdigest()
        db.execute('UPDATE events SET id=?,payload=? WHERE sequence=?', (handle, canonical(value), sequence))
    before = w.store.read_bytes()
    assert w.call('show', w.binding).returncode == 1
    assert w.store.read_bytes() == before


@pytest.mark.parametrize('kind', ['symlink', 'invalid_utf8', 'blank', 'oversize'])
def test_bad_statement_is_refused_before_history(workspace, kind):
    w = workspace
    if kind == 'symlink':
        target = w.root / 'target.txt'
        w.statement.rename(target)
        w.statement.symlink_to(target)
    elif kind == 'invalid_utf8':
        w.statement.write_bytes(b'\xff')
    elif kind == 'blank':
        w.statement.write_text(' \n')
    else:
        w.statement.write_bytes(b'a' * 65537)
    before = w.store.read_bytes()
    result = w.call('challenge', 'p:old', '--statement', str(w.statement), '--kind', 'factual', *SCOPE, *ATTR)
    assert result.returncode == 1 and w.store.read_bytes() == before


def proposal_args(w, candidate, *extra):
    return ('submit', 'p', str(candidate), '--path', 'knowledge/new.md', '--support', 'evidence.txt',
            '--authority', 'source', *SCOPE, *ATTR, *extra)


@pytest.mark.parametrize('defect', ['existing_id', 'unknown_field', 'missing_predecessor', 'self_cycle', 'hypothesis_valid', 'support_symlink', 'candidate_inside', 'candidate_ancestor_symlink'])
def test_prospective_validation_and_safe_inputs_never_write_adopter(workspace, defect):
    w = workspace
    candidate = w.root / 'candidate.md'
    candidate.write_text('---\nid: new\nevidence: verifiable\nsupersedes:\n  - old\n---\nNew finding.\n')
    if defect == 'existing_id':
        candidate.write_text('---\nid: old\nevidence: verifiable\n---\nCollision.\n')
    elif defect == 'unknown_field':
        candidate.write_text('---\nid: new\nevidence: verifiable\nundeclared: value\n---\nUnknown field.\n')
    elif defect == 'missing_predecessor':
        candidate.write_text('---\nid: new\nevidence: verifiable\nsupersedes:\n  - missing\n---\nMissing target.\n')
    elif defect == 'self_cycle':
        candidate.write_text('---\nid: new\nevidence: verifiable\nsupersedes:\n  - new\n---\nSelf cycle.\n')
    elif defect == 'hypothesis_valid':
        candidate.write_text('---\nid: new\nevidence: hypothesis\nsupersedes:\n  - old\n---\nStill uncertain.\n')
    elif defect == 'support_symlink':
        (w.project / 'evidence.txt').unlink()
        (w.project / 'evidence.txt').symlink_to(w.statement)
    elif defect == 'candidate_inside':
        candidate = w.project / 'candidate.md'
        candidate.write_text('---\nid: new\nevidence: verifiable\n---\nInside.\n')
    elif defect == 'candidate_ancestor_symlink':
        (w.root / 'alias').symlink_to(w.root, target_is_directory=True)
        candidate = w.root / 'alias' / 'candidate.md'
    before = {str(p.relative_to(w.project)): p.read_bytes() for p in w.project.rglob('*') if p.is_file()}
    rows = w.rows()
    result = w.call(*proposal_args(w, candidate))
    assert result.returncode == (0 if defect == 'hypothesis_valid' else 1), result.stderr
    if defect != 'hypothesis_valid':
        assert w.rows() == rows
    assert {str(p.relative_to(w.project)): p.read_bytes() for p in w.project.rglob('*') if p.is_file()} == before


def test_proposal_uses_declared_extension_without_altering_candidate(workspace):
    w = workspace
    (w.project / 'validated-memory.md').write_text('---\nextension:\n  schema: local-schema.md\n  version: "1"\n---\n')
    (w.project / 'local-schema.md').write_text('---\nversion: "1"\nfields:\n  - name: confidence\n    type: enum\n    values:\n      - high\n      - low\n---\n')
    candidate = w.root / 'candidate.md'
    candidate.write_text('---\nid: new\nevidence: verifiable\nconfidence: high\n---\nExtension allowed.\n')
    result = w.call(*proposal_args(w, candidate))
    assert result.returncode == 0, result.stderr
    candidate.write_text(candidate.read_text().replace('high', 'unknown'))
    assert w.call(*proposal_args(w, candidate)).returncode == 1


@pytest.mark.parametrize('path', ['knowledge/old.md', 'memory/MEMORY.md', 'validated-memory.md', '.git/config', '.validated-memory/hidden.txt', 'local-schema.md'])
def test_publication_exclusions_refuse_without_history(workspace, path):
    w = workspace
    (w.project / 'validated-memory.md').write_text('---\nextension:\n  schema: local-schema.md\n  version: "1"\n---\n')
    (w.project / 'local-schema.md').write_text('---\nversion: "1"\nfields: []\n---\n')
    receipt = w.ok('read', 'p:old', *SCOPE)['id']
    use = w.ok('record-use', 'p:old', '--receipt', receipt)['id']
    challenge = w.challenge()
    before = w.rows()
    result = w.call('track-publication', challenge, '--project', 'p', '--path', path, '--use', use, *ATTR)
    assert result.returncode == 1 and w.rows() == before


def test_unchanged_inspection_before_acceptance_cannot_resolve(workspace):
    w = workspace
    inspected = w.inspect(w.binding)
    challenge = w.challenge()
    decision = w.accept(challenge)
    before = w.rows()
    result = w.call('resolve', challenge, '--decision', decision, '--review', w.binding, '--inspection', inspected, *ATTR)
    assert result.returncode == 1 and 'after acceptance' in result.stderr
    assert w.rows() == before
    new_inspection = w.inspect(w.binding)
    assert new_inspection != inspected
    w.ok('resolve', challenge, '--decision', decision, '--review', w.binding, '--inspection', new_inspection, *ATTR)
    result = w.call('resolve', challenge, '--decision', decision, '--review', w.binding, '--inspection', new_inspection,
                    '--actor', 'other actor', '--reason', 'Different assertion')
    assert result.returncode == 1 and 'already resolved' in result.stderr


def test_reconcile_requires_complete_bounded_result(workspace):
    w = workspace
    # Distinct scoped receipts create distinct affected uses without changing canonical input.
    for index in range(129):
        receipt = w.ok('read', 'p:old', *SCOPE, '--scope', f'case={index}')['id']
        w.ok('record-use', 'p:old', '--receipt', receipt)
    challenge = w.challenge()
    before = w.store.read_bytes()
    result = w.call('reconcile', challenge)
    assert result.returncode == 1 and '128' in result.stderr and not result.stdout
    assert w.store.read_bytes() == before


def test_upgrade_respects_live_sqlite_lock(workspace):
    w = workspace
    w.legacy()
    before = w.store.read_bytes()
    with sqlite3.connect(w.store) as db:
        db.execute('BEGIN IMMEDIATE')
        result = w.call('upgrade')
        assert result.returncode == 1 and 'busy' in result.stderr
        assert w.store.read_bytes() == before
    w.ok('upgrade')


def test_complete_proposal_inspection_bound_refuses_before_storing(workspace):
    w = workspace
    candidate = w.root / 'candidate.md'
    candidate.write_text('---\nid: new\nevidence: verifiable\n---\n' + 'x' * 600000)
    # Retained source and candidate together exceed the complete 1 MiB envelope.
    (w.project / 'evidence.txt').write_text('e' * 600000)
    rows = w.rows()
    result = w.call(*proposal_args(w, candidate))
    assert result.returncode == 1 and 'inspection bound' in result.stderr
    assert w.rows() == rows


def test_submission_rechecks_external_candidate_after_capture_rendezvous(workspace):
    import time

    w = workspace
    candidate = w.root / 'candidate.md'
    candidate.write_text('---\nid: new\nevidence: verifiable\n---\nAccepted bytes.\n')
    ready, release = w.root / 'ready', w.root / 'release'
    env = dict(os.environ, PYTHONPATH=str(REPO), VALIDATED_MEMORY_CONSULTATION_FAULT='before-recapture',
               VALIDATED_MEMORY_CONSULTATION_FAULT_ACTION='pause', VALIDATED_MEMORY_CONSULTATION_READY=str(ready),
               VALIDATED_MEMORY_CONSULTATION_RELEASE=str(release))
    rows = w.rows()
    process = subprocess.Popen([sys.executable, '-P', '-m', 'validated_memory', 'consultation', '--store', str(w.store),
                                *proposal_args(w, candidate)], cwd=w.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        until = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < until:
            time.sleep(.02)
        assert ready.exists()
        candidate.write_text(candidate.read_text().replace('Accepted bytes', 'Different bytes'))
        release.touch()
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 1 and 'candidate changed' in stderr and not stdout
        assert w.rows() == rows
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
