"""HTML escaping and document shell. Escape repository text before markup assembly."""

import html as _html


def escape_text(value):
    """Escape text content, not quotes; stringify values and render None as empty."""
    if value is None:
        return ""
    return _html.escape(str(value), quote=False)


def escape_attribute(value):
    """Escape double-quoted attributes; stringify values and render None as empty."""
    if value is None:
        return ""
    return _html.escape(str(value), quote=True)


def page(title, body, stylesheet):
    """Escape the title and wrap trusted body markup and repository-owned CSS.

    Body and stylesheet are inserted verbatim; never pass adopter-authored CSS.
    """
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape_text(title)}</title>\n"
        f"<style>{stylesheet}</style>\n</head>\n<body>\n"
        f"{body}\n</body>\n</html>\n"
    )
