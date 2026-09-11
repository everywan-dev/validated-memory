# Consultation storage schema

Schema version 1. This is the strict local storage contract for
[checked consultation](consultation.md), selected in
[ADR 0017](../adr/0017-checked-consultation-retains-use-outside-canonical-authoring.md).
Use `consultation show HANDLE` for historical inspection. This reference describes
stored artifacts; create and revise them through the CLI, never by editing rows.

## Encoding and common types

Every object below is closed: all listed fields are required, no other field is
accepted. `?T` means JSON null or T, not an omitted field. Arrays are ordered;
sets must be sorted and contain no duplicates. Booleans do not satisfy integers.
Decode JSON with duplicate-key rejection at every depth and reject NaN/Infinity.
Canonical encoding C(value) is UTF-8 JSON with sorted object keys, compact
separators, ensure_ascii=False, allow_nan=False, no final newline. Every stored
payload string must equal the canonical encoding decoded to Unicode exactly.
H(value) is lowercase SHA-256 hex of C(value); B(bytes) hashes unmodified bytes.
UTF-8 byte bounds are checked before expensive parsing when possible.

- Digest: exactly 64 lowercase hexadecimal characters.
- UUID: canonical lowercase hyphenated UUIDv4 spelling; generated internally.
- Name: ASCII `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`.
- UnitID: existing contract ID pattern; do not impose the Name bound/prefix.
- Actor: nonblank Unicode string, at most 256 UTF-8 bytes.
- Reason: nonblank Unicode string, at most 4096 UTF-8 bytes.
- Timestamp: UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ`, valid calendar/time.
  Sequence determines chronology; timestamps need not increase.
- Scope: object containing 1..32 Name keys and nonblank strings of at most
  256 UTF-8 bytes. Matching is exact, case-sensitive subset matching.
- RelativePath: nonempty POSIX path, no empty, `.` or `..` component, no initial
  slash, NUL or backslash; at most 4096 UTF-8 bytes. Filesystem capture additionally
  enforces regular nonsymlink nodes and no symlink ancestors.
- RootPath: canonical absolute POSIX path, at most 4096 UTF-8 bytes, no NUL;
  physical capture verifies it, never normalize a stored invalid path into validity.
- Identity: `{project: UUID, unit: UnitID}`. Sort identities by `(project, unit)`.
- Node: `{device: integer >= 0, inode: integer >= 0}`.
- FileRevision: `{path: RelativePath, sha256: Digest, size: integer 0..1048576}`.
- CapturedFile: `{path: RelativePath, sha256: Digest, size: integer 0..1048576,
  text: string}`. Text UTF-8 bytes exactly reproduce size/hash. No newline conversion.
- Reference: `{identity: Identity, unit_sha256: Digest}`; sorted by identity.
- Candidate: `{identity: Identity, binding: Digest, unit_sha256: Digest}`.

Strings may not contain unpaired Unicode surrogates. Actor/reason attribution is
an assertion and does not authenticate any person. UUID identity is local to
this workspace and does not authenticate independent clones.

## SQLite format

Exact DDL, executed inside the initial BEGIN IMMEDIATE transaction:

```sql
CREATE TABLE workspace (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    workspace_id TEXT NOT NULL
);
CREATE TABLE events (
    sequence INTEGER PRIMARY KEY CHECK (sequence >= 1),
    id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN (
        'registration', 'relocation', 'checkpoint', 'binding', 'support-review',
        'conflict', 'choice', 'receipt', 'use'
    )),
    prior TEXT REFERENCES events(id),
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

Application validates exact user table/column/type/nullability/primary-key/index
and foreign-key definitions, no additional user tables, views, indexes or triggers;
SQLite's implicit events ID uniqueness index is expected. Do not compare raw
sqlite_master SQL whitespace. No sqlite_sequence table is needed. Reject extra
workspace rows, missing singleton, unsupported schema, unexpected SQLite storage
classes, and invalid event kinds/types. No physical migration or repair.

Use one database only, rollback mode DELETE, synchronous EXTRA, foreign_keys ON,
finite busy timeout (5000 ms), and read-only URI transactions for
show/check-use. Do not set a persistent pragma through a read-only connection.
Read existing journal mode and refuse unsupported modes before mutation; do not
silently convert a foreign WAL store. PRAGMA integrity_check must return exactly
`ok`; application event replay remains mandatory afterwards. Limit to 10,000
events and 32 MiB total UTF-8 payload bytes (decoded TEXT re-encoded to UTF-8).

Sequence is contiguous 1..N. Event ID is:

```text
H({kind: KIND, prior: PRIOR_OR_NULL, payload: PAYLOAD_OBJECT})
```

Payload carries `version: 1`. Database sequence and timestamp are excluded from
ID. They still undergo strict validation. No UPDATE or DELETE of either table.
Insert metadata once. Events must reference only lower-sequence events, including
all embedded handles. Roll back the entire operation on any failure.

SQLite may leave transient rollback sidecars. A hot journal prevents read-only
inspection until explicit recover succeeds; never delete sidecars manually.
Recover opens existing paths with mode=rw and lets SQLite recover, then validates.
If the recovered database has no user schema objects, recover may create the two
tables and metadata in one transaction, without a registration. Zero-length files
are included in this explicit empty case. Any nonempty partial schema refuses.
Existing files are never unlinked automatically. Initial creation/retry must inspect
schema again after obtaining BEGIN IMMEDIATE; a concurrent initializer may have
committed while this invocation waited. An empty valid workspace can have zero
events; ordinary registration can then create event 1.

## Semantic project inventory and workspace snapshot

ProjectInventory (closed object):

```text
{
  version: 1,
  project: UUID,
  root: RootPath,
  root_identity: Node,
  config: FileRevision,                 # path exactly validated-memory.md
  schema: ?FileRevision,                # exactly the declared in-root schema
  knowledge: [FileRevision],            # all knowledge/**/*.md, sorted by path
  support: [FileRevision]               # current binding support paths, sorted
}
```

Schema absence is explicit. Knowledge/support roles may name the same file;
metadata must agree across roles and physical capture bytes count once. Capture complete knowledge first and validate it to determine active units.
Include support from binding heads of active units only. Retain historical
bindings and their bytes when canonical units are superseded, but their obsolete
support paths no longer gate current capture. Complete knowledge membership
still detects a new successor and invalidates earlier receipts. Missing support
for an active unit refuses capture. Capture covers
all enrolled projects, at most 16, 4096 distinct input files, 16 MiB input bytes,
1 MiB each. No actor/reason, receipt/use/checkpoint or operational output enters inventory.

Root-neutral inventory digest for relocation:

```text
H({version: 1, config: inventory.config, schema: inventory.schema,
   knowledge: inventory.knowledge, support: inventory.support})
```

This deliberately excludes path/device/inode and project UUID, while preserving
file identities and bytes. Registration saves this digest for historical inspection. Relocation requires
an explicit current-registration checkpoint, never this initial digest implicitly.

Snapshot (closed object):

```text
{
  version: 1,
  workspace: UUID,
  projects: [ProjectInventory],        # sorted by project UUID
  heads: {
    registrations: [Digest],           # sorted IDs, one per current project
    bindings: [Digest],                # sorted IDs, one per bound Identity
    conflicts: [Digest],               # sorted IDs, one per conflict chain
    choices: [Digest]                  # sorted IDs, current choices only
  }
}
```

Each head must equal replayed state at receipt sequence immediately before its
insert. Current choices are the latest choices for current conflict heads; old
choices remain events but no longer apply. Snapshot digest is H(snapshot).
A receipt/use insertion does not change heads. New registration/relocation/binding/conflict/
choice or any input change changes snapshot. Checkpoint publication is operational
and does not change snapshot heads. Current checking constructs a new
snapshot and compares both digests and structured differences; history is never
silently rewritten.

## Payloads and chronological relationships

### Registration and relocation

```text
registration = {
  version: 1, alias: Name, project: UUID, root: RootPath, source: Name,
  root_identity: Node, inventory_sha256: Digest
}
relocation = {
  version: 1, alias: Name, project: UUID, root: RootPath, source: Name,
  root_identity: Node, inventory_sha256: Digest, checkpoint: Digest,
  actor: Actor, reason: Reason
}
```

Registration prior is null. Alias, project UUID and root must be unique at that
sequence; roots may not nest, alias names are never recycled, store may not be
inside an enrolled root or encompass it. Repeating register resolves its existing
identity before UUID generation. Exact alias/root/source repetition returns the
current matching registration artifact only after validation.

Relocation prior is the current registration/relocation head for its project;
alias/source/project are unchanged, root differs, old root is unavailable, new
root inventory matches the explicit checkpoint exactly. New root identity is
captured. A root replaced by a different inode at the old spelling does not
silently qualify as unavailable. Forked prior refuses. Historical replay validates
relationships and checkpoint linkage; it cannot re-observe past path availability.

```text
checkpoint = {
  version: 1, project: UUID, registration: Digest,
  inventory: {version: 1, config: FileRevision, schema: ?FileRevision,
              knowledge: [FileRevision], support: [FileRevision]},
  inventory_sha256: Digest, actor: Actor, reason: Reason
}
```

Checkpoint references the current registration/relocation head; inventory contains
exact current project content, excluding project UUID/root/device/inode. Digest
is H(inventory). Prior is null for the first checkpoint for that registration,
otherwise its current checkpoint head; forks refuse. Checkpoint publication does
not change the semantic snapshot. Relocation must name the latest checkpoint for
the project AND current registration. Its inventory hash must equal checkpoint
inventory hash. After relocation, old checkpoints cannot authorize another move;
create a checkpoint tied to the new registration head. All file members and bounds
match ProjectInventory. Historical replay recomputes the digest from the retained complete checkpoint
inventory and validates checkpoint registration/project/latest-head linkage plus
the relocation copied digest. Destination inventory equality was checked at
relocation time; destination bytes/inventory are not independently reconstructed
from an omitted historical payload.

### Binding and support review

```text
binding_or_support_review = {
  version: 1,
  identity: Identity,
  unit: CapturedFile,
  authority: Name,
  scope: Scope,
  actor: Actor,
  reason: Reason,
  support: [CapturedFile],             # 1..64, sorted by path
  references: [Reference]              # 0..64, sorted by identity
}
```

Initial binding prior is null and Identity has no earlier binding. Identity's
project must already be registered; authority equals its source. Unit path belongs
to that project's knowledge inventory and parsed ID equals Identity.unit. Support
paths belong to the same project. Reference projects exist; identities are distinct
within the list; self-reference may be retained as an explicit declaration; checked acquisition
refuses cycles, including self-reference, under the existing frozen eligibility rule.

Support-review prior is the current binding/support-review head for that Identity;
Identity, canonical unit path and hash remain unchanged. Unit size must also match.
New support/reference/scope declaration is explicit and actor/reason retained.
A no-change semantic request returns the head rather than creating an identical
review; semantic comparison includes scope/authority/support/reference/unit,
excludes attribution. A changed binding through bind refuses. Unit content changes
require canonical successor, not support-review. Old binding rows retain exact canonical and support text, sizes and hashes even
if no receipt was ever acquired. Validate their canonical text with the captured
Identity/hash; historical text is evidence of bytes, not current eligibility.
Retained bytes count against the 32 MiB store payload ceiling.

### Conflict and choice

```text
conflict = {
  version: 1,
  scope: Scope,
  candidates: [Candidate],             # 2..64, sorted by identity
  lineage: [{before: Identity, after: Identity, proof: [CapturedFile]}],
  actor: Actor,
  reason: Reason
}
choice = {
  version: 1,
  conflict: Digest,
  candidate: Candidate,
  scope: Scope,
  actor: Actor,
  reason: Reason
}
```

New conflict prior is null and lineage is empty. Every candidate names an earlier
binding head and its matching Identity/hash; candidates are distinct, currently
active and applicable in conflict scope when committed.

Conflict successor prior is its current conflict head. Scope stays identical
(changing applicability requires a separate conflict). `lineage` is an
explicit sorted one-to-one mapping only for changed identities, as supplied via
repeatable --replacement OLD=NEW. Unchanged identities map implicitly to themselves;
together these cover every old and new candidate exactly once. Changed identity
must retain project UUID. Its canonical unit must transitively supersede the old
canonical unit in that project. Retain `proof` as exact captured canonical documents
in successor-to-predecessor order: first document is the new candidate unit and
last document is the old candidate unit. Each preceding document's parsed
supersedes contains the following document's ID. IDs on the path are distinct;
every document's ID/project/path/hash matches its captured project inventory at
conflict creation. New candidate hash matches the new binding and the old endpoint
matches the prior candidate's historical hash. If an endpoint's canonical bytes
were edited without a successor, refuse instead of laundering that edit.
Select the shortest valid path, breaking ties lexicographically by UnitID sequence,
so identical requests remain deterministic. Limit each proof to 128 documents and
all proof text to the aggregate storage limit; fail actionably before insertion.
Historical replay parses retained proof text and verifies the same ID/hash/edge
relationships without requiring old filesystem state. Intermediate IDs are not
new conflict candidates. No cross-project supersession or changed cardinality.
A predecessor conflict no longer gates once its successor is committed;
predecessor choices do not carry over. Exact new bindings/revisions are recaptured.


Choice conflict must be a current conflict head; scope exactly equals its scope;
candidate exactly equals one captured Candidate. Prior is null for the first choice
for that exact conflict event, otherwise the current choice for it. Changing choice
retains its predecessor; no fork. Choice creation checks all candidate revisions
are still current, not only the selected candidate. Multiple applicable conflicts
must independently permit every participant in the checked closure.

### Receipt and use

```text
content = {
  units: [{identity: Identity, binding: Digest,
           path: RelativePath, sha256: Digest, text: string}],
  support: [{project: UUID, path: RelativePath, sha256: Digest, text: string}]
}
receipt = {
  version: 1,
  root: Identity,
  scope: Scope,
  max_bytes: integer 2048..1048576,
  snapshot: Snapshot,
  snapshot_sha256: Digest,
  content: CONTENT,
  content_sha256: Digest,
  inspection_text: string,
  inspection_sha256: Digest
}
use = {
  version: 1,
  root: Identity,
  receipt: Digest,
  snapshot_sha256: Digest,
  content_sha256: Digest,
  scope: Scope
}
```

Receipt/use prior is null. Units sort by Identity; support sorts by project/path,
with each visited unit and support file once. All text is strict UTF-8 decoded
unmodified input; hashing its UTF-8 encoding must reproduce stored hash. Preserve
CRLF as CRLF, do not use universal-newline conversion. Every content unit matches
snapshot file metadata, binding head, Identity/hash/path; every support item matches
a declared support revision of a closure member and snapshot metadata. Root is in
units. Content contains exactly the transitive reference closure and its support,
not an arbitrary superset or subset. Enforce at most 128 units and 512 reference
edges, detect real directed cycles, allow diamonds and return to a different unit
in the same project. All units must be active/non-hypothesis and bindings applicable
in scope; no ordering between measured and verifiable.

The inspection wire line is C of:

```text
{schema_version: 1, operation: "read", status: "inspected",
 root: Identity, scope: Scope, content: CONTENT,
 limitation: "Inspection is not checked use. Hashes do not prove entailment; anchor verdicts are not checked."}
```

`inspection_text` stores that exact UTF-8 line INCLUDING final newline;
inspection_sha256 is B of those bytes. Rebuild and compare exact bytes on load.
There is no receipt handle in this line and thus no self-referential digest.
After its successful write+flush, recapture inputs; if still equal, commit receipt.
Then emit a second JSON line containing the receipt ID. Failure
before commit leaves no receipt; content may already have been inspected. Failure
after commit reports the existing ID when stderr is available. No claim is made
that an OS flush authenticates agent reading or understanding.

The max-bytes boundary bounds combined bytes
of both lines, precomputing receipt ID and line two before the first output. At
most 1 MiB returned output also bounds the duplicated stored inspection string.
Second line shape:

```text
{schema_version: 1, operation: "read", status: "receipt recorded",
 id: Digest}
```

Historical receipt validation recomputes its content/snapshot/inspection digests
and chronological head relationships. Use receipt must name an earlier receipt;
all copied fields equal the receipt exactly. Use creation/current checking additionally
rechecks current snapshot and closure; show only validates historical integrity.
Repeated record-use returns the identical use ID only after current validation.
Repeated read may return the same content-addressed receipt, but must re-output
complete content and pass current checks before returning that handle.

## Refusal and replay order

1. Validate store path/applicability and SQLite mode; open without creation except
   authorized first registration or explicit empty-store recovery.
2. Validate SQLite integrity, exact schema and bounded row counts/payload sizes.
3. Validate metadata, contiguous sequence, row storage types, canonical JSON,
   payload shape/bounds, ID digest, timestamps and backward-only references.
4. Replay chronological identity/head transitions; refuse dangling handles,
   wrong kinds, forks, alias/root clashes and inconsistent copied revisions.
5. Validate receipt/use content and linkage against reconstructed historical heads.
6. For mutating semantic operations capture prospective enrolled inputs using
   only the operation-scoped overlays below; check-use captures committed inputs.
   Apply canonical validation and operation checks, then re-capture the same
   projected or committed inventory before commit or reporting current. Show never claims or needs current eligibility.

Any malformed row invalidates the entire store; do not return an apparently
usable prefix. A valid unknown future schema refuses. Busy/recovery/corruption
are different diagnostics. No refusal commits an event. Store-level physical
corruption is not repaired by inventing/removing event rows.

## Success envelopes

All successful operations emit canonical UTF-8 JSON followed by newline, with
`schema_version: 1`, `operation`, and `status`. Artifact-producing commands use
`id` for the event handle. Register/relocate additionally return `project_id` and
`alias`; checkpoint returns `project_id`; bind/review-support/record-use return
`root` (Identity). Conflict/choose return only common fields plus id. Read has the
two frozen lines above. Show returns id and `artifact` (full event with sequence,
id, kind, prior, payload, created_at), status `historical; not a current eligibility
check`. Check-use returns id with status `current`. Recover has no id. Historical
inspection is bounded by the store payload ceiling rather than read's evidence
acquisition max-bytes setting.

## Idempotent retries

Registration UUID allocation occurs only after identical-request lookup inside the
transaction. All immutable event retries must compare normalized semantic content
before constructing prior against the current head, so retried support review,
checkpoint, choice or conflict successor returns its existing event rather than
creating a fork or extra revision. Attribution changes alone do not create a new
semantic declaration; return the existing artifact without claiming new attribution.

## Historical replay boundary

Historical replay validates retained content/hashes, references, event kinds and
heads, conflict lineage, receipt closure and copied field consistency. Complete
project canonical validity and absence of unrelated superseding units were
observations at capture, represented by the recorded complete inventory; this
schema does not retain every unreturned canonical document's text in each receipt.
Do not claim to reconstruct or independently revalidate an entire historical
filesystem from hashes. Current-use checks reacquire and validate the whole
current corpus, including supersession. Local event hashing detects accidental
corruption; it does not authenticate a store against a writer able to rewrite
rows and recompute hashes.

Precommit input recapture must equal the snapshot that produced the inspection
line. A mismatch refuses, emits no handle, and commits no receipt even though
complete inspection content may already have reached stdout. Retrying performs
a new complete acquisition. Never replace the receipt snapshot with different
post-output content or reduce the final equality check to an advisory warning.

## Operation-scoped prospective capture

A semantic mutation validates the proposed next state without requiring the
obsolete input it explicitly replaces to remain available. Strict historical
store replay and prior/head validation always run first. The only overlays are:

- register adds the proposed root and initial registration to input enumeration;
- relocate substitutes only its project's proposed root/identity, after checking
  unavailable old root and exact current-registration checkpoint;
- bind adds only its first proposed binding/support declaration;
- review-support substitutes only its target binding's proposed support/reference
  declaration and scope, after verifying canonical unit bytes/path/identity are
  unchanged from its retained prior.

All other active declarations remain in force. If another active unit still
names a removed support path, that missing support still gates. A review can
replace old.txt with new.txt even if old.txt was renamed or removed; the old
binding retains old.txt text and hash, while proposed new.txt must be captured
and explicitly reviewed. No attribution is inferred and no bare refresh exists.
The transaction captures and recaptures the identical prospective inventory,
checking equality before publishing the new event/head. A failure publishes
nothing. Read, record-use and check-use have no prospective overlay and continue
to refuse against missing committed support until a review commits.
