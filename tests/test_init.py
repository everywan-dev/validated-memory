"""End-to-end tests for `init`: the adopter scaffold.

`init` creates the minimal layout for both layers -- curated knowledge and
agent memory -- plus the adopter's configuration and a valid declared
extension stub. Every item is created only if missing (idempotent, never
overwrites), and the two-layer enforcement (`validate`, `lint`) must pass
clean right after a run on an empty project.
"""

import os
import re
import stat

import pytest


HARNESS_OUTSIDE_ERROR = (
    "validated-memory init: error: --harness-memory must name a path outside "
    "the adopter project"
)
PROJECT_MEMORY_OUTSIDE_ERROR = (
    "project memory resolves outside the adopter, so the harness path was "
    "left untouched"
)


def _tree_snapshot(root):
    """Capture node types, file bytes and link targets without following links."""
    snapshot = {}

    def visit(path, relative):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            snapshot[relative] = ("symlink", os.readlink(path))
            return
        if stat.S_ISDIR(mode):
            snapshot[relative] = ("directory",)
            for child in sorted(path.iterdir(), key=lambda item: item.name):
                visit(child, relative / child.name)
            return
        if stat.S_ISREG(mode):
            snapshot[relative] = ("file", path.read_bytes())
            return
        snapshot[relative] = ("other", stat.S_IFMT(mode))

    visit(root, root.relative_to(root))
    return snapshot


def _assert_harness_usage_refusal(result):
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.splitlines()[-1] == HARNESS_OUTSIDE_ERROR
    assert "Traceback" not in result.stderr


def _external_harness_path(adopter_dir, name="memory"):
    return adopter_dir.parent / f"{adopter_dir.name}-harness" / name


def _link_records(adopter_dir):
    import json

    history = adopter_dir / ".validated-memory" / "local.jsonl"
    if not history.exists():
        return []
    return [
        entry
        for line in history.read_text(encoding="utf-8").splitlines()
        if (entry := json.loads(line))["op"] == "link"
    ]


def test_app_requires_view_before_any_write(adopter_dir, run_cli):
    before = sorted(adopter_dir.rglob("*"))
    result = run_cli("init", "--app", cwd=adopter_dir)
    assert result.returncode == 2
    assert "--app requires --view" in result.stderr
    assert sorted(adopter_dir.rglob("*")) == before


@pytest.mark.parametrize("value", ["-1", "-0.5", "abc", "", "nan", "inf", "-inf"])
def test_lock_wait_must_be_a_finite_number_of_seconds_of_zero_or_more(
    adopter_dir, run_cli, value
):
    before = _tree_snapshot(adopter_dir)

    result = run_cli("init", f"--lock-wait={value}", cwd=adopter_dir)

    assert result.returncode == 2
    assert result.stdout == ""
    assert (
        "--lock-wait must be a finite number of seconds, zero or more"
        in result.stderr.splitlines()[-1]
    )
    assert "Traceback" not in result.stderr
    assert _tree_snapshot(adopter_dir) == before


@pytest.mark.parametrize("value", ["0", "2.5", "1e1"])
def test_lock_wait_accepts_a_number_of_seconds_of_zero_or_more(
    adopter_dir, run_cli, value
):
    result = run_cli("init", "--lock-wait", value, cwd=adopter_dir)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert (adopter_dir / "journal.jsonl").is_file()


def test_init_help_documents_the_lock_wait(adopter_dir, run_cli):
    result = run_cli("init", "--help", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    text = " ".join(result.stdout.split())
    assert "--lock-wait SECONDS" in text
    assert "(default: 10)" in text


def test_init_help_requires_an_external_harness_memory_path(
    adopter_dir, run_cli
):
    result = run_cli("init", "--help", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert (
        "make PATH outside the adopter project a move-proof symlink to this "
        "project's memory/ directory"
    ) in " ".join(result.stdout.split())


def test_init_view_app_creates_all_three_pages(adopter_dir, run_cli):
    result = run_cli("init", "--view", "--app", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    for name in ("knowledge.html", "memory.html", "knowledge-app.html"):
        assert f"init: created {name}" in result.stdout
        assert (adopter_dir / name).read_text().startswith("<!doctype html>")


@pytest.mark.parametrize("content", ["", "Hand-edited app\n"])
def test_init_preserves_an_existing_app(adopter_dir, run_cli, content):
    app = adopter_dir / "knowledge-app.html"
    app.write_text(content)
    stamp = app.stat().st_mtime_ns
    result = run_cli("init", "--view", "--app", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    assert "init: kept knowledge-app.html" in result.stdout
    assert app.read_text() == content
    assert app.stat().st_mtime_ns == stamp


def test_init_keeps_a_broken_app_symlink(adopter_dir, run_cli):
    app = adopter_dir / "knowledge-app.html"
    app.symlink_to("missing.html")
    result = run_cli("init", "--view", "--app", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    assert "WARNING: knowledge-app.html: create:" in result.stderr
    assert app.is_symlink()
    assert app.readlink().as_posix() == "missing.html"
    assert not (adopter_dir / "missing.html").exists()


def test_init_does_not_activate_app_when_build_is_refused(
    adopter_dir, run_cli, write_unit
):
    run_cli("init", cwd=adopter_dir)
    write_unit("kb-0001.md", "id: kb-0001\nevidence: invalid\n")
    result = run_cli("init", "--view", "--app", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr
    for name in ("knowledge.html", "memory.html", "knowledge-app.html"):
        assert not (adopter_dir / name).exists()


def test_plain_init_view_does_not_create_app(adopter_dir, run_cli):
    result = run_cli("init", "--view", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    assert not (adopter_dir / "knowledge-app.html").exists()

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


@pytest.mark.parametrize("target", ("nowhere-at-all", "knowledge"))
def test_a_directory_blocked_by_an_unresolved_symlink_gates_without_a_record(
    adopter_dir, run_cli, target
):
    """A missing-target or looping link is preserved without false history.

    `Path.exists()` follows a symlink and reads a broken one as absent, so
    `_ensure_dir` wrote the `create` `prepared` record and only then found
    the link in the way of `mkdir`. The ERROR was right; the record was
    not. Nothing closed it and nothing ever could, and the reconciler reads
    the link itself as evidence the mutation happened -- `applied`, for a
    `create` whose inverse is "remove the adopter's own symlink".
    `journal.jsonl` is versioned, so each session start appended another
    one to shared history. `_ensure_file` has guarded this exact shape all
    along: nothing is recorded, because nothing happened.
    """
    import json

    (adopter_dir / "knowledge").symlink_to(target)

    for _ in range(3):
        result = run_cli("init", cwd=adopter_dir)
        assert result.returncode == 1, result.stdout

    assert "knowledge" in result.stderr, result.stderr
    assert "broken symlink" in result.stderr, result.stderr
    assert (adopter_dir / "knowledge").is_symlink()
    assert not (adopter_dir / "knowledge").exists()
    records = [
        json.loads(line)
        for line in (adopter_dir / "journal.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert not [
        entry for entry in records if entry["path"] == "knowledge"
    ], records
    check = run_cli("journal", "--check", cwd=adopter_dir)
    assert check.returncode == 0, check.stdout


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


def test_a_direct_looping_managed_file_keeps_the_broken_symlink_diagnostic(
    adopter_dir, run_cli
):
    import json

    managed = adopter_dir / "validated-memory.md"
    managed.symlink_to("validated-memory.md")

    for _ in range(2):
        result = run_cli("init", cwd=adopter_dir)

        assert result.returncode == 1
        assert "validated-memory.md: create: exists as a broken symlink" in (
            result.stderr
        )
        assert "path contains a symlink loop" not in result.stderr
        assert "Traceback" not in result.stderr
        assert managed.is_symlink()
        assert os.readlink(managed) == "validated-memory.md"

    records = [
        json.loads(line)
        for line in (adopter_dir / "journal.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert not [
        entry for entry in records if entry["path"] == "validated-memory.md"
    ]
    assert not list(
        (adopter_dir / ".validated-memory" / "transactions").glob("*.json")
    )


def test_a_directory_where_a_scaffold_file_goes_is_refused_not_kept(
    adopter_dir, run_cli
):
    """`kept` means a regular file was found, and a directory is not one.

    `Path.exists()` answers "something is there", which is a different
    question, so a directory named `validated-memory.md` was reported
    `kept` and observed as "file already present": a permanent,
    uninvertible claim that adoption found a file, written about a
    directory, after which every command that reads the configuration
    fails on the name the journal describes wrongly. It is the mirror of
    the plain file where `memory/` goes, and it now gets the same answer --
    the intention expects the name to be absent, and the refusal names what
    is really there.
    """
    import json

    (adopter_dir / "validated-memory.md").mkdir()

    result = run_cli("init", cwd=adopter_dir)

    assert result.returncode == 1, result.stdout
    assert (
        "validated-memory.md is a directory, and this create expects it to "
        "be absent. Nothing has been written." in result.stderr
    ), result.stderr
    assert "kept validated-memory.md" not in result.stdout, result.stdout
    assert (adopter_dir / "validated-memory.md").is_dir()
    records = [
        json.loads(line)
        for line in (adopter_dir / "journal.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert not [
        entry for entry in records if entry["path"] == "validated-memory.md"
    ], records
    # The refusal is about that one item: the rest of the scaffold is there.
    assert (adopter_dir / "knowledge").is_dir()
    assert (adopter_dir / "knowledge-extension.md").is_file()


def test_a_symlink_to_a_file_where_a_scaffold_file_goes_is_refused_not_kept(
    adopter_dir, run_cli
):
    """A link that resolves is still not the file `init` would have made.

    The link's own record would say `observe`, which means "adoption found
    this file here", and the bytes it names are somewhere else entirely --
    the same false claim a broken symlink earns an ERROR for, made quietly
    because this one happens to resolve. Nothing is written and nothing is
    recorded: the link is the adopter's.
    """
    (adopter_dir / "elsewhere.md").write_text("hand written\n", encoding="utf-8")
    (adopter_dir / "validated-memory.md").symlink_to("elsewhere.md")

    result = run_cli("init", cwd=adopter_dir)

    assert result.returncode == 1, result.stdout
    assert (
        "validated-memory.md is a symlink to 'elsewhere.md'" in result.stderr
    ), result.stderr
    assert (adopter_dir / "validated-memory.md").is_symlink()
    assert (
        adopter_dir / "elsewhere.md"
    ).read_text(encoding="utf-8") == "hand written\n"


def test_an_observation_that_cannot_be_recorded_gates_only_its_own_item(
    tmp_path, run_cli
):
    """One item's record refuses; the other items are still created.

    `memory/` is a symlink to a directory outside the project, so the fact
    that adoption found it there may not be filed in the versioned journal
    (`journal.authorise`,
    docs/design/2026-08-30-the-journal-coverage-and-reversal-design.md §7).
    `authorise` raises `OSError` for exactly that reason -- so a caller
    can gate the one item that named it -- and `_ensure_dir` let it
    reach `init.run`'s outer handler instead:
    the run was reported as "the journal could not be opened" against
    `journal.jsonl`, a file that is perfectly valid, and every other item
    was abandoned with it. `journal.authorise`'s own docstring says `init`
    gates per item; this is what makes that true.
    """
    outside = tmp_path / "outside" / "other"
    outside.mkdir(parents=True)
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    (adopter / "memory").symlink_to(
        os.path.join("..", "outside", "other"), target_is_directory=True
    )

    result = run_cli("init", cwd=adopter)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "ERROR: memory: journal: memory resolves outside the adopter root" in (
        result.stderr
    ), result.stderr
    assert "journal could not be opened" not in result.stderr, result.stderr
    # The items that named nothing outside the root were created anyway.
    assert (adopter / "knowledge").is_dir()
    assert (adopter / "validated-memory.md").is_file()
    assert (adopter / "knowledge-extension.md").is_file()
    assert not (outside / "MEMORY.md").exists()


@pytest.mark.parametrize(
    ("managed", "absolute_target"),
    (("memory", False), ("knowledge", True)),
)
def test_in_root_directory_symlink_is_kept_with_a_truthful_first_sight_note(
    adopter_dir, run_cli, managed, absolute_target
):
    import json

    target = adopter_dir / "containers" / f"real-{managed}"
    target.mkdir(parents=True)
    marker = target / "preserved.txt"
    marker.write_bytes(b"preserved target bytes\x00\xff")
    raw_target = (
        str(target)
        if absolute_target
        else target.relative_to(adopter_dir).as_posix()
    )
    link = adopter_dir / managed
    link.symlink_to(raw_target, target_is_directory=True)

    result = run_cli("init", cwd=adopter_dir)

    assert result.returncode == 0, result.stderr
    assert f"init: kept {managed}" in result.stdout.splitlines()
    assert f"init: created {managed}" not in result.stdout.splitlines()
    assert link.is_symlink()
    assert os.readlink(link) == raw_target
    assert marker.read_bytes() == b"preserved target bytes\x00\xff"
    records = [
        json.loads(line)
        for line in (adopter_dir / "journal.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    managed_records = [entry for entry in records if entry["path"] == managed]
    assert len(managed_records) == 1
    observation = managed_records[0]
    assert {
        key: value
        for key, value in observation.items()
        if key not in ("adoption", "at", "run", "version")
    } == {
        "durability": "repo",
        "note": (
            "directory symlink already present; resolves inside the "
            f"adopter to 'containers/real-{managed}'"
        ),
        "op": "observe",
        "path": managed,
        "purpose": "init",
        "schema": 1,
        "stage": "committed",
    }
    assert all(observation[key] for key in ("adoption", "at", "run", "version"))
    assert not list(
        (adopter_dir / ".validated-memory" / "transactions").glob("*.json")
    )

    if managed == "memory":
        assert (target / "MEMORY.md").is_file()
        child = [
            entry for entry in records if entry["path"] == "memory/MEMORY.md"
        ]
        assert [entry["op"] for entry in child] == ["create", "create"]
        assert [entry["stage"] for entry in child] == ["prepared", "committed"]

    journal_before = (adopter_dir / "journal.jsonl").read_bytes()
    link_before = os.readlink(link)
    target_before = _tree_snapshot(target)

    repeated = run_cli("init", cwd=adopter_dir)

    assert repeated.returncode == 0, repeated.stderr
    assert f"init: kept {managed}" in repeated.stdout.splitlines()
    assert f"init: created {managed}" not in repeated.stdout.splitlines()
    assert (adopter_dir / "journal.jsonl").read_bytes() == journal_before
    assert os.readlink(link) == link_before
    assert _tree_snapshot(target) == target_before


def test_existing_directory_observation_is_not_rewritten_for_a_later_symlink(
    adopter_dir, run_cli
):
    import json

    memory = adopter_dir / "memory"
    memory.mkdir()
    first = run_cli("init", cwd=adopter_dir)
    assert first.returncode == 0, first.stderr
    journal = adopter_dir / "journal.jsonl"
    before = journal.read_bytes()
    observations = [
        json.loads(line)
        for line in before.decode("utf-8").splitlines()
        if json.loads(line)["path"] == "memory"
    ]
    assert len(observations) == 1
    assert observations[0]["note"] == "directory already present"

    target = adopter_dir / "containers" / "real-memory"
    target.parent.mkdir()
    memory.rename(target)
    memory.symlink_to("containers/real-memory", target_is_directory=True)
    target_before = _tree_snapshot(target)

    repeated = run_cli("init", cwd=adopter_dir)

    assert repeated.returncode == 0, repeated.stderr
    assert "init: kept memory" in repeated.stdout
    assert journal.read_bytes() == before
    assert memory.is_symlink()
    assert os.readlink(memory) == "containers/real-memory"
    assert _tree_snapshot(target) == target_before


def test_an_item_blocked_by_a_file_gates_with_an_error(adopter_dir, run_cli):
    # A regular file where the scaffold needs a directory: creating
    # memory/MEMORY.md fails for every user, root included -- unlike
    # permission bits, which root ignores.
    (adopter_dir / "memory").write_text("not a directory\n", encoding="utf-8")

    result = run_cli("init", cwd=adopter_dir)

    assert result.returncode == 1
    assert "ERROR" in result.stderr


# --- --harness-memory: the move-proof symlink ----------------------------------


@pytest.mark.parametrize(
    "case_name",
    (
        "relative",
        "absolute",
        "root",
        "nested-missing",
        "normalized-parent",
        "memory-shaped",
        "redirected-parent",
        "redirected-parent-dotdot",
        "terminal-dot",
        "terminal-dotdot",
        "unresolvable-parent",
    ),
)
def test_in_project_harness_memory_is_a_repeatable_usage_refusal_before_writes(
    tmp_path, run_cli, case_name
):
    case_root = tmp_path / case_name
    adopter = case_root / "adopter"
    adopter.mkdir(parents=True)
    (adopter / "existing.txt").write_bytes(b"preserve exactly\x00\xff")

    if case_name == "relative":
        argument = "harness-memory"
    elif case_name == "absolute":
        argument = str(adopter / "harness-memory")
    elif case_name == "root":
        argument = str(adopter)
    elif case_name == "nested-missing":
        argument = str(adopter / "missing" / "nested" / "memory")
    elif case_name == "normalized-parent":
        other = case_root / "other"
        other.mkdir()
        argument = str(other / ".." / "adopter" / "memory")
    elif case_name == "memory-shaped":
        destination = adopter / "native-memory"
        destination.mkdir()
        (destination / "MEMORY.md").write_text(
            "# Agent memory\n\n- [Fact](fact.md) — retained\n", encoding="utf-8"
        )
        (destination / "fact.md").write_text(
            "---\nname: fact\ndescription: Retained.\nmetadata:\n"
            "  type: project\n---\n\nExact body.\n",
            encoding="utf-8",
        )
        argument = str(destination)
    elif case_name == "redirected-parent":
        redirected = adopter / "redirected"
        redirected.mkdir()
        parent_link = case_root / "apparently-external"
        parent_link.symlink_to(redirected, target_is_directory=True)
        argument = str(parent_link / "memory")
    elif case_name in (
        "redirected-parent-dotdot",
        "terminal-dot",
        "terminal-dotdot",
    ):
        redirected = adopter / "nested"
        redirected.mkdir()
        external = case_root / "external"
        external.mkdir()
        (external / "into").symlink_to(redirected, target_is_directory=True)
        if case_name == "redirected-parent-dotdot":
            argument = str(external / "into" / ".." / "harness-memory")
        elif case_name == "terminal-dot":
            argument = f"{external / 'into'}{os.sep}."
        else:
            argument = f"{external / 'into'}{os.sep}.."
    else:
        parent_link = case_root / "parent-loop"
        parent_link.symlink_to(parent_link.name, target_is_directory=True)
        argument = str(parent_link / "memory")

    before = _tree_snapshot(case_root)
    for _ in range(2):
        result = run_cli("init", "--harness-memory", argument, cwd=adopter)
        _assert_harness_usage_refusal(result)
        assert _tree_snapshot(case_root) == before


def test_in_project_harness_refusal_precedes_view_creation(
    adopter_dir, run_cli
):
    before = _tree_snapshot(adopter_dir)

    for _ in range(2):
        result = run_cli(
            "init",
            "--harness-memory",
            str(adopter_dir / "harness-memory"),
            "--view",
            "--app",
            cwd=adopter_dir,
        )
        _assert_harness_usage_refusal(result)
        assert _tree_snapshot(adopter_dir) == before


def test_app_without_view_precedes_in_project_harness_refusal(
    adopter_dir, run_cli
):
    before = _tree_snapshot(adopter_dir)

    for _ in range(2):
        result = run_cli(
            "init",
            "--harness-memory",
            str(adopter_dir / "harness-memory"),
            "--app",
            cwd=adopter_dir,
        )
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr.splitlines()[-1] == (
            "validated-memory init: error: --app requires --view"
        )
        assert "--harness-memory must name" not in result.stderr
        assert "Traceback" not in result.stderr
        assert _tree_snapshot(adopter_dir) == before


def test_harness_memory_creates_a_symlink_when_missing(adopter_dir, tmp_path, run_cli):
    harness_memory = _external_harness_path(adopter_dir)

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
    harness_memory = _external_harness_path(adopter_dir)
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
    harness_memory = _external_harness_path(adopter_dir)
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
    harness_memory = _external_harness_path(adopter_dir)
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
    harness_memory = _external_harness_path(adopter_dir)
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
    harness_memory = _external_harness_path(adopter_dir)
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
    harness_memory = _external_harness_path(adopter_dir, "memory-file")
    harness_memory.parent.mkdir(parents=True)
    harness_memory.write_text("Do not touch.\n", encoding="utf-8")

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
    )

    assert result.returncode == 0, result.stderr
    assert "WARNING" in result.stderr
    assert not harness_memory.is_symlink()
    assert harness_memory.read_text(encoding="utf-8") == "Do not touch.\n"


@pytest.mark.parametrize(
    "gate", ("healthy", "corrupt-journal", "unignored-vault")
)
@pytest.mark.parametrize("harness_state", ("missing", "stale", "native"))
def test_outside_project_memory_is_refused_before_every_harness_action(
    adopter_dir, run_cli, gate, harness_state
):
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    outside = adopter_dir.parent / f"{adopter_dir.name}-outside-memory"
    (adopter_dir / "memory").rename(outside)
    marker = outside / "outside-marker.bin"
    marker.write_bytes(b"outside project memory\x00\xff")
    project_link = adopter_dir / "memory"
    raw_project_target = os.path.relpath(outside, adopter_dir)
    project_link.symlink_to(raw_project_target, target_is_directory=True)

    harness = _external_harness_path(adopter_dir)
    if harness_state == "stale":
        harness.parent.mkdir(parents=True)
        stale = harness.parent / "stale-target"
        stale.mkdir()
        harness.symlink_to(stale, target_is_directory=True)
    elif harness_state == "native":
        harness.mkdir(parents=True)
        (harness / "harness-only.md").write_text(
            "---\nname: harness-only\ndescription: Harness bytes.\n"
            "metadata:\n  type: user\n---\n\nHarness bytes.\n",
            encoding="utf-8",
        )
        (harness / "MEMORY.md").write_text(
            "# Agent memory\n\n"
            "- [Harness only](harness-only.md) — harness bytes\n",
            encoding="utf-8",
        )

    if gate == "corrupt-journal":
        history = adopter_dir / "journal.jsonl"
        history.write_text(
            history.read_text(encoding="utf-8") + "{not json\n",
            encoding="utf-8",
        )
    elif gate == "unignored-vault":
        ignore_file = adopter_dir / ".gitignore"
        ignore_file.unlink()
        ignore_target = adopter_dir / "unignored-target"
        ignore_target.write_bytes(b"adopter-owned ignore bytes\n")
        ignore_file.symlink_to(ignore_target.name)

    adopter_before = _tree_snapshot(adopter_dir)
    outside_before = _tree_snapshot(outside)
    harness_parent_before = (
        _tree_snapshot(harness.parent) if harness.parent.exists() else None
    )
    local_history = adopter_dir / ".validated-memory" / "local.jsonl"
    local_before = local_history.read_bytes() if local_history.exists() else None
    expected_error = (
        f"ERROR: {harness}: symlink: {PROJECT_MEMORY_OUTSIDE_ERROR}"
    )

    results = []
    for _ in range(2):
        result = run_cli(
            "init", "--harness-memory", str(harness), cwd=adopter_dir
        )
        results.append((result.stdout, result.stderr))

        assert result.returncode == 1, (result.stdout, result.stderr)
        # A journal refusal reaches the harness step, which validates the
        # project target as on a healthy run (ADR 0029), so the target's ERROR
        # is reported under every gate and the refused history adds its own.
        assert result.stderr.splitlines().count(expected_error) == 1
        if gate == "corrupt-journal":
            assert result.stderr.count("not valid JSON") == 1
        assert str(outside.resolve()) not in result.stderr
        assert "Traceback" not in result.stderr
        assert "created symlink" not in result.stdout
        assert "re-pointed symlink" not in result.stdout
        assert "kept symlink" not in result.stdout
        assert "adopted" not in result.stdout
        assert "parked" not in result.stdout
        assert project_link.is_symlink()
        assert os.readlink(project_link) == raw_project_target
        assert marker.read_bytes() == b"outside project memory\x00\xff"
        assert _tree_snapshot(adopter_dir) == adopter_before
        assert _tree_snapshot(outside) == outside_before
        assert (
            _tree_snapshot(harness.parent) if harness.parent.exists() else None
        ) == harness_parent_before
        assert not list(harness.parent.glob("memory.bak*"))
        assert (
            local_history.read_bytes() if local_history.exists() else None
        ) == local_before
        assert not _link_records(adopter_dir)

    assert results[1] == results[0]


def test_b1_usage_preflight_precedes_b6_project_memory_check(
    adopter_dir, run_cli
):
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    outside = adopter_dir.parent / f"{adopter_dir.name}-outside-memory"
    (adopter_dir / "memory").rename(outside)
    marker = outside / "outside-marker.bin"
    marker.write_bytes(b"outside project memory stays untouched\x00\xff")
    project_memory = adopter_dir / "memory"
    raw_project_target = os.path.relpath(outside, adopter_dir)
    project_memory.symlink_to(raw_project_target, target_is_directory=True)

    parent_loop = adopter_dir.parent / f"{adopter_dir.name}-harness-loop"
    parent_loop.symlink_to(parent_loop.name, target_is_directory=True)
    harness = parent_loop / "memory"
    adopter_before = _tree_snapshot(adopter_dir)
    outside_before = _tree_snapshot(outside)
    loop_target = os.readlink(parent_loop)
    local_history = adopter_dir / ".validated-memory" / "local.jsonl"
    local_before = local_history.read_bytes() if local_history.exists() else None

    try:
        for _ in range(2):
            result = run_cli(
                "init", "--harness-memory", str(harness), cwd=adopter_dir
            )

            assert result.returncode == 2
            assert result.stdout == ""
            assert result.stderr.splitlines()[-1] == HARNESS_OUTSIDE_ERROR
            assert PROJECT_MEMORY_OUTSIDE_ERROR not in result.stderr
            assert "Traceback" not in result.stderr
            assert _tree_snapshot(adopter_dir) == adopter_before
            assert _tree_snapshot(outside) == outside_before
            assert parent_loop.is_symlink()
            assert os.readlink(parent_loop) == loop_target
            assert not harness.exists() and not harness.is_symlink()
            assert marker.read_bytes() == (
                b"outside project memory stays untouched\x00\xff"
            )
            assert (
                local_history.read_bytes() if local_history.exists() else None
            ) == local_before
            assert not _link_records(adopter_dir)
    finally:
        # Keep pytest's basetemp cleanup from resolving this deliberate loop.
        parent_loop.unlink()


def test_looping_project_memory_is_diagnosed_before_harness_parent_creation(
    adopter_dir, run_cli
):
    project_memory = adopter_dir / "memory"
    project_memory.symlink_to("memory", target_is_directory=True)
    harness = _external_harness_path(adopter_dir)

    result = run_cli(
        "init", "--harness-memory", str(harness), cwd=adopter_dir
    )

    assert result.returncode == 1
    assert "no 'memory/' to link to" in result.stderr
    assert "Traceback" not in result.stderr
    assert project_memory.is_symlink()
    assert os.readlink(project_memory) == "memory"
    assert not harness.parent.exists()
    assert not _link_records(adopter_dir)


@pytest.mark.parametrize(
    "project_memory", ("missing", "broken", "loop", "non-directory")
)
def test_corrupt_journal_with_unusable_project_memory_leaves_the_harness_alone(
    adopter_dir, run_cli, project_memory
):
    """The harness step validates the target first, and the path stays absent.

    Pins that a refused history with no project memory to link to creates
    nothing: the harness parent is not made, the adopter tree is untouched,
    and the only extra line is the no-project-memory WARNING that any run
    would give, not a withheld-link one."""
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    memory = adopter_dir / "memory"
    (memory / "MEMORY.md").unlink()
    memory.rmdir()
    if project_memory == "broken":
        memory.symlink_to("missing-memory", target_is_directory=True)
    elif project_memory == "loop":
        memory.symlink_to("memory", target_is_directory=True)
    elif project_memory == "non-directory":
        memory.write_bytes(b"not a project memory directory\n")

    history = adopter_dir / "journal.jsonl"
    history.write_text(
        history.read_text(encoding="utf-8") + "{not json\n",
        encoding="utf-8",
    )
    harness = _external_harness_path(adopter_dir)
    adopter_before = _tree_snapshot(adopter_dir)

    result = run_cli(
        "init", "--harness-memory", str(harness), cwd=adopter_dir
    )

    assert result.returncode == 1
    assert result.stdout == "init: 0 item(s) confirmed, 1 gate(s)\n"
    assert result.stderr.splitlines()[0] == (
        "ERROR: journal.jsonl:14: journal: line is not valid JSON: Expecting "
        "property name enclosed in double quotes. No target or permanent-history "
        "change was left by this operation"
    )
    assert len(result.stderr.splitlines()) == 2, result.stderr
    assert result.stderr.splitlines()[1].startswith(
        f"WARNING: {harness}: symlink: this project has no 'memory/' to link to"
    )
    assert "harness link was not restored" not in result.stderr
    assert PROJECT_MEMORY_OUTSIDE_ERROR not in result.stderr
    assert "Traceback" not in result.stderr
    assert not harness.parent.exists()
    assert _tree_snapshot(adopter_dir) == adopter_before
    assert not _link_records(adopter_dir)


@pytest.mark.parametrize("project_memory", ("direct", "relative", "absolute"))
def test_valid_project_memory_targets_create_a_recorded_harness_link(
    adopter_dir, run_cli, project_memory
):
    target = adopter_dir / "memory"
    raw_target = None
    if project_memory != "direct":
        target = adopter_dir / "containers" / f"{project_memory}-memory"
        target.mkdir(parents=True)
        (target / "preserved.bin").write_bytes(b"in-root memory\x00\xff")
        raw_target = (
            target.relative_to(adopter_dir).as_posix()
            if project_memory == "relative"
            else str(target)
        )
        (adopter_dir / "memory").symlink_to(
            raw_target, target_is_directory=True
        )
    harness = _external_harness_path(adopter_dir)

    result = run_cli(
        "init", "--harness-memory", str(harness), cwd=adopter_dir
    )

    assert result.returncode == 0, result.stderr
    assert f"init: created symlink {harness} -> {target.resolve()}" in (
        result.stdout.splitlines()
    )
    assert harness.is_symlink()
    assert os.readlink(harness) == str(target.resolve())
    assert [entry["stage"] for entry in _link_records(adopter_dir)] == [
        "prepared",
        "committed",
    ]
    if raw_target is not None:
        assert os.readlink(adopter_dir / "memory") == raw_target
        assert (target / "preserved.bin").read_bytes() == b"in-root memory\x00\xff"


@pytest.mark.parametrize("harness_state", ("missing", "stale"))
def test_unrelated_item_error_still_restores_a_valid_harness_link(
    adopter_dir, run_cli, harness_state
):
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    config = adopter_dir / "validated-memory.md"
    config.unlink()
    config.mkdir()
    marker = adopter_dir / "memory" / "project-marker.bin"
    marker.write_bytes(b"project memory stays valid\n")
    harness = _external_harness_path(adopter_dir)
    if harness_state == "stale":
        harness.parent.mkdir(parents=True)
        stale = harness.parent / "stale-target"
        stale.mkdir()
        harness.symlink_to(stale, target_is_directory=True)

    result = run_cli(
        "init", "--harness-memory", str(harness), cwd=adopter_dir
    )

    assert result.returncode == 1
    assert "validated-memory.md: create" in result.stderr
    assert "Traceback" not in result.stderr
    assert harness.is_symlink()
    assert os.readlink(harness) == str((adopter_dir / "memory").resolve())
    assert marker.read_bytes() == b"project memory stays valid\n"
    verb = "created" if harness_state == "missing" else "re-pointed"
    assert (
        f"init: {verb} symlink {harness} -> "
        f"{(adopter_dir / 'memory').resolve()}"
    ) in result.stdout.splitlines()
    assert [entry["stage"] for entry in _link_records(adopter_dir)] == [
        "prepared",
        "committed",
    ]


def test_a_repointed_symlink_records_its_previous_target_before_losing_it(
    adopter_dir, tmp_path, run_cli
):
    """The record comes first, because the mutation destroys what it records.

    A `link` record's payload is the previous target, and its inverse is
    "restore the previous target". Re-pointing the symlink is what makes
    that target unreadable, so a record written afterwards has a window in
    which the only copy of it is in memory -- and the same window leaves a
    re-pointed link no record mentions at all.

    The link goes through the executor now, so what is written first is the
    transaction file, fsynced with the previous target in it, and the two
    history records are appended together afterwards under one transaction
    id -- which is what says the two lines are one act, since the
    transaction file itself is local and leaves the disk on the next line.
    The stages and the note are what this pins; the id is what ties them.

    The link is never absent between two runs either, by construction
    rather than by measurement: publication builds the new link under a
    pid-named temporary and renames it over the path (the journal executor
    does this for the recorded path, `init._sync_symlink.relink` for the
    fail-open one), and nothing on either path unlinks anything. A test cannot
    observe a window that does not exist; reading the two code paths is how
    this is checked.
    """
    import json

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    harness_memory = _external_harness_path(adopter_dir)
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
    assert len({entry["transaction"] for entry in links}) == 1, links
    # And the transaction it names is closed: nothing is left open for the
    # next run to gate on.
    assert not list(
        (adopter_dir / ".validated-memory" / "transactions").glob("*.json")
    ), sorted((adopter_dir / ".validated-memory" / "transactions").iterdir())


def test_a_first_link_records_that_there_was_no_previous_target(
    adopter_dir, tmp_path, run_cli
):
    """The other half of the note, and the one every first adoption writes.

    `init._previous_target` has two answers, and the sibling test above
    pins only the one a re-pointing writes. This is the one a fresh
    `--harness-memory` writes, which is every project's first run: there is
    no previous target, and the note says so rather than being absent or
    empty. A `link` record's inverse is "restore the previous target", so
    "there was none" is the fact that tells a reversal to remove the link
    instead of re-pointing it.
    """
    import json

    harness_memory = _external_harness_path(adopter_dir)

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
    )

    assert result.returncode == 0, result.stderr
    assert harness_memory.is_symlink(), sorted(tmp_path.iterdir())
    vault = adopter_dir / ".validated-memory" / "local.jsonl"
    links = [
        json.loads(line)
        for line in vault.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["op"] == "link"
    ]
    assert [entry["stage"] for entry in links] == ["prepared", "committed"], links
    for entry in links:
        assert entry["note"] == "no previous link", entry


def test_a_recorded_symlink_carries_no_mode(
    adopter_dir, tmp_path, run_cli, monkeypatch
):
    """A symlink's `lstat` mode is 0777, and 0777 is not a fact about anything.

    Nobody chooses it, no platform this runs on varies it, and the node it
    describes is a pointer rather than bytes. A record carrying `mode: 511`
    would be read back by a reversal as the mode to restore -- and the only
    thing a `chmod` on that path can reach is the DIRECTORY the link points
    at, this project's `memory/`. The executor already refuses to record it
    as a link's PREIMAGE mode for exactly that reason; the postimage is the
    same fact and gets the same answer, in the records `execute` writes and
    in the ones recovery rebuilds.
    """
    import json

    harness_memory = _external_harness_path(adopter_dir)

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
    )

    assert result.returncode == 0, result.stderr
    vault = adopter_dir / ".validated-memory" / "local.jsonl"
    links = [
        json.loads(line)
        for line in vault.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["op"] == "link"
    ]
    assert [entry["stage"] for entry in links] == ["prepared", "committed"], links
    for entry in links:
        assert "mode" not in entry, entry
    # And the directory the link points at is still a directory: nothing
    # here ever asked for a mode on it.
    assert (adopter_dir / "memory").is_dir()

    # The records recovery rebuilds are the records `execute` would have
    # written, and that includes the field it would have left out. The
    # ignore entry and every item are already there, so the link is this
    # run's first and only mutation and the kill lands on it.
    harness_memory.unlink()
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-published")
    killed = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
    )
    assert killed.returncode == 70, (killed.stdout, killed.stderr)
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")

    recovered = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
    )

    assert recovered.returncode == 0, recovered.stderr
    rebuilt = [
        json.loads(line)
        for line in vault.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["op"] == "link"
    ][len(links):]
    assert [entry["stage"] for entry in rebuilt] == ["prepared", "committed"], rebuilt
    for entry in rebuilt:
        assert "mode" not in entry, entry


def test_a_corrupt_journal_stops_before_the_harness_symlink(
    adopter_dir, tmp_path, run_cli
):
    """Damaged retained history grants no unrecorded harness authority."""
    harness_memory = _external_harness_path(adopter_dir)
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
    assert result.stdout == "init: 0 item(s) confirmed, 1 gate(s)\n"
    assert result.stderr.count("not valid JSON") == 1
    assert not harness_memory.exists() and not harness_memory.is_symlink()
    assert "could not be recorded" not in result.stderr


@pytest.mark.parametrize("gate", ("corrupt-journal", "unignored-vault"))
@pytest.mark.parametrize("link_state", ("missing", "correct", "stale"))
def test_a_whole_run_gate_still_restores_external_harness_symlinks(
    adopter_dir, run_cli, gate, link_state
):
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    harness_memory = _external_harness_path(adopter_dir)
    harness_memory.parent.mkdir(parents=True)
    if link_state == "correct":
        harness_memory.symlink_to(
            (adopter_dir / "memory").resolve(), target_is_directory=True
        )
    elif link_state == "stale":
        stale = harness_memory.parent / "stale-target"
        stale.mkdir()
        harness_memory.symlink_to(stale, target_is_directory=True)

    if gate == "corrupt-journal":
        journal = adopter_dir / "journal.jsonl"
        journal.write_text(
            journal.read_text(encoding="utf-8") + "{not json\n",
            encoding="utf-8",
        )
        gate_reason = "not valid JSON"
    else:
        ignore_file = adopter_dir / ".gitignore"
        ignore_file.unlink()
        target = adopter_dir / "unignored-target"
        target.write_text("adopter-owned bytes\n", encoding="utf-8")
        ignore_file.symlink_to(target.name)
        gate_reason = "vault's ignore entry"

    adopter_before = _tree_snapshot(adopter_dir)
    local_history = adopter_dir / ".validated-memory" / "local.jsonl"
    local_before = local_history.read_bytes() if local_history.exists() else None

    first = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
    )

    assert first.returncode == 1
    assert gate_reason in first.stderr
    assert "Traceback" not in first.stderr
    if gate == "corrupt-journal":
        if link_state == "missing":
            assert not harness_memory.exists() and not harness_memory.is_symlink()
        elif link_state == "correct":
            assert harness_memory.resolve() == (adopter_dir / "memory").resolve()
        else:
            assert harness_memory.resolve() == stale.resolve()
    else:
        assert harness_memory.is_symlink()
        assert harness_memory.resolve() == (adopter_dir / "memory").resolve()
        assert os.readlink(harness_memory) == str(
            (adopter_dir / "memory").resolve()
        )
    assert _tree_snapshot(adopter_dir) == adopter_before
    assert (
        local_history.read_bytes() if local_history.exists() else None
    ) == local_before
    verb = {
        "missing": "created symlink",
        "correct": "kept symlink",
        "stale": "re-pointed symlink",
    }[link_state]
    if gate == "corrupt-journal":
        assert verb not in first.stdout
        # A withheld link is said, and a link that needs nothing is not
        # reported as withheld.
        withheld = "the harness link was not restored" in first.stderr
        assert withheld == (link_state != "correct"), first.stderr
    else:
        assert verb in first.stdout
    assert "adopted" not in first.stdout
    assert "parked" not in first.stdout

    second = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter_dir
    )
    assert second.returncode == 1
    assert gate_reason in second.stderr
    if gate == "corrupt-journal":
        assert "kept symlink" not in second.stdout
    else:
        assert "kept symlink" in second.stdout
        assert os.readlink(harness_memory) == str(
            (adopter_dir / "memory").resolve()
        )
    assert _tree_snapshot(adopter_dir) == adopter_before
    assert (
        local_history.read_bytes() if local_history.exists() else None
    ) == local_before


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
def test_an_unwritable_root_gates_and_leaves_no_link_pointing_nowhere(
    adopter_dir, tmp_path, run_cli
):
    """A link to a `memory/` that does not exist is not a restored link.

    An adopter root that cannot be written to fails before any scaffold item
    -- the lock and the journal's own bootstrap both need to create files in
    it -- so this project has no `memory/` and cannot get one. Measured
    before this test said so: `init` printed `created symlink` and left the
    harness's memory path dangling at a directory that does not exist, which
    is the one outcome worse than not linking at all -- the harness then has
    no memory, where an untouched path leaves it its own, and a later run
    absorbs that into the project (`adopt.take_over`).

    The gate itself is unchanged: exit 1, and the journal ERROR says why.
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
        assert "no 'memory/' to link to" in result.stderr, result.stderr
        assert not harness_memory.is_symlink(), "the harness path was linked"
        assert not harness_memory.exists()
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
    assert result.stdout == (
        "init: kept knowledge\n"
        "init: kept memory\n"
        "init: kept memory/MEMORY.md\n"
        "init: kept validated-memory.md\n"
        "init: kept knowledge-extension.md\n"
    )
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
