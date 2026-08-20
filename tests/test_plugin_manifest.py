"""Sanity checks for the plugin manifest, the marketplace listing and the version.

The repository is its own marketplace: `.claude-plugin/marketplace.json` lists
the plugin that `.claude-plugin/plugin.json` defines, with `source: "./"`.
That is what makes the plugin installable by anyone rather than only by
someone who already has the directory on disk.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _manifest():
    manifest_path = REPO_ROOT / ".claude-plugin" / "plugin.json"
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _marketplace():
    path = REPO_ROOT / ".claude-plugin" / "marketplace.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_manifest_is_valid_json_with_identity():
    manifest = _manifest()
    assert manifest["name"] == "validated-memory"
    assert manifest["version"]
    assert manifest["description"]


def test_the_version_agrees_across_the_three_places_it_is_written():
    # Read as text rather than imported: these tests never import the
    # package's internals (see CLAUDE.md).
    package = (REPO_ROOT / "validated_memory" / "__init__.py").read_text(
        encoding="utf-8"
    )
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    package_version = re.search(
        r'^__version__ = "([^"]+)"', package, re.MULTILINE
    ).group(1)
    pyproject_version = re.search(
        r'^version = "([^"]+)"', pyproject, re.MULTILINE
    ).group(1)

    assert package_version == pyproject_version == _manifest()["version"]


def test_the_marketplace_lists_this_plugin_from_this_repository():
    marketplace = _marketplace()
    # The README documents `/plugin install validated-memory@validated-memory`;
    # the part after `@` is this name, so renaming the marketplace would break
    # the documented install command.
    assert marketplace["name"] == "validated-memory"
    assert marketplace["owner"]["name"]

    listed = marketplace["plugins"]
    assert len(listed) == 1, "the repository publishes exactly one plugin"
    entry = listed[0]
    # A listing that names a plugin the repository does not define installs
    # nothing, and says nothing about why: the two names have to agree.
    assert entry["name"] == _manifest()["name"]
    assert entry["source"] == "./", "the plugin is this repository, not another"
    assert entry["description"]
