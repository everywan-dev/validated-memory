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
# 0005). The `uses:` value and its trailing comment are validated
# separately, not with one combined regex: strip the comment off first,
# then require the value to *end* in a full SHA (the trailing `$` refuses
# anything dangling after it, pinned-looking or not), and require the
# comment to carry the human-readable tag on its own.
PIN_SHA_PATTERN = re.compile(r"@[0-9a-f]{40}$")
PIN_COMMENT_PATTERN = re.compile(r"^v\S+$")
USES_PATTERN = re.compile(r"^\s*-?\s*uses:", re.MULTILINE)


def _uses_lines(path):
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if USES_PATTERN.match(line)
    ]


def _value_and_comment(line):
    value, _, comment = line.partition("#")
    return value.strip(), comment.strip()


def test_the_action_exists_and_is_composite():
    text = ACTION_FILE.read_text(encoding="utf-8")
    assert "using: composite" in text


def test_the_action_runs_status_from_its_own_checkout():
    # The whole distribution model: the code that gates is the code at the
    # action ref, imported via PYTHONPATH -- no install step, no skew. `-P`
    # closes the module-shadowing hole ADR 0006 records: the checkout being
    # gated is exactly where a hostile validated_memory/ package would sit.
    text = ACTION_FILE.read_text(encoding="utf-8")
    assert "set -f" in text
    assert "ARGS: ${{ inputs.args }}" in text
    assert (
        'PYTHONPATH="$GITHUB_ACTION_PATH" python3 -P -m validated_memory status $ARGS'
        in text
    )


def test_the_action_never_interpolates_inputs_outside_declared_spots():
    # `${{ }}` inside `run:` is a shell-injection surface; inputs reach the
    # script through the environment only. Scanning only `run: |` blocks
    # (the previous shape of this test) misses an inline `run: ...` or a
    # folded scalar `run: >` -- neither matches that block pattern at all,
    # so a `${{ }}` hidden there would sail through unnoticed. Scanning the
    # whole file for every `${{` occurrence and naming the exact two that
    # are allowed closes that hole outright. YAML comment lines (the `#`
    # explaining this very rule mentions the literal syntax `${{ }}`) are
    # prose, not a YAML value, so they are excluded from the scan.
    text = ACTION_FILE.read_text(encoding="utf-8")
    interpolations = {
        line.strip()
        for line in text.splitlines()
        if "${{" in line and not line.strip().startswith("#")
    }
    assert interpolations == {
        "python-version: ${{ inputs.python-version }}",
        "ARGS: ${{ inputs.args }}",
    }


def test_every_third_party_action_is_sha_pinned():
    files = (
        [ACTION_FILE]
        + sorted(WORKFLOWS_DIR.glob("*.yml"))
        + sorted(WORKFLOWS_DIR.glob("*.yaml"))
    )
    assert len(files) >= 2, "expected action.yml and at least one workflow"
    for path in files:
        for line in _uses_lines(path):
            value, comment = _value_and_comment(line)
            assert PIN_SHA_PATTERN.search(value), (
                f"{path}: '{line}' is not pinned to a full commit SHA"
            )
            assert PIN_COMMENT_PATTERN.match(comment), (
                f"{path}: '{line}' has no 'v...' version comment"
            )


def test_the_readme_documents_the_action_and_sha_pinning_first():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    sha_example = "uses: everywan-dev/validated-memory@<full commit SHA>"
    v1_example = "uses: everywan-dev/validated-memory@v1"
    sha_index = text.find(sha_example)
    v1_index = text.find(v1_example)
    assert sha_index != -1, "README does not show the SHA-pinned, placeholder-free example"
    assert v1_index != -1, "README does not show the @v1 convenience example"
    assert sha_index < v1_index, (
        "the SHA-pinned example must be presented before the @v1 convenience one"
    )
    assert "full commit SHA" in text, "README does not document SHA pinning"


def test_gitlab_runs_full_suite_as_an_unprivileged_checkout_owner():
    """Structural pin: root bypasses permission failures and skips DAC coverage."""
    text = (REPO_ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    commands = [
        line.removeprefix("    - ")
        for line in text.splitlines()
        if line.startswith("    - ")
    ]
    assert commands == [
        "apt-get update -qq && apt-get install -y -qq git passwd util-linux",
        'pip install --quiet ".[dev]"',
        "useradd --create-home --user-group vm-test",
        'test "${CI_PROJECT_DIR:?}" != /',
        'chown -R vm-test:vm-test "$CI_PROJECT_DIR"',
        "runuser -u vm-test -- python -m pytest -q -rs",
    ]
