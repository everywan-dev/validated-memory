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

from . import html, verdicts
from . import corpus as corpus_module


def build(corpus):
    """The whole overview, as one section."""
    return (
        '<section class="overview" id="overview">\n'
        + _counts(corpus)
        + _map(corpus)
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
        f"{html.escape_text(verdicts.canonical_payload(payload))}</pre>"
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
