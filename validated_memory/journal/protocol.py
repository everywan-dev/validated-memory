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

from .durable import (
    BootstrapPreparationFailed,
    NoReplaceUnavailable,
    StagingCleanupUnconfirmed,
    VisibilityUnconfirmed,
)
from .executor import (
    AppendReconfirmationNotApplicable,
    AppendReconfirmationRefused,
    AppendReconfirmationRetained,
    AppendReconfirmed,
    AppendUnconfirmed,
    IdentityConfirmed,
    IdentityRefused,
    IdentityRetained,
    adopting_session,
)
from .reconcile import reconcile
from .records import (
    COMMITTED,
    JOURNAL_FILENAME,
    LOCAL,
    OBSERVE,
    REPO,
    JournalError,
    RawHistoryFailure,
    RawHistoryPair,
    acquire_history_pair,
    artifact_name,
    confirm_histories,
    existing_adoption_id,
    is_complete_opening,
    journal_path,
    new_id,
    parse_acquired_history,
    publish_opening,
    reconfirm_append,
    reconfirm_opening,
    record,
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


def _refused(message, artifact=JOURNAL_FILENAME):
    return IdentityRefused(artifact, message)


def _retained(message, artifact=JOURNAL_FILENAME):
    return IdentityRetained(artifact, message)


_APPEND_DATA_UNCONFIRMED = "append file-data durability unconfirmed"
_APPEND_DIRECTORY_UNCONFIRMED = (
    "append file data confirmed; carrying-directory durability unconfirmed"
)


def _append_failure(transaction, durability, error):
    """Retain the exact proof boundary reached by one failed append."""
    artifact = artifact_name(durability)
    if isinstance(error, VisibilityUnconfirmed) and error.complete:
        reason = _APPEND_DIRECTORY_UNCONFIRMED
        message = (
            "the exact append data is confirmed, but its carrying-directory "
            f"durability is unconfirmed. Transaction {transaction} was "
            "retained. Preserve the retained transaction and rerun init"
        )
    else:
        reason = _APPEND_DATA_UNCONFIRMED
        message = (
            "the exact append effect may be visible, but file-data durability "
            f"is unconfirmed. Transaction {transaction} was retained. "
            "Preserve the retained transaction and rerun init"
        )
    return AppendUnconfirmed(artifact, reason, message)


def _append_refused(artifact, reason, category="evidence"):
    actions = {
        "race": "Wait for the other writer to finish, then rerun init",
        "evidence": (
            "Restore the affected history from a trusted copy, then rerun init"
        ),
        "environment": (
            "Restore access or remove the environmental obstruction, then "
            "rerun init"
        ),
    }
    return AppendReconfirmationRefused(
        artifact,
        f"history reconfirmation wrote nothing because {reason}. The retained "
        f"transaction and both histories were preserved. {actions[category]}",
    )


def _append_deferred(artifact, transaction, state):
    return AppendReconfirmationRefused(
        artifact,
        "history reconfirmation wrote nothing because the retained append is "
        f"{state} in the current history. Transaction {transaction} and both "
        "histories were preserved. This operation does not complete or replay "
        "an append. Restore the affected history from a trusted copy, then "
        "rerun init",
    )


def _append_refusal_category(error):
    reason = str(error)
    if any(
        text in reason
        for text in (
            "changed before append reconfirmation",
            "history identity changed",
            "history mode changed",
            "history bytes changed",
            "authorized append range changed",
        )
    ):
        return "race"
    if isinstance(error, OSError):
        return "environment"
    return "evidence"


def _append_retained(artifact, transaction, reason):
    return AppendReconfirmationRetained(
        artifact,
        "the selected append range was re-dirtied or its directory was "
        "reconfirmed, but coherent successor confirmation is incomplete: "
        f"{reason}. Transaction {transaction} was retained. Preserve the "
        "selected WAL and both histories; rerun init. Do not replace or "
        "truncate either history",
    )


def _semantic_append_precondition(root, pair, histories):
    topology = inspect_topology(pair)
    unfinished, disagreements, anomalies = reconcile(histories, root)
    if topology.snapshot is None:
        return "the coherent pair has damaged topology"
    errors = tuple(
        condition.code for condition in topology.conditions
        if condition.level == "error"
    )
    if errors:
        return "the current histories have a conflicting semantic state"
    if unfinished or disagreements or anomalies:
        return "the coherent pair has an unfinished or conflicting history condition"
    return None


def _append_reconfirmation(root, item):
    """Own exact retained append authority and return one closed result."""
    transaction = item.get("transaction", item.get("id", "unknown"))
    projected = classify_evidence(root, item, include_stored_claim=True)
    if not isinstance(projected, HistoryClaimWal):
        return AppendReconfirmationNotApplicable()
    claim = item.get("history_append")
    durability = claim.get("artifact") if isinstance(claim, Mapping) else REPO
    artifact = (
        artifact_name(durability)
        if durability in {REPO, LOCAL}
        else JOURNAL_FILENAME
    )
    try:
        before = confirm_histories(root)
        histories = {
            REPO: before.repository,
            LOCAL: before.local,
        }
        adoption = existing_adoption_id(before.repository, before.local)
        evidence = classify_evidence(
            root,
            item,
            adoption,
            history_pair=before.pair,
            histories=histories,
            include_stored_claim=True,
        )
        if evidence.unconfirmed is None:
            if (
                evidence.claim == "valid"
                and evidence.occurrence in {"zero", "prepared"}
            ):
                return _append_deferred(
                    artifact,
                    transaction,
                    "absent" if evidence.occurrence == "zero" else "incomplete",
                )
            return AppendReconfirmationNotApplicable()
        if (
            not isinstance(evidence, HistoryClaimWal)
            or evidence.claim != "valid"
            or evidence.occurrence != "complete"
        ):
            state = "mismatched"
            if isinstance(evidence, HistoryClaimWal):
                if evidence.occurrence == "zero":
                    state = "absent"
                elif evidence.occurrence in {"prepared", "torn"}:
                    state = "incomplete"
            return _append_deferred(artifact, transaction, state)
        precondition = _semantic_append_precondition(root, before.pair, histories)
        if precondition is not None:
            return _append_refused(artifact, precondition)
        rewrite = item.get("unconfirmed_reason") != _APPEND_DIRECTORY_UNCONFIRMED
        after = reconfirm_append(root, before, dict(claim), rewrite=rewrite)
    except VisibilityUnconfirmed as error:
        return _append_retained(artifact, transaction, error)
    except (JournalError, OSError, KeyError, TypeError, ValueError) as error:
        return _append_refused(
            artifact,
            error,
            _append_refusal_category(error),
        )

    try:
        after_histories = {
            REPO: after.repository,
            LOCAL: after.local,
        }
        postcondition = _semantic_append_precondition(
            root,
            after.pair,
            after_histories,
        )
        if postcondition is not None:
            return _append_retained(artifact, transaction, postcondition)
    except Exception as error:
        return _append_retained(artifact, transaction, error)
    return AppendReconfirmed(after.repository, after.local)


def _identity_transition(root, run, _stale_repository, _stale_local):
    """Own the C1b identity decision and return one closed executor result."""
    root = Path(root)
    path = journal_path(root, REPO)
    try:
        current = confirm_histories(root)
        adoption = existing_adoption_id(current.repository, current.local)
    except JournalError as error:
        return _refused(error.message, error.artifact or JOURNAL_FILENAME)

    if current.repository:
        if len(current.repository) == 1 and is_complete_opening(
            current.repository[0]
        ):
            try:
                current = reconfirm_opening(root, current.repository[0])
            except VisibilityUnconfirmed as error:
                return _retained(
                    "the complete canonical opening was re-dirtied or its "
                    "directory was reconfirmed, but coherent successor "
                    f"confirmation is incomplete: {error}. Preserve the "
                    "canonical opening; no private staging residue is known"
                )
            except OSError as error:
                return _refused(
                    "history reconfirmation refused before writing because "
                    f"{error}. Preserve the exact canonical opening and rerun "
                    "init after removing the obstruction"
                )
        return IdentityConfirmed(
            adoption,
            current.repository,
            current.local,
        )

    if path.is_symlink():
        return _refused(
            f"{JOURNAL_FILENAME} is a symlink and holds no records; it was "
            "preserved. Restore the correct canonical artifact under operator "
            "control, then rerun journal --check and init"
        )
    if current.pair.repository.data is not None:
        return _refused(
            "the existing bootstrap name is not one complete validated "
            "opening; it was preserved"
        )

    adoption = adoption or new_id()
    opening = record(
        OBSERVE,
        "init",
        JOURNAL_FILENAME,
        durability=REPO,
        stage=COMMITTED,
        adoption=adoption,
        run=run,
        note="journal opened",
    )
    try:
        current = publish_opening(root, opening, current.local)
    except NoReplaceUnavailable:
        return _refused(
            "journal bootstrap requires no-replace publication, which is "
            "unavailable on this filesystem. Nothing has been published at "
            "the canonical name"
        )
    except FileExistsError:
        return _refused(
            "another artifact reached the canonical name before bootstrap; "
            "it was preserved and not replaced"
        )
    except BootstrapPreparationFailed as error:
        return _refused(
            "bootstrap stopped before canonical publication because "
            f"{error.phase} failed: {error.error}. The canonical name was not "
            "published by this operation. Remove the environmental obstruction "
            "and rerun init"
        )
    except StagingCleanupUnconfirmed as error:
        residue = error.staging.name
        if error.published:
            message = (
                "the canonical opening is visible and coherently confirmed, "
                "but cleanup of private staging residue "
                f"{residue} is unconfirmed; preserve the canonical name and "
                "the named private residue"
            )
        else:
            message = (
                "bootstrap stopped before canonical publication; the "
                "canonical name was not published by this operation, but "
                f"cleanup of private staging residue {residue} is unconfirmed"
            )
        return _retained(message)
    except VisibilityUnconfirmed:
        staging = sorted(path.parent.glob(f".{path.name}.*.bootstrap"))
        residue = (
            f"; private staging residue {staging[0].name} remains"
            if staging
            else "; no private staging residue is known"
        )
        return _retained(
            "the canonical opening is visible or may be visible, but "
            "publication or coherent readback is unconfirmed; preserve the "
            f"canonical name{residue}"
        )
    return IdentityConfirmed(adoption, current.repository, current.local)


@contextmanager
def adopting_run(root=Path()):
    """Preserve the opaque facade while protocol transitions are injected."""
    with adopting_session(
        _identity_transition,
        _append_failure,
        _append_reconfirmation,
        root,
    ) as session:
        yield session


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
