"""Normalize validated knowledge documents and one verdict snapshot for rendering.

Counts, groups, queues and cards share this reading. The declared extension
is carried without rendering its fields. No clock or hash ordering enters
the output: presentations sort by codepoint, anchors keep declaration order,
and history keeps log file order. Anchor keys must match `derive.unit_verdict`.
"""

import re
from collections import namedtuple

from . import derive, verdicts
from . import memory as memory_module
from .contract import EVIDENCE_STATES
from .frontmatter import parse as parse_frontmatter

HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)

# Counts use the full declared domains in presentation order.
COUNT_ROWS = EVIDENCE_STATES
COUNT_COLUMNS = verdicts.VERDICTS

# Distinguish anchorless units from a system named `unclassified`.
UNCLASSIFIED = "unclassified (no anchors)"

# `name` is display text, never a DOM id; `units` contains sorted unit ids.
Group = namedtuple("Group", "name units")

# `state`: active or "superseded by <ids>"; `graded`: derive.UnitVerdict.
# `systems` contains distinct anchor systems sorted by codepoint.
Unit = namedtuple("Unit", "unit_id state data headline body graded systems")

# `active` and `superseded` partition `units` ids, sorted.
# `view` distinguishes absent records from unknown verdicts. `history` groups
# payload-bearing records, including orphan keys, in file order; `record_total`
# also counts legacy records without payloads, which join no history group.
Corpus = namedtuple(
    "Corpus",
    "basis units active superseded extension view history record_total",
)


def headline(body_text, unit_id):
    """Return the first raw-text heading, including inside fences, or the id.

    Extract only this line; bodies remain verbatim, not rendered Markdown.
    """
    match = HEADING_PATTERN.search(body_text)
    return match.group(1) if match else unit_id


def build(documents, basis, extension, records, view):
    """Build from validated documents and one snapshot's `records` and `view`.

    `verdicts.read` validates the entire log, including rejecting null payloads.
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
    """Group validated payload-bearing records by anchor key in file order.

    Missing-payload records count only in the log total. Orphan keys still
    get groups; rendered-anchor membership is the view's separate count.
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
    """Count active units over the full evidence/verdict product, including zeros."""
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
    """Group active units by anchor system, then append the anchorless group.

    A unit appears once per distinct system. Sort groups by name and their
    units by id. Do not substitute extension enums: additive schema changes
    must not reorganize the map. Arbitrary system names are labels, not DOM ids.
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
    """Return active anchors with no record under their payload-inclusive key.

    A recorded unknown verdict is probed. Sort by unit id, system, kind,
    then canonical payload JSON, all by codepoint.
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
                row[0], str(row[1]), str(row[2]), verdicts.canonical_payload(row[3])
            ),
        )
    )


def anchor_rows(corpus, unit_id):
    """Return `(key, anchor)` pairs in declaration order.

    Equal payload-inclusive keys share a history, including duplicate anchors.
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
