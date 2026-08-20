"""Builds `memory.html`: the agent-memory layer, one entry per file.

Unlike `knowledge_view`, this layer has no `gated_source` -- `lint` is the
rules on top of `memory.py`'s reading, and this module is not `lint`. So this
view does not enforce: every document collected gets an entry, an entry with
no line in the index still appears, an unresolved wikilink is marked rather
than dropped, and a document whose frontmatter will not parse is rendered
with the parse error stated in place of the fields that could not be read.
Hiding a record because `lint` would complain about it would make the page
lie about what the repository holds, and `lint` is one command away.

Nothing validated these values either, so nothing here may assume their
type: a `description` can be a list, `metadata.type` a mapping, `name` a
number. Every value that reaches the page goes through `html.escape_text` or
`html.escape_attribute`, both of which stringify first, and no membership
test, sort or `.strip()` touches a frontmatter value directly -- the few
operations that need one (`is_declared`, `wikilinks`, `supersession`) are the
reader functions in `memory.py` that already guard with `isinstance`.
"""

from collections import namedtuple

from . import html
from . import memory as memory_module
from .frontmatter import FrontmatterError
from .frontmatter import parse as parse_frontmatter

TITLE = "Agent memory"

# One document as read for this page.
#
# `filename` is the canonical identity (ADR 0001), always present -- it is a
# plain path computation, never touched by unparsed or mistyped frontmatter.
# `identity` is what a resolved wikilink targets: `name` when it is declared,
# `filename` otherwise; `declared` says which. A document with no declared
# name can never be a resolution target (`memory.resolution` excludes it
# from `by_name`), so nothing a reference resolves to ever names the
# fallback -- but the entry still needs an anchor of its own to sit at on
# the page. `anchor` is that DOM id (see `_anchor`): for a declared entry it
# is built from `identity` (the name); for one with no declared name it is
# built from `document.relpath`, not from `identity` (the filename) --
# `identity` can repeat across subdirectories where `relpath` cannot, so the
# anchor and `identity` deliberately part ways there too.
#
# `error` is set when the frontmatter would not parse; then `data`, `body`,
# `targets` and `marker` are never populated, because deriving them needs the
# frontmatter (or, for `body`, needs the closing fence `memory.body` assumes
# is there).
_Record = namedtuple(
    "_Record",
    "document filename identity declared anchor data body targets marker error",
)


def _anchor(value, declared):
    """The DOM id an entry sits at.

    Disjoint by construction, not by hoping the two inputs differ: a
    declared name anchors as `entry-name-<name>`, an entry with no declared
    name anchors as `entry-path-<relpath>`, and `name-` and `path-` are
    fixed tokens that differ at the first character right after the shared
    `entry-` prefix. No string either scheme can produce is ever a string
    the other can produce, whatever `value` holds. That is what
    `entry-<value>` for both, with a `file-` prefix folded into `value` on
    the fallback side, did not guarantee: `_anchor('alpha', False)` (no
    declared name, filename `alpha`) and `_anchor('file-alpha', True)`
    (declared `name: file-alpha`) both landed on `entry-file-alpha`,
    because the discriminator lived inside the same string space it was
    supposed to keep separate.

    Each namespace still has to be injective on its own inputs for the
    anchor to be unique, and only one of the two can make that promise
    unconditionally. The no-declared-name scheme is keyed on `relpath`,
    not the bare filename: `relpath` -- the document's path relative to
    the memory directory (`memory.documents` builds it as
    `candidate.relative_to(target)`) -- is unique by construction across
    every document `memory` reads, no matter what the corpus contains,
    unlike the filename, which two memories in different subdirectories
    may share (`lint` reports that as its own finding).

    The declared-name scheme has no such guarantee, and does not claim
    one. `memory.resolution` builds `by_name` as a plain set of declared
    names (`{name for name in declared.values() if is_declared(name)}`),
    with no uniqueness check -- that check exists only on the separate
    `by_filename` table, over a different key. Two documents can declare
    the same `name`, and both then anchor at the identical
    `entry-name-<name>`: a real duplicate id inside this one namespace.
    `lint` reports a duplicate declared name as an ERROR; this view
    renders it anyway, per the "does not enforce" rule at the top of this
    module. That is a known, accepted consequence of not enforcing, not
    something the anchor scheme guards against -- and it predates this
    fix, which only closed the *cross-scheme* collision. No anchor
    spelling could close this one instead: with two entries claiming one
    name, a `[[wikilink]]` to it is already ambiguous before an id is
    ever built, and inventing a disambiguated anchor would let the page
    imply a resolution `lint` does not make.
    """
    if declared:
        return f"entry-name-{value}"
    return f"entry-path-{value}"


def build(documents, basis, resolution):
    """Return the whole page as a string.

    `basis` is the path the memory files were read under, in the same form
    `knowledge_view.build` reports for the curated layer, so the two basis
    lines agree on what "basis" discloses.
    """
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
        # Only a record whose identity is itself a declared name can be the
        # target `_incoming_map` indexed anything under -- a fallback
        # identity (a filename) is never in `resolution.by_name`, so looking
        # it up here regardless would risk a coincidental match: some other
        # document's real name happening to equal this one's filename.
        referrers = incoming.get(record.identity, []) if record.declared else []
        parts.append(_entry_section(record, resolution, referrers))
    return html.page(TITLE, "\n".join(parts))


def _read(document):
    """Parse one document into a `_Record`, never raising.

    Called once per document, independently of whatever resolution the
    caller already computed -- parsing frontmatter is a pure function of the
    text with no ambiguity to disagree with `lint` about, unlike resolving a
    reference, which is why `resolution` is taken as an argument instead.
    """
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
    # The anchor is keyed on `identity` (the name) when declared, but on
    # `document.relpath` -- not `identity` -- when it falls back: `identity`
    # is the filename there, and a filename is not unique across
    # subdirectories the way `relpath` is (see `_anchor`).
    anchor = (
        _anchor(identity, declared)
        if declared
        else _anchor(document.relpath, declared)
    )
    body = memory_module.body(document.text)
    description = data.get("description")
    marker = memory_module.supersession(description)
    scan_text = marker.remainder if marker is not None else description
    # `wikilinks` guards non-string input itself (returns `[]`), so this is
    # safe to call on `description` whatever its type turned out to be.
    targets = _ordered_unique(
        memory_module.wikilinks(scan_text) + memory_module.wikilinks(body)
    )
    return _Record(
        document, filename, identity, declared, anchor,
        data, body, targets, marker, None,
    )


def _ordered_unique(items):
    """De-duplicate `items` (already known to be strings), first seen first."""
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _incoming_map(records, resolution):
    """Map each declared name to the records whose outgoing targets name it.

    Built once over every record, restricted to targets `resolution.by_name`
    recognises -- the same set `_outgoing_list` and `_supersession_html` test
    membership against, so a wikilink the outgoing side marks unresolved can
    never turn into a live incoming reference on the other end. Looked up by
    `identity`, so an entry's incoming list is exactly "which other entries
    carry this identity among their outgoing targets" -- the mirror image of
    the outgoing list, with no separate notion of what counts as a
    reference.
    """
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
        # `marker.target` is a declared name, checked against `by_name` on
        # the line above -- `_anchor`'s declared branch, same as every other
        # link built from a resolved target.
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
