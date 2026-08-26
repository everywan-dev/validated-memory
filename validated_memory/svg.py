"""The generated diagrams: an anchor's freshness over time, and a confluence.

All inline SVG, generated deterministically from the data alone, and all of
them obey one set of rules -- written once, here, because a rule kept in
three places is a rule two of them will drift from:

- **Deterministic.** Element ids derive from the data, never from `hash()`,
  which is salted per process. No clock, no generation timestamp, no font
  metrics. The freshness strip's right edge is the LAST RECORD, never "now":
  an edge at "now" would redraw the artifact on every regeneration and dirty
  `git status` on every session.
- **Inert.** No `href` of any kind, no `<use>`, no `<image>`.
- **Escaped.** Every text node and attribute goes through
  `html.escape_text` / `html.escape_attribute`. An SVG carrying unescaped
  adopter text is an XSS surface, not a drawing.
- **Not colour-alone.** State differs in shape and in text as well as in
  fill, so the diagrams survive colour blindness and a black and white
  printer. Every diagram carries a `<title>` and a `<desc>`.
- **Never load-bearing.** Everything a diagram shows is also on the page as
  structured HTML.

A diagram's `<title>`, `aria-label` and `<desc>` are built from values of
closed domains -- counts, verdicts, unit ids -- and never from adopter or
probe text. An attribute is the one place on the page where a stray `://`
would breach the self-containment rule, and `recorded_at`, a `label` and a
`question` are all values nothing constrains. They reach the page as escaped
text instead, which is where they belong.
"""

from . import html

BAND_HEIGHT = 24
WIDTH = 640
CONFLUENCE_WIDTH = 460
COLOURS = {"current": "#2e7d32", "drifted": "#c62828", "unknown": "#757575"}
# One character per verdict, drawn in the band. These three are ASCII, so no
# font has to have them.
MARKS = {"current": "+", "drifted": "!", "unknown": "?"}
# And one shape per verdict -- `(top edge, height, dash pattern)` -- so the
# strip reads the same in greyscale as in colour: a full solid band, a full
# dashed band, and a half-height band.
SHAPES = {
    "current": (0, BAND_HEIGHT, None),
    "drifted": (0, BAND_HEIGHT, "3 2"),
    "unknown": (BAND_HEIGHT // 2, BAND_HEIGHT // 2, None),
}


def _diagram(class_name, width, height, label, description, body):
    """The shell every diagram shares: sized, titled, described and inert.

    `label` is both the `aria-label` and the `<title>`, so a reader using a
    screen reader and a reader hovering the drawing get the same sentence.
    `<desc>` says what the drawing means and, more importantly, what it does
    not mean -- which for the freshness strip is "distance in time".

    Both are built by the caller out of closed-domain values only; see the
    module docstring for why nothing adopter-authored may reach them.

    `class_name`, `label` and `description` are escaped here, by the shell.
    `body` is inserted verbatim -- it is the caller's job to have already
    escaped everything inside it, since the shell has no way to tell markup
    from text once the two are concatenated into one string.
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
    """A horizontal band per record, oldest to newest, labelled with its verdict.

    `records` is the anchor's own group, oldest first, in log file order,
    never re-sorted by `recorded_at`: the log is append-only, so file order is
    chronological, and the verdict parser requires only `unit`, `system`,
    `kind`, `verdict` and the payload -- `recorded_at` is what `probe`
    happens to write, not something a reader can demand. Each band is still
    labelled with its `recorded_at` when there is one, in its own `<title>`.

    This is a sequence, NOT a time axis, and the `<desc>` says so: with
    `recorded_at` optional, no width here may imply distance in time between
    two records.

    Three channels tell a band apart and only one is colour: its shape
    (`SHAPES`), its mark (`MARKS`) and its `<title>`.
    """
    if not records:
        return ""
    count = len(records)
    band = WIDTH / count
    bands = []
    for index, record in enumerate(records):
        verdict = record["verdict"]
        top, band_height, dash = SHAPES[verdict]
        outline = (
            f' stroke="currentColor" stroke-dasharray="{dash}"' if dash else ""
        )
        # The mark sits vertically centred in its own band: `top +
        # band_height // 2` is the band's own midline, and `+ 4` compensates
        # for a 12px glyph's baseline sitting below its visual centre. A
        # full-height band centres at 16; the half-height band starts lower
        # (`top` is 12, not 0), so its midline -- and its mark -- moves down
        # with it, to 22, keeping the glyph inside the shorter box instead
        # of centred on a band twice its height.
        baseline = top + band_height // 2 + 4
        bands.append(
            f'<rect x="{index * band:.2f}" y="{top}" width="{band:.2f}" '
            f'height="{band_height}" fill="{COLOURS[verdict]}"{outline}>'
            f"<title>{html.escape_text(record.get('recorded_at', ''))} {html.escape_text(verdict)}</title>"
            "</rect>"
            f'<text x="{index * band + band / 2:.2f}" y="{baseline}" '
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
    """Three or more units merging into one. Below three, nothing is drawn.

    Below three, a chain is two boxes and an arrow saying what one line of
    text already says.
    """
    if len(superseded_ids) < 3:
        return ""
    ordered = sorted(set(superseded_ids))
    height = len(ordered) * 28 + 12
    lines = []
    for index, unit_id in enumerate(ordered):
        y = index * 28 + 14
        lines.append(
            f'<text x="4" y="{y + 4}" font-size="12" fill="currentColor">'
            f"{html.escape_text(unit_id)}</text>"
            f'<line x1="120" y1="{y}" x2="300" y2="{height / 2}" '
            'stroke="currentColor" stroke-width="1"/>'
        )
    lines.append(
        f'<text x="308" y="{height / 2 + 4}" font-size="12" fill="currentColor">'
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
