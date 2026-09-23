# Repair requires one explicit proof-carrying transaction

A malformed permanent history and an abandoned temporary are not, by their
names alone, evidence that validated-memory owns the bytes or knows the intended
result. Automatic startup repair, batch repair and PID- or age-based sweeping
would therefore turn ambiguous residue into silent data loss. Repair is an
explicit targeted operation, `journal --repair TRANSACTION_ID`, and succeeds
only when that one current-adoption WAL carries the exact history frontier,
canonical append payload or temporary provenance needed to prove the change.

`journal --check` remains strictly read-only, and ordinary `init` continues to
refuse malformed history. Tail repair atomically publishes the complete snapshot
formed by the descriptor-bound validated prefix plus the WAL's exact complete
append payload; it never truncates or completes a file in place. A missing
history is not a tail and is never reconstructed from one WAL. Temporary names
are unpredictable and exclusively created. Only an exact WAL claim may authorize
cleanup outside plugin-owned canonical duplicates; legacy, foreign, symlinked,
directory or otherwise unproven residue is retained.

The alternative global interfaces were rejected because selecting and ordering
multiple WALs would make the repair command a second recovery policy. Targeting
one transaction keeps authority, diagnostics, idempotency and retry local while
reusing ADR 0026's exact republication and truthful durability outcomes.
