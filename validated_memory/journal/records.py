"""The record format, and the two permanent artifacts it is written to.

What a record is made of, what names its bytes, where the two journals
live, how a line is appended durably, and the reader that refuses a journal
it cannot account for. Publishing a file is `durable`, the module below
this one. Nothing here knows about the vault's own machinery: the lock, the
preimage store and the write-ahead log are their own modules.
"""

import hashlib
import json
import os
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__


JOURNAL_FILENAME = "journal.jsonl"
VAULT_DIRNAME = ".validated-memory"
VAULT_JOURNAL = "local.jsonl"


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

# What each field must hold. A journal is repository content, which this
# project's rule makes data and never instructions
# (docs/design/2026-08-30-the-journal-coverage-and-reversal-design.md §7),
# and every later reader -- the schema comparison here, the path the
# reconciler builds -- assumes a type only this table checks. `bool` is
# excluded from `int` deliberately: `isinstance(True, int)` is true, and
# `"schema": true` is not a schema.
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
    # path ended up with (so a reversal can put it back --
    # docs/design/2026-09-01-the-journal-core.md §7).
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


def artifact_name(durability):
    """The journal of `durability`, named the way a finding names a file."""
    return journal_path(Path(), durability).as_posix()


def read(root=Path(), durability=REPO):
    """Every record in the journal of `durability`, in file order.

    A missing journal reads as no records. A journal that is there but
    cannot be parsed raises: see the package docstring for why a partial
    answer is not offered.

    "Cannot be parsed" is the whole of
    docs/design/2026-08-30-the-journal-coverage-and-reversal-design.md §7,
    not just JSON: a record whose field holds the wrong type, whose
    `durability` disagrees with the file it is in, or whose
    repository-durability path leaves the adopter root, is refused here,
    before any reader acts on it and before anything can read it as an
    instruction.

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
        if field not in entry:
            continue
        value = entry[field]
        # The same exclusion the loop above makes, for the same reason:
        # `mode` is what a reversal `chmod`s, and `"mode": true` is not a
        # mode.
        if int in expected and isinstance(value, bool):
            value = None
        if not isinstance(value, expected):
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
