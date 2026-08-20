"""End-to-end tests for the `SessionStart` hook (`hooks/refresh-views.sh`).

This is the piece that keeps an activated view fresh unattended: `init
--view` creates `knowledge.html` and `memory.html` once each, and this hook
runs `render --only-existing` on every session start so neither goes stale
while nobody remembers to rebuild it by hand. See
`validated_memory/render.py`'s `--only-existing` docstring for the contract
this delegates to.

The hook is invoked as a subprocess (`bash hooks/refresh-views.sh`) with a
controlled, minimal environment -- a fake `CLAUDE_PROJECT_DIR` under
`tmp_path`, plus the real `PATH` so `bash`, coreutils and `python3` resolve.
The hook itself locates the plugin's own `validated_memory` package
relative to its own path, so no `PYTHONPATH` is injected here -- exercising
exactly the self-sufficiency a real plugin install needs, without
hand-holding from the test. This mirrors
`test_restore_memory_symlink_hook.py`'s approach to its sibling hook.

Every scenario here must be fail-open: no case may make the hook exit
non-zero. Several cases also assert that no artifact is created, not only
that the process exits clean -- that is the whole point of
`--only-existing`: an adopter who never ran `init --view` has no artifacts,
so the hook must find nothing to do and cost them nothing.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "hooks" / "refresh-views.sh"


def _run_hook(env_overrides, cwd=None):
    env = {"PATH": os.environ.get("PATH", "")}
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def _init_adopter(project_dir, *args):
    """Scaffold an adopter project with the real CLI, run as a subprocess.

    `*args` is forwarded to `init` -- pass `"--view"` to also create both
    HTML artifacts once. This is the same `sys.executable -m
    validated_memory` invocation `conftest.py`'s `run_cli` fixture uses,
    written out directly here (rather than depending on that fixture) so
    this file reads standalone, matching how
    `test_restore_memory_symlink_hook.py` builds its own adopter fixture
    rather than importing one.
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    result = subprocess.run(
        [sys.executable, "-m", "validated_memory", "init", *args],
        capture_output=True,
        text=True,
        cwd=project_dir,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return project_dir


# --- no CLAUDE_PROJECT_DIR at all: exit 0 --------------------------------------


def test_hook_exits_clean_without_a_claude_project_dir(tmp_path):
    result = _run_hook({})

    assert result.returncode == 0, result.stderr


# --- non-adopter project: a clean no-op ----------------------------------------


def test_hook_is_a_clean_noop_for_a_non_adopter_project(tmp_path):
    # Neither `validated-memory.md` nor `memory/`: not an adopter project,
    # the same marker the sibling hook checks for.
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    result = _run_hook({"CLAUDE_PROJECT_DIR": str(project_dir)})

    assert result.returncode == 0, result.stderr
    assert not (project_dir / "knowledge.html").exists()
    assert not (project_dir / "memory.html").exists()


# --- no python3 on PATH: exit 0, nothing written --------------------------------


def test_hook_exits_clean_without_python3_on_path(tmp_path):
    project_dir = _init_adopter(tmp_path / "project", "--view")
    before = (project_dir / "knowledge.html").read_text(encoding="utf-8")

    # A PATH with only `bash` on it: enough to run the hook itself, nothing
    # else -- in particular no `python3`. Excluding one directory by name is
    # not reliable (on this machine `bash` and `python3` live side by side
    # under `/usr/bin`), so the minimal PATH only ever offers `bash`.
    minimal_bin = tmp_path / "minimal-bin"
    minimal_bin.mkdir()
    (minimal_bin / "bash").symlink_to(shutil.which("bash"))

    result = _run_hook(
        {"CLAUDE_PROJECT_DIR": str(project_dir), "PATH": str(minimal_bin)}
    )

    assert result.returncode == 0, result.stderr
    assert "python3" in result.stderr
    assert (project_dir / "knowledge.html").read_text(encoding="utf-8") == before


# --- adopter with no artifacts: the case that matters most ---------------------


def test_hook_is_a_clean_noop_for_an_adopter_who_never_activated_a_view(tmp_path):
    # Every adopter who never ran `init --view` lands here: no artifact
    # exists to regenerate, and `--only-existing` must create neither. This
    # is what makes the views opt-in at all -- the hook costs a plain
    # adopter nothing.
    project_dir = _init_adopter(tmp_path / "project")

    result = _run_hook({"CLAUDE_PROJECT_DIR": str(project_dir)})

    assert result.returncode == 0, result.stderr
    assert not (project_dir / "knowledge.html").exists()
    assert not (project_dir / "memory.html").exists()


# --- adopter with one artifact present: regenerated; the other still absent ----


def test_hook_regenerates_the_one_artifact_present_and_creates_no_other(tmp_path):
    project_dir = _init_adopter(tmp_path / "project", "--view")
    # Simulate a project where only `memory.html` was ever activated: delete
    # `knowledge.html` and make `memory.html`'s content stale.
    (project_dir / "knowledge.html").unlink()
    (project_dir / "memory.html").write_text("stale\n", encoding="utf-8")

    result = _run_hook({"CLAUDE_PROJECT_DIR": str(project_dir)})

    assert result.returncode == 0, result.stderr
    assert not (project_dir / "knowledge.html").exists()
    refreshed = (project_dir / "memory.html").read_text(encoding="utf-8")
    assert refreshed != "stale\n"
    assert refreshed.startswith("<!doctype html>")


# --- stdout stays quiet; the hook never prints render's own chatter ------------


def test_hook_silences_render_stdout(tmp_path):
    project_dir = _init_adopter(tmp_path / "project", "--view")
    (project_dir / "knowledge.html").write_text("stale\n", encoding="utf-8")

    result = _run_hook({"CLAUDE_PROJECT_DIR": str(project_dir)})

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
