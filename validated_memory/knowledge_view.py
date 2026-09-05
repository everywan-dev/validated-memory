"""Build the curated-knowledge page from one corpus, active conclusions first."""

from . import html, knowledge_overview, styles, svg, verdicts
from . import corpus as corpus_module

TITLE = "Curated knowledge"

# Window presentation only; retain the log and disclose unwindowed totals.
HISTORY_WINDOW = 20


def build(corpus):
    """Return the page; overview and cards share the supplied corpus snapshot."""
    # Count records belonging to anchors actually rendered, not orphan log keys.
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
    # The log outlives its units and anchors; distinguish total from belonging.
    parts.append(
        f'<p class="window">Verdict log: {corpus.record_total} record(s) in '
        f"{html.escape_text(verdicts.LOG_FILENAME)}, of which {belonging} "
        f"belong to an anchor shown below; at most {HISTORY_WINDOW} shown "
        "per anchor.</p>"
    )
    parts.append(knowledge_overview.build(corpus))
    parts.extend(sections)
    return html.page(TITLE, "\n".join(parts), styles.KNOWLEDGE)


def _unit_section(corpus, unit_id, rendered, shown_keys, top=True):
    """Render a validated supersession DAG with an explicit stack.

    Targets must exist and cycles must already be rejected. Shared ancestors
    become links after their first render. Recursion-limit-depth behavior lacks
    a direct test.
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
        # Duplicate supersedes declarations must not multiply rows or diagram counts.
        "children": sorted(set(unit.data.get("supersedes") or [])),
        "index": 0,
        "pieces": [],
    }


def _render_section(corpus, frame, shown_keys):
    unit = frame["unit"]
    chain = "".join(frame["pieces"])
    if chain:
        chain = f'<div class="chain">\n{chain}\n</div>\n'
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
    """Render optional rationale and every option in full beside its diagram.

    Rejected options are neither superseded units nor failed verdicts. Keep the
    complete text even when the diagram substitutes a question mark or number.
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


def _repeat_reference(unit_id):
    return (
        f'<p class="repeat">Already shown above: '
        f'<a href="#unit-{html.escape_attribute(unit_id)}">'
        f"{html.escape_text(unit_id)}</a></p>"
    )


def _anchors(corpus, unit_id, shown_keys):
    # Payload contents are arbitrary: serialize as the key's JSON, then HTML-escape.
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
            f"{html.escape_text(verdicts.canonical_payload(payload))}</pre>"
            f"{_history(corpus.history.get(key, []))}"
            "</li>"
        )
    return '<ul class="anchors">\n' + "\n".join(items) + "\n</ul>\n"


def _history(matching):
    """Show the last HISTORY_WINDOW appended records, latest append first.

    `matching` is one anchor's log-ordered group, not timestamp-sorted. Disclose
    the full group total; the strip uses the same window in append order. Exact
    SVG window ordering lacks a direct test.
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
