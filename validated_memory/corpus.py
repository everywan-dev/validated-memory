"""The normalized reading of the curated corpus, built once per render.

`render` reads the units, the verdict log and the adopter's declared
extension; every consumer of that reading -- the overview's counts, the map,
the unprobed queue, the unit cards -- works from this one object instead of
walking the documents again. Building it once is what keeps the parts of the
page from disagreeing: the number in a count, the group a unit is listed
under and the badge on its card are the same value read from the same place.

`extension` is carried and not read. It is the adopter's declared schema,
loaded once by `validate.collect_and_validate`; nothing in the views renders
a declared field, because the design fixes the card's order and does not list
them. It is here so that a reader of this model has the schema in hand
without loading it a second time and risking a different answer from the
validation that just ran.

Everything here is a pure function of the documents, the log and the
extension. No clock, no `hash()` -- which is salted per process -- and no set
iteration order reaches the output: every sequence returned is sorted by
plain codepoint, with no locale and no collation, so the same corpus renders
the same bytes on every machine.

The documents reaching this module are validated: `render.build_artifacts`
gates on an ERROR finding before it builds anything. So `id` is present and
unique, `evidence` is one of `contract.EVIDENCE_STATES`, an anchor's `system`
and `kind` are strings and its `payload` a mapping, and a `rationale`, where
present, has the shape the contract fixes. That is why values are read
directly here, unlike `memory_view`, which renders a layer nothing validated.
The one exception is anchor key construction, which uses `.get()` exactly as
`derive.unit_verdict` does: the two must build byte-identical keys or a
lookup misses silently, so they read the mapping the same way.
"""

import json
import re
from collections import namedtuple

from . import derive, verdicts
from . import memory as memory_module
from .contract import EVIDENCE_STATES
from .frontmatter import parse as parse_frontmatter

HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)

# The counts table's axes, in the order the page draws them. Both come from
# the domains the rest of the codebase already fixes, so a new evidence state
# or a new verdict grows the table without touching the renderer.
COUNT_ROWS = EVIDENCE_STATES
COUNT_COLUMNS = verdicts.VERDICTS

# The label carried by the group of units with no anchors. With no id on a
# group, a label is the only thing telling two groups apart on the page, so
# it says what the group IS rather than borrowing a word an adopter's system
# may legitimately be called: a corpus with a system named `unclassified`
# renders that group and this one distinctly, one after the other.
UNCLASSIFIED = "unclassified (no anchors)"

# One group of the map: `name` is the label, `units` the ids linked from it,
# sorted. There is no id: nothing links to a group, and a system name is
# arbitrary text with no business in a DOM id.
Group = namedtuple("Group", "name units")

# One unit, read once.
#
# `state` is `derive.effective_states`' string: "active", or
# "superseded by <ids>". `graded` is a `derive.UnitVerdict`. `systems` is the
# distinct `anchors[].system` values this unit declares, sorted -- several
# anchors on one system count once, which is what makes the map's grouping
# single-valued per group.
Unit = namedtuple("Unit", "unit_id state data headline body graded systems")

# The whole corpus, read once.
#
# `units` is keyed by id; `active` and `superseded` partition its keys, both
# sorted. `extension` is the adopter's declared schema, carried and not read
# (see the module docstring). `view` is the verdict service view -- the latest verdict per anchor
# key -- and is carried rather than folded into `graded` because "this anchor
# has no record at all" and "this anchor's record says unknown" are different
# facts, and the unprobed queue needs the first one. `history` is the log
# grouped by anchor key, oldest first; `record_total` is the log's own total,
# which is larger than the sum of the groups whenever the log outlives the
# corpus.
Corpus = namedtuple(
    "Corpus",
    "basis units active superseded extension view history record_total",
)


def headline(body_text, unit_id):
    """The first heading of the body, or the id when there is none.

    THIS IS THE BOUNDARY AND IT IS CLOSED. Extracting one line by a
    documented rule is not rendering the body. "And the first paragraph too"
    would be, and the design rejects it: bodies are shown verbatim.

    "First" is by line position in the raw text, not by Markdown structure:
    `HEADING_PATTERN` has no notion of a fenced code block, so a `#` line
    inside one, ahead of the real heading, becomes the headline. This is a
    known consequence of the rule as stated, not a bug -- rendering fenced
    code differently would mean parsing the body's structure, which is
    exactly what a verbatim block does not do.

    It lives here, not in the view, because two renderers need it: the card
    and the map's links, which would otherwise be a column of bare ids.
    """
    match = HEADING_PATTERN.search(body_text)
    return match.group(1) if match else unit_id


def build(documents, basis, extension, records, view):
    """Return the whole normalized reading of one corpus.

    `records` and `view` are two projections of one `verdicts.read()`
    snapshot, which validates every record it returns -- so `_group_history`
    below never sees a record that would have been refused, such as one
    carrying an explicit `payload: null`.
    """
    states = derive.effective_states(documents)
    bodies = {}
    for _location, text in documents:
        bodies[parse_frontmatter(text)["id"]] = memory_module.body(text)

    units = {}
    for unit_id in sorted(states):
        data, state = states[unit_id]
        anchors = data.get("anchors") or []
        body = bodies.get(unit_id, "")
        units[unit_id] = Unit(
            unit_id=unit_id,
            state=state,
            data=data,
            headline=headline(body, unit_id),
            body=body,
            graded=derive.unit_verdict(unit_id, anchors, view),
            # `isinstance` rather than a bare set comprehension: validation
            # guarantees a string here, but `sorted` over a set holding a
            # `None` raises `TypeError`, and a renderer must not be one
            # contract change away from a traceback on a reader's page.
            systems=tuple(
                sorted(
                    {
                        anchor.get("system")
                        for anchor in anchors
                        if isinstance(anchor.get("system"), str)
                    }
                )
            ),
        )

    return Corpus(
        basis=basis,
        units=units,
        active=tuple(uid for uid in sorted(units) if units[uid].state == "active"),
        superseded=tuple(
            uid for uid in sorted(units) if units[uid].state != "active"
        ),
        extension=extension,
        view=view,
        history=_group_history(records),
        record_total=len(records),
    )


def _group_history(records):
    """Group every record that names an anchor by the key it names.

    `records` reaches here only after `service_view()` has validated the
    whole log without raising (see `build`'s docstring for where and why),
    so every record here is already known to carry `unit`, `system` and
    `kind` as strings and, when present, `payload` as a mapping -- which is
    why they are indexed directly (`record["unit"]` and friends) rather than
    with `.get()`.

    A record with no `payload` field predates payloads and is read by no
    anchor -- see `verdicts.NO_PAYLOAD` -- so it never joins a group here.
    It is still counted in `record_total`, since that total is the log's own,
    not an anchor's; it just never counts toward an anchor's. Grouping
    preserves `records`' file order, so each group is oldest-first; the
    renderer reverses only the slice it shows.
    """
    grouped = {}
    for record in records:
        if "payload" not in record:
            continue
        key = verdicts.anchor_key(
            record["unit"], record["system"], record["kind"], record["payload"]
        )
        grouped.setdefault(key, []).append(record)
    return grouped


def counts(corpus):
    """Active units counted by evidence state crossed with aggregate verdict.

    Active only, following `status`, which grades exactly the active set
    (`validated_memory/status.py:143-165`): a superseded unit is not probed,
    so a verdict for it is a number nobody can act on. The cross itself is
    new -- `status` counts verdicts, not the pairing. Superseded units are
    reported as one separate figure, `len(corpus.superseded)`, and never
    folded in here, so the two populations cannot be added up by accident.

    Dense over the full product of `COUNT_ROWS` and `COUNT_COLUMNS`, so a
    zero cell is a real zero and the table's shape is a function of the
    domains rather than of the corpus.
    """
    table = {
        (evidence, verdict): 0
        for evidence in COUNT_ROWS
        for verdict in COUNT_COLUMNS
    }
    for unit_id in corpus.active:
        unit = corpus.units[unit_id]
        table[(unit.data["evidence"], unit.graded.verdict)] += 1
    return table


def groups(corpus):
    """The map's groups: active units by `anchors[].system`, unclassified last.

    The axis is ALWAYS `anchors[].system`. A declared extension enum cannot
    take it over: declaring a second enum is an additive schema change that
    does not even bump the schema version
    (`docs/reference/curated-knowledge.md`), so the page would silently
    reorganize itself the moment an adopter added an unrelated field. A page
    that rearranges itself on a change nobody made to it is worse than a page
    grouped by a coarser axis.

    Multi-valued, and well defined because the map is a navigation index and
    not a second rendering: a unit anchored in three systems is a link in
    three groups while its card is still rendered exactly once. Several
    anchors on the same system count once for that unit -- `Unit.systems` is
    already the distinct set.

    A unit with no anchors goes to an explicit `unclassified (no anchors)`
    group rather than being dropped: a unit that cannot expire is a fact
    about the corpus. That group is always emitted last, whatever its label
    sorts as, and it is built separately from the system groups -- so an
    adopter whose corpus has a system called `unclassified` gets two groups,
    labelled differently, and not a merge.

    No group carries a DOM id. Nothing links to one, and `anchors[].system`
    is validated only as a non-empty string, so a system name can hold
    whitespace or a URL: neither belongs in an id.

    Active units only. A superseded unit stays reachable from the card of the
    unit that superseded it, which is where the view already nests it.
    Groups by name, units within a group by id, both plain codepoint sorts.
    """
    by_system = {}
    unclassified = []
    for unit_id in corpus.active:
        systems = corpus.units[unit_id].systems
        if not systems:
            unclassified.append(unit_id)
            continue
        for system in systems:
            by_system.setdefault(system, []).append(unit_id)

    result = [
        Group(system, tuple(sorted(by_system[system])))
        for system in sorted(by_system)
    ]
    if unclassified:
        result.append(Group(UNCLASSIFIED, tuple(sorted(unclassified))))
    return tuple(result)


def unprobed(corpus):
    """Anchors of active units with no record under their current key.

    The key is the one `verdicts.anchor_key` builds -- `(unit, system, kind,
    payload)` -- so an anchor whose payload changed has no record under its
    new key and is listed here. That is the honest reading: the old record
    describes something the anchor no longer points at. Membership in
    `corpus.view` is what is tested, not the graded verdict: an anchor whose
    latest record says `unknown` HAS been probed, and a queue that conflated
    the two would ask someone to re-run a probe that already answered.

    Active units only, for the reason the counts are: `probe` probes the
    anchors of active units alone (`derive.effective_states`), so a
    superseded unit's anchor would be a queue item nobody can drain.

    Ordered by unit id, then system, then kind, then the payload's canonical
    JSON -- all plain codepoint sorts, so the queue is a function of the
    corpus and not of dictionary order.
    """
    rows = []
    for unit_id in corpus.active:
        for key, anchor in anchor_rows(corpus, unit_id):
            if key in corpus.view:
                continue
            rows.append(
                (unit_id, anchor.get("system"), anchor.get("kind"), anchor.get("payload"))
            )
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row[0], str(row[1]), str(row[2]), canonical_payload(row[3])
            ),
        )
    )


def anchor_rows(corpus, unit_id):
    """This unit's anchors as `(key, anchor)` pairs, in declaration order.

    The key is the one `derive.unit_verdict` and `verdicts.anchor_key` build
    -- what the anchor points at, payload included -- computed here once so
    the card, the unprobed queue and the verdict all read the same string.
    Two anchors that happen to share every field share a key and therefore a
    history; that is a true fact about the log, not a bug in this grouping.
    """
    rows = []
    for anchor in corpus.units[unit_id].data.get("anchors") or []:
        rows.append(
            (
                verdicts.anchor_key(
                    unit_id,
                    anchor.get("system"),
                    anchor.get("kind"),
                    anchor.get("payload"),
                ),
                anchor,
            )
        )
    return rows


def canonical_payload(payload):
    """A payload as the page writes it: JSON with sorted keys.

    A reader of the page has no Python. This is the same deterministic form
    `verdicts._canonical` keys a record with and `probe` writes into the log
    the page also displays -- `str` would show Python's single-quoted repr,
    `{'ref': 'main'}`, which is neither JSON nor what the log holds.
    """
    return json.dumps(payload, sort_keys=True)
