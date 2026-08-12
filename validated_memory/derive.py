"""The `derive` subcommand: re-derive the knowledge index from units.

`derive` requires a valid source: it runs the same validation as `validate`
first (the base contract plus the adopter's declared extension) and refuses to
write or check anything when that validation reports an ERROR. The index is a
plain Markdown table -- readable without the plugin -- naming, per unit, its
effective state (computed from `supersedes`, never mutating the unit), its
evidence state, and a verdict column that this version always reports as
`unknown`: freshness probes land in a later ticket.
"""

from datetime import datetime, timezone
from pathlib import Path

from . import validate
from .contract import ERROR
from .frontmatter import parse as parse_frontmatter

INDEX_FILENAME = "knowledge-index.md"
VERDICT_UNKNOWN = "unknown"

EXIT_OK = 0
EXIT_ERROR = 1


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


def _rows(documents):
    """Compute the effective state per unit, sorted by id.

    Documents already passed validation, so every `id` is present, valid and
    unique, and every `supersedes` entry names a declared id. The unit itself
    is never mutated; the effective state is computed from the whole set.
    """
    units = {}
    for _location, text in documents:
        data = parse_frontmatter(text)
        units[data["id"]] = data

    superseded_by = {}
    for unit_id, data in units.items():
        for target_id in data.get("supersedes") or []:
            superseded_by.setdefault(target_id, []).append(unit_id)

    rows = []
    for unit_id in sorted(units):
        data = units[unit_id]
        supersessors = sorted(superseded_by.get(unit_id, []))
        if supersessors:
            state = "superseded by " + ", ".join(supersessors)
        else:
            state = "active"
        rows.append((unit_id, state, data["evidence"]))
    return rows


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
    for unit_id, state, evidence in rows:
        lines.append(f"| {unit_id} | {state} | {evidence} | {VERDICT_UNKNOWN} |")
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
