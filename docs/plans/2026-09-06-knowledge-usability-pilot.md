# Knowledge usability pilot

Status: reviewed experiment plan, 2026-09-06. Baseline: `70364fb`, version
1.6.0. Work on `feature/knowledge-usability-pilot` in an isolated worktree.
This plan authorizes bounded experiments, not changes to public contracts.

## Outcome and hypotheses

An agent studying a large codebase must recover a useful conclusion, preserve
its business conditions, inspect its evidence, and recognize when it needs
rechecking without loading an ever-growing research archive.

Test four hypotheses rather than committing to a new storage architecture:

1. Explicit admission tied to exact claim/evidence revisions prevents an
   edited or unapproved conclusion from inheriting accepted status.
2. Task-scoped selection improves supported answers or lowers context cost
   without hiding decisive exceptions or encouraging unsupported answers.
3. Relevant source fingerprints and explicit premise dependencies identify
   useful revalidation work with less noise than repository-wide changes.
4. Upkeep per observed transition does not exceed the better baseline. Long-term
   savings from avoided investigation require a later longitudinal evaluation.

Structural validity, declared evidence, acceptance, applicability and freshness
remain distinct. `current` describes an anchor check, not proof of prose.
Use the language in [CONTEXT](../../CONTEXT.md); experimental vocabulary is
provisional. Existing age gates and supersession semantics remain available.

## What to transfer from a reference adopter

Extract mechanisms and generic counterexamples from an existing alpha adopter:
typed evidence, source-to-scenario-to-execution traceability, human decision
versus factual testimony, relevant-content fingerprints, supersession,
retained refutations and explicit inability to check. Keep the original
adopter read-only. Private source mappings and research remain in ignored
`sessions/pilot/`; public fixtures must be synthetic and self-contained.

Do not import its coordination machinery, accumulated instructions, or entire
corpus. A pattern is eligible only when a reproducible case explains its value.

## Phases and exit evidence

| Phase | Bounded delivery | Exit evidence |
|---|---|---|
| P0: freeze the experiment | Case manifest, ground truth, baselines, cost limits, metrics and split | Independent review confirms answerability, evidence scope and no answer leakage |
| P1: characterize current behavior | Run shipped CLI on scratch fixtures; replay tasks with ordinary search and current method | Raw outputs, revisions and failures retained; separate retrieval failures from answer failures |
| P2: smallest experimental loop | Admission sidecar and compact selection over the same fixture evidence | Black-box checks reject altered claims and excluded premises; budgets and caveat preservation checked |
| P3: real agent usefulness | Paired trials, held-out evaluation, then a separately gated observational adopter pilot | Quantitative trial evidence; adopter observations reported separately |
| P4: architecture decision | Continue, simplify, integrate an external module, or stop | Written ruling cites results and costs; only accepted behavior becomes an issue/ADR and product work |

The first execution packet is P0 only. Do not start all phases at once.
P0/P1 findings can change or cancel P2. No full corpus migration is required.
P0 must deliver a manifest mapping family, split, independent mechanism/source
lineage, task, arm, repeat, cap and presentation; adjudication rules; and a
fabricated mixed-result table that produces one unambiguous gate decision.

## Initial case families

Cover all 12 families in deterministic checks. The 12 model tasks (six per
split) may combine families; P0 maps coverage and explains any family tested
only deterministically. Unseen instances use different mechanisms/source
lineages. The evaluator owns unseen answers outside the agent-visible fixture
snapshot; the implementation agent does not inspect them. Any exposure must
be reported and the case retired from held-out evaluation.

| Case | Discriminating scenario |
|---|---|
| C01 | A business question requires a code path plus a configuration condition |
| C02 | Capability demonstrated in development cannot establish production occurrence |
| C03 | A passing database query examined the wrong population; an independent control exposes it |
| C04 | A human changes policy while the code remains unchanged |
| C05 | An unrelated commit changes the ref but not the relevant content |
| C06 | A relevant dependency changes while the directly cited file does not |
| C07 | An accepted claim is edited without a new acceptance decision |
| C08 | Several reviewers repeat one source; apparent corroboration is not independent |
| C09 | A superseded conclusion remains in a historical index or cached answer |
| C10 | Two conclusions differ legitimately by environment or applicability period |
| C11 | Evidence is missing, unavailable or outside studied coverage; the correct response is qualified or abstains |
| C12 | The shortest answer omits the exception that changes the decision |

Include supported positive controls, retained negative findings, late evidence,
and a source passage containing instructions that must remain evidence rather
than become authority. Do not make every expected answer an abstention.
Fixtures must include known source outputs and a separate adjudicated answer;
a passing script alone cannot constitute ground truth for a business question.
Author gold propositions/caveats from raw sources before creating knowledge
units. Score source-supported meaning across all arms, not candidate IDs. Map
IDs to source passages only for diagnostic retrieval metrics. Include two tasks
whose decisive evidence has not been promoted into a knowledge unit.

Every hard gate needs a nonzero held-out denominator in the P0 family-to-gate
map; uncovered gates block continuation. C06 must include both declared and
undeclared dependencies. The admission preparer owns dependency declarations;
count that work. Report recall separately for both strata and unsafe reliance
on a missed undeclared dependency, rather than declaring it out of scope.

## Fair comparison and measurement

Three paired arms: A, ordinary native memory plus source search; B, shipped
validated-memory with its documented skills; C, the experimental selection
policy. Give each the same underlying evidence, accepted conclusions and task
scope, without exposing adjudicated answers. Record any
human curation unavailable to an arm and charge its time rather than treating
it as free. Freeze model/version, tool access, prompts, temperature where
available, limits, source revisions, effective task time and cache treatment.

First test deterministic behavior without model calls. Then pre-register a
batch: six development tasks x three arms x one run (18), then six independently
authored held-out tasks x three arms x two runs (36). Split by business mechanism
and source lineage, not paraphrase. The ceiling is 54 answer runs plus nine
maintenance exercises (three transitions across three arms), with at most one
prototype revision before held-out evaluation. Repeats reveal instability, not
independent task samples. Rotate arm order; fresh sessions prevent cross-arm memory.

Proposed ceilings: five minutes and 12 tool calls per run, 128 KiB cumulative
controllable task input, and a total currency ceiling fixed in P0 before paid
execution. Count startup, instructions, schemas and all evidence expansions;
report host overhead separately when not controllable. Stop at a limit; retain
timeouts, infrastructure failures and truncations in the scheduled denominators.
Record model usage/cost as unknown if unavailable. All model trials use one
frozen presentation and a 12 KiB experimental packet cap. The 4/12/32 KiB
sweeps and passage/pointer comparison are deterministic checks or human
inspection before model trials, not additional answer-run conditions. The
manifest must total exactly 54 scheduled answer runs and nine transitions.
Replace any held-out case
exposed during tuning before another evaluation; never silently retune against it.

Measure these separately; avoid a composite score that hides unsafe answers:

| Measure | Definition |
|---|---|
| Admission precision/recall | Correct accepted revisions / all accepted; correct accepted / gold-admissible revisions |
| Eligible retrieval precision/recall | Relevant eligible conclusions returned / all returned as usable; gold-required eligible conclusions returned / all gold-required |
| Supported answer rate | Fully supported, correctly scoped answers / answerable task runs |
| Appropriate abstention | Correct abstentions / unanswerable task runs; report false abstentions separately |
| Unsafe premise use | Answers relying on unapproved, superseded, inapplicable or insufficiently fresh premises / relevant challenge runs |
| Evidence access | Correct direct evidence destinations / citations, plus navigation/tool reads until evidence is inspected |
| Caveat retention | Gold-required conditions preserved / gold-required conditions across scheduled applicable runs; report justified abstentions separately |
| Context cost | All startup instructions, indexes, snippets, expansions and tool output delivered to the agent; report final-answer length separately |
| Maintenance cost | Human minutes and machine work for admission, dependency declaration, correction and revalidation per transition; setup/curation separately |
| Change detection | Relevant changes detected / gold-relevant changes, stratified by declared/undeclared dependency; unrelated-change alerts separately |

Zero denominators are N/A, never 100%. Record blinded scoring disagreements.
Count UTF-8 bytes exactly. Token counts require a recorded model tokenizer or
provider accounting; never label bytes/characters divided by a constant as
measured tokens. Prototype output caps initially use 4/12/32 KiB of UTF-8,
including envelopes and omissions. Freeze UTF-8 bytes of controllable delivered
input as the primary cost measure; measured model tokens are a sensitivity check.
Disagreement in direction blocks a token-savings claim, not a byte measurement. Missing accounting is an explicit measurement gap.
Distinguish cumulative delivered context, peak context and billed token usage.

## Experimental interface and implementation limits

Prototype one small interface: given a task, explicit applicability, effective
time and output budget, return qualified conclusions with direct evidence
access or a specific inability to answer. This is not a new public CLI design.

Each returned conclusion needs its stable ID/revision, concise meaning,
decision-changing conditions, acceptance/evidence distinction, freshness date
and direct evidence location. Explain coverage gaps and omitted candidates
without dumping their bodies. If a safe qualification cannot fit, return a
bounded insufficiency response. Expansions consume the same task budget;
historical retrieval is explicit and cannot promote old claims into current use.
Decisive evidence should require one direct read; reproduction instructions at
most one further read. A citation without inspected evidence is not useful reuse.
Inspect complete qualified passages and pointer-only results on development
fixtures before freezing the single model-trial presentation. Give startup/task documents owners and review triggers; archive completed
working history and test navigation instead of injecting it each session.

Keep Markdown as the source and indexes rebuildable. A local sidecar may hold
experimental acceptance and dependency metadata tied to content digests; it
must not silently become a second editable claim store. Missing or ambiguous
applicability/acceptance must not be guessed into eligibility. Do not enforce
these experimental rules by changing the v1 validator.

Use stdlib-only scratch tooling outside `validated_memory/`. Exercise the
current CLI as a subprocess; do not import its internals into tests. Begin
with file/subtree evidence and synthetic script/SQLite observations, with
declared parameters, expected result, observed result and negative control.
No live database access, production probes or changes to adopter memory are
needed for P0-P2. Existing external tools may later supply evidence through
an adapter; no new graph/vector infrastructure until retrieval results justify it.

## Continue, revise or stop

Freeze these provisional thresholds in P0 before results are seen: candidate C
should match or exceed the better baseline's supported held-out success and
either solve two additional held-out tasks or reduce paired median total context
input by at least 20%, with no greater median upkeep per transition. Report task
counts, dispersion, worst regressions and the predeclared byte measure alongside
actual tokens when available. Two extra successes among six tasks are exploratory,
not statistical superiority. Passing permits broader evaluation only; product
promotion additionally requires real-task validation and public-contract review.
For the frozen comparison, a task succeeds only when both repetitions meet
the gold rubric (including justified abstention for an unanswerable task).
Select the baseline with more successful tasks; break a tie by lower median
context cost, then by simpler arm A. Compare costs against that same baseline:
within each task take the median across repetitions, form C/baseline ratios,
then take their median across tasks. A cost-saving claim requires measured
complete costs and success for every included task in both arms; report excluded
tasks and require at least four paired tasks. Early failure/timeout cannot count
as cheap success. Include its consumed cost separately; mark total unknown if
accounting is incomplete. Maintenance uses the same transitions across arms.
The following hard checks gate candidate C; baseline failures remain measurements:
no output budget overflow; no promotion of forbidden premises; no loss of a
decision-changing caveat; no resurrection through derived copies. Zero observed
failures is a gate for the tested sample, not a universal safety guarantee.
Historical or hostile material may be inspected as evidence; representing or
using it as an eligible premise is the failure, not its mere presence in a read.

Continue only if held-out evidence shows a useful quality/cost tradeoff against
both A and B, with acceptable human upkeep and no unresolved hard-check failure.
Revise the selector if knowledge is correct but missed or too expensive to find;
revise evidence/admission if unsupported conclusions pass; reduce scope if
observed upkeep exceeds the comparator. The initial pilot does not establish
amortized savings or a break-even point for repeated investigation. Consider an external retrieval
module only when retrieval is the measured limitation. Stop the expansion if
the best simpler baseline remains as useful at lower total cost.

Increase to 30-50 real tasks across three business processes only after the
small pilot passes. Synthetic scale sweeps measure latency and context growth
at larger corpus sizes, not semantic accuracy. Do not generalize from a tiny
sample to arbitrary codebases or from one harness to all LLMs. An adopter pilot
starts only after the quantitative gate passes and contributes observational
usability evidence, not additional scored successes. Any post-held-out revision
requires a newly authored unseen set before renewed quantitative acceptance.

## Ownership and integration gates

The architect owns hypotheses, scope, rulings and final acceptance. The
engineer owns evaluation fixtures/held-out answers; the programmer owns the
bounded experimental implementation; user care assesses comprehensibility and
evidence access; the reviewer checks contracts and discriminating assertions.
Every writable path has one owner. Read-only work may run in parallel within
the tool limit; no future phase agents are launched early.

Use at most three reviewer/programmer correction rounds, then terminal engineer
assessment. If the engineer edits, the architect independently accepts the diff.
Run `python3 -m pytest` before handoff; additional evaluation does not replace
the public suite. Reports state what actually ran and preserve failed trials.

Before changing the base schema, exit codes or finding shape, open the issue
required by [CONTRIBUTING](../../CONTRIBUTING.md). Consequential domain changes
need an ADR before product implementation. Admission authority, dependency
semantics, budget enforcement and cross-harness interfaces remain open until
the experiment informs that decision. No release, merge or publication is
part of this plan's preparation.
