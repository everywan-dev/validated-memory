"""The executor: adopting sessions, targeted resolution and shared mechanics.

The adopting session is the whole of
docs/design/2026-09-01-the-journal-core.md §4's execution protocol -- the
lock, path authorisation, the expected-state check, the preimage, the
transaction file, the publication and its durability barriers, the mode,
and both history records.

It is one class because the three ways a mutation is closed share the
protocol's later steps, not because they share all of them. Recovery
rebuilds the same record pair through `_record`, under the same lock and
by the same rule about a symlink's mode; resolution does that and also
puts bytes back through the same `_park_preimage`, `_publish` and
`_unpublish`. Neither authorises a path, checks an expected state or opens
a transaction file: those belong to execution alone.
"""

import json
import os
import secrets
import stat
from contextlib import contextmanager
from dataclasses import dataclass, replace as _replace
from pathlib import Path

from .durable import (
    VisibilityUnconfirmed,
    create_directory,
    create_exclusive,
    ensure_external_directory,
    ensure_owned_directory,
    install_bytes,
    read_file_snapshot,
    repair_symlink,
    remove_name,
    replace_symlink,
    republish_directory,
    republish_file,
)
from .fault import fault_at, rendezvous_at, sleep_at
from .lock import Lock
from .operations import (
    OUTCOME_APPLIED,
    OUTCOME_NOOP,
    OUTCOME_REFUSED,
    Outcome,
    Intention,
    link_to,
    replace_file,
)
from .paths import (
    ABSENT,
    DIRECTORY,
    FILE,
    SYMLINK,
    describe,
    own_directory,
    postimage_state,
    write_denied,
    authorise,
    current_state,
    satisfies,
)
from .records import (
    APPEND,
    COMMITTED,
    JOURNAL_FILENAME,
    LINK,
    LOCAL,
    OBSERVE,
    PREPARED,
    REPO,
    STAGES,
    VAULT_DIRNAME,
    JournalError,
    is_inside_path,
    append,
    artifact_name,
    digest,
    encode_records,
    history_snapshot,
    journal_path,
    new_id,
    read,
    record,
    existing_adoption_id,
    validate_snapshot,
    ensure_history_compatibility,
)
from .transactions import (
    ACCEPT,
    DISCARDED,
    CLEANUP_UNCONFIRMED,
    HISTORY_CLAIM_UNCONFIRMED,
    HISTORY_UNCONFIRMED,
    PROBLEM_DAMAGED,
    PROBLEM_DIVERGED,
    PROBLEM_UNKNOWN,
    PUBLISHED,
    RECOVERABLE,
    RECOVERED,
    REMOVED,
    RESOLUTIONS,
    RESTORE,
    RESTORE_UNCONFIRMED,
    TARGET_UNCONFIRMED,
    Recovery,
    Resolution,
    abort_transaction,
    classify,
    cleanup_private_duplicates,
    has_transaction,
    VERDICT_COMPLETE,
    VERDICT_DISCARD,
    mark_published,
    mark_history_append,
    mark_temporary_claim,
    mark_unconfirmed,
    no_such_transaction,
    open_transaction,
    open_transactions,
    historyless_transactions_message,
    read_transaction,
    VERDICT_REMOVE,
    VERDICT_TARGET_UNCONFIRMED,
    VERDICT_HISTORY_UNCONFIRMED,
    VERDICT_CLEANUP_UNCONFIRMED,
    VERDICT_RESTORE_UNCONFIRMED,
    resolution_advice,
    remove_transaction_file,
    transaction_artifact,
)


PREIMAGE_DIRNAME = "preimages"


def _exact_append_claim(durability, prefix, records):
    """Bind a complete record pair to its exact history prefix and bytes."""
    payload = encode_records(records)
    return {
        "artifact": durability,
        "prefix": {
            "length": len(prefix),
            "digest": digest(prefix),
        },
        "records": records,
        "timestamps": [entry["at"] for entry in records],
        "encoding": "json-sorted-keys-utf8-lf",
        "append": {
            "length": len(payload),
            "digest": digest(payload),
        },
    }


def _repair_complete_prefix(data, artifact):
    """Decode only complete JSONL records, leaving an EOF tail untouched."""
    records = []
    offset = 0
    for line in data.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        try:
            entry = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise JournalError(
                None,
                f"history has interior corruption before EOF: {error}",
                artifact,
            ) from error
        if not isinstance(entry, dict):
            raise JournalError(None, "history line is not an object", artifact)
        records.append(entry)
        offset += len(line)
    return records, offset, data[offset:]


def _repair_history_target(path, source_data, expected_data, expected_mode):
    """Atomically install a complete history while preserving a symlink name."""
    path = Path(path)
    link_target = os.readlink(path) if path.is_symlink() else None
    backing = path.resolve(strict=True) if link_target is not None else path
    before_stat = os.stat(backing)
    if not stat.S_ISREG(before_stat.st_mode):
        raise OSError("history target is not a regular file")
    if backing.read_bytes() != source_data:
        raise OSError("history snapshot changed during repair")
    requested = {
        item.strip()
        for item in os.environ.get("VALIDATED_MEMORY_PERSISTENCE_FAULT", "").split(",")
        if item.strip()
    }
    if f"swap-repair-history:{path.name}" in requested:
        raise OSError("history pathname changed during repair")
    if link_target is not None and os.readlink(path) != link_target:
        raise OSError("history symlink was retargeted during repair")
    after_stat = os.stat(backing)
    if (before_stat.st_dev, before_stat.st_ino) != (after_stat.st_dev, after_stat.st_ino):
        raise OSError("history backing file was replaced during repair")
    # The mode is a property of the resolved backing file, never of the link.
    if stat.S_IMODE(after_stat.st_mode) != expected_mode:
        raise OSError("history mode changed during repair")
    install_bytes(backing, expected_data, mode=expected_mode)
    return backing


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


def _preimages_dir(root):
    """Where parked preimages live: under the vault, one file per digest."""
    return own_directory(root, PREIMAGE_DIRNAME)


@dataclass(frozen=True)
class IdentityConfirmed:
    adoption: str
    repository: tuple[dict, ...]
    local: tuple[dict, ...]


@dataclass(frozen=True)
class IdentityRefused:
    artifact: str
    message: str


@dataclass(frozen=True)
class IdentityRetained:
    artifact: str
    message: str


@dataclass(frozen=True)
class AppendUnconfirmed:
    artifact: str
    retained_reason: str
    message: str


@dataclass(frozen=True)
class AppendReconfirmed:
    repository: tuple[dict, ...]
    local: tuple[dict, ...]


@dataclass(frozen=True)
class AppendReconfirmationRefused:
    artifact: str
    message: str


@dataclass(frozen=True)
class AppendReconfirmationRetained:
    artifact: str
    message: str


@dataclass(frozen=True)
class AppendReconfirmationNotApplicable:
    pass


class Run:
    """One invocation's journalling context.

    Holds the adoption id, this run's id and the paths either journal
    already knows about, and performs mutations through `execute`, which
    is the whole of docs/design/2026-09-01-the-journal-core.md §4's
    protocol and the only thing a caller needs. One session per invocation.

    Three methods, and no fourth: `observe` for a fact about the state
    adoption found, `execute` for every mutation, and `recover` for what an
    earlier run left open. No module outside this package can open a
    stage: a `prepared` record with no `committed` twin is still what
    `journal --check` reconciles, because a history written before this
    protocol can hold one, but nothing here writes another.

    `adopting_session` owns construction and holds `Lock` around both history
    reads, the injected identity transition and this session's complete caller
    scope. Every
    public method below also takes the lock itself: what serialises a write
    is the lock the write itself holds, not one a caller might happen to be
    inside.
    """

    def __init__(
        self,
        root,
        run,
        adoption,
        append_failure=None,
        append_reconfirmation=None,
    ):
        self.root = Path(root)
        self.run = run
        self.adoption = adoption
        self._append_failure = append_failure
        self._append_reconfirmation = append_reconfirmation

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
        Reading only the histories would then observe it as a fact about
        the state adoption found, which is permanent and has no inverse.
        Recovery normally puts the records back first,
        but a transaction it cannot resolve stays open, and this is what
        makes that state safe to run over.

        `_open_paths` -- the transaction id still open on each of those
        paths, which is what `_execute` refuses to write over. Only the
        affected path gates: the rest of the run proceeds, which is
        narrower than docs/design/2026-09-01-the-journal-core.md §8's "no
        mutating command proceeds", because a single-path transaction can
        be reasoned about piecewise and blocking everything would brick
        the session hook over one stale file.

        A damaged transaction file carries no intention and so names no
        path; it is skipped here and reported by `journal --check`.

        The one reader of a transaction file that does not go through
        `classify`, and deliberately: this runs at every adopting session, and
        classifying would `lstat` and digest each open transaction's path
        to answer a question nothing here asks. What is wanted is the path
        and the id, which are in the file.
        """
        self._seen = {
            (entry["durability"], entry["path"])
            for entry in records + local
        }
        self._open_paths = {}
        for item in open_transactions(self.root):
            intention = item.get("intention")
            if not isinstance(intention, dict):
                continue
            path = intention.get("path")
            durability = intention.get("durability")
            if isinstance(path, str) and isinstance(durability, str):
                # Spelled the way `authorise` returns it, because that is
                # what `execute` and `observe` look up: a repository path is
                # normalised and a local one is left exactly as written,
                # since the vault legitimately names paths this package may
                # not rewrite (ADR 0008). `classify` normalises the same
                # way, so a transaction gates the path its own recovery
                # reports.
                if durability == REPO:
                    path = Path(path).as_posix()
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
        two together, after publication, so a second call here would refuse
        a mutation that has already happened -- which leaves published bytes
        with no record at all, exactly the state reconciliation exists to
        avoid manufacturing on its own.

        What is left here is the cheap lexical guard, kept as a last line
        of defence: a `repo` record whose path is absolute or climbs out
        with `..` must never reach `append` even if some future caller
        forgets to ask `authorise` first, because `read` refuses exactly
        that record back.
        """
        if durability == REPO and not is_inside_path(path):
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

        Under the lock, like every other public method here. The `_seen`
        check and the append are a read-modify-write over a file another
        process may be appending to, and one that runs outside the lock is
        serialised by nothing at all: the executor's own mutations run
        inside it, so an observation racing one could file a fact about the
        state adoption found for a path the other was already changing.
        `Lock` is re-entrant, so the caller that already holds it
        (`init.run` holds one for its whole run) neither waits here nor has
        it released early.
        """
        with Lock(self.root):
            location = authorise(self.root, path, durability)
            if (durability, location) in self._seen:
                return
            append(
                [
                    self._record(
                        OBSERVE,
                        "init",
                        location,
                        durability,
                        COMMITTED,
                        note=note,
                    )
                ],
                self.root,
                durability,
            )
            self._seen.add((durability, location))

    def _park_preimage(self, path):
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
        blob = _preimages_dir(self.root) / reference.replace("sha256:", "")
        if blob.exists() and not _blob_matches(blob, reference):
            remove_name(blob)
        if not blob.exists():
            ensure_owned_directory(blob.parent, self.root)
            def verify(temporary):
                if not _blob_matches(temporary, reference):
                    raise OSError(
                        f"the preimage of {path} written to "
                        f"{temporary.as_posix()} does not digest to "
                        f"{reference}, the name it would be filed under; the "
                        "vault's copy of the bytes about to be overwritten "
                        "cannot be trusted"
                    )
            install_bytes(blob, data, verify=verify)
        return reference

    # --- the executor: one intention, one path, one outcome -------------------

    def execute(self, intention):
        """Perform one intention, wholly, and return an `Outcome`.

        This is the mutating surface
        docs/design/2026-09-01-the-journal-core.md §4 asks for: the
        executor owns the lock, path authorisation, the expected-state
        check, the preimage, the transaction file, the publication and its
        durability barriers, the mode, and both history records. No caller
        may do any of it for itself, because every caller that did got one
        of the steps wrong -- the six defects §1 measured are six
        spellings of the same protocol, reimplemented per call site.

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
           and docs/design/2026-09-01-the-journal-core.md §5 is explicit
           that a precondition failing before anything is prepared is a
           result the caller renders, never a line in a versioned file.
           `init` runs at every session start, so a refusal recorded in
           the history would be recorded for ever, once per session.
        4. **Return `noop` if the path is already in the state the intention
           would produce.** Not an optimisation: a transaction whose
           preimage and postimage are the same state cannot be recovered --
           nothing on the filesystem tells recovery whether it ran -- and a
           mutation that changes nothing is not a mutation to record. It is
           also not an `observe`: `observe` means "adoption found this",
           which is a different claim entirely (§4).
        5. **Refuse a target whose mode denies writing** (`write_denied`),
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
           A prepared transaction at its exact postimage is already
           recoverable because step 4 opens no no-op transaction. The marker
           proves publication after a later writer makes the target diverge.
        9. **Append both history records together**, carrying the
           transaction id and the mode. Together, because the history holds
           consummated facts only (§3): the write-ahead half of this
           protocol is the transaction file, not a `prepared` line in a
           versioned journal that no later run can ever close.
        10. **Resolve the transaction** -- the file leaves the disk -- and
            remember the path, so it can never be observed as pre-existing.

        The four `fault_at` points are the seams between those steps, so a
        test can kill the process at each and assert what is left.
        """
        if not isinstance(intention, Intention):
            # A type gate, not a vocabulary one, and it is load-bearing:
            # this method reads fields off whatever it is given, so any
            # object shaped like an intention reaches publication. One
            # carrying `op="observe"` was measured doing exactly that --
            # a published file and a `prepared`/`committed` observation,
            # a record pair nothing in this package writes and no reader
            # expects. Unreachable from the CLI seam, which is why no test
            # here covers it: only Python code holding an adopting session can do it,
            # and this refuses it.
            raise TypeError(
                "an intention is built by journal.create_file, "
                "create_directory, append_to_file or link_to; this is a "
                f"{type(intention).__name__}"
            )
        with Lock(self.root):
            return self._execute(intention)

    def _execute(self, intention):
        """`execute`'s body, with the lock already held."""
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
        try:
            actual = current_state(self.root, location)
        except OSError as error:
            # The expected-state check is the first thing that reads the
            # path, and a regular file this user may not read raises out of
            # the digest `current_state` takes. A refusal, like every other
            # precondition that cannot be met: nothing has been prepared, so
            # there is nothing to record and nothing to take back.
            return Outcome(
                OUTCOME_REFUSED,
                intention.op,
                intention.path,
                intention.durability,
                message=(
                    f"{location} could not be read, so nothing here can say "
                    f"what state it is in: {error}. Nothing has been written."
                ),
            )

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
                f"{resolution_advice(held)}. Nothing has been written.",
            )

        if not satisfies(actual, intention.expected):
            return self._refused(
                intention,
                actual,
                f"{location} is {describe(actual)}, and this "
                f"{intention.op} expects it to be "
                f"{describe(intention.expected)}. Nothing has been written.",
            )

        # A byte-publishing op may only ever land on nothing or on a
        # regular file. The check above already refuses every caller that
        # states what it expects to find; this one stands for the caller
        # that expects something a symlink can satisfy -- an `append`
        # naming a digest matches no symlink, but nothing in the shape of
        # an intention stops a future one from expecting less. Without it
        # an adopter's symlink reaches the replacement branch:
        # `os.replace` destroys the link itself, no preimage is parked for
        # it (`_park_preimage` sees a symlink, not a file), and the record
        # would say `replace` with a null preimage -- the exact trade
        # `init.BROKEN_SYMLINK` refuses everywhere else, made silently.
        if intention.content is not None and actual["kind"] not in (ABSENT, FILE):
            return self._refused(
                intention,
                actual,
                f"{location} is {describe(actual)}; refusing to replace it. "
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

        postimage = postimage_state(intention, actual, data)
        if satisfies(actual, postimage):
            return Outcome(
                OUTCOME_NOOP,
                intention.op,
                location,
                intention.durability,
                mode=actual.get("mode"),
            )

        denied = write_denied(self.root, location, actual)
        if denied is not None:
            return self._refused(intention, actual, denied)

        if intention.op == LINK:
            try:
                ensure_external_directory(
                    (self.root / location).parent, self.root
                )
            except VisibilityUnconfirmed as cause:
                error = JournalError(
                    None,
                    f"the harness parent may be visible, but its durability "
                    f"is unconfirmed: {cause}. No link transaction was opened",
                    artifact_name(LOCAL),
                )
                error.visibility_unconfirmed = True
                raise error from cause
            except OSError as error:
                return self._refused(
                    intention,
                    actual,
                    f"the harness parent could not be prepared: {error}. "
                    "Nothing has been written.",
                )

        blob = None
        if actual["kind"] == FILE:
            try:
                blob = self._park_preimage(location)
            except VisibilityUnconfirmed as error:
                return self._refused(
                    intention,
                    actual,
                    f"the preimage store could not be trusted, so the "
                    f"mutation was not attempted: {error}. The target and "
                    "history were not changed.",
                )
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

        # The line this method turns on. Above it are ten returns that
        # left nothing behind -- seven `_refused`, two refusals built
        # before there is an `actual` to report, and the no-op -- and
        # below it three that have a transaction file on disk to close
        # first, which is why they are `_aborted`. A refusal added below
        # this line that does not close its transaction leaves the path
        # gated for ever against a mutation that never happened.
        try:
            transaction = open_transaction(
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
        except (OSError, VisibilityUnconfirmed) as error:
            return self._refused(
                intention,
                actual,
                f"the transaction store could not be trusted, so the "
                f"mutation was not attempted: {error}. The target and "
                "history were not changed.",
            )
        fault_at("after-transaction")
        sleep_at("during-lock")

        # The state as it is NOW, not as it was before the preimage was
        # parked and the transaction fsynced. Compared whole rather than
        # through `satisfies`: the transaction file has already committed to
        # `actual` as this mutation's preimage and `_publish` is about to
        # carry its mode over, so anything but `actual` invalidates both.
        try:
            again = current_state(self.root, location)
        except OSError as error:
            # An unreadable path is a refusal here as it is everywhere
            # above, but there is a transaction by now, so it closes the
            # way the state mismatch below closes: `aborted` with the
            # reason, and then removed.
            return self._aborted(
                transaction,
                intention,
                actual,
                f"{location} could not be read while its mutation was being "
                f"prepared: {error}. Nothing has been published.",
            )
        if again != actual:
            return self._aborted(
                transaction,
                intention,
                again,
                f"{location} changed while its mutation was being prepared: "
                f"it is {describe(again)} now and was {describe(actual)} "
                "when this run checked. Nothing has been written.",
            )

        temporary_path = None
        if intention.op == LINK or (
            data is not None and actual["kind"] != ABSENT
        ):
            target = self.root / location
            forced_name = os.environ.get("VALIDATED_MEMORY_SYMLINK_TEMP_NAME")
            temporary_path = target.parent / (
                forced_name
                if forced_name and intention.op == LINK
                else f".{target.name}.{secrets.token_hex(16)}.tmp"
            )
            temporary_claim = {
                "role": "target-staging",
                "name": temporary_path.name,
                "target": location,
                "transaction": transaction,
                "adoption": self.adoption,
                "kind": "symlink" if intention.op == LINK else "regular-file",
                "digest": digest(
                    (intention.target or "").encode("utf-8")
                    if intention.op == LINK
                    else data
                ),
                "mode": preimage_mode if intention.op != LINK else None,
            }
            try:
                mark_temporary_claim(self.root, transaction, temporary_claim)
            except (OSError, VisibilityUnconfirmed) as error:
                raise self._uncertainty(
                    transaction,
                    TARGET_UNCONFIRMED,
                    f"the target staging claim for {location} could not be "
                    f"confirmed: {error}",
                ) from error
        try:
            mode = self._publish(
                intention,
                location,
                None if actual["kind"] == ABSENT else actual["mode"],
                data,
                temporary_path=temporary_path,
            )
        except VisibilityUnconfirmed as error:
            raise self._uncertainty(
                transaction,
                TARGET_UNCONFIRMED,
                f"the intended state of {location} is visible, but its "
                f"durability is unconfirmed: {error}",
            ) from error
        except OSError as error:
            return self._aborted(
                transaction,
                intention,
                again,
                f"{location} could not be written: {error}. Nothing has "
                "been published.",
            )
        fault_at("after-publish")
        try:
            mark_published(self.root, transaction, published_mode=mode)
        except (OSError, VisibilityUnconfirmed) as error:
            raise self._uncertainty(
                transaction,
                TARGET_UNCONFIRMED,
                f"the intended state of {location} is visible and its target "
                "barrier completed, but the published marker's durability "
                f"is unconfirmed: {error}",
            ) from error
        fault_at("after-published")

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
        try:
            history_records = [
                self._record(
                    intention.op,
                    intention.purpose,
                    location,
                    intention.durability,
                    stage,
                    **fields,
                )
                for stage in (PREPARED, COMMITTED)
            ]
            prefix = history_snapshot(self.root, intention.durability)
            claim = _exact_append_claim(
                intention.durability,
                prefix,
                history_records,
            )
            mark_history_append(self.root, transaction, claim)
        except (OSError, VisibilityUnconfirmed) as error:
            raise self._uncertainty(
                transaction,
                HISTORY_CLAIM_UNCONFIRMED,
                f"the exact history append proof for {location} could not be "
                f"durably recorded, so no history bytes were appended: {error}",
            ) from error
        try:
            append(history_records, self.root, intention.durability)
        except (OSError, VisibilityUnconfirmed) as error:
            if self._append_failure is None:
                raise
            retained = self._append_failure(
                transaction,
                intention.durability,
                error,
            )
            if not isinstance(retained, AppendUnconfirmed):
                raise TypeError("append failure callback returned an unknown result")
            raise self._uncertainty(
                transaction,
                HISTORY_UNCONFIRMED,
                retained.message,
                retained_reason=retained.retained_reason,
                artifact=retained.artifact,
                complete=True,
            ) from error
        fault_at("after-history")

        try:
            mark_unconfirmed(
                self.root,
                transaction,
                CLEANUP_UNCONFIRMED,
                "target and history confirmed; transaction cleanup pending",
            )
        except (OSError, VisibilityUnconfirmed) as error:
            raise self._uncertainty(
                transaction,
                CLEANUP_UNCONFIRMED,
                f"{location} and its history are confirmed, but the cleanup "
                f"fact could not be confirmed: {error}",
            ) from error
        try:
            remove_transaction_file(self.root, transaction)
        except VisibilityUnconfirmed as error:
            raise self._uncertainty(
                transaction,
                CLEANUP_UNCONFIRMED,
                f"{location} and its history are confirmed, but transaction "
                f"cleanup durability is unconfirmed: {error}",
            ) from error
        self._seen.add((intention.durability, location))
        return Outcome(
            OUTCOME_APPLIED,
            intention.op,
            location,
            intention.durability,
            transaction=transaction,
            mode=mode,
        )

    def _uncertainty(
        self,
        transaction,
        phase,
        message,
        *,
        retained_reason=None,
        artifact=None,
        complete=False,
    ):
        """Retain a phase fact and build one truthful gating journal error."""
        secondary = ""
        try:
            mark_unconfirmed(
                self.root,
                transaction,
                phase,
                message if retained_reason is None else retained_reason,
            )
        except (OSError, VisibilityUnconfirmed) as error:
            secondary = (
                "; recording that recovery fact was itself unconfirmed: "
                f"{error}. Every recovery artifact that remains was retained"
            )
        error = JournalError(
            None,
            f"{message}"
            + (
                ""
                if complete
                else f"; transaction {transaction} is retained for recovery"
            )
            + secondary,
            artifact or transaction_artifact(transaction),
        )
        error.visibility_unconfirmed = True
        return error

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
        try:
            abort_transaction(self.root, transaction, message)
        except VisibilityUnconfirmed as error:
            return self._refused(
                intention,
                actual,
                f"{message} The target was not published, but recording the "
                f"aborted decision is unconfirmed: {error}; transaction "
                f"{transaction} remains for recovery.",
            )
        try:
            remove_transaction_file(self.root, transaction)
        except VisibilityUnconfirmed as error:
            return self._refused(
                intention,
                actual,
                f"{message} The target was not published and the transaction "
                f"is aborted, but cleanup is unconfirmed: {error}; its "
                "artifact was retained for retry.",
            )
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

    def _publish(self, intention, location, replacing_mode, data, temporary_path=None):
        """Put the new state on disk, atomically and durably; return its mode.

        `replacing_mode` is the mode of the node this is about to write
        over, and `None` says there is nothing there. It is the whole of
        what publication needs to know about the current state, which is
        why it is not the state itself: `_restore` publishes the
        PREIMAGE's mode over whatever a third party left at the path, and
        a state dict passed there would have to be invented.

        Publication is not one primitive, and treating it as one is what
        docs/design/2026-09-01-the-journal-core.md §6 refuses. Four
        shapes, chosen by what is expected to be there rather than by the
        op's name:

        - **A directory** -- `os.mkdir`, never `parents=True`. Creating an
          ancestor nobody asked for is a second mutation with no intention
          and no record, so a missing parent is a refusal that names it.
        - **A creation over nothing** (`replacing_mode is None`) --
          `O_CREAT | O_EXCL`, which
          fails if the name is taken. §6 promises "a strong no-replace
          guarantee for a creation, because the primitive exists", and
          check-then-`os.replace` is not that primitive: a third party
          creating the file between the re-read and here would be
          overwritten by a record that says `create`. A missing
          parent is refused rather than built, for the reason the directory
          branch gives: a run that has already refused to create a
          directory must not go on to create it as somebody else's parent.
        - **A replacement** -- a temporary, fsynced, given the TARGET's mode
          (§7: the install copies the mode onto the temporary before the
          rename, or the adopter's 0640 comes back 0644), then
          `os.replace`, then the directory fsync `install` performs.
        - **A symlink** -- built under a pid-named temporary and `os.replace`d
          over the path, so the link is never absent for an instant. An
          `unlink` then `symlink_to` leaves a session with no memory at all
          if it dies in between. It is the one shape that reports no mode:
          a symlink's is 0777 and means nothing (see below). It is also the
          one shape that still builds its parent: the only link this
          plugin publishes is the harness one, whose path is absolute and
          outside the adopter root by construction (ADR 0008), so the
          directory it goes in belongs to the harness rather than to the
          project and is not a mutation of this tree for a record to
          describe. Inside the root, a parent is an item with an intention
          and a record of its own.

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
            create_directory(target)
        elif intention.op == LINK:
            replace_symlink(
                target, intention.target, temporary=temporary_path,
                crash_storage=temporary_path is not None,
            )
        elif replacing_mode is None:
            if not target.parent.is_dir():
                raise FileNotFoundError(
                    f"its parent directory "
                    f"{Path(location).parent.as_posix()} does not exist"
                )
            create_exclusive(target, data)
        else:
            install_bytes(
                target, data, mode=replacing_mode, temporary=temporary_path,
                crash_storage=temporary_path is not None,
            )
        if intention.op == LINK:
            # A symlink has no mode this protocol may carry, for the same
            # reason its preimage mode is dropped above: `lstat` reports
            # 0777 on every platform this runs on, nobody chose it, and a
            # record carrying it would tell a reversal to `chmod` the
            # DIRECTORY the link points at. None, and the records omit the
            # field entirely.
            return None
        try:
            return stat.S_IMODE(os.lstat(target).st_mode)
        except OSError:
            # Published, but its mode cannot be read back. The records are
            # written without one rather than with a guess: a reversal
            # restoring a mode nobody measured is worse than one that knows
            # it was never told.
            return None

    # --- recovery and resolution: closing what an earlier run left open -------

    def recover(self):
        """Resolve every transaction an earlier run left behind; report each.

        Explicit, and not a side effect of `__init__`: a constructor that
        completes half-finished mutations is a constructor with a
        filesystem's worth of failure modes, and targeted resolution must
        not recover -- an operator closing one transaction by hand must not
        have the others silently closed underneath.

        `init` calls this immediately after opening its adopting scope, under the
        run-wide lock and BEFORE its own first intention. Recovery only ever
        completes or closes what an earlier run began, and unlinks the
        files that said so, so it reduces what is on disk rather than adding
        to it -- and an open transaction on `.gitignore` itself is settled
        before the new `.gitignore` intention is formed.

        Idempotent over the same residue, in both directions. A transaction
        this resolves leaves the disk, so a second pass never sees it again;
        and completing one appends only the history records that are not
        already there, checked by transaction id AND stage, because a crash
        between `append` and `remove_transaction_file` leaves a `published`
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
                for item in open_transactions(self.root)
            ]
            self._survey(read(self.root, REPO), read(self.root, LOCAL))
            return results

    def _recover_one(self, item, histories):
        """Act on `classify`'s verdict for one transaction; return a `Recovery`.

        `histories` caches each journal's records across a recovery pass, so
        a tree with several open transactions reads each file once. A
        caller completing a single transaction passes none.
        """
        verdict, facts = classify(self.root, item, self.adoption)
        transaction_id = facts["id"]
        path = facts["path"]
        durability = facts["durability"]

        if verdict == PROBLEM_DAMAGED:
            return Recovery(
                transaction_id,
                path,
                durability,
                problem=PROBLEM_DAMAGED,
                message=(
                    f"damaged transaction {transaction_id}: {facts['problem_reason']}; "
                    f"nothing here can say what it did, so "
                    f"{transaction_artifact(transaction_id)} "
                    "is left for inspection"
                ),
            )

        if verdict == VERDICT_REMOVE:
            reason = facts["abort_reason"]
            try:
                remove_transaction_file(self.root, transaction_id)
            except VisibilityUnconfirmed as error:
                return Recovery(
                    transaction_id,
                    path,
                    durability,
                    problem=PROBLEM_UNKNOWN,
                    message=(
                        f"transaction {transaction_id} remains aborted, but "
                        f"removal durability is unconfirmed: {error}; its "
                        "artifact was republished for another cleanup attempt"
                    ),
                )
            said = f" ({reason})" if reason is not None else ""
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

        if verdict == VERDICT_DISCARD:
            try:
                remove_transaction_file(self.root, transaction_id)
            except VisibilityUnconfirmed as error:
                return Recovery(
                    transaction_id,
                    path,
                    durability,
                    problem=PROBLEM_UNKNOWN,
                    message=(
                        f"transaction {transaction_id} still has its preimage, "
                        f"but removal durability is unconfirmed: {error}; its "
                        "artifact was republished for another cleanup attempt"
                    ),
                )
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

        if verdict == VERDICT_TARGET_UNCONFIRMED:
            try:
                self._republish_target(facts)
                mark_published(self.root, transaction_id)
            except (OSError, VisibilityUnconfirmed, JournalError) as error:
                return self._recovery_uncertain(facts, TARGET_UNCONFIRMED, error)
            try:
                appended = self._complete(facts, histories)
            except (OSError, VisibilityUnconfirmed, JournalError) as error:
                return self._recovery_uncertain(facts, HISTORY_UNCONFIRMED, error)
            try:
                self._remove_after_confirmed_history(facts)
            except (OSError, VisibilityUnconfirmed, JournalError) as error:
                return self._recovery_uncertain(facts, CLEANUP_UNCONFIRMED, error)
            return Recovery(
                transaction_id,
                path,
                durability,
                action=RECOVERED,
                appended=appended,
                message=(
                    f"transaction {transaction_id} republished and confirmed "
                    f"{path}; its history was completed exactly once"
                ),
            )

        if verdict == VERDICT_HISTORY_UNCONFIRMED:
            if self._append_reconfirmation is None:
                raise RuntimeError("append reconfirmation callback is unavailable")
            result = self._append_reconfirmation(self.root, item)
            if isinstance(result, AppendReconfirmationRefused):
                return Recovery(
                    transaction_id,
                    result.artifact,
                    durability,
                    problem=PROBLEM_UNKNOWN,
                    message=result.message,
                )
            if isinstance(result, AppendReconfirmationRetained):
                error = JournalError(None, result.message, result.artifact)
                error.visibility_unconfirmed = True
                raise error
            if not isinstance(result, AppendReconfirmed):
                raise TypeError(
                    "append reconfirmation callback returned an unknown result"
                )
            histories[REPO] = list(result.repository)
            histories[LOCAL] = list(result.local)
            try:
                self._remove_after_confirmed_history(facts)
            except (OSError, VisibilityUnconfirmed, JournalError) as error:
                retained = JournalError(
                    None,
                    "the selected append range was coherently confirmed, but "
                    f"transaction cleanup is incomplete: {error}. Transaction "
                    f"{transaction_id} was retained. The append is already "
                    "confirmed and must not be repeated. Preserve the retained "
                    "transaction and rerun init",
                    artifact_name(durability),
                )
                retained.visibility_unconfirmed = True
                raise retained from error
            return Recovery(
                transaction_id,
                path,
                durability,
                action=RECOVERED,
                appended=False,
                message=(
                    f"transaction {transaction_id} reconfirmed "
                    f"its complete {artifact_name(durability)} history"
                ),
            )

        if verdict == VERDICT_CLEANUP_UNCONFIRMED:
            try:
                remove_transaction_file(self.root, transaction_id)
            except (OSError, VisibilityUnconfirmed) as error:
                return self._recovery_uncertain(facts, CLEANUP_UNCONFIRMED, error)
            return Recovery(
                transaction_id,
                path,
                durability,
                action=REMOVED,
                message=(
                    f"transaction {transaction_id} had confirmed target and "
                    "history; cleanup is now confirmed"
                ),
            )

        if verdict == VERDICT_RESTORE_UNCONFIRMED:
            try:
                self._republish_restore(facts)
                remove_transaction_file(self.root, transaction_id)
            except (OSError, VisibilityUnconfirmed, JournalError) as error:
                return self._recovery_uncertain(
                    facts, RESTORE_UNCONFIRMED, error
                )
            return Recovery(
                transaction_id,
                path,
                durability,
                action=REMOVED,
                message=(
                    f"transaction {transaction_id} has its exact restored "
                    "preimage confirmed and its WAL removed"
                ),
            )

        if verdict == VERDICT_COMPLETE:
            if self._append_reconfirmation is None:
                raise RuntimeError("append reconfirmation callback is unavailable")
            boundary = self._append_reconfirmation(self.root, item)
            if isinstance(boundary, AppendReconfirmationRefused):
                return Recovery(
                    transaction_id,
                    boundary.artifact,
                    durability,
                    problem=PROBLEM_UNKNOWN,
                    message=boundary.message,
                )
            if not isinstance(boundary, AppendReconfirmationNotApplicable):
                raise TypeError(
                    "append reconfirmation callback returned an unknown result"
                )
            try:
                appended = self._complete(facts, histories)
            except VisibilityUnconfirmed as error:
                return self._recovery_uncertain(
                    facts, HISTORY_UNCONFIRMED, error
                )
            try:
                self._remove_after_confirmed_history(facts)
            except (OSError, VisibilityUnconfirmed) as error:
                return self._recovery_uncertain(
                    facts, CLEANUP_UNCONFIRMED, error
                )
            return Recovery(
                transaction_id,
                path,
                durability,
                action=RECOVERED,
                appended=appended,
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
                f"is {describe(facts['actual'])} now and not what was "
                "published; nothing here can say whether that is wanted -- "
                f"{resolution_advice(transaction_id)}"
            )
        elif facts["actual"] is None and facts["stage"] == PUBLISHED:
            # The stage is most of what is known about a transaction whose
            # path cannot be read, and the two stages are not the same
            # trouble: a `prepared` one may never have run, where a
            # `published` one certainly did and only its records were lost.
            # One sentence for both would tell the milder story about the
            # graver state, so there are two.
            message = (
                f"transaction {transaction_id} published {path}, and {path} "
                f"cannot be read: {facts['problem_reason']}; nothing here can say "
                "whether what it published is still there -- "
                f"{resolution_advice(transaction_id)}"
            )
        elif facts["actual"] is None:
            message = (
                f"transaction {transaction_id} prepared a mutation of {path}, "
                f"and {path} cannot be read: {facts['problem_reason']}; nothing here "
                "can say whether it ran -- "
                f"{resolution_advice(transaction_id)}"
            )
        elif facts.get("unconfirmed") == TARGET_UNCONFIRMED:
            message = (
                f"transaction {transaction_id} left {path} with unconfirmed "
                "target durability, but it no longer has the exact postimage "
                "recorded by the transaction; recovery left the WAL in place"
            )
        else:
            message = (
                f"transaction {transaction_id} prepared a mutation of {path}, "
                f"and {path} is {describe(facts['actual'])} now, which is "
                "neither the state it was to change from nor the one it was "
                "to change to; nothing here can say whether it ran -- "
                f"{resolution_advice(transaction_id)}"
            )
        return Recovery(
            transaction_id, path, durability, problem=verdict, message=message
        )

    def _republish_target(self, facts):
        """Establish and confirm a fresh namespace operation for the postimage."""
        target = self.root / facts["path"]
        kind = facts["postimage"]["kind"]
        if kind == FILE:
            data, mode = read_file_snapshot(target)
            expected = facts["postimage"]
            if (
                digest(data) != expected["digest"]
                or ("mode" in expected and mode != expected["mode"])
            ):
                raise JournalError(
                    None,
                    f"{facts['path']} no longer has its full validated "
                    "postimage state",
                    transaction_artifact(facts["id"]),
                )
            republish_file(target, data, mode, "target")
        elif kind == DIRECTORY:
            republish_directory(target)
        elif kind == SYMLINK:
            replace_symlink(target, facts["postimage"]["target"])
        else:
            raise JournalError(
                None,
                f"the exact postimage of {facts['path']} is absent, so there "
                "is no visible target to republish",
                transaction_artifact(facts["id"]),
            )

    def _republish_restore(self, facts):
        """Confirm a fresh namespace operation for an operator's restore."""
        target = self.root / facts["path"]
        kind = facts["preimage"]["kind"]
        if kind == FILE:
            data, mode = read_file_snapshot(target)
            expected = facts["preimage"]
            if (
                digest(data) != expected["digest"]
                or ("mode" in expected and mode != expected["mode"])
            ):
                raise JournalError(
                    None,
                    f"{facts['path']} no longer has its full validated "
                    "preimage state",
                    transaction_artifact(facts["id"]),
                )
            republish_file(target, data, mode, "restore")
        elif kind == SYMLINK:
            replace_symlink(target, facts["preimage"]["target"])
        elif kind == ABSENT:
            remove_name(target)
        else:
            raise JournalError(
                None,
                f"the restored state of {facts['path']} cannot be republished",
                transaction_artifact(facts["id"]),
            )

    def _remove_after_confirmed_history(self, facts):
        """Record cleanup intent, then confirm removal of the WAL name."""
        mark_unconfirmed(
            self.root,
            facts["id"],
            CLEANUP_UNCONFIRMED,
            "target and history confirmed; transaction cleanup pending",
        )
        remove_transaction_file(self.root, facts["id"])

    def _recovery_uncertain(self, facts, phase, error):
        """Retain a failed recovery phase and return one actionable problem."""
        journal_error = self._uncertainty(
            facts["id"],
            phase,
            f"recovery of {facts['path']} could not confirm {phase}: {error}",
        )
        return Recovery(
            facts["id"],
            facts["path"],
            facts["durability"],
            problem=PROBLEM_UNKNOWN,
            message=journal_error.message,
        )

    def _complete(self, facts, histories=None, published=None):
        """Append whichever of the mutation's two records is not there yet.

        Returns whether anything was appended, so the caller can say which
        of the two shapes of `completed` this was.

        The records are rebuilt from `classify`'s facts and the state the
        mutation PUBLISHED. Recovery passes nothing for `published` and the
        state is the one the path is in now, which `classify` has just
        proven is that postimage; `_resolve_one` passes the transaction's
        own postimage, because a `diverged` transaction did publish and
        something wrote over it afterwards, and the mode of that later
        write is not a fact about the mutation. `op`, `purpose`, `path`,
        `durability` and `note` come from the intention; `preimage` (the
        parked blob's reference) and `prior_bytes` from the facts, already
        type-checked there;
        `postimage` from the postimage STATE's own digest, which is the
        same value `_execute` wrote; `mode` from the published node, unless
        that node is a symlink, whose mode is 0777 and means nothing. Both
        halves carry the `transaction`, exactly as `_execute` writes them,
        because that id is the only thing that survives the file to say the
        two lines are one act. `run` is the crashed run's: it is the run
        that wrote the bytes.

        `adoption` is NOT taken from the file. One project has one adoption
        id (`_adoption_id`), this run has already established which, and a
        record filed under any other would attach a mutation of this tree to
        somebody else's history.

        `missing` is ordered by `STAGES`, so the two are appended
        `prepared` then `committed`, as `_execute` writes them. A history
        already holding the `committed` half alone -- which no writer in
        this package can produce, only a hand edit or a torn merge -- would
        therefore get its `prepared` half appended AFTER it, and
        `reconcile`, which pairs in file order, would go on reporting it as
        unfinished. That is the honest outcome: the file order of an
        append-only journal is not something recovery may rearrange.
        """
        transaction_id = facts["id"]
        durability = facts["durability"]
        location = facts["path"]
        intention = facts["intention"]
        postimage = facts["postimage"]
        if histories is None:
            histories = {}
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
        state = facts["actual"] if published is None else published
        mode = state.get("mode")
        # A symlink's mode is 0777 on every platform this runs on and means
        # nothing, so it is not a fact these records may carry -- the same
        # rule `_execute` applies to a link's preimage mode and to the mode
        # `_publish` reports. Recovery rebuilds the records `_execute` would
        # have written, and that includes the field it would have omitted.
        if mode is not None and state.get("kind") != SYMLINK:
            fields["mode"] = mode
        if intention.get("note") is not None:
            fields["note"] = intention["note"]
        if postimage["kind"] == FILE and "digest" in postimage:
            # A byte-publishing mutation, and the only kind whose records
            # carry digests. `preimage` is the blob reference, null for a
            # path that did not exist -- the same null `_execute` writes,
            # and the same one that distinguishes "undo by truncating" from
            # "undo by removing".
            fields["preimage"] = facts["preimage_blob"]
            fields["postimage"] = postimage["digest"]
            if facts["prior_bytes"] is not None:
                fields["prior_bytes"] = facts["prior_bytes"]
        built = [
            self._record(
                intention["op"],
                intention["purpose"],
                location,
                durability,
                stage,
                run=facts["run"],
                **fields,
            )
            for stage in missing
        ]
        append(built, self.root, durability)
        records.extend(built)
        return True

    def _resolve_one(self, item, resolution):
        """Resolve the already-read named transaction under the caller's lock."""
        transaction_id = item["id"]
        artifact = transaction_artifact(transaction_id)
        verdict, facts = classify(self.root, item, self.adoption)
        if verdict == PROBLEM_DAMAGED:
            return Resolution(
                transaction_id,
                resolution,
                artifact,
                f"transaction {transaction_id} is damaged "
                f"({facts['problem_reason']}), so nothing here can say what it did "
                f"or what to record about it; inspect {artifact} and remove "
                "it by hand. Nothing has been changed.",
            )

        if verdict not in (PROBLEM_DIVERGED, PROBLEM_UNKNOWN):
            # Recovery can account for this one, so an operator must not
            # close it: `--accept` and `--abandon` would write an `observe`
            # about a path the PLUGIN created and throw away the mutation's
            # record pair for ever, and `--restore` would undo a mutation
            # the next run is about to finish recording. The three flags
            # exist for the states nothing can decide, and this is not one.
            return Resolution(
                transaction_id,
                resolution,
                facts["path"],
                f"transaction {transaction_id} on {facts['path']} is "
                f"{RECOVERABLE}: the next 'validated-memory init' closes it "
                "on its own, and closing it by hand would lose the record "
                "of the mutation it carries. --accept, --restore and "
                "--abandon are for a transaction recovery cannot account "
                "for. Nothing has been changed.",
            )

        if facts["actual"] is None:
            # `unknown` because the path could not be read at all. All three
            # flags need to know what is there: `--accept` records the state
            # it accepted, `--abandon` records that the path was left as
            # found, and `--restore` parks what it is about to discard. None
            # of them may be answered out of a state nothing established.
            return Resolution(
                transaction_id,
                resolution,
                facts["path"],
                f"{facts['path']} could not be read ({facts['problem_reason']}), so "
                "nothing here can say what state is being closed over. "
                "Nothing has been changed.",
            )

        if resolution == RESTORE:
            return self._restore(facts)

        # `diverged` is a `published` transaction: its bytes reached the
        # disk and only the two history records were lost, and what the
        # verdict adds is that something wrote the path AFTERWARDS. The
        # mutation happened, so it is a fact about this project whichever
        # way the operator closes it, and its own pair goes into the
        # history FIRST -- the same pair recovery would have appended, the
        # crashed run's id and all -- with the resolution's `observe` after
        # it. Closing with the observe alone would leave published bytes
        # with no record at all, in an append-only history where nothing
        # puts them back: the lie the executor exists to stop manufacturing.
        # Idempotent per record, as recovery is -- `_complete` appends only
        # the halves that are not already there -- and the postimage is
        # passed because the mode of whatever wrote the path afterwards is
        # not a fact about the mutation. `unknown` gets no pair: it is a
        # `prepared` transaction whose path matches neither of its states,
        # and nothing there says the mutation ever ran.
        if verdict == PROBLEM_DIVERGED:
            self._complete(facts, published=facts["postimage"])

        # `classify` read the path under this same lock, so this is the
        # state the verdict was reached on and not a second, later reading.
        found = facts["actual"]["kind"]
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
        remove_transaction_file(self.root, transaction_id)
        return Resolution(transaction_id, resolution, facts["path"])

    def _restore(self, facts):
        """Put the preimage back, or refuse and leave everything alone.

        Two refusals come first, and both leave the transaction open.

        A transaction whose records are ALREADY in the history is not one
        recovery may reverse. The `committed` record means the mutation
        happened and is history; taking the bytes back without taking the
        record back would make the journal describe a state that is not
        there, and the record cannot be taken back -- the history is
        append-only. `--accept` or `--abandon` is the answer.

        A preimage blob that is missing, or whose bytes do not digest to the
        name it is filed under, refuses. This is the case
        docs/design/2026-09-01-the-journal-core.md §10 says must never be
        confused with the other one: for a CLOSED history record a
        missing blob is normal, because the journal travels and the vault
        does not, and it means only that this clone cannot reverse that
        mutation. For an OPEN transaction the blob is the sole copy of
        the bytes the plugin was about to overwrite, parked and verified
        moments before -- its absence is a damaged log, and writing
        something else over the path would be writing wrong bytes.

        The publication is the executor's own (`_publish`), so a restore is
        as atomic and as durable as the mutation it reverses. The mode comes
        from the transaction file, which recorded the preimage's own -- not
        from whatever is at the path now, which may be the plugin's
        replacement or a third party's file. The read-only bit is NOT
        consulted: `write_denied` exists so a mutation never quietly
        overwrites what an adopter marked unwritable, and this is the
        opposite -- an operator's explicit instruction to put that adopter's
        own bytes back.

        What the restore DISCARDS is parked before it is discarded. The
        operator has chosen to throw the current state away, and that
        choice is honoured -- but a regular file at the path is bytes
        somebody wrote, and no command here destroys bytes without leaving
        a copy: they go into the same content-addressed, verified preimage
        store the executor parks into, and the success line names the blob.
        That covers both directions symmetrically -- putting a file back
        over them, and taking the path away because the preimage says it
        was never there. A symlink is unlinked with nothing parked and its
        target is NOT kept anywhere: a link is a name and a target rather
        than bytes, there is nothing for a content-addressed store to hold,
        and the bytes it resolved to belong to the path it named, which
        this does not touch. The transaction file does not hold that target
        either -- what it records is the PREIMAGE's, and a link a third
        party put there afterwards is exactly the case `--restore` is being
        asked about. Where the target is written down is the finding that
        reported the divergence: `journal --check` and `init` both describe
        the state they found, symlinks by their target. A directory has no
        bytes of its own, and `_unpublish` refuses a non-empty one rather
        than parking anything.

        Nothing is recorded. A path returned to the state a record would
        have described the departure from is not a fact about the project.
        The parked blob is not a record either: it is a copy in the vault,
        which the vault is for.
        """
        transaction_id = facts["id"]
        location = facts["path"]
        durability = facts["durability"]
        # The state `classify` reached its verdict on, read under this
        # same lock -- what is about to be discarded.
        present = facts["actual"]

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
        replacing_mode = None
        if kind == FILE:
            reference = facts["preimage_blob"]
            if reference is None:
                return refuse(
                    f"transaction {transaction_id} says {location} was a "
                    "file and names no preimage for it, so the bytes it was "
                    "about to overwrite were never parked; this log is "
                    "damaged. Nothing has been restored."
                )
            blob = _preimages_dir(self.root) / reference.replace("sha256:", "")
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
            mode = facts["mode"]
            if mode is None:
                mode = preimage.get("mode")
            if not isinstance(mode, int) or isinstance(mode, bool):
                return refuse(
                    f"transaction {transaction_id} records no mode for the "
                    f"preimage of {location}, and bytes are not put back "
                    "under a mode nobody chose. Nothing has been restored."
                )
            data = blob.read_bytes()
            replacing_mode = mode
            intention = replace_file(
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
            intention = link_to(
                purpose=facts["intention"]["purpose"],
                path=location,
                durability=durability,
                expected=preimage,
                target=target,
            )

        # A regular file, and only a regular file, has bytes to keep. A
        # node that is neither a directory, a symlink nor a regular file
        # carries no `digest` (`current_state`) and must not be read at
        # all: reading through a FIFO can block for ever, and there is
        # nothing there for a later reader to want back.
        kept = None
        if present["kind"] == FILE and "digest" in present:
            try:
                reference = self._park_preimage(location)
            except VisibilityUnconfirmed as error:
                return refuse(
                    f"the bytes now at {location} remain visible and were not "
                    f"discarded, but their parked copy is unconfirmed: {error}. "
                    "Nothing has been restored."
                )
            except OSError as error:
                return refuse(
                    f"the bytes now at {location} could not be parked, and "
                    f"nothing here discards bytes it has not kept: {error}. "
                    "Nothing has been restored."
                )
            if reference is not None:
                kept = (
                    f"{VAULT_DIRNAME}/{PREIMAGE_DIRNAME}/"
                    f"{reference.replace('sha256:', '')}"
                )

        try:
            if intention is not None:
                self._publish(intention, location, replacing_mode, data)
            else:
                self._unpublish(location, present)
        except VisibilityUnconfirmed as error:
            journal_error = self._uncertainty(
                transaction_id,
                RESTORE_UNCONFIRMED,
                f"the restored state of {location} is visible or may be "
                f"visible, but its durability is unconfirmed: {error}",
            )
            return refuse(journal_error.message)
        except OSError as error:
            return refuse(
                f"{location} could not be put back: {error}. Nothing has "
                "been restored."
            )
        try:
            mark_unconfirmed(
                self.root,
                transaction_id,
                RESTORE_UNCONFIRMED,
                "restored preimage confirmed; transaction cleanup pending",
            )
            remove_transaction_file(self.root, transaction_id)
        except (OSError, VisibilityUnconfirmed) as error:
            journal_error = self._uncertainty(
                transaction_id,
                RESTORE_UNCONFIRMED,
                f"the restored state of {location} is confirmed, but WAL "
                f"cleanup is unconfirmed: {error}",
            )
            return refuse(journal_error.message)
        return Resolution(transaction_id, RESTORE, location, kept=kept)

    def _unpublish(self, location, actual):
        """Take the published node away: the inverse of an `absent` preimage.

        A directory is `rmdir`, never a recursive removal: anything inside
        it was put there by something this transaction knows nothing about,
        and a non-empty directory raises, which the caller renders as a
        refusal naming it. A symlink is unlinked with nothing parked: the
        bytes it resolves to belong to the path it points at, not to this
        one, and removing a link destroys none of them. A regular file has
        already been parked by the caller. A path already absent is nothing
        to undo. The
        parent is fsynced afterwards, for the same reason `install` does:
        the removal of a directory entry is itself buffered.
        """
        target = self.root / location
        if actual["kind"] == DIRECTORY:
            remove_name(target, directory=True)
            return
        elif actual["kind"] != ABSENT:
            remove_name(target)
            return
        remove_name(target)


def repair_harness_link(path, target, anchor=Path()):
    """Restore and confirm the harness link when no journal session can serve it.

    This is the fail-open integration boundary, not a journal transaction:
    callers receive ordinary pre-visibility ``OSError`` failures, while a
    visible effect with an unconfirmed barrier is tagged as a gating journal
    error so it cannot be downgraded to the historical warning-only path.
    """
    try:
        repair_symlink(path, target, anchor)
    except VisibilityUnconfirmed as cause:
        error = JournalError(
            None,
            f"the harness symlink is visible or may be visible, but its "
            f"durability is unconfirmed: {cause}",
            os.fspath(path),
        )
        error.visibility_unconfirmed = True
        raise error from cause


@contextmanager
def adopting_session(
    identity_transition,
    append_failure,
    append_reconfirmation,
    root=Path(),
):
    """Yield one adopting session while holding its run-wide lock.

    Identity creation is part of this operation: the histories are read and
    the repository journal is bootstrapped under the same lock, before the
    caller can perform either journalled work or the unjournalled harness
    take-over. Session operations retain their own locks underneath it.
    """
    root = Path(root)
    with Lock(root):
        ensure_history_compatibility()
        run = new_id()
        records = read(root, REPO)
        local = read(root, LOCAL)
        transactions = open_transactions(root)
        if not records and not local and transactions:
            raise JournalError(
                None,
                historyless_transactions_message(transactions),
                f"{VAULT_DIRNAME}/transactions",
            )
        rendezvous_at("before-identity-transition", 1)
        result = identity_transition(root, run, records, local)
        if isinstance(result, IdentityConfirmed):
            confirmed = result
        elif isinstance(result, IdentityRefused):
            raise JournalError(None, result.message, result.artifact)
        elif isinstance(result, IdentityRetained):
            raise JournalError(None, result.message, result.artifact)
        else:
            raise TypeError(
                "identity transition returned an unknown closed result"
            )
        session = Run(
            root,
            run,
            confirmed.adoption,
            append_failure,
            append_reconfirmation,
        )
        session._survey(confirmed.repository, confirmed.local)
        yield session


def resolve_transaction(root, transaction_id, resolution):
    """Resolve exactly one existing transaction without adopting the tree.

    The exact name is probed deliberately outside the lock. Acquiring the
    filesystem lock creates its parent directory, so an absent transaction
    must be answered before that materializing action. A present artifact is
    read under the lock; only that result is acted on.
    """
    if resolution not in RESOLUTIONS:
        raise ValueError(f"unknown resolution '{resolution}'")

    root = Path(root)
    if not has_transaction(root, transaction_id):
        return Resolution(
            transaction_id,
            resolution,
            transaction_artifact(transaction_id),
            no_such_transaction(transaction_id),
        )

    with Lock(root):
        ensure_history_compatibility()
        item = read_transaction(root, transaction_id)
        if item is None:
            return Resolution(
                transaction_id,
                resolution,
                transaction_artifact(transaction_id),
                no_such_transaction(transaction_id),
            )

        records = read(root, REPO)
        local = read(root, LOCAL)
        adoption = existing_adoption_id(records, local)
        if adoption is None:
            artifact = transaction_artifact(transaction_id)
            return Resolution(
                transaction_id,
                resolution,
                artifact,
                f"transaction {transaction_id} has no adoption history in "
                f"{JOURNAL_FILENAME} or {artifact_name(LOCAL)}; a transaction "
                "cannot establish which adopter it belongs to. Nothing has "
                "been changed.",
            )

        session = Run(root, new_id(), adoption)
        return session._resolve_one(item, resolution)


def repair_transaction(root, transaction_id):
    """Repair one proof-carrying history tail, or refuse without writes."""
    root = Path(root)
    if not has_transaction(root, transaction_id):
        return Resolution(
            transaction_id,
            "repair",
            transaction_artifact(transaction_id),
            no_such_transaction(transaction_id),
        )
    with Lock(root):
        item = read_transaction(root, transaction_id)
        if item is None:
            return Resolution(
                transaction_id, "repair", transaction_artifact(transaction_id),
                no_such_transaction(transaction_id),
            )
        refusal = _repair_refusal(item, transaction_id)
        if refusal is not None:
            return Resolution(transaction_id, "repair", transaction_artifact(transaction_id), refusal)
        claim = item["history_append"]
        durability = claim["artifact"]
        history = journal_path(root, durability)
        if not history.exists() or history.is_symlink() and not history.resolve().exists():
            return Resolution(
                transaction_id, "repair", artifact_name(durability),
                "the selected history is absent; one WAL cannot reconstruct missing append-only history; Nothing has been changed.",
            )
        try:
            data = history.read_bytes()
            complete, _, _ = _repair_complete_prefix(
                data, artifact_name(durability)
            )
            prefix = claim["prefix"]
            payload_records = claim["records"]
            payload = encode_records(payload_records)
            identities = {
                entry.get("adoption")
                for entry in complete
                if isinstance(entry, dict) and isinstance(entry.get("adoption"), str)
            }
            other = LOCAL if durability == REPO else REPO
            try:
                other_records = read(root, other)
            except JournalError as error:
                return _repair_refusal_result(transaction_id, artifact_name(other), str(error))
            identities.update(
                entry.get("adoption")
                for entry in other_records
                if isinstance(entry.get("adoption"), str)
            )
            if identities and identities != {item.get("adoption")}:
                return _repair_refusal_result(transaction_id, artifact_name(durability), "history adoption identity does not match the selected WAL")
            for competing in open_transactions(root):
                competing_claim = competing.get("history_append")
                if competing.get("id") == transaction_id or not isinstance(competing_claim, dict):
                    continue
                if (
                    competing_claim.get("artifact") == durability
                    and competing_claim.get("prefix") == prefix
                ):
                    return _repair_refusal_result(transaction_id, artifact_name(durability), "another current WAL claims the same history frontier")
            prefix_length = prefix["length"]
            # The claim boundary, rather than the parser's current EOF
            # boundary, is the authority for an append repair.  In
            # particular, a history may contain a complete prepared record
            # followed by a partial committed record, while the parser's
            # tail begins in the middle of the claimed append.
            if prefix_length > len(data):
                return _repair_refusal_result(transaction_id, artifact_name(durability), "the claimed history prefix is not present")
            if digest(data[:prefix_length]) != prefix["digest"]:
                return _repair_refusal_result(transaction_id, artifact_name(durability), "history prefix changed since the WAL proof")
            append_tail = data[prefix_length:]
            if append_tail and not payload.startswith(append_tail):
                return _repair_refusal_result(transaction_id, artifact_name(durability), "history EOF bytes are not a prefix of the claimed append")
            if len(append_tail) > len(payload):
                return _repair_refusal_result(transaction_id, artifact_name(durability), "history contains unrelated bytes after the claimed frontier")
            target_name = item.get("intention", {}).get("path")
            if not isinstance(target_name, str):
                return _repair_refusal_result(transaction_id, artifact_name(durability), "the selected WAL has no target path")
            if not satisfies(current_state(root, target_name), item.get("postimage", {})):
                return _repair_refusal_result(transaction_id, target_name, "the target no longer has the WAL postimage required for repair")
            candidate = data[:prefix_length] + payload
            try:
                candidate_records = validate_snapshot(
                    candidate, durability, artifact_name(durability)
                )
            except JournalError as error:
                return _repair_refusal_result(transaction_id, artifact_name(durability), str(error))
            if candidate_records.count(payload_records[0]) != 1 or candidate_records.count(payload_records[1]) != 1:
                return _repair_refusal_result(transaction_id, artifact_name(durability), "the complete candidate does not contain exactly one claimed record pair")
            mode = stat.S_IMODE(history.resolve(strict=True).stat().st_mode) if history.is_symlink() else stat.S_IMODE(history.stat().st_mode)
            # Even an already complete final snapshot receives a fresh atomic
            # publication and barrier. A prior visible append is not proof
            # that its directory entry survived the failed confirmation.
            _repair_history_target(history, data, candidate, mode)
            final_data = history.read_bytes()
            try:
                final_records = validate_snapshot(
                    final_data, durability, artifact_name(durability)
                )
            except JournalError as error:
                return _repair_refusal_result(transaction_id, artifact_name(durability), str(error))
            if final_data != candidate or final_records != candidate_records:
                return _repair_refusal_result(transaction_id, artifact_name(durability), "repaired history did not reread as the claimed snapshot")
            _cleanup_temporary_claim(root, item)
            cleanup_private_duplicates(root)
            remove_transaction_file(root, transaction_id)
        except (OSError, VisibilityUnconfirmed, JournalError) as error:
            return Resolution(
                transaction_id, "repair", artifact_name(durability),
                f"history repair could not be confirmed: {error}; transaction evidence was retained.",
            )
    return Resolution(transaction_id, "repair", artifact_name(durability), None)


def _repair_refusal(item, transaction_id):
    """Validate the selected WAL proof before any history read or write."""
    if item.get("damaged"):
        return f"transaction {transaction_id} is damaged: {item['damaged']}; Nothing has been changed."
    if item.get("stage") != PUBLISHED or item.get("unconfirmed") not in (None, HISTORY_UNCONFIRMED):
        return f"transaction {transaction_id} is not a current published history repair; Nothing has been changed."
    claim = item.get("history_append")
    if not isinstance(claim, dict):
        return f"transaction {transaction_id} has no proof-carrying history append claim; Nothing has been changed."
    if claim.get("artifact") not in (REPO, LOCAL):
        return f"transaction {transaction_id} names an invalid history artifact; Nothing has been changed."
    if claim.get("encoding") != "json-sorted-keys-utf8-lf":
        return f"transaction {transaction_id} names an unsupported history encoding; Nothing has been changed."
    records = claim.get("records")
    if not isinstance(records, list) or len(records) != 2:
        return f"transaction {transaction_id} does not carry exactly two history records; Nothing has been changed."
    if [entry.get("stage") for entry in records if isinstance(entry, dict)] != [PREPARED, COMMITTED]:
        return f"transaction {transaction_id} does not carry prepared then committed records; Nothing has been changed."
    if any(not isinstance(entry, dict) or entry.get("transaction") != transaction_id for entry in records):
        return f"transaction {transaction_id} carries a foreign history record; Nothing has been changed."
    if any(entry.get("adoption") != item.get("adoption") for entry in records):
        return f"transaction {transaction_id} carries a foreign adoption identity; Nothing has been changed."
    expected = _expected_history_pair(item, transaction_id)
    if expected is None:
        return f"transaction {transaction_id} does not contain a complete WAL intention; Nothing has been changed."
    for actual, wanted in zip(records, expected):
        if set(actual) != set(wanted) | {"at", "version"}:
            return f"transaction {transaction_id} history claim fields do not match its WAL; Nothing has been changed."
        if any(actual.get(field) != value for field, value in wanted.items()):
            return f"transaction {transaction_id} history claim is not bound to its WAL; Nothing has been changed."
        if not isinstance(actual.get("at"), str) or actual.get("version") != item.get("version"):
            return f"transaction {transaction_id} history claim metadata is invalid; Nothing has been changed."
    shared = (
        "run", "adoption", "transaction", "durability", "op", "purpose",
        "path", "preimage", "postimage", "mode", "prior_bytes",
    )
    if any(records[0].get(field) != records[1].get(field) for field in shared):
        return f"transaction {transaction_id} carries an inconsistent history pair; Nothing has been changed."
    prefix = claim.get("prefix")
    append_claim = claim.get("append")
    timestamps = claim.get("timestamps")
    if not isinstance(prefix, dict) or not isinstance(append_claim, dict) or not isinstance(timestamps, list):
        return f"transaction {transaction_id} has an incomplete history proof; Nothing has been changed."
    if timestamps != item.get("history_timestamps"):
        return f"transaction {transaction_id} history timestamps are not bound to its WAL; Nothing has been changed."
    if timestamps != [entry.get("at") for entry in records]:
        return f"transaction {transaction_id} history timestamps are not proof-bound; Nothing has been changed."
    try:
        payload = encode_records(records)
        if append_claim.get("length") != len(payload) or append_claim.get("digest") != digest(payload):
            return f"transaction {transaction_id} history append proof does not match its records; Nothing has been changed."
        prefix_length = prefix.get("length")
        if (
            type(prefix_length) is not int
            or prefix_length < 0
            or not isinstance(prefix.get("digest"), str)
        ):
            raise ValueError
    except (TypeError, ValueError):
        return f"transaction {transaction_id} has an invalid history proof; Nothing has been changed."
    return None


def _expected_history_pair(item, transaction_id):
    """Derive the only history pair authorized by one WAL entry."""
    intention = item.get("intention")
    postimage = item.get("postimage")
    if not isinstance(intention, dict) or not isinstance(postimage, dict):
        return None
    required = {
        "schema": item.get("schema"),
        "version": item.get("version"),
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
    if mode is None and postimage.get("kind") == FILE:
        return None
    if mode is not None:
        extra["mode"] = mode
    if intention.get("note") is not None:
        extra["note"] = intention["note"]
    if postimage.get("kind") == FILE:
        extra["preimage"] = item.get("preimage_blob")
        extra["postimage"] = postimage.get("digest")
        if item.get("prior_bytes") is not None:
            extra["prior_bytes"] = item["prior_bytes"]
    return [
        {**required, "stage": stage, **extra}
        for stage in (PREPARED, COMMITTED)
    ]


def _repair_refusal_result(transaction_id, location, message):
    return Resolution(transaction_id, "repair", location, f"{message}; Nothing has been changed.")


def _cleanup_temporary_claim(root, item):
    """Remove only an exact, no-longer-needed target staging candidate."""
    claim = item.get("temporary")
    if claim is None:
        return
    intention = item.get("intention")
    if not isinstance(claim, dict) or not isinstance(intention, dict):
        raise OSError("temporary claim is malformed")
    target_name = claim.get("target")
    if target_name != intention.get("path") or claim.get("transaction") != item.get("transaction"):
        raise OSError("temporary claim target or transaction does not match the WAL")
    if claim.get("role") != "target-staging" or claim.get("adoption") != item.get("adoption"):
        raise OSError("temporary claim role or adoption does not match the WAL")
    expected_kind = "symlink" if intention.get("op") == LINK else "regular-file"
    if claim.get("kind") != expected_kind or claim.get("mode") != item.get("mode"):
        raise OSError("temporary claim kind or mode does not match the WAL")
    if intention.get("durability") == REPO and not is_inside_path(target_name):
        raise OSError("temporary claim leaves the adopter root")
    target = Path(root) / target_name if intention.get("durability") == REPO else Path(target_name)
    try:
        parent_info = target.parent.lstat()
        if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode):
            raise OSError("temporary candidate parent is not a real directory")
        parent_identity = (parent_info.st_dev, parent_info.st_ino)
        resolved_parent = target.parent.resolve(strict=True)
        if resolved_parent != target.parent.resolve(strict=True):
            raise OSError("temporary candidate parent changed during validation")
        if intention.get("durability") == REPO:
            resolved_root = Path(root).resolve(strict=True)
            resolved_parent.relative_to(resolved_root)
    except (FileNotFoundError, OSError, ValueError) as error:
        raise OSError("temporary candidate parent is outside the validated target") from error
    actual = current_state(root, target_name)
    if not satisfies(actual, item.get("postimage", {})):
        raise OSError("target no longer has the WAL postimage required for cleanup")
    name = claim.get("name")
    if not isinstance(name, str) or Path(name).name != name or not name.startswith(f".{target.name}.") or not name.endswith(".tmp"):
        raise OSError("temporary claim name is outside the current staging grammar")
    candidate = target.parent / name
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        return
    if claim.get("kind") == "regular-file":
        if not stat.S_ISREG(info.st_mode) or digest(candidate.read_bytes()) != claim.get("digest"):
            raise OSError("temporary candidate does not match its claimed regular file")
        if stat.S_IMODE(info.st_mode) != claim.get("mode"):
            raise OSError("temporary candidate mode does not match its claim")
    elif claim.get("kind") == "symlink":
        if not stat.S_ISLNK(info.st_mode) or digest(os.readlink(candidate).encode("utf-8")) != claim.get("digest"):
            raise OSError("temporary candidate does not match its claimed symlink")
    else:
        raise OSError("temporary candidate kind is not supported")
    before_remove = (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode))
    try:
        parent_info = candidate.parent.lstat()
        after_info = candidate.lstat()
    except FileNotFoundError as error:
        raise OSError("temporary candidate changed before cleanup") from error
    if (
        stat.S_ISLNK(parent_info.st_mode)
        or not stat.S_ISDIR(parent_info.st_mode)
        or (parent_info.st_dev, parent_info.st_ino) != parent_identity
        or (after_info.st_dev, after_info.st_ino, stat.S_IMODE(after_info.st_mode)) != before_remove
        or candidate.parent.resolve(strict=True) != resolved_parent
    ):
        raise OSError("temporary candidate changed before cleanup")
    remove_name(candidate)
