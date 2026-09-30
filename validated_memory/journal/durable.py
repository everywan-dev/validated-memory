"""Private persistence primitives for journal-owned namespace effects.

Visibility and durability are different outcomes. An ``OSError`` means the
requested effect did not become visible. ``VisibilityUnconfirmed`` means the
effect completed, but the directory barrier that would confirm its survival
did not. Keeping those exception families disjoint prevents callers which can
safely clean up a pre-publication failure from claiming rollback after a
rename or mkdir has already happened.
"""

import errno
import os
import stat
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable



class Persistence(Enum):
    """The truthful result of a completed persistence effect."""

    CONFIRMED = "confirmed"
    UNSUPPORTED = "unsupported"


class VisibilityUnconfirmed(Exception):
    """A namespace effect is visible but its durability is unconfirmed."""

    def __init__(self, path, operation, complete, error):
        self.path = Path(path)
        self.operation = operation
        self.complete = complete
        self.error = error
        state = "is visible" if complete else "may be visible"
        super().__init__(
            f"{operation} of {self.path.as_posix()} {state}, but its "
            f"durability is unconfirmed: {error}"
        )


class ReplaceDeclined(Exception):
    """A link replacement was stopped just before its rename; nothing was published.

    `answer` is what the `before_replace` callable of `replace_symlink`
    returned, and the caller ends its repair with it.
    """

    def __init__(self, answer):
        self.answer = answer
        super().__init__("the replacement was declined before its rename")


class StagedLinkChanged(Exception):
    """The link staged for a replacement is no longer the link that was staged.

    `path` is the staged name. It is left as it is: it was neither renamed over
    the target nor unlinked.
    """

    def __init__(self, path):
        self.path = Path(path)
        super().__init__(
            f"{self.path.as_posix()} is no longer the link that was staged"
        )


class NoReplaceUnavailable(Exception):
    """The platform cannot publish a staged file without replacement."""


class BootstrapPreparationFailed(Exception):
    """Private bootstrap preparation failed before canonical publication."""

    def __init__(self, phase, error):
        self.phase = phase
        self.error = error
        super().__init__(str(error))


class StagingCleanupUnconfirmed(Exception):
    """Private bootstrap staging may remain after a failed cleanup."""

    def __init__(self, staging, published, error):
        self.staging = Path(staging)
        self.published = published
        self.error = error
        super().__init__(str(error))


class _Operation(Enum):
    """The closed set of namespace effects this persistence seam owns."""

    ENSURE_DIRECTORY = "ensure-directory"
    INSTALL = "install"
    CREATE_EXCLUSIVE = "create-exclusive"
    CREATE_DIRECTORY = "create-directory"
    REPLACE_SYMLINK = "replace-symlink"
    APPEND = "append"
    REMOVE = "remove"
    CONFIRM = "confirm"


@dataclass(frozen=True)
class _Effect:
    """One closed persistence operation, kept private to this module."""

    operation: _Operation
    path: Path
    directory: Path
    apply: Callable[[], None]
    complete: bool = True


def storage_crash(point, path):
    """Private storage-crash seam, inert unless explicitly selected."""
    requested = {
        item.strip()
        for item in os.environ.get("VALIDATED_MEMORY_STORAGE_CRASH", "").split(",")
        if item.strip()
    }
    path = Path(path)
    if f"{point}:*" in requested or f"{point}:{path.name}" in requested:
        os._exit(71)


def storage_prefix(path, data, handle):
    """Write and fsync a selected history prefix before the storage crash."""
    requested = os.environ.get("VALIDATED_MEMORY_STORAGE_CRASH", "")
    if not requested.startswith("history-prefix:") or Path(path).name not in {"journal.jsonl", "local.jsonl"}:
        return
    try:
        prefix = int(requested.split(":", 1)[1])
    except ValueError:
        return
    if 0 <= prefix < len(data):
        handle.write(data[:prefix])
        handle.flush()
        os.fsync(handle.fileno())
        os._exit(71)


def _temporary_path(directory, prefix, suffix=".tmp"):
    """Reserve an unpredictable, exclusive regular-file staging name."""
    descriptor, name = tempfile.mkstemp(
        prefix=f".{prefix}.", suffix=suffix, dir=directory
    )
    return Path(name), descriptor


def _same_open_entry(path, identity):
    """Whether a path still names the descriptor-owned staging inode."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_dev == identity.st_dev
        and info.st_ino == identity.st_ino
    )


def _chmod_staging(descriptor, path, mode):
    """Set staging metadata only through its still-owned descriptor."""
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, mode)
        return
    # A pathname-based fallback is unsafe even with follow_symlinks=False:
    # another actor can replace it with a foreign regular file between the
    # replacement and chmod. Refuse the operation rather than mutating an
    # entry the descriptor does not own.
    raise OSError(
        errno.ENOTSUP,
        "cannot safely set staging mode without descriptor metadata support",
        os.fspath(path),
    )


_UNSUPPORTED_DIRECTORY_ERRORS = {
    value
    for value in (
        errno.EINVAL,
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
    )
    if value is not None
}

_UNSUPPORTED_NO_REPLACE_ERRORS = {
    value
    for value in (
        errno.EINVAL,
        errno.ENOSYS,
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
    )
    if value is not None
}


def _injected_error(point, path):
    """Raise a deterministic test-only I/O error at one persistence point."""
    requested = {
        item.strip()
        for item in os.environ.get(
            "VALIDATED_MEMORY_PERSISTENCE_FAULT", ""
        ).split(",")
        if item.strip()
    }
    path = Path(path)
    if requested.intersection((
        f"{point}:*",
        f"{point}:{path.name}",
        f"{point}:{path.as_posix()}",
    )):
        raise OSError(errno.EIO, "injected persistence failure", os.fspath(path))


def _swap_staging_for_test(path):
    """Replace one reserved staging name to exercise descriptor ownership."""
    requested = {
        item.strip()
        for item in os.environ.get(
            "VALIDATED_MEMORY_PERSISTENCE_FAULT", ""
        ).split(",")
        if item.strip()
    }
    path = Path(path)
    for item in requested:
        prefix = "swap-staging:"
        if not item.startswith(prefix):
            continue
        target_name = item.removeprefix(prefix)
        if not target_name or not path.name.startswith(f".{target_name}."):
            continue
        foreign = path.with_name(path.name + ".foreign")
        foreign.write_bytes(b"foreign staging entry\n")
        os.chmod(foreign, 0o640)
        path.unlink()
        os.symlink(foreign.name, path)
        return


def _corrupt_staging_for_test(path, descriptor, data):
    """Corrupt verified staging bytes in place when the private seam selects it."""
    requested = {
        item.strip()
        for item in os.environ.get(
            "VALIDATED_MEMORY_PERSISTENCE_FAULT", ""
        ).split(",")
        if item.strip()
    }
    path = Path(path)
    if f"corrupt-staging:{path.name}" not in requested:
        return
    replacement = (
        bytes((data[0] ^ 0xFF,)) + data[1:]
        if data
        else b"\x00"
    )
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.ftruncate(descriptor, 0)
    os.write(descriptor, replacement)


def _bootstrap_competitor_for_test(path):
    """Create a bounded canonical competitor immediately before publication."""
    requested = {
        item.strip()
        for item in os.environ.get(
            "VALIDATED_MEMORY_PERSISTENCE_FAULT", ""
        ).split(",")
        if item.strip()
    }
    path = Path(path)
    if f"bootstrap-competitor:{path.name}" not in requested:
        return
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(b"competitor\n")
        handle.flush()
        os.fsync(handle.fileno())


def _cleanup_bootstrap_staging(path):
    """Remove only the private bootstrap name and confirm its directory."""
    path = Path(path)
    _injected_error("bootstrap-cleanup", path)
    path.unlink()
    try:
        _confirm_directory(path.parent, "bootstrap-cleanup")
    except OSError as error:
        raise VisibilityUnconfirmed(
            path, "bootstrap-cleanup", True, error
        ) from error


def _short_opening_write_for_test(path, written):
    """Fail after the first descriptor write for bounded retained testing."""
    requested = {
        item.strip()
        for item in os.environ.get(
            "VALIDATED_MEMORY_PERSISTENCE_FAULT", ""
        ).split(",")
        if item.strip()
    }
    path = Path(path)
    if written and f"opening-short-write:{path.name}" in requested:
        raise OSError(errno.EIO, "injected failure after a short opening write")


def _short_append_reconfirmation_for_test(path, view):
    """Select a strict first range-write prefix for retained fault coverage."""
    requested = {
        item.strip()
        for item in os.environ.get(
            "VALIDATED_MEMORY_PERSISTENCE_FAULT", ""
        ).split(",")
        if item.strip()
    }
    path = Path(path)
    selected = f"append-reconfirmation-short-write:{path.name}" in requested
    if not selected or len(view) <= 1:
        return view, False
    return view[: max(1, len(view) // 2)], True


def _confirm_directory(path, operation):
    """Confirm directory entries, distinguishing unsupported from failure."""
    path = Path(path)
    if os.name == "nt":
        return Persistence.UNSUPPORTED
    requested = {
        item.strip()
        for item in os.environ.get(
            "VALIDATED_MEMORY_PERSISTENCE_FAULT", ""
        ).split(",")
        if item.strip()
    }
    if requested.intersection((
        f"unsupported:{path.name}",
        f"unsupported:{path.as_posix()}",
    )):
        return Persistence.UNSUPPORTED
    try:
        _injected_error("open", path)
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
    except OSError as error:
        if error.errno in _UNSUPPORTED_DIRECTORY_ERRORS:
            return Persistence.UNSUPPORTED
        raise
    try:
        _injected_error(operation, path)
        _injected_error("fsync", path)
        os.fsync(descriptor)
    except OSError as error:
        if error.errno in _UNSUPPORTED_DIRECTORY_ERRORS:
            return Persistence.UNSUPPORTED
        raise
    finally:
        os.close(descriptor)
    return Persistence.CONFIRMED


def persist(effect):
    """Apply one private namespace effect and confirm its carrying directory."""
    effect.apply()
    try:
        _injected_error(effect.operation.value, effect.path)
        return _confirm_directory(effect.directory, effect.operation.value)
    except OSError as error:
        raise VisibilityUnconfirmed(
            effect.path, effect.operation.value, effect.complete, error
        ) from error


def confirm_directory(path):
    """Confirm an existing directory without changing its namespace."""
    path = Path(path)
    try:
        return _confirm_directory(path, "confirm")
    except OSError as error:
        raise VisibilityUnconfirmed(path, "confirmation", True, error) from error


def ensure_owned_directory(path, anchor, creation_point="mkdir"):
    """Create and confirm a journal-owned directory chain below ``anchor``."""
    path = Path(path)
    anchor = Path(anchor)
    try:
        relative = path.relative_to(anchor)
    except ValueError as error:
        raise ValueError(f"{path} is not below persistence anchor {anchor}") from error

    current = anchor
    created = []
    for part in relative.parts:
        current = current / part
        try:
            _injected_error(creation_point, current)
            os.mkdir(current)
        except FileExistsError:
            if not current.is_dir():
                raise
        except OSError as error:
            if created:
                raise VisibilityUnconfirmed(
                    created[-1], "ensure-directory-chain", False, error
                ) from error
            raise
        else:
            created.append(current)

    result = Persistence.CONFIRMED
    confirmations = []
    current = path
    while True:
        confirmations.append(current)
        if current == anchor:
            break
        current = current.parent
    seen = set()
    for directory in confirmations:
        if directory in seen:
            continue
        seen.add(directory)
        outcome = confirm_directory(directory)
        if outcome is Persistence.UNSUPPORTED:
            result = outcome
    return result


def ensure_external_directory(path, reference, creation_point="mkdir"):
    """Create an external chain below one stable, lexically derived anchor."""
    path = Path(os.path.abspath(path))
    reference = Path(os.path.abspath(reference))
    try:
        anchor = Path(os.path.commonpath((path, reference)))
    except ValueError:
        # Different Windows drives have no common lexical ancestor. The
        # target drive root is still stable and this operation creates only
        # names in the caller-supplied target chain below it.
        anchor = Path(path.anchor)
    if not anchor.is_dir():
        raise NotADirectoryError(os.fspath(anchor))
    return ensure_owned_directory(path, anchor, creation_point)


def repair_symlink(path, target, reference, before_replace=None):
    """Durably restore the fail-open harness link through this private seam.

    `before_replace` is passed to `replace_symlink`. When it declines, or the
    staged link changed, the parent chain has been made and nothing else is
    visible: the ancestry error below is not raised, because the run has no
    restored link to gate.
    """
    path = Path(path)
    ancestry_error = None
    try:
        ensure_external_directory(
            path.parent, reference, creation_point="repair-mkdir"
        )
    except VisibilityUnconfirmed as error:
        # The fail-open contract still restores the link when all required
        # parent names are visible. Preserve the ancestry failure so the run
        # gates even if that restoration and its immediate barrier succeed.
        ancestry_error = error
    outcome = replace_symlink(path, target, before_replace=before_replace)
    if ancestry_error is not None:
        raise ancestry_error
    return outcome


def install(temporary, target):
    """Atomically install complete bytes and confirm the target name."""
    temporary = Path(temporary)
    target = Path(target)

    def apply():
        _injected_error("atomic-install", target)
        os.replace(temporary, target)

    return persist(
        _Effect(
            _Operation.INSTALL,
            target,
            target.parent,
            apply,
        )
    )


def install_bytes(
    path,
    data,
    mode=0o600,
    verify=None,
    temporary=None,
    crash_storage=False,
    *,
    identify=False,
):
    """Build complete private bytes, optionally verify them, then install."""
    path = Path(path)
    provided = temporary is not None
    descriptor = None
    if provided:
        temporary = Path(temporary)
    else:
        temporary, descriptor = _temporary_path(path.parent, path.name)
    identity = None
    try:
        if provided:
            descriptor = os.open(
                temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        identity = os.fstat(descriptor)
        # Keep the descriptor open through metadata changes.  Applying mode
        # through the pathname after closing it lets a concurrent replacement
        # turn a harmless staging operation into a chmod of a foreign target.
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            _injected_error("write", path)
            handle.write(data)
            handle.flush()
            _injected_error("file-fsync", path)
            os.fsync(handle.fileno())
        _swap_staging_for_test(temporary)
        _chmod_staging(descriptor, temporary, mode)
        if verify is not None:
            _corrupt_staging_for_test(path, descriptor, data)
            verify(temporary)
        if identity is None or not _same_open_entry(temporary, identity):
            raise OSError(
                errno.EBUSY,
                "staging entry changed before atomic installation",
                os.fspath(temporary),
            )
        if crash_storage:
            storage_crash("staged-before-install", temporary)
        published_identity = os.fstat(descriptor) if identify else None
        outcome = install(temporary, path)
        return (outcome, published_identity) if identify else outcome
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        if identity is not None and _same_open_entry(temporary, identity):
            temporary.unlink()
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def publish_no_replace(path, data, mode, verify):
    """Publish complete staged bytes without replacing a canonical name.

    ``verify`` runs after the canonical directory barrier while the private
    hard-link name still exists.  It must coherently confirm the permanent
    history pair before private cleanup can turn the operation into success.
    """
    path = Path(path)
    staging = None
    descriptor = None
    published = False
    identity = None
    preparation_phase = "staging creation"
    try:
        _injected_error("bootstrap-staging-create", path)
        staging, descriptor = _temporary_path(
            path.parent, path.name, ".bootstrap"
        )
        _injected_error("bootstrap-staging-identity", path)
        identity = os.fstat(descriptor)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            preparation_phase = "staging write"
            _injected_error("bootstrap-write", path)
            handle.write(data)
            handle.flush()
            preparation_phase = "staging mode"
            _injected_error("bootstrap-mode", path)
            _chmod_staging(descriptor, staging, mode)
            preparation_phase = "staging file flush"
            _injected_error("bootstrap-file-fsync", path)
            os.fsync(handle.fileno())
        if not _same_open_entry(staging, identity):
            raise OSError(
                errno.EBUSY,
                "bootstrap staging entry changed before publication",
                os.fspath(staging),
            )
        storage_crash("bootstrap-staged", staging)
        preparation_phase = "canonical publication"
        _bootstrap_competitor_for_test(path)
        requested = os.environ.get("VALIDATED_MEMORY_PERSISTENCE_FAULT", "")
        try:
            _injected_error("bootstrap-canonical-publication", path)
            selected = {item.strip() for item in requested.split(",")}
            if f"no-replace-unavailable:{path.name}" in selected:
                raise NoReplaceUnavailable(
                    "no-replace publication is unavailable"
                )
            if f"no-replace-not-implemented:{path.name}" in selected:
                raise NotImplementedError("hard-link publication is unavailable")
            injected_errno = None
            for name, value in (
                ("no-replace-enosys", errno.ENOSYS),
                ("no-replace-eopnotsupp", getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)),
                ("no-replace-eperm", errno.EPERM),
            ):
                if f"{name}:{path.name}" in selected:
                    injected_errno = value
                    break
            if injected_errno is not None:
                raise OSError(
                    injected_errno,
                    "injected unavailable no-replace primitive",
                    os.fspath(path),
                )
            if os.name == "nt":
                os.rename(staging, path)
            elif hasattr(os, "link"):
                os.link(staging, path, follow_symlinks=False)
            else:
                raise NoReplaceUnavailable(
                    "no-replace publication is unavailable"
                )
        except NotImplementedError as error:
            raise NoReplaceUnavailable(str(error)) from error
        except OSError as error:
            hard_link_unavailable = (
                os.name != "nt" and error.errno in {errno.EPERM, errno.EXDEV}
            )
            if (
                error.errno in _UNSUPPORTED_NO_REPLACE_ERRORS
                or hard_link_unavailable
            ):
                raise NoReplaceUnavailable(str(error)) from error
            raise
        published = True
        storage_crash("bootstrap-visible", path)
        try:
            _injected_error("bootstrap-publish", path)
            _confirm_directory(path.parent, "bootstrap-publish")
            verify(
                (identity.st_dev, identity.st_ino),
                stat.S_IMODE(identity.st_mode),
                data,
            )
        except Exception as error:
            if isinstance(error, VisibilityUnconfirmed):
                raise
            raise VisibilityUnconfirmed(
                path, "bootstrap-publication", True, error
            ) from error
        if staging.exists():
            try:
                _cleanup_bootstrap_staging(staging)
            except (OSError, VisibilityUnconfirmed) as error:
                raise StagingCleanupUnconfirmed(staging, True, error) from error
    except Exception as error:
        if not published and staging is not None and identity is None:
            raise StagingCleanupUnconfirmed(staging, False, error) from error
        if (
            not published
            and staging is not None
            and identity is not None
            and _same_open_entry(staging, identity)
        ):
            try:
                _cleanup_bootstrap_staging(staging)
            except (OSError, VisibilityUnconfirmed) as cleanup_error:
                raise StagingCleanupUnconfirmed(
                    staging, False, cleanup_error
                ) from error
        if (
            not published
            and not isinstance(
                error,
                (FileExistsError, NoReplaceUnavailable, BootstrapPreparationFailed),
            )
        ):
            raise BootstrapPreparationFailed(preparation_phase, error) from error
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def reconfirm_exact_file(path, data, identity, mode):
    """Re-dirty one complete file through its non-truncating descriptor."""
    path = Path(path)
    descriptor = os.open(
        path,
        os.O_RDWR | getattr(os, "O_NONBLOCK", 0),
    )
    acted = False
    try:
        info = os.fstat(descriptor)
        current_identity = (info.st_dev, info.st_ino)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, "history is not a regular file", os.fspath(path))
        if current_identity != identity:
            raise OSError(errno.EBUSY, "history identity changed", os.fspath(path))
        if stat.S_IMODE(info.st_mode) != mode:
            raise OSError(errno.EBUSY, "history mode changed", os.fspath(path))
        if info.st_size != len(data):
            raise OSError(errno.EBUSY, "history bytes changed", os.fspath(path))
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.read(descriptor, len(data) + 1) != data:
            raise OSError(errno.EBUSY, "history bytes changed", os.fspath(path))
        os.lseek(descriptor, 0, os.SEEK_SET)
        _injected_error("opening-rewrite", path)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(errno.EIO, "history rewrite made no progress")
            acted = True
            _short_opening_write_for_test(path, written)
            view = view[written:]
        _injected_error("opening-file-fsync", path)
        os.fsync(descriptor)
        _injected_error("opening-directory", path)
        outcome = _confirm_directory(path.parent, "opening-directory")
        _injected_error("opening-after-barriers", path)
        return current_identity, outcome
    except Exception as error:
        if acted:
            raise VisibilityUnconfirmed(
                path, "opening-reconfirmation", True, error
            ) from error
        raise
    finally:
        os.close(descriptor)


def reconfirm_exact_range(path, snapshot, offset, data, identity, mode, *, rewrite):
    """Confirm one retained append range without replacing or truncating it."""
    path = Path(path)
    descriptor = os.open(
        path,
        os.O_RDWR
        | getattr(os, "O_NONBLOCK", 0),
    )
    acted = False
    try:
        info = os.fstat(descriptor)
        current_identity = (info.st_dev, info.st_ino)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, "history is not a regular file", os.fspath(path))
        if current_identity != identity:
            raise OSError(errno.EBUSY, "history identity changed", os.fspath(path))
        if stat.S_IMODE(info.st_mode) != mode:
            raise OSError(errno.EBUSY, "history mode changed", os.fspath(path))
        if info.st_size != len(snapshot):
            raise OSError(errno.EBUSY, "history bytes changed", os.fspath(path))
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.read(descriptor, len(snapshot) + 1) != snapshot:
            raise OSError(errno.EBUSY, "history bytes changed", os.fspath(path))
        if snapshot[offset : offset + len(data)] != data:
            raise OSError(errno.EBUSY, "authorized append range changed", os.fspath(path))

        if rewrite:
            os.lseek(descriptor, offset, os.SEEK_SET)
            _injected_error("append-reconfirmation-rewrite", path)
            view = memoryview(data)
            while view:
                chunk, fail_after = _short_append_reconfirmation_for_test(path, view)
                written = os.write(descriptor, chunk)
                if written <= 0:
                    raise OSError(errno.EIO, "history rewrite made no progress")
                acted = True
                if fail_after:
                    raise OSError(
                        errno.EIO,
                        "injected failure after a short append-range write",
                    )
                view = view[written:]
            _injected_error("append-reconfirmation-file-fsync", path)
            os.fsync(descriptor)

        acted = True
        _injected_error("append-reconfirmation-directory", path)
        outcome = _confirm_directory(path.parent, "append-reconfirmation-directory")
        _injected_error("append-reconfirmation-after-barriers", path)
        return current_identity, outcome
    except Exception as error:
        if acted:
            raise VisibilityUnconfirmed(
                path, "append-reconfirmation", True, error
            ) from error
        raise
    finally:
        os.close(descriptor)


def create_exclusive(path, data, mode=0o666):
    """Create and flush one complete file without replacing an existing name."""
    path = Path(path)

    def apply():
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
        try:
            with open(descriptor, "wb", closefd=True) as handle:
                _injected_error("write", path)
                handle.write(data)
                handle.flush()
                _injected_error("file-fsync", path)
                os.fsync(handle.fileno())
        except OSError as content_error:
            try:
                remove_name(path)
            except (OSError, VisibilityUnconfirmed) as cleanup_error:
                combined = OSError(
                    errno.EIO,
                    f"{content_error}; cleanup could not be confirmed: "
                    f"{cleanup_error}",
                    os.fspath(path),
                )
                raise VisibilityUnconfirmed(
                    path,
                    _Operation.CREATE_EXCLUSIVE.value,
                    False,
                    combined,
                ) from cleanup_error
            raise

    return persist(
        _Effect(_Operation.CREATE_EXCLUSIVE, path, path.parent, apply)
    )


def create_directory(path):
    """Create one directory and confirm the name in its parent."""
    path = Path(path)

    def apply():
        _injected_error("mkdir", path)
        os.mkdir(path)

    return persist(
        _Effect(
            _Operation.CREATE_DIRECTORY,
            path,
            path.parent,
            apply,
        )
    )


def _staged_identity(temporary, target):
    """The `(device, inode)` of the link just staged at `temporary`.

    Raises `StagedLinkChanged` when the name is not a symlink to `target`, and
    `OSError` when it cannot be examined.
    """
    info = os.lstat(temporary)
    if not stat.S_ISLNK(info.st_mode) or os.readlink(temporary) != os.fspath(target):
        raise StagedLinkChanged(temporary)
    return info.st_dev, info.st_ino


def _examine_staged(temporary, target, identity):
    """Whether `temporary` is still the link staged, without following it.

    False when the name is absent or is anything else. Raises `OSError` when
    the name cannot be examined at all, which says nothing about whether it
    changed.
    """
    try:
        info = os.lstat(temporary)
    except FileNotFoundError:
        return False
    if not stat.S_ISLNK(info.st_mode) or (info.st_dev, info.st_ino) != identity:
        return False
    try:
        return os.readlink(temporary) == os.fspath(target)
    except FileNotFoundError:
        return False


def replace_symlink(
    path, target, temporary=None, crash_storage=False, before_replace=None
):
    """Atomically publish a symlink and confirm its carrying directory.

    The link is staged under a temporary name beside `path` and renamed over
    it. `before_replace`, when given, is called once with the link staged and
    before the rename: None lets the rename go ahead, and anything else stops
    it, raises `ReplaceDeclined` carrying that answer and publishes nothing.

    The staged link is identified when it is made (`lstat`: device, inode, a
    symlink, the text of `target`) and identified again after `before_replace`
    returns. If the name is no longer that link it is neither renamed nor
    unlinked, and `StagedLinkChanged` is raised, whatever `before_replace`
    answered. A name that cannot be examined is not that verdict: when the
    answer stops the replacement it stands, and when it does not the `OSError`
    is raised and nothing is renamed. Whatever else ends the replacement
    without publishing, the staged link is unlinked when it is still that
    link; that removal is best effort, and a staged link remains when the name
    cannot be examined or unlinked, as when the parent directory cannot be
    searched or written. Only the second identification and `before_replace`'s
    answer may stand between `before_replace` and the rename.
    """
    path = Path(path)
    forced = os.environ.get("VALIDATED_MEMORY_SYMLINK_TEMP_NAME")
    temporary = (
        Path(temporary)
        if temporary is not None
        else path.parent / (
            forced
            if forced
            else f".{path.name}.{next(tempfile._get_candidate_names())}.tmp"
        )
    )

    def apply():
        staged = None
        published = False
        try:
            os.symlink(target, temporary)
            staged = _staged_identity(temporary, target)
            if crash_storage:
                storage_crash("staged-before-install", temporary)
            answer = None
            if before_replace is not None:
                answer = before_replace()
            try:
                intact = _examine_staged(temporary, target, staged)
            except OSError:
                if answer is None:
                    raise
                intact = True
            if not intact:
                raise StagedLinkChanged(temporary)
            if answer is not None:
                raise ReplaceDeclined(answer)
            os.replace(temporary, path)
            published = True
        finally:
            if staged is not None and not published:
                try:
                    if _examine_staged(temporary, target, staged):
                        temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    return persist(
        _Effect(_Operation.REPLACE_SYMLINK, path, path.parent, apply)
    )


def append_bytes(path, data):
    """Append complete bytes, flush them, then confirm the history location."""
    path = Path(path)

    def apply():
        handle = path.open("ab")
        try:
            with handle:
                storage_prefix(path, data, handle)
                handle.write(data)
                handle.flush()
                _injected_error("history-file-fsync", path)
                os.fsync(handle.fileno())
        except OSError as error:
            raise VisibilityUnconfirmed(
                path, _Operation.APPEND.value, False, error
            ) from error

    return persist(_Effect(_Operation.APPEND, path, path.parent, apply))


def remove_name(path, directory=False):
    """Remove one name and confirm its absence in the carrying directory."""
    path = Path(path)
    apply = (lambda: os.rmdir(path)) if directory else (
        lambda: path.unlink(missing_ok=True)
    )
    return persist(
        _Effect(
            _Operation.REMOVE,
            path,
            path.parent,
            apply,
        )
    )


def read_file_snapshot(path, *, identify=False):
    """Read bytes and mode from the regular file held by one descriptor."""
    path = Path(path)
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise OSError(errno.EBUSY, "path is not a regular file", os.fspath(path))
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        held = os.fstat(descriptor)
        after = os.lstat(path)
        identity = (held.st_dev, held.st_ino)
        if (
            not stat.S_ISREG(held.st_mode)
            or (before.st_dev, before.st_ino) != identity
            or (after.st_dev, after.st_ino) != identity
        ):
            raise OSError(
                errno.EBUSY,
                "path changed while its file snapshot was read",
                os.fspath(path),
            )
        with open(descriptor, "rb", closefd=False) as handle:
            data = handle.read()
    finally:
        os.close(descriptor)
    result = (data, stat.S_IMODE(held.st_mode))
    return (*result, held) if identify else result


def read_regular_file(path):
    """The bytes of `path` when it is a regular file, read without blocking.

    A symlink is not followed, and a node that is not a regular file -- a named
    pipe, a directory, a device -- is refused before it is opened: opening a
    pipe for reading blocks until a writer appears, and following a link reads
    whatever it points at. What raises is `FileNotFoundError` for a name that
    is absent and an `OSError` carrying only its message for a node that is not
    a regular file. The open uses `O_NONBLOCK` and `O_NOFOLLOW` where the
    platform has them, and the descriptor is checked with `fstat`; keep all
    three, because the `lstat` describes the name only at the moment it ran.
    """
    path = Path(path)
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise OSError("it is not a regular file; it was not opened")
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("it is not a regular file; it was closed unread")
        with open(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _swap_snapshot_for_test(path, snapshot_kind, data, mode):
    """Deterministically change a validated pathname for race coverage."""
    requested = {
        item.strip()
        for item in os.environ.get(
            "VALIDATED_MEMORY_PERSISTENCE_FAULT", ""
        ).split(",")
        if item.strip()
    }
    name = Path(path).name
    if f"swap-target-symlink:{name}" in requested:
        backing = Path(path).with_name(f".{name}.swap-target")
        backing.write_bytes(data)
        Path(path).unlink()
        os.symlink(backing.name, path)
    elif f"swap-target-mode:{name}" in requested:
        os.chmod(path, 0o600 if mode != 0o600 else 0o644)
    elif f"swap-{snapshot_kind}:{name}" in requested:
        Path(path).write_bytes(b"adversarial swap\n")


def republish_file(path, data, mode, snapshot_kind):
    """Republish exact readable bytes through a fresh atomic namespace effect."""
    path = Path(path)
    temporary, descriptor = _temporary_path(
        path.parent, path.name, ".republish.tmp"
    )
    identity = None
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            identity = os.fstat(handle.fileno())
        _swap_staging_for_test(temporary)
        _chmod_staging(descriptor, temporary, mode)
        _swap_snapshot_for_test(path, snapshot_kind, data, mode)
        try:
            current_data, current_mode = read_file_snapshot(path)
        except OSError as error:
            raise OSError(
                errno.EBUSY,
                f"{snapshot_kind} changed after its validated snapshot: "
                f"{error}",
                os.fspath(path),
            ) from error
        if current_data != data or current_mode != mode:
            raise OSError(
                errno.EBUSY,
                f"{snapshot_kind} changed after its validated snapshot",
                os.fspath(path),
            )
        return install(temporary, path)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        if identity is not None and _same_open_entry(temporary, identity):
            temporary.unlink()
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def republish_directory(path):
    """Establish a fresh namespace operation for an exact directory state."""
    path = Path(path)
    if any(path.iterdir()):
        raise OSError(
            errno.ENOTEMPTY,
            "directory postimage is not empty",
            os.fspath(path),
        )
    if os.name == "nt":
        raise OSError(
            errno.ENOTSUP,
            "automatic empty-directory recovery is unsupported on Windows; "
            "the transaction is retained for manual inspection",
            os.fspath(path),
        )
    parent = path.parent.resolve()
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{path.name}.republish.", dir=parent)
    )
    try:
        os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        staging = ensure_owned_directory(temporary, parent)
        if staging is Persistence.UNSUPPORTED:
            error = OSError(
                errno.ENOTSUP,
                "the staging directory and its carrying entry cannot be "
                "confirmed on this filesystem",
                os.fspath(temporary),
            )
            raise VisibilityUnconfirmed(
                temporary, "stage-directory", True, error
            ) from error
        return persist(
            _Effect(
                _Operation.INSTALL,
                path,
                path.parent,
                lambda: os.replace(temporary, path),
            )
        )
    except VisibilityUnconfirmed:
        raise
    except Exception:
        try:
            temporary.rmdir()
        except FileNotFoundError:
            pass
        raise


def fsync_directory(path):
    """Compatibility name for a truthful directory confirmation."""
    return confirm_directory(path)
