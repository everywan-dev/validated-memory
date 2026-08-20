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
    """A horizontal band per record, oldest to newest, labelled with its verdict.

    `records` is the anchor's own group from `_group_history` -- oldest
    first, in log file order, never re-sorted by `recorded_at`: the log is
    append-only, so file order is chronological, and the verdict parser
    requires only `unit`, `system`, `kind`, `verdict` and the payload --
    `recorded_at` is what `probe` happens to write, not something a reader
    can demand. Each band is still labelled with its `recorded_at` when
    there is one.
    """
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
