"""The three generated diagrams: freshness, confluence and rationale.

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

Two guarantees, and only two: these drawings are BYTE-DETERMINISTIC, and they
are NOT promised to look the same on every platform. An SVG `<text>` does not
wrap, and no layout computed without font metrics can promise "it fits",
"nothing is truncated" and "it is legible" at once for arbitrary adopter text
-- CJK, emoji sequences, combining marks and right-to-left text break any
character-count estimate. Hence `LABEL_LIMIT` below, which is deterministic
rather than clever.
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

# A label at or under this many characters is drawn inside its node; above
# it, the node draws its number and the full text is read from the list
# beside the diagram. A character count is not a width, which is exactly why
# this is a fallback and not an estimate.
LABEL_LIMIT = 48
# Above this many options every node draws its number, whatever its label
# measures: past this point a column of numbers reads better than a column of
# half-fitting text, and a uniform rule never leaves a reader wondering why
# one node is numbered and its neighbour is not.
NUMBERED_ABOVE = 8
# The rationale diagram is a top-down tree of full-width rows: the question
# across the top, the options indented beneath it. A side-by-side layout is
# what would force two different thresholds -- a narrow left-hand question
# column overflows into the option column long before `LABEL_LIMIT`
# characters -- and one threshold every node can honour is worth more than a
# layout that looks more like a graph.
ROW_HEIGHT = 34
BOX_HEIGHT = 28
OPTION_INDENT = 24


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


def rationale(unit_id, record):
    """One diagram per unit that carries a rationale: the question, then the options.

    A top-down tree of fixed depth: the question in a full-width row across
    the top, one full-width row per option indented beneath it, and one line
    from the question down to each. No edges between options and no edge
    leaving the unit -- a rationale holds no reference to anything, so there
    is no global graph here and no hairball to avoid. Size and edge count are
    linear in the number of options.

    Every node is a full-width row precisely so that ONE threshold governs
    them all. Laid out side by side, the question would sit in a narrow
    column and overflow into the options well before `LABEL_LIMIT`
    characters, and a drawing with two different limits is one a reader
    cannot predict.

    The chosen option is told apart three ways at once, none of them colour:
    a rounded, heavier border, the word `chosen` drawn inside the node, and
    its position in the numbered list beside the diagram.

    Nothing is omitted and nothing is silently truncated. Text at or under
    `LABEL_LIMIT` characters is drawn inline; above it -- or, for an option,
    past `NUMBERED_ABOVE` options, where the whole diagram switches to
    numbers at once -- the node draws `#n`, or `?` for the question, and the
    reader finds the full text beside the drawing. The `<desc>` says so,
    inside the drawing.

    The label, the `aria-label` and the `<desc>` name the unit and count the
    options, and quote no adopter text at all: a `question` is unconstrained,
    so one carrying `://` would put a URL in an SVG attribute (which the
    page's self-containment rule allows nowhere but `a[href]`), and one past
    the threshold would contradict the fallback the drawing has just applied.

    Every coordinate here is an integer, so no float formatting can differ
    between platforms: the same rationale renders the same bytes.

    `record` is a validated rationale mapping: `question` is a non-empty
    string and `options` a list of at least two mappings, each with `label`,
    `disposition` and `reason`, exactly one of them `chosen`. It is named
    `record` rather than `rationale` so that it does not shadow this
    function.
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
        "'#n', or a question showing '?', means the text ran past 48 "
        "characters and could not be drawn: the full text is beside this "
        "drawing -- the question just above it, an option at position n of "
        "the list.",
        "".join(parts),
    )
