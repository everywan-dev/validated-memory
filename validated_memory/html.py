"""HTML primitives: escaping, and the shell every view is poured into.

No domain knowledge lives here. The one rule this module exists to enforce is
that text from the repository is escaped before it becomes markup, never
after: a `<pre>` block does not escape anything by itself.
"""

import html as _html


def escape_text(value):
    """Escape `value` for use as text content. Never returns markup."""
    if value is None:
        return ""
    return _html.escape(str(value), quote=False)


def escape_attribute(value):
    """Escape `value` for use inside a double-quoted attribute. Never returns markup.

    Shares `escape_text`'s `None` handling: an absent value spells the same
    way, `""`, wherever it is rendered -- a history list built with one and
    a freshness strip's `aria-label` built with the other must not disagree
    about what "absent" looks like.
    """
    if value is None:
        return ""
    return _html.escape(str(value), quote=True)


def page(title, body, stylesheet):
    """Wrap `body` in the document shell. `title` is escaped; `body` is markup.

    `stylesheet` is the caller's own -- this module holds none. A view that
    wants to restyle itself edits its own constant in `styles`, and cannot
    reach another view's by doing so. It is inlined verbatim: CSS has no
    escaping that survives being parsed as CSS, so passing anything
    adopter-authored here would be an injection, and no caller does.
    """
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape_text(title)}</title>\n"
        f"<style>{stylesheet}</style>\n</head>\n<body>\n"
        f"{body}\n</body>\n</html>\n"
    )
