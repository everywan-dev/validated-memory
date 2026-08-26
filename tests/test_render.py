"""End-to-end tests for the `render` subcommand."""

import json
import os
import re
import shutil

import pytest

HISTORY_WINDOW = 20


def _log(adopter_dir, records):
    (adopter_dir / "verdicts.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


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


def _scaffold(run_cli, adopter_dir, write_unit):
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\n",
        "# The first conclusion\n\nSupporting prose.\n",
    )


def test_render_writes_the_knowledge_page(run_cli, adopter_dir, write_unit):
    _scaffold(run_cli, adopter_dir, write_unit)

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in page
    assert "1 unit(s) under knowledge/" in page
    assert "render: wrote knowledge.html" in result.stdout


def test_a_second_run_reports_unchanged_and_leaves_the_bytes_identical(
    run_cli, adopter_dir, write_unit
):
    _scaffold(run_cli, adopter_dir, write_unit)
    run_cli("render", cwd=adopter_dir)
    first = (adopter_dir / "knowledge.html").read_bytes()

    stamp = (adopter_dir / "knowledge.html").stat().st_mtime_ns

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0
    assert "render: unchanged knowledge.html" in result.stdout
    assert (adopter_dir / "knowledge.html").read_bytes() == first
    # Identical bytes alone would pass an implementation that rewrites the
    # same content and prints `unchanged`. The file must not be touched.
    assert (adopter_dir / "knowledge.html").stat().st_mtime_ns == stamp


def test_an_error_finding_stops_the_run_and_writes_nothing(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: invented\n")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR" in result.stderr
    assert not (adopter_dir / "knowledge.html").exists()


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
    "desc",
    "h2", "table", "thead", "tbody", "tr", "th", "td",
}


# The complete set of (element, attribute) pairs either view is allowed to
# emit anywhere on the page. This is a real whitelist, not a blacklist of
# attributes known to carry a URL: a blacklist misses a *relative* `href` on
# anything other than `<a>` (`<base href>`, `<link href>`, `<area href>`),
# and `<meta http-equiv="refresh" content="...">`, since `content` cannot be
# blacklisted -- the viewport meta uses it legitimately. Walking the parsed
# document against this set instead means any element or attribute neither
# view emits today fails the moment it appears, whatever it is called.
#
# `("a", "href")` is the only pair permitted to carry an external URL --
# checked separately, below, since membership here says nothing about a
# pair's *value*.
SELF_CONTAINED_ATTRIBUTES = {
    ("html", "lang"),
    ("meta", "charset"),
    ("meta", "name"),
    ("meta", "content"),
    ("section", "class"),
    ("section", "id"),
    ("section", "data-unit"),
    ("section", "data-state"),
    ("section", "data-name"),
    ("section", "data-evidence"),
    ("section", "data-verdict"),
    ("span", "dir"),
    ("p", "dir"),
    ("p", "class"),
    ("span", "class"),
    ("code", "class"),
    ("pre", "class"),
    ("ul", "class"),
    ("li", "class"),
    ("div", "class"),
    ("a", "href"),
    ("a", "target"),
    ("a", "rel"),
    ("svg", "class"),
    ("svg", "role"),
    # `html.parser.HTMLParser` lowercases attribute names, so the source's
    # `viewBox` is observed here as `viewbox`.
    ("svg", "viewbox"),
    ("svg", "width"),
    ("svg", "height"),
    ("svg", "aria-label"),
    ("rect", "x"),
    ("rect", "y"),
    ("rect", "width"),
    ("rect", "height"),
    ("rect", "fill"),
    ("rect", "stroke"),
    ("rect", "stroke-dasharray"),
    ("text", "x"),
    ("text", "y"),
    ("text", "font-size"),
    ("text", "fill"),
    ("text", "text-anchor"),
    ("line", "x1"),
    ("line", "y1"),
    ("line", "x2"),
    ("line", "y2"),
    ("line", "stroke"),
    ("table", "class"),
    ("tr", "class"),
    ("th", "scope"),
    ("td", "class"),
    ("line", "stroke-width"),
}


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
# attribute loop exactly as `<script>` does. `bare_script` is the element
# check's own hole: no attributes at all, so the attribute loop never runs.
# `anchor_inside_svg` and `uppercase_anchor_inside_svg` are the nesting rule,
# case-folded and not: an `<a>` that is legal outside an `<svg>` and
# forbidden inside one. `unclosed_svg` is not another nesting case -- it
# proves the depth counter itself, not just the rule it drives: its `<a>` is
# only ever "inside" the `<svg>` because the `<svg>` never closes, so the
# depth never decrements, and it must still be rejected with the same
# "an <a> inside an <svg>" message as the closed cases above. The positive
# control below pins the other side of that same fact: a real `<a href>`,
# placed after an `<svg>` that DOES close, is accepted. `style_import` is
# the last hole, a `<style>` element whose own text fetches from the
# network.
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
    # Never closed: `<text>` is the svg's only real child, and the `<a>`
    # that follows would be a legitimate link after a closed `</svg>` --
    # which is exactly what the positive control below accepts. Here the
    # `</svg>` never comes, so the depth counter never decrements and the
    # `<a>` is still counted as nested: this proves the counter itself, not
    # just the nesting rule, which `anchor_inside_svg` above already covers
    # with a `<svg>` that does close.
    "unclosed_svg": (
        '<svg class="rationale"><text x="0" y="0">ok</text>'
        '<a href="#unit-kb-0001">doc</a>'
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
    # hostile body rather than proving the scan rejects everything. The
    # trailing `<svg>...</svg>` followed by a real `<a href>` pins the other
    # side of `unclosed_svg`: a link of the same shape, after an `<svg>`
    # that DOES close, is accepted -- so `unclosed_svg`'s rejection is shown
    # to come from the missing close, not from something else about that
    # shape.
    _assert_self_contained(
        _wrapped(
            '<p class="basis">Basis: 0 unit(s) under knowledge/</p>'
            '<svg class="rationale"><text x="0" y="0">ok</text></svg>'
            '<a href="https://example.invalid/doc">doc</a>'
        ),
        page_events,
    )


def test_every_unit_has_a_section_with_its_headline_and_grades(
    run_cli, adopter_dir, write_unit, page_elements
):
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: hypothesis\n",
        "# A claim worth checking\n\nProse.\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    sections = [
        attrs for tag, attrs in page_elements(page)
        if tag == "section" and attrs.get("class") == "unit"
    ]

    assert [attrs["data-unit"] for attrs in sections] == ["kb-0001"]
    assert sections[0]["data-state"] == "active"
    assert "A claim worth checking" in page
    assert "hypothesis" in page


def test_a_unit_without_a_heading_falls_back_to_its_id(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n", "Just prose.\n")

    run_cli("render", cwd=adopter_dir)

    assert "kb-0001" in (adopter_dir / "knowledge.html").read_text(encoding="utf-8")


def test_hostile_content_never_becomes_live_markup(
    run_cli, adopter_dir, write_unit, page_elements
):
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nprovenance:\n  - 'a \"quoted\" source'\n",
        "# Title\n\n<script>alert(1)</script>\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert not [tag for tag, _ in page_elements(page) if tag == "script"]


def test_only_an_anchor_href_ever_carries_an_external_url(
    run_cli, adopter_dir, write_unit, page_elements, page_events
):
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nprovenance:\n  - https://example.invalid/doc\n",
        "# Title\n\nProse.\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    elements = _assert_self_contained(page, page_events)

    assert any(
        tag == "a" and attrs.get("href") == "https://example.invalid/doc"
        for tag, attrs in elements
    )


def test_a_hostile_provenance_scheme_is_text_and_never_a_link(
    run_cli, adopter_dir, write_unit, page_elements
):
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nprovenance:\n  - 'javascript:alert(1)'\n",
        "# Title\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    # The contract validates nothing about a provenance entry, and escaping
    # does not neutralise a scheme -- only the link would arm it.
    assert "javascript:alert(1)" in page
    assert not [
        attrs for tag, attrs in page_elements(page)
        if tag == "a" and attrs.get("href", "").startswith("javascript:")
    ]


def test_a_hostile_anchor_payload_is_text_and_never_live_markup(
    run_cli, adopter_dir, write_unit, page_elements
):
    # `payload` is a mapping the contract checks is present as one and never
    # looks inside: the probe interprets its contents, not the contract. So
    # arbitrary structure -- here, markup nested two levels deep -- reaches
    # the page from inside the otherwise-validated layer.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: gitlab\n"
        "    kind: file-hash\n"
        "    captured_at: 2026-08-01T00:00:00Z\n"
        "    payload:\n"
        '      note: "<script>alert(1)</script>"\n',
        "# Title\n",
    )

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    assert "gitlab" in page
    assert "file-hash" in page
    assert "2026-08-01T00:00:00Z" in page
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert not [tag for tag, _ in page_elements(page) if tag == "script"]


def test_the_anchor_payload_is_rendered_as_json_not_a_python_repr(
    run_cli, adopter_dir, write_unit
):
    # A reader of this page has no Python: `json.dumps` is the form `probe`
    # itself writes into the log, and the form both a person and a JSON
    # parser can read back, unlike a Python `repr`.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n"
        "    payload:\n      ref: main\n",
        "# Title\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert '{"ref": "main"}' in page
    assert "{'ref': 'main'}" not in page


def test_a_verdict_log_record_that_is_not_json_is_reported_not_raised(
    run_cli, adopter_dir, write_unit
):
    # `knowledge_view.build` reads the service view through the same log
    # `derive` reads. A log it cannot parse is a finding naming the file and
    # line, not a traceback: the person opening this page has no repository
    # to read a stack trace against.
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n")
    (adopter_dir / "verdicts.jsonl").write_text("{not json}\n", encoding="utf-8")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "verdicts.jsonl:1" in result.stderr
    assert "ERROR" in result.stderr
    assert not (adopter_dir / "knowledge.html").exists()


def test_a_verdict_log_that_is_not_utf8_is_reported_without_a_line_number(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n")
    (adopter_dir / "verdicts.jsonl").write_bytes(b"\xff\xfe not utf-8\n")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    # No line number: the fault is the file's, not one line's -- rendered as
    # `verdicts.jsonl: log: …`, never `verdicts.jsonl:<N>: log: …`.
    assert "verdicts.jsonl: log:" in result.stderr
    assert not re.search(r"verdicts\.jsonl:\d", result.stderr)
    assert "ERROR" in result.stderr
    assert not (adopter_dir / "knowledge.html").exists()


def test_the_history_window_shows_twenty_and_states_the_true_total(
    run_cli, adopter_dir, write_unit
):
    # 25 probes of one anchor: only the most recent 20 are shown, newest
    # first, but the log's total and the anchor's own total both count all 25.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2025-12-01T00:00:00Z\n    payload: {}\n",
        "# Title\n",
    )
    _log(adopter_dir, [
        {"unit": "kb-0001", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "current", "recorded_at": f"2026-01-{day:02d}T00:00:00Z"}
        for day in range(1, 26)
    ])

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert page.count('class="record"') == HISTORY_WINDOW
    assert "25 record(s)" in page
    assert "of which 25 belong to an anchor shown below" in page
    assert "25 record(s) for this anchor; showing 20." in page
    assert "2026-01-25T00:00:00Z" in page
    assert "2026-01-01T00:00:00Z" not in page


def test_a_record_without_a_payload_is_never_attributed_to_an_anchor(
    run_cli, adopter_dir, write_unit
):
    # The rule that matters most: a record written before payloads were
    # recorded carries no `payload` field at all, and NO anchor reads it --
    # not even one whose own payload happens to be empty (`{}`), because
    # `{}` and "absent" are different keys. The record still counts toward
    # the log's total, but not toward the anchor's, and the anchor it would
    # have matched on `(system, kind)` alone stays `unknown`.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# Title\n",
    )
    _log(adopter_dir, [
        {"unit": "kb-0001", "system": "repo", "kind": "git_ref",
         "verdict": "current", "recorded_at": "2026-01-01T00:00:00Z"},
    ])

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert page.count('class="record"') == 0
    assert "1 record(s)" in page
    assert "of which 0 belong to an anchor shown below" in page
    assert "0 record(s) for this anchor; showing 0." in page
    assert '<span class="verdict">unknown</span>' in page


def test_a_record_whose_payload_is_null_stops_render_and_writes_nothing(
    run_cli, adopter_dir, write_unit
):
    # `_group_history` treats "no `payload` key" as "predates payloads" and
    # skips it -- safe only because `service_view()` (through `_keyed`)
    # rejects an explicit `payload: null` before grouping ever sees a
    # record. `derive` already pins this rule for its own read of the log
    # (`tests/test_derive.py`); this pins it for `render`'s, which reads the
    # log through a separate call of its own.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# Title\n",
    )
    (adopter_dir / "verdicts.jsonl").write_text(
        '{"unit": "kb-0001", "system": "repo", "kind": "git_ref", '
        '"payload": null, "verdict": "current", '
        '"recorded_at": "2026-01-01T00:00:00Z"}\n',
        encoding="utf-8",
    )

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "verdicts.jsonl:1" in result.stderr
    assert "'payload' field is not a mapping" in result.stderr
    assert not (adopter_dir / "knowledge.html").exists()
    assert not (adopter_dir / "memory.html").exists()


def test_a_superseded_unit_appears_only_inside_its_successor(
    run_cli, adopter_dir, write_unit, page_elements
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: hypothesis\n", "# Old\n")
    write_unit(
        "kb-0002.md",
        "id: kb-0002\nevidence: measured\nsupersedes:\n  - kb-0001\n",
        "# New\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    sections = [
        attrs for tag, attrs in page_elements(page)
        if tag == "section" and attrs.get("class") in {"unit", "unit superseded"}
    ]

    top = [a for a in sections if a.get("class") == "unit"]
    assert [a["data-unit"] for a in top] == ["kb-0002"]
    assert any(
        a["data-unit"] == "kb-0001" and a["data-state"] == "superseded by kb-0002"
        for a in sections
    )


def test_a_unit_superseded_twice_is_rendered_once_and_referenced_after(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: hypothesis\n", "# Old\n")
    for new in ("kb-0002", "kb-0003"):
        write_unit(
            f"{new}.md",
            f"id: {new}\nevidence: measured\nsupersedes:\n  - kb-0001\n",
            f"# {new}\n",
        )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert page.count('data-unit="kb-0001"') == 1
    assert '<a href="#unit-kb-0001">' in page


def test_a_chain_three_deep_nests_correctly_and_renders_each_unit_once(
    run_cli, adopter_dir, write_unit
):
    # The one piece of non-obvious control flow on this branch: the walk is
    # iterative with an explicit stack, not recursive. A chain three deep is
    # enough to prove the nesting comes out right without re-testing the
    # 200-deep run already done by hand.
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: hypothesis\n", "# Oldest\n")
    write_unit(
        "kb-0002.md",
        "id: kb-0002\nevidence: measured\nsupersedes:\n  - kb-0001\n",
        "# Middle\n",
    )
    write_unit(
        "kb-0003.md",
        "id: kb-0003\nevidence: measured\nsupersedes:\n  - kb-0002\n",
        "# Newest\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    # Each unit renders exactly once.
    assert page.count('id="unit-kb-0001"') == 1
    assert page.count('id="unit-kb-0002"') == 1
    assert page.count('id="unit-kb-0003"') == 1

    # Correct nesting: kb-0003 (top) contains kb-0002 inside its own chain,
    # which contains kb-0001 inside its own chain -- each one level deeper.
    assert (
        '<div class="chain">\n<section class="unit superseded" id="unit-kb-0002"'
        in page
    )
    assert (
        '<div class="chain">\n<section class="unit superseded" id="unit-kb-0001"'
        in page
    )
    index_3 = page.index('id="unit-kb-0003"')
    index_2 = page.index('id="unit-kb-0002"')
    index_1 = page.index('id="unit-kb-0001"')
    assert index_3 < index_2 < index_1

    # No repeat reference anywhere: a straight chain never re-enters a unit.
    assert 'class="repeat"' not in page


def test_a_diamond_below_one_root_renders_the_shared_unit_once(
    run_cli, adopter_dir, write_unit
):
    # kb-0004 supersedes both kb-0002 and kb-0003, which both supersede
    # kb-0001: a diamond, not a plain chain. `_unit_section` marks a unit
    # `rendered` globally as soon as it is first reached, so the walk down
    # kb-0004's second branch must find kb-0001 already rendered and emit an
    # internal reference instead of a second copy -- a regression here (back
    # to recursing, or dropping the shared `rendered` set) would either blow
    # the stack or double-render kb-0001 silently.
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: hypothesis\n", "# Shared root\n")
    write_unit(
        "kb-0002.md",
        "id: kb-0002\nevidence: measured\nsupersedes:\n  - kb-0001\n",
        "# Left branch\n",
    )
    write_unit(
        "kb-0003.md",
        "id: kb-0003\nevidence: measured\nsupersedes:\n  - kb-0001\n",
        "# Right branch\n",
    )
    write_unit(
        "kb-0004.md",
        "id: kb-0004\nevidence: measured\nsupersedes:\n  - kb-0002\n  - kb-0003\n",
        "# Confluence\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    # Each unit -- including the one two branches point at -- renders
    # exactly once.
    for unit_id in ("kb-0001", "kb-0002", "kb-0003", "kb-0004"):
        assert page.count(f'id="unit-{unit_id}"') == 1, unit_id

    # Correct nesting: kb-0004 is the only top-level section, and both
    # branches are inside it.
    index_4 = page.index('id="unit-kb-0004"')
    index_2 = page.index('id="unit-kb-0002"')
    index_3 = page.index('id="unit-kb-0003"')
    index_1 = page.index('id="unit-kb-0001"')
    assert index_4 < index_2
    assert index_4 < index_3

    # The second time the walk reaches kb-0001 (down the branch that is not
    # the first to reach it), it is an internal reference -- one `<p
    # class="repeat">` linking to the section already rendered on the first
    # branch, not a second `<section>`.
    assert page.count('class="repeat"') == 1
    assert page.count('<a href="#unit-kb-0001">') == 1
    repeat_index = page.index('class="repeat"')
    assert repeat_index > index_1, "the repeat must come after the real section"


def test_the_memory_page_lists_entries_with_their_references(
    run_cli, adopter_dir, write_unit, write_memory, write_index, page_elements
):
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory("coffee.md", "name: coffee\ndescription: oat milk\nmetadata:\n  type: user\n",
                 "Related: [[tea]].\n")
    write_memory("tea.md", "name: tea\ndescription: green\nmetadata:\n  type: user\n")
    write_index("- [Coffee](coffee.md) — oat milk\n- [Tea](tea.md) — green\n")

    result = run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    entries = [attrs for tag, attrs in page_elements(page)
               if tag == "section" and attrs.get("class") == "entry"]

    assert result.returncode == 0, result.stderr
    assert [attrs["data-name"] for attrs in entries] == ["coffee", "tea"]
    assert '<a href="#entry-name-tea">' in page
    assert "render: wrote memory.html" in result.stdout


def test_the_memory_basis_line_names_the_path_like_the_knowledge_page_does(
    run_cli, adopter_dir, write_unit, write_memory, write_index
):
    # `knowledge.html`'s basis line names the path units were read under
    # ("... under knowledge/"); `memory.html`'s omitted it. The two pages
    # should agree on what "basis" discloses.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory("coffee.md", "name: coffee\ndescription: oat milk\nmetadata:\n  type: user\n")
    write_index("- [Coffee](coffee.md) — oat milk\n")

    run_cli("render", cwd=adopter_dir)
    knowledge_page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    memory_page = (adopter_dir / "memory.html").read_text(encoding="utf-8")

    assert "1 unit(s) under knowledge/" in knowledge_page
    assert "1 memory file(s) under memory/" in memory_page


def test_an_unresolved_wikilink_is_marked_rather_than_linked(
    run_cli, adopter_dir, write_unit, write_memory, write_index
):
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory("coffee.md", "name: coffee\ndescription: oat milk\nmetadata:\n  type: user\n",
                 "Related: [[nothing-here]].\n")
    write_index("- [Coffee](coffee.md) — oat milk\n")

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")

    assert 'class="unresolved"' in page
    assert '<a href="#entry-nothing-here">' not in page


def test_an_undeclared_name_falling_back_to_the_filename_does_not_collide_with_a_declared_name(
    run_cli, adopter_dir, write_unit, write_memory, write_index, page_elements
):
    # `alpha.md` declares no `name`, so its identity falls back to its
    # filename, "alpha". `other.md` declares `name: alpha` -- a real
    # declared name, just not `alpha.md`'s own (a `lint` ERROR the memory
    # view does not gate on, since it does not enforce). Before the first
    # fix both entries anchored at id="entry-alpha": a `[[alpha]]` reference
    # resolves through `by_name` to `other.md`, but the href built from the
    # same collided id would have landed the reader on `alpha.md` instead.
    # The two now anchor in disjoint namespaces (`entry-path-<relpath>` vs.
    # `entry-name-<name>`), so this stays collision-free by construction.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory("alpha.md", "description: no name here\nmetadata:\n  type: user\n")
    write_memory(
        "other.md",
        "name: alpha\ndescription: the real alpha\nmetadata:\n  type: user\n",
    )
    write_memory(
        "gamma.md",
        "name: gamma\ndescription: refers to alpha\nmetadata:\n  type: user\n",
        "See [[alpha]].\n",
    )
    write_index(
        "- [Alpha](alpha.md) — no name here\n"
        "- [Other](other.md) — the real alpha\n"
        "- [Gamma](gamma.md) — refers to alpha\n"
    )

    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    section_ids = [
        attrs["id"] for tag, attrs in elements
        if tag == "section" and attrs.get("class") == "entry"
    ]
    assert len(section_ids) == len(set(section_ids)), f"duplicate ids: {section_ids}"
    assert "entry-path-alpha.md" in section_ids
    assert "entry-name-alpha" in section_ids

    outgoing_hrefs = [
        attrs["href"] for tag, attrs in elements
        if tag == "a" and attrs.get("href", "").startswith("#entry")
    ]
    assert "#entry-name-alpha" in outgoing_hrefs
    assert "#entry-path-alpha.md" not in outgoing_hrefs


def test_the_fallback_and_declared_schemes_do_not_collide_through_the_file_token(
    run_cli, adopter_dir, write_unit, write_memory, write_index, page_elements
):
    # The residual collision the first fix left standing: `alpha.md`
    # declares no `name`, so under the old scheme it fell back to
    # `entry-file-<filename>` = "entry-file-alpha". `other.md` declares
    # `name: file-alpha` for real -- a legal, if unusual, name -- which
    # under the old scheme anchored at `entry-<name>` = "entry-file-alpha"
    # too: the discriminator token lived inside the same string space the
    # two schemes shared, so a declared name starting with "file-" could
    # walk straight into the fallback scheme's territory. The fixed scheme
    # puts the token before either payload (`entry-name-` / `entry-path-`),
    # so no declared name can ever reach into the fallback namespace or
    # back.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory("alpha.md", "description: no name here\nmetadata:\n  type: user\n")
    write_memory(
        "other.md",
        "name: file-alpha\ndescription: shares the old fallback token\n"
        "metadata:\n  type: user\n",
    )
    write_index(
        "- [Alpha](alpha.md) — no name here\n"
        "- [Other](other.md) — shares the old fallback token\n"
    )

    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    section_ids = [
        attrs["id"] for tag, attrs in elements
        if tag == "section" and attrs.get("class") == "entry"
    ]
    assert len(section_ids) == len(set(section_ids)), f"duplicate ids: {section_ids}"


def test_two_undeclared_entries_sharing_a_filename_in_different_subdirectories_get_distinct_anchors(
    run_cli, adopter_dir, write_unit, write_memory, write_index, page_elements
):
    # `memory.documents` collects files recursively (`target.rglob('*.md')`),
    # so two memories can share a bare filename as long as they sit in
    # different subdirectories -- `lint` reports that as its own collision,
    # but this view does not enforce and renders both anyway. Neither
    # declares a `name`, so both fall back; keying the fallback anchor on
    # the filename ("dup") would collide even after the file/name-token fix
    # above, because the filename alone does not tell the two apart. Keying
    # on `relpath` does, since `relpath` includes the subdirectory.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory("first/dup.md", "description: first copy\nmetadata:\n  type: user\n")
    write_memory("second/dup.md", "description: second copy\nmetadata:\n  type: user\n")
    write_index(
        "- [Dup one](first/dup.md) — first copy\n"
        "- [Dup two](second/dup.md) — second copy\n"
    )

    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    dup_ids = [
        attrs["id"] for tag, attrs in elements
        if tag == "section" and attrs.get("class") == "entry"
        and attrs.get("data-name") == "dup"
    ]
    assert len(dup_ids) == 2, f"expected both undeclared 'dup' entries, got {dup_ids}"
    assert dup_ids[0] != dup_ids[1], f"duplicate anchor for both copies: {dup_ids}"
    assert set(dup_ids) == {"entry-path-first/dup.md", "entry-path-second/dup.md"}


def test_an_outgoing_href_to_a_declared_name_matches_that_sections_id_exactly(
    run_cli, adopter_dir, write_unit, write_memory, write_index, page_elements
):
    # A sanity check that the rename did not break navigation: a link from
    # one entry to another still lands on the linked entry's own anchor,
    # byte for byte, under the new `entry-name-<name>` spelling.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory(
        "target.md",
        "name: target-name\ndescription: the link target\nmetadata:\n  type: user\n",
    )
    write_memory(
        "source.md",
        "name: source-name\ndescription: links out\nmetadata:\n  type: user\n",
        "See [[target-name]].\n",
    )
    write_index(
        "- [Target](target.md) — the link target\n"
        "- [Source](source.md) — links out\n"
    )

    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    target_ids = [
        attrs["id"] for tag, attrs in elements
        if tag == "section" and attrs.get("class") == "entry"
        and attrs.get("data-name") == "target-name"
    ]
    assert len(target_ids) == 1
    target_id = target_ids[0]
    assert target_id == "entry-name-target-name"

    outgoing_hrefs = [
        attrs["href"] for tag, attrs in elements
        if tag == "a" and attrs.get("href", "").startswith("#entry")
    ]
    assert f"#{target_id}" in outgoing_hrefs


def test_two_entries_declaring_the_same_name_both_render_at_the_shared_anchor(
    run_cli, adopter_dir, write_unit, write_memory, write_index, page_elements
):
    # Pins the one exposure this fix does not close: `memory.resolution`
    # builds `by_name` as a plain set of declared names with no ambiguity
    # check (the `len(names) == 1` filter lives only on the separate
    # `by_filename` table), so two documents declaring the same `name`
    # both land in `by_name` and both anchor at the identical
    # `entry-name-<name>`. `lint` reports a duplicate declared `name` as
    # an ERROR; this view does not gate on it (the "does not enforce"
    # rule at the top of this module), so it renders both anyway rather
    # than silently dropping one. No anchor scheme can disambiguate them
    # -- a `[[wikilink]]` to the shared name is itself ambiguous -- so the
    # assertion here is honest, not a fix: both entries appear, and both
    # carry the same id.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory(
        "first.md",
        "name: alpha\ndescription: first claimant\nmetadata:\n  type: user\n",
    )
    write_memory(
        "second.md",
        "name: alpha\ndescription: second claimant\nmetadata:\n  type: user\n",
    )
    write_index(
        "- [First](first.md) — first claimant\n"
        "- [Second](second.md) — second claimant\n"
    )

    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    alpha_entries = [
        attrs for tag, attrs in elements
        if tag == "section" and attrs.get("class") == "entry"
        and attrs.get("data-name") == "alpha"
    ]
    assert len(alpha_entries) == 2, (
        f"expected both claimants rendered, got {alpha_entries}"
    )
    assert [attrs["id"] for attrs in alpha_entries] == [
        "entry-name-alpha", "entry-name-alpha",
    ]


def test_an_unresolved_reference_is_not_linked_from_the_incoming_side_either(
    run_cli, adopter_dir, write_unit, write_memory, write_index, page_elements
):
    # `[[noname]]` targets a memory with no declared `name`, so `by_name`
    # resolution -- the only resolution the outgoing list tests against --
    # marks it unresolved, matching `lint`. The incoming list must agree: it
    # is the mirror image of the outgoing list, not a second, looser notion
    # of what counts as a reference. Before the fix, `noname.md`'s own
    # incoming list still lists `other.md` as a referrer (keyed by the raw
    # wikilink text, unfiltered by `resolution.by_name`) and links to it --
    # a live link for the very reference the outgoing side just marked
    # unresolved.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory(
        "noname.md", "description: has no declared name\nmetadata:\n  type: user\n"
    )
    write_memory(
        "other.md",
        "name: other\ndescription: refers to noname\nmetadata:\n  type: user\n",
        "See [[noname]].\n",
    )
    write_index(
        "- [Noname](noname.md) — has no declared name\n"
        "- [Other](other.md) — refers to noname\n"
    )

    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    assert 'class="unresolved"' in page
    # The only entry that could ever link to "other.md" is noname.md's own
    # incoming list -- nothing else on this page names "other". A live link
    # there would be the incoming side treating a reference the outgoing
    # side marked unresolved as if it had resolved.
    assert not [
        attrs for tag, attrs in elements
        if tag == "a" and attrs.get("href") == "#entry-name-other"
    ]


def test_a_missing_memory_index_stops_the_run(run_cli, adopter_dir, write_unit):
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "memory" / "MEMORY.md").unlink()

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "MEMORY.md" in result.stderr
    # Both artifacts are built before either is written: a memory-layer
    # precondition failing must not leave a knowledge.html written from a
    # run that, as a whole, did not succeed.
    assert not (adopter_dir / "knowledge.html").exists()
    assert not (adopter_dir / "memory.html").exists()


def test_a_missing_knowledge_directory_stops_the_run_and_writes_nothing(
    run_cli, adopter_dir
):
    # The mirror case: a curated-layer precondition failing must not leave a
    # memory.html written either. `render` does not render half a project
    # quietly.
    run_cli("init", cwd=adopter_dir)
    shutil.rmtree(adopter_dir / "knowledge")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert not (adopter_dir / "knowledge.html").exists()
    assert not (adopter_dir / "memory.html").exists()


def test_a_memory_file_with_unparseable_frontmatter_is_rendered_not_raised(
    run_cli, adopter_dir, write_unit, write_memory, write_index
):
    # There is no `gated_source` for the memory layer: `render` stops only on
    # what it cannot read (the directory, the index). A document whose
    # frontmatter will not parse is still one of the files that directory
    # holds, so it gets an entry, with the parse error stated in place of the
    # fields that could not be read -- not a traceback, and not silence.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory("broken.md", "name coffee\n")
    write_index("- [Broken](broken.md) — bad frontmatter\n")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    assert "broken" in page
    assert "expected a 'key: value' entry" in page


def test_a_non_string_description_does_not_raise(
    run_cli, adopter_dir, write_unit, write_memory, write_index
):
    # Nothing validated this layer's frontmatter, so `description` can be any
    # JSON type the parser accepts -- here, a list rather than the string
    # `lint` requires. The page must stringify it, not crash trying to
    # `.strip()` or membership-test a value of unknown type.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory(
        "coffee.md",
        "name: coffee\ndescription:\n  - oat\n  - milk\nmetadata:\n  type: user\n",
    )
    write_index("- [Coffee](coffee.md) — a list description\n")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    assert "oat" in page and "milk" in page


def test_hostile_memory_content_never_becomes_live_markup(
    run_cli, adopter_dir, write_unit, write_memory, write_index, page_elements,
    page_events
):
    # `memory_view` renders the same kind of adopter-authored freeform text
    # as `knowledge_view` (body, description, metadata), so it carries the
    # same injection risk -- mirrors the curated layer's hostile-content and
    # URL-whitelist tests above, over `memory.html` instead.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory(
        "coffee.md",
        'name: coffee\ndescription: \'a "quoted" <tag>\'\nmetadata:\n  type: user\n',
        "# Title\n\n<script>alert(1)</script>\n",
    )
    write_index("- [Coffee](coffee.md) — hostile\n")

    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert 'a "quoted" &lt;tag&gt;' in page
    assert "user" in page

    elements = _assert_self_contained(page, page_events)
    assert not [tag for tag, _ in elements if tag == "script"]


def test_a_null_recorded_at_reads_as_absent_in_the_list_and_the_strip_alike(
    run_cli, adopter_dir, write_unit, page_elements
):
    # `recorded_at` is not a key field and nothing validates it, so an
    # explicit `null` is a legal record. Wherever it reaches the page it must
    # spell an absent value the same way -- "" and never the literal word
    # "None" -- and it reaches the page twice: in the history list and in
    # each band's own <title>, both through `html.escape_text`. The strip's
    # `aria-label` no longer quotes it at all, being built from the record
    # count and the last verdict, so the assertion on that attribute is now
    # a guard that the label stays free of record fields.
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# Title\n",
    )
    _log(adopter_dir, [
        {"unit": "kb-0001", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "current", "recorded_at": None},
    ])

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    assert "None" not in page
    strip = next(
        attrs for tag, attrs in elements
        if tag == "svg" and attrs.get("class") == "freshness"
    )
    assert "None" not in strip["aria-label"]


def test_the_freshness_strip_is_drawn_and_ends_at_the_last_record(
    run_cli, adopter_dir, write_unit, page_elements
):
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo\n    kind: git_ref\n"
        "    captured_at: 2026-01-01T00:00:00Z\n    payload: {}\n",
        "# Title\n",
    )
    _log(adopter_dir, [
        {"unit": "kb-0001", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "current", "recorded_at": "2026-01-01T00:00:00Z"},
        {"unit": "kb-0001", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "drifted", "recorded_at": "2026-02-01T00:00:00Z"},
    ])

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert any(tag == "svg" for tag, _ in page_elements(page))
    assert "2026-02-01T00:00:00Z" in page
    assert "drifted" in page


def test_no_confluence_is_drawn_for_a_two_link_chain(
    run_cli, adopter_dir, write_unit, page_elements
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: hypothesis\n", "# Old\n")
    write_unit(
        "kb-0002.md",
        "id: kb-0002\nevidence: measured\nsupersedes:\n  - kb-0001\n",
        "# New\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert not [attrs for tag, attrs in page_elements(page)
                if tag == "svg" and attrs.get("class") == "confluence"]


def test_a_confluence_is_drawn_when_three_units_are_superseded_at_once(
    run_cli, adopter_dir, write_unit, page_elements
):
    run_cli("init", cwd=adopter_dir)
    for old in ("kb-0001", "kb-0002", "kb-0003"):
        write_unit(f"{old}.md", f"id: {old}\nevidence: hypothesis\n", f"# {old}\n")
    write_unit(
        "kb-0004.md",
        "id: kb-0004\nevidence: measured\nsupersedes:\n"
        "  - kb-0001\n  - kb-0002\n  - kb-0003\n",
        "# The one that replaced them\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert [attrs for tag, attrs in page_elements(page)
            if tag == "svg" and attrs.get("class") == "confluence"]


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


SVG_FORBIDDEN_ELEMENTS = {"use", "image", "iframe", "object", "embed", "script"}


def test_the_svg_diagrams_never_load_a_resource_or_carry_live_markup(
    run_cli, adopter_dir, write_unit, page_elements, page_events
):
    # A page where BOTH diagrams are actually drawn: a confluence (three
    # units superseded at once) and a freshness strip (several probes of one
    # anchor). The generic URL-whitelist and hostile-content tests elsewhere
    # in this file exercise pages with no anchors and no three-way
    # supersession, so they never contain an <svg> at all -- they would pass
    # unchanged even if `svg.py` started emitting a `<use href=...>`. This
    # test exists so that scan actually has an SVG to scan.
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
    # A hostile `recorded_at` -- angle brackets and a quote -- on the LAST
    # record: the band's own <title> is the one place the strip shows a
    # record field at all, so it is the sharpest place a missed escape would
    # show up as live markup. The strip's aria-label is built from a count
    # and a verdict and quotes no record field.
    _log(adopter_dir, [
        {"unit": "kb-0004", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "current", "recorded_at": "2026-01-01T00:00:00Z"},
        {"unit": "kb-0004", "system": "repo", "kind": "git_ref", "payload": {},
         "verdict": "drifted",
         "recorded_at": '2026-02-01T00:00:00Z"><script>alert(1)</script>'},
    ])

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    # The page really does draw both diagrams -- asserted by count, not just
    # "any svg", so this test cannot go vacuous the way the reused one did.
    svgs = [(tag, attrs) for tag, attrs in elements if tag == "svg"]
    assert len(svgs) == 2, svgs
    assert {attrs.get("class") for _, attrs in svgs} == {"freshness", "confluence"}

    # The hostile `recorded_at` reaches the page escaped, inside the strip's
    # <title>, never as an unescaped tag.
    assert '"><script>alert(1)</script>' not in page
    assert (
        '2026-02-01T00:00:00Z"&gt;&lt;script&gt;alert(1)&lt;/script&gt;' in page
    )

    for tag, attrs in elements:
        assert tag not in SVG_FORBIDDEN_ELEMENTS, f"<{tag}> must never appear"
        for name, value in attrs.items():
            assert not name.lower().startswith("on"), f"{tag}[{name}] is an event attribute"

    _assert_self_contained(page, page_events)


def test_an_outgoing_href_matches_a_spaced_and_punctuated_name_anchor(
    run_cli, adopter_dir, write_unit, write_memory, write_index, page_elements
):
    # Unlike a curated unit's `id`, nothing constrains a memory's `name`: a
    # real corpus (ADR 0001) has names shaped like titles, with spaces, dots,
    # capitals and parentheses. The anchor (`id="entry-name-<name>"`) and a
    # resolved wikilink's `href` are built by the same escaping call on the
    # same string, so they should agree byte for byte -- verified here
    # rather than assumed.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_memory(
        "release-owner.md",
        "name: 'Release owner (approved)'\n"
        "description: who owns releases\nmetadata:\n  type: user\n",
    )
    write_memory(
        "coffee.md",
        "name: coffee\ndescription: oat milk\nmetadata:\n  type: user\n",
        "See [[Release owner (approved)]].\n",
    )
    write_index(
        "- [Release owner](release-owner.md) — who owns releases\n"
        "- [Coffee](coffee.md) — oat milk\n"
    )

    result = run_cli("render", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    page = (adopter_dir / "memory.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    section_ids = {
        attrs["id"]
        for tag, attrs in elements
        if tag == "section" and attrs.get("class") == "entry"
    }
    outgoing_hrefs = [
        attrs["href"]
        for tag, attrs in elements
        if tag == "a" and attrs.get("href", "").startswith("#entry-")
    ]

    assert outgoing_hrefs, "expected the wikilink to resolve to a link"
    for href in outgoing_hrefs:
        assert href[1:] in section_ids, f"{href} does not match any entry id"


def test_only_existing_regenerates_what_is_there_and_creates_nothing(
    run_cli, adopter_dir, write_unit
):
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "knowledge.html").write_text("stale\n", encoding="utf-8")

    result = run_cli("render", "--only-existing", cwd=adopter_dir)

    assert result.returncode == 0
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") != "stale\n"
    assert not (adopter_dir / "memory.html").exists()


def test_an_existing_artifact_that_is_not_valid_utf8_is_overwritten_not_raised(
    run_cli, adopter_dir, write_unit
):
    # `write_if_changed` reads the existing artifact only to decide whether a
    # write is needed. A file it cannot decode is not thereby known to equal
    # what is about to be written, so the safe default is to write over it --
    # never a traceback on a page a reader has no repository to debug.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "knowledge.html").write_bytes(b"\xff\xfe not valid utf-8\n")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert "render: wrote knowledge.html" in result.stdout
    assert (
        (adopter_dir / "knowledge.html")
        .read_text(encoding="utf-8")
        .startswith("<!doctype html>")
    )


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
def test_only_existing_is_fail_open_on_an_unwritable_working_directory(
    run_cli, adopter_dir, write_unit
):
    # The other reproduction: the temporary file itself cannot be written
    # (here, a read-only working directory). Fail-open is documented for
    # `--only-existing`, so this must warn and exit 0, leaving the artifact
    # already on disk exactly as it was -- not a `PermissionError` traceback
    # on every session start.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "knowledge.html").write_text("stale\n", encoding="utf-8")
    adopter_dir.chmod(0o555)
    try:
        result = run_cli("render", "--only-existing", cwd=adopter_dir)
    finally:
        adopter_dir.chmod(0o755)

    assert result.returncode == 0
    assert "Traceback" not in result.stderr
    assert "WARNING" in result.stderr
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") == "stale\n"


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
def test_a_write_failure_gates_when_render_runs_explicitly(
    run_cli, adopter_dir, write_unit
):
    # The mirror case: run explicitly (no `--only-existing`), the same write
    # failure gates as an ERROR does, same as any other finding -- a person
    # asking for the views by hand is entitled to be told they were not
    # written, without a traceback.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "knowledge.html").write_text("stale\n", encoding="utf-8")
    adopter_dir.chmod(0o555)
    try:
        result = run_cli("render", cwd=adopter_dir)
    finally:
        adopter_dir.chmod(0o755)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "ERROR" in result.stderr
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") == "stale\n"


def test_only_existing_is_fail_open_when_the_artifact_cannot_be_replaced(
    run_cli, adopter_dir, write_unit
):
    # The root-proof mirror of the reproduction above: a directory where the
    # artifact belongs. The atomic rename onto it fails with `EISDIR` for
    # every user, root included -- unlike permission bits, which root
    # ignores -- so the fail-open contract stays pinned in the CI container
    # too, where the test above can only be skipped.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "knowledge.html").mkdir()

    result = run_cli("render", "--only-existing", cwd=adopter_dir)

    assert result.returncode == 0
    assert "Traceback" not in result.stderr
    assert "WARNING: knowledge.html: write: file could not be written" in result.stderr
    assert (adopter_dir / "knowledge.html").is_dir()
    assert not list(adopter_dir.glob("knowledge.html.*.tmp"))


def test_a_write_failure_gates_when_the_artifact_cannot_be_replaced(
    run_cli, adopter_dir, write_unit
):
    # The gating half of the same root-proof reproduction: run explicitly and
    # the write failure is an ERROR that gates, still without a traceback.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "knowledge.html").mkdir()

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "ERROR: knowledge.html: write: file could not be written" in result.stderr
    assert (adopter_dir / "knowledge.html").is_dir()
    assert not list(adopter_dir.glob("knowledge.html.*.tmp"))


def test_duplicate_supersedes_entries_count_once_in_the_page(
    run_cli, adopter_dir, write_unit
):
    # The frontmatter subset accepts a list naming the same id three times;
    # the page must not multiply that into a "3 units" confluence of three
    # identical rows. The set of superseded units is what the page states --
    # here one unit, below the three-source threshold, so no confluence at
    # all, exactly as a single-entry list renders.
    _scaffold(run_cli, adopter_dir, write_unit)
    write_unit(
        "kb-0002.md",
        "id: kb-0002\nevidence: measured\nsupersedes:\n"
        "  - kb-0001\n  - kb-0001\n  - kb-0001\n",
        "# The replacement\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert "3 units superseded" not in page
    assert "confluence" not in page


def test_a_memory_file_that_cannot_be_read_is_a_finding_not_a_traceback(
    run_cli, adopter_dir, write_unit
):
    # `documents` reads every memory file; one that is not valid UTF-8 must
    # surface as an ERROR naming the file -- the same posture the unreadable
    # verdict log already gets -- never a traceback.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "memory" / "broken.md").write_bytes(b"\xff\xfe not utf-8\n")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "ERROR" in result.stderr
    assert "memory/broken.md" in result.stderr


def test_only_existing_is_fail_open_on_a_memory_file_that_cannot_be_read(
    run_cli, adopter_dir, write_unit
):
    # The unattended mirror: the same unreadable memory file must warn and
    # exit 0, leaving the active page exactly as it was.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "memory.html").write_text("stale\n", encoding="utf-8")
    (adopter_dir / "memory" / "broken.md").write_bytes(b"\xff\xfe not utf-8\n")

    result = run_cli("render", "--only-existing", cwd=adopter_dir)

    assert result.returncode == 0
    assert "Traceback" not in result.stderr
    assert "WARNING" in result.stderr
    assert (adopter_dir / "memory.html").read_text(encoding="utf-8") == "stale\n"


def test_only_existing_is_fail_open_on_an_invalid_corpus(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: invented\n")
    (adopter_dir / "knowledge.html").write_text("stale\n", encoding="utf-8")

    unattended = run_cli("render", "--only-existing", cwd=adopter_dir)
    explicit = run_cli("render", cwd=adopter_dir)

    assert unattended.returncode == 0
    assert "WARNING" in unattended.stderr
    # Fail-open does NOT mean "publish a page built on rejected data": the
    # artifact already on disk is left exactly as it was.
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") == "stale\n"
    assert explicit.returncode == 1


def test_only_existing_with_neither_artifact_present_is_a_clean_no_op(
    run_cli, adopter_dir, write_unit
):
    # Nobody has activated either view yet: `--only-existing` must not
    # create one, and -- since there is nothing to regenerate -- it must
    # not even read or validate the corpus to get there. An adopter who
    # never ran `init --view` sees no output and no findings from the
    # startup hook.
    _scaffold(run_cli, adopter_dir, write_unit)

    result = run_cli("render", "--only-existing", cwd=adopter_dir)

    assert result.returncode == 0
    assert result.stderr == ""
    assert not (adopter_dir / "knowledge.html").exists()
    assert not (adopter_dir / "memory.html").exists()


def test_only_existing_is_fail_open_on_a_corrupt_verdict_log(
    run_cli, adopter_dir, write_unit
):
    # The broader ruling: unattended mode downgrades every build failure,
    # not only the contract's own ERROR findings. A verdict log this reader
    # cannot parse is one such failure -- same fail-open contract, same
    # "leave the artifact exactly as it was" guarantee.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "knowledge.html").write_text("stale\n", encoding="utf-8")
    (adopter_dir / "verdicts.jsonl").write_text("{not json}\n", encoding="utf-8")

    result = run_cli("render", "--only-existing", cwd=adopter_dir)

    assert result.returncode == 0
    assert "WARNING" in result.stderr
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") == "stale\n"


def test_only_existing_is_fail_open_on_a_missing_memory_index(
    run_cli, adopter_dir, write_unit
):
    # Same ruling, over the memory-layer read precondition rather than the
    # curated-layer contract or the verdict log.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "knowledge.html").write_text("stale\n", encoding="utf-8")
    (adopter_dir / "memory" / "MEMORY.md").unlink()

    result = run_cli("render", "--only-existing", cwd=adopter_dir)

    assert result.returncode == 0
    assert "WARNING" in result.stderr
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") == "stale\n"


def test_only_existing_regenerates_memory_when_only_memory_exists(
    run_cli, adopter_dir, write_unit
):
    # The mirror of the brief's own case: with `knowledge.html` absent and
    # `memory.html` present, only the latter is regenerated and the former
    # is still not created.
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "memory.html").write_text("stale\n", encoding="utf-8")

    result = run_cli("render", "--only-existing", cwd=adopter_dir)

    assert result.returncode == 0
    assert (adopter_dir / "memory.html").read_text(encoding="utf-8") != "stale\n"
    assert not (adopter_dir / "knowledge.html").exists()


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
