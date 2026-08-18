# `render` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `render` subcommand that writes two self-contained, inert HTML
views of an adopter's validated memory, plus the opt-in wiring that keeps them
fresh.

**Architecture:** `render.py` is the subcommand seam: it gates on the same
validation `derive` uses, builds each page entirely in memory, and writes it
only when the bytes change. `html.py` holds escaping and page primitives with
no domain knowledge; `knowledge_view.py` and `memory_view.py` build one page
each from readers that already exist (`validate`, `derive`, `memory`,
`verdicts`); `svg.py` draws the only two diagrams. Nothing re-implements a
reader, because a view that disagrees with the enforcement is worse than no
view.

**Tech Stack:** Python 3, standard library only. pytest for tests, driving the
CLI as a subprocess.

**Spec:** `docs/design/2026-08-18-render-views.md`

## Global Constraints

- Standard library only. No new runtime dependency, ever.
- All content in English: code, comments, CLI output, docs, HTML.
- Tests drive the CLI as a subprocess over fixture trees and never import the
  package's internals. `tests/conftest.py` helpers are fair game.
- Exit codes: `0` clean or WARNING-only, `1` ERROR, `2` usage error.
- No JavaScript in the output, ever. Collapsing uses `<details>`/`<summary>`.
- No generation timestamp anywhere in an artifact.
- An artifact whose content is unchanged is not rewritten.
- The only attribute in a page carrying an external URL is `href` on `<a>`.
- History window: 20 records per `(unit, system, kind)`, most recent first,
  with the true total stated on the page.
- Artifacts: `knowledge.html` and `memory.html`, in the working directory.
- Commit messages: Conventional Commits, in Spanish, matching the repo's log.

---

### Task 1: Register `render` as a subcommand

Clears the red the design document already causes:
`tests/test_skills_structure.py::test_every_documented_command_names_a_real_subcommand`
fails because the spec names `python3 -m validated_memory render`. Do this
first, or an inherited red is indistinguishable from a new test's red.

**Files:**
- Create: `validated_memory/render.py`
- Modify: `validated_memory/cli.py`
- Test: `tests/test_cli.py`, `tests/test_skills_structure.py`

**Interfaces:**
- Produces: `render.run(only_existing, stdout, stderr) -> int`

- [ ] **Step 1: Write the failing test**

In `tests/test_cli.py`, add `"render"` to the module-level list:

```python
SUBCOMMANDS = ["init", "lint", "validate", "derive", "probe", "render"]
```

In `tests/test_skills_structure.py`, add it to the registry the doc check
reads:

```python
REAL_SUBCOMMANDS = {"init", "lint", "validate", "derive", "probe", "render"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_cli.py -q`
Expected: FAIL -- `render` is not a valid subcommand (argparse exits 2).

- [ ] **Step 3: Write the minimal implementation**

Create `validated_memory/render.py`:

```python
"""The `render` subcommand: static HTML views of an adopter's validated memory.

Two artifacts, `knowledge.html` and `memory.html`, written to the working
directory. Each is self-contained and inert: no JavaScript, no request to the
network, nothing to trust in an attachment. See
docs/design/2026-08-18-render-views.md.
"""

from .findings import EXIT_OK

KNOWLEDGE_ARTIFACT = "knowledge.html"
MEMORY_ARTIFACT = "memory.html"


def run(only_existing, stdout, stderr):
    """Render the views. Returns an exit code."""
    return EXIT_OK
```

In `validated_memory/cli.py`, import `render`, add its entry to
`SUBCOMMANDS`, its flag, and its dispatch:

```python
from . import __version__, derive, init, lint, probe, render, validate

SUBCOMMANDS = {
    ...
    "render": "Render static HTML views of the curated and agent-memory layers",
}
```

```python
        if name == "render":
            subparser.add_argument(
                "--only-existing",
                action="store_true",
                help=(
                    "regenerate only the artifacts that already exist, and "
                    "create none (the startup hook's mode: fail-open)"
                ),
            )
```

```python
    if args.command == "render":
        return render.run(args.only_existing, stdout=sys.stdout, stderr=sys.stderr)
```

- [ ] **Step 4: Run the whole suite**

Run: `python3 -m pytest -q`
Expected: PASS, no failures. The design document's red is gone.

- [ ] **Step 5: Commit**

```bash
git add validated_memory/render.py validated_memory/cli.py tests/test_cli.py tests/test_skills_structure.py
git commit -m "feat: registra el subcomando render en el CLI"
```

---

### Task 2: The page shell, and `knowledge.html` written once

**Files:**
- Create: `validated_memory/html.py`
- Create: `validated_memory/knowledge_view.py`
- Modify: `validated_memory/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `validate.gated_source(path, stderr) -> (documents, ok)`,
  `validate.resolve_target(path)`, `findings.EXIT_OK`, `findings.EXIT_ERROR`
- Produces: `html.escape_text(value) -> str`, `html.page(title, body) -> str`,
  `knowledge_view.build(documents, basis) -> str`,
  `render.write_if_changed(path, content) -> "wrote" | "unchanged"`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_render.py`:

```python
"""End-to-end tests for the `render` subcommand."""


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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_render.py -q`
Expected: FAIL -- no `knowledge.html` is written.

- [ ] **Step 3: Write the implementation**

Create `validated_memory/html.py`:

```python
"""HTML primitives: escaping, and the shell every view is poured into.

No domain knowledge lives here. The one rule this module exists to enforce is
that text from the repository is escaped before it becomes markup, never
after: a `<pre>` block does not escape anything by itself.
"""

import html as _html

STYLESHEET = """
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


def escape_text(value):
    """Escape `value` for use as text content. Never returns markup."""
    if value is None:
        return ""
    return _html.escape(str(value), quote=False)


def escape_attribute(value):
    """Escape `value` for use inside a double-quoted attribute."""
    return _html.escape(str(value), quote=True)


def page(title, body):
    """Wrap `body` in the document shell. `title` is escaped; `body` is markup."""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape_text(title)}</title>\n"
        f"<style>{STYLESHEET}</style>\n</head>\n<body>\n"
        f"{body}\n</body>\n</html>\n"
    )
```

Create `validated_memory/knowledge_view.py`:

```python
"""Builds `knowledge.html`: the curated layer, live conclusions first."""

from . import html

TITLE = "Curated knowledge"


def build(documents, basis):
    """Return the whole page as a string."""
    parts = [f"<h1>{html.escape_text(TITLE)}</h1>"]
    parts.append(
        f'<p class="basis">Basis: {len(documents)} unit(s) under '
        f"{html.escape_text(basis)}</p>"
    )
    return html.page(TITLE, "\n".join(parts))
```

Rewrite `validated_memory/render.py`'s `run`:

```python
import os
from pathlib import Path

from . import knowledge_view, validate
from .findings import EXIT_ERROR, EXIT_OK


def run(only_existing, stdout, stderr):
    documents, ok = validate.gated_source(None, stderr)
    if not ok:
        return EXIT_ERROR
    content = knowledge_view.build(documents, _basis_location(None))
    action = write_if_changed(Path(KNOWLEDGE_ARTIFACT), content)
    print(f"render: {action} {KNOWLEDGE_ARTIFACT}", file=stdout)
    return EXIT_OK


def _basis_location(path):
    target = validate.resolve_target(path)
    location = target.as_posix()
    if target.is_dir():
        location += "/"
    return location


def write_if_changed(path, content):
    """Write `content` to `path` only when it differs. Returns what happened.

    The write is atomic -- a temporary file in the same directory, then a
    rename -- so a failure can never leave a half-written page for a reader
    to open, and an unchanged artifact is not touched at all, which is what
    keeps the startup hook from dirtying `git status` on every session.
    """
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return "unchanged"
    # The temporary name carries this process's pid: a fixed one is shared
    # state between concurrent runs, and the startup hook makes concurrent
    # runs ordinary. It stays in the destination's directory, because
    # `os.replace` is only atomic within one filesystem.
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return "wrote"
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add validated_memory/html.py validated_memory/knowledge_view.py validated_memory/render.py tests/test_render.py
git commit -m "feat: render escribe knowledge.html y no reescribe lo que no cambia"
```

---

### Task 3: Live conclusions, with their detail and their escaping

**Files:**
- Modify: `validated_memory/knowledge_view.py`
- Modify: `tests/conftest.py` (add the page-parsing helper)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `derive.effective_states(documents) -> {id: (data, state)}`,
  `derive.unit_verdict(unit_id, anchors, view) -> UnitVerdict`,
  `verdicts.service_view(root) -> {(unit, system, kind): verdict}`,
  `memory.body(text) -> str`, `frontmatter.parse(text) -> dict`
- Produces: `knowledge_view.headline(body_text, unit_id) -> str`

- [ ] **Step 1: Write the failing tests**

Add to `tests/conftest.py`:

```python
from html.parser import HTMLParser


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag


@pytest.fixture
def page_elements():
    """Parse a page into `[(tag, {attr: value})]` with the stdlib parser.

    Substring assertions pass over malformed HTML, so structure is asserted
    structurally. This reads the artifact as data; it imports nothing from
    the package.
    """

    def _parse(text):
        collector = _Collector()
        collector.feed(text)
        return collector.elements

    return _parse
```

Add to `tests/test_render.py`:

```python
URL_BEARING = {"src", "srcset", "data", "poster", "action", "formaction",
               "cite", "background", "xlink:href", "ping"}


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
        # The frontmatter subset rejects backslash escapes inside a quoted
        # scalar on purpose, so the quotes go the other way round.
        "id: kb-0001\nevidence: measured\nprovenance:\n  - 'a \"quoted\" source'\n",
        "# Title\n\n<script>alert(1)</script>\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert not [tag for tag, _ in page_elements(page) if tag == "script"]


def test_only_an_anchor_href_ever_carries_an_external_url(
    run_cli, adopter_dir, write_unit, page_elements
):
    run_cli("init", cwd=adopter_dir)
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nprovenance:\n  - https://example.invalid/doc\n",
        "# Title\n\nProse.\n",
    )

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")
    elements = page_elements(page)

    for tag, attrs in elements:
        for name, value in attrs.items():
            carries_url = name in URL_BEARING or "://" in (value or "")
            if carries_url:
                assert (tag, name) == ("a", "href"), f"{tag}[{name}]={value}"
        if tag == "a":
            assert "ping" not in attrs
            if attrs.get("target") == "_blank":
                assert attrs.get("rel") == "noopener noreferrer"
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_render.py -q`
Expected: FAIL -- the page has no `section.unit` and no provenance link.

- [ ] **Step 3: Write the implementation**

Replace `knowledge_view.build` with the real one:

```python
import re

from . import derive, html, memory, verdicts
from .frontmatter import parse as parse_frontmatter

HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def headline(body_text, unit_id):
    """The first heading of the body, or the id when there is none.

    THIS IS THE BOUNDARY AND IT IS CLOSED. Extracting one line by a
    documented rule is not rendering the body. "And the first paragraph too"
    would be, and the design rejects it: bodies are shown verbatim.
    """
    match = HEADING_PATTERN.search(body_text)
    return match.group(1) if match else unit_id


def build(documents, basis):
    states = derive.effective_states(documents)
    view = verdicts.service_view()
    bodies = {}
    for _location, text in documents:
        bodies[parse_frontmatter(text)["id"]] = memory.body(text)

    parts = [f"<h1>{html.escape_text(TITLE)}</h1>"]
    parts.append(
        f'<p class="basis">Basis: {len(documents)} unit(s) under '
        f"{html.escape_text(basis)}</p>"
    )
    for unit_id in sorted(states):
        data, state = states[unit_id]
        if state != "active":
            continue
        parts.append(_unit_section(unit_id, data, state, bodies, view))
    return html.page(TITLE, "\n".join(parts))


def _unit_section(unit_id, data, state, bodies, view):
    graded = derive.unit_verdict(unit_id, data.get("anchors") or [], view)
    body_text = bodies.get(unit_id, "")
    return (
        f'<section class="unit" id="unit-{html.escape_attribute(unit_id)}"'
        f' data-unit="{html.escape_attribute(unit_id)}"'
        f' data-state="{html.escape_attribute(state)}">\n'
        "<details>\n<summary>"
        f'<span class="headline">{html.escape_text(headline(body_text, unit_id))}</span> '
        f'<code class="id">{html.escape_text(unit_id)}</code> '
        f'<span class="evidence">{html.escape_text(data["evidence"])}</span> '
        f'<span class="verdict">{html.escape_text(graded.verdict)}</span>'
        "</summary>\n"
        f'<pre class="body">{html.escape_text(body_text)}</pre>\n'
        f"{_anchors(data.get('anchors') or [])}"
        f"{_provenance(data.get('provenance') or [])}"
        "</details>\n</section>"
    )


def _anchors(anchors):
    # `payload` is a mapping the contract never looks inside -- the probe
    # interprets it, not the contract -- so it is arbitrary structure even
    # here, in the validated layer. `html.escape_text` stringifies before
    # escaping, which is what keeps that from raising.
    if not anchors:
        return '<p class="meta">No anchors: this unit cannot expire.</p>\n'
    items = []
    for anchor in anchors:
        payload = anchor.get("payload")
        items.append(
            "<li>"
            f'<span class="system">{html.escape_text(anchor.get("system"))}</span> '
            f'<span class="kind">{html.escape_text(anchor.get("kind"))}</span> '
            f'<span class="captured">{html.escape_text(anchor.get("captured_at"))}</span>'
            f'<pre class="payload">{html.escape_text(payload)}</pre>'
            "</li>"
        )
    return '<ul class="anchors">\n' + "\n".join(items) + "\n</ul>\n"


def _provenance(entries):
    if not entries:
        return ""
    items = []
    for entry in entries:
        text = html.escape_text(entry)
        if isinstance(entry, str) and entry.startswith(("http://", "https://")):
            items.append(
                f'<li><a href="{html.escape_attribute(entry)}"'
                ' target="_blank" rel="noopener noreferrer">'
                f"{text}</a></li>"
            )
        else:
            items.append(f"<li>{text}</li>")
    return '<ul class="provenance">\n' + "\n".join(items) + "\n</ul>\n"
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add validated_memory/knowledge_view.py tests/conftest.py tests/test_render.py
git commit -m "feat: la vista curada enseña titular, evidencia, veredicto, anclas y procedencia"
```

---

### Task 4: The supersession chain, nested and never duplicated

**Files:**
- Modify: `validated_memory/knowledge_view.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `derive.effective_states` (already used), each unit's
  `supersedes` list from its frontmatter
- Produces: nothing new outside the module

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_render.py -q`
Expected: FAIL -- superseded units are absent from the page entirely.

- [ ] **Step 3: Write the implementation**

In `build`, thread a `rendered` set through, and after the unit's provenance
emit its chain:

```python
def build(documents, basis):
    states = derive.effective_states(documents)
    view = verdicts.service_view()
    bodies = {}
    for _location, text in documents:
        bodies[parse_frontmatter(text)["id"]] = memory.body(text)

    parts = [f"<h1>{html.escape_text(TITLE)}</h1>"]
    parts.append(
        f'<p class="basis">Basis: {len(documents)} unit(s) under '
        f"{html.escape_text(basis)}</p>"
    )
    rendered = set()
    for unit_id in sorted(states):
        data, state = states[unit_id]
        if state != "active":
            continue
        parts.append(
            _unit_section(unit_id, data, state, bodies, view, states, rendered)
        )
    return html.page(TITLE, "\n".join(parts))
```

`_unit_section` gains the chain, and the repeat rule:

```python
def _unit_section(unit_id, data, state, bodies, view, states, rendered, top=True):
    if unit_id in rendered:
        return (
            f'<p class="repeat">Already shown above: '
            f'<a href="#unit-{html.escape_attribute(unit_id)}">'
            f"{html.escape_text(unit_id)}</a></p>"
        )
    rendered.add(unit_id)
    ...  # summary, body, anchors, provenance exactly as in Task 3
    # ILLUSTRATIVE ONLY -- and note it must NOT be written as the recursive
    # call it reads like. The closing requirement below is binding: push each
    # `sorted(data.get("supersedes") or [])` target onto an explicit stack,
    # mark it rendered as it is pushed, and build each section as one balanced
    # string before appending it to its parent's pieces.
    chain = _walk_chain(unit_id, states, bodies, view, rendered)
    if chain:
        chain = f'<div class="chain">\n{chain}\n</div>\n'
    css_class = "unit" if top else "unit superseded"
    return (
        f'<section class="{css_class}" id="unit-{html.escape_attribute(unit_id)}"'
        ...
        f"{chain}"
        "</details>\n</section>"
    )
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add validated_memory/knowledge_view.py tests/test_render.py
git commit -m "feat: la cadena de supersesion se anida bajo la conclusion vigente"
```

**Walk the chain iteratively, with an explicit stack — not with recursion.**
A chain's length is written by people and has nothing bounding it, and
`validate`'s own cycle detection is iterative for exactly this reason. The
`rendered` set still does the work of emitting an internal reference instead
of re-entering a unit already shown.

There is no separate handling for a supersession cycle: `validate` rejects one
as an ERROR, and `render` validates before it renders. With cycles rejected
the graph is a DAG, so every unit reaches an active root backwards and the
"unreachable" set is empty by construction. See the spec's "Why there is no
'unreachable units' section".

---

### Task 5: `verdicts.history()` and the anchor's probe history

**Files:**
- Modify: `validated_memory/verdicts.py`
- Modify: `validated_memory/knowledge_view.py`
- Modify: `validated_memory/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Produces: `verdicts.history(root=Path()) -> [dict]` -- every record, in file
  order, uncollapsed, sharing `service_view`'s parsing and raising the same
  `VerdictLogError(lineno, message)`.
- Consumes: `verdicts.LOG_FILENAME`, `findings.Finding`, `contract.ERROR`

- [ ] **Step 1: Write the failing tests**

```python
import json

HISTORY_WINDOW = 20


# Every record must carry the SAME payload as the anchor whose history it
# belongs to: the anchor's identity is `(system, kind, payload)`, and a
# record with no payload field belongs to no anchor at all. A fixture that
# logs payload-less records and then asserts they fill an anchor's history
# is self-contradictory, and would hide the very defect that rule prevents.
def _log(adopter_dir, records):
    (adopter_dir / "verdicts.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def test_the_history_window_shows_twenty_and_states_the_true_total(
    run_cli, adopter_dir, write_unit
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
        {"unit": "kb-0001", "system": "repo", "kind": "git_ref",
         "verdict": "current", "recorded_at": f"2026-01-{day:02d}T00:00:00Z"}
        for day in range(1, 26)
    ])

    run_cli("render", cwd=adopter_dir)
    page = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    assert page.count('class="record"') == HISTORY_WINDOW
    assert "25 record(s)" in page
    assert "of which 25 belong to an anchor shown below" in page
    assert "2026-01-25T00:00:00Z" in page
    assert "2026-01-01T00:00:00Z" not in page


def test_an_unreadable_verdict_log_stops_render_with_its_line(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n", "# Title\n")
    (adopter_dir / "verdicts.jsonl").write_text("{not json}\n", encoding="utf-8")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "verdicts.jsonl:1" in result.stderr
    assert not (adopter_dir / "knowledge.html").exists()


def test_a_log_that_cannot_be_decoded_stops_render_without_a_line(
    run_cli, adopter_dir, write_unit
):
    # `VerdictLogError.lineno` is None when the fault is the file's rather
    # than a line's, and `Finding.render()` omits the `:N` in that case.
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n", "# Title\n")
    (adopter_dir / "verdicts.jsonl").write_bytes(b"\xff\xfe not utf-8\n")

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "verdicts.jsonl:" not in result.stderr
    assert "verdicts.jsonl" in result.stderr
    assert not (adopter_dir / "knowledge.html").exists()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_render.py -q`
Expected: FAIL -- no history is rendered, and an unreadable log crashes.

- [ ] **Step 3: Write the implementation**

`verdicts.records(root)` is already public on `main`: it yields
`(lineno, record)`, opens the file inside its `try`, and raises
`VerdictLogError(None, ...)` for a file it cannot read or decode and
`VerdictLogError(lineno, ...)` for a line that is not a JSON object.
`service_view` is built on it. So `history` is built on it too, and **writing
a second loop would be worse than duplicating one: there would be two readers
where there is one good one.**

```python
def history(root=Path()):
    """Every record, in file order, uncollapsed.

    Windowing is the renderer's business, not the reader's: a reader that
    truncated would decide for every consumer at once.
    """
    return [record for _lineno, record in records(root)]
```

In `knowledge_view`, take `records` and `total` as arguments to `build`,
group by key, and render the window:

```python
HISTORY_WINDOW = 20


def _history(unit_id, anchor, records):
    # An anchor is identified by what it points at, payload included: two
    # anchors of one unit can share a system and a kind and measure different
    # things. A record written before the payload was recorded carries none
    # and is read by NO anchor: uniqueness would settle which anchor it
    # belongs to, never what it measured, and an anchor gets recaptured. The
    # anchor reads `unknown` until the next probe repairs it.
    key = _anchor_key(unit_id, anchor)
    matching = [
        record for record in records if _record_key(record) == key
    ]
    shown = list(reversed(matching))[:HISTORY_WINDOW]
    items = "\n".join(
        f'<li class="record">{html.escape_text(record.get("recorded_at", ""))} '
        f'{html.escape_text(record["verdict"])}</li>'
        for record in shown
    )
    return (
        f'<p class="meta">{len(matching)} record(s) for this anchor; '
        f"showing {len(shown)}.</p>\n"
        f'<ul class="history">\n{items}\n</ul>\n'
    )
```

And the page header states **two** totals, because the log outlives the
corpus: nothing prunes records whose unit or anchor is gone, so a single total
cannot be reconciled with the histories on the page.

```python
    parts.append(
        f'<p class="window">Verdict log: {len(records)} record(s) in '
        f"{html.escape_text(verdicts.LOG_FILENAME)}, of which {belonging} "
        f"belong to an anchor shown below; at most {HISTORY_WINDOW} shown "
        "per anchor.</p>"
    )
```

When a unit carries two anchors with the same `(system, kind)` -- which the
contract permits and the log cannot tell apart, since `probe` records no
position or payload -- the history is emitted once and marked as shared by
those anchors, rather than repeated under each as if it were its own.

In `render.run`, read the history once and report a bad log exactly as
`derive` does:

```python
    try:
        records = verdicts.history()
    except verdicts.VerdictLogError as error:
        finding = Finding(
            ERROR, verdicts.LOG_FILENAME, "log", error.message, line=error.lineno
        )
        print(finding.render(), file=stderr)
        return EXIT_ERROR
    content = knowledge_view.build(documents, _basis_location(None), records)
```

- [ ] **Step 4: Run the whole suite**

Run: `python3 -m pytest -q`
Expected: PASS, including the existing `derive` tests -- `service_view`'s
behaviour must not change.

- [ ] **Step 5: Commit**

```bash
git add validated_memory/verdicts.py validated_memory/knowledge_view.py validated_memory/render.py tests/test_render.py
git commit -m "feat: historial de sondeos por ancla con ventana declarada"
```

---

### Task 6: The two diagrams

**Files:**
- Create: `validated_memory/svg.py`
- Modify: `validated_memory/knowledge_view.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Produces: `svg.freshness_strip(records) -> str`,
  `svg.confluence(superseded_ids, successor_id) -> str`

- [ ] **Step 1: Write the failing tests**

```python
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
        {"unit": "kb-0001", "system": "repo", "kind": "git_ref",
         "verdict": "current", "recorded_at": "2026-01-01T00:00:00Z"},
        {"unit": "kb-0001", "system": "repo", "kind": "git_ref",
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_render.py -q`
Expected: FAIL -- no `<svg>` anywhere.

- [ ] **Step 3: Write the implementation**

Create `validated_memory/svg.py`:

```python
"""The only two diagrams: an anchor's freshness over time, and a confluence.

Both are inline SVG, generated deterministically from the data alone. The
freshness strip's right edge is the LAST RECORD, never "now": an edge at
"now" would redraw the artifact on every regeneration and dirty `git status`
on every session. Colour is never the only channel -- every band carries its
verdict as text -- so the diagrams survive colour blindness and a black and
white printer. Nothing here loads a resource: no `<use>`, no `<image>`, no
`href` of any kind.
"""

from . import html

BAND_HEIGHT = 24
WIDTH = 640
COLOURS = {"current": "#2e7d32", "drifted": "#c62828", "unknown": "#757575"}


def freshness_strip(records):
    """A horizontal band per record, oldest to newest, labelled with its verdict."""
    if not records:
        return ""
    count = len(records)
    band = WIDTH / count
    bands = []
    for index, record in enumerate(records):
        verdict = record["verdict"]
        bands.append(
            f'<rect x="{index * band:.2f}" y="0" width="{band:.2f}" '
            f'height="{BAND_HEIGHT}" fill="{COLOURS[verdict]}">'
            f"<title>{html.escape_text(record.get('recorded_at', ''))} {html.escape_text(verdict)}</title>"
            "</rect>"
        )
    last = records[-1]
    return (
        f'<svg class="freshness" role="img" viewBox="0 0 {WIDTH} {BAND_HEIGHT}" '
        f'width="100%" height="{BAND_HEIGHT}" '
        f'aria-label="Probe history, oldest to newest, ending '
        f'{html.escape_attribute(last.get("recorded_at", ""))} {html.escape_attribute(last["verdict"])}">'
        + "".join(bands)
        + "</svg>"
    )


def confluence(superseded_ids, successor_id):
    """Three or more units merging into one. Below three, nothing is drawn."""
    if len(superseded_ids) < 3:
        return ""
    rows = len(superseded_ids)
    height = rows * 28 + 12
    lines = []
    for index, unit_id in enumerate(sorted(superseded_ids)):
        y = index * 28 + 14
        lines.append(
            f'<text x="4" y="{y + 4}" font-size="12">{html.escape_text(unit_id)}</text>'
            f'<line x1="120" y1="{y}" x2="300" y2="{height / 2}" '
            'stroke="currentColor" stroke-width="1"/>'
        )
    lines.append(
        f'<text x="308" y="{height / 2 + 4}" font-size="12">'
        f"{html.escape_text(successor_id)}</text>"
    )
    return (
        f'<svg class="confluence" role="img" viewBox="0 0 460 {height}" '
        f'width="100%" height="{height}" aria-label="{len(superseded_ids)} units '
        f'superseded by {html.escape_attribute(successor_id)}">'
        + "".join(lines)
        + "</svg>"
    )
```

Call them from `knowledge_view`: the strip inside `_history` (after the
list), the confluence just before the `chain` div when the unit supersedes
three or more.

**The strip draws the same windowed slice the list shows**, not the full
group. The two must agree: the page states "showing N of M", and a strip
drawn over all M while the list shows N would contradict that disclosure in
the one artifact whose whole point is not misleading its reader.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_render.py -q`
Expected: PASS, including the URL whitelist test from Task 3 -- it now also
guards the SVG.

- [ ] **Step 5: Commit**

```bash
git add validated_memory/svg.py validated_memory/knowledge_view.py tests/test_render.py
git commit -m "feat: franja de frescura y confluencia muchos-a-uno en SVG"
```

---

### Task 7: `memory.html`

**Files:**
- Create: `validated_memory/memory_view.py`
- Modify: `validated_memory/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `memory.documents(target)`, `memory.filename(location)`,
  `memory.body(text)`, `memory.wikilinks(text)`,
  `memory.supersession(description)`, `memory.resolution(documents, declared)`,
  `memory.is_declared(value)`, `memory.DEFAULT_DIR`, `memory.INDEX_FILENAME`
- Produces: `memory_view.build(documents, resolution) -> str`

- [ ] **Step 1: Write the failing tests**

```python
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
    assert '<a href="#entry-tea">' in page
    assert "render: wrote memory.html" in result.stdout


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


def test_a_missing_memory_index_stops_the_run(run_cli, adopter_dir, write_unit):
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "memory" / "MEMORY.md").unlink()

    result = run_cli("render", cwd=adopter_dir)

    assert result.returncode == 1
    assert "MEMORY.md" in result.stderr
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_render.py -q`
Expected: FAIL -- `memory.html` is never written.

- [ ] **Step 3: Write the implementation**

Create `validated_memory/memory_view.py`, building one `section.entry` per
document ordered by filename, each with its `description`, `metadata.type`,
verbatim body, supersession marker linked to its successor, and two reference
lists: outgoing (resolved links to `#entry-<name>`, unresolved marked
`class="unresolved"`) and incoming. Resolution comes from
`memory.resolution(documents, declared)` -- never re-derived, so the view
resolves exactly as `lint` does, ADR 0001 included.

In `render.run`, read the memory layer, stop when the directory or the index
is missing -- that is a read precondition, the same one `lint` stops on --
build the page, and report the second artifact on its own line.

**Nothing validated these values, so nothing may assume their type.** A
`description` can be a list, a `metadata.type` a mapping, a `name` a number:
the frontmatter parser reads what is written and only `lint` judges it. Every
value reaching the page is stringified by the escaping helper, and no
membership test, sort or `.strip()` touches a value without a type check
first. A `TypeError` here is a traceback where a page should be.

**The view does not enforce.** There is no `gated_source` for this layer:
`memory.py` reads and every rule lives in private functions of `lint`. So an
entry with no line in `MEMORY.md` is still rendered, an unresolved reference
is marked rather than hidden, and a document whose frontmatter will not parse
is rendered with that stated in place of its fields. Hiding a record because
`lint` would complain about it would make the view lie about what the
repository holds, and `lint` is one command away.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add validated_memory/memory_view.py validated_memory/render.py tests/test_render.py
git commit -m "feat: memory.html con las referencias entrantes y salientes de cada entrada"
```

---

### Task 8: `--only-existing`, the unattended mode

**Files:**
- Modify: `validated_memory/render.py`
- Test: `tests/test_render.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_only_existing_regenerates_what_is_there_and_creates_nothing(
    run_cli, adopter_dir, write_unit
):
    _scaffold(run_cli, adopter_dir, write_unit)
    (adopter_dir / "knowledge.html").write_text("stale\n", encoding="utf-8")

    result = run_cli("render", "--only-existing", cwd=adopter_dir)

    assert result.returncode == 0
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") != "stale\n"
    assert not (adopter_dir / "memory.html").exists()


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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_render.py -q`
Expected: FAIL -- the flag is accepted but ignored.

- [ ] **Step 3: Write the implementation**

`run` decides its target set first: with `only_existing`, only artifacts whose
path already exists. If the set is empty, it is a clean no-op. Validation
failure downgrades to a WARNING and `EXIT_OK` in that mode, because an ERROR
would be reported on every session start until someone fixed the corpus.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_render.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add validated_memory/render.py tests/test_render.py
git commit -m "feat: --only-existing regenera solo lo activado y es fail-open"
```

---

### Task 9: `init --view`

**Files:**
- Modify: `validated_memory/init.py`
- Modify: `validated_memory/cli.py`
- Test: `tests/test_init.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_init_view_creates_both_artifacts_once_and_keeps_them(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n", "# Title\n")

    first = run_cli("init", "--view", cwd=adopter_dir)
    stamp = (adopter_dir / "knowledge.html").read_bytes()
    (adopter_dir / "knowledge.html").write_text("edited by hand\n", encoding="utf-8")
    second = run_cli("init", "--view", cwd=adopter_dir)

    assert "created knowledge.html" in first.stdout
    assert "created memory.html" in first.stdout
    assert stamp
    assert "kept knowledge.html" in second.stdout
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") == "edited by hand\n"


def test_init_view_on_an_invalid_corpus_warns_without_gating(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: invented\n")

    result = run_cli("init", "--view", cwd=adopter_dir)

    assert result.returncode == 0
    assert "WARNING" in result.stderr
    assert not (adopter_dir / "knowledge.html").exists()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_init.py -q`
Expected: FAIL -- `--view` is not a recognised option (exit 2).

- [ ] **Step 3: Write the implementation**

Add the flag in `cli.py` and a `view` parameter to `init.run`. Creating an
artifact calls the renderer once; an artifact that already exists is reported
`kept` and **never regenerated** -- `init`'s contract is that an existing
item, even one edited by hand, is never touched, and a generator inside
`init` would break that contract in the command that defines it. A corpus the
renderer refuses is a WARNING and no artifact, never a gate.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_init.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add validated_memory/init.py validated_memory/cli.py tests/test_init.py
git commit -m "feat: init --view crea las vistas una vez y despues las respeta"
```

---

### Task 10: The startup hook

**Files:**
- Create: `hooks/refresh-views.sh`
- Modify: `hooks/hooks.json`
- Test: `tests/test_hooks_manifest.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_session_start_also_refreshes_the_views():
    manifest = json.loads(
        (REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    commands = [
        hook["command"]
        for entry in manifest["hooks"]["SessionStart"]
        for hook in entry["hooks"]
        if hook.get("type") == "command"
    ]
    assert any("refresh-views.sh" in command for command in commands)


def test_the_views_hook_exists_and_is_a_shell_script():
    script_path = REPO_ROOT / "hooks" / "refresh-views.sh"
    assert script_path.is_file()
    assert script_path.read_text(encoding="utf-8").startswith("#!/bin/bash")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest tests/test_hooks_manifest.py -q`
Expected: FAIL -- the script does not exist.

- [ ] **Step 3: Write the implementation**

`hooks/refresh-views.sh` mirrors `restore-memory-symlink.sh`'s discipline and
nothing else: `set -u`, exit 0 unconditionally, no `$CLAUDE_PROJECT_DIR` is a
no-op, a project without `validated-memory.md` and `memory/` is a no-op -- the same adoption test the existing hook applies, read from its source rather than restated here, no
`python3` is a no-op. It runs
`python3 -m validated_memory render --only-existing` from the project
directory with stdout silenced, with `PYTHONPATH` set to the plugin root
resolved from the script's own path.

Add it as a second entry under the same `SessionStart` in `hooks/hooks.json`.
It is a separate script on purpose: the existing one's contract turns on
"never loses data", and a generator writes.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/refresh-views.sh hooks/hooks.json tests/test_hooks_manifest.py
git commit -m "feat: hook de arranque que refresca solo las vistas ya activadas"
```

---

### Task 11: Documentation and the version

**Files:**
- Modify: `README.md`, `docs/adoption.md`,
  `skills/adopt-validated-memory/SKILL.md`
- Modify: `pyproject.toml`, `.claude-plugin/plugin.json`,
  `validated_memory/__init__.py`

- [ ] **Step 1: Write the failing test**

The version test already exists and is the gate here. Bump one of the three
places only, and watch it fail:

Run: `python3 -m pytest tests/test_plugin_manifest.py -q`
Expected: FAIL -- the three declared versions disagree.

- [ ] **Step 2: Set 1.1.0 in all three places**

`pyproject.toml`, `.claude-plugin/plugin.json` and
`validated_memory/__init__.py` must all read `1.1.0`.

- [ ] **Step 3: Write the documentation**

- `README.md`: a `render` section under the CLI covering both artifacts, the
  window, the inertness of the output and `--only-existing`; and the
  startup-hook section extended with the second hook. **Do not document the
  verdict log's format change** -- the anchor identity, the payload in each
  record, and how records older than it are treated are already written by
  the session that made that change.
- `docs/adoption.md`: activating the views with `init --view`, and that
  deleting an artifact deactivates it.
- `skills/adopt-validated-memory/SKILL.md`: the activation step.

- [ ] **Step 4: Run the whole suite**

Run: `python3 -m pytest -q`
Expected: PASS. `test_skills_structure.py` also checks that every documented
command names a real subcommand and that no doc mentions an internal project.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/adoption.md skills/adopt-validated-memory/SKILL.md pyproject.toml .claude-plugin/plugin.json validated_memory/__init__.py
git commit -m "docs: documenta render y sube la version a 1.1.0"
```

---

## Self-review notes

- **Spec coverage.** Every section of the spec maps to a task: CLI surface
  (1, 2, 8), artifacts and determinism (2), headline and detail (3),
  chain (4), history window and `verdicts.history` (5), diagrams (6),
  `memory.html` (7), activation (9, 10), docs and version (11). Escaping and
  the URL whitelist are tested from Task 3 onward, so every later task
  inherits the guard.
- **Not covered by a task, deliberately:** `render --check`, which the spec
  puts out of 1.1.0.
- **Naming consistency:** `write_if_changed`, `build`, `headline`,
  `freshness_strip`, `confluence`, `history` are used with the same names in
  every task that references them.
