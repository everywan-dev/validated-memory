"""End-to-end tests for `lint` against the agent-memory layer.

Fixtures are synthetic adopter trees: a `memory/` directory holding one
Markdown file per fact, each with frontmatter in the Claude Code harness
shape, plus a one-line-per-entry index at `memory/MEMORY.md`.
"""

HEALTHY_MEMORY = """\
name: coffee-preference
description: Prefers oat milk in coffee.
metadata:
  type: user
"""

HEALTHY_INDEX = """\
# Agent memory

- [Coffee preference](coffee-preference.md) — oat milk in coffee
"""


def test_healthy_memory_passes_clean(adopter_dir, write_memory, write_index, run_cli):
    write_memory("coffee-preference.md", HEALTHY_MEMORY)
    write_index(HEALTHY_INDEX)

    result = run_cli("lint", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "ERROR" not in result.stderr
    assert "WARNING" not in result.stderr
    assert "1 memory file(s) checked" in result.stdout


# --- index/file sync, in both directions --------------------------------------


def test_index_entry_without_a_matching_file_gates(adopter_dir, write_index, run_cli):
    write_index("- [Missing](missing.md) — a fact that was removed\n")

    result = run_cli("lint", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: memory/MEMORY.md: entry: " in result.stderr
    assert "missing.md" in result.stderr


def test_a_memory_file_without_an_index_entry_gates(
    adopter_dir, write_memory, write_index, run_cli
):
    write_memory("coffee-preference.md", HEALTHY_MEMORY)
    write_index("# Agent memory\n")  # no entries at all

    result = run_cli("lint", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: memory/coffee-preference.md: index: " in result.stderr
    assert "MEMORY.md" in result.stderr
