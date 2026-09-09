"""Safe, read-only, descriptor-relative acquisition of the recall corpus.

POSIX only: each directory component is opened relative to its already-open
parent descriptor with `O_DIRECTORY | O_NOFOLLOW`; every file is opened with
`O_NOFOLLOW | O_NONBLOCK` and its type checked with `fstat` before it is
read. No symlink is ever followed, inside a selected tree or at its root, for
data files or for configuration, schema and log files alike. Enumeration and
every read are re-verified once acquisition finishes; a change observed in
between is reported as a failure, never as a silently partial result. This
module creates no files and holds no state across calls.
"""

import errno
import os
import stat

from . import extension as extension_module

MAX_DOCUMENTS = 20_000
MAX_MARKDOWN_BYTES = 1024 * 1024
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_DEPTH = 64
MAX_ENTRIES = 50_000

MEMORY_DIR = "memory"
KNOWLEDGE_DIR = "knowledge"
INDEX_FILENAME = "MEMORY.md"
CONFIG_FILENAME = "validated-memory.md"
LOG_FILENAME = "verdicts.jsonl"


class AcquisitionError(Exception):
    """A corpus that cannot be safely and completely acquired.

    `message` is a short, sanitized summary fit for the failure envelope and
    stderr alike: it never repeats attacker-controlled path content beyond a
    project-relative name the caller already selected.

    `observed` carries the counts acquisition had reached when it gave up, so
    a failure envelope can report what was seen instead of nulling everything.
    """

    def __init__(self, message, observed=None):
        super().__init__(message)
        self.message = message
        self.observed = dict(observed) if observed else {}


class MissingFile(AcquisitionError):
    """An expected file or directory component is absent.

    Callers that tolerate absence catch this; every other caller still sees an
    `AcquisitionError`, so no `FileNotFoundError` escapes acquisition.
    """


class Acquired:
    """Everything recall reads, as bytes plus the stamps needed to re-verify.

    `memory_documents` and `knowledge_documents` are `(relpath, text)` pairs,
    sorted by codepoint. `enumerated` counts Markdown documents found (the
    memory index excluded); `auxiliary_read` counts config/schema/log files
    successfully read. `unreadable` counts Markdown documents that could not
    be decoded as UTF-8.
    """

    __slots__ = (
        "memory_documents",
        "memory_index_text",
        "knowledge_documents",
        "config_text",
        "config_location",
        "schema_text",
        "schema_location",
        "log_text",
        "enumerated",
        "unreadable",
        "auxiliary_read",
        "total_bytes",
    )

    def __init__(self):
        self.memory_documents = []
        self.memory_index_text = None
        self.knowledge_documents = []
        self.config_text = None
        self.config_location = None
        self.schema_text = None
        self.schema_location = None
        self.log_text = None
        self.enumerated = 0
        self.unreadable = 0
        self.auxiliary_read = 0
        self.total_bytes = 0


class _Budget:
    __slots__ = ("entries", "documents", "total_bytes")

    def __init__(self):
        self.entries = 0
        self.documents = 0
        self.total_bytes = 0

    def add_bytes(self, count):
        self.total_bytes += count
        if self.total_bytes > MAX_TOTAL_BYTES:
            raise AcquisitionError("total input bytes exceed the 64 MiB ceiling")

    def add_entries(self, count):
        self.entries += count
        if self.entries > MAX_ENTRIES:
            raise AcquisitionError("directory entries exceed the 50,000 ceiling")

    def add_document(self):
        self.documents += 1
        if self.documents > MAX_DOCUMENTS:
            raise AcquisitionError("Markdown documents exceed the 20,000 ceiling")


def _require_posix():
    """Require the descriptor-relative capabilities, not merely the constants.

    A platform can define `O_NOFOLLOW` and still reject `dir_fd`, which would
    surface as `NotImplementedError` mid-acquisition instead of the explicit
    platform limitation the specification requires.
    """
    missing = []
    if not (hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW")):
        missing.append("O_DIRECTORY and O_NOFOLLOW")
    if os.open not in os.supports_dir_fd:
        missing.append("directory-relative open")
    if os.stat not in os.supports_dir_fd:
        missing.append("directory-relative stat")
    if os.scandir not in os.supports_fd:
        missing.append("descriptor-based scandir")
    if missing:
        raise AcquisitionError(
            "platform limitation: recall requires "
            + ", ".join(missing)
            + "; recall targets POSIX"
        )


def _open_root():
    _require_posix()
    try:
        return os.open(".", os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise AcquisitionError(
            f"cannot open the project root: {error.strerror or error}"
        ) from error


def _open_dir(parent_fd, name, missing_message, relpath=None):
    where = relpath or name
    try:
        return os.open(
            name, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd
        )
    except FileNotFoundError as error:
        raise MissingFile(missing_message) from error
    except OSError as error:
        # A symlink where a directory is expected fails as ELOOP or, because
        # O_DIRECTORY is checked against the link itself, as ENOTDIR. Say what
        # was actually refused instead of reporting a type mismatch.
        if error.errno in (errno.ELOOP, errno.ENOTDIR) and _is_symlink(parent_fd, name):
            raise AcquisitionError(f"refusing to follow symlink '{where}'") from error
        raise AcquisitionError(
            f"cannot open directory '{where}': {error.strerror or error}"
        ) from error


def _is_symlink(parent_fd, name):
    try:
        return stat.S_ISLNK(_stat_entry(parent_fd, name).st_mode)
    except OSError:
        return False


def _stat_entry(dir_fd, name):
    return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)


def _stamp(st):
    return (st.st_ino, st.st_dev, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _open_regular(dir_fd, name, relpath=None):
    """Open and fstat `name` under `dir_fd`. Refuses symlinks and specials."""
    where = relpath or name
    try:
        fd = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd
        )
    except FileNotFoundError as error:
        raise MissingFile(f"'{where}' not found") from error
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise AcquisitionError(f"refusing to follow symlink '{where}'") from error
        raise AcquisitionError(
            f"cannot open '{where}': {error.strerror or error}"
        ) from error
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise AcquisitionError(f"'{where}' is not a regular file")
        return fd, st
    except Exception:
        os.close(fd)
        raise


def _read_capped(fd, limit, ceiling, where):
    """Read at most `limit + 1` bytes, and fstat before and after the read.

    One byte past the ceiling is enough to prove a file is oversize; the
    before/after stamps prove the returned bytes belong to a single file that
    nobody replaced or extended while it was being read.
    """
    try:
        before = _stamp(os.fstat(fd))
        chunks = []
        total = 0
        while total <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > limit:
            raise AcquisitionError(f"'{where}' exceeds the {ceiling}")
        if _stamp(os.fstat(fd)) != before:
            raise AcquisitionError(f"'{where}' changed while it was being read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _descend(dir_fd, parts, relpath):
    """Open every intermediate component of `parts` under `dir_fd`.

    Returns `(deepest_fd, opened)`; the caller closes each fd in `opened`.
    Walking component by component is what makes `O_NOFOLLOW` meaningful: it
    guards only the last component of whatever path it is handed.
    """
    opened = []
    current = dir_fd
    try:
        for part in parts[:-1]:
            current = _open_dir(current, part, f"'{relpath}' not found", relpath=part)
            opened.append(current)
    except Exception:
        for handle in opened:
            os.close(handle)
        raise
    return current, opened


def _read_named(dir_fd, relpath, limit, ceiling, display=None):
    """Read one regular file at `relpath` under `dir_fd`. Returns `(bytes, stamp)`.

    Every component is walked with `O_DIRECTORY | O_NOFOLLOW`, so a symlinked
    ancestor is refused rather than followed out of the tree. `display` names
    the file in diagnostics when `relpath` alone would not locate it.
    """
    where = display or relpath
    parts = relpath.split("/")
    base_fd, opened = _descend(dir_fd, parts, where)
    try:
        fd, st = _open_regular(base_fd, parts[-1], where)
        stamp = _stamp(st)
        raw = _read_capped(fd, limit, ceiling, where)
    finally:
        for handle in opened:
            os.close(handle)
    return raw, stamp


def _walk_inventory(dir_fd, prefix, depth, budget, skip_name=None):
    """Enumerate one selected tree as sorted `(kind, relpath, stamp)` triples.

    `kind` is `d` for a directory, `f` for a Markdown document and `o` for any
    other ordinary file. Directories appear even when empty and ignored files
    appear too, so the inventory is the tree's full identity: re-verification
    then sees an addition or deletion anywhere under it. A directory also
    carries the stamp of the descriptor actually descended into, so a
    directory replaced by another with identical contents -- an empty one
    swapped for an empty one -- is a change, not a match. Files carry no
    stamp here: each acquired document is re-stat'ed on its own. The entry
    ceiling is charged while iterating, never after materializing the listing.
    """
    if depth > MAX_DEPTH:
        raise AcquisitionError("directory depth exceeds the 64 ceiling")
    entries = []
    subdirectories = []
    with os.scandir(dir_fd) as iterator:
        for entry in iterator:
            budget.add_entries(1)
            relpath = prefix + entry.name
            if entry.is_symlink():
                raise AcquisitionError(f"refusing to follow symlink '{relpath}'")
            if entry.is_dir(follow_symlinks=False):
                subdirectories.append((entry.name, relpath))
                continue
            if entry.is_file(follow_symlinks=False):
                if prefix == "" and entry.name == skip_name:
                    continue
                entries.append(
                    ("f" if entry.name.endswith(".md") else "o", relpath, None)
                )
                continue
            raise AcquisitionError(f"refusing special file '{relpath}'")
    for name, relpath in subdirectories:
        sub_fd = _open_dir(
            dir_fd, name, f"'{relpath}' vanished during acquisition", relpath=relpath
        )
        try:
            entries.append(("d", relpath, _stamp(os.fstat(sub_fd))))
            entries.extend(_walk_inventory(sub_fd, relpath + "/", depth + 1, budget))
        finally:
            os.close(sub_fd)
    return entries


def _read_tree(root_fd, dirname, budget, observed, skip_name=None):
    """Return `(inventory, documents, root_stamp)` for one selected tree.

    `documents` are `(relpath, bytes, stamp)` for every `*.md` file, sorted by
    codepoint; `inventory` is the directory identity used to re-verify, and
    `root_stamp` is the stamp of the selected root's own descriptor, so a
    replacement of the selected directory itself is detected too.

    Progress is published into `observed` while the tree is being acquired,
    not once it returns: enumeration is complete the moment the inventory is,
    and a document counts as read as soon as its bytes are in hand. A ceiling
    tripped part way through therefore reports what was actually seen, and
    counts already established by an earlier tree survive untouched.
    """
    dir_fd = _open_dir(
        root_fd, dirname, f"no '{dirname}' directory found", relpath=dirname
    )
    try:
        root_stamp = _stamp(os.fstat(dir_fd))
        inventory = sorted(_walk_inventory(dir_fd, "", 0, budget, skip_name))
        pending = [entry for entry in inventory if entry[0] == "f"]
        observed["enumerated"] = (observed["enumerated"] or 0) + len(pending)
        documents = []
        for _kind, relpath, _dir_stamp in pending:
            budget.add_document()
            raw, stamp = _read_named(
                dir_fd, relpath, MAX_MARKDOWN_BYTES, "1 MiB per-Markdown ceiling",
                display=f"{dirname}/{relpath}",
            )
            observed["read"] = (observed["read"] or 0) + 1
            budget.add_bytes(len(raw))
            documents.append((relpath, raw, stamp))
        return inventory, documents, root_stamp
    finally:
        os.close(dir_fd)


def _decode_documents(raw_documents):
    """Split `(relpath, bytes, stamp)` into texts, an unreadable count and the
    first undecodable relative path."""
    texts = []
    unreadable = 0
    first_unreadable = None
    for relpath, raw, _stamp in raw_documents:
        try:
            texts.append((relpath, raw.decode("utf-8")))
        except UnicodeDecodeError:
            unreadable += 1
            if first_unreadable is None:
                first_unreadable = relpath
    return texts, unreadable, first_unreadable


def acquire(layers, need_config, need_log):
    """Acquire the selected corpus. Raises `AcquisitionError` on any failure.

    `layers` is a subset of `{"memory", "knowledge"}`. Returns an `Acquired`
    snapshot; re-enumeration and re-stat happen before this function returns,
    so a caller sees either a consistent snapshot or an explicit failure. A
    failure carries the counts observed so far on its `observed` attribute.
    """
    root_fd = _open_root()
    # Every count starts unknown: null is reserved for a total that was never
    # determined, so an observed zero stays distinguishable from ignorance.
    observed = {
        "enumerated": None, "read": None, "unreadable": None, "auxiliary_read": None,
    }
    try:
        return _acquire(root_fd, layers, need_config, need_log, observed)
    except AcquisitionError as error:
        if not error.observed:
            error.observed = dict(observed)
        raise
    except (OSError, NotImplementedError) as error:
        # No standard-library I/O fault reaches the user as a traceback: the
        # acquisition boundary converts it into the same bounded envelope as
        # every other failure, keeping the counts observed so far.
        raise AcquisitionError(
            f"cannot acquire the selected inputs: {_sanitized(error)}",
            observed=dict(observed),
        ) from error
    finally:
        os.close(root_fd)


def _sanitized(error):
    """A short, path-free description of a standard-library I/O fault."""
    if isinstance(error, OSError):
        return error.strerror or errno.errorcode.get(error.errno, "I/O error")
    return "unsupported platform operation"


def _acquire(root_fd, layers, need_config, need_log, observed):
    budget = _Budget()
    result = Acquired()
    recheck = []  # ("tree"|"file"|"absent", dirname, ...)

    if "memory" in layers:
        memory_fd = _open_dir(
            root_fd, MEMORY_DIR, "no 'memory' directory found", relpath=MEMORY_DIR
        )
        try:
            index_raw, index_stamp = _read_named(
                memory_fd, INDEX_FILENAME, MAX_MARKDOWN_BYTES, "1 MiB index ceiling",
                display=f"{MEMORY_DIR}/{INDEX_FILENAME}",
            )
        except MissingFile as error:
            raise AcquisitionError(
                f"'{MEMORY_DIR}/{INDEX_FILENAME}' not found; memory requires its index"
            ) from error
        finally:
            os.close(memory_fd)
        # The index is an auxiliary file, counted the moment its bytes are in
        # hand: a later failure still reports what was actually read.
        result.auxiliary_read += 1
        observed["auxiliary_read"] = result.auxiliary_read
        budget.add_bytes(len(index_raw))
        try:
            result.memory_index_text = index_raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise AcquisitionError(
                f"'{MEMORY_DIR}/{INDEX_FILENAME}' is not valid UTF-8",
                observed=dict(observed),
            ) from error
        inventory, raw_documents, root_stamp = _read_tree(
            root_fd, MEMORY_DIR, budget, observed, skip_name=INDEX_FILENAME
        )
        result.memory_documents = _decoded(MEMORY_DIR, raw_documents, result, observed)
        recheck.append(("tree", MEMORY_DIR, inventory, root_stamp))
        recheck.append(("file", MEMORY_DIR, INDEX_FILENAME, index_stamp))
        for relpath, _raw, stamp in raw_documents:
            recheck.append(("file", MEMORY_DIR, relpath, stamp))

    if "knowledge" in layers:
        inventory, raw_documents, root_stamp = _read_tree(
            root_fd, KNOWLEDGE_DIR, budget, observed
        )
        result.knowledge_documents = _decoded(
            KNOWLEDGE_DIR, raw_documents, result, observed
        )
        recheck.append(("tree", KNOWLEDGE_DIR, inventory, root_stamp))
        for relpath, _raw, stamp in raw_documents:
            recheck.append(("file", KNOWLEDGE_DIR, relpath, stamp))

    if need_config:
        config_raw, config_stamp = _read_optional(
            root_fd, CONFIG_FILENAME, MAX_MARKDOWN_BYTES, "1 MiB configuration ceiling"
        )
        if config_raw is None:
            recheck.append(("absent", ".", CONFIG_FILENAME))
        else:
            budget.add_bytes(len(config_raw))
            result.config_text = _decode_auxiliary(CONFIG_FILENAME, config_raw)
            result.config_location = CONFIG_FILENAME
            result.auxiliary_read += 1
            observed["auxiliary_read"] = result.auxiliary_read
            recheck.append(("file", ".", CONFIG_FILENAME, config_stamp))
            schema_relpath = _declared_schema_path(
                result.config_location, result.config_text
            )
            if schema_relpath:
                schema_raw, schema_stamp = _read_optional(
                    root_fd, schema_relpath, MAX_MARKDOWN_BYTES, "1 MiB schema ceiling"
                )
                if schema_raw is None:
                    # The declared schema is absent: configuration validation
                    # reports that with its own location and field, and the
                    # absence is re-checked like every other input.
                    recheck.append(("absent", ".", schema_relpath))
                else:
                    budget.add_bytes(len(schema_raw))
                    result.schema_text = _decode_auxiliary(schema_relpath, schema_raw)
                    result.schema_location = schema_relpath
                    result.auxiliary_read += 1
                    observed["auxiliary_read"] = result.auxiliary_read
                    recheck.append(("file", ".", schema_relpath, schema_stamp))

    if need_log and "knowledge" in layers:
        log_raw, log_stamp = _read_optional(
            root_fd, LOG_FILENAME, MAX_LOG_BYTES, "16 MiB verdict-log ceiling"
        )
        if log_raw is None:
            recheck.append(("absent", ".", LOG_FILENAME))
        else:
            budget.add_bytes(len(log_raw))
            result.log_text = _decode_auxiliary(LOG_FILENAME, log_raw)
            result.auxiliary_read += 1
            observed["auxiliary_read"] = result.auxiliary_read
            recheck.append(("file", ".", LOG_FILENAME, log_stamp))

    result.total_bytes = budget.total_bytes
    _reverify(root_fd, recheck)
    return result


def _read_optional(root_fd, relpath, limit, ceiling):
    """Read a file that may legitimately be absent. Returns `(bytes, stamp)`."""
    try:
        return _read_named(root_fd, relpath, limit, ceiling)
    except MissingFile:
        return None, None


def _decode_auxiliary(relpath, raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AcquisitionError(f"'{relpath}' is not valid UTF-8") from error


def _decoded(dirname, raw_documents, result, observed):
    """Decode one tree's documents, failing on the first undecodable one.

    An undecodable document means the corpus cannot be validated as a whole,
    so recall fails with the counts it observed rather than returning results
    computed from a corpus it could not read.

    Enumeration and reads were already published while the tree was being
    acquired; decoding only moves documents out of `read` and into
    `unreadable`. `unreadable` stays null until some tree has been decoded,
    because until then nothing about decodability was determined.
    """
    texts, unreadable, first_unreadable = _decode_documents(raw_documents)
    result.enumerated += len(raw_documents)
    result.unreadable += unreadable
    observed["enumerated"] = result.enumerated
    observed["unreadable"] = result.unreadable
    observed["read"] = result.enumerated - result.unreadable
    if unreadable:
        raise AcquisitionError(
            f"'{dirname}/{first_unreadable}' is not valid UTF-8; refusing an "
            "incomplete corpus",
            observed=observed,
        )
    return texts


def _declared_schema_path(location, config_text):
    """The schema path the acquired configuration declares, or None.

    The configuration is parsed and validated by the extension module's own
    parser and declaration validator, so acquisition and `validate` never
    disagree about which file is declared. A configuration that module
    rejects yields no second read here: the identical error is raised again,
    with its location and field, when validation runs over the same bytes.
    """
    try:
        declared = extension_module.declared_schema(location, config_text)
    except extension_module.ExtensionError:
        return None
    if declared is None:
        return None
    parts = declared.split("/")
    if declared.startswith("/") or "" in parts or ".." in parts:
        raise AcquisitionError(
            "declared schema path must be a relative path without '..' segments"
        )
    return declared


def _reverify(root_fd, recheck):
    """Re-enumerate and re-stat everything acquired; fail on any difference."""
    for kind, dirname, *rest in recheck:
        if kind == "tree":
            expected, expected_root = rest[0], rest[1]
            dir_fd = _open_dir(
                root_fd,
                dirname,
                f"'{dirname}' vanished during acquisition",
                relpath=dirname,
            )
            try:
                actual_root = _stamp(os.fstat(dir_fd))
                budget = _Budget()
                actual = sorted(_walk_inventory(dir_fd, "", 0, budget, skip_name=(
                    INDEX_FILENAME if dirname == MEMORY_DIR else None
                )))
            finally:
                os.close(dir_fd)
            if actual != expected or actual_root != expected_root:
                raise AcquisitionError(
                    f"'{dirname}' changed during acquisition; refusing a partial result"
                )
            continue

        name = rest[0]
        where = name if dirname == "." else f"{dirname}/{name}"
        parts = name.split("/") if dirname == "." else [dirname] + name.split("/")
        if kind == "absent":
            if _stat_relative(root_fd, parts) is not None:
                raise AcquisitionError(
                    f"'{where}' appeared during acquisition; refusing a partial result"
                )
            continue

        st = _stat_relative(root_fd, parts)
        if st is None:
            raise AcquisitionError(f"'{where}' vanished during acquisition")
        if _stamp(st) != rest[1]:
            raise AcquisitionError(
                f"'{where}' changed during acquisition; refusing a partial result"
            )


def _stat_relative(root_fd, parts):
    """Stat a relative path component-wise without following any symlink.

    Returns None when the path, or any directory on the way to it, is absent:
    that is what proves an expected absence is still an absence.
    """
    relpath = "/".join(parts)
    try:
        dir_fd, opened = _descend(root_fd, parts, relpath)
    except MissingFile:
        return None
    try:
        return _stat_entry(dir_fd, parts[-1])
    except FileNotFoundError:
        return None
    except OSError as error:
        raise AcquisitionError(
            f"cannot re-check '{relpath}': {error.strerror or error}"
        ) from error
    finally:
        for handle in opened:
            os.close(handle)
