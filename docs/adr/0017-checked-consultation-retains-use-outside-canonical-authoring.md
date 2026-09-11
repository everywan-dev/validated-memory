# Checked consultation retains use outside canonical authoring

Status: accepted architectural decision. Contract discussion:
[issue #6](https://github.com/everywan-dev/validated-memory/issues/6).

An opt-in checked-use operation records an exact existing consumer knowledge-unit
revision and a complete consultation receipt. Canonical authoring stays separate:
a refused use does not unpublish an authored conclusion, and existing validation,
recall, rendering and fail-open hooks gain no implicit use gate. A historical
record is never a current eligibility result; current use requires an explicit
read-only check.

Retain consultation declarations and history in one explicitly selected local
SQLite workspace outside registered adopter roots. Its standard-library writer
owns only this database and SQLite sidecars, with one transaction as the operation's
authoritative commit. Rows retain logical history; database bytes are not an
append-only file. This is a scoped exception to adopter-journal mutation ownership,
not permission to mutate canonical documents outside their existing workflow.
Pin SQLite ownership to the consultation store module and test adopter bytes
unchanged. DELETE rollback journaling, EXTRA synchronous durability and OS-managed
locks avoid inventing another crash-recovery or age-based lock-takeover protocol.
Read-only operations refuse recovery requiring writes; explicit recovery is separate.

Bindings carry attributed semantic declarations; generated hashes identify
captured revisions without proving entailment or authority. Initial applicability
uses declared key/value scope and conservative enrolled-input invalidation,
including membership. Selected-file hashing would miss new successors and
conflicts. Narrower invalidation, coordinated canonical publication, distributed
writers and transfer remain separate decisions.
