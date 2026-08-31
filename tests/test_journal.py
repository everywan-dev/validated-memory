"""End-to-end tests for the journal: the durable record of every mutation.

Like every test in this suite these drive the CLI as a subprocess over a
fixture adopter tree, and never import the package's internals. What a
record means is `docs/reference/journal.md`; what it is for is
`docs/design/2026-08-30-the-journal-coverage-and-reversal-design.md`.
"""

import ast
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Path methods that mutate, whatever the receiver.
PATH_MUTATORS = {"write_text", "write_bytes", "mkdir", "symlink_to", "rmdir",
                 "unlink", "rename"}
# `os` functions that mutate, matched qualified: `str.replace` is not one of
# them, and `status.parse_timestamp` calls it.
OS_MUTATORS = {"replace", "rename", "remove", "symlink", "unlink"}
# Calls that reach the journal.
RECORDING = {"observe", "write", "append_op", "append", "_record_symlink"}

# Exempt, each with the reason it is not an adopter mutation this plan
# records. `journal.py` IS the write path. `render.py` and `derive.py` write
# only derived artifacts, which their own commands regenerate. `adopt.py`
# performs the harness absorption, which the reversal plan records with the
# rest of it. `_ensure_views` writes the two HTML views, derived the same way.
EXEMPT_MODULES = {"journal.py", "render.py", "derive.py", "adopt.py"}
EXEMPT_FUNCTIONS = {("init.py", "_ensure_views")}


def _called_name(call):
    function = call.func
    if isinstance(function, ast.Attribute):
        if function.attr in PATH_MUTATORS:
            return function.attr
        if (
            function.attr in OS_MUTATORS
            and isinstance(function.value, ast.Name)
            and function.value.id == "os"
        ):
            return f"os.{function.attr}"
    return None


def _records(path):
    """Every JSON record in a `.jsonl` file, in file order."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_init_writes_a_journal_whose_records_carry_the_common_fields(
    run_cli, tmp_path
):
    """Every record says when, by what, under which adoption and run.

    Without those a record cannot be attributed, and an unattributable
    record cannot be diffed against the next run -- which is the whole
    reason the log exists rather than a report that lives for one message.
    """
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    journal = tmp_path / "journal.jsonl"
    assert journal.is_file(), sorted(p.name for p in tmp_path.iterdir())
    records = _records(journal)
    assert records, "the journal is empty"
    for entry in records:
        assert entry["schema"] == 1, entry
        assert entry["at"].endswith("Z"), entry
        assert entry["version"], entry
        assert entry["adoption"], entry
        assert entry["run"], entry
        assert entry["durability"] == "repo", entry
        assert entry["op"] in (
            "observe",
            "create",
            "replace",
            "patch",
            "append",
            "link",
            "rename",
            "remove",
            "move",
        ), entry


def test_the_adoption_id_is_minted_once_and_survives_later_runs(run_cli, tmp_path):
    """One adoption, one id, however many times `init` runs.

    `init` is deliberately re-runnable at session start. An id minted per
    run would make every run look like a separate adoption, and the reversal
    a later plan builds could not tell which records belong together.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    first = {entry["adoption"] for entry in _records(tmp_path / "journal.jsonl")}
    assert len(first) == 1, first

    assert run_cli("init", cwd=tmp_path).returncode == 0
    second = {entry["adoption"] for entry in _records(tmp_path / "journal.jsonl")}
    assert second == first, (first, second)


def test_each_invocation_groups_its_records_under_one_run_id(run_cli, tmp_path):
    """One command, one run id -- and a second command, a second one."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    assert run_cli("init", cwd=tmp_path).returncode == 0

    runs = [entry["run"] for entry in _records(tmp_path / "journal.jsonl")]
    assert len(set(runs)) == 2, runs


def test_a_journal_that_cannot_be_parsed_is_refused_with_its_line(run_cli, tmp_path):
    """A partial answer from a journal is worse than no answer.

    Nothing regenerates a journal, so a reader that skipped the line it did
    not understand would silently narrow the record -- the failure mode this
    whole component exists to remove.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        journal.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8"
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "journal.jsonl" in result.stderr, result.stderr
    assert "not valid JSON" in result.stderr, result.stderr


def test_a_created_file_records_its_postimage_and_both_stages(run_cli, tmp_path):
    """Creating a file records both stages and the bytes it ended up with.

    A `created` path needs no preimage: its inverse is removal. A replaced
    one does, and it can only be taken the first time, because only the
    first copy is the pre-adoption state.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    # `init` keeps an existing file rather than replacing it, so the second
    # run must record `observe`, not `replace`, for the same paths.
    records = _records(tmp_path / "journal.jsonl")
    # `create` also covers a directory made via `append_op` (no preimage or
    # postimage: a directory has no content to digest), so file creates are
    # the ones that carry a `postimage`.
    creates = [
        e
        for e in records
        if e["op"] == "create" and e["stage"] == "committed" and "postimage" in e
    ]
    assert creates, records
    for entry in creates:
        assert entry["postimage"].startswith("sha256:"), entry

    prepared = [e for e in records if e["stage"] == "prepared"]
    committed = [e for e in records if e["stage"] == "committed"]
    assert len(prepared) == len(creates), (prepared, creates)
    # Every prepared record is followed by its committed twin for the same path.
    assert {e["path"] for e in prepared} <= {e["path"] for e in committed}


def test_init_records_create_for_what_it_made_and_observe_for_what_it_kept(
    run_cli, tmp_path
):
    """The two outcomes `init` already prints are the two records it writes.

    "It was already here" is a fact about the pre-adoption state and cannot
    be re-derived afterwards, which is why it is written down rather than
    inferred at reversal time -- the defect that retired the first uninstall
    design.
    """
    (tmp_path / "knowledge").mkdir()

    assert run_cli("init", cwd=tmp_path).returncode == 0

    records = _records(tmp_path / "journal.jsonl")
    committed = [e for e in records if e["stage"] == "committed"]
    by_path = {e["path"]: e["op"] for e in committed}
    assert by_path["knowledge"] == "observe", by_path
    assert by_path["memory"] == "create", by_path
    assert by_path["validated-memory.md"] == "create", by_path
    assert by_path["memory/MEMORY.md"] == "create", by_path
    assert by_path["knowledge-extension.md"] == "create", by_path


def test_a_second_init_records_observe_for_everything_it_kept(run_cli, tmp_path):
    """A re-run creates nothing, so it records no `create`."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    first = len(_records(tmp_path / "journal.jsonl"))

    assert run_cli("init", cwd=tmp_path).returncode == 0

    added = _records(tmp_path / "journal.jsonl")[first:]
    assert added, "the second run recorded nothing"
    assert {e["op"] for e in added} == {"observe"}, added


def test_journal_reports_the_log_and_exits_clean(run_cli, tmp_path):
    """Reading the journal is read-only and never gates on its own."""
    assert run_cli("init", cwd=tmp_path).returncode == 0

    result = run_cli("journal", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "journal:" in result.stdout, result.stdout
    assert "record(s)" in result.stdout, result.stdout


def test_journal_check_reconciles_an_unfinished_transaction(run_cli, tmp_path):
    """A prepared record with no committed twin is reported, never guessed at.

    Recovery says which of the three states the path is in and stops. A
    recovery that decided for itself would be the silent narrowing again,
    one layer down.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    orphan = {
        "schema": 1,
        "at": "2026-08-31T00:00:00Z",
        "version": "1.6.0",
        "adoption": json.loads(journal.read_text().splitlines()[0])["adoption"],
        "run": "0000000000000000",
        "durability": "repo",
        "op": "replace",
        "purpose": "init",
        "path": "validated-memory.md",
        "stage": "prepared",
        "preimage": "sha256:" + "0" * 64,
        "postimage": "sha256:" + "1" * 64,
    }
    journal.write_text(
        journal.read_text(encoding="utf-8")
        + json.dumps(orphan, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "validated-memory.md" in result.stderr, result.stderr
    assert "unfinished" in result.stderr, result.stderr
    assert "diverged" in result.stderr, result.stderr


def test_journal_check_catches_a_second_write_to_one_path_in_one_run(
    run_cli, tmp_path
):
    """A `committed` record closes the ONE `prepared` it follows, not every
    `prepared` that happens to share its (run, path).

    `init` writes `validated-memory.md` once, closing that write's own
    `prepared`/`committed` pair. Here a second write of the same path under
    the same run is appended by hand, with its `prepared` record but no
    matching `committed` -- the second write was interrupted. Pairing by set
    membership would let the first write's `committed` record close this
    second `prepared` too, since both share the same (run, path); reporting
    would then wrongly say the journal is clean.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    first_write = next(
        entry
        for entry in records
        if entry["path"] == "validated-memory.md" and entry["stage"] == "committed"
    )

    second_write = dict(first_write)
    second_write["stage"] = "prepared"
    second_write["op"] = "replace"
    second_write["preimage"] = "sha256:" + "2" * 64
    second_write["postimage"] = "sha256:" + "3" * 64

    journal.write_text(
        journal.read_text(encoding="utf-8")
        + json.dumps(second_write, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "validated-memory.md" in result.stderr, result.stderr
    assert "unfinished" in result.stderr, result.stderr


def test_every_write_in_the_package_goes_through_the_journal():
    """A mutation with no record fails here, not in the field.

    The 1.5.0 and 1.5.1 failures were both silent narrowings that no test
    could see. This is the pin that makes a new unjournalled write path
    visible the moment it is added: a function that mutates the filesystem
    must also reach the journal, or be named exempt above with its reason.

    The check is deliberately coarse -- it asks whether a function contains
    both kinds of call, not whether one guards the other -- so a function
    that mutates and separately calls something journal-shaped would pass.
    A call-graph would be exact and would also be a second implementation of
    the thing it checks.
    """
    offenders = []
    for path in sorted((REPO_ROOT / "validated_memory").rglob("*.py")):
        if path.name in EXEMPT_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if (path.name, node.name) in EXEMPT_FUNCTIONS:
                continue
            mutations = []
            records = False
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                name = _called_name(sub)
                if name is not None:
                    mutations.append((name, sub.lineno))
                called = getattr(sub.func, "attr", None) or getattr(
                    sub.func, "id", None
                )
                if called in RECORDING:
                    records = True
            if records:
                continue
            for name, lineno in mutations:
                offenders.append(
                    f"{path.name}:{lineno}: {node.name}() calls {name}() "
                    "and never reaches the journal"
                )
    assert not offenders, (
        "these mutate without reaching the journal; route them through a "
        "`Run` method or add them to EXEMPT_MODULES/EXEMPT_FUNCTIONS with "
        "the reason:\n" + "\n".join(offenders)
    )
