"""Pure semantic inspection of one coherent pair of journal histories.

This is the sole owner of schema-2 history topology.  Filesystem acquisition
stays in :mod:`records`; callers hand :func:`inspect` the exact descriptor-bound
bytes and receive immutable facts, conditions, and capabilities.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .records import (
    COMMITTED,
    COMMON_FIELDS,
    DURABILITIES,
    LOCAL,
    OBSERVE,
    OPS,
    OPTIONAL_FIELD_TYPES,
    PREPARED,
    REPO,
    STAGES,
    RawHistoryPair,
    is_inside_path,
)


_SCHEMA2_REQUIRED = COMMON_FIELDS + (
    "kind",
    "frontier",
    "node",
    "topology_unknown_before",
)
_SCHEMA2_OPTIONAL = tuple(OPTIONAL_FIELD_TYPES) + ("transaction",)
_SCHEMA2_FIELDS = frozenset(_SCHEMA2_REQUIRED + _SCHEMA2_OPTIONAL)
_DIGEST_PREFIX = "sha256:"
_NO_SNAPSHOT = frozenset(
    {
        "malformed_record",
        "schema_order_damage",
        "invalid_frontier",
        "node_digest_mismatch",
        "duplicate_stage_conflict",
        "invalid_stage_multiplicity",
        "mutation_pair_disagreement",
        "transaction_identity_damage",
        "same_artifact_ancestry_missing",
        "topology_cycle",
        "topology_unknown_flag_damage",
        "lineage_transition",
    }
)
_PAIRED_LEGACY_FIELDS = (
    "op",
    "purpose",
    "path",
    "durability",
    "preimage",
    "postimage",
    "note",
    "prior_bytes",
    "mode",
)


@dataclass(frozen=True, order=True)
class FrontierReference:
    kind: str
    artifact: str
    digest: str


@dataclass(frozen=True, order=True)
class SourceLocation:
    artifact: str
    line: int


@dataclass(frozen=True)
class Condition:
    code: str
    level: str
    artifact: str | None = None
    locations: tuple[SourceLocation, ...] = ()
    node: str | None = None
    transaction: str | None = None
    reference: FrontierReference | None = None
    reason: str | None = None
    fields: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()


@dataclass(frozen=True)
class Capabilities:
    appendable: bool
    reversal_ready: bool
    identity_available: bool
    topology_known_before: bool
    ancestry_available: bool


@dataclass(frozen=True)
class ArtifactSnapshot:
    artifact: str
    present: bool
    mode: int | None
    generation: tuple[int, int, int, int, int, int] | None
    length: int | None
    digest: str | None
    physical_count: int


@dataclass(frozen=True)
class LegacyAnchor:
    reference: FrontierReference
    length: int
    physical_count: int
    adoption_ids: tuple[str, ...]


@dataclass(frozen=True, repr=False)
class _SnapshotToken:
    repository: tuple[Any, ...]
    local: tuple[Any, ...]


@dataclass(frozen=True)
class _TopologyState:
    edges: tuple[tuple[FrontierReference, FrontierReference], ...]


@dataclass(frozen=True)
class HistorySnapshot:
    token: _SnapshotToken = field(repr=False)
    artifacts: tuple[ArtifactSnapshot, ...]
    anchors: tuple[LegacyAnchor, ...]
    heads: tuple[FrontierReference, ...]
    physical_count: int
    adoption_ids: tuple[str, ...]
    active_adoption: str | None
    _topology: _TopologyState = field(repr=False)


@dataclass(frozen=True)
class Inspection:
    snapshot: HistorySnapshot | None
    conditions: tuple[Condition, ...]
    capabilities: Capabilities


@dataclass
class _Occurrence:
    artifact: str
    line: int
    entry: dict[str, Any]

    @property
    def location(self) -> SourceLocation:
        return SourceLocation(self.artifact, self.line)


@dataclass
class _Artifact:
    name: str
    raw: Any
    records: list[_Occurrence]
    legacy: list[_Occurrence]
    schema2: list[_Occurrence]
    anchor: LegacyAnchor | None
    snapshot: ArtifactSnapshot


@dataclass
class _Node:
    digest: str
    artifact: str
    entry: dict[str, Any]
    locations: tuple[SourceLocation, ...]
    frontier: tuple[FrontierReference, ...]
    kind: str
    valid: bool

    @property
    def reference(self) -> FrontierReference:
        return FrontierReference("node", self.artifact, self.digest)


class _DuplicateKey(ValueError):
    def __init__(self, key: str):
        self.key = key


class _NonstandardConstant(ValueError):
    def __init__(self, value: str):
        self.value = value


class _Conditions:
    def __init__(self) -> None:
        self._items: dict[tuple[Any, ...], Condition] = {}

    def add(
        self,
        code: str,
        *,
        artifact: str | None = None,
        locations: tuple[SourceLocation, ...] = (),
        node: str | None = None,
        transaction: str | None = None,
        reference: FrontierReference | None = None,
        reason: str | None = None,
        fields: tuple[str, ...] = (),
        subjects: tuple[str, ...] = (),
    ) -> None:
        level = "warning" if code in {
            "topology_unknown_before",
            "cross_artifact_ancestry_unavailable",
        } else "error"
        fields = tuple(sorted(set(fields)))
        subjects = tuple(dict.fromkeys(subjects))
        key = (
            code,
            level,
            artifact,
            node,
            transaction,
            reference,
            reason,
            fields,
            subjects,
        )
        old = self._items.get(key)
        merged = tuple(sorted(set(locations + (() if old is None else old.locations)), key=_location_key))
        self._items[key] = Condition(
            code,
            level,
            artifact,
            merged,
            node,
            transaction,
            reference,
            reason,
            fields,
            subjects,
        )

    def result(self) -> tuple[Condition, ...]:
        return tuple(sorted(self._items.values(), key=_condition_key))


def _artifact_rank(value: str | None) -> int:
    return {REPO: 0, LOCAL: 1, None: 2}[value]


def _location_key(value: SourceLocation) -> tuple[int, int]:
    return (_artifact_rank(value.artifact), value.line)


def _reference_key(value: FrontierReference) -> tuple[int, int, str]:
    return (_artifact_rank(value.artifact), 0 if value.kind == "legacy" else 1, value.digest)


def _reference_subject(value: FrontierReference) -> str:
    return f"reference:{value.kind}:{value.artifact}:{value.digest}"


def _condition_key(value: Condition) -> tuple[Any, ...]:
    reference = value.reference
    reference_key = (2, 2, "") if reference is None else _reference_key(reference)
    return (
        0 if value.level == "error" else 1,
        value.code,
        _artifact_rank(value.artifact),
        value.node or "",
        reference_key,
        value.transaction or "",
        value.reason or "",
        value.fields,
        value.subjects,
    )


def _sha(data: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(data).hexdigest()


def _token_part(name: str, raw: Any) -> tuple[Any, ...]:
    return (name, raw.data is not None, raw.data, raw.generation, raw.mode)


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _strict_constant(value: str) -> Any:
    raise _NonstandardConstant(value)


def _malformed(
    conditions: _Conditions,
    artifact: str,
    line: int | None,
    reason: str,
    *,
    fields: tuple[str, ...] = (),
    subjects: tuple[str, ...] = (),
) -> None:
    locations = () if line is None else (SourceLocation(artifact, line),)
    conditions.add(
        "malformed_record",
        artifact=artifact,
        locations=locations,
        reason=reason,
        fields=fields,
        subjects=subjects,
    )


def _valid_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(_DIGEST_PREFIX)
        and len(value) == 71
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _wrong_type(value: Any, expected: type | tuple[type, ...]) -> bool:
    types = expected if isinstance(expected, tuple) else (expected,)
    return (int in types and isinstance(value, bool)) or not isinstance(value, types)


def _validate_common(
    entry: dict[str, Any], artifact: str
) -> tuple[str, tuple[str, ...]] | None:
    missing = tuple(field for field in COMMON_FIELDS if field not in entry)
    if missing:
        return "missing_field", missing
    common_types = {
        "schema": int,
        "at": str,
        "version": str,
        "adoption": str,
        "run": str,
        "durability": str,
        "op": str,
        "purpose": str,
        "path": str,
        "stage": str,
    }
    for field, expected in common_types.items():
        if _wrong_type(entry[field], expected):
            return "invalid_type", (field,)
    for field, expected in OPTIONAL_FIELD_TYPES.items():
        if field in entry and _wrong_type(entry[field], expected):
            return "invalid_type", (field,)
    for field, valid in (
        ("op", entry["op"] in OPS),
        ("stage", entry["stage"] in STAGES),
        ("durability", entry["durability"] == artifact),
        ("path", artifact != REPO or is_inside_path(entry["path"])),
    ):
        if not valid:
            return "invalid_domain", (field,)
    return None


def _decode_frontier(entry: dict[str, Any]) -> tuple[tuple[FrontierReference, ...] | None, str | None, tuple[str, ...]]:
    value = entry["frontier"]
    if not isinstance(value, list):
        return None, "invalid_shape", ()
    references: list[FrontierReference] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"kind", "artifact", "digest"}:
            return None, "invalid_reference", ()
        if (
            item["kind"] not in {"legacy", "node"}
            or item["artifact"] not in DURABILITIES
            or not _valid_digest(item["digest"])
        ):
            return None, "invalid_reference", ()
        references.append(FrontierReference(item["kind"], item["artifact"], item["digest"]))
    if len(set(references)) != len(references):
        return None, "duplicate_reference", tuple(
            _reference_subject(item) for item in sorted(set(references), key=_reference_key)
        )
    if references != sorted(references, key=_reference_key):
        return None, "noncanonical_order", tuple(
            _reference_subject(item) for item in sorted(references, key=_reference_key)
        )
    return tuple(references), None, ()


def _validate_schema2(entry: dict[str, Any], artifact: str) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    missing = tuple(field for field in _SCHEMA2_REQUIRED if field not in entry)
    if missing:
        return "missing_field", missing, ()
    unknown = tuple(sorted(set(entry) - _SCHEMA2_FIELDS))
    if unknown:
        return "unknown_field", unknown, ()
    common = _validate_common(entry, artifact)
    if common is not None:
        return common[0], common[1], ()
    typed = {
        "kind": str,
        "frontier": list,
        "node": str,
        "topology_unknown_before": bool,
    }
    for field, expected in typed.items():
        if not isinstance(entry[field], expected):
            return "invalid_type", (field,), ()
    if entry["schema"] != 2:
        return "invalid_domain", ("schema",), ()
    if entry["kind"] not in {"mutation", "observation", "activation"}:
        return "invalid_domain", ("kind",), ()
    if not _valid_digest(entry["node"]):
        return "invalid_domain", ("node",), ()
    frontier, reason, subjects = _decode_frontier(entry)
    if reason is not None:
        return "frontier:" + reason, (), subjects
    entry["_frontier"] = frontier
    kind = entry["kind"]
    forbidden = {"preimage", "postimage", "prior_bytes", "mode", "transaction"}
    if kind == "mutation":
        if entry["op"] == OBSERVE:
            return "invalid_domain", ("op",), ()
        if not isinstance(entry.get("transaction"), str):
            return ("missing_field" if "transaction" not in entry else "invalid_type"), ("transaction",), ()
    else:
        if entry["op"] != OBSERVE:
            return "invalid_domain", ("op",), ()
        if not isinstance(entry.get("note"), str):
            return ("missing_field" if "note" not in entry else "invalid_type"), ("note",), ()
        present = tuple(sorted(forbidden.intersection(entry)))
        if present:
            return "invalid_domain", present, ()
    return None


def _parse_artifact(name: str, raw: Any, conditions: _Conditions) -> _Artifact:
    if raw.error is not None:
        raise ValueError("a raw failure is not a coherent history pair")
    present = raw.data is not None
    data = b"" if raw.data is None else raw.data
    snapshot = ArtifactSnapshot(
        name,
        present,
        raw.mode if present else None,
        raw.generation if present else None,
        len(data) if present else None,
        _sha(data) if present else None,
        0,
    )
    if not present:
        return _Artifact(name, raw, [], [], [], None, snapshot)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        _malformed(conditions, name, None, "invalid_utf8")
        return _Artifact(name, raw, [], [], [], None, snapshot)
    if data and not data.endswith(b"\n"):
        _malformed(conditions, name, None, "missing_final_lf")
        return _Artifact(name, raw, [], [], [], None, snapshot)

    records: list[_Occurrence] = []
    legacy: list[_Occurrence] = []
    schema2: list[_Occurrence] = []
    first_schema2_offset: int | None = None
    offset = 0
    for line_number, physical in enumerate(data.splitlines(keepends=True), 1):
        start = offset
        offset += len(physical)
        line = physical[:-1]
        if line.endswith(b"\r"):
            line = line[:-1]
        if not line.strip():
            continue
        try:
            compatibility = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            _malformed(conditions, name, line_number, "invalid_json")
            break
        if not isinstance(compatibility, dict):
            _malformed(conditions, name, line_number, "non_object")
            break
        schema = compatibility.get("schema")
        if "schema" not in compatibility:
            _malformed(conditions, name, line_number, "missing_field", fields=("schema",))
            break
        if isinstance(schema, bool) or not isinstance(schema, int):
            _malformed(conditions, name, line_number, "invalid_type", fields=("schema",))
            break
        if schema > 2:
            _malformed(conditions, name, line_number, "invalid_domain", fields=("schema",))
            break
        entry = compatibility
        if schema == 2:
            if first_schema2_offset is None:
                first_schema2_offset = start
            try:
                entry = json.loads(
                    line.decode("utf-8"),
                    object_pairs_hook=_strict_pairs,
                    parse_constant=_strict_constant,
                )
            except _DuplicateKey as error:
                _malformed(conditions, name, line_number, "duplicate_key", fields=(error.key,))
                break
            except _NonstandardConstant as error:
                _malformed(conditions, name, line_number, "nonstandard_constant", subjects=(error.value,))
                break
            result = _validate_schema2(entry, name)
            if result is not None:
                reason, fields, subjects = result
                if reason.startswith("frontier:"):
                    conditions.add(
                        "invalid_frontier",
                        artifact=name,
                        locations=(SourceLocation(name, line_number),),
                        node=entry.get("node") if isinstance(entry.get("node"), str) else None,
                        reason=reason.split(":", 1)[1],
                        fields=fields,
                        subjects=subjects,
                    )
                    entry["_frontier"] = ()
                    entry["_shape_invalid"] = True
                    occurrence = _Occurrence(name, line_number, entry)
                    records.append(occurrence)
                    schema2.append(occurrence)
                    continue
                else:
                    _malformed(conditions, name, line_number, reason, fields=fields, subjects=subjects)
                break
            occurrence = _Occurrence(name, line_number, entry)
            records.append(occurrence)
            schema2.append(occurrence)
        else:
            if first_schema2_offset is not None:
                conditions.add(
                    "schema_order_damage",
                    artifact=name,
                    locations=(SourceLocation(name, line_number),),
                    reason="legacy_after_schema_2",
                )
                break
            result = _validate_common(entry, name)
            if result is not None:
                _malformed(conditions, name, line_number, result[0], fields=result[1])
                break
            occurrence = _Occurrence(name, line_number, entry)
            records.append(occurrence)
            legacy.append(occurrence)

    snapshot = ArtifactSnapshot(
        name,
        True,
        raw.mode,
        raw.generation,
        len(data),
        _sha(data),
        len(records),
    )
    anchor_bytes = data[:first_schema2_offset] if first_schema2_offset is not None else data
    anchor = None
    if anchor_bytes:
        ids = tuple(sorted({item.entry["adoption"] for item in legacy}))
        anchor = LegacyAnchor(
            FrontierReference("legacy", name, _sha(anchor_bytes)),
            len(anchor_bytes),
            len(legacy),
            ids,
        )
    return _Artifact(name, raw, records, legacy, schema2, anchor, snapshot)


def _canonical_node(entry: dict[str, Any]) -> str:
    projection = {
        key: value
        for key, value in entry.items()
        if key != "node" and not key.startswith("_")
    }
    encoded = json.dumps(
        projection,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha(encoded)


def _locations(items: list[_Occurrence] | tuple[_Occurrence, ...]) -> tuple[SourceLocation, ...]:
    return tuple(sorted({item.location for item in items}, key=_location_key))


def _occurrence_key(item: _Occurrence) -> str:
    return json.dumps(
        {key: value for key, value in item.entry.items() if not key.startswith("_")},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _build_nodes(artifacts: tuple[_Artifact, ...], conditions: _Conditions) -> list[_Node]:
    by_transaction: dict[tuple[str, str], list[_Occurrence]] = {}
    for artifact in artifacts:
        for occurrence in artifact.schema2:
            transaction = occurrence.entry.get("transaction")
            if occurrence.entry["kind"] == "mutation" and isinstance(transaction, str):
                by_transaction.setdefault((artifact.name, transaction), []).append(occurrence)
    for (artifact, transaction), occurrences in by_transaction.items():
        prepared = [item for item in occurrences if item.entry["stage"] == PREPARED]
        committed = [item for item in occurrences if item.entry["stage"] == COMMITTED]
        if len(prepared) == len(committed) == 1 and prepared[0].entry["node"] != committed[0].entry["node"]:
            conditions.add(
                "mutation_pair_disagreement",
                artifact=artifact,
                locations=_locations((prepared[0], committed[0])),
                transaction=transaction,
                fields=("node",),
            )
    grouped: dict[tuple[str, str], list[_Occurrence]] = {}
    for artifact in artifacts:
        for occurrence in artifact.schema2:
            grouped.setdefault(
                (artifact.name, occurrence.entry["node"]), []
            ).append(occurrence)
    nodes: list[_Node] = []
    for (_node_artifact, digest), occurrences in grouped.items():
        by_stage: dict[str, list[_Occurrence]] = {PREPARED: [], COMMITTED: []}
        for item in occurrences:
            by_stage[item.entry["stage"]].append(item)
        valid = True
        representatives: dict[str, _Occurrence] = {}
        distinct_by_stage: dict[str, list[_Occurrence]] = {}
        stage_counts: dict[str, int] = {}
        stage_conflict = False
        for stage, stage_items in by_stage.items():
            distinct: list[_Occurrence] = []
            for item in stage_items:
                if not any(item.entry == old.entry for old in distinct):
                    distinct.append(item)
            distinct.sort(key=_occurrence_key)
            distinct_by_stage[stage] = distinct
            if len(distinct) > 1:
                first = stage_items[0]
                conditions.add(
                    "duplicate_stage_conflict",
                    artifact=first.artifact,
                    locations=_locations(stage_items),
                    node=digest,
                    subjects=(stage,),
                )
                valid = False
                stage_conflict = True
            stage_counts[stage] = len(distinct)
            if len(distinct) == 1:
                representatives[stage] = distinct[0]
        semantic_occurrences = sorted(
            distinct_by_stage[PREPARED] + distinct_by_stage[COMMITTED],
            key=_occurrence_key,
        )
        carrier = (
            representatives.get(COMMITTED)
            or representatives.get(PREPARED)
            or semantic_occurrences[0]
        )
        if any(item.entry.get("_shape_invalid") for item in semantic_occurrences):
            valid = False
        kinds = {item.entry["kind"] for item in semantic_occurrences}
        if kinds == {"mutation"}:
            if set(representatives) != {PREPARED, COMMITTED}:
                conditions.add(
                    "invalid_stage_multiplicity",
                    artifact=carrier.artifact,
                    locations=_locations(occurrences),
                    node=digest,
                    transaction=carrier.entry.get("transaction"),
                    subjects=(
                        f"prepared:{stage_counts[PREPARED]}",
                        f"committed:{stage_counts[COMMITTED]}",
                    ),
                )
                valid = False
        elif set(representatives) != {COMMITTED}:
            conditions.add(
                "invalid_stage_multiplicity",
                artifact=carrier.artifact,
                locations=_locations(occurrences),
                node=digest,
                subjects=(
                    f"prepared:{stage_counts[PREPARED]}",
                    f"committed:{stage_counts[COMMITTED]}",
                ),
            )
            valid = False
        for prepared in distinct_by_stage[PREPARED]:
            for committed in distinct_by_stage[COMMITTED]:
                left = prepared.entry
                right = committed.entry
                if "mutation" not in {left["kind"], right["kind"]}:
                    continue
                fields = tuple(
                    sorted(
                        field
                        for field in set(left) | set(right)
                        if field not in {"at", "stage"}
                        and not field.startswith("_")
                        and (
                            (field in left) != (field in right)
                            or left.get(field) != right.get(field)
                        )
                    )
                )
                if fields:
                    transaction = carrier.entry.get("transaction")
                    if not isinstance(transaction, str):
                        transaction = next(
                            (
                                item.entry.get("transaction")
                                for item in (prepared, committed)
                                if item.entry["kind"] == "mutation"
                                and isinstance(item.entry.get("transaction"), str)
                            ),
                            None,
                        )
                    conditions.add(
                        "mutation_pair_disagreement",
                        artifact=carrier.artifact,
                        locations=_locations((prepared, committed)),
                        node=digest,
                        transaction=transaction,
                        fields=fields,
                    )
                    valid = False
        for committed in distinct_by_stage[COMMITTED]:
            expected = _canonical_node(committed.entry)
            if expected != digest:
                conditions.add(
                    "node_digest_mismatch",
                    artifact=committed.artifact,
                    locations=(committed.location,),
                    node=digest,
                    subjects=(f"computed:{expected}",),
                )
                valid = False
        # A conflicted stage has no semantic carrier. Its primitive evidence is
        # complete above, but choosing either representation would let physical
        # order decide graph kind or ancestry.
        if stage_conflict:
            continue
        nodes.append(
            _Node(
                digest,
                carrier.artifact,
                carrier.entry,
                _locations(occurrences),
                carrier.entry["_frontier"],
                carrier.entry["kind"],
                valid,
            )
        )
    return nodes


def _legacy_conditions(artifacts: tuple[_Artifact, ...], nodes: list[_Node], conditions: _Conditions) -> None:
    identities: dict[str, dict[str, tuple[SourceLocation, ...]]] = {}
    legacy_by_artifact: dict[str, dict[str, list[_Occurrence]]] = {}
    for artifact in artifacts:
        groups: dict[str, list[_Occurrence]] = {}
        no_id: dict[tuple[str, str], list[_Occurrence]] = {}
        for item in artifact.legacy:
            transaction = item.entry.get("transaction")
            if isinstance(transaction, str):
                groups.setdefault(transaction, []).append(item)
            elif item.entry["op"] != OBSERVE:
                no_id.setdefault((item.entry["run"], item.entry["path"]), []).append(item)
        legacy_by_artifact[artifact.name] = groups
        for transaction, items in groups.items():
            subject = f"legacy:{artifact.name}:transaction:{transaction}"
            identities.setdefault(transaction, {})[subject] = _locations(items)
        for key, items in no_id.items():
            _legacy_stage_group(None, artifact.name, items, conditions)
    for artifact in artifacts:
        for item in artifact.schema2:
            transaction = item.entry.get("transaction")
            if item.entry["kind"] == "mutation" and isinstance(transaction, str):
                subject = f"node:{artifact.name}:{item.entry['node']}"
                existing = identities.setdefault(transaction, {}).get(subject, ())
                identities[transaction][subject] = tuple(
                    sorted(set(existing + (item.location,)), key=_location_key)
                )
    reused = {transaction for transaction, values in identities.items() if len(values) > 1}
    for transaction, values in identities.items():
        if transaction in reused:
            locations = tuple(sorted({location for group in values.values() for location in group}, key=_location_key))
            conditions.add(
                "transaction_identity_damage",
                transaction=transaction,
                locations=locations,
                subjects=tuple(sorted(values)),
            )
    for artifact, groups in legacy_by_artifact.items():
        for transaction, items in groups.items():
            _legacy_stage_group(
                transaction,
                artifact,
                items,
                conditions,
                suppress_multiplicity=transaction in reused,
            )


def _legacy_stage_group(
    transaction: str | None,
    artifact: str,
    items: list[_Occurrence],
    conditions: _Conditions,
    *,
    suppress_multiplicity: bool = False,
) -> None:
    prepared = [item for item in items if item.entry["stage"] == PREPARED]
    committed = [item for item in items if item.entry["stage"] == COMMITTED]
    open_prepared: list[_Occurrence] = []
    unmatched_committed: list[_Occurrence] = []
    pairs: list[tuple[_Occurrence, _Occurrence]] = []
    for item in items:
        if item.entry["stage"] == PREPARED:
            open_prepared.append(item)
        elif open_prepared:
            pairs.append((open_prepared.pop(0), item))
        else:
            unmatched_committed.append(item)
    for prepared_item, committed_item in pairs:
        fields = tuple(
            field
            for field in _PAIRED_LEGACY_FIELDS
            if prepared_item.entry.get(field) != committed_item.entry.get(field)
        )
        if fields:
            conditions.add(
                "mutation_pair_disagreement",
                artifact=artifact,
                locations=_locations((prepared_item, committed_item)),
                transaction=transaction,
                fields=fields,
            )
    for item in open_prepared:
        conditions.add(
            "unfinished_transaction",
            artifact=artifact,
            locations=(item.location,),
            transaction=transaction,
        )
    if not suppress_multiplicity and (
        unmatched_committed or (transaction is not None and len(items) > 2)
    ):
        conditions.add(
            "invalid_stage_multiplicity",
            artifact=artifact,
            locations=_locations(items),
            transaction=transaction,
            subjects=(f"prepared:{len(prepared)}", f"committed:{len(committed)}"),
        )


def _reachable(start: FrontierReference, target: FrontierReference, parents: dict[FrontierReference, tuple[FrontierReference, ...]]) -> bool:
    pending = [start]
    seen: set[FrontierReference] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        pending.extend(parents.get(current, ()))
    return False


def _cycle_members(parents: dict[FrontierReference, tuple[FrontierReference, ...]]) -> set[FrontierReference]:
    cyclic: set[FrontierReference] = set()
    for node in parents:
        if any(_reachable(parent, node, parents) for parent in parents[node]):
            cyclic.add(node)
    return cyclic


def _graph(
    artifacts: tuple[_Artifact, ...], nodes: list[_Node], conditions: _Conditions
) -> tuple[tuple[FrontierReference, ...], str | None, tuple[tuple[FrontierReference, FrontierReference], ...]]:
    anchors = {artifact.anchor.reference: artifact.anchor for artifact in artifacts if artifact.anchor is not None}
    all_nodes = {node.reference: node for node in nodes}
    available_references = set(anchors) | set(all_nodes)
    declared_parents = {
        node.reference: tuple(
            ref for ref in node.frontier if ref in available_references
        )
        for node in nodes
    }
    ancestry_eligible = {node.reference for node in nodes if node.valid}
    cyclic = _cycle_members(declared_parents)
    if cyclic:
        members = tuple(sorted(cyclic, key=_reference_key))
        locations = tuple(
            sorted(
                {location for ref in members for location in all_nodes[ref].locations},
                key=_location_key,
            )
        )
        conditions.add(
            "topology_cycle",
            locations=locations,
            subjects=tuple(_reference_subject(ref) for ref in members),
        )

    for node in nodes:
        if node.reference not in ancestry_eligible:
            continue
        for reference in node.frontier:
            if reference not in anchors and reference not in all_nodes:
                if reference.artifact == node.artifact:
                    conditions.add(
                        "same_artifact_ancestry_missing",
                        artifact=node.artifact,
                        locations=node.locations,
                        node=node.digest,
                        reference=reference,
                    )
                    node.valid = False
                else:
                    conditions.add(
                        "cross_artifact_ancestry_unavailable",
                        artifact=node.artifact,
                        locations=node.locations,
                        node=node.digest,
                        reference=reference,
                    )

    for node in nodes:
        if node.kind != "activation" and len(node.frontier) > 1:
            redundant = any(
                left != right
                and (_reachable(left, right, declared_parents) or _reachable(right, left, declared_parents))
                for left in node.frontier
                for right in node.frontier
            )
            reason = "redundant_reference" if redundant else "implicit_join"
            conditions.add(
                "invalid_frontier",
                artifact=node.artifact,
                locations=node.locations,
                node=node.digest,
                reason=reason,
                subjects=tuple(_reference_subject(ref) for ref in node.frontier),
            )
            node.valid = False
        if node.kind == "activation" and node.reference in ancestry_eligible:
            activation_subject = None
            if any(ref.kind == "node" for ref in node.frontier):
                activation_subject = "node_reference"
            available_anchors = [anchors[ref] for ref in node.frontier if ref in anchors]
            ids = {identity for anchor in available_anchors for identity in anchor.adoption_ids}
            if len(ids) > 1 or any(len(anchor.adoption_ids) > 1 for anchor in available_anchors):
                activation_subject = "mixed_anchor_lineage"
            elif ids and node.entry["adoption"] != next(iter(ids)):
                activation_subject = f"adoption_mismatch:{next(iter(ids))}:{node.entry['adoption']}"
            if activation_subject is not None:
                conditions.add(
                    "invalid_frontier",
                    artifact=node.artifact,
                    locations=node.locations,
                    node=node.digest,
                    reason="invalid_activation",
                    subjects=(activation_subject,),
                )
                node.valid = False

    valid = {node.reference: node for node in nodes if node.valid and node.reference not in cyclic}
    known_flags: dict[FrontierReference, bool] = {}
    known_ids: dict[FrontierReference, str] = {}
    pending = set(valid)
    while pending:
        progressed = False
        for reference in sorted(pending, key=_reference_key):
            node = valid[reference]
            available = [parent for parent in node.frontier if parent in anchors or parent in valid]
            node_parents = [parent for parent in available if parent.kind == "node"]
            if any(parent not in known_flags for parent in node_parents):
                continue
            missing = any(parent not in anchors and parent not in valid for parent in node.frontier)
            derived_true = any(
                (parent.kind == "legacy" and bool(anchors[parent].physical_count))
                or (parent.kind == "node" and known_flags[parent])
                for parent in available
            )
            derived = node.entry["topology_unknown_before"] if missing and not derived_true else derived_true
            if node.entry["topology_unknown_before"] != derived:
                conditions.add(
                    "topology_unknown_flag_damage",
                    artifact=node.artifact,
                    locations=node.locations,
                    node=node.digest,
                    subjects=(
                        f"recorded:{str(node.entry['topology_unknown_before']).lower()}",
                        f"derived:{str(derived).lower()}",
                    ),
                )
                node.valid = False
                del valid[reference]
                pending.remove(reference)
                progressed = True
                break
            known_flags[reference] = derived
            parent_ids = {
                identity
                for parent in available
                if parent.kind == "legacy"
                for identity in anchors[parent].adoption_ids
            }
            parent_ids.update(known_ids[parent] for parent in node_parents if parent in known_ids)
            if node.kind != "activation" and len(parent_ids) > 1:
                conditions.add(
                    "unreconciled_lineages",
                    locations=node.locations,
                    subjects=tuple(
                        [
                            *(
                                _reference_subject(parent)
                                for parent in sorted(available, key=_reference_key)
                            ),
                            *(f"adoption:{identity}" for identity in sorted(parent_ids)),
                        ]
                    ),
                )
                known_ids[reference] = node.entry["adoption"]
            elif node.kind != "activation" and len(parent_ids) == 1 and node.entry["adoption"] != next(iter(parent_ids)):
                conditions.add(
                    "lineage_transition",
                    artifact=node.artifact,
                    locations=node.locations,
                    node=node.digest,
                    subjects=(f"adoption:{next(iter(parent_ids))}", f"adoption:{node.entry['adoption']}"),
                )
                node.valid = False
                del valid[reference]
            else:
                known_ids[reference] = node.entry["adoption"]
            pending.remove(reference)
            progressed = True
            break
        if not progressed:
            break

    consumed: set[FrontierReference] = set()
    edges: list[tuple[FrontierReference, FrontierReference]] = []
    for reference, node in valid.items():
        for parent in node.frontier:
            if parent in anchors or parent in valid:
                consumed.add(parent)
                edges.append((parent, reference))
    inputs = [reference for reference in valid if reference not in consumed]
    inputs.extend(reference for reference in anchors if reference not in consumed)
    inputs = sorted(set(inputs), key=_reference_key)
    if nodes and len(inputs) > 1:
        locations = tuple(
            sorted(
                {
                    location
                    for ref in inputs
                    for location in (
                        valid[ref].locations
                        if ref.kind == "node"
                        else tuple(item.location for artifact in artifacts for item in artifact.legacy if artifact.anchor and artifact.anchor.reference == ref)
                    )
                },
                key=_location_key,
            )
        )
        conditions.add(
            "fork",
            locations=locations,
            subjects=tuple(_reference_subject(ref) for ref in inputs),
        )
    ids: dict[FrontierReference, str] = {}
    for reference in inputs:
        if reference.kind == "node":
            ids[reference] = valid[reference].entry["adoption"]
        elif len(anchors[reference].adoption_ids) == 1:
            ids[reference] = anchors[reference].adoption_ids[0]
    distinct_ids = set(ids.values())
    if nodes and len(distinct_ids) > 1:
        locations = tuple(
            sorted(
                {
                    location
                    for ref in inputs
                    for location in (
                        valid[ref].locations
                        if ref.kind == "node"
                        else tuple(
                            item.location
                            for artifact in artifacts
                            for item in artifact.legacy
                            if artifact.anchor and artifact.anchor.reference == ref
                        )
                    )
                },
                key=_location_key,
            )
        )
        conditions.add(
            "unreconciled_lineages",
            locations=locations,
            subjects=tuple(
                [
                    *(_reference_subject(ref) for ref in inputs),
                    *(f"adoption:{value}" for value in sorted(distinct_ids)),
                ]
            ),
        )
    active = next(iter(distinct_ids)) if len(distinct_ids) == 1 else None
    return tuple(inputs), active, tuple(sorted(edges, key=lambda pair: (_reference_key(pair[0]), _reference_key(pair[1]))))


def inspect(acquired_pair: RawHistoryPair) -> Inspection:
    """Return immutable semantic facts for one coherent raw history pair."""
    if not isinstance(acquired_pair, RawHistoryPair):
        raise TypeError("inspect requires a coherent raw history pair")
    conditions = _Conditions()
    artifacts = (
        _parse_artifact(REPO, acquired_pair.repository, conditions),
        _parse_artifact(LOCAL, acquired_pair.local, conditions),
    )
    primitive = any(item.code in {"malformed_record", "schema_order_damage"} for item in conditions.result())
    nodes: list[_Node] = []
    heads: tuple[FrontierReference, ...] = ()
    active: str | None = None
    edges: tuple[tuple[FrontierReference, FrontierReference], ...] = ()
    if not primitive:
        for artifact in artifacts:
            if artifact.anchor is not None and artifact.anchor.physical_count:
                conditions.add(
                    "topology_unknown_before",
                    artifact=artifact.name,
                    locations=_locations(artifact.legacy),
                    reference=artifact.anchor.reference,
                )
        anchors = [artifact.anchor for artifact in artifacts if artifact.anchor is not None]
        mixed = [anchor for anchor in anchors if len(anchor.adoption_ids) > 1]
        if mixed or (not any(artifact.schema2 for artifact in artifacts) and len({identity for anchor in anchors for identity in anchor.adoption_ids}) > 1):
            affected = mixed or anchors
            affected_locations = tuple(
                sorted(
                    {
                        item.location
                        for artifact in artifacts
                        if artifact.anchor in affected
                        for item in artifact.legacy
                    },
                    key=_location_key,
                )
            )
            conditions.add(
                "legacy_mixed_lineage",
                locations=affected_locations,
                subjects=tuple(
                    [
                        *(
                            _reference_subject(anchor.reference)
                            for anchor in sorted(
                                affected, key=lambda item: _reference_key(item.reference)
                            )
                        ),
                        *(
                            f"adoption:{identity}"
                            for identity in sorted(
                                {
                                    identity
                                    for anchor in affected
                                    for identity in anchor.adoption_ids
                                }
                            )
                        ),
                    ]
                ),
            )
        nodes = _build_nodes(artifacts, conditions)
        _legacy_conditions(artifacts, nodes, conditions)
        heads, active, edges = _graph(artifacts, nodes, conditions)

    result_conditions = conditions.result()
    usable = not any(item.code in _NO_SNAPSHOT for item in result_conditions)
    snapshot = None
    if usable:
        anchors = tuple(sorted((artifact.anchor for artifact in artifacts if artifact.anchor is not None), key=lambda anchor: _reference_key(anchor.reference)))
        adoption_ids = tuple(sorted({item.entry["adoption"] for artifact in artifacts for item in artifact.records}))
        token = _SnapshotToken(
            _token_part(REPO, acquired_pair.repository),
            _token_part(LOCAL, acquired_pair.local),
        )
        snapshot = HistorySnapshot(
            token,
            tuple(artifact.snapshot for artifact in artifacts),
            anchors,
            heads,
            sum(artifact.snapshot.physical_count for artifact in artifacts),
            adoption_ids,
            active,
            _TopologyState(edges),
        )
    errors = any(item.level == "error" for item in result_conditions)
    warnings = any(item.level == "warning" for item in result_conditions)
    unavailable = any(item.code == "cross_artifact_ancestry_unavailable" for item in result_conditions)
    opacity = any(item.code == "topology_unknown_before" for item in result_conditions)
    capabilities = Capabilities(
        snapshot is not None and not errors,
        snapshot is not None and not errors and not warnings,
        snapshot is not None and snapshot.active_adoption is not None,
        snapshot is not None and not opacity,
        snapshot is not None and not unavailable,
    )
    return Inspection(snapshot, result_conditions, capabilities)
