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

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"
DOCS_DIR = REPO_ROOT / "docs"

# The CLI's real subcommands (see validated_memory/cli.py's SUBCOMMANDS).
REAL_SUBCOMMANDS = {"init", "lint", "validate", "derive", "probe", "render"}

FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
# Only a real, bare subcommand word right after the module invocation, on the
# same line, counts: a usage placeholder like `<command>` starts with '<' and
# never matches, and `[ \t]+` (not `\s+`) keeps a probe module invocation like
# `python3 -m validated_memory.probes.git_ref`, followed by nothing else on
# its line, from spilling over into the next line's leading word.
COMMAND_PATTERN = re.compile(r"python3 -m validated_memory(?:\.\w+)*[ \t]+([a-zA-Z][\w-]*)")

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


def test_five_skills_are_present():
    # Pinned by the ticket: adopt, create, supersede, probe, maintain.
    names = {path.parent.name for path in _skill_files()}
    assert names == {
        "adopt-validated-memory",
        "create-knowledge-unit",
        "supersede-knowledge",
        "probe-freshness",
        "maintain-agent-memory",
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


def test_at_least_one_real_command_is_documented_per_skill():
    # Every skill's job is to point at the CLI surface, not reimplement it:
    # each one names at least one literal, real subcommand invocation.
    for path in _skill_files():
        text = path.read_text(encoding="utf-8")
        commands = {match.group(1) for match in COMMAND_PATTERN.finditer(text)}
        assert commands & REAL_SUBCOMMANDS, f"{path} names no real CLI subcommand"


# --- docs/ exist and cover the required topics --------------------------------


def test_adoption_and_walkthrough_docs_exist():
    assert (DOCS_DIR / "adoption.md").is_file()
    assert (DOCS_DIR / "walkthrough.md").is_file()


# --- AC4: clean-room -----------------------------------------------------------


def test_skills_and_docs_are_clean_room():
    for path in _skill_files() + _doc_files():
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for forbidden in FORBIDDEN_MENTIONS:
            assert forbidden.lower() not in lowered, (
                f"{path} mentions '{forbidden}', which is not allowed in this "
                "self-contained, clean-room repo"
            )
