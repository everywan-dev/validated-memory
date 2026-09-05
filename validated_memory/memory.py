"""Read agent-memory files, index entries and references; `lint` owns policy.

References resolve by declared name; filenames are canonical identities
(ADR 0001). Filename hints require uniqueness across all collected files,
including those with unparseable frontmatter.
"""

import re
from collections import namedtuple
from pathlib import PurePosixPath

DEFAULT_DIR = "memory"
SUFFIX = ".md"
INDEX_FILENAME = "MEMORY.md"

INDEX_ENTRY_PATTERN = re.compile(r"^-\s+\[([^\]]*)\]\(([^)]+)\)(.*)$")
WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")
SUPERSEDED_PREFIX = "superseded by "
SUPERSEDED_WIKILINK_PATTERN = re.compile(r"^\[\[([^\]]+)\]\]")

# `relpath` is index-relative; `location` is the path a finding names.
Document = namedtuple("Document", "location relpath text")

# `note` is uninterpreted link suffix; `line` preserves the stripped entry.
IndexEntry = namedtuple("IndexEntry", "title href note line")

# Only `by_name` resolves; `by_filename` explains unresolved references.
Resolution = namedtuple("Resolution", "by_name by_filename")


class MemoryReadError(Exception):
    """Unreadable memory text, with location and reason for a caller's finding."""

    def __init__(self, location, reason):
        super().__init__(f"{location}: {reason}")
        self.location = location
        self.reason = reason

# A malformed marker has target=None and no remainder to scan for wikilinks.
# Otherwise `remainder` contains the description after the successor link.
Supersession = namedtuple("Supersession", "target remainder")


def filename(location):
    """Return the memory's canonical identity: its filename minus `.md`.

    Do not use `PurePosixPath.stem`: `.md` must have an empty identity.
    """
    name = PurePosixPath(location).name
    if name.endswith(SUFFIX):
        return name[: -len(SUFFIX)]
    return name  # pragma: no cover - only `*.md` files are ever collected


def documents(target):
    """Return sorted recursive memory documents, excluding the root index.

    Raises `MemoryReadError` for unreadable UTF-8 files. The caller checks
    directory/index existence and decides finding severity.
    """
    index_path = target / INDEX_FILENAME
    collected = []
    for candidate in sorted(target.rglob(f"*{SUFFIX}")):
        if not candidate.is_file() or candidate == index_path:
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise MemoryReadError(
                candidate.as_posix(), "not valid UTF-8"
            ) from error
        except OSError as error:
            raise MemoryReadError(
                candidate.as_posix(), error.strerror or str(error)
            ) from error
        collected.append(
            Document(
                location=candidate.as_posix(),
                relpath=candidate.relative_to(target).as_posix(),
                text=text,
            )
        )
    return collected


def index_entries(text):
    """Return bullet-with-link entries in order, with href and note stripped."""
    entries = []
    for line in text.splitlines():
        match = INDEX_ENTRY_PATTERN.match(line.strip())
        if match:
            title, href, note = match.groups()
            entries.append(
                IndexEntry(title, href.strip(), note.strip(), line.strip())
            )
    return entries


def body(text):
    """Return text after the closing fence; requires parsed frontmatter."""
    lines = text.split("\n")
    for index in range(1, len(lines)):
        if lines[index].rstrip() == "---":
            return "\n".join(lines[index + 1 :])
    return ""  # pragma: no cover - unreachable once the frontmatter parsed


def wikilinks(text):
    """Return the `[[name]]` targets in `text`, first occurrence first, once each."""
    if not isinstance(text, str):
        return []
    targets = []
    seen = set()
    for match in WIKILINK_PATTERN.finditer(text):
        target = match.group(1)
        if target in seen:
            continue
        seen.add(target)
        targets.append(target)
    return targets


def supersession(description):
    """Return None for no prefix, otherwise a parsed `Supersession`.

    A prefix without a following wikilink yields target=None.
    """
    if not isinstance(description, str) or not description.startswith(
        SUPERSEDED_PREFIX
    ):
        return None
    remainder = description[len(SUPERSEDED_PREFIX) :]
    match = SUPERSEDED_WIKILINK_PATTERN.match(remainder)
    if not match:
        return Supersession(None, "")
    return Supersession(match.group(1), remainder[match.end() :])


def resolution(documents_read, declared):
    """Build resolution from all documents and location-to-declared-name data.

    Filename ambiguity includes unparseable siblings, not just `declared`.
    """
    by_filename = {}
    for document in documents_read:
        by_filename.setdefault(filename(document.location), []).append(
            declared.get(document.location)
        )
    return Resolution(
        by_name=set(name for name in declared.values() if is_declared(name)),
        by_filename={
            name: names[0]
            for name, names in by_filename.items()
            if len(names) == 1 and is_declared(names[0])
        },
    )


def filename_hint(target, resolved):
    """Describe an unambiguous filename's declared name, or return None."""
    declared = resolved.by_filename.get(target)
    if declared is None:
        return None
    return f"'{target}{SUFFIX}' declares name '{declared}'"


def is_declared(value):
    """Whether a value is a string containing non-whitespace characters."""
    return isinstance(value, str) and bool(value.strip())
