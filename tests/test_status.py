"""End-to-end tests for `status`: the read-only consistency-and-freshness report.

`status` computes one internal pass over the curated layer, the agent-memory
layer, the derived index and the verdict log, without shelling out to
`validate`, `lint`, `derive` or `probe` and without running validation or
reading the verdict log twice (see docs/adr/0002, docs/adr/0004). It never
runs `probe`.

Most fixtures start from `validated-memory init` so the agent-memory layer
(an independent gate `status` also reports) is present and clean; tests that
exercise the index gate itself write units directly, the same way
test_derive.py does.
"""

import json

INDEX_FILENAME = "knowledge-index.md"
VERDICT_LOG = "verdicts.jsonl"

ACTIVE_UNIT = """\
id: kb-0001
evidence: measured
anchors: []
"""

ONE_ANCHOR_UNIT = """\
id: kb-0001
evidence: measured
anchors:
  - system: repo-a
    kind: git_ref
    captured_at: 2026-08-01T00:00:00Z
    payload: {}
"""


def _write_record(adopter_dir, **fields):
    path = adopter_dir / VERDICT_LOG
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(existing + json.dumps(fields) + "\n", encoding="utf-8")


# --- structural gates: validation, lint, index ------------------------------


def test_a_clean_project_after_derive_exits_ok(adopter_dir, write_unit, run_cli):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ACTIVE_UNIT)
    run_cli("derive", cwd=adopter_dir)

    result = run_cli("status", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "status: validate: 1 unit(s) checked, 0 error(s)" in result.stdout
    assert "status: lint: 0 memory file(s) checked, 0 error(s), 0 warning(s)" in (
        result.stdout
    )
    assert "status: index: up to date" in result.stdout
    assert "status: 0 error(s)" in result.stdout


def test_a_validation_error_gates_and_lint_still_runs(adopter_dir, write_unit, run_cli):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: probable\nanchors: []\n")

    result = run_cli("status", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: knowledge/kb-0001.md: evidence: " in result.stderr
    # lint is an independent layer and still runs and reports.
    assert "status: lint: 0 memory file(s) checked, 0 error(s), 0 warning(s)" in (
        result.stdout
    )
    # nothing that needs a valid source runs.
    assert "status: index:" not in result.stdout
    assert "status: freshness:" not in result.stdout


def test_a_missing_index_gates_with_error(adopter_dir, write_unit, run_cli):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ACTIVE_UNIT)

    result = run_cli("status", cwd=adopter_dir)

    assert result.returncode == 1
    assert f"ERROR: {INDEX_FILENAME}: index: file not found" in result.stderr
    assert "status: index:" not in result.stdout


def test_skip_index_bypasses_the_index_gate(adopter_dir, write_unit, run_cli):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ACTIVE_UNIT)

    result = run_cli("status", "--skip-index", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "status: index: skipped (--skip-index)" in result.stdout
    assert f"ERROR: {INDEX_FILENAME}" not in result.stderr


def test_a_hand_edited_index_fails_like_derive_check(adopter_dir, write_unit, run_cli):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ACTIVE_UNIT)
    run_cli("derive", cwd=adopter_dir)
    index_path = adopter_dir / INDEX_FILENAME
    mutated = index_path.read_text(encoding="utf-8").replace(
        "| kb-0001 | active | measured | unknown |",
        "| kb-0001 | active | hypothesis | unknown |",
    )
    index_path.write_text(mutated, encoding="utf-8")

    result = run_cli("status", cwd=adopter_dir)

    assert result.returncode == 1
    assert f"ERROR: {INDEX_FILENAME}: index: " in result.stderr
    assert index_path.read_text(encoding="utf-8") == mutated


def test_status_never_writes_the_index_or_the_log(adopter_dir, write_unit, run_cli):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ACTIVE_UNIT)
    run_cli("derive", cwd=adopter_dir)
    before = (adopter_dir / INDEX_FILENAME).read_text(encoding="utf-8")

    result = run_cli("status", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert (adopter_dir / INDEX_FILENAME).read_text(encoding="utf-8") == before
    assert not (adopter_dir / VERDICT_LOG).exists()


# --- freshness is reported, gated only by --fail-on -------------------------
#
# These pass --skip-index: they exercise the freshness/age sections in
# isolation, and a fake verdict record written after `derive` would
# otherwise also fail the unrelated index check (`derive --check` already
# covers that interaction; see test_derive.py).


def test_drifted_is_reported_but_does_not_gate_by_default(
    adopter_dir, write_unit, run_cli
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)
    _write_record(
        adopter_dir,
        recorded_at="2026-08-01T00:00:00Z",
        unit="kb-0001",
        system="repo-a",
        kind="git_ref",
        payload={},
        verdict="drifted",
        detail=None,
    )

    result = run_cli("status", "--skip-index", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "status: freshness: 1 active unit(s): 0 current, 1 drifted, 0 unknown" in (
        result.stdout
    )


def test_fail_on_drifted_gates(adopter_dir, write_unit, run_cli):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)
    _write_record(
        adopter_dir,
        recorded_at="2026-08-01T00:00:00Z",
        unit="kb-0001",
        system="repo-a",
        kind="git_ref",
        payload={},
        verdict="drifted",
        detail=None,
    )

    result = run_cli(
        "status", "--skip-index", "--fail-on", "drifted", cwd=adopter_dir
    )

    assert result.returncode == 1
    assert "ERROR: kb-0001: verdict: active unit's verdict is 'drifted'" in result.stderr


def test_fail_on_unknown_gates(adopter_dir, write_unit, run_cli):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)
    # Never probed: the anchor stays `unknown`.

    result = run_cli(
        "status", "--skip-index", "--fail-on", "unknown", cwd=adopter_dir
    )

    assert result.returncode == 1
    assert "ERROR: kb-0001: verdict: active unit's verdict is 'unknown'" in result.stderr


def test_unknown_does_not_gate_without_fail_on(adopter_dir, write_unit, run_cli):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)

    result = run_cli("status", "--skip-index", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "status: freshness: 1 active unit(s): 0 current, 0 drifted, 1 unknown" in (
        result.stdout
    )


def test_freshness_counts_only_active_units_excluding_superseded(
    adopter_dir, write_unit, run_cli
):
    # kb-0001 is superseded by kb-0002; kb-0001's own anchor is drifted, but a
    # superseded unit's verdicts describe knowledge already retired.
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)
    write_unit(
        "kb-0002.md",
        "id: kb-0002\nevidence: measured\nsupersedes:\n  - kb-0001\nanchors:\n"
        "  - system: repo-b\n    kind: git_ref\n"
        "    captured_at: 2026-08-01T00:00:00Z\n    payload: {}\n",
    )
    _write_record(
        adopter_dir,
        recorded_at="2026-08-01T00:00:00Z",
        unit="kb-0001",
        system="repo-a",
        kind="git_ref",
        payload={},
        verdict="drifted",
        detail=None,
    )
    _write_record(
        adopter_dir,
        recorded_at="2026-08-01T00:00:00Z",
        unit="kb-0002",
        system="repo-b",
        kind="git_ref",
        payload={},
        verdict="current",
        detail=None,
    )

    result = run_cli(
        "status", "--skip-index", "--fail-on", "drifted", cwd=adopter_dir
    )

    assert result.returncode == 0, result.stderr
    assert "status: freshness: 1 active unit(s): 1 current, 0 drifted, 0 unknown" in (
        result.stdout
    )
    assert "kb-0001" not in result.stderr


# --- verdict age (--max-verdict-age, --as-of, --fail-on-aged) ---------------

AS_OF = "2026-08-21T00:00:00Z"


def test_max_verdict_age_warns_on_an_aged_anchor_without_gating(
    adopter_dir, write_unit, run_cli
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)
    _write_record(
        adopter_dir,
        recorded_at="2026-08-01T00:00:00Z",  # 20 days before AS_OF
        unit="kb-0001",
        system="repo-a",
        kind="git_ref",
        payload={},
        verdict="current",
        detail=None,
    )

    result = run_cli(
        "status",
        "--skip-index",
        "--max-verdict-age",
        "10",
        "--as-of",
        AS_OF,
        cwd=adopter_dir,
    )

    assert result.returncode == 0, result.stderr
    assert "WARNING: kb-0001: repo-a/git_ref: verdict is 20 day(s) old (max 10)" in (
        result.stderr
    )
    assert "status: age: 1 aged, 0 age-unknown (max 10 day(s))" in result.stdout


def test_fail_on_aged_gates_an_aged_anchor(adopter_dir, write_unit, run_cli):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)
    _write_record(
        adopter_dir,
        recorded_at="2026-08-01T00:00:00Z",
        unit="kb-0001",
        system="repo-a",
        kind="git_ref",
        payload={},
        verdict="current",
        detail=None,
    )

    result = run_cli(
        "status",
        "--skip-index",
        "--max-verdict-age",
        "10",
        "--fail-on-aged",
        "--as-of",
        AS_OF,
        cwd=adopter_dir,
    )

    assert result.returncode == 1
    assert "ERROR: kb-0001: repo-a/git_ref: verdict is 20 day(s) old" in result.stderr


def test_boundary_age_equal_to_max_is_not_aged(adopter_dir, write_unit, run_cli):
    # recorded exactly 10 days before --as-of: age == 10, strictly not > 10.
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)
    _write_record(
        adopter_dir,
        recorded_at="2026-08-11T00:00:00Z",
        unit="kb-0001",
        system="repo-a",
        kind="git_ref",
        payload={},
        verdict="current",
        detail=None,
    )

    result = run_cli(
        "status",
        "--skip-index",
        "--max-verdict-age",
        "10",
        "--as-of",
        AS_OF,
        cwd=adopter_dir,
    )

    assert result.returncode == 0, result.stderr
    assert "repo-a/git_ref" not in result.stderr
    assert "status: age: 0 aged, 0 age-unknown (max 10 day(s))" in result.stdout


def test_recorded_at_absent_is_age_unknown_under_the_flag(
    adopter_dir, write_unit, run_cli
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)
    _write_record(
        adopter_dir,
        unit="kb-0001",
        system="repo-a",
        kind="git_ref",
        payload={},
        verdict="current",
        detail=None,
    )

    result = run_cli(
        "status",
        "--skip-index",
        "--max-verdict-age",
        "10",
        "--as-of",
        AS_OF,
        cwd=adopter_dir,
    )

    assert result.returncode == 0, result.stderr
    assert "WARNING: kb-0001: repo-a/git_ref: age unknown" in result.stderr
    assert "status: age: 0 aged, 1 age-unknown (max 10 day(s))" in result.stdout


def test_recorded_at_invalid_is_age_unknown_under_the_flag(
    adopter_dir, write_unit, run_cli
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)
    _write_record(
        adopter_dir,
        recorded_at="not-a-timestamp",
        unit="kb-0001",
        system="repo-a",
        kind="git_ref",
        payload={},
        verdict="current",
        detail=None,
    )

    result = run_cli(
        "status",
        "--skip-index",
        "--max-verdict-age",
        "10",
        "--as-of",
        AS_OF,
        cwd=adopter_dir,
    )

    assert result.returncode == 0, result.stderr
    assert "WARNING: kb-0001: repo-a/git_ref: age unknown" in result.stderr


def test_recorded_at_in_the_future_is_age_unknown_under_the_flag(
    adopter_dir, write_unit, run_cli
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)
    _write_record(
        adopter_dir,
        recorded_at="2026-08-22T00:00:00Z",  # one day after AS_OF
        unit="kb-0001",
        system="repo-a",
        kind="git_ref",
        payload={},
        verdict="current",
        detail=None,
    )

    result = run_cli(
        "status",
        "--skip-index",
        "--max-verdict-age",
        "10",
        "--as-of",
        AS_OF,
        cwd=adopter_dir,
    )

    assert result.returncode == 0, result.stderr
    assert "WARNING: kb-0001: repo-a/git_ref: age unknown" in result.stderr


def test_fail_on_aged_gates_age_unknown_too(adopter_dir, write_unit, run_cli):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)
    _write_record(
        adopter_dir,
        unit="kb-0001",
        system="repo-a",
        kind="git_ref",
        payload={},
        verdict="current",
        detail=None,
    )

    result = run_cli(
        "status",
        "--skip-index",
        "--max-verdict-age",
        "10",
        "--fail-on-aged",
        "--as-of",
        AS_OF,
        cwd=adopter_dir,
    )

    assert result.returncode == 1
    assert "ERROR: kb-0001: repo-a/git_ref: age unknown" in result.stderr


def test_without_the_flag_recorded_at_is_never_read(adopter_dir, write_unit, run_cli):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)
    _write_record(
        adopter_dir,
        recorded_at="not-a-timestamp",
        unit="kb-0001",
        system="repo-a",
        kind="git_ref",
        payload={},
        verdict="current",
        detail=None,
    )

    result = run_cli("status", "--skip-index", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "status: age:" not in result.stdout
    assert "age unknown" not in result.stderr


def test_an_anchor_never_probed_is_not_reported_as_age_unknown(
    adopter_dir, write_unit, run_cli
):
    # No record at all for the anchor: the freshness section already grades
    # it `unknown`; the age check does not repeat that as "age unknown".
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ONE_ANCHOR_UNIT)

    result = run_cli(
        "status",
        "--skip-index",
        "--max-verdict-age",
        "10",
        "--as-of",
        AS_OF,
        cwd=adopter_dir,
    )

    assert result.returncode == 0, result.stderr
    assert "status: age: 0 aged, 0 age-unknown (max 10 day(s))" in result.stdout


# --- the verdict log's own read contract (shared with derive) --------------


def test_an_unparseable_verdict_log_is_reported_like_derive(
    adopter_dir, write_unit, run_cli
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", ACTIVE_UNIT)
    run_cli("derive", cwd=adopter_dir)
    (adopter_dir / VERDICT_LOG).write_text("not json\n", encoding="utf-8")

    result = run_cli("status", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: verdicts.jsonl:1: " in result.stderr
    assert "Traceback" not in result.stderr
    assert "status: index:" not in result.stdout
    assert "status: freshness:" not in result.stdout


# --- usage errors (exit 2) ---------------------------------------------------


def test_max_verdict_age_rejects_a_non_integer(adopter_dir, run_cli):
    result = run_cli("status", "--max-verdict-age", "soon", cwd=adopter_dir)

    assert result.returncode == 2


def test_as_of_rejects_an_invalid_timestamp(adopter_dir, run_cli):
    result = run_cli("status", "--as-of", "yesterday", cwd=adopter_dir)

    assert result.returncode == 2


def test_fail_on_rejects_an_unknown_verdict(adopter_dir, run_cli):
    result = run_cli("status", "--fail-on", "current", cwd=adopter_dir)

    assert result.returncode == 2


def test_status_help_exits_clean(adopter_dir, run_cli):
    result = run_cli("status", "--help", cwd=adopter_dir)

    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
