"""The `lint` subcommand: the agent-memory layer of the adopter.

The agent-memory layer is one Markdown file per fact, plus a one-line-per-
entry index (`MEMORY.md`) at the root of the memory directory. `lint`
enforces five things: the index and the files agree in both directions, each
file's frontmatter is complete, `name` matches the filename that is the
memory's canonical identity, a wikilink between memories names a real memory
(or is flagged as pending), and the supersession convention -- a
`description` rewritten to start with `superseded by [[name]]` -- is either
well formed or reported.
"""

import re
from collections import namedtuple
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

# How a reference to another memory is resolved. `by_name` is what resolution
# actually goes by; `by_filename` is only ever used to explain a reference that
# did not resolve, since the canonical identity is the filename and that is
# what a writer reaches for.
Resolution = namedtuple("Resolution", "by_name by_filename")


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
    findings = _check_filename_collisions(documents)
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

    resolution = Resolution(
        by_name=set(declared_names),
        by_filename=_index_by_filename(documents, parsed),
    )
    for location, data, body in parsed:
        own_name = data.get("name")
        if not _is_non_empty_string(own_name):
            own_name = None
        description = data.get("description")
        findings.extend(
            _check_description_wikilinks(location, own_name, description, resolution)
        )
        findings.extend(
            _check_wikilink_targets(location, "body", body, resolution)
        )

    return findings


def _check_filename_collisions(documents):
    """Report two memories claiming the same canonical identity.

    Two files in one directory cannot share a name, so this only ever fires
    across subdirectories. It is a fact about the files, so it is checked
    before any frontmatter is read and holds even when neither file parses.

    A WARNING for the same reason the divergence rule is one -- it becomes an
    ERROR in 2.0.0 alongside it. Reporting it matters now because without it
    `lint` tells both files to repair `name` towards the same value, and
    following that advice lands on the duplicate-name ERROR unannounced.
    """
    findings = []
    first_seen = {}
    for location, _text in documents:
        filename = _filename(location)
        if not filename:
            continue  # no identity at all; its own rule reports that
        if filename not in first_seen:
            first_seen[filename] = location
            continue
        findings.append(
            Finding(
                WARNING,
                location,
                "filename",
                f"the filename '{filename}' is also carried by "
                f"{first_seen[filename]}; the filename is the canonical "
                "identity, so these are two memories with the same identity "
                "-- rename one",
            )
        )
    return findings


def _index_by_filename(documents, parsed):
    """Map each unambiguous filename (without `.md`) to the `name` it declares.

    Used to explain a reference that does not resolve -- a wikilink or a
    supersession target: the writer reached for the filename, which is the
    canonical identity, while resolution goes by `name`.

    Ambiguity is counted over **every** file collected, not just the ones that
    parsed. A filename carried by two memories in different subdirectories is
    left out, and it stays ambiguous even when only one of the two has readable
    frontmatter: the readable one is not thereby known to be the memory meant.
    A filename whose `name` is missing or empty is left out too -- that defect
    has its own rule.
    """
    declared_by_location = {
        location: data.get("name") for location, data, _body in parsed
    }
    names_by_filename = {}
    for location, _text in documents:
        names_by_filename.setdefault(_filename(location), []).append(
            declared_by_location.get(location)
        )
    return {
        filename: declared[0]
        for filename, declared in names_by_filename.items()
        if len(declared) == 1 and _is_non_empty_string(declared[0])
    }


def _filename(location):
    """Return the memory's canonical identity: its filename minus `.md`.

    Not `PurePosixPath.stem`, which reads a leading dot as the start of a name
    rather than as a suffix boundary: a file called `.md` has `stem == '.md'`,
    which would hand it the identity `.md` instead of none at all.
    """
    name = PurePosixPath(location).name
    if name.endswith(MEMORY_SUFFIX):
        return name[: -len(MEMORY_SUFFIX)]
    return name  # pragma: no cover - only `*.md` files are ever collected


def _filename_hint(target, resolution):
    """Name the file a reference was reaching for, when it is certain which."""
    declared = resolution.by_filename.get(target)
    if declared is None:
        return None
    return f"'{target}{MEMORY_SUFFIX}' declares name '{declared}'"


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


def _check_description_wikilinks(location, own_name, description, resolution):
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
            findings.extend(
                _check_supersession_target(location, own_name, target, resolution)
            )
    findings.extend(
        _check_wikilink_targets(location, "description", scan_text, resolution)
    )
    return findings


def _check_supersession_target(location, own_name, target, resolution):
    """Check the successor a memory is retired onto.

    Order matters. A target that resolves to another memory is a valid
    supersession even when it happens to equal this file's own filename, so
    resolution is settled first. Only then does the filename count as naming
    this memory itself -- it is the canonical identity, so retiring a memory
    onto it is retiring it onto itself, whatever `name` currently says.
    """
    if target in resolution.by_name and target != own_name:
        return []
    if target == own_name or target == _filename(location):
        return [
            Finding(
                ERROR,
                location,
                "description",
                f"supersession points at itself: '{target}'",
            )
        ]
    hint = _filename_hint(target, resolution)
    if hint is None:
        message = f"supersession points at '{target}', which does not exist"
    else:
        # Unlike a wikilink, a successor cannot be left pending: this gates.
        # Saying the memory does not exist would send the repair to the wrong
        # file, so the message names what actually fails to resolve.
        message = (
            f"supersession points at '{target}', which does not resolve by "
            f"name; {hint}"
        )
    return [Finding(ERROR, location, "description", message)]


def _check_wikilink_targets(location, field, text, resolution):
    if not isinstance(text, str):
        return []
    findings = []
    seen = set()
    for match in WIKILINK_PATTERN.finditer(text):
        target = match.group(1)
        if target in resolution.by_name or target in seen:
            continue
        seen.add(target)
        hint = _filename_hint(target, resolution)
        if hint is None:
            message = (
                f"wikilink to '{target}' has no matching memory (not written yet)"
            )
        else:
            # The file is right there; what does not resolve is its `name`.
            # Saying 'not written yet' here would point at the wrong repair.
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


def _check_filename_identity(location, data):
    """Check `name` against the filename, which is the canonical identity.

    When the two disagree the filename wins and `name` is repaired to match it,
    never the other way round: a third of a real corpus carries titles in
    `name` -- spaces, dots, capitals -- for which no rename exists (ADR 0001).

    This is a WARNING rather than an ERROR purely as a migration concession for
    memory sets written before the rule. It becomes an ERROR in 2.0.0.
    """
    name = data["name"]
    filename = _filename(location)
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
