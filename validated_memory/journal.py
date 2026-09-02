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
from dataclasses import dataclass, replace as _replace
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
KINDS = (ABSENT, DIRECTORY, FILE, SYMLINK)

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
    # Written by the executor on both halves of one mutation: the
    # transaction that carried it (so the two records can be recognised as
    # one act long after the transaction file is gone) and the mode the
    # path ended up with (so a reversal can put it back -- design §7).
    "transaction": (str,),
    "mode": (int,),
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


def _blob_matches(path, reference):
    """Whether the bytes at `path` digest to `reference`, without raising.

    A preimage blob is named after its own digest, so this is the one
    question that can be asked of it. Bytes that cannot be read at all
    answer it the same way bytes that disagree do: this blob is not the
    preimage it claims to be, and the caller replaces it rather than
    trusting it.
    """
    try:
        return digest(path.read_bytes()) == reference
    except OSError:
        return False


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


# The seams of the executor's protocol: a transaction file fsynced with
# nothing published yet, the new bytes published but the transaction not yet
# marked, the transaction marked published but the permanent history not yet
# appended, and the history appended but the transaction not yet resolved.
# `after-prepared` and `after-mutation` are not executor points -- they are
# the two-record protocol `prepare_op`/`append_op` still run for the harness
# symlink alone, and they go with those two methods in task 6.
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

    `__post_init__` refuses seven combinations, each a way the payload could
    silently disagree with `op`: a `LINK` carrying `content`, a directory
    `CREATE` carrying `content`, a file `CREATE`/`REPLACE`/`APPEND` carrying
    no `content`, a `LINK` carrying no `target`, an `OBSERVE` carrying any
    payload (`content`, `target` or `directory=True`), a `CREATE` expecting
    anything but `{"kind": ABSENT}`, and an unknown `op` or `durability`.
    Every refusal is `ValueError`: nothing has been touched yet to reach it.

    The `CREATE` rule is what makes "a creation is never a no-op" true by
    construction rather than by inspection of each caller. A create over
    something already there is not a creation -- it is a replacement, and it
    has to say so, because the record is what a reversal reads and the
    inverse of a create is removal.
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
        if self.op == CREATE and self.expected != {"kind": ABSENT}:
            raise ValueError(
                "a create intention must expect the path to be absent; "
                f"this one expects {self.expected}"
            )


OUTCOME_APPLIED = "applied"
OUTCOME_NOOP = "noop"
OUTCOME_REFUSED = "refused"
OUTCOME_STATUSES = (OUTCOME_APPLIED, OUTCOME_NOOP, OUTCOME_REFUSED)


@dataclass(frozen=True)
class Outcome:
    """What `Run.execute` did with one intention, and what it did not.

    A refusal is a RESULT here, not an exception. Design §5: a precondition
    that fails before anything is prepared "writes nothing anywhere -- it is
    a result the caller renders, not a transaction", and `init` renders one
    ERROR per item and carries on. `execute` raises only for what it cannot
    express in this shape: a journal that cannot be written at all.

    - `status` -- `applied` (the mutation happened and both records are in
      the history), `noop` (the path is already in the state the intention
      asks for, so nothing was written and nothing was recorded) or
      `refused`.
    - `op`, `path`, `durability` -- the intention's, with `path` spelled the
      way `authorise` normalised it and a record will carry it.
    - `transaction` -- the id of the transaction that carried the mutation,
      on an `applied` outcome. None otherwise: a noop and a refusal open no
      transaction, or close the one they opened before returning.
    - `message` -- None unless refused, when it is the sentence the caller
      renders. It always ends by saying what was left untouched, because a
      refusal a user cannot act on is a stopped session.
    - `mode` -- the target's mode after publication for an `applied`
      outcome, the mode it has now for a `noop` or a refusal that found a
      node there, and None where there is none to report.
    """

    status: str
    op: str
    path: str
    durability: str
    transaction: str | None = None
    message: str | None = None
    mode: int | None = None


def _describe(state):
    """A state in the words a refusal uses, not in the words a record uses.

    A digest and a mode are what the transaction file carries; a person
    reading an ERROR needs to know what is at the path, so this says the
    kind and, for a symlink, where it points -- the one field whose value
    changes what the reader should do next.
    """
    kind = state.get("kind")
    if kind == ABSENT:
        return "absent"
    if kind == DIRECTORY:
        return "a directory"
    if kind == SYMLINK:
        return f"a symlink to '{state.get('target')}'"
    return "a file"


def _postimage_state(intention, actual, data):
    """The state `intention` will leave at its path, in `current_state`'s words.

    Computed here rather than in `_open_transaction`, for the reason that
    function's docstring gives: an `append`'s digest needs the bytes already
    on disk, and only the executor has read them. `data` is the full new
    bytes publication will write, or None for a mutation that has none.

    `mode` is carried only where publication preserves one. A replacement
    keeps the target's mode (design §7), so the postimage can name it and
    recovery can check it; a creation's mode is the umask's answer and is
    not known until the node exists, so the field is omitted and `satisfies`
    then matches whatever mode it turns out to have.
    """
    if intention.op == LINK:
        return {"kind": SYMLINK, "target": intention.target}
    if intention.directory:
        return {"kind": DIRECTORY}
    state = {"kind": FILE, "digest": digest(data)}
    if actual.get("kind") == FILE:
        state["mode"] = actual["mode"]
    return state


def _write_denied(root, location, actual):
    """Why this user may not write over `location`, or None when it may.

    The read-only bit is how an adopter says do not write here, and nothing
    in the install path consulted it: `os.replace` needs write permission on
    the DIRECTORY, not on the file, so a `.gitignore` at mode 0444 was
    replaced in silence and handed back at 0644 (design §1, measured).

    The question is asked of the file's own mode bits and the POSIX class
    this process falls in -- owner, else group, else other -- and of nothing
    else. `os.access` is not used: it answers for the REAL uid and its own
    documentation warns against using it to decide whether an operation will
    succeed. There is no exception for root: a process that can write
    anywhere still gets the refusal, because the bit is a statement of
    intent by the adopter and this is the one place that reads it.

    Only a regular file is asked about. An absent path has no mode to deny
    with, a symlink's mode means nothing on the platforms this runs on, and
    a directory is never published over.
    """
    if actual.get("kind") != FILE:
        return None
    try:
        info = os.lstat(Path(root) / location)
    except OSError:
        # The node went away between the check and here; the publication
        # below will fail or the re-read will refuse, both with a better
        # message than a guess made from nothing.
        return None
    if info.st_uid == os.geteuid():
        bit = stat.S_IWUSR
    elif info.st_gid in {os.getegid(), *os.getgroups()}:
        bit = stat.S_IWGRP
    else:
        bit = stat.S_IWOTH
    if stat.S_IMODE(info.st_mode) & bit:
        return None
    return (
        f"{location} is mode {stat.S_IMODE(info.st_mode):04o}, which denies "
        "writing to this user. Nothing has been written."
    )


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

    Deliberately NOT called from `_record`: that helper builds both halves
    of a mutation, and for the two-record protocol `prepare_op`/`append_op`
    still run, the second call happens after the caller's own mutation has
    already run. Asking the resolved question there again could refuse
    after the bytes were already on disk, abandoning a `prepared` record
    that reconciliation was built to close rather than a fresh failure to
    raise past -- see `_record`.

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
    `Run._publish`): the bytes are flushed and fsynced before the rename,
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
    prior_bytes=None,
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
    | `prior_bytes`    | an `append`'s prior length, or `None` for every other op        |
    | `stage`          | `"prepared"`, `"published"` or `"aborted"`                     |
    | `reason`         | present only once `stage` is `"aborted"`                       |

    `prior_bytes` is here for recovery alone. The inverse of an `append` is
    "truncate to the recorded prior length" (§2), so the `committed` record
    carries it -- and recovery, which rebuilds that record from this file
    and the current state, has nowhere else to read it from: the bytes it
    describes have already been appended to by the time recovery runs.

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
        "prior_bytes": prior_bytes,
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


# --- recovery: what a run does with what an earlier run left open -------------
#
# A crash leaves a transaction file, and design §3 makes the residue
# decidable rather than inferable: the file records a FACT -- what stage the
# mutation reached -- so the next run reads it instead of guessing from a
# filesystem some later process may have changed.

# What recovery did with one unresolved transaction.
RECOVERED = "completed"
DISCARDED = "discarded"
REMOVED = "aborted-removed"
RECOVERY_ACTIONS = (RECOVERED, DISCARDED, REMOVED)

# ... or why it could do nothing with it. `reconcile`'s four words below
# answer a different question -- what state one PATH is in, for a record
# pair the two journals never closed -- and are deliberately not the same
# names. Two of the strings coincide because a reader meets the same word
# for the same shape of trouble.
PROBLEM_DIVERGED = "diverged"
PROBLEM_UNKNOWN = "unknown"
PROBLEM_DAMAGED = "damaged"
RECOVERY_PROBLEMS = (PROBLEM_DIVERGED, PROBLEM_UNKNOWN, PROBLEM_DAMAGED)

# What `_classify` says when recovery would resolve the transaction on its
# own, and what `journal --check` calls all three: it reports, so the three
# ways of resolving one are one answer to the only question it asks --
# would a run clear this away by itself?
_COMPLETE = "complete"
_DISCARD = "discard"
_REMOVE = "remove"
RECOVERABLE = "recoverable"


@dataclass(frozen=True)
class Recovery:
    """What recovery did with one unresolved transaction, or why it could not.

    Exactly one of `action` and `problem` is set, and `__post_init__`
    refuses anything else: "recovered it" and "could not touch it" are the
    whole of what this can report, and a caller rendering both or neither
    would be rendering a state recovery cannot be in.

    - `transaction` -- the id, which is also the file's name stem.
    - `path`, `durability` -- the intention's, or None for a transaction so
      damaged that it names neither.
    - `action` -- `completed` (the mutation happened; the history now holds
      its two records), `discarded` (it never published; nothing was
      recorded) or `aborted-removed` (it was already closed `aborted`, and
      its file is gone).
    - `problem` -- `diverged`, `unknown` or `damaged`. The transaction file
      is LEFT where it is in all three: recovery closes only what it can
      account for, and `journal --resolve` is the way out.
    - `message` -- the sentence a caller renders. For a problem it names the
      transaction and the three flags, because a finding a user cannot act
      on is a stopped session.
    """

    transaction: str
    path: str | None
    durability: str | None
    action: str | None = None
    problem: str | None = None
    message: str = ""

    def __post_init__(self):
        if (self.action is None) == (self.problem is None):
            raise ValueError(
                "a recovery reports exactly one of an action and a problem"
            )
        if self.action is not None and self.action not in RECOVERY_ACTIONS:
            raise ValueError(f"unknown recovery action '{self.action}'")
        if self.problem is not None and self.problem not in RECOVERY_PROBLEMS:
            raise ValueError(f"unknown recovery problem '{self.problem}'")


def _classify(root, item):
    """What recovery would do with one unresolved transaction, doing none of it.

    Returns `(verdict, facts)`. The verdict is `_COMPLETE`, `_DISCARD`,
    `_REMOVE` or one of the three `RECOVERY_PROBLEMS`; `facts` carries what
    the file and the filesystem said, so the caller neither re-reads nor
    re-decides. This function writes nothing and is the ONE place the table
    in the step's brief is expressed -- `Run.recover` acts on it and
    `journal --check` reports it, and a reader who has to compare two copies
    of a decision table is a reader who will find them disagreeing.

    The rules, in order:

    - A file that could not be read at all (`_open_transactions` said so),
      or that is readable but says nothing recovery can use -- no intention,
      no operation, no path, no pair of states in `current_state`'s
      vocabulary -- is `damaged`. Nothing is inferred from half a file.
    - `aborted` is closed already: `_REMOVE`, and the file goes.
    - `published` means publication completed and the history had not been
      appended when the process died. The path matching the postimage is
      the mutation: `_COMPLETE`. Anything else means something wrote the
      path afterwards: `diverged`.
    - `prepared` means the write-ahead entry was fsynced and nothing more is
      known from the file. The path matching the preimage says the mutation
      never happened: `_DISCARD`. Matching the postimage says it did, and
      only the marker was lost: `_COMPLETE`. Neither, or BOTH -- which the
      executor's no-op rule makes unreachable for a transaction it opened,
      but not for a hand-written one -- is `unknown`.

    The path is checked lexically for a repository transaction and not
    resolved: `read` refuses a repository record whose path is absolute or
    climbs out with `..`, so a record recovery is about to append has to
    pass that test, but a path that resolves out of the root through a
    symlink is not a reason to refuse to record a mutation that already
    happened.
    """
    transaction_id = item["id"]
    facts = {
        "path": None,
        "durability": None,
        "stage": item.get("stage"),
        "reason": None,
    }

    def damaged(reason):
        facts["reason"] = reason
        return PROBLEM_DAMAGED, facts

    if "damaged" in item:
        return damaged(item["damaged"])

    intention = item.get("intention")
    if not isinstance(intention, dict):
        return damaged("it carries no intention")
    op = intention.get("op")
    purpose = intention.get("purpose")
    path = intention.get("path")
    durability = intention.get("durability")
    note = intention.get("note")
    if op not in OPS:
        return damaged("its intention names no operation this plugin knows")
    if not isinstance(purpose, str) or not isinstance(path, str):
        return damaged("its intention names no path and purpose")
    if durability not in DURABILITIES:
        return damaged(f"its intention claims durability '{durability}'")
    if note is not None and not isinstance(note, str):
        return damaged("its intention's note is not text")
    if durability == REPO and not _is_inside_path(path):
        return damaged(
            f"its intention names '{path}', which is not a path inside the "
            "adopter root"
        )
    facts["path"] = Path(path).as_posix() if durability == REPO else path
    facts["durability"] = durability
    facts["intention"] = intention

    for field in ("mode", "prior_bytes"):
        value = item.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            return damaged(f"its {field} is not a number")
    blob = item.get("preimage_blob")
    if blob is not None and not isinstance(blob, str):
        return damaged("its preimage reference is not a digest")

    stage = item.get("stage")
    if stage == ABORTED:
        return _REMOVE, facts
    if stage not in (PREPARED, PUBLISHED):
        return damaged(
            f"its stage is '{stage}', and a transaction is one of "
            f"{', '.join(TRANSACTION_STAGES)}"
        )

    preimage = item.get("preimage")
    postimage = item.get("postimage")
    if not isinstance(preimage, dict) or not isinstance(postimage, dict):
        return damaged("it records no preimage and postimage states")
    if preimage.get("kind") not in KINDS or postimage.get("kind") not in KINDS:
        return damaged("its preimage or postimage is in no state this plugin knows")
    facts["preimage"] = preimage
    facts["postimage"] = postimage

    actual = current_state(root, facts["path"])
    facts["actual"] = actual
    matches_post = satisfies(actual, postimage)
    if stage == PUBLISHED:
        return (_COMPLETE if matches_post else PROBLEM_DIVERGED), facts
    if matches_post and satisfies(actual, preimage):
        # The two states this transaction names cannot be told apart on
        # disk, so nothing here can say whether the mutation ran. The
        # executor never opens such a transaction -- step 4 of `execute`
        # returns `noop` for exactly this -- so it can only be hand-written.
        return PROBLEM_UNKNOWN, facts
    if matches_post:
        return _COMPLETE, facts
    if satisfies(actual, preimage):
        return _DISCARD, facts
    return PROBLEM_UNKNOWN, facts


def _resolution_advice(transaction_id):
    """How an operator closes a transaction recovery would not touch.

    Every problem message ends with this: a path that gates and a
    transaction nothing will ever clear is a project stuck at the session
    hook, and the three flags are the whole of the way out.
    """
    return (
        f"run 'validated-memory journal --resolve {transaction_id}' with "
        "one of --accept, --restore or --abandon"
    )


# The operator's three ways out of a transaction recovery will not touch.
# They are flags on `journal`, not a subcommand of their own: the pinned
# subcommand set moves once, with the public write interface (design §13).
ACCEPT = "accept"
RESTORE = "restore"
ABANDON = "abandon"
RESOLUTIONS = (ACCEPT, RESTORE, ABANDON)


@dataclass(frozen=True)
class Resolution:
    """What `journal --resolve` did with one transaction, or why it would not.

    `message` is None when the transaction was closed, and the refusal
    otherwise -- the same shape `Outcome` uses, and for the same reason: a
    refusal here is a result the caller renders, never an exception, and it
    always ends by saying what was left untouched.

    `location` is what a `Finding` should name -- the path when the
    transaction names one, the transaction file itself when it does not.
    """

    transaction: str
    resolution: str
    location: str
    message: str | None = None


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

    Holds the adoption id, this run's id and the paths either journal
    already knows about, and performs mutations through `execute`, which is
    the whole of design §4's protocol and the only thing a caller needs.
    One `Run` per invocation.

    Two methods still run the older two-record protocol -- `prepare_op` and
    `append_op`, for the harness symlink alone, until task 6 moves it. A
    `prepared` record of theirs with no `committed` twin is what
    `journal --check` reconciles; nothing here guesses at one.

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
            self._survey(records, local)

    def _survey(self, records, local):
        """Take stock of what the histories and the open transactions say.

        Sets two things, and is called again by `recover` once it has
        finished, because recovery moves paths between them: a completed
        transaction puts its path in the history, a discarded one leaves the
        path as adoption found it, and a transaction recovery could not
        touch keeps gating.

        `_seen` -- every path either journal already carries a record for.
        `observe` is written on first sight (§2), and first sight is exactly
        this: a path the record has never mentioned. Keying it on every op
        rather than on `observe` alone is what stops a path the plugin
        itself created -- or was interrupted while creating -- from being
        observed later as a fact about the state adoption found.

        Both journals are read by the caller, so a vault that cannot be
        parsed refuses the run rather than being written to blind; `init`
        keeps the harness symlink working over that failure (`init.run`).

        `_seen` also holds every path an UNRESOLVED transaction names, which
        the two histories cannot know about. The executor appends its
        records after publication, so a run killed in between leaves a path
        the plugin created on disk with nothing in either journal naming it.
        Reading only the histories would then observe it as a fact about the
        state adoption found -- the permanent, uninvertible lie commit
        `4ce59a9` removed. Recovery normally puts the records back first,
        but a transaction it cannot resolve stays open, and this is what
        makes that state safe to run over.

        `_open_paths` -- the transaction id still open on each of those
        paths, which is what `_execute` refuses to write over. Only the
        affected path gates: the rest of the run proceeds, which is
        narrower than design §8's "no mutating command proceeds", because a
        single-path transaction can be reasoned about piecewise and
        blocking everything would brick the session hook over one stale
        file.

        A damaged transaction file carries no intention and so names no
        path; it is skipped here and reported by `journal --check`.
        """
        self._seen = {
            (entry["durability"], entry["path"])
            for entry in records + local
        }
        self._open_paths = {}
        for item in _open_transactions(self.root):
            intention = item.get("intention")
            if not isinstance(intention, dict):
                continue
            path = intention.get("path")
            durability = intention.get("durability")
            if isinstance(path, str) and isinstance(durability, str):
                self._seen.add((durability, path))
                self._open_paths.setdefault((durability, path), item["id"])

    def _record(self, op, purpose, path, durability, stage, run=None, **extra):
        """Build one record. The caller has already asked `authorise`.

        `run` is this invocation's unless the caller names another, which
        exactly one caller does: recovery, rebuilding the two records of a
        mutation an EARLIER run performed. Filing those under the run that
        recovered them would say a run wrote bytes it never wrote.

        Every public method calls `authorise` itself, once, before it parks
        a preimage, writes bytes or appends anything -- never here, because
        `_record` builds BOTH halves of a mutation. `execute` appends the
        two together, after publication, so a second call there would refuse
        a mutation that has already happened; and `prepare_op`/`append_op`
        are two calls with the caller's own `mkdir`/`relink` between them,
        so a refusal on the second would leave a `prepared` record with no
        `committed` twin for a mutation that did happen -- exactly the state
        reconciliation exists to avoid manufacturing on its own.

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
            run=self.run if run is None else run,
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

        The blob is VERIFIED, and verified BEFORE it is installed: the
        temporary is read back and its digest compared with the name it is
        about to be filed under, and a mismatch removes the temporary and
        raises. A preimage is the only copy of bytes this plugin is about to
        overwrite, and nothing else ever checks it -- a reversal years later
        would restore whatever is in the file the record names -- so bad
        bytes must never reach the name a later reader trusts. Verifying
        after the install would leave them there, and the dedup below would
        then skip re-parking for ever: one bad write would refuse the same
        mutation on every run until someone deleted the file by hand.

        A blob ALREADY there whose bytes do not digest to its own name is
        removed and re-parked, once. It is worthless to every reader -- the
        name is the digest, so bytes that disagree with it can only be a
        corrupt earlier park or a hand edit -- and the bytes to replace it
        with are in hand right now. Refusing instead would wedge the
        adoption on a file nothing else will ever repair.

        The check is read-back, not a proof about the platter: a filesystem
        that lies about what it stored will lie to this read too. What it
        does catch is the reachable half -- a short or torn write, and a
        blob left corrupt by an earlier run or an edit.
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
        if blob.exists() and not _blob_matches(blob, reference):
            blob.unlink(missing_ok=True)
        if not blob.exists():
            blob.parent.mkdir(parents=True, exist_ok=True)
            temporary = blob.with_name(f"{blob.name}.{os.getpid()}.tmp")
            try:
                # The blob is named after its own digest, so a torn write
                # would leave bytes that silently disagree with the name
                # every later reader trusts. Every other atomic write in
                # this module fsyncs before the rename; this one has to as
                # well, and then prove what it wrote before publishing it.
                with temporary.open("wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                if not _blob_matches(temporary, reference):
                    raise OSError(
                        f"the preimage of {path} written to "
                        f"{temporary.as_posix()} does not digest to "
                        f"{reference}, the name it would be filed under; the "
                        "vault's copy of the bytes about to be overwritten "
                        "cannot be trusted"
                    )
                install(temporary, blob)
            except OSError:
                # Nothing else ever removes it: the name carries this
                # process's pid, so no later run would recognise it as
                # abandoned, and it would sit in the vault for ever.
                temporary.unlink(missing_ok=True)
                raise
        return reference

    # --- the executor: one intention, one path, one outcome -------------------

    def execute(self, intention):
        """Perform one `Intention`, wholly, and return an `Outcome`.

        This is the mutating surface design §4 asks for: the executor owns
        the lock, path authorisation, the expected-state check, the
        preimage, the transaction file, the publication and its durability
        barriers, the mode, and both history records. No caller may do any
        of it for itself, because every caller that did got one of the steps
        wrong -- the six defects §1 measured are six spellings of the same
        protocol, reimplemented per call site.

        The order, and why each step is where it is:

        1. **Take the lock.** Re-entrant within the process, so a caller
           already holding it (`init.run` holds one for a whole run) neither
           waits here nor has it released early. Everything below happens
           inside it, including the re-read at step 6: a check taken under a
           lock the publication does not also hold checks nothing.
        2. **Authorise the path** -- once, before anything is read or
           parked. A refusal here is a refusal with nothing written, so it
           comes back as an `Outcome`, carrying `authorise`'s own message.
        3. **Compare the current state with the expected one.** A mismatch
           writes NOTHING ANYWHERE: there is no transaction to abort yet,
           and design §5 is explicit that a precondition failing before
           anything is prepared is a result the caller renders, never a line
           in a versioned file. `init` runs at every session start, so a
           refusal recorded in the history would be recorded for ever, once
           per session.
        4. **Return `noop` if the path is already in the state the intention
           would produce.** Not an optimisation: a transaction whose
           preimage and postimage are the same state cannot be recovered --
           nothing on the filesystem tells recovery whether it ran -- and a
           mutation that changes nothing is not a mutation to record. It is
           also not an `observe`: `observe` means "adoption found this",
           which is a different claim entirely (§4).
        5. **Refuse a target whose mode denies writing** (`_write_denied`),
           before the preimage, so this refusal too leaves nothing behind.
        6. **Park and verify the preimage**, fsynced, and open the
           transaction file, fsynced. From here on there is a write-ahead
           record on disk saying what this run intended.
        7. **Re-read the state, under the same lock, immediately before
           publishing.** The window between step 3 and here is real -- a
           preimage is copied and fsynced in it -- and a third party writing
           there would otherwise be overwritten by a record claiming a
           preimage that was already gone. This refusal has a transaction to
           close, so it closes it `aborted` with the reason and removes it.
        8. **Publish** (`_publish`), then mark the transaction `published`.
           That marker is what makes recovery possible for the mutations
           whose two states are indistinguishable on disk.
        9. **Append both history records together**, carrying the
           transaction id and the mode. Together, because the history holds
           consummated facts only (§3): the write-ahead half of this
           protocol is the transaction file, not a `prepared` line in a
           versioned journal that no later run can ever close.
        10. **Resolve the transaction** -- the file leaves the disk -- and
            remember the path, so it can never be observed as pre-existing.

        The four `_fault` points are the seams between those steps, so a
        test can kill the process at each and assert what is left.
        """
        with Lock(self.root):
            return self._execute(intention)

    def _execute(self, intention):
        """`execute`'s body, with the lock already held."""
        if intention.op == OBSERVE:
            # An observation publishes nothing, opens no transaction and
            # has no postimage, so every step below would be a no-op with a
            # record at the end of it. It is `observe`'s, and the two must
            # not be confused: §4 turns exactly that confusion into a
            # permanent lie about the pre-adoption state.
            raise ValueError(
                "an observation is not a mutation and does not go through "
                "the executor; use Run.observe"
            )
        try:
            location = authorise(self.root, intention.path, intention.durability)
        except (ValueError, OSError) as error:
            return Outcome(
                OUTCOME_REFUSED,
                intention.op,
                intention.path,
                intention.durability,
                message=str(error),
            )
        intention = _replace(intention, path=location)
        actual = current_state(self.root, location)

        # A path an earlier run left an unresolved transaction on is a path
        # nothing knows the truth about: recovery either could not tell
        # whether the mutation ran, or found the path changed since. Writing
        # over it would destroy the evidence the operator needs to decide,
        # and the record it wrote would name a preimage that was already
        # gone. Only THIS path gates; the rest of the run proceeds.
        held = self._open_paths.get((intention.durability, location))
        if held is not None:
            return self._refused(
                intention,
                actual,
                f"{location} has an unresolved transaction {held} that "
                "recovery could neither complete nor discard; nothing may "
                f"write to it until it is closed -- "
                f"{_resolution_advice(held)}. Nothing has been written.",
            )

        if not satisfies(actual, intention.expected):
            return self._refused(
                intention,
                actual,
                f"{location} is {_describe(actual)}, and this "
                f"{intention.op} expects it to be "
                f"{_describe(intention.expected)}. Nothing has been written.",
            )

        # A byte-publishing op may only ever land on nothing or on a
        # regular file. `write` and `append_text` are public shims that
        # build their own expectation from whatever they find, so without
        # this an adopter's symlink would be handed to the replacement
        # branch: `os.replace` destroys the link itself, no preimage is
        # parked for it (`park_preimage` sees a symlink, not a file), and
        # the record would say `replace` with a null preimage -- the exact
        # trade `init.BROKEN_SYMLINK` refuses everywhere else, made
        # silently. The expected-state check does not cover it, because a
        # shim's expectation IS the symlink it just read.
        if intention.content is not None and actual["kind"] not in (ABSENT, FILE):
            return self._refused(
                intention,
                actual,
                f"{location} is {_describe(actual)}; refusing to replace it. "
                "Nothing has been written.",
            )

        try:
            data, prior_bytes = self._payload(intention, location, actual)
        except OSError as error:
            return self._refused(
                intention,
                actual,
                f"{location} could not be read, so the mutation was not "
                f"attempted: {error}. Nothing has been written.",
            )
        if data is None and intention.content is not None:
            return self._refused(
                intention,
                actual,
                f"{location} is not a regular file, so nothing here can read "
                "its bytes or write over it. Nothing has been written.",
            )

        postimage = _postimage_state(intention, actual, data)
        if satisfies(actual, postimage):
            return Outcome(
                OUTCOME_NOOP,
                intention.op,
                location,
                intention.durability,
                mode=actual.get("mode"),
            )

        denied = _write_denied(self.root, location, actual)
        if denied is not None:
            return self._refused(intention, actual, denied)

        blob = None
        if actual["kind"] == FILE:
            try:
                blob = self.park_preimage(location)
            except OSError as error:
                return self._refused(
                    intention,
                    actual,
                    f"the preimage of {location} could not be parked, so "
                    f"the mutation was not attempted: {error}. Nothing has "
                    "been written.",
                )

        # Only a regular file's mode is a mode this protocol carries: it is
        # what the replacement copies onto its temporary and what a reversal
        # would restore. A symlink's `lstat` mode is 0777 on every platform
        # this runs on and means nothing, and recording it would invite a
        # reversal to restore a number nobody chose.
        preimage_mode = actual["mode"] if actual["kind"] == FILE else None

        transaction = _open_transaction(
            self.root,
            intention,
            actual,
            postimage,
            preimage_blob=blob,
            mode=preimage_mode,
            prior_bytes=prior_bytes,
            adoption=self.adoption,
            run=self.run,
        )
        _fault("after-transaction")

        # The state as it is NOW, not as it was before the preimage was
        # parked and the transaction fsynced. Compared whole rather than
        # through `satisfies`: the transaction file has already committed to
        # `actual` as this mutation's preimage and `_publish` is about to
        # carry its mode over, so anything but `actual` invalidates both.
        again = current_state(self.root, location)
        if again != actual:
            return self._aborted(
                transaction,
                intention,
                again,
                f"{location} changed while its mutation was being prepared: "
                f"it is {_describe(again)} now and was {_describe(actual)} "
                "when this run checked. Nothing has been written.",
            )

        try:
            mode = self._publish(intention, location, actual, data)
        except OSError as error:
            return self._aborted(
                transaction,
                intention,
                again,
                f"{location} could not be written: {error}. Nothing has "
                "been published.",
            )
        _fault("after-publish")
        _mark_published(self.root, transaction)
        _fault("after-published")

        # Both records carry the transaction that produced them, because the
        # transaction FILE is local and leaves the disk on the next line:
        # the id in the history is the only thing that survives to say these
        # two lines are one act. Everything else each op already carried
        # stays exactly as it was, so every reader written against the
        # earlier shape still reads them.
        fields = {"transaction": transaction}
        if mode is not None:
            fields["mode"] = mode
        if intention.note is not None:
            fields["note"] = intention.note
        if data is not None:
            fields["preimage"] = blob
            fields["postimage"] = digest(data)
            if prior_bytes is not None:
                fields["prior_bytes"] = prior_bytes
        append(
            [
                self._record(
                    intention.op,
                    intention.purpose,
                    location,
                    intention.durability,
                    stage,
                    **fields,
                )
                for stage in (PREPARED, COMMITTED)
            ],
            self.root,
            intention.durability,
        )
        _fault("after-history")

        _resolve_transaction(self.root, transaction)
        self._seen.add((intention.durability, location))
        return Outcome(
            OUTCOME_APPLIED,
            intention.op,
            location,
            intention.durability,
            transaction=transaction,
            mode=mode,
        )

    def _refused(self, intention, actual, message):
        """A refusal that left nothing behind: no transaction, no record."""
        return Outcome(
            OUTCOME_REFUSED,
            intention.op,
            intention.path,
            intention.durability,
            message=message,
            mode=actual.get("mode"),
        )

    def _aborted(self, transaction, intention, actual, message):
        """A refusal after the transaction was opened: close it, then remove it.

        `aborted` is written before the file is unlinked rather than instead
        of it. The two are not one operation, and a crash between them is
        what recovery reads: an `aborted` entry says a decision was made and
        nothing was published, where a file that simply vanished says
        nothing at all.
        """
        _abort_transaction(self.root, transaction, message)
        _resolve_transaction(self.root, transaction)
        return self._refused(intention, actual, message)

    def _payload(self, intention, location, actual):
        """The bytes publication will write, and the length the file had.

        `(None, None)` for a mutation with no bytes -- a directory, a
        symlink. `prior_bytes` is not None only for an `append`, whose
        inverse is "truncate to the recorded prior length" (§2) and which is
        therefore the only op that needs it.

        A node that is neither a regular file, a directory nor a symlink --
        a FIFO, a socket, a device -- is reported by `current_state` as a
        file with no digest, and reading through one can block for ever.
        Nothing here reads it: `(None, ...)` for a byte-carrying intention
        is the caller's signal to refuse.
        """
        if intention.content is None:
            return None, None
        if actual["kind"] == FILE and "digest" not in actual:
            return None, None
        if intention.op != APPEND:
            return intention.content, None
        existing = b""
        if actual["kind"] == FILE:
            existing = (self.root / location).read_bytes()
        return existing + intention.content, len(existing)

    def _publish(self, intention, location, actual, data):
        """Put the new state on disk, atomically and durably; return its mode.

        Publication is not one primitive, and treating it as one is what
        design §6 refuses. Four shapes, chosen by what is expected to be
        there rather than by the op's name:

        - **A directory** -- `os.mkdir`, never `parents=True`. Creating an
          ancestor nobody asked for is a second mutation with no intention
          and no record, so a missing parent is a refusal that names it.
        - **A creation over an absent name** -- `O_CREAT | O_EXCL`, which
          fails if the name is taken. §6 promises "a strong no-replace
          guarantee for a creation, because the primitive exists", and
          check-then-`os.replace` is not that primitive: a third party
          creating the file between the re-read and here would be
          overwritten by a record that says `create`.
        - **A replacement** -- a temporary, fsynced, given the TARGET's mode
          (§7: the install copies the mode onto the temporary before the
          rename, or the adopter's 0640 comes back 0644), then
          `os.replace`, then the directory fsync `install` performs.
        - **A symlink** -- built under a pid-named temporary and `os.replace`d
          over the path, so the link is never absent for an instant. An
          `unlink` then `symlink_to` leaves a session with no memory at all
          if it dies in between.

        A failure anywhere raises `OSError` and leaves the target as it was:
        the temporary is removed, and a partial `O_EXCL` creation is
        unlinked, so the caller's `aborted` transaction is the only trace.
        """
        target = self.root / location
        if intention.directory:
            if not target.parent.is_dir():
                raise FileNotFoundError(
                    f"its parent directory "
                    f"{Path(location).parent.as_posix()} does not exist"
                )
            os.mkdir(target)
            fsync_directory(target.parent)
        elif intention.op == LINK:
            temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary.unlink(missing_ok=True)
            try:
                os.symlink(intention.target, temporary)
                os.replace(temporary, target)
            except OSError:
                temporary.unlink(missing_ok=True)
                raise
            fsync_directory(target.parent)
        elif actual["kind"] == ABSENT:
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666
            )
            try:
                # Through a file object rather than `os.write`, which is
                # allowed to write fewer bytes than it was given.
                with open(descriptor, "wb", closefd=True) as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError:
                target.unlink(missing_ok=True)
                raise
            fsync_directory(target.parent)
        else:
            temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
            try:
                # Created 0600 and only then given the target's mode, both
                # before the rename. A temporary made under the umask would
                # carry the adopter's bytes at 0644 for the length of the
                # write, so publishing a 0600 file would put its contents
                # where anyone could read them for that window. The unlink
                # first is what keeps `O_EXCL` usable: a temporary left by a
                # run that died here carries this pid and nothing else would
                # ever clear it.
                temporary.unlink(missing_ok=True)
                descriptor = os.open(
                    temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
                with open(descriptor, "wb", closefd=True) as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, actual["mode"])
                install(temporary, target)
            except OSError:
                temporary.unlink(missing_ok=True)
                raise
        try:
            return stat.S_IMODE(os.lstat(target).st_mode)
        except OSError:
            # Published, but its mode cannot be read back. The records are
            # written without one rather than with a guess: a reversal
            # restoring a mode nobody measured is worse than one that knows
            # it was never told.
            return None

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

    def write(self, path, content, purpose, durability=REPO):
        """Create or replace the text file at `path`, through the executor.

        A shim, and it says so: `init` does not yet state what it expects to
        find, so this reads the current state and states it on the caller's
        behalf -- absent, and the op is a `create`; a file, and the op is a
        `replace` expecting exactly the digest and mode just read. That is
        strictly weaker than an expectation the caller declares, because
        anything already there is accepted rather than refused; what it
        does buy is every step of the protocol, which no caller now
        reimplements. `init` declares its own expectations in task 6 and
        this method goes.

        A refusal comes back as `OSError` carrying the executor's message,
        because that is what the callers already catch per item
        (`init._ensure_file`, `init._ensure_ignored`): one item gets an
        ERROR and the rest of the run continues.
        """
        return self._through_executor(
            path, content, purpose, durability, append_to_it=False
        )

    def append_text(self, path, content, purpose, durability=REPO):
        """Append `content` to the text file at `path`, through the executor.

        The inverse of an `append` is "truncate to the recorded prior
        length" (§2), so the record carries `prior_bytes` alongside the
        preimage and postimage digests -- `_execute` computes both, since
        only it has read the bytes already there. `preimage` is null when
        the file did not exist at all, exactly as it is for a `create`: the
        inverse is then removing the file, not truncating it to nothing, and
        only the record can say which of the two this was. That is why the
        op stays `append` over an absent path rather than becoming a
        `create`: the two have different inverses, and the record is the
        only place the difference survives.

        The append is a read-modify-write published atomically, not an
        `open(..., "a")`: a torn append would leave bytes no postimage
        describes, which is the state this protocol exists to rule out.
        """
        return self._through_executor(
            path, content, purpose, durability, append_to_it=True
        )

    def _through_executor(self, path, content, purpose, durability, append_to_it):
        """Build the intention `write`/`append_text` never asked their caller for."""
        location = self._location(path)
        actual = current_state(self.root, location)
        if append_to_it:
            op = APPEND
        else:
            op = CREATE if actual["kind"] == ABSENT else REPLACE
        outcome = self.execute(
            Intention(
                op=op,
                purpose=purpose,
                path=location,
                durability=durability,
                expected=actual,
                content=content.encode("utf-8"),
            )
        )
        if outcome.status == OUTCOME_REFUSED:
            raise OSError(outcome.message)
        return outcome

    def prepare_op(self, op, purpose, path, note, durability=REPO):
        """Record that a mutation with no file preimage is about to happen.

        Public for ONE caller, `init._record_symlink`, until the harness
        link moves onto the executor in task 6; nothing else may open a
        stage. Design §4 takes `prepare_op` and `append_op` off the public
        surface precisely so that no module outside this one can
        reimplement the protocol the way `init.py` did.

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

        Public for the same one caller as `prepare_op`, and for as long.

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

    # --- recovery and resolution: closing what an earlier run left open -------

    def recover(self):
        """Resolve every transaction an earlier run left behind; report each.

        Explicit, and not a side effect of `__init__`: a constructor that
        completes half-finished mutations is a constructor with a
        filesystem's worth of failure modes, and `journal --resolve` needs
        a `Run` that does NOT recover -- an operator closing one transaction
        by hand must not have the others silently closed underneath.

        `init` calls this immediately after building its `Run`, under the
        run-wide lock and BEFORE its own first intention. Recovery only ever
        completes or closes what an earlier run began, and unlinks the
        files that said so, so it reduces what is on disk rather than adding
        to it -- and an open transaction on `.gitignore` itself is settled
        before the new `.gitignore` intention is formed.

        Idempotent over the same residue, in both directions. A transaction
        this resolves leaves the disk, so a second pass never sees it again;
        and completing one appends only the history records that are not
        already there, checked by transaction id AND stage, because a crash
        between `append` and `_resolve_transaction` leaves a `published`
        transaction whose records exist. Appending them again would double
        the mutation in a versioned, append-only file, where nothing takes
        it back.

        A problem leaves the transaction file exactly where it is. That is
        the point: `diverged`, `unknown` and `damaged` are the three states
        nothing here may decide for the user, and the file is what keeps the
        path gated and the evidence available until they do.
        """
        with Lock(self.root):
            histories = {}
            results = [
                self._recover_one(item, histories)
                for item in _open_transactions(self.root)
            ]
            self._survey(read(self.root, REPO), read(self.root, LOCAL))
            return results

    def _recover_one(self, item, histories):
        """Act on `_classify`'s verdict for one transaction; return a `Recovery`.

        `histories` caches each journal's records across the pass, so a tree
        with several open transactions reads each file once.
        """
        transaction_id = item["id"]
        verdict, facts = _classify(self.root, item)
        path = facts["path"]
        durability = facts["durability"]

        if verdict == PROBLEM_DAMAGED:
            return Recovery(
                transaction_id,
                path,
                durability,
                problem=PROBLEM_DAMAGED,
                message=(
                    f"damaged transaction {transaction_id}: {facts['reason']}; "
                    f"nothing here can say what it did, so "
                    f"{_transaction_path(Path(), transaction_id).as_posix()} "
                    "is left for inspection"
                ),
            )

        if verdict == _REMOVE:
            reason = item.get("reason")
            _resolve_transaction(self.root, transaction_id)
            said = f" ({reason})" if isinstance(reason, str) else ""
            return Recovery(
                transaction_id,
                path,
                durability,
                action=REMOVED,
                message=(
                    f"transaction {transaction_id} on {path} was closed "
                    f"aborted{said} and published nothing; its file has been "
                    "removed"
                ),
            )

        if verdict == _DISCARD:
            _resolve_transaction(self.root, transaction_id)
            return Recovery(
                transaction_id,
                path,
                durability,
                action=DISCARDED,
                message=(
                    f"transaction {transaction_id} never published, so {path} "
                    "is as it was and nothing has been recorded"
                ),
            )

        if verdict == _COMPLETE:
            appended = self._complete(transaction_id, item, facts, histories)
            _resolve_transaction(self.root, transaction_id)
            return Recovery(
                transaction_id,
                path,
                durability,
                action=RECOVERED,
                message=(
                    f"transaction {transaction_id} published {path}, and its "
                    "two history records have been appended"
                    if appended
                    else f"transaction {transaction_id} published {path} and "
                    "was already recorded; only its file was left to remove"
                ),
            )

        if verdict == PROBLEM_DIVERGED:
            message = (
                f"transaction {transaction_id} published {path}, but {path} "
                f"is {_describe(facts['actual'])} now and not what was "
                "published; nothing here can say whether that is wanted -- "
                f"{_resolution_advice(transaction_id)}"
            )
        else:
            message = (
                f"transaction {transaction_id} prepared a mutation of {path}, "
                f"and {path} is {_describe(facts['actual'])} now, which is "
                "neither the state it was to change from nor the one it was "
                "to change to; nothing here can say whether it ran -- "
                f"{_resolution_advice(transaction_id)}"
            )
        return Recovery(
            transaction_id, path, durability, problem=verdict, message=message
        )

    def _complete(self, transaction_id, item, facts, histories):
        """Append whichever of the mutation's two records is not there yet.

        Returns whether anything was appended, so the caller can say which
        of the two shapes of `completed` this was.

        The records are rebuilt from the transaction file and the state the
        path is in NOW -- which `_classify` has just proven is the postimage
        this transaction published. `op`, `purpose`, `path`, `durability`
        and `note` come from the intention; `preimage` (the parked blob's
        reference) and `prior_bytes` from the file; `postimage` from the
        postimage STATE's own digest, which is the same value `_execute`
        wrote; `mode` from the published node. Both halves carry the
        `transaction`, exactly as `_execute` writes them, because that id is
        the only thing that survives the file to say the two lines are one
        act. `run` is the crashed run's: it is the run that wrote the bytes.

        `adoption` is NOT taken from the file. One project has one adoption
        id (`_adoption_id`), this run has already established which, and a
        record filed under any other would attach a mutation of this tree to
        somebody else's history.
        """
        durability = facts["durability"]
        location = facts["path"]
        intention = facts["intention"]
        postimage = facts["postimage"]
        if durability not in histories:
            histories[durability] = read(self.root, durability)
        records = histories[durability]
        present = {
            entry["stage"]
            for entry in records
            if entry.get("transaction") == transaction_id
        }
        missing = [stage for stage in STAGES if stage not in present]
        if not missing:
            return False

        fields = {"transaction": transaction_id}
        mode = facts["actual"].get("mode")
        if mode is not None:
            fields["mode"] = mode
        if intention.get("note") is not None:
            fields["note"] = intention["note"]
        if postimage["kind"] == FILE and "digest" in postimage:
            # A byte-publishing mutation, and the only kind whose records
            # carry digests. `preimage` is the blob reference, null for a
            # path that did not exist -- the same null `_execute` writes,
            # and the same one that distinguishes "undo by truncating" from
            # "undo by removing".
            fields["preimage"] = item.get("preimage_blob")
            fields["postimage"] = postimage["digest"]
            if item.get("prior_bytes") is not None:
                fields["prior_bytes"] = item["prior_bytes"]
        run = item.get("run")
        built = [
            self._record(
                intention["op"],
                intention["purpose"],
                location,
                durability,
                stage,
                run=run if isinstance(run, str) else None,
                **fields,
            )
            for stage in missing
        ]
        append(built, self.root, durability)
        records.extend(built)
        return True

    def resolve_transaction(self, transaction_id, resolution):
        """Close ONE transaction the way an operator says; return a `Resolution`.

        `journal --resolve <id>` with `--accept`, `--restore` or
        `--abandon`. Design §8 is explicit that recovery needs its own
        interface or the project deadlocks: "refuse" is not a terminal
        state, and a diverged transaction gates its path for ever without a
        way out.

        Under the lock, because two of the three write to a journal and one
        of them publishes bytes. It recovers nothing else on the way: an
        operator closing one transaction has not asked for the others.

        What each one records is what is true of it, and no more:

        - `--accept` -- the state the path is in is what the user wants. ONE
          `observe` record, whose note says it was accepted after
          divergence and which transaction found what. An observation, not a
          mutation: the plugin did not produce this state, and a `create`
          or `replace` record would claim it did and offer a reversal that
          would undo somebody else's work. It is the one `observe` written
          about a path the journal already mentions, which is why it does
          not go through `observe` -- its note is what keeps it honest.
        - `--abandon` -- ONE `observe` record saying the path was left as
          found. Nothing is published, nothing is undone.
        - `--restore` -- the preimage goes back and NOTHING is recorded:
          putting a path back where it was leaves no fact about the project
          behind, and the transaction that intended the mutation is the
          thing being cancelled.
        """
        if resolution not in RESOLUTIONS:
            raise ValueError(f"unknown resolution '{resolution}'")
        with Lock(self.root):
            return self._resolve_one(transaction_id, resolution)

    def _resolve_one(self, transaction_id, resolution):
        """`resolve_transaction`'s body, with the lock already held."""
        artifact = _transaction_path(Path(), transaction_id).as_posix()
        item = next(
            (
                entry
                for entry in _open_transactions(self.root)
                if entry["id"] == transaction_id
            ),
            None,
        )
        if item is None:
            return Resolution(
                transaction_id,
                resolution,
                artifact,
                f"there is no unresolved transaction {transaction_id}; "
                "'validated-memory journal --check' lists the ones there "
                "are. Nothing has been changed.",
            )

        verdict, facts = _classify(self.root, item)
        if verdict == PROBLEM_DAMAGED:
            return Resolution(
                transaction_id,
                resolution,
                artifact,
                f"transaction {transaction_id} is damaged "
                f"({facts['reason']}), so nothing here can say what it did "
                f"or what to record about it; inspect {artifact} and remove "
                "it by hand. Nothing has been changed.",
            )

        if resolution == RESTORE:
            return self._restore(transaction_id, item, facts)
        found = current_state(self.root, facts["path"])["kind"]
        note = (
            f"accepted after divergence: transaction {transaction_id} "
            f"found {found}"
            if resolution == ACCEPT
            else f"abandoned: transaction {transaction_id}, path left as found"
        )
        append(
            [
                self._record(
                    OBSERVE,
                    facts["intention"]["purpose"],
                    facts["path"],
                    facts["durability"],
                    COMMITTED,
                    note=note,
                )
            ],
            self.root,
            facts["durability"],
        )
        _resolve_transaction(self.root, transaction_id)
        return Resolution(transaction_id, resolution, facts["path"])

    def _restore(self, transaction_id, item, facts):
        """Put the preimage back, or refuse and leave everything alone.

        Two refusals come first, and both leave the transaction open.

        A transaction whose records are ALREADY in the history is not one
        recovery may reverse. The `committed` record means the mutation
        happened and is history; taking the bytes back without taking the
        record back would make the journal describe a state that is not
        there, and the record cannot be taken back -- the history is
        append-only. `--accept` or `--abandon` is the answer.

        A preimage blob that is missing, or whose bytes do not digest to the
        name it is filed under, refuses. This is the case design §10 says
        must never be confused with the other one: for a CLOSED history
        record a missing blob is normal, because the journal travels and the
        vault does not, and it means only that this clone cannot reverse
        that mutation. For an OPEN transaction the blob is the sole copy of
        the bytes the plugin was about to overwrite, parked and verified
        moments before -- its absence is a damaged log, and writing
        something else over the path would be writing wrong bytes.

        The publication is the executor's own (`_publish`), so a restore is
        as atomic and as durable as the mutation it reverses. The mode comes
        from the transaction file, which recorded the preimage's own -- not
        from whatever is at the path now, which may be the plugin's
        replacement or a third party's file. The read-only bit is NOT
        consulted: `_write_denied` exists so a mutation never quietly
        overwrites what an adopter marked unwritable, and this is the
        opposite -- an operator's explicit instruction to put that adopter's
        own bytes back.

        Nothing is recorded. A path returned to the state a record would
        have described the departure from is not a fact about the project.
        """
        location = facts["path"]
        durability = facts["durability"]
        artifact = _transaction_path(Path(), transaction_id).as_posix()

        def refuse(message):
            return Resolution(transaction_id, RESTORE, location, message)

        if any(
            entry.get("transaction") == transaction_id
            for entry in read(self.root, durability)
        ):
            return refuse(
                f"transaction {transaction_id} is already recorded in "
                f"{artifact_name(durability)}: the mutation happened, and an "
                "append-only history is not taken back. Close it with "
                "--accept or --abandon instead. Nothing has been restored."
            )

        preimage = facts["preimage"]
        kind = preimage["kind"]
        if kind == DIRECTORY:
            return refuse(
                f"the preimage of {location} is a directory, and nothing "
                "here rebuilds one: its contents were never parked. Close "
                "the transaction with --accept or --abandon. Nothing has "
                "been restored."
            )

        intention = None
        data = None
        actual = current_state(self.root, location)
        if kind == FILE:
            reference = item.get("preimage_blob")
            if not isinstance(reference, str):
                return refuse(
                    f"transaction {transaction_id} says {location} was a "
                    "file and names no preimage for it, so the bytes it was "
                    "about to overwrite were never parked; this log is "
                    "damaged. Nothing has been restored."
                )
            blob = (
                self.root
                / VAULT_DIRNAME
                / PREIMAGE_DIRNAME
                / reference.replace("sha256:", "")
            )
            if not blob.exists():
                return refuse(
                    f"the preimage of {location}, {reference}, is not in "
                    f"{VAULT_DIRNAME}/{PREIMAGE_DIRNAME}/. This transaction "
                    "is still OPEN, so that blob is the only copy of the "
                    "bytes it was about to overwrite: this is a damaged "
                    "log, not a clone whose vault stayed behind. Nothing "
                    "has been restored."
                )
            if not _blob_matches(blob, reference):
                return refuse(
                    f"the preimage of {location} in "
                    f"{VAULT_DIRNAME}/{PREIMAGE_DIRNAME}/ does not digest to "
                    f"{reference}, the name it is filed under, so it is not "
                    "the bytes this transaction parked. Nothing has been "
                    "restored."
                )
            mode = item.get("mode")
            if mode is None:
                mode = preimage.get("mode")
            if not isinstance(mode, int) or isinstance(mode, bool):
                return refuse(
                    f"transaction {transaction_id} records no mode for the "
                    f"preimage of {location}, and bytes are not put back "
                    "under a mode nobody chose. Nothing has been restored."
                )
            data = blob.read_bytes()
            actual = {"kind": FILE, "mode": mode}
            intention = Intention(
                op=REPLACE,
                purpose=facts["intention"]["purpose"],
                path=location,
                durability=durability,
                expected=preimage,
                content=data,
            )
        elif kind == SYMLINK:
            target = preimage.get("target")
            if not isinstance(target, str):
                return refuse(
                    f"transaction {transaction_id} says {location} was a "
                    "symlink and does not say where it pointed; this log is "
                    "damaged. Nothing has been restored."
                )
            intention = Intention(
                op=LINK,
                purpose=facts["intention"]["purpose"],
                path=location,
                durability=durability,
                expected=preimage,
                target=target,
            )

        try:
            if intention is not None:
                self._publish(intention, location, actual, data)
            else:
                self._unpublish(location, actual)
        except OSError as error:
            return refuse(
                f"{location} could not be put back: {error}. Nothing has "
                "been restored."
            )
        _resolve_transaction(self.root, transaction_id)
        return Resolution(transaction_id, RESTORE, location)

    def _unpublish(self, location, actual):
        """Take the published node away: the inverse of an `absent` preimage.

        A directory is `rmdir`, never a recursive removal: anything inside
        it was put there by something this transaction knows nothing about,
        and a non-empty directory raises, which the caller renders as a
        refusal naming it. A path already absent is nothing to undo. The
        parent is fsynced afterwards, for the same reason `install` does:
        the removal of a directory entry is itself buffered.
        """
        target = self.root / location
        if actual["kind"] == DIRECTORY:
            os.rmdir(target)
        elif actual["kind"] != ABSENT:
            target.unlink()
        fsync_directory(target.parent)


UNAPPLIED = "unapplied"
APPLIED = "applied"
DIVERGED = "diverged"
UNKNOWN = "unknown"


# What both halves of one mutation must say identically. `at` is excluded
# because the two records are written in one `append` but stamped
# separately, `stage` because it is what tells them apart, and `run`,
# `adoption`, `schema` and `version` because `_record` fills them in from
# one source for both. What is left is everything the mutation itself
# decided -- and a `committed` half that disagrees with its `prepared` half
# describes a mutation nobody performed.
PAIRED_FIELDS = (
    "op",
    "path",
    "durability",
    "preimage",
    "postimage",
    "note",
    "prior_bytes",
    "mode",
)


def reconcile(root=Path()):
    """Every unfinished transaction and every disagreeing pair, as two lists.

    Returns `(unfinished, disagreements)`: `(record, state)` pairs for the
    `prepared` records nothing ever closed, and `(transaction, field,
    record)` triples for the closed pairs whose two halves do not say the
    same thing.

    Pairing is by TRANSACTION ID wherever both records carry one, which is
    every mutation the executor has written since it took over the protocol:
    the id is minted per mutation, so it says which `committed` closes which
    `prepared` without any inference at all. Records without the field --
    everything written before, and `prepare_op`/`append_op`'s two halves --
    keep the older rule: file order within a (run, path), so a `committed`
    record closes the ONE `prepared` record it follows and never every
    prepared record that happens to share its key.

    A pair that agrees on nothing but its id is not a pair. `PAIRED_FIELDS`
    is checked on every id-matched pair, because the two records are the
    only evidence a mutation left behind and a reader that averages them is
    a reader inventing a third mutation. A disagreement is reported; it is
    never resolved by preferring one half.

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
    component exists to remove. Repair is `Run.recover`, which reads the
    write-ahead log rather than these two journals, and only ever closes
    what that log accounts for.
    """
    root = Path(root)
    unfinished = []
    disagreements = []
    for durability in DURABILITIES:
        open_by_id = {}
        open_by_key = {}
        for entry in read(root, durability):
            transaction = entry.get("transaction")
            if isinstance(transaction, str):
                if entry["stage"] == PREPARED:
                    open_by_id.setdefault(transaction, []).append(entry)
                elif entry["stage"] == COMMITTED and open_by_id.get(transaction):
                    prepared = open_by_id[transaction].pop(0)
                    disagreements.extend(
                        (transaction, field, entry)
                        for field in PAIRED_FIELDS
                        if prepared.get(field) != entry.get(field)
                    )
                continue
            key = (entry["run"], entry["path"])
            if entry["stage"] == PREPARED:
                open_by_key.setdefault(key, []).append(entry)
            elif entry["stage"] == COMMITTED and open_by_key.get(key):
                open_by_key[key].pop(0)
        for group in (open_by_id, open_by_key):
            for entries in group.values():
                for entry in entries:
                    unfinished.append((entry, _state_of(root, entry)))
    return unfinished, disagreements


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


def run(check, resolve, resolution, stdout, stderr):
    """The `journal` subcommand: report the record, reconcile, or resolve one.

    Read-only in both REPORTING modes, and `--check` is read-only in
    particular: it classifies every unresolved transaction by what recovery
    WOULD do with it and does none of it. Without `--check` it summarises
    and exits 0 whatever it finds, so a reader can look at a project without
    gating on it; with `--check` an unfinished transaction -- from the two
    journals' own pairing (`reconcile`), from a pair whose halves disagree,
    or from a transaction file still on disk (`_open_transactions`) -- is an
    ERROR, because a caller that asked to be told cannot be told by an exit
    code of 0.

    `--resolve` is the third mode and the only one that writes: an
    operator's answer to a transaction recovery would not touch. It is not
    reporting and does not report -- see `Run.resolve_transaction`.

    A transaction file is reported even without `--check`, but only as a
    count: a reader who did not ask to gate on one should still be told
    something is open, on a second line, only when there is something to
    say.
    """
    from .findings import ERROR, EXIT_ERROR, EXIT_OK, Finding

    root = Path()
    if resolve is not None:
        return _run_resolve(root, resolve, resolution, stdout, stderr)
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
        unfinished, disagreements = reconcile(root) if check else ([], [])
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
    for transaction, field, entry in disagreements:
        print(
            Finding(
                ERROR,
                entry["path"],
                "journal",
                f"records of transaction {transaction} disagree on {field}",
            ).render(),
            file=stderr,
        )
    for item in transactions:
        # Classified by the one function recovery itself acts on, so what
        # `--check` promises and what the next run does cannot drift apart.
        verdict, facts = _classify(root, item)
        if verdict == PROBLEM_DAMAGED:
            location = _transaction_path(Path(), item["id"]).as_posix()
            message = f"damaged transaction {item['id']}: {facts['reason']}"
        else:
            location = facts["path"]
            word = (
                RECOVERABLE
                if verdict in (_COMPLETE, _DISCARD, _REMOVE)
                else verdict
            )
            message = (
                f"open transaction {item['id']} ({facts['stage']}) on "
                f"{location}: {word}"
            )
        print(Finding(ERROR, location, "journal", message).render(), file=stderr)

    total_errors = len(unfinished) + len(disagreements) + len(transactions)
    print(
        f"journal: {len(records)} record(s), {total_errors} error(s)",
        file=stdout,
    )
    return EXIT_ERROR if total_errors else EXIT_OK


def _run_resolve(root, transaction_id, resolution, stdout, stderr):
    """`journal --resolve`: close one transaction the way the operator says.

    The one mode of this subcommand that writes, and the only place outside
    `init` that opens a `Run`. It does NOT recover: `Run.recover` is
    explicit precisely so that an operator answering for one transaction
    does not have every other one closed underneath them in the same
    breath.

    A refusal is an ERROR and exit 1, not a traceback and not a usage
    error: the id was well formed and the flags were legal, and what could
    not be done is a fact about this project's state. An unknown id is one
    of those. The success line names the flag as it was typed, because a
    resolution is a decision someone made and the record of the session
    should show which one.
    """
    from .findings import ERROR, EXIT_ERROR, EXIT_OK, Finding

    try:
        outcome = Run(root).resolve_transaction(transaction_id, resolution)
    except JournalError as error:
        where = error.artifact or JOURNAL_FILENAME
        location = where if error.lineno is None else f"{where}:{error.lineno}"
        print(Finding(ERROR, location, "journal", error.message).render(), file=stderr)
        return EXIT_ERROR
    except OSError as error:
        print(
            Finding(
                ERROR,
                error.filename or JOURNAL_FILENAME,
                "journal",
                f"the transaction could not be resolved: {error}",
            ).render(),
            file=stderr,
        )
        return EXIT_ERROR
    if outcome.message is not None:
        print(
            Finding(ERROR, outcome.location, "journal", outcome.message).render(),
            file=stderr,
        )
        return EXIT_ERROR
    print(
        f"journal: resolved {transaction_id} (--{resolution})",
        file=stdout,
    )
    return EXIT_OK
