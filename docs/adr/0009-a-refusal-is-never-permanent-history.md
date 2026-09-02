# A refusal is never permanent history

The journal shipped with one record and a two-stage protocol: a `prepared`
line, the caller's own mutation, then a `committed` line. Two fix waves and
three adversarial reviews closed sixteen defects in it and left a residue
that would not close the same way, and this is what was decided about that
residue on 2026-09-02. Each item below was reproduced by execution before it
was believed.

**A `prepared` record could never be closed, and `--check` then called it
`applied`.** `_ensure_dir` wrote `prepared`, the `mkdir` failed -- a
read-only root was enough, no race, no crash -- and the record stayed open
in an always-versioned file. When a later run created the directory for
real, reconciliation saw the node exist and reported the old record
`applied`, which the reference defines as "the mutation happened; only the
closing `committed` record was lost". It did not happen. One junk record was
appended on every session start.

**An absence check was not a precondition.** `_ensure_file` tested
`path.exists()` and then called a writer that parked a preimage and turned
`create` into `replace` if something was there by then, silently. The
module's own promise -- an existing item is never touched -- had a window in
which it was false.

**The install wrote a file the adopter had made read-only, and hid it.**
Measured: a `.gitignore` at mode 0444 came back at 0644, with `0 error(s), 0
warning(s)` and exit 0. `os.replace` needs write permission on the
directory, not on the file.

These are not three unrelated bugs. `Run` handed its callers the protocol
rather than a result: it offered `write`, `append_text`, `prepare_op` and
`append_op`, and made each caller choose the operation, the durability, the
order of the two stages, and what to do when the mutation between them
failed. So `init.py` reimplemented fragments of one state machine four
times, and every correction crossed several dimensions at once. More review
does not exhaust that space; the space does not shrink.

The decision: **the record splits in three, by lifetime.** A local
write-ahead log (`.validated-memory/transactions/<id>.json`, one file per
open transaction) holds what a mutation intends and how far it got. The
permanent history (`journal.jsonl` and `.validated-memory/local.jsonl`)
holds consummated facts only. The derived artifacts, and the verdict log
alongside them, get no record at all: the command that wrote one rebuilds
or re-appends it.

**One executor over one path owns the protocol.** `execute(intention)` takes
the lock, authorises the path, checks the expected state, parks and verifies
the preimage, writes the transaction file, re-reads the state, publishes,
and appends the history. `prepare_op` and `append_op` leave the public
surface, so no caller can reimplement it.

**A refusal never enters the permanent history.** A precondition that fails
before anything is prepared writes nothing anywhere: it is a result the
caller renders. A failure after the transaction file exists closes that file
`aborted` and removes it. This is not tidiness. `init` runs on every session
start and the history is never compacted, so a project that is broken in
some way `init` refuses would append one refusal per session, for ever, to a
versioned file -- a growth property of the model rather than an
implementation detail.

**Both history records are appended together, after the mutation
succeeds.** The two-record vocabulary stays, because a reader already parses
it and because histories written before this carry unpaired `prepared`
records that still have to be reconciled. What moves is the write-ahead
guarantee: it is held by the transaction file, which is local, decidable and
removable, rather than by a line in a versioned file that no later run can
ever close.

**The mode is the only metadata.** A replacement copies the target's mode
onto the temporary before the rename, and both records carry the mode the
path ended up with, so a reversal can restore it. Ownership, timestamps,
ACLs, extended attributes and hardlink identity are not preserved, and the
reference says so rather than letting a reader assume a general promise. A
symlink's record carries no mode at all.

**The read-only bit is a refusal.** A target whose mode denies writing to
the current user -- asked of the file's own bits and the POSIX class of the
effective uid and group, never through `os.access`, with no exception for
root -- is refused with an ERROR naming the file and its mode. The bit is
how an adopter says do not write here, and writing through the directory's
permissions is not a trade this method makes.

## Considered options

- **Keep one record and add an `aborted` stage** -- rejected. That is what
  made `aborted` look like a stage of a mutation rather than the end of a
  transaction, and it puts refusals into the permanent, versioned,
  never-compacted file. The growth arithmetic above is the whole argument.
- **One `apply(change set)` operation owning everything** -- rejected by a
  design challenge, and the rejection holds: local recovery, permanent
  history and write authorisation are three problems, and three real callers
  (the fail-open link repair, the conflict-tolerant harness absorption, the
  derived views) do not fit a set-or-nothing shape. A change-set language
  broad enough to hold them puts the complexity back in the interface.
- **An executor that accepts `unrecorded=True`** -- rejected. It exposes the
  very policy it was meant to hide, and an executor that silently proceeds
  unrecorded on any journal failure is a general bypass. The two exceptions
  are declared by name and pinned by a test instead.
- **Raise the schema to 2** -- rejected as unnecessary here. Nothing changed
  shape; `transaction` and `mode` are additive.

## Consequences

- **`init` gains an ERROR where it silently succeeded.** A `.gitignore` the
  adopter made read-only is no longer rewritten: the run gates, and because
  the `SessionStart` hook runs `init` at every session start, it gates every
  session until someone `chmod`s the file or puts `/.validated-memory/` into
  `.git/info/exclude`. That is the intended price of honouring the bit.
- **`journal` gains resolution flags.** `--resolve ID` with `--accept`,
  `--restore` or `--abandon` is how an operator closes a transaction
  recovery cannot account for. "Refuse" is not a terminal state, and a
  transaction nothing will ever clear is a project stuck at its session
  hook. They are flags rather than a subcommand, so the pinned subcommand
  set does not move.
- **No schema bump, and no migration.** `transaction` and `mode` are
  additive fields on records that are otherwise unchanged, and the reader
  accepts a record whose `schema` is lower than its own, so an older reader
  ignores them and no clone on an older version is broken.
- **`adopt.take_over` remains unrecorded**, and documented as unrecorded,
  until its planner exists. A reversal driven by today's record cannot
  restore a harness memory directory.
- **One environment variable exists**, `VALIDATED_MEMORY_FAULT`, for
  crash-injection tests. It is read in exactly one function, it is inert
  unless set, and a test asserts that a run with it set to an unreached
  point is byte-identical to one without it.
