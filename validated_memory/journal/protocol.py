"""Private journal operation vocabulary and coherent workflow inspection.

The operation and result unions are closed and remain behind the package
facade.  Entering :func:`history_workflow` does not acquire a lock, create a
directory, or read an artifact; the submitted operation selects those effects.
"""

from __future__ import annotations

import json
import os
import stat
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
    read_file_snapshot,
    read_regular_file,
    remove_name,
)
from .executor import (
    PREIMAGE_DIRNAME,
    AppendReconfirmationNotApplicable,
    AppendReconfirmationRefused,
    AppendReconfirmationRetained,
    AppendReconfirmed,
    AppendUnconfirmed,
    IdentityConfirmed,
    IdentityRefused,
    IdentityRetained,
    Run,
)
from .fault import rendezvous_at
from .lock import Lock
from .operations import (
    OUTCOME_APPLIED,
    OUTCOME_NOOP,
    link_to,
    replace_file,
)
from .paths import ABSENT, DIRECTORY, FILE, SYMLINK, current_state, describe, satisfies
from .reconcile import reconcile
from .records import (
    COMMITTED,
    HISTORY_WRITE_SCHEMA,
    HistoryErrorKind,
    JOURNAL_FILENAME,
    LOCAL,
    OBSERVE,
    PREPARED,
    REPO,
    STAGES,
    VAULT_DIRNAME,
    WAL_SCHEMA,
    JournalError,
    RawHistoryFailure,
    RawHistory,
    RawHistoryPair,
    acquire_history_pair,
    append,
    artifact_name,
    confirm_histories,
    digest,
    encode_records,
    existing_adoption_id,
    ensure_history_compatibility,
    is_complete_opening,
    is_inside_path,
    journal_path,
    new_id,
    parse_acquired_history,
    publish_opening,
    reconfirm_append,
    reconfirm_opening,
    record,
    repair_complete_prefix,
    repair_history_target,
)
from .topology import Inspection as TopologyInspection
from .topology import inspect as inspect_topology
from .transactions import (
    ACCEPT,
    CLEANUP_UNCONFIRMED,
    DISCARDED,
    HISTORY_CLAIM_UNCONFIRMED,
    HISTORY_UNCONFIRMED,
    PROBLEM_DAMAGED,
    PROBLEM_DIVERGED,
    PROBLEM_UNKNOWN,
    PUBLISHED,
    RECOVERABLE,
    RECOVERED,
    REMOVED,
    RESOLUTIONS,
    RESTORE,
    RESTORE_UNCONFIRMED,
    TARGET_UNCONFIRMED,
    TRANSACTIONS_DIRNAME,
    VERDICT_CLEANUP_UNCONFIRMED,
    VERDICT_COMPLETE,
    VERDICT_DISCARD,
    VERDICT_REMOVE,
    VERDICT_RESTORE_UNCONFIRMED,
    VERDICT_TARGET_UNCONFIRMED,
    DamagedWal,
    HistoryClaimWal,
    ReadableTargetWal,
    Recovery,
    Resolution,
    UnsupportedWal,
    UnreadableTargetWal,
    classify_evidence,
    claimed_temporary_residue,
    cleanup_private_duplicates,
    has_transaction,
    mark_history_append,
    mark_published,
    mark_unconfirmed,
    no_such_transaction,
    open_transactions,
    read_transaction,
    reestablish_transaction,
    report_word,
    remove_transaction_file,
    resolution_advice,
    retained_residue,
    transaction_artifact,
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
    public_message: str = ""


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
class RepairPresentation:
    transaction: str
    location: str
    message: str | None = None
    gates: tuple[ProtocolCondition, ...] = ()


@dataclass(frozen=True)
class _FrozenRepairWal:
    path: Path
    data: bytes
    mode: int
    identity: tuple[int, int, int, int]


@dataclass(frozen=True)
class _FrozenRepairTemporary:
    path: Path
    parent_identity: tuple[int, int]
    resolved_parent: Path
    kind: str
    mode: int
    identity: tuple[int, int]
    data: bytes


@dataclass(frozen=True)
class Completed:
    inspection: WorkflowInspection
    repair: RepairPresentation | None = None
    value: Any = None


@dataclass(frozen=True)
class Noop:
    inspection: WorkflowInspection
    repair: RepairPresentation | None = None
    value: Any = None


@dataclass(frozen=True)
class Reported:
    inspection: WorkflowInspection
    repair: RepairPresentation | None = None
    value: Any = None


@dataclass(frozen=True)
class Warning:
    inspection: WorkflowInspection
    repair: RepairPresentation | None = None
    value: Any = None


@dataclass(frozen=True)
class ConfirmedWithGates:
    inspection: WorkflowInspection
    repair: RepairPresentation | None = None
    value: Any = None


@dataclass(frozen=True)
class Refused:
    inspection: WorkflowInspection
    repair: RepairPresentation | None = None
    value: Any = None


@dataclass(frozen=True)
class Retained:
    inspection: WorkflowInspection
    repair: RepairPresentation | None = None
    value: Any = None


@dataclass(frozen=True)
class Unsupported:
    inspection: WorkflowInspection
    repair: RepairPresentation | None = None
    value: Any = None


@dataclass(frozen=True)
class Damaged:
    inspection: WorkflowInspection
    repair: RepairPresentation | None = None
    value: Any = None


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


def _mutable(value):
    if isinstance(value, Mapping):
        return {key: _mutable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable(item) for item in value]
    return value


def _workflow_snapshot(root):
    """Acquire one coherent recovery/resolution snapshot under its caller's lock."""
    return _HistoryWorkflow(Path(root))._inspect(
        True, include_stored_claim=True
    ).inspection


def _valid_provisions(snapshot):
    """Return only WALs that independently prove their exact prepared gap."""
    if not isinstance(snapshot.compatibility, Compatible):
        return ()
    provisions = []
    for observation in snapshot.wal:
        evidence = observation.evidence
        if isinstance(evidence, (DamagedWal, UnsupportedWal)):
            continue
        if isinstance(evidence, HistoryClaimWal):
            if evidence.claim == "valid" and evidence.occurrence == "prepared":
                provisions.append(evidence)
            continue
        histories = snapshot.compatibility.histories
        occurrences = tuple(
            entry
            for durability in (REPO, LOCAL)
            for entry in histories[durability]
            if entry.get("transaction") == evidence.transaction
        )
        if len(occurrences) != 1 or occurrences[0].get("stage") != PREPARED:
            continue
        expected = _reconstructed_prepared_fields(observation)
        actual = {
            key: value for key, value in occurrences[0].items() if key != "at"
        }
        if actual == expected:
            provisions.append(evidence)
    return tuple(provisions)


def _reconstructed_prepared_fields(observation):
    """Project every WAL-bound field a released prepared record can prove."""
    evidence = observation.evidence
    raw = observation.raw
    intention = evidence.intention
    expected = {
        "schema": HISTORY_WRITE_SCHEMA,
        "version": raw.get("version"),
        "adoption": raw.get("adoption"),
        "run": evidence.run,
        "transaction": evidence.transaction,
        "durability": evidence.durability,
        "op": intention["op"],
        "purpose": intention["purpose"],
        "path": evidence.path,
        "stage": PREPARED,
    }
    if intention.get("note") is not None:
        expected["note"] = intention["note"]
    postimage = evidence.postimage
    mode = raw.get("published_mode")
    if mode is None and postimage is not None:
        mode = postimage.get("mode")
    if (
        mode is not None
        and postimage is not None
        and postimage.get("kind") != SYMLINK
    ):
        expected["mode"] = mode
    if postimage is not None and postimage.get("kind") == FILE:
        expected["preimage"] = evidence.preimage_blob
        expected["postimage"] = postimage["digest"]
        if evidence.prior_bytes is not None:
            expected["prior_bytes"] = evidence.prior_bytes
    return expected


def _is_exact_provision(condition, evidence):
    transaction = f"transaction:{evidence.transaction}"
    if transaction not in condition.pairing:
        return False
    if condition.detail == "unfinished_transaction":
        return condition.subject in {
            evidence.durability,
            artifact_name(evidence.durability),
        }
    if condition.order and condition.order[0] == 2:
        return (
            condition.subject == evidence.path
            and f"run:{evidence.run}" in condition.pairing
            and f"durability:{evidence.durability}" in condition.pairing
        )
    return False


def _outstanding_history_conditions(snapshot):
    """The `history.*` conditions no exact WAL provision discharges.

    The one definition of what stops an adopting run: `_mutation_gate` refuses
    on it, and the guarded harness repair and `history_condition_count` read
    the same set, so neither can call a history clear that the gate refuses.
    """
    provisions = _valid_provisions(snapshot)
    return tuple(
        condition
        for condition in snapshot.conditions
        if condition.identity.startswith("history.")
        and not any(
            _is_exact_provision(condition, evidence) for evidence in provisions
        )
    )


def _mutation_gate(snapshot):
    """Name a global history gate not discharged by its exact WAL provision."""
    unusable = _unusable_snapshot(snapshot)
    if unusable is not None:
        return unusable
    outstanding = _outstanding_history_conditions(snapshot)
    if outstanding:
        condition = outstanding[0]
        return f"{condition.subject} has unresolved history condition {condition.detail}"
    return None


def _unusable_snapshot(snapshot):
    if not isinstance(snapshot.compatibility, Compatible):
        return "the coherent history pair is incompatible"
    if isinstance(snapshot.topology, TopologyUnavailable):
        return f"history topology inspection is unavailable: {snapshot.topology.reason}"
    if snapshot.topology is None or snapshot.topology.snapshot is None:
        return "the coherent history topology is unavailable or damaged"
    return None


def _history_conditions(snapshot):
    return frozenset(
        condition
        for condition in snapshot.conditions
        if condition.identity.startswith("history.")
    )


def _recovery_gate_problem(evidence, snapshot):
    reason = _mutation_gate(snapshot)
    if reason is None:
        return None
    return _recovery_problem(
        evidence,
        f"transaction {evidence.transaction} cannot change retained state because "
        f"{reason}; every artifact was preserved",
    )


def _observation(snapshot, transaction):
    return next(
        (
            observation
            for observation in snapshot.wal
            if observation.evidence.transaction == transaction
        ),
        None,
    )


def _facts(evidence):
    result = {
        "id": evidence.transaction,
        "path": evidence.path,
        "durability": evidence.durability,
        "stage": evidence.stage,
        "problem_reason": evidence.problem_reason,
        "abort_reason": evidence.abort_reason,
        "run": evidence.run,
        "preimage_blob": evidence.preimage_blob,
        "prior_bytes": evidence.prior_bytes,
        "mode": evidence.mode,
        "unconfirmed": evidence.unconfirmed,
    }
    for name in ("intention", "preimage", "postimage"):
        value = getattr(evidence, name)
        if value is not None:
            result[name] = _mutable(value)
    target = evidence.target if isinstance(evidence, HistoryClaimWal) else evidence
    if isinstance(target, ReadableTargetWal):
        result["actual"] = _mutable(target.actual)
    elif isinstance(target, UnreadableTargetWal):
        result["actual"] = None
    return result


def _reconstructed_records(session, evidence, stages, *, published=None):
    facts = _facts(evidence)
    intention = facts["intention"]
    postimage = facts["postimage"]
    actual = facts["actual"] if published is None else published
    fields = {"transaction": evidence.transaction}
    mode = actual.get("mode")
    if mode is not None and actual.get("kind") != SYMLINK:
        fields["mode"] = mode
    if intention.get("note") is not None:
        fields["note"] = intention["note"]
    if postimage["kind"] == FILE and "digest" in postimage:
        fields["preimage"] = evidence.preimage_blob
        fields["postimage"] = postimage["digest"]
        if evidence.prior_bytes is not None:
            fields["prior_bytes"] = evidence.prior_bytes
    return tuple(
        session.build_record(
            intention["op"],
            intention["purpose"],
            evidence.path,
            evidence.durability,
            stage,
            run=evidence.run,
            **fields,
        )
        for stage in stages
    )


def _append_successor(root, snapshot, evidence, records):
    """Append selected bytes and return the freshly acquired coherent successor."""
    before = confirm_histories(root)
    if before.pair != snapshot.pair:
        raise JournalError(
            None,
            "journal histories changed before the selected recovery append",
            artifact_name(evidence.durability),
        )
    selected_before = (
        before.pair.repository
        if evidence.durability == REPO
        else before.pair.local
    )
    opposite_before = (
        before.pair.local
        if evidence.durability == REPO
        else before.pair.repository
    )
    expected_data = (selected_before.data or b"") + encode_records(records)
    expected_history_conditions = {
        condition
        for condition in snapshot.conditions
        if condition.identity.startswith("history.")
        and not (
            any(
                record.get("stage") == COMMITTED
                and record.get("transaction") == evidence.transaction
                for record in records
            )
            and _is_exact_provision(condition, evidence)
        )
    }
    append(records, root, evidence.durability)
    rendezvous_at("after-selected-history-append", 1)
    successor = _workflow_snapshot(root)
    selected_after = (
        successor.pair.repository
        if successor.pair is not None and evidence.durability == REPO
        else successor.pair.local
        if successor.pair is not None
        else None
    )
    opposite_after = (
        successor.pair.local
        if successor.pair is not None and evidence.durability == REPO
        else successor.pair.repository
        if successor.pair is not None
        else None
    )
    identity = (
        selected_before.generation[:2]
        if selected_before.generation is not None
        else None
    )
    after_identity = (
        selected_after.generation[:2]
        if selected_after is not None and selected_after.generation is not None
        else None
    )
    actual_history_conditions = _history_conditions(successor)
    if (
        _unusable_snapshot(successor) is not None
        or selected_after is None
        or selected_after.data != expected_data
        or (
            identity is not None
            and (
                selected_after.mode != selected_before.mode
                or after_identity != identity
            )
        )
        or (identity is None and after_identity is None)
        or opposite_after != opposite_before
        or actual_history_conditions != expected_history_conditions
    ):
        raise JournalError(
            None,
            "the selected append did not produce its exact frozen coherent successor",
            artifact_name(evidence.durability),
        )
    return successor


def _retain_recovery(root, evidence, phase, reason, artifact=None):
    try:
        mark_unconfirmed(root, evidence.transaction, phase, str(reason))
    except (OSError, VisibilityUnconfirmed) as secondary:
        reason = f"{reason}; retaining the recovery fact was also unconfirmed: {secondary}"
    error = JournalError(
        None,
        f"recovery of {evidence.path} left its {phase} effect visible or "
        f"indeterminate: {reason}. Transaction {evidence.transaction} and all "
        "available evidence were retained; preserve them and rerun init",
        artifact or transaction_artifact(evidence.transaction),
    )
    error.visibility_unconfirmed = True
    raise error


def _retain_without_transition(evidence, reason, *, resolution=False):
    prefix = (
        "the selected resolution cannot continue"
        if resolution
        else "recovery cannot continue"
    )
    error = JournalError(
        None,
        f"{prefix} because retained evidence could not be durably and "
        f"coherently confirmed: {reason}. Transaction {evidence.transaction} "
        "and all available evidence were retained; preserve them and rerun init",
        transaction_artifact(evidence.transaction),
    )
    error.visibility_unconfirmed = True
    raise error


def _confirm_frozen_snapshot(root, snapshot, evidence, phase):
    successor = _workflow_snapshot(root)
    if (
        _unusable_snapshot(successor) is not None
        or successor.pair != snapshot.pair
        or _history_conditions(successor) != _history_conditions(snapshot)
    ):
        _retain_recovery(
            root,
            evidence,
            phase,
            "the coherent history pair changed before selected cleanup",
        )
    return successor


def _reestablish_claim_evidence(
    session, snapshot, observation, *, resolution=False
):
    evidence = observation.evidence
    expected = _mutable(observation.raw)
    rendezvous_at("before-claim-reestablishment", 1)
    try:
        reestablish_transaction(
            session.root, evidence.transaction, expected
        )
        successor = _workflow_snapshot(session.root)
    except (OSError, VisibilityUnconfirmed, JournalError) as error:
        _retain_without_transition(evidence, error, resolution=resolution)
    refreshed = _observation(successor, evidence.transaction)
    if (
        _unusable_snapshot(successor) is not None
        or successor.pair != snapshot.pair
        or _history_conditions(successor) != _history_conditions(snapshot)
        or refreshed is None
        or _mutable(refreshed.raw) != expected
        or not isinstance(refreshed.evidence, HistoryClaimWal)
        or refreshed.evidence.claim != "valid"
        or refreshed.evidence.occurrence != evidence.occurrence
        or refreshed.evidence.unconfirmed != HISTORY_CLAIM_UNCONFIRMED
    ):
        _retain_without_transition(
            evidence,
            "the selected WAL or coherent history pair changed during "
            "re-establishment",
            resolution=resolution,
        )
    return successor, refreshed


def _cleanup_recovered(
    root, evidence, *, append_confirmed=False, snapshot=None
):
    try:
        mark_unconfirmed(
            root,
            evidence.transaction,
            CLEANUP_UNCONFIRMED,
            "target and history confirmed; transaction cleanup pending",
        )
        marked = _workflow_snapshot(root)
        if (
            _unusable_snapshot(marked) is not None
            or (
                snapshot is not None
                and (
                    marked.pair != snapshot.pair
                    or _history_conditions(marked) != _history_conditions(snapshot)
                )
            )
        ) or _observation(marked, evidence.transaction) is None:
            raise JournalError(
                None,
                "the selected WAL or coherent history pair changed before cleanup",
                transaction_artifact(evidence.transaction),
            )
        remove_transaction_file(root, evidence.transaction)
    except (OSError, VisibilityUnconfirmed, JournalError) as error:
        if append_confirmed:
            retained = JournalError(
                None,
                "the selected append range was coherently confirmed, but "
                f"transaction cleanup is incomplete: {error}. Transaction "
                f"{evidence.transaction} was retained. The append is already "
                "confirmed and must not be repeated. Preserve the retained "
                "transaction and rerun init",
                artifact_name(evidence.durability),
            )
            retained.visibility_unconfirmed = True
            raise retained from error
        _retain_recovery(root, evidence, CLEANUP_UNCONFIRMED, error)


def _recovery_problem(evidence, message=None):
    return Recovery(
        evidence.transaction,
        evidence.path,
        evidence.durability,
        problem=(
            PROBLEM_DAMAGED
            if isinstance(evidence, (DamagedWal, UnsupportedWal))
            else evidence.verdict
            if evidence.verdict in {PROBLEM_DIVERGED, PROBLEM_UNKNOWN}
            else PROBLEM_UNKNOWN
        ),
        message=message or evidence.problem_reason or evidence.verdict,
    )


def _recover_claimed(session, snapshot, observation):
    evidence = observation.evidence
    transaction = evidence.transaction
    claim = _mutable(observation.raw["history_append"])
    if evidence.claim != "valid":
        refused = _append_deferred(
            artifact_name(evidence.durability), transaction, "mismatched"
        )
        return Recovery(
            transaction, refused.artifact, evidence.durability,
            problem=PROBLEM_UNKNOWN, message=refused.message,
        )
    if evidence.occurrence in {"conflicting", "unavailable"}:
        refused = _append_deferred(
            artifact_name(evidence.durability), transaction, "mismatched"
        )
        return Recovery(
            transaction, refused.artifact, evidence.durability,
            problem=PROBLEM_UNKNOWN, message=refused.message,
        )
    if evidence.occurrence == "torn":
        action = (
            f"run 'validated-memory journal --repair {transaction}' only if "
            "the retained proof is accepted"
        )
        return _recovery_problem(
            evidence,
            f"transaction {transaction} conflicts with permanent history; "
            f"{action}. The retained transaction was left unchanged",
        )
    if (
        evidence.occurrence in {"zero", "prepared"}
        or evidence.unconfirmed in {
            HISTORY_CLAIM_UNCONFIRMED,
            HISTORY_UNCONFIRMED,
        }
    ):
        gated = _recovery_gate_problem(evidence, snapshot)
        if gated is not None:
            return gated
    if evidence.unconfirmed == HISTORY_CLAIM_UNCONFIRMED:
        snapshot, observation = _reestablish_claim_evidence(
            session, snapshot, observation
        )
        evidence = observation.evidence
        claim = _mutable(observation.raw["history_append"])
    if (
        evidence.unconfirmed == HISTORY_UNCONFIRMED
        and evidence.occurrence == "complete"
    ):
        boundary = _append_reconfirmation(session.root, _mutable(observation.raw))
        if isinstance(boundary, AppendReconfirmationRefused):
            return Recovery(
                transaction,
                boundary.artifact,
                evidence.durability,
                problem=PROBLEM_UNKNOWN,
                message=boundary.message,
            )
        if isinstance(boundary, AppendReconfirmationRetained):
            error = JournalError(None, boundary.message, boundary.artifact)
            error.visibility_unconfirmed = True
            raise error
        if not isinstance(boundary, AppendReconfirmed):
            raise TypeError("append reconfirmation returned an unknown result")
        snapshot = _workflow_snapshot(session.root)
        appended = False
    elif evidence.occurrence == "zero":
        try:
            snapshot = _append_successor(
                session.root, snapshot, evidence, tuple(claim["records"])
            )
        except (OSError, VisibilityUnconfirmed, JournalError) as error:
            _retain_recovery(
                session.root, evidence, HISTORY_UNCONFIRMED, error,
                artifact_name(evidence.durability),
            )
        appended = True
    elif evidence.occurrence == "prepared":
        try:
            snapshot = _append_successor(
                session.root, snapshot, evidence, (claim["records"][1],)
            )
        except (OSError, VisibilityUnconfirmed, JournalError) as error:
            _retain_recovery(
                session.root, evidence, HISTORY_UNCONFIRMED, error,
                artifact_name(evidence.durability),
            )
        appended = True
    else:
        appended = False
    successor = _observation(snapshot, transaction)
    if successor is None or not isinstance(successor.evidence, HistoryClaimWal):
        _retain_recovery(
            session.root, evidence, HISTORY_UNCONFIRMED,
            "the coherent successor no longer contains the selected transaction",
        )
    if successor.evidence.occurrence != "complete":
        _retain_recovery(
            session.root, evidence, HISTORY_UNCONFIRMED,
            "the exact stored record pair was not confirmed in the successor",
        )
    snapshot = _confirm_frozen_snapshot(
        session.root, snapshot, successor.evidence, HISTORY_UNCONFIRMED
    )
    refreshed = _observation(snapshot, transaction)
    if refreshed is None:
        _retain_recovery(
            session.root,
            successor.evidence,
            HISTORY_UNCONFIRMED,
            "the selected WAL changed before cleanup",
        )
    _cleanup_recovered(
        session.root,
        refreshed.evidence,
        append_confirmed=True,
        snapshot=snapshot,
    )
    return Recovery(
        transaction,
        evidence.path,
        evidence.durability,
        action=RECOVERED,
        appended=appended,
        message=f"transaction {transaction} has its exact history confirmed",
    )


def _recover_reconstructed(session, snapshot, evidence):
    """Complete a claimless zero occurrence from exact released WAL fields."""
    gated = _recovery_gate_problem(evidence, snapshot)
    if gated is not None:
        return gated
    records = snapshot.compatibility.histories[evidence.durability]
    present = {
        entry["stage"]
        for entry in records
        if entry.get("transaction") == evidence.transaction
    }
    missing = tuple(stage for stage in STAGES if stage not in present)
    try:
        built = _reconstructed_records(session, evidence, missing)
        if built:
            snapshot = _append_successor(session.root, snapshot, evidence, built)
    except (OSError, VisibilityUnconfirmed, JournalError) as error:
        _retain_recovery(
            session.root, evidence, HISTORY_UNCONFIRMED, error,
            artifact_name(evidence.durability),
        )
    snapshot = _confirm_frozen_snapshot(
        session.root, snapshot, evidence, HISTORY_UNCONFIRMED
    )
    refreshed = _observation(snapshot, evidence.transaction)
    if refreshed is None:
        _retain_recovery(
            session.root,
            evidence,
            HISTORY_UNCONFIRMED,
            "the selected WAL changed before cleanup",
        )
    _cleanup_recovered(
        session.root,
        refreshed.evidence,
        append_confirmed=bool(missing),
        snapshot=snapshot,
    )
    return Recovery(
        evidence.transaction,
        evidence.path,
        evidence.durability,
        action=RECOVERED,
        appended=bool(missing),
        message=f"transaction {evidence.transaction} history was completed",
    )


def _recover_item(session, snapshot, observation):
    evidence = observation.evidence
    facts = _facts(evidence)
    if isinstance(evidence, UnsupportedWal):
        return _recovery_problem(
            evidence,
            f"transaction {evidence.transaction} uses WAL schema {evidence.found}, "
            f"newer than this reader's maximum {evidence.maximum}",
        )
    if isinstance(evidence, DamagedWal):
        return _recovery_problem(
            evidence,
            f"damaged transaction {evidence.transaction}: "
            f"{evidence.problem_reason}; its artifact is left for inspection",
        )
    if evidence.verdict in {VERDICT_REMOVE, VERDICT_DISCARD}:
        try:
            remove_transaction_file(session.root, evidence.transaction)
        except (OSError, VisibilityUnconfirmed, JournalError) as error:
            _retain_recovery(session.root, evidence, CLEANUP_UNCONFIRMED, error)
        return Recovery(
            evidence.transaction,
            evidence.path,
            evidence.durability,
            action=REMOVED if evidence.verdict == VERDICT_REMOVE else DISCARDED,
            message=f"transaction {evidence.transaction} left no recorded effect",
        )
    if evidence.verdict == VERDICT_TARGET_UNCONFIRMED:
        gated = _recovery_gate_problem(evidence, snapshot)
        if gated is not None:
            return gated
        before_pair = snapshot.pair
        before_conditions = _history_conditions(snapshot)
        try:
            session.republish_target(facts)
        except (OSError, VisibilityUnconfirmed, JournalError) as error:
            _retain_recovery(session.root, evidence, TARGET_UNCONFIRMED, error)
        rendezvous_at("after-selected-target-recovery", 1)
        snapshot = _workflow_snapshot(session.root)
        observation = _observation(snapshot, evidence.transaction)
        if (
            _unusable_snapshot(snapshot) is not None
            or snapshot.pair != before_pair
            or _history_conditions(snapshot) != before_conditions
        ):
            _retain_recovery(
                session.root, evidence, TARGET_UNCONFIRMED,
                "the coherent history pair changed after target confirmation",
            )
        if observation is None or not isinstance(
            observation.evidence.target
            if isinstance(observation.evidence, HistoryClaimWal)
            else observation.evidence,
            ReadableTargetWal,
        ) or not satisfies(
            _mutable(
                (
                    observation.evidence.target
                    if isinstance(observation.evidence, HistoryClaimWal)
                    else observation.evidence
                ).actual
            ),
            _mutable(evidence.postimage),
        ):
            _retain_recovery(
                session.root, evidence, TARGET_UNCONFIRMED,
                "the selected target or transaction changed after confirmation",
            )
        try:
            mark_published(session.root, evidence.transaction)
        except (OSError, VisibilityUnconfirmed, JournalError) as error:
            _retain_recovery(session.root, evidence, TARGET_UNCONFIRMED, error)
        snapshot = _workflow_snapshot(session.root)
        observation = _observation(snapshot, evidence.transaction)
        target = (
            observation.evidence.target
            if observation is not None
            and isinstance(observation.evidence, HistoryClaimWal)
            else observation.evidence
            if observation is not None
            else None
        )
        if (
            _unusable_snapshot(snapshot) is not None
            or snapshot.pair != before_pair
            or _history_conditions(snapshot) != before_conditions
            or not isinstance(target, ReadableTargetWal)
            or not satisfies(_mutable(target.actual), _mutable(evidence.postimage))
        ):
            _retain_recovery(
                session.root, evidence, TARGET_UNCONFIRMED,
                "the selected target, WAL, or history pair changed after "
                "publication confirmation",
            )
        evidence = observation.evidence
        facts = _facts(evidence)
    if evidence.verdict == VERDICT_RESTORE_UNCONFIRMED:
        gated = _recovery_gate_problem(evidence, snapshot)
        if gated is not None:
            return gated
        before_pair = snapshot.pair
        before_conditions = _history_conditions(snapshot)
        try:
            session.republish_restore(facts)
        except (OSError, VisibilityUnconfirmed, JournalError) as error:
            _retain_recovery(session.root, evidence, RESTORE_UNCONFIRMED, error)
        rendezvous_at("after-selected-restore-recovery", 1)
        successor = _workflow_snapshot(session.root)
        refreshed = _observation(successor, evidence.transaction)
        target = (
            refreshed.evidence.target
            if refreshed is not None and isinstance(refreshed.evidence, HistoryClaimWal)
            else refreshed.evidence
            if refreshed is not None
            else None
        )
        if (
            _unusable_snapshot(successor) is not None
            or successor.pair != before_pair
            or _history_conditions(successor) != before_conditions
            or not isinstance(target, ReadableTargetWal)
            or not satisfies(_mutable(target.actual), _mutable(evidence.preimage))
        ):
            _retain_recovery(
                session.root,
                evidence,
                RESTORE_UNCONFIRMED,
                "the exact restored target and coherent history pair were not confirmed",
            )
        try:
            remove_transaction_file(session.root, evidence.transaction)
        except (OSError, VisibilityUnconfirmed, JournalError) as error:
            _retain_recovery(session.root, evidence, RESTORE_UNCONFIRMED, error)
        return Recovery(
            evidence.transaction, evidence.path, evidence.durability,
            action=REMOVED,
            message=f"transaction {evidence.transaction} restored state was confirmed",
        )
    if evidence.verdict == VERDICT_CLEANUP_UNCONFIRMED:
        _cleanup_recovered(session.root, evidence, snapshot=snapshot)
        return Recovery(
            evidence.transaction, evidence.path, evidence.durability,
            action=REMOVED,
            message=f"transaction {evidence.transaction} cleanup was confirmed",
        )
    if isinstance(evidence, HistoryClaimWal):
        if evidence.claim == "claimless" and evidence.occurrence == "zero":
            target = evidence.target
            if isinstance(target, ReadableTargetWal) and satisfies(
                _mutable(target.actual), _mutable(evidence.postimage)
            ):
                return _recover_reconstructed(session, snapshot, evidence)
            return _recovery_problem(
                evidence,
                f"transaction {evidence.transaction} cannot reconstruct history "
                "because its target is not the recorded published state",
            )
        return _recover_claimed(session, snapshot, observation)
    if evidence.verdict == VERDICT_COMPLETE:
        return _recover_reconstructed(session, snapshot, evidence)
    if evidence.verdict == PROBLEM_DIVERGED:
        message = (
            f"transaction {evidence.transaction} published {evidence.path}, but "
            f"{evidence.path} is {describe(facts['actual'])} now and not what was "
            f"published; nothing here can say whether that is wanted -- "
            f"{resolution_advice(evidence.transaction)}"
        )
    elif evidence.unconfirmed == TARGET_UNCONFIRMED:
        message = (
            f"transaction {evidence.transaction} left {evidence.path} with "
            "unconfirmed target durability, but it no longer has the exact "
            "postimage recorded by the transaction; recovery left the WAL in "
            "place"
        )
    elif facts.get("actual") is None:
        if evidence.stage == PUBLISHED:
            message = (
                f"transaction {evidence.transaction} published {evidence.path}, "
                f"and {evidence.path} cannot be read: {evidence.problem_reason}; "
                "nothing here can say whether what it published is still there "
                f"-- {resolution_advice(evidence.transaction)}"
            )
        else:
            message = (
                f"transaction {evidence.transaction} prepared a mutation of "
                f"{evidence.path}, and {evidence.path} cannot be read: "
                f"{evidence.problem_reason}; nothing here can say whether it ran "
                f"-- {resolution_advice(evidence.transaction)}"
            )
    else:
        message = (
            f"transaction {evidence.transaction} prepared a mutation of "
            f"{evidence.path}, whose current state is not proven; "
            f"{resolution_advice(evidence.transaction)}"
        )
    return _recovery_problem(evidence, message)


def _recover_all(session):
    """Own RecoverAll policy while the executor supplies target mechanics."""
    with Lock(session.root):
        initial = _workflow_snapshot(session.root)
        order = tuple(observation.evidence.transaction for observation in initial.wal)
        results = []
        for transaction in order:
            current = _workflow_snapshot(session.root)
            observation = _observation(current, transaction)
            if observation is None:
                continue
            results.append(_recover_item(session, current, observation))
        final = _workflow_snapshot(session.root)
        if not isinstance(final.compatibility, Compatible):
            raise final.compatibility.error
        return results, final.compatibility.repository, final.compatibility.local


def _resolution_refusal(evidence, disposition, message, location=None):
    return Resolution(
        evidence.transaction,
        disposition,
        location or evidence.path or transaction_artifact(evidence.transaction),
        message,
    )


def _resolution_gates(snapshot, selected):
    provisions = _valid_provisions(snapshot)
    gates = []
    for condition in snapshot.conditions:
        if (
            condition.identity.startswith("wal.")
            and f"transaction:{selected}" in condition.pairing
        ):
            continue
        if condition.identity.startswith("history.") and any(
            _is_exact_provision(condition, evidence) for evidence in provisions
        ):
            continue
        if condition.identity in {
            "history.topology_damage",
            "history.topology_gate",
            "history.unreadable",
            "history.malformed",
            "history.unsupported",
            "wal.diverged",
            "wal.unknown_readable",
            "wal.target_unreadable",
            "wal.damaged",
            "wal.unsupported",
        }:
            gates.append((condition.subject, condition.detail))
    return tuple(gates)


def _selected_unknown_provision(snapshot, observation):
    """Return whether this unknown WAL owns one exact prepared condition."""
    evidence = observation.evidence
    return evidence.verdict == PROBLEM_UNKNOWN and any(
        provision.transaction == evidence.transaction
        for provision in _valid_provisions(snapshot)
    )


def _retain_resolution(root, evidence, phase, reason):
    try:
        mark_unconfirmed(root, evidence.transaction, phase, str(reason))
    except (OSError, VisibilityUnconfirmed) as secondary:
        reason = f"{reason}; retaining the resolution fact was also unconfirmed: {secondary}"
    error = JournalError(
        None,
        f"the selected resolution effect is visible or may be visible, but "
        f"coherent successor confirmation is incomplete: {reason}. Transaction "
        f"{evidence.transaction} was retained. Preserve the selected WAL and "
        "histories, then run journal --check; do not repeat the disposition",
        evidence.path or transaction_artifact(evidence.transaction),
    )
    error.visibility_unconfirmed = True
    raise error


def _append_selected_mutation(session, snapshot, observation):
    evidence = observation.evidence
    if isinstance(evidence, HistoryClaimWal):
        if evidence.claim != "valid" or evidence.occurrence not in {
            "zero", "prepared", "complete"
        }:
            raise ValueError("selected transaction has no usable history authority")
        records = _mutable(observation.raw["history_append"])["records"]
        selected = (
            tuple(records)
            if evidence.occurrence == "zero"
            else (records[1],)
            if evidence.occurrence == "prepared"
            else ()
        )
    else:
        selected = _reconstructed_records(
            session, evidence, STAGES, published=_mutable(evidence.postimage)
        )
    if selected:
        return _append_successor(session.root, snapshot, evidence, selected)
    return snapshot


def _restore_selected(session, snapshot, evidence, disposition):
    facts = _facts(evidence)
    location = evidence.path
    before_pair = snapshot.pair
    before_conditions = _history_conditions(snapshot)
    before_observation = _observation(snapshot, evidence.transaction)

    def refuse(message):
        return _resolution_refusal(evidence, disposition, message)

    if any(
        entry.get("transaction") == evidence.transaction
        for entry in snapshot.compatibility.histories[evidence.durability]
    ):
        return refuse(
            f"transaction {evidence.transaction} is already recorded in "
            f"{artifact_name(evidence.durability)}; append-only history is not "
            "reversed. Use --accept or --abandon instead. Nothing has been "
            "restored."
        )
    preimage = facts["preimage"]
    kind = preimage["kind"]
    if kind == DIRECTORY:
        return refuse(
            f"the preimage of {location} is a directory whose contents were "
            "not parked. Use --accept or --abandon. Nothing has been restored."
        )
    intention = None
    data = None
    mode = None
    if kind == FILE:
        reference = evidence.preimage_blob
        if reference is None:
            return refuse(
                f"transaction {evidence.transaction} names no trusted preimage "
                f"bytes for {location}. Nothing has been restored."
            )
        blob = (
            session.root
            / VAULT_DIRNAME
            / "preimages"
            / reference.removeprefix("sha256:")
        )
        try:
            data = read_regular_file(blob)
        except FileNotFoundError:
            return refuse(
                f"the preimage of {location}, {reference}, is not in "
                f"{VAULT_DIRNAME}/preimages/. This open transaction therefore "
                "has a damaged log. Nothing has been restored."
            )
        except OSError as error:
            return refuse(
                f"the trusted preimage of {location}, {reference}, is unavailable: "
                f"{error}. Nothing has been restored."
            )
        if digest(data) != reference:
            return refuse(
                f"the preimage of {location} in {VAULT_DIRNAME}/preimages/ "
                f"does not digest to {reference}, the name it is filed under, "
                "so it is not the bytes this transaction parked. Nothing has "
                "been restored."
            )
        mode = evidence.mode if evidence.mode is not None else preimage.get("mode")
        if not isinstance(mode, int) or isinstance(mode, bool):
            return refuse(
                f"transaction {evidence.transaction} records no usable mode for "
                f"the preimage of {location}. Nothing has been restored."
            )
        intention = replace_file(
            purpose=facts["intention"]["purpose"],
            path=location,
            durability=evidence.durability,
            expected=preimage,
            content=data,
        )
    elif kind == SYMLINK:
        target = preimage.get("target")
        if not isinstance(target, str):
            return refuse(
                f"transaction {evidence.transaction} records no target for the "
                f"preimage symlink at {location}. Nothing has been restored."
            )
        intention = link_to(
            purpose=facts["intention"]["purpose"],
            path=location,
            durability=evidence.durability,
            expected=preimage,
            target=target,
        )
    elif kind != ABSENT:
        return refuse(
            f"the preimage of {location} is not restorable. Nothing has been "
            "restored."
        )
    try:
        kept = session.restore_effect(
            location, facts["actual"], intention, mode, data
        )
    except VisibilityUnconfirmed as error:
        _retain_resolution(session.root, evidence, RESTORE_UNCONFIRMED, error)
    except OSError as error:
        return refuse(
            f"{location} could not be put back: {error}. Nothing has been "
            "restored."
        )
    rendezvous_at("after-selected-resolution-restore", 1)
    try:
        if not satisfies(current_state(session.root, location), preimage):
            _retain_resolution(
                session.root,
                evidence,
                RESTORE_UNCONFIRMED,
                "the restored target did not match the exact preimage",
            )
        successor = _workflow_snapshot(session.root)
        refreshed = _observation(successor, evidence.transaction)
        if (
            _unusable_snapshot(successor) is not None
            or successor.pair != before_pair
            or _history_conditions(successor) != before_conditions
            or before_observation is None
            or refreshed is None
            or _mutable(refreshed.raw) != _mutable(before_observation.raw)
        ):
            _retain_resolution(
                session.root,
                evidence,
                RESTORE_UNCONFIRMED,
                "the selected WAL or coherent history pair changed after restore",
            )
        mark_unconfirmed(
            session.root,
            evidence.transaction,
            RESTORE_UNCONFIRMED,
            "restored preimage and histories confirmed; cleanup pending",
        )
        marked = _workflow_snapshot(session.root)
        if (
            _unusable_snapshot(marked) is not None
            or marked.pair != successor.pair
            or _history_conditions(marked) != _history_conditions(successor)
            or _observation(marked, evidence.transaction) is None
            or not satisfies(current_state(session.root, location), preimage)
        ):
            _retain_resolution(
                session.root,
                evidence,
                RESTORE_UNCONFIRMED,
                "the restored target was not confirmed before cleanup",
            )
        remove_transaction_file(session.root, evidence.transaction)
    except (OSError, VisibilityUnconfirmed, JournalError) as error:
        _retain_resolution(session.root, evidence, RESTORE_UNCONFIRMED, error)
    final = _workflow_snapshot(session.root)
    return Resolution(
        evidence.transaction,
        disposition,
        location,
        kept=kept,
        gates=_resolution_gates(final, evidence.transaction),
    )


def resolve_one(root, transaction_id, disposition):
    """Own ResolveOne selection, authority, successor and cleanup policy."""
    if disposition not in RESOLUTIONS:
        raise ValueError(f"unknown resolution '{disposition}'")
    root = Path(root)
    if not has_transaction(root, transaction_id):
        return Resolution(
            transaction_id,
            disposition,
            transaction_artifact(transaction_id),
            no_such_transaction(transaction_id),
        )
    rendezvous_at("before-resolution-lock", 1)
    with Lock(root):
        ensure_history_compatibility()
        item = read_transaction(root, transaction_id)
        if item is None:
            return Resolution(
                transaction_id,
                disposition,
                transaction_artifact(transaction_id),
                no_such_transaction(transaction_id),
            )
        snapshot = _workflow_snapshot(root)
        if not isinstance(snapshot.compatibility, Compatible):
            raise snapshot.compatibility.error
        adoption = existing_adoption_id(
            snapshot.compatibility.repository,
            snapshot.compatibility.local,
        )
        if adoption is None:
            return Resolution(
                transaction_id,
                disposition,
                transaction_artifact(transaction_id),
                f"transaction {transaction_id} has no adoption history in "
                f"{JOURNAL_FILENAME} or {artifact_name(LOCAL)}; a transaction "
                "cannot establish which adopter it belongs to. Nothing has "
                "been changed.",
            )
        observation = _observation(snapshot, transaction_id)
        if observation is None:
            return Resolution(
                transaction_id,
                disposition,
                transaction_artifact(transaction_id),
                no_such_transaction(transaction_id),
            )
        evidence = observation.evidence
        if isinstance(evidence, UnsupportedWal):
            return _resolution_refusal(
                evidence,
                disposition,
                f"transaction {transaction_id} uses WAL schema {evidence.found}, "
                f"newer than this reader's maximum {evidence.maximum}. Nothing "
                "has been changed.",
                transaction_artifact(transaction_id),
            )
        if isinstance(evidence, DamagedWal):
            return _resolution_refusal(
                evidence,
                disposition,
                f"transaction {transaction_id} is damaged "
                f"({evidence.problem_reason}); preserve its artifact and restore "
                "trusted evidence. Nothing has been changed.",
                transaction_artifact(transaction_id),
            )
        if isinstance(evidence, HistoryClaimWal) and (
            evidence.claim == "claimless"
            and evidence.occurrence == "zero"
            and isinstance(evidence.target, ReadableTargetWal)
            and satisfies(
                _mutable(evidence.target.actual), _mutable(evidence.postimage)
            )
        ):
            return _resolution_refusal(
                evidence,
                disposition,
                f"transaction {transaction_id} on {evidence.path} is "
                "recoverable: the next 'validated-memory init' completes it. "
                "Nothing has been changed.",
            )
        if evidence.verdict not in {PROBLEM_DIVERGED, PROBLEM_UNKNOWN}:
            return _resolution_refusal(
                evidence,
                disposition,
                f"transaction {transaction_id} on {evidence.path} is "
                f"{RECOVERABLE}: the next 'validated-memory init' completes "
                "recovery. Nothing has been changed.",
            )
        facts = _facts(evidence)
        if facts.get("actual") is None:
            return _resolution_refusal(
                evidence,
                disposition,
                f"{evidence.path} could not be read "
                f"({evidence.problem_reason}); restore read access and rerun "
                "journal --check. Nothing has been changed.",
            )
        session = Run(
            root,
            new_id(),
            adoption,
            append_history=lambda records, durability: append(
                records, root, durability
            ),
            claim_history=lambda transaction, claim: mark_history_append(
                root, transaction, claim
            ),
            cleanup_transaction=lambda transaction: remove_transaction_file(
                root, transaction
            ),
        )
        if (
            isinstance(evidence, HistoryClaimWal)
            and evidence.claim == "valid"
            and evidence.unconfirmed == HISTORY_CLAIM_UNCONFIRMED
        ):
            snapshot, observation = _reestablish_claim_evidence(
                session,
                snapshot,
                observation,
                resolution=True,
            )
            evidence = observation.evidence
            facts = _facts(evidence)
        if _selected_unknown_provision(snapshot, observation):
            return _resolution_refusal(
                evidence,
                disposition,
                f"transaction {transaction_id} cannot be resolved because its "
                "exact prepared history provision remains unfinished while its "
                "target state is unknown. Preserve the WAL and histories; restore "
                "an accepted exact history state from a trusted source, then run "
                "journal --check. Nothing has been changed.",
            )
        gate = _mutation_gate(snapshot)
        if gate is not None:
            return _resolution_refusal(
                evidence,
                disposition,
                f"transaction {transaction_id} cannot change retained state "
                f"because {gate}. Nothing has been changed.",
            )
        if disposition == RESTORE:
            return _restore_selected(session, snapshot, evidence, disposition)
        if evidence.verdict == PROBLEM_DIVERGED:
            try:
                snapshot = _append_selected_mutation(session, snapshot, observation)
            except (OSError, VisibilityUnconfirmed, JournalError) as error:
                _retain_resolution(root, evidence, HISTORY_UNCONFIRMED, error)
            except ValueError as error:
                return _resolution_refusal(
                    evidence,
                    disposition,
                    f"transaction {transaction_id} conflicts with permanent "
                    f"history: {error}. Nothing has been changed.",
                )
        found = facts["actual"]["kind"]
        note = (
            f"accepted after divergence: transaction {transaction_id} found {found}"
            if disposition == ACCEPT
            else f"abandoned: transaction {transaction_id}, path left as found"
        )
        observation_record = session.build_record(
            OBSERVE,
            facts["intention"]["purpose"],
            evidence.path,
            evidence.durability,
            COMMITTED,
            note=note,
        )
        try:
            snapshot = _append_successor(
                root, snapshot, evidence, (observation_record,)
            )
        except (OSError, VisibilityUnconfirmed, JournalError) as error:
            _retain_resolution(root, evidence, HISTORY_UNCONFIRMED, error)
        confirmed = _workflow_snapshot(root)
        if (
            _unusable_snapshot(confirmed) is not None
            or confirmed.pair != snapshot.pair
            or _history_conditions(confirmed) != _history_conditions(snapshot)
        ):
            _retain_resolution(
                root,
                evidence,
                CLEANUP_UNCONFIRMED,
                "the coherent history pair changed before selected cleanup",
            )
        try:
            mark_unconfirmed(
                root,
                transaction_id,
                CLEANUP_UNCONFIRMED,
                "selected resolution and successor confirmed; cleanup pending",
            )
            marked = _workflow_snapshot(root)
            if (
                _unusable_snapshot(marked) is not None
                or marked.pair != confirmed.pair
                or _history_conditions(marked) != _history_conditions(confirmed)
                or _observation(marked, transaction_id) is None
            ):
                raise JournalError(
                    None,
                    "the selected WAL or coherent history pair changed before cleanup",
                    transaction_artifact(transaction_id),
                )
            remove_transaction_file(root, transaction_id)
        except (OSError, VisibilityUnconfirmed, JournalError) as error:
            _retain_resolution(root, evidence, CLEANUP_UNCONFIRMED, error)
        final = _workflow_snapshot(root)
        return Resolution(
            transaction_id,
            disposition,
            evidence.path,
            gates=_resolution_gates(final, transaction_id),
        )


def _repair_expected_records(item, transaction):
    """Derive the only record pair one proof-carrying WAL may authorize."""
    intention = item.get("intention")
    postimage = item.get("postimage")
    if not isinstance(intention, Mapping) or not isinstance(postimage, Mapping):
        return None
    required = {
        "schema": item.get("schema"),
        "version": item.get("version"),
        "adoption": item.get("adoption"),
        "run": item.get("run"),
        "durability": intention.get("durability"),
        "op": intention.get("op"),
        "purpose": intention.get("purpose"),
        "path": intention.get("path"),
        "transaction": transaction,
    }
    if any(value is None for value in required.values()):
        return None
    extra = {}
    mode = item.get("published_mode")
    if mode is None and postimage.get("kind") == FILE:
        return None
    if mode is not None:
        extra["mode"] = mode
    if intention.get("note") is not None:
        extra["note"] = intention["note"]
    if postimage.get("kind") == FILE:
        extra["preimage"] = item.get("preimage_blob")
        extra["postimage"] = postimage.get("digest")
        if item.get("prior_bytes") is not None:
            extra["prior_bytes"] = item["prior_bytes"]
    return tuple(
        {**required, "stage": stage, **extra}
        for stage in (PREPARED, COMMITTED)
    )


def _repair_precondition(item, transaction):
    """Return why one selected WAL does not carry exact repair authority."""
    if item.get("damaged"):
        return f"the selected WAL is damaged: {item['damaged']}"
    if item.get("stage") != PUBLISHED or item.get("unconfirmed") not in (
        None,
        HISTORY_UNCONFIRMED,
    ):
        return "the selected WAL is not a current published history repair"
    claim = item.get("history_append")
    if not isinstance(claim, Mapping):
        return "the selected WAL has no proof-carrying history append"
    if claim.get("artifact") not in (REPO, LOCAL):
        return "the selected WAL names an invalid history artifact"
    if claim.get("encoding") != "json-sorted-keys-utf8-lf":
        return "the selected WAL names an unsupported history encoding"
    records = claim.get("records")
    if not isinstance(records, list) or len(records) != 2:
        return "the selected WAL does not carry exactly two history records"
    if [entry.get("stage") for entry in records if isinstance(entry, Mapping)] != [
        PREPARED,
        COMMITTED,
    ]:
        return "the selected WAL does not carry prepared then committed records"
    if any(
        not isinstance(entry, Mapping)
        or entry.get("transaction") != transaction
        for entry in records
    ):
        return "the selected WAL carries a foreign history record"
    if any(entry.get("adoption") != item.get("adoption") for entry in records):
        return "the selected WAL carries a foreign adoption identity"
    expected = _repair_expected_records(item, transaction)
    if expected is None:
        return "the selected WAL does not contain a complete intention"
    for actual, wanted in zip(records, expected):
        if set(actual) != set(wanted) | {"at", "version"}:
            return "the history claim fields do not match the selected WAL"
        if any(actual.get(field) != value for field, value in wanted.items()):
            return "the history claim is not bound to the selected WAL"
        if (
            not isinstance(actual.get("at"), str)
            or actual.get("version") != item.get("version")
        ):
            return "the history claim metadata is invalid"
    shared = (
        "run",
        "adoption",
        "transaction",
        "durability",
        "op",
        "purpose",
        "path",
        "preimage",
        "postimage",
        "mode",
        "prior_bytes",
    )
    if any(records[0].get(field) != records[1].get(field) for field in shared):
        return "the selected WAL carries an inconsistent history pair"
    prefix = claim.get("prefix")
    append_claim = claim.get("append")
    timestamps = claim.get("timestamps")
    if not all(
        (
            isinstance(prefix, Mapping),
            isinstance(append_claim, Mapping),
            isinstance(timestamps, list),
        )
    ):
        return "the selected WAL has an incomplete history proof"
    if timestamps != item.get("history_timestamps"):
        return "the history timestamps are not bound to the selected WAL"
    if timestamps != [entry.get("at") for entry in records]:
        return "the history timestamps are not proof-bound"
    try:
        payload = encode_records(records)
        if (
            append_claim.get("length") != len(payload)
            or append_claim.get("digest") != digest(payload)
        ):
            return "the history append proof does not match its records"
        prefix_length = prefix.get("length")
        if (
            type(prefix_length) is not int
            or prefix_length < 0
            or not isinstance(prefix.get("digest"), str)
        ):
            raise ValueError
    except (TypeError, ValueError):
        return "the selected WAL has an invalid history proof"
    return None


def _repair_candidate(root, pair, item, transaction):
    """Build and inspect the exact candidate pair before publication."""
    claim = item["history_append"]
    durability = claim["artifact"]
    selected = pair.repository if durability == REPO else pair.local
    opposite = pair.local if durability == REPO else pair.repository
    if selected.error is not None:
        raise JournalError(None, selected.error.message, artifact_name(durability))
    if selected.data is None:
        raise ValueError(
            "the selected history is absent; one WAL cannot reconstruct it"
        )
    data = selected.data
    repair_complete_prefix(data, artifact_name(durability))
    prefix = claim["prefix"]
    payload_records = claim["records"]
    payload = encode_records(payload_records)
    prefix_length = prefix["length"]
    if prefix_length > len(data):
        raise ValueError("the claimed history prefix is not present")
    if digest(data[:prefix_length]) != prefix["digest"]:
        raise ValueError("the history prefix changed since the WAL proof")
    tail = data[prefix_length:]
    if tail and not payload.startswith(tail):
        raise ValueError("history EOF bytes are not a prefix of the claimed append")
    if len(tail) > len(payload):
        raise ValueError("history contains unrelated bytes after the claimed frontier")
    target = item.get("intention", {}).get("path")
    if not isinstance(target, str):
        raise ValueError("the selected WAL has no target path")
    if not satisfies(current_state(root, target), item.get("postimage", {})):
        raise ValueError("the target no longer has the WAL postimage required for repair")
    candidate = data[:prefix_length] + payload
    candidate_raw = RawHistory(
        candidate,
        selected.mode,
        selected.generation,
    )
    candidate_pair = (
        RawHistoryPair(candidate_raw, opposite)
        if durability == REPO
        else RawHistoryPair(opposite, candidate_raw)
    )
    histories = {
        REPO: tuple(parse_acquired_history(candidate_pair.repository, REPO)),
        LOCAL: tuple(parse_acquired_history(candidate_pair.local, LOCAL)),
    }
    occurrences = tuple(
        entry
        for records in histories.values()
        for entry in records
        if entry.get("transaction") == transaction
    )
    if occurrences != tuple(payload_records):
        raise ValueError(
            "the complete candidate does not contain exactly one claimed record pair"
        )
    identities = {
        entry.get("adoption")
        for records in histories.values()
        for entry in records
        if isinstance(entry.get("adoption"), str)
    }
    if identities != {item.get("adoption")}:
        raise ValueError("the candidate pair has an adoption mismatch")
    try:
        topology = inspect_topology(candidate_pair)
    except Exception as error:
        raise ValueError(
            "candidate topology inspection is unavailable: "
            f"{type(error).__name__}: {error}"
        ) from error
    unfinished, disagreements, anomalies = reconcile(histories, root)
    observations, residue = _observe_wal(
        root,
        candidate_pair,
        histories,
        item.get("adoption"),
        include_stored_claim=True,
    )
    conditions = tuple(sorted((
        *_topology_conditions(topology),
        *_legacy_conditions(unfinished, disagreements, anomalies),
        *(_wal_condition(observation) for observation in observations),
        *(
            ProtocolCondition(
                (4, location, message),
                "wal.damaged",
                location,
                (),
                message,
                message,
            )
            for location, message in residue
        ),
    )))
    selected_key = f"transaction:{transaction}"
    if any(
        selected_key in condition.pairing
        and condition.identity.startswith("history.")
        for condition in conditions
    ):
        raise ValueError("the candidate pair still involves the selected transaction")
    if any(
        condition.detail == "transaction_identity_damage"
        for condition in conditions
    ):
        raise ValueError("the candidate pair reuses a transaction identity")
    state = WorkflowInspection(
        candidate_pair,
        Compatible(histories[REPO], histories[LOCAL]),
        topology,
        observations,
        residue,
        conditions,
        tuple(unfinished),
        tuple(disagreements),
        tuple(anomalies),
    )
    return state, data, candidate, selected.mode


def _repair_refused(root, transaction, location, reason, result=Refused):
    inspection = _HistoryWorkflow(root)._inspect(
        True, include_stored_claim=True
    ).inspection
    message = (
        f"transaction {transaction} does not prove this repair: {reason}. "
        "No target or permanent-history change was left by this operation. "
        "Preserve all evidence and select a transaction carrying the required "
        "valid proof, or restore exact trusted history."
    )
    return result(
        inspection,
        RepairPresentation(transaction, location, message),
    )


def _repair_classified(root, transaction, location, result, message):
    """Return one damaged or unsupported repair result without policy inference."""
    inspection = _HistoryWorkflow(root)._inspect(
        True, include_stored_claim=True
    ).inspection
    return result(
        inspection,
        RepairPresentation(transaction, location, message),
    )


def _repair_retained(root, transaction, location, reason):
    inspection = _HistoryWorkflow(root)._inspect(
        True, include_stored_claim=True
    ).inspection
    message = (
        "the selected repair effect is visible or may be visible, but coherent "
        f"successor confirmation failed: {reason}. Transaction {transaction} "
        "was retained. Preserve both histories and the WAL; rerun "
        f"journal --repair {transaction}."
    )
    return Retained(
        inspection,
        RepairPresentation(transaction, location, message),
    )


def _stat_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size)


def _read_repair_wal(root, transaction):
    """Freeze one selected WAL from a descriptor-bound locked read."""
    path = Path(root) / transaction_artifact(transaction)
    try:
        data, mode, info = read_file_snapshot(path, identify=True)
        text = data.decode("utf-8")
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("transaction is not a JSON object")
    except UnicodeError as error:
        return None, None, f"not valid UTF-8: {error}"
    except json.JSONDecodeError as error:
        return None, None, f"not valid JSON: {error.msg}"
    except (OSError, ValueError) as error:
        return None, None, str(error)
    value["id"] = transaction
    return value, _FrozenRepairWal(path, data, mode, _stat_identity(info)), None


def _repair_wal_matches(frozen):
    data, mode, info = read_file_snapshot(frozen.path, identify=True)
    return (
        data == frozen.data
        and mode == frozen.mode
        and _stat_identity(info) == frozen.identity
    )


def _freeze_repair_temporary(root, item):
    """Validate and freeze the selected WAL's exact staging candidate."""
    claim = item.get("temporary")
    if claim is None:
        return None
    intention = item.get("intention")
    postimage = item.get("postimage")
    if not all(isinstance(value, Mapping) for value in (claim, intention, postimage)):
        raise OSError("temporary claim is malformed")
    target_name = claim.get("target")
    if (
        target_name != intention.get("path")
        or claim.get("transaction") != item.get("transaction")
        or claim.get("role") != "target-staging"
        or claim.get("adoption") != item.get("adoption")
    ):
        raise OSError("temporary claim is not bound to the selected WAL")
    expected_kind = "symlink" if intention.get("op") == "link" else "regular-file"
    expected_mode = None if expected_kind == "symlink" else item.get("mode")
    if claim.get("kind") != expected_kind or claim.get("mode") != expected_mode:
        raise OSError("temporary claim kind or mode does not match the WAL")
    if expected_kind == "symlink":
        expected_target = intention.get("target")
        if (
            postimage.get("kind") != SYMLINK
            or postimage.get("target") != expected_target
            or not isinstance(expected_target, str)
        ):
            raise OSError("temporary symlink is not bound to the WAL postimage")
        expected_data = os.fsencode(expected_target)
    else:
        if postimage.get("kind") != FILE or not isinstance(postimage.get("digest"), str):
            raise OSError("temporary file is not bound to the WAL postimage")
        expected_data = None
    expected_digest = (
        digest(expected_data) if expected_data is not None else postimage["digest"]
    )
    if claim.get("digest") != expected_digest:
        raise OSError("temporary claim digest is not bound to the WAL postimage")
    if intention.get("durability") == REPO and not is_inside_path(target_name):
        raise OSError("temporary claim leaves the adopter root")
    target = (
        Path(root) / target_name
        if intention.get("durability") == REPO
        else Path(target_name)
    )
    try:
        parent_info = target.parent.lstat()
        if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode):
            raise OSError
        parent_identity = (parent_info.st_dev, parent_info.st_ino)
        resolved_parent = target.parent.resolve(strict=True)
        if intention.get("durability") == REPO:
            resolved_parent.relative_to(Path(root).resolve(strict=True))
    except (FileNotFoundError, OSError, ValueError) as error:
        raise OSError("temporary candidate parent is outside the validated target") from error
    if not satisfies(current_state(root, target_name), item.get("postimage", {})):
        raise OSError("target no longer has the WAL postimage required for cleanup")
    name = claim.get("name")
    if (
        not isinstance(name, str)
        or Path(name).name != name
        or not name.startswith(f".{target.name}.")
        or not name.endswith(".tmp")
    ):
        raise OSError("temporary claim name is outside the current staging grammar")
    candidate = target.parent / name
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        return None
    if claim.get("kind") == "regular-file":
        candidate_data, candidate_mode, held = read_file_snapshot(
            candidate, identify=True
        )
        if (
            not stat.S_ISREG(info.st_mode)
            or digest(candidate_data) != expected_digest
            or candidate_mode != claim.get("mode")
            or (held.st_dev, held.st_ino) != (info.st_dev, info.st_ino)
        ):
            raise OSError("temporary candidate does not match its claim")
    else:
        candidate_data = os.fsencode(os.readlink(candidate))
        after = candidate.lstat()
        if (
            not stat.S_ISLNK(info.st_mode)
            or candidate_data != expected_data
            or (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino)
        ):
            raise OSError("temporary candidate does not match its claim")
    return _FrozenRepairTemporary(
        candidate,
        parent_identity,
        resolved_parent,
        expected_kind,
        stat.S_IMODE(info.st_mode),
        (info.st_dev, info.st_ino),
        candidate_data,
    )


def _cleanup_frozen_temporary(frozen):
    """Remove a staging candidate only while every frozen fact still matches."""
    if frozen is None:
        return
    candidate = frozen.path
    try:
        parent_after = candidate.parent.lstat()
        after = candidate.lstat()
    except FileNotFoundError as error:
        raise OSError("temporary candidate changed before cleanup") from error
    if (
        (parent_after.st_dev, parent_after.st_ino) != frozen.parent_identity
        or (after.st_dev, after.st_ino) != frozen.identity
        or stat.S_IMODE(after.st_mode) != frozen.mode
        or candidate.parent.resolve(strict=True) != frozen.resolved_parent
    ):
        raise OSError("temporary candidate changed before cleanup")
    if frozen.kind == "regular-file":
        data, mode, held = read_file_snapshot(candidate, identify=True)
        if (
            not stat.S_ISREG(after.st_mode)
            or data != frozen.data
            or mode != frozen.mode
            or (held.st_dev, held.st_ino) != frozen.identity
        ):
            raise OSError("temporary candidate changed before cleanup")
    else:
        data = os.fsencode(os.readlink(candidate))
        final = candidate.lstat()
        if (
            not stat.S_ISLNK(after.st_mode)
            or data != frozen.data
            or (final.st_dev, final.st_ino) != frozen.identity
        ):
            raise OSError("temporary candidate changed before cleanup")
    remove_name(candidate)


def _repair_condition_domain(snapshot, transaction):
    """Return all authoritative gates except the selected repair WAL itself."""
    selected = f"transaction:{transaction}"
    return frozenset(
        condition
        for condition in snapshot.conditions
        if not (
            condition.identity.startswith("wal.")
            and selected in condition.pairing
        )
    )


def repair_one(root, transaction):
    """Own RepairOne proof, candidate inspection, successor and cleanup."""
    root = Path(root)
    if not has_transaction(root, transaction):
        return _repair_refused(
            root,
            transaction,
            transaction_artifact(transaction),
            "there is no unresolved transaction with that id",
        )
    with Lock(root):
        item, frozen_wal, wal_error = _read_repair_wal(root, transaction)
        if item is None:
            if wal_error is not None and has_transaction(root, transaction):
                return _repair_classified(
                    root,
                    transaction,
                    transaction_artifact(transaction),
                    Damaged,
                    f"damaged transaction evidence: {wal_error}. No target or "
                    "permanent-history change was left by this operation. "
                    "Preserve this evidence and restore the exact artifact from a "
                    "trusted source before rerunning journal --repair "
                    f"{transaction}.",
                )
            return _repair_refused(
                root,
                transaction,
                transaction_artifact(transaction),
                "there is no unresolved transaction with that id",
            )
        schema = item.get("schema")
        if (
            isinstance(schema, int)
            and not isinstance(schema, bool)
            and schema > WAL_SCHEMA
        ):
            return _repair_classified(
                root,
                transaction,
                transaction_artifact(transaction),
                Unsupported,
                f"protocol {schema} is newer than this reader (maximum "
                f"{WAL_SCHEMA}). No target or permanent-history change was "
                "left by this operation. Install a compatible validated-memory "
                f"version and rerun journal --repair {transaction}.",
            )
        reason = _repair_precondition(item, transaction)
        location = transaction_artifact(transaction)
        claim = item.get("history_append")
        if isinstance(claim, Mapping) and claim.get("artifact") in (REPO, LOCAL):
            location = artifact_name(claim["artifact"])
        if reason is not None:
            return _repair_refused(root, transaction, location, reason)
        try:
            pair = acquire_history_pair(root)
            if isinstance(pair, RawHistoryFailure):
                raise pair.error
            for competing in open_transactions(root):
                competing_claim = competing.get("history_append")
                if (
                    competing.get("id") != transaction
                    and isinstance(competing_claim, Mapping)
                    and competing_claim.get("artifact") == claim["artifact"]
                    and competing_claim.get("prefix") == claim["prefix"]
                ):
                    raise ValueError("another current WAL claims the same history frontier")
            candidate_state, source, candidate, mode = _repair_candidate(
                root, pair, item, transaction
            )
            frozen_conditions = _repair_condition_domain(
                candidate_state, transaction
            )
            frozen_temporary = _freeze_repair_temporary(root, item)
            rendezvous_at("after-repair-inspection", 1)
            if not _repair_wal_matches(frozen_wal):
                raise ValueError("the selected WAL changed before publication")
        except (JournalError, OSError, KeyError, TypeError, ValueError) as error:
            return _repair_refused(root, transaction, location, str(error))
        history = journal_path(root, claim["artifact"])

        def verify(staging):
            staged_data, staged_mode = read_file_snapshot(staging)
            if staged_data != candidate or staged_mode != mode:
                raise OSError("the staged repair candidate changed before publication")
            if not _repair_wal_matches(frozen_wal):
                raise OSError("the selected WAL changed before publication")
            current_pair = acquire_history_pair(root)
            if isinstance(current_pair, RawHistoryFailure) or current_pair != pair:
                raise OSError("the coherent history pair changed before publication")
            current_state, current_source, current_candidate, current_mode = (
                _repair_candidate(root, current_pair, item, transaction)
            )
            if (
                current_source != source
                or current_candidate != candidate
                or current_mode != mode
                or _repair_condition_domain(current_state, transaction)
                != frozen_conditions
            ):
                raise OSError("the repair proof changed before publication")

        try:
            installed = repair_history_target(
                history, source, candidate, mode, verify=verify
            )
        except VisibilityUnconfirmed as error:
            return _repair_retained(root, transaction, location, str(error))
        except (JournalError, OSError, KeyError, TypeError, ValueError) as error:
            return _repair_refused(root, transaction, location, str(error))
        try:
            rendezvous_at("after-repair-publication", 1)
            successor = _workflow_snapshot(root)
            if (
                not isinstance(successor.compatibility, Compatible)
                or successor.pair is None
                or successor.topology is None
                or isinstance(successor.topology, TopologyUnavailable)
            ):
                raise OSError("coherent successor inspection is unavailable")
            selected = (
                successor.pair.repository
                if claim["artifact"] == REPO
                else successor.pair.local
            )
            opposite = (
                successor.pair.local
                if claim["artifact"] == REPO
                else successor.pair.repository
            )
            frozen_opposite = (
                candidate_state.pair.local
                if claim["artifact"] == REPO
                else candidate_state.pair.repository
            )
            generation = selected.generation
            installed_identity = (
                installed.st_dev,
                installed.st_ino,
                installed.st_mode,
                installed.st_size,
            )
            if (
                selected.data != candidate
                or selected.mode != mode
                or generation is None
                or generation[:4] != installed_identity
                or opposite != frozen_opposite
            ):
                raise OSError("the published pair changed identity, mode, or bytes")
            successor_conditions = _repair_condition_domain(
                successor, transaction
            )
            selected_key = f"transaction:{transaction}"
            if any(
                selected_key in condition.pairing
                and condition.identity.startswith("history.")
                for condition in successor_conditions
            ):
                raise OSError("the selected repair condition was not discharged")
            if not successor_conditions.issubset(frozen_conditions):
                raise OSError("a new condition appeared after publication")
            rendezvous_at("before-repair-cleanup", 1)
            if not _repair_wal_matches(frozen_wal):
                raise OSError("the selected WAL changed before cleanup")
            _cleanup_frozen_temporary(frozen_temporary)
            cleanup_private_duplicates(root)
            if not _repair_wal_matches(frozen_wal):
                raise OSError("the selected WAL changed before cleanup")
            rendezvous_at("after-repair-auxiliary-cleanup", 1)
            final = _workflow_snapshot(root)
            if (
                not isinstance(final.compatibility, Compatible)
                or final.pair is None
                or final.topology is None
                or isinstance(final.topology, TopologyUnavailable)
                or final.pair != successor.pair
            ):
                raise OSError("final coherent snapshot is unavailable")
            final_conditions = _repair_condition_domain(final, transaction)
            if any(
                selected_key in condition.pairing
                and condition.identity.startswith("history.")
                for condition in final_conditions
            ):
                raise OSError(
                    "the selected repair condition returned before selected WAL cleanup"
                )
            if not final_conditions.issubset(frozen_conditions):
                raise OSError("a new condition appeared before selected WAL cleanup")
            if not _repair_wal_matches(frozen_wal):
                raise OSError("the selected WAL changed before cleanup")
            remove_transaction_file(root, transaction)
        except (JournalError, OSError, VisibilityUnconfirmed) as error:
            return _repair_retained(root, transaction, location, str(error))
        gates = tuple(sorted(final_conditions))
        presentation = RepairPresentation(transaction, location, gates=gates)
        if gates:
            return ConfirmedWithGates(final, presentation)
        return Completed(final, presentation)


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
            "the canonical name. Use a supported local filesystem or platform, "
            "then rerun init"
        )
    except FileExistsError:
        return _refused(
            "another artifact reached the canonical name before bootstrap; "
            "it was preserved and not replaced. Preserve it. If it may be a "
            "valid established opening, rerun init so the classifier decides. "
            "Otherwise restore the correct exact canonical artifact from a "
            "trusted source under operator control, then rerun journal --check "
            "and init. The plugin does not remove or rename it"
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
def adopting_run(root=Path(), deadline=None):
    """Yield one protocol-owned adopting session under the run-wide lock.

    `deadline` is the `time.monotonic()` instant at which waiting for the lock
    ends; without one the wait is `LOCK_WAIT_SECONDS` from the call. A run
    that also calls `guarded_harness_repair` passes both the same instant.
    """
    root = Path(root)
    # An inspector implementation failure is knowable without materializing
    # mutation infrastructure. Refuse it read-only first so a virgin tree or
    # a lone established opening retains its exact names and bytes. Every
    # workflow that can proceed reacquires the authoritative snapshot under
    # the run-wide lock below.
    preliminary = _workflow_snapshot(root)
    if isinstance(preliminary.topology, TopologyUnavailable):
        _raise_adoption_gate(preliminary, before_effect=True)
    with Lock(root, deadline):
        ensure_history_compatibility()
        run = new_id()
        before = _workflow_snapshot(root)
        _raise_adoption_gate(before, before_effect=True)
        transactions = open_transactions(root)
        if not before.compatibility.records and transactions:
            from .transactions import historyless_transactions_message
            error = JournalError(
                None,
                historyless_transactions_message(transactions),
                f"{VAULT_DIRNAME}/transactions",
            )
            error.stops_adoption = True
            raise error
        records = before.compatibility.repository
        local = before.compatibility.local
        rendezvous_at("before-identity-transition", 1)
        result = _identity_transition(root, run, records, local)
        if isinstance(result, IdentityRefused):
            error = JournalError(None, result.message, result.artifact)
            error.stops_adoption = True
            raise error
        if isinstance(result, IdentityRetained):
            error = JournalError(None, result.message, result.artifact)
            error.stops_adoption = True
            raise error
        if not isinstance(result, IdentityConfirmed):
            raise TypeError("identity transition returned an unknown closed result")
        mechanics = Run(
            root,
            run,
            result.adoption,
            append_history=lambda records, durability: append(
                records, root, durability
            ),
            claim_history=lambda transaction, claim: mark_history_append(
                root, transaction, claim
            ),
            cleanup_transaction=lambda transaction: remove_transaction_file(
                root, transaction
            ),
            append_failure=_append_failure,
            recover_all=_recover_all,
            confirm_effect=lambda transaction, location: _confirm_current_effect(
                root, transaction, location
            ),
        )
        mechanics.survey(result.repository, result.local)
        snapshot = _workflow_snapshot(root)
        _raise_adoption_gate(snapshot)
        workflow = _HistoryWorkflow(root, mechanics)
        adopted = workflow.perform(Adopt(None))
        yield _ProtocolAdoptionSession(workflow, adopted.value)


class _ProtocolAdoptionSession:
    """Compatibility-shaped presenter for protocol-dispatched operations."""

    def __init__(self, workflow, mechanics):
        self._workflow = workflow
        self._mechanics = mechanics

    @property
    def root(self):
        return self._mechanics.root

    def observe(self, path, note, durability=REPO):
        try:
            result = self._workflow.perform(Observe((path, note, durability)))
        except (JournalError, OSError) as error:
            error.stops_adoption = True
            raise
        if isinstance(result, Refused) and isinstance(result.value, str):
            error = JournalError(None, result.value, artifact_name(durability))
            error.stops_adoption = True
            raise error
        return result.value

    def execute(self, intention):
        try:
            result = self._workflow.perform(Mutate(intention))
        except (JournalError, OSError) as error:
            error.stops_adoption = True
            raise
        if isinstance(result, Refused) and isinstance(result.value, str):
            error = JournalError(
                None, result.value, artifact_name(intention.durability)
            )
            error.stops_adoption = True
            raise error
        return result.value

    def recover(self):
        try:
            return self._workflow.perform(RecoverAll())
        except (JournalError, OSError) as error:
            error.stops_adoption = True
            raise

    def path_is_gated(self, path, durability=REPO):
        return self._mechanics.path_is_gated(path, durability)


def _raise_adoption_gate(snapshot, *, before_effect=False):
    """Refuse when authoritative history gates.

    `before_effect` says that no adopting effect of this run precedes the
    gate. It is what lets `harness_repair_regime` name the refusal a
    `pre_effect_gate`; a refusal that does not say so is never one, because
    the run may already have changed the project.
    """
    gate = _mutation_gate(snapshot)
    if gate is None:
        return
    outstanding = _outstanding_history_conditions(snapshot)
    condition = outstanding[0] if outstanding else None
    artifact = condition.subject if condition is not None else JOURNAL_FILENAME
    message = condition.public_message if condition is not None else gate
    error = JournalError(
        None,
        f"{message}. No target or permanent-history change was left by this operation",
        artifact,
    )
    if condition is None or condition.identity != "history.unreadable":
        error.stops_adoption = True
        error.pre_effect_gate = before_effect
    raise error


def _confirm_current_effect(root, transaction, location):
    """Fail closed while the current WAL still retains exact retry evidence."""
    snapshot = _workflow_snapshot(root)
    reason = _mutation_gate(snapshot)
    if reason is None:
        return
    error = JournalError(
        None,
        f"{reason}; coherent successor confirmation for {location} failed",
        JOURNAL_FILENAME,
    )
    error.visibility_unconfirmed = True
    raise error


class _HistoryWorkflow:
    def __init__(self, root: Path, session=None):
        self._root = root
        self._session = session

    def perform(self, operation: Operation) -> Result:
        if type(operation) is Inspect:
            return self._inspect(operation.checked)
        if type(operation) is Adopt:
            if self._session is None:
                raise RuntimeError("Adopt requires the protocol-owned adopting scope")
            observed = self._inspect(True)
            result_type = ConfirmedWithGates if observed.inspection.conditions else Completed
            return result_type(observed.inspection, value=self._session)
        if type(operation) is Observe:
            if self._session is None:
                raise RuntimeError("Observe requires the protocol-owned adopting scope")
            before = self._inspect(True).inspection
            gate = _mutation_gate(before)
            if gate is not None:
                return Refused(before, value=gate)
            path, note, durability = operation.observation
            outcome = self._session.observe(path, note, durability)
            after = self._inspect(True).inspection
            result_type = Noop if outcome.status == OUTCOME_NOOP else (
                ConfirmedWithGates if after.conditions else Completed
            )
            return result_type(after, value=outcome)
        if type(operation) is Mutate:
            if self._session is None:
                raise RuntimeError("Mutate requires the protocol-owned adopting scope")
            before = self._inspect(True).inspection
            gate = _mutation_gate(before)
            if gate is not None:
                return Refused(before, value=gate)
            outcome = self._session.execute(operation.intention)
            after = self._inspect(True).inspection
            if outcome.status == OUTCOME_APPLIED:
                result_type = ConfirmedWithGates if after.conditions else Completed
            elif outcome.status == OUTCOME_NOOP:
                result_type = Noop
            else:
                result_type = Refused
            return result_type(after, value=outcome)
        if type(operation) is RecoverAll:
            if self._session is None:
                raise RuntimeError("RecoverAll requires the protocol-owned adopting scope")
            recoveries, repository, local = _recover_all(self._session)
            self._session.survey(list(repository), list(local))
            after = self._inspect(True).inspection
            effects = any(item.action == RECOVERED for item in recoveries)
            gates = any(item.problem is not None for item in recoveries)
            result_type = (
                ConfirmedWithGates if effects and gates
                else Completed if effects
                else Refused if gates
                else Noop
            )
            return result_type(after, value=tuple(recoveries))
        if type(operation) is ResolveOne:
            resolution = resolve_one(
                self._root, operation.transaction, operation.disposition
            )
            after = self._inspect(True).inspection
            result_type = (
                Refused if resolution.message is not None
                else ConfirmedWithGates if resolution.gates
                else Completed
            )
            return result_type(after, value=resolution)
        if type(operation) is RepairOne:
            return repair_one(self._root, operation.transaction)
        raise NotImplementedError(
            f"{type(operation).__name__} is not available in this workflow"
        )

    def _inspect(self, checked: bool, *, include_stored_claim: bool = False) -> Result:
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
                    include_stored_claim=include_stored_claim,
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
                include_stored_claim=include_stored_claim,
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

        legacy = _legacy_conditions(unfinished, disagreements, anomalies)
        topology_conditions = tuple(
            condition
            for condition in _topology_conditions(topology)
            if not any(
                set(condition.pairing).intersection(item.pairing)
                for item in legacy
            )
        )
        conditions = tuple(sorted((
            *topology_conditions,
            *legacy,
            *(_wal_condition(item) for item in observations),
            *(
                ProtocolCondition(
                    (4, location, message),
                    "wal.damaged",
                    location,
                    (),
                    message,
                    message,
                )
                for location, message in residue
            ),
        )))
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


def resolve_transaction(root, transaction_id, disposition):
    """Compatibility facade that submits one protocol-owned resolution."""
    with history_workflow(root) as history:
        return history.perform(
            ResolveOne(transaction_id, disposition)
        ).value


def repair_transaction(root, transaction_id):
    """Compatibility facade that submits one protocol-owned repair."""
    with history_workflow(root) as history:
        return history.perform(RepairOne(transaction_id))


# The regimes `harness_repair_regime` names and the outcomes
# `guarded_harness_repair` returns. The facade exports only what `init` reads:
# `PRE_EFFECT_GATE`, `UNAVAILABLE`, `REPAIR_WITHHELD` and `REPAIR_CURRENT`.
PRE_EFFECT_GATE = "pre_effect_gate"
LOCK_BUSY = "lock_busy"
UNAVAILABLE = "unavailable"
REPAIR_RELINKED = "relinked"
REPAIR_WITHHELD = "withheld"
REPAIR_CURRENT = "current"

# What a withheld repair says, in words a reader of the WARNING can act on:
# a rule's own identifiers and line numbers stay in the code.
_LOCK_BUSY_REASON = (
    "another validated-memory process holds the run-wide lock and may be "
    "recording a change to the harness link"
)
_DAMAGED_HISTORY_REASON = (
    "the history is damaged, unsupported or changing, so it cannot be shown "
    "to leave the link alone"
)
_DAMAGED_TOPOLOGY_REASON = (
    "the history topology is damaged, so it cannot be shown to leave the "
    "link alone"
)
_CONDITION_ON_LINK_REASON = "a history condition names the harness path"


def harness_repair_regime(failure):
    """The regime a failed run may repair the harness link under, or None.

    `failure` is the exception that ended the journalled part of the run.
    None means no repair may be attempted: the failure may follow an
    adopting effect of the run, or leaves an effect unconfirmed.

    A failure that carries none of the markers read here is taken for an
    unreadable journal, which reaches the repair only through the vault
    conditions of `guarded_harness_repair`. A new refusal must still set
    `stops_adoption`: without it the repair is allowed whenever the vault
    holds nothing on the harness path, and the history is not read at all.
    """
    if getattr(failure, "visibility_unconfirmed", False):
        return None
    if getattr(failure, "lock_busy", False):
        return LOCK_BUSY
    if getattr(failure, "pre_effect_gate", False):
        return PRE_EFFECT_GATE
    if getattr(failure, "stops_adoption", False):
        return None
    return UNAVAILABLE


def guarded_harness_repair(
    root, harness_path, regime, relink, recheck, deadline=None
):
    """Relink the harness path under the run-wide lock, or withhold and say why.

    Returns `(outcome, reason)`, the outcome being `REPAIR_RELINKED`,
    `REPAIR_WITHHELD` or `REPAIR_CURRENT`. `reason` is None unless the repair
    was withheld, and then it is a plain sentence naming what stopped it.

    `relink` is a zero-argument callable that publishes the link atomically.
    It is called at most once. With the lock held it is called in the same
    block that checks the vault and the history, so no transaction of this
    plugin can appear between the decision and the link. It is called
    without the lock only when the lock cannot be taken, and without the
    vault check only when the vault cannot be read. Whatever `relink` raises
    reaches the caller unchanged. `harness_path` is the path `relink` publishes.

    `recheck` is a zero-argument callable that reads the harness path again.
    Every route that calls `relink` calls it first, immediately before, and
    `relink` is not called unless it returns None. Anything else it returns is
    the `(outcome, reason)` pair the repair ends with: `REPAIR_CURRENT` when
    the path already is what `relink` would publish, `REPAIR_WITHHELD` when it
    changed under the wait. A process outside the plugin that replaces the
    harness path between that reading and the rename inside `relink` is not
    guarded against: the window is one `lstat` and one `rename`, and the
    standard library has no compare-and-swap on a pathname to close it.

    `regime` is what the caller knows of the refusal:

    - `LOCK_BUSY`: withheld at once, without waiting for the lock again. The
      holder may be recording a change to the harness path.
    - `PRE_EFFECT_GATE`: the history was readable and refused before any
      adopting effect. The link is restored only if the vault holds no
      residue, every entry of its transaction and preimage directories is a
      regular file, and no transaction is unreadable, names no path or names
      the harness path; every outstanding `history.*` condition is
      `history.topology_gate`; the history snapshot is usable; and no
      condition names the harness path as its subject or in its pairing.
      When the lock or the vault cannot be read those rules cannot be shown
      to hold, and the repair is withheld.
    - `UNAVAILABLE`: the history could not be read, or the run gated on an
      unignored vault, so only the vault rules of `PRE_EFFECT_GATE` apply.
      When the lock cannot be taken for a reason other than another process
      holding it, the vault is still read, without the lock, and the same
      rules apply. When the vault cannot be listed the link is restored
      unguarded: that is the exception of
      docs/design/2026-09-01-the-journal-core.md §4, which promises the link
      when the journal cannot be read at all, and it is an availability
      promise, not a proof.

    A regime that is none of these is a caller error. `deadline` is the
    `time.monotonic()` instant at which waiting for the lock ends, the one
    `adopting_run` was given when the same run took the lock before; without
    one the wait is `LOCK_WAIT_SECONDS` from the call. A lock another process
    takes between the refusal and this call is waited for until that instant,
    and then withholds the repair.
    """
    if regime == LOCK_BUSY:
        return REPAIR_WITHHELD, _LOCK_BUSY_REASON
    if regime not in (PRE_EFFECT_GATE, UNAVAILABLE):
        raise ValueError(f"unknown harness repair regime: {regime!r}")
    root = Path(root)
    lock = Lock(root, deadline)
    rendezvous_at("before-harness-repair-lock", 1)
    held = False
    try:
        lock.__enter__()
        held = True
    except JournalError as error:
        if getattr(error, "lock_busy", False):
            return REPAIR_WITHHELD, _LOCK_BUSY_REASON
        not_held = error.message
    except OSError as error:
        not_held = str(error)
    if not held and regime == PRE_EFFECT_GATE:
        return _unreadable_repair(regime, relink, recheck, not_held)
    try:
        try:
            reason = _harness_repair_obstacle(root, harness_path, regime)
        except (OSError, JournalError) as error:
            message = error.message if isinstance(error, JournalError) else str(error)
            return _unreadable_repair(regime, relink, recheck, message)
        if reason is not None:
            return REPAIR_WITHHELD, reason
        settled = recheck()
        if settled is not None:
            return settled
        relink()
        return REPAIR_RELINKED, None
    finally:
        if held:
            lock.__exit__(None, None, None)


def _unreadable_repair(regime, relink, recheck, why):
    """Answer for a lock or a vault that cannot be read at all.

    `relink` runs for `UNAVAILABLE` only, after `recheck` and with no vault
    check; the lock is held when the vault was the failure and not when the
    lock was.
    """
    if regime == UNAVAILABLE:
        settled = recheck()
        if settled is not None:
            return settled
        relink()
        return REPAIR_RELINKED, None
    return REPAIR_WITHHELD, f"the lock or the vault could not be read: {why}"


def _harness_repair_obstacle(root, harness_path, regime):
    """The reason the link must not be restored, or None; the lock is held.

    Raises `OSError` or `JournalError` when the vault cannot be read at all.
    `retained_residue` reports a directory it cannot list as residue, so the
    two vault directories are listed first, by `_vault_node_that_is_not_a_file`:
    an unreadable one is the unreadable-vault case, not an obstruction, and a
    node that is not a regular file is retained residue, found before anything
    is opened.
    """
    same = _same_entry(root, harness_path)
    node = _vault_node_that_is_not_a_file(root)
    if node is not None:
        return f"the vault holds {node}, which is not a regular file"
    residue = retained_residue(root)
    if residue:
        location, why = residue[0]
        return f"the vault holds {location}: {why}"
    for entry in open_transactions(root):
        transaction = entry["id"]
        if "damaged" in entry:
            return f"transaction {transaction} cannot be read: {entry['damaged']}"
        intention = entry.get("intention")
        path = intention.get("path") if isinstance(intention, Mapping) else None
        if not isinstance(path, str) or not path:
            return f"transaction {transaction} names no path"
        if same(path):
            return f"transaction {transaction} names the harness path"
    if regime != PRE_EFFECT_GATE:
        return None
    snapshot = _workflow_snapshot(root)
    for condition in _outstanding_history_conditions(snapshot):
        if condition.identity != "history.topology_gate":
            return _DAMAGED_HISTORY_REASON
    if _unusable_snapshot(snapshot) is not None:
        return _DAMAGED_TOPOLOGY_REASON
    for condition in snapshot.conditions:
        if any(same(name) for name in (condition.subject, *condition.pairing)):
            return _CONDITION_ON_LINK_REASON
    return None


def _vault_node_that_is_not_a_file(root):
    """The first entry of the vault's transaction or preimage directory that is
    not a regular file, as `<vault>/<directory>/<name>`, or None.

    Entries are classified by type without following links and nothing is
    opened, because opening a symlink can block on a pipe or read a file
    outside the vault. A directory that does not exist has no such entry;
    `OSError` reaches the caller for one that cannot be listed.
    """
    for name in (TRANSACTIONS_DIRNAME, PREIMAGE_DIRNAME):
        try:
            with os.scandir(root / VAULT_DIRNAME / name) as entries:
                for entry in entries:
                    if not entry.is_file(follow_symlinks=False):
                        return f"{VAULT_DIRNAME}/{name}/{entry.name}"
        except FileNotFoundError:
            pass
    return None


def _same_entry(root, harness_path):
    """A predicate: does a recorded path name the same directory entry?

    Nothing is collapsed lexically: `..` after a symlink names the parent of
    the symlink's target, and only the operating system knows which that is.
    Both paths are joined to the current directory when relative, a trailing
    `/` is dropped, and the parent is split from the final name. The final
    name is never followed, because the link itself is what may be stale.
    Two paths name one entry when the real paths of their parents are equal
    and their final names are equal under `str.casefold`, or when both
    entries exist and are the same file (`os.path.samestat` on `lstat`). The
    case folding serves a filesystem that folds case, where `Memory` and
    `memory` are one entry that may not exist yet, and withholds more than
    needed on one that keeps case; the file identity serves an entry that
    exists under another name, as it does on that filesystem and through a
    hard link. Equivalence only ever widens, which withholds more. A string that is not a path -- `transaction:<id>`, `adoption:<id>`
    -- is joined the same way, so it equals the harness path only if that
    path is literally `<root>/<string>`.
    """
    base = os.path.join(os.getcwd(), os.fspath(root))

    def absolute(path):
        return os.path.join(base, os.fspath(path)).rstrip("/") or "/"

    def key(path):
        parent, name = os.path.split(absolute(path))
        return os.path.realpath(parent), name.casefold()

    def entry(path):
        try:
            return os.lstat(absolute(path))
        except OSError:
            return None

    wanted = key(harness_path)
    wanted_entry = entry(harness_path)

    def same(recorded):
        if not isinstance(recorded, str) or not recorded:
            return False
        if key(recorded) == wanted:
            return True
        found = entry(recorded)
        return (
            found is not None
            and wanted_entry is not None
            and os.path.samestat(found, wanted_entry)
        )

    return same


def history_condition_count(root=Path()):
    """How many outstanding `history.*` conditions stop an adopting run.

    Read-only: it acquires no lock and creates no file, and it needs no
    vault. It reads the snapshot the adoption gate reads. A history that
    cannot be read as records counts as one, the single error `journal
    --check` reports for it. Nothing is read from the vault while one of its
    transaction or preimage entries is not a regular file, because opening a
    symlink can block on a pipe: `JournalError` reaches the caller then, and
    `OSError` when the inspection cannot be made.
    """
    root = Path(root)
    node = _vault_node_that_is_not_a_file(root)
    if node is not None:
        raise JournalError(
            None, f"{node} is not a regular file; nothing in the vault was read", node
        )
    snapshot = _workflow_snapshot(root)
    if isinstance(snapshot.compatibility, Incompatible):
        return 1
    return len(_outstanding_history_conditions(snapshot))


def _parse_preceding(failure: RawHistoryFailure):
    parsed = []
    for durability, raw in zip((REPO, LOCAL), failure.preceding):
        parsed.extend(
            _freeze_mapping(entry)
            for entry in parse_acquired_history(raw, durability)
        )
    return tuple(parsed)


def _observe_wal(root, pair, histories, adoption, *, include_stored_claim=False):
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
                include_stored_claim=include_stored_claim,
            ),
        )
        for item in raw_wal
    )
    claimed = tuple(
        residue
        for item in raw_wal
        for residue in claimed_temporary_residue(root, item)
    )
    return observations, tuple((*claimed, *retained_residue(root)))


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
    identity = (
        "history.unreadable"
        if error.history_kind is HistoryErrorKind.UNAVAILABLE
        else "history.malformed"
    )
    if "newer than this plugin" in message:
        identity = "history.unsupported"
    artifact = error.artifact or "journal.jsonl"
    subject = artifact if error.lineno is None else f"{artifact}:{error.lineno}"
    return ProtocolCondition(
        (0, identity, error.artifact or "", error.lineno or 0, message),
        identity,
        subject,
        (),
        message,
        message,
    )


def _history_error_result(state: WorkflowInspection, error: JournalError) -> Result:
    if "newer than this plugin" in error.message:
        return Unsupported(state)
    if error.history_kind is HistoryErrorKind.DAMAGED:
        return Damaged(state)
    return Refused(state)


def _topology_conditions(topology):
    if isinstance(topology, TopologyUnavailable):
        return (
            ProtocolCondition(
                (1, "history.topology_damage", topology.reason),
                "history.topology_damage",
                "journal histories",
                (),
                topology.reason,
                "damaged history topology: topology inspection is unavailable: "
                f"{topology.reason}. Preserve both histories and restore the exact "
                "affected artifact from a trusted source; rerun journal --check",
            ),
        )
    if topology is None:
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
            tuple(
                dict.fromkeys(
                    (
                        *((f"transaction:{condition.transaction}",) if condition.transaction else ()),
                        *condition.subjects,
                    )
                )
            ),
            condition.code,
            (
                "damaged history topology: "
                f"{_topology_condition_message(condition)}. Preserve both histories "
                "and restore the exact affected artifact from a trusted source; "
                "rerun journal --check"
                if damaged
                else f"{_topology_condition_message(condition)}. No automatic "
                "reconciliation is available. Preserve both histories; restore an "
                "accepted exact state from a trusted source if possible, then rerun "
                "journal --check"
            ),
        )
        for condition in topology.conditions
        if condition.level == "error"
    )


def _topology_condition_message(condition):
    """Present topology evidence without exposing its internal code."""
    subject = (
        f"transaction {condition.transaction}"
        if condition.transaction
        else f"history node {condition.node}"
        if condition.node
        else "history topology"
    )
    if condition.fields:
        message = f"{subject} has conflicting {', '.join(condition.fields)} field(s)"
    else:
        message = f"{subject} has an unresolved topology condition"
    if condition.reason:
        message += f": {condition.reason}"
    return message


def _legacy_conditions(unfinished, disagreements, anomalies):
    conditions = []
    for entry, state in unfinished:
        transaction = entry.get("transaction")
        conditions.append(
            ProtocolCondition(
                (2, entry["path"], entry["run"], state),
                "history.topology_gate",
                entry["path"],
                tuple(
                    item
                    for item in (
                        f"transaction:{transaction}" if transaction else None,
                        f"run:{entry['run']}",
                        f"durability:{entry['durability']}",
                    )
                    if item is not None
                ),
                state,
                f"unfinished transaction from run {entry['run']}: "
                f"the path is {state}",
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
                f"records of transaction {transaction} disagree on {field}",
            )
        )
    for message, entry in anomalies:
        words = message.split()
        pairing = (
            (f"transaction:{words[1]}",)
            if len(words) > 1 and words[0] == "transaction"
            else ()
        )
        conditions.append(
            ProtocolCondition(
                (2, entry["path"], message),
                "history.topology_gate",
                entry["path"],
                pairing,
                message,
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
    message = (
        f"damaged transaction {evidence.transaction}: {evidence.problem_reason}"
        if isinstance(evidence, (DamagedWal, UnsupportedWal))
        else f"open transaction {evidence.transaction} ({evidence.stage}) on "
        f"{evidence.path}: {report_word(evidence.verdict)}"
    )
    return ProtocolCondition(
        (3, evidence.transaction, identity),
        identity,
        (
            transaction_artifact(evidence.transaction)
            if isinstance(evidence, (DamagedWal, UnsupportedWal))
            else evidence.path
        ),
        (f"transaction:{evidence.transaction}",),
        evidence.problem_reason or evidence.verdict,
        message,
    )
