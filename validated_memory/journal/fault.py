"""The crash seams and bounded, private test-only rendezvous points.

One module owns every fault/test-control environment variable, so each has one
reader.
A grep for `fault_at` finds every line that can hard-kill the process; the
sleeping and descriptor rendezvous seams are separate and cannot become extra
crash points. No helper is exported by the package.
"""

import os
import select
import sys
import time


# The seams of the executor's protocol: a transaction file fsynced with
# nothing published yet, the new bytes published but the transaction not yet
# marked, the transaction marked published but the permanent history not yet
# appended, and the history appended but the transaction not yet resolved.
# Four, and only four: every mutation this package performs goes through the
# executor, so these are the whole of the protocol's seams.
FAULT_POINTS = (
    "after-transaction",
    "after-publish",
    "after-published",
    "after-history",
)


_TEST_SEAM_VARIABLE = "VALIDATED_MEMORY_TEST_SEAM"
_TEST_SEAM_SECONDS = 2
_test_seam_used = False
_RENDEZVOUS_VARIABLE = "VALIDATED_MEMORY_TEST_RENDEZVOUS"
_RENDEZVOUS_READY_FD = "VALIDATED_MEMORY_TEST_READY_FD"
_RENDEZVOUS_CONTINUE_FD = "VALIDATED_MEMORY_TEST_CONTINUE_FD"
_RENDEZVOUS_TIMEOUT = 5


def fault_at(point):
    """Die at `point`, hard, if `VALIDATED_MEMORY_FAULT` names it.

    The one place in the package that reads that variable: a test driving
    the CLI as a subprocess has no `monkeypatch` reaching past the
    subprocess boundary.

    The death is `os._exit`, not `sys.exit` or a raised exception: no
    `finally` clause runs, no lock is released, no temporary is cleaned up.
    That is what a real crash looks like, and a fault test's assertions are
    only honest if the seam does not clean up after itself. `70` is chosen
    only to be distinguishable from an ordinary exit code and a signal
    death; nothing reads it back.

    Unset or naming a point this run never reaches, it is inert: every call
    site falls through exactly as if `fault_at` were not called at all.
    """
    if os.environ.get("VALIDATED_MEMORY_FAULT") == point:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(70)


def sleep_at(point):
    """Pause once at the private test seam, and otherwise remain inert.

    This is deliberately not a crash seam. Its fixed duration bounds a failed
    rendezvous, and claiming the one permitted pause before sleeping prevents
    repeated mutations in one process from extending that bound.
    """
    global _test_seam_used
    if _test_seam_used or os.environ.get(_TEST_SEAM_VARIABLE) != point:
        return
    _test_seam_used = True
    time.sleep(_TEST_SEAM_SECONDS)


def rendezvous_at(point, attempt):
    """Meet a bounded test process at one descriptor-only read boundary."""
    if os.environ.get(_RENDEZVOUS_VARIABLE) != point:
        return
    try:
        ready = int(os.environ[_RENDEZVOUS_READY_FD])
        proceed = int(os.environ[_RENDEZVOUS_CONTINUE_FD])
        os.write(ready, f"{attempt}\n".encode("ascii"))
        readable, _, _ = select.select([proceed], [], [], _RENDEZVOUS_TIMEOUT)
        if not readable:
            raise RuntimeError(f"test rendezvous at {point} timed out")
        if len(os.read(proceed, 1)) != 1:
            raise RuntimeError(f"test rendezvous at {point} received no continuation")
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise RuntimeError(f"test rendezvous at {point} is invalid: {error}") from error
