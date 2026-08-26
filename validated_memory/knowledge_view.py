"""Builds `knowledge.html`: the curated layer, live conclusions first."""

from . import html, knowledge_overview, styles, svg, verdicts
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
    parts.append(knowledge_overview.build(corpus))
    parts.extend(sections)
    return html.page(TITLE, "\n".join(parts), styles.KNOWLEDGE)


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


def _repeat_reference(unit_id):
    return (
        f'<p class="repeat">Already shown above: '
        f'<a href="#unit-{html.escape_attribute(unit_id)}">'
        f"{html.escape_text(unit_id)}</a></p>"
    )


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


def _history(matching):
    """This anchor's probe history: at most `HISTORY_WINDOW` records, newest first.

    `matching` is already this anchor's own records (`corpus._group_history`
    keys on the same `anchor_key` computed above), oldest first; reversed
    here so the most recent probe reads first, then windowed. The disclosure
    line states the anchor's true total before the window, so a reader never
    mistakes a partial history for a complete one.

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
