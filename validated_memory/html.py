"""HTML primitives: escaping, and the shell every view is poured into.

No domain knowledge lives here. The one rule this module exists to enforce is
that text from the repository is escaped before it becomes markup, never
after: a `<pre>` block does not escape anything by itself.
"""

import html as _html

STYLESHEET = """
:root { color-scheme: light dark; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
       margin: 2rem auto; max-width: 60rem; padding: 0 1rem; line-height: 1.5; }
pre { white-space: pre-wrap; overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      background: rgba(127,127,127,0.12); padding: .75rem; border-radius: .25rem; }
summary { cursor: pointer; }
.chain { border-left: 3px solid rgba(127,127,127,0.4); margin-left: .5rem;
         padding-left: 1rem; }
.meta { color: rgba(127,127,127,1); font-size: .9em; }
"""


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


def page(title, body):
    """Wrap `body` in the document shell. `title` is escaped; `body` is markup."""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape_text(title)}</title>\n"
        f"<style>{STYLESHEET}</style>\n</head>\n<body>\n"
        f"{body}\n</body>\n</html>\n"
    )
