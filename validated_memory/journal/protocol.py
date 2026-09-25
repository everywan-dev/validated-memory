"""Private journal operation vocabulary and coherent workflow inspection.

The operation and result unions are closed and remain behind the package
facade.  Entering :func:`history_workflow` does not acquire a lock, create a
directory, or read an artifact; the submitted operation selects those effects.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .reconcile import reconcile
from .records import (
    LOCAL,
    REPO,
    JournalError,
    RawHistoryFailure,
    RawHistoryPair,
    acquire_history_pair,
    parse_acquired_history,
)
from .topology import Inspection as TopologyInspection
from .topology import inspect as inspect_topology
from .transactions import (
    PROBLEM_DIVERGED,
    PROBLEM_UNKNOWN,
    DamagedWal,
    HistoryClaimWal,
    UnsupportedWal,
    UnreadableTargetWal,
    classify_evidence,
    open_transactions,
    retained_residue,
)


# Closed operation vocabulary.  This module dispatches without inspecting an
# operation's caller-owned payload before that operation is available.
@dataclass(frozen=True)
class Inspect:
    checked: bool


@dataclass(frozen=True)
class Adopt:
    plan: Any


@dataclass(frozen=True)
class Observe:
    observation: Any


@dataclass(frozen=True)
class Mutate:
    intention: Any


@dataclass(frozen=True)
class RecoverAll:
    pass


@dataclass(frozen=True)
class ResolveOne:
    transaction: str
    disposition: str


@dataclass(frozen=True)
class RepairOne:
    transaction: str


Operation = Inspect | Adopt | Observe | Mutate | RecoverAll | ResolveOne | RepairOne


@dataclass(frozen=True)
class Compatible:
    repository: tuple[Mapping[str, Any], ...]
    local: tuple[Mapping[str, Any], ...]

    @property
    def histories(self) -> Mapping[str, tuple[Mapping[str, Any], ...]]:
        return MappingProxyType({REPO: self.repository, LOCAL: self.local})

    @property
    def records(self) -> tuple[Mapping[str, Any], ...]:
        return self.repository + self.local


@dataclass(frozen=True)
class Incompatible:
    error: JournalError
    preceding: tuple[Mapping[str, Any], ...]

    @property
    def records(self) -> tuple[Mapping[str, Any], ...]:
        return self.preceding


Compatibility = Compatible | Incompatible


@dataclass(frozen=True)
class TopologyUnavailable:
    reason: str


@dataclass(frozen=True, order=True)
class ProtocolCondition:
    order: tuple[Any, ...]
    identity: str
    subject: str
    pairing: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class WalObservation:
    raw: Mapping[str, Any]
    evidence: Any


@dataclass(frozen=True)
class WorkflowInspection:
    pair: RawHistoryPair | None
    compatibility: Compatibility
    topology: TopologyInspection | TopologyUnavailable | None
    wal: tuple[WalObservation, ...]
    residue: tuple[tuple[str, str], ...]
    conditions: tuple[ProtocolCondition, ...]
    unfinished: tuple[Any, ...]
    disagreements: tuple[Any, ...]
    anomalies: tuple[Any, ...]


@dataclass(frozen=True)
class Completed:
    inspection: WorkflowInspection


@dataclass(frozen=True)
class Noop:
    inspection: WorkflowInspection


@dataclass(frozen=True)
class Reported:
    inspection: WorkflowInspection


@dataclass(frozen=True)
class Warning:
    inspection: WorkflowInspection


@dataclass(frozen=True)
class ConfirmedWithGates:
    inspection: WorkflowInspection


@dataclass(frozen=True)
class Refused:
    inspection: WorkflowInspection


@dataclass(frozen=True)
class Retained:
    inspection: WorkflowInspection


@dataclass(frozen=True)
class Unsupported:
    inspection: WorkflowInspection


@dataclass(frozen=True)
class Damaged:
    inspection: WorkflowInspection


Result = (
    Completed
    | Noop
    | Reported
    | Warning
    | ConfirmedWithGates
    | Refused
    | Retained
    | Unsupported
    | Damaged
)


class _HistoryWorkflow:
    def __init__(self, root: Path):
        self._root = root

    def perform(self, operation: Operation) -> Result:
        if type(operation) is not Inspect:
            raise NotImplementedError(
                f"{type(operation).__name__} is not available in this workflow"
            )
        return self._inspect(operation.checked)

    def _inspect(self, checked: bool) -> Result:
        try:
            acquired = acquire_history_pair(self._root)
        except JournalError as error:
            state = WorkflowInspection(
                None,
                Incompatible(error, ()),
                None,
                (),
                (),
                (
                    ProtocolCondition(
                        (0, "history.pair_unstable"),
                        "history.pair_unstable",
                        error.artifact or "journal.jsonl",
                        (),
                        error.message,
                    ),
                ),
                (),
                (),
                (),
            )
            return Refused(state)

        if isinstance(acquired, RawHistoryFailure):
            preceding = _parse_preceding(acquired)
            state = WorkflowInspection(
                None,
                Incompatible(acquired.error, preceding),
                None,
                (),
                (),
                (_history_error_condition(acquired.error),),
                (),
                (),
                (),
            )
            return _history_error_result(state, acquired.error)

        topology: TopologyInspection | TopologyUnavailable
        try:
            topology = inspect_topology(acquired)
        except Exception as error:
            # Inspection cannot change the released renderer's observation.
            topology = TopologyUnavailable(
                f"{type(error).__name__}: {error}"
            )

        parsed: dict[str, tuple[Mapping[str, Any], ...]] = {}
        preceding: list[Mapping[str, Any]] = []
        for durability, raw in (
            (REPO, acquired.repository),
            (LOCAL, acquired.local),
        ):
            try:
                records = tuple(
                    _freeze_mapping(entry)
                    for entry in parse_acquired_history(raw, durability)
                )
            except JournalError as error:
                observations, residue = _observe_wal(
                    self._root,
                    acquired,
                    parsed,
                    preceding[0].get("adoption") if preceding else None,
                )
                state = WorkflowInspection(
                    acquired,
                    Incompatible(error, tuple(preceding)),
                    topology,
                    observations,
                    residue,
                    tuple(sorted(
                        (
                            *_topology_conditions(topology),
                            _history_error_condition(error),
                            *(_wal_condition(item) for item in observations),
                        )
                    )),
                    (),
                    (),
                    (),
                )
                return _history_error_result(state, error)
            parsed[durability] = records
            preceding.extend(records)

        compatibility = Compatible(parsed[REPO], parsed[LOCAL])
        histories = compatibility.histories
        unfinished, disagreements, anomalies = (
            reconcile(histories, self._root) if checked else ([], [], [])
        )
        adoption = compatibility.records[0]["adoption"] if compatibility.records else None
        try:
            observations, residue = _observe_wal(
                self._root,
                acquired,
                histories,
                adoption,
            )
        except JournalError as error:
            state = WorkflowInspection(
                acquired,
                Incompatible(error, compatibility.records),
                topology,
                (),
                (),
                tuple(sorted(
                    (*_topology_conditions(topology), _history_error_condition(error))
                )),
                tuple(unfinished),
                tuple(disagreements),
                tuple(anomalies),
            )
            return _history_error_result(state, error)

        conditions = tuple(
            sorted(
                (
                    *_topology_conditions(topology),
                    *_legacy_conditions(unfinished, disagreements, anomalies),
                    *(_wal_condition(item) for item in observations),
                    *(
                        ProtocolCondition(
                            (4, location, message),
                            "wal.damaged",
                            location,
                            (),
                            message,
                        )
                        for location, message in residue
                    ),
                )
            )
        )
        state = WorkflowInspection(
            acquired,
            compatibility,
            topology,
            observations,
            residue,
            conditions,
            tuple(unfinished),
            tuple(disagreements),
            tuple(anomalies),
        )
        if any(
            condition.identity in {"history.unsupported", "wal.unsupported"}
            for condition in conditions
        ):
            return Unsupported(state)
        if any(
            condition.identity in {"history.topology_damage", "wal.damaged"}
            for condition in conditions
        ):
            return Damaged(state)
        return Refused(state) if conditions else Reported(state)


@contextmanager
def history_workflow(root=Path()):
    """Yield an inert private workflow; dispatch decides when to observe."""
    yield _HistoryWorkflow(Path(root))


def _parse_preceding(failure: RawHistoryFailure):
    parsed = []
    for durability, raw in zip((REPO, LOCAL), failure.preceding):
        parsed.extend(
            _freeze_mapping(entry)
            for entry in parse_acquired_history(raw, durability)
        )
    return tuple(parsed)


def _observe_wal(root, pair, histories, adoption):
    raw_wal = open_transactions(root)
    observations = tuple(
        WalObservation(
            _freeze_mapping(item),
            classify_evidence(
                root,
                item,
                adoption,
                history_pair=pair,
                histories=histories,
            ),
        )
        for item in raw_wal
    )
    return observations, tuple(retained_residue(root))


def _freeze_value(value):
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value):
    return _freeze_value(value)


def _history_error_condition(error: JournalError) -> ProtocolCondition:
    message = error.message
    identity = "history.unreadable" if error.lineno is None else "history.malformed"
    if "newer than this plugin" in message:
        identity = "history.unsupported"
    return ProtocolCondition(
        (0, identity, error.artifact or "", error.lineno or 0, message),
        identity,
        error.artifact or "journal.jsonl",
        (),
        message,
    )


def _history_error_result(state: WorkflowInspection, error: JournalError) -> Result:
    if "newer than this plugin" in error.message:
        return Unsupported(state)
    if error.lineno is not None:
        return Damaged(state)
    return Refused(state)


def _topology_conditions(topology):
    if isinstance(topology, TopologyUnavailable) or topology is None:
        return ()
    damaged = topology.snapshot is None
    identity = "history.topology_damage" if damaged else "history.topology_gate"
    return tuple(
        ProtocolCondition(
            (
                1,
                identity,
                condition.code,
                condition.artifact or "",
                condition.transaction or "",
                condition.node or "",
                condition.reason or "",
            ),
            identity,
            condition.artifact or "journal histories",
            tuple(condition.subjects),
            condition.code,
        )
        for condition in topology.conditions
        if condition.level == "error"
    )


def _legacy_conditions(unfinished, disagreements, anomalies):
    conditions = []
    for entry, state in unfinished:
        conditions.append(
            ProtocolCondition(
                (2, entry["path"], entry["run"], state),
                "history.topology_gate",
                entry["path"],
                (f"run:{entry['run']}",),
                state,
            )
        )
    for transaction, field, entry in disagreements:
        conditions.append(
            ProtocolCondition(
                (2, entry["path"], transaction, field),
                "history.topology_gate",
                entry["path"],
                (f"transaction:{transaction}",),
                field,
            )
        )
    for message, entry in anomalies:
        conditions.append(
            ProtocolCondition(
                (2, entry["path"], message),
                "history.topology_gate",
                entry["path"],
                (),
                message,
            )
        )
    return tuple(conditions)


def _wal_condition(observation: WalObservation) -> ProtocolCondition:
    evidence = observation.evidence
    if isinstance(evidence, UnsupportedWal):
        identity = "wal.unsupported"
    elif isinstance(evidence, DamagedWal):
        identity = "wal.damaged"
    elif isinstance(evidence, UnreadableTargetWal) or (
        isinstance(evidence, HistoryClaimWal)
        and isinstance(evidence.target, UnreadableTargetWal)
    ):
        identity = "wal.target_unreadable"
    elif evidence.verdict == PROBLEM_DIVERGED:
        identity = "wal.diverged"
    elif evidence.verdict == PROBLEM_UNKNOWN:
        identity = "wal.unknown_readable"
    else:
        identity = "wal.recoverable"
    return ProtocolCondition(
        (3, evidence.transaction, identity),
        identity,
        evidence.path or f"transaction:{evidence.transaction}",
        (f"transaction:{evidence.transaction}",),
        evidence.problem_reason or evidence.verdict,
    )
