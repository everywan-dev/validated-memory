# The Journal — Implementation Plan (plan 1 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the plugin a durable, crash-consistent record of every
mutation it performs, so that what it did can be contradicted later and, in
a later plan, undone.

**Architecture:** One new module, `validated_memory/journal.py`, owns two
append-only artifacts: `journal.jsonl` at the adopter root (always versioned,
repository-visible mutations) and `.validated-memory/` (always local to the
clone, preimages and out-of-repository mutations). Every mutation is written
as a `prepared` record, then the atomic file operation, then a `committed`
record, under a per-adopter lock. `init` becomes the first caller. A new
read-only `journal` subcommand reports the log and reconciles an unfinished
transaction against the bytes on disk.

**Tech Stack:** Python 3.11+, standard library only (`json`, `hashlib`, `os`,
`pathlib`, `secrets`, `datetime`). pytest for tests, driving the CLI as a
subprocess.

**Spec:** `docs/design/2026-08-30-the-journal-coverage-and-reversal-design.md`
— read §1, §2, §4 and §7 before starting. This plan implements those; §3, §5
and §6 are plans 2, 3 and 4.

## Global Constraints

- Runtime code is Python 3 and uses the **standard library only**. `pytest`
  is the sole development dependency.
- All repository content is written in **English**: code, comments, CLI
  messages, docs, skills, tests, commit messages.
- Exit codes: `0` clean or WARNING-only, `1` ERROR (gates), `2` usage error.
- Tests drive the CLI **as a subprocess** over fixture adopter trees and
  **never import the package's internals**. Use the `run_cli` fixture in
  `tests/conftest.py`.
- The CLI is always invoked as `python3 -P -m validated_memory` (ADR 0006).
  From this checkout, with the repository as the working directory:
  `PYTHONPATH=. python3 -P -m validated_memory ...`.
- **Run pytest with NO `PYTHONPATH` set: `python3 -m pytest -q`.** The
  `run_cli` fixture does `env.setdefault("PYTHONPATH", REPO_ROOT)` and its
  subprocess runs with `cwd` inside a temporary directory, so a relative
  `PYTHONPATH=.` inherited from your shell resolves against that temporary
  directory and every CLI-driving test fails to import the package. Measured
  2026-08-31: `PYTHONPATH=. python3 -m pytest` reports 360 spurious failures
  where the same suite is green.
- Commit messages: Conventional Commits, in English. **Do not add a
  `Claude-Session:` trailer** — this repository is public and self-contained.
  `Co-Authored-By:` is fine.
- Work on this branch (`feature/uninstall-and-restore`); never force-push
  `main`.
- Nothing is ever deleted to make a check pass: a record that turns out wrong
  is superseded, not edited away.

## File Structure

| File | Responsibility |
|---|---|
| `validated_memory/journal.py` (create) | The record format, the two artifacts, digests, the lock, the transaction, reading and validation. Nothing else knows how a record is shaped. |
| `validated_memory/init.py` (modify) | Its four write paths become journalled transactions. It gains no knowledge of the record format beyond calling the transaction. |
| `validated_memory/cli.py` (modify) | Wires the `journal` subcommand. |
| `tests/test_journal.py` (create) | End-to-end over the CLI: records written, malformed log refused, lock held, unfinished transaction reconciled. |
| `tests/test_init.py` (modify) | `init` now leaves a journal; the existing assertions must still hold. |
| `tests/test_skills_structure.py` (modify) | `REAL_SUBCOMMANDS` gains `journal`. |
| `docs/adr/0008-the-journal-is-versioned-and-the-vault-is-local.md` (create) | The durability split, which is hard to reverse once adopters have logs. |
| `docs/reference/journal.md` (create) | What the two artifacts are, what a record means, what `journal` reports. |
| `skills/adopt-validated-memory/SKILL.md` (modify) | The ignore block gains `/.validated-memory/`; the questionnaire says `journal.jsonl` is not subject to the versioning choice. |

---

### Task 1: The record format and the two artifacts

**Files:**
- Create: `validated_memory/journal.py`
- Create: `tests/test_journal.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `JOURNAL_FILENAME = "journal.jsonl"`, `VAULT_DIRNAME = ".validated-memory"`,
    `VAULT_JOURNAL = "local.jsonl"`, `PREIMAGE_DIRNAME = "preimages"`,
    `LOCK_FILENAME = "lock"`, `SCHEMA = 1`
  - `REPO = "repo"`, `LOCAL = "local"`
  - ops: `OBSERVE`, `CREATE`, `REPLACE`, `PATCH`, `APPEND`, `LINK`, `RENAME`,
    `REMOVE`, `MOVE`; tuple `OPS`
  - stages: `PREPARED = "prepared"`, `COMMITTED = "committed"`
  - `class JournalError(Exception)` with `.lineno` and `.message`
  - `digest(data: bytes) -> str` — `"sha256:<hex>"`
  - `now() -> str` — ISO-8601 UTC with a trailing `Z`
  - `record(op, purpose, path, durability, stage, **extra) -> dict`
  - `append(records, root=Path(), durability=REPO) -> None`
  - `read(root=Path(), durability=REPO) -> list[dict]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_journal.py`:

```python
"""End-to-end tests for the journal: the durable record of every mutation.

Like every test in this suite these drive the CLI as a subprocess over a
fixture adopter tree, and never import the package's internals. What a
record means is `docs/reference/journal.md`; what it is for is
`docs/design/2026-08-30-the-journal-coverage-and-reversal-design.md`.
"""

import json


def _records(path):
    """Every JSON record in a `.jsonl` file, in file order."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_init_writes_a_journal_whose_records_carry_the_common_fields(
    run_cli, tmp_path
):
    """Every record says when, by what, under which adoption and run.

    Without those a record cannot be attributed, and an unattributable
    record cannot be diffed against the next run -- which is the whole
    reason the log exists rather than a report that lives for one message.
    """
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    journal = tmp_path / "journal.jsonl"
    assert journal.is_file(), sorted(p.name for p in tmp_path.iterdir())
    records = _records(journal)
    assert records, "the journal is empty"
    for entry in records:
        assert entry["schema"] == 1, entry
        assert entry["at"].endswith("Z"), entry
        assert entry["version"], entry
        assert entry["adoption"], entry
        assert entry["run"], entry
        assert entry["durability"] == "repo", entry
        assert entry["op"] in (
            "observe",
            "create",
            "replace",
            "patch",
            "append",
            "link",
            "rename",
            "remove",
            "move",
        ), entry
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_journal.py -q`
Expected: FAIL — `journal.jsonl` does not exist (`assert journal.is_file()`).

- [ ] **Step 3: Write `validated_memory/journal.py`**

```python
"""The append-only record of every mutation the plugin performs.

Two artifacts, because durability is not one question (ADR 0008). The
repository journal `journal.jsonl` travels with the project: it carries the
mutations a clone can see and the history a later run diffs against. The
vault `.validated-memory/` never leaves this clone: it carries preimages,
which may hold bytes the adopter deliberately kept local, and the record of
mutations whose path leaves the repository root.

Both are append-only, one JSON object per line, never rewritten, never
compacted, never sorted -- the same shape `verdicts.jsonl` already uses, for
the same reason: an appended log is the only one that cannot lose history by
accident.

Unlike the verdict log, a journal is NOT regenerable. Nothing re-derives a
preimage or the fact that a path already existed before adoption, so a
reader that cannot parse it must fail loudly rather than serve a partial
answer computed from the lines it happened to understand.
"""

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

JOURNAL_FILENAME = "journal.jsonl"
VAULT_DIRNAME = ".validated-memory"
VAULT_JOURNAL = "local.jsonl"
PREIMAGE_DIRNAME = "preimages"
LOCK_FILENAME = "lock"

# The record format. A reader that meets a higher number refuses rather than
# guessing at fields it does not know.
SCHEMA = 1

REPO = "repo"
LOCAL = "local"
DURABILITIES = (REPO, LOCAL)

OBSERVE = "observe"
CREATE = "create"
REPLACE = "replace"
PATCH = "patch"
APPEND = "append"
LINK = "link"
RENAME = "rename"
REMOVE = "remove"
MOVE = "move"
OPS = (OBSERVE, CREATE, REPLACE, PATCH, APPEND, LINK, RENAME, REMOVE, MOVE)

PREPARED = "prepared"
COMMITTED = "committed"
STAGES = (PREPARED, COMMITTED)

COMMON_FIELDS = (
    "schema",
    "at",
    "version",
    "adoption",
    "run",
    "durability",
    "op",
    "purpose",
    "path",
    "stage",
)


class JournalError(Exception):
    """Raised when a journal cannot be read as records.

    `lineno` is None when the fault is the file's rather than a line's: it
    could not be opened or decoded at all.
    """

    def __init__(self, lineno, message):
        super().__init__(message)
        self.lineno = lineno
        self.message = message


def digest(data):
    """The content digest of `data` (bytes), as `sha256:<hex>`."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def now():
    """The current UTC time, ISO-8601 with a trailing 'Z'.

    Same shape `probe` writes into the verdict log, so a reader that already
    parses one parses the other.
    """
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return stamp.replace("+00:00", "Z")


def new_id():
    """A short, collision-resistant identifier for a run or an adoption."""
    return secrets.token_hex(8)


def record(op, purpose, path, durability=REPO, stage=COMMITTED, **extra):
    """One journal record, with its common fields filled in.

    `adoption` and `run` are supplied by the caller through `extra`, because
    an adoption id outlives the process and a run id groups one invocation's
    records: neither is this function's to invent.
    """
    if op not in OPS:
        raise ValueError(f"unknown op '{op}'")
    if durability not in DURABILITIES:
        raise ValueError(f"unknown durability '{durability}'")
    if stage not in STAGES:
        raise ValueError(f"unknown stage '{stage}'")
    entry = {
        "schema": SCHEMA,
        "at": now(),
        "version": __version__,
        "durability": durability,
        "op": op,
        "purpose": purpose,
        "path": path,
        "stage": stage,
    }
    entry.update(extra)
    return entry


def journal_path(root=Path(), durability=REPO):
    """Where the journal of `durability` lives, relative to the adopter root."""
    root = Path(root)
    if durability == LOCAL:
        return root / VAULT_DIRNAME / VAULT_JOURNAL
    return root / JOURNAL_FILENAME


def append(records, root=Path(), durability=REPO):
    """Append `records` to the journal of `durability`, one JSON line each.

    The handle is flushed and fsynced before returning: a `prepared` record
    that is still in a buffer when the process dies is a record that never
    existed, which is precisely the state the two-record protocol exists to
    rule out.
    """
    path = journal_path(root, durability)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for entry in records:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read(root=Path(), durability=REPO):
    """Every record in the journal of `durability`, in file order.

    A missing journal reads as no records. A journal that is there but
    cannot be parsed raises: see the module docstring for why a partial
    answer is not offered.
    """
    path = journal_path(root, durability)
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise JournalError(None, f"journal could not be read: {error}") from error
    records = []
    for offset, line in enumerate(text.splitlines()):
        lineno = offset + 1
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as error:
            raise JournalError(lineno, f"line is not valid JSON: {error.msg}")
        if not isinstance(entry, dict):
            raise JournalError(lineno, "record is not a JSON object")
        missing = [field for field in COMMON_FIELDS if field not in entry]
        if missing:
            raise JournalError(lineno, f"record is missing {', '.join(missing)}")
        if entry["schema"] > SCHEMA:
            raise JournalError(
                lineno,
                f"record uses schema {entry['schema']}, newer than this "
                f"plugin understands ({SCHEMA}); upgrade the plugin",
            )
        if entry["op"] not in OPS:
            raise JournalError(lineno, f"record has unknown op '{entry['op']}'")
        if entry["stage"] not in STAGES:
            raise JournalError(lineno, f"record has unknown stage '{entry['stage']}'")
        records.append(entry)
    return records
```

Add `import os` to the imports (it is used by `append`'s fsync):

```python
import hashlib
import json
import os
import secrets
```

- [ ] **Step 4: Run the test to verify it still fails, for the right reason**

Run: `python3 -m pytest tests/test_journal.py -q`
Expected: still FAIL on `assert journal.is_file()` — nothing calls the module
yet. This confirms the test is pinning behaviour, not the module's existence.

- [ ] **Step 5: Commit**

```bash
git add validated_memory/journal.py tests/test_journal.py
git commit -m "feat: the journal record format and its two artifacts

Two append-only artifacts, because durability is not one question: the
repository journal travels with the project, the vault never leaves the
clone. A journal is not regenerable, so a reader that cannot parse one
raises with the line rather than serving an answer computed from the lines
it happened to understand.

The test is red until init calls this: it pins the record, not the module.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Adoption identity, atomic bootstrap and the lock

**Files:**
- Modify: `validated_memory/journal.py`
- Modify: `tests/test_journal.py`

**Interfaces:**
- Consumes: Task 1's `record`, `append`, `read`, `new_id`, `journal_path`.
- Produces:
  - `bootstrap(root=Path()) -> str` — returns the adoption id, creating the
    journal atomically on first call and reading it back on every later one.
  - `class Lock` — context manager over `.validated-memory/lock`; raises
    `JournalError(None, ...)` when it cannot be taken.
  - `STALE_LOCK_SECONDS = 300`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_journal.py`:

```python
def test_the_adoption_id_is_minted_once_and_survives_later_runs(run_cli, tmp_path):
    """One adoption, one id, however many times `init` runs.

    `init` is deliberately re-runnable at session start. An id minted per
    run would make every run look like a separate adoption, and the reversal
    a later plan builds could not tell which records belong together.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    first = {entry["adoption"] for entry in _records(tmp_path / "journal.jsonl")}
    assert len(first) == 1, first

    assert run_cli("init", cwd=tmp_path).returncode == 0
    second = {entry["adoption"] for entry in _records(tmp_path / "journal.jsonl")}
    assert second == first, (first, second)


def test_each_invocation_groups_its_records_under_one_run_id(run_cli, tmp_path):
    """One command, one run id -- and a second command, a second one."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    assert run_cli("init", cwd=tmp_path).returncode == 0

    runs = [entry["run"] for entry in _records(tmp_path / "journal.jsonl")]
    assert len(set(runs)) == 2, runs


def test_a_journal_that_cannot_be_parsed_is_refused_with_its_line(run_cli, tmp_path):
    """A partial answer from a journal is worse than no answer.

    Nothing regenerates a journal, so a reader that skipped the line it did
    not understand would silently narrow the record -- the failure mode this
    whole component exists to remove.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        journal.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8"
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "journal.jsonl" in result.stderr, result.stderr
    assert "not valid JSON" in result.stderr, result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_journal.py -q`
Expected: FAIL — no `journal.jsonl` is produced at all yet.

- [ ] **Step 3: Add bootstrap and the lock to `validated_memory/journal.py`**

Append to the module:

```python
# A lock older than this whose owner is gone is broken rather than waited on:
# a process killed between taking the lock and releasing it must not wedge
# every later run of a startup hook.
STALE_LOCK_SECONDS = 300


class Lock:
    """A per-adopter exclusive lock, taken for the duration of a mutation.

    `init` is deliberately re-runnable at session start and concurrent
    renderers are already expected, so two processes appending interleaved
    `prepared` records to one journal is a real state, not a theoretical
    one -- and it would produce a journal describing a state that never
    existed.

    The lock is a file created with `O_CREAT | O_EXCL`, which is atomic. Its
    contents are the owning pid, so a stale lock can be attributed.
    """

    def __init__(self, root=Path()):
        self.path = Path(root) / VAULT_DIRNAME / LOCK_FILENAME
        self._fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 10
        while True:
            try:
                self._fd = os.open(
                    self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
                )
                try:
                    os.write(self._fd, f"{os.getpid()}\n".encode("ascii"))
                except OSError:
                    # `__exit__` never runs when `__enter__` raises, so the
                    # descriptor and the lock file have to be released here or
                    # they outlive the failure by the whole stale window.
                    os.close(self._fd)
                    self._fd = None
                    self.path.unlink(missing_ok=True)
                    raise
                return self
            except FileExistsError:
                if self._break_if_stale():
                    continue
                if time.monotonic() >= deadline:
                    raise JournalError(
                        None,
                        f"another validated-memory process holds "
                        f"{self.path.as_posix()}; retry when it finishes",
                    )
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, traceback):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self.path.unlink(missing_ok=True)
        return False

    def _break_if_stale(self):
        """Remove the lock when its owner is gone. Returns whether it broke one."""
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return True  # It went away on its own; try again immediately.
        if age < STALE_LOCK_SECONDS:
            return False
        self.path.unlink(missing_ok=True)
        return True


def bootstrap(root=Path()):
    """Ensure the journal exists, and return this adoption's id.

    This is the one write that cannot journal itself: a record describing
    the journal's own creation would have nowhere to go until the journal
    exists. So the opening record is written complete to a temporary file,
    flushed, and atomically installed -- before any adopter mutation, so
    there is no window in which a mutation has happened and no journal
    exists to describe it. The temporary is plugin-owned and is not itself
    journalled.

    The caller must already hold `Lock`. This does not take it: `init` holds
    it for the whole run, and re-entering would need a re-entrant lock. Two
    processes bootstrapping the same new adopter without it would mint two
    adoption ids, and the second install would win in silence.
    """
    path = journal_path(root, REPO)
    existing = read(root, REPO)
    if existing:
        return existing[0]["adoption"]

    adoption = new_id()
    opening = record(
        OBSERVE,
        "init",
        JOURNAL_FILENAME,
        durability=REPO,
        stage=COMMITTED,
        adoption=adoption,
        run=new_id(),
        note="journal opened",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(opening, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return adoption
```

Add `import time` to the imports:

```python
import hashlib
import json
import os
import secrets
import time
```

**Measured while writing this plan:** taking the lock twice in one process
blocks for the full ten-second deadline before raising, because the holder is
this same process and is never stale. That is correct, but it makes any test
that exercises contention slow. Test the lock by holding it in a subprocess
and asserting the second caller's message, or lower the deadline through a
module constant — do not add a ten-second test to the suite.

- [ ] **Step 4: Run the tests to verify they still fail for the right reason**

Run: `python3 -m pytest tests/test_journal.py -q`
Expected: still FAIL on the missing `journal.jsonl` — Task 4 wires `init`.
Confirm the module itself is sound:

Run: `PYTHONPATH=. python3 -P -c "from validated_memory import journal; import tempfile, pathlib; d=pathlib.Path(tempfile.mkdtemp()); a=journal.bootstrap(d); print(a == journal.bootstrap(d)); print((d/'journal.jsonl').read_text())"`
Expected: `True`, then one JSON line whose `op` is `observe`.

- [ ] **Step 5: Commit**

```bash
git add validated_memory/journal.py tests/test_journal.py
git commit -m "feat: the adoption id, the atomic bootstrap and the per-adopter lock

The journal's own creation is the one write that cannot journal itself, so
it is written complete to a temporary, fsynced, and installed with a rename
before any adopter mutation: there is no window where a mutation happened
and no journal describes it.

The adoption id is minted once and read back afterwards, because init is
re-runnable at session start and an id per run would make every run look
like a separate adoption. The lock is O_CREAT|O_EXCL and breaks a stale one
rather than wedging a startup hook forever.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The transaction — `prepared`, the atomic write, `committed`

**Files:**
- Modify: `validated_memory/journal.py`
- Modify: `tests/test_journal.py`

**Interfaces:**
- Consumes: Task 1's `record`/`append`/`digest`; Task 2's `bootstrap`/`Lock`.
- Produces:
  - `class Run` — one invocation's journalling context. Constructed
    `Run(root=Path(), purpose_default=None)`; exposes `.adoption`, `.run`,
    and the methods below.
  - `Run.observe(path, note, durability=REPO) -> None` — a single
    `committed` record for a pre-adoption fact; no preimage, no inverse.
  - `Run.write(path, content, purpose, durability=REPO) -> None` — the
    two-record transaction around creating or replacing a text file.
  - `Run.park_preimage(path) -> str | None` — copy a file's current bytes
    into the vault and return the stored reference, or None when the path
    does not exist.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_journal.py`:

```python
def test_a_replaced_file_records_its_preimage_and_both_stages(run_cli, tmp_path):
    """Replacing a file records what it was, before it stops being that.

    A `created` path needs no preimage: its inverse is removal. A replaced
    one does, and it can only be taken the first time, because only the
    first copy is the pre-adoption state.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    # `init` keeps an existing file rather than replacing it, so the second
    # run must record `observe`, not `replace`, for the same paths.
    records = _records(tmp_path / "journal.jsonl")
    creates = [e for e in records if e["op"] == "create" and e["stage"] == "committed"]
    assert creates, records
    for entry in creates:
        assert entry["postimage"].startswith("sha256:"), entry

    prepared = [e for e in records if e["stage"] == "prepared"]
    committed = [e for e in records if e["stage"] == "committed"]
    assert len(prepared) == len(creates), (prepared, creates)
    # Every prepared record is followed by its committed twin for the same path.
    assert {e["path"] for e in prepared} <= {e["path"] for e in committed}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_journal.py -q`
Expected: FAIL — no journal is written by `init` yet.

- [ ] **Step 3: Add the transaction to `validated_memory/journal.py`**

Append to the module:

```python
class Run:
    """One invocation's journalling context.

    Holds the adoption id, this run's id and the lock, and turns a mutation
    into the three steps §4 of the design requires: a flushed `prepared`
    record carrying the preimage and the expected postimage, the atomic
    mutation, then a flushed `committed` record. A `prepared` with no
    `committed` is what `journal --check` reconciles; nothing here guesses
    at one.
    """

    def __init__(self, root=Path()):
        self.root = Path(root)
        self.adoption = bootstrap(self.root)
        self.run = new_id()

    def _record(self, op, purpose, path, durability, stage, **extra):
        return record(
            op,
            purpose,
            path,
            durability=durability,
            stage=stage,
            adoption=self.adoption,
            run=self.run,
            **extra,
        )

    def observe(self, path, note, durability=REPO):
        """Record a pre-adoption fact about `path`. Written once, never inverted."""
        append(
            [self._record(OBSERVE, "init", path, durability, COMMITTED, note=note)],
            self.root,
            durability,
        )

    def park_preimage(self, path):
        """Copy the current bytes of `path` into the vault; return the reference.

        Returns None when `path` does not exist, which is what distinguishes
        a `create` from a `replace`. A preimage is parked only the first
        time a given path is written, because only that copy is the
        pre-adoption state -- a second copy would record an intermediate
        state as if it were the original.
        """
        target = self.root / path
        if not target.exists() or target.is_dir():
            return None
        data = target.read_bytes()
        reference = digest(data)
        blob = (
            self.root
            / VAULT_DIRNAME
            / PREIMAGE_DIRNAME
            / reference.replace("sha256:", "")
        )
        if not blob.exists():
            blob.parent.mkdir(parents=True, exist_ok=True)
            temporary = blob.with_name(f"{blob.name}.{os.getpid()}.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, blob)
        return reference

    def write(self, path, content, purpose, durability=REPO):
        """Create or replace the text file at `path`, journalling both stages.

        `path` is relative to the adopter root and is written to the journal
        exactly as given, so a record never carries an absolute path -- which
        §7 of the design refuses to act on later.
        """
        location = Path(path).as_posix()
        data = content.encode("utf-8")
        preimage = self.park_preimage(location)
        op = CREATE if preimage is None else REPLACE
        postimage = digest(data)

        append(
            [
                self._record(
                    op,
                    purpose,
                    location,
                    durability,
                    PREPARED,
                    preimage=preimage,
                    postimage=postimage,
                )
            ],
            self.root,
            durability,
        )

        target = self.root / location
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

        append(
            [
                self._record(
                    op,
                    purpose,
                    location,
                    durability,
                    COMMITTED,
                    preimage=preimage,
                    postimage=postimage,
                )
            ],
            self.root,
            durability,
        )
```

- [ ] **Step 4: Verify the module behaves, since the CLI test is still red**

Run:
```bash
PYTHONPATH=. python3 -P - <<'PY'
import json, pathlib, tempfile
from validated_memory import journal
d = pathlib.Path(tempfile.mkdtemp())
run = journal.Run(d)
run.write("hello.md", "first\n", "init")
run.write("hello.md", "second\n", "init")
for line in (d / "journal.jsonl").read_text().splitlines():
    e = json.loads(line)
    print(e["op"], e["stage"], e["path"], e.get("preimage"))
print((d / "hello.md").read_text())
PY
```
Expected: an `observe` opening record, then `create prepared` / `create
committed` with `preimage` None, then `replace prepared` / `replace
committed` with a `sha256:` preimage; and `second` as the file's content.

- [ ] **Step 5: Commit**

```bash
git add validated_memory/journal.py tests/test_journal.py
git commit -m "feat: the two-record transaction with parked preimages

Neither one-record protocol works: record first and a crash leaves the
journal claiming a state that never existed; mutate first and it leaves an
unjournalled mutation. So every mutation is a flushed prepared record, the
atomic write, and a flushed committed record.

A preimage is parked only the first time a path is written, because only
that copy is the pre-adoption state.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `init` journals its write paths

**Files:**
- Modify: `validated_memory/init.py:117-200`
- Modify: `tests/test_init.py`

**Interfaces:**
- Consumes: Task 3's `Run` (`observe`, `write`), Task 2's `Lock`.
- Produces: no new names. `init.run` gains the side effect of a journal, and
  keeps its exact stdout contract (`init: created <path>` / `init: kept
  <path>` and the summary line) so no existing test changes meaning.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_journal.py`:

```python
def test_init_records_create_for_what_it_made_and_observe_for_what_it_kept(
    run_cli, tmp_path
):
    """The two outcomes `init` already prints are the two records it writes.

    "It was already here" is a fact about the pre-adoption state and cannot
    be re-derived afterwards, which is why it is written down rather than
    inferred at reversal time -- the defect that retired the first uninstall
    design.
    """
    (tmp_path / "knowledge").mkdir()

    assert run_cli("init", cwd=tmp_path).returncode == 0

    records = _records(tmp_path / "journal.jsonl")
    committed = [e for e in records if e["stage"] == "committed"]
    by_path = {e["path"]: e["op"] for e in committed}
    assert by_path["knowledge"] == "observe", by_path
    assert by_path["memory"] == "create", by_path
    assert by_path["validated-memory.md"] == "create", by_path
    assert by_path["memory/MEMORY.md"] == "create", by_path
    assert by_path["knowledge-extension.md"] == "create", by_path


def test_a_second_init_records_observe_for_everything_it_kept(run_cli, tmp_path):
    """A re-run creates nothing, so it records no `create`."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    first = len(_records(tmp_path / "journal.jsonl"))

    assert run_cli("init", cwd=tmp_path).returncode == 0

    added = _records(tmp_path / "journal.jsonl")[first:]
    assert added, "the second run recorded nothing"
    assert {e["op"] for e in added} == {"observe"}, added
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_journal.py -q`
Expected: FAIL — `journal.jsonl` is still not written.

- [ ] **Step 3: Wire `init` to the journal**

In `validated_memory/init.py`, add to the imports:

```python
from . import adopt, journal, render
```

Replace the body of `run` from the `findings = []` line down to (and
including) the `for item, outcome, finding in steps:` loop with:

```python
    findings = []
    created = 0
    kept = 0

    try:
        with journal.Lock():
            session = journal.Run()
            steps = (
                _ensure_dir(Path("knowledge"), session),
                _ensure_dir(Path("memory"), session),
                _ensure_file(
                    Path("memory") / "MEMORY.md", MEMORY_INDEX, session
                ),
                _ensure_file(Path("validated-memory.md"), CONFIG, session),
                _ensure_file(
                    Path("knowledge-extension.md"), EXTENSION_STUB, session
                ),
            )
    except journal.JournalError as error:
        where = journal.JOURNAL_FILENAME
        location = where if error.lineno is None else f"{where}:{error.lineno}"
        print(
            Finding(ERROR, location, "journal", error.message).render(),
            file=stderr,
        )
        print(
            "init: 0 created, 0 kept, 1 error(s), 0 warning(s)", file=stdout
        )
        return EXIT_ERROR

    for item, outcome, finding in steps:
        if finding is not None:
            findings.append(finding)
            continue
        print(f"init: {outcome} {item}", file=stdout)
        if outcome == "created":
            created += 1
        else:
            kept += 1
```

Then give the two helpers a `session` parameter:

```python
def _ensure_dir(path, session):
    """Create `path` as a directory if missing. Returns `(item, outcome, finding)`.

    A directory that is already there is recorded as an observation: that it
    pre-existed is a fact about the state before adoption, and nothing can
    re-derive it later.
    """
    location = path.as_posix()
    if path.exists():
        session.observe(location, "directory already present")
        return location, "kept", None
    try:
        path.mkdir(parents=True)
    except OSError as error:
        return location, None, Finding(
            ERROR, location, "create", f"directory could not be created: {error}"
        )
    session.observe(location, "directory created")
    return location, "created", None


def _ensure_file(path, content, session):
    """Write `content` to `path` if missing. Returns `(item, outcome, finding)`."""
    location = path.as_posix()
    if path.exists():
        session.observe(location, "file already present")
        return location, "kept", None
    try:
        session.write(location, content, "init")
    except OSError as error:
        return location, None, Finding(
            ERROR, location, "create", f"file could not be created: {error}"
        )
    return location, "created", None
```

`mkdir` is not a file write and has no preimage, so the created case carries
no transaction -- but its inverse belongs in the `op`, not in prose. Replace
`_ensure_dir`'s created branch with:

```python
    session.append_op(journal.CREATE, "init", location, "directory created")
    return location, "created", None
```

and add to `Run` in `validated_memory/journal.py`:

```python
    def append_op(self, op, purpose, path, note, durability=REPO):
        """Record a completed mutation with no file preimage, such as a mkdir."""
        append(
            [self._record(op, purpose, path, durability, COMMITTED, note=note)],
            self.root,
            durability,
        )
```

**The harness symlink is the fourth write path, and it is LOCAL.** Its target
is outside the repository root, so §1 puts its record in the vault, not in
`journal.jsonl`. In `_sync_symlink` (`init.py:199-240`), record what the path
was before the link replaced it -- that former target is the single fact no
later computation recovers. Give `_sync_symlink` the session and add, at the
two points where it calls `symlink_to`:

```python
    previous = os.readlink(path) if path.is_symlink() else None
    ...
    session.append_op(
        journal.LINK,
        "init",
        path.as_posix(),
        f"previous target: {previous}" if previous else "no previous link",
        durability=journal.LOCAL,
    )
```

Read `previous` BEFORE the `unlink`/`symlink_to` pair, not after: once the
link is re-pointed its former target is gone, which is exactly the preimage
problem in miniature. `path.as_posix()` here is an absolute path outside the
root, which is why it may only ever live in the vault -- ADR 0008 and §7 of
the design forbid a repository record from carrying one.
- [ ] **Step 4: Run the journal and init suites**

Run: `python3 -m pytest tests/test_journal.py tests/test_init.py -q`
Expected: PASS. If `tests/test_init.py` fails on a count of files created in
the adopter root, it is because `journal.jsonl` and `.validated-memory/` are
now there: update that assertion to name them, and add a comment saying the
journal is a root output like the rest.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m pytest -q`
Expected: PASS. `tests/test_hooks_manifest.py` and
`tests/test_walkthrough.py` are the likely fallers, for the same reason.

- [ ] **Step 6: Commit**

```bash
git add validated_memory/init.py validated_memory/journal.py tests/
git commit -m "feat: init journals what it created and what it found

The two outcomes init already prints are now the two records it writes.
'It was already here' is a fact about the pre-adoption state that nothing
re-derives afterwards -- assuming it could be computed at uninstall time is
the defect that retired the first reversal design.

The whole run is under the per-adopter lock, because init is deliberately
re-runnable at session start.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The `journal` subcommand and reconciliation

**Files:**
- Modify: `validated_memory/journal.py`
- Modify: `validated_memory/cli.py:14-22` (SUBCOMMANDS), `:25-165`
  (build_parser), `:167-215` (main)
- Modify: `tests/test_skills_structure.py:29`
- Modify: `tests/test_journal.py`

**Interfaces:**
- Consumes: Task 1's `read`, Task 3's `Run`.
- Produces:
  - `journal.run(check, stdout, stderr) -> int` — the subcommand entry point,
    matching the shape `lint.run`/`status.run` use.
  - `reconcile(root=Path()) -> list[tuple[dict, str]]` — every `prepared`
    record with no `committed` twin, paired with one of
    `"unapplied"`, `"applied"`, `"diverged"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_journal.py`:

```python
def test_journal_reports_the_log_and_exits_clean(run_cli, tmp_path):
    """Reading the journal is read-only and never gates on its own."""
    assert run_cli("init", cwd=tmp_path).returncode == 0

    result = run_cli("journal", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "journal:" in result.stdout, result.stdout
    assert "record(s)" in result.stdout, result.stdout


def test_journal_check_reconciles_an_unfinished_transaction(run_cli, tmp_path):
    """A prepared record with no committed twin is reported, never guessed at.

    Recovery says which of the three states the path is in and stops. A
    recovery that decided for itself would be the silent narrowing again,
    one layer down.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    orphan = {
        "schema": 1,
        "at": "2026-08-31T00:00:00Z",
        "version": "1.6.0",
        "adoption": json.loads(journal.read_text().splitlines()[0])["adoption"],
        "run": "0000000000000000",
        "durability": "repo",
        "op": "replace",
        "purpose": "init",
        "path": "validated-memory.md",
        "stage": "prepared",
        "preimage": "sha256:" + "0" * 64,
        "postimage": "sha256:" + "1" * 64,
    }
    journal.write_text(
        journal.read_text(encoding="utf-8")
        + json.dumps(orphan, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "validated-memory.md" in result.stderr, result.stderr
    assert "unfinished" in result.stderr, result.stderr
    assert "diverged" in result.stderr, result.stderr
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_journal.py -q -k journal_report or journal_check`
Expected: FAIL with exit code 2 — `journal` is not a subcommand yet.

- [ ] **Step 3: Add reconciliation and the entry point to `journal.py`**

Append to the module:

```python
UNAPPLIED = "unapplied"
APPLIED = "applied"
DIVERGED = "diverged"


def reconcile(root=Path()):
    """Every unfinished transaction, paired with the state its path is in.

    `unapplied` -- the bytes still match the preimage, so the mutation never
    happened. `applied` -- they match the postimage, so it happened and only
    the closing record was lost. `diverged` -- neither, so something else
    wrote the path afterwards.

    This reports. It does not repair: choosing for the user between three
    states the record cannot distinguish is exactly the guessing this
    component exists to remove.
    """
    root = Path(root)
    unfinished = []
    for durability in DURABILITIES:
        records = read(root, durability)
        closed = {
            (entry["run"], entry["path"])
            for entry in records
            if entry["stage"] == COMMITTED
        }
        for entry in records:
            if entry["stage"] != PREPARED:
                continue
            if (entry["run"], entry["path"]) in closed:
                continue
            unfinished.append((entry, _state_of(root, entry)))
    return unfinished


def _state_of(root, entry):
    target = root / entry["path"]
    try:
        actual = digest(target.read_bytes())
    except (OSError, ValueError):
        actual = None
    if actual == entry.get("postimage"):
        return APPLIED
    if actual == entry.get("preimage"):
        return UNAPPLIED
    return DIVERGED


def run(check, stdout, stderr):
    """The `journal` subcommand: report the record, and optionally reconcile.

    Read-only in both modes. Without `--check` it summarises and exits 0
    whatever it finds, so a reader can look at a project without gating on
    it; with `--check` an unfinished transaction is an ERROR, because a
    caller that asked to be told cannot be told by an exit code of 0.
    """
    from .findings import ERROR, EXIT_ERROR, EXIT_OK, Finding

    root = Path()
    try:
        records = read(root, REPO) + read(root, LOCAL)
    except JournalError as error:
        where = JOURNAL_FILENAME
        location = where if error.lineno is None else f"{where}:{error.lineno}"
        print(Finding(ERROR, location, "journal", error.message).render(), file=stderr)
        print("journal: 0 record(s), 1 error(s)", file=stdout)
        return EXIT_ERROR

    if not check:
        print(f"journal: {len(records)} record(s)", file=stdout)
        return EXIT_OK

    unfinished = reconcile(root)
    for entry, state in unfinished:
        print(
            Finding(
                ERROR,
                entry["path"],
                "journal",
                f"unfinished transaction from run {entry['run']}: "
                f"the path is {state}",
            ).render(),
            file=stderr,
        )
    print(
        f"journal: {len(records)} record(s), {len(unfinished)} error(s)",
        file=stdout,
    )
    return EXIT_ERROR if unfinished else EXIT_OK
```

The `findings` import is local to `run` to keep the module importable by the
hook path without pulling the reporting layer in; if `journal.py` already
imports it at module level for another reason, move it up rather than
duplicating.

- [ ] **Step 4: Wire the subcommand in `validated_memory/cli.py`**

Add to the import line:

```python
from . import (
    __version__,
    derive,
    init,
    journal,
    lint,
    probe,
    render,
    status,
    validate,
)
```

Add to `SUBCOMMANDS`, after `"status"`:

```python
    "journal": "Report the append-only record of what the plugin has written",
```

Add to `build_parser`, alongside the other per-command blocks:

```python
        if name == "journal":
            subparser.add_argument(
                "--check",
                action="store_true",
                help=(
                    "report every unfinished transaction and gate on it "
                    "(exit 1); without it, reporting never gates"
                ),
            )
```

Add to `main`, before the final `return init.run(...)`:

```python
    if args.command == "journal":
        return journal.run(args.check, stdout=sys.stdout, stderr=sys.stderr)
```

Update `tests/test_skills_structure.py:29`:

```python
REAL_SUBCOMMANDS = {
    "init",
    "lint",
    "validate",
    "derive",
    "probe",
    "render",
    "status",
    "journal",
}
```

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m pytest -q`
Expected: PASS. `tests/test_cli.py` may pin the subcommand list or the
`--help` output; update it to include `journal` with a comment saying the
set moved deliberately.

- [ ] **Step 6: Commit**

```bash
git add validated_memory/journal.py validated_memory/cli.py tests/
git commit -m "feat: the journal subcommand reports and reconciles

Read-only in both modes. Plain reporting never gates, so a reader can look
at a project without failing it; --check gates, because a caller that asked
to be told cannot be told by an exit code of 0.

Reconciliation names which of three states an unfinished transaction left
its path in and stops there. Repairing it would mean choosing for the user
between states the record cannot distinguish, which is the guessing this
component exists to remove.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The completeness pin, the ADR, the reference and the ignore block

**Files:**
- Create: `docs/adr/0008-the-journal-is-versioned-and-the-vault-is-local.md`
- Create: `docs/reference/journal.md`
- Modify: `skills/adopt-validated-memory/SKILL.md:31` (the ignore block)
- Modify: `tests/test_journal.py`
- Modify: `.claude-plugin/plugin.json`, `pyproject.toml`,
  `validated_memory/__init__.py` (version `1.5.2` → `1.6.0`)

**Interfaces:**
- Consumes: everything above.
- Produces: no new names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_journal.py`:

```python
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_every_write_in_the_package_goes_through_the_journal():
    """A mutation with no record fails here, not in the field.

    The 1.5.0 and 1.5.1 failures were both silent narrowings that no test
    could see. This is the pin that makes a new unjournalled write path
    visible the moment it is added: `write_text`, `write_bytes`, `mkdir`,
    `symlink_to`, `rmdir`, `unlink`, `replace` and `rename` are the calls
    that mutate, and every one outside `journal.py` must be reachable from a
    `Run` method or named here with the reason it is exempt.
    """
    mutating = re.compile(
        r"\.(write_text|write_bytes|mkdir|symlink_to|rmdir|unlink|rename)\("
        r"|os\.(replace|rename|remove|symlink)\("
    )
    # Exempt, with the reason. `journal.py` IS the write path. `render.py`
    # writes only derived artifacts, which are regenerable by definition and
    # are journalled in plan 2 with the rest of the confirmed writes.
    exempt = {"journal.py", "render.py", "adopt.py"}
    offenders = []
    for path in sorted((REPO_ROOT / "validated_memory").rglob("*.py")):
        if path.name in exempt:
            continue
        for offset, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if mutating.search(line) and "journal" not in line:
                offenders.append(f"{path.name}:{offset + 1}: {line.strip()}")
    assert not offenders, (
        "these mutate without going through the journal; route them through "
        "a Run method or add them to `exempt` with the reason:\n"
        + "\n".join(offenders)
    )


def test_the_vault_is_ignored_and_the_journal_is_not():
    """The durability split is a claim the adoption skill has to carry.

    The journal travels with the project or the method's promise is empty in
    a fresh clone; the vault never does, because a preimage may hold bytes
    the adopter deliberately kept local.
    """
    skill = (
        REPO_ROOT / "skills" / "adopt-validated-memory" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "/.validated-memory/" in skill, "the vault is not in the ignore block"
    assert "/journal.jsonl" not in skill, (
        "the journal must not be offered as ignorable: it is not regenerable"
    )
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_journal.py -q -k "every_write or vault_is_ignored"`
Expected: FAIL — `init.py` still has bare `mkdir` calls, and the skill has no
`/.validated-memory/` line.

- [ ] **Step 3: Route the remaining writes and update the skill**

Measured on `main` at 1.5.2, the regex above finds **eleven** mutating calls
outside `journal.py`. Tasks 4 and 5 route five of them. Here is every one and
its disposition — the pin must end green with exactly these exemptions and no
others, so a new write path added later cannot hide among them.

| Call | Disposition |
|---|---|
| `init.py:176` `path.mkdir(parents=True)` | Routed in Task 4 via `append_op(CREATE, ...)`. |
| `init.py:190` `path.parent.mkdir(...)` | Deleted in Task 4 — `Run.write` already does it. |
| `init.py:191` `path.write_text(...)` | Routed in Task 4 via `Run.write`. |
| `init.py:221-222`, `init.py:232-233` `unlink` + `symlink_to` | Routed in Task 4 via `append_op(LINK, ..., durability=LOCAL)`. |
| `init.py:311-315` view temporaries | **Exempt.** `_ensure_views` writes `knowledge.html` and `memory.html`, derived artifacts that `render` regenerates. Journalled in plan 2 with the rest of the derived surface. |
| `derive.py:59` `index_path.write_text(...)` | **Exempt.** `knowledge-index.md` is derived and regenerable by `derive` itself; nothing is lost that a re-run does not restore. Plan 2. |

So the exemption set in the test becomes file-scoped for the two whole
modules that only write derived artifacts, plus two named lines in `init.py`.
Replace the `exempt` set in the test with:

```python
    # Exempt, each with the reason it is not an adopter mutation this plan
    # has to record. `journal.py` IS the write path. `render.py` and
    # `derive.py` write only derived artifacts, which their own commands
    # regenerate; they are journalled in plan 2. `adopt.py` performs the
    # harness absorption, which plan 5 records with the rest of reversal.
    exempt_files = {"journal.py", "render.py", "derive.py", "adopt.py"}
    # The view temporaries in `init._ensure_views` write derived artifacts
    # too, but the module around them is not exempt, so they are named.
    exempt_lines = {("init.py", "temporary.write_text"), ("init.py", "temporary.unlink")}
```

and skip a line when its `(file, needle)` pair is in `exempt_lines`. If the
pin is red on anything not in these two collections, that call is a write
path this plan missed: route it, do not widen the exemption.

In `skills/adopt-validated-memory/SKILL.md`, add `/.validated-memory/` to the
"Local, ignored" block, and add this paragraph immediately after it:

```markdown
     `journal.jsonl` is **not** part of this choice and is never added to the
     ignore list. It is the record of what adoption did, it is not
     regenerable by anything, and a clone without it cannot reverse an
     adoption or diff one scan's coverage against the next. `.validated-
     memory/` is the other half of that split and is always ignored, whatever
     the answer here: it holds preimages, which may carry bytes this very
     question chose to keep local.
```

- [ ] **Step 4: Write the ADR**

Create `docs/adr/0008-the-journal-is-versioned-and-the-vault-is-local.md`:

```markdown
# 0008 — The journal is always versioned and the vault is always local

## Status

Accepted, 2026-08-31.

## Context

ADR 0002 and ADR 0003 let the adopter decide whether the derived artifacts
travel with the repository. That question is answerable for them because they
are derived: a clone without `knowledge-index.md` re-derives it.

The journal is not derived. Nothing re-computes a preimage, or the fact that
`memory/` existed before adoption. A first design put the journal under the
same question. An adversarial review showed the two answers are both wrong
for one artifact: always-versioned publishes preimages the adopter chose to
keep local, and always-local means a fresh clone or a CI run cannot reverse
an adoption or diff one scan's coverage against the next.

## Decision

Two artifacts with fixed, opposite durability.

`journal.jsonl` at the adopter root is **always versioned**. It carries
repository-visible mutations and the portable history of coverage and
rejection. The adoption questionnaire does not offer to ignore it.

`.validated-memory/` at the adopter root is **always local to the clone**.
It carries preimages and the record of mutations whose path leaves the
repository root. `init` writes its ignore entry; the questionnaire does not
ask.

Every record names its own domain in a `durability` field, so a reader knows
which artifact holds its preimage and can say what it cannot do when that
artifact is absent. Required repository history that is missing or corrupt is
**exit 1**, never a silent fall back to a degraded algorithm.

## Consequences

A versioned journal is repository content, and this project's rule is that
repository content is data, never instructions. So a reader validates the
schema, rejects absolute paths, `..`, symlink ancestors and path-type
changes, and refuses before the first write rather than acting on a record it
half understands. A path outside the repository root can never be authorised
by the file itself: it lives in the vault, and acting on it needs a fresh CLI
argument naming that same path.

An adopter who versions the journal publishes the shape of their adoption —
which paths existed, which were created, when. That is the price of a
reversal that works in a fresh clone, and the questionnaire says so.
```

- [ ] **Step 5: Write the reference page**

Create `docs/reference/journal.md`, covering: the two artifacts and their
durability; the common fields; the `op` table from §2 of the design with each
inverse; the two stages and what an unfinished transaction means; what
`journal` and `journal --check` print and when each gates. Cross-link it from
`docs/reference/hooks.md` and the README's reference list, and check
`tests/test_docs_links.py` still passes.

- [ ] **Step 6: Bump the version in the three files**

```bash
sed -i 's/"version": "1.5.2"/"version": "1.6.0"/' .claude-plugin/plugin.json
sed -i 's/^version = "1.5.2"/version = "1.6.0"/' pyproject.toml
sed -i 's/^__version__ = "1.5.2"/__version__ = "1.6.0"/' validated_memory/__init__.py
grep -rn '1\.6\.0' .claude-plugin/plugin.json pyproject.toml validated_memory/__init__.py
```

Expected: three lines, one per file (ADR 0005).

- [ ] **Step 7: Run the whole suite**

Run: `python3 -m pytest -q`
Expected: PASS, with the suite grown by the tests this plan added.

- [ ] **Step 8: Commit**

```bash
git add docs/ skills/ tests/ .claude-plugin/plugin.json pyproject.toml validated_memory/
git commit -m "feat: pin the journal's completeness, and record the durability split

A mutation with no record now fails a test rather than the field: every
mutating call outside the journal module must go through a Run method or be
named exempt with its reason. Both 1.5.0 and 1.5.1 shipped a silent
narrowing that no test could see; this is the pin that makes the next one
visible when it is written.

ADR 0008 records why the journal is outside the versioning question ADR 0002
and 0003 ask, and why the vault is on the other side of it.

Version 1.5.2 -> 1.6.0.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage.** §1 (two records, `durability`, exit 1 on missing history)
→ Tasks 1 and 6. §2 (`op`/`purpose` axes, the inverse table) → Task 1, with
the table itself in Task 6's reference page. §4 (crash consistency,
`prepared`/`committed`, atomic bootstrap, meta-path exemption, lock) → Tasks
2 and 3. §7's journal-as-hostile-data rules → Task 1's `read` validation and
ADR 0008; the path constraints (`..`, symlink ancestors, path-type change)
are **declared but not enforced here**, because nothing acts on a record
until plan 5 — they are that plan's first task, and this plan's `read`
deliberately validates only what it needs to refuse a log it cannot trust.

**Not in this plan, by design:** §3 (the transaction interface for confirmed
writes — plan 2), §5 (coverage enumeration — plan 3), §6 (rejection — plan
4), §7's reversal, destination and manifest (plan 5). `render.py` and
`adopt.py` are exempt from Task 6's pin for now and are journalled in plan 2
and plan 5 respectively; the exemption is named in the test so it cannot be
forgotten.

**Known open question for the executor.** Task 4 gives `_ensure_dir` an
`observe` for the kept case and a `create` for the made case, which means one
call site records through two different `Run` methods. If that reads badly in
the code, fold both into `append_op` with the op chosen by the branch — the
records must stay as specified, the shape of the call is the implementer's.
