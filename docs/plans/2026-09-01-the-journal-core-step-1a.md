# The journal core, step 1a — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the record stop lying. Today a `prepared` record that can never
be closed is reported `applied`; an absence check is not a precondition, so
`create` becomes `replace` silently; and an install writes through a
read-only bit and changes the mode with no finding. This plan replaces the
protocol callers currently reimplement with one executor that owns it, and
moves intentions and failures into a local log the history never sees.

**Spec:** `docs/design/2026-09-01-the-journal-core.md` — read it in full,
especially §3 (three records), §4 (one executor, two declared exceptions), §5
(terminal states), §6 (what a precondition can promise) and §7 (metadata and
the lock). §8 (groups), §9 (frontiers), and the earlier design's §5, §6 and
§7 are later steps and are **out of scope**.

**Architecture:** `validated_memory/journal.py` keeps the two permanent
artifacts unchanged in shape and gains a third record it owns entirely: a
directory of transaction files under the vault, one per open transaction. The
mutating surface becomes one function over one path. `init.py` stops writing
stages: it describes what it wants and what it expects to find, and renders
the outcome.

**Tech Stack:** Python 3.11+, standard library only. pytest, driving the CLI
as a subprocess.

## Global Constraints

- Runtime code is Python 3 and uses the **standard library only**. `pytest`
  is the sole development dependency.
- All repository content is written in **English**: code, comments, CLI
  messages, docs, skills, tests, commit messages.
- Exit codes: `0` clean or WARNING-only, `1` ERROR (gates), `2` usage error.
- Tests drive the CLI **as a subprocess** over fixture adopter trees and
  **never import the package's internals**. Use the `run_cli` fixture in
  `tests/conftest.py`.
- The CLI is always invoked as `python3 -P -m validated_memory` (ADR 0006).
  From this checkout: `PYTHONPATH=. python3 -P -m validated_memory ...`.
- **Run pytest with NO `PYTHONPATH` set: `python3 -m pytest -q`.** A relative
  `PYTHONPATH=.` inherited from the shell resolves against the fixture's
  temporary directory and produces hundreds of spurious failures.
- Commit messages: Conventional Commits, in English. **No `Claude-Session:`
  trailer.** `Co-Authored-By:` is fine.
- Work on `feature/uninstall-and-restore`; never force-push `main`.
- Nothing is deleted to make a check pass.
- **Every behaviour change needs a test that goes red without it.** Prove it
  by reverting the change and watching the test fail.
- **No task may leave the suite red.** A task that changes a pinned count or
  a pinned string moves the pin in the same task.

## Two decisions this plan fixes

The design leaves the shape of the log open. These are settled here.

**The log is one file per open transaction**, at
`.validated-memory/transactions/<transaction-id>.json`, written and fsynced
before the mutation and unlinked when the transaction is resolved. Its
presence *is* the definition of an open transaction. Nothing has to be
compacted, and "what was left half done" becomes a directory listing rather
than a pairing algorithm.

**The permanent history keeps the two-record shape it publishes, and both
records are appended together, after the mutation succeeds.** The
write-ahead guarantee moves to the transaction file, which is fsynced first.
An orphan `prepared` in the history therefore becomes impossible by
construction — which is the defect — while `_seen`, the walkthrough's record
count, the existing stage assertions and the published two-step vocabulary
all stay true.

A first draft of this plan removed `prepared` from the history instead. Two
reviews measured what that costs: five tests red, the walkthrough's
`13 record(s)` becoming `7`, four published sentences false, and — worst —
`Run._seen` is built from the histories, so a path with no history trace is
observed as a pre-adoption fact on the next run, reintroducing the defect
commit `4ce59a9` fixed. Verified by simulating it: the named test
`test_a_path_the_journal_already_knows_is_never_observed_as_pre_existing`
fails.

**There is no schema bump and no migration.** The new fields (`transaction`,
`mode`, the expected states) are additive, and the reader ignores fields it
does not know — measured: a record carrying `transaction` and `mode` appended
to a 1.6.0 journal reads clean, `journal: 14 record(s)`, exit 0. So a clone
on 1.6.0 keeps reading a journal this version wrote, a `merge=union` of the
two versions is not a special case, and the five migration rules the first
draft needed are all unnecessary. Existing schema 1 orphan `prepared` records
are reported by `--check` exactly as they are today; they are **not**
silently completed, because completing a directory `create` on existence
alone is the false `applied` this plan exists to remove.

## File Structure

| File | Responsibility |
|---|---|
| `validated_memory/journal.py` (modify) | The record format, the two permanent artifacts, the transaction directory, digests, the lock, the executor, reading, validation, recovery. Nothing else knows how a record is shaped or that stages exist. |
| `validated_memory/init.py` (modify) | Describes intentions and expected states; renders outcomes. Loses every stage-writing call and its outer `Lock`. |
| `validated_memory/cli.py` (modify) | `journal` gains resolution flags. The subcommand set does not change. |
| `validated_memory/adopt.py` (unchanged) | Still unrecorded, still documented as unrecorded. Its planner is step 3. |
| `tests/test_journal.py` (modify) | The vocabulary, the log, the executor, the lock, recovery. |
| `tests/test_init.py`, `tests/test_adoption_decisions.py` (modify) | Where `init`'s behaviour changes: the read-only refusal, the mode, a recovered transaction, the dropped outer lock. |
| `tests/test_walkthrough.py`, `docs/walkthrough.md` (modify, Task 7 only) | Only if a record count or a printed line actually changes. Decision 2 is chosen so that it does not. |
| `docs/reference/journal.md`, `docs/reference/cli.md` (modify) | The published contract. |
| `docs/adr/` (create one) | The record splits in three; a refusal never enters permanent history. |

---

### Task 1: Expected states and path authorisation

**Files:** `validated_memory/journal.py`, `tests/test_journal.py`

This comes first because Task 2's transaction file stores states from this
vocabulary, and Task 5's recovery is expressed over it. Two digests are not
enough: three of the seven mutations an ordinary `init` performs have no
bytes to digest.

**The vocabulary, as data:**

| State | Satisfied when |
|---|---|
| `absent` | nothing at the name, following no symlink |
| `directory` | a directory is there — not merely that the name resolves |
| `file(digest)` | a regular file whose content digests to exactly that |
| `symlink(target)` | a symlink whose `readlink` is exactly that target |
| `mode(bits)` | combined with the above: the target's mode bits |

`directory` exists because checking that a name resolves is exactly what
produces today's false `applied`: a broken symlink satisfies it.

**Path authorisation moves here whole, and applies to every intention**,
including one that only observes. Today `observe`, `prepare_op` and
`append_op` validate lexically while `write` and `append_text` also ask the
resolved question, which is how a `repo` record can still name a path
resolving outside the root — the residue both waves left.

- Lexical: relative, no `..`, for a `repo` intention.
- Resolved: stays below the resolved root for a `repo` intention.

**Out of scope, deliberately:** `dir_fd`-relative ancestor stabilisation. It
hardens against a hostile process swapping an ancestor between authorisation
and action, which none of the measured defects is, and it cannot be
demonstrated through this repository's test seam. It belongs to step 1b. Say
so in the docstring rather than implying the guarantee is there.

**Acceptance:** a `memory/` that is a symlink to a directory outside the tree
is refused for every intention, observation included; a broken symlink fails
`directory`; each state has a test that goes red when its check is relaxed.

---

### Task 2: The transaction log, and the fault seam

**Files:** `validated_memory/journal.py`, `tests/test_journal.py`

**Interfaces produced:**

- `TRANSACTIONS_DIRNAME = "transactions"` under the vault.
- `open_transaction(root, intention) -> str` — mints an id, writes the file,
  fsyncs the file **and** its directory, returns the id.
- `mark_published(root, transaction_id) -> None` — records, fsynced, that
  publication completed. See below: this is what makes recovery decidable.
- `resolve_transaction(root, transaction_id) -> None` — unlinks and fsyncs
  the directory.
- `open_transactions(root) -> list[dict]` — every unresolved transaction,
  ordered by the timestamp inside it, each carrying its id.

**The file holds** the schema, the timestamp, the plugin version, the
adoption id, the run id, the transaction id, the intention (op, purpose,
path, durability), the **preimage state** and the **postimage state** in
Task 1's vocabulary, the preimage blob digest when there is one, the target's
mode when it had one, and a stage: `prepared`, `published`, or `aborted` with
its reason.

**`published` is not decoration.** Recovery cannot always tell from the
filesystem whether the mutation happened: a `replace` whose new bytes equal
the old, an `append` of empty content, and every no-bytes intention
(`create` of a directory, `link`) satisfy the preimage and postimage states
at once. A marker fsynced after publication turns that inference into a fact.

**The fault seam.** Tasks 2 and 5 need a crash at a named protocol point, and
`monkeypatch` cannot cross the subprocess boundary this repository's seam
requires — there is no such hook today, and the only existing crash
simulation is hand-editing the artifact afterwards. So this task produces
one: a single environment variable read in `journal.py` naming the point to
die at, with the points enumerated as constants. It must be inert when unset,
and a test must assert that inertness. Nothing else in the package may read
it.

**Acceptance:** a transaction file exists between the fsync and the
resolution, and survives a kill in that window with the intention in it; a
resolved transaction leaves the directory empty; a transaction file that is
not valid JSON is reported as an unreadable open transaction naming its id,
never a traceback and never silently skipped; the fault variable, unset,
changes nothing.

---

### Task 3: The lock

**Files:** `validated_memory/journal.py`, `validated_memory/init.py`,
`tests/test_journal.py`, `tests/test_init.py`

**`Lock` becomes re-entrant within one process.** This is a blocker, not a
nicety: `init.run` wraps its whole run in `with journal.Lock():` and the lock
is `O_CREAT|O_EXCL` with an unconditional unlink, so an executor that takes
the lock deadlocks against its own caller. Measured: the nested acquisition
fails after the full ten-second wait with "another validated-memory process
holds .validated-memory/lock", which `init.run` catches and turns into a
journal ERROR — every session start, exit 1.

Re-entrancy rather than removing the outer lock, because the outer lock
protects two things `execute` does not: `adopt.take_over`, which copies a
tree and is not journalled, and `bootstrap`'s once-only adoption-id mint,
whose own docstring says two processes without it mint two ids.

Also in this task:

- A lock whose owning pid is alive (`os.kill(pid, 0)`) is **never** broken,
  whatever its age. The pid is already in the file.
- Release identifies the lock by the device and inode of the descriptor it
  holds, not by the pathname, so a process whose lock was broken cannot
  delete its successor's.
- The age horizon survives only for a dead owner whose file outlived it.
- The lock is taken on the **resolved** artifact, so two adopter trees whose
  `journal.jsonl` symlinks into one shared store serialise against each
  other; today they take two different local locks and serialise nothing.

**Acceptance:** the nested acquisition succeeds; two processes still exclude
each other; the two-process test that reproduced the defect — hold the lock,
backdate its mtime, start a second run — shows the second waiting rather than
breaking in, and the first does not delete a lock it no longer owns. Assert
on both exit codes and on the lock file's inode.

---

### Task 4: The executor

**Files:** `validated_memory/journal.py`, `tests/test_journal.py`

**Interface:** `execute(root, intention) -> outcome`. One path, one intention.

It owns, and no caller may do for itself: the lock; the bootstrap and the
adoption id; authorisation; the expected-state check; the preimage; staging,
publication and the durability barriers; the mode; the transaction file; and
the history records on success.

`prepare_op` and `append_op` **leave the public surface**. No module outside
`journal.py` may call anything that writes a stage.

**The order:**

1. take the lock (re-entrant, Task 3);
2. authorise the path (Task 1);
3. compare the current state with the expected state — a mismatch returns a
   refusal and **writes nothing anywhere**, because at this point there is no
   transaction to abort;
4. park and verify the preimage, then fsync it;
5. write and fsync the transaction file (Task 2);
6. re-read the expected state immediately before publishing, under the same
   lock;
7. publish, preserving the target's mode, and fsync the directory;
8. `mark_published`;
9. append **both** history records together, carrying the transaction id;
10. resolve the transaction.

**Publication is not one primitive.** For an intention whose expected state
is `absent`, publish with `O_CREAT|O_EXCL` and fail if the name is taken —
the design promises a strong no-replace guarantee for a creation, and
check-then-`os.replace` is not one: a third party creating the file between
steps 6 and 7 would be overwritten and the history would say `create`. For a
replacement, `os.replace` over a temporary, which is what exists today.

**`observe` keeps its published meaning**: written once, on first sight, a
fact about the state adoption found. It is not "the expected state was
already satisfied". The `_seen` rule that enforces this must survive intact,
and its test must not be weakened.

**`_seen` is seeded from the open transaction files as well as from both
histories.** Recovery runs first and normally puts the missing history
records back, but a transaction it leaves open — diverged, or a state it
cannot tell apart — means a path the plugin created is on disk with nothing
in the history naming it. Reading only the histories there would observe it
as a pre-adoption fact, which is the permanent, uninvertible lie commit
`4ce59a9` removed. The first draft of this plan missed exactly this, and a
review caught it by simulation.

**Mode.** The install copies the target's mode onto the temporary before the
rename, and the record carries it. A target whose mode denies writing to the
current user is a **refusal** with an ERROR naming the file and its mode.

**Acceptance:** a 0444 `.gitignore` is refused, is byte-identical afterwards
and is still 0444 — today it comes back 0644 and 276 bytes at exit 0; a 0640
file that the process may write is replaced and comes back 0640; an intention
expecting `absent` over a path that now holds a file is refused with nothing
recorded; both history records for one mutation carry the same transaction
id; reverting the mode copy, the re-read, the `O_EXCL` publication or the
refusal each turns a named test red.

---

### Task 5: Recovery and resolution

**Files:** `validated_memory/journal.py`, `validated_memory/cli.py`,
`tests/test_journal.py`

A run begins by resolving what a previous run left open. Decidable, because
Task 2 records a fact rather than leaving it to be inferred:

| Transaction file says | Path is | Action |
|---|---|---|
| `published` | matches the postimage state | append the history records if the transaction id is not already there, then resolve |
| `published` | anything else | leave it; report **that path** as diverged |
| `prepared` | matches the preimage state | nothing happened: resolve, record nothing |
| `prepared` | matches the postimage state, states distinguishable | complete forward as above |
| `prepared` | neither, or the states are indistinguishable | leave it; report that path as unknown |
| `aborted` | — | report it, then unlink: it is closed |
| unreadable | — | report it as damaged, naming the id |

**Commit idempotency is explicit.** A crash between step 9 and step 10 leaves
a `published` transaction whose history records already exist; appending them
again would double the record. The transaction id in the history record is
what makes the check possible, and the rule is stated rather than assumed.

**Only the affected path gates.** The rest of the run proceeds. This is
narrower than design §8, which blocks every mutating command while a group is
open: a group spans paths and cannot be reasoned about piecewise; a
single-path transaction can, and blocking everything would brick the
SessionStart hook over one stale file.

**A diverged transaction needs a way out, or it gates for ever.** `journal`
gains resolution flags — this adds no subcommand, so the pinned subcommand
set does not move:

- `journal --resolve <id> --accept` — the current state is what the user
  wants: close the transaction, recording the divergence as an observation
  of fact, not as a mutation that happened.
- `journal --resolve <id> --restore` — put the preimage back from the vault
  and close, refusing when the blob is missing or does not match its digest.
- `journal --resolve <id> --abandon` — close it, recording that the path was
  left as found.

Each writes to the history only what is true.

**A preimage blob that is missing or mismatched** is two different things and
the code must not conflate them: for a **closed** history record it means
this clone cannot reverse that mutation, which is normal since the vault does
not travel; for an **open** transaction it means the log is damaged, and
`--restore` must refuse rather than write wrong bytes.

**Acceptance:** kill the CLI at each protocol point and assert the next run
reaches a clean `journal --check` and that the history gained **exactly one**
pair for that mutation — not zero, not two; recovery is idempotent over the
same residue; each resolution flag has a test; a mismatched blob makes
`--restore` refuse.

---

### Task 6: `init` on the executor, and the two exception sets

**Files:** `validated_memory/init.py`, `tests/test_init.py`,
`tests/test_adoption_decisions.py`, `tests/test_journal.py`

`_ensure_dir`, `_ensure_file`, `_ensure_ignored` and `_record_symlink` stop
writing stages and start describing intentions with expected states.
`init.run` keeps its outer lock (Task 3 makes that safe).

**The ignore entry stays first and still gates everything after it.** That
ordering is load-bearing under this plan too: it is what guarantees no
transaction file is ever written while the vault is unignored. A transaction
file for a `local` intention carries an absolute path, so a stageable one
would leak exactly what ADR 0008 exists to prevent.

**A consequence to state in the release notes, not to soften:** an adopter
whose `.gitignore` is read-only now gets a gated adoption on every session
instead of a silent rewrite. The ERROR names the file and its mode, and one
`chmod` fixes it. Writing to a file the adopter marked read-only is not an
option this method takes.

**Two sets, which the plan's first draft conflated:**

- **Executor exceptions** — callers that mutate without going through
  `execute`, by decision: the fail-open harness link repair (design §4), and
  `adopt.take_over`'s absorption (design §4, planner in step 3). Exactly two.
- **Unrecorded writes** — writes that reach no journal because their artifact
  is derived or is another log: `render`, `derive`, `init --view`, and
  `verdicts.append`. These are already exempt by name in the completeness
  pin and are not executor exceptions.

The pin asserts **both** sets explicitly and by name, so a third of either
kind fails the test rather than passing unnoticed.

**One docstring must be rewritten, not left to drift.**
`tests/test_journal.py::test_a_directory_is_recorded_before_it_is_created`
asserts only that the two stages are present and in order, so it stays green
— verified. But its docstring says the `prepared` record "is what makes that
window visible", and under decision 2 the transaction file does. Rewrite it
to say where the guarantee now lives; a test whose reason is false is a test
nobody can maintain.

**Acceptance:** every `init` write path goes through `execute`; the outer
lock no longer deadlocks; the pin fails when a name is added to either set;
`init`'s outputs and exit codes are unchanged except for the read-only
refusal; and a transaction left open by recovery does not produce an
`observe` on the next run.

---

### Task 7: The reference, the ADR and the walkthrough

**Files:** `docs/reference/journal.md`, `docs/reference/cli.md`,
`docs/adr/<next>-*.md`, `docs/walkthrough.md`, `tests/test_walkthrough.py`

- **The reference** says: what the three records are and which one a reader
  is looking at; that a refusal never enters permanent history; the expected
  states; that the two history records are now written together after the
  mutation, with the write-ahead guarantee held by the transaction file —
  this is the one published sentence decision 2 changes, and it must be
  changed precisely rather than deleted; that metadata means the mode, naming
  what is not preserved (ownership, timestamps, ACLs, extended attributes,
  hardlink identity, which `os.replace` breaks by construction); what a
  precondition guarantees and what it does not, in design §6's four bullets,
  without overstating the replacement case; that the lock is taken on the
  resolved artifact; the resolution flags; and that a missing preimage blob
  in a clone is normal rather than corruption.
- **The ADR** records the split into three records and the rule that a
  refusal is never permanent history, with the measurement that forced it:
  `init` runs on every session start and the history is never compacted, so
  one refusal per session would be a property of the model.
- **The walkthrough** changes only if a printed line or a count actually
  changes. Decision 2 is chosen so that neither does; verify it rather than
  assuming it, and if something moved, move the pin here.

---

## Status

Not started. Written 2026-09-01, revised the same day against two reviews.

## Context

Plan 1 shipped the record; two fix waves and three adversarial reviews closed
sixteen defects and left a residue that shares one cause. The design explains
the cause; this plan implements the smallest core that makes the record
truthful, and leaves groups, frontiers and the absorption planner to later
steps.

## Decision

Step 1a is the release gate for `feature/uninstall-and-restore`. Nothing
merges until it lands.

## Consequences

- `init` gains an ERROR where it silently succeeded, on a target whose mode
  denies writing. Release notes.
- `journal` gains resolution flags. No new subcommand, so the pinned set does
  not move.
- The history's shape, counts and vocabulary are unchanged, deliberately, so
  no clone on an older version is broken and no migration is needed.
- `adopt.take_over` remains unrecorded and documented as unrecorded until
  step 3's planner.
- One environment variable exists for fault injection. It is inert unless
  set, and a test says so.

## Self-Review

No task leaves the suite red. No behaviour change ships without a test proven
red by reverting it. The permanent history contains no record a refusal
produced. `_seen` is still enforced by its own test. `git status` is clean.
The pre-existing 1.5.2 defects in `TODO.md` are still listed there, unfixed
and unclaimed: they are not this plan's scope.
