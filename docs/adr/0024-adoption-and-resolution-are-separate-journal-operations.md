# Adoption and resolution are separate journal operations

## Context

Constructing the journal's former `Run` both took a filesystem lock and
created the opening journal record. `init` needed those effects, but
`journal --resolve` did not: it compensated with a separate
missing-transaction check before construction, then searched the transaction
log again after construction. Without that compensation, refusing an unknown
transaction in a virgin directory left both `.validated-memory/` and
`journal.jsonl` behind while saying nothing had changed.

`init` also exposed and held the journal lock directly because its complete
scope includes the unjournalled harness memory take-over. The session's own
operations still need to take their locks independently, whether or not a
caller happens to hold a wider one.

## Decision

The journal facade exposes `adopting_run(root)` and
`resolve_transaction(root, transaction_id, resolution)` instead of exposing
the journal lock or a side-effecting `Run` constructor. Only an adopting run
may establish an adopter identity and it holds the lock for its complete
scope; resolution opens existing journal state, acts on exactly one named
transaction, and never adopts or recovers another transaction. This keeps
locking, identity creation, and the non-materializing missing-transaction
check behind the journal interface, while a transaction artifact that has no
history capable of establishing its adopter identity is refused as
contradictory state rather than used to create one.

`adopting_run` takes the lock before reading either history, validates or
creates the one adoption identity, and keeps the lock until its context exits.
It yields an opaque session with only `observe`, `execute` and explicit
`recover`. Those operations retain their own locks; re-entrance is an
implementation fact rather than caller knowledge.

`resolve_transaction` validates the resolution word first. It checks the exact
artifact without taking the materializing lock, returns the established
missing result when absent, and otherwise takes the lock and reads that exact
artifact again. The locked read is authoritative. Both histories must then
establish an existing adoption identity. Resolution acts on the already-read
item and does not enumerate the transaction directory again, survey adopter
paths, bootstrap history or invoke recovery.

## Consequences

- An unknown transaction leaves a virgin tree byte-empty, with the same
  refusal and exit code as before.
- A transaction file without adoption history is contradictory residue. It
  remains available for inspection and cannot cause a new adoption.
- `init` no longer imports the lock or constructs a session; its adopting
  context owns the complete concurrency scope, including harness take-over.
- Resolution still preserves accept, abandon and restore behavior, record
  ordering, kept-byte reporting and error rendering, while affecting only the
  named transaction.
- The filesystem and process dependency remains local-substitutable and is
  exercised with real temporary directories and subprocesses. No port or
  adapter is introduced for a single implementation.
