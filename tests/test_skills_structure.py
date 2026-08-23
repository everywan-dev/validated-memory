"""Structural checks over `skills/` and `docs/`: data, not package internals.

These tests read the plugin's Markdown surface directly -- skill files and
docs are content the plugin ships, not implementation the enforcement CLI
owns, so reading them here does not cross the "tests never import the
package's internals" seam (see CLAUDE.md and the README's "Development"
section): nothing here imports `validated_memory`.

Two things are pinned:

- AC1 (structural): every skill has a non-empty `name` and `description` in
  its frontmatter, and every literal `python3 -m validated_memory ...`
  command line found in skills or docs names a real CLI subcommand -- so a
  skill or doc can never silently drift onto a subcommand that does not
  exist.
- AC4 (clean-room): neither skills nor docs mention the internal projects
  this method was studied on while writing this plugin.
"""

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"
DOCS_DIR = REPO_ROOT / "docs"

# The CLI's real subcommands (see validated_memory/cli.py's SUBCOMMANDS).
REAL_SUBCOMMANDS = {"init", "lint", "validate", "derive", "probe", "render", "status"}

FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
# Only a real, bare subcommand word right after the module invocation, on the
# same line, counts: a usage placeholder like `<command>` starts with '<' and
# never matches, and `[ \t]+` (not `\s+`) keeps a probe module invocation like
# `python3 -m validated_memory.probes.git_ref`, followed by nothing else on
# its line, from spilling over into the next line's leading word. `-P` is
# optional here (unlike in CLI_INVOCATION_PATTERN below): this pattern's job
# is only naming a real subcommand, not gating the -P form itself.
COMMAND_PATTERN = re.compile(
    r"python3(?: -P)? -m validated_memory(?:\.\w+)*[ \t]+([a-zA-Z][\w-]*)"
)

FORBIDDEN_MENTIONS = ("everyWAN", "everywan", "odoo")


def _skill_dirs():
    return sorted(path for path in SKILLS_DIR.iterdir() if path.is_dir())


def _skill_files():
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def _doc_files():
    # Recursive: `docs/adr/` already exists and more subdirectories will
    # follow, and a doc that escapes these checks by sitting one level down
    # is exactly the one nobody notices drifting.
    return sorted(DOCS_DIR.rglob("*.md"))


def _all_prose_files():
    return _skill_files() + _doc_files() + [REPO_ROOT / "README.md"]


# --- AC1: every skill declares when it fires ---------------------------------


def test_every_skill_directory_has_a_skill_md():
    dirs = _skill_dirs()
    assert dirs, "expected at least one skill under skills/"
    for directory in dirs:
        assert (directory / "SKILL.md").is_file(), f"{directory} has no SKILL.md"


def test_the_skill_set_is_exactly_the_documented_one():
    # The README and the adoption guide state this set; adding or removing
    # a skill updates them and this pin in the same change.
    names = {path.parent.name for path in _skill_files()}
    assert names == {
        "adopt-validated-memory",
        "create-knowledge-unit",
        "supersede-knowledge",
        "probe-freshness",
        "maintain-agent-memory",
        "ask-validated-memory",
        "bootstrap-from-repo",
    }


def test_every_skill_has_a_non_empty_name_and_description():
    for path in _skill_files():
        text = path.read_text(encoding="utf-8")
        match = FRONTMATTER_PATTERN.match(text)
        assert match, f"{path} has no '---' frontmatter block"
        frontmatter = match.group(1)

        name_match = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
        assert name_match, f"{path} frontmatter has no 'name'"
        assert name_match.group(1).strip(), f"{path} has an empty 'name'"

        description_match = re.search(
            r"^description:\s*(.+)$", frontmatter, re.MULTILINE
        )
        assert description_match, f"{path} frontmatter has no 'description'"
        assert description_match.group(1).strip(), f"{path} has an empty 'description'"


def test_skill_directory_name_matches_its_declared_name():
    for path in _skill_files():
        text = path.read_text(encoding="utf-8")
        match = FRONTMATTER_PATTERN.match(text)
        name_match = re.search(r"^name:\s*(.+)$", match.group(1), re.MULTILINE)
        assert name_match.group(1).strip() == path.parent.name


def test_every_documented_command_names_a_real_subcommand():
    for path in _all_prose_files():
        text = path.read_text(encoding="utf-8")
        for match in COMMAND_PATTERN.finditer(text):
            command = match.group(1)
            assert command in REAL_SUBCOMMANDS, (
                f"{path} references 'python3 -m validated_memory {command}', "
                f"which is not one of the CLI's subcommands {sorted(REAL_SUBCOMMANDS)}"
            )


# A CLI invocation proper: the module, not a dotted submodule like
# `validated_memory.probes.git_ref` -- probe registrations are executed
# without a shell, where an env-assignment prefix would be invalid. `-P` is
# required, not optional, here: it is the module-shadowing fix (ADR 0006),
# so a documented invocation without it must fail this gate, not merely
# fail to be recognized as one.
CLI_INVOCATION_PATTERN = re.compile(r"python3 -P -m validated_memory(?![.\w])")

# The same idiom the hooks use: pin the plugin root while preserving any
# PYTHONPATH the session already carries (an adopter's own probe modules may
# only be importable through it).
PYTHONPATH_PREFIX = 'PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}" '


def test_every_skill_command_sets_pythonpath_to_the_plugin_root():
    # Skills execute in the adopter's project, where the package is not
    # importable -- only the plugin checkout has it. The hooks already export
    # PYTHONPATH before invoking the CLI; a bare `python3 -P -m
    # validated_memory` in a skill fails with ModuleNotFoundError in every
    # real session. Checked per invocation, not per line, so a chained
    # second command (`... && python3 -P -m validated_memory lint`) cannot
    # slip through bare.
    for path in _skill_files():
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for match in CLI_INVOCATION_PATTERN.finditer(line):
                assert line[: match.start()].endswith(PYTHONPATH_PREFIX), (
                    f"{path}:{number} invokes the CLI without pinning "
                    f"PYTHONPATH: expected {PYTHONPATH_PREFIX!r} immediately "
                    "before the invocation"
                )


def test_at_least_one_real_command_is_documented_per_skill():
    # Every skill's job is to point at the CLI surface, not reimplement it:
    # each one carries at least one literal CLI invocation. Most name a
    # subcommand; `ask-validated-memory` points at `--help` and
    # `--version`, which is still the CLI and not a reimplementation --
    # subcommand validity is the previous test's job.
    for path in _skill_files():
        text = path.read_text(encoding="utf-8")
        assert CLI_INVOCATION_PATTERN.search(text), (
            f"{path} carries no literal CLI invocation"
        )


# --- docs/ exist and cover the required topics --------------------------------


def test_adoption_and_walkthrough_docs_exist():
    assert (DOCS_DIR / "adoption.md").is_file()
    assert (DOCS_DIR / "walkthrough.md").is_file()


# --- AC4: clean-room -----------------------------------------------------------

# The repository's public home. Mentioning the org that hosts the repo is
# not an internal reference, so it is struck from the text before both scans;
# the bare company name anywhere else still fails.
PUBLIC_ORG = "everywan-dev"



def test_skills_and_docs_are_clean_room():
    for path in _skill_files() + _doc_files():
        text = path.read_text(encoding="utf-8")
        lowered = text.lower().replace(PUBLIC_ORG, "")
        for forbidden in FORBIDDEN_MENTIONS:
            assert forbidden.lower() not in lowered, (
                f"{path} mentions '{forbidden}', which is not allowed in this "
                "self-contained, clean-room repo"
            )


CLEAN_ROOM_SUFFIXES = {".py", ".sh", ".md", ".json", ".toml", ".yml", ".yaml", ".svg"}
# `.claude` holds harness-managed state, including agent worktrees under
# `.claude/worktrees/` -- full checkouts of this repo whose files are not
# part of this checkout's surface.
CLEAN_ROOM_SKIPPED_DIRS = {".git", ".claude", "build", "__pycache__", ".pytest_cache"}


def _publishable_files():
    """Every file a commit could carry: tracked, plus untracked and not ignored.

    The clean-room surface is what can leave this checkout, so git-ignored
    files are out of it by definition -- this repository adopts its own
    plugin with the layout kept local (`/memory/` and friends in
    `.gitignore`), and the agent memory living there is the adopter's, not
    the plugin's. Without a usable git (a tarball, a checkout with no
    `.git`), fall back to walking the tree: the scan over-reads rather than
    under-reads.
    """
    def git(*args):
        return subprocess.run(
            ["git", *args], capture_output=True, cwd=REPO_ROOT, check=True
        ).stdout

    try:
        # A checkout without `.git` that sits inside some other repository
        # would get that repository's index and ignore rules: only a git
        # whose top level is this checkout answers for it.
        toplevel = Path(os.fsdecode(git("rev-parse", "--show-toplevel").strip()))
        if toplevel.resolve() != REPO_ROOT.resolve():
            raise OSError("not this repository's top level")
        listing = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    except (OSError, subprocess.CalledProcessError):
        return sorted(REPO_ROOT.rglob("*"))
    return sorted(
        REPO_ROOT / os.fsdecode(entry) for entry in listing.split(b"\0") if entry
    )


def test_the_whole_repository_is_clean_room():
    # The narrower skills/docs scan above predates this one and stays as the
    # fast gate; this exists because the one leak that happened came in
    # through a file outside `skills/` -- an example path in the README, the
    # hook and a test fixture.
    this_test = Path(__file__).resolve()
    scanned = 0
    for path in _publishable_files():
        if path.suffix not in CLEAN_ROOM_SUFFIXES or not path.is_file():
            continue
        parts = path.relative_to(REPO_ROOT).parts
        if any(
            part in CLEAN_ROOM_SKIPPED_DIRS or part.endswith(".egg-info")
            for part in parts
        ):
            continue
        if path.resolve() == this_test:
            continue
        lowered = path.read_text(encoding="utf-8").lower()
        lowered = lowered.replace(PUBLIC_ORG, "")
        for forbidden in FORBIDDEN_MENTIONS:
            assert forbidden.lower() not in lowered, (
                f"{path} mentions '{forbidden}', which is not allowed in this "
                "self-contained, clean-room repo"
            )
        scanned += 1
    assert scanned > 20, "the walk found too few files to be the real repo"
