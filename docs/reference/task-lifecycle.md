# Task lifecycle and trusted handoff

Use the adopter's existing trusted agent handoff to carry a memory-assisted task
across sessions, compaction, or delegation. The handoff is working prose inside
the task's authorized scope. It is not canonical memory, a required filename or
schema, a profile field, or a hidden transcript store.

A request for memory-assisted work on a stated task activates this workflow for
that task. A recall prefix activates one query only. The shipped prompt hook does
not provide task-wide activation, deliver context after compaction, propagate
context to subagents, capture learning, or gate arbitrary output. See
[agent integration](agent-integration.md) for its query-only boundary.

## Minimal handoff

Keep only the detail needed to identify and continue the work. Add the checked
fields from [everyday checked reuse](everyday-workflow.md#resume-a-task) when the
task uses consultation receipts.

```text
Task identity: <stable human-readable identity>
Adopter root: <exact root>
Outcome and authorized work: <intended result and permission boundary>
Applicability: <conditions and requested scope>
Status: <active, completed, or cancelled>
Sources selected: <exact locators or handles>
Sources inspected: <complete originals actually read and checks run>
Pending: <uncertainty, learning/review proposals, and unresolved decisions>
Next action: <smallest concrete continuation step>
```

Task identity and status are trusted-handoff guidance. The CLI does not own a
task handle: each consultation operation checks its own store, artifact kind,
root, scope, and current inputs. Consequently, copying a current use handle into
another trusted handoff with the same scope remains mechanically valid. When
mechanical separation between tasks is required, include a stable task identity
as an explicit receipt scope pair; a different task scope then requires a new
complete `read` and `record-use`.

## Start, resume, and hand off

At the start, record the exact adopter root, intended outcome, authorized work,
applicability, and selected source locators. Record only sources actually
inspected as inspected. For a bare continuation, resume only an unambiguous
active handoff. If none or several qualify, ask which task and scope to use.
Completed and cancelled tasks stay inactive unless a new explicit request creates
fresh scope; keep their prior disposition.

Before relying on a recalled conclusion after resumption, verify the root, task
identity, active status, and current requested scope. Inspect the relevant
complete originals and known corrections again. Old context delivery is not
fresh acquisition. For checked work, also follow the existing `resume-use`,
selected-material, complete `read`, and `record-use` protocols linked above.

Before compaction or session handoff, retain the minimal pending work, exact
locators and handles, and unresolved uncertainty. A subagent receives an explicit
bounded packet containing project and task identity, only its authorized subset,
relevant locators, and pending checks. It returns sources actually inspected,
changed paths, unresolved questions, and check results. The parent rechecks
current inputs before relying on the result. These are workflow obligations, not
automatic host enforcement.

On completion or cancellation, record the disposition and outstanding proposals
separately. Neither status automatically captures proposed learning, changes the
profile, or erases history. Cancellation does not roll back completed authorized
writes; name them. Follow [requested learning](learning.md) only for accepted or
explicitly requested capture.

## Scenario matrix

These examples describe expected handling; they are not an executable task state
machine.

| Situation | Response |
| --- | --- |
| One active handoff and a bare continuation | Verify identity, root, and scope; inspect current sources; resume the recorded next action. |
| No handoff or more than one plausible handoff | Ask for the task and requested scope; do not infer either from the profile or a hook candidate. |
| Completed or cancelled handoff | Keep it inactive on a bare continuation; a new explicit request may establish fresh scope while preserving the old disposition. |
| Conversational context was lost | Reconstruct from trusted locators and recorded uncertainty, then freshly inspect relevant complete originals and corrections. |
| A correction is pending | Keep it pending; existing checked operations refuse or qualify use according to their own current-input rules until maintenance completes. |
| Work goes to a subagent | Send a bounded authorized subset and locators; require inspection evidence, then have the parent recheck current inputs before integration. |
| Task is cancelled after some authorized writes | Preserve and report those writes and retain pending, unaccepted learning proposals without capturing them. |
| Observations differ under different conditions | Retain both with their conditions; do not infer contradiction or supersession from recency. |

No automatic lifecycle, compaction, delegation, capture, completion, cancellation,
or delivery gate is supplied by this reference.
