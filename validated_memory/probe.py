"""The `probe` subcommand: run freshness probes and record ternary verdicts.

`probe` requires a valid source: it runs the same validation as `validate`
first (the base contract plus the adopter's declared extension) and probes
nothing when that validation reports an ERROR. It then walks the anchors of
every *active* unit -- one superseded within the validated set is not current
-- and dispatches each anchor to the probe registered for its `kind` in
`validated-memory.md`'s `probes` map.

Probe contract: the registered command is split with `shlex.split` and run
without a shell. It receives the anchor's envelope on stdin, as JSON:
`{"system": ..., "kind": ..., "captured_at": ..., "payload": {...}}` -- no
unit id, the producer/store boundary. It answers on stdout, as JSON:
`{"verdict": "current" | "drifted" | "unknown", "detail": "..."}` (`detail`
optional), with exit 0. Any failure falls back to `unknown`, with a note
explaining why, and never aborts the run: no probe registered for the
anchor's `kind` (or no configuration at all), a command that cannot be run,
a non-zero exit, a command still running at its deadline, unparseable stdout,
or a verdict outside the domain.

Each command runs under its own deadline. Its streams are anonymous temporary
files in `tempfile`'s configured directory, not pipes, so a descendant that
inherits stdout or stderr cannot keep the run waiting for EOF. On expiry the
invocation is killed -- on POSIX its whole process group, elsewhere only the
direct child -- and reaped within a bounded wait; a kill or reap that fails is
named in the WARNING. The deadline is not a sandbox: it bounds neither output
volume nor a descendant that leaves the group, and a command that succeeds may
leave descendants running.

Every anchor probed is appended to `verdicts.jsonl` (see `verdicts`), one
JSON line per anchor, regardless of the outcome.
"""

import json
import locale
import os
import shlex
import signal
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import derive as derive_module
from . import extension as extension_module
from . import validate
from . import verdicts as verdicts_module
from .findings import EXIT_ERROR, EXIT_OK, WARNING, Finding

DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_TIMEOUT_SECONDS = 3600.0
_REAP_SECONDS = 5.0

_POSIX = os.name == "posix"


class _Expired(Exception):
    """A command reached its deadline; `problems` names any failed cleanup."""

    def __init__(self, problems):
        super().__init__(problems)
        self.problems = problems


def run(path, stdout, stderr, timeout=DEFAULT_TIMEOUT_SECONDS):
    """Probe every active unit's anchors and record their verdicts."""
    documents, ok = validate.gated_source(path, stderr)
    if not ok:
        return EXIT_ERROR

    registry = extension_module.probes(Path())
    records, probe_findings = _probe_all(documents, registry, timeout)

    if records:
        try:
            verdicts_module.append(records)
        except OSError as error:
            print(
                f"ERROR: {verdicts_module.LOG_FILENAME}: log: cannot be written: "
                f"{error}",
                file=stderr,
            )
            return EXIT_ERROR

    for finding in probe_findings:
        print(finding.render(), file=stderr)

    print(_summary(records), file=stdout)
    return EXIT_OK


def _probe_all(documents, registry, timeout):
    """Dispatch every anchor of every active unit. Returns `(records, findings)`."""
    states = derive_module.effective_states(documents)
    recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    recorded_at = recorded_at.replace("+00:00", "Z")

    records = []
    findings = []
    for unit_id in sorted(states):
        data, state = states[unit_id]
        if state != "active":
            continue
        for position, anchor in enumerate(data.get("anchors") or []):
            verdict, detail, note = _dispatch(anchor, registry, timeout)
            if note:
                findings.append(
                    Finding(WARNING, unit_id, f"anchors[{position}]", note)
                )
            records.append(
                {
                    "recorded_at": recorded_at,
                    "unit": unit_id,
                    "system": anchor.get("system"),
                    "kind": anchor.get("kind"),
                    "payload": anchor.get("payload"),
                    "verdict": verdict,
                    "detail": detail,
                }
            )
    return records, findings


def _dispatch(anchor, registry, timeout):
    """Run the probe registered for `anchor`'s kind. Never raises.

    Returns `(verdict, detail, note)`: `note` is None on success, and set to
    an explanation whenever the outcome falls back to `unknown`.
    """
    kind = anchor.get("kind")
    command = registry.get(kind)
    if not command:
        return (
            verdicts_module.UNKNOWN,
            None,
            f"no probe registered for kind '{kind}'",
        )

    try:
        argv = shlex.split(command)
    except ValueError as error:
        return (
            verdicts_module.UNKNOWN,
            None,
            f"probe command '{command}' cannot be parsed: {error}",
        )

    envelope = json.dumps(
        {
            "system": anchor.get("system"),
            "kind": anchor.get("kind"),
            "captured_at": anchor.get("captured_at"),
            "payload": anchor.get("payload"),
        }
    )

    try:
        returncode, output, errors = _execute(argv, envelope, timeout)
    except _Expired as expired:
        return (
            verdicts_module.UNKNOWN,
            None,
            "; ".join(
                [
                    f"probe command '{command}' timed out after {timeout:g} second(s)",
                    *expired.problems,
                ]
            ),
        )
    except OSError as error:
        return (
            verdicts_module.UNKNOWN,
            None,
            f"probe command '{command}' could not be run: {error}",
        )

    if returncode != 0:
        return (
            verdicts_module.UNKNOWN,
            None,
            f"probe command '{command}' exited {returncode}: "
            f"{_text(errors, 'replace').strip()}",
        )

    try:
        payload = json.loads(_text(output))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return (
            verdicts_module.UNKNOWN,
            None,
            f"probe command '{command}' produced unparseable output on stdout",
        )

    if not isinstance(payload, dict) or payload.get("verdict") not in verdicts_module.VERDICTS:
        return (
            verdicts_module.UNKNOWN,
            None,
            f"probe command '{command}' returned a verdict outside "
            + ", ".join(verdicts_module.VERDICTS),
        )

    return payload["verdict"], payload.get("detail"), None


def _execute(argv, envelope, timeout):
    """Run `argv` with `envelope` on stdin, waiting at most `timeout` seconds.

    Returns `(returncode, stdout bytes, stderr bytes)`; raises `_Expired` when
    the deadline expired and the invocation was killed.
    """
    with (
        tempfile.TemporaryFile() as stdin,
        tempfile.TemporaryFile() as stdout,
        tempfile.TemporaryFile() as stderr,
    ):
        stdin.write(envelope.encode(locale.getpreferredencoding(False)))
        stdin.seek(0)
        process = subprocess.Popen(
            argv, stdin=stdin, stdout=stdout, stderr=stderr,
            start_new_session=_POSIX,
        )
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise _Expired(_kill(process)) from None
        except BaseException:
            # Cleanup is best-effort on an exceptional exit. In particular,
            # it must never replace the exception that interrupted the probe.
            try:
                _kill(process)
            except BaseException:
                pass
            raise
        stdout.seek(0)
        stderr.seek(0)
        return returncode, stdout.read(), stderr.read()


def _kill(process):
    """Kill the invocation -- its process group on POSIX -- and reap it.

    Never raises an `OSError` and waits at most `_REAP_SECONDS` to reap.
    Returns the cleanup problems; an empty list means the kill was delivered,
    or needless because the invocation had already exited, and it was reaped.
    """
    problems = []
    try:
        if _POSIX:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass  # The invocation exited on its own in the meantime.
    except OSError as error:
        target = "its process group" if _POSIX else "it"
        problems.append(f"killing {target} failed: {error}")
        if _POSIX:
            try:
                process.kill()
            except OSError as direct_error:
                problems.append(f"killing it directly failed: {direct_error}")
    try:
        process.wait(timeout=_REAP_SECONDS)
    except subprocess.TimeoutExpired:
        problems.append(f"it was not reaped within {_REAP_SECONDS:g} second(s)")
    except OSError as error:
        problems.append(f"waiting for it to be reaped failed: {error}")
    return problems


def _text(data, errors="strict"):
    """Decode captured output as `subprocess`'s text mode would."""
    text = data.decode(locale.getpreferredencoding(False), errors)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _summary(records):
    counts = {verdict: 0 for verdict in verdicts_module.VERDICTS}
    units = set()
    for record in records:
        counts[record["verdict"]] += 1
        units.add(record["unit"])
    return (
        f"probe: {len(records)} anchor(s) probed across {len(units)} unit(s): "
        f"{counts[verdicts_module.CURRENT]} current, "
        f"{counts[verdicts_module.DRIFTED]} drifted, "
        f"{counts[verdicts_module.UNKNOWN]} unknown"
    )
