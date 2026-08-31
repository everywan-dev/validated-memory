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
RECORDING_METHODS = {"observe", "write", "append_op", "append"}
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
