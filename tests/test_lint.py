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


# --- frontmatter completeness --------------------------------------------------

INVALID_FRONTMATTER = [
    (
        "name_missing",
        "description: Prefers oat milk in coffee.\nmetadata:\n  type: user\n",
        "name",
    ),
    (
        "description_missing",
        "name: coffee-preference\nmetadata:\n  type: user\n",
        "description",
    ),
    (
        "type_missing",
        "name: coffee-preference\ndescription: Prefers oat milk.\nmetadata: {}\n",
        "metadata.type",
    ),
    (
        "metadata_missing",
        "name: coffee-preference\ndescription: Prefers oat milk.\n",
        "metadata.type",
    ),
    (
        "type_out_of_domain",
        (
            "name: coffee-preference\ndescription: Prefers oat milk.\n"
            "metadata:\n  type: opinion\n"
        ),
        "metadata.type",
    ),
]


def test_every_incomplete_frontmatter_gates_naming_file_and_field(
    adopter_dir, write_memory, write_index, run_cli
):
    entries = []
    for name, frontmatter, _field in INVALID_FRONTMATTER:
        write_memory(f"{name}.md", frontmatter)
        entries.append(f"- [{name}]({name}.md) — fixture entry")
    write_index("\n".join(entries) + "\n")

    result = run_cli("lint", cwd=adopter_dir)

    assert result.returncode == 1
    for name, _frontmatter, field in INVALID_FRONTMATTER:
        expected = f"ERROR: memory/{name}.md: {field}: "
        assert expected in result.stderr, (
            f"{name}: missing finding for field '{field}'\n{result.stderr}"
        )


def test_malformed_frontmatter_gates_naming_the_line(
    adopter_dir, write_memory, write_index, run_cli
):
    write_memory("broken.md", "name: coffee-preference\ndescription: |\n  block\n")
    write_index("- [Broken](broken.md) — fixture entry\n")

    result = run_cli("lint", cwd=adopter_dir)

    assert result.returncode == 1
    assert "frontmatter" in result.stderr
    # A parse error is the only finding for a file: nothing is validated on a
    # best-effort basis once the frontmatter itself could not be parsed.
    assert "name" not in result.stderr.split("frontmatter")[0]
    assert "memory/broken.md:3:" in result.stderr


# --- duplicate name --------------------------------------------------------


def test_duplicate_name_gates_naming_both_files(
    adopter_dir, write_memory, write_index, run_cli
):
    write_memory("first.md", HEALTHY_MEMORY)
    write_memory("second.md", HEALTHY_MEMORY)
    write_index(
        "- [First](first.md) — fixture entry\n- [Second](second.md) — fixture entry\n"
    )

    result = run_cli("lint", cwd=adopter_dir)

    assert result.returncode == 1
    assert "duplicate name 'coffee-preference'" in result.stderr
    assert "memory/first.md" in result.stderr
    assert "memory/second.md" in result.stderr


# --- wikilinks -------------------------------------------------------------


def test_a_broken_wikilink_in_description_warns_without_gating(
    adopter_dir, write_memory, write_index, run_cli
):
    write_memory(
        "coffee-preference.md",
        "name: coffee-preference\n"
        "description: See also [[tea-preference]].\n"
        "metadata:\n  type: user\n",
    )
    write_index(HEALTHY_INDEX)

    result = run_cli("lint", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "WARNING: memory/coffee-preference.md: description: " in result.stderr
    assert "tea-preference" in result.stderr
    assert "1 warning(s)" in result.stdout


def test_a_broken_wikilink_in_the_body_warns_without_gating(
    adopter_dir, write_memory, write_index, run_cli
):
    write_memory(
        "coffee-preference.md", HEALTHY_MEMORY, body="Related: [[tea-preference]].\n"
    )
    write_index(HEALTHY_INDEX)

    result = run_cli("lint", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "WARNING: memory/coffee-preference.md: body: " in result.stderr
    assert "tea-preference" in result.stderr


def test_a_wikilink_resolving_to_a_real_memory_is_not_a_finding(
    adopter_dir, write_memory, write_index, run_cli
):
    write_memory(
        "coffee-preference.md",
        HEALTHY_MEMORY,
        body="Related: [[tea-preference]].\n",
    )
    write_memory(
        "tea-preference.md",
        "name: tea-preference\ndescription: Prefers green tea.\nmetadata:\n  type: user\n",
    )
    write_index(
        HEALTHY_INDEX + "- [Tea preference](tea-preference.md) — green tea\n"
    )

    result = run_cli("lint", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "WARNING" not in result.stderr


# --- supersession convention ------------------------------------------------


def test_a_well_formed_supersession_is_recognized_without_findings(
    adopter_dir, write_memory, write_index, run_cli
):
    write_memory(
        "old.md",
        "name: old-coffee-preference\n"
        "description: superseded by [[coffee-preference]]\n"
        "metadata:\n  type: user\n",
    )
    write_memory("new.md", HEALTHY_MEMORY)
    write_index("- [Old](old.md) — fixture entry\n- [New](new.md) — fixture entry\n")

    result = run_cli("lint", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "ERROR" not in result.stderr
    assert "WARNING" not in result.stderr


def test_a_malformed_supersession_marker_gates(
    adopter_dir, write_memory, write_index, run_cli
):
    write_memory(
        "old.md",
        "name: old-coffee-preference\n"
        "description: superseded by coffee-preference\n"
        "metadata:\n  type: user\n",
    )
    write_index("- [Old](old.md) — fixture entry\n")

    result = run_cli("lint", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: memory/old.md: description: " in result.stderr
    assert "supersession" in result.stderr


def test_a_supersession_pointing_at_a_missing_memory_gates(
    adopter_dir, write_memory, write_index, run_cli
):
    write_memory(
        "old.md",
        "name: old-coffee-preference\n"
        "description: superseded by [[coffee-preference]]\n"
        "metadata:\n  type: user\n",
    )
    write_index("- [Old](old.md) — fixture entry\n")

    result = run_cli("lint", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: memory/old.md: description: " in result.stderr
    assert "coffee-preference" in result.stderr
    assert "WARNING" not in result.stderr


def test_a_supersession_pointing_at_itself_gates(
    adopter_dir, write_memory, write_index, run_cli
):
    write_memory(
        "old.md",
        "name: old-coffee-preference\n"
        "description: superseded by [[old-coffee-preference]]\n"
        "metadata:\n  type: user\n",
    )
    write_index("- [Old](old.md) — fixture entry\n")

    result = run_cli("lint", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: memory/old.md: description: " in result.stderr
    assert "itself" in result.stderr
