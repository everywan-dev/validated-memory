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
import re
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
    """The harness's per-project directory name for `path`.

    Every character that is not a letter or a digit becomes '-'. Note this
    covers '_' and '.', not just '/': a project at `a/b_c/.d` lands under
    `-a-b-c--d`. Written out here rather than deferring to the hook, so the
    test states the rule the hook has to implement.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


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


def test_hook_does_not_repair_over_a_corrupt_journal(tmp_path):
    """The shell hook stays fail-open, but damaged history grants no repair."""
    project_dir = tmp_path / "project"
    memory_dir = _write_adopter_project(project_dir)
    config_dir = tmp_path / "config"
    environment = {
        "HOME": str(tmp_path / "home"),
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "CLAUDE_PROJECT_DIR": str(project_dir),
    }
    assert _run_hook(environment).returncode == 0
    harness_memory = config_dir / "projects" / _slug(project_dir) / "memory"
    harness_memory.unlink()
    journal = project_dir / "journal.jsonl"
    journal.write_text(
        journal.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8"
    )
    journal_before = journal.read_bytes()

    result = _run_hook(environment)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == (
        "ERROR: journal.jsonl:11: journal: line is not valid JSON: "
        "Expecting property name enclosed in double quotes. No target or "
        "permanent-history change was left by this operation\n"
    )
    assert not harness_memory.exists() and not harness_memory.is_symlink()
    assert journal.read_bytes() == journal_before
    assert memory_dir.is_dir()


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


def test_hook_absorbs_a_harness_memory_directory_that_already_holds_memories(
    tmp_path,
):
    # The deployment case: the harness already has native agent memory for
    # this project, written before the plugin was ever installed. The hook
    # must end with a single set of files, inside the project, visible through
    # the symlink -- not two memories that cannot see each other.
    project_dir = tmp_path / "project"
    memory_dir = _write_adopter_project(project_dir)
    config_dir = tmp_path / "config"
    harness_memory = config_dir / "projects" / _slug(project_dir) / "memory"
    harness_memory.mkdir(parents=True)
    (harness_memory / "deploy-window.md").write_text(
        "---\nname: deploy-window\ndescription: Tuesdays only.\n"
        "metadata:\n  type: project\n---\n\nBody.\n",
        encoding="utf-8",
    )
    (harness_memory / "MEMORY.md").write_text(
        "# Agent memory\n\n- [Deploy window](deploy-window.md) — Tuesdays only\n",
        encoding="utf-8",
    )

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
    # Both memories are now one set, reachable through the harness's path.
    assert (harness_memory / "deploy-window.md").is_file()
    assert (harness_memory / "coffee-preference.md").is_file()
    index = (harness_memory / "MEMORY.md").read_text(encoding="utf-8")
    assert "deploy-window.md" in index and "coffee-preference.md" in index
    # The harness's original directory survives untouched, alongside.
    parked = harness_memory.parent / "memory.bak"
    assert (parked / "deploy-window.md").is_file()


def test_post_park_link_failure_names_the_exact_backup_through_the_hook(
    tmp_path,
):
    project_dir = tmp_path / "project"
    memory_dir = _write_adopter_project(project_dir)
    config_dir = tmp_path / "config"
    harness_memory = config_dir / "projects" / _slug(project_dir) / "memory"
    harness_memory.mkdir(parents=True)
    native = harness_memory / "native.md"
    native_bytes = (
        b"---\nname: native\ndescription: Native memory.\n"
        b"metadata:\n  type: project\n---\n\nNative body.\n"
    )
    native.write_bytes(native_bytes)
    (harness_memory / "MEMORY.md").write_text(
        "# Agent memory\n\n- [Native](native.md) — Native memory.\n",
        encoding="utf-8",
    )
    sentinel = harness_memory.parent / ".memory.collision.tmp"
    sentinel.write_bytes(b"foreign staging name\n")

    result = _run_hook(
        {
            "HOME": str(tmp_path / "home"),
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "CLAUDE_PROJECT_DIR": str(project_dir),
            "VALIDATED_MEMORY_SYMLINK_TEMP_NAME": sentinel.name,
        }
    )

    backup = harness_memory.parent / "memory.bak"
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        f"ERROR: {harness_memory}: journal: could not be linked to "
        f"'{memory_dir.resolve()}': {harness_memory} could not be written: "
        "[Errno 17] File exists: "
        f"'{memory_dir.resolve()}' -> '{sentinel}'. The harness link was not "
        "published; "
        "harness memory was "
        f"parked at '{backup}' and remains there for recovery"
    ]
    assert not harness_memory.exists()
    assert not harness_memory.is_symlink()
    assert (backup / "native.md").read_bytes() == native_bytes
    assert sentinel.is_file() and not sentinel.is_symlink()
    assert sentinel.read_bytes() == b"foreign staging name\n"
    assert (memory_dir / "native.md").read_bytes() == native_bytes


def test_conflict_warning_survives_post_park_link_failure_through_the_hook(
    tmp_path,
):
    project_dir = tmp_path / "project"
    memory_dir = _write_adopter_project(project_dir)
    project_bytes = (memory_dir / "coffee-preference.md").read_bytes()
    config_dir = tmp_path / "config"
    harness_memory = config_dir / "projects" / _slug(project_dir) / "memory"
    harness_memory.mkdir(parents=True)
    harness_bytes = (
        b"---\nname: coffee-preference\ndescription: Prefers black coffee.\n"
        b"metadata:\n  type: user\n---\n\nHarness body.\n"
    )
    (harness_memory / "coffee-preference.md").write_bytes(harness_bytes)
    (harness_memory / "MEMORY.md").write_text(
        "# Agent memory\n\n- [Coffee preference](coffee-preference.md)"
        " — black coffee\n",
        encoding="utf-8",
    )
    sentinel = harness_memory.parent / ".memory.collision.tmp"
    sentinel_bytes = b"foreign staging name\n"
    sentinel.write_bytes(sentinel_bytes)

    result = _run_hook(
        {
            "HOME": str(tmp_path / "home"),
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "CLAUDE_PROJECT_DIR": str(project_dir),
            "VALIDATED_MEMORY_SYMLINK_TEMP_NAME": sentinel.name,
        }
    )

    backup = harness_memory.parent / "memory.bak"
    backup_file = backup / "coffee-preference.md"
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        f"WARNING: {memory_dir / 'coffee-preference.md'}: adopt: this project "
        "already has a different 'coffee-preference.md'; the project's copy "
        f"was kept and the harness's is preserved at '{backup_file}'",
        f"ERROR: {harness_memory}: journal: could not be linked to "
        f"'{memory_dir.resolve()}': {harness_memory} could not be written: "
        "[Errno 17] File exists: "
        f"'{memory_dir.resolve()}' -> '{sentinel}'. The harness link was not "
        "published; "
        "harness memory was "
        f"parked at '{backup}' and remains there for recovery",
    ]
    assert (memory_dir / "coffee-preference.md").read_bytes() == project_bytes
    assert backup_file.read_bytes() == harness_bytes
    assert not harness_memory.exists()
    assert not harness_memory.is_symlink()
    assert sentinel.read_bytes() == sentinel_bytes


def test_post_empty_directory_removal_link_failure_names_the_exact_state(
    tmp_path,
):
    project_dir = tmp_path / "project"
    memory_dir = _write_adopter_project(project_dir)
    config_dir = tmp_path / "config"
    harness_memory = config_dir / "projects" / _slug(project_dir) / "memory"
    harness_memory.mkdir(parents=True)
    sentinel = harness_memory.parent / ".memory.collision.tmp"
    sentinel.write_bytes(b"foreign staging name\n")

    result = _run_hook(
        {
            "HOME": str(tmp_path / "home"),
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "CLAUDE_PROJECT_DIR": str(project_dir),
            "VALIDATED_MEMORY_SYMLINK_TEMP_NAME": sentinel.name,
        }
    )

    backup = harness_memory.parent / "memory.bak"
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        f"ERROR: {harness_memory}: journal: could not be linked to "
        f"'{memory_dir.resolve()}': {harness_memory} could not be written: "
        "[Errno 17] File exists: "
        f"'{memory_dir.resolve()}' -> '{sentinel}'. The harness link was not "
        "published; "
        "the empty harness "
        "directory was removed, no memory data was parked, and the harness "
        "path is absent; clear the link publication error and rerun init"
    ]
    assert "session unaffected" not in result.stderr
    assert not harness_memory.exists()
    assert not harness_memory.is_symlink()
    assert not backup.exists()
    assert not backup.is_symlink()
    assert sentinel.is_file() and not sentinel.is_symlink()
    assert sentinel.read_bytes() == b"foreign staging name\n"


def test_the_project_slug_replaces_every_non_alphanumeric_character(tmp_path):
    # The harness keys `~/.claude/projects/` by the project's own path with
    # every non-alphanumeric character replaced by '-' -- '_' and '.' included,
    # not just '/'. Getting this wrong plants the symlink in a directory the
    # harness never reads, which fails silently: `init` reports success and the
    # memory simply never shows up.
    project_dir = tmp_path / "data_tools.v2" / "import_jobs"
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
    expected = config_dir / "projects" / _slug(project_dir) / "memory"
    assert "_" not in expected.parent.name and "." not in expected.parent.name
    assert expected.is_symlink()
    assert expected.resolve() == memory_dir.resolve()
    # Nothing is planted under the naive '/'-only slug.
    naive = config_dir / "projects" / str(project_dir).replace("/", "-")
    assert not naive.exists()


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
