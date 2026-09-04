"""The `derive` subcommand: re-derive the knowledge index from units.

`derive` requires a valid source: it runs the same validation as `validate`
first (the base contract plus the adopter's declared extension) and refuses to
write or check anything when that validation reports an ERROR. The index is a
plain Markdown table -- readable without the plugin -- naming, per unit, its
effective state (computed from `supersedes`, never mutating the unit), its
evidence state, and a verdict column read from the service view of
`verdicts.jsonl` (see `verdicts` and the `probe` subcommand): an anchor never
probed is `unknown`, fail-explicit.
"""

from datetime import datetime, timezone
from collections import namedtuple
from pathlib import Path

from . import validate
from . import verdicts as verdicts_module
from .findings import ERROR, EXIT_ERROR, EXIT_OK, Finding
from .frontmatter import parse as parse_frontmatter

INDEX_FILENAME = "knowledge-index.md"

# One anchor's freshness, and a unit's graded from all of them. Named so a
# reader other than the index -- which needs the per-anchor detail, not a
# table cell -- can grade a unit without reimplementing the rule.
AnchorVerdict = namedtuple("AnchorVerdict", "system kind verdict")
UnitVerdict = namedtuple("UnitVerdict", "verdict unknown_systems per_anchor")


def run(path, check, stdout, stderr):
    """Derive the knowledge index, or check it against disk. Returns an exit code."""
    documents, ok = validate.gated_source(path, stderr)
    if not ok:
        return EXIT_ERROR

    basis = validate.basis_location(path)
    try:
        view = verdicts_module.read().view
    except verdicts_module.VerdictLogError as error:
        # The log is the reader's source of verdicts: one it cannot parse is
        # reported like any other unreadable document, never served around.
        finding = Finding(
            ERROR,
            verdicts_module.LOG_FILENAME,
            "log",
            error.message,
            line=error.lineno,
        )
        print(finding.render(), file=stderr)
        return EXIT_ERROR
    content = render_index(index_rows(effective_states(documents), view), basis)

    index_path = Path(INDEX_FILENAME)
    if check:
        return _check(index_path, content, stdout, stderr)

    index_path.write_text(content, encoding="utf-8")
    print(f"derive: {len(documents)} unit(s) indexed", file=stdout)
    return EXIT_OK


def effective_states(documents):
    """Compute each unit's frontmatter and effective state, keyed by id.

    Shared with `probe`, which only probes anchors of *active* units: one that
    appears in another unit's `supersedes` within the set is not current.
    Documents already passed validation, so every `id` is present, valid and
    unique, and every `supersedes` entry names a declared id. The unit itself
    is never mutated; the effective state is computed from the whole set.
    Returns `{unit_id: (data, state)}`.
    """
    units = {}
    for _location, text in documents:
        data = parse_frontmatter(text)
        units[data["id"]] = data

    superseded_by = {}
    for unit_id, data in units.items():
        for target_id in data.get("supersedes") or []:
            superseded_by.setdefault(target_id, []).append(unit_id)

    states = {}
    for unit_id, data in units.items():
        supersessors = sorted(superseded_by.get(unit_id, []))
        if supersessors:
            state = "superseded by " + ", ".join(supersessors)
        else:
            state = "active"
        states[unit_id] = (data, state)
    return states


def index_rows(states, view):
    """One row per unit, sorted by id: state, evidence and verdict.

    Takes `effective_states(documents)` and a verdict view already read, so a
    caller that also needs either of them for something else -- `status`
    grades freshness from the same `states` and `view` -- builds them once
    and shares them, rather than this function reading the log again.
    """
    result = []
    for unit_id in sorted(states):
        data, state = states[unit_id]
        verdict = _verdict_cell(unit_id, data.get("anchors") or [], view)
        result.append((unit_id, state, data["evidence"], verdict))
    return result


def unit_verdict(unit_id, anchors, view):
    """Grade one unit's freshness from its anchors and the service view.

    The worst verdict among the unit's anchors (`drifted` > `unknown` >
    `current`). No anchors -- nothing to probe -- grades `unknown` on its own.
    With anchors, one absent from the service view (never probed) is
    `unknown`, fail-explicit.

    `view` is keyed by `verdicts.anchor_key`.

    Returns a `UnitVerdict`: the grade, the systems behind an `unknown` anchor
    (sorted), and the per-anchor detail. Computation only -- how the grade is
    written into the index is `_verdict_cell`'s business, and any other reader
    grades a unit by calling this rather than by reimplementing the rule.

    `anchors` are a validated unit's: `system` and `kind` are strings, so they
    can be keyed on.
    """
    if not anchors:
        return UnitVerdict(verdicts_module.UNKNOWN, (), ())
    per_anchor = tuple(
        AnchorVerdict(
            anchor.get("system"),
            anchor.get("kind"),
            view.get(
                verdicts_module.anchor_key(
                    unit_id,
                    anchor.get("system"),
                    anchor.get("kind"),
                    anchor.get("payload"),
                ),
                verdicts_module.UNKNOWN,
            ),
        )
        for anchor in anchors
    )
    verdict = verdicts_module.worst(anchor.verdict for anchor in per_anchor)
    unknown_systems = tuple(
        sorted(
            {
                anchor.system
                for anchor in per_anchor
                if anchor.verdict == verdicts_module.UNKNOWN
            }
        )
    )
    return UnitVerdict(verdict, unknown_systems, per_anchor)


def _verdict_cell(unit_id, anchors, view):
    """The verdict column for one unit, written as the index states it.

    When the grade is `unknown`, the systems behind it are listed; when it is
    `drifted` and some anchors are also `unknown`, those are listed too,
    tagged as such.
    """
    graded = unit_verdict(unit_id, anchors, view)
    if graded.verdict == verdicts_module.UNKNOWN and graded.unknown_systems:
        return f"{verdicts_module.UNKNOWN} (" + ", ".join(graded.unknown_systems) + ")"
    if graded.verdict == verdicts_module.DRIFTED and graded.unknown_systems:
        return (
            f"{verdicts_module.DRIFTED} (unknown: "
            + ", ".join(graded.unknown_systems)
            + ")"
        )
    return graded.verdict


def render_index(table, basis):
    derived_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    derived_at = derived_at.replace("+00:00", "Z")
    lines = [
        "# Knowledge index",
        "",
        f"Derived: {derived_at}",
        f"Basis: {len(table)} unit(s) under {basis}",
        "",
        "| id | state | evidence | verdict |",
        "|----|-------|----------|---------|",
    ]
    for unit_id, state, evidence, verdict in table:
        lines.append(f"| {unit_id} | {state} | {evidence} | {verdict} |")
    return "\n".join(lines) + "\n"


def index_findings(content, index_path=None):
    """Compare `content` (a recalculated index) against disk. Never writes.

    Returns the findings `--check` gates on: empty when they match -- the
    on-disk `Derived:` line is required but its timestamp is ignored, exactly
    as `--check` defines -- one ERROR naming a missing file, or one ERROR
    naming the first differing line, numbered as on disk. The seam `status`
    shares with `derive --check` so a missing or stale index reads as the
    same finding from both.
    """
    index_path = index_path or Path(INDEX_FILENAME)
    location = index_path.as_posix()
    if not index_path.exists():
        return [
            Finding(
                ERROR,
                location,
                "index",
                "file not found; run 'validated-memory derive' first",
            )
        ]

    existing = index_path.read_text(encoding="utf-8")
    mismatch = _first_mismatch(existing.splitlines(), content.splitlines())
    if mismatch is None:
        return []
    number, found, expected = mismatch
    return [
        Finding(
            ERROR,
            location,
            "index",
            f"on-disk index does not match the recalculated index at line "
            f"{number}: found {found}, expected {expected}",
        )
    ]


def _check(index_path, content, stdout, stderr):
    findings = index_findings(content, index_path)
    if findings:
        for finding in findings:
            print(finding.render(), file=stderr)
        return EXIT_ERROR
    print("derive --check: index is up to date", file=stdout)
    return EXIT_OK


def _first_mismatch(actual_lines, expected_lines):
    """Return `(line_number, found, expected)` for the first differing line.

    Lines are numbered as on disk. A recalculated `Derived:` line matches any
    on-disk `Derived:` line -- `--check` protects the content, not the
    timestamp -- but the line itself must be there.
    """
    for number in range(1, max(len(actual_lines), len(expected_lines)) + 1):
        actual = actual_lines[number - 1] if number <= len(actual_lines) else None
        expected = expected_lines[number - 1] if number <= len(expected_lines) else None
        if _lines_match(actual, expected):
            continue
        return (
            number,
            "end of file" if actual is None else repr(actual),
            "end of file" if expected is None else repr(expected),
        )
    return None


def _lines_match(actual, expected):
    if expected is not None and expected.startswith("Derived: "):
        return actual is not None and actual.startswith("Derived: ")
    return actual == expected
