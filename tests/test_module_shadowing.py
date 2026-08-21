"""Regression test for module shadowing under `python3 -m` (see ADR 0006).

VERIFIED FACT: under `python3 -m validated_memory`, `sys.path[0]` is the
current working directory, inserted before `PYTHONPATH`. An adopter project
that happens to contain its own `validated_memory/` package -- an unrelated
directory, a stray experiment, a name collision nobody chose on purpose --
shadows the plugin's package, and the adopter's code runs in its place
instead of the real CLI.

Each test here builds a fixture adopter tree carrying a hostile
`validated_memory/__init__.py` + `__main__.py` that proves it ran by writing
a marker file, then drives a real entry point against that tree: `validate`
through `run_cli` (`tests/conftest.py`, always invoked with `-P` now), and
the `refresh-views.sh` SessionStart hook (`hooks/refresh-views.sh`, which
also invokes the CLI with `-P`). Both must run the real package -- the
marker must never appear, and the real package's own output must.

The hook helpers here duplicate `tests/test_refresh_views_hook.py`'s
`_run_hook`/`_init_adopter` rather than importing them, matching this
suite's convention that every test file stays self-contained.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_SCRIPT = REPO_ROOT / "hooks" / "refresh-views.sh"

MARKER_NAME = "SHADOW-RAN"

_HOSTILE_MAIN = f"""\
from pathlib import Path

# __file__ is <adopter_dir>/validated_memory/__main__.py; the marker lands
# next to the package, at the adopter root, where the test looks for it.
Path(__file__).resolve().parent.parent.joinpath({MARKER_NAME!r}).write_text(
    "shadowed\\n", encoding="utf-8"
)
"""


def _plant_hostile_package(adopter_dir):
    """Drop a hostile `validated_memory/` package at the adopter's root.

    Real enough to be picked up as the `-m` target if it shadows: a package
    (`__init__.py`) with a `__main__.py`, exactly the shape `python3 -m
    validated_memory` looks for.
    """
    package_dir = adopter_dir / "validated_memory"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "__main__.py").write_text(_HOSTILE_MAIN, encoding="utf-8")


def _marker(adopter_dir):
    return adopter_dir / MARKER_NAME


# --- `validate`, via the CLI subprocess -----------------------------------


def test_validate_runs_the_real_package_not_the_shadowing_one(
    adopter_dir, write_unit, run_cli
):
    write_unit(
        "kb-0001.md",
        "id: kb-0001\n"
        "evidence: measured\n"
        "anchors:\n"
        "  - system: adopter-repo\n"
        "    kind: git_ref\n"
        "    captured_at: 2026-08-11T10:00:00Z\n"
        "    payload:\n"
        "      repo: .\n"
        "      ref: refs/heads/main\n",
    )
    _plant_hostile_package(adopter_dir)

    result = run_cli("validate", cwd=adopter_dir)

    assert not _marker(adopter_dir).exists(), (
        "the hostile validated_memory/ package under the adopter's cwd ran "
        "instead of the real, installed one"
    )
    assert "validate: 1 unit(s) checked, 0 error(s), 0 warning(s)" in result.stdout


# --- the `refresh-views.sh` SessionStart hook -------------------------------


def _run_hook(env_overrides, cwd=None):
    env = {"PATH": os.environ.get("PATH", "")}
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(HOOK_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def _init_adopter(project_dir, *args):
    """Scaffold an adopter project with the real CLI, run as a subprocess.

    Run before `_plant_hostile_package` in every test below, so `init`
    itself never risks being shadowed by the fixture it is about to create.
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    result = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init", *args],
        capture_output=True,
        text=True,
        cwd=project_dir,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return project_dir


def test_refresh_views_hook_runs_the_real_package_not_the_shadowing_one(tmp_path):
    project_dir = _init_adopter(tmp_path / "project", "--view")
    (project_dir / "knowledge.html").write_text("stale\n", encoding="utf-8")
    _plant_hostile_package(project_dir)

    result = _run_hook({"CLAUDE_PROJECT_DIR": str(project_dir)})

    assert result.returncode == 0, result.stderr
    assert not _marker(project_dir).exists(), (
        "the hostile validated_memory/ package under the adopter's cwd ran "
        "instead of the real, installed one"
    )
    refreshed = (project_dir / "knowledge.html").read_text(encoding="utf-8")
    assert refreshed != "stale\n"
    assert refreshed.startswith("<!doctype html>")
