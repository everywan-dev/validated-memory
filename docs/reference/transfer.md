# Portable historical contributions

**Available since 2.3.0.** Version 2.2.0 provides checked consultation and schema 1;
it does not provide incorporation or transfer. Version 2.3.0 includes both and
creates schema 3. Schema 2 was an unreleased incorporation format. Existing
schema 1/2 stores require explicit `upgrade` for transfer writes; installing the
release does not implicitly migrate a workspace or alter adopter Markdown.

Transfer carries receipt-selected historical material **and complete recorded
workspace history**. It supports offline inspection and explicit local
incorporation. It does not copy files into an adopter, enroll foreign roots,
execute submitted text, fetch a network resource, or authorize local checked use.

Start with the [workflow](#executable-offline-workflow). Exact retained shapes and
bounds are in [Transfer storage](transfer-storage.md). Local proposals, inspection,
acceptance and installation observations follow [Incorporation](incorporation.md).

## Before exporting or authoring

The mandatory `--include-workspace-history` acknowledges disclosure of unrelated
project content, absolute paths, retained publications and nested imports. Choosing
one receipt does not make its history selective. Run `export-transfer --assess`
first: its bounded summary reports exact capsule bytes and whether export fits.
A valid 32 MiB workspace can exceed the 8 MiB export ceiling. Another selected
receipt cannot remove mandatory history. No truncation or history compaction is
performed to make it fit.

An import is historical material with **live origin not checked**. Its capsule
contains the selected canonical/support bytes and recorded correction history.
Hashes establish internal consistency and byte identity; they do not authenticate
an origin, prove semantic equivalence or count independent corroboration. Two
investigations sharing evidence remain two investigations over shared evidence.

Before local authoring, `show-transfer` exposes source scope, dependencies,
predecessors, known status and affected local links. Local proposal scope must
exactly equal the selected source receipt scope in this slice. Different local
IDs, paths, extension fields and wording require explicit semantic judgment;
identity is never inferred from matching names or bytes.
Current reads propagate their requested scope, including a narrower scope, through
the reached origins. Local acceptance does not bypass an applicable origin gate
when the read scope is narrowed.

Full material remains in historical `show IMPORT`. Use bounded read-only JSON
selection, as below, to inspect the selected bytes without sending an entire
capsule into routine agent context. Do not edit capsule JSON, construct event
hashes, or manufacture acceptance. Author destination Markdown and local support
files through the normal authorized workflow.

## Commands and returned material

All commands are under `python3 -P -m validated_memory consultation --store PATH`.
Attribution uses `--actor ACTOR --reason REASON`; it records an assertion, not
an authenticated person or evidence of human approval.

| Command | Outcome |
| --- | --- |
| `export-transfer RECEIPT --include-workspace-history [--max-bytes N] [--assess]` | Read-only historical export. Assess returns counts, exact bytes, disclosure and `supported`; actual export emits only canonical capsule JSON plus newline. |
| `import-transfer FILE --actor ACTOR --reason REASON` | Validate and recapture a safe local capsule, then retain one import. Return `id` and compact `inventory`; no foreign root is opened. |
| `show-transfer IMPORT [--max-bytes N]` | Read-only bounded inventory with scope, mapping material, known statuses, affected local revisions and dependency-first recovery instructions. |
| `link-transfer PROPOSAL --import IMPORT --origin PROJECT_UUID:ID [--dependency ORIGIN_PROJECT:ID=DEST_ALIAS:ID] [--predecessor ORIGIN_PROJECT:ID=DEST_ALIAS:ID] [--origin-only-predecessor ORIGIN_PROJECT:ID] [--prior LINK] [--max-bytes N] --actor ACTOR --reason REASON` | Inspect complete relevant source material and retain explicit correspondence to the local proposal. Mapping flags are repeatable. |
| `detach-transfer PROPOSAL --from LINK [--prior LINK] [--max-bytes N] --actor ACTOR --reason REASON` | Inspect inherited material/corrections and record an attributed independent canonical successor. It cannot detach the identical local revision in place. |

Export bounds are 2048..8388608 bytes, default 8388608. Show/link/detach bounds are
2048..1048576 bytes, default 1048576. Bounds include complete output and newline;
link/detach include both lines. Oversize actual export/show/link refuses before
output. `--assess` returns ordered actionable `refusals`; `supported` is true exactly
when that array is empty. It checks complete capsule byte, origin-depth, registry
and event-work limits including the outer workspace. Actual export repeats that
preflight before output. Assess cannot promise compatibility with an unknown
destination's existing forks or identity.

Each read, use, resolve, link or detach shares one 128-node/512-edge budget across
its complete local and foreign provenance, including inspected material. Helpers
do not restart that budget. Each inventory row is a separate rooted judgment;
unrelated rows and all historical operations are not combined into one graph cap.
Reports still obey selected-closure, history and complete-output bounds, and
return all rows or refuse.

`link-transfer` and `detach-transfer` emit two JSON lines: first `status: inspected`
and complete `review`, then `status: recorded` and the committed `id`. Review
contains exact source canonical/support bytes, binding declarations, predecessor
canonical files, maps, relevant corrections, inherited obligations and limitations.
This includes reached transitive origins and their currently known corrections,
qualified by workspace, plus intermediary transfer-link records showing mappings.
Independent review also follows inherited sibling links even when their origin
claims are ineligible. The complete review must fit the bound; it is not truncated.
Material is written and flushed before append/commit. Consume both lines and exit
0 before using the returned handle. An inspection line alone cannot establish
whether a link committed: precommit failure rolls back, while postcommit delivery
failure may retain the link. Retry validates and reports its existing handle.

Import and exact relink retries preserve their original handles after the required
validation. Relinking changed upstream context requires the exact `--prior` and
fresh local inspection/decision. Changed attribution alone does not create another
import or equivalent link. A shorter compatible capsule cannot roll back a newer
known origin; divergent histories refuse rather than silently choosing a fork.

## Local mappings and origin changes

Incorporate dependencies first. Each immediate source dependency maps one-to-one
to an already incorporated local revision retaining that exact origin revision;
local proposal references include those revisions. All source support hashes must
remain represented in local support, though local paths may differ. Extra local
evidence/dependencies are permitted and remain authored declarations.

Every direct source predecessor needs an explicit disposition. Map all active
canonical counterparts retaining that origin in this destination project before
prospective insertion. When no such counterpart exists, use
`--origin-only-predecessor`: retain exact source predecessor history without
inventing a foreign-to-local supersession edge. This permits A2 when A1 had a
binding but never a receipt. Missing or inconsistent old canonical material
refuses linking; import still remains inspectable. Retired or explicitly independent
counterparts are not reactivated to satisfy a mapping.

Create all effective origin links before the local proposal's final `inspect`
and acceptance. Several origins may be linked sequentially; an unfinished target
need not already be accepted/incorporated to plan its links. Acceptance checks the
complete obligation set, while mapped dependencies retain their normal current
gates. Then author/install, bind and `incorporate` normally. The CLI does not write
canonical files. Acquire the final local consumer receipt after this work and use
`record-use`; a foreign receipt/use is never a local authorization handle.

Proposal acceptance, incorporation and both resolution remedies follow declared
dependencies transitively, including canonical ancestry. The pending-acceptance
exception applies only to the exact target, not its dependencies. Unbound
referenced units remain allowed when their exact canonical bytes and complete
ancestry are already retained; this does not require every dependency to be bound
or exclude hypotheses from resolution. If a new assertion after transfer links
has only live canonical bytes without replayable retained proof, it refuses
atomically. Retain a reviewed binding, or supported retained canonical lineage,
then retry. Frozen histories before transfer links remain unchanged.

Known origin corrections, changed justification, supersession and withdrawn
acceptance can block current linked use. A->B->C followed by a newer A imported
at C invalidates relevant C use even without a new B export. Recovery may require
B to review and re-export; stale gating does not wait for that export. Even a
metadata-only source event conservatively requires renewed link review. Follow
`show-transfer` recovery in dependency order; complete review before reacquisition.
Old local uses remain historical artifacts. Unrelated origin imports do not stale
unrelated uses, and resolving a correction does not revive an old receipt frontier.

Declared local supersession preserves origin obligations through unbound
intermediate ancestors. An independent successor records the removal explicitly,
with complete inherited context and an attributed reason. At a merge, an independent
sibling does not cancel a sibling that retains the origin. Ordinary unlinked
manual copying is outside this opt-in contract; no semantic-copy detector is claimed.

For a corrected source successor A2, explicitly map its old source revision A1
to the active local retaining predecessor that the new local candidate supersedes.
That correspondence replaces A1 with A2 on the mapped path; it does not require
retired A1 to become eligible again and is not a claim of independence. Preserve
A1 bytes and local ancestry in review/history. Unmapped retaining siblings and
unrelated origins remain obligations. An origin-only disposition cannot remove
an existing local retaining path.

## Store compatibility

Fresh registration and explicit empty-store recovery in version 2.3.0 create
schema 3. Existing complete schema 1/2 stores retain ordinary consultation without
implicit migration. Incorporation requires at least 2; transfer writes require 3.
`upgrade` explicitly changes a complete 1/2 store to 3 in one transaction,
preserving UUIDs and all six old event fields exactly. It refuses missing/empty
stores; repeat upgrade on 3 is idempotent. Old clients refuse unsupported 3.
Export of compatible existing history does not require implicit upgrade.

Historical receipt1/2 records before any transfer link retain their original
replay checks, including when old ancestry was not retained. Upgrade does not
rewrite those records. Current checks and receipt3 require complete ancestry.
If a new assertion after transfer links cannot establish ancestry from a legacy
receipt, reconsult to obtain receipt3 lineage, then retry with that receipt.
Existing historical use handles remain readable and can be checked with live inputs.

## Executable offline workflow

Run the following Bash blocks in one shell from the source checkout. They create
only disposable synthetic data. Automatic decisions here are exercise fixtures,
not permission to automate semantic judgment in real projects. Shell/Python file
writes below are explicit normal authoring outside consultation commands. Every
consultation call retains its arguments, stdout/stderr, exit code, output bytes,
and a before/after adopter byte-and-membership comparison.

```bash
set -e
export PYTHONPATH="$PWD"
transfer_demo=$(mktemp -d /tmp/transfer-workflow.XXXXXX)
export transfer_demo
mkdir "$transfer_demo/transcript"
cat > "$transfer_demo/invoke.py" <<'PY'
import hashlib, json, os, subprocess, sys, time
from pathlib import Path
root = Path(os.environ['transfer_demo'])
store, expected, *args = sys.argv[1:]
logs = root / 'transcript'
number = len(list(logs.glob('*.result.json'))) + 1
prefix = logs / str(number)
def adopter_state():
    result = {}
    for alias in ('a', 'a-offline', 'b'):
        directory = root / alias
        if directory.exists():
            result[alias] = {str(p.relative_to(directory)):
                hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else 'directory'
                for p in sorted(directory.rglob('*'))}
    return result
before = adopter_state()
command = [sys.executable, '-P', '-m', 'validated_memory', 'consultation',
           '--store', str(root / (store + '.sqlite')), *args]
started = time.monotonic()
result = subprocess.run(command, capture_output=True)
elapsed = time.monotonic() - started
after = adopter_state()
prefix.with_suffix('.stdout').write_bytes(result.stdout)
prefix.with_suffix('.stderr').write_bytes(result.stderr)
report = dict(command=command, expected=int(expected), exit=result.returncode,
              elapsed_seconds=elapsed,
              stdout_bytes=len(result.stdout), stderr_bytes=len(result.stderr),
              adopter_unchanged=before == after, before=before, after=after)
prefix.with_suffix('.result.json').write_text(json.dumps(report, indent=2) + '\n')
(root / 'last-output').write_text(str(prefix.with_suffix('.stdout')))
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
assert before == after, 'consultation changed adopter bytes/membership'
assert result.returncode == int(expected), report
PY
invoke() { python3 "$transfer_demo/invoke.py" "$@"; }
retain() {
  local variable="$1" store_name="$2" result_id
  shift 2
  invoke "$store_name" 0 "$@" || return
  result_id=$(python3 - "$transfer_demo/last-output" "$1" <<'PY'
import json, sys
from pathlib import Path
rows = [json.loads(line) for line in Path(Path(sys.argv[1]).read_text()).read_text().splitlines()]
operation = sys.argv[2]
assert all(row['schema_version'] == 1 and row['operation'] == operation for row in rows)
if operation in ('read', 'inspect', 'link-transfer', 'detach-transfer'):
    assert len(rows) == 2
    statuses = {'read': ('inspected', 'receipt recorded'),
                'inspect': ('historical material; not a current evidence check', 'inspection recorded'),
                'link-transfer': ('inspected', 'recorded'), 'detach-transfer': ('inspected', 'recorded')}
    assert tuple(row['status'] for row in rows) == statuses[operation]
else:
    assert len(rows) == 1
print(rows[-1]['id'])
PY
  ) || return
  printf -v "$variable" '%s' "$result_id"
}
actor=(--actor fixture-agent)
scope=(--scope exercise=transfer)
python3 - <<'PY'
import os
from pathlib import Path
root = Path(os.environ['transfer_demo'])
for alias in ('a', 'b'):
    for folder in ('knowledge', 'support'):
        (root / alias / folder).mkdir(parents=True)
    (root / alias / 'validated-memory.md').write_text('---\nid_prefix: kb\n---\n')
def unit(name, body, supersedes=''):
    (root / 'a/knowledge' / (name + '.md')).write_text(
        f'---\nid: {name}\nevidence: verifiable\n{supersedes}---\n{body}\n')
unit('kb-basis', 'The exercise counts sealed training boxes.')
unit('kb-a1', 'The earlier synthetic limit was ten boxes.')
(root / 'a/support/basis.txt').write_text('Fixture definition: sealed training boxes.\n')
(root / 'a/support/a1.txt').write_text('Earlier fixture limit: ten.\n')
(root / 'a/support/a2.txt').write_text('Revised fixture limit: twelve.\n')
PY
retain origin_registration a register source "$transfer_demo/a" --source policy
retain basis_binding a bind source:kb-basis --support support/basis.txt --authority policy \
  "${scope[@]}" "${actor[@]}" --reason 'Inspect the synthetic counting basis.'
retain predecessor_binding a bind source:kb-a1 --support support/a1.txt --authority policy \
  "${scope[@]}" "${actor[@]}" --reason 'Retain predecessor before its successor; no A1 receipt.'
printf '%s\n' '---' 'id: kb-a2' 'evidence: verifiable' 'supersedes:' '  - kb-a1' '---' \
  'The revised synthetic limit is twelve sealed training boxes.' > "$transfer_demo/a/knowledge/kb-a2.md"
retain successor_binding a bind source:kb-a2 --support support/a2.txt --reference source:kb-basis \
  --authority policy "${scope[@]}" "${actor[@]}" --reason 'Inspect the revised limit and its dependency.'
retain source_receipt a read source:kb-a2 "${scope[@]}"
mv "$transfer_demo/a" "$transfer_demo/a-offline"
invoke a 0 export-transfer "$source_receipt" --include-workspace-history --assess
invoke a 0 export-transfer "$source_receipt" --include-workspace-history > /dev/null
cp "$(cat "$transfer_demo/last-output")" "$transfer_demo/contribution.json"
retain destination_registration b register destination "$transfer_demo/b" --source planning
retain imported b import-transfer "$transfer_demo/contribution.json" "${actor[@]}" --reason 'Retain historical source material offline.'
invoke b 0 show-transfer "$imported"
```

Select only the receipt's bounded content from the retained import for inspection.
The selector below reads JSON and writes stdout only; it never extracts archive
paths or runs imported text. The next authoring block explicitly creates local
support and candidate files, adapting the predecessor disposition without creating
a foreign supersession edge. The CLI generates all mechanical hashes and handles.

```bash
invoke b 0 show "$imported" > /dev/null
python3 - "$(cat "$transfer_demo/last-output")" <<'PY' > "$transfer_demo/selected.json"
import json, sys
from pathlib import Path
capsule = json.loads(Path(sys.argv[1]).read_text())['artifact']['payload']['capsule']
receipt = next(event for event in capsule['events'] if event['id'] == capsule['receipt'])
content = receipt['payload']['content']
encoded = json.dumps(content, ensure_ascii=False).encode('utf-8')
assert len(encoded) + 1 <= 1048576, 'selected inspection exceeds 1 MiB'
sys.stdout.buffer.write(encoded + b'\n')
PY
cat "$transfer_demo/selected.json"
origin_project=$(python3 - "$transfer_demo/selected.json" <<'PY'
import json, sys
from pathlib import Path
content = json.loads(Path(sys.argv[1]).read_text())
print(next(unit['identity']['project'] for unit in content['units'] if unit['identity']['unit'] == 'kb-a2'))
PY
)
python3 - <<'PY'
import json, os
from pathlib import Path
root = Path(os.environ['transfer_demo'])
content = json.loads((root / 'selected.json').read_text())
for filename in ('basis', 'a2'):
    support = next(item for item in content['support'] if item['path'] == f'support/{filename}.txt')
    (root / 'b/support' / (filename + '.txt')).write_text(support['text'], encoding='utf-8', newline='')
(root / 'basis-candidate.md').write_text('---\nid: kb-local-basis\nevidence: verifiable\n---\nThis local exercise counts sealed training boxes.\n')
(root / 'plan-candidate.md').write_text('---\nid: kb-local-plan\nevidence: verifiable\n---\nThis local exercise admits twelve sealed training boxes.\n')
PY
retain basis_proposal b submit destination "$transfer_demo/basis-candidate.md" --path knowledge/kb-local-basis.md \
  --support support/basis.txt --authority planning "${scope[@]}" "${actor[@]}" --reason 'Propose the local counting basis.'
retain basis_link b link-transfer "$basis_proposal" --import "$imported" --origin "$origin_project:kb-basis" \
  "${actor[@]}" --reason 'Assert applicability of the inspected historical counting basis.'
retain basis_inspection b inspect "$basis_proposal"
retain basis_decision b decide "$basis_proposal" --inspection "$basis_inspection" --outcome accept \
  "${actor[@]}" --reason 'Accept the inspected local proposal after source linking.'
cp "$transfer_demo/basis-candidate.md" "$transfer_demo/b/knowledge/kb-local-basis.md"
retain local_basis_binding b bind destination:kb-local-basis --support support/basis.txt --authority planning \
  "${scope[@]}" "${actor[@]}" --reason 'Bind the explicitly authored local basis.'
retain basis_incorporation b incorporate "$basis_proposal" --decision "$basis_decision" \
  "${actor[@]}" --reason 'Observe the accepted local basis installation.'
retain plan_proposal b submit destination "$transfer_demo/plan-candidate.md" --path knowledge/kb-local-plan.md \
  --support support/a2.txt --reference destination:kb-local-basis --authority planning \
  "${scope[@]}" "${actor[@]}" --reason 'Propose the local plan with its incorporated dependency.'
invoke b 1 link-transfer "$plan_proposal" --import "$imported" --origin "$origin_project:kb-a2" \
  --origin-only-predecessor "$origin_project:kb-a1" "${actor[@]}" --reason 'Demonstrate refusal of the missing dependency mapping.'
retain plan_link b link-transfer "$plan_proposal" --import "$imported" --origin "$origin_project:kb-a2" \
  --dependency "$origin_project:kb-basis=destination:kb-local-basis" \
  --origin-only-predecessor "$origin_project:kb-a1" "${actor[@]}" --reason 'Retain A1 only as origin history; no installed local counterpart exists.'
retain plan_inspection b inspect "$plan_proposal"
retain plan_decision b decide "$plan_proposal" --inspection "$plan_inspection" --outcome accept \
  "${actor[@]}" --reason 'Accept the inspected adapted local plan.'
cp "$transfer_demo/plan-candidate.md" "$transfer_demo/b/knowledge/kb-local-plan.md"
retain local_plan_binding b bind destination:kb-local-plan --support support/a2.txt --reference destination:kb-local-basis \
  --authority planning "${scope[@]}" "${actor[@]}" --reason 'Bind the explicitly authored plan.'
retain plan_incorporation b incorporate "$plan_proposal" --decision "$plan_decision" \
  "${actor[@]}" --reason 'Observe exact accepted plan installation.'
retain local_receipt b read destination:kb-local-plan "${scope[@]}"
retain local_use b record-use destination:kb-local-plan --receipt "$local_receipt"
invoke b 0 check-use "$local_use"
```

A recorded origin correction can be exported without restoring the offline source
files. Importing the newer compatible history does not rewrite the old local use;
it blocks its current assertion and exposes the relevant review/recovery route.

```bash
printf 'Review whether the revised fixture limit applies to this exercise.\n' > "$transfer_demo/question.txt"
retain challenge a challenge source:kb-a2 --statement "$transfer_demo/question.txt" --kind factual \
  "${scope[@]}" "${actor[@]}" --reason 'Retain an explicit question about the selected origin revision.'
retain challenge_inspection a inspect "$challenge"
retain challenge_decision a decide "$challenge" --inspection "$challenge_inspection" --outcome accept \
  "${actor[@]}" --reason 'Accept the request for review, not a claim of falsity.'
invoke a 0 export-transfer "$source_receipt" --include-workspace-history > /dev/null
cp "$(cat "$transfer_demo/last-output")" "$transfer_demo/corrected-history.json"
retain updated_import b import-transfer "$transfer_demo/corrected-history.json" "${actor[@]}" --reason 'Retain newer recorded origin review history.'
invoke b 1 check-use "$local_use"
invoke b 0 show-transfer "$updated_import"
invoke b 0 show "$local_use"
python3 - <<'PY'
import json, os
from pathlib import Path
root = Path(os.environ['transfer_demo'])
reports = [json.loads(p.read_text()) for p in (root / 'transcript').glob('*.result.json')]
summary = dict(commands=len(reports), human_interventions=0,
               decisions='predetermined synthetic assertions, not operational approvals',
               expected_refusals=sum(r['expected'] == 1 for r in reports),
               cli_elapsed_seconds=sum(r['elapsed_seconds'] for r in reports),
               stdout_bytes=sum(r['stdout_bytes'] for r in reports),
               stderr_bytes=sum(r['stderr_bytes'] for r in reports),
               adopter_unchanged=all(r['adopter_unchanged'] for r in reports))
(root / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary, indent=2))
print(root)
PY
```

This synthetic walkthrough demonstrates mechanical workflow and refusal behavior.
It does not establish empirical usefulness, semantic equivalence or cost savings.
No operational corpus was transferred by running it.
