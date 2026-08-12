"""End-to-end tests for `derive`: the re-derived knowledge index.

Fixtures are synthetic adopter trees: a `knowledge/` directory holding curated
knowledge units, each a Markdown file whose frontmatter carries the contract.
`derive` writes `knowledge-index.md` in the working directory (never inside
`knowledge/`, since anything ending in `.md` there is read as a unit).
"""

import re

INDEX_FILENAME = "knowledge-index.md"

ACTIVE_UNIT = """\
id: kb-0001
evidence: measured
anchors: []
"""


def test_derive_writes_the_index_for_a_single_unit(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", ACTIVE_UNIT)

    result = run_cli("derive", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "derive: 1 unit(s) indexed" in result.stdout

    index = (adopter_dir / INDEX_FILENAME).read_text(encoding="utf-8")
    assert index.startswith("# Knowledge index\n")
    assert "Derived: " in index
    assert "Basis: 1 unit(s) under knowledge/" in index
    assert "| kb-0001 | active | measured | unknown |" in index


def test_many_to_one_supersession_marks_the_superseded_unit(
    adopter_dir, write_unit, run_cli
):
    # kb-0001 is superseded by both kb-0002 and kb-0003 (many-to-one): the
    # effective state names every superseding id, sorted, and the superseded
    # unit is still listed, never omitted.
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\nanchors: []\n")
    write_unit(
        "kb-0003.md",
        "id: kb-0003\nevidence: hypothesis\nsupersedes:\n  - kb-0001\nanchors: []\n",
    )
    write_unit(
        "kb-0002.md",
        "id: kb-0002\nevidence: verifiable\nsupersedes:\n  - kb-0001\nanchors: []\n",
    )

    result = run_cli("derive", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    index = (adopter_dir / INDEX_FILENAME).read_text(encoding="utf-8")
    assert "Basis: 3 unit(s) under knowledge/" in index
    assert "| kb-0001 | superseded by kb-0002, kb-0003 | measured | unknown |" in index
    assert "| kb-0002 | active | verifiable | unknown |" in index
    assert "| kb-0003 | active | hypothesis | unknown |" in index


# --- validation gates derive ---------------------------------------------


def test_a_validation_error_gates_and_writes_nothing(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", "id: kb-0001\nevidence: probable\nanchors: []\n")

    result = run_cli("derive", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: knowledge/kb-0001.md: evidence: " in result.stderr
    assert not (adopter_dir / INDEX_FILENAME).exists()


def test_a_validation_warning_does_not_gate_derive(adopter_dir, write_unit, run_cli):
    # No anchors is a WARNING, not an ERROR; derive still writes the index.
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n")

    result = run_cli("derive", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "WARNING: knowledge/kb-0001.md: anchors: " in result.stderr
    assert (adopter_dir / INDEX_FILENAME).exists()


# --- --check ---------------------------------------------------------------


def test_check_without_a_prior_derive_gates_pointing_at_derive(
    adopter_dir, write_unit, run_cli
):
    write_unit("kb-0001.md", ACTIVE_UNIT)

    result = run_cli("derive", "--check", cwd=adopter_dir)

    assert result.returncode == 1
    assert INDEX_FILENAME in result.stderr
    assert "derive" in result.stderr
    assert not (adopter_dir / INDEX_FILENAME).exists()


def test_check_passes_right_after_derive(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", ACTIVE_UNIT)
    run_cli("derive", cwd=adopter_dir)

    result = run_cli("derive", "--check", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "derive --check: index is up to date" in result.stdout


def test_check_ignores_only_the_derived_timestamp(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", ACTIVE_UNIT)
    run_cli("derive", cwd=adopter_dir)
    index_path = adopter_dir / INDEX_FILENAME
    lines = index_path.read_text(encoding="utf-8").split("\n")
    lines = [
        "Derived: 2000-01-01T00:00:00Z" if line.startswith("Derived: ") else line
        for line in lines
    ]
    index_path.write_text("\n".join(lines), encoding="utf-8")

    result = run_cli("derive", "--check", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr


def test_a_hand_edited_index_fails_check_with_error(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", ACTIVE_UNIT)
    run_cli("derive", cwd=adopter_dir)
    index_path = adopter_dir / INDEX_FILENAME
    mutated = index_path.read_text(encoding="utf-8").replace(
        "| kb-0001 | active | measured | unknown |",
        "| kb-0001 | active | hypothesis | unknown |",
    )
    index_path.write_text(mutated, encoding="utf-8")

    result = run_cli("derive", "--check", cwd=adopter_dir)

    assert result.returncode == 1
    assert f"ERROR: {INDEX_FILENAME}: index: " in result.stderr
    # --check never writes, even when it fails.
    assert index_path.read_text(encoding="utf-8") == mutated


def test_deleting_the_derived_line_fails_check(adopter_dir, write_unit, run_cli):
    # The timestamp's value is ignored, but the line itself is content: a
    # hand-deleted `Derived:` line is a mutation and must fail the check.
    write_unit("kb-0001.md", ACTIVE_UNIT)
    run_cli("derive", cwd=adopter_dir)
    index_path = adopter_dir / INDEX_FILENAME
    lines = index_path.read_text(encoding="utf-8").split("\n")
    lines = [line for line in lines if not line.startswith("Derived: ")]
    index_path.write_text("\n".join(lines), encoding="utf-8")

    result = run_cli("derive", "--check", cwd=adopter_dir)

    assert result.returncode == 1
    assert f"ERROR: {INDEX_FILENAME}: index: " in result.stderr
    assert "Derived" in result.stderr


def test_a_mismatch_names_the_line_as_numbered_on_disk(
    adopter_dir, write_unit, run_cli
):
    # The row sits on line 8 of the file (title, blank, Derived, Basis,
    # blank, header, separator, row); the message must use that numbering.
    write_unit("kb-0001.md", ACTIVE_UNIT)
    run_cli("derive", cwd=adopter_dir)
    index_path = adopter_dir / INDEX_FILENAME
    mutated = index_path.read_text(encoding="utf-8").replace(
        "| kb-0001 | active | measured | unknown |",
        "| kb-0001 | active | hypothesis | unknown |",
    )
    index_path.write_text(mutated, encoding="utf-8")

    result = run_cli("derive", "--check", cwd=adopter_dir)

    assert result.returncode == 1
    assert "at line 8" in result.stderr


def test_an_extra_trailing_line_fails_check_naming_the_line(
    adopter_dir, write_unit, run_cli
):
    write_unit("kb-0001.md", ACTIVE_UNIT)
    run_cli("derive", cwd=adopter_dir)
    index_path = adopter_dir / INDEX_FILENAME
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write("\n")

    result = run_cli("derive", "--check", cwd=adopter_dir)

    assert result.returncode == 1
    assert "at line 9" in result.stderr
    assert "end of file" in result.stderr
    assert "['']" not in result.stderr


def test_check_does_not_rewrite_the_index_on_success(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", ACTIVE_UNIT)
    run_cli("derive", cwd=adopter_dir)
    index_path = adopter_dir / INDEX_FILENAME
    before = index_path.read_text(encoding="utf-8")

    result = run_cli("derive", "--check", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert index_path.read_text(encoding="utf-8") == before


def test_check_with_a_validation_error_gates_without_reading_the_index(
    adopter_dir, write_unit, run_cli
):
    write_unit("kb-0001.md", ACTIVE_UNIT)
    run_cli("derive", cwd=adopter_dir)
    write_unit("kb-0002.md", "id: kb-0002\nevidence: probable\nanchors: []\n")

    result = run_cli("derive", "--check", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: knowledge/kb-0002.md: evidence: " in result.stderr


# --- PATH resolution mirrors `validate` -------------------------------------


def test_a_missing_default_directory_gates_and_points_at_init(adopter_dir, run_cli):
    result = run_cli("derive", cwd=adopter_dir)

    assert result.returncode == 1
    assert "knowledge" in result.stderr
    assert "init" in result.stderr
    assert not (adopter_dir / INDEX_FILENAME).exists()


def test_a_missing_explicit_path_gates(adopter_dir, run_cli):
    result = run_cli("derive", "nowhere", cwd=adopter_dir)

    assert result.returncode == 1
    assert "nowhere" in result.stderr


def test_an_explicit_path_overrides_the_default_directory(
    adopter_dir, write_unit, run_cli
):
    write_unit("kb-0001.md", ACTIVE_UNIT)
    (adopter_dir / "knowledge").rename(adopter_dir / "facts")

    result = run_cli("derive", "facts", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    index = (adopter_dir / INDEX_FILENAME).read_text(encoding="utf-8")
    assert "Basis: 1 unit(s) under facts/" in index


def test_a_single_file_can_be_the_derivation_source(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", ACTIVE_UNIT)
    write_unit("kb-0002.md", "id: kb-0002\nevidence: probable\nanchors: []\n")

    result = run_cli("derive", "knowledge/kb-0001.md", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    index = (adopter_dir / INDEX_FILENAME).read_text(encoding="utf-8")
    assert "Basis: 1 unit(s) under knowledge/kb-0001.md" in index
    assert "| kb-0001 | active | measured | unknown |" in index


def test_derive_never_mutates_the_source_units(adopter_dir, write_unit, run_cli):
    path = write_unit("kb-0001.md", ACTIVE_UNIT)
    before = path.read_text(encoding="utf-8")

    result = run_cli("derive", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert path.read_text(encoding="utf-8") == before


# --- the declared extension applies to derive too ---------------------------


def test_a_declared_extension_violation_gates_derive(
    adopter_dir, write_document, write_unit, run_cli
):
    write_document(
        "validated-memory.md",
        "extension:\n  schema: knowledge-extension.md\n  version: \"1\"\n",
    )
    write_document(
        "knowledge-extension.md",
        "fields:\n  - name: domain\n    type: enum\n    values:\n      - network\n",
    )
    write_unit(
        "kb-0001.md",
        "id: kb-0001\nevidence: measured\nanchors: []\ndomain: telepathy\n",
    )

    result = run_cli("derive", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR: knowledge/kb-0001.md: domain: " in result.stderr
    assert not (adopter_dir / INDEX_FILENAME).exists()


# --- derivation date and basis format ---------------------------------------


def test_derived_declares_an_iso8601_utc_timestamp(adopter_dir, write_unit, run_cli):
    write_unit("kb-0001.md", ACTIVE_UNIT)

    result = run_cli("derive", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    index = (adopter_dir / INDEX_FILENAME).read_text(encoding="utf-8")
    match = re.search(r"^Derived: (.+)$", index, re.MULTILINE)
    assert match, index
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", match.group(1))


def test_an_empty_knowledge_directory_warns_and_derives_an_empty_index(
    adopter_dir, run_cli
):
    (adopter_dir / "knowledge").mkdir()

    result = run_cli("derive", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr
    index = (adopter_dir / INDEX_FILENAME).read_text(encoding="utf-8")
    assert "Basis: 0 unit(s) under knowledge/" in index
