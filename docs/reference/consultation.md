# Checked consultation

`consultation` records opt-in checked use across local adopter projects on a
trusted POSIX host. Start with
[the commands](#commands) or run the [two-adopter example](#two-adopter-walkthrough).
The [storage reference](consultation-storage.md) defines every retained artifact.
Available since version 2.2.0. Existing lookup with [recall](recall.md) remains read-only
word-based discovery and does not register projects or create checked-use records.

The outcome is a checked-use record for an exact consumer conclusion. Authoring
the conclusion remains a separate operation: a refusal can leave authored
Markdown without a checked-use record. Normal validation, discovery and rendering
do not acquire an implicit publication gate.

The [incorporation lifecycle](incorporation.md) is an unreleased source-tree
addition for proposals, challenges and correction effects. Its schema 2 requires
explicit `upgrade` for existing schema 1 stores; published 2.2.0 does not provide
those commands. Current source registration creates schema 3 with unreleased
[portable transfer](transfer.md), while ordinary consultation on complete schema
1/2 stores remains available without migration. Existing stores require explicit
upgrade to 3 for transfer writes; incorporation requires at least 2.

For the complete correction-to-report lifecycle, see the
[everyday workflow](everyday-workflow.md).

## Two-adopter walkthrough

Run from the source checkout in Bash. These are ordinary adopter files, created
through `init` and Markdown authoring; no consultation database rows or hashes
are supplied by the author. Keep the printed workspace path for later inspection.
Execute each stage only after its prerequisites succeed. The intentional drift
check below must refuse; other failures require inspection before continuing.

```bash
export PYTHONPATH="$PWD"
walkthrough_root=$(mktemp -d /tmp/consultation-workflow.XXXXXX)
export walkthrough_root
mkdir "$walkthrough_root/policy" "$walkthrough_root/planning"
(cd "$walkthrough_root/policy" && python3 -P -m validated_memory init)
(cd "$walkthrough_root/planning" && python3 -P -m validated_memory init)
python3 - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["walkthrough_root"])
for project in ("policy", "planning"):
    (root / project / "sources").mkdir()
(root / "policy/sources/policy.txt").write_text(
    "For the training exercise, dispatch the parcel by 16:00.\n", encoding="utf-8")
(root / "policy/knowledge/kb-rule.md").write_text(
    "---\nid: kb-rule\nevidence: verifiable\nprovenance:\n"
    "  - sources/policy.txt\n---\n# Training dispatch deadline\n\n"
    "Dispatch the training parcel by 16:00.\n", encoding="utf-8")
(root / "planning/sources/design.txt").write_text(
    "Training plan: pack at 15:00 and dispatch at 15:30.\n", encoding="utf-8")
print(root)
PY
(cd "$walkthrough_root/policy" && python3 -P -m validated_memory validate)
(cd "$walkthrough_root/planning" && python3 -P -m validated_memory validate)
consult() {
  python3 -P -m validated_memory consultation \
    --store "$walkthrough_root/consultation.sqlite" "$@"
}
# Display the full response for inspection and retain its CLI-generated handle.
# A failed command or malformed response never replaces a previously held handle.
capture() {
  local handle_name="$1" response_path result_id
  shift
  response_path=$(mktemp "$walkthrough_root/response.XXXXXX") || return
  if ! consult "$@" > "$response_path"; then
    cat "$response_path"
    return 1
  fi
  cat "$response_path" || return
  result_id=$(python3 - "$response_path" "$1" <<'PY'
import json
import sys
from pathlib import Path

lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
rows = [json.loads(line) for line in lines]
operation = sys.argv[2]
assert all(row["schema_version"] == 1 and row["operation"] == operation
           for row in rows)
if operation == "read":
    assert len(rows) == 2
    assert rows[0]["status"] == "inspected" and "id" not in rows[0]
    assert rows[1]["status"] == "receipt recorded"
else:
    assert len(rows) == 1
handle = rows[-1]["id"]
assert isinstance(handle, str) and handle
print(handle)
PY
  ) || return
  printf -v "$handle_name" '%s' "$result_id"
}
consult register policy "$walkthrough_root/policy" --source training-policy
consult register planning "$walkthrough_root/planning" --source training-planning
capture rule_binding bind policy:kb-rule --support sources/policy.txt \
  --authority training-policy --scope exercise=parcel \
  --actor workflow-agent --reason 'Inspected the training policy and its scope.'
```

The agent retains the returned binding handle as `rule_binding`. Handles in the
following commands are variables populated from successful CLI responses by the
coordinating agent, not values the human invents or copies. The declared actor
identifies the agent's assertion; it must not imply a human reviewed the claim.
Warnings about missing anchors do not gate these synthetic, verifiable units.

## Inspect, author, then acquire the final consumer

```bash
consult read policy:kb-rule --scope exercise=parcel --max-bytes 65536
```

For each successful `read`, the agent consumes **two ordered JSON lines**:

1. Parse and inspect the complete content line: root, scope, canonical unit text,
   dependency text, support text and limitations. Do not summarize away an input
   before assessing whether the conclusion follows from it.
2. Accept the receipt handle only from the subsequent committed-receipt line,
   after successful command completion. Retain it with the corresponding content
   and root. One content line followed by a refusal is not a usable receipt.

Do not parse the entire stdout as one JSON object, use the first line as a receipt,
or manufacture a receipt from locally calculated hashes. Both lines together
count against `--max-bytes`. A failed handle delivery may report a committed ID
on stderr; retrying `read` returns the full content again after validation.
OS write/flush success does not prove that the agent or a human understood it.

The agent now authors its own conclusion, following the existing knowledge-unit
workflow. This example uses plain Markdown and explicitly retained provenance:

```bash
python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["walkthrough_root"]) / "planning/knowledge/kb-plan.md"
path.write_text(
    "---\nid: kb-plan\nevidence: verifiable\nprovenance:\n"
    "  - sources/design.txt\n---\n# Training dispatch plan\n\n"
    "Dispatching at 15:30 meets the training policy deadline of 16:00.\n",
    encoding="utf-8")
PY
(cd "$walkthrough_root/planning" && python3 -P -m validated_memory validate)
consult bind planning:kb-plan --support sources/design.txt \
  --reference policy:kb-rule --authority training-planning \
  --scope exercise=parcel --actor workflow-agent \
  --reason 'Compared the planned dispatch time with the training policy.'
capture plan_receipt read planning:kb-plan --scope exercise=parcel --max-bytes 65536
```

The capture helper retains this final read's receipt as `plan_receipt`. The final root is the exact
consumer `planning:kb-plan`. The earlier source receipt cannot record its use:
its root differs, and the intervening authoring/binding changed the snapshot.

```bash
capture plan_use record-use planning:kb-plan --receipt "$plan_receipt"
consumer_root=planning:kb-plan
consult check-use "$plan_use"
consult show "$plan_use"
```

Report the first result as “checked-use recorded.” Report `check-use` success as
“current at this check.” `show` is historical inspection, including after drift;
it is never a substitute for `check-use` in a later session.

## Maintain an unchanged claim after support drift

Append a nonsemantic clarification to the disposable policy support file:

```bash
python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["walkthrough_root"]) / "policy/sources/policy.txt"
with path.open("a", encoding="utf-8") as stream:
    stream.write("This example concerns one training parcel.\n")
PY
consult check-use "$plan_use"
```

Expect a gating refusal naming the changed support and affected identity. The
agent reads the changed file and its retained predecessor before deciding whether
the claim still holds. For this clarification the claim is unchanged:

```bash
capture rule_binding review-support policy:kb-rule --prior "$rule_binding" \
  --support sources/policy.txt --authority training-policy \
  --scope exercise=parcel --actor workflow-agent \
  --reason 'Inspected the added single-parcel clarification; the 16:00 deadline is unchanged.'
capture plan_receipt read planning:kb-plan --scope exercise=parcel --max-bytes 65536
capture plan_use record-use planning:kb-plan --receipt "$plan_receipt"
```

Retain the new binding and use handles alongside their predecessors. No human
confirmation is needed for an already authorized, unambiguous agent judgment.
If interpretation is materially unresolved, ask for that decision and attribute
the eventual review honestly. Repeating `bind` or mechanically refreshing hashes
must not replace the attributed review. If the canonical claim changed, author
a successor instead.

## Conflicts and changed meaning

Continue the fixture with a competing candidate. Keep both claims visible and
inspect their evidence before choosing:

```bash
python3 - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["walkthrough_root"]) / "policy"
(root / "sources/alternative.txt").write_text(
    "Alternative proposal: dispatch the training parcel by 15:00.\n", encoding="utf-8")
(root / "knowledge/kb-alternative.md").write_text(
    "---\nid: kb-alternative\nevidence: verifiable\nprovenance:\n"
    "  - sources/alternative.txt\n---\n# Alternative deadline\n\n"
    "The alternative proposal requires dispatch by 15:00.\n", encoding="utf-8")
PY
(cd "$walkthrough_root/policy" && python3 -P -m validated_memory validate)
consult bind policy:kb-alternative --support sources/alternative.txt \
  --authority training-policy --scope exercise=parcel --actor workflow-agent \
  --reason 'Inspected the alternative proposal and its applicability.'
capture deadline_conflict conflict --candidate policy:kb-rule --candidate policy:kb-alternative \
  --scope exercise=parcel --actor workflow-agent \
  --reason 'The two policy candidates give incompatible dispatch deadlines.'
# Expected refusal: the conflict has no choice yet.
consult read planning:kb-plan --scope exercise=parcel
consult choose "$deadline_conflict" --candidate policy:kb-rule \
  --scope exercise=parcel --actor workflow-agent \
  --reason 'For this synthetic exercise, retain the original policy as the applicable candidate.'
capture plan_receipt read planning:kb-plan --scope exercise=parcel --max-bytes 65536
capture plan_use record-use planning:kb-plan --receipt "$plan_receipt"
```

An undisposed conflict blocks its participants; choosing one permits only that
exact candidate revision for this scope. This scripted exercise choice is not
evidence that real conflicting policy can be resolved automatically. Obtain an
unresolved material decision from the appropriate human instead of inventing it.

Now author successors reflecting a 17:00 policy deadline. Supersession belongs
to the replacing unit and uses a YAML block list. Both predecessors remain:

```bash
python3 - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["walkthrough_root"])
(root / "policy/sources/policy-v2.txt").write_text(
    "The revised training policy requires dispatch by 17:00.\n", encoding="utf-8")
(root / "policy/knowledge/kb-rule-v2.md").write_text(
    "---\nid: kb-rule-v2\nevidence: verifiable\nsupersedes:\n  - kb-rule\n"
    "provenance:\n  - sources/policy-v2.txt\n---\n# Revised deadline\n\n"
    "Dispatch the training parcel by 17:00.\n", encoding="utf-8")
(root / "planning/knowledge/kb-plan-v2.md").write_text(
    "---\nid: kb-plan-v2\nevidence: verifiable\nsupersedes:\n  - kb-plan\n"
    "provenance:\n  - sources/design.txt\n---\n# Revised plan\n\n"
    "Dispatching at 15:30 meets the revised training deadline of 17:00.\n",
    encoding="utf-8")
PY
(cd "$walkthrough_root/policy" && python3 -P -m validated_memory validate)
(cd "$walkthrough_root/planning" && python3 -P -m validated_memory validate)
consult bind policy:kb-rule-v2 --support sources/policy-v2.txt \
  --authority training-policy --scope exercise=parcel --actor workflow-agent \
  --reason 'Inspected the revised policy and its support.'
consult bind planning:kb-plan-v2 --support sources/design.txt \
  --reference policy:kb-rule-v2 --authority training-planning \
  --scope exercise=parcel --actor workflow-agent \
  --reason 'Compared the plan with the revised policy.'
capture deadline_conflict conflict --prior "$deadline_conflict" \
  --candidate policy:kb-rule-v2 --candidate policy:kb-alternative \
  --replacement policy:kb-rule=policy:kb-rule-v2 \
  --scope exercise=parcel --actor workflow-agent \
  --reason 'The authored policy successor replaces the previous candidate.'
# Expected refusal: the predecessor conflict choice does not carry forward.
consult read planning:kb-plan-v2 --scope exercise=parcel
consult choose "$deadline_conflict" --candidate policy:kb-rule-v2 \
  --scope exercise=parcel --actor workflow-agent \
  --reason 'For this synthetic exercise apply the revised training policy.'
capture plan_receipt read planning:kb-plan-v2 --scope exercise=parcel --max-bytes 65536
capture plan_use record-use planning:kb-plan-v2 --receipt "$plan_receipt"
consumer_root=planning:kb-plan-v2
consult check-use "$plan_use"
```

Canonical successors are always in the same project as their predecessors.
Cross-project references do not create cross-project supersession. When a
consumer's claim stays unchanged but references change, use attributed
`review-support` with the complete new reference set instead. Finish all semantic
maintenance and choices before reading the final consumer again. Superseded
units, binding bytes, conflicts, choices and historical uses remain inspectable.

## Checkpoint before relocation

Once the adopter is stable, capture its content before moving it:

```bash
capture policy_checkpoint checkpoint policy --actor workflow-agent --reason 'Prepare the fixture relocation.'
mv "$walkthrough_root/policy" "$walkthrough_root/policy-moved"
consult relocate policy "$walkthrough_root/policy-moved" \
  --checkpoint "$policy_checkpoint" --actor workflow-agent \
  --reason 'Moved the same fixture without changing its captured content.'
# Expected refusal: relocation changed the registration snapshot.
consult check-use "$plan_use"
capture plan_receipt read "$consumer_root" --scope exercise=parcel --max-bytes 65536
capture plan_use record-use "$consumer_root" --receipt "$plan_receipt"
consult check-use "$plan_use"
```

The old root must be unavailable; the latest checkpoint must belong to the
current registration and match the new content exactly. A changed or cloned tree
is not silently accepted. Checkpoint creation alone does not invalidate a receipt;
relocation changes registration and requires final reacquisition for new use.

## Refusals and limits the workflow must expose

Exit codes remain `0` for success/warnings, `1` for gating failures and `2` for
malformed command arguments. Diagnostics must name affected identities or paths,
explain the reason and offer a next step; these are guidance, not literal strings:

| Situation | Actionable next step |
| --- | --- |
| Changed support | Inspect old/new content; review an unchanged claim or author its successor; reconsult. |
| Changed enrolled snapshot, even outside the selected closure | Name the unrelated path/change; inspect its impact and reacquire the final consumer. |
| Wrong-root or stale receipt | Complete authoring/binding, then read the exact consumer and pass its returned receipt. |
| Missing/stale choice | Inspect candidates; retain the required conflict successor and an explicit new choice. |
| Missing/unavailable adopter | Restore its registered root, or follow checkpoint-backed relocation; do not claim current eligibility. |
| Oversized acquisition or closure | Name the exceeded bound; narrow enrolled scope or request a permitted larger byte limit without truncation. |
| Hypothesis, unbound dependency, invalid documents or real cycle | Resolve the named eligibility problem through evidence, binding or authoring; do not retry blindly. |
| Busy store or interrupted capture | Retry after the active writer/change finishes; do not break a live lock. |
| Hot rollback journal | Run explicit `recover`, then retry; recovery does not bless changed evidence. |
| Corrupt history or relocation mismatch | Preserve history and inspect/restore inputs; no force-accept or partial usable history. |

To narrow enrolled scope, select a separate explicit store with fewer project
registrations; this slice does not provide an unregister operation.

Consultation captures all enrolled semantic inputs. Repeated unrelated drift
may make routine use expensive. It does not probe anchors or check verdict logs
unless those files are explicitly declared support, and even then it reads their
bytes rather than executing probes. Authority, applicability, support selection,
dependency completeness and semantic conflict discovery remain declarations and
judgments. Hashes prove neither entailment nor independent corroboration.

The store is local, outside adopter roots; retention is not a portable backup or
federation promise. Canonical authoring is separate from store transactions.
External editors do not share SQLite locking, so optimistic recapture detects
observed races but leaves a residual change window. Hooks do not gate arbitrary
responses. No success implies perpetual truth, comprehension or measured savings.


## Commands

```text
python3 -P -m validated_memory consultation --store PATH OPERATION
```

`PATH` names an explicit SQLite database whose parent already exists, outside all
registered roots. There is no global default, implicit latest handle or automatic
store discovery. All operations leave adopter files unchanged; ordinary authoring
in the walkthrough is separate. Registration requires a canonical nonsymlink
adopter root, `validated-memory.md` and `knowledge/`. A declared extension schema
must be inside that root. Base-contract-only adopters are supported. Existing
canonical validation errors gate; warnings do not.

| Operation and arguments | Effect |
| --- | --- |
| `register ALIAS ROOT --source LABEL` | Enroll a validated root with a generated stable workspace-local project UUID. Alias collisions, duplicate/nested roots and store overlap refuse. |
| `bind ALIAS:ID --support PATH [--reference ALIAS:ID] --authority LABEL --scope KEY=VALUE --actor ACTOR --reason REASON` | Capture a first binding of an active unit to explicit local support and qualified dependencies. Support and reference options are repeatable. |
| `review-support ALIAS:ID --prior HANDLE` plus every binding declaration option | Retain a reviewed revision for unchanged canonical unit bytes. Supply the complete support/reference set, authority and scope, including unchanged declarations. |
| `conflict --candidate ALIAS:ID --candidate ALIAS:ID --scope KEY=VALUE --actor ACTOR --reason REASON` | Retain conflicting exact candidate revisions. Each must be active, bound and applicable in conflict scope. Repeat candidate for more participants. |
| `conflict --prior HANDLE` plus conflict options and any `--replacement OLD=NEW` mappings | Replace the current conflict with explicitly captured revisions. Scope and candidate count stay fixed; changed identities require same-project canonical supersession. |
| `choose CONFLICT_HANDLE --candidate ALIAS:ID --scope KEY=VALUE --actor ACTOR --reason REASON` | Retain an attributed choice for the exact current conflict. Scope must equal conflict scope; every candidate revision must still be current. |
| `read ALIAS:ID --scope KEY=VALUE [--max-bytes N]` | Deliver the complete eligible transitive closure and support once each, then retain its receipt. Final acquisition uses the authored consumer as root. |
| `record-use ALIAS:ID --receipt HANDLE` | Recheck the exact consumer/receipt root, complete enrolled snapshot and eligibility; retain a checked-use record. |
| `check-use HANDLE` | Read-only current-use check of a historical use; no new receipt or persisted timestamp. |
| `show HANDLE` | Read-only historical artifact inspection, including retained predecessor bytes. Does not require current adopter availability. |
| `checkpoint ALIAS --actor ACTOR --reason REASON` | Retain a content-only relocation baseline for the current registration. Stale bindings alone do not prevent a checkpoint. |
| `relocate ALIAS ROOT --checkpoint HANDLE --actor ACTOR --reason REASON` | Retain an explicit new root after checking old-root unavailability and exact content equivalence with the latest checkpoint. |
| `recover` | Explicit SQLite recovery and full integrity validation; no semantic history changes. May initialize a genuinely empty existing database, without registering an adopter. |

`--scope` is repeatable, with unique keys. All binding scope pairs must be present
and equal in requested scope; matching is case-sensitive and has no wildcard.
A conflict applies when its scope is a subset of requested scope. Every applicable
conflict must permit every participant reached by the closure. Equal unit IDs in
different projects remain distinct. `--authority` must equal the owning
registration's source label, an explicit declaration rather than authenticated
ownership. `--source` is a registration label, not a source-file path.

References are declared dependencies; they do not assert independent corroboration.
Reached units must be active, bound at current canonical/support/reference
revisions and `measured` or `verifiable`. There is no strength ordering between
those states. Hypotheses refuse checked use. Shared dependencies are visited once;
a true reference cycle refuses, while returning to another unit in the same
project is allowed. Conflicts are explicitly declared, not inferred from text.

### Success output and retry

All successes return canonical UTF-8 JSON with `schema_version: 1`, `operation`
and `status`, followed by newline. Artifact-producing operations return a handle
in `id`. Register/relocate add `project_id` and `alias`; bind/review-support/record-use
add `root`; checkpoint adds `project_id`. `show` adds `artifact`, the full event,
and uses status `historical; not a current eligibility check`. `check-use` returns
`id` and status `current`; `record-use` uses `checked-use recorded`. `recover`
uses `recovered` and has no artifact ID.

`read` is the exception: exactly two JSON lines, in this order. The notation
below describes fields rather than supplying fabricated artifact IDs:

```text
{schema_version: 1, operation: "read", status: "inspected",
 root: {project: UUID, unit: UnitID}, scope: Scope, content: Content,
 limitation: "Inspection is not checked use. Hashes do not prove entailment; anchor verdicts are not checked."}
{schema_version: 1, operation: "read", status: "receipt recorded", id: Digest}
```

The first line has no usable ID. It must be written and flushed before final
input recapture and receipt commit; only then is the second line emitted. If
inputs change after content delivery, the command refuses without committing a
receipt or emitting a handle. Downstream display and understanding are outside
this OS-output guarantee. Count both serialized lines, including newlines, against
`--max-bytes`. No truncated output can become a usable receipt.

Unchanged semantic retries return the existing artifact after required integrity
and current-state checks. Attribution changes alone do not create a revision or
claim a new review. A repeated read still returns full content before its handle.
Precommit failure creates no event. Postcommit output failure reports the committed
ID on stderr when possible; neither channel being available makes delivery
impossible. Preserve diagnostics and retry the operation rather than inventing IDs.

### Support rename and retained history

If an active unit's support file is renamed or removed, reads and current-use
checks refuse. The following example applies to the fixture while `policy:kb-rule`
is still active, before the successor and relocation sections above. After inspecting retained old content and new content, use
`review-support` with the current binding handle and the replacement path:

```bash
capture rule_binding review-support policy:kb-rule --prior "$rule_binding" \
  --support sources/policy-renamed.txt --authority training-policy \
  --scope exercise=parcel --actor workflow-agent \
  --reason 'Inspected the renamed support file; its content and claim are unchanged.'
```

This example assumes the file was actually moved within its owning root. The
review evaluates its proposed replacement declaration; it need not reopen the
missing old file. The old binding retains the old path, text and hash. Other
active bindings retain their own declarations: if another still names the missing
old path, that missing support still gates this operation. Restore that input and
review each declaration without silently retargeting other units. Superseded
canonical units retain history, but their obsolete support paths no longer gate
current capture. New use still requires final reacquisition.

### Bounds and retention

| Bound | Limit |
| --- | --- |
| Read output, combined JSON lines | Default 65,536 bytes; `--max-bytes` range 2,048–1,048,576 |
| Workspace | 16 projects, 10,000 events, 32 MiB total canonical payload UTF-8 bytes |
| Captured input | 4,096 distinct files, 1 MiB each, 16 MiB aggregate |
| Dependency closure | 128 units, 512 reference edges |
| Per binding | 64 support files, 64 references |
| Per conflict | 2–64 candidates; at most 128 documents per successor proof |
| Scope | 1–32 entries; each value nonblank and at most 256 UTF-8 bytes |
| Actor/reason | Nonblank; at most 256 / 4,096 UTF-8 bytes |
| Alias/source/scope key | ASCII `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}` |
| Paths | At most 4,096 UTF-8 bytes; support paths are root-relative, without traversal |
| Store lock wait | 5,000 milliseconds |

Input files must be strict UTF-8 regular nonsymlink files without symlink
ancestors. Support cannot escape its registered root. All enrolled configuration,
declared schemas, complete knowledge membership/bytes, support of active bound
units and current semantic declaration heads participate in snapshot invalidation.
Receipts, checked-use records and checkpoints do not invalidate themselves.
In schema 2, matching accepted challenges additionally gate current use and
enter a receipt review frontier; resolution or reversal does not revive older
affected receipts. See [correction eligibility](incorporation.md#inspection-retries-and-current-eligibility).
Operational outputs, memory, indexes and verdict logs are excluded unless an
ordinary in-root file is explicitly declared support.

SQLite uses DELETE rollback journaling, EXTRA synchronous durability and native
locks. One transaction commits each operation; physical database bytes are not
an append-only file, and transient rollback sidecars may exist. Read-only commands
use `mode=ro`, create no files and refuse a hot journal needing explicit recovery.
Recovery does not remove corrupt semantic rows or migrate unknown schemas. Never
manually delete sidecars or force-break a live lock. Retained canonical/support
text and inspection content consume the store bound; there is no automatic
compaction or history-pruning command. Explicit [whole-history transfer](transfer.md)
is available in the unreleased source tree. Event hashes detect accidental
corruption, not an attacker able to rewrite rows and recompute hashes.
