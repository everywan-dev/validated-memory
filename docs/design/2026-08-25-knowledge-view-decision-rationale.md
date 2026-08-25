# Design: a legible `knowledge.html`, and the `decision` field that makes it honest

Target version: 2.0.0. Status: approved, not implemented.

## Purpose

`render` already carries the corpus to someone who has neither the repository
nor the plugin. What it does not do is let that person *see* it. The page is a
list of units, one after another: correct, inert, and close to unreadable at
any size beyond a handful of conclusions. A reader cannot tell at a glance
what the project knows, which parts are solid, what has drifted, or how any
particular conclusion was reached.

This design does two things, and they are one change because neither is worth
much alone:

1. It gives `knowledge.html` a visual vocabulary -- an overview, grouping,
   cards, badges and generated diagrams -- instead of a transcript.
2. It adds the one contract field without which the most valuable of those
   diagrams would be a picture of data that does not exist.

`memory.html` is deliberately out of scope; see [Scope](#scope).

## What this supersedes

The 2026-08-18 design of `render` remains the record of what was decided then
and is not rewritten. Three of its decisions are superseded here, and only
these three:

- Its scope rejected "a decision or ADR unit type with considered options and
  consequences", "not to be reintroduced without a new design"
  (`docs/design/2026-08-18-render-views.md:17-34`). This is that design. What
  is reintroduced is narrower than what was rejected: see
  [What `decision` is not](#what-decision-is-not).
- Its diagram section fixed the set at two, the freshness strip and the
  confluence (`docs/design/2026-08-18-render-views.md:339`). The set grows.
- Its blanket rejection of JavaScript
  (`docs/design/2026-08-18-render-views.md:553-563`) is replaced by a sharper
  distinction: the canonical artifact is static, complete and inert; an
  optional installed layer may add navigation only. See
  [Static first](#static-first-and-the-optional-layer).

Everything else in that document stands, including determinism, atomic
writes, the `--only-existing` fail-open contract, verbatim bodies, and the
escaping discipline.

No ADR from 0001 to 0007 is superseded. Two new ADRs are written; see
[ADRs](#adrs).

## Scope

In: the `decision` field, its validation, and the redesign of
`knowledge.html`.

Out, and to be done as separate work:

- `memory.html`. It keeps its current format. The two pages will not look
  alike until that second delivery, which is a real cost, accepted
  deliberately: shipping one page well is worth more than shipping two
  half-designed.
- Typed relations between units (`derives_from`, `supports`, `contradicts`).
  Rejected again here, on their own merits; see
  [Rejected alternatives](#rejected-alternatives).
- References from an option to the units used to weigh it (`basis`). A
  possible later evolution, not the minimal step. It reintroduces edge
  resolution, justification cycles, and the question of what a cycle across
  `basis` and `supersedes` means. Nothing in a real corpus yet shows anchors,
  provenance, a local reason and the supersession chain to be insufficient.
- Multi-line reasons. They would require widening the frontmatter subset,
  which is a decision about the whole subset and not a need of this model.

## The contract change: `decision`

One field is added to `BASE_FIELDS` (`validated_memory/contract.py:15`):

```python
BASE_FIELDS = ("id", "evidence", "supersedes", "anchors", "provenance", "decision")
```

`decision` is **optional**, and its absence produces neither ERROR nor
WARNING. Most units are measurements or observations; they record no choice
between alternatives and must not be nagged into inventing one.

### Canonical form

```yaml
---
id: kb-0007
evidence: verifiable
decision:
  question: How should knowledge views be delivered?
  options:
    - label: Generate a complete static artifact
      disposition: chosen
      reason: It stays readable without Python, JavaScript or network access.
    - label: Build an interactive application
      disposition: rejected
      reason: It introduces a runtime dependency for the reader.
    - label: Infer the decision from body sections
      disposition: rejected
      reason: The body carries no validated structure to derive a diagram from.
anchors: []
provenance: []
---
```

Fields:

| Path | Type | Required | Values |
|---|---|---|---|
| `decision` | mapping | no | envelope closed to `question`, `options` |
| `decision.question` | string | yes, when `decision` exists | non-empty after `strip()` |
| `decision.options` | list of mappings | yes, when `decision` exists | at least two |
| `decision.options[].label` | string | yes | non-empty |
| `decision.options[].disposition` | enum | yes | `chosen` \| `rejected` |
| `decision.options[].reason` | string | yes | non-empty |

There is no separate `chosen` key naming the winner. The choice has exactly
one representation, `disposition`, so it cannot contradict itself. Options
carry no identifier: an option is local content, not an entity that can be
referenced, superseded or probed.

### Parser compatibility

The form is expressible with the frontmatter subset as it stands
(`validated_memory/frontmatter.py`), which already accepts mappings, lists of
mappings and nested blocks -- the same shape `anchors` uses. The subset is not
widened. Its limits apply and must be documented with the field:

- `question`, `label` and `reason` are each one physical line.
- Block scalars (`|`, `>`) and non-empty inline collections (`[a, b]`) are
  rejected by the parser.
- Quoted scalars admit no backslash escapes, so a value cannot contain its own
  quote character.
- Every scalar arrives as a string; there is no type inference.
- In a plain scalar, ` #` starts a comment.

Long-form argument stays where it already belongs: the unit body, or
`provenance`.

Option order is presentation order and is preserved. Mapping key order carries
no meaning; the renderer emits its own fixed order and never depends on the
incidental order of keys.

### Validation

A new `_check_decision` is dispatched from `contract.py:81-86`. Every
structural defect is an ERROR:

- `decision` is not a mapping.
- An unknown key inside `decision`.
- `question` missing, not a string, or empty after `strip()`.
- `options` missing, not a list, or holding fewer than two elements.
- An option that is not a mapping.
- An unknown key inside an option.
- Any of `label`, `disposition`, `reason` missing.
- `label` or `reason` not a non-empty string.
- `disposition` other than `chosen` or `rejected`.
- Not exactly one option with `disposition: chosen`.
- Two options with the same exact `label`, which would draw as
  indistinguishable nodes.

No new WARNING is introduced. In particular: a missing `decision` is valid;
`evidence: hypothesis` together with a decision is valid, and describes a
conclusion not yet proven; a large number of options is a presentation
problem, not evidence of invalid knowledge; and the length of a reason never
turns a valid unit invalid to suit the SVG.

This keeps the existing severity line intact: ERROR is contractual
inconsistency, WARNING is a valid but operationally weak condition, such as a
unit with no anchors (`validated_memory/contract.py:25`).

### What `decision` is not

- **Not a graph.** `decision` holds no reference to any unit. It adds no
  edges, so it cannot create a cycle. `supersedes` remains the only relation
  between units and its DAG remains the only global graph; active and
  superseded states keep being computed from `supersedes` alone.
- **Not an ADR store.** A unit is still a claim backed by evidence.
  `decision` explains how that claim was selected; it does not turn the unit
  into a decision record with consequences and status.
- **Not a verdict on the alternatives.** `rejected` means "considered and not
  chosen for this decision". It does not mean false, and it does not mean
  superseded. The renderer and the documentation must not let those three
  read as the same thing.
- **Not cumulative along a chain.** Rationales are never merged across a
  supersession chain. Each block explains its own unit's conclusion at that
  time.

A substantive change to the question, the chosen option or a reason is a new
unit that supersedes the old one -- not an edit. A typographical or structural
correction remains a repair, as it is today.

## The page

### Overview

The page opens with what a reader needs before any individual unit:

- **Counts** of units by evidence state crossed with verdict
  (`measured`/`verifiable`/`hypothesis` x `current`/`drifted`/`unknown`).
- **A map of the corpus**, grouped by `anchors[].system` -- a validated field
  that says which system a piece of knowledge is about, present on every
  anchored unit. When the adopter declares an enum in their extension
  (`knowledge-extension.md`), that enum is the preferred axis and the system
  grouping becomes secondary. No field is added to the base contract to carry
  a topic.
- **The unprobed queue**: anchors with no record in the verdict log.

Units with no anchors are grouped explicitly, not dropped: a unit that cannot
expire is a fact about the corpus, not an omission.

### Unit card

Each unit becomes a card carrying, in a fixed order: headline and id; badges
for evidence state and aggregate verdict; the freshness strip per anchor; the
decision diagram when the unit has one; the supersession chain; `provenance`;
and the verbatim body.

The freshness strip keeps its current semantics exactly: it is a sequence of
records in log order, oldest to newest, and **not** a time axis.
`recorded_at` is not validated anywhere -- it may be absent, null, or of any
type (`validated_memory/verdicts.py:139-165`) -- so no chart may imply
distance in time between two records. The right edge remains the last record,
never "now", or the artifact would be rewritten on every run.

`knowledge-index.md` is unchanged. `derive` keeps emitting
`| id | state | evidence | verdict |`; `decision` does not appear in the
index.

## Diagrams

### The decision diagram

One per unit that carries a `decision`: the question, then the options, with
the chosen one distinguished. Fixed depth, no edges between options, no edge
leaving the unit. Size and edge count are linear in the number of options, so
the shape cannot degenerate -- there is no global graph to draw, and therefore
no hairball to avoid.

If a unit carries many options, the renderer may switch layout
deterministically, but it never omits or truncates an option silently.

### Rules every generated diagram obeys

- **Deterministic**: element ids derive from the unit id and the option's
  position, never from `hash()`, which is salted per process. No clock, no
  generation timestamp, no platform-dependent font metrics.
- **Inert**: no `href` of any kind inside the SVG, no `<use>`, no `<image>`.
  An `href` inside SVG is the documented way a diagram loads an external
  resource without anyone noticing.
- **Escaped**: every text node and attribute passes through
  `html.escape_text` / `html.escape_attribute`. A generated SVG with
  unescaped adopter text is an XSS surface, not a drawing.
- **Not colour-alone**: `chosen` and `rejected` differ in shape and in text,
  not only in fill. Each diagram carries `<title>` and `<desc>`.
- **Never load-bearing**: everything a diagram shows is also present as
  structured, accessible HTML. A reader who cannot see the SVG loses nothing
  but the picture.

## Static first, and the optional layer

`render` always writes a complete page: full content, full styling, full
diagrams, no dependency at reading time. The CSS is compiled at release time
-- Tailwind may be a build tool of this repository, never something the
adopter or the reader runs -- and ships inlined. Diagrams are inline SVG
generated in Python, deepening `validated_memory/svg.py`.

On top of that, and only on top, an **optional installed layer** may add
navigation: search, filters, pan and zoom. It never adds content and never
adds semantics. A reader without it sees the same information.

Three consequences that are part of this design, not details of it:

- **The profile is declared in the adopter project, not detected from the
  machine.** A manifest in the adopter fixes which profile `render` writes.
  Deriving it from whatever happens to be installed locally would make two
  developers alternate static and enhanced HTML on every `SessionStart`, and
  the artifact would flip in `git diff` with no change in the corpus.
- **A declared enhanced profile with missing assets is an error, not a silent
  downgrade.** Explicit `render` reports ERROR; `render --only-existing` --
  the startup hook's mode -- warns and leaves the existing artifact untouched,
  which is exactly its current fail-open contract.
- **`init` stops importing `render` eagerly** (`validated_memory/init.py:41`).
  With optional assets in play, that coupling would let a missing view
  dependency break an unrelated subcommand.

## Adoption

Per ADR 0007, adoption decisions live in the skill, not in `init`. The
`adopt-validated-memory` skill asks three separate questions:

1. Are the HTML views activated at all?
2. Is the optional interaction layer installed?
3. Are the HTML, the profile manifest and the assets versioned by the
   repository, or kept local?

Each has a default the skill states, and a "local" answer names both places it
has to be honoured, as ADR 0007 requires.

## Tests

The suite is the gate; TDD applies. Beyond the per-feature tests:

- **The self-containment whitelist is replaced, not widened.**
  `tests/test_render.py:86-158` pins a whitelist of (element, attribute) pairs
  that may carry a URL. It is rewritten for the new element set. Widening it
  until it tolerates any `<script>` or any URL would delete the property it
  exists to enforce.
- **A new test ties `BASE_FIELDS` to the documents that enumerate it.** The
  blast-radius census found five places that list the contract by hand --
  `README.md`, `docs/reference/curated-knowledge.md`, `docs/walkthrough.md`,
  `skills/create-knowledge-unit/SKILL.md`, and the `EXTENSION_STUB` template
  at `validated_memory/init.py:86-87` -- with no test connecting any of them
  to `contract.py`. Today the contract can be documented incompletely and
  nothing fails. That closes in this change, in the same spirit as the
  subcommand check in `tests/test_skills_structure.py:117`.
- **Determinism is re-pinned over the new output**: a second run reports
  `unchanged`, leaves the bytes identical and leaves `st_mtime_ns` untouched.
- **Adversarial content** through `question`, `label` and `reason`, asserting
  that nothing lands live in the HTML or inside the SVG.

## Versioning and release

This is **2.0.0**. By ADR 0005 a release is one commit where
`pyproject.toml`, `validated_memory/__init__.py` and
`.claude-plugin/plugin.json` agree with the tag, pushed to both remotes.

The moving `v1` tag stops being re-pointed and a `v2` channel starts.
Adopters pinned to `@v1` stay on 1.4.0 until they upgrade deliberately, which
is the orderly exit for a contract change.

Three migration facts belong in the release notes:

- **No existing corpus stops validating.** `decision` is optional and purely
  additive on the data side.
- **The break runs the other way.** A unit carrying `decision` is an unknown
  field, and therefore an ERROR, for any 1.x reader
  (`validated_memory/contract.py:70-79`). CLI, CI, the Action and the hooks
  must be on 2.x before the first `decision` is written.
- **An adopter who already declared a `decision` field in their extension**
  will find their `knowledge-extension.md` refusing to load, because an
  extension may not redeclare a base field
  (`validated_memory/extension.py:119-125`). Their migration is to rename
  their field and bump their schema version.

There is no mass backfill. Nothing can reconstruct what was considered and
why, and inferring it from bodies, commits or supersession would fabricate
exactly the record this field exists to make trustworthy. `decision` is
written on new conclusions and on natural supersessions.

## ADRs

Two new ADRs, numbered in sequence:

- **Decision rationale is local structured metadata on a unit.** Fixes the
  envelope, the single-choice rule, the absence of references, and that
  `rejected` is neither false nor superseded.
- **Knowledge views are static first; interaction is progressive
  enhancement.** Fixes that the complete, accessible, inert artifact is
  canonical, and that any installable layer adds navigation only.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| Typed relations between units (`derives_from`, `supports`, `contradicts`, `answers`) | Cannot express an option that never became a unit, nor why it was discarded, without giving that option an identity, an evidence state and possibly anchors -- which is where this becomes a decision manager. Each relation carries its own cycle semantics, and the union with `supersedes` raises questions nothing in the corpus asks yet. |
| `basis` references from an option to units | Reintroduces edge resolution, self-reference, justification cycles and expansion limits, to serve a view. Reconsider when a real corpus shows the local reason to be insufficient. |
| A separate `chosen` key naming the winner | Two representations of one fact, free to disagree. |
| Identifiers on options | Makes an option look referenceable, supersedable and probeable. It is none of those. |
| Solving this with the adopter's declared extension | The extension mechanism carries `string` and `enum` only, cannot express a closed mapping or "exactly one chosen", and extension fields are permitted, never required. A renderer that knows about an extension called `decision` has made it a hidden part of the base contract, owned by nobody. |
| Tailwind and Mermaid from a CDN, as in the report this look is modelled on | The page stops opening without network, stops surviving as an attachment, and byte determinism stops meaning anything: the same bytes can render differently, or blank, depending on what the CDN serves that day. |
| Inlining the third-party assets into every page | Keeps the single file, at multiple megabytes duplicated across pages, unreadable diffs, and a high chance of being stripped or quarantined as an attachment. |
| Widening the frontmatter subset for multi-line reasons | A decision about the whole subset, not a need of this model. The body already holds long-form argument. |

## Risks

- Reasons and rejected alternatives end up in versioned Markdown and in a
  self-contained HTML file that is designed to be sent to third parties.
  Nothing sensitive belongs in them, and the documentation must say so where
  the field is introduced.
- Validation guarantees shape, not honesty or sufficiency. A well-formed
  rationale can still be a bad one.
- The model has no pending decision and no multi-select. A composite option
  covers simple cases; a real need for either is another contract revision.
- A rationale can go stale while its claim stays current: anchors prove the
  claim, not that the reasoning still holds.
- If the optional layer ever parses the block itself instead of consuming the
  same normalized model the static renderer uses, there will be two
  semantics. It consumes, it does not re-interpret.

## Sequencing

1. `decision` in the contract, with `_check_decision` and its tests. Nothing
   renders it yet.
2. The documentation sync test, and the five documents it pins.
3. The page structure: overview, grouping, cards -- on the existing two
   diagrams.
4. The decision diagram, and the replaced self-containment whitelist.
5. Compiled CSS, and the release step that produces it.
6. The profile manifest, the `init` decoupling, and the adoption questions.
7. The two ADRs, the reference documentation, and the 2.0.0 release.

Steps 1 and 2 are independently releasable and carry no visual risk. Step 5
is the only one that adds a build step to this repository.
