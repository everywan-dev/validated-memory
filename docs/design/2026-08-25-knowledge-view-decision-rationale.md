# Design: a legible `knowledge.html`, and the `rationale` field that makes it honest

Target version: 2.0.0. Status: approved after adversarial review, not
implemented.

## Purpose

`knowledge.html` is a transcript. It is correct, inert and close to unreadable
past a handful of units: a reader cannot tell what the project knows, which
parts are solid, what has drifted, or how any conclusion was reached.

This design does three things, and they are one change because none is worth
much alone:

1. It gives `knowledge.html` a visual vocabulary -- an overview, grouping,
   cards, badges and generated diagrams -- instead of a list.
2. It adds the one contract field without which the most valuable of those
   diagrams would picture data that does not exist.
3. It adds a second, optional artifact for people who would rather browse than
   read, without weakening the first one by a single guarantee.

`memory.html` keeps its current format; see [Scope](#scope).

## What this supersedes

The 2026-08-18 design of `render` is the record of what was decided then and
is not rewritten. Three of its decisions are superseded here, and only these:

- Its scope rejected "a decision or ADR unit type with considered options and
  consequences", "not to be reintroduced without a new design"
  (`docs/design/2026-08-18-render-views.md:17-34`). This is that design, and
  what returns is narrower than what was rejected; see
  [What `rationale` is not](#what-rationale-is-not).
- Its diagram set was fixed at two, the freshness strip and the confluence
  (`docs/design/2026-08-18-render-views.md:339`). The set grows, and the two
  existing diagrams are brought up to the rules the new one obeys.
- Its blanket rejection of JavaScript
  (`docs/design/2026-08-18-render-views.md:553-563`) becomes a sharper rule:
  `knowledge.html` stays inert, and interaction lives in a separate artifact
  that never carries content the inert one lacks. See
  [Two artifacts](#two-artifacts).

No ADR from 0001 to 0007 is superseded except ADR 0005 on one point, the
number of moving channels; see [ADRs](#adrs), where four new ADRs are
listed.

## Scope

In: the `rationale` field and its validation, the redesign of
`knowledge.html`, and the optional `knowledge-app.html`.

Out, and separate work:

- `memory.html`. Its stylesheet is separated from `knowledge.html`'s in this
  change (see [Styling](#styling)) but its markup and content are untouched.
  The two pages will not look alike until a second delivery. That is a real
  cost, accepted deliberately.
- Typed relations between units (`derives_from`, `supports`, `contradicts`).
  Rejected again on their own merits; see
  [Rejected alternatives](#rejected-alternatives).
- `basis` references from an option to the units used to weigh it. A possible
  later evolution; nothing in a real corpus yet shows anchors, provenance, a
  local reason and the supersession chain to be insufficient.
- Multi-line reasons, which would mean widening the frontmatter subset -- a
  decision about the whole subset, not a need of this model.
- Any third-party runtime dependency. See
  [No third-party code](#no-third-party-code).

## The contract change: `rationale`

One field joins `BASE_FIELDS` (`validated_memory/contract.py:15`):

```python
BASE_FIELDS = ("id", "evidence", "supersedes", "anchors", "provenance", "rationale")
```

The name is `rationale`, not `decision`. Adding a base field silently breaks
any adopter whose declared extension already uses that name -- their schema
stops loading (`validated_memory/extension.py:116-125`), `validate` returns
zero documents and one ERROR (`validated_memory/validate.py:41-52`), and their
historical units carry a value the new base check rejects. `rationale` is the
less collision-prone of the two names; see
[Migration](#versioning-and-release) for what an adopter who used it anyway
must do.

`rationale` is **optional**, and its absence is neither ERROR nor WARNING.
Most units are measurements; they record no choice and must not be nagged into
inventing one.

### Canonical form

```yaml
---
id: kb-0007
evidence: verifiable
rationale:
  question: "How should knowledge views be delivered?"
  options:
    - label: "Generate a complete static artifact"
      disposition: chosen
      reason: "It stays readable without Python, JavaScript or network access."
    - label: "Build an interactive application"
      disposition: rejected
      reason: "It makes the reader depend on a runtime."
anchors: []
provenance: []
---
```

| Path | Type | Required | Values |
|---|---|---|---|
| `rationale` | mapping | no | envelope closed to `question`, `options` |
| `rationale.question` | string | yes, when `rationale` exists | non-empty after `strip()` |
| `rationale.options` | list of mappings | yes, when `rationale` exists | at least two |
| `rationale.options[].label` | string | yes | non-empty after `strip()` |
| `rationale.options[].disposition` | enum | yes | `chosen` \| `rejected` |
| `rationale.options[].reason` | string | yes | non-empty after `strip()` |

No separate key names the winner: the choice has one representation,
`disposition`, so it cannot contradict itself. Options carry no identifier --
an option is local content, not something that can be referenced, superseded
or probed.

### Serialization rule: the values are quoted

The shape parses under the current frontmatter subset without widening it; it
is the same "list of mappings" `anchors` already uses. But one plain-scalar
behaviour makes quoting mandatory rather than stylistic:

```
reason: keep the # literal here     ->  "keep the"
reason: "keep the # literal here"   ->  "keep the # literal here"
```

In a plain scalar, ` #` starts a comment (`validated_memory/frontmatter.py:164-181`),
so text is lost **silently**, before validation can see it -- and
`_check_rationale` cannot detect what the parser already discarded. The rule
is therefore part of the contract, not advice:

- `question`, `label` and `reason` are always written quoted. The skill that
  writes units emits them quoted; the documentation states it as a rule.
- Prefer `"` and fall back to `'` when the text contains a double quote.
- Text containing **both** quote characters has no representation: quoted
  scalars admit no backslash escapes
  (`validated_memory/frontmatter.py:169-177`). This limit is documented where
  the field is introduced. It is the one text that must be rephrased.
- Each value is one physical line. Block scalars (`|`, `>`) and non-empty
  inline collections (`[a, b]`) are rejected by the parser. Long-form argument
  stays in the unit body or in `provenance`.

A plain scalar may contain quotes, colons and leading hyphens without trouble;
the claim that "a value cannot contain its own quote character" is true only
of a quoted scalar using that character as its delimiter
(`validated_memory/frontmatter.py:198-203`).

**The CLI enforces the rule; the skill does not.** The parser returns the same
string for a quoted and an unquoted scalar, so `_check_rationale` cannot tell
them apart from the parsed mapping alone -- and by then ` #` and everything
after it is already gone. `validate_documents` receives `(location, text)`
pairs, so the check reads the **raw frontmatter text**: a `question`, `label`
or `reason` whose value does not begin with `"` or `'` is an ERROR, whether or
not it contains a `#`. Making only the `#` case an ERROR would leave the rule
unenforceable exactly where it silently loses data. The skill emits quoted
values because the CLI would reject anything else, which is this project's
normal order: the CLI is the authority, the skill follows it.

Option order is presentation order and is preserved. Mapping key order carries
no meaning: the renderer emits its own fixed order.

### Validation

`_check_rationale` is dispatched alongside the existing per-field checks
(`validated_memory/contract.py:81-86`, which today dispatches `id`,
`evidence`, `supersedes`, `anchors`, `provenance` and the extension). Every
structural defect is an ERROR:

- `rationale` is not a mapping.
- An unknown key inside `rationale`.
- `question` missing, not a string, or empty after `strip()`.
- `options` missing, not a list, or holding fewer than two elements.
- An option that is not a mapping, or carrying an unknown key.
- Any of `label`, `disposition`, `reason` missing, or not a non-empty string.
- `disposition` other than `chosen` or `rejected`.
- Not exactly one option with `disposition: chosen`.
- Two options whose `label.strip()` collide, or which collide after
  whitespace collapsing. Exact-string comparison is not enough: `"A"` and
  `"A "` are different strings that draw as the same node.
- A `question`, `label` or `reason` written as an unquoted scalar in the raw
  frontmatter, per the rule above.
- A `question`, `label` or `reason` containing one of the bidirectional
  **embedding, override or isolate** controls: U+202A, U+202B, U+202C,
  U+202D, U+202E, U+2066, U+2067, U+2068, U+2069. They reorder text visually
  without changing the string, on a page meant to be sent to third parties.
  The set is enumerated, not derived from `unicodedata.category(ch) == "Cf"`,
  which would also reject legitimate formatting characters. The bidirectional
  **marks** -- U+200E, U+200F, U+061C -- are allowed: they resolve direction
  for mixed text and are how correct Arabic and Hebrew content is written.

No new WARNING. A missing `rationale` is valid; `evidence: hypothesis` with a
rationale is valid and describes a conclusion not yet proven; many options are
a presentation problem, not invalid knowledge; and the length of a reason
never turns a valid unit invalid to suit a diagram.

This keeps the existing severity line: ERROR is contractual inconsistency,
WARNING is a valid but operationally weak condition, such as the no-anchors
warning at `validated_memory/contract.py:274-283`.

### What `rationale` is not

- **Not a graph.** It holds no reference to any unit, adds no edges, and
  cannot form a cycle. `supersedes` remains the only relation between units
  and its DAG the only global graph; active and superseded states keep being
  computed from `supersedes` alone.
- **Not an ADR store.** A unit is a claim backed by evidence. `rationale`
  explains how that claim was selected.
- **Not a verdict on the alternatives.** `rejected` means "considered and not
  chosen here". It does not mean false and it does not mean superseded, and
  the page must not let those three read alike.
- **Not cumulative.** Rationales are never merged along a supersession chain;
  each explains its own unit at its own time.

A substantive change to the question, the chosen option or a reason is a new
unit that supersedes the old one. A typographical correction remains a repair.

## Two artifacts

`render` writes:

- **`knowledge.html`** -- always. Complete content, full styling, generated
  diagrams, and **inert**: no script of any kind, no request to the network.
  Every guarantee it has today it keeps, unchanged: byte determinism, no
  generation timestamp, atomic write, untouched `st_mtime_ns` on a no-op run,
  and the self-containment whitelist.
- **`knowledge-app.html`** -- only when it already exists. Same content, same
  diagrams, plus one inline script that adds search, filters and pan/zoom.

**Activation is presence**, which is already this project's idiom for the
views: `init --view` creates whichever artifact is missing, never regenerates
one that exists, and deleting a file deactivates it
(`docs/reference/hooks.md:41-54`). `knowledge-app.html` becomes a third item
of that same command, behind `init --view --app`, which the adoption skill
passes when the adopter says yes. There is therefore **no profile manifest and
no new configuration key** -- in particular not in `validated-memory.md`,
whose `CONFIG_FIELDS` is closed to `extension`, `id_prefix` and `probes`
(`validated_memory/extension.py:16`) and where an unknown key gates every
consumer of the configuration (`validated_memory/extension.py:164-182`).

The full matrix, because "activation by presence" is underspecified without
it:

| State | `render` | `render --only-existing` |
|---|---|---|
| Neither view exists | writes the two views, not the app | writes nothing (`validated_memory/render.py:65-70`) |
| Views exist, no app | rewrites the views | rewrites the views |
| App exists, `knowledge.html` missing | writes both | writes both -- see below |
| App exists and is empty | fills it | fills it |
| App deleted | not recreated | not recreated |

The third row is the one new rule: **the app never outlives the canonical
page**. If `knowledge-app.html` is present, `knowledge.html` is written even
under `--only-existing`, which otherwise creates nothing. Without that rule the
hook could regenerate an interactive page while the inert one stayed deleted,
and the artifact this design calls canonical would be the one that is missing.

Two consequences stated rather than glossed:

- **Deleting the app is how you deactivate it**, so if the adopter versions
  the file, an accidental deletion and a deliberate deactivation are the same
  state -- visible in `git status`, which is where a versioned artifact is
  meant to be reviewed. The adoption decision about versioning covers all
  three files, not two.
- **Atomicity is per artifact, not across the set.** That is an accepted,
  documented limit of the writing model already
  (`validated_memory/render.py:8-17`): a failure between writes leaves pages
  from two generations side by side, and the next run heals it. A third
  artifact widens that existing window; it does not open a new kind of
  problem, and this design does not pretend to close it.
- The strict self-containment test keeps applying to `knowledge.html`
  unchanged. `knowledge-app.html` gets its own, explicitly weaker whitelist,
  written as its own policy rather than as a relaxation of the strict one.

### No third-party code

The interaction layer is **ours**: a few hundred lines of vanilla JavaScript
in this repository, inlined at render time. No Mermaid -- the diagrams are
generated as SVG in Python. No Tailwind -- the CSS is written by hand. And
therefore: no CDN, no downloaded assets, no asset manifest, no hashes to
verify, no third-party licences inside an adopter's repository, no Node in CI,
and no change to the rule that pytest is this project's only development
dependency (`CONTRIBUTING.md:11`).

The script is subject to a policy the tests enforce; see
[Tests](#tests):

- Exactly one `<script>` element, with inline content and no `src`.
- No `fetch`, `XMLHttpRequest`, `WebSocket`, `eval`, `new Function`, no
  dynamic `import`, no `href` or `src` construction.
- It reads the DOM the renderer produced. It never fetches, never stores,
  never phones home.
- It is **progressive enhancement in the strict sense**: with the script
  removed, `knowledge-app.html` is `knowledge.html`. A test asserts that
  every unit id, headline, badge and rationale text present in the inert page
  is present in the app page's source too.

## The page

### Overview

- **Counts** by evidence state crossed with verdict, over **active units
  only**. "Active only" follows `status`, which grades exactly the active set
  (`validated_memory/status.py:143-165`); the cross itself is new -- `status`
  counts verdicts, not the pairing. Superseded units are counted separately,
  as one number, not folded into the same table.
- **A map of the corpus**, which is a *navigation index* -- links to cards,
  not the cards themselves. This is what makes multi-valued grouping
  well-defined: a unit with anchors in three systems appears as a link in
  three groups, while its card still renders exactly once, so no id is
  duplicated and the existing single-render rule
  (`validated_memory/knowledge_view.py:61-71`) is untouched.
- **The unprobed queue**: anchors with no record under their current key
  `(unit, system, kind, payload)` -- the same key `verdicts.anchor_key` builds
  (`validated_memory/verdicts.py:68-93`). An anchor whose payload changed has
  no record under its new key and is unprobed, which is the honest reading.

The grouping axis is **always `anchors[].system`**. An earlier draft let a
single declared extension enum take the axis over; that is withdrawn.
Declaring a second enum is an additive schema change that does not even bump
the schema version (`docs/reference/curated-knowledge.md:128-133`), so the
page would silently reorganize itself from a semantic axis to the anchor
system the moment an adopter added an unrelated field. A page that rearranges
itself on a change nobody made to it is worse than a page grouped by a coarser
axis.

The rest of the rule is fixed so the output is a function of the corpus alone:

- Units with no anchors go to an explicit **unclassified** group. They are
  never dropped: a unit that cannot expire is a fact about the corpus.
- Several anchors on the same system count once for that unit.
- Groups are ordered by name and units within a group by id, both by plain
  codepoint sort. No locale, no collation.
- The map indexes **active units**. Superseded units stay reachable from the
  card of the unit that superseded them, which is where the view already
  nests them (`validated_memory/knowledge_view.py:61-71`).

This requires the `Extension` object to reach the view.
`validate.collect_and_validate` builds it and currently discards it
(`validated_memory/validate.py:33-52`); a normalized model carrying documents,
effective states, verdicts, the extension and the computed groups is built
once and passed to the renderer. The optional script consumes that same model
as rendered data attributes; it never re-interprets the corpus, so the two can
never disagree.

### Unit card

Fixed order: headline and id; badges for evidence state and aggregate verdict;
the freshness strip per anchor; the rationale diagram when the unit has one;
the supersession chain; `provenance`; the verbatim body.

The freshness strip keeps its exact semantics: a sequence of records in log
order, oldest to newest, **not** a time axis. `recorded_at` is not part of the
verdict log's required schema -- the log reader accepts it absent, null or of
any type (`validated_memory/verdicts.py:139-165`) -- so no chart may imply
distance in time between two records. (`status --max-verdict-age` does demand
it and reports absent, invalid or future values,
`validated_memory/status.py:179-216`; that is a gate on freshness policy, not
a guarantee the renderer can lean on.) The strip's right edge stays the last
record, never "now".

`knowledge-index.md` is unchanged: `derive` keeps emitting
`| id | state | evidence | verdict |` (`validated_memory/derive.py:192-205`).
Note that `derive.run` still gates on validation
(`validated_memory/derive.py:32-40`), so a malformed `rationale` blocks it
like any other ERROR.

## Diagrams

### The rationale diagram

One per unit that carries a `rationale`: the question, then the options, with
the chosen one distinguished. Fixed depth, no edges between options, no edge
leaving the unit. Size and edge count are linear in the number of options.
There is no global graph to draw, so there is no hairball to avoid.

### Two guarantees, not one

The design promises **byte determinism**, which is achievable, and does not
promise **visual fidelity across platforms**, which is not. An SVG `<text>`
does not wrap, and no layout computed without font metrics can simultaneously
guarantee "it fits", "nothing is truncated" and "it is legible" for arbitrary
adopter text -- CJK, emoji sequences, combining marks and RTL all break any
character-count estimate.

The fallback is explicit and deterministic:

- A label at or under 48 characters is drawn inline in its node.
- Above that, the node is drawn as a number, and the full text appears in the
  HTML list beside the diagram, which is where the complete text always lives
  anyway.
- The threshold is a character count, and character count is not width. That
  is precisely why the fallback exists rather than a cleverer estimate.
- Options are never omitted or silently truncated. With many options the
  renderer switches layout deterministically.
- Adopter text carries `dir="auto"` so RTL renders in its own direction
  without reordering anything around it.

### Rules every generated diagram obeys

These apply to all three diagrams. The two that exist today do not meet them
yet: `freshness_strip` has a `<title>` per band but no diagram-level `<desc>`
and distinguishes bands by colour alone, and `confluence` has neither
(`validated_memory/svg.py:30-77`). Bringing them up is part of this work, not
a later tidy-up.

- **Deterministic**: element ids derive from the unit id and the option's
  position, never from `hash()`, which is salted per process. No clock, no
  generation timestamp, no font metrics.
- **Inert**: no `href` of any kind inside the SVG, no `<use>`, no `<image>`.
- **Escaped**: every text node and attribute through `html.escape_text` /
  `html.escape_attribute`. An SVG with unescaped adopter text is an XSS
  surface, not a drawing.
- **Not colour-alone**: state differs in shape and in text, not only in fill.
  Every diagram carries `<title>` and `<desc>`.
- **Never load-bearing**: everything a diagram shows is also present as
  structured HTML.

## Styling

The CSS is hand-written and **split per page**. Today `html.page` inlines one
shared `STYLESHEET` (`validated_memory/html.py:10,51`) that both views use, so
restyling `knowledge.html` through it would silently restyle `memory.html`,
which this design says it leaves alone. `page` takes the stylesheet as an
argument; each view owns its own.

## Tests

TDD applies. Beyond the per-feature tests:

- **The self-containment helper is fixed, then split.** `_assert_self_contained`
  iterates attributes, so an element with **no attributes** never enters the
  loop: a bare `<script>` with inline content passes it today
  (`tests/test_render.py:131-158`). It becomes an element-and-attribute
  whitelist. `knowledge.html` and `memory.html` keep the strict policy;
  `knowledge-app.html` gets its own whitelist that admits exactly one
  attribute-less `<script>` and nothing else.
- **Progressive enhancement, proved by construction.** The renderer builds
  the inert document once and inserts the script into that exact byte
  sequence; nothing is rendered twice. The test removes the single `<script>`
  element from `knowledge-app.html` and asserts the remainder equals
  `knowledge.html` **byte for byte**. Asserting that some strings appear in
  both pages would pass while bodies, anchors, provenance or supersession
  chains were missing, or while the text lived only in a hidden attribute.
- **The app page's script policy**: exactly one `<script>`, inline, no `src`.
  The forbidden-API scan over the script source is a lint and is described as
  one -- it cannot prove the absence of an indirect call. What constrains the
  page at runtime is a Content-Security-Policy `<meta>`, `default-src 'none';
  connect-src 'none'; img-src 'none'` with inline style and script allowed:
  the one `http-equiv` the app page's whitelist admits, and which the strict
  whitelist still forbids.
- **A documentation-sync test.** Five places enumerate the contract by hand --
  `README.md`, `docs/reference/curated-knowledge.md`, `docs/walkthrough.md`,
  `skills/create-knowledge-unit/SKILL.md` and the `EXTENSION_STUB` at
  `validated_memory/init.py:86-87` -- with nothing tying any of them to
  `contract.py`. They are not homogeneous: the README and the walkthrough hold
  valid examples that legitimately omit optional fields, so equality against
  `BASE_FIELDS` is the wrong test. The canonical enumerations are marked with
  an explicit comment marker, and only the marked blocks are compared.
- **Determinism re-pinned** over both artifacts: a second run reports
  `unchanged`, leaves the bytes identical and `st_mtime_ns` untouched.
- **Adversarial content** through `question`, `label` and `reason`: nothing
  lands live in the HTML, in the SVG, or inside the app page's script.
- **The unquoted-scalar case**: a unit whose `reason` is an unquoted scalar,
  with and without a `#`, is an ERROR from the CLI -- the rule is pinned where
  it is enforced, not only in prose.
- **The adoption twin lists.** `knowledge-app.html` is a new root artifact, so
  it enters the skill's ignore list, the adoption guide's copy of it, and the
  versioning decision, per ADR 0007. The existing pins compare the skill's
  list against exactly what the CLI creates at the root and against the
  guide's copy (`tests/test_adoption_decisions.py:116-125`); both go red until
  all three agree, which is the point of having them.

## Versioning and release

This is **2.0.0**: one commit where `pyproject.toml`,
`validated_memory/__init__.py` and `.claude-plugin/plugin.json` agree with the
tag, pushed to both remotes (ADR 0005).

A `v2` channel starts and `v1` stops being re-pointed. `CONTRIBUTING.md:39-51`
currently instructs re-pointing `v1` at every release and says "Only `v1`
moves"; it is updated in this change, or the release procedure contradicts
itself.

Migration facts for the release notes:

- **A corpus with no colliding extension field keeps validating.**
  `rationale` is optional and additive on the data side.
- **The break runs the other way.** A unit carrying `rationale` is an unknown
  field, and an ERROR, for any 1.x reader
  (`validated_memory/contract.py:70-79`). CLI, CI, the Action and the hooks
  must be on 2.x before the first `rationale` is written.
- **An adopter whose extension already declares `rationale`** has a hard
  migration, and it is stated plainly rather than glossed: their schema stops
  loading (`validated_memory/extension.py:116-125`), and renaming the
  declaration is not enough, because their historical units still carry the
  key and the new base check rejects the value. They must rename the key in
  the schema **and** in every unit that used it. That is a mechanical rename
  forced by a major version, not a content correction, and it is the one
  documented exception to "units already written are never rewritten"
  (`docs/reference/curated-knowledge.md:128-133`). Renaming the field also
  **narrows** their schema, which by their own contract bumps
  `extension.version` -- but that bump is a record, not a mechanism: `load`
  reads `schema` and never acts on `version`
  (`validated_memory/extension.py:64-73`), so nothing migrates on their
  behalf.

There is no mass backfill. Nothing can reconstruct what was considered and
why, and inferring it from bodies, commits or supersession would fabricate the
very record this field exists to make trustworthy.

## ADRs

- **Rationale is local structured metadata on a unit.** The closed envelope,
  the single-choice rule, the absence of references, and that `rejected` is
  neither false nor superseded.
- **The canonical view is inert; interaction is a second artifact.**
  `knowledge.html` never carries a script; `knowledge-app.html` exists only
  where the adopter created it, adds no content, and is activated by presence
  like the views themselves.
- **The plugin ships no third-party runtime code.** Diagrams are generated,
  CSS and script are ours, and nothing is fetched at render time or at read
  time.
- **A major channel per major version.** ADR 0005 names `v1` as *the* moving
  convenience channel and says "Only `v1` moves"
  (`docs/adr/0005-a-release-is-one-commit-where-the-three-versions-agree.md:22-25`,
  `CONTRIBUTING.md:39-51`). Opening `v2` generalizes that decision, so it is
  recorded as an ADR that supersedes ADR 0005 on this point alone -- not as an
  edit to `CONTRIBUTING.md` that leaves the ADR saying something else. The
  README and its tests still recommend `@v1` and are updated with it.

## Rejected alternatives

| Alternative | Why not |
|---|---|
| Typed relations between units | Cannot express an option that never became a unit, nor why it was discarded, without giving that option an identity, an evidence state and possibly anchors -- which is where this becomes a decision manager. Each relation carries its own cycle semantics, and the union with `supersedes` raises questions nothing asks yet. |
| `basis` references from an option to units | Reintroduces edge resolution, self-reference, justification cycles and expansion limits, to serve a view. |
| Keeping the name `decision` | More likely to collide with an adopter's declared extension field, and a collision costs them a rename across every historical unit. |
| A separate key naming the chosen option | Two representations of one fact, free to disagree. |
| Identifiers on options | Makes an option look referenceable, supersedable and probeable. It is none of those. |
| Solving this through the adopter's declared extension | The mechanism carries `string` and `enum` only, cannot express a closed mapping or "exactly one chosen", and extension fields are permitted, never required. A renderer that knows about an extension field called `rationale` has made it a hidden part of the base contract, owned by nobody. |
| One artifact with two profiles | The same filename would carry different guarantees depending on a setting, and the inert artifact would stop existing exactly when someone enables the app. |
| A profile manifest in the adopter project | A new configuration surface with no home: `validated-memory.md` is closed and an unknown key there gates every consumer. Presence of the file already answers the question. |
| Tailwind and Mermaid from a CDN | The page stops opening without network, stops surviving as an attachment, and byte determinism stops meaning anything: the same bytes can render differently, or blank, depending on what the CDN serves. |
| Tailwind compiled at release, output vendored | Adds Node, a lockfile, a staleness check and a licensing surface to a repository whose distribution model is "the checkout is the program", to produce CSS we can write ourselves. |
| Third-party assets installed into the adopter | Hashes, versions, licences and an install step, for search and pan/zoom. |
| Widening the frontmatter subset for multi-line reasons | A decision about the whole subset, not a need of this model. |

## Risks

- Reasons and rejected alternatives end up in versioned Markdown and in an
  HTML file designed to be sent to third parties. Nothing sensitive belongs in
  them, and the documentation says so where the field is introduced.
- Validation guarantees shape, not honesty or sufficiency.
- The model has no pending decision and no multi-select. A composite option
  covers simple cases; a real need for either is another contract revision.
- A rationale can go stale while its claim stays current: anchors prove the
  claim, not that the reasoning still holds.
- The quoting rule is enforceable by test only for what reaches the parser
  intact. An unquoted reason truncated at ` #` is lost before validation; the
  skill that writes units is the real guard, and the test pins the behaviour
  so nobody rediscovers it by accident.
- Two artifacts mean two pages to keep in step. The progressive-enhancement
  test is what stops them diverging in content; nothing stops them diverging
  in style except keeping one stylesheet per page and using it in both.

## Sequencing

Each step leaves the suite green and the repository coherent.

1. **ADRs first**, then `rationale` in the contract with `_check_rationale`,
   the quoting rule, and the five documentation blocks updated **in the same
   change**. A contract field and its public documentation are one unit of
   work; splitting them was the previous plan's mistake.
2. The documentation-sync test, with its markers.
3. The normalized model: documents, states, verdicts, extension and groups
   built once and handed to the view. No visual change.
4. `page` takes a stylesheet argument; `knowledge.html` and `memory.html` get
   their own. No visual change to `memory.html`.
5. The self-containment helper becomes an element-and-attribute whitelist.
   This lands before any new markup, so new elements are gated from the first
   commit that emits them.
6. The page: overview, grouping index, cards, hand-written CSS.
7. The diagrams: the rationale diagram, and the two existing ones brought up
   to the shared rules.
8. `knowledge-app.html`: the script, its whitelist, the enhancement test, and
   the adoption question that creates the file.
9. Adoption: the `--app` item in `init --view`, the skill's question, and the
   three twin lists ADR 0007 requires.
10. Reference documentation, the `v2` channel ADR, `CONTRIBUTING.md`'s release
    procedure, the README's pinning guidance, and the 2.0.0 release.
