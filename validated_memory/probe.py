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

Each command runs under its own deadline and may emit at most 1,048,576 raw
bytes across stdout and stderr. Both pipes are drained while it runs. Overflow
and expiry kill the invocation -- on POSIX its whole process group, on Windows
only the direct child -- and reap it within a bounded wait; a kill or reap that
fails is named in the WARNING. Output collection supports POSIX and Windows;
another runtime platform fails the invocation explicitly. These bounds are not
a subprocess sandbox: a descendant that leaves its process group can escape
cleanup, and a command that succeeds may leave descendants running.

Every anchor probed is appended to `verdicts.jsonl` (see `verdicts`), one
JSON line per anchor, regardless of the outcome.
"""

import json
import locale
import os
import selectors
import shlex
import signal
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import derive as derive_module
from . import extension as extension_module
from . import validate
from . import verdicts as verdicts_module
from .findings import EXIT_ERROR, EXIT_OK, WARNING, Finding

DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_TIMEOUT_SECONDS = 3600.0
OUTPUT_LIMIT_BYTES = 1_048_576
_REAP_SECONDS = 5.0
_READ_BYTES = 64 * 1024
_POLL_SECONDS = 0.05

_PLATFORM = os.name
_POSIX = _PLATFORM == "posix"


class _Expired(Exception):
    """A command reached its deadline; `problems` names any failed cleanup."""

    def __init__(self, problems):
        super().__init__(problems)
        self.problems = problems


class _OutputOverflow(Exception):
    """A command exceeded its output budget; `problems` names failed cleanup."""

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
    except _OutputOverflow as overflow:
        return (
            verdicts_module.UNKNOWN,
            None,
            "; ".join(
                [
                    "probe output exceeded the 1,048,576-byte aggregate "
                    "stdout/stderr limit",
                    *overflow.problems,
                ]
            ),
        )
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
    if _PLATFORM not in {"posix", "nt"}:
        raise OSError(
            f"probe output collection is unsupported on platform '{_PLATFORM}'"
        )

    with tempfile.TemporaryFile() as stdin:
        stdin.write(envelope.encode(locale.getpreferredencoding(False)))
        stdin.seek(0)
        process = subprocess.Popen(
            argv, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=_POSIX,
        )
        try:
            if _PLATFORM == "posix":
                return _collect_posix(process, timeout)
            return _collect_windows(process, timeout)
        except (_Expired, _OutputOverflow):
            raise
        except BaseException:
            # Cleanup is best-effort on an exceptional exit. In particular,
            # it must never replace the exception that interrupted the probe.
            try:
                _kill(process)
            except BaseException:
                pass
            raise


def _accept_output(buffers, stream, chunk, total):
    """Retain `chunk` within the shared limit; return `(total, overflowed)`."""
    remaining = OUTPUT_LIMIT_BYTES - total
    if len(chunk) > remaining:
        return total, True
    buffers[stream].extend(chunk)
    return total + len(chunk), False


def _collect_posix(process, timeout):
    """Collect both pipes without blocking on a surviving descendant's EOF."""
    import array
    import fcntl
    import termios

    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    total = 0
    try:
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)

        while True:
            remaining_time = deadline - time.monotonic()
            events = selector.select(max(0.0, min(_POLL_SECONDS, remaining_time)))
            for key, _ in events:
                stream = key.fileobj
                remaining = OUTPUT_LIMIT_BYTES - total
                chunk = os.read(stream.fileno(), min(_READ_BYTES, remaining + 1))
                if not chunk:
                    selector.unregister(stream)
                    continue
                total, overflowed = _accept_output(streams, stream, chunk, total)
                if overflowed:
                    raise _OutputOverflow(_kill(process))

            returncode = process.poll()
            if returncode is not None:
                # Snapshot bytes already buffered when the direct child exits.
                # A surviving descendant may keep writing, so do not chase a
                # moving ready set or wait for inherited descriptors to close.
                pending = {}
                for stream in streams:
                    count = array.array("i", [0])
                    fcntl.ioctl(stream.fileno(), termios.FIONREAD, count, True)
                    pending[stream] = count[0]
                for stream, count in pending.items():
                    while count:
                        remaining = OUTPUT_LIMIT_BYTES - total
                        chunk = os.read(
                            stream.fileno(),
                            min(count, _READ_BYTES, remaining + 1),
                        )
                        if not chunk:
                            break
                        count -= len(chunk)
                        total, overflowed = _accept_output(
                            streams, stream, chunk, total
                        )
                        if overflowed:
                            raise _OutputOverflow(_kill(process))
                return returncode, bytes(streams[process.stdout]), bytes(
                    streams[process.stderr]
                )

            if time.monotonic() >= deadline:
                raise _Expired(_kill(process))
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()


def _collect_windows(process, timeout):
    """Collect Windows anonymous pipes without waiting for inherited EOF."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    deadline = time.monotonic() + timeout
    total = 0
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.PeekNamedPipe.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    kernel32.PeekNamedPipe.restype = wintypes.BOOL
    available = ctypes.c_ulong()

    def pending(stream):
        handle = msvcrt.get_osfhandle(stream.fileno())
        available.value = 0
        ok = kernel32.PeekNamedPipe(
            handle, None, 0, None, ctypes.byref(available), None
        )
        if ok:
            return available.value
        error = ctypes.get_last_error()
        if error == 109:  # ERROR_BROKEN_PIPE: every writer has closed.
            return 0
        raise ctypes.WinError(error)

    try:
        while True:
            returncode = process.poll()
            if returncode is not None:
                # Freeze one snapshot per pipe. A surviving descendant may
                # keep writing, so bytes arriving after this point are outside
                # this completed direct-child invocation.
                snapshot = {stream: pending(stream) for stream in streams}
                for stream, count in snapshot.items():
                    while count:
                        remaining = OUTPUT_LIMIT_BYTES - total
                        chunk = os.read(
                            stream.fileno(),
                            min(count, _READ_BYTES, remaining + 1),
                        )
                        if not chunk:
                            break
                        count -= len(chunk)
                        total, overflowed = _accept_output(
                            streams, stream, chunk, total
                        )
                        if overflowed:
                            raise _OutputOverflow(_kill(process))
                return returncode, bytes(streams[process.stdout]), bytes(
                    streams[process.stderr]
                )

            read_any = False
            for stream in streams:
                count = pending(stream)
                if not count:
                    continue
                read_any = True
                remaining = OUTPUT_LIMIT_BYTES - total
                chunk = os.read(
                    stream.fileno(), min(count, _READ_BYTES, remaining + 1)
                )
                total, overflowed = _accept_output(streams, stream, chunk, total)
                if overflowed:
                    raise _OutputOverflow(_kill(process))

            if time.monotonic() >= deadline:
                raise _Expired(_kill(process))
            if not read_any:
                time.sleep(min(_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
    finally:
        process.stdout.close()
        process.stderr.close()


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
