"""Structural checks over the reusable GitHub Action and the repo's own CI.

Like the skills checks, these read shipped content as text -- there is no
YAML parser in the standard library, and a structural pin on the literal
lines is exactly what protects the contract here: which refs are trusted,
and how the CLI is invoked.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_FILE = REPO_ROOT / "action.yml"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# A third-party action is trusted by commit, never by a movable ref (ADR
# 0005); the tag stays alongside as a comment for the human reader.
PINNED_USES_PATTERN = re.compile(r"uses:\s*[\w./-]+@[0-9a-f]{40}\s+#\s*v\S+")
USES_PATTERN = re.compile(r"^\s*-?\s*uses:", re.MULTILINE)


def _uses_lines(path):
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if USES_PATTERN.match(line)
    ]


def test_the_action_exists_and_is_composite():
    text = ACTION_FILE.read_text(encoding="utf-8")
    assert "using: composite" in text


def test_the_action_runs_status_from_its_own_checkout():
    # The whole distribution model: the code that gates is the code at the
    # action ref, imported via PYTHONPATH -- no install step, no skew.
    text = ACTION_FILE.read_text(encoding="utf-8")
    assert 'PYTHONPATH="$GITHUB_ACTION_PATH" python3 -m validated_memory status' in text


def test_the_action_never_interpolates_inputs_into_the_script():
    # `${{ }}` inside `run:` is a shell-injection surface; inputs reach the
    # script through the environment only.
    text = ACTION_FILE.read_text(encoding="utf-8")
    run_blocks = re.findall(r"run:\s*\|([\s\S]*?)(?=\n\S|\Z)", text)
    assert run_blocks, "action.yml has no run block"
    for block in run_blocks:
        assert "${{" not in block


def test_every_third_party_action_is_sha_pinned():
    files = [ACTION_FILE] + sorted(WORKFLOWS_DIR.glob("*.yml"))
    assert len(files) >= 2, "expected action.yml and at least one workflow"
    for path in files:
        for line in _uses_lines(path):
            assert PINNED_USES_PATTERN.search(line), (
                f"{path}: '{line}' is not pinned to a full commit SHA "
                "with its version tag in a trailing comment"
            )


def test_the_readme_documents_the_action_and_sha_pinning_first():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "uses: everywan-dev/validated-memory@v1" in text
    sha_mention = text.find("full commit SHA")
    assert sha_mention != -1, "README does not document SHA pinning"
