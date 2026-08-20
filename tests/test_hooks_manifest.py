"""Sanity check for the plugin's `hooks/hooks.json`.

The wrapper shape checked here (`{"hooks": {"<Event>": [{"hooks": [...]}]}}`)
is the plugin-specific hooks format, verified against several currently
installed Claude Code plugins (ralph-loop, security-guidance, superpowers,
codex, explanatory-output-style) and against the official `plugin-dev`
skill's hook-development reference -- not a guess.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_hooks_json_is_valid_and_registers_session_start():
    manifest_path = REPO_ROOT / "hooks" / "hooks.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    session_start = manifest["hooks"]["SessionStart"]
    assert isinstance(session_start, list) and session_start

    commands = [
        hook["command"]
        for entry in session_start
        for hook in entry["hooks"]
        if hook.get("type") == "command"
    ]
    assert any("restore-memory-symlink.sh" in command for command in commands)


def test_the_referenced_hook_script_exists_and_is_a_shell_script():
    script_path = REPO_ROOT / "hooks" / "restore-memory-symlink.sh"
    assert script_path.is_file()
    assert script_path.read_text(encoding="utf-8").startswith("#!/bin/bash")


def test_session_start_also_refreshes_the_views():
    manifest = json.loads(
        (REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    commands = [
        hook["command"]
        for entry in manifest["hooks"]["SessionStart"]
        for hook in entry["hooks"]
        if hook.get("type") == "command"
    ]
    assert any("refresh-views.sh" in command for command in commands)


def test_the_views_hook_exists_and_is_a_shell_script():
    script_path = REPO_ROOT / "hooks" / "refresh-views.sh"
    assert script_path.is_file()
    assert script_path.read_text(encoding="utf-8").startswith("#!/bin/bash")


def test_every_registered_command_points_at_a_file_that_exists_under_hooks():
    # Catches a typo in either hooks.json entry: each command embeds a path
    # under hooks/ (e.g. "hooks/refresh-views.sh"), and that path must
    # resolve to a real file relative to the repo root.
    manifest_path = REPO_ROOT / "hooks" / "hooks.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    commands = [
        hook["command"]
        for entry in manifest["hooks"]["SessionStart"]
        for hook in entry["hooks"]
        if hook.get("type") == "command"
    ]
    assert commands

    for command in commands:
        match = re.search(r"hooks/[\w.-]+", command)
        assert match, f"no hooks/<file> path found in command: {command!r}"
        referenced = REPO_ROOT / match.group(0)
        assert referenced.is_file(), f"{referenced} does not exist"
