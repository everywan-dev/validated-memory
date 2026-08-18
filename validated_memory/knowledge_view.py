"""Builds `knowledge.html`: the curated layer, live conclusions first."""

import json
import re

from . import derive, html, memory, svg, verdicts
from .frontmatter import parse as parse_frontmatter

TITLE = "Curated knowledge"

HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)

# The most recent probes shown under each anchor. The log itself is never
# truncated -- only what a page shows of it -- and each anchor states its
# own true total beside the window, so a reader can tell a full history from
# a partial one without leaving the page.
HISTORY_WINDOW = 20


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
    """
    match = HEADING_PATTERN.search(body_text)
    return match.group(1) if match else unit_id


def build(documents, basis, records, view):
    """Return the whole page as a string.

    `records` is the verdict log's full history (`verdicts.history()`) and
    `view` is its graded view (`verdicts.service_view()`) -- both read once
    by the caller, `render.build_artifacts`, in that order, before this is
    ever called: `service_view` is what validates the log (it raises on a
    malformed record, such as an explicit `payload: null`), so by the time
    `records` reaches `_group_history` below, every record in it has
    already passed that check. Reading both here instead would let a future
    edit reorder the two calls and silently lose that guarantee.
    """
    states = derive.effective_states(documents)
    bodies = {}
    for _location, text in documents:
        bodies[parse_frontmatter(text)["id"]] = memory.body(text)

    grouped = _group_history(records)
    # Populated as anchors are rendered below, so the header's "belonging"
    # total reflects exactly what ended up on the page -- not a count
    # derived separately from `states`, which could drift from the walk.
    shown_keys = set()

    sections = []
    rendered = set()
    for unit_id in sorted(states):
        data, state = states[unit_id]
        if state != "active":
            continue
        sections.append(
            _unit_section(
                unit_id, data, state, states, bodies, view, rendered,
                grouped, shown_keys,
            )
        )

    belonging = sum(len(grouped[key]) for key in shown_keys if key in grouped)
    parts = [f"<h1>{html.escape_text(TITLE)}</h1>"]
    parts.append(
        f'<p class="basis">Basis: {len(documents)} unit(s) under '
        f"{html.escape_text(basis)}</p>"
    )
    # Two totals, not one: the log outlives the corpus (nothing prunes a
    # record whose unit or anchor is gone), so the log's own total can never
    # be reconciled by a reader against the histories on the page -- only
    # the "belonging" count can be.
    parts.append(
        f'<p class="window">Verdict log: {len(records)} record(s) in '
        f"{html.escape_text(verdicts.LOG_FILENAME)}, of which {belonging} "
        f"belong to an anchor shown below; at most {HISTORY_WINDOW} shown "
        "per anchor.</p>"
    )
    parts.extend(sections)
    return html.page(TITLE, "\n".join(parts))


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
    It is still counted in the page header's log total, since that total is
    the log's own, not an anchor's; it just never counts toward an anchor's.
    Grouping preserves `records`' file order, so each group is oldest-first;
    `_history` reverses only the slice it shows.
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


def _unit_section(
    unit_id, data, state, states, bodies, view, rendered, grouped, shown_keys,
    top=True,
):
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
    so every `target` below is guaranteed to be a key of `states`.
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
            if target in rendered:
                frame["pieces"].append(_repeat_reference(target))
                continue
            rendered.add(target)
            target_data, target_state = states[target]
            stack.append(_new_frame(target, target_data, target_state, False))
            continue

        stack.pop()
        section = _render_section(frame, bodies, view, grouped, shown_keys)
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


def _render_section(frame, bodies, view, grouped, shown_keys):
    unit_id = frame["unit_id"]
    data = frame["data"]
    state = frame["state"]
    graded = derive.unit_verdict(unit_id, data.get("anchors") or [], view)
    body_text = bodies.get(unit_id, "")
    chain = "".join(frame["pieces"])
    if chain:
        chain = f'<div class="chain">\n{chain}\n</div>\n'
    # A confluence is drawn only when three or more units are superseded at
    # once by this one -- below three, a chain is two boxes and an arrow
    # saying what one line of text already says, so nothing is drawn.
    confluence = svg.confluence(frame["children"], unit_id)
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
        f"{_anchors(unit_id, data.get('anchors') or [], grouped, shown_keys)}"
        f"{_provenance(data.get('provenance') or [])}"
        f"{confluence}"
        f"{chain}"
        "</details>\n</section>"
    )


def _repeat_reference(unit_id):
    return (
        f'<p class="repeat">Already shown above: '
        f'<a href="#unit-{html.escape_attribute(unit_id)}">'
        f"{html.escape_text(unit_id)}</a></p>"
    )


def _anchors(unit_id, anchors, grouped, shown_keys):
    # `payload` is a mapping the contract never looks inside -- the probe
    # interprets it, not the contract -- so it is arbitrary structure even
    # here, in the validated layer. `html.escape_text` stringifies before
    # escaping, which is what keeps that from raising. `json.dumps` is what
    # it stringifies with, not `str`/`repr`: a reader of this page has no
    # Python, and `json.dumps` is the same deterministic form `verdicts._canonical`
    # already uses to key a record and the form `probe` writes into the log
    # this page also displays -- `str` would instead show Python's
    # single-quoted repr, `{'ref': 'main'}`, which is neither.
    if not anchors:
        return '<p class="meta">No anchors: this unit cannot expire.</p>\n'
    items = []
    for anchor in anchors:
        payload = anchor.get("payload")
        # Same key an anchor's own verdict is read under (`derive.unit_verdict`)
        # -- what it points at, payload included. Two anchors that happen to
        # share every field share a key and therefore a history; that is a
        # true fact about the log, not a bug in this grouping.
        key = verdicts.anchor_key(
            unit_id, anchor.get("system"), anchor.get("kind"), payload
        )
        shown_keys.add(key)
        items.append(
            "<li>"
            f'<span class="system">{html.escape_text(anchor.get("system"))}</span> '
            f'<span class="kind">{html.escape_text(anchor.get("kind"))}</span> '
            f'<span class="captured">{html.escape_text(anchor.get("captured_at"))}</span>'
            f'<pre class="payload">'
            f'{html.escape_text(json.dumps(payload, sort_keys=True))}</pre>'
            f"{_history(grouped.get(key, []))}"
            "</li>"
        )
    return '<ul class="anchors">\n' + "\n".join(items) + "\n</ul>\n"


def _history(matching):
    """This anchor's probe history: at most `HISTORY_WINDOW` records, newest first.

    `matching` is already this anchor's own records (`_group_history` keys on
    the same `anchor_key` computed above), oldest first; reversed here so the
    most recent probe reads first, then windowed. The disclosure line states
    the anchor's true total before the window, so a reader never mistakes a
    partial history for a complete one.

    The freshness strip below the list is drawn from `shown` -- the same
    windowed records the text history displays, put back in oldest-first
    order -- not a second pass over the log. A record outside the window
    never appears on the page in any form, text or drawn.
    """
    shown = list(reversed(matching))[:HISTORY_WINDOW]
    items = "\n".join(
        f'<li class="record">{html.escape_text(record.get("recorded_at"))} '
        f'{html.escape_text(record.get("verdict"))}</li>'
        for record in shown
    )
    return (
        f'<p class="meta">{len(matching)} record(s) for this anchor; '
        f"showing {len(shown)}.</p>\n"
        f'<ul class="history">\n{items}\n</ul>\n'
        f"{svg.freshness_strip(list(reversed(shown)))}"
    )


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
