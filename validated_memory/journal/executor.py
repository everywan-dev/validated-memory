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
    LINK,
    LOCAL,
    OBSERVE,
    PREPARED,
    REPO,
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
    ensure_history_compatibility,
)
from .transactions import (
    CLEANUP_UNCONFIRMED,
    HISTORY_CLAIM_UNCONFIRMED,
    HISTORY_UNCONFIRMED,
    PUBLISHED,
    TARGET_UNCONFIRMED,
    Resolution,
    abort_transaction,
    has_transaction,
    mark_published,
    mark_history_append,
    mark_temporary_claim,
    mark_unconfirmed,
    no_such_transaction,
    open_transaction,
    open_transactions,
    historyless_transactions_message,
    read_transaction,
    resolution_advice,
    remove_transaction_file,
    transaction_artifact,
)


PREIMAGE_DIRNAME = "preimages"
_protocol_resolve_one = None
_protocol_repair_one = None


def bind_resolution(callback):
    """Install the protocol-owned selected-resolution transition."""
    global _protocol_resolve_one
    _protocol_resolve_one = callback


def bind_repair(callback):
    """Install the protocol-owned proof-bound repair transition."""
    global _protocol_repair_one
    _protocol_repair_one = callback


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
        recover_all=None,
    ):
        self.root = Path(root)
        self.run = run
        self.adoption = adoption
        self._append_failure = append_failure
        self._recover_all = recover_all

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
        """Adapt the opaque adopting session to protocol-owned recovery."""
        if self._recover_all is None:
            raise RuntimeError("recovery callback is unavailable")
        results, repository, local = self._recover_all(self)
        self._survey(list(repository), list(local))
        return results

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

    def _restore_effect(
        self, location, present, intention, replacing_mode=None, data=None
    ):
        """Publish one already-authorized restore and retain displaced bytes."""
        kept = None
        if present["kind"] == FILE and "digest" in present:
            reference = self._park_preimage(location)
            if reference is not None:
                kept = (
                    f"{VAULT_DIRNAME}/{PREIMAGE_DIRNAME}/"
                    f"{reference.replace('sha256:', '')}"
                )
        if intention is not None:
            self._publish(intention, location, replacing_mode, data)
        else:
            self._unpublish(location, present)
        return kept

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
    recover_all,
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
            recover_all,
        )
        session._survey(confirmed.repository, confirmed.local)
        yield session


def resolve_transaction(root, transaction_id, resolution):
    """Compatibility facade for protocol-owned selected resolution."""
    if _protocol_resolve_one is None:
        raise RuntimeError("resolution callback is unavailable")
    return _protocol_resolve_one(root, transaction_id, resolution)


def repair_transaction(root, transaction_id):
    """Compatibility facade for protocol-owned proof-bound repair."""
    if _protocol_repair_one is None:
        raise RuntimeError("repair callback is unavailable")
    return _protocol_repair_one(root, transaction_id)
