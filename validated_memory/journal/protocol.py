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
    Run,
    adopting_session,
    bind_resolution,
)
from .fault import rendezvous_at
from .lock import Lock
from .operations import link_to, replace_file
from .paths import ABSENT, DIRECTORY, FILE, SYMLINK, current_state, describe, satisfies
from .reconcile import reconcile
from .records import (
    COMMITTED,
    HISTORY_WRITE_SCHEMA,
    JOURNAL_FILENAME,
    LOCAL,
    OBSERVE,
    PREPARED,
    REPO,
    STAGES,
    VAULT_DIRNAME,
    JournalError,
    RawHistoryFailure,
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
    has_transaction,
    mark_published,
    mark_unconfirmed,
    no_such_transaction,
    open_transactions,
    read_transaction,
    reestablish_transaction,
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


def _mutation_gate(snapshot):
    """Name a global history gate not discharged by its exact WAL provision."""
    unusable = _unusable_snapshot(snapshot)
    if unusable is not None:
        return unusable
    provisions = _valid_provisions(snapshot)
    outstanding = tuple(
        condition
        for condition in snapshot.conditions
        if condition.identity.startswith("history.")
        and not any(
            _is_exact_provision(condition, evidence) for evidence in provisions
        )
    )
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
        session._record(
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
            session._republish_target(facts)
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
            session._republish_restore(facts)
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
            data = blob.read_bytes()
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
        kept = session._restore_effect(
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
        session = Run(root, new_id(), adoption)
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
        observation_record = session._record(
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


bind_resolution(resolve_one)


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
        _recover_all,
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
            tuple(
                dict.fromkeys(
                    (
                        *((f"transaction:{condition.transaction}",) if condition.transaction else ()),
                        *condition.subjects,
                    )
                )
            ),
            condition.code,
        )
        for condition in topology.conditions
        if condition.level == "error"
    )


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
