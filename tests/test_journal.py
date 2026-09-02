"""End-to-end tests for the journal: the durable record of every mutation.

Like every test in this suite these drive the CLI as a subprocess over a
fixture adopter tree, and never import the package's internals. What a
record means is `docs/reference/journal.md`; what it is for is
`docs/design/2026-08-30-the-journal-coverage-and-reversal-design.md`.
"""

import ast
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Path methods that mutate, whatever the receiver.
PATH_MUTATORS = {
    "write_text", "write_bytes", "mkdir", "symlink_to", "hardlink_to",
    "touch", "chmod", "lchmod", "rmdir", "unlink", "rename",
}
# `os` functions that mutate, matched qualified: `str.replace` is not one of
# them, and `status.parse_timestamp` calls it. `Path.replace` is the same
# name and cannot be told apart from `str.replace` by name alone, so it is
# matched on arity instead -- `str.replace` takes at least two arguments,
# `Path.replace` exactly one.
OS_MUTATORS = {
    "replace", "rename", "renames", "remove", "removedirs", "symlink",
    "link", "unlink", "makedirs", "mkdir", "rmdir", "truncate", "chmod",
    "utime", "write",
}
# `shutil` functions that mutate.
SHUTIL_MUTATORS = {
    "copy", "copy2", "copyfile", "copytree", "copymode", "copystat", "move",
    "rmtree", "make_archive", "unpack_archive",
}
# The journal's own atomic install (rename plus the durability barrier). It
# is a mutation like any other when called from outside `journal.py`.
JOURNAL_MUTATORS = {"install"}

# A call reaches the journal when it is made ON the journal -- `session.write`,
# `journal.append` -- or is the one helper that wraps it. Matching the bare
# method name would let `findings.append(...)` and `handle.write(...)` stand
# in for a record, and `adopt._absorb` contains exactly such a call while
# genuinely recording nothing.
RECORDERS = {"session", "journal"}
RECORDING_METHODS = {"observe", "write", "append_op", "append", "execute"}
RECORDING_FUNCTIONS = {"_record_symlink"}

# Exempt, each with the reason it is not an adopter mutation this plan
# records. `journal.py` IS the write path. `render.py` and `derive.py` write
# only derived artifacts, which their own commands regenerate. The four
# `adopt.py` functions perform the harness absorption, deferred whole to the
# reversal plan -- named one by one rather than by module, so a new write
# added to that file is still caught.
EXEMPT_MODULES = {"journal.py", "render.py", "derive.py"}
EXEMPT_FUNCTIONS = {
    ("init.py", "_ensure_views"),
    ("adopt.py", "take_over"),
    ("adopt.py", "_absorb"),
    ("adopt.py", "_reconcile_index"),
    ("adopt.py", "_park"),
    # `verdicts.jsonl` is the other append-only log. `probe` writes it and
    # re-running `probe` rebuilds it, which is exactly what a journal is
    # not -- and why journalling it is deferred with the rest of the write
    # paths outside `init` (see `docs/reference/journal.md`, "What is
    # recorded, and what is not yet").
    ("verdicts.py", "append"),
}


def _writing_mode(call):
    """Whether this `open` call opens its target for writing.

    The mode is the second argument of `open(path, mode)` and the first of
    `path.open(mode)`. No mode at all is a read. A mode this cannot read --
    a variable, an expression -- counts as a write: guessing the other way
    would let one indirection hide the single most ordinary way to write a
    file in Python.
    """
    index = 1 if isinstance(call.func, ast.Name) else 0
    mode = call.args[index] if len(call.args) > index else None
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    if mode is None:
        return False
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return any(character in mode.value for character in "wax+")
    return True


def _mutating_call(call):
    """The name of the filesystem mutation this call performs, or None."""
    function = call.func
    if isinstance(function, ast.Name):
        if function.id == "open" and _writing_mode(call):
            return "open(...) for writing"
        return None
    if not isinstance(function, ast.Attribute):
        return None
    if function.attr == "open":
        return "open(...) for writing" if _writing_mode(call) else None
    receiver = (
        function.value.id if isinstance(function.value, ast.Name) else None
    )
    for module, vocabulary in (
        ("os", OS_MUTATORS),
        ("shutil", SHUTIL_MUTATORS),
        ("journal", JOURNAL_MUTATORS),
    ):
        if receiver == module and function.attr in vocabulary:
            return f"{module}.{function.attr}"
    if function.attr in PATH_MUTATORS:
        return function.attr
    if function.attr == "replace" and len(call.args) == 1 and not call.keywords:
        return "replace"
    return None


def _recording_call(call):
    """Whether this call reaches the journal."""
    function = call.func
    if isinstance(function, ast.Attribute):
        return (
            function.attr in RECORDING_METHODS
            and isinstance(function.value, ast.Name)
            and function.value.id in RECORDERS
        )
    return isinstance(function, ast.Name) and function.id in RECORDING_FUNCTIONS


def _scopes(tree):
    """Every scope in a module, as `(name, [call nodes])`.

    One scope per function that is not defined inside another function,
    carrying everything nested in it -- a closure is part of the function
    that builds it, which is how `_sync_symlink` can hand its own mutation
    to the function that journals it. Plus one `<module>` scope for
    everything else, because a write at module level runs on import and is
    no less a write for having no `def` above it.
    """
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    nested = {
        id(child)
        for function in functions
        for child in ast.walk(function)
        if child is not function
        and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    outer = [function for function in functions if id(function) not in nested]
    inside = {id(node) for function in outer for node in ast.walk(function)}
    scopes = [
        (
            function.name,
            [node for node in ast.walk(function) if isinstance(node, ast.Call)],
        )
        for function in outer
    ]
    scopes.append(
        (
            "<module>",
            [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and id(node) not in inside
            ],
        )
    )
    return scopes


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
    """One command, one run id -- and a second command, a second one.

    The second run has to have something to record: a re-run that creates
    nothing writes nothing at all, so the item is removed between the two.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    (tmp_path / "knowledge-extension.md").unlink()

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
    records = _records(tmp_path / "journal.jsonl")
    # `create` also covers a directory (no preimage or postimage: a
    # directory has no content to digest), so file creates are the ones that
    # carry a `postimage`.
    creates = [
        e
        for e in records
        if e["op"] == "create" and e["stage"] == "committed" and "postimage" in e
    ]
    assert creates, records
    for entry in creates:
        assert entry["postimage"].startswith("sha256:"), entry

    # Every mutation -- a file write, the `append` of the vault's ignore
    # entry, a directory -- is one `prepared` record closed by one
    # `committed` twin for the same op and path, in that order. Only
    # `observe` stands alone: it is a fact about a path, not a change to one.
    prepared = [
        (e["op"], e["path"]) for e in records if e["stage"] == "prepared"
    ]
    committed = [
        (e["op"], e["path"])
        for e in records
        if e["stage"] == "committed" and e["op"] != "observe"
    ]
    assert prepared == committed, records


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


def test_a_re_run_that_creates_nothing_records_nothing(run_cli, tmp_path):
    """`observe` is written once, on first sight -- not once per run.

    The `SessionStart` hook runs `init --harness-memory` at every session
    start of an adopted project, and `journal.jsonl` is always versioned:
    re-observing the same five paths every time added 1317 bytes per
    session to a repository file, with a diff on every commit, and
    `bootstrap` re-read all of it each run. Worse than the growth, the
    meaning was wrong -- after the first run "file already present" is a
    fact about a file the plugin itself created, not about the state
    adoption found.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    first = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")

    assert run_cli("init", cwd=tmp_path).returncode == 0
    assert run_cli("init", cwd=tmp_path).returncode == 0

    assert (tmp_path / "journal.jsonl").read_text(encoding="utf-8") == first


def test_a_pre_existing_path_is_observed_once_and_only_once(run_cli, tmp_path):
    """The fact that adoption found `knowledge/` there is recorded exactly once."""
    (tmp_path / "knowledge").mkdir()

    assert run_cli("init", cwd=tmp_path).returncode == 0
    assert run_cli("init", cwd=tmp_path).returncode == 0

    records = _records(tmp_path / "journal.jsonl")
    observed = [
        e for e in records if e["op"] == "observe" and e["path"] == "knowledge"
    ]
    assert len(observed) == 1, records


def test_a_directory_is_recorded_before_it_is_created(run_cli, tmp_path):
    """A `mkdir` is a mutation, so it is two records like every other one.

    Design §4 rejects both one-record protocols, and "mutate first, record
    after" is one of them: a crash in between leaves a directory nothing
    knows about. The `prepared` record is what makes that window visible.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0

    records = _records(tmp_path / "journal.jsonl")
    stages = [
        e["stage"] for e in records if e["path"] == "memory" and e["op"] == "create"
    ]
    assert stages == ["prepared", "committed"], records


def test_a_path_the_journal_already_knows_is_never_observed_as_pre_existing(
    run_cli, tmp_path
):
    """The residue of an interrupted mutation must not become a false `observe`.

    Reconstructed here the way a crash leaves it: the `committed` record of
    the `knowledge/` mkdir is removed, so the journal carries the `prepared`
    record and the directory is on disk. The next run finds the directory
    present -- and must not write "directory already present", which would
    be a permanent, uninvertible record claiming adoption found a directory
    the plugin created itself.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    kept = [
        line
        for line in journal.read_text(encoding="utf-8").splitlines()
        if not (
            json.loads(line)["path"] == "knowledge"
            and json.loads(line)["stage"] == "committed"
        )
    ]
    journal.write_text("\n".join(kept) + "\n", encoding="utf-8")

    assert run_cli("init", cwd=tmp_path).returncode == 0

    records = _records(journal)
    assert not [
        e for e in records if e["op"] == "observe" and e["path"] == "knowledge"
    ], records
    # The interrupted transaction is still open, and still reported.
    result = run_cli("journal", "--check", cwd=tmp_path)
    assert result.returncode == 1, result.stdout
    assert "knowledge" in result.stderr, result.stderr
    assert "applied" in result.stderr, result.stderr


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


def test_journal_check_reports_each_of_the_four_states(run_cli, tmp_path):
    """`applied`, `unapplied` and `unknown`, alongside the `diverged` above.

    Plan 5 decides whether to invert a record on exactly this
    classification, and three of the four states had no regression
    protection at all -- one hand-run from a review, which leaves nothing
    behind.

    Each orphan carries its own run id, so none of them pairs with a
    `committed` record `init` wrote.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    written = _records(journal)
    config = next(
        entry
        for entry in written
        if entry["path"] == "validated-memory.md"
        and entry["stage"] == "committed"
    )

    # `applied`: the bytes on disk are the postimage, so the mutation
    # happened and only the closing record was lost.
    _append_record(
        journal,
        _record(
            journal,
            run="1111111111111111",
            op="create",
            path="validated-memory.md",
            preimage=None,
            postimage=config["postimage"],
        ),
    )
    # `unapplied`: a `create` whose path is genuinely absent.
    _append_record(
        journal,
        _record(
            journal,
            run="2222222222222222",
            op="create",
            path="never-written.md",
            preimage=None,
            postimage="sha256:" + "3" * 64,
        ),
    )
    # `unknown`: the bytes cannot be read at all -- here a directory, which
    # binds every user including root, unlike a permission bit.
    _append_record(
        journal,
        _record(journal, run="4444444444444444", path="knowledge"),
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    reported = {
        line.split(": journal: ")[0].removeprefix("ERROR: "): line
        for line in result.stderr.splitlines()
    }
    assert "the path is applied" in reported["validated-memory.md"], reported
    assert "the path is unapplied" in reported["never-written.md"], reported
    assert "the path is unknown" in reported["knowledge"], reported


def test_a_broken_symlink_where_a_directory_was_expected_is_not_applied(
    run_cli, tmp_path
):
    """`directory` means a directory is there, not merely that the name resolves.

    A `create` record with no postimage describes a `mkdir` (`_ensure_dir`
    is the only writer of that shape today). Reading `exists() or
    is_symlink()` for that case read a broken symlink as `applied`, since
    `is_symlink()` is true whether or not the link resolves -- design §6's
    false `applied`, reachable this time through the reconciler rather than
    through `write`. `current_state`'s `directory` check reads the node
    itself, so a broken symlink left where a directory was expected is
    `unapplied`: the `mkdir` never happened, and nothing here pretends
    otherwise.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    orphan = _record(
        journal,
        run="6666666666666666",
        op="create",
        path="never-created-dir",
    )
    del orphan["preimage"]
    del orphan["postimage"]
    orphan["note"] = "directory created"
    _append_record(journal, orphan)
    (tmp_path / "never-created-dir").symlink_to(tmp_path / "does-not-exist")

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "never-created-dir" in result.stderr, result.stderr
    assert "the path is unapplied" in result.stderr, result.stderr


def test_a_write_over_an_existing_file_parks_its_preimage(run_cli, tmp_path):
    """The preimage store, driven through the CLI rather than hand-written.

    Every `preimage` in this suite used to be a literal, so
    `.validated-memory/preimages/` was never created by anything the tests
    ran: the digest naming, the dedup and the fsync-before-rename had no
    coverage at all, in the store the whole reversal plan is built on.
    `init` appending the vault's ignore entry to an ignore file that
    already exists is the CLI path that parks one.
    """
    import hashlib

    before = "build/\n"
    (tmp_path / ".gitignore").write_text(before, encoding="utf-8")

    assert run_cli("init", cwd=tmp_path).returncode == 0

    reference = hashlib.sha256(before.encode("utf-8")).hexdigest()
    blob = tmp_path / ".validated-memory" / "preimages" / reference
    assert blob.is_file(), sorted(
        p.name for p in (tmp_path / ".validated-memory").iterdir()
    )
    # Named after its own digest, and holding exactly the bytes that were
    # there before the append.
    assert blob.read_text(encoding="utf-8") == before
    record = next(
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["op"] == "append" and entry["stage"] == "committed"
    )
    assert record["preimage"] == f"sha256:{reference}", record
    assert record["prior_bytes"] == len(before.encode("utf-8")), record

    # Parked only the first time: the same bytes park the same digest, and
    # the blob that is already there is left alone rather than rewritten.
    identity = blob.stat().st_ino
    (tmp_path / ".gitignore").write_text(before, encoding="utf-8")
    assert run_cli("init", cwd=tmp_path).returncode == 0
    blobs = sorted((tmp_path / ".validated-memory" / "preimages").iterdir())
    assert [entry.name for entry in blobs] == [reference], blobs
    assert blob.stat().st_ino == identity, "the blob was rewritten"


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


# --- a journal is data, never instructions (design §7) -------------------------


def _append_record(path, entry):
    """Append one hand-built record to a journal file, as a hostile edit would."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(
        existing + json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8"
    )


def _record(journal, **overrides):
    """A complete record, copied from one the CLI wrote, with fields replaced."""
    first = _records(journal)[0]
    entry = {
        "schema": 1,
        "at": "2026-08-31T00:00:00Z",
        "version": first["version"],
        "adoption": first["adoption"],
        "run": "0000000000000000",
        "durability": "repo",
        "op": "replace",
        "purpose": "init",
        "path": "validated-memory.md",
        "stage": "prepared",
        "preimage": "sha256:" + "0" * 64,
        "postimage": "sha256:" + "1" * 64,
    }
    entry.update(overrides)
    return entry


def test_a_field_of_the_wrong_type_is_a_finding_not_a_traceback(run_cli, tmp_path):
    """`journal.jsonl` is versioned repository content, so it is untrusted data.

    Checking that a field is present says nothing about what it holds. A
    `"schema": "1"` written as a string is valid JSON and carries every
    common field, and comparing it to an integer raised a `TypeError`
    through the CLI -- a stack trace where every other refusal in this CLI
    is a rendered finding, and, before this, a crash that happened before
    `init` restored the harness symlink.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    _append_record(journal, _record(journal, schema="1"))

    for arguments in (("journal",), ("journal", "--check"), ("init",)):
        result = run_cli(*arguments, cwd=tmp_path)

        assert result.returncode == 1, (arguments, result.stdout)
        assert "Traceback" not in result.stderr, (arguments, result.stderr)
        assert "ERROR" in result.stderr, (arguments, result.stderr)
        assert "schema" in result.stderr, (arguments, result.stderr)


def test_a_path_that_is_not_a_string_is_a_finding_not_a_traceback(run_cli, tmp_path):
    """The reconciler builds a path out of the record, so its type is load-bearing."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    _append_record(journal, _record(journal, path=123))

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "path" in result.stderr, result.stderr


def test_a_repository_record_may_not_send_the_reader_outside_the_root(
    run_cli, tmp_path
):
    """`journal --check` acts on record paths, so the record cannot name any path.

    A repository record carrying `/etc/passwd` made the reconciler read that
    file and print its state -- a content oracle driven by a versioned,
    adopter-editable file. Design §7: a repository record may only carry a
    relative path that stays below the root, and one that does not is
    refused rather than read.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    _append_record(journal, _record(journal, path="/etc/passwd"))

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "diverged" not in result.stderr, result.stderr
    assert "unfinished" not in result.stderr, result.stderr
    assert "/etc/passwd" in result.stderr, result.stderr
    assert "adopter root" in result.stderr, result.stderr


def test_a_repository_record_may_not_climb_out_with_dot_dot(run_cli, tmp_path):
    """The same refusal for the relative way out of the root."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert run_cli("init", cwd=adopter).returncode == 0
    journal = adopter / "journal.jsonl"
    _append_record(journal, _record(journal, path="../outside.md"))

    result = run_cli("journal", "--check", cwd=adopter)

    assert result.returncode == 1, result.stdout
    assert "adopter root" in result.stderr, result.stderr


def test_a_record_in_the_wrong_artifact_is_refused(run_cli, tmp_path):
    """`durability` says which file holds the record, so the two cannot disagree.

    `append()` derives the file from the same field it stamps, so a
    disagreement is never something the plugin wrote -- it is a hand edit,
    and taking it at face value would let a `local` record smuggle an
    out-of-root path into the versioned journal.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    _append_record(
        journal, _record(journal, durability="local", path="/etc/passwd")
    )

    result = run_cli("journal", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "durability" in result.stderr, result.stderr


def test_a_corrupt_vault_journal_is_reported_against_the_vault(run_cli, tmp_path):
    """The error must name the artifact it came from, not the other one.

    A reader told `journal.jsonl:2` about a fault in the vault opens a file
    that is perfectly valid, at a line that is fine, and finds nothing.
    """
    harness_memory = tmp_path / "harness" / "memory"
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert (
        run_cli(
            "init", "--harness-memory", str(harness_memory), cwd=adopter
        ).returncode
        == 0
    )
    vault = adopter / ".validated-memory" / "local.jsonl"
    vault.write_text(
        vault.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8"
    )

    result = run_cli("journal", cwd=adopter)

    assert result.returncode == 1, result.stdout
    assert ".validated-memory/local.jsonl:" in result.stderr, result.stderr


def test_every_write_in_the_package_goes_through_the_journal():
    """A mutation with no record fails here, not in the field.

    The 1.5.0 and 1.5.1 failures were both silent narrowings that no test
    could see. This is the pin that makes a new unjournalled write path
    visible the moment it is added: a function that mutates the filesystem
    must also reach the journal, or be named exempt above with its reason.

    The check is deliberately coarse -- it asks whether a scope contains
    both kinds of call, not whether one guards the other -- so a function
    that mutates and separately calls something journal-shaped would pass.
    A call-graph would be exact and would also be a second implementation of
    the thing it checks. A recording call is recognised by its receiver
    (`session.write`, `journal.append`), not by its bare method name, so a
    plain `list.append(...)` or `handle.write(...)` elsewhere in the same
    scope cannot stand in for a record.

    What it does NOT see, stated because a pin trusted beyond its reach is
    worse than no pin: it recognises a **fixed vocabulary** of write idioms
    (above), so a write through an alias (`import os as _os`, `writer =
    open`), or through a name nobody listed, is invisible to it. The
    vocabulary was widened after a review wrote four unjournalled write
    paths -- `open(p, "w")`, `shutil.copy2`, `os.makedirs`, and a write at
    module level -- and this test stayed green against all four.
    Mutation-testing it with an idiom already in the vocabulary can only
    confirm the vocabulary; a new idiom in the package is a reason to add it
    here.
    """
    offenders = []
    for path in sorted((REPO_ROOT / "validated_memory").rglob("*.py")):
        if path.name in EXEMPT_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, calls in _scopes(tree):
            if (path.name, name) in EXEMPT_FUNCTIONS:
                continue
            mutations = [
                (_mutating_call(call), call.lineno)
                for call in calls
                if _mutating_call(call) is not None
            ]
            if any(_recording_call(call) for call in calls):
                continue
            for mutation, lineno in mutations:
                offenders.append(
                    f"{path.name}:{lineno}: {name} calls {mutation} "
                    "and never reaches the journal"
                )
    assert not offenders, (
        "these mutate without reaching the journal; route them through a "
        "`Run` method or add them to EXEMPT_MODULES/EXEMPT_FUNCTIONS with "
        "the reason:\n" + "\n".join(offenders)
    )


# --- the root a record names, and the root the filesystem agrees with ---------


def test_an_observation_that_escapes_the_root_through_a_symlink_is_refused(
    run_cli, tmp_path
):
    """A record saying `memory` must not mean bytes outside the root, even to observe it.

    `memory/` is a symlink to a directory outside the adopter root, so
    `_ensure_dir` finds it already there and observes it -- lexically the
    path is fine, and the record would claim a repository-relative fact
    about bytes that are not in the repository at all. Design §7 requires a
    repository-relative record to resolve below the resolved root without
    following a symlink out of it, and `authorise` now asks that question
    for `observe`, not only for a write: before this, only the file `init`
    tried to write beneath `memory/` was refused, and `memory` itself was
    filed into the versioned journal as a fact about the tree that was
    false the moment it was read back.
    """
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    outside = tmp_path / "outside" / "other"
    outside.mkdir(parents=True)
    (adopter / "memory").symlink_to(
        Path("..") / "outside" / "other", target_is_directory=True
    )

    result = run_cli("init", cwd=adopter)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "memory" in result.stderr, result.stderr
    assert "adopter root" in result.stderr, result.stderr
    # Nothing outside the root was created, and nothing claims it was.
    assert not (outside / "MEMORY.md").exists(), sorted(
        p.name for p in outside.iterdir()
    )
    records = _records(adopter / "journal.jsonl")
    assert not [e for e in records if e["path"] == "memory"], records


def test_one_record_the_reader_may_not_follow_does_not_hide_the_others(
    run_cli, tmp_path
):
    """`--check` reports every unfinished transaction, not the first refusal.

    A path that resolves out of the root is a fact about that one record:
    the reader may not read its bytes, so its state is `unknown`. Raising
    instead ended the pass, and every other unfinished transaction in the
    project disappeared behind one line -- while `journal` without `--check`
    read the same file and reported a dozen records, so the two shipped
    modes disagreed about how many records the file holds.
    """
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    (adopter / "knowledge").symlink_to(tmp_path / "nonexistent" / "elsewhere")

    # `init` refuses the link and records nothing, so both open records are
    # written here. The first names a path that resolves out of the root --
    # the shape this test is about -- and the second is an ordinary
    # unfinished transaction that must survive the first one's refusal.
    assert run_cli("init", cwd=adopter).returncode == 1
    journal = adopter / "journal.jsonl"
    for run, path in (("1111111111111111", "knowledge"),
                      ("2222222222222222", "never-written.md")):
        _append_record(
            journal,
            _record(
                journal,
                run=run,
                op="create",
                path=path,
                preimage=None,
                postimage="sha256:" + "3" * 64,
            ),
        )

    result = run_cli("journal", "--check", cwd=adopter)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    reported = {
        line.split(": journal: ")[0].removeprefix("ERROR: "): line
        for line in result.stderr.splitlines()
    }
    assert "the path is unknown" in reported["knowledge"], reported
    assert "the path is unapplied" in reported["never-written.md"], reported
    # The two modes agree about how many records the file holds.
    plain = run_cli("journal", cwd=adopter)
    assert plain.stdout.split()[1] == str(len(_records(journal))), plain.stdout
    assert f"{len(_records(journal))} record(s)" in result.stdout, result.stdout


def test_a_refused_journal_still_reports_the_records_it_did_read(
    run_cli, tmp_path
):
    """The count on the error path is what was read, never a hardcoded zero.

    The repository journal here is perfectly readable and its records were
    read; the vault is the one that could not be parsed. Printing `0
    record(s)` alongside the ERROR describes a project with no history at
    all, which is a different -- and much worse -- fault than the one that
    happened.
    """
    harness_memory = tmp_path / "harness" / "memory"
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert (
        run_cli(
            "init", "--harness-memory", str(harness_memory), cwd=adopter
        ).returncode
        == 0
    )
    vault = adopter / ".validated-memory" / "local.jsonl"
    vault.write_text(
        vault.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8"
    )

    result = run_cli("journal", cwd=adopter)

    assert result.returncode == 1, result.stdout
    read_back = len(_records(adopter / "journal.jsonl"))
    assert f"journal: {read_back} record(s), 1 error(s)" in result.stdout, (
        result.stdout,
        read_back,
    )


# --- one adoption, one id, whatever a checkout leaves behind ------------------


def test_the_adoption_id_survives_a_journal_a_checkout_took_away(
    run_cli, tmp_path
):
    """`journal.jsonl` is versioned; the vault is not. A checkout parts them.

    Checking out a commit from before the adoption removes the tracked
    journal and leaves the ignored vault exactly where it was. Minting a
    second id there would split one adoption in two, with the vault's
    preimages filed under the id nothing else mentions -- and `--check`
    reports clean throughout, because no record is missing or malformed.
    """
    harness_memory = tmp_path / "harness" / "memory"
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert (
        run_cli(
            "init", "--harness-memory", str(harness_memory), cwd=adopter
        ).returncode
        == 0
    )
    journal = adopter / "journal.jsonl"
    minted = _records(journal)[0]["adoption"]
    journal.unlink()

    assert (
        run_cli(
            "init", "--harness-memory", str(harness_memory), cwd=adopter
        ).returncode
        == 0
    )

    adopted = {entry["adoption"] for entry in _records(journal)}
    assert adopted == {minted}, (adopted, minted)


def test_two_artifacts_holding_different_adoption_ids_are_refused(
    run_cli, tmp_path
):
    """Two ids over one project is a state nothing here can resolve.

    A vault filed under one adoption and a journal under another cannot
    both describe this project: the preimages belong to one of the two, and
    nothing in either file says which. Guessing would attach this run's
    records to an adoption whose preimages are somebody else's, so the run
    refuses and names both ids instead -- and says what to do about it,
    because `init` is what the session hook runs and a refusal with no way
    out leaves the project with no runnable command at all.
    """
    harness_memory = tmp_path / "harness" / "memory"
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert (
        run_cli(
            "init", "--harness-memory", str(harness_memory), cwd=adopter
        ).returncode
        == 0
    )
    vault = adopter / ".validated-memory" / "local.jsonl"
    foreign = "f" * 16
    vault.write_text(
        "".join(
            json.dumps({**json.loads(line), "adoption": foreign}, sort_keys=True)
            + "\n"
            for line in vault.read_text(encoding="utf-8").splitlines()
        ),
        encoding="utf-8",
    )
    mine = _records(adopter / "journal.jsonl")[0]["adoption"]

    result = run_cli("init", cwd=adopter)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "adoption" in result.stderr, result.stderr
    assert foreign in result.stderr, result.stderr
    assert mine in result.stderr, result.stderr
    assert "restore" in result.stderr, result.stderr
    assert "adopt afresh" in result.stderr, result.stderr


# --- a journal that is there is never treated as one that is not --------------


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
def test_a_journal_that_cannot_be_read_is_a_finding_not_a_traceback(
    run_cli, tmp_path
):
    """Present but unreadable is a refusal, not an absence and not a crash.

    `Path.exists()` raises on a permission denial rather than answering,
    so the check for a missing journal was itself the thing that crashed --
    a stack trace where every other refusal in this CLI is a rendered
    finding.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    vault = tmp_path / ".validated-memory"
    vault.chmod(0o000)
    try:
        result = run_cli("journal", cwd=tmp_path)
    finally:
        vault.chmod(0o755)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "ERROR" in result.stderr, result.stderr
    assert ".validated-memory/local.jsonl" in result.stderr, result.stderr
    assert "record(s), 1 error(s)" in result.stdout, result.stdout


def test_a_journal_symlinked_to_a_regular_file_is_read_and_appended_to(
    run_cli, tmp_path
):
    """A link that resolves is an ordinary journal, read and written through.

    Keeping `journal.jsonl` in a store outside the project and linking it
    back is a setup nothing here has a reason to refuse: `append` opens the
    name and writes through the link, and `bootstrap` never reaches its
    install, because the file the link resolves to already has records.
    Refusing because the NAME is not a regular file takes a working
    adoption away over a question about the link rather than about the
    bytes that are actually read.
    """
    store = tmp_path / "store"
    store.mkdir()
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert run_cli("init", cwd=adopter).returncode == 0
    link = adopter / "journal.jsonl"
    kept = store / "journal.jsonl"
    link.rename(kept)
    link.symlink_to(kept)
    before = _records(kept)
    # A re-run over an untouched tree keeps every item and records nothing,
    # so one scaffold file is removed: its `create` is what proves the
    # append reached the store through the link.
    (adopter / "validated-memory.md").unlink()

    reported = run_cli("journal", cwd=adopter)
    again = run_cli("init", cwd=adopter)

    assert reported.returncode == 0, reported.stderr
    counted = f"journal: {len(before)} record(s)"
    assert counted in reported.stdout, reported.stdout
    assert again.returncode == 0, again.stderr
    after = _records(kept)
    assert link.is_symlink(), "the adopter's link was replaced"
    assert len(after) > len(before), "nothing was appended through the link"
    assert {entry["adoption"] for entry in after} == {before[0]["adoption"]}


def test_a_journal_that_is_a_broken_symlink_is_never_replaced(
    run_cli, tmp_path
):
    """A broken symlink at `journal.jsonl` is the adopter's; `init` keeps it.

    A broken symlink reads as absent (there is nothing to read through it),
    so the journal looks missing: `init` would mint a second adoption id
    and install over the link, which `os.replace` destroys rather than
    follows. That is exactly the trade `init.BROKEN_SYMLINK` refuses
    everywhere else -- never destroy what the adopter put there -- so the
    refusal lives where the replacement would happen, not where the reading
    does.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    journal.unlink()
    journal.symlink_to(tmp_path / "nowhere" / "journal.jsonl")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "journal.jsonl" in result.stderr, result.stderr
    assert journal.is_symlink(), "the adopter's symlink was replaced"
    assert not journal.exists(), "the symlink was followed and written through"


# --- the transaction log, and the fault seam ------------------------------


def test_journal_check_reports_a_readable_open_transaction(run_cli, tmp_path):
    """A hand-written transaction file, in `_open_transaction`'s own field shape.

    No caller opens a transaction until Task 4's executor exists, so this is
    the contract the reader (`_open_transactions`, exercised through
    `journal --check`) and Task 4's writer have to agree on. The exact
    shape is `_open_transaction`'s docstring, in `validated_memory/journal.py`.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    adoption = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])[
        "adoption"
    ]

    transactions = tmp_path / ".validated-memory" / "transactions"
    transactions.mkdir(parents=True, exist_ok=True)
    entry = {
        "schema": 1,
        "at": "2026-09-01T00:00:00Z",
        "version": "1.6.0",
        "adoption": adoption,
        "run": "7777777777777777",
        "transaction": "aaaaaaaaaaaaaaaa",
        "intention": {
            "op": "replace",
            "purpose": "init",
            "path": "validated-memory.md",
            "durability": "repo",
        },
        "preimage": {"kind": "file", "digest": "sha256:" + "0" * 64, "mode": 420},
        "postimage": {"kind": "file", "digest": "sha256:" + "1" * 64, "mode": 420},
        "preimage_blob": "sha256:" + "0" * 64,
        "mode": 420,
        "stage": "prepared",
    }
    (transactions / "aaaaaaaaaaaaaaaa.json").write_text(
        json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8"
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        "open transaction aaaaaaaaaaaaaaaa (prepared) on validated-memory.md"
        in result.stderr
    ), result.stderr
    read_back = len(_records(journal))
    assert (
        f"journal: {read_back} record(s), 1 error(s)" in result.stdout
    ), result.stdout


def test_journal_check_reports_a_damaged_transaction_file(run_cli, tmp_path):
    """A transaction file that is not valid JSON is reported, never a traceback.

    Same promise `read` already makes for the two journals: the id is
    named, the file is called out as damaged, and the pass over every other
    unresolved transaction continues rather than raising out of `--check`.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    transactions = tmp_path / ".validated-memory" / "transactions"
    transactions.mkdir(parents=True, exist_ok=True)
    (transactions / "bbbbbbbbbbbbbbbb.json").write_text(
        "{not json", encoding="utf-8"
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "bbbbbbbbbbbbbbbb" in result.stderr, result.stderr
    assert "damaged" in result.stderr, result.stderr


def test_journal_reports_unresolved_transactions_only_when_nonzero(
    run_cli, tmp_path
):
    """Without `--check`, the second line appears only when there is one to say.

    `journal` never gates on its own (`test_journal_reports_the_log_and_exits_clean`),
    and a transaction count is no exception: the summary below stays exit 0
    whether or not the second line prints.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0

    clean = run_cli("journal", cwd=tmp_path)
    assert clean.returncode == 0, clean.stdout
    assert "unresolved transaction" not in clean.stdout, clean.stdout

    transactions = tmp_path / ".validated-memory" / "transactions"
    transactions.mkdir(parents=True, exist_ok=True)
    (transactions / "cccccccccccccccc.json").write_text(
        "{not json", encoding="utf-8"
    )

    result = run_cli("journal", cwd=tmp_path)

    assert result.returncode == 0, result.stdout
    assert "journal: 1 unresolved transaction(s)" in result.stdout, result.stdout


def _transactions(root):
    """Every transaction file left in a tree's vault, newest name last."""
    directory = root / ".validated-memory" / "transactions"
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def test_a_kill_at_after_transaction_leaves_the_path_untouched(
    run_cli, tmp_path, monkeypatch
):
    """The fault seam, proven live: a kill with the write-ahead entry fsynced.

    `.gitignore` is the first mutation a fresh adopter's `init` performs
    (`_ensure_ignored` runs before the scaffold, and `_write_entry` calls
    `append_text`), so this is the earliest `after-transaction` point a
    plain `init` reaches.

    What the kill leaves is the shape design §3 asks for and §5 explains:
    the write-ahead log knows what was intended, and the permanent history
    -- which is versioned, and holds consummated facts only -- says nothing
    at all. Nothing was published, so there is nothing on disk for the
    history to have described.
    """
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-transaction")
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 70, (result.returncode, result.stdout, result.stderr)
    assert not (tmp_path / ".gitignore").exists()
    records = _records(tmp_path / "journal.jsonl")
    assert not [e for e in records if e["path"] == ".gitignore"], records

    open_transactions = _transactions(tmp_path)
    assert len(open_transactions) == 1, open_transactions
    entry = open_transactions[0]
    assert entry["stage"] == "prepared", entry
    assert entry["intention"]["path"] == ".gitignore", entry
    assert entry["intention"]["op"] == "append", entry

    # `os._exit` skips every `finally`, including `Lock.__exit__`, so the
    # lock file this run took is still there, with the pid of a process that
    # no longer exists inside it. It is left exactly where the kill left it:
    # `journal --check` reads and takes no lock, and the next run that does
    # take one breaks a lock whose owner is gone
    # (`test_a_lock_whose_owner_is_gone_is_broken_at_once`).
    assert (tmp_path / ".validated-memory" / "lock").exists()

    checked = run_cli("journal", "--check", cwd=tmp_path)
    assert checked.returncode == 1, checked.stdout
    assert ".gitignore" in checked.stderr, checked.stderr
    assert f"open transaction {entry['transaction']} (prepared)" in checked.stderr

    _recovers_to_exactly_one_pair(run_cli, tmp_path, ".gitignore", monkeypatch)


def test_a_kill_at_after_publish_leaves_bytes_the_transaction_has_not_claimed(
    run_cli, tmp_path, monkeypatch
):
    """A kill between publication and the marker that records it.

    The narrowest window in the protocol, and the one that makes the
    `published` marker worth writing at all: the bytes are on disk and the
    transaction still says `prepared`, so nothing in the write-ahead log
    asserts that the mutation ran. Recovery answers it from the filesystem
    instead -- the path matches the postimage, and the postimage is a state
    the executor's no-op rule guarantees is distinguishable from the
    preimage, so "it ran" is a reading and not a guess.
    """
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-publish")
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 70, (result.returncode, result.stdout, result.stderr)
    ignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "/.validated-memory/" in ignore, ignore
    records = _records(tmp_path / "journal.jsonl")
    assert not [e for e in records if e["path"] == ".gitignore"], records

    open_transactions = _transactions(tmp_path)
    assert len(open_transactions) == 1, open_transactions
    assert open_transactions[0]["stage"] == "prepared", open_transactions

    _recovers_to_exactly_one_pair(run_cli, tmp_path, ".gitignore", monkeypatch)


def test_a_kill_at_after_history_leaves_records_the_transaction_outlived(
    run_cli, tmp_path, monkeypatch
):
    """A kill between the two history records and the transaction's removal.

    This is the residue the idempotency rule exists for, and the only one
    where recovery must write NOTHING: the mutation is already in the
    permanent history, in full, and appending the pair again would double a
    mutation in an append-only versioned file where nothing takes it back.
    The transaction id in each record is what makes the check possible.
    """
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-history")
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 70, (result.returncode, result.stdout, result.stderr)
    open_transactions = _transactions(tmp_path)
    assert len(open_transactions) == 1, open_transactions
    entry = open_transactions[0]
    assert entry["stage"] == "published", entry

    written = [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record.get("transaction") == entry["transaction"]
    ]
    assert {record["stage"] for record in written} == {
        "prepared",
        "committed",
    }, written

    _recovers_to_exactly_one_pair(run_cli, tmp_path, ".gitignore", monkeypatch)


def _recovers_to_exactly_one_pair(run_cli, tree, path, monkeypatch):
    """The next run recovers `path` to one record pair; the one after adds none.

    The acceptance every fault point shares. "Exactly one pair" is the
    whole of it: zero would be the mutation the history never admits to,
    and two would be the doubled record recovery must not append over a
    crash it has already completed once. The third run is the idempotency
    half -- recovery over a residue it already cleared has nothing left to
    find, and a run that keeps appending on every session start is the
    failure this file's oldest tests were written against.

    Returns the records, so a caller can assert more about them.
    """
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT", raising=False)
    recovered = run_cli("init", cwd=tree)
    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    assert not _transactions(tree), _transactions(tree)

    checked = run_cli("journal", "--check", cwd=tree)
    assert checked.returncode == 0, (checked.stdout, checked.stderr)

    records = _records(tree / "journal.jsonl")
    pair = [
        record
        for record in records
        if record["path"] == path and record["op"] != "observe"
    ]
    assert len(pair) == 2, pair
    assert {record["stage"] for record in pair} == {"prepared", "committed"}, pair
    # Asserted before the comparison, so two records carrying no id at all
    # cannot pass this as agreement.
    assert pair[0]["transaction"], pair
    assert pair[0]["transaction"] == pair[1]["transaction"], pair

    again = run_cli("init", cwd=tree)
    assert again.returncode == 0, again.stderr
    assert _records(tree / "journal.jsonl") == records
    return records


def test_a_kill_at_after_published_leaves_bytes_with_no_history(
    run_cli, tmp_path, monkeypatch
):
    """A kill after the bytes are on disk and the transaction says so.

    This is the state the marker exists for. The bytes are published and
    the history has not been written, and the transaction file -- fsynced
    as `published` before the records are appended -- is what tells a later
    recovery that the mutation happened, rather than leaving it to infer it
    from a filesystem some later run may have changed.

    The recovery this residue feeds is asserted below, by the shared
    helper; what is asserted here is the residue itself.
    """
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-published")
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 70, (result.returncode, result.stdout, result.stderr)
    ignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "/.validated-memory/" in ignore, ignore
    records = _records(tmp_path / "journal.jsonl")
    assert not [e for e in records if e["path"] == ".gitignore"], records

    open_transactions = _transactions(tmp_path)
    assert len(open_transactions) == 1, open_transactions
    entry = open_transactions[0]
    assert entry["stage"] == "published", entry
    assert entry["intention"]["path"] == ".gitignore", entry

    # Left where the kill left it, as in the sibling test above: nothing
    # removes a dead owner's lock by hand, and the next run to take one
    # breaks it.
    assert (tmp_path / ".validated-memory" / "lock").exists()

    checked = run_cli("journal", "--check", cwd=tmp_path)
    assert checked.returncode == 1, checked.stdout
    assert ".gitignore" in checked.stderr, checked.stderr
    assert f"open transaction {entry['transaction']} (published)" in checked.stderr

    recovered = _recovers_to_exactly_one_pair(
        run_cli, tmp_path, ".gitignore", monkeypatch
    )
    # The two records recovery rebuilt are filed under the run that wrote
    # the bytes, not the run that found them: the mutation happened in the
    # run the kill ended, and a record saying otherwise would put a write
    # in a session that performed none.
    rebuilt = [
        item
        for item in recovered
        if item["path"] == ".gitignore" and item["op"] != "observe"
    ]
    assert rebuilt[0]["run"] == entry["run"], (rebuilt, entry)
    assert rebuilt[0]["transaction"] == entry["transaction"], (rebuilt, entry)


def test_the_fault_variable_is_inert_when_unset_or_unreached(
    run_cli, tmp_path, monkeypatch
):
    """`VALIDATED_MEMORY_FAULT`, unset or naming a point this run never reaches,
    changes nothing: the same `init`, byte for byte, on both sides.

    `run_cli` copies `os.environ` for the subprocess, so `monkeypatch` here
    reaches it. `after-prepared` is wired into `prepare_op` alone, which
    only the harness symlink still reaches (`init._record_symlink`), so a
    plain `init` with no `--harness-memory` never gets there -- which is
    exactly the "set to a point that is never reached" half of this test.
    """
    baseline = tmp_path / "baseline"
    unreached = tmp_path / "unreached"
    baseline.mkdir()
    unreached.mkdir()

    monkeypatch.delenv("VALIDATED_MEMORY_FAULT", raising=False)
    control = run_cli("init", cwd=baseline)

    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-prepared")
    faulted = run_cli("init", cwd=unreached)

    assert control.returncode == 0, control.stderr
    assert faulted.returncode == 0, faulted.stderr
    assert control.stdout == faulted.stdout
    assert control.stderr == faulted.stderr

    def _stripped(path):
        # `at`, `adoption`, `run` and `transaction` are the only fields two
        # independent runs can never agree on -- all four are minted per
        # run or per mutation; everything else -- op, purpose, path, stage,
        # preimage, postimage, mode -- is what "identical journals" means
        # here.
        return [
            {
                k: v
                for k, v in entry.items()
                if k not in ("at", "adoption", "run", "transaction")
            }
            for entry in _records(path)
        ]

    assert _stripped(baseline / "journal.jsonl") == _stripped(
        unreached / "journal.jsonl"
    )


# --- the executor: what it refuses, what it preserves, what it records ---------


def test_a_read_only_file_is_refused_and_left_exactly_as_it_was(run_cli, tmp_path):
    """The read-only bit is how an adopter says do not write here.

    Measured on shipped 1.5.2 (design §1): a `.gitignore` at mode 0444
    holding `build/` came back at 0644 and 276 bytes, with
    `0 error(s), 0 warning(s)` and exit 0. `os.replace` needs write
    permission on the DIRECTORY, not on the file, so nothing in the install
    path ever consulted the bit, and the temporary carried a fresh mode over
    the adopter's.

    Now it is a refusal (§7), and a refusal that reaches the user: the ERROR
    names the file and its mode, the bytes are the adopter's own, and the
    mode is untouched.
    """
    before = "build/\n"
    ignore = tmp_path / ".gitignore"
    ignore.write_text(before, encoding="utf-8")
    ignore.chmod(0o444)

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert ".gitignore" in result.stderr, result.stderr
    # The whole sentence, because a refusal that does not say what was left
    # alone is a refusal the reader cannot act on. The mode is printed with
    # four digits so that a mode below 0o100 reads as `0040` and not `040`;
    # no path `init` can reach produces one, since a file the process cannot
    # read never gets this far, so the width is a guard rather than a
    # behaviour this test can drive.
    assert (
        ".gitignore is mode 0444, which denies writing to this user. "
        "Nothing has been written." in result.stderr
    ), result.stderr
    assert ignore.read_text(encoding="utf-8") == before
    assert oct(ignore.stat().st_mode & 0o777) == "0o444"
    # A refusal before anything was prepared writes nothing anywhere
    # (design §5): not a record, not a transaction file, not a preimage.
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == ".gitignore"
    ]
    assert not _transactions(tmp_path)
    assert not (tmp_path / ".validated-memory" / "preimages").exists()


def test_a_writable_file_keeps_the_mode_the_adopter_gave_it(run_cli, tmp_path):
    """A file `init` may write comes back with its own mode, not a fresh one.

    0640 is writable by its owner, so the append happens; what it must not
    do is hand back 0644, which is what a temporary created under the
    default umask carries. §7: the install copies the target's mode onto
    the temporary before the rename.
    """
    ignore = tmp_path / ".gitignore"
    ignore.write_text("build/\n", encoding="utf-8")
    ignore.chmod(0o640)

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "/.validated-memory/" in ignore.read_text(encoding="utf-8")
    assert oct(ignore.stat().st_mode & 0o777) == "0o640"
    # And the mode it kept is what the record says it kept, so a reversal
    # has something to restore.
    committed = next(
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == ".gitignore" and entry["stage"] == "committed"
    )
    assert committed["mode"] == 0o640, committed


def test_a_plain_file_where_a_directory_goes_is_refused_not_kept(run_cli, tmp_path):
    """An expected state is a precondition, and `exists()` is not one.

    Through 1.5.2 a regular file at `memory/` was reported `kept` and
    journalled as `observe: directory already present` -- a permanent,
    uninvertible claim about the pre-adoption state, written about
    something that is not a directory at all. The intention expects the
    name to be absent, the executor finds a file, and a mismatch "writes
    nothing anywhere, because at this point there is no transaction to
    abort".
    """
    placeholder = tmp_path / "memory"
    placeholder.write_text("notes\n", encoding="utf-8")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "memory: create" in result.stderr, result.stderr
    assert "expects it to be absent" in result.stderr, result.stderr
    assert "init: kept memory" not in result.stdout, result.stdout
    assert placeholder.read_text(encoding="utf-8") == "notes\n"
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == "memory"
    ]
    assert not _transactions(tmp_path)


def test_both_records_of_one_mutation_carry_one_transaction_id(run_cli, tmp_path):
    """The two halves of one act are recognisable as one act, for ever.

    The transaction file is local and short-lived -- it leaves the disk as
    soon as the mutation resolves -- so the id is the only thing that
    survives to say these two records are one mutation and not two. Design
    §3: the write-ahead log "is not history and must never grow without
    bound"; the history is where the pairing has to keep working.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0

    records = [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["op"] != "observe"
    ]
    assert records, "init recorded no mutation"
    by_stage = {}
    for entry in records:
        by_stage.setdefault(entry["path"], {})[entry["stage"]] = entry
    for path, stages in by_stage.items():
        assert set(stages) == {"prepared", "committed"}, (path, stages)
        assert stages["prepared"]["transaction"], (path, stages)
        assert (
            stages["prepared"]["transaction"] == stages["committed"]["transaction"]
        ), (path, stages)
        # And the mode the path ended up with, on both halves.
        assert isinstance(stages["committed"]["mode"], int), stages

    # One id per mutation, never one per run: two mutations of one run that
    # shared an id could not be told apart either.
    ids = {stages["committed"]["transaction"] for stages in by_stage.values()}
    assert len(ids) == len(by_stage), by_stage

    # Every transaction the run opened was resolved, so none is left behind.
    assert not _transactions(tmp_path)
    assert run_cli("journal", "--check", cwd=tmp_path).returncode == 0


def test_a_path_left_by_an_open_transaction_is_never_observed_as_pre_existing(
    run_cli, tmp_path, monkeypatch
):
    """A path the plugin created, with nothing in the history naming it.

    The kill lands after publication and before the two records are
    appended, so `knowledge/` is on disk and BOTH journals are silent about
    it -- the state `_seen` cannot learn from the history, because there is
    no record to learn from. Reading only the histories would observe it as
    a fact about the state adoption found: the permanent, uninvertible lie
    commit `4ce59a9` removed. The unresolved transaction file is what makes
    the next run safe.

    `.gitignore` already carries the rule, so the first mutation the run
    reaches is `knowledge/` rather than the ignore entry.
    """
    (tmp_path / ".gitignore").write_text("/.validated-memory/\n", encoding="utf-8")

    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-published")
    killed = run_cli("init", cwd=tmp_path)
    assert killed.returncode == 70, (killed.stdout, killed.stderr)
    assert (tmp_path / "knowledge").is_dir()
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == "knowledge"
    ]

    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "init: kept knowledge" in result.stdout, result.stdout
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["op"] == "observe" and entry["path"] == "knowledge"
    ], _records(tmp_path / "journal.jsonl")


def test_a_corrupt_preimage_blob_is_replaced_rather_than_wedging_the_run(
    run_cli, tmp_path
):
    """A blob whose bytes disagree with its own name is worthless, not fatal.

    The store is content-addressed: the filename IS the digest, so bytes
    that do not hash to it can only be a corrupt earlier park or an edit,
    and no reader anywhere can ever want them. Refusing on sight would wedge
    the adoption -- the dedup skips re-parking a name that exists, so every
    later run would read the same bad bytes and refuse again, for ever, over
    a file nothing else will repair. The bytes to replace it with are in
    hand at exactly that moment, so it is replaced.
    """
    import hashlib

    before = "build/\n"
    ignore = tmp_path / ".gitignore"
    ignore.write_text(before, encoding="utf-8")
    reference = hashlib.sha256(before.encode("utf-8")).hexdigest()
    preimages = tmp_path / ".validated-memory" / "preimages"
    preimages.mkdir(parents=True)
    blob = preimages / reference
    blob.write_text("not the preimage at all\n", encoding="utf-8")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert blob.read_text(encoding="utf-8") == before
    # The mutation the preimage was taken for went through and is recorded
    # against the blob that now holds the true bytes.
    committed = next(
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == ".gitignore" and entry["stage"] == "committed"
    )
    assert committed["preimage"] == f"sha256:{reference}", committed
    # No pid-named temporary is left in the store either.
    assert sorted(p.name for p in preimages.iterdir()) == [reference]


def test_a_symlink_where_a_directory_goes_is_refused_not_kept(run_cli, tmp_path):
    """The same family as the plain file above: `is_dir()` is the question.

    A symlink to a file at `memory/` resolves, so `exists()` was true and
    1.5.2 reported it `kept` and journalled "directory already present". It
    is not a directory, every command that reads the layout fails on it, and
    the observation would be a permanent claim about the pre-adoption state
    that is simply false. It is now the same ERROR, and nothing is recorded
    -- above all, `init` does not replace the adopter's link.
    """
    (tmp_path / "elsewhere.md").write_text("notes\n", encoding="utf-8")
    (tmp_path / "memory").symlink_to(tmp_path / "elsewhere.md")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "memory: create" in result.stderr, result.stderr
    assert "expects it to be absent" in result.stderr, result.stderr
    assert "init: kept memory" not in result.stdout, result.stdout
    assert (tmp_path / "memory").is_symlink()
    assert (tmp_path / "elsewhere.md").read_text(encoding="utf-8") == "notes\n"
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == "memory"
    ]
    assert not _transactions(tmp_path)


# A third party that is not this plugin and does not wait its turn: it
# watches for the run to start parking a preimage and then replaces the file
# under it, atomically, so the run can never read a half-written state.
# Standard library only, and it imports nothing from the package.
OVERWRITE_WHILE_PARKING = """
import os
import sys
import time

trigger, target, text = sys.argv[1], sys.argv[2], sys.argv[3]
deadline = time.monotonic() + 60
while not os.path.exists(trigger):
    if time.monotonic() > deadline:
        raise SystemExit("the run never parked a preimage")
    time.sleep(0.0002)
temporary = target + ".intruder"
with open(temporary, "w", encoding="utf-8") as handle:
    handle.write(text)
os.replace(temporary, target)
"""

# Big enough that parking it -- write, fsync, install, read back and verify
# -- takes long enough for the writer above to land inside the window the
# re-read exists to close. Nothing about the guarantee depends on the size;
# only this test's ability to reach the window does, and design §6 is
# explicit that what the re-read buys is "a narrower window, not an atomic
# guarantee".
INTRUDER_WINDOW_BYTES = 8 * 1024 * 1024


def test_the_state_is_re_read_immediately_before_publishing(run_cli, tmp_path):
    """A file that changed after the expected-state check is not overwritten.

    The check at the start of the protocol is not enough on its own: the
    preimage is copied and fsynced and the transaction file is written and
    fsynced after it, and an adopter's editor writing in that window would
    be overwritten by a mutation whose record names a preimage that was
    already gone. So the state is read again, under the same lock,
    immediately before publication (design §6), and a mismatch closes the
    transaction `aborted` rather than publishing.

    The intruder here is a real second process racing a real run; it wins
    the race because the file it is racing is large enough that parking it
    takes time. What it proves is the refusal, not the width of the window:
    §6 says plainly that a third party writing in the remaining gap between
    the re-read and the rename is not detected at all.
    """
    ignore = tmp_path / ".gitignore"
    ignore.write_text("build/\n" + "# filler\n" * (INTRUDER_WINDOW_BYTES // 9),
                      encoding="utf-8")
    intruder_text = "build/\ndist/\n"

    intruder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            OVERWRITE_WHILE_PARKING,
            str(tmp_path / ".validated-memory" / "preimages"),
            str(ignore),
            intruder_text,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        result = run_cli("init", cwd=tmp_path)
    finally:
        intruder.wait(timeout=90)
    assert intruder.returncode == 0, intruder.communicate()

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert ".gitignore" in result.stderr, result.stderr
    assert "changed while its mutation was being prepared" in result.stderr
    # The intruder's bytes, exactly: not the original, and not the original
    # with the ignore entry appended to it.
    assert ignore.read_text(encoding="utf-8") == intruder_text
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == ".gitignore"
    ]
    # The transaction was opened, so it is closed `aborted` and removed --
    # not left open for a recovery that has nothing to recover.
    assert not _transactions(tmp_path)


def test_a_creation_publishes_with_o_excl_rather_than_replacing():
    """The no-replace guarantee for a creation is the primitive, not a check.

    Design §6 promises "a strong no-replace guarantee for a creation,
    because the primitive exists: create with `O_CREAT|O_EXCL` and fail if
    the name is taken", and says why check-then-`os.replace` is not that
    promise: a third party creating the file between the re-read and the
    rename would be overwritten, and the history would say `create`.

    This pin is structural, and that is a statement about the guarantee, not
    a shortcut. What separates `O_EXCL` from `os.replace` lives entirely
    inside the gap between two adjacent syscalls -- narrower than any seam
    this suite has, and the same gap `authorise` documents as one "this
    project's test seam cannot demonstrate". A behavioural test could only
    reach the expected-state check, which is a different guarantee with its
    own test above; every mechanism that CAN be observed from outside stays
    green when `O_EXCL` is swapped for a rename. So the flags are pinned
    where they are written.
    """
    tree = ast.parse(
        (REPO_ROOT / "validated_memory" / "journal.py").read_text(encoding="utf-8")
    )
    publish = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_publish"
    )
    # The call that publishes, identified by what it opens: `_publish` opens
    # a temporary as well, and pinning "somewhere in this function" would go
    # green on the temporary's flags while the publication replaced whatever
    # it found.
    opens = [
        node
        for node in ast.walk(publish)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "os.open"
        and node.args
    ]
    published = [node for node in opens if ast.unparse(node.args[0]) == "target"]
    assert len(published) == 1, [ast.unparse(node) for node in opens]
    flags = ast.unparse(published[0].args[1])
    assert "os.O_CREAT" in flags and "os.O_EXCL" in flags, (
        "a creation must be published with os.O_CREAT | os.O_EXCL, which "
        f"fails when the name is taken; this one opens with {flags}"
    )

    # And the temporary a replacement is built in is created 0600, so an
    # adopter's bytes are never briefly readable by anyone the target's own
    # mode excludes. Same kind of guarantee, same reason it is pinned here:
    # the window is the length of one write.
    staged = [node for node in opens if ast.unparse(node.args[0]) == "temporary"]
    assert len(staged) == 1, [ast.unparse(node) for node in opens]
    assert ast.literal_eval(staged[0].args[2]) == 0o600, ast.unparse(staged[0])


# --- the lock: who holds it, who may break it, and where it lives -------------

# A lock holder that is not this plugin: it takes the lock file exactly as
# `Lock` does -- `O_CREAT | O_EXCL`, its own pid inside -- announces the pid
# it wrote, and then stays alive until the test kills it. Standard library
# only, and it imports nothing from the package: these tests drive the CLI
# from the outside and this holder is part of the outside.
HOLD_THE_LOCK = """
import os
import sys
import time

path = sys.argv[1]
os.makedirs(os.path.dirname(path), exist_ok=True)
descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
os.write(descriptor, ("%d\\n" % os.getpid()).encode("ascii"))
os.close(descriptor)
print(os.getpid(), flush=True)
time.sleep(600)
"""


def _hold_the_lock(path):
    """Start the holder above on `path` and return it, already holding."""
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_THE_LOCK, str(path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    pid = holder.stdout.readline().strip()
    assert pid, "the lock holder died before it took the lock"
    return holder, pid


def _a_pid_that_is_gone():
    """A pid nothing owns, asked of the system rather than assumed.

    A child that has exited and been reaped gives up its pid, so that is the
    first candidate; the walk upwards covers the case where the kernel has
    already handed it out again by the time this is asked.
    """
    child = subprocess.Popen([sys.executable, "-c", ""])
    child.wait(timeout=30)
    reaped = child.pid
    for offset in range(10000):
        candidate = reaped + offset
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except OSError:
            continue
    raise AssertionError("every probed pid was in use")


def _run_init_in_background(cwd):
    """Start `init` as a subprocess the test can interfere with while it runs."""
    environment = dict(os.environ)
    environment.setdefault("PYTHONPATH", str(REPO_ROOT))
    return subprocess.Popen(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=environment,
    )


def test_init_takes_the_lock_it_already_holds(run_cli, tmp_path):
    """The nested acquisition: `init` holds the lock, `Run` takes it again.

    `init.run` wraps the whole run in one lock and `journal.Run.__init__`
    takes it again around its reads and `bootstrap`. Without re-entrancy the
    inner one is a second `O_CREAT | O_EXCL` create against a file this same
    process holds: it waits out the ten-second deadline and then refuses, so
    every session start would exit 1 on a lock nobody else wants.
    """
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert "another validated-memory process holds" not in result.stderr
    assert (tmp_path / "journal.jsonl").exists()
    # Taken and released, not leaked: the next run must not have to break it.
    assert not (tmp_path / ".validated-memory" / "lock").exists()


def test_a_lock_whose_owner_is_alive_is_never_broken(run_cli, tmp_path):
    """An age-old lock whose pid is still running is waited on, not taken.

    This test costs the whole contention deadline -- about ten seconds -- on
    purpose: refusing is what takes that long, and there is no shorter way
    to see the run give up rather than break in.

    The mtime is backdated far past `STALE_LOCK_SECONDS`, which is exactly
    the state the old age-only rule broke: a live holder in the middle of a
    slow mutation would have had its lock taken from under it.
    """
    lock = tmp_path / ".validated-memory" / "lock"
    holder, pid = _hold_the_lock(lock)
    try:
        ancient = time.time() - 100 * 300
        os.utime(lock, (ancient, ancient))
        before = lock.stat().st_ino

        result = run_cli("init", cwd=tmp_path)

        assert result.returncode == 1, (result.stdout, result.stderr)
        assert "another validated-memory process holds" in result.stderr
        assert os.path.realpath(lock) in result.stderr, result.stderr
        # An alive pid is never broken, and a pid the system has since
        # handed to something else is still alive: the message has to say
        # what an operator can do about a lock nothing will ever release.
        assert (
            f"if no validated-memory process is running, delete "
            f"{os.path.realpath(lock)}" in result.stderr
        ), result.stderr
        # The finding names the lock itself. Inside the root, that is the
        # relative path a reader can act on directly.
        assert result.stderr.startswith(
            "ERROR: .validated-memory/lock: journal: "
        ), result.stderr
        # The holder's own file, untouched: same inode, same pid inside.
        assert lock.stat().st_ino == before
        assert lock.read_text(encoding="ascii").strip() == pid
        # And the run that was refused mutated nothing.
        assert not (tmp_path / "journal.jsonl").exists()
        assert not (tmp_path / ".gitignore").exists()
    finally:
        holder.terminate()
        holder.wait(timeout=30)
        holder.stdout.close()


def test_a_lock_whose_owner_is_gone_is_broken_at_once(run_cli, tmp_path):
    """A lock file that outlived its owner is broken now, not in five minutes.

    A run killed between taking the lock and releasing it leaves the file
    behind with its pid inside. Under the age-only rule the next `init` --
    the one a session start runs -- waited ten seconds and then failed, and
    kept failing until the file was five minutes old. The mtime here is
    fresh, so age alone would refuse.
    """
    lock = tmp_path / ".validated-memory" / "lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(f"{_a_pid_that_is_gone()}\n", encoding="ascii")

    started = time.monotonic()
    result = run_cli("init", cwd=tmp_path)
    elapsed = time.monotonic() - started

    assert result.returncode == 0, (result.stdout, result.stderr)
    # Well inside the ten-second deadline: broken on the first attempt, not
    # waited on and then given up.
    assert elapsed < 5, elapsed
    assert (tmp_path / "journal.jsonl").exists()
    assert not lock.exists()


def test_a_run_whose_lock_was_broken_leaves_its_successor_alone(tmp_path):
    """Release identifies the lock by inode, so a broken run deletes nothing.

    Driving this needs a run that is still holding the lock a while after
    taking it. The lock file appears when `init.run` takes the run-wide
    lock, which is the first thing it does, so what the padded journal
    below buys is wall-clock INSIDE that outer lock -- about a second of
    reading, in `journal.Run`'s reads and everything after them -- not a
    window inside any one call. The test waits for the file, then swaps it.

    A lock broken while its owner is still running -- the age horizon does
    exactly that to a lock whose pid cannot be read -- is the case the
    identity check exists for, and this stages it: the file the run created
    is removed, a successor takes its place, and the run must finish
    without unlinking a lock that is no longer its own.

    The successor's pid is this test's, which is alive, so nothing in the
    run under test may break it either.
    """
    seed = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        check=False,
    )
    assert seed.returncode == 0, seed.stderr
    journal = tmp_path / "journal.jsonl"
    line = journal.read_text(encoding="utf-8").splitlines()[0]
    with journal.open("a", encoding="utf-8") as handle:
        handle.write((line + "\n") * 150000)

    lock = tmp_path / ".validated-memory" / "lock"
    assert not lock.exists()
    running = _run_init_in_background(tmp_path)
    try:
        deadline = time.monotonic() + 30
        while not lock.exists():
            assert running.poll() is None, "the run ended before it took the lock"
            assert time.monotonic() < deadline, "the run never took the lock"
            time.sleep(0.005)
        broken = lock.stat().st_ino
        lock.unlink()
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        successor = lock.stat().st_ino
        assert successor != broken
        assert running.poll() is None, "the run finished before the lock was swapped"
        stdout, stderr = running.communicate(timeout=120)
    finally:
        if running.poll() is None:  # pragma: no cover - only on a timeout
            running.kill()
            running.communicate()

    assert running.returncode == 0, (stdout, stderr)
    assert lock.exists(), "the run deleted a lock it no longer owned"
    assert lock.stat().st_ino == successor
    assert lock.read_text(encoding="ascii").strip() == str(os.getpid())


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
def test_two_trees_sharing_one_journal_take_one_lock(run_cli, tmp_path):
    """A `journal.jsonl` symlinked into a store locks beside the store.

    Two adopter trees pointing at one journal used to take two different
    local locks and serialise nothing, which is the whole point of the lock
    gone. Both runs below are made to fail at the lock by taking the write
    permission off the store's vault directory, so each one names the lock
    file it actually tried to create -- and both name the store's.
    """
    store = tmp_path / "store"
    first = tmp_path / "first"
    second = tmp_path / "second"
    for tree in (store, first, second):
        tree.mkdir()

    seeded = run_cli("init", cwd=first)
    assert seeded.returncode == 0, seeded.stderr
    (first / "journal.jsonl").rename(store / "journal.jsonl")
    for tree in (first, second):
        (tree / "journal.jsonl").symlink_to(store / "journal.jsonl")

    shared_vault = store / ".validated-memory"
    shared_vault.mkdir()
    os.chmod(shared_vault, 0o500)  # read + execute: no lock can be created
    try:
        refusals = {tree: run_cli("init", cwd=tree) for tree in (first, second)}
    finally:
        os.chmod(shared_vault, 0o700)

    shared_lock = str(shared_vault / "lock")
    for tree, result in refusals.items():
        assert result.returncode == 1, (tree, result.stdout, result.stderr)
        assert shared_lock in result.stderr, (tree, result.stderr)
    # And neither tree fell back to a lock of its own.
    assert not (first / ".validated-memory" / "lock").exists()
    assert not (second / ".validated-memory").exists()


def test_a_broken_journal_symlink_locks_inside_the_root(run_cli, tmp_path):
    """A link that resolves to nothing creates nothing outside the root.

    `journal.jsonl` may be a symlink into a shared store, and the lock
    follows it -- but only as far as a regular file. A broken link (a
    directory is the same case) has no store behind it to serialise
    against, and following it would put a `.validated-memory/` directory
    somewhere the adopter never named. The journal itself is refused, as it
    already was, by `bootstrap`.
    """
    root = tmp_path / "adopter"
    elsewhere = tmp_path / "elsewhere"
    root.mkdir()
    (root / "journal.jsonl").symlink_to(elsewhere / "journal.jsonl")

    result = run_cli("init", cwd=root)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "symlink" in result.stderr, result.stderr
    assert not elsewhere.exists(), "the lock was taken outside the adopter root"
    assert (root / ".validated-memory").is_dir()


def test_a_journal_symlink_that_cannot_be_resolved_refuses_cleanly(
    run_cli, tmp_path
):
    """A link pointing at itself is refused by the reader, not by a crash.

    Choosing where the lock goes resolves `journal.jsonl`, and resolving a
    symlink loop raises `RuntimeError` -- which is not `OSError`, so nothing
    in `init` catches it and the whole command would end in a traceback
    before the journal was ever read. The lock stays home when the name
    cannot be resolved, and the run reaches the refusal `read` already had
    for a journal it cannot open.
    """
    (tmp_path / "journal.jsonl").symlink_to("journal.jsonl")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert "journal could not be read" in result.stderr, result.stderr
    assert "journal.jsonl" in result.stderr, result.stderr


# --- recovery: what a run does with what an earlier run left open -------------


def _diverged(tree, before=None, kill_after="an adopter wrote this\n"):
    """Build a residue nothing but an operator can decide.

    The kill lands with the bytes on disk and the transaction fsynced
    `published`, and the adopter's own write afterwards is what makes the
    path match neither of the two states the transaction names. Returns the
    transaction id.

    `before` is what `.gitignore` holds when `init` starts, so a caller that
    needs a real preimage blob in the vault -- everything `--restore`
    touches -- can ask for one. `kill_after` is that intruding write, and
    `None` skips it: a caller whose tree already ignores the vault gets a
    transaction on `knowledge/` instead, which `--check` calls recoverable
    and `--resolve` closes just the same.
    """
    if before is not None:
        (tree / ".gitignore").write_text(before, encoding="utf-8")
    environment = dict(os.environ, VALIDATED_MEMORY_FAULT="after-published")
    killed = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        capture_output=True,
        text=True,
        cwd=tree,
        env={**environment, "PYTHONPATH": str(REPO_ROOT)},
        check=False,
    )
    assert killed.returncode == 70, (killed.stdout, killed.stderr)
    if kill_after is not None:
        (tree / ".gitignore").write_text(kill_after, encoding="utf-8")
    open_transactions = _transactions(tree)
    assert len(open_transactions) == 1, open_transactions
    return open_transactions[0]["transaction"]


def _created_then_diverged(tree):
    """A killed `create` of a file, then an adopter's own bytes over it.

    The residue whose preimage is `absent`: its inverse is removal, not
    bytes, which is the half of `--restore` that has something to discard
    and nothing to put back. A complete `init` runs first and one file is
    taken away, so the killed run's first and only mutation is that file's
    `create` -- the ignore entry is already there and every other item is a
    no-op. Returns the transaction id.
    """
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    first = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        capture_output=True, text=True, cwd=tree, env=environment, check=False,
    )
    assert first.returncode == 0, (first.stdout, first.stderr)
    (tree / "knowledge-extension.md").unlink()
    killed = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        capture_output=True,
        text=True,
        cwd=tree,
        env={**environment, "VALIDATED_MEMORY_FAULT": "after-published"},
        check=False,
    )
    assert killed.returncode == 70, (killed.stdout, killed.stderr)
    open_transactions = _transactions(tree)
    assert len(open_transactions) == 1, open_transactions
    assert open_transactions[0]["preimage"] == {"kind": "absent"}, open_transactions
    assert open_transactions[0]["intention"]["path"] == "knowledge-extension.md"
    return open_transactions[0]["transaction"]


def _transaction_file(tree, transaction_id, **overrides):
    """Hand-write one transaction file, in `_open_transaction`'s field shape.

    The same fixture `test_journal_check_reports_a_readable_open_transaction`
    builds, factored out because the classification has four answers and
    three of them need a residue no kill produces on demand.
    """
    adoption = _records(tree / "journal.jsonl")[0]["adoption"]
    entry = {
        "schema": 1,
        "at": "2026-09-01T00:00:00Z",
        "version": "1.6.0",
        "adoption": adoption,
        "run": "7777777777777777",
        "transaction": transaction_id,
        "intention": {
            "op": "replace",
            "purpose": "init",
            "path": "validated-memory.md",
            "durability": "repo",
        },
        "preimage": {"kind": "file", "digest": "sha256:" + "0" * 64, "mode": 420},
        "postimage": {"kind": "file", "digest": "sha256:" + "1" * 64, "mode": 420},
        "preimage_blob": "sha256:" + "0" * 64,
        "mode": 420,
        "prior_bytes": None,
        "stage": "prepared",
    }
    entry.update(overrides)
    directory = tree / ".validated-memory" / "transactions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{transaction_id}.json").write_text(
        json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8"
    )
    return entry


def test_recovery_leaves_a_transaction_it_cannot_account_for_untouched(
    run_cli, tmp_path
):
    """Idempotency for the half of the table recovery may not resolve.

    A diverged transaction is the one thing a run must NOT clear away: the
    file is what keeps the path gated and the evidence available until
    someone decides. So two runs over it produce the same ERROR, the same
    file, and the same history -- a recovery that appended a line per
    session would be the versioned refusal design §3 refuses, one per
    session start, for ever.
    """
    transaction = _diverged(tmp_path)
    residue = (
        tmp_path / ".validated-memory" / "transactions" / f"{transaction}.json"
    ).read_text(encoding="utf-8")

    first = run_cli("init", cwd=tmp_path)
    records = _records(tmp_path / "journal.jsonl")
    second = run_cli("init", cwd=tmp_path)

    assert first.returncode == 1, (first.stdout, first.stderr)
    assert second.returncode == 1, (second.stdout, second.stderr)
    assert transaction in first.stderr, first.stderr
    assert first.stderr == second.stderr, (first.stderr, second.stderr)
    assert _records(tmp_path / "journal.jsonl") == records
    assert (
        tmp_path / ".validated-memory" / "transactions" / f"{transaction}.json"
    ).read_text(encoding="utf-8") == residue


def test_only_the_path_a_transaction_names_is_gated(
    run_cli, tmp_path, monkeypatch
):
    """The rest of the run proceeds, which is narrower than design §8.

    A group spans paths and cannot be reasoned about piecewise, so §8 blocks
    every mutating command while one is open. A single-path transaction can,
    and blocking everything would brick the session hook over one stale
    file: everything but `knowledge/` is created.

    `.gitignore` already carries the rule, so the first mutation the killed
    run reaches is `knowledge/` -- the ignore entry is the one item whose
    own ERROR gates the whole scaffold on its own (`init._ensure_ignored`),
    which would hide the narrowing this test is about.
    """
    (tmp_path / ".gitignore").write_text("/.validated-memory/\n", encoding="utf-8")
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-published")
    assert run_cli("init", cwd=tmp_path).returncode == 70
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
    # The directory the kill published is taken away, so the transaction's
    # postimage describes a state that is no longer there and recovery can
    # say neither that the mutation happened nor that it did not.
    (tmp_path / "knowledge").rmdir()
    transaction = _transactions(tmp_path)[0]["transaction"]

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (tmp_path / "memory" / "MEMORY.md").exists()
    assert (tmp_path / "validated-memory.md").exists()
    assert "init: created memory" in result.stdout, result.stdout
    assert not (tmp_path / "knowledge").exists()
    assert (
        f"has an unresolved transaction {transaction}" in result.stderr
    ), result.stderr
    for flag in ("--accept", "--restore", "--abandon"):
        assert flag in result.stderr, result.stderr


def test_journal_check_says_what_recovery_would_do_with_each_transaction(
    run_cli, tmp_path
):
    """Four words, one per row of the table, and `--check` writes nothing.

    The classification is `_classify`'s, which is also what `Run.recover`
    acts on, so what `--check` promises and what the next run does cannot
    drift apart. `recoverable` covers complete, discard and remove alike:
    `--check` asks one question -- would a run clear this away by itself?
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    # `published`, and the path is the postimage: recovery completes it.
    _transaction_file(
        tmp_path,
        "1111111111111111",
        stage="published",
        postimage={
            "kind": "file",
            "digest": _records(tmp_path / "journal.jsonl")[-1]["postimage"],
        },
        intention={
            "op": "create",
            "purpose": "init",
            "path": "knowledge-extension.md",
            "durability": "repo",
        },
    )
    # `published`, and the path is something else entirely.
    _transaction_file(
        tmp_path,
        "2222222222222222",
        stage="published",
        intention={
            "op": "replace",
            "purpose": "init",
            "path": "memory/MEMORY.md",
            "durability": "repo",
        },
    )
    # `prepared`, and the path matches neither state.
    _transaction_file(tmp_path, "3333333333333333")
    (tmp_path / ".validated-memory" / "transactions" / "4444444444444444.json").write_text(
        "{not json", encoding="utf-8"
    )
    before = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        "open transaction 1111111111111111 (published) on "
        "knowledge-extension.md: recoverable" in result.stderr
    ), result.stderr
    assert (
        "open transaction 2222222222222222 (published) on "
        "memory/MEMORY.md: diverged" in result.stderr
    ), result.stderr
    assert (
        "open transaction 3333333333333333 (prepared) on "
        "validated-memory.md: unknown" in result.stderr
    ), result.stderr
    assert "damaged transaction 4444444444444444:" in result.stderr, result.stderr
    assert "journal: 13 record(s), 4 error(s)" in result.stdout, result.stdout
    # Read-only: nothing was completed, discarded or removed.
    assert (tmp_path / "journal.jsonl").read_text(encoding="utf-8") == before
    left = sorted(
        entry.name
        for entry in (tmp_path / ".validated-memory" / "transactions").iterdir()
    )
    assert len(left) == 4, left


def test_an_aborted_transaction_is_reported_and_removed(run_cli, tmp_path):
    """`aborted` is closed already: recovery unlinks it and records nothing.

    Design §5: an `aborted` entry "is never inverted by a reversal, and
    disappears with the log once resolved". It is not a problem the operator
    has to answer for -- the decision was made and nothing was published --
    so the run that finds it clears it and carries on clean.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    _transaction_file(
        tmp_path,
        "5555555555555555",
        stage="aborted",
        reason="validated-memory.md changed while its mutation was prepared",
    )
    before = _records(tmp_path / "journal.jsonl")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not _transactions(tmp_path), _transactions(tmp_path)
    assert _records(tmp_path / "journal.jsonl") == before
    assert "5555555555555555" not in result.stdout, result.stdout
    assert run_cli("journal", "--check", cwd=tmp_path).returncode == 0


def test_journal_resolve_accept_records_an_observation_never_a_mutation(
    run_cli, tmp_path
):
    """`--accept`: the state found is a fact, not something the plugin did.

    A `replace` record here would claim the plugin produced these bytes and
    would offer a reversal that undoes somebody else's work. What is true is
    that a state was found and accepted, which is what `observe` means -- and
    the note says which transaction found what, because this is the one
    `observe` written about a path the journal already mentions.
    """
    transaction = _diverged(tmp_path)

    result = run_cli("journal", "--resolve", transaction, "--accept", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == "", result.stderr
    assert f"journal: resolved {transaction} (--accept)" in result.stdout
    assert not _transactions(tmp_path), _transactions(tmp_path)

    written = [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record["path"] == ".gitignore"
    ]
    assert len(written) == 1, written
    assert written[0]["op"] == "observe", written
    assert written[0]["stage"] == "committed", written
    assert written[0]["note"] == (
        f"accepted after divergence: transaction {transaction} found file"
    ), written
    # The path is untouched, and the run that follows is clean.
    assert (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    ) == "an adopter wrote this\n"
    assert run_cli("journal", "--check", cwd=tmp_path).returncode == 0


def test_journal_resolve_abandon_records_that_the_path_was_left_as_found(
    run_cli, tmp_path
):
    """`--abandon`: nothing is published and nothing is undone, and it says so."""
    transaction = _diverged(tmp_path)

    result = run_cli("journal", "--resolve", transaction, "--abandon", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert f"journal: resolved {transaction} (--abandon)" in result.stdout
    assert not _transactions(tmp_path), _transactions(tmp_path)
    written = [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record["path"] == ".gitignore"
    ]
    assert len(written) == 1, written
    assert written[0]["op"] == "observe", written
    assert written[0]["note"] == (
        f"abandoned: transaction {transaction}, path left as found"
    ), written
    assert (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    ) == "an adopter wrote this\n"


def test_journal_resolve_restore_puts_the_preimage_back_and_records_nothing(
    run_cli, tmp_path
):
    """`--restore`: the adopter's own bytes and mode, through the same publication.

    Nothing is recorded, because a path returned to the state a record would
    have described the departure from is not a fact about the project. The
    mode comes from the transaction file -- the preimage's own -- and not
    from whatever is at the path now, which here is the intruding write.
    """
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / ".gitignore").chmod(0o640)
    transaction = _diverged(tmp_path, before="build/\n")
    before = _records(tmp_path / "journal.jsonl")

    result = run_cli("journal", "--resolve", transaction, "--restore", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert f"journal: resolved {transaction} (--restore)" in result.stdout
    assert not _transactions(tmp_path), _transactions(tmp_path)
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "build/\n"
    assert (tmp_path / ".gitignore").stat().st_mode & 0o777 == 0o640
    assert _records(tmp_path / "journal.jsonl") == before
    # And the path is writable again: the next run ignores the vault.
    assert run_cli("init", cwd=tmp_path).returncode == 0
    assert "/.validated-memory/" in (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    )


def test_journal_resolve_restore_refuses_a_blob_that_is_not_the_preimage(
    run_cli, tmp_path
):
    """An OPEN transaction's blob is the only copy: a bad one is a damaged log.

    Design §10 keeps the two cases apart, and so must this. For a CLOSED
    history record a missing blob is normal -- the journal travels and the
    vault does not -- and means only that this clone cannot reverse that
    mutation. Here the transaction is open, the blob was parked and verified
    moments before the crash, and bytes that disagree with the name they are
    filed under are not the preimage: `--restore` refuses rather than
    writing something else over the adopter's path.
    """
    transaction = _diverged(tmp_path, before="build/\n")
    preimages = tmp_path / ".validated-memory" / "preimages"
    blob = next(iter(preimages.iterdir()))
    blob.write_text("not the bytes that were parked\n", encoding="utf-8")

    mismatched = run_cli(
        "journal", "--resolve", transaction, "--restore", cwd=tmp_path
    )

    assert mismatched.returncode == 1, mismatched.stdout
    assert "does not digest to" in mismatched.stderr, mismatched.stderr
    assert "Nothing has been restored." in mismatched.stderr, mismatched.stderr
    assert (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    ) == "an adopter wrote this\n"
    assert len(_transactions(tmp_path)) == 1, _transactions(tmp_path)

    blob.unlink()
    missing = run_cli("journal", "--resolve", transaction, "--restore", cwd=tmp_path)

    assert missing.returncode == 1, missing.stdout
    assert "is not in .validated-memory/preimages/" in missing.stderr, missing.stderr
    assert "damaged log" in missing.stderr, missing.stderr
    assert len(_transactions(tmp_path)) == 1, _transactions(tmp_path)


def test_journal_resolve_restore_refuses_once_the_mutation_is_history(
    run_cli, tmp_path, monkeypatch
):
    """A `committed` record means it happened, and history is not taken back.

    The kill lands after both records are appended and before the
    transaction is removed, so the mutation is in a versioned append-only
    file. Restoring the bytes without removing the record -- which cannot be
    removed -- would leave the journal describing a state that is not there.
    """
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-history")
    assert run_cli("init", cwd=tmp_path).returncode == 70
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
    (tmp_path / ".gitignore").write_text("an adopter wrote this\n", encoding="utf-8")
    transaction = _transactions(tmp_path)[0]["transaction"]

    result = run_cli("journal", "--resolve", transaction, "--restore", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "already recorded in journal.jsonl" in result.stderr, result.stderr
    assert "--accept or --abandon" in result.stderr, result.stderr
    assert len(_transactions(tmp_path)) == 1, _transactions(tmp_path)
    assert (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    ) == "an adopter wrote this\n"

    accepted = run_cli("journal", "--resolve", transaction, "--accept", cwd=tmp_path)
    assert accepted.returncode == 0, (accepted.stdout, accepted.stderr)
    assert run_cli("journal", "--check", cwd=tmp_path).returncode == 0


def test_journal_resolve_refuses_an_id_no_transaction_carries(run_cli, tmp_path):
    """An unknown id is an ERROR about this project's state, not a usage error.

    The id was well formed and the flag was legal; what is missing is the
    transaction, which is a fact about the tree. So it gates (exit 1) and
    names the command that lists the ones there are, rather than printing a
    usage line the operator has no way to act on.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0

    result = run_cli(
        "journal", "--resolve", "deadbeefdeadbeef", "--accept", cwd=tmp_path
    )

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "no unresolved transaction deadbeefdeadbeef" in result.stderr
    assert "Nothing has been changed." in result.stderr, result.stderr


def test_journal_resolve_needs_exactly_one_of_the_three_flags(run_cli, tmp_path):
    """Every other combination is a usage error, because none has one meaning.

    Guessing at one would close a transaction on terms the operator did not
    choose, and `--check` is read-only, so pairing it with the one mode that
    writes is a contradiction rather than a preference. An id that is empty
    or nothing but spaces is the same kind of fault: there is no transaction
    it could be a mistyping of.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0

    for arguments in (
        ("--resolve", "aaaaaaaaaaaaaaaa"),
        ("--resolve", "aaaaaaaaaaaaaaaa", "--accept", "--abandon"),
        ("--accept",),
        ("--check", "--resolve", "aaaaaaaaaaaaaaaa", "--accept"),
        # An empty id reaches no transaction and names none in the refusal
        # either, so it is a malformed command line and not a fact about
        # the project: exit 2, like every other way of mistyping this.
        ("--resolve", "", "--accept"),
        ("--resolve", "   ", "--accept"),
    ):
        result = run_cli("journal", *arguments, cwd=tmp_path)
        assert result.returncode == 2, (arguments, result.stdout, result.stderr)
        assert "usage: validated-memory journal" in result.stderr, arguments


def test_a_missing_preimage_blob_for_a_closed_record_is_never_an_error(
    run_cli, tmp_path
):
    """The vault does not travel, and a clone without one is not damaged.

    Design §10: "a missing preimage blob in a clone is normal, not
    corruption". This is exactly what a fresh clone looks like -- the
    versioned journal carries a `replace` record naming a preimage, and the
    ignored vault that held the bytes is not there -- and nothing recovery
    or resolution added may report it. The refusal in
    `test_journal_resolve_restore_refuses_a_blob_that_is_not_the_preimage`
    is the OTHER case, where the transaction is still open.
    """
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    assert run_cli("init", cwd=tmp_path).returncode == 0
    replaced = [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record["path"] == ".gitignore" and record.get("preimage")
    ]
    assert replaced, "init recorded no preimage for the ignore file"
    for blob in (tmp_path / ".validated-memory" / "preimages").iterdir():
        blob.unlink()

    checked = run_cli("journal", "--check", cwd=tmp_path)
    again = run_cli("init", cwd=tmp_path)

    assert checked.returncode == 0, (checked.stdout, checked.stderr)
    assert "preimage" not in checked.stderr, checked.stderr
    assert again.returncode == 0, (again.stdout, again.stderr)
    assert again.stderr == "", again.stderr


def test_two_halves_of_one_transaction_that_disagree_are_reported(
    run_cli, tmp_path
):
    """Strict pairing by id, and field agreement on top of it (design §13).

    The two records are the only evidence a mutation left behind, so a
    reader that averages them is a reader inventing a third mutation. The
    disagreement is reported and never resolved by preferring one half --
    `journal.jsonl` is repository content, and a hand edit or a bad merge is
    exactly how one half comes to say something the other does not.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    path = tmp_path / "journal.jsonl"
    rewritten = []
    for record in _records(path):
        if record["path"] == "validated-memory.md" and record["stage"] == "committed":
            record = dict(record, mode=0o600)
            transaction = record["transaction"]
        rewritten.append(json.dumps(record, sort_keys=True))
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        f"records of transaction {transaction} disagree on mode" in result.stderr
    ), result.stderr
    assert "validated-memory.md" in result.stderr, result.stderr
    assert "journal: 13 record(s), 1 error(s)" in result.stdout, result.stdout


def test_journal_resolve_restore_keeps_the_bytes_it_discards(run_cli, tmp_path):
    """`--restore` throws a state away by the operator's choice, never bytes.

    The preimage here is `absent`, so putting it back means taking the path
    away -- and what is at the path is an adopter's own writing, which this
    command has no copy of anywhere. So it is parked into the same
    content-addressed, verified store the executor parks into before it is
    discarded, and the success line names the blob: a copy nobody can find
    is not a copy.

    This is what keeps the two directions symmetric. The file branch parks
    what it overwrites for exactly the same reason, and the difference
    between them -- which is which of the two states survives -- is the
    operator's to choose, not this command's to be careless about.
    """
    transaction = _created_then_diverged(tmp_path)
    intruder = "the adopter's own words\n"
    (tmp_path / "knowledge-extension.md").write_text(intruder, encoding="utf-8")
    digest = hashlib.sha256(intruder.encode("utf-8")).hexdigest()
    before = _records(tmp_path / "journal.jsonl")

    result = run_cli("journal", "--resolve", transaction, "--restore", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not (tmp_path / "knowledge-extension.md").exists()
    blob = tmp_path / ".validated-memory" / "preimages" / digest
    assert blob.read_text(encoding="utf-8") == intruder
    assert (
        f"journal: resolved {transaction} (--restore); the discarded bytes "
        f"are kept at .validated-memory/preimages/{digest}" in result.stdout
    ), result.stdout
    # A copy in the vault is not a record: nothing was appended.
    assert _records(tmp_path / "journal.jsonl") == before
    assert not _transactions(tmp_path), _transactions(tmp_path)


def test_journal_resolve_restore_takes_away_what_an_absent_preimage_names(
    run_cli, tmp_path
):
    """The other half of `--restore`: putting back "nothing was here".

    A `create` expects the path to be absent, so its preimage is `absent`
    and its inverse is removal. A directory there is `rmdir` and never a
    recursive delete: whatever is inside it was put there by something this
    transaction knows nothing about, so a non-empty one refuses, leaves its
    contents and leaves the transaction open. A directory has no bytes of
    its own, which is why nothing is parked and the success line says
    nothing about a copy.
    """
    transaction = _created_then_diverged(tmp_path)
    # The path diverged into something that is not a file at all.
    (tmp_path / "knowledge-extension.md").unlink()
    (tmp_path / "knowledge-extension.md").mkdir()
    (tmp_path / "knowledge-extension.md" / "left-behind.md").write_text(
        "kept\n", encoding="utf-8"
    )

    occupied = run_cli("journal", "--resolve", transaction, "--restore", cwd=tmp_path)

    assert occupied.returncode == 1, occupied.stdout
    assert "could not be put back" in occupied.stderr, occupied.stderr
    assert (tmp_path / "knowledge-extension.md" / "left-behind.md").exists()
    assert len(_transactions(tmp_path)) == 1, _transactions(tmp_path)

    (tmp_path / "knowledge-extension.md" / "left-behind.md").unlink()
    result = run_cli("journal", "--resolve", transaction, "--restore", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not (tmp_path / "knowledge-extension.md").exists()
    assert "discarded bytes" not in result.stdout, result.stdout
    assert not _transactions(tmp_path), _transactions(tmp_path)
    assert not [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record["path"] == "knowledge-extension.md"
        and record["op"] == "observe"
    ]


def test_a_transaction_the_next_run_resolves_is_not_an_operator_s_to_close(
    run_cli, tmp_path
):
    """The three flags are for what recovery cannot account for, and no more.

    A `published` transaction whose path still matches its postimage is a
    mutation the next `init` completes: it appends the two records and
    removes the file. Closing it by hand would throw that pair away for
    ever -- `--accept` and `--abandon` would write an `observe` about a path
    the PLUGIN created, which is the permanent, uninvertible lie the whole
    of §4 exists to rule out, and `--restore` would undo a mutation nothing
    had finished recording.

    So all three refuse, and the refusal names the command that does resolve
    it. The transaction is left exactly as it was found.
    """
    transaction = _diverged(tmp_path, kill_after=None)
    before = _records(tmp_path / "journal.jsonl")
    residue = (
        tmp_path / ".validated-memory" / "transactions" / f"{transaction}.json"
    ).read_text(encoding="utf-8")

    for flag in ("--accept", "--abandon", "--restore"):
        result = run_cli("journal", "--resolve", transaction, flag, cwd=tmp_path)

        assert result.returncode == 1, (flag, result.stdout, result.stderr)
        assert "is recoverable" in result.stderr, (flag, result.stderr)
        assert "validated-memory init" in result.stderr, (flag, result.stderr)
        assert "Nothing has been changed." in result.stderr, (flag, result.stderr)
        assert _records(tmp_path / "journal.jsonl") == before, flag
        assert (
            tmp_path / ".validated-memory" / "transactions" / f"{transaction}.json"
        ).read_text(encoding="utf-8") == residue, flag

    # And the run that IS its resolution finishes it.
    assert run_cli("init", cwd=tmp_path).returncode == 0
    assert not _transactions(tmp_path), _transactions(tmp_path)
