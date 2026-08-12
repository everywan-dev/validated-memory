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

VERDICT_LOG = "verdicts.jsonl"

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
