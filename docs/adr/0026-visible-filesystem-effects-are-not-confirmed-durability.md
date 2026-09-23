# Visible filesystem effects are not confirmed durability

## Context

The journal publishes files, directories and links before it asks the
filesystem to persist the directory entry carrying the new name. A directory
barrier can therefore fail after the intended state is already visible. The
former helper suppressed both directory-open and directory-`fsync` errors,
while callers described any propagated publication error as if the target had
not changed. Those two behaviours hid opposite facts: success did not always
mean confirmed durability, and an error after visibility could not mean
rollback.

The same ordering applies to the journal's own evidence. A transaction file,
parked preimage or first local history can be fsynced inside a directory whose
entry, or whose newly created ancestor's entry, was never confirmed. Losing
that ancestor can remove the evidence on which recovery depends. Conversely, a
surviving transaction file is evidence for one unresolved mutation, but it is
not a complete copy of either append-only history.

Directory barriers are a local-filesystem capability rather than a portable
Python guarantee. A known platform or filesystem without that capability is
different from an unexpected permission, resource or I/O failure on a barrier
the platform supports.

## Decision

The journal distinguishes three filesystem outcomes: an effect that did not
become visible, an effect that became visible and whose required barriers were
confirmed, and a visible or indeterminate effect whose durability was not
confirmed. Only the first may be described as unchanged. The third is a
gating result and retains every available recovery artifact; it is never
routed through a generic `OSError` handler that aborts the transaction or
claims rollback.

Persistence is owned by one private deep module. Its interface accepts a
closed set of filesystem effects and hides temporary files, exclusive
creation, content flushing, namespace publication, cleanup and directory
barriers. The journal facade remains narrow: `adopting_run`, its opaque session
operations, targeted resolution and one high-level `repair_harness_link`
operation for the existing fail-open startup path. That operation exposes no
persistence primitive or persistence exception. Fault injection remains private
and separates a reported persistence failure from the existing
hard-process-crash points.

Every journal-owned directory created below its durability anchor is confirmed
before a durable child is trusted. The anchor is the adopter root, or an
explicit resolved store root; the journal gains no authority above it. When a
created chain needs confirmation, barriers proceed from the directory carrying
the deepest new name towards the anchor. The transient lock file itself need
not survive, and creation performed for locking does not count as durability
for later transaction, preimage or history state.

An external harness chain uses a stable lexical durability anchor: the common
ancestor of the adopter root and harness path, or the target volume root when
Windows paths have no common drive. A failed run therefore cannot move the
anchor by leaving a newly visible ancestor behind. The fail-open repair creates
only the requested parent chain and republishes even an already-correct link;
the complete chain is then confirmed back to the stable anchor. A visible repair
whose barrier fails gates the run, while an ordinary pre-visibility repair
failure retains the established warning-only behavior.

The transaction artifact records an explicit post-visibility barrier failure
before the run returns. Target, history and cleanup uncertainty are distinct
because their safe next operations differ. Recovery does not call `fsync`
again and call the earlier failure repaired. It first verifies the exact state
and establishes a new namespace operation where recovery is possible:

- an exact target postimage is republished and confirmed before history is
  completed;
- a readable permanent history is completed idempotently when needed, then its
  exact bytes are republished and confirmed before the transaction is removed;
- an absent, unreadable or inconsistent append-only history is not rebuilt
  from one transaction artifact;
- cleanup is repeated only after target and history durability are established.

The ordinary prepared transaction remains sufficient for a hard crash between
target publication and its published marker: the executor never opens a
transaction whose preimage and postimage are equal, so an exact postimage
identifies a completed mutation. The published marker remains useful because
it proves publication after a later writer changes the target. It is not a
license to complete history whose own durability is unknown.

Adoption identity continues to come only from permanent history. Under the
adopting lock, both histories are read before bootstrap. If either establishes
an identity, that history remains authoritative. If both are empty and there
is no transaction artifact, a fresh adopter may mint an identity. If both are
empty and any transaction artifact exists, adoption and `journal --check`
refuse before minting, bootstrap or unrelated writes. A common identity in
well-formed transactions is restoration advice, not authority to reconstruct
history; conflicting or damaged artifacts also refuse. Restoring matching
authoritative history re-enables ordinary recovery.

Bootstrap has no predecessor transaction. Its complete opening record is
installed and its parent barrier must succeed before any adopter mutation. A
retry that sees only that valid opening record republishes the same bytes and
confirms the new namespace operation before relying on it. If a power loss
removed the failed opening record, no later mutation was permitted and a fresh
bootstrap is safe.

On a known platform or filesystem without directory barriers, the contract is
conditional: file contents are flushed and namespace changes retain their
atomic-visibility guarantee, but survival of those names after power loss is
not claimed. Unexpected barrier errors on a supported location remain gating
errors. Public documentation states the distinction rather than silently
promising identical guarantees on every filesystem.

## Consequences

- Exit `1` can accompany a target that already shows the intended state. The
  diagnostic names the visible effect and retained recovery evidence instead
  of promising rollback.
- Transaction state grows local recovery information, but permanent history
  remains append-only and receives no record for an unconfirmed mutation.
- The vault, transaction and preimage directory chains become part of the
  durability protocol rather than incidental `mkdir(parents=True)` effects.
- A WAL-only tree fails closed instead of forking adoption identity or filing
  unrelated mutations under a newly minted identity.
- Exact-byte republication costs additional I/O on rare recovery paths and is
  preferred to treating a second `fsync` as proof about a failed first one.
- A transaction cannot reconstruct missing earlier history. Whole-entry loss
  therefore produces actionable refusal, not a misleading clean recovery.
- Torn JSONL tails and abandoned temporary files remain separate work; this
  decision does not weaken strict history parsing to absorb them.
