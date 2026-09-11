# Incorporation separates acceptance from canonical observation

Status: accepted architectural decision. Contract discussion:
[issue #7](https://github.com/everywan-dev/validated-memory/issues/7).

Retain contribution proposals, challenges, dispositions and incorporation
observations in the consultation workspace. Acceptance records an attributed
decision about retained material; incorporation separately checks that exact
accepted content and evidence are now canonical. Existing authoring remains
explicit. A ledger transaction neither publishes Markdown nor guarantees an
atomic change across the ledger and adopter files. Partial authoring remains
visible until its incorporation is checked, and prior content is retained through
canonical supersession.

An accepted challenge requires review of its exact claim revision in the matching
scope. It gates current consultation without rewriting historical receipts or
claiming the disputed fact is false. Reacquisition after acceptance is required
even after resolution; receipts retain the relevant acceptance history separately
from existing input snapshots. Declared downstream uses and publication artifacts
have separate reconciliation observations. Disappearance from a dependency closure
is not evidence that a consumer or publication was reconsidered.

One explicitly upgraded workspace provides coherent identities and event order.
A separate optional correction ledger could be missing while ordinary use checks
incorrectly ignored it. Schema upgrade is transactional and preserves every old
logical event field and workspace identity; it is a narrowly scoped exception to
the prohibition on physical metadata/table replacement. New clients retain legacy
store support without implicit migration; older clients refuse upgraded stores.
Coordinated canonical writing, portable transfer and automatic semantic correction
remain separate capabilities.
