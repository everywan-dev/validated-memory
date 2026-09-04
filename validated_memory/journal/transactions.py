"""The write-ahead log: the transaction file, and what a later run reads in it.

The vault directory it lives in, the four stages that write it, the reader
that answers for a damaged one rather than raising, and the classification
a recovery acts on and `journal --check` reports. Also the two frozen
results a caller renders: what recovery did with one transaction, and what
an operator's resolution did.

Not everything that knows the file's shape is here. `Run._complete` and
`Run._restore` read its `intention`, `preimage`, `postimage`,
`preimage_blob`, `mode`, `prior_bytes` and `run` fields directly, because
they rebuild the records the crashed run would have written; closing that
leak is a design change, not a move.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .. import __version__
from .durable import fsync_directory, install
from .operations import INTENTION_OPS
from .paths import own_directory, well_formed_state, current_state, satisfies
from .records import (
    DURABILITIES,
    OBSERVE,
    PREPARED,
    REPO,
    SCHEMA,
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

    Temporary, fsync, `install` -- the same durability shape every other
    atomic write in this package uses (`_bootstrap`, `_park_preimage`,
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
    already on disk, which only the caller (`Run.execute`) has read.
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


def mark_published(root, transaction_id):
    """Record, fsynced, that publication completed.

    Not decoration: a `replace` whose new bytes equal the old, an `append`
    of empty content, and every no-bytes intention (`create` of a
    directory, `link`) satisfy the preimage and postimage states at once,
    so recovery cannot always tell from the filesystem alone whether the
    mutation happened. This marker, fsynced after publication, is what
    turns that inference into a fact
    (docs/design/2026-09-01-the-journal-core.md §3).
    """
    path = _transaction_path(root, transaction_id)
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["stage"] = PUBLISHED
    _write_transaction_file(root, transaction_id, entry)


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
    path.unlink(missing_ok=True)
    fsync_directory(path.parent)


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
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            results.append({"id": transaction_id, "damaged": str(error)})
            continue
        except ValueError as error:
            # Bytes that are not text at all. `read_text` raises
            # `UnicodeDecodeError`, which is a `ValueError` and not an
            # `OSError`, so the handler above does not see it.
            results.append(
                {"id": transaction_id, "damaged": f"it is not valid UTF-8: {error}"}
            )
            continue
        try:
            entry = json.loads(text)
        except json.JSONDecodeError as error:
            results.append(
                {"id": transaction_id, "damaged": f"not valid JSON: {error.msg}"}
            )
            continue
        except ValueError as error:
            # Everything else the decoder refuses by value rather than by
            # syntax -- a nesting depth it will not follow, a number it
            # will not build. The same answer: this file is not a
            # transaction, and saying so is not a traceback.
            results.append(
                {"id": transaction_id, "damaged": f"it could not be decoded: {error}"}
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
_RECOVERABLE_VERDICTS = (VERDICT_COMPLETE, VERDICT_DISCARD, VERDICT_REMOVE)
RECOVERABLE = "recoverable"


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


def classify(root, item, adoption=None):
    """What recovery would do with one unresolved transaction, doing none of it.

    Returns `(verdict, facts)`. The verdict is `VERDICT_COMPLETE`, `VERDICT_DISCARD`,
    `VERDICT_REMOVE` or one of the three `RECOVERY_PROBLEMS`; `facts` carries what
    the file and the filesystem said, so the caller neither re-reads nor
    re-decides. This function writes nothing and is the ONE place the
    decision table below is expressed -- `Run.recover` acts on it and
    `journal --check` reports it, and a reader who has to compare two
    copies of a decision table is a reader who will find them disagreeing.

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
      `facts["actual"]` is None with the reason in `facts["reason"]`.
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

    schema = item.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool):
        return damaged("it names no schema, so nothing here knows how to read it")
    if schema > SCHEMA:
        return damaged(
            f"its schema is {schema} and this plugin reads up to {SCHEMA}; a "
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
    blob = item.get("preimage_blob")
    if blob is not None and not isinstance(blob, str):
        return damaged("its preimage reference is not a digest")

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

    try:
        actual = current_state(root, facts["path"])
    except OSError as error:
        # `current_state` swallows every `lstat` failure -- what it cannot
        # see at all is `absent` -- but a regular file it CAN see is read
        # for its digest, and that read raises. What is at the path cannot
        # be established, which is what `unknown` says; `actual` is None
        # because there is no state to report, and every caller reads
        # `reason` instead.
        facts["actual"] = None
        facts["reason"] = str(error)
        return PROBLEM_UNKNOWN, facts
    facts["actual"] = actual
    matches_post = satisfies(actual, postimage)
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


def no_such_transaction(transaction_id):
    """The refusal for an id nothing in the log carries.

    One sentence, in two places: `_run_resolve` asks the question before it
    opens a `Run`, so a tree with no adoption at all is not given one by a
    command that then says it changed nothing; the resolver asks it again
    under the lock, where a file can have gone since. Two spellings of it
    would drift, and this one is what `docs/reference/cli.md` prints.
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


def missing_resolution(root, transaction_id, resolution):
    """The refusal for an id no transaction file carries, or None to proceed.

    Asked before a `Run` exists, because building one adopts the tree: its
    lock creates `.validated-memory/` and its bootstrap installs
    `journal.jsonl`. An unknown id must not adopt a project under a refusal
    whose own last sentence says nothing has been changed. `lexists`, so a
    transaction file that is there but unreadable still reaches the
    resolver, which has a `damaged` answer for it.

    Where the file lives, and what the refusal says, stay inside this
    module: the caller asks whether there is anything to resolve, not how a
    transaction is stored.
    """
    if os.path.lexists(_transaction_path(root, transaction_id)):
        return None
    return Resolution(
        transaction_id,
        resolution,
        transaction_artifact(transaction_id),
        no_such_transaction(transaction_id),
    )
