"""The scoped SQLite writer approved by ADR 0017; no adopter mutations.

All database connections, SQLite sidecars and test rendezvous writes belong here.
Semantic history is immutable rows, not physically append-only database bytes.
"""

import datetime
import os
import re
import sqlite3
import stat
import time
import uuid
from pathlib import Path

from . import model as m
from .state import State

DDL = (
    'CREATE TABLE workspace (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), '
    'schema_version INTEGER NOT NULL CHECK (schema_version = 1), workspace_id TEXT NOT NULL)',
    'CREATE TABLE events (sequence INTEGER PRIMARY KEY CHECK (sequence >= 1), '
    'id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK (kind IN (' +
    ','.join("'" + kind + "'" for kind in m.KINDS) + ')), '
    'prior TEXT REFERENCES events(id), payload TEXT NOT NULL, created_at TEXT NOT NULL)',
)
COLUMNS = {
    'workspace': [(0, 'singleton', 'INTEGER', 0, None, 1),
                  (1, 'schema_version', 'INTEGER', 1, None, 0),
                  (2, 'workspace_id', 'TEXT', 1, None, 0)],
    'events': [(0, 'sequence', 'INTEGER', 0, None, 1), (1, 'id', 'TEXT', 1, None, 0),
               (2, 'kind', 'TEXT', 1, None, 0), (3, 'prior', 'TEXT', 0, None, 0),
               (4, 'payload', 'TEXT', 1, None, 0), (5, 'created_at', 'TEXT', 1, None, 0)],
}


def location(value):
    target = Path(os.path.abspath(value))
    m.path(str(target), True)
    for part in reversed((target, *target.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            m.require(part == target, f'{part}: parent missing; create the store parent explicitly')
            continue
        if part == target:
            m.require(info.st_nlink == 1, f'{part}: hardlinked database refused; use an independent workspace')
        m.require(not stat.S_ISLNK(info.st_mode), f'{part}: symlink refused; choose canonical store path')
        m.require(stat.S_ISREG(info.st_mode) if part == target else stat.S_ISDIR(info.st_mode),
                  f'{part}: invalid store node; choose regular database in existing directory')
    for suffix in ('-journal', '-wal', '-shm'):
        sidecar = Path(str(target) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            mode = sidecar.lstat().st_mode
            m.require(sidecar.lstat().st_nlink == 1, f'{sidecar}: hardlinked sidecar refused; inspect storage')
            m.require(stat.S_ISREG(mode), f'{sidecar}: unsafe SQLite sidecar; inspect storage')
    return str(target)


def fault(point):
    """Deterministic subprocess seam, inert unless explicitly configured."""
    if os.environ.get('VALIDATED_MEMORY_CONSULTATION_FAULT') != point:
        return
    action = os.environ.get('VALIDATED_MEMORY_CONSULTATION_FAULT_ACTION', 'refuse')
    if action == 'crash':
        os._exit(71)
    if action == 'pause':
        ready = os.environ.get('VALIDATED_MEMORY_CONSULTATION_READY')
        release = os.environ.get('VALIDATED_MEMORY_CONSULTATION_RELEASE')
        m.require(ready and release, 'test rendezvous requires ready/release paths')
        Path(ready).write_text(point, encoding='utf-8')
        until = time.monotonic() + 15
        while not Path(release).exists() and time.monotonic() < until:
            time.sleep(0.02)
        m.require(Path(release).exists(), 'test rendezvous timed out')
        return
    raise m.Refusal(f'{point}: injected failure; retry the operation')


class Store:
    def __init__(self, path, writable=False, create=False, recover=False):
        self.path = location(path)
        self.writable = writable
        self.create = create
        self.recover = recover
        self.connection = None
        self.state = None
        self.committed_id = None
        self.empty = False

    def __enter__(self):
        m.require(self.create or Path(self.path).exists(),
                  f'{self.path}: store unavailable; register a project before this operation')
        mode = 'rwc' if self.create else ('rw' if self.writable else 'ro')
        try:
            self.connection = sqlite3.connect(Path(self.path).as_uri() + '?mode=' + mode,
                                              uri=True, isolation_level=None, timeout=5)
            c = self.connection
            m.require(c.execute('PRAGMA journal_mode').fetchone()[0] == 'delete',
                      'unsupported SQLite journal mode; restore a DELETE-mode workspace')
            c.execute('PRAGMA foreign_keys=ON')
            if self.writable:
                c.execute('PRAGMA synchronous=EXTRA')
            c.execute('BEGIN IMMEDIATE' if self.writable else 'BEGIN')
            fault('after-lock')
            objects = c.execute('SELECT type,name,tbl_name FROM sqlite_master').fetchall()
            if not objects:
                m.require(self.create or self.recover,
                          'empty/incomplete workspace; run consultation recover explicitly')
                self.empty = True
                for statement in DDL:
                    c.execute(statement)
                c.execute('INSERT INTO workspace VALUES (1,1,?)', (str(uuid.uuid4()),))
            self._load()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _load(self):
        c = self.connection
        m.require(c.execute('PRAGMA integrity_check').fetchall() == [('ok',)],
                  'SQLite integrity failure; restore intact workspace')
        objects = c.execute('SELECT type,name,tbl_name FROM sqlite_master').fetchall()
        m.require(set(objects) == {('table', 'workspace', 'workspace'), ('table', 'events', 'events'),
                                  ('index', 'sqlite_autoindex_events_1', 'events')},
                  'unsupported/partial workspace schema; restore intact workspace')
        def tokens(statement):
            return [token if token.startswith("'") else token.upper()
                    for token in re.findall(r"'(?:''|[^'])*'|[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[^\s]", statement)]
        for table, statement in zip(('workspace', 'events'), DDL):
            actual = c.execute('SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone()[0]
            m.require(tokens(actual) == tokens(statement), 'workspace table constraints mismatch')
        for table, expected in COLUMNS.items():
            m.require(c.execute('PRAGMA table_info(' + table + ')').fetchall() == expected,
                      'workspace column schema mismatch; restore intact workspace')
        m.require(c.execute('PRAGMA foreign_key_list(events)').fetchall() ==
                  [(0, 0, 'events', 'prior', 'id', 'NO ACTION', 'NO ACTION', 'NONE')],
                  'workspace foreign-key schema mismatch')
        m.require(c.execute('PRAGMA index_list(events)').fetchall() ==
                  [(0, 'sqlite_autoindex_events_1', 1, 'u', 0)] and
                  c.execute('PRAGMA index_info(sqlite_autoindex_events_1)').fetchall() == [(0, 1, 'id')],
                  'workspace uniqueness schema mismatch')
        rows = c.execute('SELECT singleton,schema_version,workspace_id FROM workspace').fetchall()
        m.require(len(rows) == 1 and rows[0][:2] == (1, 1) and
                  all(type(x) is int for x in rows[0][:2]), 'unsupported workspace metadata')
        self.state = State(rows[0][2], self.path)
        size, count = c.execute('SELECT coalesce(sum(length(cast(payload AS BLOB))),0),count(*) FROM events').fetchone()
        m.require(count <= 10000 and size <= 32 * 1024 * 1024, 'workspace history bound exceeded; inspect enrollment/history')
        for row in c.execute('SELECT sequence,id,kind,prior,payload,created_at FROM events ORDER BY sequence'):
            m.require(type(row[0]) is int and all(type(row[i]) is str for i in (1, 2, 4, 5))
                      and (row[3] is None or type(row[3]) is str), 'event SQLite storage class mismatch')
            value = m.decode(row[4])
            m.require(m.canonical(value) == row[4], 'event payload is not canonical JSON')
            self.state.add(dict(zip(('sequence', 'id', 'kind', 'prior', 'payload', 'created_at'),
                                    (*row[:4], value, row[5]))))
        self.payload_size = size

    def append(self, kind, payload, prior=None):
        m.require(self.writable, 'read-only operation cannot publish history')
        handle = m.event_id(kind, prior, payload)
        if handle in self.state.events:
            return self.state.events[handle]
        encoded = m.canonical(payload)
        m.require(self.payload_size + len(encoded.encode('utf-8')) <= 32 * 1024 * 1024,
                  '32 MiB history bound exceeded; narrow future declarations')
        event = dict(sequence=len(self.state.events) + 1, id=handle, kind=kind, prior=prior,
                     payload=payload, created_at=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'))
        self.state.add(event)
        self.connection.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',
                                (event['sequence'], handle, kind, prior, encoded, event['created_at']))
        self.payload_size += len(encoded.encode('utf-8'))
        fault('after-insert')
        return event

    def commit(self, handle=None):
        fault('before-commit')
        self.connection.commit()
        self.committed_id = handle
        fault('after-commit')

    def __exit__(self, *args):
        if self.connection is not None:
            try:
                if self.connection.in_transaction:
                    self.connection.rollback()
            finally:
                self.connection.close()
                self.connection = None


def storage_error(error):
    if isinstance(error, sqlite3.Error):
        if 'locked' in str(error) or 'busy' in str(error):
            return 'workspace busy; wait for the active command and retry'
        if 'readonly' in str(error):
            return 'workspace needs writable recovery; run consultation recover, then retry'
        return 'workspace storage/corruption error: ' + str(error) + '; inspect or restore intact history'
    return str(error)
