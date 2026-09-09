# Memory consultation and task retrieval

Status: completed and architect-accepted, 2026-09-09. P0–P5 are complete. The
[frozen specification](memory-reuse/specification.md) defines the implemented
interface. P4's [bounded comparison](memory-reuse/usefulness-report.md) did not
meet its benefit gate: retain retrieval and opt-in consultation, defer broad
consultation-first recommendation. Work branch: `feature/memory-reuse-plan`.
The original proposal below is retained as planning history, not an additional
promise; the frozen specification, final disposition and public reference govern.

## Execution disposition

- P0: architect specification and ADR accepted; issue opened before product edits.
- P1: read-only bounded retrieval and CLI subprocess tests implemented.
- P2: consultation skill and confirmed block-only upgrade implemented; after P4,
  the final instruction block advertises opt-in use instead of a default mandate.
- P3: product review completed in two rounds, then terminal engineering resolved
  remaining findings. The architect independently reviewed the engineer's edits
  and ran the full suite: 781 passed before final disposition documentation.
  [Scale and ceiling evidence](memory-reuse/scale-report.md) records bounded
  failure and the 100/1,000/10,000-document observations.
- P4: sixteen fresh Claude Sonnet 5 sessions completed; independent Opus scoring
  found 8/8 supported outcomes per arm, no safety loss and zero improved pairs.
  No default consultation-first recommendation or savings claim is accepted.
- P5: final documentation, compatibility and [delivery preparation](memory-reuse/delivery.md)
  completed. Architect full suite: 782 passed in 85.85 seconds. No commit,
  push, version change, tag or release was performed.

## Outcome and baseline

Make relevant memory entries and knowledge units easy to discover and consult
before substantial investigation, design and changes. Consultation has priority;
stored claims do not override present evidence or user instructions. Success
means better supported decisions and fewer unnecessary repeated investigations,
not merely more file reads or citations.

Current implementation provides:

- A complete one-line-per-entry `memory/MEMORY.md`, with links and descriptions.
- A derived knowledge index with id, state, evidence and verdict, without topic
  summaries or file links (`validated_memory/derive.py`).
- Session status and source counts, not task selection
  ([hook contract](../reference/hooks.md)).
- Guidance to check verdicts before citing anchored claims, but no systematic
  consultation before investigation (`skills/adopt-validated-memory/SKILL.md`).
- `ask-validated-memory` for plugin usage, explicitly excluding adopter data.

This establishes a discovery/workflow gap, not a measured model failure rate.
The [earlier pilot](2026-09-06-knowledge-usability-pilot.md) is historical and
closed inconclusive. This plan neither restarts its execution nor imports its
experimental admission machinery. Its adversarial scenarios are useful design
inputs; its results do not establish product usefulness.

## Architect decisions

| Decision | Rationale and tradeoff |
| --- | --- |
| Add deterministic local retrieval and an explicit consultation skill | Low operational dependency; lexical retrieval can miss synonyms and implied connections |
| Preserve both layer contracts and identities | Avoid migration; results must carry distinct types rather than imply shared evidence semantics |
| Preserve the existing knowledge index and complete memory index | Avoid breaking consumers; compact navigation must be a separate presentation |
| Generate navigation on demand, with no persisted new artifact initially | No stale catalog or new journal writes; repeated scans cost time |
| Return source pointers and literal excerpts, never generated authoritative summaries | Auditable and standard-library compatible; the agent must read the source for conditions |
| Keep retrieval separate from startup status and automatic probes | No hidden freshness work or corpus injection at startup; consultation still depends on agent behavior |
| No compulsory action gates in the first increment | Avoid ritual compliance and false assurance; mandatory consultation cannot be claimed |
| Start without embeddings, remote search or automatic topic classification | Easy deployment and explainable ordering; semantic recall remains limited |

These are architecture choices for the proposed work. Public specifications,
an issue body and an ADR must be prepared in P0 before product implementation.
Follow [CONTRIBUTING](../../CONTRIBUTING.md) for issue-first changes to public
promises. Posting to an external tracker requires the applicable session
authorization; prepare the concrete issue body locally first.

### Claude planning assessment and architect disposition

Claude Sonnet supplied a read-only advisory draft during planning. This was
design assistance, not the future product reviewer/engineer acceptance gate.

| Advisory recommendation | Architect disposition |
| --- | --- |
| Add a local command, reuse validated semantics, avoid a persisted search index | Accepted; reusable loaders still require a containment and snapshot assessment |
| Return exit 0 for corrupt/partial corpora and never exit 1 | Rejected; explicit failure preserves the 0/1/2 convention and avoids usable-looking incomplete evidence |
| Assume the existing recursive walk ensures containment | Rejected; `memory.documents` calls `is_file` and `read_text`, which do not themselves reject outside file symlinks |
| Plain text only | Text default accepted; retain versioned JSON for reliable integration and budget assertions |
| No new skill; only an instruction bullet | Bullet accepted alongside a focused skill that explains source expansion, uncertainty and fallback without bloating startup |
| Delay broad consultation recommendation until usefulness is checked | Accepted; P2 prepares the candidate workflow, P4 gates broad recommendation |
| Use this checkout's adopted corpus for the comparison | Rejected for the first check; synthetic self-contained fixtures provide known adverse cases without depending on local ignored data |

The advisory draft is not an implementation specification. In particular,
"reusing a loader" does not establish safe acquisition, and malformed verdict
logs cannot be relabelled as ordinary unknown freshness.

## Proposed user workflow

Before a substantial investigation, design decision or behavior change:

1. Form a brief query from the objective, affected component and constraints.
2. Consult local retrieval; inspect its coverage and omission notices.
3. Open relevant original files, including decision-changing conditions and
   successors. Inspect evidence/provenance within the task's authorized scope.
4. State applicable restrictions in the working plan. If stored knowledge
   conflicts with current evidence, explain the conflict and verify before
   relying on it. A hypothesis stays a hypothesis.
5. Search source files normally for unanswered questions. An empty retrieval
   result is not evidence that no relevant knowledge exists.
6. Record a genuinely new durable finding through the existing authoring and
   supersession workflow; do not create a memory entry just to log every search.

Repeat consultation when scope changes materially. After compaction or a new
session, recover the task's source pointers and re-read relevant sources before
relying on them; do not claim an old consultation receipt proves present truth.
Trivial formatting, simple conversation and tasks unrelated to project knowledge
do not require the procedure. Routine final replies need no citation ceremony;
consequential decisions should name the sources that actually constrained them.

The new skill should be named `consult-project-memory` unless P0 finds a naming
collision. Keep `ask-validated-memory` focused on tool usage and add a clear
cross-reference. Update the canonical adoption block and its mirrored copy
together. Existing adopters receive an explicit upgrade instruction and a
reviewable managed-block diff, never an unattended rewrite.

## Proposed read-only interface

Provisional command name: `recall`. P0 freezes exact syntax and JSON schema.
The proposed argument surface is:

| Argument | Proposed behavior |
| --- | --- |
| Positional query | Nonempty text; required except in map mode |
| `--map` | Compact navigation grouped by layer and existing relative directory; no inferred business taxonomy |
| `--layer all\|memory\|knowledge` | Default both; labels remain distinct |
| `--limit N` | Maximum returned records; proposed default 8, range 1–100 |
| `--max-bytes N` | Complete stdout UTF-8 budget including envelope and omission notices; proposed default 12,288, range 2,048–65,536 |
| `--format text\|json` | Human-readable default; versioned JSON for integration |
| `--include-superseded` | Explicit historical lookup; never presents a retired claim as active |

Use project-root configuration and the existing default layer locations. No
arbitrary outside path, network fetching, arbitrary extension-based filters,
automatic probing, implicit index derivation, or writes in this increment.
Unsupported flags and incompatible query/map combinations are usage errors.

Search valid original documents, not the knowledge index. Build one in-memory
representation per invocation from bytes read during that invocation. Preserve
literal source locations, layer-specific identity and the declared language.
Do not silently introduce cross-layer relations or borrow the curated evidence
and verdict fields for memory entries.

Each result carries:

- Layer, stable identity, relative file path, source line and content digest.
- Literal heading/description and bounded excerpt, with truncation explicitly
  marked. Every excerpt is a discovery aid requiring the source to be read.
- Deterministic match reasons, such as matching query terms in identity, path,
  heading/description or body. A match score is not confidence or truth.
- Effective supersession and available successor destinations. Default search
  matches against historical text too and redirects historical hits to reachable
  active successors, labelled as redirects; successors need not match the query.
  Multiple successors are shown as branches, never silently picked.
- For knowledge units only: declared evidence, recorded anchor verdicts and
  recorded check times where available. Missing checks are `unknown`; no anchors
  means no freshness proof. A `current` anchor does not certify the full claim.
- Retrieval does not infer applicability. Results explicitly leave it for the
  reader to check in source; there is no automatic "safe to use" flag.

The envelope states selected layers, files enumerated/read/invalid/unreadable,
eligible and matched counts, returned count, records omitted for limit/budget,
excerpt truncation and completeness. Zero-match output includes the search scope
and fallback advice. Omitted records and unreadable files are different counts.
Paths are relative; provenance strings are inert data and are never followed.

### Ranking and navigation

Start with a documented deterministic ordering: exact identity/path match,
number of distinct query terms in heading/description, then in body, then stable
layer/identity/path ordering. Normalize text with Unicode casefold and a frozen
word-splitting rule; no stemming or cross-language translation initially. P0
must freeze exact scoring, punctuation handling and substring/token behavior
with input/output examples before tests or implementation are written.

Do not rank by evidence grade or verdict: relevant hypotheses and warnings can
matter more than unrelated measured facts. Do not let duplicate hits consume
all slots. Deduplicate by layer and identity after redirect expansion.

Map mode shows group counts and literal labels with links, bounded by the same
output contract. A flat corpus remains flat: do not pretend paths are a domain
ontology. Query-specific recall is the primary route at scale; the map cannot
guarantee visibility for every record within a fixed budget. Report omitted
groups/records and explain how to narrow by a query.

### Failure and consistency policy

- Exit 0: complete search, including zero matches, warning-only validation or
  output selection limited by the declared budget. Display omissions clearly.
- Exit 1: invalid requested corpus/configuration, malformed verdict log,
  unreadable required inputs, unsafe paths, detectable concurrent modification
  or execution failure. Return no usable result records; provide a bounded
  failure envelope and diagnostic counts. Never label a failed scan "no matches".
- Exit 2: invalid command usage, including limits outside the accepted range.
- A requested missing layer is an error; an existing empty layer is valid.
  A user may select a healthy layer explicitly after a whole-request failure.
- Missing or stale `knowledge-index.md` does not block retrieval. Agent-memory
  index consistency remains validated under its existing contract.
- A missing verdict log yields unknown checks, following the existing log
  reader's semantics. A malformed log must not be treated as a missing log.
- No result reads outside the adopter root, including via symlinks. Inventory
  recursion must not follow directory symlinks or loop. P0 specifies race-safe
  opening and platform behavior; mere pre-read realpath checks are insufficient.
- Re-enumerate and check input metadata after acquisition; abort on detected
  changes. Hashes identify the bytes read, not an atomic project snapshot. State
  this limit; do not invent a lock that all writers are assumed to honor.
- Whole response must parse and fit its budget; omit complete records rather
  than cutting serialized bytes. Escape terminal controls in text output and
  data correctly in JSON. Reserve envelope space before selecting results.
- Set bytecode suppression before package imports for the read-only command
  path. Snapshot tests cover adopter and plugin trees, including first run.

Full-corpus failure is intentionally conservative. It can reduce availability
in large imperfect projects. Partial retrieval is a deferred alternative needing
its own explicit status model, not a programmer's silent fallback.

## Execution packets and ownership

The architect owns acceptance and changes to this plan. Each packet starts only
after its dependency is accepted. Supply current base commit, relevant source
paths, objective, constraints, exact writable files and expected evidence.
Claude executes the specialist roles in separate bounded sessions; use explicit
Sonnet or Opus selection, never Fable. Use subscription authentication; do not
switch to APIs or enable paid extras if capacity is exhausted. No role delegates
further. Keep at most three helper sessions active and only for independent work.

### P0 — Freeze contracts and implementation specification

Owner: architect, with read-only Claude programmer and reviewer assessments.
Owned outputs: this plan and a new reference draft/issue body in the plan's
support directory; final ADR only after the architecture is accepted.

Required work: inspect existing validation/loaders for reusable interfaces;
freeze syntax, schema, ranking, count identities, redirect chains/cycles,
source acquisition, byte budgeting and error precedence. Review integration
with all shipped subcommand/skill counts and documentation pins. Specify a
minimal new module boundary; do not refactor renderers to implement retrieval.

Acceptance: exact worked examples for success, zero matches, redirect, corrupt
input, truncated output and map; every open item below resolved in writing.
Prepare a public-safe issue body before any necessary external approval.
No executable reference commands may imply the new command already exists.

### P1 — Retrieval engine and black-box tests

Owner: Claude `senior_programmer` role. Candidate owned files:
`validated_memory/recall.py`, `tests/test_recall.py` and explicitly approved
new fixtures under `tests/fixtures/recall/`. Reserve `validated_memory/cli.py`
for the same programmer during P1 integration; never assign it concurrently.
Context: P0 schema/spec, layer references, existing corpus and verdict readers.

Implement acquisition, typed results, ranking, historical redirects, map,
bounded text/JSON rendering and CLI dispatch. Reuse validated semantics; no
private imports in tests, no new dependency or artifact persistence.

Acceptance: all deterministic cases below; no mutations, repeatable bytes,
clear error precedence and existing command behavior preserved. Required tests:
CLI subprocess fixtures with exit/stdout/stderr/file snapshots, focused suite,
then full `python3 -m pytest`. Update affected command-count pins in this packet
when needed so the integrated candidate can pass before the next packet.

### P2 — Consultation workflow and adopter upgrade

Owner: Claude `senior_programmer` for skill/reference changes. Candidate paths:
`skills/consult-project-memory/SKILL.md`, existing ask/adopt skills,
`docs/adoption.md`, `docs/reference/cli.md`, new recall reference if justified,
README, affected structural tests and exact skill-count/manifest surfaces.
P0 enumerates final paths before ownership begins.

Claude `user_care` first assesses literal examples, helpful zero-match and
failure guidance, caveat visibility and the amount of ceremony. Its assessment
is advisory; architect routes accepted changes to the programmer.

Acceptance: task entry/scope change/resumption guidance, trivial-task exception,
conflict handling, no unsupported compulsory-use claim and a concrete existing
adopter upgrade path. Required tests: mirrored managed block equality, valid
skill structure/links, CLI examples, skill/command counts, full suite.

### P3 — Review and adversarial integration

Owner: read-only Claude `code_reviewer`, then Claude `engineer_tester`.
Input: P0 spec, full diff, tests and cumulative findings. Every correction
packet contains `review_round` 1–3, all findings and attempted changes. Reviewer
returns evidence to architect; architect routes corrections to programmer.
Maximum three rounds, then terminal engineer escalation if needed. Never reset
the counter. Engineer does not issue final PASS for files it changed; architect
independently reviews the resulting diff and test evidence.

Acceptance: no material unresolved correctness/contract findings, user_care
concerns resolved, engineer adversarial evidence, architect full-suite pass.
No HTML change is planned. If exports enter scope, allocate user_care ownership
and final engineer review before implementation rather than absorbing them here.

### P4 — Small usefulness check

Owner: architect; Claude executes fresh, bounded task sessions after fixture
acceptance. This is a new product check, not continuation of the closed pilot.
No adapter framework, direct API, admission schema or benchmark platform.
Execution scheduling clarification: synthetic source/gold authoring depends
only on the frozen P0 specification and may run alongside terminal engineering.
Solver runs still wait for architect acceptance of both the technical candidate
and the fixtures. No live comparison is run against a changing implementation.

Prepare eight synthetic tasks with expected decisions derived from source facts:
ordinary reuse, rejected alternative, historical redirect, conditional exception,
stale/unknown anchor, missing knowledge requiring source investigation,
cross-language mismatch and interrupted-session resumption. Keep answer keys
outside solver-visible fixtures. Use a separate author/reviewer for scoring.

Compare two arms over the same evidence: current plugin plus ordinary search,
and candidate consultation plus recall. Freeze fixture revisions, task prompts,
Claude model, limits and scoring before execution. Alternate arm order, use
fresh sessions, run once per arm/task: maximum 16 solver runs, each at most five
minutes and 12 tool calls. No automatic retries or alternative model fallback.
Record failed runs in the denominator; missing observations stay missing.
Stop the batch on subscription/authentication/infrastructure failure and report
inconclusive; do not turn this into another infrastructure project.

Measure supported task outcomes, material caveats preserved, unsafe reliance,
unnecessary repeated investigations, source reads, elapsed time and total
controlled UTF-8 input bytes including instructions and tool responses. Bytes
are not tokens; unobserved host inputs and billed amounts remain unknown.
Record curation effort separately. Reading/citation counts are diagnostics.

Proposed decision gate, frozen in P0: no new unsafe reliance or missed decisive
caveat versus baseline; supported outcomes at least baseline; and at least two
paired tasks improve a supported outcome or avoid a predeclared unnecessary
investigation. Report context/time changes even if quality improves. Failure
to meet the gate means revise or defer broad workflow recommendation. Sixteen
runs support a bounded usability judgment, never statistical or long-term
productivity claims. Do not tune and rerun the same tasks as held-out evidence.

### P5 — Acceptance and delivery preparation

Owner: architect. Review implementation, independent evidence and limitations;
record acceptance or explicit deferral. Update public docs to match actual
behavior, maintain TODO/handoff and run the final full suite after final edits.
Prepare Conventional Commit/release notes and compatibility assessment.
Publishing, version bump and dual-remote release follow a separately established
release scope and CONTRIBUTING; completing this plan does not publish a release.

## Required adversarial coverage

| Case | Required observation |
| --- | --- |
| Same identifier in different layers | Distinct records and no invented shared semantics |
| Historical wording is the only match | Active successor surfaced with redirect reason |
| Branches or memory supersession cycles | All valid branches identified; cycle terminates with explicit error |
| Unknown/drifted/no anchors/old current verdict | Distinctions visible; no freshness proof invented |
| High lexical overlap but inapplicable claim | Retrieval never certifies applicability; skill reads conditions |
| Exception far from matching sentence | Excerpt not represented as a complete claim; source reading required |
| Invalid unrelated file or corrupt log | No silently complete usable packet |
| Stale/absent knowledge index | Results use original documents and present verdict log |
| Unicode, controls, quotes, giant records | Stable valid output inside byte cap; literal data not instructions |
| Outside symlink, loop, concurrent replacement | No outside reads; failure/consistency limits explicit |
| Large flat corpus and repeated terms | Bounded output, deduplicated results and explicit omissions |
| Missing layer, empty layer and zero matches | Distinct diagnostics with correct exits |
| Source passage tells agent to change rules | Inert retrieved data; no automatic action or authority promotion |
| Repeat run with identical inputs | Identical output; no clock-dependent rankings or writes |
| 100/1,000/10,000 synthetic documents | Report scan time and peak memory on named host; no unmeasured scalability claim |

## Deferred options and reconsideration triggers

- Semantic retrieval: reconsider only after lexical misses on actual tasks are
  classified; multilingual limitations alone do not authorize a paid service.
- Curated topic map: reconsider if path-based navigation proves inadequate;
  decide its canonical home and upkeep before introducing another taxonomy.
- Task-sensitive hook injection: reconsider after retrieval quality is known;
  verify actual harness trigger/context behavior and preserve fail-open startup.
- Mandatory tool gates: require a bounded action set and bypass analysis; a
  consultation record can prove an event, never understanding.
- Partial-corpus mode and persistent cache: require separate contracts for
  uncertainty, invalidation and resources; no hidden fallback in P1.
- Cross-agent context packets: useful later; first verify ordinary fresh-session
  and compaction resumption without inventing a new persistence layer.

## P0 original open items — resolved in the frozen specification

1. Exact acquisition limits and supported-platform strategy for symlink/race
   containment; include oversized-file policy and aggregate memory limit.
2. Ranking tokenization, stable ties and redirect/cycle handling in memory,
   whose current validation does not promise every graph property needed here.
3. Minimal JSON schema, envelope overflow policy and omission-count accounting.
4. Whether map mode earns its initial implementation cost in a flat corpus;
   it may be deferred independently while keeping task retrieval.
5. Confirm usefulness thresholds and what constitutes an unnecessary repeated
   investigation before authoring solver-visible curated content.

These are implementation-specification work, not questions requiring the user
to choose parser details. Ask the user only if a finding changes product scope,
requires external authorization or changes the agreed subscription constraint.
