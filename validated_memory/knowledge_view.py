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
    rendered = set()
    for unit_id in sorted(states):
        data, state = states[unit_id]
        if state != "active":
            continue
        parts.append(_unit_section(unit_id, data, state, states, bodies, view, rendered))
    return html.page(TITLE, "\n".join(parts))


def _unit_section(unit_id, data, state, states, bodies, view, rendered, top=True):
    """Render this unit's section, with its supersession chain nested inside.

    A chain's length is set by whoever writes `supersedes` and nothing in
    the contract bounds it, so this walks with an explicit stack rather than
    recursing -- a deep chain must not turn into a `RecursionError`. Two
    units may supersede the same one, so a unit already rendered elsewhere
    on the page is referenced by anchor (`<a href="#unit-...">`) instead of
    rendered again; that repeat rule is also what stops the walk from
    re-entering a shared ancestor. `render` validates before it renders, so
    `validate`'s rejection of a supersession cycle already guarantees this
    walk is over a DAG -- there is no separate cycle guard here.
    """
    if unit_id in rendered:
        return _repeat_reference(unit_id)

    rendered.add(unit_id)
    root = _new_frame(unit_id, data, state, top)
    stack = [root]
    while True:
        frame = stack[-1]
        if frame["index"] < len(frame["children"]):
            target = frame["children"][frame["index"]]
            frame["index"] += 1
            if target not in states:
                continue
            if target in rendered:
                frame["pieces"].append(_repeat_reference(target))
                continue
            rendered.add(target)
            target_data, target_state = states[target]
            stack.append(_new_frame(target, target_data, target_state, False))
            continue

        stack.pop()
        section = _render_section(frame, bodies, view)
        if not stack:
            return section
        stack[-1]["pieces"].append(section)


def _new_frame(unit_id, data, state, top):
    return {
        "unit_id": unit_id,
        "data": data,
        "state": state,
        "top": top,
        "children": sorted(data.get("supersedes") or []),
        "index": 0,
        "pieces": [],
    }


def _render_section(frame, bodies, view):
    unit_id = frame["unit_id"]
    data = frame["data"]
    state = frame["state"]
    graded = derive.unit_verdict(unit_id, data.get("anchors") or [], view)
    body_text = bodies.get(unit_id, "")
    chain = "".join(frame["pieces"])
    if chain:
        chain = f'<div class="chain">\n{chain}\n</div>\n'
    css_class = "unit" if frame["top"] else "unit superseded"
    return (
        f'<section class="{css_class}" id="unit-{html.escape_attribute(unit_id)}"'
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
        f"{chain}"
        "</details>\n</section>"
    )


def _repeat_reference(unit_id):
    return (
        f'<p class="repeat">Already shown above: '
        f'<a href="#unit-{html.escape_attribute(unit_id)}">'
        f"{html.escape_text(unit_id)}</a></p>"
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
