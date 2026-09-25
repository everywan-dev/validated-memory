"""The record format, and the two permanent artifacts it is written to.

What a record is made of, what names its bytes, where the two journals
live, how a line is appended and fsynced, and the reader that refuses a
journal it cannot account for. Publishing a file over a name is `durable`,
the layer beneath this one. Nothing here knows about the vault's own machinery: the lock, the
preimage store and the write-ahead log are their own modules.
"""

import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from .durable import (
    VisibilityUnconfirmed,
    append_bytes,
    ensure_owned_directory,
    publish_no_replace,
    reconfirm_exact_range,
    reconfirm_exact_file,
)
from .fault import rendezvous_at


JOURNAL_FILENAME = "journal.jsonl"
VAULT_DIRNAME = ".validated-memory"
VAULT_JOURNAL = "local.jsonl"


# These formats evolve through separate compatibility protocols. They retain
# the same value until those protocols are delivered.
HISTORY_READ_SCHEMA = 1
HISTORY_WRITE_SCHEMA = 1
WAL_SCHEMA = 1


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


_Generation = tuple[int, int, int, int, int, int]


@dataclass(frozen=True)
class RawHistory:
    data: bytes | None
    mode: int | None
    generation: _Generation | None
    error: JournalError | None = None


@dataclass(frozen=True)
class RawHistoryPair:
    repository: RawHistory
    local: RawHistory


@dataclass(frozen=True)
class RawHistoryFailure:
    preceding: tuple[RawHistory, ...]
    error: JournalError


@dataclass(frozen=True)
class ConfirmedHistories:
    pair: RawHistoryPair
    repository: tuple[dict, ...]
    local: tuple[dict, ...]


@dataclass
class _OpenedHistory:
    durability: str
    path: Path
    descriptor: int | None = None
    generation: _Generation | None = None
    data: bytes | None = None
    error: JournalError | None = None
    absent: bool = False
    failure_stage: str | None = None
    failure_error: OSError | None = None


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
        "schema": HISTORY_WRITE_SCHEMA,
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
    ensure_owned_directory(path.parent, Path(root))
    payload = encode_records(records)
    append_bytes(path, payload)


def encode_records(records):
    """Return the canonical UTF-8 JSONL bytes for history records."""
    return b"".join(
        (json.dumps(entry, sort_keys=True) + "\n").encode("utf-8")
        for entry in records
    )


def history_snapshot(root=Path(), durability=REPO):
    """Read one history through a descriptor for a proof-bound append."""
    path = journal_path(root, durability)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return b""
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise JournalError(
                None, "history is not a regular file", artifact_name(durability)
            )
        with open(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    except OSError as error:
        raise JournalError(
            None, f"history could not be read: {error}", artifact_name(durability)
        ) from error
    finally:
        os.close(descriptor)


def artifact_name(durability):
    """The journal of `durability`, named the way a finding names a file."""
    return journal_path(Path(), durability).as_posix()


def ensure_history_compatibility():
    """Refuse a history writer its paired reader cannot validate."""
    if HISTORY_WRITE_SCHEMA > HISTORY_READ_SCHEMA:
        raise JournalError(
            None,
            f"journal history write schema {HISTORY_WRITE_SCHEMA} exceeds "
            f"read schema {HISTORY_READ_SCHEMA}; the history upgrade is "
            "incomplete",
            JOURNAL_FILENAME,
        )


def is_complete_opening(entry):
    """Whether one validated repository record is the adoption opening."""
    return (
        entry["op"] == OBSERVE
        and entry["purpose"] == "init"
        and entry["path"] == JOURNAL_FILENAME
        and entry["durability"] == REPO
        and entry["stage"] == COMMITTED
        and entry.get("note") == "journal opened"
    )


def existing_adoption_id(repository, vault):
    """Return the one adoption identity established across both histories."""
    repository_ids = {entry["adoption"] for entry in repository}
    vault_ids = {entry["adoption"] for entry in vault}
    established = repository_ids | vault_ids
    if len(established) <= 1:
        return next(iter(established), None)

    minted = next(iter(repository_ids)) if len(repository_ids) == 1 else None
    kept = next(iter(vault_ids)) if len(vault_ids) == 1 else None
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

    descriptions = []
    if repository_ids:
        descriptions.append(
            f"{JOURNAL_FILENAME} is filed under "
            + ", ".join(f"'{identity}'" for identity in sorted(repository_ids))
        )
    if vault_ids:
        descriptions.append(
            f"{artifact_name(LOCAL)} is filed under "
            + ", ".join(f"'{identity}'" for identity in sorted(vault_ids))
        )
    conflict = (
        artifact_name(REPO) if len(repository_ids) > 1 else artifact_name(LOCAL)
    )
    raise JournalError(
        None,
        f"{'; '.join(descriptions)}; one project has one adoption id, and "
        "nothing here can say which identity later records belong to -- "
        "restore the histories so every record carries the same adoption "
        f"id, or move {VAULT_DIRNAME}/ aside to adopt afresh only if its "
        "preimages do not belong to this project",
        conflict,
    )


def validate_snapshot(data, durability, where):
    """Strictly validate an in-memory complete JSONL history snapshot."""
    if data and not data.endswith(b"\n"):
        raise JournalError(None, "history snapshot has no final line feed", where)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise JournalError(None, f"history snapshot is not UTF-8: {error}", where) from error
    result = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as error:
            raise JournalError(lineno, f"line is not valid JSON: {error.msg}", where) from error
        if not isinstance(entry, dict):
            raise JournalError(lineno, "record is not a JSON object", where)
        _validate_entry(lineno, entry, durability, where)
        result.append(entry)
    return result


def _validate_entry(lineno, entry, durability, where):
    missing = [field for field in COMMON_FIELDS if field not in entry]
    if missing:
        raise JournalError(lineno, f"record is missing {', '.join(missing)}", where)
    _check_types(lineno, entry, where)
    if entry["schema"] > HISTORY_READ_SCHEMA:
        raise JournalError(lineno, f"record uses schema {entry['schema']}, newer than this plugin understands ({HISTORY_READ_SCHEMA}); upgrade the plugin", where)
    if entry["op"] not in OPS:
        raise JournalError(lineno, f"record has unknown op '{entry['op']}'", where)
    if entry["stage"] not in STAGES:
        raise JournalError(lineno, f"record has unknown stage '{entry['stage']}'", where)
    if entry["durability"] != durability:
        raise JournalError(lineno, f"record claims durability '{entry['durability']}' in the '{durability}' journal", where)
    if durability == REPO and not is_inside_path(entry["path"]):
        raise JournalError(lineno, f"record path '{entry['path']}' is not inside the adopter root", where)


def read(root=Path(), durability=REPO, with_snapshot=False):
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
    journal creation that must not then install over the link, where the
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
        return ([], None, None) if with_snapshot else []
    except OSError as error:
        raise JournalError(
            None, f"journal could not be read: {error}", where
        ) from error
    try:
        file_mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(file_mode):
            raise JournalError(
                None,
                "journal is not a regular file; a directory, a device or a "
                "pipe holds no records and nothing here can read one from it",
                where,
            )
        # `closefd=False`: the descriptor has one owner, the `finally` below,
        # so a failure between the two closes it exactly once.
        with open(descriptor, "rb", closefd=False) as handle:
            data = handle.read()
    except OSError as error:
        raise JournalError(
            None, f"journal could not be read: {error}", where
        ) from error
    finally:
        os.close(descriptor)
    records = _parse_history_bytes(data, durability, where)
    if with_snapshot:
        return records, data, stat.S_IMODE(file_mode)
    return records


def _parse_history_bytes(data, durability, where):
    """Parse exact descriptor bytes with the ordinary history diagnostics."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise JournalError(
            None, f"journal could not be read: {error}", where
        ) from error
    if data and not data.endswith(b"\n"):
        raise JournalError(
            None,
            "non-empty history does not end with a line feed; the final "
            "record is not appendable without explicit targeted repair",
            where,
        )
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
        if entry["schema"] > HISTORY_READ_SCHEMA:
            raise JournalError(
                lineno,
                f"record uses schema {entry['schema']}, newer than this "
                f"plugin understands ({HISTORY_READ_SCHEMA}); upgrade the plugin",
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
        if durability == REPO and not is_inside_path(entry["path"]):
            raise JournalError(
                lineno,
                f"record path '{entry['path']}' is not inside the adopter "
                "root; a repository record may only carry a relative path "
                "that stays below it",
                where,
            )
        records.append(entry)
    return records


def _generation(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _descriptor_stat(descriptor):
    return os.fstat(descriptor)


def _open_history(root, durability):
    path = journal_path(root, durability)
    opened = _OpenedHistory(durability, path)
    try:
        opened.descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        )
    except FileNotFoundError:
        opened.absent = True
        return opened
    except OSError as error:
        opened.error = JournalError(
            None,
            f"journal could not be read: {error}",
            artifact_name(durability),
        )
        opened.failure_stage = "open"
        opened.failure_error = error
        return opened
    try:
        info = _descriptor_stat(opened.descriptor)
        opened.generation = _generation(info)
        if not stat.S_ISREG(info.st_mode):
            opened.error = JournalError(
                None,
                "journal is not a regular file; a directory, a device or a "
                "pipe holds no records and nothing here can read one from it",
                artifact_name(durability),
            )
    except OSError as error:
        opened.error = JournalError(
            None,
            f"journal could not be read: {error}",
            artifact_name(durability),
        )
        opened.failure_stage = "initial-fstat"
        opened.failure_error = error
    return opened


def _read_to_eof(descriptor):
    chunks = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _read_descriptor(opened):
    if opened.descriptor is None or opened.error is not None:
        return
    try:
        before = _generation(_descriptor_stat(opened.descriptor))
    except OSError as error:
        opened.failure_stage = "pre-fstat"
        opened.failure_error = error
    else:
        try:
            data = _read_to_eof(opened.descriptor)
        except OSError as error:
            opened.failure_stage = "read"
            opened.failure_error = error
        else:
            try:
                after = _generation(_descriptor_stat(opened.descriptor))
            except OSError as error:
                opened.failure_stage = "post-fstat"
                opened.failure_error = error
            else:
                if before != opened.generation or after != before:
                    opened.generation = None
                    return
                opened.data = data
                return
    error = opened.failure_error
    if error is not None:
        opened.error = JournalError(
            None,
            f"journal could not be read: {error}",
            artifact_name(opened.durability),
        )


def _repeat_descriptor_failure(opened):
    if opened.failure_stage in {"pre-fstat", "post-fstat"}:
        try:
            _descriptor_stat(opened.descriptor)
        except OSError as error:
            return _same_error(opened.failure_error, error)
        return False
    if opened.failure_stage == "read":
        try:
            os.lseek(opened.descriptor, 0, os.SEEK_SET)
        except OSError:
            return False
        try:
            _read_to_eof(opened.descriptor)
        except OSError as error:
            return _same_error(opened.failure_error, error)
    return False


def _same_error(first, second):
    return (
        first is not None
        and type(first) is type(second)
        and first.errno == second.errno
    )


def _verify_history_name(opened):
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    if opened.absent:
        try:
            descriptor = os.open(opened.path, flags)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        else:
            os.close(descriptor)
            return False
    if opened.descriptor is None:
        try:
            descriptor = os.open(opened.path, flags)
        except OSError as error:
            return _same_error(opened.failure_error, error)
        else:
            os.close(descriptor)
            return False
    if opened.failure_stage == "initial-fstat":
        try:
            _descriptor_stat(opened.descriptor)
        except OSError as error:
            if not _same_error(opened.failure_error, error):
                return False
        else:
            return False
        try:
            replacement = os.open(opened.path, flags)
        except OSError:
            return False
        try:
            try:
                _descriptor_stat(replacement)
            except OSError as error:
                return _same_error(opened.failure_error, error)
            return False
        finally:
            os.close(replacement)
    if opened.generation is None:
        return False
    try:
        descriptor_fstat_failed = opened.failure_stage in {
            "pre-fstat",
            "post-fstat",
        }
        if opened.failure_stage is not None:
            if not _repeat_descriptor_failure(opened):
                return False
        elif _generation(_descriptor_stat(opened.descriptor)) != opened.generation:
            return False
        replacement = os.open(opened.path, flags)
        try:
            if _generation(_descriptor_stat(replacement)) != opened.generation:
                return False
        finally:
            os.close(replacement)
        return descriptor_fstat_failed or (
            _generation(_descriptor_stat(opened.descriptor)) == opened.generation
        )
    except OSError:
        return False


def _raw_history(opened):
    if opened.absent:
        return RawHistory(None, None, None)
    mode = (
        stat.S_IMODE(opened.generation[2])
        if opened.generation is not None
        else None
    )
    return RawHistory(opened.data, mode, opened.generation, opened.error)


def acquire_history_pair(root=Path()):
    """Acquire one coherent descriptor-bound generation of both histories."""
    root = Path(root)
    for attempt in range(1, 4):
        opened = [_open_history(root, REPO), _open_history(root, LOCAL)]
        try:
            _read_descriptor(opened[0])
            rendezvous_at("after-first-history-read", attempt)
            _read_descriptor(opened[1])
            if not _verify_history_name(opened[0]):
                continue
            if opened[0].error is not None:
                return RawHistoryFailure((), opened[0].error)
            repository = _raw_history(opened[0])
            if not _verify_history_name(opened[1]):
                continue
            if opened[1].error is not None:
                return RawHistoryFailure((repository,), opened[1].error)
            return RawHistoryPair(repository, _raw_history(opened[1]))
        finally:
            for item in opened:
                if item.descriptor is not None:
                    os.close(item.descriptor)
    raise JournalError(
        None,
        "journal histories changed during inspection; rerun the command",
        f"{artifact_name(REPO)} + {artifact_name(LOCAL)}",
    )


def parse_acquired_history(raw, durability):
    """Validate one acquired artifact in repository/local presentation order."""
    if raw.error is not None:
        raise raw.error
    if raw.data is None:
        return []
    return _parse_history_bytes(raw.data, durability, artifact_name(durability))


def confirm_histories(root):
    """Return one coherent, parsed pair with descriptor identity evidence."""
    acquired = acquire_history_pair(root)
    if isinstance(acquired, RawHistoryFailure):
        raise acquired.error
    return ConfirmedHistories(
        acquired,
        tuple(parse_acquired_history(acquired.repository, REPO)),
        tuple(parse_acquired_history(acquired.local, LOCAL)),
    )


def publish_opening(root, opening, expected_local):
    """Publish and coherently confirm one complete opening without replacement."""
    root = Path(root)
    path = journal_path(root, REPO)
    data = encode_records((opening,))
    before = confirm_histories(root)
    if before.pair.repository.data is not None:
        raise FileExistsError(os.fspath(path))
    local_before = before.pair.local
    successor = None
    if before.local != tuple(expected_local):
        raise JournalError(
            None,
            "local history changed before opening publication",
            artifact_name(LOCAL),
        )

    def verify(identity, _staging_mode, expected):
        nonlocal successor
        try:
            info = path.lstat()
        except OSError as error:
            raise JournalError(
                None, f"published opening could not be read back: {error}",
                artifact_name(REPO),
            ) from error
        if (
            not stat.S_ISREG(info.st_mode)
            or (info.st_dev, info.st_ino) != identity
            or stat.S_IMODE(info.st_mode) != 0o644
        ):
            raise JournalError(
                None,
                "published opening identity or mode changed before readback",
                artifact_name(REPO),
            )
        confirmed = confirm_histories(root)
        pair = confirmed.pair
        if (
            pair.repository.data != expected
            or confirmed.repository != (opening,)
            or pair.local.data != local_before.data
            or pair.local.mode != local_before.mode
            or pair.local.generation != local_before.generation
            or confirmed.local != tuple(expected_local)
        ):
            raise JournalError(
                None,
                "published opening did not become the expected coherent pair",
                artifact_name(REPO),
            )
        successor = confirmed

    publish_no_replace(path, data, 0o644, verify)
    if successor is None:
        raise RuntimeError("opening publication returned without coherent readback")
    return successor


def reconfirm_opening(root, opening):
    """Re-dirty and coherently confirm one descriptor-bound complete opening."""
    root = Path(root)
    path = journal_path(root, REPO)
    try:
        named = path.lstat()
    except OSError as error:
        raise JournalError(
            None, f"opening could not be inspected: {error}", artifact_name(REPO)
        ) from error
    if stat.S_ISLNK(named.st_mode) or not stat.S_ISREG(named.st_mode):
        raise JournalError(
            None,
            "the existing bootstrap name is not a regular canonical file; it "
            "was preserved",
            artifact_name(REPO),
        )

    before = confirm_histories(root)
    repository = before.repository
    expected = encode_records((opening,))
    if (
        len(repository) != 1
        or repository[0] != opening
        or not is_complete_opening(repository[0])
        or before.pair.repository.data != expected
        or before.pair.repository.generation is None
        or before.pair.repository.mode is None
    ):
        raise JournalError(
            None,
            "the existing bootstrap name is not one complete validated "
            "opening; it was preserved",
            artifact_name(REPO),
        )
    identity = (
        before.pair.repository.generation[0],
        before.pair.repository.generation[1],
    )
    opposite = before.pair.local
    reconfirm_exact_file(
        path,
        expected,
        identity,
        before.pair.repository.mode,
    )
    rendezvous_at("after-opening-reconfirmation", 1)
    try:
        after = confirm_histories(root)
        after_repository = after.repository
        after_identity = (
            after.pair.repository.generation[0],
            after.pair.repository.generation[1],
        ) if after.pair.repository.generation is not None else None
        if (
            after_identity != identity
            or after.pair.repository.mode != before.pair.repository.mode
            or after.pair.repository.data != expected
            or after.pair.local.data != opposite.data
            or after.pair.local.mode != opposite.mode
            or after.pair.local.generation != opposite.generation
            or after_repository != (opening,)
        ):
            raise JournalError(
                None,
                "complete opening reconfirmation did not produce the exact "
                "coherent successor",
                artifact_name(REPO),
            )
    except Exception as error:
        raise VisibilityUnconfirmed(
            path, "opening-reconfirmation", True, error
        ) from error
    return after


def reconfirm_append(root, before, claim, *, rewrite):
    """Reconfirm one exact retained append range and its frozen coherent pair."""
    root = Path(root)
    durability = claim["artifact"]
    path = journal_path(root, durability)
    selected = (
        before.pair.repository if durability == REPO else before.pair.local
    )
    opposite = before.pair.local if durability == REPO else before.pair.repository
    snapshot = selected.data
    offset = claim["prefix"]["length"]
    payload = encode_records(claim["records"])
    if (
        snapshot is None
        or selected.generation is None
        or selected.mode is None
        or len(snapshot) < offset + len(payload)
        or digest(snapshot[:offset]) != claim["prefix"]["digest"]
        or snapshot[offset : offset + len(payload)] != payload
    ):
        raise JournalError(
            None,
            "the retained prefix or exact append range is no longer present",
            artifact_name(durability),
        )
    identity = (selected.generation[0], selected.generation[1])
    rendezvous_at("before-append-reconfirmation", 1)
    current = confirm_histories(root)
    if current.pair != before.pair or (
        current.repository != before.repository or current.local != before.local
    ):
        raise JournalError(
            None,
            "the coherent history pair changed before append reconfirmation",
            artifact_name(durability),
        )
    reconfirm_exact_range(
        path,
        snapshot,
        offset,
        payload,
        identity,
        selected.mode,
        rewrite=rewrite,
    )
    rendezvous_at("after-append-reconfirmation", 1)
    try:
        after = confirm_histories(root)
        final = after.pair.repository if durability == REPO else after.pair.local
        final_opposite = (
            after.pair.local if durability == REPO else after.pair.repository
        )
        final_identity = (
            (final.generation[0], final.generation[1])
            if final.generation is not None
            else None
        )
        if (
            final_identity != identity
            or final.mode != selected.mode
            or final.data != snapshot
            or final_opposite != opposite
            or after.repository != before.repository
            or after.local != before.local
        ):
            raise JournalError(
                None,
                "append reconfirmation did not produce the exact coherent successor",
                artifact_name(durability),
            )
    except Exception as error:
        raise VisibilityUnconfirmed(
            path, "append-reconfirmation", True, error
        ) from error
    return after


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


def is_inside_path(path):
    """Whether `path` is relative and names nothing above the adopter root.

    Lexical, because this runs on every record read: `authorise` applies the
    same rule to a record before it is written. The filesystem question --
    whether the path resolves below the root once symlinks are followed --
    is asked where something is about to be touched: `authorise` again,
    before every write and every record, and reconciliation before a read.
    """
    candidate = Path(path)
    return not candidate.is_absolute() and ".." not in candidate.parts
