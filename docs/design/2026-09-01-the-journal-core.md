# The journal core: what one write must own — design (2026-09-01)

Plan 1 shipped the record: two artifacts, a two-stage protocol, and a
`journal` subcommand that reports and reconciles. Three adversarial reviews
over two fix waves then closed sixteen defects in it, and left a residue that
will not close the same way. This document says why, specifies the core that
§3 of `2026-08-30-the-journal-coverage-and-reversal-design.md` needs before
its interface can be built, and narrows two promises that document makes and
cannot keep as written.

It amends that design rather than superseding it: §1, §2, §5, §6 and §7 stand.
What changes is §3's mechanism and §4's account of crash consistency.

## 1. What the waves measured

Every item below was reproduced by execution before it was believed. The
first wave closed six defects an ordinary `init` reaches; the second closed
two regressions the first introduced. What follows is what remains, and it is
the evidence this design answers.

**A `prepared` record can never be closed, and `--check` then calls it
`applied`.** `_ensure_dir` writes `prepared`, `mkdir` fails — a read-only
root is enough, no race, no crash — and the function returns a `Finding`. The
record stays open. When a later run creates the directory for real,
`_state_of` sees the node exist and reports the old record `applied`, which
`docs/reference/journal.md` defines as "the mutation happened; only the
closing `committed` record was lost". It did not happen. `Run.write` and
`Run.append_text` leave the same residue when a known exception aborts them
before the install. One junk record is appended to the always-versioned
journal on every session start.

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
blob referenced by a record is present or still matches its name. An ordinary
`git clone` produces exactly that state, since the journal travels and the
vault does not.

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
found at the path (absent, file, directory, broken symlink, symlink leaving
the tree), the durability, whether the failure lands before `prepared` or
after it, whether the operation carries a digest, whether the outcome is an
ERROR or a WARNING, and whether the caller is inside the lock. Fixing one
cell of that space uncovers the next, which is exactly the history of the two
waves. More review does not exhaust it; the space does not shrink.

## 3. One operation

The core becomes a deep module with one external operation:

    apply(change set) -> result

The change set is a list of intentions, each carrying the state it expects to
find. The result says what happened to each, as data the caller renders.

**What the core owns**, and no caller may do for itself: the lock; the
bootstrap and the adoption id; path authorisation, lexical and resolved,
for every path in the set; the expected-state check; parking and verifying
preimages; staging, publication and the durability barriers; metadata
preservation; the terminal states; and the group.

**What the caller supplies** is intention and expectation — "create this
directory if the path is still absent", "append this rule if the file still
hashes to this digest", "point this symlink at that target, whose previous
target was this" — plus how to present the result. `init` keeps its findings,
its outcomes and its exit code; it stops keeping a copy of the protocol.

`prepare_op` and `append_op` leave the public surface. `observe` stops being a
call: an observation is what the core records when an expected state is
already satisfied, which is the only thing it ever meant.

This is the module §3 of the earlier design assumed it had. Wrapping today's
`Run` in a new layer while leaving its methods reachable would not deliver
it: the four reimplementations in `init.py` would remain legal.

## 4. Terminal states

A record needs a third stage, `aborted`, written when a mutation the core
prepared is known not to have happened.

Today the protocol has two stages and one silence. `prepared` with no
`committed` means "either the mutation happened and the closing record was
lost, or it never happened" — and the reconciler resolves that ambiguity by
looking at the filesystem, which is why it answers `applied` for a directory
some later run created. A failure the core observed is not ambiguous, and
recording it removes the guess:

| Stage | What it asserts |
|---|---|
| `prepared` | the core is about to act, and holds the preimage |
| `committed` | the mutation was published |
| `aborted` | the mutation was refused or failed, and did not happen |

`journal --check` then reports an open transaction only when the process
died between records — a genuine unknown — and says `aborted` transactions
are closed. An `aborted` record is never inverted by a reversal, and it is
still evidence: it names a path the plugin wanted and could not have.

`STAGES` is validated on read, so a reader that does not know `aborted`
refuses the file. That is what `schema` is for: this bumps the record format
to **2**. A plugin at schema 1 meeting a schema 2 journal already refuses
with the message that says to upgrade.

## 5. Preconditions, not observations

Every operation in a change set carries the state it expects at its path:
absent, or a content digest, or a node type. The core re-reads immediately
before publishing, under its own lock, and a mismatch is a refusal of the
whole set — nothing written, nothing recorded but the `aborted` records that
say why.

This replaces three separate patches. The `create`-that-became-`replace` has
no window, because "absent" is checked by the party that writes. The parked
preimage cannot go stale between the park and the install. And a skill that
paged content to a user and took a confirmation gets the guarantee §3 already
promised: a file that changed under it is a refusal, not an overwrite.

## 6. Metadata

The install copies the target's mode onto the temporary before the rename, so
a replacement preserves what the adopter set, and the record carries the mode
so a reversal can restore it.

A target whose mode denies writing to the current user is a **refusal**, not
a mutation performed through the directory's permissions. The read-only bit
is how an adopter says do not write here, and `os.replace` is not entitled to
route around it because POSIX lets it. This changes behaviour: `init` gains an
ERROR where it silently succeeded. That is the correct direction, and the
message names the file and its mode.

## 7. The lock

The lock file already carries the owning pid. The core uses it:

- before breaking a lock, check whether that pid is alive (`os.kill(pid, 0)`);
  a live owner is never broken, whatever the age;
- `__exit__` releases only a lock it still owns, identified by the
  device and inode of the descriptor it opened, not by the pathname;
- the age horizon remains as a last resort for a dead owner whose lock file
  outlived it, and it stops being the only test.

The caller-must-hold-the-lock rule disappears with `apply`, which takes it.

## 8. Group atomicity: the promise, narrowed

§3 says the interface "applies the whole set or nothing". Over several
arbitrary paths that cannot be delivered: POSIX has no multi-file atomic
rename, and a crash between two `os.replace` calls leaves one published and
one not. The honest promise is **recoverable atomicity**: no partial success
is ever reported, and a crash leaves a state the next run detects and
resolves before doing anything else.

The protocol, all of it under one lock:

1. authorise every path, and check every expected state;
2. park and verify every preimage, and fsync them;
3. write and fsync `group_prepared`, carrying the transaction id, the ordered
   manifest, the operation count and a digest of the manifest;
4. build and fsync every temporary, publishing none;
5. publish each path atomically, fsync its directory, and record each as
   `committed`;
6. only when all are published, write one `group_committed`. That record, not
   the per-path ones, is what makes the set's success visible;
7. a known failure before step 5 gives `group_aborted`; a failure during it
   restores the paths already published and gives `group_rolled_back`;
8. after a crash, no mutating command proceeds while a group is open.
   Recovery compares each path in the manifest against its preimage and
   postimage: restore what is still at the postimage, leave what is already
   at the preimage, refuse on anything else.

Partial state can exist on disk until recovery runs. Promising otherwise
would be false. This is a write-ahead log with undo images and one active
group — not a version control system: no branches, no checkouts, no general
conflict resolution.

`run` is not a group. One invocation may apply several change sets, and the
transaction id, not the run id, is what groups a set.

## 9. A merged journal is not a linear history

`merge=union` settles the syntax of a conflict on `journal.jsonl`. It does not
settle its semantics, and the merge-story commit claims more than it delivers.

Each transaction gains a **parent**: the digest of the closing record of the
transaction that preceded it in the same artifact. Two transactions naming the
same parent are a fork, and a fork is detectable without heuristics.

- `journal --check` reports a forked history as a finding, naming both
  branches. It does not guess an order.
- `uninstall` refuses a forked history outright. Inverting a fork means
  choosing which of two incompatible preimages was true, and nothing in the
  file says.
- The way out is an explicit reconciliation record, written by a command, that
  names the chosen post-state and becomes the single parent of what follows.

Reversal reads a chain, not a file.

## 10. What this does not fix

Named so it is not assumed. Four defects measured on shipped 1.5.2 are
outside this core and are `init`'s own: `--harness-memory` given a path inside
the project, which parks the project's memory and leaves a self-referential
symlink at exit 0; a plain file where a directory goes, reported `kept`; a
directory where a file goes, after which `lint` and `status` raise instead of
returning a finding; and `_sync_symlink`'s `unlink`-then-`symlink_to`, whose
crash window leaves the harness with no link while the WARNING says the
session is unaffected.

A negation line in an adopter's `.gitignore` (`!/.validated-memory/` after the
rule) leaves the vault unignored while `init` sees the literal entry and
writes nothing. Measured: `local.jsonl`, carrying an absolute harness path,
becomes stageable. Deciding how much of git's ignore semantics `init` may
implement, or whether it may ask git, is its own decision and does not belong
to the core.

Coverage (§5), rejection records (§6) and `uninstall` (§7) are unchanged by
this document.

## 11. Testing

The seam does not move: the CLI as a subprocess over fixture adopter trees,
asserting exit codes, output and produced files.

- **The failure-then-repair round trip**, end to end, for every known failure
  the core can hit: provoke it, repair the environment, run again, and assert
  `journal --check` is clean and no record from the failed run is open.
- **Crash injection at every protocol point**: between the group record and
  the first publication, between two publications, and between the last
  publication and `group_committed`. Each must leave a state recovery
  resolves, and recovery must be idempotent.
- **The completeness pin extends**: today it asserts every write in the
  package reaches the journal. It must also assert that nothing outside the
  core reaches the stage-writing surface, so a future caller cannot
  reimplement the protocol the way `init.py` did.
- **Mode, preimage and parent are pinned by their own tests**, since each
  replaces a silent behaviour with a refusal.

## 12. Sequence

1. The core: `apply`, terminal states, preconditions, metadata, the lock,
   the group protocol and recovery, with `init` migrated onto it and the
   stage-writing surface made private. Schema 2.
2. Parents and fork detection, with `journal --check` reporting a fork.
3. Coverage (§5), which the two design challenges agree comes before the
   public write interface, and whose digest-bound dispositions give the
   preconditions their stable identity.
4. The public write interface (§3), delivered behind one subcommand taking a
   versioned request, migrating `bootstrap-from-repo` page by page. The
   pinned subcommand set moves once.
5. Rejection records (§6), then `uninstall` (§7), which may assume a chain.

Nothing here is released until step 1 lands: the record's whole purpose is to
be trustworthy, and a record that can say `applied` for a mutation that never
happened is not.
