"""The adoption decision: what the adopter repository versions.

`adopt-validated-memory` asks, before `init`, whether the layout is versioned
in the adopter repository or kept local to the clone. The "local" answers
write an ignore list, and that list is pinned here against what the CLI
really creates at the adopter's root -- every item `init` (with and without
`--view`), `derive` and `probe` write, and nothing else -- so a new root
artifact cannot appear without the skill and the adoption guide learning to
ignore it, and a stale entry cannot linger after one is retired.

The list is read from the skill and the guide as data (the same seam as
`test_skills_structure.py`); the artifacts come from driving the CLI as a
subprocess, never from the package.
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ADOPT_SKILL = REPO_ROOT / "skills" / "adopt-validated-memory" / "SKILL.md"
ADOPTION_GUIDE = REPO_ROOT / "docs" / "adoption.md"

# The ignore list is the one fenced block opening with this comment line; the
# entries are its remaining non-blank, non-comment lines.
IGNORE_LIST_MARKER = "# validated-memory layout, local to this clone"
FENCED_BLOCK_PATTERN = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

CURRENT_PROBE = """\
import sys, json
sys.stdin.read()
print(json.dumps({"verdict": "current"}))
"""

ANCHORED_UNIT = """\
---
id: kb-0001
evidence: measured
anchors:
  - system: repo-a
    kind: git_ref
    captured_at: 2026-08-01T00:00:00Z
    payload: {}
---

Unit body.
"""


def _ignore_entries(path):
    text = path.read_text(encoding="utf-8")
    blocks = [
        block
        for block in FENCED_BLOCK_PATTERN.findall(text)
        if block.lstrip().startswith(IGNORE_LIST_MARKER)
    ]
    assert len(blocks) == 1, (
        f"{path} must carry exactly one fenced block opening with "
        f"{IGNORE_LIST_MARKER!r}; found {len(blocks)}"
    )
    return {
        line.strip()
        for line in blocks[0].splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def _root_artifacts(adopter_dir, tmp_path_factory, run_cli):
    """Everything the CLI writes at the adopter's root, as ignore entries."""
    for args in (("init",), ("init", "--view")):
        result = run_cli(*args, cwd=adopter_dir)
        assert result.returncode == 0, result.stderr

    # A fake probe, kept outside the adopter tree so the tree holds only what
    # the CLI itself wrote; registered in place of the bundled git_ref probe.
    helper = tmp_path_factory.mktemp("probe-helper") / "current_probe.py"
    helper.write_text(CURRENT_PROBE, encoding="utf-8")
    (adopter_dir / "validated-memory.md").write_text(
        f"---\nprobes:\n  git_ref: {sys.executable} {helper.as_posix()}\n---\n",
        encoding="utf-8",
    )
    (adopter_dir / "knowledge" / "kb-0001.md").write_text(
        ANCHORED_UNIT, encoding="utf-8"
    )
    for command in ("probe", "derive"):
        result = run_cli(command, cwd=adopter_dir)
        assert result.returncode == 0, result.stderr

    return {
        f"/{entry.name}/" if entry.is_dir() else f"/{entry.name}"
        for entry in adopter_dir.iterdir()
    }


def test_the_skill_ignore_list_is_exactly_what_the_cli_creates_at_the_root(
    adopter_dir, tmp_path_factory, run_cli
):
    assert _ignore_entries(ADOPT_SKILL) == _root_artifacts(
        adopter_dir, tmp_path_factory, run_cli
    )


def test_the_adoption_guide_carries_the_same_ignore_list_as_the_skill():
    assert _ignore_entries(ADOPTION_GUIDE) == _ignore_entries(ADOPT_SKILL)


def test_the_skill_asks_the_versioning_question_before_init():
    # The decision is useless after `init --harness-memory` has already
    # absorbed the harness's memory into a repository that versions it, so
    # the question must precede the bootstrap command in the skill's text.
    text = ADOPT_SKILL.read_text(encoding="utf-8")
    decide = text.index("## Decide what this repository versions")
    bootstrap = text.index("## Bootstrap the layout")
    assert decide < bootstrap
    # The two ways of keeping the layout local, and the git limit that rules
    # out a per-remote answer, are all named -- an adopter who wants the data
    # on one host and not another must learn here that a repository cannot.
    for needle in (".gitignore", ".git/info/exclude", "per remote"):
        assert needle in text, f"adopt skill does not mention {needle!r}"
