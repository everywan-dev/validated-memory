"""Deterministic, inert freshness, confluence and rationale SVG.

Use integer geometry, no clock/font metrics/hash ordering, and no links or
external assets. Escape text and attributes; callers provide complete HTML
text alongside diagrams. Diagram-level titles, labels and descriptions use
counts, verdicts and unit IDs; band titles also carry escaped optional
timestamps. Shape and text supplement colour, but screen-reader, print and
contrast behavior lack direct tests. Byte stability does not guarantee text
fit or identical appearance across fonts and platforms.
"""

from . import html

BAND_HEIGHT = 24
WIDTH = 640
CONFLUENCE_WIDTH = 460
COLOURS = {"current": "#2e7d32", "drifted": "#c62828", "unknown": "#757575"}
# Redundant verdict marks supplement shape and colour.
MARKS = {"current": "+", "drifted": "!", "unknown": "?"}
# Shape tuples: top edge, height, dash pattern.
SHAPES = {
    "current": (0, BAND_HEIGHT, None),
    "drifted": (0, BAND_HEIGHT, "3 2"),
    "unknown": (BAND_HEIGHT // 2, BAND_HEIGHT // 2, None),
}

# Character-count fallback, not a text-width estimate.
LABEL_LIMIT = 48
# Above this count number every option, regardless of label length.
NUMBERED_ABOVE = 8
# Full-width question and option rows share one character-count threshold.
ROW_HEIGHT = 34
BOX_HEIGHT = 28
OPTION_INDENT = 24


def _diagram(class_name, width, height, label, description, body):
    """Wrap trusted SVG markup with an escaped label and description.

    The label supplies both aria-label and the diagram title. Callers must
    escape body text before assembly; only closed-domain values belong in the
    diagram-level metadata. Per-diagram accessibility structure is unpinned.
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
    """Draw records in supplied append order, never sorted by timestamp.

    Band titles include optional recorded_at text. Width represents sequence,
    not elapsed time; the right edge is the last record, never now. Verdicts
    differ by shape, mark and colour.
    """
    if not records:
        return ""
    count = len(records)
    bands = []
    for index, record in enumerate(records):
        verdict = record["verdict"]
        top, band_height, dash = SHAPES[verdict]
        outline = (
            f' stroke="currentColor" stroke-dasharray="{dash}"' if dash else ""
        )
        # Offset the glyph baseline from its own band's midline.
        baseline = top + band_height // 2 + 4
        # Derive integer edges independently: adjacent bands tile WIDTH without drift.
        left = index * WIDTH // count
        right = (index + 1) * WIDTH // count
        bands.append(
            f'<rect x="{left}" y="{top}" width="{right - left}" '
            f'height="{band_height}" fill="{COLOURS[verdict]}"{outline}>'
            f"<title>{html.escape_text(record.get('recorded_at', ''))} {html.escape_text(verdict)}</title>"
            "</rect>"
            f'<text x="{(left + right) // 2}" y="{baseline}" '
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
    """Draw three or more distinct superseded IDs, sorted; otherwise return empty."""
    # Count and draw the same distinct IDs.
    ordered = sorted(set(superseded_ids))
    if len(ordered) < 3:
        return ""
    height = len(ordered) * 28 + 12
    midline = height // 2
    lines = []
    for index, unit_id in enumerate(ordered):
        y = index * 28 + 14
        lines.append(
            f'<text x="4" y="{y + 4}" font-size="12" fill="currentColor">'
            f"{html.escape_text(unit_id)}</text>"
            f'<line x1="120" y1="{y}" x2="300" y2="{midline}" '
            'stroke="currentColor" stroke-width="1"/>'
        )
    lines.append(
        f'<text x="308" y="{midline + 4}" font-size="12" fill="currentColor">'
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


def rationale(unit_id, record):
    """Draw a validated rationale: question, then one row per option.

    Require at least two options and exactly one chosen disposition. Rounded,
    heavier borders and disposition text distinguish the chosen option; list
    position identifies options, not their disposition. There are no edges
    between options or outside the unit.

    Long questions use ?, long labels use #n; above NUMBERED_ABOVE options all
    labels use #n. The adjacent HTML retains full text. Exact fallback thresholds
    lack direct tests; the emitted description omits the option-count trigger.
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
        f"'#n', or a question showing '?', means the text ran past {LABEL_LIMIT} "
        "characters and could not be drawn: the full text is beside this "
        "drawing -- the question just above it, an option at position n of "
        "the list.",
        "".join(parts),
    )
