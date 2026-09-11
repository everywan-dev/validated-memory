# Incorporation storage schema

**Unreleased source-tree addition.** Published 2.2.0 supports the original
consultation commands and schema 1; it does not provide this lifecycle or schema 2.
Current source storage is schema 3; [Transfer storage](transfer-storage.md)
supersedes current creation/upgrade behavior and adds origin projection. The
schema-2 shapes and layout below remain the incorporation compatibility contract.

Schema version 2 extends [consultation storage](consultation-storage.md) with
retained proposals, challenges, inspections, dispositions and downstream
observations. The [incorporation reference](incorporation.md) documents commands
and an executable synthetic workflow. Every object below is closed: unknown
fields refuse. All unchanged legacy payloads retain their original shapes.

Fresh registration and explicit empty-store recovery now create schema 3. Existing
complete schema 1 stores continue ordinary consultation without implicit
migration; schema-1 lifecycle operations require explicit `upgrade`, while
schema-2 lifecycle operations remain available. Older schema-1-only
clients refuse schema 2/3. The JSON envelope `schema_version: 1` is a separate
wire-format version and does not identify the database schema.

## Encoding and event envelope

Reuse all closed scalar/object definitions, canonical encoding C, hashes H/B,
ordering and bounds in [Consultation storage](consultation-storage.md). No unknown
fields. Integers exclude bool. Every ID reference points strictly backward in
sequence and has its specified kind. Event envelope remains exactly
`{sequence,id,kind,prior,payload,created_at}`; ID remains
`H({kind,prior,payload})`. No timestamp or sequence enters the ID. All existing
event payloads, Snapshot and ProjectInventory shapes stay unchanged, except the
explicit receipt payload version below. New payload versions are integer 1.

New kinds, in this exact order after the nine legacy kinds:
`proposal`, `challenge`, `inspection`, `decision`, `incorporation`, `resolution`,
`address`, `publication`, `reflection`.

Aliases are resolved before payload construction. Retain UUID identities, not
aliases. Actor and reason use legacy bounds. Arrays described as sorted are
strictly sorted and unique. A CanonicalFile is a CapturedFile whose path starts
`knowledge/` and ends `.md`, and whose parsed frontmatter has the stated UnitID.
All captured files preserve UTF-8 bytes exactly, including CRLF.

## Lifecycle payloads

In the following shapes, every displayed field is required. A union has the
explicit tag and exactly the fields of that alternative, never optional spare
fields. All new event priors are null except decision and renewed proposal.

### Proposal

```text
{version:1, identity:Identity, unit:CanonicalFile, authority:Name,
 scope:Scope, support:[CapturedFile], references:[Reference],
 predecessors:[{identity:Identity, unit:CanonicalFile}],
 expected:Snapshot, installed_binding:Digest|null, actor:Actor, reason:Reason}
```

`identity` is the new target. `unit.path` is the requested future canonical path,
not the external candidate input location. Its ID equals identity.unit.
Support has 1..64 members sorted by path; references 0..64 sorted by identity.
Predecessors have 0..4096 members sorted by identity and exactly equal the unit's
direct `supersedes` list, all in the target project. The submit live preflight
requires each predecessor active and retains its unchanged bytes. Authority
equals the retained registration source at submission.

`expected.projects` contains full prospective ProjectInventories after inserting
the candidate and applying prospective active support selection. Its workspace
equals this workspace. Its heads are exactly the pre-insertion retained heads;
there is no synthetic binding ID and no binding-head entry for the candidate.
Initial submission has installed_binding null and prior null. A renewal is another
proposal event with prior equal the named previous proposal. Its identity/unit/
authority/predecessors must equal the prior exactly. expected, installed_binding
and attribution may change; support/references/scope can differ only through the
explicit complete-declaration renewal command, with fresh inspection/acceptance.
Retained rows do not authenticate which CLI flags were used; changed declarations
are explicit immutable proposed material, never mutation of the prior event. No mutable proposal
head exists: decisions address each immutable proposal separately. If absent,
installed_binding is null and capture is prospective as for submission. If
already installed, it is the exact current matching binding ID included in
expected.heads; expected.projects is the actual full inventory. Retained original
predecessor bytes must still match canonical lineage; now-retired predecessors
need not be active. Installed candidate bytes/path/ID changes refuse renewal. The current installed
binding must match the renewed explicit declaration; a changed declaration may
be proposed through the complete-declaration renewal form.
This intentional hybrid is a proposal expectation, not a consultation receipt.
Replay requires current historical heads equality, matching registered projects,
candidate inventory entry equality, and matching declared support inventory.
Replay checks predecessor text/identity and supersedes correspondence; it cannot
revalidate uncaptured unrelated canonical text and makes no such claim.

Live submit validates the prospective whole configured corpus and declaration,
then recaptures candidate bytes plus the same prospective inventory. Destination
path and ID must be absent before insertion. The declaration's references use
exact currently active captured revisions. The total proposal inspection wire
budget must fit 1048576 bytes including both inspection output lines; test this
before storing a proposal that could never be accepted through inspect.

### Challenge

```text
{version:1, target:{identity:Identity,binding:Digest,unit:CanonicalFile},
 kind:"factual"|"policy", statement:{sha256:Digest,size:integer,text:string},
 scope:Scope, actor:Actor, reason:Reason}
```

Statement is nonblank strict UTF-8, 1..65536 bytes, with matching byte hash/size.
Its external filename is deliberately not identity-bearing retained material.
Target binding is the current retained binding/support-review head; its identity
and unit equal the copied fields exactly. No live target read. Challenge scope
must fit the target binding's applicability: binding.scope is a subset of scope.

### Inspection

```text
{version:1, submission:Digest, observed_head:Digest, max_bytes:integer,
 inspection_text:string, inspection_sha256:Digest}
```

Submission kind is proposal, challenge, binding or support-review. observed_head
is the ID of the immediately preceding event at insertion, checked during replay.
An inspectable handle guarantees nonempty history, so null is forbidden. Compute
this field and the resulting inspection ID before output under the writer lock.
Each repeat inspect emits full content and creates a new occurrence: the last
event is now different, even if the retained binding bytes are identical. max_bytes is 2048..1048576. The exact
first wire line below must equal inspection_text, including its trailing newline;
inspection_sha256 is B(its UTF-8). Replay recomputes it from the referenced full
event. Both wire lines together fit max_bytes. Emit and successfully flush first
line before append/commit; the second line identifies the committed inspection.
No live evidence check or review-frontier change occurs.

### Decision

```text
{version:1, submission:Digest, inspection:Digest,
 outcome:"accept"|"reject"|"defer", actor:Actor, reason:Reason}
```

Submission kind for a decision remains proposal or challenge. Inspection must
name this exact submission. Initial prior is null; otherwise
prior must be the current decision for this submission. Exact full payload
equality with the current decision returns that event before comparing the
supplied prior. Every other transition requires the exact current prior,
including a same-outcome transition with changed attribution/inspection. Replay
rejects a redundant identical head payload as well as forks. Decisions never
erase earlier incorporation/resolution records.

### Incorporation

```text
{version:1, proposal:Digest, decision:Digest, binding:Digest,
 actor:Actor, reason:Reason}
```

Decision is the current accepted decision for this proposal. Binding is the
current binding head for proposal.identity; its semantic declaration exactly
equals proposal identity/unit/authority/scope/support/references. Attribution is
excluded only from that declaration comparison. Historical heads must equal
proposal.expected.heads with this one added binding ID; all other head arrays
unchanged when installed_binding is null. Otherwise current heads must equal
expected.heads exactly and binding must equal installed_binding. Live inventories
must equal proposal.expected.projects and recapture.
This records a verified installation observation, not a canonical write.

### Resolution

```text
{version:1, challenge:Digest, decision:Digest, inspection:Digest,
 remedy:{kind:"incorporation",incorporation:Digest,binding:Digest,proof:[CanonicalFile]}
       |{kind:"review",binding:Digest},
 actor:Actor, reason:Reason}
```

Decision is the current accepted decision for this challenge. Exactly one
resolution per decision. Incorporation remedy points to a currently accepted
incorporation and current matching binding. Proof has 2..128 files, from that
incorporated successor to challenge target, canonical paths, distinct IDs,
same project, and successive explicit supersedes edges. First file equals its
incorporated binding unit; last preserves target ID/hash/text, allowing a moved
old endpoint inside knowledge as in the legacy lineage contract.

Review remedy names a current binding or support-review with the same target
identity and exact canonical unit bytes/path, with binding.scope subset of
challenge.scope. Both alternatives require inspection.submission to equal the
exact current resolved binding ID; inspection sequence must be strictly later
than the accepted challenge decision. The resolution is the fresh unchanged-
meaning assertion; no fabricated support-review event is required. Both remedies
require live binding/support checks and stable recapture, without acquiring an
unrelated conflict choice. Resolution does not delete the acceptance frontier.

### Address

```text
{version:1, challenge:Digest, decision:Digest, resolution:Digest,
 old_use:Digest, new_use:Digest, mode:"replacement"|"dependency-removed", consumer_proof:[CanonicalFile],
 actor:Actor, reason:Reason}
```

Decision/resolution must be the current accepted challenge disposition and its
resolution. Old use receipt includes exact challenge target revision and has
matching scope; new use scope equals old scope. Address creation, not use insertion,
is the fresh post-decision observation; an older use is allowed only if current
under the same normal eligibility check and stable recapture. Consumer proof is empty for identical consumer identity and canonical
revision, otherwise 2..128 canonical files from new consumer to old consumer,
same-project supersedes edges and exact endpoint revisions. Reuse legacy proof
path/hash/ID validation. For mode replacement, new receipt includes the resolution successor revision
and binding, or reviewed target revision and binding, respectively. For explicit
mode dependency-removed, the exact challenged identity/hash must be absent from
new receipt content; attribution explains the intentional dependency removal.
Both modes retain the same consumer/proof, scope, current decision and eligibility checks. New use must
pass ordinary current eligibility and recapture. An address is an attributed
connection, not proof that the new claim is semantically sufficient.

### Publication

```text
{version:1, challenge:Digest, project:UUID, use:Digest,
 file:CapturedFile, actor:Actor, reason:Reason}
```

Use is an affected historical use of this challenge and its consumer project
equals project. File is an existing regular nonsymlink in-root UTF-8 file of at
most 1MiB, outside the explicitly excluded paths. Retain bytes, recapture before
commit, and never execute. No accepted disposition is required to declare the
publication. This records a declared dependent artifact, not distribution.

### Reflection

```text
{version:1, publication:Digest, address:Digest, file:CapturedFile,
 actor:Actor, reason:Reason}
```

Address challenge and old_use equal the publication challenge/use. File path
equals publication.file.path, but bytes must differ from the original. Validate
the address's current decision/resolution and current new use; read/recapture the
tracked file. Do not compare against a different path or infer semantic repair.
Multiple later reflection observations may be retained; exact retry requires
current file/use validation before returning.

## Retry rules needing no fabricated prior

Proposal/challenge exact complete payload retries return the existing event;
different attribution remains distinct. Inspection always creates a new chronological occurrence using observed_head,
while emitting complete historical material before its new handle.
Decision follows the head-specific rule above. For incorporation, resolution,
address, publication and reflection, these operations use exact complete payload
retry after all current live checks required by that operation. A resolution
with a different payload for an already resolved decision refuses instead of
adding a second resolution. Attribution changes are therefore not silently
discarded by the legacy retry policy: the new attributed observations compare
complete payloads.

## Receipt 2 and live review gate

Schema 2 newly acquired receipts have the complete legacy receipt payload with
`version:2` and exactly one additional field `review_frontier:[Digest]`, strictly
sorted, at most 10000 IDs. Schema 1 emits version 1 with no added field. Schema 2
replays either version. No change to Snapshot, content, inspection_text, wire
format, use payload or their existing digest formulas. Receipt event IDs already
hash the full payload and consequently include the frontier.

For a receipt closure and scope, compute all earlier decision events with
outcome accept and submission kind challenge whose exact target identity/hash
occurs in content.units and whose challenge.scope is a subset of receipt.scope.
Do not filter by latest disposition or resolution. The resulting sorted IDs
must equal review_frontier; version 1 has implicit []. Validate this equality
against chronology before inserting/replaying the receipt. Repeated acceptance
decisions each enter this set, including acceptance after rejection.

Separately, an open gate is a matching challenge whose latest decision accepts
and has no resolution referencing that exact decision. Historical receipt/use
replay evaluates state at its own event sequence, never later live state.
Current read/record-use/check-use evaluate current open gates; current uses also
compare their receipt frontier with the newly computed frontier. Resolution or
reversal never makes a pre-acceptance receipt eligible again. Clerical proposal,
inspection and non-acceptance decisions do not affect frontier or legacy heads.

## Output envelopes

Reuse canonical UTF-8 JSON plus newline and `schema_version:1` for command wire
envelopes. The database schema is a separate field only where explicitly shown.
Legacy operations remain byte-contract compatible except IDs of new receipts.

```text
upgrade: {schema_version:1,operation:"upgrade",status:"upgraded",storage_version:3}
new artifact: {schema_version:1,operation:COMMAND,status:"recorded",id:Digest}
inspect first: {schema_version:1,operation:"inspect",
 status:"historical material; not a current evidence check",id:SUBMISSION_ID,
 artifact:FULL_SUBMISSION_EVENT}
inspect second: {schema_version:1,operation:"inspect",status:"inspection recorded",id:INSPECTION_ID}
```

Artifact commands are submit/renew/challenge/decide/incorporate/resolve/address/
track-publication/reflect. Inspect's event ID and bound use the exact two-line
encoding above. Post-commit output errors report known committed ID on stderr
where available; no invented success on failed delivery.

Reconcile has this closed shape:

```text
{schema_version:1,operation:"reconcile",status:"reported",id:CHALLENGE_ID,
 decision:Digest|null, disposition:"none"|"accept"|"reject"|"defer",
 review:"not-open"|"open"|"resolved", resolutions:[Digest],
 uses:[{id:Digest,dependence:"declared",
   observation:{status:ObservationStatus,reason:string},
   addresses:[{id:Digest,new_use:Digest,observation:Observation}],
   publications:[{id:Digest,observation:Observation,
     reflections:[{id:Digest,observation:Observation}]}]}]}
```

ObservationStatus is exactly `current`, `stale snapshot`, `review required`,
`conflict/invalid binding`, or `unavailable`. Observation denotes the same closed
`{status,reason}` object everywhere. IDs arrays and nested event arrays sort by
ID, not timestamps. All historical resolutions are listed even after reversal;
review uses only current disposition/resolution. Each publication observation
compares the original tracked bytes; each reflection observation compares its
own retained bytes and linked current new use. Non-current source observations
have an actionable reason; current reason is the empty string. Complete
historical use enumeration precedes live capture, with unavailable observations
on capture failure. Output is bounded to 128 uses,128 publications,1MiB total,
or refuses without a truncated result. Full report exits 0 regardless of rows;
store corruption/over-bound exits 1, usage 2.

## Exact schema 2 DDL and migration

The following DDL and 1-to-2 algorithm preserve the earlier unreleased format as
an exact compatibility reference. Current upgrade targets schema 3 instead;
[Transfer storage](transfer-storage.md) provides that layout and current behavior.
Do not use this historical section as instructions to edit a database manually.

```sql
CREATE TABLE workspace (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), schema_version INTEGER NOT NULL CHECK (schema_version = 2), workspace_id TEXT NOT NULL);
CREATE TABLE events (sequence INTEGER PRIMARY KEY CHECK (sequence >= 1), id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK (kind IN ('registration','relocation','checkpoint','binding','support-review','conflict','choice','receipt','use','proposal','challenge','inspection','decision','incorporation','resolution','address','publication','reflection')), prior TEXT REFERENCES events(id), payload TEXT NOT NULL, created_at TEXT NOT NULL);
```

Exact object/column/index/FK/storage-class validation remains version-specific.
Keep a frozen literal legacy KINDS/DDL rather than deriving v1 DDL from the new
full kind list. No extra tables or indexes remain after migration. Foreign keys
must be ON for the complete upgrade transaction; journal DELETE, synchronous
EXTRA, five-second busy timeout, same safe store/sidecar checks.

Historical 1-to-2 upgrade algorithm, all writes under one BEGIN IMMEDIATE:

1. Open existing store, obtain lock, then validate supported exact layout,
   metadata, integrity, bounds and every chronological event. Do not bootstrap a
   missing/empty store for upgrade. Schema 2 is a validated no-rewrite retry.
2. Capture workspace UUID and old rows in bounded memory, keeping the original
   payload TEXT strings exactly; do not decode/re-encode them for insertion.
3. DELETE all old events in reverse sequence order, so self-referencing prior
   children disappear before their parents and foreign keys stay enabled.
   DROP TABLE events and DROP TABLE workspace. These changes are uncommitted.
4. Execute exact schema 2 DDL; insert original UUID with schema_version 2; insert
   original rows in ascending sequence, preserving all six columns exactly.
5. Validate exact schema 2, integrity, full replay, and row-for-row equality
   with the retained originals; validate foreign_key_check returns no rows.
6. Commit once. Failures roll back. Deterministic fault seams before rebuild,
   after rebuild/before commit, and after commit establish old-complete/new-
   complete recovery behavior. Never delete sidecars manually.

Deleting/reinserting physical rows inside this upgrade is the explicit migration exception in
[ADR 0018](../adr/0018-incorporation-separates-acceptance-from-canonical-observation.md); it does not remove or rewrite logical history.
Read-only modes never recover writes. Explicit recover preserves a complete
supported layout. Empty recovery created schema 2 in that implementation;
current empty recovery creates schema 3.

## Current assertions and publication eligibility

Downstream `resolve`, `address` and `reflect` require the incorporation's current
accepted proposal decision, the same canonical incorporated unit revision, and a
validated current binding descended through support-review from its original
binding. They do not recheck the original full installation inventory/head context;
that strict comparison belongs to `incorporate` creation and retry. Legitimate
later consumer bindings therefore do not prevent source correction effects.

An incorporation remedy retains the exact current binding inspected after the
challenge acceptance. A replacement address's current receipt may contain a later
support-review descendant for that same unit revision. Replay validates the
binding chain; live checks validate current content, support and scope. Changed
canonical meaning needs an actual successor and explicit new resolution.

Resolution historically closes its named acceptance. Later evidence changes do
not erase it or implicitly reopen review; current observations can become stale
or unavailable. Reopening requires an explicit new acceptance decision, and prior
acceptances remain in the receipt frontier. Reversing a proposal disposition does
not unpublish its canonical unit or add an implicit global publication gate; it
invalidates current assertions that rely on its incorporation acceptance.

Publication paths exclude first components `knowledge`, `memory`, `.git` and
`.validated-memory`, root `validated-memory.md`, the actual declared schema, and
the store and its sidecars. Any other in-root regular nonsymlink UTF-8 file may
be observed, including generated HTML or executable-mode text. Observation never
executes a file. Derived artifacts are changed only by their normal generator.

An older checked use may be named by `address` if current now: address creation,
current decision/resolution checks, eligibility and recapture provide the fresh
observation. There is no new-use/receipt insertion-recency requirement. Omission
of a dependency alone never creates an address.

## Explicit renewal

`renew` with no declaration flags retains the old captured justification and
refuses its drift. If any `--support`, `--reference` or `--scope` flag appears,
support and scope are mandatory complete replacements; omitted references
explicitly mean no references. Partial declarations are usage errors. Capture
new support/reference/scope material in a new proposal linked to its prior.
Candidate bytes/path/ID/authority and predecessor bytes remain identical.

For an installed candidate, its current binding must match the new declaration;
ordinary `bind`/`review-support` happens separately before renewal. The renewed
proposal requires fresh complete inspection and acceptance. This supports evidence
maintenance without deleting installed knowledge or inventing a successor to an
unchanged claim; no prior proposal or retained support is rewritten.

## Schema-3 extension

Explicit current upgrade moves a complete schema 1/2 store to 3 without changing
old logical rows or receipt1/2 inspection strings. Incorporation still requires
at least 2; transfer writes require 3. See [Transfer storage](transfer-storage.md)
for new kinds, receipt3 lineage/frontier/origin summaries, source histories and
post-link proposal eligibility. Existing lifecycle payloads above stay closed and
unchanged. Source-tree schema 2 and 3 remain unreleased relative to published 2.2.0.
