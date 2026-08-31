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
import os
import secrets
import time
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


def bootstrap(root=Path(), run=None):
    """Ensure the journal exists, and return this adoption's id.

    This is the one write that cannot journal itself: a record describing
    the journal's own creation would have nowhere to go until the journal
    exists. So the opening record is written complete to a temporary file,
    flushed, and atomically installed -- before any adopter mutation, so
    there is no window in which a mutation has happened and no journal
    exists to describe it. The temporary is plugin-owned and is not itself
    journalled.

    `run` is the invocation's run id, so the opening record -- minted only
    the first time a project ever bootstraps -- carries the same run id as
    every other record that invocation writes, rather than a run of its own.
    A caller that does not have one yet (there is none besides `Run`) gets
    one minted here, so the record is always complete.

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
        run=run if run is not None else new_id(),
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


class Run:
    """One invocation's journalling context.

    Holds the adoption id and this run's id, and turns a mutation into the
    three steps §4 of the design requires: a flushed `prepared` record
    carrying the preimage and the expected postimage, the atomic mutation,
    then a flushed `committed` record. A `prepared` with no `committed` is
    what `journal --check` reconciles; nothing here guesses at one.

    It does NOT hold the lock. `bootstrap`, which `__init__` calls, requires
    the caller to hold `Lock` already, and so does every mutation below it.
    """

    def __init__(self, root=Path()):
        self.root = Path(root)
        self.run = new_id()
        self.adoption = bootstrap(self.root, self.run)

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
            # The blob is named after its own digest, so a torn write would
            # leave bytes that silently disagree with the name every later
            # reader trusts. Every other atomic write in this module fsyncs
            # before the rename; this one has to as well.
            with temporary.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, blob)
        return reference

    def write(self, path, content, purpose, durability=REPO):
        """Create or replace the text file at `path`, journalling both stages.

        `path` is relative to the adopter root and is written to the journal
        exactly as given, so a record never carries an absolute path -- which
        §7 of the design refuses to act on later.
        """
        location = Path(path).as_posix()
        if Path(location).is_absolute() or ".." in Path(location).parts:
            raise ValueError(
                f"{location} is not a path inside the adopter root; a "
                "repository record may only carry a relative path that stays "
                "below it. Nothing has been recorded."
            )
        if (self.root / location).is_dir():
            raise IsADirectoryError(
                f"{location} is a directory; refusing to journal a file write "
                "against it. Nothing has been recorded."
            )
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

    def append_op(self, op, purpose, path, note, durability=REPO):
        """Record a completed mutation with no file preimage, such as a mkdir."""
        append(
            [self._record(op, purpose, path, durability, COMMITTED, note=note)],
            self.root,
            durability,
        )


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
