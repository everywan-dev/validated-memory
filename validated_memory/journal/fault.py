"""The crash seam: the four points at which a test may kill the process.

One module for one environment variable, so the package has exactly one
reader of it and a grep for `_fault` finds every line that can act on it.
Neither name here is exported by the package.
"""

import os
import sys


# The seams of the executor's protocol: a transaction file fsynced with
# nothing published yet, the new bytes published but the transaction not yet
# marked, the transaction marked published but the permanent history not yet
# appended, and the history appended but the transaction not yet resolved.
# Four, and only four: every mutation this package performs now goes through
# the executor, so these are the whole of the protocol's seams. The two points
# that named the older `prepare_op`/`append_op` protocol went with it.
FAULT_POINTS = (
    "after-transaction",
    "after-publish",
    "after-published",
    "after-history",
)


def _fault(point):
    """Die at `point`, hard, if `VALIDATED_MEMORY_FAULT` names it.

    This is the one place in the package that reads that variable: a test
    driving the CLI as a subprocess has no `monkeypatch` that reaches past
    the subprocess boundary, and the only crash simulation this suite had
    before this function was hand-editing an artifact afterwards, which
    proves nothing about what the process actually leaves behind mid-write.

    The death is `os._exit`, not `sys.exit` or a raised exception: no
    `finally` clause runs, no lock is released, no temporary is cleaned up.
    That is what a real crash looks like, and a fault test's assertions are
    only honest if the seam does not clean up after itself. `70` (`EX_SOFTWARE`
    in the BSD sysexits convention this project otherwise ignores) is
    chosen only to be distinguishable from an ordinary exit code and a
    signal death; nothing reads it back.

    Unset, this changes nothing: `os.environ.get(...) == point` is false
    for every `point` when the variable is absent, so every call site below
    falls through exactly as if `_fault` were not called at all. Set to a
    point this run never reaches, it is equally inert. Nothing outside this
    function may read `VALIDATED_MEMORY_FAULT`.
    """
    if os.environ.get("VALIDATED_MEMORY_FAULT") == point:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(70)
