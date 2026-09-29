# The harness link survives a refusal that does not name it

## Context

The `SessionStart` hook exists to restore one symlink: the harness's memory path
pointing at the project's `memory/`. [The journal core](../design/2026-09-01-the-journal-core.md)
§4 declares that link an exception to the executor: it is restored, unrecorded
and with a WARNING, when the journal cannot be read or written at all.

Since the schema-one cutover the exception is narrower than that. A readable
history that `init` refuses with a topology gate -- two adoption lineages in one
legacy history, a fork of the frontier -- stops the whole run, link included,
and says nothing about the link. The session then starts without project memory,
although most such gates concern history records unrelated to the harness path.

The narrowing closed two real defects, and both must stay closed. A retained
transaction on the harness path owns that path's preimage, and relinking over it
destroys what recovery needs. Damaged history cannot prove that no such
transaction exists, so it cannot be treated as unavailable history.

The unavailable path and the unignored-vault path still have the first defect:
both relink without reading the vault's transactions. It also treats a lock held by another run as unavailability,
and that run may be writing a transaction for the harness path at that moment.
The check and the relink also run after the run-wide lock is released, so a
transaction can appear between them.

## Decision

The unrecorded link repair after a refusal is one guarded operation of the
journal protocol. It takes the run-wide lock, reads the history snapshot and the
vault, decides, relinks if allowed, and releases the lock; the decision and the
relink cannot be separated. It returns either `relinked` or `withheld` with the
reason, and `init` reports `withheld` as a WARNING naming the harness path and
the reason, so the session never loses its memory silently.

The harness path is made absolute before anything is compared. A recorded path
is equivalent to it when both name the same directory entry: the parent
directories resolve to the same directory and the final names are equal. The
final component is never followed, because the link itself is what may be stale.
Final names are compared without regard to case, and two existing entries that
are the same file are equivalent whatever their names: equivalence only ever
withholds more.

**A readable refusal** allows the repair only when all of the following hold,
read under the lock:

- the refusal happened before any adopting effect of the run;
- the history snapshot is usable;
- every outstanding `history.*` condition is `history.topology_gate`;
- no condition names a path equivalent to the harness path as its subject or in
  its pairing;
- every entry in the vault's transaction and preimage directories is a regular
  file (a symlink or any other node is never opened and withholds), is a canonical
  transaction artifact that parses, and no transaction names a path equivalent to
  the harness path.

Any other readable refusal withholds the repair: damaged, unsupported, identity
and bootstrap refusals cannot prove the absence of authority over the harness
path.

**A journal that cannot be read** keeps §4's availability promise, which is a
declared exception and not a proof:

- when another process holds the run-wide lock, the repair is withheld: that
  process may be creating a transaction for the harness path, and a later run
  restores the link once the lock is free;
- when the lock can be taken and the vault read, the vault conditions above
  apply;
- when the lock cannot be taken for any other reason, the vault is still read,
  without the lock, and the same vault conditions apply;
- only when the vault itself cannot be read does the repair run as §4 always
  allowed, unguarded, with its WARNING.

A failure that carries no marker of the protocol is treated as an unreadable
journal, so it reaches the repair only through these vault conditions.

**An unignored vault** gates the run without any journal refusal. Its repair goes
through the same guard, with the vault conditions of an unreadable journal.

In every case the repair never absorbs or parks a real directory, the project
target is validated exactly as on a healthy run, and the run's exit code does not
change: the refusal is still an ERROR.

`status` reports the refusal. When the read-only inspection that `journal
--check` performs finds history conditions, `status` adds a summary line with
their count and a WARNING pointing at `journal --check`. Its exit code does not
change, so [ADR 0002](0002-status-gates-consistency-and-only-reports-freshness.md)
holds: `status` still gates only what `validate`, `lint` and `derive --check`
gate.

## Scope of the supersession

This ADR supersedes only these parts of earlier decisions:

- the last sentence of the first discriminating case of
  [ADR 0025](0025-journal-history-uses-cross-artifact-frontiers.md) ("The
  declared fail-open harness-link repair keeps its existing narrow contract"):
  after a topology refusal the repair follows this ADR;
- in §4 of the journal core, "restored when the journal cannot be read or written
  at all": a lock held by another run no longer counts as unreadable, and a
  readable vault is checked first.

## Consequences

- An adopter whose history carries a topology gate that does not name the harness
  path keeps project memory in every session, with the refusal on stderr and in
  `status`, until the history is resolved.
- A link restored under a refusal is not recorded; its WARNING names the previous
  target, as the unavailable path already does.
- Two sessions starting together no longer race on the link: the one that waits
  for the lock withholds, and the link is restored by the one holding it if that
  run is an `init`, or by the next session otherwise.
- A clone without the vault sees in `status` only conditions of the repository
  history.
- The lock serialises validated-memory processes only. A process outside the
  plugin that replaces the harness path between the check and the relink is not
  guarded against; the relink never replaces a directory.

## Rejected alternatives

- **Relink after any readable refusal.** Damaged and unsupported histories cannot
  prove that no transaction names the harness path.
- **Keep the whole-run stop.** The link is the hook's only job, and a topology
  gate on unrelated records does not concern it.
- **Check first and relink after the lock is released.** A transaction can be
  opened between the two.
- **Compare paths as text.** Recorded local paths keep the spelling they were
  written with, and `--harness-memory` reaches `init` as the user typed it, so the
  same entry can have two spellings.
- **Make `status` exit 1 on a refused journal.** That changes ADR 0002's contract
  and fails the CI of every adopter with a divergent committed history, which is
  not a patch-release change.
