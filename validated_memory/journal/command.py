"""The `journal` subcommand: report the record, reconcile, or resolve one.

The only module of the package that renders findings, and the only one an
argument parser reaches.
"""

import os
from pathlib import Path

from .executor import Run
from .reconcile import reconcile
from .records import DURABILITIES, JOURNAL_FILENAME, JournalError, read
from .transactions import (
    PROBLEM_DAMAGED,
    RECOVERABLE,
    Resolution,
    _classify,
    _COMPLETE,
    _DISCARD,
    _no_such_transaction,
    _open_transactions,
    _REMOVE,
    _transaction_artifact,
    _transaction_path,
)


def run(check, resolve, resolution, stdout, stderr):
    """The `journal` subcommand: report the record, reconcile, or resolve one.

    Read-only in both REPORTING modes, and `--check` is read-only in
    particular: it classifies every unresolved transaction by what recovery
    WOULD do with it and does none of it. Without `--check` it summarises
    and exits 0 whatever it finds, so a reader can look at a project without
    gating on it; with `--check` an unfinished transaction -- from the two
    journals' own pairing (`reconcile`), from a pair whose halves disagree,
    from an id that is not a pair at all, or from a transaction file still
    on disk (`_open_transactions`) -- is an ERROR, because a caller that asked to be told cannot be told by an exit
    code of 0.

    `--resolve` is the third mode and the only one that writes: an
    operator's answer to a transaction recovery would not touch, which is
    the only kind it may be applied to. It is not reporting and does not
    report -- see `Run.resolve_transaction`.

    A transaction file is reported even without `--check`, but only as a
    count: a reader who did not ask to gate on one should still be told
    something is open, on a second line, only when there is something to
    say.
    """
    from ..findings import ERROR, EXIT_ERROR, EXIT_OK, Finding

    root = Path()
    if resolve is not None:
        return _run_resolve(root, resolve, resolution, stdout, stderr)
    # Accumulated one artifact at a time so the summary below can say how
    # many records were actually read when a later one is refused. Printing
    # a hardcoded 0 there described a project with no history at all, which
    # is a different and much worse fault than the one that happened.
    records = []
    try:
        for durability in DURABILITIES:
            records.extend(read(root, durability))
        # `reconcile` reads both journals again, so it belongs inside this
        # handler: a journal a concurrent writer left unreadable between the
        # two reads must be reported the same way as anything else the
        # reader cannot accept.
        unfinished, disagreements, anomalies = (
            reconcile(root) if check else ([], [], [])
        )
        # `_open_transactions` never raises -- an unreadable transaction file
        # is one of its own results, not a `JournalError` -- so it does not
        # need this `try`, but reading the log alongside the two journals in
        # one pass is what lets the summary below count everything actually
        # read even when one of them is later refused.
        transactions = _open_transactions(root)
        # The id the journals themselves carry, taken in the order
        # `_adoption_id` prefers them (the repository journal first, since
        # `records` is filled in `DURABILITIES` order) and never minted: a
        # tree whose journals are empty has no adoption to compare a
        # transaction file against, and a fresh id invented here would call
        # every one of them foreign.
        adoption = records[0]["adoption"] if records else None
    except JournalError as error:
        where = error.artifact or JOURNAL_FILENAME
        location = where if error.lineno is None else f"{where}:{error.lineno}"
        print(Finding(ERROR, location, "journal", error.message).render(), file=stderr)
        print(f"journal: {len(records)} record(s), 1 error(s)", file=stdout)
        return EXIT_ERROR

    if not check:
        print(f"journal: {len(records)} record(s)", file=stdout)
        if transactions:
            print(
                f"journal: {len(transactions)} unresolved transaction(s)",
                file=stdout,
            )
        return EXIT_OK

    for entry, state in unfinished:
        print(
            Finding(
                ERROR,
                entry["path"],
                "journal",
                f"unfinished transaction from run {entry['run']}: "
                f"the path is {state}",
            ).render(),
            file=stderr,
        )
    for transaction, field, entry in disagreements:
        print(
            Finding(
                ERROR,
                entry["path"],
                "journal",
                f"records of transaction {transaction} disagree on {field}",
            ).render(),
            file=stderr,
        )
    for message, entry in anomalies:
        print(
            Finding(ERROR, entry["path"], "journal", message).render(),
            file=stderr,
        )
    for item in transactions:
        # Classified by the one function recovery itself acts on, so what
        # `--check` promises and what the next run does cannot drift apart.
        verdict, facts = _classify(root, item, adoption)
        if verdict == PROBLEM_DAMAGED:
            location = _transaction_artifact(item["id"])
            message = f"damaged transaction {item['id']}: {facts['reason']}"
        else:
            location = facts["path"]
            word = (
                RECOVERABLE
                if verdict in (_COMPLETE, _DISCARD, _REMOVE)
                else verdict
            )
            message = (
                f"open transaction {item['id']} ({facts['stage']}) on "
                f"{location}: {word}"
            )
        print(Finding(ERROR, location, "journal", message).render(), file=stderr)

    total_errors = (
        len(unfinished) + len(disagreements) + len(anomalies) + len(transactions)
    )
    print(
        f"journal: {len(records)} record(s), {total_errors} error(s)",
        file=stdout,
    )
    return EXIT_ERROR if total_errors else EXIT_OK


def _run_resolve(root, transaction_id, resolution, stdout, stderr):
    """`journal --resolve`: close one transaction the way the operator says.

    The one mode of this subcommand that writes, and the only place outside
    `init` that opens a `Run`. It does NOT recover: `Run.recover` is
    explicit precisely so that an operator answering for one transaction
    does not have every other one closed underneath them in the same
    breath.

    A refusal is an ERROR and exit 1, not a traceback and not a usage
    error: the id was well formed and the flags were legal, and what could
    not be done is a fact about this project's state. An unknown id is one
    of those, and it is answered before anything is opened, so the refusal's
    own promise that nothing has been changed is true of the tree as well as
    of the log. The success line names the flag as it was typed, because a
    resolution is a decision someone made and the record of the session
    should show which one.
    """
    from ..findings import ERROR, EXIT_ERROR, EXIT_OK, Finding

    try:
        # Asked BEFORE a `Run` is built. `Run.__init__` bootstraps the
        # journal -- it mints an adoption id and installs `journal.jsonl`
        # with its opening record -- so an id nothing carries used to leave
        # a virgin tree adopted, under a refusal whose own last sentence
        # said nothing had been changed. `lexists`, so a transaction file
        # that is there but unreadable still reaches the resolver, which
        # has a `damaged` answer for it.
        if not os.path.lexists(_transaction_path(root, transaction_id)):
            outcome = Resolution(
                transaction_id,
                resolution,
                _transaction_artifact(transaction_id),
                _no_such_transaction(transaction_id),
            )
        else:
            outcome = Run(root).resolve_transaction(transaction_id, resolution)
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
    return EXIT_OK
