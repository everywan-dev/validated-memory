"""The crash seam: the four points at which a test may kill the process.

One module for one environment variable, so the package has exactly one
reader of it and a grep for `fault_at` finds every line that can act on it.
Neither name here is exported by the package.
"""

import os
import sys


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
