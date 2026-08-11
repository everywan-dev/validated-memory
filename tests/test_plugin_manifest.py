"""Sanity check for the Claude Code plugin manifest."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_plugin_manifest_is_valid_json_with_identity():
    manifest_path = REPO_ROOT / ".claude-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["name"] == "validated-memory"
    assert manifest["version"]
    assert manifest["description"]
