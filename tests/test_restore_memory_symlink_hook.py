"""End-to-end tests for the `SessionStart` hook (`hooks/restore-memory-symlink.sh`).

This closes the wiring the README's `--harness-memory` section deferred to
"a later ticket": on every session start, the hook restores the move-proof
symlink `init --harness-memory` creates, for whatever project the harness
just opened, with no manual step.

The hook is invoked as a subprocess (`bash hooks/restore-memory-symlink.sh`)
with a controlled, minimal environment -- fake `HOME`, `CLAUDE_CONFIG_DIR`
and `CLAUDE_PROJECT_DIR` under `tmp_path`, plus the real `PATH` so `bash`,
coreutils and `python3` resolve. The hook itself locates the plugin's own
`validated_memory` package relative to its own path, so no `PYTHONPATH` is
injected here -- exercising exactly the self-sufficiency a real plugin
install needs, without hand-holding from the test.

Every scenario here must be fail-open: no case may make the hook exit
non-zero, and no case may delete data. That is the whole point of a
`SessionStart` hook -- it must never be able to break a session.
"""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "hooks" / "restore-memory-symlink.sh"


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


def _slug(path):
    """The same '/'->'-' substitution the hook uses for the harness's per-project dir."""
    return str(path).replace("/", "-")


def _write_adopter_project(project_dir):
    """Create the minimal adopter markers the hook checks for: config + memory/."""
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "validated-memory.md").write_text(
        "---\nid_prefix: kb-\n---\n\nAdopter configuration.\n", encoding="utf-8"
    )
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(exist_ok=True)
    (memory_dir / "coffee-preference.md").write_text(
        "---\nname: coffee-preference\ndescription: Prefers oat milk.\n"
        "metadata:\n  type: user\n---\n\nBody.\n",
        encoding="utf-8",
    )
    (memory_dir / "MEMORY.md").write_text(
        "# Agent memory\n\n- [Coffee preference](coffee-preference.md) — oat milk\n",
        encoding="utf-8",
    )
    return memory_dir


# --- adopter project: the symlink is created ---------------------------------


def test_hook_creates_the_symlink_for_an_adopter_project(tmp_path):
    project_dir = tmp_path / "project"
    memory_dir = _write_adopter_project(project_dir)
    config_dir = tmp_path / "config"

    result = _run_hook(
        {
            "HOME": str(tmp_path / "home"),
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "CLAUDE_PROJECT_DIR": str(project_dir),
        }
    )

    assert result.returncode == 0, result.stderr
    harness_memory = config_dir / "projects" / _slug(project_dir) / "memory"
    assert harness_memory.is_symlink()
    assert harness_memory.resolve() == memory_dir.resolve()
    assert (harness_memory / "coffee-preference.md").read_text(encoding="utf-8") == (
        memory_dir / "coffee-preference.md"
    ).read_text(encoding="utf-8")


# --- non-adopter project: a clean no-op ---------------------------------------


def test_hook_is_a_clean_noop_for_a_non_adopter_project(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_dir = tmp_path / "config"

    result = _run_hook(
        {
            "HOME": str(tmp_path / "home"),
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "CLAUDE_PROJECT_DIR": str(project_dir),
        }
    )

    assert result.returncode == 0, result.stderr
    assert not (config_dir / "projects").exists()


def test_hook_is_a_clean_noop_when_only_the_config_file_is_present(tmp_path):
    # Half-adopted (e.g. mid-scaffold): validated-memory.md without memory/
    # is not yet an adopter project either.
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "validated-memory.md").write_text("id_prefix: kb-\n", encoding="utf-8")
    config_dir = tmp_path / "config"

    result = _run_hook(
        {
            "HOME": str(tmp_path / "home"),
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "CLAUDE_PROJECT_DIR": str(project_dir),
        }
    )

    assert result.returncode == 0, result.stderr
    assert not (config_dir / "projects").exists()


# --- no CLAUDE_PROJECT_DIR at all: exit 0, untouched --------------------------


def test_hook_exits_clean_without_a_claude_project_dir(tmp_path):
    result = _run_hook({"HOME": str(tmp_path / "home")})

    assert result.returncode == 0, result.stderr


# --- the harness-side symlink is re-pointed when it is stale ------------------


def test_hook_repoints_a_stale_symlink_left_over_from_before_a_rename_or_reclone(
    tmp_path,
):
    # Mirrors `init`'s own move-proof contract (see its README section and
    # docstring: "restoring it after the adopter project is renamed or
    # re-cloned is exactly re-running init --harness-memory PATH"). At the
    # hook's own boundary this shows up as: the harness-side symlink at the
    # computed path already exists, but points elsewhere or is broken --
    # left over from before the project living at this path was renamed or
    # re-cloned into place. Re-running the hook must re-point it at the
    # current project's memory/, without touching any data.
    project_dir = tmp_path / "project"
    memory_dir = _write_adopter_project(project_dir)
    config_dir = tmp_path / "config"
    harness_memory = config_dir / "projects" / _slug(project_dir) / "memory"
    harness_memory.parent.mkdir(parents=True)
    stale_target = tmp_path / "stale-elsewhere"
    stale_target.mkdir()
    harness_memory.symlink_to(stale_target, target_is_directory=True)

    result = _run_hook(
        {
            "HOME": str(tmp_path / "home"),
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "CLAUDE_PROJECT_DIR": str(project_dir),
        }
    )

    assert result.returncode == 0, result.stderr
    assert harness_memory.is_symlink()
    assert harness_memory.resolve() == memory_dir.resolve()
    assert (harness_memory / "coffee-preference.md").is_file()


def test_hook_repoints_a_broken_symlink(tmp_path):
    project_dir = tmp_path / "project"
    memory_dir = _write_adopter_project(project_dir)
    config_dir = tmp_path / "config"
    harness_memory = config_dir / "projects" / _slug(project_dir) / "memory"
    harness_memory.parent.mkdir(parents=True)
    harness_memory.symlink_to(tmp_path / "gone", target_is_directory=True)

    result = _run_hook(
        {
            "HOME": str(tmp_path / "home"),
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "CLAUDE_PROJECT_DIR": str(project_dir),
        }
    )

    assert result.returncode == 0, result.stderr
    assert harness_memory.resolve() == memory_dir.resolve()


# --- an existing real target: warn, exit 0, untouched -------------------------


def test_hook_leaves_an_existing_real_target_untouched_and_warns(tmp_path):
    project_dir = tmp_path / "project"
    _write_adopter_project(project_dir)
    config_dir = tmp_path / "config"
    harness_memory = config_dir / "projects" / _slug(project_dir) / "memory"
    harness_memory.mkdir(parents=True)
    marker = harness_memory / "pre-existing.md"
    marker.write_text("Do not touch.\n", encoding="utf-8")

    result = _run_hook(
        {
            "HOME": str(tmp_path / "home"),
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "CLAUDE_PROJECT_DIR": str(project_dir),
        }
    )

    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr
    assert not harness_memory.is_symlink()
    assert marker.read_text(encoding="utf-8") == "Do not touch.\n"


# --- stdout stays quiet; the hook never prints init's own chatter -------------


def test_hook_silences_init_stdout(tmp_path):
    project_dir = tmp_path / "project"
    _write_adopter_project(project_dir)
    config_dir = tmp_path / "config"

    result = _run_hook(
        {
            "HOME": str(tmp_path / "home"),
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "CLAUDE_PROJECT_DIR": str(project_dir),
        }
    )

    assert result.returncode == 0, result.stderr
    assert "init:" not in result.stdout


# --- idempotent: running it twice in a row keeps everything, changes nothing --


def test_hook_is_idempotent_across_two_runs(tmp_path):
    project_dir = tmp_path / "project"
    memory_dir = _write_adopter_project(project_dir)
    config_dir = tmp_path / "config"
    env = {
        "HOME": str(tmp_path / "home"),
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "CLAUDE_PROJECT_DIR": str(project_dir),
    }

    first = _run_hook(env)
    second = _run_hook(env)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    harness_memory = config_dir / "projects" / _slug(project_dir) / "memory"
    assert harness_memory.resolve() == memory_dir.resolve()


def test_a_relative_config_dir_resolves_against_the_hooks_own_cwd(tmp_path):
    # A relative CLAUDE_CONFIG_DIR must never leak into the adopter project:
    # the hook resolves it against its own working directory BEFORE changing
    # into the project to run `init`.
    project_dir = tmp_path / "project"
    _write_adopter_project(project_dir)

    result = _run_hook(
        {
            "CLAUDE_PROJECT_DIR": str(project_dir),
            "CLAUDE_CONFIG_DIR": "relcfg",
        },
        cwd=tmp_path,
    )

    assert result.returncode == 0
    expected = tmp_path / "relcfg" / "projects" / _slug(project_dir) / "memory"
    assert expected.is_symlink()
    assert not (project_dir / "relcfg").exists()
