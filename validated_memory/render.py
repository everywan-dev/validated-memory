"""The `render` subcommand: static HTML views of an adopter's validated memory.

Two artifacts, `knowledge.html` and `memory.html`, written to the working
directory. Each is self-contained and inert: no JavaScript, no request to the
network, nothing to trust in an attachment. See
docs/design/2026-08-18-render-views.md.
"""

import os
from pathlib import Path

from . import knowledge_view, memory_view, validate
from . import memory as memory_module
from . import verdicts as verdicts_module
from .contract import ERROR
from .findings import EXIT_ERROR, EXIT_OK, Finding
from .frontmatter import FrontmatterError
from .frontmatter import parse as parse_frontmatter

KNOWLEDGE_ARTIFACT = "knowledge.html"
MEMORY_ARTIFACT = "memory.html"

# Fixed order: what `build_artifacts` returns and `run` writes, in the order
# reported on stdout. A dict keeps insertion order in practice, but writing
# it out here means that fact is never load-bearing.
ARTIFACTS = (KNOWLEDGE_ARTIFACT, MEMORY_ARTIFACT)


def run(only_existing, stdout, stderr):
    """Render the views. Returns an exit code."""
    artifacts, ok = build_artifacts(stderr)
    if not ok:
        return EXIT_ERROR
    for path in ARTIFACTS:
        action = write_if_changed(Path(path), artifacts[path])
        print(f"render: {action} {path}", file=stdout)
    return EXIT_OK


def build_artifacts(stderr):
    """Build every artifact's content in memory. Writes nothing.

    Returns `(artifacts, ok)`. On success `artifacts` maps each path in
    `ARTIFACTS` to its rendered content and `ok` is True. On a read or
    validation precondition failing -- an ERROR finding over the curated
    layer, an unreadable verdict log, or a missing memory directory or index
    -- every finding has already been printed to `stderr`, `artifacts` is
    empty and `ok` is False.

    Both artifacts are built before either is written (`run` does the
    writing) so that a failure on the second can never leave the first
    written from a run that, as a whole, did not succeed. `--only-existing`
    (`init --view`) needs the same build-without-writing step, so it lives
    here once rather than being composed at each of those call sites too.
    """
    documents, ok = validate.gated_source(None, stderr)
    if not ok:
        return {}, False

    try:
        # Both reads of the log happen here, together, before either is
        # handed to `knowledge_view.build`: `service_view` is the one that
        # validates (it raises on a record such as an explicit
        # `payload: null`), and it must run before `build` groups `history`'s
        # records by key -- keeping both calls at this one site, rather than
        # one of them inside `build`, is what keeps that order from being an
        # accident of which line comes first in a function body.
        records = verdicts_module.history()
        view = verdicts_module.service_view()
        knowledge_content = knowledge_view.build(
            documents, _basis_location(None), records, view
        )
    except verdicts_module.VerdictLogError as error:
        # Same shape `derive` reports: a log this reader cannot parse is a
        # finding naming the file (and line, when the fault is one line's
        # rather than the whole file's), never a traceback -- the person
        # opening this page has no repository to read a stack trace against.
        finding = Finding(
            ERROR,
            verdicts_module.LOG_FILENAME,
            "log",
            error.message,
            line=error.lineno,
        )
        print(finding.render(), file=stderr)
        return {}, False

    memory_target = Path(memory_module.DEFAULT_DIR)
    precondition = _memory_precondition(memory_target)
    if precondition is not None:
        print(precondition.render(), file=stderr)
        return {}, False

    memory_documents, memory_resolution = _memory_source(memory_target)
    memory_content = memory_view.build(memory_documents, memory_resolution)

    return {
        KNOWLEDGE_ARTIFACT: knowledge_content,
        MEMORY_ARTIFACT: memory_content,
    }, True


def _memory_precondition(target):
    """Check the memory directory and its index exist. Returns a `Finding`, or `None`.

    This is a read precondition, the same one `lint` stops on -- not a
    validation gate. Everything else about the memory layer (frontmatter,
    sync with the index, wikilink resolution) is `lint`'s business, not
    `render`'s: this view does not enforce.
    """
    location = target.as_posix()
    if not target.exists():
        return Finding(
            ERROR,
            location,
            "target",
            f"no agent-memory directory found at '{location}'; run "
            "'validated-memory init'",
        )
    index_path = target / memory_module.INDEX_FILENAME
    if not index_path.exists():
        return Finding(
            ERROR,
            index_path.as_posix(),
            "index",
            f"index '{memory_module.INDEX_FILENAME}' not found; run "
            "'validated-memory init'",
        )
    return None


def _memory_source(target):
    """Read the memory documents and build the resolution table for them.

    A document whose frontmatter will not parse is simply absent from
    `declared` -- the same two-pass shape `lint` uses -- so it can never be a
    resolution target, while `documents` (the full, unfiltered set) still
    goes to `memory_view.build` so that document gets an entry of its own.
    """
    documents = memory_module.documents(target)
    declared = {}
    for document in documents:
        try:
            data = parse_frontmatter(document.text)
        except FrontmatterError:
            continue
        declared[document.location] = data.get("name")
    return documents, memory_module.resolution(documents, declared)


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
    keeps the startup hook from dirtying `git status` on every session. The
    temporary file's name includes this process's pid: the startup hook runs
    `render --only-existing` on every session, and this environment routinely
    has several sessions working the same repository at once, so a fixed
    temp name would let two concurrent runs interleave writes to it before
    either reaches its rename -- the very half-written page the atomic
    rename exists to prevent. On any failure the temporary file is removed
    rather than left behind.
    """
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return "unchanged"
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return "wrote"
