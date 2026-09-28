# Bounded consultation comparison

## Decision

P4 completed on 2026-09-09. The preregistered benefit gate was **not met**:
both arms answered all eight tasks correctly, with material caveats preserved
and no unsafe reliance, but no paired task demonstrated an improvement or
avoided a preregistered redundant investigation. This is absence of demonstrated
benefit, not evidence that retrieval is useless.

The architect accepts `recall` as a tested discovery capability and retains
`consult-project-memory` as an **opt-in** procedure. Broad recommendation of
consultation before every substantial task is deferred. The final managed
adoption block points to the optional capability; it does not impose the
candidate's unconditional consultation-first rule. No mandatory gate or startup
injection is added. Product correctness and usefulness are separate decisions.

## Method and frozen evidence

Eight synthetic case families, one fresh session per arm and case, sixteen
completed sessions total. Baseline: shipped plugin at
`d5d0dbb0fa8802c34f210421c19e98b67fef72bc`, ordinary file search and native
memory index. Candidate: the technically accepted runtime, a consultation-first
managed block and the full consultation skill in its prompt. Both arms received
the same source tree, native index, question, source-citation instruction and
180-word answer request. This compares the complete workflow, not retrieval
in isolation; the candidate's extra instructions count toward its input.

An independent Claude author prepared source facts and curated representations;
an independent reviewer corrected invalid representations and unsupported
requirements before any solver ran. The architect accepted those corrections
and removed an unasked backoff factor from required answer propositions before
freezing. No scoring threshold, question, or source was changed after answers
existed. Expected optional details were not required for a supported answer.

Actual solver model: `claude-sonnet-5`, medium effort, Claude subscription.
Three sessions maximum in parallel, alternating arm order, fresh nonpersistent
sessions, five-minute and twelve-observed-tool-call ceilings, no retries or
model fallback. No API credentials, paid-extra fallback, Fable, external
services, solver delegation, or solver fixture writes. All sixteen sessions
completed within the ceilings; every fixture tree remained byte-identical.
The older closed inconclusive pilot was not reopened.

The fixed gate required safety parity, at least baseline supported outcomes,
and at least two distinct pairs with improved supported outcomes or avoided
preregistered redundant investigation. Ordinary reading and verification never
counted as waste. An independent Claude Opus 5 evaluator scored answers against
the frozen gold and inspected tool traces; the architect inspected the answers
and accepted the scoring. The baseline's immediately self-corrected end-time
typo in the resumption answer was not turned into a candidate improvement.

Durable evidence:

- [Protocol and input hashes](usefulness-protocol.json).
- [Synthetic corpus](usefulness-corpus.json) and [frozen source-derived gold](usefulness-gold.json).
- [Answers and observed run measurements](usefulness-observations.json).
- [Independent scoring and paired gate](usefulness-evaluation.json).

Raw session streams and prompt files remain clone-local evaluation state, not
plugin runtime or adopter content. The public evaluation corrects only a prose
typo saying "two" while listing three caveat-bearing cases; scores are unchanged.

## Results

| Observation | Baseline | Candidate |
| --- | ---: | ---: |
| Completed / attempted sessions | 8 / 8 | 8 / 8 |
| Supported outcomes | 8 / 8 | 8 / 8 |
| Material caveats preserved | 8 / 8 | 8 / 8 |
| Unsafe reliance | 0 | 0 |
| Sessions invoking `recall` | 0 | 6 |
| Tool calls | 22 | 24 |
| Sum of per-session elapsed seconds | 89.393 | 87.450 |
| Controlled UTF-8 input bytes | 24,872 | 68,088 |

Every pair tied: ordinary reuse, rejected alternative, historical successor,
applicability exception, stale freshness, source-only draft, cross-language
lookup, and session resumption. Candidate sessions for the exception and
resumption read files directly without invoking `recall`. Instructions did not
guarantee tool use. Reading original memory/knowledge documents and opening their
underlying `docs/` provenance are separate actions; the evaluation's
`opened_original` field counts the latter, not compliance with every source-read
step in the skill.

Controlled bytes are the initial prompt plus returned tool-result text, not
tokens, cumulative provider input, or complete host context. The candidate's
larger prompt dominates its approximately 2.74-times byte total. Summed session
durations are not batch wall time, and a small single-run timing difference is
not a speedup claim. Host inputs and billed amounts remain unknown. Curation
and reviewer effort was not measured and must not be reported as zero.

## Limits and next decision

The tiny, clean synthetic corpora produced a ceiling effect: ordinary search
already solved everything. Seven fixtures cannot observe repeated investigation
at all. The remaining fixture registered only repetition of the identical
failed Spanish query; the candidate queried in English immediately, so lexical
recovery was not exercised and no avoided investigation was observed. This
does not establish behavior in multilingual searches or long, complex projects.
One observation per arm/case permits no statistical inference or productivity,
cost, context-saving, or maintenance-return claim.

A future proposal should use new, source-grounded tasks that actually require
recovering prior decisions amid substantial distractors or avoiding observable
repeat investigation. It should measure curation/upkeep as well as solver work.
That is a separate scoped decision, not permission to tune and rerun this set,
reopen the earlier pilot, add paid semantic services, or introduce an enforced
gate. Meanwhile, users can opt into consultation for their projects and judge
its value without treating stored claims as higher authority than current facts.
