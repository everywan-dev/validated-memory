# Transfer storage schema

**Unreleased source-tree addition.** Published 2.2.0 supports consultation schema 1.
Incorporation introduced schema 2; portable historical contributions require
schema 3. This reference describes the current source implementation, not a release.
[Transfer](transfer.md) documents commands, disclosure and an executable example.

## Compatibility and transaction boundary

Schema 3 preserves the [consultation](consultation-storage.md) and
[incorporation](incorporation-storage.md) event contracts and adds `transfer-import`
and `transfer-link`. The immutable event envelope remains exactly
`{sequence,id,kind,prior,payload,created_at}`. IDs remain
`H({kind,prior,payload})`; original sequence, timestamps and payload strings survive
explicit upgrade unchanged. Capsule prefix comparison nevertheless compares all
six fields, not just IDs.

Fresh register/explicit empty recovery creates 3. `upgrade` validates an existing
complete 1/2 store, then atomically rebuilds the schema-3 metadata/event constraints,
preserving workspace UUID and old logical rows. Repeat upgrade on 3 is idempotent;
missing/empty upgrade refuses. Interrupted upgrades retain complete old/new state
through SQLite rollback/recovery. Ordinary operations never implicitly migrate
1/2. Incorporation requires >=2, transfer writes require 3, and old clients refuse
unsupported 3. JSON wire `schema_version: 1` is distinct from storage version.

Historical receipt1/2 replay without live projects and without a transfer-link in
that chronological prefix uses its frozen original checks, without new ancestry
proof. Later transfer events do not change earlier prefixes or rewrite old rows.
Live current checks and receipt3 remain strict. New assertions after transfer
links using a legacy receipt require complete retained ancestry; if missing, they
refuse atomically with instructions to reconsult and obtain receipt3 lineage.
Existing historical uses remain readable and current-checkable with live inputs;
an exact retry may return the old handle after successful current checking.

The only writer remains the local SQLite store module, using DELETE rollback
journaling, EXTRA synchronization and one authoritative transaction per import or
link. No foreign SQLite database is opened/deserialized. Source path strings are
replay context only; no origin filesystem or network request is made. Canonical
adopter files remain outside this writer. Read-only operations cannot recover a
hot journal; explicit recovery remains separate.

All objects below are closed: all listed fields are required, unknown fields and
versions refuse. Reuse C/H/B, UTF-8 preservation, exact scalar types, qualified
identities and sorted unique arrays from consultation storage. Boolean is not an
integer. Qualified event references point backward in their own chronology.
No best-effort usable prefix of corrupt material is returned.

## Exact schema-3 layout

```sql
CREATE TABLE workspace (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), schema_version INTEGER NOT NULL CHECK (schema_version = 3), workspace_id TEXT NOT NULL);
CREATE TABLE events (sequence INTEGER PRIMARY KEY CHECK (sequence >= 1), id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK (kind IN ('registration','relocation','checkpoint','binding','support-review','conflict','choice','receipt','use','proposal','challenge','inspection','decision','incorporation','resolution','address','publication','reflection','transfer-import','transfer-link')), prior TEXT REFERENCES events(id), payload TEXT NOT NULL, created_at TEXT NOT NULL);
```

This is a format reference, not an invitation to construct or edit store rows.
Exact table, column, constraint, index, foreign-key and SQLite storage-class checks
remain version-specific. The schema-1/2 layouts stay frozen for compatibility.

## Capsule

```text
{format:"validated-memory-transfer", version:1, workspace:UUID,
 source_store_path:RootPath, storage_version:1|2|3, receipt:Digest,
 events:[{sequence,id,kind,prior,payload,created_at}], sha256:Digest}
```

`sha256` is H of the envelope excluding that field. Input is exactly canonical
UTF-8 JSON plus one final newline; equivalent pretty JSON refuses with an
instruction to use `export-transfer` output. Events are complete source history,
unmodified and ordered from sequence 1 to its exported head. The selected receipt
must exist and pass historical replay. Receipt selection supplies canonical and
support/dependency material; whole history also discloses unrelated content,
absolute paths, tracked publication bytes and nested imports.

Export reads retained state only. Assess emits exact wire bytes, supported boolean,
ordered actionable `refusals` and disclosure/counts without corpus text. `supported`
is true exactly when `refusals` is empty. Assess checks byte, origin-depth, registry
and event-work limits of the complete emitted capsule, including its outer
workspace. Actual export performs the same preflight and refuses before stdout.
Assess cannot promise compatibility with an unknown destination's existing forks
or identity; those remain import-time checks.
Import captures a regular nonsymlink file, rejects unsafe ancestors,
validates it, and recaptures identical bytes before commit. Its reader permits
8 MiB capsules without increasing the ordinary 1 MiB canonical/support file bound.

## Origin registry and limits

Each imported foreign State replays only its own chronological history and nested
imports already encountered at that event. Historical receipts cannot see later
foreign or destination knowledge. Current destination knowledge takes the longest
compatible prefix per origin UUID across all retained capsules, including transitive
imports. Equal-length prefixes require exact six-field row equality. A shorter
package is historical material and never rolls back the newest known head. A fork
anywhere refuses the whole import. Changed replay paths or schema upgrades do not
authenticate a new origin identity.

Cache validated histories per invocation using full history digest, schema and
replay path; never reuse a future-aware State for an earlier chronological point.
Registry/work limits are checked before expensive replay. Import cannot contain
the destination workspace UUID at any nesting level. Cycles/missing required
material fail explicitly; none authorizes fallback use.

| Bound | Maximum |
| --- | --- |
| Complete capsule including final newline | 8 MiB |
| Source/destination stored payloads | 32 MiB per store |
| Local/source event history | 10,000 rows per store |
| Imported origin registry | 16 distinct workspace UUIDs |
| Distinct registry `(origin UUID, sequence)` rows across newest prefixes | 10,000 |
| Nested origin levels | 4 |
| Complete rooted provenance judgment | 128 local/foreign unit nodes; 512 dependency/lineage/link edges |
| Retained strict local ancestor files | 128 |
| Complete link/detach/show output | 1 MiB |

The 128-node/512-edge budget covers one complete rooted provenance judgment for
read, use, resolve, link or detach. Local and foreign ancestry, dependencies, link
traversal and inspected source material share it; helpers never reset the budget.
Distinct qualified nodes and edges count once. Link artifacts count as edges,
not canonical knowledge-unit nodes.

Each inventory row is an independent rooted judgment with that same shared budget.
Unrelated report rows and entire chronological histories are not unioned into one
128-node cap. Reports still enforce the selected closure's 128-unit bound, the
10,000-row history bounds and complete 1 MiB wire bound. Return all rows or refuse;
never truncate or infer broader-use authorization from a historical report.

Output-bound numeric minima are 2048 bytes. Export defaults to 8388608;
link/detach/show default to 1048576. Both lines of an inspection count. A valid
32 MiB source workspace can exceed the export ceiling permanently under this
slice's whole-history rule. No compaction or misleading smaller-receipt workaround
is provided. Import's complete inventory response must also fit before commit.

## Import and link payloads

```text
transfer-import = {version:1, capsule:Capsule, actor:Actor, reason:Reason}
```

Prior is null. One event retains the capsule without a sidecar material lifetime.
An exact capsule retry returns its existing handle after validation regardless of
new attribution. Different selected receipts at the same origin/head remain
separate imported material, never separate independent sources.

```text
transfer-link = {
 version:1, mode:"retain"|"independent", proposal:Digest,
 origin:{workspace:UUID, identity:Identity, unit_sha256:Digest, binding:Digest},
 import:Digest|null, from:Digest|null,
 lineage:[{identity:Identity,file:CanonicalFile}],
 dependencies:[{origin:Identity, local:Identity, unit_sha256:Digest, incorporation:Digest}],
 predecessors:[{origin:Identity, unit_sha256:Digest,
                mode:"mapped"|"origin-only", local:Identity|null, local_sha256:Digest|null}],
 observed:Frontier, max_bytes:integer, inspection_text:string,
 inspection_sha256:Digest, actor:Actor, reason:Reason
}
```

For retain, import is an earlier local import and from is null. Origin is an exact
selected receipt closure member with its captured binding justification. For
independent, import is null and from is an inherited ancestor local link; origin
is copied from that link. It applies only to a strict canonical successor, never
the same local revision. Independent mode retains removal context without making
that origin an ongoing dependency.

Link head key is `(local project,local unit,local unit SHA,origin workspace,
origin project,origin unit)`. First prior is null; replacement requires exact
`--prior`. Retry compares complete semantic payload/observed context except
actor/reason/output limit, after current validation and complete output. Origin
identity changes add obligations rather than replacing a different origin. Renewing
a proposal for identical local canonical bytes cannot shed its links.

Dependencies are sorted by source project/unit and exactly cover immediate source
binding references, with one-to-one destination targets. Every target is an exact
already-incorporated local revision retaining the corresponding source revision;
proposal references include those exact local revisions. Additional authored
local dependencies/evidence are allowed. Source support hashes must occur among
local proposal supports, with local path changes permitted. Scope equals the
selected source receipt scope exactly. Later support review cannot silently remove
mapped evidence or change a mapped dependency revision.
Current checks propagate the requested read scope, including a narrower scope,
through every reached origin. Local acceptance does not bypass an applicable
origin gate by narrowing that scope.

Predecessors exactly cover direct source `supersedes` IDs, sorted by source
identity and local key (empty for origin-only). Obtain predecessor canonical bytes
from retained bindings/support reviews or canonical receipt/lineage observations;
require one unique SHA per exact identity. Missing/inconsistent material refuses.
Complete predecessor text is included in inspection, including origin-only entries.

Map all ACTIVE local canonical counterparts retaining that source obligation in
the destination tree before prospective insertion. Retired or explicitly independent
counterparts are not required as direct predecessors again. If no active retaining
counterpart exists, origin-only has null local/local SHA. It retains foreign history
without inserting a foreign-to-local supersession edge. An abandoned uninstalled
proposal is not an installed counterpart.

## Inspection and eligibility phases

Link/detach first wire line has envelope schema_version 1, status `inspected`,
and `review` with exactly:

```text
{mode,proposal,origin,import,from,dependencies,predecessors,observed,
 actor,reason,material,inherited,limitation}
material = {units,support,bindings,predecessor_files,corrections}
units/predecessor_files element = {workspace,identity,file}
support element = {workspace,project,path,sha256,size,text}
bindings/corrections element = {workspace,event}
```

Material contains complete exact source member/dependency closure, canonical/support
bytes, binding declarations, predecessor files and relevant correction records.
Each event is the unchanged full six-field foreign artifact. Collect every reached
retaining source closure using the newest compatible registry, including transitive
corrections and intermediary transfer-link events that expose recorded mappings.
Sort units/predecessors by workspace, project, unit; support by workspace, project,
path; events by workspace, sequence. Deduplicate only exact qualified identities
and artifacts; conflicting revisions never silently overwrite. Do not print
unrelated entire capsules. The complete traversal is bounded to 128 nodes and 512
edges, and the complete two-line wire to 1 MiB; no truncation.
Source predecessor mappings still apply only to the selected root's direct source
supersedes, not every transitive material predecessor.
Inherited context is the closed `{links,lineage}` object: qualified inherited
links and retained local canonical ancestry in sorted order. Limitations state
historical evidence, live origin not checked and attributed correspondence rather
than semantic proof. Independent inspection also includes currently known relevant
corrections and all inherited paths, even when origin claims are ineligible.

`inspection_text` equals the exact first UTF-8 line including newline; its digest
is B of those bytes. Reconstruct it exactly during chronological replay. Bound
both lines before output, successfully flush complete review, append/commit, then
emit `status: recorded` with id. The first inspection line alone cannot establish
whether commitment occurred. A precommit failure rolls back; a postcommit delivery
failure may retain the link, and retry validates/reports its existing handle.
Failed final delivery identifies committed id on stderr when available;
no mechanism guarantees delivery when both channels are lost.

Link planning checks proposed maps and upstream context, without requiring the
target's own incorporation or its other pending links. Several origin links can
therefore be prepared sequentially. `observed` includes reached foreign heads/
links and exact incorporated dependency links; it excludes all link heads for the
target local revision and historical predecessor links as continuing dependencies.
Adding another origin to the same target cannot self-invalidate an earlier link.

Proposal acceptance checks a post-link local inspection and prospective acceptance
for that exact target, omitting only its not-yet-created incorporation. All origin
obligations must be covered; mapped dependencies retain current incorporation gates.
Incorporation requires committed current acceptance/inspection, omitting only its
own not-yet-recorded installation observation. Checked use and downstream current
assertions require fully current acceptance, incorporation and origin gates.

## Ancestry and receipt payload 3

Each transfer-link retains `lineage` for the complete strict local canonical
ancestors of its target proposal and mapped dependency roots, excluding canonical
roots already retained in proposal/binding files. Entries use the receipt3 lineage
shape below, sorted unique by identity, bounded to 128, and represented exactly
in proposal.expected knowledge inventories. Live prospective capture establishes
closure; replay verifies it without opening a filesystem. Never-bound intermediate
canonical units remain reviewable. The same lineage appears in review.inherited.

Explicit local `supersedes` ancestry carries origin obligations, including through
unbound intermediate canonical units. Evaluate every path. An independent disposition
stops its named origin on that path; a sibling retaining path survives. A merge
unions retained obligations. An explicit independent merged successor can discharge
its named inherited origin across all inspected paths. History is retained.

Explicit mapped source/local supersession provides another precise transition.
A link to source A2 with direct source predecessor A1 and mapped local predecessor
L2 replaces the inherited A1 obligation on that L2 path when the local candidate
canonically supersedes L2. Match origin workspace/project/unit/exact predecessor
SHA. Gate the new A2 obligation; do not demand current eligibility of known-retired
A1. Its exact canonical bytes and local ancestry remain in inspection/history.
This is correspondence of recorded supersession, not an independence assertion.
It cannot discharge unrelated origins, unmatched revisions or unmapped retaining
siblings, and origin-only cannot discharge an existing retaining path. Evaluate
ancestry bottom-up so later local descendants inherit A2 without reviving A1.

Receipt payload 3 retains receipt-2 fields and adds:

```text
transfer = {lineage:[{identity:Identity, file:CanonicalFile}],
            frontier:Frontier, origins:[OriginSummary]}
Frontier = {origins:[{workspace:UUID,head:Digest}],
            links:[{workspace:UUID,link:Digest}]}
OriginSummary = {
 local:{workspace:UUID,identity:Identity}, mode:"retain"|"independent",
 origin:{workspace:UUID,identity:Identity,unit_sha256:Digest,binding:Digest},
 link:{workspace:UUID,id:Digest}, head:Digest, scope:Scope,
 evidence_sha256:[Digest], actor:Actor, reason:Reason, live_origin:"not-checked"
}
```

Lineage is the complete strict local canonical ancestor closure, excluding units
already in receipt content, with exact files represented in snapshot knowledge
inventory. Validate IDs, recursive edges, project confinement and the 128-file
bound. Frontier origins sort by workspace; links by workspace/link. Summaries sort
by local workspace/project/unit, then origin workspace/project/unit; evidence
hashes are sorted unique. Foreign local identities always retain workspace
qualification. There is no corroboration count.

Frontier includes all relevant effective local/transitive links and newest reached
origin heads. Independent dispositions contribute their links and frozen reviewed
summary context, but not continuing origin heads. A retaining sibling still gates.
A->B->C with newer A imported directly at C updates C eligibility without waiting
for B; subsequent repair may require B review/re-export. Unrelated origin imports
do not invalidate unrelated uses.

Receipt1/2 payloads and inspection strings remain frozen. Their historical transfer
projection is empty. Current schema-3 checks reconstruct live ancestry and refuse
new obligations/frontiers. Receipt3 read inspection includes the entire transfer
object alongside existing content/scope/limitation, within the ordinary read bound.
Historical artifacts are not rewritten when later imports stale current use.

## Inventory and known status wire

Assess status is `assessed` with fields `bytes max_bytes supported refusals history_events
projects origins selected disclosure`; selected contains receipt/units/dependencies/
support counts. Import status is `recorded`, with id and inventory. Show-transfer
status is `historical; live origin not checked`, with id and inventory:

```text
inventory = {origin,scope,live_origin,units,local_links,recovery,disclosure,limits}
origin = {workspace,head,receipt}
```

Units expose identity/binding/unit SHA/support hashes/dependencies/predecessors/
statuses. Predecessor entries identify sorted observed revisions and availability
`complete`, `unavailable` or `inconsistent`. Affected local links identify exact
local revision/handle and status, including direct and transitive relationships.
For A to B to C, inspecting an A-only update at C exposes the related C revision
and B link with its review requirement. Independent and retired historical links
remain visible without being described as continuing current origin dependencies.
Recovery is deterministic dependency-first
instruction text; it never claims automatic reconciliation.

Known statuses are reported in this order: `review-required`, `known-superseded`,
`binding-changed`, `acceptance-withdrawn`, `conflict`, `origin-review-stale`,
`local-incorporation-pending`, `historical`. Known supersession comes from installed
binding/canonical receipt/lineage observations, never uninstalled proposals.
Current gates follow newest prefix-compatible history and retained links, not
current foreign files. Accepted unresolved challenges and other listed gating
conditions prevent linked local acceptance/incorporation/use as applicable.

The same projection gates read, record-use/check-use, proposal acceptance/
incorporation and downstream current-use/resolve/address/reflect/reconcile.
For proposal acceptance, incorporation and both resolution remedies, create,
retry and replay follow exact declared dependencies transitively and canonical
ancestry. A pending-acceptance exception covers only the exact target. Unbound
referenced units remain permitted when existing retained records supply their exact
canonical bytes and complete ancestry; no new payload field is introduced. A new
assertion after transfer links with only live canonical bytes and insufficient
replayable retained proof refuses atomically. Recovery is to retain a reviewed
binding, or supported retained canonical lineage, then retry. This is not a
requirement to bind every dependency or a prohibition on hypothesis resolution.
Frozen histories before transfer links retain their original behavior.
The missing-proof diagnostic is `referenced canonical proof unavailable; retain a
reviewed binding or supported canonical lineage, then retry`. A mismatched exact
revision instead reports `referenced canonical revision changed; review the
declaration and retry`. Live projection and retained replayable projection both
pass before append.
Historical resolutions remain recorded; later source support drift requires updated
mapping/review. Local actor attribution and internally consistent hashes never
prove source truth, live freshness or semantic sufficiency.
