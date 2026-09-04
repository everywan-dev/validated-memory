"""The per-adopter exclusive lock: where it lives, and how it is held.

Taken for the duration of a mutation, re-entrant within one process, and
broken only when its owner is provably gone.
"""

import os
import stat
import time
from pathlib import Path

from .records import JOURNAL_FILENAME, VAULT_DIRNAME, JournalError


LOCK_FILENAME = "lock"


# The last resort, not the liveness test: a lock older than this is broken on
# age alone. `Lock` reads the owning pid first and never breaks a lock whose
# owner is still running, whatever its age, and breaks one whose owner is
# gone at once -- a run killed mid-mutation must not wedge the next session's
# `init` for five minutes. What is left for this horizon is a lock file whose
# pid cannot be read at all: a run killed between creating the file and
# writing its pid leaves an empty one, and nothing about it says who to ask.
# Five minutes is a bet that no legitimate holder is slower than that, which
# holds today because the lock is held for the length of one `init`.
STALE_LOCK_SECONDS = 300

# How long a run waits for a lock somebody else legitimately holds before it
# refuses. Bounded because the caller is a session hook: a command that
# blocks for as long as the holder wants is a session that never starts.
# Ten seconds is not a promise that no holder is slower -- `init` holds this
# lock across `adopt.take_over`, which copies as many files as the harness
# memory has -- it is the point at which waiting stops being useful and the
# message below, which says what to do, is better than a hang.
LOCK_WAIT_SECONDS = 10


def lock_path(root=Path()):
    """Where `root`'s lock file lives: beside the journal it protects.

    The lock exists to stop two processes appending interleaved records to
    ONE journal, and `journal.jsonl` is allowed to be a symlink into a
    shared store (`read` reads through it, `append` writes through it). Two
    adopter trees linked at one store would take two different local locks
    and serialise nothing, so the answer is taken from the RESOLVED
    artifact: the lock is `.validated-memory/lock` under the directory the
    journal really lives in. For an adopter whose journal is a plain file
    -- every adopter until someone links one -- that directory is the
    adopter root, and the lock is the one inside it, named absolutely.

    A `journal.jsonl` symlink that resolves to anything but a regular file
    -- broken, or a directory -- keeps the local lock. There is no shared
    store to serialise against, `read` refuses such a journal anyway, and
    following the link would create `.validated-memory/` somewhere outside
    the adopter root that nobody asked for.

    One thing a shared store does not get: a store reached over a network
    filesystem is shared between HOSTS, and the pid `Lock` writes into the
    file means nothing on another host or in another pid namespace, where
    it may name a live local process or none at all. The mutual exclusion
    still holds -- it is `O_CREAT | O_EXCL` -- but breaking a lock left
    behind by a dead run is a single-host promise.
    """
    root = Path(root)
    artifact = root / JOURNAL_FILENAME
    resolved = _resolved(artifact)
    if resolved is None or (
        artifact.is_symlink() and not _is_regular_file(resolved)
    ):
        home = _resolved(root)
        if home is None:
            # Absolute even here: `_HELD` keys on this path, and two
            # spellings of one directory must not become two holders.
            home = root.absolute()
        resolved = home / JOURNAL_FILENAME
    return resolved.parent / VAULT_DIRNAME / LOCK_FILENAME


def _resolved(path):
    """`path` with its symlinks followed, or None when they cannot be.

    Resolution is not total: a symlink loop raises, and it raises
    `RuntimeError` rather than `OSError`, which is not what any caller of
    this package catches. Deciding where the lock goes must not be the step
    that ends a run with a traceback -- a journal whose name cannot be
    resolved is refused moments later by `read`, with a message that names
    the file and the reason. So an unresolvable name gets the local answer
    and the run reaches that refusal.
    """
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _is_regular_file(path):
    """Whether `path` is a regular file, following links, without raising."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except OSError:
        return False


# The locks this process holds: `{lock path: [descriptor, depth]}`, keyed by
# the resolved path so that two `Lock` objects naming one file are one
# holder. Re-entrancy has to be per PROCESS, not per object: `init.run`
# takes the lock for a whole run and `Run.__init__` takes it again
# underneath through an object the outer caller never sees. This is process
# state and it is not thread-safe: the depth is read and written without a
# mutex, so two threads of one process entering at once can miscount. Every
# caller today is single-threaded.
_HELD = {}


class Lock:
    """A per-adopter exclusive lock, taken for the duration of a mutation.

    `init` is deliberately re-runnable at session start and concurrent
    renderers are already expected, so two processes appending interleaved
    `prepared` records to one journal is a real state, not a theoretical
    one -- and it would produce a journal describing a state that never
    existed.

    The lock is a file created with `O_CREAT | O_EXCL`, which is atomic. Its
    contents are the owning pid, and that pid is what the rest of this class
    is built on: it is read back to ask whether the owner is still running.

    Within one process the lock is RE-ENTRANT, counted per resolved lock
    path and not per object (`_HELD`): `init.run` holds it for a whole run
    and `Run.__init__` takes it again underneath, and a second `O_EXCL`
    create would wait out the whole `LOCK_WAIT_SECONDS` and then refuse the
    run.
    The file is created by the outermost `__enter__` and removed by the
    `__exit__` that brings the depth back to zero. Between processes it
    excludes exactly as before. The registry is module state, so a `Lock`
    object means nothing outside the process that entered it: one must not
    be shared with a child process or carried across a fork as held.

    Waiting for a lock someone else holds breaks in only when the holder is
    provably gone. A lock whose pid names a live process is never broken,
    whatever its age -- the holder is doing the work the lock protects. A
    lock whose pid names no process is broken at once, because a run that
    died mid-mutation must not wedge the next session's `init` behind the
    age horizon. `STALE_LOCK_SECONDS` covers only what is left: a pid that
    cannot be read.

    Never breaking a live pid has a price, and the contention message pays
    it: if the operating system has handed that pid to an unrelated process
    since the run died, nothing here can tell, so the lock is honoured
    forever and the message says to delete the file when no
    validated-memory process is running.

    Releasing identifies the lock by the device and inode of the descriptor
    this process holds, not by the name, and unlinks nothing else, so a
    process whose lock was broken cannot delete its successor's. Breaking
    re-checks the same identity immediately before it unlinks. Neither
    makes breaking atomic: between that check and the `unlink` the file can
    still be replaced, and a break landing in that window can leave two
    processes holding the lock. What the checks buy is the width of the
    window -- the two adjacent syscalls that check and unlink, and nothing
    in between -- and the certainty that a release never removes a file
    this process did not create.
    """

    def __init__(self, root=Path()):
        self.path = lock_path(root)
        # What a Finding should call this lock. Naming the vault of the tree
        # the command ran in would send a reader to a directory that holds
        # no lock whenever the journal is a symlink into a store, so the
        # label is the lock's own path: relative to the root while it is
        # inside it, which is every adopter that has not linked its journal
        # away, and absolute when it is not.
        home = _resolved(root) or Path(root).absolute()
        try:
            self.artifact = self.path.relative_to(home).as_posix()
        except ValueError:
            self.artifact = self.path.as_posix()
        # The registry key, and a count of the entries THIS object made, so
        # an `__exit__` without a matching `__enter__` cannot decrement a
        # depth some other object is holding.
        self._key = str(self.path)
        self._entries = 0

    def __enter__(self):
        held = _HELD.get(self._key)
        if held is not None:
            held[1] += 1
            self._entries += 1
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                descriptor = os.open(
                    self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
                )
                try:
                    os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
                except OSError:
                    # `__exit__` never runs when `__enter__` raises, so the
                    # descriptor and the lock file have to be released here or
                    # they outlive the failure by the whole stale window. A
                    # lock file with no pid in it is also the one shape no
                    # later run can ask about, so it must not be left behind.
                    os.close(descriptor)
                    self.path.unlink(missing_ok=True)
                    raise
                _HELD[self._key] = [descriptor, 1]
                self._entries += 1
                return self
            except FileExistsError:
                if self._break_if_unowned():
                    continue
                if time.monotonic() >= deadline:
                    raise JournalError(
                        None,
                        f"another validated-memory process holds "
                        f"{self.path.as_posix()}; retry when it finishes, "
                        f"or if no validated-memory process is running, "
                        f"delete {self.path.as_posix()}",
                        self.artifact,
                    )
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, traceback):
        if not self._entries:
            return False
        self._entries -= 1
        held = _HELD.get(self._key)
        if held is None:
            return False
        held[1] -= 1
        if held[1] > 0:
            return False
        del _HELD[self._key]
        descriptor = held[0]
        try:
            self._unlink_if_still_ours(descriptor)
        finally:
            os.close(descriptor)
        return False

    def _unlink_if_still_ours(self, descriptor):
        """Remove the lock file only when it is still the one we created.

        The descriptor outlives the name: if this lock was broken and a
        successor created its own file under the same path, `st_dev` and
        `st_ino` differ and the successor's lock is left exactly where it
        is. A path that is already gone -- broken and not yet retaken -- is
        nothing to undo either.
        """
        try:
            mine = os.fstat(descriptor)
            there = os.stat(self.path)
        except OSError:
            return
        if (there.st_dev, there.st_ino) == (mine.st_dev, mine.st_ino):
            self.path.unlink(missing_ok=True)

    def _break_if_unowned(self):
        """Remove the lock when nothing is holding it any more.

        Returns whether it broke one, so the caller can retry at once.

        The file is opened ONCE and every question is asked of that
        descriptor -- the pid it holds, its age, and which file it is --
        because a lock file can be replaced between two calls that name it,
        and the answers would then describe two different files.

        Three answers, cheapest and most certain first. The file is gone:
        someone released it, retry. The pid names a process that exists:
        never break it, whatever the file's age -- that process is inside
        the mutation this lock protects, and `PermissionError` from
        `os.kill` is one of those, a live process owned by another user.
        The pid names no process (`ProcessLookupError`): the file outlived
        its owner, so break it now rather than making every later run wait
        out `STALE_LOCK_SECONDS` behind a run that is already dead.

        Only a pid that cannot be read -- an empty file from a run killed
        between creating it and writing to it, bytes that are not a
        positive integer, a file that cannot even be opened -- falls
        through to the age horizon. `os.kill(0, 0)` signals this process's
        whole group, so a pid that is not positive is never asked about.
        """
        try:
            descriptor = os.open(
                self.path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
            )
        except FileNotFoundError:
            return True  # It went away on its own; try again immediately.
        except OSError:
            # Unopenable, so nothing can be asked of it: the age of the name
            # is all that is left.
            return self._break_if_old()
        try:
            status = os.fstat(descriptor)
            raw = os.read(descriptor, 64)
        except OSError:
            return self._break_if_old()
        finally:
            os.close(descriptor)
        identity = (status.st_dev, status.st_ino)
        try:
            pid = int(raw.decode("ascii").strip())
        except ValueError:
            pid = 0
        if pid <= 0:
            return self._break_if_old(status, identity)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            self._unlink_if_unchanged(identity)
            return True
        except OSError:
            return False  # Alive, or unknowable: either way, not ours.
        return False

    def _break_if_old(self, status=None, identity=None):
        """Break the lock on age alone -- the last resort, for an unreadable pid.

        `status` and `identity` come from the descriptor the caller already
        had, so the age and the file are the same file; without them the
        name is stat'ed here, which is the only way in when the file could
        not be opened at all.
        """
        if status is None:
            try:
                status = os.stat(self.path)
            except FileNotFoundError:
                return True  # Gone on its own; try again immediately.
            except OSError:
                return False  # Cannot even be looked at: wait it out.
            identity = (status.st_dev, status.st_ino)
        if time.time() - status.st_mtime < STALE_LOCK_SECONDS:
            return False
        self._unlink_if_unchanged(identity)
        return True

    def _unlink_if_unchanged(self, identity):
        """Remove the lock only while it is still the file `identity` names.

        Between deciding a lock may be broken and removing it, its owner can
        have released it and a third process taken its place. Without this
        re-check the breaker would delete the newcomer's live lock and then
        create its own, and two processes would hold the lock at once. The
        re-check does not close that hole -- the file can still be replaced
        between this `stat` and the `unlink` below -- it narrows it to those
        two calls.
        """
        try:
            here = os.stat(self.path)
        except OSError:
            return
        if (here.st_dev, here.st_ino) == identity:
            self.path.unlink(missing_ok=True)
