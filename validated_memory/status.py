"""The `status` subcommand: one read-only report of project consistency.

`status` answers "is this project structurally consistent, and what does its
freshness look like?" -- structural findings gate, freshness is reported
(see docs/adr/0002). It computes one internal pass over the curated layer,
the agent-memory layer, the derived index and the verdict log -- reusing
`validate`, `lint` and `derive`'s own building blocks -- rather than shelling
out to those subcommands: `validate.collect_and_validate` runs once, its
`documents` feed both the index check and the freshness/age sections, and
`verdicts.latest_records` reads the log once for both the verdict view and
`recorded_at`. `status` never runs `probe`.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import derive as derive_module
from . import lint as lint_module
from . import validate
from . import verdicts as verdicts_module
from .findings import ERROR, EXIT_ERROR, EXIT_OK, Finding, WARNING

# The two verdicts `--fail-on` accepts: `current` needs no opt-in, it never
# gates anything.
DRIFTED = verdicts_module.DRIFTED
UNKNOWN = verdicts_module.UNKNOWN


def parse_timestamp(value):
    """Parse an ISO 8601 timestamp, a trailing 'Z' accepted, to aware UTC.

    Used both as `--as-of`'s argparse `type` -- a `ValueError` here becomes
    the usage error (exit 2) argparse reports for a bad `type` conversion --
    and to read a verdict record's `recorded_at` for the age check, where a
    parse failure is caught by the caller instead and reported as "age
    unknown" rather than raised.
    """
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def run(skip_index, fail_on, max_verdict_age, fail_on_aged, as_of, stdout, stderr):
    """Report validation, lint, index and freshness. Returns an exit code.

    `fail_on` is the set of verdicts (`drifted`, `unknown`) whose presence on
    an active unit upgrades to a gating ERROR; empty by default, since
    freshness is reported, never gated, unless the adopter opts in.
    `max_verdict_age` (days) and `as_of` (the moment age is computed from)
    gate age only when `max_verdict_age` is not None -- see `_age_findings`.
    """
    fail_on = set(fail_on or ())
    as_of = as_of if as_of is not None else datetime.now(timezone.utc)

    findings = []
    summaries = []

    documents, validation_findings = validate.collect_and_validate(None)
    findings.extend(validation_findings)
    summaries.append(
        _section_summary("validate", len(documents), "unit(s)", validation_findings)
    )

    memory_documents, lint_findings = lint_module.collect_and_lint(None)
    findings.extend(lint_findings)
    summaries.append(
        _section_summary(
            "lint", len(memory_documents), "memory file(s)", lint_findings
        )
    )

    validation_ok = not any(finding.severity == ERROR for finding in validation_findings)
    if validation_ok:
        _check_derived_state(
            documents,
            skip_index,
            fail_on,
            max_verdict_age,
            fail_on_aged,
            as_of,
            findings,
            summaries,
        )

    for finding in findings:
        print(finding.render(), file=stderr)
    for line in summaries:
        print(line, file=stdout)

    error_count = sum(1 for finding in findings if finding.severity == ERROR)
    warning_count = sum(1 for finding in findings if finding.severity == WARNING)
    print(
        f"status: {error_count} error(s), {warning_count} warning(s) overall",
        file=stdout,
    )
    return EXIT_ERROR if error_count else EXIT_OK


def _check_derived_state(
    documents,
    skip_index,
    fail_on,
    max_verdict_age,
    fail_on_aged,
    as_of,
    findings,
    summaries,
):
    """The index, freshness and age sections: everything that needs the
    verdict log and a valid curated source. Appends to `findings` and
    `summaries` in place; nothing here runs when validation gates.
    """
    try:
        latest = verdicts_module.latest_records()
    except verdicts_module.VerdictLogError as error:
        findings.append(
            Finding(
                ERROR,
                verdicts_module.LOG_FILENAME,
                "log",
                error.message,
                line=error.lineno,
            )
        )
        return

    view = {key: record["verdict"] for key, record in latest.items()}
    states = derive_module.effective_states(documents)

    if skip_index:
        summaries.append("status: index: skipped (--skip-index)")
    else:
        table = derive_module.rows(states, view)
        basis = validate.basis_location(None)
        content = derive_module.render_index(table, basis)
        index_result = derive_module.index_findings(content, Path(derive_module.INDEX_FILENAME))
        findings.extend(index_result)
        if not index_result:
            summaries.append("status: index: up to date")

    active_units = sorted(
        unit_id for unit_id, (_data, state) in states.items() if state == "active"
    )
    counts = {verdicts_module.CURRENT: 0, verdicts_module.DRIFTED: 0, verdicts_module.UNKNOWN: 0}
    for unit_id in active_units:
        data, _state = states[unit_id]
        graded = derive_module.unit_verdict(unit_id, data.get("anchors") or [], view)
        counts[graded.verdict] += 1
        if graded.verdict in fail_on:
            findings.append(
                Finding(
                    ERROR,
                    unit_id,
                    "verdict",
                    f"active unit's verdict is '{graded.verdict}'; --fail-on "
                    f"{graded.verdict} gates it",
                )
            )
    summaries.append(
        f"status: freshness: {len(active_units)} active unit(s): "
        f"{counts[verdicts_module.CURRENT]} current, "
        f"{counts[verdicts_module.DRIFTED]} drifted, "
        f"{counts[verdicts_module.UNKNOWN]} unknown"
    )

    if max_verdict_age is not None:
        age_findings, aged_count, unknown_count = _age_findings(
            states, active_units, latest, as_of, max_verdict_age, fail_on_aged
        )
        findings.extend(age_findings)
        summaries.append(
            f"status: age: {aged_count} aged, {unknown_count} age-unknown "
            f"(max {max_verdict_age} day(s))"
        )


def _age_findings(states, active_units, latest, as_of, max_verdict_age, fail_on_aged):
    """WARNING (ERROR under `--fail-on-aged`) per active-unit anchor whose
    latest recorded verdict is more than `max_verdict_age` days old, or whose
    age cannot be determined (`recorded_at` absent, invalid, or in the
    future) -- ADR 0004: an enforced age bound cannot be satisfied by an age
    that cannot be verified, so `--fail-on-aged` upgrades both alike.

    An anchor never probed at all has no entry in `latest` and is skipped:
    the freshness section above already reports it as `unknown`, and an
    "age unknown" finding for it would only repeat that. Returns
    `(findings, aged_count, unknown_count)`.
    """
    severity = ERROR if fail_on_aged else WARNING
    findings = []
    aged_count = 0
    unknown_count = 0
    for unit_id in active_units:
        data, _state = states[unit_id]
        for anchor in data.get("anchors") or []:
            system = anchor.get("system")
            kind = anchor.get("kind")
            key = verdicts_module.anchor_key(unit_id, system, kind, anchor.get("payload"))
            record = latest.get(key)
            if record is None:
                continue
            label = f"{system}/{kind}"
            parsed = _try_parse_timestamp(record.get("recorded_at"))
            if parsed is None or parsed > as_of:
                unknown_count += 1
                findings.append(
                    Finding(
                        severity,
                        unit_id,
                        label,
                        "age unknown: recorded_at is missing, invalid or in "
                        "the future",
                    )
                )
                continue
            delta = as_of - parsed
            if delta > timedelta(days=max_verdict_age):
                aged_count += 1
                # `.days` alone would floor an over-threshold delta like
                # "N days and one second" back down to N, matching the
                # boundary it must not: the gate above compares the full
                # timedelta (ADR 0004's strict `age > N`), and only the
                # reported figure is floored to whole days.
                findings.append(
                    Finding(
                        severity,
                        unit_id,
                        label,
                        f"verdict is {delta.days} day(s) old (max {max_verdict_age})",
                    )
                )
    return findings, aged_count, unknown_count


def _try_parse_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        return parse_timestamp(value)
    except ValueError:
        return None


def _section_summary(label, checked, noun, section_findings):
    errors = sum(1 for finding in section_findings if finding.severity == ERROR)
    warnings = sum(1 for finding in section_findings if finding.severity == WARNING)
    return (
        f"status: {label}: {checked} {noun} checked, "
        f"{errors} error(s), {warnings} warning(s)"
    )
