# 0019: Portable contributions preserve foreign history

Status: Accepted

## Context

A retained consultation describes inspected knowledge in one workspace. Reusing
that knowledge in another project must preserve its evidence, origin and known
corrections without interpreting the original project's decisions or receipts as
local authorization. Copying a workspace database does not provide this boundary.

## Decision

An explicit, bounded JSON transfer capsule carries a selected historical receipt
and the complete recorded source workspace history. Whole-history disclosure is
acknowledged explicitly; selecting a receipt does not narrow that disclosure.
Foreign events retain their original fields and replay in their own chronology.
Destination storage retains imported material separately from native operational
events and refuses incompatible history prefixes.

Local contribution proposals explicitly map source evidence, dependencies and
predecessor history. Complete source inspection precedes the mapping event, and
local inspection, acceptance and incorporation remain separate requirements.
Declared local supersession preserves origin obligations; an independent successor
requires an attributed, inspectable disposition rather than omission.

Current eligibility evaluates reached origins against the newest compatible
history known to the destination, including through intermediary transfers.
Historical replay remains tied to what was known at each original event. New
receipt provenance frontiers prevent later corrections from silently restoring
old local authorization. Identical evidence bytes and repeated copies do not
establish independent corroboration or semantic equivalence.

The SQLite workspace advances through an explicit schema upgrade preserving
identity and logical event history. Transfer adds no adopter filesystem writer,
external fetch, code execution, authenticated authorship or automatic semantic
merge. Offline historical material does not establish live source freshness.

## Consequences

Full history makes correction coverage inspectable and replayable, but can include
unrelated content and exceed the initial transfer size bound. Assessment reports
these limits before export. Selective-history proofs and memory-entry transfer
need separate contracts. Local applicability and independence remain attributed
judgments whose semantic truth is not established by hashes or inspection records.

New assertions after transfer linking require replayable proof across their declared
reference closure and canonical ancestry. An unbound reference can remain unbound
when its exact canonical material is retained; live-only material that cannot be
reconstructed from history requires explicit retention before the assertion.
Missing proof never implies independence. This conservative boundary avoids adding
an unreviewed proof format or admitting a history that cannot subsequently replay.

Public contract discussion: [issue #8](https://github.com/everywan-dev/validated-memory/issues/8).
