"""End-to-end tests for `probe`: the freshness-probe framework.

Fixtures register fake probes in `validated-memory.md`'s `probes:` map. A fake
probe is a small Python script invoked through the interpreter
(`sys.executable script.py`), so no `chmod +x` or shebang is needed: the
registered command is just split with `shlex.split`, exactly like any other
probe command would be.

`probe` writes one append-only JSON line per anchor probed to `verdicts.jsonl`
in the working directory. That file is a produced artifact, like
`knowledge-index.md`, so tests read it directly instead of importing any
package internals.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

VERDICT_LOG = "verdicts.jsonl"
REPO_ROOT = Path(__file__).resolve().parents[1]
POSIX = os.name == "posix"

ACTIVE_UNIT = """\
id: kb-0001
evidence: measured
anchors: []
"""


def _records(adopter_dir):
    path = adopter_dir / VERDICT_LOG
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- the validation gate (shared with validate/derive) ----------------------


def test_a_validation_error_gates_and_probes_nothing(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", "id: kb-0001\nevidence: probable\nanchors: []\n")

    result = run_cli("probe", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: knowledge/kb-0001.md: evidence: " in result.stderr
    assert not (adopter_dir / VERDICT_LOG).exists()


def test_a_validation_warning_does_not_gate_probe(adopter_dir, write_unit, run_cli):
    # No anchors is a WARNING, not an ERROR; probe still runs (there is
    # nothing to probe, but the run itself is not blocked).
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n")

    result = run_cli("probe", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "WARNING: knowledge/kb-0001.md: anchors: " in result.stderr


def test_a_missing_default_directory_gates_and_points_at_init(adopter_dir, run_cli):
    result = run_cli("probe", cwd=adopter_dir)

    assert result.returncode == 1
    assert "knowledge" in result.stderr
    assert "init" in result.stderr


def test_an_explicit_path_overrides_the_default_directory(
    adopter_dir, write_unit, run_cli
):
    write_unit("kb-0001.md", ACTIVE_UNIT)
    (adopter_dir / "knowledge").rename(adopter_dir / "facts")

    result = run_cli("probe", "facts", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr


# --- the probe contract: dispatch by kind, the three verdicts, detail -------

CURRENT_PROBE = """\
import sys, json
sys.stdin.read()
print(json.dumps({"verdict": "current"}))
"""

DRIFTED_PROBE = """\
import sys, json
sys.stdin.read()
print(json.dumps({"verdict": "drifted", "detail": "branch moved since captured_at"}))
"""

UNKNOWN_PROBE = """\
import sys, json
sys.stdin.read()
print(json.dumps({"verdict": "unknown", "detail": "probe could not reach the system"}))
"""

THREE_ANCHOR_UNIT = """\
id: kb-0001
evidence: measured
anchors:
  - system: repo-a
    kind: git_ref
    captured_at: 2026-08-01T00:00:00Z
    payload: {}
  - system: api-b
    kind: http_health
    captured_at: 2026-08-01T00:00:00Z
    payload: {}
  - system: queue-c
    kind: sync_state
    captured_at: 2026-08-01T00:00:00Z
    payload: {}
"""


def test_probe_dispatches_by_kind_and_records_the_three_verdicts_with_detail(
    adopter_dir, write_document, write_unit, write_probe, run_cli
):
    current_cmd = write_probe("probes/current_probe.py", CURRENT_PROBE)
    drifted_cmd = write_probe("probes/drifted_probe.py", DRIFTED_PROBE)
    unknown_cmd = write_probe("probes/unknown_probe.py", UNKNOWN_PROBE)
    write_document(
        "validated-memory.md",
        "probes:\n"
        f"  git_ref: {current_cmd}\n"
        f"  http_health: {drifted_cmd}\n"
        f"  sync_state: {unknown_cmd}\n",
    )
    write_unit("kb-0001.md", THREE_ANCHOR_UNIT)

    result = run_cli("probe", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert (
        "probe: 3 anchor(s) probed across 1 unit(s): "
        "1 current, 1 drifted, 1 unknown" in result.stdout
    )

    records = {(record["system"], record["kind"]): record for record in _records(adopter_dir)}
    assert records[("repo-a", "git_ref")]["verdict"] == "current"
    assert records[("repo-a", "git_ref")]["detail"] is None
    assert records[("api-b", "http_health")]["verdict"] == "drifted"
    assert (
        records[("api-b", "http_health")]["detail"] == "branch moved since captured_at"
    )
    assert records[("queue-c", "sync_state")]["verdict"] == "unknown"
    assert (
        records[("queue-c", "sync_state")]["detail"]
        == "probe could not reach the system"
    )
    for record in records.values():
        assert record["unit"] == "kb-0001"
        assert "recorded_at" in record


# --- failure never aborts the run: unknown with a note -----------------------

CRASH_PROBE = """\
raise RuntimeError("simulated probe crash")
"""

UNREGISTERED_AND_CRASHING_UNIT = """\
id: kb-0001
evidence: measured
anchors:
  - system: repo-a
    kind: no_such_probe
    captured_at: 2026-08-01T00:00:00Z
    payload: {}
  - system: repo-b
    kind: crashy
    captured_at: 2026-08-01T00:00:00Z
    payload: {}
"""


def test_a_missing_registration_and_a_crashing_probe_both_yield_unknown_without_aborting(
    adopter_dir, write_document, write_unit, write_probe, run_cli
):
    crash_cmd = write_probe("probes/crash_probe.py", CRASH_PROBE)
    write_document("validated-memory.md", f"probes:\n  crashy: {crash_cmd}\n")
    write_unit("kb-0001.md", UNREGISTERED_AND_CRASHING_UNIT)

    result = run_cli("probe", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert (
        "probe: 2 anchor(s) probed across 1 unit(s): "
        "0 current, 0 drifted, 2 unknown" in result.stdout
    )
    assert "WARNING: kb-0001: anchors[0]: " in result.stderr
    assert "no_such_probe" in result.stderr
    assert "WARNING: kb-0001: anchors[1]: " in result.stderr

    records = {record["system"]: record for record in _records(adopter_dir)}
    assert records["repo-a"]["verdict"] == "unknown"
    assert records["repo-b"]["verdict"] == "unknown"


GARBAGE_PROBE = """\
print("not json")
"""

OUT_OF_DOMAIN_PROBE = """\
import sys, json
sys.stdin.read()
print(json.dumps({"verdict": "maybe"}))
"""


def test_a_missing_executable_unparseable_output_and_an_out_of_domain_verdict_all_yield_unknown(
    adopter_dir, write_document, write_unit, write_probe, run_cli
):
    garbage_cmd = write_probe("probes/garbage_probe.py", GARBAGE_PROBE)
    out_of_domain_cmd = write_probe("probes/out_of_domain_probe.py", OUT_OF_DOMAIN_PROBE)
    write_document(
        "validated-memory.md",
        "probes:\n"
        "  missing_exe: /no/such/executable-at-all\n"
        f"  garbage: {garbage_cmd}\n"
        f"  out_of_domain: {out_of_domain_cmd}\n",
    )
    write_unit(
        "kb-0001.md",
        "id: kb-0001\n"
        "evidence: measured\n"
        "anchors:\n"
        "  - system: repo-a\n"
        "    kind: missing_exe\n"
        "    captured_at: 2026-08-01T00:00:00Z\n"
        "    payload: {}\n"
        "  - system: repo-b\n"
        "    kind: garbage\n"
        "    captured_at: 2026-08-01T00:00:00Z\n"
        "    payload: {}\n"
        "  - system: repo-c\n"
        "    kind: out_of_domain\n"
        "    captured_at: 2026-08-01T00:00:00Z\n"
        "    payload: {}\n",
    )

    result = run_cli("probe", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert (
        "probe: 3 anchor(s) probed across 1 unit(s): "
        "0 current, 0 drifted, 3 unknown" in result.stdout
    )
    records = {record["system"]: record for record in _records(adopter_dir)}
    assert records["repo-a"]["verdict"] == "unknown"
    assert records["repo-b"]["verdict"] == "unknown"
    assert records["repo-c"]["verdict"] == "unknown"


def test_with_no_configuration_at_all_every_anchor_falls_back_to_unknown(
    adopter_dir, write_unit, run_cli
):
    # No `validated-memory.md` means an empty probe registry: the same
    # fallback path as a `kind` that is simply not registered in it.
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)

    result = run_cli("probe", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "WARNING: kb-0001: anchors[0]: no probe registered for kind 'git_ref'" in (
        result.stderr
    )
    records = _records(adopter_dir)
    assert len(records) == 1
    assert records[0]["verdict"] == "unknown"


# --- append-only history; the service view is the latest record -------------

ONE_ANCHOR_UNIT = """\
id: kb-0001
evidence: measured
anchors:
  - system: repo-a
    kind: git_ref
    captured_at: 2026-08-01T00:00:00Z
    payload: {}
"""


def test_re_probing_appends_to_the_log_without_clobbering_history(
    adopter_dir, write_document, write_unit, write_probe, run_cli
):
    current_cmd = write_probe("probes/current_probe.py", CURRENT_PROBE)
    write_document("validated-memory.md", f"probes:\n  git_ref: {current_cmd}\n")
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)

    run_cli("probe", cwd=adopter_dir)

    drifted_cmd = write_probe("probes/drifted_probe.py", DRIFTED_PROBE)
    write_document("validated-memory.md", f"probes:\n  git_ref: {drifted_cmd}\n")

    result = run_cli("probe", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    records = _records(adopter_dir)
    # History is never rewritten: both runs' records are on the log, in order.
    assert len(records) == 2
    assert records[0]["verdict"] == "current"
    assert records[1]["verdict"] == "drifted"
    # The service view -- what a reader wants -- is the latest one.
    assert records[-1]["verdict"] == "drifted"


# --- an unwritable log is an operational ERROR, not a data finding ----------


def test_an_unwritable_verdict_log_is_an_operational_error(
    adopter_dir, write_document, write_unit, write_probe, run_cli
):
    # `verdicts.jsonl` is a directory, not a file: appending to it raises
    # OSError. Verdicts are data, so a `drifted` outcome never gates -- but a
    # registry that cannot be written is an operational failure and must.
    (adopter_dir / VERDICT_LOG).mkdir()
    current_cmd = write_probe("probes/current_probe.py", CURRENT_PROBE)
    write_document("validated-memory.md", f"probes:\n  git_ref: {current_cmd}\n")
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)

    result = run_cli("probe", cwd=adopter_dir)

    assert result.returncode == 1
    assert f"ERROR: {VERDICT_LOG}: " in result.stderr


# --- a corrupt verdict log fails loud for its readers -------------------------


def test_a_corrupt_verdict_log_gates_derive_with_an_error(
    adopter_dir, write_unit, run_cli
):
    # `probe` only appends; `derive` is the reader, and a reader must never
    # guess about a log it cannot parse -- nor dump a raw traceback.
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\nanchors: []\n")
    (adopter_dir / "verdicts.jsonl").write_text("not json\n", encoding="utf-8")

    result = run_cli("derive", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: verdicts.jsonl:1: " in result.stderr
    assert "Traceback" not in result.stderr
    assert not (adopter_dir / "knowledge-index.md").exists()


def test_a_verdict_outside_the_domain_in_the_log_gates_derive(
    adopter_dir, write_unit, run_cli
):
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\nanchors: []\n")
    record = (
        '{"kind": "git_ref", "recorded_at": "2026-08-12T08:00:00Z", '
        '"system": "repo-a", "unit": "kb-0001", "verdict": "maybe"}\n'
    )
    (adopter_dir / "verdicts.jsonl").write_text(record, encoding="utf-8")

    result = run_cli("derive", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: verdicts.jsonl:1: " in result.stderr
    assert "maybe" in result.stderr
    assert "Traceback" not in result.stderr


def test_the_record_carries_the_payload_that_was_probed(
    adopter_dir, write_document, write_unit, write_probe, run_cli
):
    # The log is a historical record. Without the payload it can say that
    # something of some kind in some system was probed, but not which thing --
    # a record that cannot say what it measured is not evidence.
    command = write_probe("probes/current_probe.py", CURRENT_PROBE)
    write_document("validated-memory.md", f"probes:\n  git_ref: {command}\n")
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors:\n"
        "  - system: repo-a\n    kind: git_ref\n"
        "    captured_at: 2026-08-01T00:00:00Z\n    payload:\n      ref: main\n",
    )

    run_cli("probe", cwd=adopter_dir)

    log = (adopter_dir / "verdicts.jsonl").read_text(encoding="utf-8")
    record = json.loads(log.splitlines()[0])
    assert record["payload"] == {"ref": "main"}


# --- the per-command deadline ------------------------------------------------
#
# These tests can hang if the deadline regresses, so none of them uses the
# shared `run_cli`: `_run_guarded` gives the CLI an independent watchdog and
# collects its output through temporary files, and every process a fixture
# probe leaves behind records its pid for the test to kill.

WATCHDOG_SECONDS = 20
OUTPUT_LIMIT_BYTES = 1_048_576
# Well below the watchdog, so a deadline that did not fire fails an
# assertion instead of reaching it.
PROMPT_SECONDS = 10
# How long a stalling fixture process sleeps: past the watchdog, yet bounded,
# so even a leaked process eventually exits on its own.
STALL_SECONDS = 45


def _run_guarded(*args, cwd, extra_env=None):
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(REPO_ROOT))
    env.update(extra_env or {})
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        started = time.monotonic()
        process = subprocess.Popen(
            [sys.executable, "-P", "-m", "validated_memory", *args],
            stdin=subprocess.DEVNULL, stdout=out, stderr=err, cwd=cwd, env=env,
            start_new_session=POSIX,
        )
        try:
            process.wait(timeout=WATCHDOG_SECONDS)
        except subprocess.TimeoutExpired:
            pytest.fail(f"probe {args} still running after {WATCHDOG_SECONDS}s")
        finally:
            if process.poll() is None:
                if POSIX:
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
        elapsed = time.monotonic() - started
        out.seek(0)
        err.seek(0)
        result = subprocess.CompletedProcess(
            args, process.returncode,
            out.read().decode("utf-8", "replace"),
            err.read().decode("utf-8", "replace"),
        )
    return result, elapsed


def _running(pid):
    """Whether `pid` is a live process; a zombie awaiting its reaper is not."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if not Path("/proc/self/stat").exists():
        return True
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _gone_within(pid, seconds):
    deadline = time.monotonic() + seconds
    while _running(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


@pytest.fixture
def leftover_pids(adopter_dir):
    """Kill every pid a fixture probe recorded under `pids/`, after the test."""
    directory = adopter_dir / "pids"
    directory.mkdir()
    yield directory
    for path in directory.iterdir():
        try:
            pid = int(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if _running(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def _anchors(*kinds):
    lines = ["id: kb-0001", "evidence: measured", "anchors:"]
    for position, kind in enumerate(kinds):
        lines += [
            f"  - system: system-{position}",
            f"    kind: {kind}",
            "    captured_at: 2026-08-01T00:00:00Z",
            "    payload:",
            "      ref: main",
        ]
    return "\n".join(lines) + "\n"


def _registry(**commands):
    return "probes:\n" + "".join(
        f"  {kind}: {command}\n" for kind, command in commands.items()
    )


def _output_probe(stdout_bytes, stderr_bytes=0, exit_code=0, delay=0):
    """Bounded fixture source emitting exact byte counts without large literals."""
    verdict = b'{"verdict":"current"}'
    if stdout_bytes and stdout_bytes < len(verdict):
        raise ValueError("stdout_bytes cannot hold the verdict fixture")
    return (
        "import sys, time\n"
        "sys.stdin.buffer.read()\n"
        f"time.sleep({delay!r})\n"
        f"verdict = {verdict!r}\n"
        f"sys.stdout.buffer.write(verdict + b' ' * ({stdout_bytes} - len(verdict)))\n"
        "sys.stdout.buffer.flush()\n"
        f"sys.stderr.buffer.write(b'e' * {stderr_bytes})\n"
        "sys.stderr.buffer.flush()\n"
        f"sys.exit({exit_code})\n"
    )


def _stall_probe(pids):
    return (
        "import os, time\n"
        f"open({str(pids / 'stalled')!r}, 'w').write(str(os.getpid()))\n"
        f"time.sleep({STALL_SECONDS})\n"
    )


ECHO_PROBE = """\
import sys, json
print(json.dumps({"verdict": "drifted", "detail": sys.stdin.read()}))
"""


@pytest.mark.parametrize("timeout_args", [[], ["--timeout", "30"], ["--timeout", "3600"]])
def test_a_probe_within_its_deadline_keeps_the_envelope_detail_and_summary(
    adopter_dir, write_document, write_unit, write_probe, timeout_args
):
    command = write_probe("probes/echo_probe.py", ECHO_PROBE)
    write_document("validated-memory.md", _registry(git_ref=command))
    write_unit("kb-0001.md", _anchors("git_ref"))

    result, _ = _run_guarded("probe", *timeout_args, cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == (
        "probe: 1 anchor(s) probed across 1 unit(s): 0 current, 1 drifted, 0 unknown\n"
    )
    [record] = _records(adopter_dir)
    assert record["verdict"] == "drifted"
    assert json.loads(record["detail"]) == {
        "system": "system-0",
        "kind": "git_ref",
        "captured_at": "2026-08-01T00:00:00Z",
        "payload": {"ref": "main"},
    }


def test_the_help_states_the_sixty_second_default_and_the_bounds(adopter_dir):
    result, _ = _run_guarded("probe", "--help", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    text = " ".join(result.stdout.split())
    assert "--timeout SECONDS" in text
    assert "at most 3600" in text
    assert "(default: 60)" in text
    assert "output-limit" not in text


@pytest.mark.parametrize(
    ("stdout_bytes", "stderr_bytes", "expected_verdict"),
    [
        (OUTPUT_LIMIT_BYTES, 0, "current"),
        (0, OUTPUT_LIMIT_BYTES, "unknown"),
        (700_000, OUTPUT_LIMIT_BYTES - 700_000, "current"),
    ],
)
def test_exactly_the_aggregate_output_limit_is_not_overflow(
    adopter_dir, write_document, write_unit, write_probe,
    stdout_bytes, stderr_bytes, expected_verdict
):
    source = (
        _output_probe(stdout_bytes, stderr_bytes)
        if stdout_bytes
        else "import sys\nsys.stdin.buffer.read()\n"
        f"sys.stderr.buffer.write(b'e' * {stderr_bytes})\n"
    )
    command = write_probe("probes/boundary_probe.py", source)
    write_document("validated-memory.md", _registry(boundary=command))
    write_unit("kb-0001.md", _anchors("boundary"))

    result, elapsed = _run_guarded("probe", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert elapsed < PROMPT_SECONDS
    assert "output exceeded" not in result.stderr
    [record] = _records(adopter_dir)
    assert (record["verdict"], record["detail"]) == (expected_verdict, None)


@pytest.mark.parametrize(
    ("stdout_bytes", "stderr_bytes"),
    [
        (OUTPUT_LIMIT_BYTES + 1, 0),
        (0, OUTPUT_LIMIT_BYTES + 1),
        (700_000, OUTPUT_LIMIT_BYTES - 700_000 + 1),
    ],
)
def test_the_next_aggregate_output_byte_overflows(
    adopter_dir, write_document, write_unit, write_probe,
    stdout_bytes, stderr_bytes
):
    source = (
        _output_probe(stdout_bytes, stderr_bytes)
        if stdout_bytes
        else "import sys\nsys.stdin.buffer.read()\n"
        f"sys.stderr.buffer.write(b'e' * {stderr_bytes})\n"
    )
    command = write_probe("probes/overflow_probe.py", source)
    write_document("validated-memory.md", _registry(overflow=command))
    write_unit("kb-0001.md", _anchors("overflow"))

    result, elapsed = _run_guarded("probe", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert elapsed < PROMPT_SECONDS
    assert result.stderr == (
        "WARNING: kb-0001: anchors[0]: probe output exceeded the "
        "1,048,576-byte aggregate stdout/stderr limit\n"
    )
    assert result.stdout == (
        "probe: 1 anchor(s) probed across 1 unit(s): "
        "0 current, 0 drifted, 1 unknown\n"
    )
    [record] = _records(adopter_dir)
    assert (record["verdict"], record["detail"]) == ("unknown", None)


def test_overflow_never_parses_a_valid_prefix_or_exposes_nonzero_stderr(
    adopter_dir, write_document, write_unit, write_probe
):
    command = write_probe(
        "probes/prefix_probe.py",
        _output_probe(OUTPUT_LIMIT_BYTES, 1, exit_code=7),
    )
    write_document("validated-memory.md", _registry(prefix=command))
    write_unit("kb-0001.md", _anchors("prefix"))

    result, _ = _run_guarded("probe", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "output exceeded the 1,048,576-byte aggregate stdout/stderr limit" in (
        result.stderr
    )
    assert "exited 7" not in result.stderr
    assert "eeee" not in result.stderr
    [record] = _records(adopter_dir)
    assert (record["verdict"], record["detail"]) == ("unknown", None)


def test_overflow_continues_to_later_anchors_and_preserves_log_history(
    adopter_dir, write_document, write_unit, write_probe
):
    prior = b'{"prior":"bytes stay exact"}\n'
    (adopter_dir / VERDICT_LOG).write_bytes(prior)
    overflow = write_probe(
        "probes/overflow_probe.py", _output_probe(OUTPUT_LIMIT_BYTES + 1)
    )
    current = write_probe("probes/current_probe.py", CURRENT_PROBE)
    write_document("validated-memory.md", _registry(overflow=overflow, fast=current))
    write_unit("kb-0001.md", _anchors("overflow", "fast"))

    result, elapsed = _run_guarded("probe", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert elapsed < PROMPT_SECONDS
    assert result.stdout == (
        "probe: 2 anchor(s) probed across 1 unit(s): "
        "1 current, 0 drifted, 1 unknown\n"
    )
    log = (adopter_dir / VERDICT_LOG).read_bytes()
    assert log.startswith(prior)
    records = [json.loads(line) for line in log[len(prior):].splitlines()]
    assert [(record["verdict"], record["detail"]) for record in records] == [
        ("unknown", None), ("current", None)
    ]


def test_concurrent_stdout_and_stderr_are_drained_without_deadlock(
    adopter_dir, write_document, write_unit, write_probe
):
    command = write_probe(
        "probes/alternating_probe.py",
        "import sys\n"
        "sys.stdin.buffer.read()\n"
        "for _ in range(129):\n"
        "    sys.stdout.buffer.write(b'o' * 4096)\n"
        "    sys.stdout.buffer.flush()\n"
        "    sys.stderr.buffer.write(b'e' * 4096)\n"
        "    sys.stderr.buffer.flush()\n",
    )
    write_document("validated-memory.md", _registry(alternating=command))
    write_unit("kb-0001.md", _anchors("alternating"))

    result, elapsed = _run_guarded("probe", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert elapsed < PROMPT_SECONDS
    assert "output exceeded the 1,048,576-byte aggregate stdout/stderr limit" in (
        result.stderr
    )


def test_immediate_exit_keeps_all_buffered_direct_child_output(
    adopter_dir, write_document, write_unit, write_probe
):
    payload = b'{"verdict":"current","detail":"buffered before exit"}'
    command = write_probe(
        "probes/immediate_probe.py",
        "import os, sys\n"
        "sys.stdin.buffer.read()\n"
        f"os.write(1, {payload!r})\n"
        "os._exit(0)\n",
    )
    write_document("validated-memory.md", _registry(immediate=command))
    write_unit("kb-0001.md", _anchors("immediate"))

    result, elapsed = _run_guarded("probe", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert elapsed < PROMPT_SECONDS
    assert result.stderr == ""
    [record] = _records(adopter_dir)
    assert (record["verdict"], record["detail"]) == (
        "current", "buffered before exit"
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows pipe snapshot behavior")
def test_windows_collection_ignores_writes_after_the_direct_child_exits(
    adopter_dir, write_document, write_unit, write_probe
):
    pid_path = adopter_dir / "surviving-writer.pid"
    started_path = adopter_dir / "surviving-writer.started"
    writer_source = (
        "import os, sys, time\n"
        "os.write(sys.stderr.fileno(), b'started')\n"
        f"open({str(started_path)!r}, 'w').close()\n"
        "try:\n"
        "    while True:\n"
        "        os.write(sys.stderr.fileno(), b'x' * 65536)\n"
        "except OSError:\n"
        f"    time.sleep({STALL_SECONDS})\n"
    )
    command = write_probe(
        "probes/windows_descendant_probe.py",
        "import json, subprocess, sys, time\n"
        "sys.stdin.buffer.read()\n"
        f"child = subprocess.Popen([sys.executable, '-c', {writer_source!r}])\n"
        f"open({str(pid_path)!r}, 'w').write(str(child.pid))\n"
        f"while not __import__('os').path.exists({str(started_path)!r}):\n"
        "    time.sleep(0.001)\n"
        "print(json.dumps({'verdict': 'current'}), flush=True)\n",
    )
    write_document("validated-memory.md", _registry(descendant=command))
    write_unit("kb-0001.md", _anchors("descendant"))

    try:
        result, elapsed = _run_guarded("probe", cwd=adopter_dir)
        # The former moving-pending loop kept finding the continuously
        # refilled stderr pipe and overflowed. A frozen post-exit snapshot
        # returns the parent's valid stdout without chasing later writes.
        assert result.returncode == 0, result.stderr
        assert elapsed < PROMPT_SECONDS
        assert result.stderr == ""
        [record] = _records(adopter_dir)
        assert (record["verdict"], record["detail"]) == ("current", None)
        pid = int(pid_path.read_text(encoding="utf-8"))
        assert _running(pid), "the writer did not survive ordinary success"
    finally:
        if pid_path.exists():
            pid = int(pid_path.read_text(encoding="utf-8"))
            if _running(pid):
                os.kill(pid, signal.SIGTERM)
            assert _gone_within(pid, 5), "the fixture writer was not terminated"


def test_unsupported_probe_platform_fails_explicitly_and_continues(
    adopter_dir, write_document, write_unit, write_probe
):
    marker = adopter_dir / "probe-launched"
    command = write_probe(
        "probes/marker_probe.py",
        f"open({str(marker)!r}, 'w').close()\n"
        "print('{\"verdict\": \"current\"}')\n",
    )
    write_document(
        "validated-memory.md", _registry(first=command, second=command)
    )
    write_unit("kb-0001.md", _anchors("first", "second"))
    shim = adopter_dir / "unsupported-platform"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        "import validated_memory.probe as probe_module\n"
        "probe_module._PLATFORM = 'injected-unsupported'\n",
        encoding="utf-8",
    )
    python_path = os.environ.get("PYTHONPATH", str(REPO_ROOT))

    result, elapsed = _run_guarded(
        "probe",
        cwd=adopter_dir,
        extra_env={"PYTHONPATH": os.pathsep.join([str(shim), python_path])},
    )

    diagnostic = (
        f"probe command '{command}' could not be run: probe output collection "
        "is unsupported on platform 'injected-unsupported'"
    )
    assert result.returncode == 0, result.stderr
    assert elapsed < PROMPT_SECONDS
    assert result.stderr == (
        f"WARNING: kb-0001: anchors[0]: {diagnostic}\n"
        f"WARNING: kb-0001: anchors[1]: {diagnostic}\n"
    )
    assert len(result.stderr.encode("utf-8")) < 2048
    assert "Traceback" not in result.stderr
    assert not marker.exists()
    assert result.stdout == (
        "probe: 2 anchor(s) probed across 1 unit(s): "
        "0 current, 0 drifted, 2 unknown\n"
    )
    outcomes = [
        (record["verdict"], record["detail"])
        for record in _records(adopter_dir)
    ]
    assert outcomes == [("unknown", None), ("unknown", None)]


@pytest.mark.skipif(not POSIX, reason="injected POSIX descriptor fault")
def test_output_snapshot_failure_is_unknown_without_a_traceback(
    adopter_dir, write_document, write_unit, write_probe
):
    command = write_probe("probes/current_probe.py", CURRENT_PROBE)
    write_document("validated-memory.md", _registry(current=command))
    write_unit("kb-0001.md", _anchors("current"))
    fault_dir = adopter_dir / "snapshot-fault"
    fault_dir.mkdir()
    (fault_dir / "sitecustomize.py").write_text(
        "import fcntl\n"
        "def failing_ioctl(*args, **kwargs):\n"
        "    raise OSError(5, 'injected snapshot failure')\n"
        "fcntl.ioctl = failing_ioctl\n",
        encoding="utf-8",
    )
    python_path = os.environ.get("PYTHONPATH", str(REPO_ROOT))

    result, elapsed = _run_guarded(
        "probe",
        cwd=adopter_dir,
        extra_env={
            "PYTHONPATH": os.pathsep.join([str(fault_dir), python_path])
        },
    )

    assert result.returncode == 0, result.stderr
    assert elapsed < PROMPT_SECONDS
    assert "Traceback" not in result.stderr
    assert "could not be run: [Errno 5] injected snapshot failure" in result.stderr
    [record] = _records(adopter_dir)
    assert (record["verdict"], record["detail"]) == ("unknown", None)


def test_timeout_observed_before_output_overflow_remains_the_primary_reason(
    adopter_dir, write_document, write_unit, write_probe
):
    command = write_probe(
        "probes/late_output_probe.py",
        _output_probe(OUTPUT_LIMIT_BYTES + 1, delay=2),
    )
    write_document("validated-memory.md", _registry(late=command))
    write_unit("kb-0001.md", _anchors("late"))

    result, _ = _run_guarded("probe", "--timeout", "0.25", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "timed out after 0.25 second(s)" in result.stderr
    assert "output exceeded" not in result.stderr


@pytest.mark.skipif(not POSIX, reason="process-group cleanup is POSIX-only")
def test_overflow_cleanup_fault_is_appended_without_changing_the_outcome(
    adopter_dir, write_document, write_unit, write_probe
):
    command = write_probe(
        "probes/overflow_probe.py", _output_probe(OUTPUT_LIMIT_BYTES + 1)
    )
    write_document("validated-memory.md", _registry(overflow=command))
    write_unit("kb-0001.md", _anchors("overflow"))
    fault = _failing_killpg(
        adopter_dir, "    raise PermissionError(1, 'injected refusal')\n"
    )

    result, elapsed = _run_guarded(
        "probe", cwd=adopter_dir, extra_env=fault
    )

    assert result.returncode == 0, result.stderr
    assert elapsed < PROMPT_SECONDS
    assert result.stderr == (
        "WARNING: kb-0001: anchors[0]: probe output exceeded the "
        "1,048,576-byte aggregate stdout/stderr limit; killing its process "
        "group failed: [Errno 1] injected refusal\n"
    )
    [record] = _records(adopter_dir)
    assert (record["verdict"], record["detail"]) == ("unknown", None)


def test_an_expired_probe_reads_unknown_and_later_anchors_still_run(
    adopter_dir, write_document, write_unit, write_probe, leftover_pids
):
    prior = (
        '{"recorded_at": "2026-08-12T08:00:00Z", "unit": "kb-0001", '
        '"system": "system-0", "kind": "slow", "payload": {"ref": "main"}, '
        '"verdict": "current", "detail": null}\n'
    ).encode("utf-8")
    (adopter_dir / VERDICT_LOG).write_bytes(prior)
    slow_cmd = write_probe("probes/slow_probe.py", _stall_probe(leftover_pids))
    current_cmd = write_probe("probes/current_probe.py", CURRENT_PROBE)
    write_document("validated-memory.md", _registry(slow=slow_cmd, fast=current_cmd))
    write_unit("kb-0001.md", _anchors("slow", "fast"))

    result, elapsed = _run_guarded("probe", "--timeout", "0.5", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert elapsed < PROMPT_SECONDS
    assert result.stderr == (
        f"WARNING: kb-0001: anchors[0]: probe command '{slow_cmd}' "
        "timed out after 0.5 second(s)\n"
    )
    assert result.stdout == (
        "probe: 2 anchor(s) probed across 1 unit(s): 1 current, 0 drifted, 1 unknown\n"
    )
    log = (adopter_dir / VERDICT_LOG).read_bytes()
    assert log.startswith(prior)
    records = _records(adopter_dir)
    assert len(records) == 3
    assert (records[1]["kind"], records[1]["verdict"], records[1]["detail"]) == (
        "slow", "unknown", None,
    )
    assert (records[2]["kind"], records[2]["verdict"]) == ("fast", "current")
    assert set(records[1]) == set(records[0])


@pytest.mark.parametrize(
    "value", ["0", "-1", "nan", "inf", "-inf", "3600.001", "1e9", "soon", ""]
)
def test_an_invalid_timeout_is_a_usage_error_before_anything_runs(
    adopter_dir, write_document, write_unit, write_probe, value
):
    marker = adopter_dir / "probe-ran"
    command = write_probe(
        "probes/marker_probe.py",
        f"open({str(marker)!r}, 'w').close()\n"
        "print('{\"verdict\": \"current\"}')\n",
    )
    write_document("validated-memory.md", _registry(git_ref=command))
    # An invalid unit too: validation reaching it would gate with exit 1.
    write_unit("kb-0001.md", _anchors("git_ref").replace("measured", "probable"))

    def snapshot():
        return {
            path: path.read_bytes()
            for path in sorted(adopter_dir.rglob("*"))
            if path.is_file()
        }

    before = snapshot()

    result, _ = _run_guarded("probe", f"--timeout={value}", cwd=adopter_dir)

    assert result.returncode == 2
    assert "--timeout must be a number of seconds above 0 and at most 3600" in (
        result.stderr
    )
    assert "ERROR" not in result.stderr
    assert result.stdout == ""
    assert not marker.exists()
    assert not (adopter_dir / VERDICT_LOG).exists()
    assert snapshot() == before


UNDECODABLE_STDOUT_PROBE = """\
import sys
sys.stdin.read()
sys.stdout.buffer.write(b"\\xff\\xfe\\xfa not text")
"""

UNDECODABLE_STDERR_PROBE = """\
import sys
sys.stdin.read()
sys.stderr.buffer.write(b"\\xff\\xfe\\xfa failure")
sys.exit(3)
"""


def test_undecodable_output_falls_back_to_unknown_without_a_traceback(
    adopter_dir, write_document, write_unit, write_probe
):
    stdout_cmd = write_probe("probes/stdout_probe.py", UNDECODABLE_STDOUT_PROBE)
    stderr_cmd = write_probe("probes/stderr_probe.py", UNDECODABLE_STDERR_PROBE)
    write_document(
        "validated-memory.md", _registry(bad_stdout=stdout_cmd, bad_stderr=stderr_cmd)
    )
    write_unit("kb-0001.md", _anchors("bad_stdout", "bad_stderr"))

    result, _ = _run_guarded("probe", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert (
        f"WARNING: kb-0001: anchors[0]: probe command '{stdout_cmd}' "
        "produced unparseable output on stdout\n" in result.stderr
    )
    assert (
        f"WARNING: kb-0001: anchors[1]: probe command '{stderr_cmd}' exited 3: "
        in result.stderr
    )
    assert "failure" in result.stderr
    assert [record["verdict"] for record in _records(adopter_dir)] == [
        "unknown", "unknown",
    ]


def _descendant_source(pids, name):
    """Source that starts a sleeping grandchild inheriting stdout and stderr."""
    return (
        "import os, subprocess, sys\n"
        "child = subprocess.Popen(\n"
        f"    [sys.executable, '-c', 'import time; time.sleep({STALL_SECONDS})']\n"
        ")\n"
        f"open({str(pids / name)!r}, 'w').write(str(child.pid))\n"
    )


def _reaped_checker(pids):
    """Source of a probe answering `current` only if the stalled probe is reaped.

    It runs while the CLI is still alive, so a stalled probe the CLI killed
    but did not reap would still exist, as the CLI's zombie.
    """
    return (
        "import json, os, sys\n"
        "sys.stdin.read()\n"
        f"pid = int(open({str(pids / 'stalled')!r}).read())\n"
        "try:\n"
        "    os.kill(pid, 0)\n"
        "except ProcessLookupError:\n"
        "    print(json.dumps({'verdict': 'current'}))\n"
        "else:\n"
        "    print(json.dumps({'verdict': 'drifted', 'detail': 'not reaped'}))\n"
    )


@pytest.mark.skipif(not POSIX, reason="process-group cleanup is POSIX-only")
def test_overflow_kills_the_invocation_group_and_reaps_the_direct_child(
    adopter_dir, write_document, write_unit, write_probe, leftover_pids
):
    child_source = (
        "import sys, time; "
        f"sys.stdout.buffer.write(b'x' * {OUTPUT_LIMIT_BYTES + 1}); "
        "sys.stdout.buffer.flush(); time.sleep(45)"
    )
    command = write_probe(
        "probes/descendant_overflow_probe.py",
        "import os, subprocess, sys, time\n"
        "sys.stdin.buffer.read()\n"
        f"child = subprocess.Popen([sys.executable, '-c', {child_source!r}])\n"
        f"open({str(leftover_pids / 'descendant')!r}, 'w').write(str(child.pid))\n"
        f"open({str(leftover_pids / 'stalled')!r}, 'w').write(str(os.getpid()))\n"
        f"time.sleep({STALL_SECONDS})\n",
    )
    checker = write_probe("probes/checker_probe.py", _reaped_checker(leftover_pids))
    write_document("validated-memory.md", _registry(noisy=command, checker=checker))
    write_unit("kb-0001.md", _anchors("noisy", "checker"))

    result, elapsed = _run_guarded("probe", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert elapsed < PROMPT_SECONDS
    assert "anchors[0]: probe output exceeded" in result.stderr
    records = _records(adopter_dir)
    assert [(record["verdict"], record["detail"]) for record in records] == [
        ("unknown", None), ("current", None)
    ]
    descendant = int((leftover_pids / "descendant").read_text(encoding="utf-8"))
    assert _gone_within(descendant, 5), "the overflowing descendant survived"


def _failing_killpg(adopter_dir, body):
    """Environment whose `sitecustomize` replaces `os.killpg` in every Python.

    This injects a fault at the operating-system boundary of the CLI
    subprocess; nothing from the package is imported.
    """
    directory = adopter_dir / "fault"
    directory.mkdir()
    (directory / "sitecustomize.py").write_text(
        "import os\n\n\ndef killpg(pid, sig):\n"
        + body
        + "\nos.killpg = killpg\n",
        encoding="utf-8",
    )
    python_path = os.environ.get("PYTHONPATH", str(REPO_ROOT))
    return {"PYTHONPATH": os.pathsep.join([str(directory), python_path])}


@pytest.mark.skipif(not POSIX, reason="process-group cleanup is POSIX-only")
def test_an_expired_probe_is_killed_with_its_descendants_and_reaped(
    adopter_dir, write_document, write_unit, write_probe, leftover_pids
):
    # The stalled probe holds a grandchild that inherited its stdout and
    # stderr; the next probe checks that the expired probe was reaped.
    stalled = write_probe(
        "probes/stalled_probe.py",
        _descendant_source(leftover_pids, "descendant") + _stall_probe(leftover_pids),
    )
    checker = write_probe("probes/checker_probe.py", _reaped_checker(leftover_pids))
    write_document("validated-memory.md", _registry(stalled=stalled, checker=checker))
    write_unit("kb-0001.md", _anchors("stalled", "checker"))

    result, elapsed = _run_guarded("probe", "--timeout", "2", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert elapsed < PROMPT_SECONDS
    assert "anchors[0]: " in result.stderr and "timed out after 2 second(s)" in (
        result.stderr
    )
    records = _records(adopter_dir)
    assert records[0]["verdict"] == "unknown"
    assert (records[1]["verdict"], records[1]["detail"]) == ("current", None)
    descendant = int((leftover_pids / "descendant").read_text(encoding="utf-8"))
    assert _gone_within(descendant, 5), "the expired probe's descendant survived"


@pytest.mark.skipif(not POSIX, reason="process-group cleanup is POSIX-only")
def test_a_refused_group_kill_is_reported_and_the_probe_is_killed_directly(
    adopter_dir, write_document, write_unit, write_probe, leftover_pids
):
    # A kill failure other than "already exited" must neither pass silently
    # nor leave the run waiting, without a limit, for a probe still alive.
    stalled = write_probe("probes/stalled_probe.py", _stall_probe(leftover_pids))
    checker = write_probe("probes/checker_probe.py", _reaped_checker(leftover_pids))
    write_document("validated-memory.md", _registry(stalled=stalled, checker=checker))
    write_unit("kb-0001.md", _anchors("stalled", "checker"))
    fault = _failing_killpg(
        adopter_dir, "    raise PermissionError(1, 'injected refusal')\n"
    )

    result, elapsed = _run_guarded(
        "probe", "--timeout", "2", cwd=adopter_dir, extra_env=fault
    )

    assert result.returncode == 0, result.stderr
    assert elapsed < PROMPT_SECONDS
    assert (
        f"WARNING: kb-0001: anchors[0]: probe command '{stalled}' timed out after "
        "2 second(s); killing its process group failed: [Errno 1] injected refusal\n"
    ) in result.stderr
    records = _records(adopter_dir)
    assert (records[0]["verdict"], records[0]["detail"]) == ("unknown", None)
    assert (records[1]["verdict"], records[1]["detail"]) == ("current", None)


@pytest.mark.skipif(not POSIX, reason="process-group cleanup is POSIX-only")
def test_a_probe_that_survives_its_kill_is_reported_after_a_bounded_reap(
    adopter_dir, write_document, write_unit, write_probe, leftover_pids
):
    # The group kill reports success but kills nothing, so the reap can never
    # finish: the run must give up after its reap bound, say so, and go on.
    stalled = write_probe("probes/stalled_probe.py", _stall_probe(leftover_pids))
    current = write_probe("probes/current_probe.py", CURRENT_PROBE)
    write_document("validated-memory.md", _registry(stalled=stalled, fast=current))
    write_unit("kb-0001.md", _anchors("stalled", "fast"))
    fault = _failing_killpg(adopter_dir, "    return None\n")

    result, _ = _run_guarded(
        "probe", "--timeout", "1", cwd=adopter_dir, extra_env=fault
    )

    assert result.returncode == 0, result.stderr
    assert (
        f"WARNING: kb-0001: anchors[0]: probe command '{stalled}' timed out after "
        "1 second(s); it was not reaped within 5 second(s)\n"
    ) in result.stderr
    assert [record["verdict"] for record in _records(adopter_dir)] == [
        "unknown", "current",
    ]
    pid = int((leftover_pids / "stalled").read_text(encoding="utf-8"))
    assert _running(pid), "the fixture probe did not survive the ignored kill"


FD_PROBE = """\
import json, os, sys
sys.stdin.read()
links = [os.readlink(f"/proc/self/fd/{fd}") for fd in (0, 1, 2)]
print(json.dumps({"verdict": "current", "detail": json.dumps(links)}))
"""


@pytest.mark.skipif(
    not Path("/proc/self/fd").is_dir(), reason="inspects descriptors through /proc"
)
def test_probe_stdin_is_a_deleted_file_and_output_uses_live_pipes(
    adopter_dir, write_document, write_unit, write_probe
):
    # The streams follow `tempfile`'s configuration, even into the adopter
    # project, and leave nothing behind there.
    temporary = adopter_dir / "tmp"
    temporary.mkdir()
    command = write_probe("probes/fd_probe.py", FD_PROBE)
    write_document("validated-memory.md", _registry(git_ref=command))
    write_unit("kb-0001.md", _anchors("git_ref"))

    result, _ = _run_guarded(
        "probe", cwd=adopter_dir, extra_env={"TMPDIR": str(temporary)}
    )

    assert result.returncode == 0, result.stderr
    [record] = _records(adopter_dir)
    links = json.loads(record["detail"])
    assert len(links) == 3
    assert links[0].endswith(" (deleted)"), links[0]
    assert Path(links[0].removesuffix(" (deleted)")).parent == temporary.resolve()
    assert links[1].startswith("pipe:["), links[1]
    assert links[2].startswith("pipe:["), links[2]
    assert list(temporary.iterdir()) == []


@pytest.mark.skipif(not POSIX, reason="the fixture relies on POSIX process groups")
def test_collection_does_not_wait_for_a_descendant_holding_output_open(
    adopter_dir, write_document, write_unit, write_probe, leftover_pids
):
    # The probe answers and exits at once, but its grandchild keeps the
    # inherited stdout and stderr open. The deadline is longer than the
    # watchdog, so only collection that ignores descendant EOF returns in time.
    command = write_probe(
        "probes/answering_probe.py",
        "import json\n"
        + _descendant_source(leftover_pids, "descendant")
        + "sys.stdin.read()\n"
        "print(json.dumps({'verdict': 'current'}), flush=True)\n",
    )
    write_document("validated-memory.md", _registry(git_ref=command))
    write_unit("kb-0001.md", _anchors("git_ref"))

    result, elapsed = _run_guarded(
        "probe", "--timeout", str(WATCHDOG_SECONDS * 2), cwd=adopter_dir
    )

    assert result.returncode == 0, result.stderr
    assert elapsed < PROMPT_SECONDS
    assert [record["verdict"] for record in _records(adopter_dir)] == ["current"]
    descendant = int((leftover_pids / "descendant").read_text(encoding="utf-8"))
    assert _running(descendant), "the fixture descendant did not hold output open"
