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
from pathlib import Path

from . import validate
from . import verdicts as verdicts_module
from .contract import ERROR
from .findings import EXIT_ERROR, EXIT_OK
from .frontmatter import parse as parse_frontmatter

INDEX_FILENAME = "knowledge-index.md"


def run(path, check, stdout, stderr):
    """Derive the knowledge index, or check it against disk. Returns an exit code."""
    documents, findings = validate.collect_and_validate(path)
    for finding in findings:
        print(finding.render(), file=stderr)
    if any(finding.severity == ERROR for finding in findings):
        return EXIT_ERROR

    basis = _basis_location(path)
    content = _render(_rows(documents), basis)

    index_path = Path(INDEX_FILENAME)
    if check:
        return _check(index_path, content, stdout, stderr)

    index_path.write_text(content, encoding="utf-8")
    print(f"derive: {len(documents)} unit(s) indexed", file=stdout)
    return EXIT_OK


def _basis_location(path):
    target = validate.resolve_target(path)
    location = target.as_posix()
    if target.is_dir():
        location += "/"
    return location


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


def _rows(documents):
    """Compute one row per unit, sorted by id: state, evidence and verdict."""
    states = effective_states(documents)
    view = verdicts_module.service_view()
    rows = []
    for unit_id in sorted(states):
        data, state = states[unit_id]
        verdict = _verdict_cell(unit_id, data.get("anchors") or [], view)
        rows.append((unit_id, state, data["evidence"], verdict))
    return rows


def _verdict_cell(unit_id, anchors, view):
    """The verdict column for one unit: the worst verdict among its anchors.

    No anchors -- nothing to probe -- reports `unknown` on its own. With
    anchors, an anchor absent from the service view (never probed) is
    `unknown`, fail-explicit. When the worst verdict is `unknown`, the
    systems behind it are listed; when it is `drifted` and some anchors are
    also `unknown`, those are listed too, tagged as such.
    """
    if not anchors:
        return verdicts_module.UNKNOWN
    per_anchor = [
        (
            anchor.get("system"),
            view.get(
                (unit_id, anchor.get("system"), anchor.get("kind")), verdicts_module.UNKNOWN
            ),
        )
        for anchor in anchors
    ]
    verdict = verdicts_module.worst(anchor_verdict for _system, anchor_verdict in per_anchor)
    unknown_systems = sorted(
        {system for system, anchor_verdict in per_anchor if anchor_verdict == verdicts_module.UNKNOWN}
    )
    if verdict == verdicts_module.UNKNOWN:
        return f"{verdicts_module.UNKNOWN} (" + ", ".join(unknown_systems) + ")"
    if verdict == verdicts_module.DRIFTED and unknown_systems:
        return f"{verdicts_module.DRIFTED} (unknown: " + ", ".join(unknown_systems) + ")"
    return verdict


def _render(rows, basis):
    derived_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    derived_at = derived_at.replace("+00:00", "Z")
    lines = [
        "# Knowledge index",
        "",
        f"Derived: {derived_at}",
        f"Basis: {len(rows)} unit(s) under {basis}",
        "",
        "| id | state | evidence | verdict |",
        "|----|-------|----------|---------|",
    ]
    for unit_id, state, evidence, verdict in rows:
        lines.append(f"| {unit_id} | {state} | {evidence} | {verdict} |")
    return "\n".join(lines) + "\n"


def _check(index_path, content, stdout, stderr):
    location = index_path.as_posix()
    if not index_path.exists():
        print(
            f"ERROR: {location}: index: file not found; "
            "run 'validated-memory derive' first",
            file=stderr,
        )
        return EXIT_ERROR

    existing = index_path.read_text(encoding="utf-8")
    mismatch = _first_mismatch(existing.splitlines(), content.splitlines())
    if mismatch is not None:
        number, found, expected = mismatch
        print(
            f"ERROR: {location}: index: on-disk index does not match the "
            f"recalculated index at line {number}: "
            f"found {found}, expected {expected}",
            file=stderr,
        )
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
