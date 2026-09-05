"""Enforce agent-memory index, frontmatter, identity and reference contracts.

`memory` owns reading and resolution; this module owns findings and severity.
See `docs/reference/agent-memory.md` for the public rules.
"""

import re
from pathlib import Path, PurePosixPath

from . import memory as memory_module
from .findings import ERROR, WARNING, Finding, report
from .frontmatter import FENCE, FrontmatterError, parse

DEFAULT_MEMORY_DIR = memory_module.DEFAULT_DIR
INDEX_FILENAME = memory_module.INDEX_FILENAME

MEMORY_TYPES = ("user", "project", "feedback", "reference")


def run(path, stdout, stderr):
    """Lint every memory file under `path` and report findings. Returns an exit code."""
    documents, findings = collect_and_lint(path)
    return report("lint", len(documents), "memory file(s)", findings, stdout, stderr)


def collect_and_lint(path):
    """Return collected memory documents and findings without printing."""
    target = Path(path) if path else Path(DEFAULT_MEMORY_DIR)
    documents, findings = _collect(target, explicit=bool(path))
    findings.extend(_lint_memories(documents))
    return documents, findings


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

    try:
        documents = memory_module.documents(target)
    except memory_module.MemoryReadError as error:
        return [], [
            Finding(
                ERROR,
                error.location,
                "memory",
                f"memory file could not be read: {error.reason}",
            )
        ]
    entries = memory_module.index_entries(index_path.read_text(encoding="utf-8"))
    findings = _check_sync(index_path.as_posix(), entries, documents)

    return documents, findings


def _check_sync(index_location, entries, documents):
    """Check the index and the files agree in both directions."""
    findings = []
    by_relpath = {document.relpath: document for document in documents}
    referenced = set()
    for entry in entries:
        relpath = PurePosixPath(entry.href).as_posix()
        referenced.add(relpath)
        if relpath not in by_relpath:
            findings.append(
                Finding(
                    ERROR,
                    index_location,
                    "entry",
                    f"'{entry.href}' has no matching memory file",
                )
            )
    for relpath, document in by_relpath.items():
        if relpath not in referenced:
            findings.append(
                Finding(
                    ERROR,
                    document.location,
                    "index",
                    f"memory file has no entry in '{INDEX_FILENAME}'",
                )
            )
    return findings


def _lint_memories(documents):
    """Check filenames and frontmatter, collect names, then resolve references."""
    findings = _check_filename_collisions(documents)
    parsed = []
    for document in documents:
        try:
            data = parse(document.text)
        except FrontmatterError as error:
            findings.append(
                Finding(
                    ERROR,
                    document.location,
                    "frontmatter",
                    error.message,
                    line=error.lineno,
                )
            )
            continue
        parsed.append((document.location, data, memory_module.body(document.text)))
        findings.extend(
            _check_source_description_form(
                document.location, document.relpath, document.text
            )
        )

    for location, data, _body in parsed:
        findings.extend(_check_memory(location, data))

    declared_names = {}
    for location, data, _body in parsed:
        name = data.get("name")
        if not memory_module.is_declared(name):
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

    resolution = memory_module.resolution(
        documents, {location: data.get("name") for location, data, _body in parsed}
    )
    for location, data, body in parsed:
        own_name = data.get("name")
        if not memory_module.is_declared(own_name):
            own_name = None
        description = data.get("description")
        findings.extend(
            _check_description_references(location, own_name, description, resolution)
        )
        findings.extend(_check_references(location, "body", body, resolution))

    return findings


def _check_filename_collisions(documents):
    """Warn on shared filename identities, including unparseable documents.

    Migration severity becomes ERROR in 2.0.0, as for name divergence.
    """
    findings = []
    first_seen = {}
    for document in documents:
        filename = memory_module.filename(document.location)
        if not filename:
            continue  # no identity at all; its own rule reports that
        if filename not in first_seen:
            first_seen[filename] = document.location
            continue
        findings.append(
            Finding(
                WARNING,
                document.location,
                "filename",
                f"the filename '{filename}' is also carried by "
                f"{first_seen[filename]}; the filename is the canonical "
                "identity, so these are two memories with the same identity "
                "-- rename one",
            )
        )
    return findings


def _check_description_references(location, own_name, description, resolution):
    """Check supersession once, then scan only the remaining description links."""
    if not isinstance(description, str):
        return []
    findings = []
    scan_text = description
    marker = memory_module.supersession(description)
    if marker is not None:
        scan_text = marker.remainder
        if marker.target is None:
            findings.append(
                Finding(
                    ERROR,
                    location,
                    "description",
                    "malformed supersession marker: 'superseded by ' must be "
                    "followed by a [[wikilink]]",
                )
            )
        else:
            findings.extend(
                _check_supersession_target(
                    location, own_name, marker.target, resolution
                )
            )
    findings.extend(_check_references(location, "description", scan_text, resolution))
    return findings


def _check_supersession_target(location, own_name, target, resolution):
    """Require an existing, different successor.

    Resolve another declared name before testing filename self-reference:
    that name may legitimately equal this entry's filename.
    """
    if target in resolution.by_name and target != own_name:
        return []
    if target == own_name or target == memory_module.filename(location):
        return [
            Finding(
                ERROR,
                location,
                "description",
                f"supersession points at itself: '{target}'",
            )
        ]
    hint = memory_module.filename_hint(target, resolution)
    if hint is None:
        message = f"supersession points at '{target}', which does not exist"
    else:
        message = (
            f"supersession points at '{target}', which does not resolve by "
            f"name; {hint}"
        )
    return [Finding(ERROR, location, "description", message)]


def _check_references(location, field, text, resolution):
    """Report every wikilink in `text` that does not resolve to a memory."""
    findings = []
    for target in memory_module.wikilinks(text):
        if target in resolution.by_name:
            continue
        hint = memory_module.filename_hint(target, resolution)
        if hint is None:
            message = f"wikilink to '{target}' has no matching memory (not written yet)"
        else:
            message = f"wikilink to '{target}' has no matching memory; {hint}"
        findings.append(Finding(WARNING, location, field, message))
    return findings


def _check_memory(location, data):
    findings = []
    name_findings = _check_name(location, data)
    findings.extend(name_findings)
    if not name_findings:
        findings.extend(_check_filename_identity(location, data))
    findings.extend(_check_description(location, data))
    findings.extend(_check_type(location, data))
    return findings


def _check_name(location, data):
    if "name" not in data:
        return [Finding(ERROR, location, "name", "required field is missing")]
    name = data["name"]
    if not memory_module.is_declared(name):
        return [
            Finding(
                ERROR, location, "name", f"{_describe(name)} is not a non-empty string"
            )
        ]
    return []


def _check_filename_identity(location, data):
    """Warn when `name` differs from the canonical filename (ADR 0001).

    Repair the name, not the filename. Migration severity becomes ERROR in 2.0.0.
    """
    name = data["name"]
    filename = memory_module.filename(location)
    if not filename:
        return [
            Finding(
                WARNING,
                location,
                "name",
                f"the filename '{PurePosixPath(location).name}' carries no "
                "identity once the '.md' suffix is removed; rename the file",
            )
        ]
    if name == filename:
        return []
    return [
        Finding(
            WARNING,
            location,
            "name",
            f"'{name}' does not match the filename '{filename}'; the filename "
            "is the canonical identity -- repair 'name' to match it",
        )
    ]


def _check_description(location, data):
    if "description" not in data:
        return [Finding(ERROR, location, "description", "required field is missing")]
    description = data["description"]
    if not memory_module.is_declared(description):
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
        return [Finding(ERROR, location, "metadata.type", "required field is missing")]
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


def _describe(value):
    if isinstance(value, str):
        return f"'{value}'"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a mapping"
    return "a missing value"


# Match the startup hook's direct-child source filename glob, not alias grammar.
# Unlike the hook, lint also checks symlinked entries; this does not imply a count.
SOURCE_FILENAME = re.compile(r"^source-[^/]*\.md$")
QUOTED_DESCRIPTION = re.compile(r"^description[ ]*:[ \t]*[\"']")
DESCRIPTION_KEY = re.compile(r"^description[ ]*:")


def _check_source_description_form(location, relpath, text):
    """Warn on quoted source descriptions; both forms parse and the hook counts both.

    Requires parsed frontmatter. Scan only its first block: the parser excludes
    payload tabs, but permits tabs on fences and in the body outside this check.
    """
    if not SOURCE_FILENAME.match(relpath):
        return []
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != FENCE:
        return []
    for line in lines[1:]:
        stripped = line.rstrip()
        if stripped == FENCE:
            return []
        if QUOTED_DESCRIPTION.match(stripped):
            return [
                Finding(
                    WARNING,
                    location,
                    "description",
                    "a source record's description is written unquoted; "
                    "quoted parses but is not the canonical form",
                )
            ]
        if DESCRIPTION_KEY.match(stripped):
            return []
    return []
