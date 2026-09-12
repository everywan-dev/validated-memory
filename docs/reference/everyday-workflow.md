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
| Resume work with a checked use | Run `check-use` against that handle before relying on it. If current, proceed within its scope; if refused, follow the affected identity and recovery route. | Whether the intended task still fits the recorded scope and conclusion. |
| Evidence or interpretation changes | Retain a factual or policy challenge, inspect and decide it. Review unchanged evidence or incorporate a canonical successor, inspect its binding and resolve. | Whether the challenge is accepted and how the claim should change. |
| An imported origin changes | Import the new complete history. Check affected old uses; map a corrected local successor and complete local review/incorporation/resolution before reacquiring a consumer receipt. | How the source correction changes the local policy and consumer conclusion. |
| A downstream report used the old conclusion | Track its old bytes before editing; obtain a checked new use, explicitly address the old use, update the report and record a reflection. | Whether the report's changed meaning is sufficient; reflection does not establish external delivery. |
| Hand off the corrected work | Export the selected updated consumer receipt with complete history; the receiving workspace imports and inspects it. | Whether to begin a separate local incorporation there. Import alone authorizes no current use. |

For full syntax and executable examples, use [checked consultation](consultation.md),
[incorporation and correction](incorporation.md), and [portable transfer](transfer.md).
Run the CLI as `PYTHONPATH=. python3 -P -m validated_memory` from this source checkout.
Handles returned as `id` are mechanical references: retain them directly rather
than constructing hashes or asking the user to transcribe them.

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
