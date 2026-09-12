# Requested learning and selective review

Validated-memory can retain a lesson the user requests and review one selected
record without adding a store, schema or runtime writer. Agents author the same
Markdown files and indexes used by ordinary memory and knowledge workflows, then
run the existing validators.

Prompt discovery does not authorize capture. A useful answer, repeated wording
or an automatic match is never an instruction to save the conversation.

Use the [agent-memory contract](agent-memory.md) with the
[maintain-agent-memory skill](../../skills/maintain-agent-memory/SKILL.md) for
experience, or the [curated-knowledge contract](curated-knowledge.md) with the
[create-knowledge-unit skill](../../skills/create-knowledge-unit/SKILL.md) for an
evidence-bearing claim. [Recall](recall.md) provides read-only discovery before
complete-source inspection.

## Route the request

| User intent | Artifact and workflow |
| --- | --- |
| Remember an experience, preference, project fact or reference note | An indexed `memory/` entry through `maintain-agent-memory` |
| Retain a claim whose support or freshness must be explicit | A `knowledge/` unit through `create-knowledge-unit`, using the evidence state supported now |
| Review one existing record without changing its meaning | An indexed `feedback` memory entry |
| Change meaning or retire current advice | A successor in the original layer; include the assessment in the successor |

An explicit request to retain a lesson authorizes that bounded write. Show the
exact proposed artifact and complete the ordinary authoring checks without asking
for another generic confirmation. If an agent independently sees a potentially
useful lesson at task completion, it may offer a concise proposal; it writes only
after the user accepts it.

Ordinary capture and review require no consultation workspace, incorporation,
transfer, store upgrade or profile change. Existing checked consultation remains
available when the project has separately enrolled and the task calls for it.

## Distill before writing

Keep these ideas distinguishable in short body prose rather than inventing new
frontmatter:

- the observation and who or what reported it;
- a tentative interpretation, clearly marked as tentative;
- the conditions where it occurred;
- sources actually inspected and checks actually executed; and
- the next check that could resolve uncertainty.

Name missing support as unavailable. Generated analysis does not independently
corroborate a reported event. `evidence: hypothesis` is a valid retained result;
do not raise the evidence state because the explanation sounds plausible.

Before writing, inspect the applicable project instructions, the current index,
the proposed identity and any supersession family. Refuse an overwrite or reused
identity. Validate the complete layer after the file and index agree. Direct
Markdown authoring is not an atomic transaction and creates no journal guarantee.

## Review one selected record

Fix the selected layer, canonical identity, repository-relative locator, claim
and intended scope before assessing it. Read the complete original, not a recall
excerpt, plus the evidence needed for that scope. Distinguish discovery
candidates, originals inspected, implementation sources inspected and checks
actually run.

Report one scoped conclusion:

| Outcome | Meaning | Retention |
| --- | --- | --- |
| `supported` | Inspected evidence supports the claim in the stated scope | Indexed feedback, unless an existing enrolled support review already records it |
| `qualified` | The claim holds only under named conditions | Indexed feedback when meaning is unchanged; same-layer successor when canonical meaning changes |
| `unresolved` | Available evidence does not discriminate | Indexed feedback naming the missing check; no evidence promotion |
| `contradicted` | Evidence rejects the interpretation in scope | Indexed feedback if a conditional observation remains valid; same-layer successor if current advice must retire |

These are descriptions, not new evidence-state values or truth labels. A review
of one item changes neither the agent profile nor unrelated records. Lightweight
reliance can still review one item; reviewed reliance does not impose a global
gate or automatically create an assessment.

Do not manufacture a conflict between different conditions. A failure under
condition A and success under condition B can coexist as separate indexed
observations. Retain a qualified assessment explaining the boundary and leave the
original condition-A bytes unchanged. Create a successor only when the original
meaning or current advice truly changes.

For a memory successor, keep the old index link and annotate it as superseded;
add the successor's own link. Recall redirects normal matches to the successor,
while `recall --include-superseded` exposes the earlier record. Knowledge
successors use the existing `supersede-knowledge` workflow. Put the assessment,
evidence and limits in the successor body; a duplicate feedback entry is optional
when it would preserve distinct history.

Cross-layer provenance is attribution, not supersession. Write the layer,
canonical identity and ordinary repository-relative path, for example memory
identity `failed-retry` at `memory/failed-retry.md`. Do not use a cross-layer
wikilink, insert a memory identity into knowledge `supersedes`, or imply that
copying the observation independently corroborates it.

## Resume interrupted authoring

Inspect the actual file and index before acting. If a complete file was written
but its index bullet was not, add the bullet only when the intended bytes and
authorized scope are still retained, then validate the whole layer. Do not ask
again for authorization that remains recorded.

If the body is truncated, unknown or differs from the retained intended bytes,
stop and obtain the actual intended material. A clean lint result cannot prove
that reconstructed prose matches the request. Never delete prior history to make
an interrupted write look complete.

## Executable synthetic example

This example retains a failed attempt under condition A, a later success under B,
and a non-retiring qualified review. It also creates a tentative curated claim
with ordinary cross-layer provenance. Run it from a validated-memory source
checkout; all writes stay in a printed temporary adopter.

```bash
plugin_root="$(pwd -P)"
demo_root="$(mktemp -d)"
cd "$demo_root"
PYTHONPATH="$plugin_root" python3 -P -m validated_memory init

cat > memory/failed-retry.md <<'EOF'
---
name: failed-retry
description: Retry failed under condition A
metadata:
  type: feedback
---

The operator reported that one retry failed under condition A. The cause is
tentative; no diagnostic trace was available. Next check: capture the retry
status and timeout source under the same condition.
EOF

cat > memory/retry-success-condition-b.md <<'EOF'
---
name: retry-success-condition-b
description: Retry succeeded under condition B
metadata:
  type: feedback
---

The operator later reported success under condition B. This does not contradict
the condition-A observation because the conditions differ.
EOF

cat > memory/review-failed-retry.md <<'EOF'
---
name: review-failed-retry
description: Qualified review of the condition-A retry observation
metadata:
  type: feedback
---

Reviewed layer: memory. Identity: failed-retry. Locator:
memory/failed-retry.md. Outcome: qualified. The failure remains an attributed
observation under condition A; success under B does not retire it. The cause is
unresolved until a condition-A diagnostic trace is available.
EOF

cat > memory/retry-advice.md <<'EOF'
---
name: retry-advice
description: superseded by [[retry-advice-scoped]]
metadata:
  type: reference
---

Earlier broad advice said that retries always succeed regardless of condition.
EOF

cat > memory/retry-advice-scoped.md <<'EOF'
---
name: retry-advice-scoped
description: Retry advice scoped by operating condition
metadata:
  type: reference
---

Assessment outcome: contradicted in the general scope. The condition-A failure
and condition-B success show that the earlier unconditional advice must retire.
Evidence is limited to the two attributed reports; the cause remains unresolved.
EOF

cat > memory/MEMORY.md <<'EOF'
# Agent memory

- [Failed retry under condition A](failed-retry.md) — attributed observation
- [Retry success under condition B](retry-success-condition-b.md) — separate condition
- [Review of failed retry](review-failed-retry.md) — qualified, cause unresolved
- [Retry advice (superseded by scoped advice)](retry-advice.md) — superseded by retry-advice-scoped
- [Scoped retry advice](retry-advice-scoped.md) — assessment retained in successor
EOF

cat > knowledge/kb-retry-scoped.md <<'EOF'
---
id: kb-retry-scoped
evidence: hypothesis
provenance:
  - memory/failed-retry.md
---

# Retry advice may depend on operating condition

The retained reports suggest that retry behavior depends on conditions A and B.
The cause has not been checked. Memory provenance: identity `failed-retry` at
`memory/failed-retry.md`. Next check: compare diagnostic traces under both
conditions.
EOF

PYTHONPATH="$plugin_root" python3 -P -m validated_memory lint
PYTHONPATH="$plugin_root" python3 -P -m validated_memory validate
PYTHONPATH="$plugin_root" python3 -P -m validated_memory recall "retry condition A"
PYTHONPATH="$plugin_root" python3 -P -m validated_memory recall "retry advice" --include-superseded
printf 'Synthetic adopter: %s\n' "$demo_root"
```

`lint` and `validate` exit 0; validation warns that the tentative unit has no
freshness anchors. The first recall keeps the condition-A failure and condition-B
success separate. The history recall shows both the retired broad advice and its
same-layer successor. Recall returns discovery candidates, after which a consumer
still reads the complete originals. The example creates no checked-use record and
leaves any `validated-memory-profile.md` unchanged.

To rehearse interruption safely, create another complete memory file while
retaining its expected bytes and requested scope outside the adopter, stop before
adding its index bullet, then resume by comparing the actual file to those bytes.
Add the missing bullet and run `lint`. A partial or mismatched file must remain
unreported as complete until the intended content is available.
