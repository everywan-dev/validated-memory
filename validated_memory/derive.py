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

from . import extension as extension_module
from . import validate
from .contract import ERROR, Finding, validate_documents
from .frontmatter import parse as parse_frontmatter

INDEX_FILENAME = "knowledge-index.md"
VERDICT_UNKNOWN = "unknown"

EXIT_OK = 0
EXIT_ERROR = 1


def run(path, check, stdout, stderr):
    """Derive the knowledge index, or check it against disk. Returns an exit code."""
    documents, findings = _validate(path)
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


def _validate(path):
    """Run the same validation as `validate`: base contract plus extension."""
    try:
        extension = extension_module.load(Path())
    except extension_module.ExtensionError as error:
        return [], [
            Finding(ERROR, error.location, error.field, error.message, line=error.line)
        ]
    target = Path(path) if path else Path(validate.DEFAULT_KNOWLEDGE_DIR)
    documents, findings = validate._collect(target, explicit=bool(path))
    findings = list(findings)
    findings.extend(validate_documents(documents, extension))
    return documents, findings


def _basis_location(path):
    target = Path(path) if path else Path(validate.DEFAULT_KNOWLEDGE_DIR)
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
    mismatch = _first_mismatch(_without_derived(existing), _without_derived(content))
    if mismatch is not None:
        print(
            f"ERROR: {location}: index: on-disk index does not match the "
            f"recalculated index at line {mismatch[0]}: "
            f"found {mismatch[1]!r}, expected {mismatch[2]!r}",
            file=stderr,
        )
        return EXIT_ERROR

    print("derive --check: index is up to date", file=stdout)
    return EXIT_OK


def _without_derived(content):
    """Drop the `Derived:` line: `--check` protects content, not the timestamp."""
    return [line for line in content.split("\n") if not line.startswith("Derived: ")]


def _first_mismatch(actual_lines, expected_lines):
    """Return `(line_number, actual, expected)` for the first differing line."""
    for number, (actual, expected) in enumerate(
        zip(actual_lines, expected_lines), start=1
    ):
        if actual != expected:
            return number, actual, expected
    if len(actual_lines) != len(expected_lines):
        shorter = min(len(actual_lines), len(expected_lines))
        return shorter + 1, actual_lines[shorter:], expected_lines[shorter:]
    return None
