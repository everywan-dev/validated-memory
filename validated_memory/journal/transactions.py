"""The write-ahead log: the transaction file, and what a later run reads in it.

The vault directory it lives in, the four stages that write it, the reader
that answers for a damaged one rather than raising, and the classification
a recovery acts on and `journal --check` reports. Also the two frozen
results a caller renders: what recovery did with one transaction, and what
an operator's resolution did.

Not everything that knows the file's shape is here. Recovery completion and
restoration read its `intention`, `preimage`, `postimage`, `preimage_blob`,
`mode`, `prior_bytes` and `run` fields directly, because they rebuild the
records the crashed run would have written; closing that leak is a design
change, not a move.
"""

import errno
import hashlib
import re
import stat
import json
import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from types import MappingProxyType
from typing import Any, Mapping

from .. import __version__
from .durable import (
    VisibilityUnconfirmed,
    ensure_owned_directory,
    install_bytes,
    remove_name,
)
from .operations import INTENTION_OPS
from .paths import own_directory, well_formed_state, current_state, satisfies
from .records import (
    COMMITTED,
    DURABILITIES,
    LOCAL,
    OBSERVE,
    PREPARED,
    REPO,
    WAL_SCHEMA,
    VAULT_DIRNAME,
    is_inside_path,
    new_id,
    now,
)


TRANSACTIONS_DIRNAME = "transactions"


# A transaction file's own stage word, not a journal record's: the two
# artifacts are different files with different lifetimes
# (docs/design/2026-09-01-the-journal-core.md §3), and `PREPARED` is
# shared between them on purpose -- both name the same moment, a
# write-ahead entry fsynced with nothing published yet.
PUBLISHED = "published"
ABORTED = "aborted"
TRANSACTION_STAGES = (PREPARED, PUBLISHED, ABORTED)
TARGET_UNCONFIRMED = "target"
HISTORY_UNCONFIRMED = "history"
HISTORY_CLAIM_UNCONFIRMED = "history-claim"
CLEANUP_UNCONFIRMED = "cleanup"
RESTORE_UNCONFIRMED = "restore"
UNCONFIRMED_PHASES = (
    TARGET_UNCONFIRMED,
    HISTORY_UNCONFIRMED,
    HISTORY_CLAIM_UNCONFIRMED,
    CLEANUP_UNCONFIRMED,
    RESTORE_UNCONFIRMED,
)


def _transactions_dir(root):
    """Where transaction files live: under the vault, never versioned.

    Not a path join: `own_directory` `lstat`s the name and raises
    `JournalError` when something that is not a directory stands there, so
    every caller that asks where a transaction lives has asked that
    question too. That is the point -- the name is where the write is about
    to happen -- and it is why `transaction_artifact` exists for the
    callers that only need to NAME the file.
    """
    return own_directory(root, TRANSACTIONS_DIRNAME)


def _transaction_path(root, transaction_id):
    """The file one transaction lives in; raises what `_transactions_dir` does."""
    return _transactions_dir(root) / f"{transaction_id}.json"


def transaction_artifact(transaction_id):
    """What a `Finding` calls a transaction's file: a name, not a path.

    Rendering must not be the step that refuses a run. `_transactions_dir`
    checks the directory it resolves, which is exactly right for a caller
    about to write through it and exactly wrong for a message naming the
    file a caller has already read.
    """
    return f"{VAULT_DIRNAME}/{TRANSACTIONS_DIRNAME}/{transaction_id}.json"


def _write_transaction_file(root, transaction_id, entry):
    """Write `entry` as the whole of one transaction file, fsynced in place.

    Temporary, fsync, `install` -- the same durability shape journal creation,
    preimage parking, and publication use: the bytes are flushed and fsynced
    before the rename, and `install` fsyncs the directory after it, so the file
    this call leaves behind is exactly as durable whether it is the first write
    of a new transaction or a rewrite of `stage` on an existing one.
    """
    directory = _transactions_dir(root)
    ensure_owned_directory(directory, Path(root))
    path = _transaction_path(root, transaction_id)
    data = (json.dumps(entry, sort_keys=True) + "\n").encode("utf-8")
    install_bytes(path, data)


def open_transaction(
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
    | `schema`         | the write-ahead log schema                                     |
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
    already on disk, which only the adopting session has read.
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
        "schema": WAL_SCHEMA,
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


def mark_published(root, transaction_id, published_mode=None):
    """Record, fsynced, that publication completed.

    Executor-created no-ops open no transaction. A prepared transaction whose
    target has the exact postimage is therefore recoverable after a hard crash
    before this marker. The marker remains useful after later divergence: it
    proves publication happened before another writer changed the target.
    """
    path = _transaction_path(root, transaction_id)
    if "mark-published" in {
        item.strip()
        for item in os.environ.get("VALIDATED_MEMORY_PERSISTENCE_FAULT", "").split(",")
        if item.strip()
    }:
        raise OSError(
            errno.EIO,
            "injected published-marker persistence failure",
            os.fspath(path),
        )
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["stage"] = PUBLISHED
    if published_mode is not None:
        entry["published_mode"] = published_mode
    entry.pop("unconfirmed", None)
    entry.pop("unconfirmed_reason", None)
    _write_transaction_file(root, transaction_id, entry)


def mark_history_append(root, transaction_id, claim):
    """Durably attach the exact history append proof to a published WAL."""
    path = _transaction_path(root, transaction_id)
    if "history-claim" in {
        item.strip()
        for item in os.environ.get("VALIDATED_MEMORY_PERSISTENCE_FAULT", "").split(",")
        if item.strip()
    }:
        raise OSError(errno.EIO, "injected history-claim persistence failure", os.fspath(path))
    entry = json.loads(path.read_text(encoding="utf-8"))
    if "history_append" in entry:
        raise OSError(
            errno.EINVAL,
            f"transaction {transaction_id} already carries a history append proof",
            os.fspath(path),
        )
    entry["history_append"] = claim
    entry["history_timestamps"] = list(claim.get("timestamps", ()))
    _write_transaction_file(root, transaction_id, entry)
    return entry


def mark_temporary_claim(root, transaction_id, claim):
    """Durably record one target-adjacent staging claim before creation."""
    path = _transaction_path(root, transaction_id)
    entry = json.loads(path.read_text(encoding="utf-8"))
    if "temporary" in entry:
        raise OSError(errno.EINVAL, "transaction already carries a temporary claim", os.fspath(path))
    entry["temporary"] = claim
    _write_transaction_file(root, transaction_id, entry)
    return entry


def mark_unconfirmed(root, transaction_id, phase, reason):
    """Persist which visible effect needs a fresh recovery operation."""
    if phase not in UNCONFIRMED_PHASES:
        raise ValueError(f"unknown unconfirmed phase '{phase}'")
    path = _transaction_path(root, transaction_id)
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["unconfirmed"] = phase
    entry["unconfirmed_reason"] = str(reason)
    _write_transaction_file(root, transaction_id, entry)
    requested = {
        item.strip()
        for item in os.environ.get(
            "VALIDATED_MEMORY_PERSISTENCE_FAULT", ""
        ).split(",")
        if item.strip()
    }
    if f"fact:{phase}" in requested or "fact:*" in requested:
        error = OSError(
            errno.EIO,
            "injected recovery-fact barrier failure",
            os.fspath(path),
        )
        raise VisibilityUnconfirmed(
            path, "failure-fact", True, error
        ) from error
    return entry


def abort_transaction(root, transaction_id, reason):
    """Close a transaction that will never publish, recording why."""
    path = _transaction_path(root, transaction_id)
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["stage"] = ABORTED
    entry["reason"] = reason
    _write_transaction_file(root, transaction_id, entry)


def remove_transaction_file(root, transaction_id):
    """Unlink a transaction's file and fsync the directory that held it.

    A resolved transaction leaves the directory: this is the only function
    that removes a transaction file, called once recovery (or the executor
    itself, on its own successful run) has no further use for it.
    """
    path = _transaction_path(root, transaction_id)
    entry = json.loads(path.read_text(encoding="utf-8"))
    try:
        remove_name(path)
    except VisibilityUnconfirmed:
        # The unlink is visible. Re-establish the exact recovery artifact
        # through a fresh confirmed install before reporting uncertainty.
        _write_transaction_file(root, transaction_id, entry)
        raise


def open_transactions(root):
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
        results.append(_read_transaction(path, transaction_id))
    results.sort(key=lambda item: (item.get("at", ""), item["id"]))
    return results


def retained_residue(root):
    """Report private entries that are not canonical transaction artifacts."""
    results = []
    for name in (TRANSACTIONS_DIRNAME, "preimages"):
        directory = Path(root) / VAULT_DIRNAME / name
        try:
            entries = list(directory.iterdir())
        except FileNotFoundError:
            continue
        except OSError as error:
            results.append((f"{VAULT_DIRNAME}/{name}", str(error)))
            continue
        for entry in entries:
            if name == TRANSACTIONS_DIRNAME and entry.name.endswith(".json"):
                continue
            if name == "preimages" and re.fullmatch(r"[0-9a-f]{64}", entry.name):
                continue
            results.append(
                (
                    entry.relative_to(Path(root)).as_posix(),
                    "retained private residue; ownership was not proven",
                )
            )
    return results


def claimed_temporary_residue(root, item):
    """Return a claimed target staging entry without scanning target trees."""
    claim = item.get("temporary")
    intention = item.get("intention")
    if not isinstance(claim, Mapping) or not isinstance(intention, Mapping):
        return []
    target_name = claim.get("target")
    name = claim.get("name")
    if not isinstance(target_name, str) or not isinstance(name, str):
        return [(transaction_artifact(item["id"]), "temporary claim is malformed")]
    if intention.get("durability") == REPO and not is_inside_path(target_name):
        return [(transaction_artifact(item["id"]), "temporary claim leaves the adopter root")]
    target = Path(root) / target_name if intention.get("durability") == REPO else Path(target_name)
    if Path(name).name != name or not name.startswith(f".{target.name}.") or not name.endswith(".tmp"):
        return [(transaction_artifact(item["id"]), "temporary claim uses an unsafe staging name")]
    candidate = target.parent / name
    try:
        candidate.lstat()
    except FileNotFoundError:
        return []
    return [(candidate.as_posix(), "claimed target staging residue is retained until exact cleanup proof")]


def cleanup_private_duplicates(root):
    """Remove only exact regular-file duplicates of canonical private artifacts."""
    removed = []
    for dirname, pattern in ((TRANSACTIONS_DIRNAME, re.compile(r"^\.(.+)\.([0-9a-f]{32})\.tmp$")), ("preimages", re.compile(r"^\.([0-9a-f]{64})\.([0-9a-f]{32})\.tmp$"))):
        directory = Path(root) / VAULT_DIRNAME / dirname
        try:
            entries = list(directory.iterdir())
        except (FileNotFoundError, OSError):
            continue
        for candidate in entries:
            match = pattern.fullmatch(candidate.name)
            if not match:
                continue
            canonical = directory / (match.group(1) if dirname == TRANSACTIONS_DIRNAME else match.group(1) + "")
            if dirname == TRANSACTIONS_DIRNAME and not canonical.name.endswith(".json"):
                canonical = directory / f"{match.group(1)}"
            try:
                cinfo = canonical.lstat()
                info = candidate.lstat()
                if not stat.S_ISREG(cinfo.st_mode) or not stat.S_ISREG(info.st_mode):
                    continue
                if stat.S_IMODE(cinfo.st_mode) != stat.S_IMODE(info.st_mode) or canonical.read_bytes() != candidate.read_bytes():
                    continue
                remove_name(candidate)
                removed.append(candidate.relative_to(Path(root)).as_posix())
            except (FileNotFoundError, OSError):
                continue
    return removed


def historyless_transactions_message(items):
    """Explain why WAL artifacts cannot establish a missing adoption history."""
    identities = sorted(
        {
            item["adoption"]
            for item in items
            if isinstance(item.get("adoption"), str)
        }
    )
    damaged = sorted(item["id"] for item in items if "damaged" in item)
    details = []
    if identities:
        details.append(
            "artifact adoption id(s): " + ", ".join(identities)
        )
    if damaged:
        details.append("damaged artifact(s): " + ", ".join(damaged))
    suffix = f" ({'; '.join(details)})" if details else ""
    return (
        "transaction artifacts exist while neither permanent history "
        "establishes an adoption identity; WAL evidence cannot mint one"
        f"{suffix}. restore repository or local history carrying the matching "
        "identity, then retry; every transaction artifact was left unchanged"
    )


def read_transaction(root, transaction_id):
    """Read exactly one transaction, or return None when its file is absent.

    This is the targeted counterpart to `open_transactions`. Resolution uses
    it under its lock, and damaged files decode exactly as enumeration does.
    """
    path = _target_transaction_path(root, transaction_id)
    if path is None or not os.path.lexists(path):
        return None
    return _read_transaction(path, transaction_id)


def has_transaction(root, transaction_id):
    """Whether the exact transaction name exists, without reading its bytes."""
    path = _target_transaction_path(root, transaction_id)
    return path is not None and os.path.lexists(path)


def _target_transaction_path(root, transaction_id):
    """The targeted transaction path, or None for a path-bearing id.

    Transaction ids are filename stems, not paths. They remain otherwise
    unrestricted: historical and hand-written safe stems need not be hex.
    Both the unlocked probe and the locked reader come through this function
    before `_transactions_dir` is asked for a name.
    """
    if (
        Path(transaction_id).is_absolute()
        or PureWindowsPath(transaction_id).drive
        or "/" in transaction_id
        or "\\" in transaction_id
    ):
        return None
    return _transaction_path(root, transaction_id)


def _read_transaction(path, transaction_id):
    """Decode one known transaction path into the shared reader shape."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return {"id": transaction_id, "damaged": str(error)}
    except ValueError as error:
        # Bytes that are not text at all. `read_text` raises
        # `UnicodeDecodeError`, which is a `ValueError` and not an
        # `OSError`, so the handler above does not see it.
        return {"id": transaction_id, "damaged": f"it is not valid UTF-8: {error}"}
    try:
        entry = json.loads(text)
    except json.JSONDecodeError as error:
        return {"id": transaction_id, "damaged": f"not valid JSON: {error.msg}"}
    except ValueError as error:
        # Everything else the decoder refuses by value rather than by
        # syntax -- a nesting depth it will not follow, a number it will
        # not build. The same answer: this file is not a transaction, and
        # saying so is not a traceback.
        return {"id": transaction_id, "damaged": f"it could not be decoded: {error}"}
    if not isinstance(entry, dict):
        return {"id": transaction_id, "damaged": "record is not a JSON object"}
    entry = dict(entry)
    entry["id"] = transaction_id
    return entry


# --- recovery: what a run does with what an earlier run left open -------------
#
# A crash leaves a transaction file, and
# docs/design/2026-09-01-the-journal-core.md §3 makes the residue
# decidable rather than inferable: the file records a FACT -- what stage
# the mutation reached -- so the next run reads it instead of guessing
# from a filesystem some later process may have changed.

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

# What `classify` says when recovery would resolve the transaction on its
# own, and what `journal --check` calls all three: it reports, so the three
# ways of resolving one are one answer to the only question it asks --
# would a run clear this away by itself?
VERDICT_COMPLETE = "complete"
VERDICT_DISCARD = "discard"
VERDICT_REMOVE = "remove"
VERDICT_TARGET_UNCONFIRMED = "confirm-target"
VERDICT_HISTORY_UNCONFIRMED = "confirm-history"
VERDICT_CLEANUP_UNCONFIRMED = "confirm-cleanup"
VERDICT_RESTORE_UNCONFIRMED = "confirm-restore"
_RECOVERABLE_VERDICTS = (
    VERDICT_COMPLETE,
    VERDICT_DISCARD,
    VERDICT_REMOVE,
    VERDICT_TARGET_UNCONFIRMED,
    VERDICT_HISTORY_UNCONFIRMED,
    VERDICT_CLEANUP_UNCONFIRMED,
    VERDICT_RESTORE_UNCONFIRMED,
)
RECOVERABLE = "recoverable"


@dataclass(frozen=True)
class ClassifiedWal:
    """One total, immutable WAL-1 classification."""

    transaction: str
    path: str | None
    durability: str | None
    stage: str | None
    verdict: str
    problem_reason: str | None
    abort_reason: str | None
    run: str | None
    preimage_blob: str | None
    prior_bytes: int | None
    mode: int | None
    unconfirmed: str | None
    intention: Mapping[str, Any] | None
    preimage: Mapping[str, Any] | None
    postimage: Mapping[str, Any] | None


@dataclass(frozen=True)
class DamagedWal(ClassifiedWal):
    pass


@dataclass(frozen=True)
class UnsupportedWal(ClassifiedWal):
    found: int
    maximum: int


@dataclass(frozen=True)
class TargetNotReadWal(ClassifiedWal):
    pass


@dataclass(frozen=True)
class ReadableTargetWal(ClassifiedWal):
    actual: Mapping[str, Any]


@dataclass(frozen=True)
class UnreadableTargetWal(ClassifiedWal):
    read_error: str


@dataclass(frozen=True)
class HistoryClaimWal(ClassifiedWal):
    """A WAL projected with its typed history-append evidence."""

    target: ReadableTargetWal | UnreadableTargetWal
    claim: str
    occurrence: str

    def __post_init__(self):
        if self.claim not in {"claimless", "valid", "invalid"}:
            raise ValueError(f"unknown history claim state '{self.claim}'")
        if self.occurrence not in _HISTORY_OCCURRENCES:
            raise ValueError(f"unknown history occurrence '{self.occurrence}'")


_HISTORY_OCCURRENCES = frozenset(
    {"zero", "prepared", "complete", "torn", "conflicting", "unavailable"}
)


def report_word(verdict):
    """The word a report gives one `classify` verdict.

    Which of the three ways a run would resolve a transaction is a decision
    this module owns; a reader of `journal --check` asked one question --
    would a later run clear this away by itself -- so the three collapse to
    `RECOVERABLE` and the problems keep their own names.
    """
    return RECOVERABLE if verdict in _RECOVERABLE_VERDICTS else verdict


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
    - `appended` -- whether records were actually written. `completed` has
      two shapes and they are not the same news: a mutation reaching the
      history a session late is a thing that happened to this project,
      while a crash between the append and the unlink left records that
      were already there and only a file to remove. A caller that announces
      both announces a recovery on every session start after the second.
    - `message` -- the sentence a caller renders. For a problem it names the
      transaction and the three flags, because a finding a user cannot act
      on is a stopped session.
    """

    transaction: str
    path: str | None
    durability: str | None
    action: str | None = None
    problem: str | None = None
    appended: bool = False
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


def _classify_legacy(root, item, adoption=None):
    """What recovery would do with one unresolved transaction, doing none of it.

    Returns `(verdict, facts)`. The verdict is `VERDICT_COMPLETE`, `VERDICT_DISCARD`,
    `VERDICT_REMOVE` or one of the three `RECOVERY_PROBLEMS`; `facts` carries
    the whole of what the file and the filesystem said, so no caller reads
    the file again. Every field a caller could want is in it, validated and
    with a settled type, which is why nothing outside this module needs the
    transaction file's own key names.

    `problem_reason` explains a `damaged` transaction or `unknown` path;
    `abort_reason` records why a run closed the transaction as `aborted`.
    This read-only decision table serves both recovery and `journal --check`.

    The rules, in order:

    - A file that could not be read at all (`open_transactions` said so),
      or that is readable but is not a well-formed transaction OF THIS
      PROJECT, is `damaged`. Nothing is inferred from half a file, and
      nothing is completed out of a file that never described a mutation
      of this tree: a schema this reader does not know, a `transaction`
      that is not the file's own name, an `adoption` that is somebody
      else's, an `op` no intention can carry, a preimage or postimage that
      is not a state. `adoption` is checked only when the caller says what
      this project's is -- a tree whose journals are gone has no answer to
      compare against, and inventing one would call every transaction
      foreign.
    - `aborted` is closed already: `VERDICT_REMOVE`, and the file goes.
    - `published` means publication completed and the history had not been
      appended when the process died. The path matching the postimage is
      the mutation: `VERDICT_COMPLETE`. Anything else means something wrote the
      path afterwards: `diverged`.
    - `prepared` means the write-ahead entry was fsynced and nothing more is
      known from the file. The path matching the preimage says the mutation
      never happened: `VERDICT_DISCARD`. Matching the postimage says it did, and
      only the marker was lost: `VERDICT_COMPLETE`. Neither, or BOTH -- which the
      executor's no-op rule makes unreachable for a transaction it opened,
      but not for a hand-written one -- is `unknown`.
    - A path whose bytes cannot be READ at all -- a file this user may not
      open, an I/O error -- is `unknown` too, whatever the stage, and
      `facts["actual"]` is None with the reason in
      `facts["problem_reason"]`.
      Nothing is known about the path, which is exactly what the word
      says; asserting `absent` or `diverged` out of a failed read would be
      the guess this function exists to remove.

    The path is checked lexically for a repository transaction and not
    resolved: `read` refuses a repository record whose path is absolute or
    climbs out with `..`, so a record recovery is about to append has to
    pass that test, but a path that resolves out of the root through a
    symlink is not a reason to refuse to record a mutation that already
    happened.
    """
    facts = {
        "id": item["id"],
        "path": None,
        "durability": None,
        "stage": item.get("stage"),
        "problem_reason": None,
        "abort_reason": None,
        "run": None,
        "preimage_blob": None,
        "prior_bytes": None,
        "mode": None,
        "unconfirmed": item.get("unconfirmed"),
    }

    def damaged(reason):
        facts["problem_reason"] = reason
        return PROBLEM_DAMAGED, facts

    if "damaged" in item:
        return damaged(item["damaged"])

    schema = item.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool):
        return damaged("it names no schema, so nothing here knows how to read it")
    if schema > WAL_SCHEMA:
        return damaged(
            f"its schema is {schema} and this plugin reads up to {WAL_SCHEMA}; a "
            "reader that meets a higher number refuses rather than guessing "
            "at fields it does not know"
        )
    if item.get("transaction") != item["id"]:
        return damaged(
            f"it calls itself transaction {item.get('transaction')} and its "
            f"file is named {item['id']}; the two are one id, and nothing "
            "here can say which of them the history should carry"
        )
    filed = item.get("adoption")
    if adoption is not None and filed != adoption:
        return damaged(
            f"it belongs to adoption {filed}, this project is {adoption}; a "
            "mutation of somebody else's tree is not one this history may "
            "record"
        )

    intention = item.get("intention")
    if not isinstance(intention, dict):
        return damaged("it carries no intention")
    op = intention.get("op")
    purpose = intention.get("purpose")
    path = intention.get("path")
    durability = intention.get("durability")
    note = intention.get("note")
    if op == OBSERVE:
        # Asked before the membership test below, and not by it: `OBSERVE`
        # is not in `INTENTION_OPS`, so the generic refusal would answer
        # first and a hand-edited observation would be reported as an
        # unknown operation. An observation is a fact about a path, not a
        # change to one: it opens no transaction, has no postimage and is
        # recorded at `committed` alone
        # (docs/design/2026-09-01-the-journal-core.md §4). Completing one
        # would append a `prepared` observation -- a record shape nothing
        # in this package writes and no reader expects.
        return damaged(
            "its intention is an observation, which publishes nothing and "
            "never opens a transaction"
        )
    if op not in INTENTION_OPS:
        # `INTENTION_OPS`, not `OPS`: the wider vocabulary is what a
        # RECORD may carry, including the ops of histories written before
        # this core and the ones
        # docs/design/2026-08-30-the-journal-coverage-and-reversal-design.md
        # §2 names for a later step. What may be prepared is only what an
        # `Intention` can hold, and completing anything else would put a
        # record in the history that no executor of this plugin could
        # have produced.
        return damaged("its intention names no operation this plugin prepares")
    if not isinstance(purpose, str) or not isinstance(path, str):
        return damaged("its intention names no path and purpose")
    if durability not in DURABILITIES:
        return damaged(f"its intention claims durability '{durability}'")
    if note is not None and not isinstance(note, str):
        return damaged("its intention's note is not text")
    if durability == REPO and not is_inside_path(path):
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
        facts[field] = value
    blob = item.get("preimage_blob")
    if blob is not None and not isinstance(blob, str):
        return damaged("its preimage reference is not a digest")
    facts["preimage_blob"] = blob
    # Neither is worth refusing a transaction over, and both are dropped
    # rather than passed on: the run id is what recovery files the rebuilt
    # records under, and a non-string there would reach `record`; the abort
    # reason is only ever quoted back to the operator.
    run = item.get("run")
    facts["run"] = run if isinstance(run, str) else None
    reason = item.get("reason")
    facts["abort_reason"] = reason if isinstance(reason, str) else None

    unconfirmed = item.get("unconfirmed")
    has_unconfirmed_reason = "unconfirmed_reason" in item
    unconfirmed_reason = item.get("unconfirmed_reason")
    if unconfirmed is not None and unconfirmed not in UNCONFIRMED_PHASES:
        return damaged(f"it names unknown unconfirmed phase '{unconfirmed}'")
    if unconfirmed is not None and not isinstance(unconfirmed_reason, str):
        return damaged("its unconfirmed phase has no recorded reason")
    if unconfirmed is None and has_unconfirmed_reason:
        return damaged("it records an unconfirmed reason without a phase")

    stage = item.get("stage")
    if stage == ABORTED:
        return VERDICT_REMOVE, facts
    if stage not in (PREPARED, PUBLISHED):
        return damaged(
            f"its stage is '{stage}', and a transaction is one of "
            f"{', '.join(TRANSACTION_STAGES)}"
        )

    preimage = item.get("preimage")
    postimage = item.get("postimage")
    if not isinstance(preimage, dict) or not isinstance(postimage, dict):
        return damaged("it records no preimage and postimage states")
    if not well_formed_state(preimage) or not well_formed_state(postimage):
        return damaged("its preimage or postimage is in no state this plugin knows")
    facts["preimage"] = preimage
    facts["postimage"] = postimage

    if unconfirmed == HISTORY_UNCONFIRMED:
        if stage != PUBLISHED:
            return damaged("history uncertainty requires a published transaction")
        facts["actual"] = postimage
        return VERDICT_HISTORY_UNCONFIRMED, facts
    if unconfirmed == HISTORY_CLAIM_UNCONFIRMED:
        try:
            facts["actual"] = current_state(root, facts["path"])
        except OSError as error:
            facts["actual"] = None
            facts["problem_reason"] = str(error)
            return PROBLEM_UNKNOWN, facts
        facts["problem_reason"] = (
            "the exact history append proof was not durably recorded; "
            "history cannot be completed from this transaction"
        )
        return PROBLEM_UNKNOWN, facts
    if unconfirmed == CLEANUP_UNCONFIRMED:
        if stage != PUBLISHED:
            return damaged("cleanup uncertainty requires a published transaction")
        facts["actual"] = postimage
        return VERDICT_CLEANUP_UNCONFIRMED, facts

    try:
        actual = current_state(root, facts["path"])
    except OSError as error:
        # A digest read failure leaves the state unknown; callers use
        # `problem_reason` instead of `actual`.
        facts["actual"] = None
        facts["problem_reason"] = str(error)
        return PROBLEM_UNKNOWN, facts
    facts["actual"] = actual
    matches_post = satisfies(actual, postimage)
    if unconfirmed == RESTORE_UNCONFIRMED:
        if satisfies(actual, preimage):
            return VERDICT_RESTORE_UNCONFIRMED, facts
        facts["problem_reason"] = (
            "the path no longer has the exact restored preimage whose "
            "durability was unconfirmed"
        )
        return PROBLEM_UNKNOWN, facts
    if unconfirmed == TARGET_UNCONFIRMED:
        if matches_post:
            return VERDICT_TARGET_UNCONFIRMED, facts
        facts["problem_reason"] = (
            "the target no longer has the exact postimage whose durability "
            "was unconfirmed"
        )
        return PROBLEM_UNKNOWN, facts
    if stage == PUBLISHED:
        return (VERDICT_COMPLETE if matches_post else PROBLEM_DIVERGED), facts
    if matches_post and satisfies(actual, preimage):
        # The two states this transaction names cannot be told apart on
        # disk, so nothing here can say whether the mutation ran. The
        # executor never opens such a transaction -- step 4 of `execute`
        # returns `noop` for exactly this -- so it can only be hand-written.
        return PROBLEM_UNKNOWN, facts
    if matches_post:
        return VERDICT_COMPLETE, facts
    if satisfies(actual, preimage):
        return VERDICT_DISCARD, facts
    return PROBLEM_UNKNOWN, facts


def classify_evidence(
    root,
    item,
    adoption=None,
    *,
    history_pair=None,
    histories=None,
    include_stored_claim=False,
):
    """Return the closed WAL evidence variant used by the protocol module.

    ``history_pair`` is the already acquired coherent byte pair and
    ``histories`` is its compatibility parse.  Supplying them prevents a WAL
    classifier from taking a second history observation.  The opt-in
    ``include_stored_claim`` also projects a claim outside the two legacy
    history phases for protocol-owned boundary decisions.  The tuple adapter
    below preserves the established executor contract.
    """
    verdict, mutable = _classify_legacy(root, item, adoption)
    facts = mutable
    common = dict(
        transaction=facts["id"],
        path=facts["path"],
        durability=facts["durability"],
        stage=facts["stage"],
        verdict=verdict,
        problem_reason=facts["problem_reason"],
        abort_reason=facts["abort_reason"],
        run=facts["run"],
        preimage_blob=facts["preimage_blob"],
        prior_bytes=facts["prior_bytes"],
        mode=facts["mode"],
        unconfirmed=facts["unconfirmed"],
        intention=_freeze_mapping(facts.get("intention")),
        preimage=_freeze_mapping(facts.get("preimage")),
        postimage=_freeze_mapping(facts.get("postimage")),
    )
    if verdict == PROBLEM_DAMAGED:
        schema = item.get("schema")
        if (
            isinstance(schema, int)
            and not isinstance(schema, bool)
            and schema >= WAL_SCHEMA + 1
        ):
            return UnsupportedWal(**common, found=schema, maximum=WAL_SCHEMA)
        return DamagedWal(**common)

    if "actual" not in facts:
        target = TargetNotReadWal(**common)
    elif facts["actual"] is None:
        target = UnreadableTargetWal(
            **common,
            read_error=facts["problem_reason"] or "target state is unavailable",
        )
    else:
        target = ReadableTargetWal(
            **common,
            actual=_freeze_mapping(facts["actual"]),
        )

    claim_phase = facts.get("unconfirmed") in {
        HISTORY_CLAIM_UNCONFIRMED,
        HISTORY_UNCONFIRMED,
    }
    if not claim_phase and not (
        include_stored_claim and "history_append" in item
    ):
        return target

    claim_kind = _history_claim_kind(item, facts)
    occurrence = _history_occurrence(
        item,
        facts,
        claim_kind,
        history_pair=history_pair,
        histories=histories,
    )
    return HistoryClaimWal(
        **common,
        target=target,
        claim=claim_kind,
        occurrence=occurrence,
    )


def classify(root, item, adoption=None):
    """Return the established executor tuple from one typed classification."""
    evidence = classify_evidence(root, item, adoption)
    facts = {
        "id": evidence.transaction,
        "path": evidence.path,
        "durability": evidence.durability,
        "stage": evidence.stage,
        "problem_reason": evidence.problem_reason,
        "abort_reason": evidence.abort_reason,
        "run": evidence.run,
        "preimage_blob": evidence.preimage_blob,
        "prior_bytes": evidence.prior_bytes,
        "mode": evidence.mode,
        "unconfirmed": evidence.unconfirmed,
    }
    for name in ("intention", "preimage", "postimage"):
        value = getattr(evidence, name)
        if value is not None:
            facts[name] = _thaw(value)
    legacy_target = (
        evidence.target
        if isinstance(evidence, HistoryClaimWal)
        and evidence.unconfirmed == HISTORY_UNCONFIRMED
        else evidence
    )
    if isinstance(legacy_target, ReadableTargetWal):
        facts["actual"] = _thaw(legacy_target.actual)
    elif isinstance(legacy_target, UnreadableTargetWal):
        facts["actual"] = None
    elif isinstance(evidence, HistoryClaimWal):
        facts["actual"] = None
    return evidence.verdict, facts


def _freeze_value(value):
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value):
    return None if value is None else _freeze_value(value)


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _history_claim_kind(item, facts):
    claim = item.get("history_append")
    if claim is None:
        return "claimless"
    if not isinstance(claim, dict):
        return "invalid"
    records = claim.get("records")
    prefix = claim.get("prefix")
    append_claim = claim.get("append")
    timestamps = claim.get("timestamps")
    if (
        claim.get("artifact") not in DURABILITIES
        or claim.get("encoding") != "json-sorted-keys-utf8-lf"
        or not isinstance(records, list)
        or len(records) != 2
        or not all(isinstance(entry, dict) for entry in records)
        or [entry.get("stage") for entry in records] != [PREPARED, COMMITTED]
        or any(entry.get("transaction") != facts["id"] for entry in records)
        or any(entry.get("adoption") != item.get("adoption") for entry in records)
        or not isinstance(prefix, dict)
        or type(prefix.get("length")) is not int
        or prefix["length"] < 0
        or not isinstance(prefix.get("digest"), str)
        or not isinstance(append_claim, dict)
        or not isinstance(timestamps, list)
        or timestamps != item.get("history_timestamps")
        or timestamps != [entry.get("at") for entry in records]
    ):
        return "invalid"
    expected = _expected_claim_pair(item, facts["id"])
    if expected is None:
        return "invalid"
    for actual, wanted in zip(records, expected):
        if set(actual) != set(wanted) | {"at", "version"}:
            return "invalid"
        if any(actual.get(field) != value for field, value in wanted.items()):
            return "invalid"
        if not isinstance(actual.get("at"), str) or actual.get("version") != item.get("version"):
            return "invalid"
    payload = b"".join(
        (json.dumps(entry, sort_keys=True) + "\n").encode("utf-8")
        for entry in records
    )
    if (
        append_claim.get("length") != len(payload)
        or append_claim.get("digest")
        != "sha256:" + hashlib.sha256(payload).hexdigest()
    ):
        return "invalid"
    return "valid"


def _expected_claim_pair(item, transaction_id):
    intention = item.get("intention")
    postimage = item.get("postimage")
    if not isinstance(intention, dict) or not isinstance(postimage, dict):
        return None
    required = {
        "schema": item.get("schema"),
        "adoption": item.get("adoption"),
        "run": item.get("run"),
        "durability": intention.get("durability"),
        "op": intention.get("op"),
        "purpose": intention.get("purpose"),
        "path": intention.get("path"),
        "transaction": transaction_id,
    }
    if any(value is None for value in required.values()):
        return None
    extra = {}
    mode = item.get("published_mode")
    if mode is None and postimage.get("kind") == "file":
        return None
    if mode is not None:
        extra["mode"] = mode
    if intention.get("note") is not None:
        extra["note"] = intention["note"]
    if postimage.get("kind") == "file":
        extra["preimage"] = item.get("preimage_blob")
        extra["postimage"] = postimage.get("digest")
        if item.get("prior_bytes") is not None:
            extra["prior_bytes"] = item["prior_bytes"]
    return tuple(
        {**required, "stage": stage, **extra}
        for stage in (PREPARED, COMMITTED)
    )


def _history_occurrence(
    item,
    facts,
    claim_kind,
    *,
    history_pair,
    histories,
):
    durability = (
        item.get("history_append", {}).get("artifact")
        if claim_kind == "valid"
        else facts["durability"]
    )
    if durability not in DURABILITIES:
        return "conflicting"
    records = () if histories is None else histories.get(durability, ())
    matching = tuple(
        entry for entry in records if entry.get("transaction") == facts["id"]
    )
    if claim_kind != "valid":
        if claim_kind == "invalid":
            return "conflicting"
        if histories is None or durability not in histories:
            return "unavailable"
        return "conflicting" if matching else "zero"

    raw = None
    if history_pair is not None:
        raw = (
            history_pair.repository.data
            if durability == REPO
            else history_pair.local.data
        )
    if raw is None:
        raw = b""
    claim = item["history_append"]
    prefix_length = claim["prefix"]["length"]
    prefix = raw[:prefix_length]
    expected_prefix = claim["prefix"]["digest"]
    if (
        len(raw) < prefix_length
        or "sha256:" + hashlib.sha256(prefix).hexdigest()
        != expected_prefix
    ):
        return "conflicting"
    payload = b"".join(
        (json.dumps(entry, sort_keys=True) + "\n").encode("utf-8")
        for entry in claim["records"]
    )
    tail = raw[prefix_length:]
    prepared = payload[: payload.find(b"\n") + 1]
    if not tail:
        return "zero"
    if tail.startswith(payload):
        if histories is None or durability not in histories:
            return "conflicting"
        claimed = tuple(claim["records"])
        if len(matching) != len(claimed) or any(
            dict(actual) != expected
            for actual, expected in zip(matching, claimed)
        ):
            return "conflicting"
        return "complete"
    if tail.startswith(prepared):
        if histories is None or durability not in histories:
            return "conflicting"
        expected = claim["records"][0]
        if len(matching) != 1 or dict(matching[0]) != expected:
            return "conflicting"
        return "prepared"
    if payload.startswith(tail):
        return "torn"
    return "conflicting"


def no_such_transaction(transaction_id):
    """The refusal for an id nothing in the log carries.

    One sentence for the resolver's probe before its materializing lock and
    its authoritative read under that lock, where a file can have gone since.
    Two spellings would drift, and this one is what `docs/reference/cli.md`
    prints.
    """
    return (
        f"there is no unresolved transaction {transaction_id}; "
        "'validated-memory journal --check' lists the ones there are. "
        "Nothing has been changed."
    )


def resolution_advice(transaction_id):
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
# subcommand set moves once, with the public write interface
# (docs/design/2026-09-01-the-journal-core.md §13).
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

    `kept` is where the bytes `--restore` discarded were parked, when there
    were any: a restore overwrites or removes whatever the path holds now,
    and no command of this plugin destroys bytes without leaving a copy
    behind. None for every resolution that discarded nothing.
    """

    transaction: str
    resolution: str
    location: str
    message: str | None = None
    kept: str | None = None
