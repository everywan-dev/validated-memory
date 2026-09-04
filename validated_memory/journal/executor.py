"""The executor: the preimage store, the opening write, and `Run`.

`_bootstrap` is the one write that cannot journal itself. `Run` is the
whole of docs/design/2026-09-01-the-journal-core.md §4's protocol -- the
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
import stat
from dataclasses import replace as _replace
from pathlib import Path

from .durable import fsync_directory, install
from .fault import fault_at
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
    journal_path,
    new_id,
    read,
    record,
)
from .transactions import (
    ACCEPT,
    DISCARDED,
    PROBLEM_DAMAGED,
    PROBLEM_DIVERGED,
    PROBLEM_UNKNOWN,
    PUBLISHED,
    RECOVERABLE,
    RECOVERED,
    REMOVED,
    RESOLUTIONS,
    RESTORE,
    Recovery,
    Resolution,
    abort_transaction,
    classify,
    VERDICT_COMPLETE,
    VERDICT_DISCARD,
    mark_published,
    no_such_transaction,
    open_transaction,
    open_transactions,
    VERDICT_REMOVE,
    resolution_advice,
    remove_transaction_file,
    transaction_artifact,
)


PREIMAGE_DIRNAME = "preimages"


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


def _bootstrap(root, run, records, local):
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

    `Run.__init__` holds the lock across this call, so a caller does not
    have to: `Lock` is re-entrant within a process, and the run-wide lock
    `init` already holds and the one taken there are the same lock. Two
    processes bootstrapping the same new adopter without it would mint two
    adoption ids, and the second install would win in silence.

    `records` and `local` are the two journals `Run.__init__` has already
    read, so the files are not read twice. Each must be exactly what
    `read(root, ...)` returned for its durability; anything else would mint
    a second adoption id over a journal that already has one.

    Both artifacts are consulted, because only one of them is versioned.
    `journal.jsonl` is tracked and the vault is ignored, so an ordinary
    `git checkout` of a commit from before the adoption takes the journal
    away and leaves the vault: minting again there would file this run's
    records under an id the vault's preimages know nothing about, split one
    adoption in two, and report clean throughout, since no record is
    missing or malformed.
    """
    path = journal_path(root, REPO)
    adoption = _adoption_id(records, local)
    if records:
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
        run=run,
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
    already knows about, and performs mutations through `execute`, which
    is the whole of docs/design/2026-09-01-the-journal-core.md §4's
    protocol and the only thing a caller needs. One `Run` per invocation.

    Four methods, and no fifth: `observe` for a fact about the state
    adoption found, `execute` for every mutation, `recover` for what an
    earlier run left open, and `resolve_transaction` for the one an
    operator answers for by hand. No module outside this package can open a
    stage: a `prepared` record with no `committed` twin is still what
    `journal --check` reconciles, because a history written before this
    protocol can hold one, but nothing here writes another.

    `__init__` takes `Lock` itself, around the two reads and `_bootstrap`:
    deciding from what was read that no adoption id exists yet and then
    installing one is a read-modify-write, and two runs interleaving there
    mint two ids for one project. `Lock` is re-entrant, so a caller already
    holding it -- `init.run` holds it for the whole run -- neither waits
    here nor has it released early. Every public method below takes it the
    same way, `observe` included: what serialises a write is the lock the
    write itself holds, not one a caller might happen to be inside.
    """

    def __init__(self, root=Path()):
        self.root = Path(root)
        self.run = new_id()
        with Lock(self.root):
            records = read(self.root, REPO)
            local = read(self.root, LOCAL)
            self.adoption = _bootstrap(self.root, self.run, records, local)
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
            blob.unlink(missing_ok=True)
        if not blob.exists():
            blob.parent.mkdir(parents=True, exist_ok=True)
            temporary = blob.with_name(f"{blob.name}.{os.getpid()}.tmp")
            try:
                # The blob is named after its own digest, so a torn write
                # would leave bytes that silently disagree with the name
                # every later reader trusts. Every other atomic write in
                # this package fsyncs before the rename; this one has to as
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
           That marker is what makes recovery possible for the mutations
           whose two states are indistinguishable on disk.
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
            # here covers it: only Python code holding a `Run` can do it,
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

        blob = None
        if actual["kind"] == FILE:
            try:
                blob = self._park_preimage(location)
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

        # The line this method turns on. Every return above it is a
        # refusal that left nothing behind, which is why they are
        # `_refused`; every return below it has a transaction file on disk
        # to close first, which is why they are `_aborted`. A refusal added
        # below this line that does not close its transaction leaves the
        # path gated for ever against a mutation that never happened.
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
        fault_at("after-transaction")

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

        try:
            mode = self._publish(
                intention,
                location,
                None if actual["kind"] == ABSENT else actual["mode"],
                data,
            )
        except OSError as error:
            return self._aborted(
                transaction,
                intention,
                again,
                f"{location} could not be written: {error}. Nothing has "
                "been published.",
            )
        fault_at("after-publish")
        mark_published(self.root, transaction)
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
        fault_at("after-history")

        remove_transaction_file(self.root, transaction)
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
        abort_transaction(self.root, transaction, message)
        remove_transaction_file(self.root, transaction)
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

    def _publish(self, intention, location, replacing_mode, data):
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
        elif replacing_mode is None:
            if not target.parent.is_dir():
                raise FileNotFoundError(
                    f"its parent directory "
                    f"{Path(location).parent.as_posix()} does not exist"
                )
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
                os.chmod(temporary, replacing_mode)
                install(temporary, target)
            except OSError:
                temporary.unlink(missing_ok=True)
                raise
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
        transaction_id = item["id"]
        verdict, facts = classify(self.root, item, self.adoption)
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
                    f"{transaction_artifact(transaction_id)} "
                    "is left for inspection"
                ),
            )

        if verdict == VERDICT_REMOVE:
            reason = item.get("reason")
            remove_transaction_file(self.root, transaction_id)
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

        if verdict == VERDICT_DISCARD:
            remove_transaction_file(self.root, transaction_id)
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

        if verdict == VERDICT_COMPLETE:
            appended = self._complete(transaction_id, item, facts, histories)
            remove_transaction_file(self.root, transaction_id)
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
                f"cannot be read: {facts['reason']}; nothing here can say "
                "whether what it published is still there -- "
                f"{resolution_advice(transaction_id)}"
            )
        elif facts["actual"] is None:
            message = (
                f"transaction {transaction_id} prepared a mutation of {path}, "
                f"and {path} cannot be read: {facts['reason']}; nothing here "
                "can say whether it ran -- "
                f"{resolution_advice(transaction_id)}"
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

    def _complete(self, transaction_id, item, facts, histories=None, published=None):
        """Append whichever of the mutation's two records is not there yet.

        Returns whether anything was appended, so the caller can say which
        of the two shapes of `completed` this was.

        The records are rebuilt from the transaction file and the state the
        mutation PUBLISHED. Recovery passes nothing for `published` and the
        state is the one the path is in now, which `classify` has just
        proven is that postimage; `_resolve_one` passes the transaction's
        own postimage, because a `diverged` transaction did publish and
        something wrote over it afterwards, and the mode of that later
        write is not a fact about the mutation. `op`, `purpose`, `path`,
        `durability` and `note` come from the intention; `preimage` (the
        parked blob's reference) and `prior_bytes` from the file;
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
        `--abandon`. docs/design/2026-09-01-the-journal-core.md §8 is
        explicit that recovery needs its own interface or the project
        deadlocks: "refuse" is not a terminal state, and a diverged
        transaction gates its path for ever without a way out.

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
        - Over a `diverged` transaction both of them append the MUTATION's
          own record pair first: that transaction published, and a
          resolution is not a reason for the history to forget it. See
          `_resolve_one`.
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
        artifact = transaction_artifact(transaction_id)
        item = next(
            (
                entry
                for entry in open_transactions(self.root)
                if entry["id"] == transaction_id
            ),
            None,
        )
        if item is None:
            return Resolution(
                transaction_id, resolution, artifact, no_such_transaction(transaction_id)
            )

        verdict, facts = classify(self.root, item, self.adoption)
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
                f"{facts['path']} could not be read ({facts['reason']}), so "
                "nothing here can say what state is being closed over. "
                "Nothing has been changed.",
            )

        if resolution == RESTORE:
            return self._restore(transaction_id, item, facts)

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
            self._complete(
                transaction_id, item, facts, published=facts["postimage"]
            )

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
            reference = item.get("preimage_blob")
            if not isinstance(reference, str):
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
        except OSError as error:
            return refuse(
                f"{location} could not be put back: {error}. Nothing has "
                "been restored."
            )
        remove_transaction_file(self.root, transaction_id)
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
            os.rmdir(target)
        elif actual["kind"] != ABSENT:
            target.unlink()
        fsync_directory(target.parent)
