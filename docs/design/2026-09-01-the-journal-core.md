# The journal core: three records, one executor — design (2026-09-01)

Plan 1 shipped the record: two artifacts, a two-stage protocol, and a
`journal` subcommand that reports and reconciles. Three adversarial reviews
over two fix waves then closed sixteen defects in it, and left a residue that
will not close the same way. This document says why, and specifies the core
that §3 of `2026-08-30-the-journal-coverage-and-reversal-design.md` needs
before its interface can be built.

It amends that design rather than superseding it: §1, §2, §5, §6 and §7
stand. What changes is §3's mechanism and §4's account of crash consistency.

**This document was rewritten the day it was written.** Its first draft
proposed one operation, `apply(change set)`, owning everything. A design
challenge rejected that draft for three reasons that hold: `apply` was the
name of a transactional language rather than an interface; three unrelated
problems — local recovery, permanent history, and write authorisation — were
being solved by one mechanism; and three real callers do not fit the shape.
§11 records what the challenge changed.

## 1. What the waves measured

Every item below was reproduced by execution before it was believed. The
first wave closed six defects an ordinary `init` reaches; the second closed
two regressions the first introduced. What follows is what remains.

**A `prepared` record can never be closed, and `--check` then calls it
`applied`.** `_ensure_dir` writes `prepared`, `mkdir` fails — a read-only
root is enough, no race, no crash — and the function returns a `Finding`. The
record stays open. When a later run creates the directory for real,
`_state_of` sees the node exist and reports the old record `applied`, which
`docs/reference/journal.md` defines as "the mutation happened; only the
closing `committed` record was lost". It did not happen. `Run.write` and
`Run.append_text` leave the same residue when the install between their two
records raises. One junk record is appended to the always-versioned journal
on every session start.

**An absence check is not a precondition.** `_ensure_file` tests
`path.exists()` and then calls `Run.write`, which parks a preimage and turns
`create` into `replace` if one is there by then, silently. The module's own
promise — "an existing item, including one already hand-edited, is never
touched" — has a window in which it is false, and the record says `replace`
rather than refusing.

**The install writes a file the adopter made read-only, and hides it.**
Measured: a `.gitignore` at mode 0444 holding `build/` comes back at 0644 and
276 bytes, with `0 error(s), 0 warning(s)`, exit 0, and no finding anywhere.
`os.replace` needs write permission on the directory, not on the file, so the
read-only bit never stops it, and the temporary carries a fresh mode.

**The lock does not exclude.** It is broken on age alone, and `__exit__`
unlinks the pathname unconditionally, so the process whose lock was broken
deletes the lock its successor now holds. Reproduced with two real runs by
backdating the lock file's mtime — the one quantity the staleness test reads.
No shipped caller reaches the 300-second horizon today, but the class
docstring's premise is already false: `init.run` holds the lock across
`adopt.take_over`, which copies a directory tree whose size the plugin does
not control.

**Nothing verifies a preimage blob.** `park_preimage` accepts any file
already named for the digest and never re-reads it; no reader checks that a
blob a record references is present or still matches its name.

**`reconcile` pairs by `(run, path)` alone**, so a duplicated pair, or a
`committed` twin whose digests disagree with its `prepared`, passes.

**A merged journal is not one history.** Two clones branch from one base and
each records a transaction over the same path; both declare preimage `A`.
Union merges them into valid JSON with coherent ids and no duplicated line,
and there is no serial order in which both preimages are true. §7 reads
records in reverse to invert them, which over that file is arbitrary.

## 2. The cause: the journal is a shallow module

These are not seven unrelated bugs. They share a shape, and the shape is the
interface.

`Run` hands its callers the protocol rather than a result. It offers
`observe`, `write`, `append_text`, `prepare_op` and `append_op`, and makes
each caller choose the operation, the purpose, the durability, the order of
the two stages, and what to do when the mutation between them fails. It does
not own the lock it requires; it documents that the caller must hold one.

So `init.py` reimplements fragments of the same state machine four times.
`_ensure_dir` writes both stages by hand around a `mkdir`. `_ensure_file`
delegates to a method that writes both. `_record_symlink` implements the
protocol again with fail-open semantics, because the harness link must
survive a journal that cannot be written. `run` decides which of those
survive each gate, inside or outside the lock.

Every correction therefore crosses several dimensions at once: the node type
found at the path, the durability, whether the failure lands before
`prepared` or after it, whether the operation carries a digest, whether the
outcome is an ERROR or a WARNING, and whether the caller is inside the lock.
Fixing one cell of that space uncovers the next, which is exactly the history
of the two waves. More review does not exhaust it; the space does not shrink.

## 3. Three records, not one

The first draft's mistake was to answer that with a single mechanism. The
record already serves three purposes with incompatible lifetimes, and giving
them one file is what made `aborted` look like a stage.

**The write-ahead log — local, short-lived, in `.validated-memory/`.** What a
mutation intends, its parked preimages, and how it ended when it did not
succeed. It exists so a crash is recoverable, it is read by recovery and by
nothing else, and a resolved transaction leaves it. It is not history and
must never grow without bound.

**The permanent history — `journal.jsonl` and the vault's own record, as
today.** Consummated facts only: an observation of the pre-adoption state,
and a mutation that happened. It is append-only, never compacted, and
versioned, so anything written into it is written for the life of the
project. A refusal is not a fact about the project; it is a fact about one
run, and it belongs in the log or in the rejection record of §6, which is
already specified to deduplicate by stable identity.

This matters concretely: `init` runs on every session start, so a project
that is broken in some way `init` refuses would otherwise append one refusal
per session, for ever, to a versioned file. That would be a property of the
model, not an implementation detail.

**The derived publisher — no record at all.** `render` and `init --view`
write regenerable artifacts, exempt by name from the completeness pin and
documented as exempt. They need atomic publication and metadata
preservation, which is the same machinery, and they must not put a line in
the history.

## 4. One executor, and its declared exceptions

The mutating surface becomes one deep operation over **one path**:

    execute(intention) -> outcome

An intention names the path, the operation, the purpose, the durability and
the **expected state**. The executor owns the lock, the bootstrap and the
adoption id, path authorisation, the expected-state check, the preimage, the
staging and publication, the metadata, and the log records at both ends.
`prepare_op` and `append_op` leave the public surface, so no caller can
reimplement the protocol the way `init.py` did.

`observe` keeps exactly the meaning the contract publishes: written once, on
first sight, recording a fact about the state adoption found. It is not "the
expectation was already satisfied" — an idempotent no-op on a path this
plugin created earlier is not an observation, and treating it as one would
reintroduce the defect commit `4ce59a9` fixed.

Multi-path grouping is **not** part of this core. §8 says what it would take
and why it waits.

Two callers do not fit the executor, and both are exceptions declared here
rather than bent into it:

**The fail-open harness link.** The contract requires the link to be restored
when the journal cannot be read or written at all — that is the SessionStart
hook's only job, and wave 2 exists because a ruling of mine took it away. An
executor that requires a working journal cannot serve it; an executor that
accepts `unrecorded=True` exposes the very policy it was meant to hide; an
executor that silently proceeds unrecorded on any journal failure is a
general bypass. So link repair is its own narrow module: it can publish that
one symlink atomically and nothing else, it records through the executor when
the journal is healthy, and it warns and proceeds when it is not.

**The harness absorption.** `adopt.take_over` recognises a tree, copies
conditionally, reconciles an index and renames the source, and its published
contract tolerates a per-file conflict and continues — the project's copy is
kept and a WARNING says so. That is not "one mismatch refuses the set". It
needs its own planner that freezes the tree, resolves conflicts and produces
a manifest the executor can then apply, and until that exists the absorption
stays where it is, unrecorded and documented as unrecorded.

## 5. Terminal states, in the log

A refusal or a failure is recorded in the **write-ahead log**, never in the
permanent history:

| Where | What it holds |
|---|---|
| log | `prepared` with the manifest and preimages; `aborted` with the reason |
| history | `observe`, and `committed` for a mutation that happened |

`journal --check` reads both. An entry in the log with no resolution is an
open transaction — a genuine unknown, because the process died. An `aborted`
entry is closed, is never inverted by a reversal, and disappears with the log
once resolved. A precondition that fails before anything is prepared writes
nothing anywhere: it is a result the caller renders, not a transaction.

This is what removes the false `applied`. Today's ambiguity exists because
`prepared` in a permanent file means "either it happened, or it did not", and
the reconciler resolves it by looking at a filesystem some later run changed.

**Schema.** The reader accepts a record whose `schema` is lower and refuses
one that is higher, so raising it is not by itself a migration. A version 2
history requires: a parse per version rather than one field table; the v1
prefix treated as legacy, promising no parents, no modes and no transaction
ids retroactively, because they were never recorded; an explicit rule for a
v1 record appended after the first v2 one; and every open v1 `prepared`
resolved before a v2 mutation is allowed.

## 6. Preconditions: what they can and cannot promise

Every intention carries the state it expects, and the vocabulary has to be
richer than the first draft's "absent, a digest, or a node type": a symlink's
previous target, a file's mode, and — for a directory — that a directory is
there, not merely that the name resolves. Checking existence alone is exactly
what produces today's false `applied`.

The check happens **under the lock, immediately before publication**, and
nowhere else; the first draft also placed it at the start of a group
protocol, which widened the window it was meant to close. What that buys,
stated exactly, because overstating it would be the same class of defect this
core exists to remove:

- **Full serialisation against other validated-memory processes**, once the
  lock is fixed (§7).
- **A strong no-replace guarantee for a creation**, because the primitive
  exists: create with `O_CREAT|O_EXCL` and fail if the name is taken.
- **Optimistic rejection for a replacement**: the digest is re-read
  immediately before `os.replace`. There is no portable POSIX
  compare-and-swap on a pathname, so a third party writing in that window is
  detected only if it also changes the digest before the read. This is a
  narrower window, not an atomic guarantee, and the contract must say so.
- **Ancestors stabilised by descriptor.** Authorising a resolved path and
  then acting on the name lets a third party swap an ancestor for a symlink
  in between. Where the platform allows it the executor opens the parent once
  and operates relative to that descriptor rather than resolving twice.

## 7. Metadata and the lock

**Metadata means the mode**, and the design says only that. The install
copies the target's mode onto the temporary before the rename, and the record
carries it so a reversal can restore it. Ownership, timestamps, ACLs, extended
attributes and hardlink identity are **not** preserved — `os.replace` breaks
hardlink identity by construction — and the reference says so rather than
letting a reader assume a general promise.

A target whose mode denies writing to the current user is a **refusal**, not
a mutation performed through the directory's permissions. The read-only bit
is how an adopter says do not write here. This changes behaviour: `init`
gains an ERROR where it silently succeeded, and the message names the file
and its mode.

**The lock** already carries the owning pid, and the executor uses it: a lock
whose owner is alive (`os.kill(pid, 0)`) is never broken, whatever its age;
release identifies the lock by the device and inode of the descriptor it
holds, not by the pathname; and the age horizon survives only as a last
resort for a dead owner. The caller-must-hold-the-lock rule disappears with
the executor, which takes it.

One hazard the lock does not cover and the contract currently permits: a
`journal.jsonl` that is a symlink into a shared store. Two adopter trees
pointing at one file take two different local locks and serialise nothing.
Either the contract forbids that, or the lock is taken on the resolved
artifact. This design takes the second, and the reference says which.

## 8. Groups: what it would take, and why it waits

§3 of the earlier design says the interface "applies the whole set or
nothing". Over several arbitrary paths that cannot be delivered: POSIX has no
multi-file atomic rename, and a crash between two `os.replace` calls leaves
one published and one not. The honest promise is **recoverable atomicity** —
no partial success is ever reported, and a crash leaves a state the next run
detects and resolves before doing anything else.

It is not required to make plan 1 truthful. The published contract promises
atomicity per mutation, not across an `init`, and reports each item
separately. So the group protocol is specified here and built with the public
write interface that needs it, not before:

1. authorise every path and check every expected state, under the lock;
2. park and verify every preimage, and fsync them;
3. write and fsync a log entry carrying the transaction id, the ordered
   manifest, the operation count and a digest of the manifest;
4. build and fsync every temporary, publishing none;
5. publish each path atomically and fsync its directory;
6. write the history's `committed` records only once all are published;
7. a known failure before step 5 closes the log entry `aborted`; a failure
   during it restores what was published and closes it `rolled back`;
8. after a crash, no mutating command proceeds while a log entry is open.

**Recovery needs its own interface, or the project deadlocks.** Permissions
and a full disk are retryable. A path the user changed is not: "refuse" is
not a terminal state. Recovery must let the operator retry the rollback,
complete forward, accept the current state, restore a preimage under a fresh
authorisation, or abandon the group recording the divergence — and must be
idempotent if it is itself interrupted. Link repair stays available
throughout, because a project that cannot find its memory cannot be repaired
by someone who cannot start a session.

**Mixed-durability groups are forbidden in the first delivery.** A group
touching both artifacts has nowhere authoritative to put its manifest:
`journal.jsonl` may not carry the vault's absolute paths, the vault does not
travel with the versioned half, and writing to both is not atomic. The
coherent answer is a local coordinator holding the manifest with correlated
projections into each artifact, removed only after both are fsynced. Until
that is designed, an intention set may not span durabilities.

## 9. A merged journal is a fork, not a history

`merge=union` settles the syntax of a conflict on `journal.jsonl`. It does
not settle its semantics, and the merge-story commit claims more than union
delivers.

Each transaction carries a **frontier**, not a single parent: the head it
observed in *each* artifact, `{repo, local}`. A parent per artifact would
create two independent chains and order nothing between a `repo` mutation and
a `local` one — and the two artifacts legitimately diverge, since a checkout
of a pre-adoption commit removes the versioned half while the ignored vault
stays. That state is already handled and is not corruption; the frontier is
what tells it apart from a fork.

- `journal --check` reports a fork — two transactions naming the same
  frontier — as a finding, naming both branches. It does not guess an order.
- `uninstall` refuses a forked history. Inverting a fork means choosing which
  of two incompatible preimages was true, and nothing in the file says.
- The way out is a reconciliation record that names **both** heads and
  declares which line reversal follows. A record naming one parent cannot
  join two branches.
- The digest is defined over the canonical serialisation of the closing
  record — the same key order and separators the writer uses — so whitespace
  or key order cannot change the topology.

The chain detects an accidental merge. It does **not** authenticate the file:
the journal is repository content, so whoever can rewrite it can recompute
every frontier. It must not be presented as tamper evidence.

## 10. What this does not fix

Named so it is not assumed.

Four defects measured on shipped 1.5.2 are `init`'s own and outside this
core: `--harness-memory` given a path inside the project, which parks the
project's memory and leaves a self-referential symlink at exit 0; a plain
file where a directory goes, reported `kept`; a directory where a file goes,
after which `lint` and `status` raise instead of returning a finding; and
`_sync_symlink`'s `unlink`-then-`symlink_to`, whose crash window leaves the
harness with no link while the WARNING says the session is unaffected.

A negation line in an adopter's `.gitignore` (`!/.validated-memory/` after
the rule) leaves the vault unignored while `init` sees the literal entry and
writes nothing. Measured: `local.jsonl`, carrying an absolute harness path,
becomes stageable. How much of git's ignore semantics `init` may implement,
or whether it may ask git, is its own decision.

**An append is durable, not atomic.** `flush` and `fsync` guarantee that what
was written survives; they do not make a JSON line indivisible. A full disk
can leave a partial last line, which the strict reader then refuses — and if
the permanent history were also the recovery mechanism, that refusal would
block the very recovery that could fix it. Keeping the log separate is what
makes this survivable, and the log's reader must tolerate a torn tail.

**A missing preimage blob in a clone is normal**, not corruption: the journal
travels and the vault does not. A reader must distinguish "this history is
damaged" from "this clone cannot reverse that mutation", and today nothing
says either.

Coverage (§5), rejection records (§6) and `uninstall` (§7) of the earlier
design are unchanged by this document.

## 11. What the challenge changed

The first draft of this document, in the order the challenge raised them:

1. **One operation for everything → one executor plus declared exceptions.**
   The fail-open link repair and the conflict-tolerant absorption do not fit
   a set-or-nothing interface, and derived views should not enter the history
   at all. A change-set language broad enough to hold all of them would put
   the complexity back in the interface.
2. **`aborted` as a stage → a separate local write-ahead log.** A permanent,
   versioned, never-compacted file that gains one refusal per session start
   is a growth property of the model. Refusals before anything is prepared
   are results, not records.
3. **`observe` redefined → `observe` left alone.** The draft made it "the
   expectation was already satisfied", which contradicts the published
   meaning and would undo an earlier fix.
4. **Schema 2 as a bump → schema 2 as a migration**, with the five rules in
   §5, since the reader accepts lower versions and nothing retroactively
   recovers a parent or a mode that was never written.
5. **"The precondition is a guarantee" → what it actually guarantees.** The
   draft placed the check in two different places and implied a
   compare-and-swap POSIX does not offer on a pathname.
6. **A parent per artifact → a frontier over both**, and a reconciliation
   that names both heads rather than one.
7. **"Nothing is released until step 1 lands" → a smaller step 1.** The
   contract promises per-mutation atomicity, so the group protocol is not
   what makes plan 1 truthful.

Added from the same challenge: the mode-only wording, the resolved-artifact
lock, the torn-tail append, the recovery interface, and the distinction
between a damaged history and a clone that cannot reverse.

## 12. Testing

The seam does not move: the CLI as a subprocess over fixture adopter trees,
asserting exit codes, output and produced files.

- **Failure then repair, end to end**, for every failure the executor can
  hit: provoke it, repair the environment, run again, and assert the
  permanent history gained nothing from the failed run and `journal --check`
  is clean.
- **Crash injection at every protocol point**, with recovery asserted
  idempotent when it is itself interrupted.
- **The completeness pin extends**: today it asserts every write in the
  package reaches the journal. It must also assert that nothing outside the
  core reaches the stage-writing surface, and that the two declared
  exceptions are the only exceptions.
- **Mode, expected state, frontier and the torn tail each get their own
  test**, since each replaces a silent behaviour with a refusal.

## 13. Sequence

**Step 1a — the executor, and the truth of plan 1.** One deep single-path
operation owning the lock, authorisation, the expected state, the preimage,
the mode and publication; the local write-ahead log with its terminal states;
transaction ids with strict pairing and field agreement; `init`'s journalled
mutations migrated onto it; the stage-writing surface made private; the two
exceptions declared; schema 1 read compatibly and open v1 transactions
resolved explicitly. This is what makes the record stop lying, and it is the
gate on merging this branch.

**Step 1b — frontiers and fork detection**, with `journal --check` reporting
a fork. Required before `uninstall`, not before 1a.

**Step 2 — coverage** (§5 of the earlier design), which both design
challenges independently put before the public write interface, and whose
digest-bound dispositions give the preconditions their stable identity.

**Step 3 — the public write interface** (§3), behind one subcommand taking a
versioned request, with the group protocol and the recovery interface,
migrating `bootstrap-from-repo` page by page. The pinned subcommand set moves
once. The harness absorption's planner belongs here.

**Step 4 — rejection records** (§6), then **step 5 — `uninstall`** (§7),
which may assume a chain.

Step 1a is the release gate. A record that reports `applied` for a mutation
that never happened is not one this method may ship.
