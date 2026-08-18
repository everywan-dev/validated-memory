"""The `render` subcommand: static HTML views of an adopter's validated memory.

Two artifacts, `knowledge.html` and `memory.html`, written to the working
directory. Each is self-contained and inert: no JavaScript, no request to the
network, nothing to trust in an attachment. See
docs/design/2026-08-18-render-views.md.
"""

import os
from pathlib import Path

from . import knowledge_view, validate
from .findings import EXIT_ERROR, EXIT_OK

KNOWLEDGE_ARTIFACT = "knowledge.html"
MEMORY_ARTIFACT = "memory.html"


def run(only_existing, stdout, stderr):
    """Render the views. Returns an exit code."""
    documents, ok = validate.gated_source(None, stderr)
    if not ok:
        return EXIT_ERROR
    content = knowledge_view.build(documents, _basis_location(None))
    action = write_if_changed(Path(KNOWLEDGE_ARTIFACT), content)
    print(f"render: {action} {KNOWLEDGE_ARTIFACT}", file=stdout)
    return EXIT_OK


def _basis_location(path):
    target = validate.resolve_target(path)
    location = target.as_posix()
    if target.is_dir():
        location += "/"
    return location


def write_if_changed(path, content):
    """Write `content` to `path` only when it differs. Returns what happened.

    The write is atomic -- a temporary file in the same directory, then a
    rename -- so a failure can never leave a half-written page for a reader
    to open, and an unchanged artifact is not touched at all, which is what
    keeps the startup hook from dirtying `git status` on every session.
    """
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return "unchanged"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)
    return "wrote"
