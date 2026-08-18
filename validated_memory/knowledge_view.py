"""Builds `knowledge.html`: the curated layer, live conclusions first."""

from . import html

TITLE = "Curated knowledge"


def build(documents, basis):
    """Return the whole page as a string."""
    parts = [f"<h1>{html.escape_text(TITLE)}</h1>"]
    parts.append(
        f'<p class="basis">Basis: {len(documents)} unit(s) under '
        f"{html.escape_text(basis)}</p>"
    )
    return html.page(TITLE, "\n".join(parts))
