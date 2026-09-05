"""Render every collected memory document, without enforcing lint.

Show malformed frontmatter and unresolved references rather than hiding
entries. Frontmatter values are unvalidated: guard type-dependent operations
and stringify/escape all displayed values.
"""

from collections import namedtuple

from . import html, styles
from . import memory as memory_module
from .frontmatter import FrontmatterError
from .frontmatter import parse as parse_frontmatter

TITLE = "Agent memory"

# filename is canonical identity (ADR 0001); identity is name or filename.
# Undeclared anchors use relpath, not identity; only declared names resolve.
# A parse error leaves data/body/marker absent and targets empty.
_Record = namedtuple(
    "_Record",
    "document filename identity declared anchor data body targets marker error",
)


def _anchor(value, declared):
    """Use disjoint declared-name and relative-path DOM-id namespaces.

    Fallback paths, not bare filenames, distinguish subdirectories. Duplicate
    declared names still produce duplicate IDs: this view does not enforce
    lint or invent a resolution for ambiguous wikilinks.
    """
    if declared:
        return f"entry-name-{value}"
    return f"entry-path-{value}"


def build(documents, basis, resolution):
    """Return entries ordered by filename, disclosing their source path as basis."""
    ordered = sorted(
        documents, key=lambda document: memory_module.filename(document.location)
    )
    records = [_read(document) for document in ordered]
    incoming = _incoming_map(records, resolution)

    parts = [f"<h1>{html.escape_text(TITLE)}</h1>"]
    parts.append(
        f'<p class="basis">Basis: {len(records)} memory file(s) under '
        f"{html.escape_text(basis)}</p>"
    )
    for record in records:
        # A fallback filename matching another entry's name must not inherit its links.
        referrers = incoming.get(record.identity, []) if record.declared else []
        parts.append(_entry_section(record, resolution, referrers))
    return html.page(TITLE, "\n".join(parts), styles.MEMORY)


def _read(document):
    """Parse a document for presentation; capture FrontmatterError in its record."""
    filename = memory_module.filename(document.location)
    try:
        data = parse_frontmatter(document.text)
    except FrontmatterError as error:
        return _Record(
            document, filename, filename, False, _anchor(document.relpath, False),
            None, None, [], None, error,
        )

    name = data.get("name")
    declared = memory_module.is_declared(name)
    identity = name if declared else filename
    # Fallback filenames may repeat across subdirectories; relative paths do not.
    anchor = (
        _anchor(identity, declared)
        if declared
        else _anchor(document.relpath, declared)
    )
    body = memory_module.body(document.text)
    description = data.get("description")
    marker = memory_module.supersession(description)
    scan_text = marker.remainder if marker is not None else description
    # wikilinks accepts unvalidated, non-string descriptions.
    targets = _ordered_unique(
        memory_module.wikilinks(scan_text) + memory_module.wikilinks(body)
    )
    return _Record(
        document, filename, identity, declared, anchor,
        data, body, targets, marker, None,
    )


def _ordered_unique(items):
    """Deduplicate strings in first-seen order."""
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _incoming_map(records, resolution):
    """Index outgoing references only under names recognized by resolution."""
    incoming = {}
    for record in records:
        for target in record.targets:
            if target not in resolution.by_name:
                continue
            incoming.setdefault(target, []).append(record)
    return incoming


def _entry_section(record, resolution, referrers):
    document = record.document
    open_tag = (
        f'<section class="entry" id="{html.escape_attribute(record.anchor)}"'
        f' data-name="{html.escape_attribute(record.identity)}">\n'
    )
    summary = (
        "<details>\n<summary>"
        f'<code class="filename">{html.escape_text(record.filename)}</code> '
        f'<span class="relpath">{html.escape_text(document.relpath)}</span>'
        "</summary>\n"
    )
    if record.error is not None:
        error_html = (
            '<p class="frontmatter-error">Frontmatter did not parse: '
            f"{html.escape_text(record.error.message)} (line "
            f"{html.escape_text(record.error.lineno)})</p>\n"
        )
        return open_tag + summary + error_html + "</details>\n</section>"

    data = record.data
    metadata = data.get("metadata")
    kind = metadata.get("type") if isinstance(metadata, dict) else None
    fields = (
        f'<p class="name">{html.escape_text(data.get("name"))}</p>\n'
        f'<p class="description">{html.escape_text(data.get("description"))}</p>\n'
        f'<p class="type">{html.escape_text(kind)}</p>\n'
    )
    return (
        open_tag
        + summary
        + fields
        + _supersession_html(record.marker, resolution)
        + f'<pre class="body">{html.escape_text(record.body)}</pre>\n'
        + _outgoing_list(record.targets, resolution)
        + _incoming_list(referrers)
        + "</details>\n</section>"
    )


def _supersession_html(marker, resolution):
    if marker is None:
        return ""
    if marker.target is not None and marker.target in resolution.by_name:
        anchor = _anchor(marker.target, True)
        target_html = (
            f'<a href="#{html.escape_attribute(anchor)}">'
            f"{html.escape_text(marker.target)}</a>"
        )
    elif marker.target is not None:
        target_html = f'<span class="unresolved">{html.escape_text(marker.target)}</span>'
    else:
        target_html = '<span class="unresolved">(malformed marker)</span>'
    return f'<p class="superseded">Superseded by {target_html}</p>\n'


def _outgoing_list(targets, resolution):
    if not targets:
        return '<p class="meta">No outgoing references.</p>\n'
    items = []
    for target in targets:
        text = html.escape_text(target)
        if target in resolution.by_name:
            anchor = _anchor(target, True)
            items.append(
                f'<li><a href="#{html.escape_attribute(anchor)}">{text}</a></li>'
            )
        else:
            items.append(f'<li class="unresolved">{text}</li>')
    return (
        '<p class="meta">Outgoing references:</p>\n'
        '<ul class="outgoing">\n' + "\n".join(items) + "\n</ul>\n"
    )


def _incoming_list(referrers):
    if not referrers:
        return '<p class="meta">No incoming references.</p>\n'
    items = []
    for referrer in referrers:
        text = html.escape_text(referrer.identity)
        items.append(
            f'<li><a href="#{html.escape_attribute(referrer.anchor)}">{text}</a></li>'
        )
    return (
        '<p class="meta">Incoming references:</p>\n'
        '<ul class="incoming">\n' + "\n".join(items) + "\n</ul>\n"
    )
