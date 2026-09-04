"""Getting one change to the filesystem to survive a power cut.

Two primitives and the order between them: the atomic publication of a
temporary file over a name, and the barrier that makes the directory entry
carrying that name durable. Nothing here knows what the bytes are: a
transaction file, a parked preimage and an adopter's own file are published
by the same two calls, and none of them is this module's to interpret. A
journal line is not among them -- `records.append` opens the journal for
append and fsyncs the handle, which is a different question and is answered
where it is asked.
"""

import os
from pathlib import Path


def install(temporary, target):
    """Atomically move `temporary` onto `target`, and ask for the barrier.

    The rename is atomic on every platform this runs on. The durability is
    conditional: `fsync_directory` skips the barrier on a platform that
    cannot open a directory for reading, for the reason its own docstring
    gives, so what this promises unconditionally is the atomicity and not
    the survival of the directory entry.

    `os.replace` publishes the new bytes under the old name, but the
    directory entry carrying that name is itself buffered. Without the
    directory fsync, a `committed` record that was flushed to disk can
    outlive the rename it describes -- "a record describes a state that
    never existed", one power cut down -- so
    docs/design/2026-08-30-the-journal-coverage-and-reversal-design.md
    §4's claim that a `committed` record means the bytes are on disk
    would hold for a process crash and not for a power loss.

    The order is the guarantee, and no test in this suite reaches it: a
    power loss is not observable at the CLI seam this project tests
    through, so what pins the sequence is this sentence and the review
    that reads it.
    """
    os.replace(temporary, target)
    fsync_directory(Path(target).parent)


def fsync_directory(path):
    """Flush a directory's own entries to disk.

    A platform where a directory cannot be opened for reading skips the
    barrier rather than failing the write it was protecting: the bytes are
    already fsynced and renamed at this point, and refusing here would turn
    a durability improvement into a lost mutation.
    """
    try:
        handle = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)
