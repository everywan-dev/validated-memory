"""End-to-end tests for `init`: the adopter scaffold.

`init` creates the minimal layout for both layers -- curated knowledge and
agent memory -- plus the adopter's configuration and a valid declared
extension stub. Every item is created only if missing (idempotent, never
overwrites), and the two-layer enforcement (`validate`, `lint`) must pass
clean right after a run on an empty project.
"""

import os


# --- the full scaffold, from an empty directory -------------------------------


def test_init_creates_the_full_scaffold(adopter_dir, run_cli):
    result = run_cli("init", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert (adopter_dir / "knowledge").is_dir()
    assert (adopter_dir / "memory").is_dir()
    assert (adopter_dir / "memory" / "MEMORY.md").is_file()
    assert (adopter_dir / "validated-memory.md").is_file()
    assert (adopter_dir / "knowledge-extension.md").is_file()


def test_init_reports_every_item_created(adopter_dir, run_cli):
    result = run_cli("init", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    for path in (
        "knowledge",
        "memory",
        "memory/MEMORY.md",
        "validated-memory.md",
        "knowledge-extension.md",
    ):
        assert f"init: created {path}" in result.stdout, result.stdout


def test_init_memory_index_has_no_entries(adopter_dir, run_cli):
    run_cli("init", cwd=adopter_dir)

    index = (adopter_dir / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    # The lint convention only counts bullet lines shaped `- [Title](file.md)`;
    # a fresh index carries none.
    assert not any(line.strip().startswith("- [") for line in index.splitlines())


def test_init_config_declares_schema_version_id_prefix_and_probes(
    adopter_dir, run_cli
):
    run_cli("init", cwd=adopter_dir)

    config = (adopter_dir / "validated-memory.md").read_text(encoding="utf-8")
    assert "extension:" in config
    assert "schema: knowledge-extension.md" in config
    assert 'version: "1"' in config
    assert "id_prefix: kb-" in config
    assert "probes:" in config
    assert "git_ref: python3 -m validated_memory.probes.git_ref" in config


def test_init_extension_stub_declares_no_fields(adopter_dir, run_cli):
    run_cli("init", cwd=adopter_dir)

    schema = (adopter_dir / "knowledge-extension.md").read_text(encoding="utf-8")
    assert "fields: []" in schema


def test_init_extension_stub_documents_the_field_format_and_versioning_rule(
    adopter_dir, run_cli
):
    run_cli("init", cwd=adopter_dir)

    schema = (adopter_dir / "knowledge-extension.md").read_text(encoding="utf-8")
    for word in ("name", "type", "values", "string", "enum"):
        assert word in schema
    assert "do not bump" in schema
    assert "supersede" in schema


# --- the enforcement it bootstraps must accept it right away ------------------


def test_after_init_validate_and_lint_pass_clean(adopter_dir, run_cli):
    run_cli("init", cwd=adopter_dir)

    validated = run_cli("validate", cwd=adopter_dir)
    linted = run_cli("lint", cwd=adopter_dir)

    assert validated.returncode == 0, validated.stderr
    assert "ERROR" not in validated.stderr
    assert linted.returncode == 0, linted.stderr
    assert "ERROR" not in linted.stderr
    assert "WARNING" not in linted.stderr


# --- idempotency: kept, never overwritten --------------------------------------


def test_re_running_init_keeps_every_item_and_says_so(adopter_dir, run_cli):
    run_cli("init", cwd=adopter_dir)

    result = run_cli("init", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    for path in (
        "knowledge",
        "memory",
        "memory/MEMORY.md",
        "validated-memory.md",
        "knowledge-extension.md",
    ):
        assert f"init: kept {path}" in result.stdout, result.stdout
        assert f"init: created {path}" not in result.stdout


def test_re_running_init_does_not_overwrite_existing_memory_data(
    adopter_dir, write_memory, run_cli
):
    run_cli("init", cwd=adopter_dir)
    custom_index = (
        "# Agent memory\n\n- [Coffee preference](coffee-preference.md) — oat milk\n"
    )
    write_memory(
        "coffee-preference.md",
        "name: coffee-preference\ndescription: Prefers oat milk.\n"
        "metadata:\n  type: user\n",
    )
    (adopter_dir / "memory" / "MEMORY.md").write_text(custom_index, encoding="utf-8")

    run_cli("init", cwd=adopter_dir)

    assert (adopter_dir / "memory" / "MEMORY.md").read_text(
        encoding="utf-8"
    ) == custom_index
    assert (adopter_dir / "memory" / "coffee-preference.md").exists()


def test_re_running_init_does_not_overwrite_a_hand_edited_config(
    adopter_dir, run_cli
):
    run_cli("init", cwd=adopter_dir)
    custom_config = (adopter_dir / "validated-memory.md").read_text(encoding="utf-8")
    edited = custom_config.replace("id_prefix: kb-", "id_prefix: adopter-")
    (adopter_dir / "validated-memory.md").write_text(edited, encoding="utf-8")

    run_cli("init", cwd=adopter_dir)

    assert (adopter_dir / "validated-memory.md").read_text(
        encoding="utf-8"
    ) == edited


# --- failure to create is an ERROR ---------------------------------------------


def test_a_directory_that_cannot_be_created_gates_with_an_error(adopter_dir, run_cli):
    locked = adopter_dir / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)  # read + execute, no write: children can't be created
    try:
        result = run_cli("init", cwd=locked)

        assert result.returncode == 1
        assert "ERROR" in result.stderr
        assert "knowledge" in result.stderr
    finally:
        os.chmod(locked, 0o700)
