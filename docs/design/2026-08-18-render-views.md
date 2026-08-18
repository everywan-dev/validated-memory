# Design: `render`, HTML views of validated memory

Target version: 1.1.0. Status: approved, not implemented.

## Purpose

An adopter accumulates two layers of Markdown that the plugin can already
enforce, index and probe. Nothing yet lets a person *read* them: which
conclusions hold today, what each one replaced, how it is proven, and whether
it is still fresh. `render` writes static HTML views that carry that story to
someone who has neither the repository, nor the plugin, nor Python.

The reader this is designed for is a third party. They should be able to open
one file, see the conclusions concentrated to one line each, and expand any of
them to follow how it was reached and go verify it themselves.

## Scope

`render` renders **only what the contract already records**. It does not
invent structure the data does not carry.

Explicitly rejected, and not to be reintroduced without a new design:

- New relations between units (`derives_from`, `depends_on`, dependency
  graphs). The corpus records exactly one relation, `supersedes`.
- A decision or ADR unit type with considered options and consequences.
- Parsing conventional sections (`Context`, `Options`, `Consequences`) out of
  unit bodies.

A drawn decision tree would be a picture of data that does not exist. What
does exist -- a chain of supersessions whose evidence state strengthens over
time, with a freshness verdict per anchor -- is a real account of how a
conclusion was reached, and is what the views render.

## Data sources

`render` never re-implements a reader:

| Material | Source |
|----------|--------|
| Units, contract plus declared extension | the loader `validate` and `derive` already use |
| Effective state (`active`, `superseded by ...`) | `derive.effective_states(documents)` |
| Per-unit verdict grading | `derive.unit_verdict(unit_id, anchors, view)` -> `UnitVerdict(verdict, unknown_systems, per_anchor)`, with an `AnchorVerdict(system, kind, verdict)` per anchor |
| Verdict history | `verdicts.history(root)`, added on this branch (see below) |
| Memory entries, resolution, supersession | `validated_memory/memory.py`: `documents`, `filename`, `index_entries`, `body`, `wikilinks`, `supersession`, `resolution` -> `Resolution(by_name, by_filename)`, `filename_hint`, `is_declared` |

Two shapes of that module are load-bearing for the views. `index_entries`
strips the whitespace around the `href` and nothing else -- that stripping
belongs to the reader so two consumers cannot do it differently -- while
turning `./x.md` and `x.md` into one key is the consumer's job, done as `lint`
and `adopt` do it, with `PurePosixPath(href).as_posix()`. And what counts as a
declared name is decided by `memory.is_declared(value)`, which exists once on
purpose.

One limit of those readers is part of this design rather than hidden by it.

**There is no `gated_source` for the memory layer.** `memory.py` reads; every
rule lives in private functions of `lint`. The views therefore **do not
enforce**: `render` gates only on what it must read -- the memory directory
and its index exist, and a document's frontmatter parses -- and everything
else stays `lint`'s business. A memory file with no index entry is still
rendered, an unresolved reference is marked as unresolved, and a document
whose frontmatter will not parse is rendered with that said in place of its
fields.

That makes the memory view the one place where **a frontmatter value can be
any JSON type at all**, because nothing validated it first: a `description`
that is a list, a `metadata.type` that is a mapping, a `name` that is a
number. Every value reaching the page goes through escaping that stringifies
first, and no membership test, sort or `.strip()` is applied to a value
without checking its type. A view that raises `TypeError` shows a traceback
where a page should be. Hiding a record because `lint` would complain about it would make the
view lie about what the repository holds; `lint` is one command away and is
the authority.

This is a correctness rule, not a style preference. `verdicts.service_view()`
is fail-loud: it raises `VerdictLogError` with a line number on a log it
cannot read, and `derive` reports that as an ERROR. A view that parsed the
log itself would silently accept records `derive` rejects, and would show a
third party verdicts computed over skipped lines. Likewise a view that graded
a unit's freshness with its own rule would disagree with `knowledge-index.md`
about the same unit. A view that contradicts the enforcement is worse than no
view.

`verdicts.history()` does not exist yet and is written on this branch, with
`render` as the consumer that exercises it -- the testing seam runs the CLI as
a subprocess, so a reader with no subcommand behind it could not be tested
today. Its contract:

- The shared loop already exists: `verdicts.records(root)` is public, yields
  `(lineno, record)`, opens the file inside its `try` and raises
  `VerdictLogError` for a file it cannot read or decode and for every line
  that is not a JSON object. `service_view` is built on it. So `history()` is
  built on it too and is one line -- **writing a second loop now would be
  worse than before, because there would be two readers where there is one
  good one.**
- `VerdictLogError.lineno` is `None` when the fault is the file's rather than
  a line's. `Finding.render()` already omits the `:N` in that case, and the
  tests must cover both shapes.
- It returns records in file order, uncollapsed. The twenty-record window is
  applied by the renderer, not by the reader.

## Artifacts

Two files, written to the working directory, alongside `knowledge-index.md`
and `verdicts.jsonl` and never inside `knowledge/`:

- `knowledge.html` -- the curated layer.
- `memory.html` -- the agent-memory layer.

Two rather than one because the glossary refuses an umbrella term for the two
layers: they share neither frontmatter, nor relations, nor the way each stops
being true. A single file would need a name, and every available name either
claims to be one layer while containing both, or is the umbrella term itself.
Two names that are each honest make the separation structural instead of a
convention of layout. Each file stays self-contained and attachable on its
own; `memory.html` is small.

## CLI surface

```
python3 -m validated_memory render [--only-existing]
```

Reads `knowledge/` and `memory/` relative to the working directory. Validates
before rendering, exactly as `derive` does: an ERROR finding is reported in
`validate`'s format and stops the run with nothing written. WARNING findings
do not block.

Each artifact is built entirely in memory and written in one operation, so a
validation ERROR or a failure mid-render can never leave a half-written page
on disk for a reader to open.

Reports one line per artifact, in `init`'s idiom:

```
render: wrote knowledge.html
render: unchanged memory.html
```

**A file whose content is identical is not rewritten.** Without this the
startup hook dirties `git status` on every session start, forever, in a
repository that treats that churn as a defect.

`--only-existing` regenerates only artifacts that already exist and creates
none. It is what the hook invokes, and it is what makes activation and
deactivation mean something.

**`--only-existing` is fail-open, and does not regenerate**: an invalid corpus
is a WARNING and exit 0, and the artifact already on disk is left exactly as
it was. Publishing a page built on data the enforcement rejects is worse than
showing an older page, and the hook would do it unattended on every session
start. It is the unattended mode, and an ERROR there would mean a
failure reported on every session start until someone fixes the corpus. Run
explicitly, without the flag, the same invalid corpus is an ERROR that gates
-- a person asking for the views is entitled to be told they were not built.
This is the discipline the existing startup hook already follows.

Exit codes follow the repository convention: `0` clean or WARNING-only, `1`
an ERROR finding, `2` a usage error.

Both layers are required, because `init` scaffolds both: a missing `memory/`
or a missing `memory/MEMORY.md` stops the run exactly as it stops `lint`, and
a missing `knowledge/` stops it exactly as it stops `validate`. `render` does
not render half a project quietly.

Every page declares `<meta charset="utf-8">` and is written as UTF-8: the
corpus is prose in whatever language the adopter writes, and a view that
mangles accented text on a double click is not readable.

## `knowledge.html`

A header carrying the recount basis -- how many units, under which path, in
`derive`'s idiom -- and the history-window disclosure: how many records the
log holds in total, that at most twenty are shown per anchor, and where the
log is. Because the log outlives the corpus -- nothing prunes records whose
unit, system or anchor no longer exists -- the header states two totals rather
than one: how many records the log holds, and how many of them belong to an
anchor shown on this page. Otherwise the reader cannot reconcile the total
with the histories in front of them, and is left to guess where the difference
went.

Each anchor's own history repeats the disclosure for itself: how many records
that `(unit, system, kind, payload)` has, and how many of them are shown.

Then one entry per **live conclusion**, ordered by `id`. Ordering by `id` is
the only order that does not move on its own: any ordering by freshness or
recency is reshuffled by a routine probe, and the hook would rewrite the whole
file on the next session.

Collapsed, an entry is one line: headline, `id`, evidence state, graded
verdict. Expanded, it carries:

- the body, verbatim and escaped;
- each anchor's envelope: `system`, `kind`, `captured_at`, `payload`;
- provenance;
- the anchor's probe history, within the declared window;
- **the chain backwards**: every unit this one supersedes, collapsed, each
  carrying the same contents, nested as deeply as the chain runs. The walk is
  **iterative, with an explicit stack**, not recursive: a chain's length is
  written by people and has no bound, and `validate`'s own cycle detection is
  iterative for the same reason.

Nothing superseded appears at the top level; it appears inside the history of
whatever replaced it. Because two units may both supersede the same one, the
second appearance is an internal reference to the first, not a copy.

### Why there is no "unreachable units" section

An earlier draft ended the page with a section for every unit no live
conclusion reaches, because a supersession cycle validated cleanly, left both
units marked superseded, and would have dropped them from the page in silence.

`validate` now rejects a cycle as an ERROR. That closes the hole at the
source, and it closes this one too: `render` validates before rendering, so
no rendering path can see a cycle, and `--only-existing` does not regenerate
an invalid corpus either. With cycles rejected the supersession graph is a
DAG, and in a finite DAG every unit reaches, backwards, a unit with no
incoming edge -- an active one. The unreachable set is empty by construction,
not merely improbable, so the section would have been code no test could
exercise. It is recorded here so nobody designs it twice.

What remains is the structural invariant the tests assert: the number of unit
sections equals the number of units. That is what would catch a regression,
without carrying dead code to do it.

### Headline

The contract has no title field. The headline is the first heading of the
body, falling back to the `id`.

**This is the boundary, and it is closed.** Extracting one line by a
documented rule is not rendering the body; taking "and the first paragraph
too" would be, and would reintroduce exactly what the scope section rejects.
The rule is to be stated in the code at the point where it is implemented.

## `memory.html`

One entry per memory file, ordered by filename, with its `description`, its
`metadata.type` and its body verbatim. A superseded entry is marked and links
to its successor.

The wikilink graph is **not drawn**. A real corpus of this shape runs to some
200 links across 110 entries; drawn, it is a hairball nobody reads anything
out of. What serves the reader is navigation: each entry lists its outgoing
references, resolved and unresolved, and the entries that reference it. That
is the graph, walkable entry by entry, with no JavaScript.

Wikilinks inside a body are **not** turned into links: the body is verbatim,
and linkifying it would be rendering it. Navigation lives in the reference
lists, which are derived from the same resolution model `lint` uses -- so the
view resolves a wikilink exactly as `lint` does, including the filename
identity rule of ADR 0001.

## Bodies are verbatim

Unit and memory bodies are shown escaped and unrendered, in a monospaced
block.

The rejected alternative was a closed, documented Markdown subset, by analogy
with `frontmatter.py`. The analogy fails on the failure mode, which is what
decides it: `frontmatter.py` rejects what it does not recognise, and the
author is told to fix the document. A renderer cannot refuse to show a valid
unit, so it must degrade silently -- the same design with the failure mode
inverted. The result is a page where some emphasis renders and some does not,
depending on which construct fell outside the subset, in front of a reader
who has no way to know why.

The maintenance asymmetry follows. A module that claims to render Markdown
receives "nested lists don't work", "tables don't work", "reference links
don't work", each a legitimate complaint against something claiming to render
Markdown, and the series has no natural end because the corpus is Markdown
written by people who do not know the subset. A verbatim block claims
nothing.

What carries the argument is not the prose anyway: it is the supersession
chain, the evidence states, the dates and the verdicts, all of which come
from frontmatter and from the log, and all of which *are* rendered as real
HTML.

If a real corpus later shows verbatim getting in the way, that is evidence,
and the decision is revisited with it in hand. It is not built today on the
hypothesis that it will be needed.

## Escaping and self-containment

Every piece of text from the repository passes through `html.escape`, bodies
included. A `<pre>` block does not escape anything by itself; a `<script>`
inside an unescaped `<pre>` is live markup. Escape first, build markup after,
never the reverse.

CSS is inline, with a system font stack. There is no JavaScript at all:
collapsing uses the browser's native mechanism. The file is inert -- it
prints, it survives a mail gateway, and nobody has to trust an attachment
that carries code. The cost, accepted deliberately, is that the views have no
search or filter of their own.

**Self-containment is about resource loading, not about URL strings.**
Provenance is rendered as a clickable link: it exists so a third party can go
and check the claim, and making them copy a URL by hand degrades it for
nothing. An outgoing link loads nothing when the page is opened.

Only `http://` and `https://` become links. The contract requires
`provenance` to be a list and validates nothing about its elements, so a
valid unit can carry `javascript:alert(1)`, a `data:` URI, or a mapping
instead of a string. `html.escape` does not neutralise a scheme -- it is the
link that would arm it -- so the scheme allowlist is where that is stopped,
and everything outside it is rendered as escaped text.

The rule is stated as a **whitelist over the parsed document**, not as a
blacklist of substrings:

> The only attribute anywhere in the page that carries an external URL is
> `href`, and only on an `<a>` element.

A substring blacklist leaks, and the leaks are not exotic:
`<meta http-equiv="refresh">` navigates on open, `<base href>` rewrites every
relative URL on the page, `<form action>` submits, `srcset` does not contain
`src=`, and `<object>`, `<embed>`, `<video>`, `<audio>`, `<source>` and
`<track>` all fetch. The one that bites this design directly is SVG: inside
`<use>` and `<image>`, `href` (and `xlink:href`) **loads** rather than links,
so "an `href` is fine" applied blindly would let it through. The page is
parsed for the structure test anyway, which gives element and attribute, so
the whitelist costs nothing and closes the cases nobody thought of.

Two further checks: no `<a>` carries `ping` (it fires a request on click),
and any `<a target="_blank">` carries `rel="noopener noreferrer"` -- without
it the destination can reach back through `window.opener`, and this file is
built to be mailed to strangers.

## Diagrams

Only two, both inline SVG, both generated deterministically:

- **The freshness strip** of an anchor: one band per probe, in log order,
  coloured by verdict. This shows what text does not -- a run of `current`
  ending in a switch to `drifted`, and how long it has stayed there.
  **Its right edge is the last record, never "now"**: an edge at "now" would
  change the SVG on every regeneration and bring the churn back.

  The strip is ordered by **position in the log, not by timestamp**. The log
  is append-only, so file order is chronological, and the verdict parser
  requires only `unit`, `system`, `kind` and `verdict` -- `recorded_at` is
  what `probe` happens to write, not something any reader can demand. A strip
  laid out on timestamps would break on a record the enforcement accepts.
  Each band is labelled with its `recorded_at` when there is one.
- **Many-to-one confluence**, when three or more units are superseded at once
  by a single one. With two links in a chain nothing is drawn: two boxes and
  an arrow take half a screen to say what one line of text says better.

Colour is never the only channel: anything distinguished by colour also
carries its label, for colour-blind readers and for black-and-white printing.

## History window

Twenty probe records per `(unit, system, kind, payload)`, most recent first.

An anchor's identity is **what it points at**: `(system, kind, payload)`.
`captured_at` dates it, it does not identify it. Two anchors of one unit can
legitimately share a `system` and a `kind` -- two refs of the same repository
are `system: repo-a, kind: git_ref` twice, differing only in `payload.ref` --
so a key without the payload merges two anchors that measure different things.
That was not a cosmetic limit: `probe` detected one anchor's drift, wrote it
to the log, and the service view collapsed it onto its sibling and kept
whichever was written last, so the index reported `current` for a unit with a
drifted anchor, order-dependently. The key now carries the canonical payload,
and each anchor has its own history.

Records written before that change carry no payload. They stay in the log --
history is not rewritten -- and **no anchor reads them, without exception**.
The anchor reads `unknown` until the next probe, which repairs it by itself.

Reading them when the `(system, kind)` happened to be unique was considered
and rejected: uniqueness settles *which* anchor a record belongs to, not
*what it measured*. An anchor gets recaptured -- that is what `captured_at`
is for -- so its payload today need not be its payload then, and serving that
record would report `current` for something already drifted. That is the
false "still current" the payload exists to prevent, reintroduced quietly.

Two consequences the view inherits. An explicit `payload: null` is a
malformed record, not an old one: presence of the field is what is checked,
so `render` reports it as it reports any other unreadable record. And the
payload is compared exactly as the frontmatter parser produced it -- every
scalar is a string, no type is inferred -- so the view shows it as it is and
normalises nothing.

Because the payload is what distinguishes two anchors, the view shows it
beside each history: a record can finally say what it measured.

The key includes the unit: `verdicts.service_view()` builds it as
`(record["unit"], record["system"], record["kind"])`, and `unit_verdict`
looks it up the same way. Two units anchored on the same system and kind have
separate histories, and a window keyed on the pair alone would blend them.

`verdicts.jsonl` is append-only and grows without bound by design, so
embedding the full history would make the artifact grow monotonically until
"you can send it by email" quietly stops being true -- in the project with the
most history, which is the one with the most to show. The window is fixed
rather than configurable until there is a reason for it not to be.

The page states how many records exist, how many are shown, and where the log
is. A view that truncates in silence lies by omission, which is the failure
this project exists to prevent.

## Activation and the startup hook

Activation is **the presence of the artifact**, not a configuration key.

`init --view` creates the two artifacts if they are missing and reports
`created` / `kept` per item. It **never regenerates an existing one**:
`init`'s documented contract is that an existing item, including one edited by
hand, is never touched, and a generator inside `init` would break that
contract in the command that defines it. Regeneration belongs to `render` and
to the hook. If the corpus is not valid, `init --view` creates nothing and
reports a WARNING, without gating -- the same fail-open behaviour
`--harness-memory` already has.

Deactivating is deleting the file. Reactivating is `init --view`, which is
the same idiom the README already uses for restoring the memory symlink after
a rename or a re-clone.

A configuration key was rejected: an unknown field in `validated-memory.md`
is an ERROR that gates *every* subcommand (`extension.py`, `CONFIG_FIELDS`),
so an adopter who wrote a `view` key and then worked on a machine with an
older plugin would find `validate`, `derive` and `probe` all dead -- not the
view disabled. (`lint` survives: `lint.run` never loads the configuration.
Three of the five subcommands, including every gate over curated knowledge, is
still bricking a project to toggle a view.) Presence-based activation needs no change to
`CONFIG_FIELDS` and cannot break an older plugin, which sees an `.html` file
it does not understand and ignores it.

Neither artifact is added to `.gitignore`. Derived artifacts land in the
working directory and the adopter decides whether to version them, exactly as
with `knowledge-index.md` and `verdicts.jsonl`. An adopter who versions them
has the views active on a fresh clone; one who does not reactivates with
`init --view`.

A new hook script, `hooks/refresh-views.sh`, is added as a second entry under
the same `SessionStart` in `hooks/hooks.json`. The existing
`restore-memory-symlink.sh` is not extended: its contract turns on "never
loses data", and a generator writes; two contracts in one script make review
impossible. `tests/test_hooks_manifest.py` iterates the entries and asserts
that one of them references the symlink script, so a second entry passes as
written -- verified, not assumed.

The new hook is fail-open throughout and always exits 0: no
`$CLAUDE_PROJECT_DIR`, a project that has not adopted, no `python3`, or any
other problem is a clean no-op. It invokes `render --only-existing`, so an
adopter who never activated the views pays nothing at session start.

`render --check`, by analogy with `derive --check`, is deliberately out of
1.1.0. The index is a contract artifact others read as truth; the views are
presentation.

## Tests

End-to-end, invoking the CLI as a subprocess over fixture adopter trees. The
seam is that no test imports the package's internals; reading files the plugin
ships -- the hook script, `hooks/hooks.json` -- is reading data, which
`tests/test_skills_structure.py` and `tests/test_hooks_manifest.py` already do
and document as such.

One invariant is not observable from outside, and is not pretended to be: that
`history()` and `service_view()` run the *same* parsing loop is a review
invariant. The tests assert the observable half -- that both reject the same
logs, with the same line numbers.

- `render` on a fixture corpus writes both artifacts; a second run reports
  `unchanged`, leaves the bytes identical **and leaves `st_mtime_ns`
  untouched**. Identical bytes alone would pass an implementation that
  rewrites the same content and prints `unchanged`, which is the thing this
  rule exists to forbid.
- A supersession cycle renders every unit involved, under the section for
  units no live conclusion reaches, and warns without gating.
- A unit with two anchors on the same `(system, kind)` shows one history,
  marked as shared.
- A `provenance` entry with a `javascript:` scheme is rendered as text, never
  as a link.
- An ERROR-level contract finding stops the run and writes nothing.
- An unreadable `verdicts.jsonl` fails the same way `derive` fails on it.
- Structure, parsed with `html.parser` from the standard library: the number
  of unit sections equals the number of units, every superseded unit appears
  and is marked as such, no `id` is missing. Substring assertions alone pass
  over malformed HTML, so structure is asserted structurally.
- Self-containment, asserted as the whitelist above over the parsed
  document: the only attribute carrying an external URL is `href` on an `<a>`
  element -- checked by walking every element and attribute, never by
  searching for substrings. No `<a>` carries `ping`, and any
  `target="_blank"` carries `rel="noopener noreferrer"`. A fixture whose
  provenance is an external URL proves the allowed case renders as a link.
- **Escaping**, with a hostile fixture: a unit body containing
  `<script>alert(1)</script>`, a `description` containing quotes and angle
  brackets, a memory entry with markup in its body. None of it becomes live
  markup. A file built to be emailed to a third party that renders repository
  content unescaped is a hole, not a detail.
- The history window: with more than twenty records for one `(unit, system, kind, payload)`,
  twenty are shown and the page states the true total.
- Determinism: two runs over an unchanged corpus produce identical bytes, and
  the output contains no generation timestamp.
- `init --view` creates both artifacts once and reports `kept` on a second
  run without rewriting them; on an invalid corpus it creates nothing, warns,
  and does not gate.
- `--only-existing` over an invalid corpus warns and exits 0, while the same
  corpus rendered explicitly exits 1.
- The new hook script exists, starts with `#!/bin/bash`, and is referenced
  from `SessionStart` in `hooks/hooks.json`. `test_hooks_manifest.py` covers
  the existing script only; the new one gets the same safety net.

## Dependencies and sequencing

The shared read model landed on `main` at `6ec754f`: `validated_memory/memory.py`
carries the memory-layer reading (collection, parsing, `Resolution`,
supersession) with `lint` left as the rules on top, and `derive.unit_verdict`
is public. `feature/render-views` is rebased onto it.

What remains on this branch, before any rendering code: `verdicts.history()`,
under the contract stated in "Data sources" above. It is the one reader the
views still need and the only reason to touch `verdicts.py`.

Implementation follows test-first.

One ordering constraint is load-bearing. `tests/test_skills_structure.py` now
walks `docs/` recursively and asserts that every literal
`python3 -m validated_memory <word>` in skills, docs or the README names a
real subcommand. This document contains that literal for `render`, so **the
first implementation commit must register `render` in `cli.SUBCOMMANDS` and
in the test's `REAL_SUBCOMMANDS`**. This is not a prediction: rebased onto
`main` at `6ec754f`, the suite on this branch is
`1 failed, 237 passed`, and the failure is exactly this document naming
`render`. Clear it before the first test-first cycle, or an inherited red is
indistinguishable from the red a new test is supposed to produce. The same test's clean-room check also scans this
document for mentions of the internal projects the method was studied on.

The version moves to 1.1.0 in the three places that must agree
(`pyproject.toml`, `.claude-plugin/plugin.json`, `validated_memory/__init__.py`),
which a test enforces.

Documentation to update on implementation: the README (a `render` section
under the CLI, and the startup-hook section for the second hook),
`docs/adoption.md` for the adopter-facing summary, and the
`adopt-validated-memory` skill for the activation step.

## Rejected alternatives

| Alternative | Why not |
|-------------|---------|
| A new relation (`derives_from`) or a decision unit type | Changes the contract for every adopter to serve a view; the declared extension cannot express relations today (`string` and `enum` only) |
| Parsing conventional body sections | Nothing validates them, so a badly written unit silently produces a wrong diagram |
| One HTML file for both layers | Every available name is either false or the umbrella term the glossary refuses |
| A Markdown subset renderer | Degrades silently on what it does not recognise, producing incoherent output within one page, and attracts an unbounded series of legitimate complaints |
| A `view` key in `validated-memory.md` | An unknown key there gates every subcommand, so an older plugin bricks the project rather than disabling the view |
| Extending `restore-memory-symlink.sh` | Mixes "never loses data" with "writes files" in one script |
| JavaScript for search and filters | Ends the inert-attachment property, which is what survives a mail gateway |
| Ordering by freshness or recency | A routine probe reshuffles the page, so the hook rewrites the whole file every session |
| Full probe history in the artifact | Grows without bound until the file stops being sendable, with no warning |
| A drawn wikilink graph | 200 links across 110 entries is a hairball; reference lists are walkable |
| `render --check` in 1.1.0 | The index is a contract artifact; the views are presentation |
