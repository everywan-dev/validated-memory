"""End-to-end tests for `init`: the adopter scaffold.

`init` creates the minimal layout for both layers -- curated knowledge and
agent memory -- plus the adopter's configuration and a valid declared
extension stub. Every item is created only if missing (idempotent, never
overwrites), and the two-layer enforcement (`validate`, `lint`) must pass
clean right after a run on an empty project.
"""

import os
import re

import pytest

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
    # An empty knowledge/ still reports its usual "no units" WARNING, which
    # does not gate.
    assert "WARNING" in validated.stderr
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


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
def test_a_directory_that_cannot_be_created_gates_with_an_error(adopter_dir, run_cli):
    locked = adopter_dir / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)  # read + execute, no write: children can't be created
    try:
        result = run_cli("init", cwd=locked)

        assert result.returncode == 1
        assert "ERROR" in result.stderr
        # A locked root can't create `.validated-memory/` for the lock or
        # `journal.jsonl` for the bootstrap record either, so that fails
        # before any scaffold item (e.g. "knowledge") is even attempted.
        assert "journal" in result.stderr, result.stderr
    finally:
        os.chmod(locked, 0o700)


def test_a_directory_blocked_by_a_dangling_symlink_gates_with_an_error(
    adopter_dir, run_cli
):
    """`_ensure_dir` reports a directory it could not create, and gates.

    The root stays writable, so the journal and its lock are created
    normally and the failure happens where this test aims it. A dangling
    symlink is the way in: `exists()` follows the link and answers False, so
    `_ensure_dir` tries to create the directory, and `mkdir` then refuses
    because the link itself is in the way.
    """
    (adopter_dir / "knowledge").symlink_to("nowhere-at-all")

    result = run_cli("init", cwd=adopter_dir)

    assert result.returncode == 1, result.stdout
    assert "knowledge" in result.stderr, result.stderr
    assert "directory could not be created" in result.stderr, result.stderr


def test_a_file_blocked_by_a_broken_symlink_gates_and_leaves_the_link(
    adopter_dir, run_cli
):
    """`init` never destroys something the adopter put there, link included.

    `Path.exists()` follows a symlink and reads a broken one as absent, so
    the scaffold walked straight into it: routing the write through
    `os.replace` (which does not follow) turned what used to be an ERROR
    into a silent success that destroyed the link -- and recorded it as a
    `create` with a null preimage, whose inverse is "remove", so a reversal
    would delete the file and restore nothing. `_ensure_views` has guarded
    this exact shape all along; the scaffold now guards it the same way.
    """
    import json

    (adopter_dir / "validated-memory.md").symlink_to("/nonexistent/elsewhere.md")

    result = run_cli("init", cwd=adopter_dir)

    assert result.returncode == 1, result.stdout
    assert "broken symlink" in result.stderr, result.stderr
    assert (adopter_dir / "validated-memory.md").is_symlink()
    assert not (adopter_dir / "validated-memory.md").exists()
    records = [
        json.loads(line)
        for line in (adopter_dir / "journal.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert not [
        entry for entry in records if entry["path"] == "validated-memory.md"
    ], records


def test_an_item_blocked_by_a_file_gates_with_an_error(adopter_dir, run_cli):
    # A regular file where the scaffold needs a directory: creating
    # memory/MEMORY.md fails for every user, root included -- unlike
    # permission bits, which root ignores.
    (adopter_dir / "memory").write_text("not a directory\n", encoding="utf-8")

    result = run_cli("init", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR" in result.stderr


# --- --harness-memory: the move-proof symlink ----------------------------------


def test_harness_memory_creates_a_symlink_when_missing(adopter_dir, tmp_path, run_cli):
    harness_memory = tmp_path / "harness" / "memory"

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
    )

    assert result.returncode == 0, result.stderr
    assert harness_memory.is_symlink()
    assert harness_memory.resolve() == (adopter_dir / "memory").resolve()
    assert "created symlink" in result.stdout


def test_harness_memory_symlink_sees_the_project_memory_files(
    adopter_dir, tmp_path, write_memory, write_index, run_cli
):
    harness_memory = tmp_path / "harness" / "memory"
    write_memory(
        "coffee-preference.md",
        "name: coffee-preference\ndescription: Prefers oat milk.\n"
        "metadata:\n  type: user\n",
    )
    write_index("- [Coffee preference](coffee-preference.md) — oat milk\n")

    run_cli("init", "--harness-memory", str(harness_memory), cwd=adopter_dir)

    assert (harness_memory / "coffee-preference.md").read_text(encoding="utf-8") == (
        adopter_dir / "memory" / "coffee-preference.md"
    ).read_text(encoding="utf-8")


def test_harness_memory_is_idempotent_when_already_correct(
    adopter_dir, tmp_path, run_cli
):
    harness_memory = tmp_path / "harness" / "memory"
    run_cli("init", "--harness-memory", str(harness_memory), cwd=adopter_dir)

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
    )

    assert result.returncode == 0, result.stderr
    assert "kept symlink" in result.stdout
    assert harness_memory.is_symlink()
    assert harness_memory.resolve() == (adopter_dir / "memory").resolve()


def test_harness_memory_repoints_a_symlink_pointing_elsewhere(
    adopter_dir, tmp_path, run_cli
):
    harness_memory = tmp_path / "harness" / "memory"
    other = tmp_path / "elsewhere"
    other.mkdir()
    harness_memory.parent.mkdir(parents=True)
    harness_memory.symlink_to(other, target_is_directory=True)

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
    )

    assert result.returncode == 0, result.stderr
    assert "re-pointed symlink" in result.stdout
    assert harness_memory.resolve() == (adopter_dir / "memory").resolve()


def test_harness_memory_repoints_a_broken_symlink(adopter_dir, tmp_path, run_cli):
    harness_memory = tmp_path / "harness" / "memory"
    gone = tmp_path / "gone"
    harness_memory.parent.mkdir(parents=True)
    harness_memory.symlink_to(gone, target_is_directory=True)

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
    )

    assert result.returncode == 0, result.stderr
    assert "re-pointed symlink" in result.stdout
    assert harness_memory.resolve() == (adopter_dir / "memory").resolve()


def test_harness_memory_move_or_clone_restores_the_symlink_without_data_loss(
    tmp_path, write_document, run_cli
):
    # Simulate a rename/clone of the adopter project: init once from the
    # original location, move the whole project directory, then init again
    # from the new location. The symlink must end up pointing at the new
    # project's memory/, and every memory file written along the way must
    # still be there afterwards -- init only ever re-points, never deletes.
    project_a = tmp_path / "project-a"
    project_a.mkdir()
    harness_memory = tmp_path / "harness" / "memory"

    run_cli("init", "--harness-memory", str(harness_memory), cwd=project_a)
    (project_a / "memory" / "coffee-preference.md").write_text(
        "---\nname: coffee-preference\ndescription: Prefers oat milk.\n"
        "metadata:\n  type: user\n---\n\nBody.\n",
        encoding="utf-8",
    )
    (project_a / "memory" / "MEMORY.md").write_text(
        "# Agent memory\n\n"
        "- [Coffee preference](coffee-preference.md) — oat milk\n",
        encoding="utf-8",
    )

    project_b = tmp_path / "project-b"
    project_a.rename(project_b)

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=project_b
    )

    assert result.returncode == 0, result.stderr
    assert "re-pointed symlink" in result.stdout
    assert harness_memory.resolve() == (project_b / "memory").resolve()
    assert (harness_memory / "coffee-preference.md").is_file()
    assert "oat milk" in (harness_memory / "MEMORY.md").read_text(encoding="utf-8")


def test_harness_memory_existing_real_directory_warns_and_is_left_untouched(
    adopter_dir, tmp_path, run_cli
):
    harness_memory = tmp_path / "harness" / "memory"
    harness_memory.mkdir(parents=True)
    marker = harness_memory / "pre-existing.md"
    marker.write_text("Do not touch.\n", encoding="utf-8")

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
    )

    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr
    assert str(harness_memory) in result.stderr
    assert not harness_memory.is_symlink()
    assert marker.read_text(encoding="utf-8") == "Do not touch.\n"


def test_harness_memory_existing_real_file_warns_and_is_left_untouched(
    adopter_dir, tmp_path, run_cli
):
    harness_memory = tmp_path / "harness-memory-file"
    harness_memory.write_text("Do not touch.\n", encoding="utf-8")

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
    )

    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr
    assert not harness_memory.is_symlink()
    assert harness_memory.read_text(encoding="utf-8") == "Do not touch.\n"


def test_a_repointed_symlink_records_its_previous_target_before_losing_it(
    adopter_dir, tmp_path, run_cli
):
    """The record comes first, because the mutation destroys what it records.

    A `link` record's payload is the previous target, and its inverse is
    "restore the previous target". Re-pointing the symlink is what makes
    that target unreadable, so a record written afterwards has a window in
    which the only copy of it is in memory -- and the same window leaves a
    re-pointed link no record mentions at all.
    """
    import json

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    harness_memory = tmp_path / "harness" / "memory"
    harness_memory.parent.mkdir(parents=True)
    harness_memory.symlink_to(elsewhere, target_is_directory=True)

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
    )

    assert result.returncode == 0, result.stderr
    assert harness_memory.resolve() == (adopter_dir / "memory").resolve()
    vault = adopter_dir / ".validated-memory" / "local.jsonl"
    links = [
        json.loads(line)
        for line in vault.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["op"] == "link"
    ]
    assert [entry["stage"] for entry in links] == ["prepared", "committed"], links
    for entry in links:
        assert entry["note"] == f"previous target: {elsewhere}", entry


def test_a_corrupt_journal_still_restores_the_harness_symlink(
    adopter_dir, tmp_path, run_cli
):
    """A journal failure must never cost the user their memory symlink.

    `journal.jsonl` is always versioned and append-only, so two branches
    that both ran `init` conflict in it on every merge, and a botched
    resolution leaves exactly this file. The corrupt journal still gates
    (ADR 0008: required history that cannot be read is exit 1), but the
    harness half of `init` is what the `SessionStart` hook exists for and it
    runs regardless -- with the loss of its own record reported, not hidden.
    """
    harness_memory = tmp_path / "harness" / "memory"
    assert (
        run_cli(
            "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
        ).returncode
        == 0
    )
    harness_memory.unlink()
    journal = adopter_dir / "journal.jsonl"
    journal.write_text(
        journal.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8"
    )

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
    )

    assert result.returncode == 1, result.stdout
    assert "not valid JSON" in result.stderr, result.stderr
    assert harness_memory.is_symlink(), "the symlink was not restored"
    assert harness_memory.resolve() == (adopter_dir / "memory").resolve()
    assert "could not be recorded" in result.stderr, result.stderr


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
def test_an_unwritable_root_still_restores_the_harness_symlink(
    adopter_dir, tmp_path, run_cli
):
    """The journal cannot be opened at all, and the symlink is still restored.

    An adopter root that cannot be written to fails before any scaffold item
    -- the lock and the journal's own bootstrap both need to create files in
    it. That is an ERROR and it gates; it is not a reason to leave the
    harness pointing nowhere.
    """
    locked = adopter_dir / "locked"
    locked.mkdir()
    harness_memory = tmp_path / "harness" / "memory"
    os.chmod(locked, 0o500)
    try:
        result = run_cli(
            "init", "--harness-memory", str(harness_memory), cwd=locked
        )

        assert result.returncode == 1, result.stdout
        assert "journal" in result.stderr, result.stderr
        assert harness_memory.is_symlink(), "the symlink was not restored"
        assert harness_memory.readlink() == locked / "memory"
    finally:
        os.chmod(locked, 0o700)


def test_without_harness_memory_flag_nothing_outside_the_project_is_touched(
    adopter_dir, run_cli
):
    result = run_cli("init", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert "symlink" not in result.stdout


# --- --view: activation is the artifact, not a config key ----------------------


def test_init_view_creates_both_artifacts_once_and_keeps_them(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n", "# Title\n")

    first = run_cli("init", "--view", cwd=adopter_dir)
    stamp = (adopter_dir / "knowledge.html").read_bytes()
    (adopter_dir / "knowledge.html").write_text("edited by hand\n", encoding="utf-8")
    second = run_cli("init", "--view", cwd=adopter_dir)

    assert "created knowledge.html" in first.stdout
    assert "created memory.html" in first.stdout
    assert stamp
    assert "kept knowledge.html" in second.stdout
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") == "edited by hand\n"


def test_init_view_leaves_a_broken_symlink_alone_and_warns(run_cli, adopter_dir):
    # `Path.exists()` follows symlinks, so a broken one reads as absent --
    # but writing through it would create a file wherever it points, outside
    # `init`'s remit. It is something real that is not the artifact: warn and
    # leave it, the same posture `--harness-memory` takes with a path that is
    # anything else real.
    (adopter_dir / "elsewhere").mkdir()
    target = adopter_dir / "elsewhere" / "page.html"
    (adopter_dir / "knowledge.html").symlink_to(target)

    result = run_cli("init", "--view", cwd=adopter_dir)

    assert result.returncode == 0
    assert "Traceback" not in result.stderr
    assert "WARNING" in result.stderr
    assert "symlink" in result.stderr
    assert (adopter_dir / "knowledge.html").is_symlink()
    assert not target.exists()
    # The other artifact is unaffected by the neighbour's defect.
    assert (adopter_dir / "memory.html").exists()


def test_init_view_on_an_invalid_corpus_warns_without_gating(
    run_cli, adopter_dir, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: invented\n")

    result = run_cli("init", "--view", cwd=adopter_dir)

    assert result.returncode == 0
    assert "WARNING" in result.stderr
    assert not (adopter_dir / "knowledge.html").exists()


def test_init_view_summary_reports_the_warning_it_printed(
    run_cli, adopter_dir, write_unit
):
    # A summary line claiming "0 warning(s)" in the same run that printed a
    # WARNING to stderr would contradict itself -- this pins that the
    # WARNING `build_artifacts` reports for an invalid corpus is folded
    # into init's own tally, not left unaccounted for. It also guards
    # against the double-print regression that folding it in could
    # introduce: every stderr line must appear exactly once.
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: invented\n")

    result = run_cli("init", "--view", cwd=adopter_dir)

    assert result.returncode == 0
    assert "WARNING" in result.stderr
    match = re.search(r"(\d+) warning\(s\)", result.stdout)
    assert match, result.stdout
    assert int(match.group(1)) >= 1
    stderr_lines = [line for line in result.stderr.splitlines() if line]
    assert len(stderr_lines) == len(set(stderr_lines)), result.stderr


def test_init_view_creates_only_the_missing_artifact_and_keeps_the_other(
    run_cli, adopter_dir, write_unit
):
    # One artifact already present (e.g. from an earlier `init --view`, then
    # hand-deleted only for `memory.html`) must be kept untouched while the
    # missing one is created, in the same run.
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: measured\n", "# Title\n")
    run_cli("init", "--view", cwd=adopter_dir)
    (adopter_dir / "memory.html").unlink()
    kept_content = (adopter_dir / "knowledge.html").read_text(encoding="utf-8")

    result = run_cli("init", "--view", cwd=adopter_dir)

    assert "kept knowledge.html" in result.stdout
    assert "created memory.html" in result.stdout
    assert "created knowledge.html" not in result.stdout
    assert (adopter_dir / "knowledge.html").read_text(encoding="utf-8") == kept_content
    assert (adopter_dir / "memory.html").exists()
