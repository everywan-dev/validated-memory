"""The `render` subcommand: static HTML views of an adopter's validated memory.

Two artifacts, `knowledge.html` and `memory.html`, written to the working
directory. Each is self-contained and inert: no JavaScript, no request to the
network, nothing to trust in an attachment. See
docs/design/2026-08-18-render-views.md.
"""

from .findings import EXIT_OK

KNOWLEDGE_ARTIFACT = "knowledge.html"
MEMORY_ARTIFACT = "memory.html"


def run(only_existing, stdout, stderr):
    """Render the views. Returns an exit code."""
    return EXIT_OK
