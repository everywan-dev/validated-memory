# A Legible `knowledge.html` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `knowledge.html` from a transcript into a page a reader can
navigate -- an overview, a map of the corpus, cards with badges, and three
generated diagrams including the rationale one -- without weakening a single
guarantee the artifact has today.

**Architecture:** One normalized reading of the corpus (`corpus.py`) is built
once from the validated documents, the verdict log and the adopter's declared
extension, and every part of the page reads it, so the overview, the map and a
card can never disagree. The page shell (`html.page`) takes its stylesheet as
an argument, so each view owns its own hand-written CSS; the overview lives in
its own module; the diagrams stay in `svg.py`, where one shared shell gives all
three a `<title>`, a `<desc>` and the same determinism and escaping rules.

**Tech Stack:** Python 3.11+, standard library only. pytest is the only
development dependency. The CLI is exercised as a subprocess; tests never
import the package's internals.

**Spec:** [`docs/design/2026-08-25-knowledge-view-decision-rationale.md`](../design/2026-08-25-knowledge-view-decision-rationale.md)

**Issue:** https://github.com/everywan-dev/validated-memory/issues/2

## Global Constraints

- Runtime code is Python 3, standard library only. pytest is the only
  development dependency.
- All content in this repository -- code, comments, CLI messages, docs, skills
  -- is written in English.
- Exit codes: `0` clean or WARNING-only, `1` ERROR, `2` usage error.
- Tests invoke the CLI as a subprocess over fixture adopter trees and never
  import the package's internals.
- Commits are Conventional Commits, in English.
- Every task ends with the full suite green: `python3 -m pytest`. The suite is
  401 tests before this plan starts.
- This plan does **not** bump the version. The three version files move
  together in the release plan, per ADR 0005.
- **The `/tmp` verification blocks in Tasks 2 and 4 are load-bearing, not
  optional.** They are how byte invariance is checked where the suite cannot
  check it. `memory.html` is pinned in the suite; `knowledge.html` is not,
  because it changes deliberately in five of these tasks, so for the two
  tasks that must not move it the capture-implement-diff sequence is the only
  thing between a refactor and a silent change to a published artifact. A
  task whose `diff` is not empty is not done.

The spec's requirements for the page, which every task below implicitly
carries:

- "**`knowledge.html`** -- always. Complete content, full styling, generated
  diagrams, and **inert**: no script of any kind, no request to the network.
  Every guarantee it has today it keeps, unchanged: byte determinism, no
  generation timestamp, atomic write, untouched `st_mtime_ns` on a no-op run,
  and the self-containment whitelist."
- "The interaction layer is **ours**: a few hundred lines of vanilla
  JavaScript in this repository, inlined at render time. No Mermaid -- the
  diagrams are generated as SVG in Python. No Tailwind -- the CSS is written by
  hand. And therefore: no CDN, no downloaded assets, no asset manifest, no
  hashes to verify, no third-party licences inside an adopter's repository, no
  Node in CI, and no change to the rule that pytest is this project's only
  development dependency."
- Rules every generated diagram obeys:
  - "**Deterministic**: element ids derive from the unit id and the option's
    position, never from `hash()`, which is salted per process. No clock, no
    generation timestamp, no font metrics."
  - "**Inert**: no `href` of any kind inside the SVG, no `<use>`, no
    `<image>`."
  - "**Escaped**: every text node and attribute through `html.escape_text` /
    `html.escape_attribute`. An SVG with unescaped adopter text is an XSS
    surface, not a drawing."
  - "**Not colour-alone**: state differs in shape and in text, not only in
    fill. Every diagram carries `<title>` and `<desc>`."
  - "**Never load-bearing**: everything a diagram shows is also present as
    structured HTML."
- The two guarantees, and only two: "The design promises **byte determinism**,
  which is achievable, and does not promise **visual fidelity across
  platforms**, which is not."
- The self-containment whitelist is the gate: no element and no attribute
  reaches either page without being added to it, deliberately, in the same
  commit that emits it.

## Plan set

This is plan 2 of 3. Each produces working, testable software on its own.

1. **The `rationale` field** -- accepted, validated, documented. Merged
   (`docs/plans/2026-08-25-rationale-contract.md`).
2. **This plan** -- the page, in nine tasks: the self-containment gate, the
   stylesheet split, the widened `collect_and_validate`, the normalized
   model, the overview, the map, the cards, the two existing diagrams under
   the shared rules, and the rationale diagram. Sequencing steps 3-7 of the
   spec.
3. **The app and the release** -- `knowledge-app.html`, its inline script and
   its own weaker whitelist, the Content-Security-Policy `<meta>` on both
   pages and the narrowed `http-equiv` whitelist rule, the
   progressive-enhancement test, `init --view --app`, the adoption twin lists,
   the four ADRs, the reference documentation, the `v2` channel,
   `CONTRIBUTING.md`'s release procedure, and 2.0.0.

Explicitly **out of scope here**, and each named above as plan 3's:
`knowledge-app.html` and anything about a script; the CSP `<meta>`;
`init --view --app`; the adoption twin lists; the ADRs; the `v2` channel; the
version bump and the release; and the reference documentation under
`docs/reference/` (spec sequencing step 10) -- with one exception, the
`render` paragraph of `docs/reference/cli.md`, which Task 9 rewrites because
landing Task 9 without it would ship a reference that describes a page this
plan has replaced. `memory.html`'s markup and content are out of scope for
the whole 2.0.0 change: this plan separates its
stylesheet and then pins its bytes so nothing here can move it.

## Guarantees kept, and the tests that already pin them

No task below may take any of these red. They are listed with the test that
holds them so a reviewer can check the claim rather than believe it.

| Guarantee | Pinned by |
|---|---|
| Byte determinism, no generation timestamp, untouched `st_mtime_ns` on a no-op run | `tests/test_render.py:42-58` `test_a_second_run_reports_unchanged_and_leaves_the_bytes_identical` |
| Atomic write; no temporary left behind on failure | `tests/test_render.py:1286-1320` (`..._when_the_artifact_cannot_be_replaced`, both halves), `tests/test_render.py:1215-1234` |
| Self-containment | `_assert_self_contained` (`tests/test_render.py:131-158`), called from `tests/test_render.py:225`, `:994`, `:1153` |
| No `<script>` anywhere on either page | `tests/test_render.py:195-210`, `tests/test_render.py:970-995` |
| Adopter text never becomes live markup | `tests/test_render.py:195`, `:233`, `:255`, `:1098` |
| Nothing is written when a precondition fails | `tests/test_render.py:61-71`, `:896-925`, `:410-441` |
| Each unit's card renders exactly once | `tests/test_render.py:469-486`, `:488-533`, `:535-587` |

Task 1 restructures `_assert_self_contained`: it moves from a flat list of
parsed elements to a parse **event stream**, and gains three rules that list
could not express -- an element whitelist (an element with no attributes was
never examined at all), a nesting rule (no `a` element and no `href`
attribute anywhere inside an `<svg>`), and a scan of each `<style>`
element's own text (`@import` and `url(` fetch, and no attribute check sees
inside a style block). Its three call sites pass the new `page_events`
fixture instead of `page_elements`; nothing they assert changes, and the
helper only rejects more than it did.

## File structure

The codebase splits a view into primitives (`html.py`), drawings (`svg.py`)
and one `*_view.py` per artifact. This plan keeps that split and adds one
layer under it: the data the views read.

**New:**

- `validated_memory/corpus.py` -- the normalized reading of the curated
  corpus: documents, effective states, verdicts, the declared extension, the
  verdict log grouped by anchor, and the derived views the page needs (the
  counts cross, the groups, the unprobed queue). Pure data; imports no HTML
  module and emits no markup. It exists because three renderers now need the
  same numbers, and computing them three times is how an overview starts
  disagreeing with the cards below it.
- `validated_memory/styles.py` -- one stylesheet constant per page. It is not
  in `html.py` because `html.py`'s docstring says it holds no domain
  knowledge, and a stylesheet full of `.unit`, `.rationale` and `.group-units`
  is nothing but domain knowledge. Both constants in one file so the fact that
  they share nothing is visible in one screen.
- `validated_memory/knowledge_overview.py` -- the overview block: the counts
  table, the map, the unprobed queue. Separate from `knowledge_view.py`
  because it renders a navigation index, not units: it never emits a card, and
  keeping the two apart is what makes "a unit is linked in three groups and
  rendered once" checkable by reading one file.

**Modified:**

- `validated_memory/html.py` -- `page` takes the stylesheet as an argument;
  `STYLESHEET` leaves the module.
- `validated_memory/svg.py` -- one shared diagram shell, the third diagram,
  and the two existing ones brought up to the shared rules. The three stay in
  one module because the rules they share are enforced by being written once.
- `validated_memory/knowledge_view.py` -- reads the model instead of the
  documents; renders the card in the spec's fixed order. Loses `headline`,
  `HEADING_PATTERN` and `_group_history` to `corpus.py`, so it stays roughly
  the size it is today while gaining the rationale block.
- `validated_memory/memory_view.py` -- one line: passes its own stylesheet.
- `validated_memory/render.py` -- builds the model and hands it to the view.
- `validated_memory/validate.py` -- `collect_and_validate` returns the
  extension it already builds instead of discarding it.
- `validated_memory/status.py` -- unpacks the widened return.
- `tests/test_render.py` -- the whitelist, and one new test per feature.
- `tests/conftest.py` -- one new fixture, `page_events`, beside the existing
  `page_elements`. It stays a generic parser: the self-containment policy is
  written in the file that owns it, `tests/test_render.py`, and conftest only
  reports what the document contains.

---

### Task 1: The self-containment scan becomes a real gate

**Files:**
- Modify: `tests/conftest.py:109-134` (the collector and a new `page_events`
  fixture beside `page_elements`)
- Modify: `tests/test_render.py:74-158` (the whitelist constants and
  `_assert_self_contained`), and its three call sites at
  `tests/test_render.py:225`, `:994`, `:1153`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `page_events(text) -> [("start", tag, attrs) | ("end", tag) | ("data", text)]`,
    a fixture in `tests/conftest.py`. Tags and attribute names are lowercased
    by `html.parser`, and a self-closing tag emits a `start` immediately
    followed by an `end`, so the stream stays balanced for a consumer
    tracking depth.
  - `SELF_CONTAINED_ELEMENTS`, a set of lowercase tag names, beside the
    existing `SELF_CONTAINED_ATTRIBUTES`.
  - `_assert_self_contained(page, page_events)` -- second argument is the new
    fixture. Still returns `[(tag, attrs)]` so its callers are unchanged
    below the signature.

Every later task adds the elements and `(element, attribute)` pairs it emits
to these two sets, in the same commit that emits them.

**Why first.** The scan has three holes, and every task after this one emits
new markup, so the whitelist has to be a real gate before that starts or the
first new element is admitted by accident rather than by decision. All three
holes have the same root: `page_elements` returns a **flat list of start
tags**, which cannot express an element's attributes-lessness, its nesting,
or its text.

- It iterates a tag's attributes, so an element with **no attributes** never
  enters the loop at all: a bare `<script>alert(1)</script>` passes today
  (`tests/test_render.py:143-149`).
- `("a", "href")` is the one pair exempt from the URL check, so an `<a href>`
  **inside an `<svg>`** passes both checks -- while the spec's diagram rule is
  "no `href` of any kind inside the SVG". Nothing in a flat list of tags can
  say which `<a>` that is.
- Nothing looks at a `<style>` element's **content**, so
  `<style>@import url(https://example.invalid/x.css)</style>` is a page that
  fetches from the network and passes every check on the page.

**Decision:** the fix is an event stream, not three more special cases.
`tests/conftest.py` gains `page_events`, which reports start tags, end tags
and text in document order; `_assert_self_contained` consumes it and keeps
the policy -- which elements, which attributes, what may nest where, what a
style block may say -- in `tests/test_render.py`, where the two pages'
contract already lives. `page_elements` stays exactly as it is for the many
tests that only want a flat list.

- [ ] **Step 1: Add the event stream, and move the helper onto it**

In `tests/conftest.py`, replace the collector (`tests/conftest.py:109-117`):

```python
class _Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.events = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))
        self.events.append(("start", tag, dict(attrs)))

    def handle_startendtag(self, tag, attrs):
        # A self-closing tag opens and closes at once, so it emits both
        # events: a consumer tracking depth must not see `<line/>` as an
        # element that never closes.
        self.elements.append((tag, dict(attrs)))
        self.events.append(("start", tag, dict(attrs)))
        self.events.append(("end", tag))

    def handle_endtag(self, tag):
        self.events.append(("end", tag))

    def handle_data(self, data):
        self.events.append(("data", data))
```

and add the fixture after `page_elements` (`tests/conftest.py:120-134`):

```python
@pytest.fixture
def page_events():
    """Parse a page into a flat event stream: start tags, end tags and text.

    `page_elements` reports start tags only, so it can say nothing about
    nesting or about what an element contains -- and two rules of this
    project's pages need both: an `<a>` is forbidden inside an `<svg>` while
    being the one linkable element outside it, and a `<style>` element's own
    text must not fetch anything. `html.parser` lowercases every tag and
    attribute name, so a consumer never has to case-fold.

    This reads the artifact as data; it imports nothing from the package.
    """

    def _parse(text):
        collector = _Collector()
        collector.feed(text)
        return collector.events

    return _parse
```

Then, in `tests/test_render.py`, move `_assert_self_contained` onto the
stream **without changing a single rule** -- same checks, same messages, read
off events instead of a list:

```python
def _assert_self_contained(page, page_events):
    """The self-containment scan both pages' tests share.

    A real whitelist over the parsed document, not a blacklist of
    substrings. Kept as one helper so `knowledge.html` and `memory.html`
    cannot drift apart on what "self-contained" means. Returns the start
    tags, so a caller can run further checks over the same parse.
    """
    elements = []
    for event in page_events(page):
        if event[0] != "start":
            continue
        _kind, tag, attrs = event
        elements.append((tag, attrs))
        if tag == "meta":
            assert "http-equiv" not in attrs, f"<meta http-equiv> found: {attrs}"
        for name, value in attrs.items():
            assert (tag, name) in SELF_CONTAINED_ATTRIBUTES, (
                f"{tag}[{name}]={value!r} is outside the self-containment whitelist"
            )
            if (tag, name) != ("a", "href"):
                assert "://" not in (value or ""), (
                    f"{tag}[{name}]={value!r} carries a URL outside the whitelist"
                )
        if tag == "a":
            assert "ping" not in attrs
            if attrs.get("target") == "_blank":
                assert attrs.get("rel") == "noopener noreferrer"
    return elements
```

and change its three call sites to pass the new fixture. Each of the three
tests already requests `page_elements`; add `page_events` to its parameters
and pass that instead:

- `tests/test_render.py:213-230`, `test_only_an_anchor_href_ever_carries_an_external_url`
- `tests/test_render.py:970-995`, `test_hostile_memory_content_never_becomes_live_markup`
- `tests/test_render.py:1098-1153`, `test_the_svg_diagrams_never_load_a_resource_or_carry_live_markup`

```python
    elements = _assert_self_contained(page, page_events)
```

(the third call site does not bind the result: `_assert_self_contained(page, page_events)`).

- [ ] **Step 2: Run the full suite to confirm nothing moved**

Run: `python3 -m pytest`
Expected: 401 passed. This step adds no test and changes no rule; if anything
goes red, the move onto the event stream lost a check and must be fixed
before the new rules go in on top of it.

- [ ] **Step 3: Write the failing tests**

Add to `tests/test_render.py`, immediately after `_assert_self_contained`:

```python
def _wrapped(body):
    """A minimal, otherwise-legal page carrying `body` in its `<body>`.

    The shell is made only of whitelisted elements and pairs, so a failure
    can only come from `body` -- which is what the control below establishes.
    """
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        "<title>Curated knowledge</title>\n<style>body { color: red; }</style>\n"
        f"</head>\n<body>\n<h1>Curated knowledge</h1>\n{body}\n</body>\n</html>\n"
    )


# One case per way a page could stop being self-contained. The first four
# are caught today, by the attribute loop or the `http-equiv` rule, and are
# here so that they stay caught once the element whitelist takes that job
# over -- an `<iframe>` with no attributes at all would slip through the
# attribute loop exactly as `<script>` does. The last five are the holes:
# an element with no attributes, an `<a>` that is legal outside an `<svg>`
# and forbidden inside one (in either case), an `<svg>` that is never closed,
# and a `<style>` element whose own text fetches from the network.
HOSTILE_BODIES = {
    "iframe": '<iframe src="https://example.invalid/"></iframe>',
    "meta_refresh": '<meta http-equiv="refresh" content="0">',
    "svg_image": '<svg class="freshness"><image href="band.png"/></svg>',
    "svg_use": '<svg class="freshness"><use href="#band"/></svg>',
    "bare_script": "<script>alert(1)</script>",
    "anchor_inside_svg": (
        '<svg class="rationale"><a href="#unit-kb-0001">'
        '<text x="0" y="0">kb-0001</text></a></svg>'
    ),
    "uppercase_anchor_inside_svg": (
        '<svg class="rationale"><A HREF="#unit-kb-0001">'
        '<text x="0" y="0">kb-0001</text></A></svg>'
    ),
    "unclosed_svg": (
        '<svg class="rationale"><a href="#unit-kb-0001">'
        '<text x="0" y="0">kb-0001</text></a>'
    ),
    "style_import": "<style>@import url(https://example.invalid/x.css);</style>",
}


@pytest.mark.parametrize("name", sorted(HOSTILE_BODIES))
def test_the_self_containment_scan_rejects_hostile_markup(name, page_events):
    with pytest.raises(AssertionError):
        _assert_self_contained(_wrapped(HOSTILE_BODIES[name]), page_events)


def test_the_self_containment_scan_accepts_a_page_built_from_the_whitelist(
    page_events
):
    # The positive control: a page made only of whitelisted elements and
    # pairs passes, so the cases above are proving the scan catches each
    # hostile body rather than proving the scan rejects everything.
    _assert_self_contained(
        _wrapped('<p class="basis">Basis: 0 unit(s) under knowledge/</p>'),
        page_events,
    )
```

- [ ] **Step 4: Run the tests to verify five of the nine fail**

Run: `python3 -m pytest tests/test_render.py -k self_containment_scan -v`
Expected: `..._rejects_hostile_markup` FAILS with
`DID NOT RAISE <class 'AssertionError'>` for exactly `anchor_inside_svg`,
`bare_script`, `style_import`, `unclosed_svg` and
`uppercase_anchor_inside_svg`. `iframe`, `meta_refresh`, `svg_image` and
`svg_use` pass already -- caught by the attribute loop on `("iframe", "src")`,
`("use", "href")` and `("image", "href")`, and by the `http-equiv` rule --
and are in the set to stay caught, not to go red. The control passes.

- [ ] **Step 5: Add the three rules the flat list could not express**

Add the element whitelist above `SELF_CONTAINED_ATTRIBUTES`, at
`tests/test_render.py:74` (before the existing comment block):

```python
# The complete set of elements either view is allowed to emit anywhere on the
# page. This exists because the attribute whitelist below cannot stand on its
# own: the scan walks a tag's attributes, so an element carrying NONE of them
# -- `<script>` with inline content is precisely that shape -- has no pairs
# to check and was admitted silently. A new element joins this set in the same
# commit that first emits it.
#
# `title` covers two different elements the parser cannot tell apart: the
# document's `<title>` in `<head>`, and the `<title>` inside an `<svg>` or an
# `<svg>` band. Both are legitimate, and neither can carry a URL.
SELF_CONTAINED_ELEMENTS = {
    "html", "head", "meta", "title", "style", "body",
    "h1", "p", "span", "code", "pre", "ul", "li", "div", "a",
    "section", "details", "summary",
    "svg", "rect", "text", "line",
}
```

and extend the helper written in Step 1 with the element check, the `<svg>`
nesting rule and the `<style>` content rule:

```python
def _assert_self_contained(page, page_events):
    """The self-containment scan both pages' tests share.

    A real whitelist over the parsed document, not a blacklist of
    substrings: every element must be in `SELF_CONTAINED_ELEMENTS`, every
    (element, attribute) pair in `SELF_CONTAINED_ATTRIBUTES`,
    `("a", "href")` is the only pair allowed to carry an external URL, and no
    `<meta>` is an `http-equiv`.

    Three of those rules need more than a flat list of start tags, which is
    why this walks an event stream:

    - The element check is not redundant with the attribute check: an element
      with no attributes has no pairs, so without it a bare `<script>` passes.
    - Inside an `<svg>`, no `a` element and no `href` attribute on anything:
      a diagram carries no href of any kind, and `("a", "href")` is exempt
      from the URL check, so nothing else here would catch one. Depth is
      counted rather than matched, so an `<svg>` that is never closed leaves
      everything after it inside a diagram -- which is the conservative
      reading, and the one that fails loudly.
    - A `<style>` element's own text is content nothing else inspects, and
      `@import` or a `url(...)` in it fetches from the network as surely as
      a remote stylesheet link would.

    Kept as one helper so `knowledge.html` and `memory.html` cannot drift
    apart on what "self-contained" means. Returns the start tags, so a caller
    can run further checks over the same parse.
    """
    elements = []
    styles = []
    svg_depth = 0
    style_depth = 0
    for event in page_events(page):
        if event[0] == "data":
            if style_depth:
                styles.append(event[1])
            continue
        if event[0] == "end":
            if event[1] == "svg" and svg_depth:
                svg_depth -= 1
            if event[1] == "style" and style_depth:
                style_depth -= 1
            continue
        _kind, tag, attrs = event
        elements.append((tag, attrs))
        assert tag in SELF_CONTAINED_ELEMENTS, (
            f"<{tag}> is outside the self-containment whitelist"
        )
        if svg_depth:
            assert tag != "a", f"an <a> inside an <svg>: {attrs}"
            assert "href" not in attrs, f"<{tag} href> inside an <svg>: {attrs}"
        if tag == "meta":
            assert "http-equiv" not in attrs, f"<meta http-equiv> found: {attrs}"
        for name, value in attrs.items():
            assert (tag, name) in SELF_CONTAINED_ATTRIBUTES, (
                f"{tag}[{name}]={value!r} is outside the self-containment whitelist"
            )
            if (tag, name) != ("a", "href"):
                assert "://" not in (value or ""), (
                    f"{tag}[{name}]={value!r} carries a URL outside the whitelist"
                )
        if tag == "a":
            assert "ping" not in attrs
            if attrs.get("target") == "_blank":
                assert attrs.get("rel") == "noopener noreferrer"
        if tag == "svg":
            svg_depth += 1
        if tag == "style":
            style_depth += 1
    for css in styles:
        assert "@import" not in css, f"a <style> block imports: {css[:120]!r}"
        assert "url(" not in css, f"a <style> block fetches a url: {css[:120]!r}"
    return elements
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_render.py -v`
Expected: PASS -- the nine hostile cases, the control, and the three existing
tests that call the helper over real pages. The element set above is exactly
what both pages emit today: `a, body, code, details, div, h1, head, html, li,
line, meta, p, pre, rect, section, span, style, summary, svg, text, title,
ul`. Neither stylesheet contains `@import` or `url(`; the CSS this project
writes uses `rgba(...)` and named font stacks, and nothing here loads a
resource.

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest`
Expected: 411 passed. No runtime source file changed; this is a test-only
commit.

- [ ] **Step 8: Commit**

```bash
git add tests/conftest.py tests/test_render.py
git commit -m "test: the self-containment scan gates elements, svg nesting and style content"
```

---

### Task 2: One stylesheet per page

**Files:**
- Create: `validated_memory/styles.py`
- Modify: `validated_memory/html.py:10-21` (delete `STYLESHEET`),
  `validated_memory/html.py:44-53` (`page` takes a stylesheet)
- Modify: `validated_memory/knowledge_view.py:91`
- Modify: `validated_memory/memory_view.py:129`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: the whitelist from Task 1.
- Produces: `styles.KNOWLEDGE` and `styles.MEMORY`, two module-level strings;
  `html.page(title, body, stylesheet)` -- three positional arguments, the
  third required. `html.STYLESHEET` no longer exists.

**Why.** `html.page` inlines one shared `STYLESHEET`
(`validated_memory/html.py:10,51`) that both views use, so restyling
`knowledge.html` through it would silently restyle `memory.html`, which this
change leaves alone. Both constants start as byte-identical copies of today's
stylesheet, so this commit changes no page by one byte: what it changes is
whether a later commit *can*.

- [ ] **Step 1: Write the regression pin**

Add to `tests/test_render.py`, after the `_log` helper (after line 17). This
test passes before the change and must still pass after it -- it is the pin,
not a red test. The expected page is what the CLI produces today over this
exact fixture tree; that is how the fixture obtains "before".

```python
# `memory.html`, byte for byte, as the CLI renders it today over the fixture
# tree in the test below. The 2.0.0 design leaves the memory view's markup,
# content and styling untouched -- only its stylesheet moves out of the shared
# constant -- so the whole page is pinned rather than sampled: a stylesheet
# edit meant for `knowledge.html` that reaches this one changes bytes here and
# nowhere a substring assertion would look.
MEMORY_PAGE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent memory</title>
<style>
:root { color-scheme: light dark; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
       margin: 2rem auto; max-width: 60rem; padding: 0 1rem; line-height: 1.5; }
pre { white-space: pre-wrap; overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      background: rgba(127,127,127,0.12); padding: .75rem; border-radius: .25rem; }
summary { cursor: pointer; }
.chain { border-left: 3px solid rgba(127,127,127,0.4); margin-left: .5rem;
         padding-left: 1rem; }
.meta { color: rgba(127,127,127,1); font-size: .9em; }
</style>
</head>
<body>
<h1>Agent memory</h1>
<p class="basis">Basis: 1 memory file(s) under memory/</p>
<section class="entry" id="entry-name-coffee" data-name="coffee">
<details>
<summary><code class="filename">coffee</code> <span class="relpath">coffee.md</span></summary>
<p class="name">coffee</p>
<p class="description">oat milk</p>
<p class="type">user</p>
<pre class="body">
Memory body.
</pre>
<p class="meta">No outgoing references.</p>
<p class="meta">No incoming references.</p>
</details>
</section>
</body>
</html>
"""


def test_the_memory_page_is_byte_for_byte_what_it_was_before_the_split(
    run_cli, adopter_dir, write_unit, write_memory, write_index
):
    # No `init` here on purpose: `init` writes an adopter configuration and a
    # schema stub, and this page must be a function of the memory directory
    # alone. `render` needs only the knowledge directory, the memory
    # directory and its index.
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\n",
        "# The first conclusion\n\nSupporting prose.\n",
    )
    write_memory(
        "coffee.md",
        "name: coffee\ndescription: oat milk\nmetadata:\n  type: user\n",
    )
    write_index("- [Coffee](coffee.md) — oat milk\n")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert (adopter_dir / "memory.html").read_text(encoding="utf-8") == MEMORY_PAGE


def test_a_second_run_leaves_the_memory_page_identical_and_untouched(
    run_cli, adopter_dir, write_unit, write_memory, write_index
):
    # The determinism pin `knowledge.html` has had since the views shipped
    # (`test_a_second_run_reports_unchanged_and_leaves_the_bytes_identical`),
    # now over the other artifact too: both pages carry the guarantee, and
    # from this task on they carry separate stylesheets, so one of them could
    # acquire a non-deterministic value without the other noticing.
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n", "# A claim\n")
    write_memory(
        "coffee.md",
        "name: coffee\ndescription: oat milk\nmetadata:\n  type: user\n",
    )
    write_index("- [Coffee](coffee.md) — oat milk\n")
    run_cli("render", cwd=adopter_dir)
    first = (adopter_dir / "memory.html").read_bytes()
    stamp = (adopter_dir / "memory.html").stat().st_mtime_ns

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "render: unchanged memory.html" in result.stdout
    assert (adopter_dir / "memory.html").read_bytes() == first
    # Identical bytes alone would pass an implementation that rewrites the
    # same content and prints `unchanged`. The file must not be touched.
    assert (adopter_dir / "memory.html").stat().st_mtime_ns == stamp
```

- [ ] **Step 2: Run them to see them pass before the change**

Run: `python3 -m pytest tests/test_render.py -k "memory_page or second_run" -v`
Expected: PASS, all four. The selector collects the two tests added above,
the existing `test_a_second_run_reports_unchanged_and_leaves_the_bytes_identical`
(`tests/test_render.py:42`) and the existing
`test_the_memory_page_lists_entries_with_their_references` (`:590`), and all
four pass before this task changes anything. If the byte-for-byte one fails,
the expected block above is wrong and must be corrected against the CLI's
real output **before** any source change, or the pin is worthless.

- [ ] **Step 3: Capture `knowledge.html` too, outside the repository**

`knowledge.html` changes in later tasks, so it is not pinned byte for byte in
the suite; this task must still not move it. Capture it and compare after
Step 4.

Run this from the repository root; `REPO` is how every later shell block in
this plan reaches the package without installing it.

```bash
REPO=$(pwd)
mkdir -p /tmp/vm-split/tree/knowledge /tmp/vm-split/tree/memory /tmp/vm-split/before
printf -- '---\nid: kb-0001\nevidence: measured\n---\n\n# The first conclusion\n\nSupporting prose.\n' > /tmp/vm-split/tree/knowledge/kb-0001.md
printf -- '---\nname: coffee\ndescription: oat milk\nmetadata:\n  type: user\n---\n\nMemory body.\n' > /tmp/vm-split/tree/memory/coffee.md
printf -- '- [Coffee](coffee.md)\n' > /tmp/vm-split/tree/memory/MEMORY.md
(cd /tmp/vm-split/tree && PYTHONPATH="$REPO" python3 -P -m validated_memory render)
cp /tmp/vm-split/tree/knowledge.html /tmp/vm-split/before/
```

- [ ] **Step 4: Write the implementation**

Create `validated_memory/styles.py`:

```python
"""The stylesheet of each page, hand-written, one per page.

Split so that restyling one view cannot restyle the other. `html.page` takes
the stylesheet as an argument and each view passes its own; there is no
shared constant left to edit by accident. `MEMORY` is the stylesheet both
pages shared before the split, kept byte for byte -- the memory view's
markup, content and styling are out of scope for the 2.0.0 change and must
not move -- and `KNOWLEDGE` starts as the same bytes and is the one that
grows.

No third-party CSS. Nothing here is generated, downloaded or vendored, so
there is no build step, no lockfile, no asset manifest, no hash to verify and
no third-party licence landing in an adopter's repository. The cost is that
this file is written by hand; the benefit is that the page opens with no
network and renders the same bytes forever.

These strings are inlined verbatim inside `<style>`. CSS has no escaping that
survives being parsed as CSS, so nothing adopter-authored may ever reach
here: every value in this file is written in this repository.
"""

KNOWLEDGE = """
:root { color-scheme: light dark; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
       margin: 2rem auto; max-width: 60rem; padding: 0 1rem; line-height: 1.5; }
pre { white-space: pre-wrap; overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      background: rgba(127,127,127,0.12); padding: .75rem; border-radius: .25rem; }
summary { cursor: pointer; }
.chain { border-left: 3px solid rgba(127,127,127,0.4); margin-left: .5rem;
         padding-left: 1rem; }
.meta { color: rgba(127,127,127,1); font-size: .9em; }
"""

MEMORY = """
:root { color-scheme: light dark; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
       margin: 2rem auto; max-width: 60rem; padding: 0 1rem; line-height: 1.5; }
pre { white-space: pre-wrap; overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      background: rgba(127,127,127,0.12); padding: .75rem; border-radius: .25rem; }
summary { cursor: pointer; }
.chain { border-left: 3px solid rgba(127,127,127,0.4); margin-left: .5rem;
         padding-left: 1rem; }
.meta { color: rgba(127,127,127,1); font-size: .9em; }
"""
```

In `validated_memory/html.py`, delete `STYLESHEET` (lines 10-21) and replace
`page` (lines 44-53) with:

```python
def page(title, body, stylesheet):
    """Wrap `body` in the document shell. `title` is escaped; `body` is markup.

    `stylesheet` is the caller's own -- this module holds none. A view that
    wants to restyle itself edits its own constant in `styles`, and cannot
    reach another view's by doing so. It is inlined verbatim: CSS has no
    escaping that survives being parsed as CSS, so passing anything
    adopter-authored here would be an injection, and no caller does.
    """
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape_text(title)}</title>\n"
        f"<style>{stylesheet}</style>\n</head>\n<body>\n"
        f"{body}\n</body>\n</html>\n"
    )
```

In `validated_memory/knowledge_view.py`, add `styles` to the import at line 6
and change line 91:

```python
from . import derive, html, memory, styles, svg, verdicts
```

```python
    return html.page(TITLE, "\n".join(parts), styles.KNOWLEDGE)
```

In `validated_memory/memory_view.py`, add `styles` to the imports (line 23
area) and change line 129:

```python
from . import html, styles
```

```python
    return html.page(TITLE, "\n".join(parts), styles.MEMORY)
```

Note `memory_view.py:23-24` currently reads `from . import html` followed by
`from . import memory as memory_module`; the first of those two lines becomes
the line above.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_render.py -v`
Expected: PASS, `MEMORY_PAGE` included.

- [ ] **Step 6: Prove `knowledge.html` did not move either**

```bash
REPO=$(pwd)
(cd /tmp/vm-split/tree && PYTHONPATH="$REPO" python3 -P -m validated_memory render)
diff -u /tmp/vm-split/before/knowledge.html /tmp/vm-split/tree/knowledge.html
```
Expected: no output. The two stylesheets are byte-identical copies, so neither
page changes.

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest`
Expected: 413 passed.

- [ ] **Step 8: Commit**

```bash
git add validated_memory/styles.py validated_memory/html.py validated_memory/knowledge_view.py validated_memory/memory_view.py tests/test_render.py
git commit -m "refactor: each view owns its stylesheet, and page takes it as an argument"
```

---

### Task 3: `collect_and_validate` returns the extension

**Files:**
- Modify: `validated_memory/validate.py:33-52` (`collect_and_validate` returns
  the extension it already builds), `validated_memory/validate.py:15`,
  `validated_memory/validate.py:26` (the two local call sites)
- Modify: `validated_memory/status.py:60`
- Modify: `validated_memory/render.py:118`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  `validate.collect_and_validate(path) -> (documents, extension, findings)`
  -- a triple where it returned a pair. `extension` is an
  `extension_module.Extension` or `None`, and it is `None` on the failure
  path too, where there are no documents and one blocking finding. All three
  call sites unpack it; none reads it yet.

**Why.** The spec requires the `Extension` object to reach the view, and
`validate.collect_and_validate` builds it and throws it away
(`validated_memory/validate.py:42-52`). Widening one return value and the
three sites that unpack it has no behaviour in it at all, which is exactly
why it is its own commit: a reviewer confirms "nothing moved" by reading
five lines, instead of hunting for it inside the migration that follows.

Nothing reads the returned extension in this plan. The page renders no
declared extension field -- the spec's card order does not list them -- and
`corpus` carries the object for plan 3. See Decision 6.

- [ ] **Step 1: Write the regression guards**

Add to `tests/test_render.py`. Both pass before the change; both must pass
after it. The first pins the path this task rewires and nothing else covers:
that an unloadable extension still stops `render` cold.

```python
def test_an_extension_that_cannot_be_loaded_stops_render_and_writes_nothing(
    run_cli, adopter_dir, write_unit, write_document
):
    # `collect_and_validate` loads the declared extension and, from this task
    # on, hands it to the renderer instead of discarding it. The failure path
    # must not soften on the way: an extension that will not load is one
    # blocking finding, no documents, and no page -- rendering the base
    # contract alone would report a pass the adopter never asked for.
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n")
    write_document("validated-memory.md", "extension:\n  schema: gone.md\n  version: \"1\"\n")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "ERROR" in result.stderr
    assert not (adopter_dir / "knowledge.html").exists()
    assert not (adopter_dir / "memory.html").exists()


def test_a_corpus_with_a_declared_extension_still_renders(
    run_cli, adopter_dir, write_unit, write_document
):
    # The other half: a schema that loads must reach the renderer without
    # changing what it draws. Nothing on the page shows an extension field
    # yet, and nothing in this plan ever will, so this is the guard that
    # threading the object through changed no output.
    run_cli("init", cwd=adopter_dir)
    write_document(
        "knowledge-extension.md",
        "fields:\n  - name: owner\n    type: string\n",
    )
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nowner: platform-team\n",
        "# A claim\n",
    )

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "kb-0001" in (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run them to see them pass before the change**

Run: `python3 -m pytest tests/test_render.py -k "extension" -v`
Expected: both PASS. They are regression guards over an existing path, not
red tests: this task deliberately changes nothing observable.

- [ ] **Step 3: Widen the return, and the three sites that unpack it**

`validated_memory/validate.py:33-52`, `collect_and_validate`. Change the
docstring's last paragraph and both returns:

```python
def collect_and_validate(path):
    """Collect units under `path` and validate them against the full contract.

    The shared front half of every consumer that needs a valid source: load
    the declared extension, read the units, apply the contract. Returns
    `(documents, extension, findings)`; when the extension cannot be loaded,
    there are no documents, no extension, and the single blocking finding.

    The extension is returned rather than dropped because a reader of the
    corpus may need to know what the adopter declared -- this function is the
    one place that loads it, and a caller that wanted it would otherwise have
    to load it a second time and risk disagreeing with the validation that
    just ran.
    """
    try:
        extension = extension_module.load(Path())
    except extension_module.ExtensionError as error:
        # An extension that cannot be loaded stops the run: validating units
        # against the base contract alone would report a pass the adopter did
        # not ask for.
        return [], None, [
            Finding(ERROR, error.location, error.field, error.message, line=error.line)
        ]
    documents, findings = _collect(resolve_target(path), explicit=bool(path))
    findings.extend(validate_documents(documents, extension))
    return documents, extension, findings
```

`validated_memory/validate.py:15` and `:26`:

```python
    documents, _extension, findings = collect_and_validate(path)
```

(the same line in both `run` and `gated_source`).

`validated_memory/status.py:60`:

```python
    documents, _extension, validation_findings = validate.collect_and_validate(None)
```

`validated_memory/render.py:118`:

```python
    documents, _extension, validation_findings = validate.collect_and_validate(None)
```

The leading underscore is the whole point at this stage: three of the four
call sites will never read it, and the fourth starts reading it in Task 4,
where the underscore comes off in the same diff that uses the value.

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest`
Expected: 415 passed. Nothing observable changed; `tests/test_status.py`,
`tests/test_validate.py` and `tests/test_derive.py` all drive the widened
function through the CLI and none of them notices.

- [ ] **Step 5: Commit**

```bash
git add validated_memory/validate.py validated_memory/status.py validated_memory/render.py tests/test_render.py
git commit -m "refactor: collect_and_validate returns the extension it already builds"
```

---

### Task 4: The normalized model

**Files:**
- Create: `validated_memory/corpus.py`
- Modify: `validated_memory/render.py:118` (drop the underscore), `:132-136`,
  `:26` (the import)
- Modify: `validated_memory/knowledge_view.py:1-119`, `:122-212` (build from
  the model; `headline`, `HEADING_PATTERN` and `_group_history` leave)

**Interfaces:**
- Consumes: `styles.KNOWLEDGE` from Task 2, and
  `validate.collect_and_validate`'s triple from Task 3.
- Produces:
  - `corpus.Unit`, a namedtuple with fields
    `unit_id state data headline body graded systems`.
  - `corpus.Corpus`, a namedtuple with fields
    `basis units active superseded extension view history record_total`,
    where `units` is `{unit_id: Unit}`, `active` and `superseded` are tuples
    of unit ids sorted by codepoint, `view` is the verdict service view,
    `history` is `{anchor_key: [record]}` and `record_total` is the log's own
    total.
  - `corpus.build(documents, basis, extension, records, view) -> Corpus`.
  - `corpus.headline(body_text, unit_id) -> str`.
  - `corpus.anchor_rows(corpus, unit_id) -> [(key, anchor)]`.
  - `corpus.canonical_payload(payload) -> str`.
  - `knowledge_view.build(corpus) -> str` -- one argument now.

**Why.** Four renderers are about to want the same numbers: the counts table,
the map, the unprobed queue and the cards. Computing them at each site is how
an overview starts disagreeing with the cards below it.

This task is a migration and is meant to be invisible: no page changes by a
byte, and it adds no test of its own. Its verification is the 415 tests that
already exist -- including `memory.html`'s byte-for-byte pin from Task 2 --
plus the byte comparison in Step 6, which is the only thing watching
`knowledge.html`.

- [ ] **Step 1: Capture both pages, outside the repository**

```bash
rm -rf /tmp/vm-model && mkdir -p /tmp/vm-model/before
cp -r /tmp/vm-split/tree /tmp/vm-model/tree
cp /tmp/vm-model/tree/knowledge.html /tmp/vm-model/tree/memory.html /tmp/vm-model/before/
```

(If `/tmp/vm-split/tree` is gone, rebuild it with the four `printf` commands
from Task 2, Step 3, and render once first.)

- [ ] **Step 2: Write `corpus.py`**

```python
"""The normalized reading of the curated corpus, built once per render.

`render` reads the units, the verdict log and the adopter's declared
extension; every consumer of that reading -- the overview's counts, the map,
the unprobed queue, the unit cards -- works from this one object instead of
walking the documents again. Building it once is what keeps the parts of the
page from disagreeing: the number in a count, the group a unit is listed
under and the badge on its card are the same value read from the same place.

`extension` is carried and not read. It is the adopter's declared schema,
loaded once by `validate.collect_and_validate`; nothing in the views renders
a declared field, because the design fixes the card's order and does not list
them. It is here so that a reader of this model has the schema in hand
without loading it a second time and risking a different answer from the
validation that just ran.

Everything here is a pure function of the documents, the log and the
extension. No clock, no `hash()` -- which is salted per process -- and no set
iteration order reaches the output: every sequence returned is sorted by
plain codepoint, with no locale and no collation, so the same corpus renders
the same bytes on every machine.

The documents reaching this module are validated: `render.build_artifacts`
gates on an ERROR finding before it builds anything. So `id` is present and
unique, `evidence` is one of `contract.EVIDENCE_STATES`, an anchor's `system`
and `kind` are strings and its `payload` a mapping, and a `rationale`, where
present, has the shape the contract fixes. That is why values are read
directly here, unlike `memory_view`, which renders a layer nothing validated.
The one exception is anchor key construction, which uses `.get()` exactly as
`derive.unit_verdict` does: the two must build byte-identical keys or a
lookup misses silently, so they read the mapping the same way.
"""

import json
import re
from collections import namedtuple

from . import derive, verdicts
from . import memory as memory_module
from .frontmatter import parse as parse_frontmatter

HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)

# One unit, read once.
#
# `state` is `derive.effective_states`' string: "active", or
# "superseded by <ids>". `graded` is a `derive.UnitVerdict`. `systems` is the
# distinct `anchors[].system` values this unit declares, sorted -- several
# anchors on one system count once, which is what makes the map's grouping
# single-valued per group.
Unit = namedtuple("Unit", "unit_id state data headline body graded systems")

# The whole corpus, read once.
#
# `units` is keyed by id; `active` and `superseded` partition its keys, both
# sorted. `extension` is the adopter's declared schema, carried and not read
# (see the module docstring). `view` is the verdict service view -- the latest verdict per anchor
# key -- and is carried rather than folded into `graded` because "this anchor
# has no record at all" and "this anchor's record says unknown" are different
# facts, and the unprobed queue needs the first one. `history` is the log
# grouped by anchor key, oldest first; `record_total` is the log's own total,
# which is larger than the sum of the groups whenever the log outlives the
# corpus.
Corpus = namedtuple(
    "Corpus",
    "basis units active superseded extension view history record_total",
)


def headline(body_text, unit_id):
    """The first heading of the body, or the id when there is none.

    THIS IS THE BOUNDARY AND IT IS CLOSED. Extracting one line by a
    documented rule is not rendering the body. "And the first paragraph too"
    would be, and the design rejects it: bodies are shown verbatim.

    "First" is by line position in the raw text, not by Markdown structure:
    `HEADING_PATTERN` has no notion of a fenced code block, so a `#` line
    inside one, ahead of the real heading, becomes the headline. This is a
    known consequence of the rule as stated, not a bug -- rendering fenced
    code differently would mean parsing the body's structure, which is
    exactly what a verbatim block does not do.

    It lives here, not in the view, because two renderers need it: the card
    and the map's links, which would otherwise be a column of bare ids.
    """
    match = HEADING_PATTERN.search(body_text)
    return match.group(1) if match else unit_id


def build(documents, basis, extension, records, view):
    """Return the whole normalized reading of one corpus.

    `records` is the verdict log's full history (`verdicts.history()`) and
    `view` its graded view (`verdicts.service_view()`) -- both read by
    `render.build_artifacts`, in that order, before this is called:
    `service_view` is what validates the log (it raises on a malformed
    record, such as an explicit `payload: null`), so by the time
    `_group_history` sees `records` below, every record in it has already
    passed that check. Reading either here instead would let a future edit
    reorder the two calls and silently lose that guarantee.
    """
    states = derive.effective_states(documents)
    bodies = {}
    for _location, text in documents:
        bodies[parse_frontmatter(text)["id"]] = memory_module.body(text)

    units = {}
    for unit_id in sorted(states):
        data, state = states[unit_id]
        anchors = data.get("anchors") or []
        body = bodies.get(unit_id, "")
        units[unit_id] = Unit(
            unit_id=unit_id,
            state=state,
            data=data,
            headline=headline(body, unit_id),
            body=body,
            graded=derive.unit_verdict(unit_id, anchors, view),
            # `isinstance` rather than a bare set comprehension: validation
            # guarantees a string here, but `sorted` over a set holding a
            # `None` raises `TypeError`, and a renderer must not be one
            # contract change away from a traceback on a reader's page.
            systems=tuple(
                sorted(
                    {
                        anchor.get("system")
                        for anchor in anchors
                        if isinstance(anchor.get("system"), str)
                    }
                )
            ),
        )

    return Corpus(
        basis=basis,
        units=units,
        active=tuple(uid for uid in sorted(units) if units[uid].state == "active"),
        superseded=tuple(
            uid for uid in sorted(units) if units[uid].state != "active"
        ),
        extension=extension,
        view=view,
        history=_group_history(records),
        record_total=len(records),
    )


def _group_history(records):
    """Group every record that names an anchor by the key it names.

    `records` reaches here only after `service_view()` has validated the
    whole log without raising (see `build`'s docstring for where and why),
    so every record here is already known to carry `unit`, `system` and
    `kind` as strings and, when present, `payload` as a mapping -- which is
    why they are indexed directly (`record["unit"]` and friends) rather than
    with `.get()`.

    A record with no `payload` field predates payloads and is read by no
    anchor -- see `verdicts.NO_PAYLOAD` -- so it never joins a group here.
    It is still counted in `record_total`, since that total is the log's own,
    not an anchor's; it just never counts toward an anchor's. Grouping
    preserves `records`' file order, so each group is oldest-first; the
    renderer reverses only the slice it shows.
    """
    grouped = {}
    for record in records:
        if "payload" not in record:
            continue
        key = verdicts.anchor_key(
            record["unit"], record["system"], record["kind"], record["payload"]
        )
        grouped.setdefault(key, []).append(record)
    return grouped


def anchor_rows(corpus, unit_id):
    """This unit's anchors as `(key, anchor)` pairs, in declaration order.

    The key is the one `derive.unit_verdict` and `verdicts.anchor_key` build
    -- what the anchor points at, payload included -- computed here once so
    the card, the unprobed queue and the verdict all read the same string.
    Two anchors that happen to share every field share a key and therefore a
    history; that is a true fact about the log, not a bug in this grouping.
    """
    rows = []
    for anchor in corpus.units[unit_id].data.get("anchors") or []:
        rows.append(
            (
                verdicts.anchor_key(
                    unit_id,
                    anchor.get("system"),
                    anchor.get("kind"),
                    anchor.get("payload"),
                ),
                anchor,
            )
        )
    return rows


def canonical_payload(payload):
    """A payload as the page writes it: JSON with sorted keys.

    A reader of the page has no Python. This is the same deterministic form
    `verdicts._canonical` keys a record with and `probe` writes into the log
    the page also displays -- `str` would show Python's single-quoted repr,
    `{'ref': 'main'}`, which is neither JSON nor what the log holds.
    """
    return json.dumps(payload, sort_keys=True)
```

- [ ] **Step 3: Build the model in `render`**

`validated_memory/render.py:118` -- the underscore Task 3 left comes off,
because this is the line that starts using the value:

```python
    documents, extension, validation_findings = validate.collect_and_validate(None)
```

`validated_memory/render.py:132-136`:

```python
        records = verdicts_module.history()
        view = verdicts_module.service_view()
        knowledge_content = knowledge_view.build(
            corpus.build(
                documents, validate.basis_location(None), extension, records, view
            )
        )
```

and add `corpus` to the import at `validated_memory/render.py:26`:

```python
from . import corpus, knowledge_view, memory_view, validate
```

- [ ] **Step 4: Rewrite `knowledge_view.py`'s top half against the model**

Replace `validated_memory/knowledge_view.py:1-119` (the docstring through
`_group_history`) with:

```python
"""Builds `knowledge.html`: the curated layer, live conclusions first."""

from . import html, styles, svg, verdicts
from . import corpus as corpus_module

TITLE = "Curated knowledge"

# The most recent probes shown under each anchor. The log itself is never
# truncated -- only what a page shows of it -- and each anchor states its
# own true total beside the window, so a reader can tell a full history from
# a partial one without leaving the page.
HISTORY_WINDOW = 20


def build(corpus):
    """Return the whole page as a string.

    `corpus` is `corpus.build(...)`, the one reading of the repository this
    page is a function of: the overview's numbers, the map's groups and each
    card's badges all come out of it, so no two parts of the page can be
    computed from different data.
    """
    # Populated as anchors are rendered below, so the header's "belonging"
    # total reflects exactly what ended up on the page -- not a count
    # derived separately, which could drift from the walk.
    shown_keys = set()

    sections = []
    rendered = set()
    for unit_id in corpus.active:
        sections.append(_unit_section(corpus, unit_id, rendered, shown_keys, top=True))

    belonging = sum(
        len(corpus.history[key]) for key in shown_keys if key in corpus.history
    )
    parts = [f"<h1>{html.escape_text(TITLE)}</h1>"]
    parts.append(
        f'<p class="basis">Basis: {len(corpus.units)} unit(s) under '
        f"{html.escape_text(corpus.basis)}</p>"
    )
    # Two totals, not one: the log outlives the corpus (nothing prunes a
    # record whose unit or anchor is gone), so the log's own total can never
    # be reconciled by a reader against the histories on the page -- only
    # the "belonging" count can be.
    parts.append(
        f'<p class="window">Verdict log: {corpus.record_total} record(s) in '
        f"{html.escape_text(verdicts.LOG_FILENAME)}, of which {belonging} "
        f"belong to an anchor shown below; at most {HISTORY_WINDOW} shown "
        "per anchor.</p>"
    )
    parts.extend(sections)
    return html.page(TITLE, "\n".join(parts), styles.KNOWLEDGE)
```

`headline`, `HEADING_PATTERN` and `_group_history` are gone from this module
-- they live in `corpus` now -- and so are the `json`, `re`, `derive`,
`memory` and `parse_frontmatter` imports they needed. The import block above
is the module's complete import block after this task.

Replace `_unit_section`, `_new_frame` and `_render_section`
(`validated_memory/knowledge_view.py:122-212`) with:

```python
def _unit_section(corpus, unit_id, rendered, shown_keys, top=True):
    """Render this unit's section, with its supersession chain nested inside.

    A chain's length is set by whoever writes `supersedes` and nothing in
    the contract bounds it, so this walks with an explicit stack rather than
    recursing -- a deep chain must not turn into a `RecursionError`. Two
    units may supersede the same one, so a unit already rendered elsewhere
    on the page is referenced by anchor (`<a href="#unit-...">`) instead of
    rendered again; that repeat rule is also what stops the walk from
    re-entering a shared ancestor. `render` validates before it renders, so
    `validate`'s rejection of a supersession cycle already guarantees this
    walk is over a DAG -- there is no separate cycle guard here. Likewise a
    `supersedes` entry naming a unit outside the validated set is its own
    contract ERROR (`_check_supersedes`) that gates before this ever runs,
    so every `target` below is guaranteed to be a key of `corpus.units`.
    """
    if unit_id in rendered:
        return _repeat_reference(unit_id)

    rendered.add(unit_id)
    stack = [_new_frame(corpus, unit_id, top)]
    while True:
        frame = stack[-1]
        if frame["index"] < len(frame["children"]):
            target = frame["children"][frame["index"]]
            frame["index"] += 1
            if target in rendered:
                frame["pieces"].append(_repeat_reference(target))
                continue
            rendered.add(target)
            stack.append(_new_frame(corpus, target, False))
            continue

        stack.pop()
        section = _render_section(corpus, frame, shown_keys)
        if not stack:
            return section
        stack[-1]["pieces"].append(section)


def _new_frame(corpus, unit_id, top):
    unit = corpus.units[unit_id]
    return {
        "unit": unit,
        "top": top,
        # The frontmatter subset accepts a list naming the same id twice;
        # the set is what the page must state, or a duplicated entry
        # multiplies one unit into a "N units" confluence of identical rows.
        "children": sorted(set(unit.data.get("supersedes") or [])),
        "index": 0,
        "pieces": [],
    }


def _render_section(corpus, frame, shown_keys):
    unit = frame["unit"]
    chain = "".join(frame["pieces"])
    if chain:
        chain = f'<div class="chain">\n{chain}\n</div>\n'
    # A confluence is drawn only when three or more units are superseded at
    # once by this one -- below three, a chain is two boxes and an arrow
    # saying what one line of text already says, so nothing is drawn.
    confluence = svg.confluence(frame["children"], unit.unit_id)
    css_class = "unit" if frame["top"] else "unit superseded"
    return (
        f'<section class="{css_class}" id="unit-{html.escape_attribute(unit.unit_id)}"'
        f' data-unit="{html.escape_attribute(unit.unit_id)}"'
        f' data-state="{html.escape_attribute(unit.state)}">\n'
        "<details>\n<summary>"
        f'<span class="headline">{html.escape_text(unit.headline)}</span> '
        f'<code class="id">{html.escape_text(unit.unit_id)}</code> '
        f'<span class="evidence">{html.escape_text(unit.data["evidence"])}</span> '
        f'<span class="verdict">{html.escape_text(unit.graded.verdict)}</span>'
        "</summary>\n"
        f'<pre class="body">{html.escape_text(unit.body)}</pre>\n'
        f"{_anchors(corpus, unit.unit_id, shown_keys)}"
        f"{_provenance(unit.data.get('provenance') or [])}"
        f"{confluence}"
        f"{chain}"
        "</details>\n</section>"
    )
```

Replace `_anchors` (`validated_memory/knowledge_view.py:223-256`) with the
model-reading version -- same output, same comments, one fewer argument:

```python
def _anchors(corpus, unit_id, shown_keys):
    # `payload` is a mapping the contract never looks inside -- the probe
    # interprets it, not the contract -- so it is arbitrary structure even
    # here, in the validated layer. `html.escape_text` stringifies before
    # escaping, which is what keeps that from raising, and
    # `corpus.canonical_payload` is what it stringifies with, not
    # `str`/`repr`: a reader of this page has no Python.
    rows = corpus_module.anchor_rows(corpus, unit_id)
    if not rows:
        return '<p class="meta">No anchors: this unit cannot expire.</p>\n'
    items = []
    for key, anchor in rows:
        shown_keys.add(key)
        payload = anchor.get("payload")
        items.append(
            "<li>"
            f'<span class="system">{html.escape_text(anchor.get("system"))}</span> '
            f'<span class="kind">{html.escape_text(anchor.get("kind"))}</span> '
            f'<span class="captured">{html.escape_text(anchor.get("captured_at"))}</span>'
            f'<pre class="payload">'
            f"{html.escape_text(corpus_module.canonical_payload(payload))}</pre>"
            f"{_history(corpus.history.get(key, []))}"
            "</li>"
        )
    return '<ul class="anchors">\n' + "\n".join(items) + "\n</ul>\n"
```

`_repeat_reference`, `_history` and `_provenance` are unchanged.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest`
Expected: 415 passed -- the same 415 as before this task, since it adds no
test. Nothing observable changed.

- [ ] **Step 6: Prove neither page moved**

```bash
REPO=$(pwd)
(cd /tmp/vm-model/tree && PYTHONPATH="$REPO" python3 -P -m validated_memory render)
diff -u /tmp/vm-model/before/knowledge.html /tmp/vm-model/tree/knowledge.html
diff -u /tmp/vm-model/before/memory.html /tmp/vm-model/tree/memory.html
```
Expected: no output from either `diff`.

- [ ] **Step 7: Commit**

```bash
git add validated_memory/corpus.py validated_memory/knowledge_view.py validated_memory/render.py
git commit -m "refactor: one normalized reading of the corpus feeds the knowledge view"
```

---

### Task 5: The overview -- counts, and the unprobed queue

**Files:**
- Create: `validated_memory/knowledge_overview.py`
- Modify: `validated_memory/corpus.py` (add `COUNT_ROWS`, `COUNT_COLUMNS`,
  `counts`, `unprobed`)
- Modify: `validated_memory/knowledge_view.py` (import and place the block)
- Modify: `validated_memory/styles.py` (`KNOWLEDGE`)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `corpus.Corpus` from Task 4.
- Produces:
  - `corpus.COUNT_ROWS` = `contract.EVIDENCE_STATES`,
    `corpus.COUNT_COLUMNS` = `verdicts.VERDICTS`.
  - `corpus.counts(corpus) -> {(evidence, verdict): int}`, dense over the
    full 3x3 product.
  - `corpus.unprobed(corpus) -> ((unit_id, system, kind, payload), ...)`.
  - `knowledge_overview.build(corpus) -> str`, a single
    `<section class="overview" id="overview">`. Task 6 adds the map inside it.

**Decision:** the unprobed queue lists anchors of **active units only**.
`probe` probes the anchors of active units alone
(`validated_memory/derive.py:64-72`), so a superseded unit's unprobed anchor
would be a queue item nobody can drain.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_render.py`:

```python
def _overview_fixture(run_cli, adopter_dir, write_unit):
    """Four active units and one superseded, spanning the counts table.

    kb-0001 measured + probed current; kb-0002 hypothesis + an anchor never
    probed; kb-0003 verifiable, no anchors; kb-0004 measured, no anchors,
    superseding kb-0005, whose own anchor was never probed either.
    """
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# Measured and current\n",
    )
    write_unit(
        "kb-0002.md",
        "id: kb-0002\nevidence: hypothesis\nanchors:\n"
        "  - system: gitlab\n    kind: file-hash\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# Never probed\n",
    )
    write_unit(
        "kb-0003.md", "id: kb-0003\nevidence: verifiable\n", "# No anchors\n"
    )
    write_unit(
        "kb-0004.md",
        "id: kb-0004\nevidence: measured\nsupersedes:\n  - kb-0005\n",
        "# The replacement\n",
    )
    write_unit(
        "kb-0005.md",
        "id: kb-0005\nevidence: measured\nanchors:\n"
        "  - system: zulu\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# Superseded, and never probed\n",
    )
    _log(adopter_dir, [
        {"unit": "kb-0001", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "current", "recorded_at": "2026-01-01T00:00:00Z"},
    ])


def test_the_overview_counts_active_units_by_evidence_crossed_with_verdict(
    run_cli, adopter_dir, write_unit
):
    _overview_fixture(run_cli, adopter_dir, write_unit)

    result = run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert (
        '<tr><th scope="row">measured</th><td>1</td><td>0</td><td>1</td>'
        '<td class="total">2</td></tr>'
    ) in page
    assert (
        '<tr><th scope="row">verifiable</th><td>0</td><td>0</td><td>1</td>'
        '<td class="total">1</td></tr>'
    ) in page
    assert (
        '<tr><th scope="row">hypothesis</th><td>0</td><td>0</td><td>1</td>'
        '<td class="total">1</td></tr>'
    ) in page
    assert (
        '<tr class="total"><th scope="row">total</th><td class="total">1</td>'
        '<td class="total">0</td><td class="total">3</td>'
        '<td class="total">4</td></tr>'
    ) in page
    # Superseded units are one separate number, never folded into the table:
    # the two populations must not be addable by accident.
    assert (
        "4 active unit(s) counted above; 1 superseded unit(s) counted separately"
    ) in page


def test_the_unprobed_queue_lists_anchors_of_active_units_only(
    run_cli, adopter_dir, write_unit
):
    _overview_fixture(run_cli, adopter_dir, write_unit)

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    queue = page.split('<ul class="unprobed">')[1].split("</ul>")[0]

    assert "1 anchor(s) of active units have no verdict under their current key" in page
    assert "kb-0002" in queue
    # kb-0001 has a record under its current key; kb-0005 has none but is
    # superseded, and `probe` never probes it, so listing it would be a queue
    # item nobody can drain.
    assert "kb-0001" not in queue
    assert "kb-0005" not in queue


def test_an_anchor_whose_payload_changed_is_unprobed_again(
    run_cli, adopter_dir, write_unit
):
    # The key is `(unit, system, kind, payload)`. A record written against
    # the old payload says nothing about what the anchor points at now, so
    # the anchor has no record under its current key and is unprobed. That is
    # the honest reading, and it is why the queue is keyed and not counted.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload:\n      ref: next\n",
        "# Repointed\n",
    )
    _log(adopter_dir, [
        {"unit": "kb-0001", "system": "repo", "kind": "git_ref",
         "payload": {"ref": "main"},
         "verdict": "current", "recorded_at": "2026-01-01T00:00:00Z"},
    ])

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    queue = page.split('<ul class="unprobed">')[1].split("</ul>")[0]

    assert "kb-0001" in queue
    assert '{"ref": "next"}' in queue


def test_the_overview_says_so_when_there_is_nothing_unprobed(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n", "# No anchors\n")

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert '<ul class="unprobed">' not in page
    assert (
        "Nothing outstanding: every anchor of an active unit has a verdict "
        "recorded under its current key."
    ) in page
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_render.py -k "overview or unprobed" -v`
Expected: all four FAIL -- there is no `<table class="counts">` and no
`<ul class="unprobed">` on the page, so `page.split(...)` raises `IndexError`
in two of them and the `in page` assertions fail in the others.

- [ ] **Step 3: Add the two derived views to `corpus.py`**

Add `from .contract import EVIDENCE_STATES` to the module's import block,
beside the other `from .` imports:

```python
from .contract import EVIDENCE_STATES
from .frontmatter import parse as parse_frontmatter
```

and the constants after `HEADING_PATTERN`:

```python
# The counts table's axes, in the order the page draws them. Both come from
# the domains the rest of the codebase already fixes, so a new evidence state
# or a new verdict grows the table without touching the renderer.
COUNT_ROWS = EVIDENCE_STATES
COUNT_COLUMNS = verdicts.VERDICTS
```

Add the two functions after `_group_history`:

```python
def counts(corpus):
    """Active units counted by evidence state crossed with aggregate verdict.

    Active only, following `status`, which grades exactly the active set
    (`validated_memory/status.py:143-165`): a superseded unit is not probed,
    so a verdict for it is a number nobody can act on. The cross itself is
    new -- `status` counts verdicts, not the pairing. Superseded units are
    reported as one separate figure, `len(corpus.superseded)`, and never
    folded in here, so the two populations cannot be added up by accident.

    Dense over the full product of `COUNT_ROWS` and `COUNT_COLUMNS`, so a
    zero cell is a real zero and the table's shape is a function of the
    domains rather than of the corpus.
    """
    table = {
        (evidence, verdict): 0
        for evidence in COUNT_ROWS
        for verdict in COUNT_COLUMNS
    }
    for unit_id in corpus.active:
        unit = corpus.units[unit_id]
        table[(unit.data["evidence"], unit.graded.verdict)] += 1
    return table


def unprobed(corpus):
    """Anchors of active units with no record under their current key.

    The key is the one `verdicts.anchor_key` builds -- `(unit, system, kind,
    payload)` -- so an anchor whose payload changed has no record under its
    new key and is listed here. That is the honest reading: the old record
    describes something the anchor no longer points at. Membership in
    `corpus.view` is what is tested, not the graded verdict: an anchor whose
    latest record says `unknown` HAS been probed, and a queue that conflated
    the two would ask someone to re-run a probe that already answered.

    Active units only, for the reason the counts are: `probe` probes the
    anchors of active units alone (`derive.effective_states`), so a
    superseded unit's anchor would be a queue item nobody can drain.

    Ordered by unit id, then system, then kind, then the payload's canonical
    JSON -- all plain codepoint sorts, so the queue is a function of the
    corpus and not of dictionary order.
    """
    rows = []
    for unit_id in corpus.active:
        for key, anchor in anchor_rows(corpus, unit_id):
            if key in corpus.view:
                continue
            rows.append(
                (unit_id, anchor.get("system"), anchor.get("kind"), anchor.get("payload"))
            )
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row[0], str(row[1]), str(row[2]), canonical_payload(row[3])
            ),
        )
    )
```

- [ ] **Step 4: Write `knowledge_overview.py`**

```python
"""The overview block of `knowledge.html`: what the corpus holds, at a glance.

Three parts, in this order: the counts of active units by evidence state
crossed with verdict, the map of the corpus, and the queue of anchors no
probe has answered for under their current key. Every figure comes from
`corpus`, so the overview and the cards below it cannot disagree.

The map is a NAVIGATION INDEX -- links to cards, never cards. That is what
makes grouping on a multi-valued axis well defined: a unit anchored in three
systems is a link in three groups while its card is still rendered exactly
once, so no id on this page is ever duplicated and the single-render rule the
card walk enforces is untouched.
"""

from . import html
from . import corpus as corpus_module


def build(corpus):
    """The whole overview, as one section."""
    return (
        '<section class="overview" id="overview">\n'
        + _counts(corpus)
        + _unprobed(corpus)
        + "</section>"
    )


def _counts(corpus):
    table = corpus_module.counts(corpus)
    header = "".join(
        f'<th scope="col">{html.escape_text(verdict)}</th>'
        for verdict in corpus_module.COUNT_COLUMNS
    )
    rows = []
    for evidence in corpus_module.COUNT_ROWS:
        cells = "".join(
            f"<td>{table[(evidence, verdict)]}</td>"
            for verdict in corpus_module.COUNT_COLUMNS
        )
        total = sum(
            table[(evidence, verdict)] for verdict in corpus_module.COUNT_COLUMNS
        )
        rows.append(
            f'<tr><th scope="row">{html.escape_text(evidence)}</th>{cells}'
            f'<td class="total">{total}</td></tr>'
        )
    column_totals = "".join(
        '<td class="total">'
        + str(sum(table[(evidence, verdict)] for evidence in corpus_module.COUNT_ROWS))
        + "</td>"
        for verdict in corpus_module.COUNT_COLUMNS
    )
    rows.append(
        f'<tr class="total"><th scope="row">total</th>{column_totals}'
        f'<td class="total">{len(corpus.active)}</td></tr>'
    )
    return (
        "<h2>Overview</h2>\n"
        '<table class="counts">\n'
        f'<thead><tr><th scope="col">evidence</th>{header}'
        '<th scope="col">total</th></tr></thead>\n'
        "<tbody>\n" + "\n".join(rows) + "\n</tbody>\n</table>\n"
        f'<p class="meta">{len(corpus.active)} active unit(s) counted above; '
        f"{len(corpus.superseded)} superseded unit(s) counted separately, "
        "each shown inside the card of the unit that superseded it.</p>\n"
    )


def _unprobed(corpus):
    rows = corpus_module.unprobed(corpus)
    if not rows:
        return (
            "<h2>Unprobed anchors</h2>\n"
            '<p class="meta">Nothing outstanding: every anchor of an active '
            "unit has a verdict recorded under its current key.</p>\n"
        )
    items = "\n".join(
        "<li>"
        f'<a href="#unit-{html.escape_attribute(unit_id)}">'
        f"{html.escape_text(unit_id)}</a> "
        f'<span class="system">{html.escape_text(system)}</span> '
        f'<span class="kind">{html.escape_text(kind)}</span>'
        f'<pre class="payload">'
        f"{html.escape_text(corpus_module.canonical_payload(payload))}</pre>"
        "</li>"
        for unit_id, system, kind, payload in rows
    )
    return (
        "<h2>Unprobed anchors</h2>\n"
        f'<p class="meta">{len(rows)} anchor(s) of active units have no '
        "verdict under their current key: never probed, or probed before the "
        "payload changed.</p>\n"
        '<ul class="unprobed">\n' + items + "\n</ul>\n"
    )
```

- [ ] **Step 5: Place the block on the page**

In `validated_memory/knowledge_view.py`, extend the import line written in
Task 4 and add one `parts.append` after the `window` paragraph, before
`parts.extend(sections)`:

```python
from . import html, knowledge_overview, styles, svg, verdicts
```

```python
    parts.append(knowledge_overview.build(corpus))
    parts.extend(sections)
```

- [ ] **Step 6: Style it**

Append to `styles.KNOWLEDGE`, before its closing `"""`:

```css
h2 { font-size: 1rem; text-transform: uppercase; letter-spacing: .05em;
     margin: 1.25rem 0 .5rem; }
.overview { border: 1px solid rgba(127,127,127,0.35); border-radius: .5rem;
            padding: .25rem 1rem 1rem; margin-bottom: 2rem; }
table.counts { border-collapse: collapse; }
table.counts th, table.counts td { border: 1px solid rgba(127,127,127,0.35);
                                   padding: .25rem .6rem; text-align: right; }
table.counts th[scope="row"], table.counts thead th:first-child {
    text-align: left; }
table.counts tr.total th, table.counts td.total { font-weight: 600; }
ul.unprobed { list-style: none; padding-left: 0; }
ul.unprobed li { margin-bottom: .5rem; }
```

- [ ] **Step 7: Widen the whitelist**

In `tests/test_render.py`, add to `SELF_CONTAINED_ELEMENTS`:

```python
    "h2", "table", "thead", "tbody", "tr", "th", "td",
```

and to `SELF_CONTAINED_ATTRIBUTES`:

```python
    ("table", "class"),
    ("tr", "class"),
    ("th", "scope"),
    ("td", "class"),
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_render.py -v`
Expected: PASS, the four new tests included. The three existing tests that
call `_assert_self_contained` now walk a page carrying the table and must
pass on the widened whitelist.

- [ ] **Step 9: Run the full suite**

Run: `python3 -m pytest`
Expected: 419 passed. `tests/test_render.py::test_the_memory_page_is_byte_for_byte_what_it_was_before_the_split`
is the guard that `memory.html` did not move: `KNOWLEDGE` grew and `MEMORY`
did not.

- [ ] **Step 10: Commit**

```bash
git add validated_memory/corpus.py validated_memory/knowledge_overview.py validated_memory/knowledge_view.py validated_memory/styles.py tests/test_render.py
git commit -m "feat: knowledge.html opens with counts by evidence and verdict, and the unprobed queue"
```

---

### Task 6: The map of the corpus

**Files:**
- Modify: `validated_memory/corpus.py` (add `UNCLASSIFIED`, `Group`, `groups`)
- Modify: `validated_memory/knowledge_overview.py` (add `_map`, place it)
- Modify: `validated_memory/styles.py` (`KNOWLEDGE`)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `corpus.Corpus` from Task 4 and `knowledge_overview.build`
  from Task 5.
- Produces: `corpus.Group`, a namedtuple with fields `name units`; and
  `corpus.groups(corpus) -> (Group, ...)`, system groups ordered by name and
  the unclassified group, when non-empty, last.

**Decision:** a group carries **no `id`**. Nothing on the page links to a
group -- the map's links target unit cards -- so an id here would buy nothing
and cost a real hole: `anchors[].system` is validated only as a non-empty
string (`validated_memory/contract.py`, `_is_non_empty_string`), so
`team alpha` and `https://host` are both legal systems, and
`id="group-system-{system}"` would produce an id containing whitespace, or
one carrying a `://` that the self-containment whitelist rejects outright.
The links that do exist target `unit-<id>`, and `contract.ID_PATTERN` admits
only letters, digits, `.`, `_` and `-` there.

**Decision:** the grouping axis is always `anchors[].system`. A declared
extension enum cannot take it over: declaring a second enum is an additive
schema change that does not even bump the schema version, so the page would
reorganize itself on a change nobody made to it.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_render.py`:

```python
def _map_block(page):
    """The map's markup alone, so an assertion cannot match a card below it."""
    return page[page.index("<h2>Map</h2>") : page.index("<h2>Unprobed anchors</h2>")]


def test_the_map_groups_active_units_by_anchor_system(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    # Anchored in two systems: a link in each group, and still one card.
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: alpha\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n"
        "  - system: beta\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# Anchored twice over\n",
    )
    # Two anchors on ONE system: counted once for that unit.
    write_unit(
        "kb-0002.md",
        "id: kb-0002\nevidence: measured\nanchors:\n"
        "  - system: alpha\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload:\n      ref: main\n"
        "  - system: alpha\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload:\n      ref: next\n",
        "# Two refs of one system\n",
    )
    write_unit("kb-0003.md", "id: kb-0003\nevidence: measured\n", "# No anchors\n")
    write_unit(
        "kb-0004.md",
        "id: kb-0004\nevidence: measured\nsupersedes:\n  - kb-0005\n",
        "# The replacement\n",
    )
    write_unit(
        "kb-0005.md",
        "id: kb-0005\nevidence: measured\nanchors:\n"
        "  - system: zulu\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# Superseded\n",
    )

    result = run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    block = _map_block(page)

    assert result.returncode == 0, result.stderr
    # Groups by name, and the unclassified group last however the names sort.
    alpha = block.index('<span class="group-name">alpha</span>')
    beta = block.index('<span class="group-name">beta</span>')
    unclassified = block.index(
        '<span class="group-name">unclassified (no anchors)</span>'
    )
    assert alpha < beta < unclassified
    assert "zulu" not in block, "a superseded unit's system"
    # No group carries an id: nothing links to one, and a system name is
    # arbitrary text that has no business in a DOM id.
    assert "<li class=\"group\" id=" not in block
    # Multi-valued grouping: two links, one card.
    assert block.count('href="#unit-kb-0001"') == 2
    assert page.count('id="unit-kb-0001"') == 1
    # Several anchors on one system count once for that unit.
    assert block.count('href="#unit-kb-0002"') == 1
    # Units with no anchors are never dropped.
    assert block.count('href="#unit-kb-0003"') == 1
    assert block.count('href="#unit-kb-0004"') == 1
    # The map indexes active units; a superseded one stays reachable from the
    # card of the unit that superseded it.
    assert "kb-0005" not in block


def test_the_map_links_carry_the_headline_not_only_the_id(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md", "id: kb-0001\nevidence: measured\n", "# A claim worth reading\n"
    )

    run_cli("render", cwd=adopter_dir)
    block = _map_block((adopter_dir / "knowledge.html").read_text(encoding="utf-8"))

    assert '<a href="#unit-kb-0001">A claim worth reading</a>' in block
    assert '<code class="id">kb-0001</code>' in block


def test_a_system_named_unclassified_and_the_no_anchors_group_are_both_shown(
    run_cli, adopter_dir, write_unit
):
    # No group carries an id, so a label is the only thing telling two groups
    # apart on the page -- which is why the group of units with no anchors is
    # labelled `unclassified (no anchors)` and not `unclassified`. It is also
    # always last, whatever the system names sort as.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: unclassified\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# Anchored in a system with an awkward name\n",
    )
    write_unit("kb-0002.md", "id: kb-0002\nevidence: measured\n", "# No anchors\n")

    run_cli("render", cwd=adopter_dir)
    block = _map_block((adopter_dir / "knowledge.html").read_text(encoding="utf-8"))

    # The exact-string match is what keeps the two apart: the system group's
    # span closes right after the word, and the other one does not.
    named = block.index('<span class="group-name">unclassified</span>')
    no_anchors = block.index(
        '<span class="group-name">unclassified (no anchors)</span>'
    )
    assert named < no_anchors
    assert block.count('href="#unit-kb-0001"') == 1
    assert block.count('href="#unit-kb-0002"') == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_render.py -k "map or unclassified" -v`
Expected: all three FAIL with `ValueError: substring not found` on
`page.index("<h2>Map</h2>")` -- the map does not exist yet.

- [ ] **Step 3: Add the grouping to `corpus.py`**

Beside `COUNT_ROWS`:

```python
# The label carried by the group of units with no anchors. With no id on a
# group, a label is the only thing telling two groups apart on the page, so
# it says what the group IS rather than borrowing a word an adopter's system
# may legitimately be called: a corpus with a system named `unclassified`
# renders that group and this one distinctly, one after the other.
UNCLASSIFIED = "unclassified (no anchors)"

# One group of the map: `name` is the label, `units` the ids linked from it,
# sorted. There is no id: nothing links to a group, and a system name is
# arbitrary text with no business in a DOM id.
Group = namedtuple("Group", "name units")
```

After `counts`:

```python
def groups(corpus):
    """The map's groups: active units by `anchors[].system`, unclassified last.

    The axis is ALWAYS `anchors[].system`. A declared extension enum cannot
    take it over: declaring a second enum is an additive schema change that
    does not even bump the schema version
    (`docs/reference/curated-knowledge.md`), so the page would silently
    reorganize itself the moment an adopter added an unrelated field. A page
    that rearranges itself on a change nobody made to it is worse than a page
    grouped by a coarser axis.

    Multi-valued, and well defined because the map is a navigation index and
    not a second rendering: a unit anchored in three systems is a link in
    three groups while its card is still rendered exactly once. Several
    anchors on the same system count once for that unit -- `Unit.systems` is
    already the distinct set.

    A unit with no anchors goes to an explicit `unclassified (no anchors)`
    group rather than being dropped: a unit that cannot expire is a fact
    about the corpus. That group is always emitted last, whatever its label
    sorts as, and it is built separately from the system groups -- so an
    adopter whose corpus has a system called `unclassified` gets two groups,
    labelled differently, and not a merge.

    No group carries a DOM id. Nothing links to one, and `anchors[].system`
    is validated only as a non-empty string, so a system name can hold
    whitespace or a URL: neither belongs in an id.

    Active units only. A superseded unit stays reachable from the card of the
    unit that superseded it, which is where the view already nests it.
    Groups by name, units within a group by id, both plain codepoint sorts.
    """
    by_system = {}
    unclassified = []
    for unit_id in corpus.active:
        systems = corpus.units[unit_id].systems
        if not systems:
            unclassified.append(unit_id)
            continue
        for system in systems:
            by_system.setdefault(system, []).append(unit_id)

    result = [
        Group(system, tuple(sorted(by_system[system])))
        for system in sorted(by_system)
    ]
    if unclassified:
        result.append(Group(UNCLASSIFIED, tuple(sorted(unclassified))))
    return tuple(result)
```

- [ ] **Step 4: Render it**

In `validated_memory/knowledge_overview.py`, add `_map` between `_counts` and
`_unprobed`, and place it in `build`:

```python
def build(corpus):
    """The whole overview, as one section."""
    return (
        '<section class="overview" id="overview">\n'
        + _counts(corpus)
        + _map(corpus)
        + _unprobed(corpus)
        + "</section>"
    )
```

```python
def _map(corpus):
    groups = corpus_module.groups(corpus)
    if not groups:
        return "<h2>Map</h2>\n<p class=\"meta\">No active units to map.</p>\n"
    items = []
    for group in groups:
        links = "\n".join(
            f'<li><a href="#unit-{html.escape_attribute(unit_id)}">'
            f"{html.escape_text(corpus.units[unit_id].headline)}</a> "
            f'<code class="id">{html.escape_text(unit_id)}</code></li>'
            for unit_id in group.units
        )
        items.append(
            '<li class="group">'
            f'<span class="group-name">{html.escape_text(group.name)}</span> '
            f'<span class="meta">{len(group.units)} unit(s)</span>\n'
            f'<ul class="group-units">\n{links}\n</ul>\n</li>'
        )
    return (
        "<h2>Map</h2>\n"
        '<p class="meta">Active units by anchor system. A unit anchored in '
        "several systems is listed in each; its card is rendered once.</p>\n"
        '<ul class="groups">\n' + "\n".join(items) + "\n</ul>\n"
    )
```

- [ ] **Step 5: Style it**

Append to `styles.KNOWLEDGE`, before its closing `"""`:

```css
ul.groups { list-style: none; padding-left: 0; }
li.group { margin-bottom: .75rem; }
.group-name { font-weight: 600; }
ul.group-units { list-style: none; padding-left: 1rem; margin-top: .25rem;
                 border-left: 2px solid rgba(127,127,127,0.35); }
```

- [ ] **Step 6: Check the whitelist needs nothing**

The map is built from `ul`, `li`, `span`, `a` and `code`, all of which both
pages already emit, and from the attribute pairs `("ul", "class")`,
`("li", "class")`, `("span", "class")`, `("a", "href")` and
`("code", "class")`, all already whitelisted. Nothing is added here, and that
is a property of the decision above: a group with an id would have needed a
new pair, and the value in it would have been arbitrary adopter text.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_render.py -v`
Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `python3 -m pytest`
Expected: 422 passed.

- [ ] **Step 9: Commit**

```bash
git add validated_memory/corpus.py validated_memory/knowledge_overview.py validated_memory/styles.py tests/test_render.py
git commit -m "feat: the overview maps the corpus by anchor system, unclassified last"
```

---

### Task 7: The unit card

**Files:**
- Modify: `validated_memory/knowledge_view.py` (`_render_section`, plus a new
  `_rationale`)
- Modify: `validated_memory/styles.py` (`KNOWLEDGE`)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `corpus.Corpus` from Task 4.
- Produces: the card's fixed order and its markup contract --
  `<section class="unit" id="unit-<id>" data-unit data-state data-evidence
  data-verdict>` and `<div class="rationale">` holding
  `<p class="question">` and `<ul class="options">`. Task 9 inserts the
  diagram between those two. `<details>` is emitted exactly as it is today,
  at every level.

**Decision:** the badges' machine-readable state lives on the `<section>`, as
`data-evidence` and `data-verdict` beside the existing `data-state` -- not on
the summary's spans, whose markup stays byte for byte what it is
(`<span class="verdict">unknown</span>`, pinned at
`tests/test_render.py:407`). A filter hides a whole card, so the attribute
belongs on the card.

**Decision:** every card stays closed, exactly as it is today -- no
`<details open>` at any level. The overview and the map are the reader's entry
into the page now, and a page that opens with every active card expanded is
the transcript this design set out to replace.

**Decision:** the card renders no declared extension field. The spec fixes the
card's order and does not list them, so adding them would be this plan
inventing a surface the design did not ask for. `corpus.extension` still
reaches the view, as the spec requires; it is carried for plan 3, unread here.

**Decision:** `dir="auto"` goes on the HTML elements carrying the rationale's
adopter text -- the question, each label, each reason -- so right-to-left text
renders in its own direction without reordering anything around it. It is not
put on SVG `<text>`: `dir` is an HTML global attribute with no defined effect
on an SVG element, and markup that does nothing is worse than none.

**Card order**, fixed, exactly the spec's list: headline and id; badges for
evidence state and aggregate verdict; the freshness strip per anchor; the
rationale; the supersession chain; `provenance`; the verbatim body. The
confluence is drawn immediately before the chain it summarizes, which is where
it is today.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_render.py`:

```python
RATIONALE_UNIT = """\
id: kb-0001
evidence: verifiable
provenance:
  - https://example.invalid/doc
anchors:
  - system: repo
    kind: git_ref
    captured_at: 2026-01-01T00:00:00Z
    payload: {}
rationale:
  question: "How should knowledge views be delivered?"
  options:
    - label: "Generate a complete static artifact"
      disposition: chosen
      reason: "It stays readable without Python, JavaScript or network access."
    - label: "Build an interactive application"
      disposition: rejected
      reason: "It makes the reader depend on a runtime."
"""


def _card_fixture(run_cli, adopter_dir, write_unit):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", RATIONALE_UNIT, "# The delivery decision\n\nProse.\n")


def test_the_card_renders_its_parts_in_the_fixed_order(
    run_cli, adopter_dir, write_unit
):
    _card_fixture(run_cli, adopter_dir, write_unit)

    result = run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    card = page[page.index('id="unit-kb-0001"') :]

    assert result.returncode == 0, result.stderr
    order = [
        "</summary>",
        '<ul class="anchors">',
        '<div class="rationale">',
        '<ul class="provenance">',
        '<pre class="body">',
    ]
    positions = [card.index(marker) for marker in order]
    assert positions == sorted(positions), dict(zip(order, positions))


def test_the_card_carries_its_evidence_and_verdict_as_data_attributes(
    run_cli, adopter_dir, write_unit, page_elements
):
    # The badge text is on the summary, unchanged; the machine-readable state
    # is on the section, beside `data-state`, because a filter hides a card
    # and not a span.
    _card_fixture(run_cli, adopter_dir, write_unit)

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    section = next(
        attrs for tag, attrs in page_elements(page)
        if tag == "section" and attrs.get("data-unit") == "kb-0001"
    )

    assert section["data-evidence"] == "verifiable"
    assert section["data-verdict"] == "unknown"
    assert section["data-state"] == "active"
    assert '<span class="evidence">verifiable</span>' in page
    assert '<span class="verdict">unknown</span>' in page


def test_the_rationale_is_on_the_card_in_full(
    run_cli, adopter_dir, write_unit
):
    _card_fixture(run_cli, adopter_dir, write_unit)

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert (
        '<p class="question" dir="auto">How should knowledge views be '
        "delivered?</p>"
    ) in page
    assert '<li class="option chosen">' in page
    assert '<li class="option rejected">' in page
    assert '<span class="option-number">1</span>' in page
    assert '<span class="option-number">2</span>' in page
    assert 'Generate a complete static artifact' in page
    assert "It makes the reader depend on a runtime." in page


def test_a_unit_with_no_rationale_gets_no_rationale_block(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n", "# Just a fact\n")

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert 'class="rationale"' not in page


def test_hostile_rationale_text_never_becomes_live_markup(
    run_cli, adopter_dir, write_unit
):
    # `question`, `label` and `reason` are adopter text that reaches an HTML
    # file meant to be sent to third parties. The contract validates their
    # shape, never their content.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n'
        '  question: "<script>alert(1)</script>"\n'
        '  options:\n'
        '    - label: "<svg onload=alert(2)></svg>"\n'
        '      disposition: chosen\n'
        '      reason: "</pre><script>alert(3)</script>"\n'
        '    - label: "plain"\n      disposition: rejected\n'
        '      reason: "also plain"\n',
        "# Hostile\n",
    )

    result = run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "<script>alert(1)</script>" not in page
    assert "<script>alert(3)</script>" not in page
    assert "<svg onload=alert(2)>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&lt;svg onload=alert(2)&gt;&lt;/svg&gt;" in page
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_render.py -k "card or rationale" -v`
Expected: FAIL. `..._in_the_fixed_order` raises `ValueError` on
`<div class="rationale">`; `..._as_data_attributes` raises `KeyError:
'data-evidence'`; `..._is_on_the_card_in_full` fails its `in page`
assertions; and
`test_hostile_rationale_text_never_becomes_live_markup` fails on
`"&lt;script&gt;alert(1)&lt;/script&gt;" in page`, because nothing puts the
question on the page yet -- its "not in page" half is vacuously true today
and is the half that must stay true once Step 3 does put it there.
`..._with_no_rationale_gets_no_rationale_block` PASSES already, which is
correct: it is the regression guard that a unit recording no choice is never
nagged into showing an empty one.

- [ ] **Step 3: Write the implementation**

Replace `_render_section` in `validated_memory/knowledge_view.py` with:

```python
def _render_section(corpus, frame, shown_keys):
    unit = frame["unit"]
    chain = "".join(frame["pieces"])
    if chain:
        chain = f'<div class="chain">\n{chain}\n</div>\n'
    # A confluence is drawn only when three or more units are superseded at
    # once by this one -- below three, a chain is two boxes and an arrow
    # saying what one line of text already says, so nothing is drawn.
    confluence = svg.confluence(frame["children"], unit.unit_id)
    css_class = "unit" if frame["top"] else "unit superseded"
    return (
        f'<section class="{css_class}" id="unit-{html.escape_attribute(unit.unit_id)}"'
        f' data-unit="{html.escape_attribute(unit.unit_id)}"'
        f' data-state="{html.escape_attribute(unit.state)}"'
        f' data-evidence="{html.escape_attribute(unit.data["evidence"])}"'
        f' data-verdict="{html.escape_attribute(unit.graded.verdict)}">\n'
        "<details>\n<summary>"
        f'<span class="headline">{html.escape_text(unit.headline)}</span> '
        f'<code class="id">{html.escape_text(unit.unit_id)}</code> '
        f'<span class="evidence">{html.escape_text(unit.data["evidence"])}</span> '
        f'<span class="verdict">{html.escape_text(unit.graded.verdict)}</span>'
        "</summary>\n"
        f"{_anchors(corpus, unit.unit_id, shown_keys)}"
        f"{_rationale(unit)}"
        f"{confluence}"
        f"{chain}"
        f"{_provenance(unit.data.get('provenance') or [])}"
        f'<pre class="body">{html.escape_text(unit.body)}</pre>\n'
        "</details>\n</section>"
    )


def _rationale(unit):
    """The unit's rationale: the question, then every option in full.

    Drawn only for a unit that carries one -- most units are measurements,
    record no choice between alternatives, and must not be nagged into
    inventing one.

    `rejected` is written as itself and nowhere near the words used for
    supersession or for a failed verdict: an option was considered and not
    chosen HERE. It is not false, and it is not superseded.

    This list is the complete text, always. The diagram beside it may fall
    back to a number for a label it cannot fit; nothing it shows is missing
    from here.
    """
    rationale = unit.data.get("rationale")
    if not rationale:
        return ""
    items = []
    for position, option in enumerate(rationale["options"], start=1):
        items.append(
            f'<li class="option {html.escape_attribute(option["disposition"])}">'
            f'<span class="option-number">{position}</span> '
            f'<span class="disposition">{html.escape_text(option["disposition"])}</span> '
            f'<span class="label" dir="auto">{html.escape_text(option["label"])}</span>'
            f'<p class="reason" dir="auto">{html.escape_text(option["reason"])}</p>'
            "</li>"
        )
    return (
        '<div class="rationale">\n'
        f'<p class="question" dir="auto">'
        f'{html.escape_text(rationale["question"])}</p>\n'
        '<ul class="options">\n' + "\n".join(items) + "\n</ul>\n"
        "</div>\n"
    )
```

- [ ] **Step 4: Style it**

Append to `styles.KNOWLEDGE`, before its closing `"""`:

```css
.unit { border: 1px solid rgba(127,127,127,0.35); border-radius: .5rem;
        padding: .25rem 1rem; margin-bottom: 1rem; }
.unit.superseded { border-style: dashed; }
.headline { font-weight: 600; }
.evidence, .verdict { font-size: .8em; border: 1px solid currentColor;
                      border-radius: 1rem; padding: .05rem .5rem; }
[data-evidence="measured"] > details > summary .evidence { border-style: solid; }
[data-evidence="verifiable"] > details > summary .evidence { border-style: dashed; }
[data-evidence="hypothesis"] > details > summary .evidence { border-style: dotted; }
[data-verdict="current"] > details > summary .verdict {
    background: rgba(46,125,50,0.18); }
[data-verdict="drifted"] > details > summary .verdict {
    background: rgba(198,40,40,0.18); }
[data-verdict="unknown"] > details > summary .verdict {
    background: rgba(117,117,117,0.18); }
ul.options, ul.anchors, ul.provenance { list-style: none; padding-left: 0; }
.rationale { border-left: 3px solid rgba(127,127,127,0.35);
             padding-left: 1rem; margin: .75rem 0; }
.question { font-weight: 600; }
li.option { border: 1px solid rgba(127,127,127,0.35); border-radius: .35rem;
            padding: .4rem .6rem; margin-bottom: .4rem; }
li.option.chosen { border-width: 2px; }
.option-number { font-variant-numeric: tabular-nums; opacity: .7; }
.disposition { font-size: .8em; text-transform: uppercase;
               letter-spacing: .05em; }
.reason { margin: .25rem 0 0; }
```

- [ ] **Step 5: Widen the whitelist**

In `tests/test_render.py`, add to `SELF_CONTAINED_ATTRIBUTES`:

```python
    ("section", "data-evidence"),
    ("section", "data-verdict"),
    ("span", "dir"),
    ("p", "dir"),
```

No new element joins `SELF_CONTAINED_ELEMENTS` in this task: the card is built
from elements both pages already emit.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_render.py -v`
Expected: PASS. In particular
`test_a_record_without_a_payload_is_never_attributed_to_an_anchor`
(`tests/test_render.py:378`) still asserts
`'<span class="verdict">unknown</span>' in page`, which this task
deliberately leaves byte for byte as it was, and
`test_a_chain_three_deep_nests_correctly_and_renders_each_unit_once`
(`:488`) still finds `'<div class="chain">\n<section class="unit superseded"
id="unit-kb-0002"'`, since the chain's own markup is unchanged.

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest`
Expected: 427 passed.

- [ ] **Step 8: Commit**

```bash
git add validated_memory/knowledge_view.py validated_memory/styles.py tests/test_render.py
git commit -m "feat: the unit card gains badges and the rationale in full"
```

---

### Task 8: The diagram shell, and the two existing diagrams under the shared rules

**Files:**
- Modify: `validated_memory/svg.py` (the module docstring, the constants,
  a new `_diagram`, `freshness_strip`, `confluence`)
- Modify: `validated_memory/styles.py` (`KNOWLEDGE`)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: the whitelist from Task 1; `_render_section` from Task 7, which
  already calls `svg.confluence`.
- Produces:
  - `svg._diagram(class_name, width, height, label, description, body) -> str`
    -- the shell every diagram shares. Task 9's builder calls it.
  - `svg.freshness_strip(records) -> str` and
    `svg.confluence(superseded_ids, successor_id) -> str` -- unchanged
    signatures, unchanged call sites.
  - `svg.BAND_HEIGHT` = 24, `svg.WIDTH` = 640, `svg.CONFLUENCE_WIDTH` = 460,
    `svg.COLOURS`, `svg.MARKS`, `svg.SHAPES`.

**Why.** The spec fixes five rules every generated diagram obeys, and says the
two that exist today do not meet them yet: `freshness_strip` has a `<title>`
per band but no diagram-level `<desc>` and distinguishes bands by colour
alone, and `confluence` has neither
(`validated_memory/svg.py:30-77`). Bringing them up is part of this work, not
a later tidy-up -- and doing it before the third diagram exists means the
third one is written against a shell that already enforces the rules.

**Decision:** a band is told apart by **shape** as well as by mark and
colour: `current` is a full-height rectangle, `drifted` is a full-height
rectangle stroked `stroke-dasharray="3 2"`, and `unknown` is a half-height
rectangle of the same width, its top edge dropped by half the band height.
"State differs in shape and in text, not only in fill" is the spec's wording,
and a mark alone leaves the shape channel unused on the one diagram a reader
scans fastest.

**Decision:** every `<text>` the module emits gains `fill="currentColor"`, so
the drawings follow the page's colour scheme instead of being black on a dark
background -- **except the band marks**, which keep a fixed `fill="#ffffff"`:
they sit on top of a saturated fill of the module's own choosing, where
`currentColor` would be unreadable in one theme or the other.

**Decision:** the strip's `aria-label` and `<title>` no longer quote
`recorded_at`. Nothing validates that field -- the log is hand-editable and
the verdict reader requires only `unit`, `system`, `kind`, `verdict` and the
payload (`validated_memory/verdicts.py:139-165`) -- so a `recorded_at`
carrying `://` would put a URL in an SVG attribute, which the
self-containment whitelist forbids anywhere but `a[href]`. The label is built
from the record count and the last verdict, both of which come from closed
domains; every `recorded_at` still reaches the page in the band's own
`<title>` and in the history list, escaped by the same call.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_render.py`:

```python
def _two_diagram_fixture(run_cli, adopter_dir, write_unit):
    """A corpus that draws a confluence and a freshness strip, and no more."""
    run_cli("init", cwd=adopter_dir)
    for old in ("kb-0001", "kb-0002", "kb-0003"):
        write_unit(f"{old}.md", f"id: {old}\nevidence: hypothesis\n", f"# {old}\n")
    write_unit(
        "kb-0004.md",
        "id: kb-0004\nevidence: measured\nsupersedes:\n"
        "  - kb-0001\n  - kb-0002\n  - kb-0003\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# The one that replaced them\n",
    )
    _log(adopter_dir, [
        {"unit": "kb-0004", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "current", "recorded_at": "2026-01-01T00:00:00Z"},
        {"unit": "kb-0004", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "drifted", "recorded_at": "2026-02-01T00:00:00Z"},
        {"unit": "kb-0004", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "unknown", "recorded_at": "2026-03-01T00:00:00Z"},
    ])


def test_the_two_existing_diagrams_carry_a_title_and_a_desc(
    run_cli, adopter_dir, write_unit, page_elements, page_events
):
    _two_diagram_fixture(run_cli, adopter_dir, write_unit)

    result = run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    assert result.returncode == 0, result.stderr
    assert {
        attrs.get("class") for tag, attrs in elements if tag == "svg"
    } == {"freshness", "confluence"}
    # One <desc> per diagram. The strip also carries a <title> per band, so
    # titles outnumber descs; descs are one apiece and that is the count to
    # assert.
    assert len([tag for tag, _ in elements if tag == "desc"]) == 2
    # The one thing the strip's description exists to deny.
    assert "Not a time axis" in page
    _assert_self_contained(page, page_events)


def test_each_freshness_band_differs_in_shape_and_in_text_not_only_colour(
    run_cli, adopter_dir, write_unit
):
    # "State differs in shape and in text, not only in fill." Three bands,
    # one per verdict: a full-height solid band, a full-height dashed band,
    # and a half-height band -- each with its own one-character mark on top.
    # Printed in black and white, or read by someone who cannot tell the
    # three fills apart, the strip still says which is which.
    _two_diagram_fixture(run_cli, adopter_dir, write_unit)

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    strip = page[page.index('<svg class="freshness"') :].split("</svg>")[0]

    assert '>+</text>' in strip
    assert '>!</text>' in strip
    assert '>?</text>' in strip
    assert 'height="24" fill="#2e7d32">' in strip
    assert (
        'height="24" fill="#c62828" stroke="currentColor" '
        'stroke-dasharray="3 2">'
    ) in strip
    assert 'y="12" width=' in strip
    assert 'height="12" fill="#757575">' in strip
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_render.py -k "two_existing_diagrams or freshness_band" -v`
Expected: both FAIL. The first on the `desc` count, which is 0; the second on
`'>+</text>'`, since a band is a bare `<rect>` today.

- [ ] **Step 3: Rewrite `svg.py`'s shell and its two diagrams**

Replace the whole module with:

```python
"""The generated diagrams: an anchor's freshness over time, and a confluence.

All inline SVG, generated deterministically from the data alone, and all of
them obey one set of rules -- written once, here, because a rule kept in
three places is a rule two of them will drift from:

- **Deterministic.** Element ids derive from the data, never from `hash()`,
  which is salted per process. No clock, no generation timestamp, no font
  metrics. The freshness strip's right edge is the LAST RECORD, never "now":
  an edge at "now" would redraw the artifact on every regeneration and dirty
  `git status` on every session.
- **Inert.** No `href` of any kind, no `<use>`, no `<image>`.
- **Escaped.** Every text node and attribute goes through
  `html.escape_text` / `html.escape_attribute`. An SVG carrying unescaped
  adopter text is an XSS surface, not a drawing.
- **Not colour-alone.** State differs in shape and in text as well as in
  fill, so the diagrams survive colour blindness and a black and white
  printer. Every diagram carries a `<title>` and a `<desc>`.
- **Never load-bearing.** Everything a diagram shows is also on the page as
  structured HTML.

A diagram's `<title>`, `aria-label` and `<desc>` are built from values of
closed domains -- counts, verdicts, unit ids -- and never from adopter or
probe text. An attribute is the one place on the page where a stray `://`
would breach the self-containment rule, and `recorded_at`, a `label` and a
`question` are all values nothing constrains. They reach the page as escaped
text instead, which is where they belong.
"""

from . import html

BAND_HEIGHT = 24
WIDTH = 640
CONFLUENCE_WIDTH = 460
COLOURS = {"current": "#2e7d32", "drifted": "#c62828", "unknown": "#757575"}
# One character per verdict, drawn in the band. These three are ASCII, so no
# font has to have them.
MARKS = {"current": "+", "drifted": "!", "unknown": "?"}
# And one shape per verdict -- `(top edge, height, dash pattern)` -- so the
# strip reads the same in greyscale as in colour: a full solid band, a full
# dashed band, and a half-height band.
SHAPES = {
    "current": (0, BAND_HEIGHT, None),
    "drifted": (0, BAND_HEIGHT, "3 2"),
    "unknown": (BAND_HEIGHT // 2, BAND_HEIGHT // 2, None),
}


def _diagram(class_name, width, height, label, description, body):
    """The shell every diagram shares: sized, titled, described and inert.

    `label` is both the `aria-label` and the `<title>`, so a reader using a
    screen reader and a reader hovering the drawing get the same sentence.
    `<desc>` says what the drawing means and, more importantly, what it does
    not mean -- which for the freshness strip is "distance in time".

    Both are built by the caller out of closed-domain values only; see the
    module docstring for why nothing adopter-authored may reach them.
    """
    return (
        f'<svg class="{html.escape_attribute(class_name)}" role="img" '
        f'viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'aria-label="{html.escape_attribute(label)}">'
        f"<title>{html.escape_text(label)}</title>"
        f"<desc>{html.escape_text(description)}</desc>"
        f"{body}</svg>"
    )


def freshness_strip(records):
    """A horizontal band per record, oldest to newest, labelled with its verdict.

    `records` is the anchor's own group, oldest first, in log file order,
    never re-sorted by `recorded_at`: the log is append-only, so file order is
    chronological, and the verdict parser requires only `unit`, `system`,
    `kind`, `verdict` and the payload -- `recorded_at` is what `probe`
    happens to write, not something a reader can demand. Each band is still
    labelled with its `recorded_at` when there is one, in its own `<title>`.

    This is a sequence, NOT a time axis, and the `<desc>` says so: with
    `recorded_at` optional, no width here may imply distance in time between
    two records.

    Three channels tell a band apart and only one is colour: its shape
    (`SHAPES`), its mark (`MARKS`) and its `<title>`.
    """
    if not records:
        return ""
    count = len(records)
    band = WIDTH / count
    bands = []
    for index, record in enumerate(records):
        verdict = record["verdict"]
        top, band_height, dash = SHAPES[verdict]
        outline = (
            f' stroke="currentColor" stroke-dasharray="{dash}"' if dash else ""
        )
        bands.append(
            f'<rect x="{index * band:.2f}" y="{top}" width="{band:.2f}" '
            f'height="{band_height}" fill="{COLOURS[verdict]}"{outline}>'
            f"<title>{html.escape_text(record.get('recorded_at', ''))} {html.escape_text(verdict)}</title>"
            "</rect>"
            f'<text x="{index * band + band / 2:.2f}" y="16" '
            'text-anchor="middle" font-size="12" fill="#ffffff">'
            f"{html.escape_text(MARKS[verdict])}</text>"
        )
    return _diagram(
        "freshness",
        WIDTH,
        BAND_HEIGHT,
        f"Probe history: {count} record(s), oldest to newest, "
        f"ending {records[-1]['verdict']}",
        "One band per probe record, in log order, oldest at the left. Not a "
        "time axis: the log records the order probes were written in, not the "
        "distance in time between them, and the right edge is the last "
        "record, never now. Each band carries its verdict three ways -- a "
        "shape (full band current, dashed band drifted, half-height band "
        "unknown), a mark (+ current, ! drifted, ? unknown) and a colour.",
        "".join(bands),
    )


def confluence(superseded_ids, successor_id):
    """Three or more units merging into one. Below three, nothing is drawn.

    Below three, a chain is two boxes and an arrow saying what one line of
    text already says.
    """
    if len(superseded_ids) < 3:
        return ""
    ordered = sorted(set(superseded_ids))
    height = len(ordered) * 28 + 12
    lines = []
    for index, unit_id in enumerate(ordered):
        y = index * 28 + 14
        lines.append(
            f'<text x="4" y="{y + 4}" font-size="12" fill="currentColor">'
            f"{html.escape_text(unit_id)}</text>"
            f'<line x1="120" y1="{y}" x2="300" y2="{height / 2}" '
            'stroke="currentColor" stroke-width="1"/>'
        )
    lines.append(
        f'<text x="308" y="{height / 2 + 4}" font-size="12" fill="currentColor">'
        f"{html.escape_text(successor_id)}</text>"
    )
    return _diagram(
        "confluence",
        CONFLUENCE_WIDTH,
        height,
        f"{len(ordered)} units superseded by {successor_id}",
        "One line from each superseded unit on the left to the single unit "
        "that replaced them all on the right. Every id drawn here is also a "
        "card nested below this one.",
        "".join(lines),
    )
```

A unit id is safe in a label: `contract.ID_PATTERN` admits only letters,
digits, `.`, `_` and `-`, so `successor_id` can carry neither a `://` nor a
character that changes the attribute's meaning.

- [ ] **Step 4: Style it**

Append to `styles.KNOWLEDGE`, before its closing `"""`:

```css
svg { display: block; margin: .5rem 0; max-width: 100%; }
```

- [ ] **Step 5: Widen the whitelist**

In `tests/test_render.py`, add to `SELF_CONTAINED_ELEMENTS`:

```python
    "desc",
```

and to `SELF_CONTAINED_ATTRIBUTES`:

```python
    ("text", "fill"),
    ("text", "text-anchor"),
    ("rect", "stroke"),
    ("rect", "stroke-dasharray"),
```

- [ ] **Step 6: Repair the two comments the new label makes false**

Two existing tests explain themselves in terms of an `aria-label` that quoted
`recorded_at`. The assertions still hold; the comments no longer describe
what the code does, and a comment that lies is worse than none.

`tests/test_render.py:1001-1005`, inside
`test_a_null_recorded_at_reads_as_absent_in_the_list_and_the_strip_alike`:

```python
    # `recorded_at` is not a key field and nothing validates it, so an
    # explicit `null` is a legal record. Wherever it reaches the page it must
    # spell an absent value the same way -- "" and never the literal word
    # "None" -- and it reaches the page twice: in the history list and in
    # each band's own <title>, both through `html.escape_text`. The strip's
    # `aria-label` no longer quotes it at all, being built from the record
    # count and the last verdict, so the assertion on that attribute is now
    # a guard that the label stays free of record fields.
```

`tests/test_render.py:1119-1122`, inside
`test_the_svg_diagrams_never_load_a_resource_or_carry_live_markup`:

```python
    # A hostile `recorded_at` -- angle brackets and a quote -- on the LAST
    # record: the band's own <title> is the one place the strip shows a
    # record field at all, so it is the sharpest place a missed escape would
    # show up as live markup. The strip's aria-label is built from a count
    # and a verdict and quotes no record field.
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_render.py -v`
Expected: PASS. Two existing tests deserve a look while it runs:

- `test_a_null_recorded_at_reads_as_absent_in_the_list_and_the_strip_alike`
  (`tests/test_render.py:998`) asserts `"None" not in page` and reads the
  strip's `aria-label`. The label no longer quotes `recorded_at` at all, so
  its half of that test is now trivially satisfied; the assertion that still
  bites is `"None" not in page`, which covers the band's own `<title>` --
  built by the same `html.escape_text` call as the history list, which is
  what the test was pinning in the first place.
- `test_the_svg_diagrams_never_load_a_resource_or_carry_live_markup`
  (`:1098`) asserts that the hostile `recorded_at` reaches the page escaped;
  it does, in the band `<title>`, unchanged by this task.

- [ ] **Step 8: Run the full suite**

Run: `python3 -m pytest`
Expected: 429 passed.

- [ ] **Step 9: Commit**

```bash
git add validated_memory/svg.py validated_memory/styles.py tests/test_render.py
git commit -m "feat: one shell for every diagram, and the strip tells its states apart by shape"
```

---

### Task 9: The rationale diagram

**Files:**
- Modify: `validated_memory/svg.py` (the docstring's first line and its
  guarantees paragraph, the rationale constants, a new `rationale`)
- Modify: `validated_memory/knowledge_view.py` (`_rationale` places the
  diagram)
- Modify: `docs/reference/cli.md:438-455` (the `render` paragraph describing
  `knowledge.html`)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `svg._diagram` from Task 8, `_rationale` from Task 7, the
  whitelist from Task 1.
- Produces:
  - `svg.rationale(unit_id, record) -> str`, where `record` is the validated
    rationale mapping (`question`, `options`). The parameter is `record`, not
    `rationale`, so it does not shadow the function.
  - `svg.LABEL_LIMIT` = 48, `svg.NUMBERED_ABOVE` = 8, `svg.ROW_HEIGHT` = 34,
    `svg.BOX_HEIGHT` = 28, `svg.OPTION_INDENT` = 24.

**Decision:** above `NUMBERED_ABOVE` options **every** node draws its number,
whatever its label measures. Past that point a column of numbers reads better
than a column of half-fitting text, and a uniform rule never leaves a reader
wondering why one node is numbered and its neighbour is not.

**Decision:** the diagram is a top-down tree of full-width rows -- the
question across the top, the options indented beneath it -- and the question
obeys the same threshold as a label: drawn inline at or under `LABEL_LIMIT`
characters, drawn as `?` above it, with the full text always in the
`<p class="question">` beside the diagram. One threshold, one fallback, and a
geometry that can honour it: a narrow left-hand question column would overflow
into the option column at roughly half that many characters, which is a limit
the drawing would break without saying so.

**Decision:** the diagram's `<title>`, `aria-label` and `<desc>` name the unit
and count the options, and quote **no** adopter text. A `question` is
unconstrained text: one containing `https://` would put a URL in an SVG
attribute, which the self-containment whitelist forbids anywhere but
`a[href]`, and one longer than the threshold would contradict the fallback the
drawing has just applied. The question reaches the SVG only as a `<text>`
node, and only when it fits; in full it lives in the card's own paragraph.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_render.py`:

```python
LONG_LABEL = "A label written out at such length that no node could hold it, ever"
LONG_QUESTION = "A question put at such length that no node could ever hold it all"


def _all_three_fixture(run_cli, adopter_dir, write_unit):
    """A corpus that draws every diagram: a confluence, a strip, a rationale."""
    run_cli("init", cwd=adopter_dir)
    for old in ("kb-0001", "kb-0002", "kb-0003"):
        write_unit(f"{old}.md", f"id: {old}\nevidence: hypothesis\n", f"# {old}\n")
    write_unit(
        "kb-0004.md",
        "id: kb-0004\nevidence: measured\nsupersedes:\n"
        "  - kb-0001\n  - kb-0002\n  - kb-0003\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n"
        'rationale:\n  question: "Which of the three?"\n  options:\n'
        '    - label: "Replace all three"\n'
        '      disposition: chosen\n      reason: "They disagreed."\n'
        '    - label: "Leave all three standing"\n'
        '      disposition: rejected\n      reason: "They disagreed."\n',
        "# The one that replaced them\n",
    )
    _log(adopter_dir, [
        {"unit": "kb-0004", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "current", "recorded_at": "2026-01-01T00:00:00Z"},
        {"unit": "kb-0004", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "drifted", "recorded_at": "2026-02-01T00:00:00Z"},
    ])


def _rationale_diagram(page):
    return page[page.index('<svg class="rationale"') :].split("</svg>")[0]


def test_all_three_diagrams_carry_a_title_and_a_desc(
    run_cli, adopter_dir, write_unit, page_elements, page_events
):
    _all_three_fixture(run_cli, adopter_dir, write_unit)

    result = run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    assert result.returncode == 0, result.stderr
    assert {
        attrs.get("class") for tag, attrs in elements if tag == "svg"
    } == {"freshness", "confluence", "rationale"}
    assert len([tag for tag, _ in elements if tag == "desc"]) == 3
    _assert_self_contained(page, page_events)


def test_a_page_with_all_three_diagrams_renders_the_same_bytes_twice(
    run_cli, adopter_dir, write_unit
):
    # The determinism pin over the page that actually draws every diagram.
    # SVG is where a non-deterministic value would hide -- an id built from
    # `hash()`, a float formatted one way here and another there, a clock in
    # a label -- and the pin that has existed since the views shipped
    # (`test_a_second_run_reports_unchanged_and_leaves_the_bytes_identical`)
    # runs over a corpus that draws no diagram at all.
    _all_three_fixture(run_cli, adopter_dir, write_unit)
    run_cli("render", cwd=adopter_dir)
    first = (adopter_dir / "knowledge.html").read_bytes()
    stamp = (adopter_dir / "knowledge.html").stat().st_mtime_ns

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "render: unchanged knowledge.html" in result.stdout
    assert (adopter_dir / "knowledge.html").read_bytes() == first
    # Identical bytes alone would pass an implementation that rewrites the
    # same content and prints `unchanged`. The file must not be touched.
    assert (adopter_dir / "knowledge.html").stat().st_mtime_ns == stamp


def test_the_rationale_diagram_is_drawn_for_that_unit_and_no_other(
    run_cli, adopter_dir, write_unit
):
    # One page, two units: only the one carrying a rationale gets a diagram.
    # A per-page diagram instead of a per-unit one would pass a test over a
    # corpus of one and fail every real corpus.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n'
        '  question: "Which?"\n  options:\n'
        '    - label: "A"\n      disposition: chosen\n      reason: "R"\n'
        '    - label: "B"\n      disposition: rejected\n      reason: "R"\n',
        "# With a rationale\n",
    )
    write_unit("kb-0002.md", "id: kb-0002\nevidence: measured\n", "# Without one\n")

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert page.count('<svg class="rationale"') == 1
    assert page.count('id="rationale-kb-0001-1"') == 1
    assert "rationale-kb-0002" not in page


def test_text_too_long_to_draw_falls_back_and_stays_on_the_page(
    run_cli, adopter_dir, write_unit
):
    # An SVG <text> does not wrap, and a character count is not a width --
    # which is exactly why the fallback is a fallback and not an estimate.
    # One threshold governs every node, question included: nothing is omitted
    # and nothing is truncated, the node says "?" or "#2" and the page says
    # the rest.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n'
        f'  question: "{LONG_QUESTION}"\n  options:\n'
        '    - label: "Short"\n      disposition: chosen\n'
        '      reason: "Fits."\n'
        f'    - label: "{LONG_LABEL}"\n      disposition: rejected\n'
        '      reason: "Does not fit."\n',
        "# Fallback\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    diagram = _rationale_diagram(page)

    assert len(LONG_QUESTION) > 48 and len(LONG_LABEL) > 48
    assert ">?</text>" in diagram
    assert LONG_QUESTION not in diagram
    assert ">#2</text>" in diagram
    assert LONG_LABEL not in diagram
    assert ">Short</text>" in diagram
    # The complete text is on the page, beside the diagram, both of them.
    assert f'<p class="question" dir="auto">{LONG_QUESTION}</p>' in page
    assert f'<span class="label" dir="auto">{LONG_LABEL}</span>' in page


def test_above_eight_options_every_node_is_numbered(
    run_cli, adopter_dir, write_unit
):
    options = "".join(
        f'    - label: "Option {n}"\n'
        f'      disposition: {"chosen" if n == 1 else "rejected"}\n'
        f'      reason: "Reason {n}"\n'
        for n in range(1, 10)
    )
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n'
        '  question: "Which of the nine?"\n  options:\n' + options,
        "# Nine options\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    diagram = _rationale_diagram(page)

    assert ">#1</text>" in diagram
    assert ">#9</text>" in diagram
    assert "Option 1" not in diagram
    # Every option is still on the page, in full.
    for n in range(1, 10):
        assert f'<span class="label" dir="auto">Option {n}</span>' in page


def test_the_rationale_diagram_element_ids_derive_from_the_unit_and_position(
    run_cli, adopter_dir, write_unit
):
    # Deterministic ids, never `hash()`, which is salted per process: the
    # same corpus must render the same bytes on every run and every machine.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n'
        '  question: "Which?"\n  options:\n'
        '    - label: "A"\n      disposition: chosen\n      reason: "R"\n'
        '    - label: "B"\n      disposition: rejected\n      reason: "R"\n',
        "# Ids\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert 'id="rationale-kb-0001-1"' in page
    assert 'id="rationale-kb-0001-2"' in page


def test_the_chosen_option_differs_in_shape_and_in_text_not_only_in_fill(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n'
        '  question: "Which?"\n  options:\n'
        '    - label: "A"\n      disposition: chosen\n      reason: "R"\n'
        '    - label: "B"\n      disposition: rejected\n      reason: "R"\n',
        "# Shapes\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    diagram = _rationale_diagram(page)

    # Shape: the chosen node is rounded and heavier.
    assert 'rx="8"' in diagram and 'stroke-width="3"' in diagram
    assert 'rx="0"' in diagram and 'stroke-width="1"' in diagram
    # Text: the disposition is drawn, not implied by colour.
    assert ">chosen</text>" in diagram
    assert ">rejected</text>" in diagram


def test_hostile_rationale_text_never_becomes_live_markup_inside_the_svg(
    run_cli, adopter_dir, write_unit, page_elements, page_events
):
    # Two hostile shapes at once. The label tries to close the <text> element
    # it is drawn in; the question carries a URL, which is the one thing no
    # attribute on this page but `a[href]` may hold -- so it must reach the
    # drawing as a text node and never as part of a title, an aria-label or
    # a description.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        'id: kb-0001\nevidence: measured\nrationale:\n'
        '  question: "Which host? https://example.invalid/x"\n  options:\n'
        '    - label: "</text><script>alert(2)</script>"\n'
        '      disposition: chosen\n      reason: "R"\n'
        '    - label: "B"\n      disposition: rejected\n      reason: "R"\n',
        "# Hostile drawing\n",
    )

    result = run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    diagram = _rationale_diagram(page)

    assert result.returncode == 0, result.stderr
    assert "<script>alert(2)</script>" not in page
    assert not [tag for tag, _ in page_elements(page) if tag == "script"]
    # The diagram's own words name the unit and count the options; no
    # adopter text is in them.
    assert (
        'aria-label="Rationale of kb-0001: 2 options considered, one chosen"'
    ) in diagram
    # The question is drawn, as a text node, because it fits.
    assert "Which host? https://example.invalid/x</text>" in diagram
    # And it is on the card in full, escaped as text.
    assert (
        '<p class="question" dir="auto">Which host? '
        'https://example.invalid/x</p>'
    ) in page
    _assert_self_contained(page, page_events)
```

Then amend the existing all-diagrams scan,
`test_the_svg_diagrams_never_load_a_resource_or_carry_live_markup`. **Every
`tests/test_render.py` line number in this task is as of the start of this
plan**; the file grows by roughly 800 lines across Tasks 1 to 8, so locate
each by test name and take the numbers as a description of where it was, not
of where it will be. It was at `tests/test_render.py:1098-1153`. Today its
fixture draws two diagrams, so
its forbidden-element and `on*`-attribute scan never sees the third one --
the only diagram built from adopter-authored text. Give `kb-0004` a rationale
by extending its frontmatter (`tests/test_render.py:1111-1118` as of the
start of this plan -- the `write_unit("kb-0004.md", ...)` call inside that
test):

```python
    write_unit(
        "kb-0004.md",
        "id: kb-0004\nevidence: measured\nsupersedes:\n"
        "  - kb-0001\n  - kb-0002\n  - kb-0003\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n"
        'rationale:\n  question: "Which of the three?"\n  options:\n'
        '    - label: "<script>alert(4)</script>"\n'
        '      disposition: chosen\n      reason: "It replaced them."\n'
        '    - label: "Leave all three standing"\n'
        '      disposition: rejected\n      reason: "They disagreed."\n',
        "# The one that replaced them\n",
    )
```

and widen its count (`tests/test_render.py:1137-1139` as of the start of
this plan -- the three lines beginning `svgs = [`):

```python
    svgs = [(tag, attrs) for tag, attrs in elements if tag == "svg"]
    assert len(svgs) == 3, svgs
    assert {attrs.get("class") for _, attrs in svgs} == {
        "freshness", "confluence", "rationale",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_render.py -k "three_diagrams or rationale or too_long or eight_options or chosen_option or never_load_a_resource" -v`
Expected: 12 collected, 8 failing and 4 passing.

**Failing.** Four raise `ValueError: substring not found` from
`page.index('<svg class="rationale"')`, since there is no such element yet:

- `test_text_too_long_to_draw_falls_back_and_stays_on_the_page`
- `test_above_eight_options_every_node_is_numbered`
- `test_the_chosen_option_differs_in_shape_and_in_text_not_only_in_fill`
- `test_hostile_rationale_text_never_becomes_live_markup_inside_the_svg`

and four fail on an assertion:

- `test_the_rationale_diagram_is_drawn_for_that_unit_and_no_other`, on
  `page.count('<svg class="rationale"') == 1`, which is 0
- `test_the_rationale_diagram_element_ids_derive_from_the_unit_and_position`,
  on `'id="rationale-kb-0001-1"' in page`
- `test_all_three_diagrams_carry_a_title_and_a_desc`, on the class set, which
  is `{"freshness", "confluence"}`
- `test_the_svg_diagrams_never_load_a_resource_or_carry_live_markup`, the one
  amended just above, on `len(svgs) == 3`, which is 2

**Passing already, and each must stay green.**
`test_a_page_with_all_three_diagrams_renders_the_same_bytes_twice` passes
over the two diagrams its fixture draws today, which is exactly the point of
it. The `rationale` term also collects Task 7's three card tests --
`test_the_rationale_is_on_the_card_in_full`,
`test_a_unit_with_no_rationale_gets_no_rationale_block` and
`test_hostile_rationale_text_never_becomes_live_markup` -- which concern the
card's own markup and not the drawing, and are unaffected either way.

- [ ] **Step 3: Add the rationale builder to `svg.py`**

Change the module docstring's first line:

```python
"""The three generated diagrams: freshness, confluence and rationale.
```

and append one paragraph to it, before the closing `"""`:

```
Two guarantees, and only two: these drawings are BYTE-DETERMINISTIC, and they
are NOT promised to look the same on every platform. An SVG `<text>` does not
wrap, and no layout computed without font metrics can promise "it fits",
"nothing is truncated" and "it is legible" at once for arbitrary adopter text
-- CJK, emoji sequences, combining marks and right-to-left text break any
character-count estimate. Hence `LABEL_LIMIT` below, which is deterministic
rather than clever.
```

Add the rationale constants after `SHAPES`:

```python
# A label at or under this many characters is drawn inside its node; above
# it, the node draws its number and the full text is read from the list
# beside the diagram. A character count is not a width, which is exactly why
# this is a fallback and not an estimate.
LABEL_LIMIT = 48
# Above this many options every node draws its number, whatever its label
# measures: past this point a column of numbers reads better than a column of
# half-fitting text, and a uniform rule never leaves a reader wondering why
# one node is numbered and its neighbour is not.
NUMBERED_ABOVE = 8
# The rationale diagram is a top-down tree of full-width rows: the question
# across the top, the options indented beneath it. A side-by-side layout is
# what would force two different thresholds -- a narrow left-hand question
# column overflows into the option column long before `LABEL_LIMIT`
# characters -- and one threshold every node can honour is worth more than a
# layout that looks more like a graph.
ROW_HEIGHT = 34
BOX_HEIGHT = 28
OPTION_INDENT = 24
```

And the builder itself, after `confluence`:

```python
def rationale(unit_id, record):
    """One diagram per unit that carries a rationale: the question, then the options.

    A top-down tree of fixed depth: the question in a full-width row across
    the top, one full-width row per option indented beneath it, and one line
    from the question down to each. No edges between options and no edge
    leaving the unit -- a rationale holds no reference to anything, so there
    is no global graph here and no hairball to avoid. Size and edge count are
    linear in the number of options.

    Every node is a full-width row precisely so that ONE threshold governs
    them all. Laid out side by side, the question would sit in a narrow
    column and overflow into the options well before `LABEL_LIMIT`
    characters, and a drawing with two different limits is one a reader
    cannot predict.

    The chosen option is told apart three ways at once, none of them colour:
    a rounded, heavier border, the word `chosen` drawn inside the node, and
    its position in the numbered list beside the diagram.

    Nothing is omitted and nothing is silently truncated. Text at or under
    `LABEL_LIMIT` characters is drawn inline; above it -- or, for an option,
    past `NUMBERED_ABOVE` options, where the whole diagram switches to
    numbers at once -- the node draws `#n`, or `?` for the question, and the
    reader finds the full text beside the drawing. The `<desc>` says so,
    inside the drawing.

    The label, the `aria-label` and the `<desc>` name the unit and count the
    options, and quote no adopter text at all: a `question` is unconstrained,
    so one carrying `://` would put a URL in an SVG attribute (which the
    page's self-containment rule allows nowhere but `a[href]`), and one past
    the threshold would contradict the fallback the drawing has just applied.

    Every coordinate here is an integer, so no float formatting can differ
    between platforms: the same rationale renders the same bytes.

    `record` is a validated rationale mapping: `question` is a non-empty
    string and `options` a list of at least two mappings, each with `label`,
    `disposition` and `reason`, exactly one of them `chosen`. It is named
    `record` rather than `rationale` so that it does not shadow this
    function.
    """
    options = record["options"]
    question = record["question"]
    numbered = len(options) > NUMBERED_ABOVE
    height = ROW_HEIGHT * (len(options) + 1) + 6
    parts = [
        f'<rect x="0" y="0" width="{WIDTH - 4}" height="{BOX_HEIGHT}" rx="4" '
        'fill="none" stroke="currentColor" stroke-width="1"/>',
        '<text x="8" y="18" font-size="12" fill="currentColor">'
        f"{html.escape_text(question if len(question) <= LABEL_LIMIT else '?')}"
        "</text>",
    ]
    for position, option in enumerate(options, start=1):
        y = ROW_HEIGHT * position
        chosen = option["disposition"] == "chosen"
        label = option["label"]
        drawn = (
            label
            if not numbered and len(label) <= LABEL_LIMIT
            else f"#{position}"
        )
        parts.append(
            f'<line x1="12" y1="{BOX_HEIGHT}" x2="{OPTION_INDENT}" '
            f'y2="{y + BOX_HEIGHT // 2}" stroke="currentColor" '
            'stroke-width="1"/>'
            f'<g id="rationale-{html.escape_attribute(unit_id)}-{position}">'
            f'<rect x="{OPTION_INDENT}" y="{y}" '
            f'width="{WIDTH - OPTION_INDENT - 4}" height="{BOX_HEIGHT}" '
            f'rx="{8 if chosen else 0}" fill="none" stroke="currentColor" '
            f'stroke-width="{3 if chosen else 1}"/>'
            f'<text x="{OPTION_INDENT + 8}" y="{y + 18}" font-size="11" '
            f'fill="currentColor">{html.escape_text(option["disposition"])}</text>'
            f'<text x="{OPTION_INDENT + 70}" y="{y + 18}" font-size="12" '
            f'fill="currentColor">{html.escape_text(drawn)}</text>'
            "</g>"
        )
    return _diagram(
        "rationale",
        WIDTH,
        height,
        f"Rationale of {unit_id}: {len(options)} options considered, one chosen",
        "The question across the top, one row per option beneath it, and no "
        "edge between options or out of this unit. The chosen option is drawn "
        "with a rounded, heavier border and the word 'chosen'. A node showing "
        "'#n', or a question showing '?', means the text ran past 48 "
        "characters and could not be drawn: the full text is beside this "
        "drawing -- the question just above it, an option at position n of "
        "the list.",
        "".join(parts),
    )
```

- [ ] **Step 4: Place the diagram on the card**

Replace `_rationale` in `validated_memory/knowledge_view.py` in full -- one
new line in the `return`, and the docstring paragraph that now has a diagram
to be true about:

```python
def _rationale(unit):
    """The unit's rationale: the question, the diagram, then every option in full.

    Drawn only for a unit that carries one -- most units are measurements,
    record no choice between alternatives, and must not be nagged into
    inventing one.

    `rejected` is written as itself and nowhere near the words used for
    supersession or for a failed verdict: an option was considered and not
    chosen HERE. It is not false, and it is not superseded.

    This list is the complete text, always. The diagram above it may fall
    back to `?` for a question or `#n` for a label it cannot fit; nothing it
    shows is missing from here, which is what "never load-bearing" means.
    """
    rationale = unit.data.get("rationale")
    if not rationale:
        return ""
    items = []
    for position, option in enumerate(rationale["options"], start=1):
        items.append(
            f'<li class="option {html.escape_attribute(option["disposition"])}">'
            f'<span class="option-number">{position}</span> '
            f'<span class="disposition">{html.escape_text(option["disposition"])}</span> '
            f'<span class="label" dir="auto">{html.escape_text(option["label"])}</span>'
            f'<p class="reason" dir="auto">{html.escape_text(option["reason"])}</p>'
            "</li>"
        )
    return (
        '<div class="rationale">\n'
        f'<p class="question" dir="auto">'
        f'{html.escape_text(rationale["question"])}</p>\n'
        f"{svg.rationale(unit.unit_id, rationale)}\n"
        '<ul class="options">\n' + "\n".join(items) + "\n</ul>\n"
        "</div>\n"
    )
```

- [ ] **Step 5: Widen the whitelist**

In `tests/test_render.py`, add to `SELF_CONTAINED_ELEMENTS`:

```python
    "g",
```

and to `SELF_CONTAINED_ATTRIBUTES`:

```python
    ("g", "id"),
    ("rect", "rx"),
    ("rect", "stroke-width"),
```

- [ ] **Step 6: Rewrite the `render` paragraph of the CLI reference**

`docs/reference/cli.md:438-455` describes the page as it was before this
plan -- a list of entries, and "Two diagrams". The moment this task lands
that paragraph states a falsehood about shipped behaviour, so it is rewritten
here rather than in plan 3's documentation pass. Replace those lines with
three paragraphs, in the same voice as the ones around them:

```markdown
**`knowledge.html`** opens with an overview and then lists live conclusions
ordered by `id` -- the one ordering that does not move on its own, unlike
freshness or recency, which a routine `probe` would reshuffle on the next
session. The overview counts active units by evidence state crossed with
verdict, reports superseded units as one separate figure rather than folding
them into the same table, maps the corpus by `anchors[].system` (a unit
anchored in several systems is a link in each group; units with no anchors
land in an explicit `unclassified (no anchors)` group, listed last), and
queues the
anchors of active units with no verdict recorded under their current key
`(unit, system, kind, payload)` -- never probed, or probed before the payload
changed. The map is an index of links, never a second copy of the cards,
which is what lets one unit appear in several groups while its card is
rendered exactly once.

Each card carries, in this fixed order: the headline and `id`, badges for
evidence state and verdict, every anchor's envelope and probe history, the
unit's `rationale` where it has one, the supersession chain that led to it
nested as deep as the chain runs, `provenance`, and the body. A superseded
unit never appears at the top level, only inside the chain of whatever
replaced it. The page states how many records the verdict log holds in total
and how many belong to an anchor shown on the page -- two totals, not one,
because the log outlives the corpus (nothing prunes a record whose unit or
anchor is gone), so a single total could never be reconciled by a reader
against the histories in front of them. Probe history itself shows at most 20
records per anchor, most recent first, and each anchor's own history repeats
the disclosure for itself -- `N record(s) for this anchor; showing M` --
which is what actually lets a reader tell a full history from a truncated
one.

Three diagrams, all inline SVG, all generated from the data with no
third-party code, and each carrying a title and a description: a freshness
strip per anchor (one band per probe, in log order, told apart by shape, mark
and colour, and never a time axis), a many-to-one confluence drawn only when
three or more units are superseded at once, and a rationale tree drawn only
for a unit that carries one. Nothing a diagram shows lives only in the
diagram: past 48 characters a question is drawn as `?` and a label as `#n`,
and the full text is read from the card beside the drawing.
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_render.py -v`
Expected: PASS, the amended all-diagrams scan included.

- [ ] **Step 8: Run the full suite**

Run: `python3 -m pytest`
Expected: 437 passed.

- [ ] **Step 9: Commit**

```bash
git add validated_memory/svg.py validated_memory/knowledge_view.py docs/reference/cli.md tests/test_render.py
git commit -m "feat: a generated rationale diagram, with one fallback threshold per node"
```

---

## Decisions taken while planning

The architect reviews these; each is written into the task that carries it.

1. **Module boundaries.** Three new modules -- `corpus.py` (the normalized
   model), `styles.py` (one stylesheet per page), `knowledge_overview.py` (the
   overview block). The diagram builders stay in `svg.py`, because the rules
   the three share are enforced by being written once.
2. **`Corpus` carries the stable reading; the derived views are functions.**
   `counts`, `groups` and `unprobed` are module functions in `corpus.py`,
   added by the task that first renders each, so no task leaves dead code
   behind.
3. **The badges' machine-readable state lives on the `<section>`**, as
   `data-evidence` and `data-verdict` beside `data-state`. The summary's spans
   keep their exact current markup, so `tests/test_render.py:407` stays green,
   and a filter in plan 3 hides a card rather than a span.
4. **Groups carry no id.** Nothing on the page links to a group -- the map's
   links target unit cards -- and `anchors[].system` is validated only as a
   non-empty string, so `team alpha` and `https://host` are both legal
   systems and an id built from one would carry whitespace or a `://`. System
   groups are ordered by name; the group of units with no anchors is built
   separately, labelled `unclassified (no anchors)` and always emitted last.
   The label says what the group is rather than borrowing a word a system may
   be called, because with no id a label is the only thing telling two groups
   apart on the page.
5. **The unprobed queue covers active units only**, because `probe` probes
   active units only.
6. **The extension object is carried, not rendered.** `corpus.extension`
   reaches the view because the spec requires it to, and nothing in this plan
   reads it: the spec fixes the card's order and does not list the adopter's
   declared fields, so rendering them would be this plan inventing a surface
   the design did not ask for. It is carried for plan 3.
7. **Cards stay closed at every level**, exactly as they are today: no
   `<details open>` anywhere. The overview and the map are the reader's entry
   into the page now, and a page that opens with every active card expanded is
   the transcript this design set out to replace.
8. **`dir="auto"` on the HTML rationale text, never on SVG `<text>`**, because
   `dir` is an HTML global attribute with no defined effect on an SVG element.
9. **The freshness strip tells its states apart by shape and by mark**, not
   by colour: `current` a full-height band, `drifted` a full-height band
   stroked `stroke-dasharray="3 2"`, `unknown` a half-height band, each with
   its own mark (`+`, `!`, `?`). Every `<text>` in `svg.py` gains
   `fill="currentColor"` so the drawings follow the page's colour scheme --
   **except the band marks**, which keep `fill="#ffffff"` for contrast over a
   saturated fill of the module's own choosing.
10. **Above 8 options, every rationale node is numbered**, uniformly.
11. **One threshold, and a geometry that can honour it.** The rationale
    diagram is a top-down tree of full-width rows -- the question across the
    top, the options indented beneath it -- so the question obeys the same
    48-character threshold as a label, falling back to `?`. Side by side, a
    narrow question column would overflow into the options at roughly half
    that many characters, which is a limit the drawing would break without
    saying so.
12. **The byte-for-byte page pin covers `memory.html` only.**
    `knowledge.html` changes in five tasks here and again in plan 3, so
    pinning its bytes would turn a regression pin into a chore; it keeps its
    existing determinism pins plus per-feature structural assertions.
13. **The Content-Security-Policy `<meta>` and the narrowed `http-equiv`
    whitelist rule stay in plan 3.** The spec introduces both under "The app
    page's script policy", which is sequencing step 8. Until then the strict
    whitelist forbids `http-equiv` outright, which is stronger than what plan
    3 will relax it to, and `knowledge.html` carries no `<meta http-equiv>`.
    Plan 3 adds the CSP to `knowledge.html` and `knowledge-app.html` and
    narrows the rule in the same commit.
14. **The card's fixed order is exactly the spec's list**, with the
    confluence drawn immediately before the chain it summarizes, which is
    where it is today.
15. **`validate.collect_and_validate` returns `(documents, extension,
    findings)`**; `validate.run`, `validate.gated_source` and `status` unpack
    and discard the middle value.
16. **A diagram's words are built from closed domains only.** A `<title>`,
    an `aria-label` and a `<desc>` name counts, verdicts and unit ids, and
    quote no adopter or probe text: a `question` or a `recorded_at`
    containing `https://` would put a URL in an SVG attribute, which the
    self-containment rule allows nowhere but `a[href]`, and a `question` past
    the fallback threshold would contradict the fallback the drawing had just
    applied. Both reach the page as escaped text instead. This applies to the
    rationale diagram and to the freshness strip's label, whose
    `recorded_at` nothing validates; the confluence keeps its unit ids, which
    `contract.ID_PATTERN` constrains.
17. **The self-containment scan reads a parse event stream, not a flat list
    of start tags.** Three of its rules cannot be expressed over a flat list:
    an element with no attributes, an `<a>` nested inside an `<svg>`, and a
    `<style>` element's own text. `tests/conftest.py` gains a generic
    `page_events` fixture; the policy stays in `tests/test_render.py`.
18. **Two tasks that carry no behaviour are their own commits.** Task 3
    widens one return value and its three unpack sites; Task 4 migrates the
    view onto the model without moving a byte of either page. Each is small
    enough for a reviewer to confirm "nothing moved" by reading it, which is
    the only way that claim ever gets checked.
19. **No reference documentation changes here except one paragraph.**
    `docs/reference/cli.md:438-455` describes the page as a list of entries
    and "Two diagrams"; it states a falsehood about shipped behaviour the
    moment Task 9 lands, so Task 9 rewrites it. Everything else -- the rest of
    `cli.md`, the adoption documents, the ADRs -- is sequencing step 10, in
    plan 3.

Decisions 1-15 and 17-19 are this plan's own; 16 restates for the freshness
strip a rule the spec already fixes for every diagram.

## Done when

- `python3 -m pytest` is green: 437 tests, up from 401.
- `knowledge.html` opens with an overview -- active units counted by evidence
  state crossed with verdict, superseded units counted separately, a map of
  the corpus grouped by anchor system with an explicit
  `unclassified (no anchors)` group last, and the queue of anchors with no
  verdict under their current key.
- A unit's card carries, in this order: headline and id, the evidence and
  verdict badges, the freshness strip per anchor, the rationale, the
  supersession chain, `provenance`, and the verbatim body. Cards stay closed
  at every level, exactly as they are today, and the card renders no declared
  extension field -- `corpus.extension` is carried for plan 3, unread here.
- A unit carrying a `rationale` gets a diagram; a unit without one gets none.
  All three diagrams carry a `<title>` and a `<desc>`, load nothing, escape
  every value, and tell their states apart by shape and text as well as
  colour: a freshness band is full, dashed or half-height as well as marked
  and coloured, and a chosen option is rounded and heavier as well as
  labelled. A diagram's `<title>`, `aria-label` and `<desc>` are built from
  counts, verdicts and unit ids, and quote no adopter or probe text at all.
- A page drawing all three diagrams renders the same bytes twice, reports
  `unchanged`, and leaves `st_mtime_ns` untouched.
- No text is truncated and no option is dropped. One 48-character threshold
  governs every node of the rationale diagram, question included, because
  every node is a full-width row: a long question draws as `?`, a long label
  -- or any label once there are more than eight options -- draws as `#n`, and
  the full text is always beside the drawing.
- The self-containment scan is a real gate over a parse event stream. It
  rejects an element outside its whitelist even when that element carries no
  attributes; an `<a>` or an `href` anywhere inside an `<svg>`, in either
  case and whether or not the `<svg>` is closed; and a `<style>` block whose
  own text carries `@import` or `url(`. Nine hostile pages and one control
  say so.
- No group in the map carries a DOM id, so no adopter-authored system name
  reaches one.
- `docs/reference/cli.md`'s `render` paragraph describes the page this plan
  actually ships: the overview, the map, the unprobed queue, the card's fixed
  order, and three diagrams.
- `memory.html` is byte for byte what it was before this plan, and a test says
  so.
- `knowledge.html` is still inert, self-contained, deterministic, written
  atomically, untouched on a no-op run, and carries no script and no
  timestamp. Every test in "Guarantees kept" is green.
- Nothing about `knowledge-app.html`, the CSP, `init --view --app`, the ADRs,
  the `v2` channel or the release exists yet. That is plan 3.
