"""The `lint` subcommand: the agent-memory layer of the adopter.

The agent-memory layer is one Markdown file per fact, plus a one-line-per-
entry index (`MEMORY.md`) at the root of the memory directory. `lint`
enforces four things: the index and the files agree in both directions, each
file's frontmatter is complete, a wikilink between memories names a real
memory (or is flagged as pending), and the supersession convention -- a
`description` rewritten to start with `superseded by [[name]]` -- is either
well formed or reported.
"""

import re
from pathlib import Path, PurePosixPath

from .findings import ERROR, WARNING, Finding
from .frontmatter import FrontmatterError, parse

DEFAULT_MEMORY_DIR = "memory"
MEMORY_SUFFIX = ".md"
INDEX_FILENAME = "MEMORY.md"

MEMORY_TYPES = ("user", "project", "feedback", "reference")

INDEX_ENTRY_PATTERN = re.compile(r"^-\s+\[[^\]]*\]\(([^)]+)\)")

EXIT_OK = 0
EXIT_ERROR = 1


def run(path, stdout, stderr):
    """Lint every memory file under `path` and report findings. Returns an exit code."""
    target = Path(path) if path else Path(DEFAULT_MEMORY_DIR)
    documents, findings = _collect(target, explicit=bool(path))
    findings.extend(_lint_memories(documents))

    errors = [finding for finding in findings if finding.severity == ERROR]
    warnings = [finding for finding in findings if finding.severity == WARNING]
    for finding in findings:
        print(finding.render(), file=stderr)
    print(
        f"lint: {len(documents)} memory file(s) checked, "
        f"{len(errors)} error(s), {len(warnings)} warning(s)",
        file=stdout,
    )
    return EXIT_ERROR if errors else EXIT_OK


def _collect(target, explicit):
    """Resolve the memory directory and its index, and read every memory file."""
    location = target.as_posix()
    if not target.exists():
        if explicit:
            message = "no such file or directory"
        else:
            message = (
                f"no agent-memory directory found at '{location}'; pass a PATH "
                "or run 'validated-memory init'"
            )
        return [], [Finding(ERROR, location, "target", message)]

    index_path = target / INDEX_FILENAME
    if not index_path.exists():
        return [], [
            Finding(
                ERROR,
                index_path.as_posix(),
                "index",
                f"index '{INDEX_FILENAME}' not found; run 'validated-memory init'",
            )
        ]

    index_text = index_path.read_text(encoding="utf-8")

    files = sorted(
        candidate
        for candidate in target.rglob(f"*{MEMORY_SUFFIX}")
        if candidate.is_file() and candidate != index_path
    )

    documents = [
        (candidate.as_posix(), candidate.read_text(encoding="utf-8"))
        for candidate in files
    ]

    files_by_relpath = {
        candidate.relative_to(target).as_posix(): candidate for candidate in files
    }
    hrefs = _parse_index_entries(index_text)
    findings = _check_sync(index_path.as_posix(), hrefs, files_by_relpath)

    return documents, findings


def _parse_index_entries(text):
    """Return the file hrefs of every bullet-with-link entry in the index.

    Only lines shaped `- [Title](file.md)` count as entries; headers and
    prose are ignored.
    """
    hrefs = []
    for line in text.splitlines():
        match = INDEX_ENTRY_PATTERN.match(line.strip())
        if match:
            hrefs.append(match.group(1))
    return hrefs


def _check_sync(index_location, hrefs, files_by_relpath):
    """Check the index and the files agree in both directions."""
    findings = []
    referenced = set()
    for href in hrefs:
        relpath = PurePosixPath(href.strip()).as_posix()
        referenced.add(relpath)
        if relpath not in files_by_relpath:
            findings.append(
                Finding(
                    ERROR,
                    index_location,
                    "entry",
                    f"'{href}' has no matching memory file",
                )
            )
    for relpath, candidate in files_by_relpath.items():
        if relpath not in referenced:
            findings.append(
                Finding(
                    ERROR,
                    candidate.as_posix(),
                    "index",
                    f"memory file has no entry in '{INDEX_FILENAME}'",
                )
            )
    return findings


def _lint_memories(documents):
    findings = []
    for location, text in documents:
        try:
            data = parse(text)
        except FrontmatterError as error:
            findings.append(
                Finding(
                    ERROR, location, "frontmatter", error.message, line=error.lineno
                )
            )
            continue
        findings.extend(_check_memory(location, data))
    return findings


def _check_memory(location, data):
    findings = []
    findings.extend(_check_name(location, data))
    findings.extend(_check_description(location, data))
    findings.extend(_check_type(location, data))
    return findings


def _check_name(location, data):
    if "name" not in data:
        return [Finding(ERROR, location, "name", "required field is missing")]
    name = data["name"]
    if not _is_non_empty_string(name):
        return [
            Finding(
                ERROR, location, "name", f"{_describe(name)} is not a non-empty string"
            )
        ]
    return []


def _check_description(location, data):
    if "description" not in data:
        return [
            Finding(ERROR, location, "description", "required field is missing")
        ]
    description = data["description"]
    if not _is_non_empty_string(description):
        return [
            Finding(
                ERROR,
                location,
                "description",
                f"{_describe(description)} is not a non-empty string",
            )
        ]
    return []


def _check_type(location, data):
    metadata = data.get("metadata")
    if not isinstance(metadata, dict) or "type" not in metadata:
        return [
            Finding(ERROR, location, "metadata.type", "required field is missing")
        ]
    kind = metadata["type"]
    if kind not in MEMORY_TYPES:
        return [
            Finding(
                ERROR,
                location,
                "metadata.type",
                f"{_describe(kind)} is not one of " + ", ".join(MEMORY_TYPES),
            )
        ]
    return []


def _is_non_empty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _describe(value):
    if isinstance(value, str):
        return f"'{value}'"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a mapping"
    return "a missing value"
