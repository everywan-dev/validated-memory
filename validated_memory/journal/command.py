"""The `journal` subcommand: report, reconcile, resolve, or repair one.

The only module of the package that renders findings, and the only one an
argument parser reaches.
"""

from pathlib import Path

from ..findings import ERROR, EXIT_ERROR, EXIT_OK, Finding
from .executor import resolve_transaction
from .protocol import (
    Completed,
    ConfirmedWithGates,
    Incompatible,
    Inspect,
    RepairOne,
    history_workflow,
)
from .records import JOURNAL_FILENAME, JournalError
from .transactions import claimed_temporary_residue, historyless_transactions_message


def run(check, resolve, resolution, repair, stdout, stderr):
    """The `journal` subcommand: report, reconcile, resolve, or repair one.

    Read-only in both REPORTING modes, and `--check` is read-only in
    particular: it classifies every unresolved transaction by what recovery
    WOULD do with it and does none of it. Without `--check` it summarises
    and exits 0 whatever it finds, so a reader can look at a project without
    gating on it; with `--check` an unfinished transaction -- from the two
    journals' own pairing (`reconcile`), from a pair whose halves disagree,
    from an id that is not a pair at all, or from a transaction file still
    on disk (`open_transactions`) -- is an ERROR, because a caller that
    asked to be told cannot be told by an exit code of 0.

    `--resolve` and `--repair` are the two targeted modes that write:
    `--resolve` records an operator's answer to a transaction recovery,
    while `--repair` performs a proof-carrying history-tail repair. Neither
    is reporting -- see the executor's targeted operations.

    A transaction file is reported even without `--check`, but only as a
    count: a reader who did not ask to gate on one should still be told
    something is open, on a second line, only when there is something to
    say.
    """
    root = Path()
    if repair is not None:
        return _run_repair(root, repair, stdout, stderr)
    if resolve is not None:
        return _run_resolve(root, resolve, resolution, stdout, stderr)
    # Accumulated one artifact at a time so the summary below says how many
    # records were actually read when a later one is refused.
    with history_workflow(root) as history:
        result = history.perform(Inspect(check))
    inspection = result.inspection
    records = list(inspection.compatibility.records)
    if isinstance(inspection.compatibility, Incompatible):
        error = inspection.compatibility.error
        where = error.artifact or JOURNAL_FILENAME
        location = where if error.lineno is None else f"{where}:{error.lineno}"
        print(Finding(ERROR, location, "journal", error.message).render(), file=stderr)
        print(f"journal: {len(records)} record(s), 1 error(s)", file=stdout)
        return EXIT_ERROR
    unfinished = inspection.unfinished
    disagreements = inspection.disagreements
    anomalies = inspection.anomalies
    transactions = [observation.raw for observation in inspection.wal]
    classifications = [observation.evidence for observation in inspection.wal]
    residue = inspection.residue

    if not check:
        print(f"journal: {len(records)} record(s)", file=stdout)
        if transactions:
            print(
                f"journal: {len(transactions)} unresolved transaction(s)",
                file=stdout,
            )
        return EXIT_OK

    if not records and transactions:
        print(
            Finding(
                ERROR,
                ".validated-memory/transactions",
                "journal",
                historyless_transactions_message(transactions),
            ).render(),
            file=stderr,
        )
        print(
            f"journal: 0 record(s), 1 error(s)",
            file=stdout,
        )
        return EXIT_ERROR

    for entry, state in unfinished:
        condition = _checked_condition(
            inspection, (2, entry["path"], entry["run"], state)
        )
        _print_checked_condition(condition, stderr)
    for transaction, field, entry in disagreements:
        condition = _checked_condition(
            inspection, (2, entry["path"], transaction, field)
        )
        _print_checked_condition(condition, stderr)
    for message, entry in anomalies:
        condition = _checked_condition(
            inspection, (2, entry["path"], message)
        )
        _print_checked_condition(condition, stderr)
    claimed_residue_count = 0
    for item, evidence in zip(transactions, classifications):
        # Classified by the one function recovery itself acts on, so what
        # `--check` promises and what the next run does cannot drift apart.
        condition = next(
            condition
            for condition in inspection.conditions
            if condition.order[:2] == (3, evidence.transaction)
        )
        _print_checked_condition(condition, stderr)
        for residue_location, residue_message in claimed_temporary_residue(root, item):
            print(Finding(ERROR, residue_location, "journal", residue_message).render(), file=stderr)
            claimed_residue_count += 1
    for location, message in residue:
        condition = _checked_condition(inspection, (4, location, message))
        _print_checked_condition(condition, stderr)

    total_errors = (
        len(unfinished) + len(disagreements) + len(anomalies) + len(transactions) + len(residue) + claimed_residue_count
    )
    print(
        f"journal: {len(records)} record(s), {total_errors} error(s)",
        file=stdout,
    )
    return EXIT_ERROR if total_errors else EXIT_OK


def _run_repair(root, transaction_id, stdout, stderr):
    """`journal --repair`: perform one proof-carrying tail repair."""
    try:
        with history_workflow(root) as history:
            result = history.perform(RepairOne(transaction_id))
    except (JournalError, OSError) as error:
        where = getattr(error, "artifact", None) or JOURNAL_FILENAME
        print(Finding(ERROR, where, "journal", str(error)).render(), file=stderr)
        return EXIT_ERROR
    outcome = result.repair
    if outcome is None:
        raise TypeError("RepairOne returned no repair presentation")
    if not isinstance(result, (Completed, ConfirmedWithGates)):
        print(
            Finding(ERROR, outcome.location, "journal", outcome.message).render(),
            file=stderr,
        )
        return EXIT_ERROR
    print(
        f"journal: repaired {outcome.location} for transaction {transaction_id}",
        file=stdout,
    )
    for condition in outcome.gates:
        _print_checked_condition(
            condition,
            stderr,
            suffix=(
                " Address this condition, then run journal --check; do not "
                "repeat the confirmed repair."
            ),
        )
    if isinstance(result, ConfirmedWithGates):
        print(
            f"journal: repair confirmed, {len(outcome.gates)} gate(s) remain",
            file=stdout,
        )
        return EXIT_ERROR
    return EXIT_OK


def _checked_condition(inspection, order):
    """Return the canonical checked condition at one established order key."""
    return next(condition for condition in inspection.conditions if condition.order == order)


def _print_checked_condition(condition, stderr, suffix=""):
    """Render one condition identically for check and confirmed repair."""
    separator = "" if not suffix or condition.public_message.endswith((".", "!", "?")) else "."
    print(
        Finding(
            ERROR,
            condition.subject,
            "journal",
            condition.public_message + separator + suffix,
        ).render(),
        file=stderr,
    )


def _run_resolve(root, transaction_id, resolution, stdout, stderr):
    """`journal --resolve`: close one transaction the way the operator says.

    The one mode of this subcommand that writes. It does NOT recover: an
    operator answering for one transaction does not have every other one
    closed underneath them in the same breath.

    A refusal is an ERROR and exit 1, not a traceback and not a usage
    error: the id was well formed and the flags were legal, and what could
    not be done is a fact about this project's state. An unknown id is one
    of those, and it is answered before anything is opened, so the refusal's
    own promise that nothing has been changed is true of the tree as well as
    of the log. The success line names the flag as it was typed, because a
    resolution is a decision someone made and the record of the session
    should show which one.
    """
    try:
        outcome = resolve_transaction(root, transaction_id, resolution)
    except JournalError as error:
        where = error.artifact or JOURNAL_FILENAME
        location = where if error.lineno is None else f"{where}:{error.lineno}"
        print(Finding(ERROR, location, "journal", error.message).render(), file=stderr)
        return EXIT_ERROR
    except OSError as error:
        print(
            Finding(
                ERROR,
                error.filename or JOURNAL_FILENAME,
                "journal",
                f"the transaction could not be resolved: {error}",
            ).render(),
            file=stderr,
        )
        return EXIT_ERROR
    if outcome.message is not None:
        print(
            Finding(ERROR, outcome.location, "journal", outcome.message).render(),
            file=stderr,
        )
        return EXIT_ERROR
    line = f"journal: resolved {transaction_id} (--{resolution})"
    if outcome.kept is not None:
        # A restore discards whatever the path held, and the operator is
        # told where those bytes went in the same breath: a copy nobody can
        # find is not a copy.
        line += f"; the discarded bytes are kept at {outcome.kept}"
    print(line, file=stdout)
    for location, message in outcome.gates:
        print(
            Finding(
                ERROR,
                location,
                "journal",
                f"{message}. This condition was left untouched; the confirmed "
                "effect is reported separately.",
            ).render(),
            file=stderr,
        )
    return EXIT_ERROR if outcome.gates else EXIT_OK
