# The session-start run is bounded and a vault node that is not a regular file never blocks it

## Context

The `SessionStart` hook that restores the harness link runs
`init --harness-memory` under a 15 s hook timeout, and the harness kills a hook
that outlasts it. Release 2.5.1 could outlast it in two ways.

Three hangs were reproduced on 2.5.1. Each ended only when `timeout` killed the
run (exit 124):

- A transaction entry in `.validated-memory/transactions/` that is a symlink to
  a named pipe. `init`, `journal --check`, `journal --resolve` and
  `journal --repair` opened it, and opening a pipe for reading waits for a
  writer.
- A preimage slot in `.validated-memory/preimages/` that is a symlink to a named
  pipe. `init` blocked on it while holding the run-wide lock, having found that
  the slot existed and read it to compare its bytes.
- `.validated-memory/lock` as a dangling symlink. The lock breaker opened the
  path through the link, got `FileNotFoundError`, took it for a lock that had
  just gone and retried before it compared the clock with the deadline, so
  `init` looped forever. A named pipe at that path waited out the whole deadline
  and then reported a holder that does not exist.

The lock's wait for a live holder is ten seconds, and `init` can take the lock
twice in one run: once for the adopting scope and once for the guarded harness
repair of [ADR 0029](0029-the-harness-link-survives-a-refusal-that-does-not-name-it.md).
Each acquisition had its own ten seconds, so a run could wait about twenty, past
the hook's fifteen. ADR 0029 made the second wait deliberate and did not bound
the sum.

ADR 0029's guarded repair also read the harness path once, before it waited for
the lock. What it relinked could differ from what it had inspected by the length
of that wait plus the vault read. The inspection was two calls, `is_symlink` and
`readlink`, and a swap between them raised an uncaught error.

## Decision

**A vault node that is not a regular file is never opened.** One reader,
`read_regular_file`, reads a transaction entry or a preimage blob:

1. `lstat` the name before any open. Anything that is not a regular file is
   refused unopened, and a symlink is not followed, even one whose target is a
   regular file.
2. Open with `O_RDONLY | O_NONBLOCK | O_NOFOLLOW`, each where the platform has
   it: the `lstat` describes the name only at the instant it ran.
3. `fstat` the descriptor and require a regular file again before reading.

Each reader applies it to what it owns:

- A transaction entry that is not a regular file is a **damaged transaction**
  with the reason `it is not a regular file; it was not opened`. Damaged
  transactions are handled as before: the entry is kept, it gates `init` and
  `journal --check`, and `journal --resolve` and `journal --repair` refuse it.
  A regular damaged file keeps its 2.5.1 output.
- A **preimage slot** is tested with `lstat`, so a dangling symlink is not
  absent. A slot that exists and is not a regular file refuses the mutation that
  needs it before any effect, with an ERROR that names the slot and says to
  remove it by hand. It is neither opened nor removed: nothing proves whose it
  is, and the guard of ADR 0029 already treats such a node as residue. A regular
  slot whose bytes do not match its name, or cannot be read, is replaced as
  before, but only while a second `lstat` right before the removal shows it is
  still the regular file that was first examined. A slot that has become
  another kind of node or another file by then is refused like a slot that is not
  a regular file: it is kept, and the ERROR says to remove it by hand.
- A trusted preimage that is not a regular file is **unavailable** to
  `journal --resolve --restore`.
- The **lock node** is never opened and never broken. A lock path that is not a
  regular file counts as held until the deadline, which is now checked on every
  iteration of the wait, and the run then refuses with an ERROR that names the
  path and says to remove it by hand. It is its own regime of the guarded
  repair, `LOCK_NODE`, and not `LOCK_BUSY`: no process holds it, so the repair
  does not wait again and its WARNING names the path and the remedy instead of
  claiming a holder.

**The run has one lock deadline.** `init --lock-wait SECONDS` takes a finite
number, zero or more, with the journal's ten seconds as the default; any other
value is a usage error, exit 2. `init.run` computes one monotonic deadline when
it starts, `adopting_run` and `guarded_harness_repair` receive that same
instant, and `Lock` never moves it. The journal commands keep their own ten
seconds.

- Each attempt takes the lock, or breaks a dead or aged one, before it compares
  the clock with the deadline, so a spent budget still recovers a breakable
  lock.
- A lock that this acquisition broke, or found gone, earns one immediate retry
  past the deadline. That retry can lose the `O_EXCL` race, and then the
  acquisition refuses.
- A break that leaves the name in place answers false and the acquisition
  sleeps, so a lost break waits and does not spin.
- The hook passes `--lock-wait 3`. A concurrent `init` that finishes in well
  under a second still lets the later one relink, a holder that lasts longer
  withholds within three seconds, and the rest of the fifteen is left to the
  work `init` does under the lock. The outcome of a busy lock is the one of
  ADR 0029: the ERROR, exit 1 and the link withheld with its WARNING.

**The harness identity is read again before the relink.** `init` records
`(file type, link target)` from one `lstat` (the target is the link's text when
the type is a symlink). No device or inode number is part of it: a session that
publishes the same link again creates a new inode, which is not a change, and
inode numbers are not stable on 9p, drvfs or FUSE. A failure of `lstat` other
than an absent name, or a symlink that cannot be resolved because it loops, is
reported as `the harness path could not be read`, and the repair is withheld.

Both routes that relink under the guard read that identity again immediately
before `relink`: the guarded repair after its vault check, and the route taken
when the vault cannot be listed, which relinks without that check.

- A path that by then resolves to the project's `memory/` is
  `REPAIR_CURRENT`, whether or not its identity changed: nothing is reported, as
  for a link that was correct at the start, and the link is not published again.
- Otherwise an identity equal to the recorded one relinks.
- Any other difference, or a path that cannot be looked at, is
  `REPAIR_BLOCKED` with the reason `the harness path changed while the repair
  waited` or the unreadable one. `REPAIR_BLOCKED` is also the outcome for a lock
  path that is not a regular file. It names a node the journal holds no record
  of, so its WARNING does not end in `run journal --check`; `REPAIR_WITHHELD`
  remains the decision of the journal or the vault.

The healthy path is not part of this: it records the state it expects in the
journal when it links.

## Consequences

- A transaction entry that is a symlink to a valid transaction file, which 2.5.1
  read, is now a damaged transaction and is not parsed. The entry stays for
  inspection and gates `init` until the operator replaces it with a regular file
  or removes it.
- A harness path that cannot be looked at is a WARNING and does not change the
  exit code. In 2.5.1 the run raised an uncaught error and exited 1.
- With a spent budget, a concurrent start withholds the link until the next
  session. The repair after a refusal waits only for what remains of the run's
  deadline, so an adopting scope that waited the whole `--lock-wait` leaves the
  repair a lock it can take only if it is free or breakable. The next session
  start is the retry.
- A lock path that is not a regular file costs the run its lock wait before the
  refusal: three seconds under the hook, ten by default.
- The bound covers waiting for the lock only. The work a run does once it holds
  the lock, including `adopt.take_over`, is not bounded by `--lock-wait`, so the
  hook's fifteen seconds are not guaranteed.
- `status` reports a vault entry that is not a regular file as
  `journal: unreadable; run journal --check`, and `journal --check` now answers
  for it without opening it: a transaction entry as a damaged transaction, a
  preimage entry as retained private residue. Before, following that pointer
  could hang.

The limits that remain are declared and not closed:

- **The window between the re-read and the rename.** A process outside the plugin
  that replaces the harness path after the `lstat` of the re-read and before the
  rename in `relink` is not guarded against. The window includes the check of the
  parent directory and the creation of the temporary link that `relink` makes
  before it renames. The standard library has no compare-and-swap on a pathname.
  The relink never replaces a directory. This narrows the limit of ADR 0029,
  which spanned the lock wait and the vault read as well.
- **The window between the second `lstat` of a preimage slot and its removal.**
  A process outside the plugin that replaces a regular slot with wrong bytes in
  that window has its file removed and the preimage parked in its place. The
  window is one `lstat` and one `unlink`; the standard library has no
  compare-and-swap on a pathname, and no test can hold a run between the two.
- **Two runs breaking the same dead lock.** Each checks that the lock is the
  file it examined and then unlinks it by name, and the file can be replaced
  between the two calls, so both runs can end up holding the lock. The race
  existed before this decision, was measured under stress on 2.5.1 and on this
  branch, and is not widened by it.

## Rejected alternatives

- **A boolean `--no-wait`.** Every acquisition would wait zero, so every brief
  collision between two starting sessions, which is the ordinary case, would
  withhold the link.
- **Lowering the global lock wait and giving the second acquisition none.** It
  exports a constraint of one hook to every user of the lock, journal commands
  included.
- **A lock check in the hook script.** It repeats the stale-owner logic (pid, age
  horizon, identity) in shell, and it races with the lock it checks.
- **A detached retry after the hook returns.** It has no reliable lifecycle: no
  process owns it once the session ends, and the next session start is already a
  retry.
- **The inode in the harness identity.** Another session that links the same
  target publishes a new inode, a change that is not one, and inode numbers are
  not stable on every filesystem.
- **A scan that gates the whole run on any vault node that is not a regular
  file.** The node can appear after the scan, so each reader would still need its
  own check, and the run would stop for items that never touch the node.
