"""The adoption decision: what the adopter repository versions (ADR 0007).

`adopt-validated-memory` asks, before `init`, whether the layout is versioned
in the adopter repository or kept local to the clone. The "local" answers
write an ignore list, and that list is pinned here against the CLI's fixed
root outputs -- every item `init` (with and without `--view`), `derive` and
`probe` write at the adopter's root on a normal run, and nothing else -- so
a new root artifact cannot appear without the skill and the adoption guide
learning to ignore it, and a stale entry cannot linger after one is retired.
Not covered, on purpose: the `--harness-memory` side effects, which live at
PATH rather than in the project, and the temporary files `render` leaves
only after a hard kill.

The list is read from the skill and the guide as data (the same seam as
`test_skills_structure.py`); the artifacts come from driving the CLI as a
subprocess, never from the package.
"""

import shlex
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ADOPT_SKILL = REPO_ROOT / "skills" / "adopt-validated-memory" / "SKILL.md"
ADOPTION_GUIDE = REPO_ROOT / "docs" / "adoption.md"

# The ignore list is the one fenced block whose first line is this comment;
# the entries are its remaining non-blank, non-comment lines.
IGNORE_LIST_MARKER = "# validated-memory layout, local to this clone"

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


def _fenced_blocks(text):
    """Yield the body lines of every fenced code block, read line by line.

    A fence opens on a line that, stripped, starts with ``` (an info string
    may follow) and closes on a line that, stripped, is exactly ```; an
    inline triple backtick or a fence that never closes is not a block.
    """
    body = None
    for line in text.splitlines():
        stripped = line.strip()
        if body is None:
            if stripped.startswith("```"):
                body = []
        elif stripped == "```":
            yield body
            body = None
        else:
            body.append(stripped)


def _ignore_entries(path):
    text = path.read_text(encoding="utf-8")
    blocks = [
        block
        for block in _fenced_blocks(text)
        if block and block[0] == IGNORE_LIST_MARKER
    ]
    assert len(blocks) == 1, (
        f"{path} must carry exactly one fenced block opening with "
        f"{IGNORE_LIST_MARKER!r}; found {len(blocks)}"
    )
    return {line for line in blocks[0] if line and not line.startswith("#")}


def _root_artifacts(adopter_dir, tmp_path_factory, run_cli):
    """Everything the CLI writes at the adopter's root, as ignore entries."""
    for args in (("init",), ("init", "--view")):
        result = run_cli(*args, cwd=adopter_dir)
        assert result.returncode == 0, result.stderr

    # A fake probe, kept outside the adopter tree so the tree holds only what
    # the CLI itself wrote; registered in place of the bundled git_ref probe.
    helper = tmp_path_factory.mktemp("probe-helper") / "current_probe.py"
    helper.write_text(CURRENT_PROBE, encoding="utf-8")
    # Quoted as `probe` splits it (shlex), so an interpreter path with a
    # space survives.
    command = shlex.join([sys.executable, helper.as_posix()])
    (adopter_dir / "validated-memory.md").write_text(
        f"---\nprobes:\n  git_ref: {command}\n---\n",
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
    first_init = text.index("python3 -P -m validated_memory init")
    assert decide < bootstrap < first_init
    # Within the decision section: the two ways of keeping the layout local,
    # the deadline (the hook's own `init --harness-memory`), and the git
    # limit that rules out a per-remote answer -- an adopter who wants the
    # data on one host and not another must learn here that a commit cannot.
    section = text[decide:bootstrap]
    for needle in (
        ".gitignore",
        ".git/info/exclude",
        "git rev-parse --git-path info/exclude",
        "init --harness-memory",
        "per remote",
    ):
        assert needle in section, f"decision section does not mention {needle!r}"


# --- the import phase (spec section 1) ----------------------------------------

# Needles are matched against the skill with whitespace normalized to single
# spaces, so a needle can quote a whole sentence without depending on where
# the paragraph wraps.


def _normalized_skill():
    return " ".join(ADOPT_SKILL.read_text(encoding="utf-8").split())


def test_the_skill_imports_after_init_and_before_verify():
    # `init` has to have run (the layout must exist to import into), and the
    # answers have to be recorded before Verify reports on them.
    text = ADOPT_SKILL.read_text(encoding="utf-8")
    bootstrap = text.index("## Bootstrap the layout")
    first_init = text.index("python3 -P -m validated_memory init")
    import_phase = text.index("## Import existing knowledge")
    verify = text.index("## Verify the adoption")
    assert bootstrap < first_init < import_phase < verify


Q1_HEADING = "**Q1 -- Sources.**"
Q2_HEADING = "**Q2 -- Scan the declared sources.**"
Q1_DENIAL = (
    "Collect the answer **as text and nothing more**: nothing is resolved, "
    "opened or looked up at this point."
)
# Every sentence that describes resolving or opening a declared path. All of
# them belong under Q2, after the user has been shown what a path resolves
# to; none may appear under Q1.
RESOLUTION_SENTENCES = (
    "the realpath it resolves to (symlinks followed)",
    "**Refuse** a path that resolves to the filesystem root, to the user's "
    "home directory, to the harness's configuration directory, or to an "
    "ancestor of the repository root",
    "A path that resolves inside the repository root",
)


def test_q1_collects_text_only_and_every_resolution_happens_under_q2():
    # Needles alone are not enough here. Adding "Resolve and open every named
    # path immediately" to Q1 leaves every needle in this file green while
    # inverting the one rule this phase exists to enforce, so the assertion
    # is positional: Q1 carries the denial and nothing else about resolving
    # or opening, and every resolution sentence sits after the Q2 heading.
    text = _normalized_skill()
    q1_at = text.index(Q1_HEADING)
    q2_at = text.index(Q2_HEADING)
    assert q1_at < q2_at, "Q2 is asked before Q1"
    assert Q1_DENIAL in text, "Q1 no longer says the answer is collected as text only"
    assert q1_at < text.index(Q1_DENIAL) < q2_at, "the denial left Q1"

    for needle in RESOLUTION_SENTENCES:
        assert needle in text, f"the import phase no longer says: {needle!r}"
        assert text.index(needle) > q2_at, (
            f"{needle!r} appears before the Q2 heading: nothing is resolved "
            "or opened until the user has seen and consented at Q2"
        )

    # Q1's own text says nothing about resolving or opening, beyond the one
    # sentence that forbids both.
    q1_section = text[q1_at:q2_at].replace(Q1_DENIAL, "")
    for word in ("resolve", "open"):
        assert word not in q1_section.lower(), (
            f"Q1 mentions {word!r} outside the sentence that forbids it"
        )

    # The question itself, and the fixed notice Q2 carries with it.
    for needle in (
        "Does this project already have a knowledge system or a source of "
        "truth we should import?",
        "Scan these sources now? Nothing is written until you confirm the "
        "report.",
        "the whole repository is scanned as well",
    ):
        assert needle in text, f"the import phase no longer says: {needle!r}"


def test_the_skill_says_why_those_resolutions_are_refused():
    text = _normalized_skill()
    for needle in (
        "they are not sources, they are everything",
        "which is what the harness-memory symlink resolves to after the first "
        "session, since it points into `memory/`",
        "keeps the exact scope declared; it is not widened to the repository",
    ):
        assert needle in text, f"the import phase no longer says: {needle!r}"


def test_the_skill_names_both_engine_modes_the_subagent_and_the_rendezvous():
    text = _normalized_skill()
    assert "`bootstrap-from-repo` in mode `declared+repo`" in text
    assert "`bootstrap-from-repo` in mode `repo`" in text
    assert "read-only subagent" in text
    assert "run the scan inline after the last question" in text
    # The "No" branch leaves a record rather than nothing at all.
    assert "`declared, not scanned`" in text
    # Q2 = Yes leaves no Q3, so the questionnaire must be told where to go
    # next -- and where the two threads meet again.
    assert "there is no Q3 left to ask" in text
    assert "there is a single rendezvous" in text
    assert "The instruction-file step never waits for the scan" in text


def test_verify_lists_the_sources_that_are_still_pending():
    text = ADOPT_SKILL.read_text(encoding="utf-8")
    verify = text.index("## Verify the adoption")
    next_steps = text.index("## Next steps")
    section = " ".join(text[verify:next_steps].split())
    assert "`memory/source-*.md`" in section
    assert "`declared, not scanned`" in section
    assert "`not located`" in section
    assert "declared and consented to again" in section
