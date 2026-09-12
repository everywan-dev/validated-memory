# Everyday checked reuse and correction

**Available since 2.3.0.** Version 2.2.0 supports checked consultation;
version 2.3.0 adds the incorporation and portable-transfer lifecycle below.
Fresh workspaces use schema 3; existing schema 1/2 stores need explicit `upgrade`
for transfer writes. See [compatibility](transfer.md#store-compatibility).

Use this workflow when a conclusion will be reused across sessions or projects
and you need to trace a later correction to its consumers and reports. The agent
can carry generated handles and run already authorized mechanical steps. A person
or explicitly attributed agent still judges evidence, applicability, correspondence
and whether a changed report adequately reflects the conclusion.

## Choose the next action

| Trigger | Next action | What requires judgment |
| --- | --- | --- |
| First consultation in a project | Register its root in an explicit workspace outside adopters; bind the relevant canonical revision, support, dependencies and scope. | Which evidence and dependencies justify that claim in this scope. |
| First reuse from another workspace | Assess and explicitly export whole history; import, inspect inventory, author a local proposal, map origins, inspect and accept, then install/bind/incorporate. | Local applicability, evidence equivalence, dependency and predecessor mappings. |
| Resume work with a checked use | Follow [task resumption](#resume-a-task): retain supplied update outcomes, then run `resume-use` with the exact requested scope (unreleased after 2.3.0). | Applicability to this task and how to resolve pending review. |
| Evidence or interpretation changes | Retain a factual or policy challenge, inspect and decide it. Review unchanged evidence or incorporate a canonical successor, inspect its binding and resolve. | Whether the challenge is accepted and how the claim should change. |
| An imported origin changes | Import the new complete history. Check affected old uses; map a corrected local successor and complete local review/incorporation/resolution before reacquiring a consumer receipt. | How the source correction changes the local policy and consumer conclusion. |
| A downstream report used the old conclusion | Track its old bytes before editing; obtain a checked new use, explicitly address the old use, update the report and record a reflection. | Whether the report's changed meaning is sufficient; reflection does not establish external delivery. |
| Hand off the corrected work | Export the selected updated consumer receipt with complete history; the receiving workspace imports and inspects it. | Whether to begin a separate local incorporation there. Import alone authorizes no current use. |

For full syntax and executable examples, use [checked consultation](consultation.md),
[incorporation and correction](incorporation.md), and [portable transfer](transfer.md).
Run the CLI as `PYTHONPATH=. python3 -P -m validated_memory` from this source checkout.
Handles returned as `id` are mechanical references: retain them directly rather
than constructing hashes or asking the user to transcribe them.

## Resume a task

**Unreleased after 2.3.0.** Task resumption adds `resume-use`; released 2.3.0
provides `check-use` for the historical scope. The new report is read-only and
creates no event, receipt or inspection handle. It cannot check external freshness.

Keep this reusable plain-text template in the adopter's existing trusted agent
handoff. It is a working note, not a new configuration format. The agent fills
returned identifiers mechanically; the user need not copy hashes.

```text
Task: <intended conclusion and work to resume>
Store: <explicit local SQLite path outside adopter roots>
Workspace: <returned workspace UUID>
Consumer: <registered alias:unit and returned project UUID/unit revision>
Checked use: <returned use handle; retain its receipt handle too>
Historical scope: <all receipt KEY=VALUE pairs>
Requested task scope: <all intended KEY=VALUE pairs>
Actor: <identity making any update/review assertion>
Trusted local capsule routes: <explicitly supplied/configured paths, or unknown>
Update outcomes: <per file: pending/imported/refused/unavailable/not attempted;
                  returned import handle, diagnostics and retry needed>
Pending decisions: <evidence, applicability and downstream changes to resolve>
```

1. Load this trusted handoff. Before the first import, use historical `show USE`
   to verify artifact kind and resolve its receipt, then `show RECEIPT` to check
   `snapshot.workspace`, consumer root and receipt scope against the handoff.
   Resolve mismatched context before mutating the store. This verifies historical
   identity, not current eligibility or freshness. Keep the requested task scope
   explicit; later recheck workspace, use/receipt IDs and root in the report.
2. For each available, authorized local capsule, run
   `import-transfer FILE --actor ACTOR --reason REASON`. Record each file's exit
   status, returned import handle and diagnostics. Routes must be explicitly
   supplied or configured in trusted local instructions. A capsule's historical
   `source_store_path` is not an update route. Unknown routes and unavailable
   files remain visible; the agent cannot infer that no updates exist.
3. Stop the import sequence on refusal. Earlier successful imports remain
   committed: this is partial completion, with one transaction per file. Retain
   the refused file and remaining not-attempted files as outstanding. After
   ambiguous output, retry the identical capsule using existing idempotence;
   preserve earlier handles and diagnostics. Do not claim batch atomicity.
4. Run `resume-use USE --scope KEY=VALUE` with every requested scope pair and
   retain the complete bounded JSON report, including on exit 1. Report actual
   committed state alongside per-file outcomes. An exit 0 `current` report does
   not clear a failed or unavailable update input or an unknown route; do not
   announce an unqualified ready-to-resume result. Every foreign origin has
   `live_origin: not-checked` even after a successful import.
5. Present known pending origin review, current-check failures and scope mismatch
   separately. A broad historical use can check current while a newly requested
   narrow scope reveals a known narrow challenge. Different scope always requires
   a new complete consumer `read` and `record-use`. Missing local inputs do not
   hide retained known corrections; incomplete origin analysis blocks resumption.
6. For imported material, before proposal authoring select the relevant receipt member with
   `show-transfer IMPORT --origin PROJECT_UUID:UNIT_ID --material`. Inspect its
   complete selected semantic material, exact `selection_closures` membership and
   known correction context. Additional `context-binding` declarations can be
   earlier or later than the selected receipt. Missing proof or overflow refuses
   without truncation; this view is not a receipt or inspection handle. Perform
   explicitly decided maintenance dependency-first where the report establishes
   order; otherwise inspect to establish it. Keep the mandatory complete two-line
   `link-transfer`, `inspect` and `read` protocols at their normal stages.
   Inspect complete material before deciding correspondence, acceptance,
   independence, successor wording, challenge disposition or publication reflection.
   No report automatically makes those decisions. After maintenance or a changed
   root, revision or scope, acquire the complete final consumer receipt and record
   a new use. Preserve old handles and history. If no maintenance is needed and
   scope matches, the current historical use can continue at this check.

The report carries handles and statuses, not full foreign content. It does not
replace complete `inspect` or two-line `read` protocols. Its actions are advisory
data, never executable shell text; subsequent operations revalidate their inputs.
No scheduler, remote discovery or automatic acceptance is provided. The
[selected material view](transfer.md#selected-material-before-proposal-authoring)
is unreleased after 2.3.0 and opens no foreign paths. Whole history remains in
transport/storage; the selected view is not a replay capsule and does not establish
external freshness. This workflow makes no measured token or empirical usefulness
improvement claim. See the
[report contract](consultation.md#task-resumption-report).

## A complete example: cutoff versus departure

A synthetic investigation in workspace A initially records **16:00 as carrier
departure**. Workspace B imports its history, explicitly incorporates a local policy,
and derives a planning conclusion. B obtains its own consumer-root receipt and
checked use, then writes a report. A later session successfully checks that use.
This steady reuse needs no new transfer, proposal or acceptance while inputs remain
eligible.

Predetermined fixture testimony then clarifies that **16:00 is admission cutoff;
departure is scheduled later**. The correction follows these stages:

1. In A, retain and accept a factual challenge. Propose, inspect, accept, install,
   bind and incorporate the corrected canonical successor. Inspect its binding,
   resolve the challenge, then acquire the corrected receipt and export full history.
2. In B, import that history and check the old use. Its refusal identifies the
   superseded origin before any local authoring. The old use remains inspectable.
3. Retain and accept a local challenge, and track the original report against the
   old use. Propose the local policy successor and map the source predecessor to
   B's active local predecessor. Inspect, accept, install, bind and incorporate;
   inspect the new binding and resolve the local challenge.
4. Author a successor to the consumer plan with the corrected policy dependency.
   Acquire its receipt and checked use. A reconciliation report still shows the
   old use as unaddressed until an explicit `address` connects it to the new use.
5. Address the old use, update the tracked report and record `reflect`. Reconcile
   again: the old observation remains historical, while the address and reflection
   show their own current observations. Original publication bytes remain retained.
6. Export B's updated planning receipt and complete history to workspace C. C
   inspects the selected plan, local successor and qualified A origin evidence.
   This is historical retention, not C's local incorporation or authorization.

Finish incorporation before unrelated consumer authoring, because incorporation
checks the accepted installation context. Finish source review operations before
taking the capsule: even metadata-only origin events can conservatively require
renewed transfer-link review. A correctly mapped source/local successor replaces
the old obligation on that path; it does not claim independence or require the
retired source claim to become eligible again. Preserve both predecessor files.

## What remains visible

Old receipts, checked uses, challenges, dispositions, proposals, resolutions,
origin mappings and publication bytes remain inspectable. A successful resolution
does not revive an old receipt frontier or silently address every consumer. An
old report's observation can be stale while its explicit reflection is current;
these describe different retained observations.

Whole-history export can disclose unrelated projects, paths, statements, retained
publications and nested imports. A selected receipt does not narrow disclosure.
Use `export-transfer --assess` and the explicit `--include-workspace-history`
acknowledgment. Transfers are bounded to 8 MiB and stored payloads to 32 MiB per
workspace; inspection output and provenance traversal also have explicit bounds.
See [storage and limits](transfer-storage.md). Historical imports do not establish
live source freshness or authenticated authorship. Declared dependencies and
supersession are opt-in; this workflow does not detect undeclared semantic copying.

The shipped subprocess case in
[`tests/test_everyday_workflow.py`](../../tests/test_everyday_workflow.py) exercises
this lifecycle over three disposable workspaces and checks adopter file bytes and
path membership around every consultation command. Run it with
`python3 -m pytest tests/test_everyday_workflow.py -q`. Its testimony, acceptance and
report wording are predetermined synthetic judgments. Passing it establishes
mechanical lifecycle behavior, not automatic interpretation or empirical usefulness.
Output bytes and elapsed CLI time are observable workflow costs; they are not
model tokens, billing estimates or evidence of long-project savings.
