# Incorporation and correction effects

**Unreleased source-tree addition.** Published 2.2.0 supports the original
consultation commands and schema 1; it does not provide this lifecycle or schema 2.
Use a source checkout containing this implementation for the commands below.
Current source storage is schema 3 with [portable transfer](transfer.md); schema 2
remains the earlier unreleased incorporation format.

Use this opt-in lifecycle to retain a contribution, inspect it, accept or reject
it, and observe whether its exact accepted content was incorporated. A challenge
can request review of an existing claim without inventing a replacement fact.
Consumers and explicitly tracked publications have separate correction records.

All operations use `python3 -P -m validated_memory consultation --store PATH`.
The store and [checked consultation](consultation.md) requirements are unchanged.
These commands never write canonical adopter Markdown or execute submitted text.
[Incorporation storage](incorporation-storage.md) defines exact retained payloads.

## What each outcome means

| Outcome | What was observed or asserted |
| --- | --- |
| Accepted proposal | An attributed disposition about exact inspected material; canonical installation has not been established. |
| Incorporation | The accepted bytes, declaration and installation context matched current canonical inputs. The CLI did not author them. |
| Accepted challenge | Review is required for the exact target revision in matching scope. The claim was not automatically proved false or superseded. |
| Resolution | Current evidence and an attributed explanation addressed that accepted challenge through its reviewed binding or an incorporated successor. |
| Address | One affected historical use is explicitly linked to a checked use validated now. Other consumers remain independently unaddressed. |
| Reflection | Changed bytes of one previously tracked artifact were observed with its address and current new use. Semantic sufficiency and external distribution are not verified. |

Factual testimony and policy authority are different assertions. Accepting either
does not change an evidence state, prove entailment, or authenticate the actor.
Separate contributions sharing support retain that common evidence; contributor
count is not independent corroboration. Existing authoring, lookup, validation,
rendering and fail-open hooks gain no implicit incorporation requirement.

## Explicit store upgrade

Fresh registration and explicit recovery of an empty database create storage
schema 3. Existing complete schema 1/2 stores continue ordinary consultation with
legacy receipts; lifecycle commands require at least 2, transfer writes require 3. Upgrade
only the explicitly selected store when this lifecycle is wanted:

```sh
python3 -P -m validated_memory consultation --store /absolute/workspace.sqlite upgrade
```

`upgrade` validates and changes an existing complete schema 1/2 store to 3 in one
transaction. It preserves workspace identity and every old logical event field,
including original payload strings. It does not create missing/empty stores.
Retry on valid schema 3 succeeds without rewriting. An interrupted upgrade leaves
a complete old or new schema after rollback/recovery. Older clients that only
support only older schemas refuse upgraded stores; ordinary commands never migrate them
implicitly. Do not edit SQLite or delete recovery sidecars manually.

The database schema is distinct from the JSON envelope `schema_version: 1`.
An upgrade response includes `storage_version: 3` and status `upgraded`.

## Commands

The attributed operations below require `--actor ACTOR --reason REASON`;
`upgrade`, `inspect` and `reconcile` have no attribution options.
`--support`, `--reference` and `--scope` are repeatable as in ordinary bindings.
Scope is nonempty and authority equals the registered source label.

| Operation | Contract |
| --- | --- |
| `submit ALIAS FILE --path knowledge/new.md --support PATH --reference ALIAS:ID --authority LABEL --scope KEY=VALUE` | Capture a new external candidate and full prospective configured corpus without writing the destination. References are optional. Both destination path and ID must be absent; declared predecessors must be active same-project units. |
| `challenge ALIAS:ID --statement FILE --kind factual\|policy --scope KEY=VALUE` | Retain a statement and exact current retained binding/claim. No live adopter capture is required. Submission alone does not gate. |
| `inspect HANDLE [--max-bytes N]` | Emit complete retained proposal, challenge or binding/support-review material, flush, then record its inspection. No actor/reason flags. Historical material is not a current evidence check. |
| `decide HANDLE --inspection ID --outcome accept\|reject\|defer [--prior ID]` | Decide on the exact inspected proposal/challenge. Initial disposition has no prior; a change requires the exact current decision. |
| `renew PROPOSAL [--support PATH --reference ALIAS:ID --scope KEY=VALUE]` | Capture a new proposal context preserving exact candidate bytes/path/ID/authority/predecessors. Installed candidates are allowed only with a matching current binding. Fresh inspection and acceptance are required. |
| `incorporate PROPOSAL --decision ACCEPTED_DECISION` | Verify latest acceptance, exact canonical installation and matching current binding against the proposal's expected inventory/heads. Refusal does not undo authoring. |
| `resolve CHALLENGE --decision ACCEPTED_DECISION --review BINDING --inspection BINDING_INSPECTION` | Resolve using the current binding for the unchanged claim. The binding inspection must follow challenge acceptance. The resolution is a fresh attributed review even when evidence/declaration stayed identical. |
| `resolve CHALLENGE --decision ACCEPTED_DECISION --incorporation ID --inspection BINDING_INSPECTION` | Resolve through an incorporated same-project canonical successor, with its exact current binding inspected after challenge acceptance. |
| `reconcile CHALLENGE` | Read-only complete bounded report of history and current observations. No actor/reason flags. |
| `address CHALLENGE --decision ACCEPTED_DECISION --old-use ID --new-use ID [--mode replacement\|dependency-removed]` | Record an explicit effect link after checking current resolution and new-use eligibility. Default replacement requires the resolved revision/binding in the new receipt; explicit dependency-removed requires the targeted revision absent. |
| `track-publication CHALLENGE --project ALIAS --path RELATIVE --use OLD_USE` | Retain original bytes of one declared artifact in the affected consumer's project. No accepted disposition is needed. |
| `reflect TRACK --address ID` | Observe changed bytes at that same path with a matching address and current new use. |

With no declaration flags, `renew` retains exact prior support/reference/scope
and refuses their drift. If any such flag appears, support and scope must both
be supplied as complete replacements; omitted references explicitly mean none.
Partial declarations are usage errors. For an installed candidate, first perform
ordinary `bind` or `review-support` to match the proposed new declaration. Changed
canonical meaning requires its real successor, not same-ID renewal.

## Inspection, retries and current eligibility

`inspect` returns exactly two JSON lines. Line one has status
`historical material; not a current evidence check`, the target `id`, and its full
`artifact`. Line two has status `inspection recorded` and the inspection `id`.
Check exit 0 and both lines before using the second handle. Complete material is
written and flushed before inspection commit; a lone first line is not a retained
inspection. Each successful inspection is a new occurrence, even for identical
material. `show` remains historical inspection without that retained occurrence.

Proposal/challenge submissions retain distinct attribution. Decision retries
compare the complete current payload before prior comparison; changed reason or
attribution requires an explicit new decision with prior. New attributed
observations/links return identical payload retries only after their required
current checks. One accepted challenge decision has exactly one resolution; a
different resolution requires a new explicit acceptance and later inspection.

Accepted challenges gate `read`, `record-use` and `check-use` when target revision
and scope match the declared closure. Reject/defer closes an open gate without
erasing earlier acceptance. Resolution/reversal does not restore pre-acceptance
receipts: their review frontier is stale. Reacquire affected consumers after
resolution. An unaffected replacement/removal use may already be current and
may retain its old deduplicated ID; `address` validates it now without requiring
a newly inserted use. Mere disappearance of a dependency never records an address.

`incorporate` creation/retry compares its complete installation context. Perform
it before unrelated consumer authoring. Downstream resolution/address/reflection
instead checks the still-accepted proposal, same canonical revision and current
binding/support-review lineage. Later legitimate consumer bindings do not make
source incorporation unusable. A proposal disposition reversal does not unpublish
Markdown, but invalidates current assertions relying on that incorporation.

## Executable two-project example

Run the following Bash blocks in order from a source checkout, in one shell.
They create disposable synthetic adopters and retain every consultation command,
exit code, stdout and stderr under the printed `incorporation_demo` directory.
The helper parses CLI-generated handles; no person constructs hashes or edits
SQLite. Displayed content is for inspection. The automatic acceptances below
are part of the synthetic exercise, not permission to automate semantic judgment
in an adopter. All actual authorship remains ordinary file writing outside the CLI.

```bash
set -e
export PYTHONPATH="$PWD"
incorporation_demo=$(mktemp -d /tmp/incorporation-workflow.XXXXXX)
export incorporation_demo
mkdir "$incorporation_demo/transcript"
call_number=0
invoke() {
  call_number=$((call_number + 1))
  last_response="$incorporation_demo/transcript/$call_number.stdout"
  local code
  python3 - "$incorporation_demo/transcript/$call_number.args.json" "$@" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]), encoding="utf-8")
PY
  if python3 -P -m validated_memory consultation \
      --store "$incorporation_demo/workspace.sqlite" "$@" \
      > "$last_response" 2> "$incorporation_demo/transcript/$call_number.stderr"; then
    code=0
  else
    code=$?
  fi
  printf '%s\n' "$code" > "$incorporation_demo/transcript/$call_number.exit"
  cat "$last_response"
  cat "$incorporation_demo/transcript/$call_number.stderr" >&2
  return "$code"
}
retain() {
  local variable="$1" result
  shift
  invoke "$@" || return
  result=$(python3 - "$last_response" "$1" <<'PY'
import json, sys
from pathlib import Path
rows = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines()]
operation = sys.argv[2]
assert all(row["schema_version"] == 1 and row["operation"] == operation for row in rows)
if operation in ("read", "inspect"):
    assert len(rows) == 2
    if operation == "read":
        assert rows[0]["status"] == "inspected" and "id" not in rows[0]
        assert rows[1]["status"] == "receipt recorded"
    else:
        assert rows[0]["status"] == "historical material; not a current evidence check"
        assert rows[1]["status"] == "inspection recorded"
else:
    assert len(rows) == 1
print(rows[-1]["id"])
PY
  ) || return
  printf -v "$variable" '%s' "$result"
  python3 - "$incorporation_demo/transcript/handles.json" "$variable" "$result" <<'PYMAP'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
handles = json.loads(path.read_text()) if path.exists() else {}
handles[sys.argv[2]] = sys.argv[3]
path.write_text(json.dumps(handles, indent=2) + "\n", encoding="utf-8")
PYMAP
}
refuse() {
  local code
  if invoke "$@"; then
    printf 'Expected refusal, received success\n' >&2
    return 1
  else
    code=$?
    test "$code" -eq 1
  fi
}
actor=(--actor fixture-agent)
scope=(--scope exercise=dispatch)
python3 - <<'PY'
import os
from pathlib import Path
root = Path(os.environ["incorporation_demo"])
for alias in ("policy", "planning"):
    for folder in ("knowledge", "sources", "exports"):
        (root / alias / folder).mkdir(parents=True, exist_ok=True)
    (root / alias / "validated-memory.md").write_text("---\nid_prefix: kb\n---\n")
def unit(alias, identity, body):
    (root / alias / "knowledge" / (identity + ".md")).write_text(
        f"---\nid: {identity}\nevidence: verifiable\n---\n{body}\n")
unit("policy", "kb-rule", "Queue admission closes at 16:00; this is not carrier departure.")
unit("planning", "kb-brief", "The operator brief follows the queue-admission rule.")
unit("planning", "kb-manual", "The manual trial follows the queue-admission rule.")
(root / "policy/sources/policy.txt").write_text("Synthetic trial policy: queue admission until 16:00.\n")
(root / "planning/sources/plan.txt").write_text("Synthetic operator planning evidence.\n")
(root / "planning/exports/brief.txt").write_text("Trial deadline: 16:00.\n")
(root / "planning/exports/manual.txt").write_text("Manual trial deadline: 16:00.\n")
(root / "question.txt").write_text("Does 16:00 mean queue admission or carrier departure?\n")
print(root)
PY
invoke register policy "$incorporation_demo/policy" --source policy
invoke register planning "$incorporation_demo/planning" --source planning
retain rule bind policy:kb-rule --support sources/policy.txt --authority policy \
  "${scope[@]}" "${actor[@]}" --reason 'Inspected synthetic queue policy.'
for consumer in kb-brief kb-manual; do
  invoke bind "planning:$consumer" --support sources/plan.txt --reference policy:kb-rule \
    --authority planning "${scope[@]}" "${actor[@]}" --reason 'Inspected declared dependency.'
done
retain receipt read planning:kb-brief "${scope[@]}"
retain old_use record-use planning:kb-brief --receipt "$receipt"
retain manual_receipt read planning:kb-manual "${scope[@]}"
retain manual_old_use record-use planning:kb-manual --receipt "$manual_receipt"
```

Accept a factual challenge and resolve it with unchanged evidence. Fresh binding
inspection follows acceptance; no fake support revision is needed. Track the
publication before rewriting it. The temporary support outage demonstrates that
retained inspection/decision and historical reconciliation survive unavailable
evidence, while live resolution refuses until the same evidence is restored.

```bash
retain challenge challenge policy:kb-rule --statement "$incorporation_demo/question.txt" \
  --kind factual "${scope[@]}" "${actor[@]}" --reason 'Review the deadline interpretation.'
retain track track-publication "$challenge" --project planning --path exports/brief.txt \
  --use "$old_use" "${actor[@]}" --reason 'Declare this brief depends on the recorded use.'
mv "$incorporation_demo/policy/sources/policy.txt" "$incorporation_demo/policy/sources/policy.unavailable"
retain inspection inspect "$challenge"
retain decision decide "$challenge" --inspection "$inspection" --outcome accept \
  "${actor[@]}" --reason 'The interpretation deserves explicit review.'
refuse check-use "$old_use"
retain binding_inspection inspect "$rule"
refuse resolve "$challenge" --decision "$decision" --review "$rule" \
  --inspection "$binding_inspection" "${actor[@]}" --reason 'Attempt live resolution while evidence is unavailable.'
invoke reconcile "$challenge"
mv "$incorporation_demo/policy/sources/policy.unavailable" "$incorporation_demo/policy/sources/policy.txt"
retain resolution resolve "$challenge" --decision "$decision" --review "$rule" \
  --inspection "$binding_inspection" "${actor[@]}" --reason 'Unchanged evidence explicitly says queue admission.'
refuse record-use planning:kb-brief --receipt "$receipt"
retain receipt_after read planning:kb-brief "${scope[@]}"
retain use_after record-use planning:kb-brief --receipt "$receipt_after"
retain address address "$challenge" --decision "$decision" --old-use "$old_use" \
  --new-use "$use_after" "${actor[@]}" --reason 'Reviewed the unchanged queue-admission interpretation.'
printf 'Queue admission closes at 16:00; carrier departure is separate.\n' \
  > "$incorporation_demo/planning/exports/brief.txt"
invoke reflect "$track" --address "$address" "${actor[@]}" --reason 'The brief now distinguishes admission from departure.'
invoke reconcile "$challenge"
```

Next accept a synthetic policy change, author its successor, and demonstrate
stale installed-proposal recovery after an unrelated canonical addition. The old
unit is retained unchanged. Renewal creates a new proposal requiring inspection
and acceptance; it does not delete and resubmit the installed unit.

```bash
python3 - <<'PY'
import os
from pathlib import Path
root = Path(os.environ["incorporation_demo"])
(root / "policy-question.txt").write_text("Propose queue admission until 17:00 for the next trial.\n")
(root / "policy/sources/next.txt").write_text("Synthetic next-trial policy: queue admission until 17:00.\n")
(root / "candidate.md").write_text("---\nid: kb-next\nevidence: verifiable\nsupersedes:\n  - kb-rule\n---\nQueue admission closes at 17:00 in the next trial.\n")
PY
retain policy_challenge challenge policy:kb-rule --statement "$incorporation_demo/policy-question.txt" \
  --kind policy "${scope[@]}" "${actor[@]}" --reason 'Record a proposed policy change, not a measurement.'
retain policy_inspection inspect "$policy_challenge"
retain policy_decision decide "$policy_challenge" --inspection "$policy_inspection" --outcome accept \
  "${actor[@]}" --reason 'Accept review of the next-trial policy.'
retain manual_track track-publication "$policy_challenge" --project planning --path exports/manual.txt \
  --use "$manual_old_use" "${actor[@]}" --reason 'Declare the manual publication dependency.'
retain proposal submit policy "$incorporation_demo/candidate.md" --path knowledge/kb-next.md \
  --support sources/next.txt --authority policy "${scope[@]}" "${actor[@]}" --reason 'Propose the explicitly changed policy.'
retain proposal_inspection inspect "$proposal"
retain proposal_decision decide "$proposal" --inspection "$proposal_inspection" --outcome accept \
  "${actor[@]}" --reason 'Accept the inspected synthetic successor.'
cp "$incorporation_demo/candidate.md" "$incorporation_demo/policy/knowledge/kb-next.md"
retain next_binding bind policy:kb-next --support sources/next.txt --authority policy \
  "${scope[@]}" "${actor[@]}" --reason 'Bind the authored accepted successor.'
printf '%s\n' '---' 'id: kb-unrelated' 'evidence: verifiable' '---' 'An unrelated fixture observation.' \
  > "$incorporation_demo/planning/knowledge/kb-unrelated.md"
refuse incorporate "$proposal" --decision "$proposal_decision" "${actor[@]}" --reason 'Check the original installation context.'
retain renewed renew "$proposal" "${actor[@]}" --reason 'Inspect the unchanged installation in its changed context.'
retain renewed_inspection inspect "$renewed"
retain renewed_decision decide "$renewed" --inspection "$renewed_inspection" --outcome accept \
  "${actor[@]}" --reason 'Accept the complete renewed context.'
mv "$incorporation_demo/policy/sources/next.txt" "$incorporation_demo/policy/sources/next-reviewed.txt"
refuse incorporate "$renewed" --decision "$renewed_decision" "${actor[@]}" --reason 'Observe the original support declaration.'
refuse renew "$renewed" "${actor[@]}" --reason 'Default renewal cannot silently replace support.'
retain reviewed_binding review-support policy:kb-next --prior "$next_binding" \
  --support sources/next-reviewed.txt --authority policy "${scope[@]}" "${actor[@]}" \
  --reason 'The support moved; its inspected meaning and the canonical claim are unchanged.'
retain renewed_support renew "$renewed" --support sources/next-reviewed.txt "${scope[@]}" \
  "${actor[@]}" --reason 'Explicitly propose the complete replacement justification.'
retain support_inspection inspect "$renewed_support"
retain support_decision decide "$renewed_support" --inspection "$support_inspection" --outcome accept \
  "${actor[@]}" --reason 'Accept the inspected new justification without changing the claim.'
retain incorporation incorporate "$renewed_support" --decision "$support_decision" \
  "${actor[@]}" --reason 'Observe the exact accepted installation and reviewed justification.'
retain next_inspection inspect "$reviewed_binding"
invoke resolve "$policy_challenge" --decision "$policy_decision" --incorporation "$incorporation" \
  --inspection "$next_inspection" "${actor[@]}" --reason 'The incorporated successor records the changed policy.'
```

The same block also renames the installed candidate's support before incorporation.
After the required refusals it explicitly reviews the unchanged claim with the
complete intended declaration. Its second renewal uses `renew PROPOSAL --support NEW_PATH --scope exercise=dispatch` and all
intended references, followed by fresh inspection/acceptance/incorporation.
Omitting reference flags means none. Default no-flags renewal must refuse changed
support. The old proposal/support stay retained; never restore obsolete evidence
or invent a successor merely to obtain incorporation.

Finish both consumer successors before final acquisition. One keeps the corrected
policy dependency; the other intentionally removes it and supplies independent
support. These are separate semantic assertions, recorded explicitly.

```bash
python3 - <<'PY'
import os
from pathlib import Path
root = Path(os.environ["incorporation_demo"])
for identity, prior, body in (
    ("kb-brief-next", "kb-brief", "The brief follows the next-trial queue policy."),
    ("kb-manual-next", "kb-manual", "The manual trial now uses an independent process without the queue deadline."),
):
    (root / "planning/knowledge" / (identity + ".md")).write_text(
        f"---\nid: {identity}\nevidence: verifiable\nsupersedes:\n  - {prior}\n---\n{body}\n")
(root / "planning/sources/manual.txt").write_text("Synthetic manual process operates independently of the queue.\n")
PY
invoke bind planning:kb-brief-next --support sources/plan.txt --reference policy:kb-next \
  --authority planning "${scope[@]}" "${actor[@]}" --reason 'The brief retains the corrected policy dependency.'
invoke bind planning:kb-manual-next --support sources/manual.txt --authority planning \
  "${scope[@]}" "${actor[@]}" --reason 'The independent manual process intentionally removes the policy dependency.'
retain final_receipt read planning:kb-brief-next "${scope[@]}"
retain final_use record-use planning:kb-brief-next --receipt "$final_receipt"
retain final_manual_receipt read planning:kb-manual-next "${scope[@]}"
retain final_manual_use record-use planning:kb-manual-next --receipt "$final_manual_receipt"
invoke reconcile "$policy_challenge"
invoke address "$policy_challenge" --decision "$policy_decision" --old-use "$old_use" \
  --new-use "$final_use" --mode replacement "${actor[@]}" --reason 'The successor brief uses the resolved policy.'
invoke address "$policy_challenge" --decision "$policy_decision" --old-use "$use_after" \
  --new-use "$final_use" --mode replacement "${actor[@]}" --reason 'The later historical brief use is also explicitly addressed.'
retain manual_address address "$policy_challenge" --decision "$policy_decision" --old-use "$manual_old_use" \
  --new-use "$final_manual_use" --mode dependency-removed "${actor[@]}" --reason 'The independent manual process deliberately removed this dependency.'
printf 'The manual trial follows its independent process; the queue deadline does not apply.\n' \
  > "$incorporation_demo/planning/exports/manual.txt"
invoke reflect "$manual_track" --address "$manual_address" "${actor[@]}" --reason 'The manual publication now states the independent process.'
invoke reconcile "$policy_challenge"
printf 'Retained synthetic workspace: %s\n' "$incorporation_demo"
```

The report before `address` is the negative control: successor authoring and fresh
use alone do not address a historical effect. The final report retains original
uses and publication bytes while displaying the explicit links and reflections.
The first challenge's history also remains; addressing the second challenge does
not silently discharge the first challenge's unaddressed manual use.

## Read reports without hiding unfinished work

`reconcile` lists historical declared dependence before attempting live capture.
Its `review` is `not-open`, `open` or `resolved`. Each use, address, publication
and reflection has a separate observation: `current`, `stale snapshot`,
`review required`, `conflict/invalid binding` or `unavailable`, with an actionable
reason when non-current. A failed current prerequisite makes the live observation
unavailable while historical rows remain visible. Exit 0 means a complete report,
including unresolved/unavailable rows; bounds or corrupt history refuse with 1,
and malformed command arguments return 2.

Track a publication before changing it. Paths must be in the consumer's project;
canonical knowledge/memory/configuration, the declared schema, `.git`,
`.validated-memory`, the store and sidecars are excluded. Generated HTML and
executable-mode text may be observed but are never executed. Regenerate derived
artifacts through their normal generator; do not hand-edit them. Reflection
compares changed bytes at the original path and the matching challenge/old-use
address, not arbitrary similar output or evidence of distribution.

## Bounds and remaining limits

| Input/output | Limit |
| --- | --- |
| External candidate | Regular nonsymlink strict UTF-8, outside enrolled roots, at most 1 MiB |
| Challenge statement | Nonblank strict UTF-8 regular nonsymlink file, at most 64 KiB |
| `inspect` output | Both lines combined; default 65,536 bytes, range 2,048–1,048,576 |
| Proposal acceptance | Complete proposal inspection must fit the 1 MiB maximum before submission is retained |
| Tracked publication | In-root regular nonsymlink strict UTF-8, at most 1 MiB |
| `reconcile` | At most 128 affected uses, 128 publications and 1 MiB output; never a silently truncated report |
| Workspace/capture | Existing 10,000 events / 32 MiB payload, 16 projects / 4,096 files / 16 MiB capture limits apply |

All current assertions recapture their inputs; this is optimistic change detection,
not an atomic transaction across adopter files and SQLite. Unrelated input changes
can require explicit renewal or reacquisition. No operation detects undeclared
semantic dependents, proves an agent understood an inspection, transfers history,
or certifies a correction reached an external publication. The synthetic example
measures behavior only; it does not establish usefulness or reduced maintenance.

## Proposals retaining transferred origins

[Portable transfer](transfer.md) is an unreleased schema-3 addition. Inspect imported
scope/material before local authoring. Link all applicable origins to the proposal
before its final local inspection and decision; a foreign receipt or import is
not local acceptance or incorporation. Mapped dependencies must already be
incorporated locally. Source inspection from link-transfer and local inspection
from inspect serve different reviews.

A renewed proposal for identical canonical bytes retains its origin obligations.
Acceptance and incorporation check current origin material with only their own
not-yet-created stage omitted. Current checked use/downstream assertions require
full current local acceptance, incorporation and origin eligibility. Source updates
can stale those assertions without rewriting their historical records. Explicit
local successors inherit origins through canonical ancestry; independence requires
an attributed successor disposition, not omission of a link.
