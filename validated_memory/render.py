"""Build canonical pages and the optional enhanced app before publication.

Publication is per artifact, not across pages; concurrent processes are
last-writer-wins and may publish an older snapshot. The knowledge page uses
one verdict-log reading. A failed temporary cleanup is reported after the
primary write failure without replacing it. Black-box concurrency coverage
pins per-artifact canonical-target visibility during staging and the documented
last-writer-wins result; it does not establish cross-artifact atomicity or
eliminate pathname races.
"""

import errno
import os
import stat
from pathlib import Path

from . import corpus, knowledge_view, memory_view, validate
from . import memory as memory_module
from . import verdicts as verdicts_module
from .findings import ERROR, EXIT_ERROR, EXIT_OK, WARNING, Finding
from .frontmatter import FrontmatterError
from .frontmatter import parse as parse_frontmatter

KNOWLEDGE_ARTIFACT = "knowledge.html"
MEMORY_ARTIFACT = "memory.html"
APP_ARTIFACT = "knowledge-app.html"

# Build, write and stdout order are explicit, independent of dict iteration.
ARTIFACTS = (KNOWLEDGE_ARTIFACT, MEMORY_ARTIFACT)


def run(only_existing, stdout, stderr):
    """Render canonical pages and an app activated by its presence.

    Unattended mode returns 0 and downgrades findings, but never bypasses build
    gates. It restores absent canonical knowledge when an app exists. With no
    existing artifact it returns before reading inputs. Write failures become
    findings and retain any temporary-cleanup failure as secondary context.
    """
    include_app = Path(APP_ARTIFACT).exists()
    if only_existing:
        targets = [path for path in ARTIFACTS if Path(path).exists()]
        if include_app and KNOWLEDGE_ARTIFACT not in targets:
            targets.insert(0, KNOWLEDGE_ARTIFACT)
        if not targets:
            return EXIT_OK
    else:
        targets = list(ARTIFACTS)
    if include_app:
        targets.append(APP_ARTIFACT)

    artifacts, findings, ok = build_artifacts(
        downgrade=only_existing, include_app=include_app
    )
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


def build_artifacts(downgrade=False, include_app=False):
    """Return `(artifacts, findings, ok)` without writes or printing.

    Validate knowledge, read one verdict snapshot, and require readable memory
    with an index; memory content is not lint-gated. A failed prerequisite
    returns no artifacts. Build all selected pages before the caller writes.
    `include_app` enhances the exact canonical knowledge page when requested.
    `downgrade` changes reported severity only, never the gate; the caller
    prints findings once.
    """
    documents, extension, validation_findings = validate.collect_and_validate(None)
    has_error = any(finding.severity == ERROR for finding in validation_findings)
    findings = [_downgraded(finding, downgrade) for finding in validation_findings]
    if has_error:
        return {}, findings, False

    try:
        log = verdicts_module.read()
        knowledge_content = knowledge_view.build(
            corpus.build(
                documents,
                validate.basis_location(None),
                extension,
                log.records,
                log.view,
            )
        )
    except verdicts_module.VerdictLogError as error:
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
        finding = Finding(
            ERROR,
            error.location,
            "memory",
            f"memory file could not be read: {error.reason}",
        )
        findings.append(_downgraded(finding, downgrade))
        return {}, findings, False
    # Match the curated layer's trailing-slash basis convention.
    memory_basis = memory_target.as_posix() + "/"
    memory_content = memory_view.build(
        memory_documents, memory_basis, memory_resolution
    )

    artifacts = {
        KNOWLEDGE_ARTIFACT: knowledge_content,
        MEMORY_ARTIFACT: memory_content,
    }
    if include_app:
        from .knowledge_app import enhance

        artifacts[APP_ARTIFACT] = enhance(knowledge_content)
    return artifacts, findings, True


def _downgraded(finding, downgrade):
    """Downgrade ERROR severity only; callers retain the original build gate."""
    if downgrade and finding.severity == ERROR:
        return Finding(
            WARNING, finding.location, finding.field, finding.message, line=finding.line
        )
    return finding


def _memory_precondition(target):
    """Require the memory target and index to exist; do not enforce lint rules."""
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
    """Return all documents and resolution excluding unparseable declarations."""
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
    """Return `(action, finding)`; leave equal UTF-8 content untouched.

    Unreadable existing content counts as different. Publish via a same-directory
    exclusively created PID-named temporary and replace, atomically per
    artifact, not across pages. Publication and cleanup act only on the regular
    inode created through the retained descriptor. The identity check does not
    make the later pathname replace a portable conditional rename; concurrent
    processes remain last-writer-wins. An OSError returns one finding whose
    primary write failure precedes any secondary cleanup context. Other
    exceptions remain fatal after cleanup is attempted; a cleanup OSError is
    attached without replacing the primary exception.
    """
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == content:
                return "unchanged", None
        except (OSError, UnicodeDecodeError):
            pass
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    descriptor = None
    identity = None
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o666,
        )
        identity = os.fstat(descriptor)
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = None
        with stream:
            stream.write(content)
        if not _is_owned_regular(temporary, identity):
            raise OSError(
                errno.EBUSY,
                "temporary name no longer identifies the file this run created",
                os.fspath(temporary),
            )
        os.replace(temporary, path)
    except OSError as error:
        cleanup_context = _cleanup_temporary(temporary, identity)
        message = f"file could not be written: {error}"
        if cleanup_context is not None:
            message += f"; {cleanup_context}"
        return None, Finding(
            ERROR, path.as_posix(), "write", message
        )
    except BaseException as error:
        cleanup_context = _cleanup_temporary(temporary, identity)
        if cleanup_context is not None:
            error.add_note(cleanup_context)
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return "wrote", None


def _is_owned_regular(path, identity):
    """Whether ``path`` still names the retained descriptor's regular inode."""
    if identity is None:
        return False
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        return False
    return (
        stat.S_ISREG(identity.st_mode)
        and stat.S_ISREG(current.st_mode)
        and (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino)
    )


def _cleanup_temporary(temporary, identity):
    """Remove one owned inode once; describe foreign names or cleanup failure."""
    try:
        current = os.lstat(temporary)
    except FileNotFoundError:
        return None
    except OSError as error:
        return (
            f"temporary '{temporary.as_posix()}' could not be removed: {error}"
        )
    if identity is None or not (
        stat.S_ISREG(identity.st_mode)
        and stat.S_ISREG(current.st_mode)
        and (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino)
    ):
        return (
            f"temporary '{temporary.as_posix()}' was not removed because it "
            "was not owned by this run"
        )
    try:
        temporary.unlink()
    except OSError as error:
        return (
            f"temporary '{temporary.as_posix()}' could not be removed: {error}"
        )
    return None
