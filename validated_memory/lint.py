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

from .findings import ERROR, WARNING, Finding, report
from .frontmatter import FrontmatterError, parse

DEFAULT_MEMORY_DIR = "memory"
MEMORY_SUFFIX = ".md"
INDEX_FILENAME = "MEMORY.md"

MEMORY_TYPES = ("user", "project", "feedback", "reference")

INDEX_ENTRY_PATTERN = re.compile(r"^-\s+\[[^\]]*\]\(([^)]+)\)")
WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")
SUPERSEDED_PREFIX = "superseded by "
SUPERSEDED_WIKILINK_PATTERN = re.compile(r"^\[\[([^\]]+)\]\]")


def run(path, stdout, stderr):
    """Lint every memory file under `path` and report findings. Returns an exit code."""
    target = Path(path) if path else Path(DEFAULT_MEMORY_DIR)
    documents, findings = _collect(target, explicit=bool(path))
    findings.extend(_lint_memories(documents))
    return report("lint", len(documents), "memory file(s)", findings, stdout, stderr)


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
    """Lint each memory file's frontmatter, then cross-file name resolution.

    Names are collected in a first pass, once every document has parsed, so
    duplicate detection and wikilink resolution do not depend on file order --
    the same two-pass shape `validate` uses for id declaration and supersedes
    resolution.
    """
    findings = []
    parsed = []
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
        parsed.append((location, data, _body(text)))

    for location, data, _text in parsed:
        findings.extend(_check_memory(location, data))

    declared_names = {}
    for location, data, _text in parsed:
        name = data.get("name")
        if not _is_non_empty_string(name):
            continue
        if name in declared_names:
            findings.append(
                Finding(
                    ERROR,
                    location,
                    "name",
                    f"duplicate name '{name}', already declared by "
                    f"{declared_names[name]}",
                )
            )
        else:
            declared_names[name] = location

    valid_names = set(declared_names)
    for location, data, body in parsed:
        own_name = data.get("name")
        if not _is_non_empty_string(own_name):
            own_name = None
        description = data.get("description")
        findings.extend(
            _check_description_wikilinks(location, own_name, description, valid_names)
        )
        findings.extend(_check_wikilink_targets(location, "body", body, valid_names))

    return findings


def _body(text):
    """Return the document text after the closing frontmatter fence.

    Called only once `parse` has already accepted the frontmatter, so the
    fences are known to be well formed.
    """
    lines = text.split("\n")
    for index in range(1, len(lines)):
        if lines[index].rstrip() == "---":
            return "\n".join(lines[index + 1 :])
    return ""  # pragma: no cover - unreachable once `parse` has succeeded


def _check_description_wikilinks(location, own_name, description, valid_names):
    """Check the supersession marker (if any), then any remaining wikilink.

    A wikilink that opens a well-formed `superseded by [[name]]` marker is
    checked by the supersession rule below, not by the generic wikilink scan,
    so it is not reported twice.
    """
    if not isinstance(description, str):
        return []
    findings = []
    scan_text = description
    if description.startswith(SUPERSEDED_PREFIX):
        remainder = description[len(SUPERSEDED_PREFIX) :]
        match = SUPERSEDED_WIKILINK_PATTERN.match(remainder)
        if not match:
            findings.append(
                Finding(
                    ERROR,
                    location,
                    "description",
                    "malformed supersession marker: 'superseded by ' must be "
                    "followed by a [[wikilink]]",
                )
            )
            scan_text = ""
        else:
            target = match.group(1)
            scan_text = remainder[match.end() :]
            if target == own_name:
                findings.append(
                    Finding(
                        ERROR,
                        location,
                        "description",
                        f"supersession points at itself: '{target}'",
                    )
                )
            elif target not in valid_names:
                findings.append(
                    Finding(
                        ERROR,
                        location,
                        "description",
                        f"supersession points at '{target}', which does not exist",
                    )
                )
    findings.extend(_check_wikilink_targets(location, "description", scan_text, valid_names))
    return findings


def _check_wikilink_targets(location, field, text, valid_names):
    if not isinstance(text, str):
        return []
    findings = []
    seen = set()
    for match in WIKILINK_PATTERN.finditer(text):
        target = match.group(1)
        if target in valid_names or target in seen:
            continue
        seen.add(target)
        findings.append(
            Finding(
                WARNING,
                location,
                field,
                f"wikilink to '{target}' has no matching memory (not written yet)",
            )
        )
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
