# Adaptive memory: evidence and remaining limits

> **Status:** Results from an unreleased candidate. Release validation and
> publication are tracked separately.

The adaptive workflow adds optional prompt discovery, requested learning,
selective review, and trusted task handoff to the existing two-layer memory and
checked-consultation system. Settings control interaction effort. They do not
change the strength of evidence or make an uncertain claim true.

| Capability | Observed or documented coverage | Remaining limit |
| --- | --- | --- |
| Discovery | Claude Code `UserPromptSubmit` can return bounded lexical candidates in [automatic or explicit mode](../reference/agent-integration.md). | Discovery does not translate concepts, infer task scope, or ensure that a model reads or applies a candidate correctly. |
| Learning | [Requested learning and selective review](../reference/learning.md) retain observations, tentative interpretations, review outcomes, and same-layer corrections. Ordinary curated authoring retains its native provenance. | Authoring remains agent file work with host permission and setup costs. It is not an atomic canonical writer or transcript collector. |
| Reviewed use | Opt-in [checked consultation](../reference/consultation.md) requires complete content, exact scope, current inputs, and retained correction history. | It does not gate arbitrary responses, edits, or publication. Evidence checks do not establish understanding. |
| Resumption | An explicit [trusted task handoff](../reference/task-lifecycle.md), together with existing update reports, can preserve pending work and selected history. | The CLI does not automatically transport task state through host compaction or into subagents. Identical receipt scope alone cannot distinguish prose tasks. |
| Recovery | [Recovery procedures](../reference/recovery.md) cover a single-project whole backup and restore. Separately staged multi-root migration is also exercised through CLI subprocesses. | Simultaneous loss of several registered root identities is unsupported. A clone or transfer capsule alone does not reconstruct a usable whole workspace. |

## What the observations show

A formative pilot used three fixed questions in an existing adopter. Candidate
delivery and subsequent inspection of original sources worked, but one answer
confused two materially different file-publication primitives. Another query
missed two expected knowledge units across languages; ordinary source inspection
later found them. The adopter's canonical baseline remained unchanged.

These results keep four claims separate: discovery, acquisition, admission to an
action, and correct application. A lexical miss does not prove absence. When a
search misses, retry with qualified IDs or terminology found in the sources,
read the complete evidence, and verify implementation primitives before treating
them as equivalent.

The pilot was not a controlled comparison. Investigations avoided, model bypass
rates, total provider context, and long-project savings are unknown. Observed
elapsed time includes model and tool work and does not isolate retrieval cost.
Three questions do not justify a new ranking or translation policy.

Three synthetic Claude authoring stages exercised capture, selective review with
a retained successor, and ordinary curated authoring:

| Stage | Model turns | Elapsed time | Permission denials |
| --- | ---: | ---: | ---: |
| Capture | 10 | 28.421 s | 4 |
| Selective review | 15 | 56.294 s | 1 |
| Curated authoring | 17 | 46.137 s | 1 |

An earlier blocked write attempt also recorded setup friction. The workflow
preserved the original memory body and project profile and did not invent
executed experiments. These are bounded authoring observations, not a cost
comparison or universal success rate.

A separate fresh-context synthetic run exercised an explicitly supplied handoff.
It followed an old locator to its successor and source, reported a
condition-specific conclusion, and kept an unaccepted capture proposal pending.
The run took 6 model turns and 14.408 seconds, with no permission denials. This
demonstrates the documented handoff workflow; it does not demonstrate automatic
host compaction or subagent propagation.

## Optimization results

The selected optimization removes duplicate validation only when a capture's
canonical documents are unchanged. In runs using the same synthetic 100-unit
checkpoint setup, the observed call counts changed as follows:

| Profile observation | Before | After |
| --- | ---: | ---: |
| `inputs.capture` calls | 2 | 2 |
| `validate_documents` calls | 4 | 2 |
| `effective_states` calls | 4 | 2 |
| Total profiled time | 0.088041781 s | 0.083535463 s |
| Cumulative time in `inputs.capture` | 0.036711362 s | 0.028011612 s |

Each column is one instrumented run. The times are descriptive observations,
not statistically measured latency or savings, and they do not support a
real-project performance claim.

A separate synthetic two-project prospective-insertion profile observed two
captures, six validations, and six state computations. For each capture, the
insertion target was validated and computed twice while the unchanged project
was validated and computed once. This retained the prospective-insertion guard.

The optimization also retains canonical pre-insertion checks, state recomputation
for the insertion target, final file verification, and independent recapture. It
adds no persistent cache, graph, dependency, or whole-codebase rewrite.

The unreleased transfer guard rejects null, list, scalar, and nested non-object
events before object payload access. Its diagnostic identifies only the numeric
event index and does not expose the malformed value. Focused subprocess tests
also preserve digest priority, full-history refusal for malformed object events,
and the exact bytes of existing stores and adopter files after refusal. The
complete transfer test module passed.

Focused prospective-insertion tests likewise preserved the exact store and
adopter bytes when ordinary canonical input or the prospective insertion was
invalid. These checks support refusal atomicity for the exercised cases; they are
not a claim that the full repository suite has completed for this candidate.

## Delivery and remaining work

The earlier eleven-project evaluation remains a bounded historical comparison.
This work does not establish universal superiority over those tools. Its value
must be assessed in projects where provenance, retained corrections, and scoped
reuse matter.

Whole-workspace disaster recovery across several simultaneously lost root
identities needs a separate batch relocation design. Current sequential
relocation requires every other original root identity to remain available.
Preserve backups and report this limit rather than editing registrations or
treating copies as identity-equivalent.

Further adoption work should measure ordinary maintenance effort, incorrect
reliance, and uncertain savings rather than candidate counts alone. Automatic
lifecycle integration needs a separately verified host path. A hard deliverable
gate needs control of the actual delivery boundary, including alternate paths.
General uninstall and reversal, and the historical journal roadmap, remain
separate work.
