"""The append-only record of what `init` does to an adopter project.

Every mutation `init` performs is recorded here as it happens. That is not
yet every mutation the plugin performs: `derive`, `probe`, `render` and
`init --view` write derived artifacts their own commands regenerate, and
they are not recorded -- see "What is recorded, and what is not yet" in
`docs/reference/journal.md` for the list and the plan that closes it.

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
import stat
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

JOURNAL_FILENAME = "journal.jsonl"
VAULT_DIRNAME = ".validated-memory"
VAULT_JOURNAL = "local.jsonl"
PREIMAGE_DIRNAME = "preimages"
LOCK_FILENAME = "lock"
TRANSACTIONS_DIRNAME = "transactions"

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

# A transaction file's own stage word, not a journal record's: the two
# artifacts are different files with different lifetimes (design §3), and
# `PREPARED` is shared between them on purpose -- both name the same moment,
# a write-ahead entry fsynced with nothing published yet.
PUBLISHED = "published"
ABORTED = "aborted"
TRANSACTION_STAGES = (PREPARED, PUBLISHED, ABORTED)

# The state a path is expected to be in, or found to be in -- lstat
# semantics throughout, so a symlink is a fact about itself, never about
# what it points at. `absent` needs no other field; `directory`, `file` and
# `symlink` may each carry a `mode` (see `satisfies`); `file` also carries a
# content `digest`, `symlink` also carries its `target` (a `readlink`).
ABSENT = "absent"
DIRECTORY = "directory"
FILE = "file"
SYMLINK = "symlink"

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

# What each field must hold. A journal is repository content, which this
# project's rule makes data and never instructions (design §7): checking that
# a field is present says nothing about what is in it, and every later reader
# -- the schema comparison here, the path the reconciler builds -- assumes a
# type nothing had checked. `bool` is excluded from `int` deliberately:
# `isinstance(True, int)` is true, and `"schema": true` is not a schema.
FIELD_TYPES = {
    "schema": int,
    "at": str,
    "version": str,
    "adoption": str,
    "run": str,
    "durability": str,
    "op": str,
    "purpose": str,
    "path": str,
    "stage": str,
}

# Fields only some ops carry, checked when present for the same reason.
OPTIONAL_FIELD_TYPES = {
    "preimage": (str, type(None)),
    "postimage": (str, type(None)),
    "note": (str,),
    "prior_bytes": (int,),
}


class JournalError(Exception):
    """Raised when a journal cannot be read as records.

    `lineno` is None when the fault is the file's rather than a line's: it
    could not be opened or decoded at all.

    `artifact` is the file the fault is in, relative to the adopter root.
    There are two journals and a lock, and a diagnostic that names the wrong
    one sends a reader to a file that is perfectly valid; None means the
    raiser did not say, and the caller falls back to the repository journal.
    """

    def __init__(self, lineno, message, artifact=None):
        super().__init__(message)
        self.lineno = lineno
        self.message = message
        self.artifact = artifact


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


# Named after the executor's protocol (Task 4, plan order): a transaction
# file fsynced with nothing published yet, the new bytes published but the
# transaction not yet marked, the transaction marked published but the
# permanent history not yet appended, and the history appended but the
# transaction not yet resolved. `after-prepared` and `after-mutation` are
# not executor points -- they are wired into the two-record protocol that
# `Run` already runs today (see `_fault`'s call sites below), so this seam
# can be proven through the CLI before the executor exists.
FAULT_POINTS = (
    "after-transaction",
    "after-publish",
    "after-published",
    "after-history",
    "after-prepared",
    "after-mutation",
)


def _fault(point):
    """Die at `point`, hard, if `VALIDATED_MEMORY_FAULT` names it.

    This is the one place in the package that reads that variable: a test
    driving the CLI as a subprocess has no `monkeypatch` that reaches past
    the subprocess boundary, and the only crash simulation this suite had
    before this function was hand-editing an artifact afterwards, which
    proves nothing about what the process actually leaves behind mid-write.

    The death is `os._exit`, not `sys.exit` or a raised exception: no
    `finally` clause runs, no lock is released, no temporary is cleaned up.
    That is what a real crash looks like, and a fault test's assertions are
    only honest if the seam does not clean up after itself. `70` (`EX_SOFTWARE`
    in the BSD sysexits convention this project otherwise ignores) is
    chosen only to be distinguishable from an ordinary exit code and a
    signal death; nothing reads it back.

    Unset, this changes nothing: `os.environ.get(...) == point` is false
    for every `point` when the variable is absent, so every call site below
    falls through exactly as if `_fault` were not called at all. Set to a
    point this run never reaches, it is equally inert. Nothing outside this
    function may read `VALIDATED_MEMORY_FAULT`.
    """
    if os.environ.get("VALIDATED_MEMORY_FAULT") == point:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(70)


def current_state(root, path):
    """What is actually at `path` (under `root`), in the vocabulary above.

    `lstat`, never `stat`: the node itself is what is reported, so a symlink
    is `symlink` whether or not it resolves -- a broken one is `symlink`,
    never `absent` or `directory`. `directory` exists because a `create`
    record with no bytes to digest needs a check richer than "the name
    resolves to something", which a broken symlink also satisfies; that is
    today's false `applied` (design §6).

    Anything `lstat` cannot see at all -- nothing there, a missing parent, a
    parent that denies traversal -- reads as `absent`; this function asks
    one question, "what is at this exact name", and every reason `lstat`
    has for not answering it collapses to the same "nothing was found"
    here. A regular file's bytes are read for its digest; a node that is
    neither a directory, a symlink nor a regular file (a FIFO, a socket, a
    device -- nothing this project ever creates) is reported as `file`
    without a `digest`, since reading through it could block forever and
    the vocabulary has no fifth word for it.

    Always carries `mode`, on every kind but `absent`: this is the actual
    side of a `satisfies` comparison, and the "any mode matches" case has to
    have something to compare away, not the absence of a comparison.
    """
    target = Path(root) / path
    try:
        info = os.lstat(target)
    except OSError:
        return {"kind": ABSENT}
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISDIR(info.st_mode):
        return {"kind": DIRECTORY, "mode": mode}
    if stat.S_ISLNK(info.st_mode):
        return {"kind": SYMLINK, "target": os.readlink(target), "mode": mode}
    if stat.S_ISREG(info.st_mode):
        return {"kind": FILE, "digest": digest(target.read_bytes()), "mode": mode}
    return {"kind": FILE, "mode": mode}


def satisfies(actual, expected):
    """Whether `actual` (from `current_state`) matches `expected`.

    Every field `expected` names must equal the same field of `actual`,
    except `mode`: an expected state that omits it matches any mode, and one
    that carries it must match exactly. This is one comparison for every
    kind rather than a branch per kind, because the fields that matter
    already differ by kind (`digest` for `file`, `target` for `symlink`) and
    `expected` only ever names the ones its own kind carries.
    """
    for field, value in expected.items():
        if field != "mode" and actual.get(field) != value:
            return False
    if "mode" in expected and actual.get("mode") != expected["mode"]:
        return False
    return True


INTENTION_OPS = (OBSERVE, CREATE, REPLACE, APPEND, LINK)


@dataclass(frozen=True)
class Intention:
    """One validated, tagged mutation the executor (Task 4) will consume.

    A frozen dataclass rather than a dict, so a caller building one gets
    the invalid combinations below refused at construction, not discovered
    by the executor three calls later. Field by field:

    - `op` -- one of `INTENTION_OPS`: `OBSERVE`, `CREATE`, `REPLACE`,
      `APPEND` or `LINK`. `PATCH`, `RENAME`, `REMOVE` and `MOVE` are in
      `OPS` for the journal record vocabulary but have no intention shape
      yet; they are not accepted here.
    - `purpose` -- the same free-text word every record already carries
      (`"init"`, `"ignore-rule"`, ...).
    - `path` -- relative to the adopter root for `REPO`, unrestricted for
      `LOCAL` (`authorise`'s rule, ADR 0008).
    - `durability` -- one of `DURABILITIES`.
    - `expected` -- the preimage state, in `current_state`'s vocabulary
      (`{"kind": ABSENT}`, `{"kind": FILE, "digest": ..., "mode": ...}`,
      ...): what the executor's expected-state check compares against.
    - `content` -- `bytes`, or `None`. The full new bytes for `CREATE` of a
      file and `REPLACE`; the bytes to add for `APPEND`. Always `None` for
      `OBSERVE`, `LINK` and `CREATE` of a directory -- this is never a
      diff or a patch, and it is never persisted: `_open_transaction`
      writes the transaction file's `preimage`/`postimage` STATE, never
      these bytes, so payload content never touches the local disk twice.
    - `target` -- the new symlink target, for `LINK`; `None` otherwise.
    - `directory` -- `True` for `CREATE` of a directory; `False` (the
      default) otherwise, including for every op that has no notion of one.
    - `note` -- the same free-text annotation `prepare_op`/`append_op`
      already carry; `None` when there is nothing to say.

    `__post_init__` refuses six combinations, each a way the payload could
    silently disagree with `op`: a `LINK` carrying `content`, a directory
    `CREATE` carrying `content`, a file `CREATE`/`REPLACE`/`APPEND` carrying
    no `content`, a `LINK` carrying no `target`, an `OBSERVE` carrying any
    payload (`content`, `target` or `directory=True`), and an unknown `op`
    or `durability`. Every refusal is `ValueError`: nothing has been
    touched yet to reach it.
    """

    op: str
    purpose: str
    path: str
    durability: str
    expected: dict
    content: bytes | None = None
    target: str | None = None
    directory: bool = False
    note: str | None = None

    def __post_init__(self):
        if self.op not in INTENTION_OPS:
            raise ValueError(f"unknown op '{self.op}'")
        if self.durability not in DURABILITIES:
            raise ValueError(f"unknown durability '{self.durability}'")
        if self.op == LINK and self.content is not None:
            raise ValueError("a link intention carries no content")
        if self.op == CREATE and self.directory and self.content is not None:
            raise ValueError("a directory creation carries no content")
        if (
            self.op in (CREATE, REPLACE, APPEND)
            and not self.directory
            and self.content is None
        ):
            raise ValueError(f"a {self.op} intention of a file must carry content")
        if self.op == LINK and self.target is None:
            raise ValueError("a link intention must carry its target")
        if self.op == OBSERVE and (
            self.content is not None or self.target is not None or self.directory
        ):
            raise ValueError("an observe intention carries no payload")


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


def install(temporary, target):
    """Atomically move `temporary` onto `target`, durably.

    `os.replace` publishes the new bytes under the old name, but the
    directory entry carrying that name is itself buffered. Without the
    directory fsync, a `committed` record that was flushed to disk can
    outlive the rename it describes -- "a record describes a state that
    never existed", one power cut down -- so design §4's claim that a
    `committed` record means the bytes are on disk would hold for a process
    crash and not for a power loss.
    """
    os.replace(temporary, target)
    fsync_directory(Path(target).parent)


def fsync_directory(path):
    """Flush a directory's own entries to disk.

    A platform where a directory cannot be opened for reading skips the
    barrier rather than failing the write it was protecting: the bytes are
    already fsynced and renamed at this point, and refusing here would turn
    a durability improvement into a lost mutation.
    """
    try:
        handle = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)


def artifact_name(durability):
    """The journal of `durability`, named the way a finding names a file."""
    return journal_path(Path(), durability).as_posix()


def read(root=Path(), durability=REPO):
    """Every record in the journal of `durability`, in file order.

    A missing journal reads as no records. A journal that is there but
    cannot be parsed raises: see the module docstring for why a partial
    answer is not offered.

    "Cannot be parsed" is the whole of design §7, not just JSON: a record
    whose field holds the wrong type, whose `durability` disagrees with the
    file it is in, or whose repository-durability path leaves the adopter
    root, is refused here -- before any reader acts on it -- rather than
    crashing one layer down or being read as an instruction.

    "Missing" is exactly one thing: nothing that can be opened at that
    name. `Path.exists()` answers a wider question and answers it wrongly
    for this one -- it raises on a permission denial, which is a stack
    trace out of the check for a missing file. So the journal is OPENED
    first and every question is then asked of the DESCRIPTOR: whether it is
    a regular file, and what bytes it holds. `lstat` then `read_text` are
    two operations on a name, and a name can be repointed between them --
    the bytes read, and afterwards appended to, would then be whatever the
    name meant by the second call, past a check the first one passed.

    A symlink that resolves to a regular file is read like any other
    journal: nothing about reading it is unsafe, `append` writes through it
    by name, and an adopter who keeps the file in a store outside the
    project has a working adoption. A BROKEN symlink reads as absent, which
    is honest -- there is nothing to read through it -- and it is
    `bootstrap` that must not then install over the link, where the
    replacement it would destroy actually happens.
    """
    path = journal_path(root, durability)
    where = artifact_name(durability)
    try:
        # `O_NONBLOCK` so the open cannot hang on the very shape the check
        # below refuses: opening a FIFO for reading waits for a writer, and
        # a reader that never returns is worse than one that refuses. Asked
        # for by name because the flag is POSIX-only, and a platform without
        # it has no FIFOs to hang on either.
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return []
    except OSError as error:
        raise JournalError(
            None, f"journal could not be read: {error}", where
        ) from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise JournalError(
                None,
                "journal is not a regular file; a directory, a device or a "
                "pipe holds no records and nothing here can read one from it",
                where,
            )
        # `closefd=False`: the descriptor has one owner, the `finally` below,
        # so a failure between the two closes it exactly once.
        with open(descriptor, "rb", closefd=False) as handle:
            text = handle.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise JournalError(
            None, f"journal could not be read: {error}", where
        ) from error
    finally:
        os.close(descriptor)
    records = []
    for offset, line in enumerate(text.splitlines()):
        lineno = offset + 1
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as error:
            raise JournalError(
                lineno, f"line is not valid JSON: {error.msg}", where
            )
        if not isinstance(entry, dict):
            raise JournalError(lineno, "record is not a JSON object", where)
        missing = [field for field in COMMON_FIELDS if field not in entry]
        if missing:
            raise JournalError(
                lineno, f"record is missing {', '.join(missing)}", where
            )
        _check_types(lineno, entry, where)
        if entry["schema"] > SCHEMA:
            raise JournalError(
                lineno,
                f"record uses schema {entry['schema']}, newer than this "
                f"plugin understands ({SCHEMA}); upgrade the plugin",
                where,
            )
        if entry["op"] not in OPS:
            raise JournalError(
                lineno, f"record has unknown op '{entry['op']}'", where
            )
        if entry["stage"] not in STAGES:
            raise JournalError(
                lineno, f"record has unknown stage '{entry['stage']}'", where
            )
        if entry["durability"] != durability:
            # `append()` derives the file from the same field `record()`
            # stamps, so the two can only disagree by a hand edit -- and
            # trusting the field would let a `local` record, which may
            # legitimately carry a path outside the root, be smuggled into
            # the versioned journal to lift the check below.
            raise JournalError(
                lineno,
                f"record claims durability '{entry['durability']}' in the "
                f"'{durability}' journal; a record's durability is the file "
                "it lives in",
                where,
            )
        if durability == REPO and not _is_inside_path(entry["path"]):
            raise JournalError(
                lineno,
                f"record path '{entry['path']}' is not inside the adopter "
                "root; a repository record may only carry a relative path "
                "that stays below it",
                where,
            )
        records.append(entry)
    return records


def _check_types(lineno, entry, where):
    """Refuse a record whose field holds something of the wrong type."""
    for field, expected in FIELD_TYPES.items():
        value = entry[field]
        if expected is int and isinstance(value, bool):
            value = None
        if not isinstance(value, expected):
            raise JournalError(
                lineno,
                f"record field '{field}' holds {type(entry[field]).__name__}, "
                f"not {expected.__name__}",
                where,
            )
    for field, expected in OPTIONAL_FIELD_TYPES.items():
        if field in entry and not isinstance(entry[field], expected):
            raise JournalError(
                lineno,
                f"record field '{field}' holds "
                f"{type(entry[field]).__name__}, which it may not",
                where,
            )


def _is_inside_path(path):
    """Whether `path` is relative and names nothing above the adopter root.

    Lexical, because this runs on every record read: `authorise` applies the
    same rule to a record before it is written. The filesystem question --
    whether the path resolves below the root once symlinks are followed --
    is asked where something is about to be touched: `authorise` again,
    before every write and every record, and `_state_of` before a read.
    """
    candidate = Path(path)
    return not candidate.is_absolute() and ".." not in candidate.parts


def authorise(root, path, durability):
    """The location a record for `path` may carry, or refuse it.

    `local` asks neither question: the vault and the harness symlink
    legitimately name paths outside the adopter root (ADR 0008), so nothing
    here may narrow what a `local` record can say, and `path` comes back
    exactly as given.

    A `repo` intention asks both, in order:

    - Lexical -- `path` is relative and does not climb out with `..`
      (`_is_inside_path`). `ValueError`, because nothing was touched to
      find that out.
    - Resolved -- the location, joined to `root`, still resolves below
      `root` once every symlink on the way is followed (`_resolves_below`).
      `OSError`, because a caller may already have read something (a
      preimage, an existing record) to reach this line.

    The two exception types are load-bearing, not incidental: `init` catches
    `OSError` per item, so a path that escapes through a symlink gates the
    one item that named it and nothing else.

    Called once, at the very start of each public `Run` method that can
    reach the journal -- `observe`, `prepare_op`, `append_op` directly, and
    `write`/`append_text` through `_location` -- before anything is parked,
    appended or written, so the resolved question is now asked for
    `observe`, `prepare_op` and `append_op` too. Before this, only `write`
    and `append_text` asked it: a `memory/` symlinked to a directory outside
    the project was lexically fine, so `observe` filed it into the
    versioned journal as a fact about the tree, even though the bytes it
    named were never inside the tree at all.

    Deliberately NOT called from `_record`: that helper builds both the
    `prepared` and the `committed` half of a write, and the second call
    happens after the mutation (`_write_bytes`) has already run. Asking the
    resolved question there again could refuse after the bytes were already
    on disk, abandoning a `prepared` record that reconciliation was built
    to close rather than a fresh failure to raise past -- see `_record`.

    What this does NOT provide: `dir_fd`-relative ancestor stabilisation.
    Both checks resolve `path` by name, and nothing stops a hostile process
    from swapping an ancestor directory for a symlink between this call
    returning and the action that follows it acting on the same name --
    that window is real, this project's test seam cannot demonstrate it,
    and closing it needs the executor's own descriptor-relative operations
    rather than a second `resolve()` here. It belongs to step 1b.
    """
    if durability != REPO:
        return path
    location = Path(path).as_posix()
    if not _is_inside_path(location):
        raise ValueError(
            f"{location} is not a path inside the adopter root; a "
            "repository record may only carry a relative path that stays "
            "below it. Nothing has been recorded."
        )
    if not _resolves_below(Path(root), Path(root) / location):
        raise OSError(
            f"{location} resolves outside the adopter root; a repository "
            "record may only name bytes that stay below it. Nothing has "
            "been written and nothing has been recorded."
        )
    return location


# The last resort, not the liveness test: a lock older than this is broken on
# age alone. `Lock` reads the owning pid first and never breaks a lock whose
# owner is still running, whatever its age, and breaks one whose owner is
# gone at once -- a run killed mid-mutation must not wedge the next session's
# `init` for five minutes. What is left for this horizon is a lock file whose
# pid cannot be read at all: a run killed between creating the file and
# writing its pid leaves an empty one, and nothing about it says who to ask.
# Five minutes is a bet that no legitimate holder is slower than that, which
# holds today because the lock is held for the length of one `init`.
STALE_LOCK_SECONDS = 300


def lock_path(root=Path()):
    """Where `root`'s lock file lives: beside the journal it protects.

    The lock exists to stop two processes appending interleaved records to
    ONE journal, and `journal.jsonl` is allowed to be a symlink into a
    shared store (`read` reads through it, `append` writes through it). Two
    adopter trees linked at one store would take two different local locks
    and serialise nothing, so the answer is taken from the RESOLVED
    artifact: the lock is `.validated-memory/lock` under the directory the
    journal really lives in. For an adopter whose journal is a plain file
    -- every adopter until someone links one -- that directory is the
    adopter root, and this names the same file as before, spelled
    absolutely.

    A `journal.jsonl` symlink that resolves to anything but a regular file
    -- broken, or a directory -- keeps the local lock. There is no shared
    store to serialise against, `read` refuses such a journal anyway, and
    following the link would create `.validated-memory/` somewhere outside
    the adopter root that nobody asked for.

    One thing a shared store does not get: a store reached over a network
    filesystem is shared between HOSTS, and the pid `Lock` writes into the
    file means nothing on another host or in another pid namespace, where
    it may name a live local process or none at all. The mutual exclusion
    still holds -- it is `O_CREAT | O_EXCL` -- but breaking a lock left
    behind by a dead run is a single-host promise.
    """
    root = Path(root)
    artifact = root / JOURNAL_FILENAME
    resolved = _resolved(artifact)
    if resolved is None or (
        artifact.is_symlink() and not _is_regular_file(resolved)
    ):
        home = _resolved(root)
        if home is None:
            # Absolute even here: `_HELD` keys on this path, and two
            # spellings of one directory must not become two holders.
            home = root.absolute()
        resolved = home / JOURNAL_FILENAME
    return resolved.parent / VAULT_DIRNAME / LOCK_FILENAME


def _resolved(path):
    """`path` with its symlinks followed, or None when they cannot be.

    Resolution is not total: a symlink loop raises, and it raises
    `RuntimeError` rather than `OSError`, which is not what any caller of
    this module catches. Deciding where the lock goes must not be the step
    that ends a run with a traceback -- a journal whose name cannot be
    resolved is refused moments later by `read`, with a message that names
    the file and the reason. So an unresolvable name gets the local answer
    and the run reaches that refusal.
    """
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _is_regular_file(path):
    """Whether `path` is a regular file, following links, without raising."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except OSError:
        return False


# The locks this process holds: `{lock path: [descriptor, depth]}`, keyed by
# the resolved path so that two `Lock` objects naming one file are one
# holder. Re-entrancy has to be per PROCESS, not per object: `init.run`
# takes the lock for a whole run and `Run.__init__` takes it again
# underneath through an object the outer caller never sees. This is process
# state and it is not thread-safe: the depth is read and written without a
# mutex, so two threads of one process entering at once can miscount. Every
# caller today is single-threaded.
_HELD = {}


class Lock:
    """A per-adopter exclusive lock, taken for the duration of a mutation.

    `init` is deliberately re-runnable at session start and concurrent
    renderers are already expected, so two processes appending interleaved
    `prepared` records to one journal is a real state, not a theoretical
    one -- and it would produce a journal describing a state that never
    existed.

    The lock is a file created with `O_CREAT | O_EXCL`, which is atomic. Its
    contents are the owning pid, and that pid is what the rest of this class
    is built on: it is read back to ask whether the owner is still running.

    Within one process the lock is RE-ENTRANT, counted per resolved lock
    path and not per object (`_HELD`): `init.run` holds it for a whole run
    and `Run.__init__` takes it again underneath, and a second `O_EXCL`
    create would wait out the whole ten seconds and then refuse the run.
    The file is created by the outermost `__enter__` and removed by the
    `__exit__` that brings the depth back to zero. Between processes it
    excludes exactly as before. The registry is module state, so a `Lock`
    object means nothing outside the process that entered it: one must not
    be shared with a child process or carried across a fork as held.

    Waiting for a lock someone else holds breaks in only when the holder is
    provably gone. A lock whose pid names a live process is never broken,
    whatever its age -- the holder is doing the work the lock protects. A
    lock whose pid names no process is broken at once, because a run that
    died mid-mutation must not wedge the next session's `init` behind the
    age horizon. `STALE_LOCK_SECONDS` covers only what is left: a pid that
    cannot be read.

    Never breaking a live pid has a price, and the contention message pays
    it: if the operating system has handed that pid to an unrelated process
    since the run died, nothing here can tell, so the lock is honoured
    forever and the message says to delete the file when no
    validated-memory process is running.

    Releasing identifies the lock by the device and inode of the descriptor
    this process holds, not by the name, and unlinks nothing else, so a
    process whose lock was broken cannot delete its successor's. Breaking
    re-checks the same identity immediately before it unlinks. Neither
    makes breaking atomic: between that check and the `unlink` the file can
    still be replaced, and a break landing in that window can leave two
    processes holding the lock. What the checks buy is the width of the
    window -- two adjacent syscalls, rather than the read, the probe and
    the unlink that spanned it before -- and the certainty that a release
    never removes a file this process did not create.
    """

    def __init__(self, root=Path()):
        self.path = lock_path(root)
        # What a Finding should call this lock. Naming the vault of the tree
        # the command ran in would send a reader to a directory that holds
        # no lock whenever the journal is a symlink into a store, so the
        # label is the lock's own path: relative to the root while it is
        # inside it, which is every adopter that has not linked its journal
        # away, and absolute when it is not.
        home = _resolved(root) or Path(root).absolute()
        try:
            self.artifact = self.path.relative_to(home).as_posix()
        except ValueError:
            self.artifact = self.path.as_posix()
        # The registry key, and a count of the entries THIS object made, so
        # an `__exit__` without a matching `__enter__` cannot decrement a
        # depth some other object is holding.
        self._key = str(self.path)
        self._entries = 0

    def __enter__(self):
        held = _HELD.get(self._key)
        if held is not None:
            held[1] += 1
            self._entries += 1
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 10
        while True:
            try:
                descriptor = os.open(
                    self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
                )
                try:
                    os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
                except OSError:
                    # `__exit__` never runs when `__enter__` raises, so the
                    # descriptor and the lock file have to be released here or
                    # they outlive the failure by the whole stale window. A
                    # lock file with no pid in it is also the one shape no
                    # later run can ask about, so it must not be left behind.
                    os.close(descriptor)
                    self.path.unlink(missing_ok=True)
                    raise
                _HELD[self._key] = [descriptor, 1]
                self._entries += 1
                return self
            except FileExistsError:
                if self._break_if_unowned():
                    continue
                if time.monotonic() >= deadline:
                    raise JournalError(
                        None,
                        f"another validated-memory process holds "
                        f"{self.path.as_posix()}; retry when it finishes, "
                        f"or if no validated-memory process is running, "
                        f"delete {self.path.as_posix()}",
                        self.artifact,
                    )
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, traceback):
        if not self._entries:
            return False
        self._entries -= 1
        held = _HELD.get(self._key)
        if held is None:
            return False
        held[1] -= 1
        if held[1] > 0:
            return False
        del _HELD[self._key]
        descriptor = held[0]
        try:
            self._unlink_if_still_ours(descriptor)
        finally:
            os.close(descriptor)
        return False

    def _unlink_if_still_ours(self, descriptor):
        """Remove the lock file only when it is still the one we created.

        The descriptor outlives the name: if this lock was broken and a
        successor created its own file under the same path, `st_dev` and
        `st_ino` differ and the successor's lock is left exactly where it
        is. A path that is already gone -- broken and not yet retaken -- is
        nothing to undo either.
        """
        try:
            mine = os.fstat(descriptor)
            there = os.stat(self.path)
        except OSError:
            return
        if (there.st_dev, there.st_ino) == (mine.st_dev, mine.st_ino):
            self.path.unlink(missing_ok=True)

    def _break_if_unowned(self):
        """Remove the lock when nothing is holding it any more.

        Returns whether it broke one, so the caller can retry at once.

        The file is opened ONCE and every question is asked of that
        descriptor -- the pid it holds, its age, and which file it is --
        because a lock file can be replaced between two calls that name it,
        and the answers would then describe two different files.

        Three answers, cheapest and most certain first. The file is gone:
        someone released it, retry. The pid names a process that exists:
        never break it, whatever the file's age -- that process is inside
        the mutation this lock protects, and `PermissionError` from
        `os.kill` is one of those, a live process owned by another user.
        The pid names no process (`ProcessLookupError`): the file outlived
        its owner, so break it now rather than making every later run wait
        out `STALE_LOCK_SECONDS` behind a run that is already dead.

        Only a pid that cannot be read -- an empty file from a run killed
        between creating it and writing to it, bytes that are not a
        positive integer, a file that cannot even be opened -- falls
        through to the age horizon. `os.kill(0, 0)` signals this process's
        whole group, so a pid that is not positive is never asked about.
        """
        try:
            descriptor = os.open(
                self.path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
            )
        except FileNotFoundError:
            return True  # It went away on its own; try again immediately.
        except OSError:
            # Unopenable, so nothing can be asked of it: the age of the name
            # is all that is left.
            return self._break_if_old()
        try:
            status = os.fstat(descriptor)
            raw = os.read(descriptor, 64)
        except OSError:
            return self._break_if_old()
        finally:
            os.close(descriptor)
        identity = (status.st_dev, status.st_ino)
        try:
            pid = int(raw.decode("ascii").strip())
        except ValueError:
            pid = 0
        if pid <= 0:
            return self._break_if_old(status, identity)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            self._unlink_if_unchanged(identity)
            return True
        except OSError:
            return False  # Alive, or unknowable: either way, not ours.
        return False

    def _break_if_old(self, status=None, identity=None):
        """Break the lock on age alone -- the last resort, for an unreadable pid.

        `status` and `identity` come from the descriptor the caller already
        had, so the age and the file are the same file; without them the
        name is stat'ed here, which is the only way in when the file could
        not be opened at all.
        """
        if status is None:
            try:
                status = os.stat(self.path)
            except FileNotFoundError:
                return True  # Gone on its own; try again immediately.
            except OSError:
                return False  # Cannot even be looked at: wait it out.
            identity = (status.st_dev, status.st_ino)
        if time.time() - status.st_mtime < STALE_LOCK_SECONDS:
            return False
        self._unlink_if_unchanged(identity)
        return True

    def _unlink_if_unchanged(self, identity):
        """Remove the lock only while it is still the file `identity` names.

        Between deciding a lock may be broken and removing it, its owner can
        have released it and a third process taken its place. Without this
        re-check the breaker would delete the newcomer's live lock and then
        create its own, and two processes would hold the lock at once. The
        re-check does not close that hole -- the file can still be replaced
        between this `stat` and the `unlink` below -- it narrows it to those
        two calls.
        """
        try:
            here = os.stat(self.path)
        except OSError:
            return
        if (here.st_dev, here.st_ino) == identity:
            self.path.unlink(missing_ok=True)


def _transactions_dir(root):
    """Where transaction files live: under the vault, never versioned."""
    return Path(root) / VAULT_DIRNAME / TRANSACTIONS_DIRNAME


def _transaction_path(root, transaction_id):
    """The file one transaction lives in."""
    return _transactions_dir(root) / f"{transaction_id}.json"


def _write_transaction_file(root, transaction_id, entry):
    """Write `entry` as the whole of one transaction file, fsynced in place.

    Temporary, fsync, `install` -- the same durability shape every other
    atomic write in this module uses (`bootstrap`, `park_preimage`,
    `_write_bytes`): the bytes are flushed and fsynced before the rename,
    and `install` fsyncs the directory after it, so the file this call
    leaves behind is exactly as durable whether it is the first write of a
    new transaction or a rewrite of `stage` on an existing one.
    """
    directory = _transactions_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = _transaction_path(root, transaction_id)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        install(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _open_transaction(
    root,
    intention,
    preimage,
    postimage,
    preimage_blob=None,
    mode=None,
    adoption=None,
    run=None,
):
    """Open the local write-ahead log entry for one mutation; return its id.

    The transaction file NEVER holds payload bytes -- `intention.content`
    is not written here, only the states either side of it -- so a torn or
    truncated transaction file never rewrites data on recovery; it only
    ever tells recovery what the mutation intended and what it should have
    changed. Its fields:

    | field            | holds                                                        |
    |------------------|---------------------------------------------------------------|
    | `schema`         | the same `SCHEMA` a journal record uses                       |
    | `at`             | when this transaction was opened                               |
    | `version`        | the plugin version that opened it                              |
    | `adoption`       | this project's adoption id                                    |
    | `run`            | the invocation's run id                                        |
    | `transaction`    | this transaction's own id (also the filename stem)             |
    | `intention`      | `{op, purpose, path, durability, note?, directory?, target?}`  |
    | `preimage`       | the preimage STATE (a `current_state`-shaped dict)              |
    | `postimage`      | the postimage STATE, computed by the caller                    |
    | `preimage_blob`  | the parked preimage's `sha256:...` reference, or `None`         |
    | `mode`           | the target's mode bits when it had one, or `None`               |
    | `stage`          | `"prepared"`, `"published"` or `"aborted"`                     |
    | `reason`         | present only once `stage` is `"aborted"`                       |

    `postimage` is not derived here: an `APPEND`'s digest needs the bytes
    already on disk, which only the caller (the executor, Task 4) has read.
    `intention.expected` is the caller's precondition, not this file's
    `preimage` -- the two usually agree, but the transaction records what
    the state actually was, not what the caller hoped to find.

    Fsynced before the id is returned: a transaction file that exists but
    is not yet durable is worse than no transaction file at all, since
    recovery would then trust a record a crash could still make disappear.
    """
    transaction_id = new_id()
    payload_intention = {
        "op": intention.op,
        "purpose": intention.purpose,
        "path": intention.path,
        "durability": intention.durability,
    }
    if intention.note is not None:
        payload_intention["note"] = intention.note
    if intention.directory:
        payload_intention["directory"] = True
    if intention.target is not None:
        payload_intention["target"] = intention.target
    entry = {
        "schema": SCHEMA,
        "at": now(),
        "version": __version__,
        "adoption": adoption,
        "run": run,
        "transaction": transaction_id,
        "intention": payload_intention,
        "preimage": preimage,
        "postimage": postimage,
        "preimage_blob": preimage_blob,
        "mode": mode,
        "stage": PREPARED,
    }
    _write_transaction_file(root, transaction_id, entry)
    return transaction_id


def _mark_published(root, transaction_id):
    """Record, fsynced, that publication completed.

    Not decoration: a `replace` whose new bytes equal the old, an `append`
    of empty content, and every no-bytes intention (`create` of a
    directory, `link`) satisfy the preimage and postimage states at once,
    so recovery cannot always tell from the filesystem alone whether the
    mutation happened. This marker, fsynced after publication, is what
    turns that inference into a fact (design §3).
    """
    path = _transaction_path(root, transaction_id)
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["stage"] = PUBLISHED
    _write_transaction_file(root, transaction_id, entry)


def _abort_transaction(root, transaction_id, reason):
    """Close a transaction that will never publish, recording why."""
    path = _transaction_path(root, transaction_id)
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["stage"] = ABORTED
    entry["reason"] = reason
    _write_transaction_file(root, transaction_id, entry)


def _resolve_transaction(root, transaction_id):
    """Unlink a transaction's file and fsync the directory that held it.

    A resolved transaction leaves the directory: this is the only function
    that removes a transaction file, called once recovery (or the executor
    itself, on its own successful run) has no further use for it.
    """
    path = _transaction_path(root, transaction_id)
    path.unlink(missing_ok=True)
    fsync_directory(path.parent)


def _open_transactions(root):
    """Every unresolved transaction, ordered by `at` with `id` as tiebreaker.

    A transaction FILE present is "unresolved"; among those, `prepared` and
    `published` are "open" and `aborted` is closed pending removal -- this
    function does not distinguish the three, because a caller such as
    `journal --check` reports all of them the same way: something is still
    on disk that a clean run would have resolved away.

    Each entry carries its `id` (the filename stem) alongside whatever the
    file held. A file that is not readable, not valid JSON, or not a JSON
    object yields `{"id": <stem>, "damaged": "<reason>"}` and nothing else
    -- never a traceback, never silently skipped, the same promise `read`
    makes for the two journals. `*.tmp` temporaries -- `_write_transaction_file`'s
    own in-flight writes -- are not transactions and are ignored.
    """
    directory = _transactions_dir(root)
    try:
        names = sorted(entry.name for entry in directory.iterdir())
    except FileNotFoundError:
        return []
    results = []
    for name in names:
        if not name.endswith(".json"):
            continue
        transaction_id = name[: -len(".json")]
        path = directory / name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            results.append({"id": transaction_id, "damaged": str(error)})
            continue
        try:
            entry = json.loads(text)
        except json.JSONDecodeError as error:
            results.append(
                {"id": transaction_id, "damaged": f"not valid JSON: {error.msg}"}
            )
            continue
        if not isinstance(entry, dict):
            results.append(
                {"id": transaction_id, "damaged": "record is not a JSON object"}
            )
            continue
        entry = dict(entry)
        entry["id"] = transaction_id
        results.append(entry)
    results.sort(key=lambda item: (item.get("at", ""), item["id"]))
    return results


def bootstrap(root=Path(), run=None, records=None, local=None):
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

    `Run.__init__` holds the lock across this call, so a caller does not
    have to: `Lock` is re-entrant within a process, and the run-wide lock
    `init` already holds and the one taken there are the same lock. Two
    processes bootstrapping the same new adopter without it would mint two
    adoption ids, and the second install would win in silence.

    `records` and `local` are the two journals the caller has already read,
    so a caller that needs the records for something else does not read the
    files twice. Each must be exactly what `read(root, ...)` returned for
    its durability; anything else would mint a second adoption id over a
    journal that already has one.

    Both artifacts are consulted, because only one of them is versioned.
    `journal.jsonl` is tracked and the vault is ignored, so an ordinary
    `git checkout` of a commit from before the adoption takes the journal
    away and leaves the vault: minting again there would file this run's
    records under an id the vault's preimages know nothing about, split one
    adoption in two, and report clean throughout, since no record is
    missing or malformed.
    """
    path = journal_path(root, REPO)
    existing = read(root, REPO) if records is None else records
    kept = read(root, LOCAL) if local is None else local
    adoption = _adoption_id(existing, kept)
    if existing:
        return adoption

    # Nothing was read, so the install below is about to publish the opening
    # record under this NAME -- and `os.replace` replaces a symlink rather
    # than following it. A link here is the adopter's: a broken one reads as
    # absent and a resolvable one carries no records yet, and replacing
    # either destroys something `init` did not create and cannot put back,
    # which is exactly the trade `init.BROKEN_SYMLINK` refuses everywhere
    # else. A link to a journal that HAS records never reaches this line.
    if path.is_symlink():
        raise JournalError(
            None,
            f"{JOURNAL_FILENAME} is a symlink and holds no records; "
            "installing the journal here would replace the link itself, "
            "which is the adopter's and cannot be put back -- point it at a "
            "journal or remove it",
            artifact_name(REPO),
        )

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
        install(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return adoption


def _adoption_id(repository, vault):
    """This project's adoption id, from whichever journal still carries one.

    A fresh one only when neither does. Two artifacts carrying DIFFERENT
    ids is a state a user can reach -- a vault copied into another tree, a
    `journal.jsonl` restored from a different clone -- and it is the one
    case nothing here can resolve: the preimages in the vault belong to one
    of the two adoptions and no record says which, so attaching this run to
    either would file it against somebody else's pre-adoption state. It
    refuses and names both, which is the only answer that leaves the user
    able to decide -- and names the two ways out, because `init` is what the
    session hook runs and this refusal stops it, so a user told only that
    the state is wrong has no command left to run.
    """
    minted = repository[0]["adoption"] if repository else None
    kept = vault[0]["adoption"] if vault else None
    if minted is not None and kept is not None and minted != kept:
        raise JournalError(
            None,
            f"the vault is filed under adoption '{kept}' while "
            f"{JOURNAL_FILENAME} is filed under '{minted}'; one project has "
            "one adoption id, and nothing here can say which of the two is "
            f"this project's -- restore the {JOURNAL_FILENAME} filed under "
            f"'{kept}', or move {VAULT_DIRNAME}/ aside to adopt afresh, "
            "since its preimages belong to the adoption it names",
            artifact_name(LOCAL),
        )
    if minted is not None:
        return minted
    return kept if kept is not None else new_id()


class Run:
    """One invocation's journalling context.

    Holds the adoption id and this run's id, and turns a mutation into the
    three steps §4 of the design requires: a flushed `prepared` record
    carrying the preimage and the expected postimage, the atomic mutation,
    then a flushed `committed` record. A `prepared` with no `committed` is
    what `journal --check` reconciles; nothing here guesses at one.

    `__init__` takes `Lock` itself, around the two reads and `bootstrap`:
    deciding from what was read that no adoption id exists yet and then
    installing one is a read-modify-write, and two runs interleaving there
    mint two ids for one project. `Lock` is re-entrant, so a caller already
    holding it -- `init.run` holds it for the whole run -- neither waits
    here nor has it released early. The mutation methods below do not take
    it: every caller of them today runs inside that run-wide lock.
    """

    def __init__(self, root=Path()):
        self.root = Path(root)
        self.run = new_id()
        with Lock(self.root):
            records = read(self.root, REPO)
            local = read(self.root, LOCAL)
            self.adoption = bootstrap(self.root, self.run, records, local)
        # Every path either journal already carries a record for. `observe`
        # is written on first sight (§2), and first sight is exactly this:
        # a path the record has never mentioned. Keying it on every op
        # rather than on `observe` alone is what stops a path the plugin
        # itself created -- or was interrupted while creating -- from being
        # observed later as a fact about the state adoption found.
        #
        # Both journals are read here, so a vault that cannot be parsed
        # refuses the run rather than being written to blind; `init` keeps
        # the harness symlink working over that failure (`init.run`).
        self._seen = {
            (entry["durability"], entry["path"])
            for entry in records + local
        }

    def _record(self, op, purpose, path, durability, stage, **extra):
        """Build one record. The caller has already asked `authorise`.

        Every public method calls `authorise` itself, once, before it parks
        a preimage, writes bytes or appends anything -- never here, because
        `_record` builds BOTH halves of a mutation, and the second call is
        made after the mutation has already happened (`_write_bytes` for a
        write, the caller's own `mkdir`/`relink` for `prepare_op`'s
        `committed` twin). Asking the resolved question again on that
        second call could refuse after the bytes are already on disk,
        leaving a `prepared` record with no `committed` twin for a mutation
        that did happen -- exactly the state reconciliation exists to
        avoid manufacturing on its own.

        What is left here is the cheap lexical guard, kept as a last line
        of defence: a `repo` record whose path is absolute or climbs out
        with `..` must never reach `append` even if some future caller
        forgets to ask `authorise` first, because `read` refuses exactly
        that record back.
        """
        if durability == REPO and not _is_inside_path(path):
            raise ValueError(
                f"{path} is not a path inside the adopter root; a "
                "repository record may only carry a relative path that "
                "stays below it. Nothing has been recorded."
            )
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
        """Record a pre-adoption fact about `path`, on first sight only.

        `authorise` is asked before the `_seen` check, not after: a path
        this journal has never mentioned but that resolves outside the root
        through a symlink (`memory/` pointing out of the project, found
        already there) must never be filed as a fact about the tree, and
        the `_seen` lookup itself does not know that -- only `authorise`
        does.

        Never inverted, and never written twice: a path this journal already
        mentions is not one adoption found there. A second `observe` would
        be a claim about the state before adoption written after the plugin
        had already changed it, and `observe` has no inverse, so nothing
        would ever take it back.
        """
        location = authorise(self.root, path, durability)
        if (durability, location) in self._seen:
            return
        append(
            [
                self._record(
                    OBSERVE, "init", location, durability, COMMITTED, note=note
                )
            ],
            self.root,
            durability,
        )
        self._seen.add((durability, location))

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
            install(temporary, blob)
        return reference

    def _location(self, path):
        """`path` as a record will carry it: relative to the adopter root.

        `authorise` is asked here, before the preimage is parked and before
        the `prepared` record is appended, so a refusal leaves nothing
        written and nothing recorded -- and its `OSError` is what the caller
        already catches per item (`init._ensure_file`, `init._ensure_ignored`):
        one item gets an ERROR finding and the rest of the run continues,
        which is what a whole-run abort or a traceback would take away.

        The directory check is this method's own, not `authorise`'s: it is
        about what a file WRITE may target, not about which path a record
        may name, and `observe`/`prepare_op`/`append_op` -- which call
        `authorise` themselves, at their own start -- have no bytes to
        write and so no reason to refuse a directory.
        """
        location = authorise(self.root, path, REPO)
        if (self.root / location).is_dir():
            raise IsADirectoryError(
                f"{location} is a directory; refusing to journal a file write "
                "against it. Nothing has been recorded."
            )
        return location

    def _write_bytes(self, location, data):
        """Put `data` at `location`, atomically: temporary, fsync, install.

        The temporary is plugin-owned and pid-named, so it is not itself an
        adopter mutation and is not journalled (§4). A failure removes it
        and raises, leaving the target exactly as it was -- which is what
        makes the open `prepared` record the only trace, and an honest one.
        """
        target = self.root / location
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            install(temporary, target)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    def write(self, path, content, purpose, durability=REPO):
        """Create or replace the text file at `path`, journalling both stages."""
        location = self._location(path)
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
        _fault("after-prepared")

        self._write_bytes(location, data)
        _fault("after-mutation")

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
        self._seen.add((durability, location))

    def append_text(self, path, content, purpose, durability=REPO):
        """Append `content` to the text file at `path`, journalling both stages.

        The inverse of an `append` is "truncate to the recorded prior
        length" (§2), so the record carries `prior_bytes` alongside the
        preimage and postimage digests. `preimage` is null when the file did
        not exist at all, exactly as it is for a `create`: the inverse is
        then removing the file, not truncating it to nothing, and only the
        record can say which of the two this was.

        The append is a read-modify-write into a temporary file and an
        atomic install, not an `open(..., "a")`: a torn append would leave
        bytes no postimage describes, which is the state the two-record
        protocol exists to rule out.
        """
        location = self._location(path)
        target = self.root / location
        preimage = self.park_preimage(location)
        existing = target.read_bytes() if preimage is not None else b""
        data = existing + content.encode("utf-8")
        fields = {
            "preimage": preimage,
            "postimage": digest(data),
            "prior_bytes": len(existing),
        }

        append(
            [
                self._record(
                    APPEND, purpose, location, durability, PREPARED, **fields
                )
            ],
            self.root,
            durability,
        )
        _fault("after-prepared")

        self._write_bytes(location, data)
        _fault("after-mutation")

        append(
            [
                self._record(
                    APPEND, purpose, location, durability, COMMITTED, **fields
                )
            ],
            self.root,
            durability,
        )
        self._seen.add((durability, location))

    def prepare_op(self, op, purpose, path, note, durability=REPO):
        """Record that a mutation with no file preimage is about to happen.

        `authorise` runs first, before this method appends anything: the
        caller has not yet touched the filesystem for this op (that is
        `prepare_op`'s whole point -- record, then mutate), so a refusal
        here is still the clean "nothing written and nothing recorded" case.

        The `committed` half is `append_op`. §4 admits no "mutate first"
        protocol, and a mutation with no bytes to digest is not an
        exception: a `mkdir` that crashes before its record leaves a
        directory nothing knows about, and a re-pointed symlink destroys the
        one fact its own record carries -- the previous target, which is
        what its inverse restores.
        """
        location = authorise(self.root, path, durability)
        append(
            [self._record(op, purpose, location, durability, PREPARED, note=note)],
            self.root,
            durability,
        )
        _fault("after-prepared")
        self._seen.add((durability, location))

    def append_op(self, op, purpose, path, note, durability=REPO):
        """Close a mutation with no file preimage: the `committed` half.

        `authorise` runs again here, before THIS method's own append -- not
        before the caller's `mkdir`/`relink`, which already happened between
        `prepare_op` and this call. That ordering is fine: `append_op`
        performs no filesystem mutation of its own, so a refusal here
        leaves the `prepared` record open with no `committed` twin, which is
        exactly the shape `journal --check` reconciles, not a new failure
        mode. What `authorise` must never do is run a second time INSIDE a
        method that mutates bytes between its own two record-writing calls
        (see `_record`) -- `append_op` does not, so it is safe here.

        `_fault("after-mutation")` fires first, before `authorise`: the
        caller's own mutation (`mkdir`, `relink`) already happened between
        `prepare_op` and this call, so by the time `append_op` runs at all
        the mutation is done and the only thing left is the `committed`
        record this method is about to write.
        """
        _fault("after-mutation")
        location = authorise(self.root, path, durability)
        append(
            [self._record(op, purpose, location, durability, COMMITTED, note=note)],
            self.root,
            durability,
        )
        self._seen.add((durability, location))


UNAPPLIED = "unapplied"
APPLIED = "applied"
DIVERGED = "diverged"
UNKNOWN = "unknown"


def reconcile(root=Path()):
    """Every unfinished transaction, paired with the state its path is in.

    Records are paired in file order, not by set membership: one run may
    write the same path more than once, and a `committed` record closes the
    ONE `prepared` record it follows, never every prepared record that
    happens to share its (run, path).

    `unapplied` -- the bytes still match the preimage, so the mutation never
    happened. `applied` -- they match the postimage, so it happened and only
    the closing record was lost. `diverged` -- neither, so something else
    wrote the path afterwards. `unknown` -- the bytes could not be read at
    all, or must not be, so nothing can be said.

    Every unfinished transaction is reported, including the ones this
    reader refuses to follow: a record it may not read is a fact about that
    record, not the end of the pass.

    This reports. It does not repair: choosing for the user between the
    states the record cannot distinguish is exactly the guessing this
    component exists to remove.
    """
    root = Path(root)
    unfinished = []
    for durability in DURABILITIES:
        # Records are paired in file order, not by set membership: one run
        # may write the same path more than once, and a `committed` record
        # closes the ONE `prepared` record it follows, never every prepared
        # record that happens to share its (run, path).
        open_by_key = {}
        for entry in read(root, durability):
            key = (entry["run"], entry["path"])
            if entry["stage"] == PREPARED:
                open_by_key.setdefault(key, []).append(entry)
            elif entry["stage"] == COMMITTED and open_by_key.get(key):
                open_by_key[key].pop(0)
        for entries in open_by_key.values():
            for entry in entries:
                unfinished.append((entry, _state_of(root, entry)))
    return unfinished


def _state_of(root, entry):
    target = root / entry["path"]
    if not _resolves_below(root, target):
        # `read()` already refused a repository record naming a path outside
        # the root, and `Run.write` refuses to write one. What is left is the
        # filesystem's half of the same question: a lexically fine path that
        # resolves out of the root through a symlink, and a vault record,
        # whose path may legitimately leave the root -- design §7 is explicit
        # that such a path "can never be authorised by the file itself" and
        # that acting on it needs a fresh CLI argument naming it. Reading the
        # bytes is acting on it.
        #
        # So it is not read -- and that is precisely what `unknown` says.
        # Raising here ended the whole pass instead, hiding every other
        # unfinished transaction in the project behind one line, and the
        # `local` record of the harness symlink, whose path is absolute BY
        # DESIGN, reached it on any ordinary crash between its two records.
        return UNKNOWN
    if "postimage" not in entry:
        # A mutation with no bytes to digest -- a directory, a symlink.
        # `create` (a `mkdir`, from `_ensure_dir`) is checked against
        # `directory`, not mere existence: a broken symlink resolves to
        # nothing and used to read as `applied` under `exists() or
        # is_symlink()`, since `is_symlink()` is true whether or not the
        # link resolves -- exactly the false `applied` design §6 names.
        # Every other no-postimage op (`link`, from `_record_symlink`) has
        # no state word richer than existence yet: the record's own subject
        # IS the symlink, so a symlink being there, resolvable or not, is
        # what its `link` record describes as applied.
        if entry["op"] == CREATE:
            return (
                APPLIED
                if current_state(root, entry["path"])["kind"] == DIRECTORY
                else UNAPPLIED
            )
        return APPLIED if target.exists() or target.is_symlink() else UNAPPLIED
    try:
        actual = digest(target.read_bytes())
    except FileNotFoundError:
        # Genuinely absent. Against a `create` record, whose preimage is
        # null, that is an honest `unapplied`.
        actual = None
    except OSError:
        # A directory, a permission denial, an I/O error: the bytes could
        # not be read, so nothing is known about this path. Saying
        # `unapplied` here would assert the mutation never happened.
        return UNKNOWN
    if actual == entry.get("postimage"):
        return APPLIED
    if actual == entry.get("preimage"):
        return UNAPPLIED
    return DIVERGED


def _resolves_below(root, target):
    """Whether `target` is still inside `root` once every symlink is followed."""
    try:
        return target.resolve().is_relative_to(Path(root).resolve())
    except OSError:
        return False


def run(check, stdout, stderr):
    """The `journal` subcommand: report the record, and optionally reconcile.

    Read-only in both modes. Without `--check` it summarises and exits 0
    whatever it finds, so a reader can look at a project without gating on
    it; with `--check` an unfinished transaction -- from the two journals'
    own `prepared`/`committed` pairing (`reconcile`), or a transaction file
    still on disk (`_open_transactions`) -- is an ERROR, because a caller
    that asked to be told cannot be told by an exit code of 0.

    A transaction file is reported even without `--check`, but only as a
    count: no caller opens one yet (Task 4's executor is what will), so
    today it can only be a hand-written fixture or a genuinely interrupted
    run, and either way a reader who did not ask to gate on it should still
    be told something is open, on a second line, only when there is
    something to say.
    """
    from .findings import ERROR, EXIT_ERROR, EXIT_OK, Finding

    root = Path()
    # Accumulated one artifact at a time so the summary below can say how
    # many records were actually read when a later one is refused. Printing
    # a hardcoded 0 there described a project with no history at all, which
    # is a different and much worse fault than the one that happened.
    records = []
    try:
        for durability in DURABILITIES:
            records.extend(read(root, durability))
        # `reconcile` reads both journals again, so it belongs inside this
        # handler: a journal a concurrent writer left unreadable between the
        # two reads must be reported the same way as anything else the
        # reader cannot accept.
        unfinished = reconcile(root) if check else []
        # `_open_transactions` never raises -- an unreadable transaction file
        # is one of its own results, not a `JournalError` -- so it does not
        # need this `try`, but reading the log alongside the two journals in
        # one pass is what lets the summary below count everything actually
        # read even when one of them is later refused.
        transactions = _open_transactions(root)
    except JournalError as error:
        where = error.artifact or JOURNAL_FILENAME
        location = where if error.lineno is None else f"{where}:{error.lineno}"
        print(Finding(ERROR, location, "journal", error.message).render(), file=stderr)
        print(f"journal: {len(records)} record(s), 1 error(s)", file=stdout)
        return EXIT_ERROR

    if not check:
        print(f"journal: {len(records)} record(s)", file=stdout)
        if transactions:
            print(
                f"journal: {len(transactions)} unresolved transaction(s)",
                file=stdout,
            )
        return EXIT_OK

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
    for item in transactions:
        artifact = f"{VAULT_DIRNAME}/{TRANSACTIONS_DIRNAME}/{item['id']}.json"
        if "damaged" in item:
            location = artifact
            message = f"open transaction {item['id']} is damaged: {item['damaged']}"
        else:
            location = item.get("intention", {}).get("path", artifact)
            stage = item.get("stage", "?")
            message = f"open transaction {item['id']} ({stage}) on {location}"
        print(Finding(ERROR, location, "journal", message).render(), file=stderr)

    total_errors = len(unfinished) + len(transactions)
    print(
        f"journal: {len(records)} record(s), {total_errors} error(s)",
        file=stdout,
    )
    return EXIT_ERROR if total_errors else EXIT_OK
