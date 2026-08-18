"""Builds `knowledge.html`: the curated layer, live conclusions first."""

import re

from . import derive, html, memory, verdicts
from .frontmatter import parse as parse_frontmatter

TITLE = "Curated knowledge"

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
    """Return the whole page as a string."""
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
