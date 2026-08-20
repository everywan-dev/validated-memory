"""The `render` subcommand: static HTML views of an adopter's validated memory.

Two artifacts, `knowledge.html` and `memory.html`, written to the working
directory. Each is self-contained and inert: no JavaScript, no request to the
network, nothing to trust in an attachment. See
docs/design/2026-08-18-render-views.md.

Known, accepted limits of the writing model -- each self-heals on the next
run, which the startup hook provides at every session start:

- Atomicity is per artifact, not across the two: a write that succeeds for
  the first page and fails for the second (reported as an ERROR, never
  silently) leaves pages from two generations side by side.
- Between concurrent sessions the last writer wins: a slower build finishing
  after a newer one re-publishes what it built. Both are complete, valid
  pages -- the pid-named temporary rules out interleaved bytes -- only the
  vintage can regress until the next run.
- The verdict log is read twice per build (`history`, then `service_view`);
  a probe appending between the two reads can leave summary and history one
  record apart on one page.
"""

import os
from pathlib import Path

from . import knowledge_view, memory_view, validate
from . import memory as memory_module
from . import verdicts as verdicts_module
from .contract import ERROR
from .findings import EXIT_ERROR, EXIT_OK, WARNING, Finding
from .frontmatter import FrontmatterError
from .frontmatter import parse as parse_frontmatter

KNOWLEDGE_ARTIFACT = "knowledge.html"
MEMORY_ARTIFACT = "memory.html"

# Fixed order: what `build_artifacts` returns and `run` writes, in the order
# reported on stdout. A dict keeps insertion order in practice, but writing
# it out here means that fact is never load-bearing.
ARTIFACTS = (KNOWLEDGE_ARTIFACT, MEMORY_ARTIFACT)


def run(only_existing, stdout, stderr):
    """Render the views. Returns an exit code.

    `--only-existing` (the unattended mode a `SessionStart` hook runs on
    every session) regenerates only the artifacts already present and
    creates neither: with nothing on disk, the run is a clean no-op, before
    the corpus is even read. It is also fail-open -- an ERROR that would
    gate an explicit run downgrades to a WARNING here, and exits 0, because
    a person who runs `render` by hand is entitled to be told the views
    were not built, while a hook re-reporting the same ERROR on every
    session start until someone fixes the corpus helps nobody. Fail-open
    never means "write a page built on data the enforcement rejected": on
    that path `build_artifacts` still returns no artifacts, so whatever is
    already on disk is left exactly as it was.

    A write that fails at the OS level (permissions, a full disk, ...) is
    downgraded the same way: a WARNING under `--only-existing`, an ERROR run
    explicitly -- see `write_if_changed`. Either way it is a `Finding`
    reported like any other, never an exception; a person who runs `render`
    by hand is entitled to a traceback-free reason it did not write, and a
    hook must never let one reach stderr on every session start.
    """
    if only_existing:
        targets = [path for path in ARTIFACTS if Path(path).exists()]
        if not targets:
            return EXIT_OK
    else:
        targets = list(ARTIFACTS)

    artifacts, findings, ok = build_artifacts(downgrade=only_existing)
    for finding in findings:
        print(finding.render(), file=stderr)
    if not ok:
        return EXIT_OK if only_existing else EXIT_ERROR
    write_ok = True
    for path in targets:
        action, finding = write_if_changed(Path(path), artifacts[path])
        if finding is not None:
            print(_downgraded(finding, only_existing).render(), file=stderr)
            write_ok = False
            continue
        print(f"render: {action} {path}", file=stdout)
    if not write_ok:
        return EXIT_OK if only_existing else EXIT_ERROR
    return EXIT_OK


def build_artifacts(downgrade=False):
    """Build every artifact's content in memory. Writes nothing, prints nothing.

    Returns `(artifacts, findings, ok)`. On success `artifacts` maps each
    path in `ARTIFACTS` to its rendered content, `findings` holds whatever
    non-gating findings came up along the way (validation WARNINGs, for
    instance), and `ok` is True. On a read or validation precondition
    failing -- an ERROR finding over the curated layer, an unreadable
    verdict log, or a missing memory directory or index -- `artifacts` is
    empty, `findings` holds every finding gathered up to the failure, and
    `ok` is False. Printing `findings` -- exactly once -- is the caller's
    job, not this function's: `run` and `init --view` both call this and
    each has its own findings to fold them into, so a single shared print
    site here would either print nothing useful to a caller building its
    own tally or print things twice.

    Both artifacts are built before either is written (`run` does the
    writing) so that a failure on the second can never leave the first
    written from a run that, as a whole, did not succeed. `--only-existing`
    (`init --view`) needs the same build-without-writing step, so it lives
    here once rather than being composed at each of those call sites too.

    `downgrade`, set by `run`'s unattended mode, downgrades every ERROR
    finding to WARNING in the returned list instead -- `ok` is computed from
    the real severity regardless, so a downgraded run still returns `False`
    here and writes nothing; only the returned severity and `run`'s exit
    code differ.
    """
    documents, validation_findings = validate.collect_and_validate(None)
    has_error = any(finding.severity == ERROR for finding in validation_findings)
    findings = [_downgraded(finding, downgrade) for finding in validation_findings]
    if has_error:
        return {}, findings, False

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
            documents, validate.basis_location(None), records, view
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
        findings.append(_downgraded(finding, downgrade))
        return {}, findings, False

    memory_target = Path(memory_module.DEFAULT_DIR)
    precondition = _memory_precondition(memory_target)
    if precondition is not None:
        findings.append(_downgraded(precondition, downgrade))
        return {}, findings, False

    try:
        memory_documents, memory_resolution = _memory_source(memory_target)
    except memory_module.MemoryReadError as error:
        # Same shape the unreadable verdict log gets above: a finding naming
        # the file, never a traceback.
        finding = Finding(
            ERROR,
            error.location,
            "memory",
            f"memory file could not be read: {error.reason}",
        )
        findings.append(_downgraded(finding, downgrade))
        return {}, findings, False
    # `_memory_precondition` already confirmed `memory_target` exists and
    # holds an index, so it is a directory by the time we reach here -- the
    # trailing slash always applies, the same convention `basis_location`
    # uses for the curated layer.
    memory_basis = memory_target.as_posix() + "/"
    memory_content = memory_view.build(
        memory_documents, memory_basis, memory_resolution
    )

    return {
        KNOWLEDGE_ARTIFACT: knowledge_content,
        MEMORY_ARTIFACT: memory_content,
    }, findings, True


def _downgraded(finding, downgrade):
    """Return `finding`, its severity downgraded from ERROR to WARNING when `downgrade`.

    Only the severity changes -- callers still decide `ok`/gating from the
    finding's real severity before this runs, so a downgraded ERROR still
    stops the build; this only ever softens what unattended mode reports.
    An already-WARNING finding is returned unchanged either way.
    """
    if downgrade and finding.severity == ERROR:
        return Finding(
            WARNING, finding.location, finding.field, finding.message, line=finding.line
        )
    return finding


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


def write_if_changed(path, content):
    """Write `content` to `path` only when it differs. Returns `(action, finding)`.

    The write is atomic -- a temporary file in the same directory, then a
    rename -- so a failure can never leave a half-written page for a reader
    to open, and an unchanged artifact is not touched at all, which is what
    keeps the startup hook from dirtying `git status` on every session. The
    temporary file's name includes this process's pid: the startup hook runs
    `render --only-existing` on every session, and this environment routinely
    has several sessions working the same repository at once, so a fixed
    temp name would let two concurrent runs interleave writes to it before
    either reaches its rename -- the very half-written page the atomic
    rename exists to prevent.

    An existing artifact this call cannot read back -- any `OSError`, or
    content that is not valid UTF-8 -- counts as "differs": the read exists
    only to decide whether a write is needed, and a file that cannot be read
    is not known to already equal what is about to be written, so the safe
    default is to attempt the write rather than raise.

    A write that then fails (permissions, a full disk, ...) is reported as a
    `Finding` rather than raised -- the same shape `init._ensure_views` uses
    for its own write failures -- so `run` can fold it into its findings and
    decide severity instead of a traceback reaching a reader who has no
    repository to read a stack trace against. The temporary file is removed
    rather than left behind. `action` is `None` when `finding` is not.
    """
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == content:
                return "unchanged", None
        except (OSError, UnicodeDecodeError):
            pass
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        return None, Finding(
            ERROR, path.as_posix(), "write", f"file could not be written: {error}"
        )
    except BaseException:
        # Anything other than `OSError` is not a fail-open case this module
        # knows how to soften into a `Finding` -- only the temp file's
        # cleanup is this call's business either way.
        temporary.unlink(missing_ok=True)
        raise
    return "wrote", None
