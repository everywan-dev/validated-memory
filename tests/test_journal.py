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
import re
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
# Qualified `os` mutations. Path.replace is detected separately by its
# one-argument shape so ordinary string replacement does not count.
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
# The journal's atomic install is a mutation when called from outside it.
JOURNAL_MUTATORS = {"install"}

# Require a journal receiver so unrelated append/write calls cannot count.
RECORDERS = {"session", "journal"}
# The complete Run method allowlist. Module exports are pinned separately.
PERMITTED_RUN_METHODS = ("execute", "observe", "recover", "resolve_transaction")
RECORDING_METHODS = set(PERMITTED_RUN_METHODS)

# Paths relative to validated_memory identify modules; basenames do not.
JOURNAL_SOURCE = "journal"

# Explicit modules allowed raw writes. Other journal modules remain scanned;
# new modules do not inherit an exemption from their directory.
RAW_WRITE_MODULES = {
    "journal/durable.py",
    "journal/executor.py",
    "journal/lock.py",
    "journal/records.py",
    "journal/transactions.py",
}


def _inside_journal(relative):
    """Whether `relative` is the journal's own source, or a module of it."""
    return relative == JOURNAL_SOURCE or relative.startswith(JOURNAL_SOURCE + "/")

# --- the two exception sets, and why they are two ------------------------------
#
# Keys are (relative module path, function); `*` covers a module. Each entry
# carries its reason because the two sets permit different behavior.

# Approved adopter-tree mutations outside the executor.
EXECUTOR_EXCEPTIONS = {
    ("init.py", "relink"): (
        "the fail-open harness link repair: the contract requires the link "
        "back when the journal cannot be read or written at all, which is "
        "the SessionStart hook's only job, and an executor that requires a "
        "working journal cannot serve it "
        "(docs/design/2026-09-01-the-journal-core.md §4). This closure is "
        "the whole of it -- `_sync_symlink`, which builds it, mutates "
        "nothing itself and so is not listed"
    ),
    ("adopt.py", "take_over"): (
        "the harness absorption: it recognises a tree, copies "
        "conditionally, reconciles an index and renames the source, and "
        "tolerates a per-file conflict -- which needs its own planner "
        "before the executor can apply it "
        "(docs/design/2026-09-01-the-journal-core.md §4)"
    ),
    ("adopt.py", "_absorb"): "the same absorption, one function of it",
    ("adopt.py", "_reconcile_index"): "the same absorption, one function of it",
    ("adopt.py", "_park"): "the same absorption, one function of it",
}

# Writes that reach no journal because what they write is not adopter data:
# a derived artifact its own command regenerates, or another append-only log.
# These are not executor exceptions -- there is nothing about them for the
# executor to own.
UNRECORDED_WRITES = {
    ("render.py", "*"): "writes only derived artifacts, which `render` rebuilds",
    ("derive.py", "*"): "writes only derived artifacts, which `derive` rebuilds",
    ("init.py", "_ensure_views"): (
        "`init --view` builds the same derived artifacts `render` does"
    ),
    ("verdicts.py", "append"): (
        "`verdicts.jsonl` is the other append-only log: `probe` writes it "
        "and re-running `probe` rebuilds it, which is exactly what a "
        "journal is not (see `docs/reference/journal.md`, \"What is "
        "recorded, and what is not yet\")"
    ),
}

# Private protocol spellings matched as text, including prose. `prepare_op`
# and `append_op` are retained as anti-reintroduction guards for the removed
# two-record API; the remaining names cover the current private write path.
#
# `_bootstrap` is absent because the symbol scan below catches it precisely.
# `record`, `append` and `install` are ordinary English and Python, so a text
# scan would reject unrelated prose or calls. The symbol and call scans below
# pin them without that false-positive surface.
PRIVATE_JOURNAL_NAMES = (
    "prepare_op",
    "append_op",
    "open_transaction",
    "mark_published",
    "abort_transaction",
    "remove_transaction_file",
    "_write_transaction_file",
    "write_denied",
    "_park_preimage",
    "_publish",
)

# Complete top-level export allowlist. Failure messages are built from this
# tuple so the advertised and enforced surfaces cannot drift.
PERMITTED_JOURNAL_EXPORTS = (
    "ABSENT",
    "FILE",
    "JOURNAL_FILENAME",
    "JournalError",
    "LOCAL",
    "Lock",
    "OUTCOME_APPLIED",
    "OUTCOME_NOOP",
    "OUTCOME_REFUSED",
    "RECOVERED",
    "REPO",
    "RESOLUTIONS",
    "Run",
    "SYMLINK",
    "VAULT_DIRNAME",
    "append_to_file",
    "create_directory",
    "create_file",
    "digest",
    "link_to",
    "run",
)

# These ordinary names are matched as calls to avoid prose false positives.
PRIVATE_JOURNAL_CALLS = ("record", "install")


def _writing_mode(call):
    """Recognize literal write modes; unknown expressions count as writes.

    Account for the different mode positions in open(path, mode) and
    path.open(mode); an omitted mode is read-only."""
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
    return (
        isinstance(function, ast.Attribute)
        and function.attr in RECORDING_METHODS
        and isinstance(function.value, ast.Name)
        and function.value.id in RECORDERS
    )


def _scopes(tree):
    """Return (name, direct calls) for each function and the module.

    Nested functions are independent scopes. Duplicate function names share
    an exception, including identically named closures or methods; this
    deliberately over-covers rather than resolving that ambiguity."""
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    def _own(node):
        """The calls made directly in `node`, not inside a function within it."""
        nested = {
            id(inner)
            for child in ast.walk(node)
            if child is not node
            and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            for inner in ast.walk(child)
        }
        return [
            call
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and id(call) not in nested
        ]

    scopes = [(function.name, _own(function)) for function in functions]
    scopes.append(("<module>", _own(tree)))
    return scopes


def _records(path):
    """Every JSON record in a `.jsonl` file, in file order."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_init_writes_a_journal_whose_records_carry_the_common_fields(
    run_cli, tmp_path
):
    """Pin nonempty attribution fields, schema and operation vocabulary."""
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
    """Rerunning init keeps the same single adoption ID."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    first = {entry["adoption"] for entry in _records(tmp_path / "journal.jsonl")}
    assert len(first) == 1, first

    assert run_cli("init", cwd=tmp_path).returncode == 0
    second = {entry["adoption"] for entry in _records(tmp_path / "journal.jsonl")}
    assert second == first, (first, second)


def test_each_invocation_groups_its_records_under_one_run_id(run_cli, tmp_path):
    """Removing an item forces the second run to record under a new run ID."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    (tmp_path / "knowledge-extension.md").unlink()

    assert run_cli("init", cwd=tmp_path).returncode == 0

    runs = [entry["run"] for entry in _records(tmp_path / "journal.jsonl")]
    assert len(set(runs)) == 2, runs


def test_a_journal_that_cannot_be_parsed_is_refused_with_its_line(run_cli, tmp_path):
    """Malformed JSON gates init with the journal and parse fault identified."""
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
    """Pin file postimage digests and matching prepared/committed sequences."""
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

    # Each mutation contributes matching prepared and committed op/path
    # sequences. This filtered comparison does not pin their interleaving.
    # Only `observe` stands alone because it records a fact, not a mutation.
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
    """Distinguish the directory present before adoption from created paths."""
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
    """Two unchanged reruns leave journal bytes identical: no re-observations."""
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
    """Pin the directory history pair and stage order, not write-ahead timing.

    Both history records follow publication. Fault-seam tests exercise the
    observable recovery residues, not transaction fsync ordering."""
    assert run_cli("init", cwd=tmp_path).returncode == 0

    records = _records(tmp_path / "journal.jsonl")
    stages = [
        e["stage"] for e in records if e["path"] == "memory" and e["op"] == "create"
    ]
    assert stages == ["prepared", "committed"], records


def test_a_path_the_journal_already_knows_is_never_observed_as_pre_existing(
    run_cli, tmp_path
):
    """Removing the committed half leaves an open mutation, not an observation.

    The next init must not claim the directory predated adoption; --check
    must still report its applied, unfinished mutation."""
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
    """A valid log prints its count and exits 0; malformed logs can still gate."""
    assert run_cli("init", cwd=tmp_path).returncode == 0

    result = run_cli("journal", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "journal:" in result.stdout, result.stdout
    assert "record(s)" in result.stdout, result.stdout


def test_journal_check_reconciles_an_unfinished_transaction(run_cli, tmp_path):
    """An unmatched prepared record reports a diverged, unfinished mutation."""
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
    """Pin applied, unapplied and unknown; the preceding test pins diverged.

    Distinct orphan run IDs prevent pairing with init history. A directory
    makes the file unreadable even for root, unlike permission bits."""
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
    """A broken symlink does not satisfy an expected directory.

    The legacy create record without image digests denotes mkdir. Testing
    exists() or is_symlink() would falsely classify this path as applied."""
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
    """Appending the ignore entry parks the original bytes under their digest.

    Repeating that append pins deduplication by blob name and inode, not
    merely equal content. This test does not establish fsync ordering."""
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
    """A second prepared half cannot reuse the first write's committed half.

    Sharing run and path must not let set-based pairing hide interruption."""
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


# --- a journal is data, never instructions -------------------------------
# (docs/design/2026-08-30-the-journal-coverage-and-reversal-design.md §7)


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
    """A string schema is valid JSON but gates all three commands cleanly."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    _append_record(journal, _record(journal, schema="1"))

    for arguments in (("journal",), ("journal", "--check"), ("init",)):
        result = run_cli(*arguments, cwd=tmp_path)

        assert result.returncode == 1, (arguments, result.stdout)
        assert "Traceback" not in result.stderr, (arguments, result.stderr)
        assert "ERROR" in result.stderr, (arguments, result.stderr)
        assert "schema" in result.stderr, (arguments, result.stderr)


def test_a_boolean_where_a_number_goes_is_refused_in_an_optional_field_too(
    run_cli, tmp_path
):
    """Reject a boolean mode even though Python treats bool as an int subtype."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    _append_record(journal, _record(journal, mode=True))

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert (
        "record field 'mode' holds bool, which it may not" in result.stderr
    ), result.stderr


def test_a_record_from_a_newer_schema_is_refused_rather_than_read_in_part(
    run_cli, tmp_path
):
    """An unknown schema gates with upgrade advice and reports zero records.

    Good lines precede the bad one: these assertions forbid a partial count,
    but do not prove refusal occurred before those lines were parsed."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    _append_record(journal, _record(journal, schema=2))

    for arguments in (("journal",), ("journal", "--check"), ("init",)):
        result = run_cli(*arguments, cwd=tmp_path)

        assert result.returncode == 1, (arguments, result.stdout)
        assert "Traceback" not in result.stderr, (arguments, result.stderr)
        assert "newer than this plugin understands" in result.stderr, (
            arguments,
            result.stderr,
        )
        assert "upgrade the plugin" in result.stderr, (arguments, result.stderr)
    assert "journal: 0 record(s)" in run_cli("journal", cwd=tmp_path).stdout


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
    """Reject an absolute repository path rather than report its content state."""
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
    """A local-durability record in the repository journal must be refused."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    _append_record(
        journal, _record(journal, durability="local", path="/etc/passwd")
    )

    result = run_cli("journal", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "durability" in result.stderr, result.stderr


def test_a_corrupt_vault_journal_is_reported_against_the_vault(run_cli, tmp_path):
    """The diagnostic identifies the corrupt local log, not journal.jsonl."""
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


def _package_modules():
    """Return sorted (relative path, path) pairs; basenames are not identities."""
    root = REPO_ROOT / "validated_memory"
    return [
        (path.relative_to(root).as_posix(), path)
        for path in sorted(root.rglob("*.py"))
    ]


def test_every_write_in_the_package_goes_through_the_journal():
    """Structurally require a recorder beside each recognized filesystem write.

    EXECUTOR_EXCEPTIONS permits specified adopter mutations without the
    executor; UNRECORDED_WRITES covers derived artifacts and the verdict log.
    RAW_WRITE_MODULES explicitly permits the journal implementation.

    This fixed vocabulary misses aliases and new write idioms. A recorder
    and mutation merely coexist in one scope: that does not prove guarding
    or ordering. Receiver matching excludes unrelated append/write calls.
    Mutation tests of known idioms establish only this vocabulary; extend
    it when the package introduces a different idiom."""
    exempt = {**EXECUTOR_EXCEPTIONS, **UNRECORDED_WRITES}
    offenders = []
    for relative, path in _package_modules():
        if relative in RAW_WRITE_MODULES or (relative, "*") in exempt:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, calls in _scopes(tree):
            if (relative, name) in exempt:
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
                    f"{relative}:{lineno}: {name} calls {mutation} "
                    "and never reaches the journal"
                )
    assert not offenders, (
        "these mutate without reaching the journal; route them through "
        "`Run.execute` or add them to EXECUTOR_EXCEPTIONS / "
        "UNRECORDED_WRITES with the reason:\n" + "\n".join(offenders)
    )


def test_no_module_outside_the_journal_reaches_past_the_executor():
    """Forbid private protocol spellings in source, including prose.

    The text denylist also prevents reintroducing prepare_op/append_op.
    Ordinary record/install names are matched as calls, not English words.
    Diagnostics use PERMITTED_JOURNAL_EXPORTS and PERMITTED_RUN_METHODS;
    aliases and unlisted spellings remain outside this structural pin."""
    offenders = []
    for relative, path in _package_modules():
        if _inside_journal(relative):
            continue
        source = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(source.splitlines(), start=1):
            for name in PRIVATE_JOURNAL_NAMES:
                if re.search(r"\b" + re.escape(name) + r"\b", line):
                    offenders.append(f"{relative}:{lineno}: names `{name}`")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            name = None
            if isinstance(called, ast.Name):
                name = called.id
            elif isinstance(called, ast.Attribute):
                name = called.attr
            if name in PRIVATE_JOURNAL_CALLS:
                offenders.append(f"{relative}:{node.lineno}: calls `{name}(...)`")
    assert not offenders, (
        "these reach past `Run.execute` into the journal's own protocol; "
        "the whole of the journal surface a module outside it may touch is "
        + ", ".join(f"`{name}`" for name in PERMITTED_JOURNAL_EXPORTS)
        + ", plus "
        + ", ".join(f"`Run.{name}`" for name in PERMITTED_RUN_METHODS)
        + ":\n"
        + "\n".join(offenders)
    )


def test_nothing_outside_the_journal_reaches_a_name_it_does_not_export():
    """Check journal imports and literal journal.X access against the exports.

    Unlike the text denylist, this rejects _bootstrap and ordinary names
    such as append as symbols, without rejecting prose. Direct submodule
    imports bypass the facade and are refused. Aliased receivers and dynamic
    access are not resolved by this structural scan."""
    exports = set(PERMITTED_JOURNAL_EXPORTS)
    offenders = []
    for relative, path in _package_modules():
        if _inside_journal(relative):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("validated_memory.journal."):
                        offenders.append(
                            f"{relative}:{node.lineno}: imports the journal "
                            f"module `{alias.name}`"
                        )
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").removeprefix("validated_memory.")
                if module.startswith("journal."):
                    offenders.append(
                        f"{relative}:{node.lineno}: imports from the journal "
                        f"module `{module}`"
                    )
                elif module == "journal":
                    for alias in node.names:
                        if alias.name not in exports:
                            offenders.append(
                                f"{relative}:{node.lineno}: imports "
                                f"`{alias.name}` from the journal"
                            )
            elif (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "journal"
                and node.attr not in exports
            ):
                offenders.append(
                    f"{relative}:{node.lineno}: names `journal.{node.attr}`"
                )
    assert not offenders, (
        "the whole of the journal a module outside it may reach is "
        + ", ".join(f"`{name}`" for name in PERMITTED_JOURNAL_EXPORTS)
        + ", plus "
        + ", ".join(f"`Run.{name}`" for name in PERMITTED_RUN_METHODS)
        + " on a `Run`; the journal is imported whole and reached by "
        "attribute, never by one of its own modules:\n"
        + "\n".join(offenders)
    )


def test_the_facade_exports_exactly_the_surface_the_pin_permits():
    """Pin sorted __all__ to PERMITTED_JOURNAL_EXPORTS without importing it.

    Run methods belong to PERMITTED_RUN_METHODS, not module-level __all__."""
    source = (
        REPO_ROOT / "validated_memory" / JOURNAL_SOURCE / "__init__.py"
    ).read_text(encoding="utf-8")
    exports = None
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            # A literal list of literal strings, so this reads it without
            # importing anything: an `__all__` built by concatenating other
            # modules' would be unreadable here, and is refused by the
            # `element.value` access below rather than silently skipped.
            exports = [element.value for element in node.value.elts]
    assert exports is not None, "journal/__init__.py declares no `__all__`"
    assert exports == sorted(exports), "`__all__` is not sorted"
    assert exports == list(PERMITTED_JOURNAL_EXPORTS), (
        "the facade and the surface this suite pins have drifted:\n"
        "  only in `__all__`: "
        f"{sorted(set(exports) - set(PERMITTED_JOURNAL_EXPORTS))}\n"
        "  only in PERMITTED_JOURNAL_EXPORTS: "
        f"{sorted(set(PERMITTED_JOURNAL_EXPORTS) - set(exports))}"
    )


# The journal's modules in the order `journal/__init__.py` lists them, which
# is also the order they may import in. The facade itself is not here: it is
# the one file that reaches every module, which is what makes it the door.
JOURNAL_LAYERS = (
    "durable",
    "records",
    "paths",
    "operations",
    "fault",
    "lock",
    "transactions",
    "executor",
    "reconcile",
    "command",
)


def test_the_journal_package_imports_only_downhill():
    """Pin module membership and static imports to JOURNAL_LAYERS.

    Relative and absolute imports are scanned even inside functions;
    dynamic imports and indirect attribute access remain invisible.
    Importing the facade by its absolute import name is also refused."""
    root = REPO_ROOT / "validated_memory" / JOURNAL_SOURCE
    present = {
        path.stem for path in sorted(root.glob("*.py")) if path.stem != "__init__"
    }
    assert present == set(JOURNAL_LAYERS), (
        "the package and the order it is pinned in have drifted:\n"
        f"  no place in the order: {sorted(present - set(JOURNAL_LAYERS))}\n"
        f"  in the order, not in the package: "
        f"{sorted(set(JOURNAL_LAYERS) - present)}"
    )
    package = f"validated_memory.{JOURNAL_SOURCE}."
    facade = package.rstrip(".")
    offenders = []
    for rank, name in enumerate(JOURNAL_LAYERS):
        tree = ast.parse((root / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            reached = []
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == facade:
                        offenders.append(
                            f"journal/{name}.py:{node.lineno}: imports the "
                            "package itself, which imports every module"
                        )
                    elif alias.name.startswith(package):
                        reached.append(alias.name.removeprefix(package))
            elif isinstance(node, ast.ImportFrom) and node.level == 1:
                reached = (
                    [alias.name for alias in node.names]
                    if node.module is None
                    else [node.module]
                )
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module or ""
                if module.startswith(package):
                    reached = [module.removeprefix(package)]
                elif module == facade:
                    reached = [alias.name for alias in node.names]
            for module in reached:
                if module in JOURNAL_LAYERS and JOURNAL_LAYERS.index(module) >= rank:
                    offenders.append(
                        f"journal/{name}.py:{node.lineno}: imports "
                        f"`{module}`, which comes after it"
                    )
    assert not offenders, (
        "these import uphill or sideways, and the facade says the package "
        "does not; the order is "
        + " -> ".join(JOURNAL_LAYERS)
        + ":\n"
        + "\n".join(offenders)
    )


def test_every_named_exception_exists_and_says_why_it_is_one():
    """Each exception must name an existing scope or module and carry a reason."""
    stale = []
    unexplained = []
    for label, entries in (
        ("EXECUTOR_EXCEPTIONS", EXECUTOR_EXCEPTIONS),
        ("UNRECORDED_WRITES", UNRECORDED_WRITES),
    ):
        for (module, function), reason in entries.items():
            path = REPO_ROOT / "validated_memory" / module
            if not path.is_file():
                stale.append(f"{label}: {module} is not a module of the package")
                continue
            if function != "*":
                defined = {
                    node.name
                    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                if function not in defined:
                    stale.append(f"{label}: {module} defines no `{function}`")
            if not reason.strip():
                unexplained.append(f"{label}: ({module}, {function})")
    assert not stale, "\n".join(stale)
    assert not unexplained, (
        "an exception with no reason is a decision nobody has to defend:\n"
        + "\n".join(unexplained)
    )


# --- the root a record names, and the root the filesystem agrees with ---------


def test_an_observation_that_escapes_the_root_through_a_symlink_is_refused(
    run_cli, tmp_path
):
    """Refuse an observation that resolves outside the adopter root.

    The assertions also pin no outside write and no false `memory` record."""
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
    """One unsafe record is unknown without hiding other unfinished records.

    Plain and checking modes must also report the same total record count."""
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
    """A corrupt local log does not erase the readable repository record count."""
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
    """A surviving local log supplies the adoption ID after journal loss."""
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
    """Conflicting adoption IDs gate with both IDs and recovery advice."""
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
    """An unreadable local log gates with its path, count and no traceback."""
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
    """A journal symlink to a regular file is read and appended through.

    The link remains a link and all resulting records keep the adoption ID."""
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
    """Init refuses a broken journal symlink without replacing or following it."""
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
    """A hand-written writer-shaped transaction is counted and named by --check.

    The fixture copies the schema manually; this test does not derive it from
    the writer."""
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
    """Invalid transaction JSON gates with its ID and no traceback."""
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
    """Plain journal prints only nonzero unresolved counts without gating on them.

    Malformed journal logs can still gate; this isolates transaction-count
    reporting by damaging only a transaction file."""
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
    """Read transactions in lexicographic filename order."""
    directory = root / ".validated-memory" / "transactions"
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def test_a_kill_at_after_transaction_leaves_the_path_untouched(
    run_cli, tmp_path, monkeypatch
):
    """After-transaction kills leave a prepared create transaction.

    The path and history stay untouched, the fault seam exits 70, and a later
    run recovers exactly one pair. This proves the staged residue observed at
    the seam, not the fsync implementation itself."""
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
    assert entry["intention"]["op"] == "create", entry

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
    """After-publish kills leave new bytes with a prepared transaction.

    No history names the mutation; recovery classifies the postimage and
    produces exactly one record pair."""
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
    """After-history kills leave a published transaction and one history pair.

    Recovery removes the residue without duplicating either record."""
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
    """Recover `path` to one ID-matched pair and prove the next run adds none."""
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


def test_recovery_completes_a_history_holding_only_the_prepared_half(
    run_cli, tmp_path, monkeypatch
):
    """Recovery appends only the missing committed half of a torn history pair.

    The rebuilt pair preserves stage order plus the original transaction and
    run IDs."""
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-history")
    assert run_cli("init", cwd=tmp_path).returncode == 70
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
    entry = _transactions(tmp_path)[0]

    journal = tmp_path / "journal.jsonl"
    lines = journal.read_text(encoding="utf-8").splitlines(keepends=True)
    last = json.loads(lines[-1])
    assert (last["path"], last["stage"]) == (".gitignore", "committed"), last
    journal.write_text("".join(lines[:-1]), encoding="utf-8")

    recovered = _recovers_to_exactly_one_pair(
        run_cli, tmp_path, ".gitignore", monkeypatch
    )
    rebuilt = [
        record
        for record in recovered
        if record["path"] == ".gitignore" and record["op"] != "observe"
    ]
    # The half that survived and the half that was appended are one act,
    # and both belong to the run that wrote the bytes.
    assert [record["stage"] for record in rebuilt] == [
        "prepared",
        "committed",
    ], rebuilt
    assert [record["transaction"] for record in rebuilt] == [
        entry["transaction"],
        entry["transaction"],
    ], rebuilt
    assert [record["run"] for record in rebuilt] == [
        entry["run"],
        entry["run"],
    ], (rebuilt, entry)


def test_init_announces_a_recovery_only_when_the_history_gained_one(
    run_cli, tmp_path, monkeypatch
):
    """Init announces recovery only when it adds missing history.

    Both published and already-recorded residues are removed and leave
    --check clean."""
    published = tmp_path / "published"
    history = tmp_path / "history"
    published.mkdir()
    history.mkdir()

    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-published")
    assert run_cli("init", cwd=published).returncode == 70
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-history")
    assert run_cli("init", cwd=history).returncode == 70
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")

    announced = run_cli("init", cwd=published)
    silent = run_cli("init", cwd=history)

    assert announced.returncode == 0, (announced.stdout, announced.stderr)
    assert silent.returncode == 0, (silent.stdout, silent.stderr)
    assert (
        "init: recovered .gitignore from transaction" in announced.stdout
    ), announced.stdout
    assert "recovered" not in silent.stdout, silent.stdout
    # Both are closed either way: what differs is what was said about them.
    assert not _transactions(published), _transactions(published)
    assert not _transactions(history), _transactions(history)
    for tree in (published, history):
        assert run_cli("journal", "--check", cwd=tree).returncode == 0


def test_a_kill_at_after_published_leaves_bytes_with_no_history(
    run_cli, tmp_path, monkeypatch
):
    """After-published kills leave bytes plus a published transaction, no history.

    The shared recovery check then pins exactly one resulting record pair."""
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
    """Unset and unreached fault points leave repeated init observably unchanged.

    Outputs match exactly. Journal comparison removes timestamps and minted
    IDs, so it proves equal operation data rather than byte-identical logs."""
    baseline = tmp_path / "baseline"
    unreached = tmp_path / "unreached"
    baseline.mkdir()
    unreached.mkdir()
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT", raising=False)
    assert run_cli("init", cwd=baseline).returncode == 0
    assert run_cli("init", cwd=unreached).returncode == 0

    control = run_cli("init", cwd=baseline)

    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-transaction")
    faulted = run_cli("init", cwd=unreached)

    assert control.returncode == 0, control.stderr
    assert faulted.returncode == 0, faulted.stderr
    assert control.stdout == faulted.stdout
    assert control.stderr == faulted.stderr

    def _stripped(path):
        # Normalize timestamps and minted identities. The comparison pins all
        # remaining operation fields, not byte-identical journal output.
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
    """A read-only target gates with exact bytes and mode left unchanged.

    Refusal writes no target record, transaction or preimage."""
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
    # No target record, transaction file or preimage accompanies the refusal.
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == ".gitignore"
    ]
    assert not _transactions(tmp_path)
    assert not (tmp_path / ".validated-memory" / "preimages").exists()


def test_a_writable_file_keeps_the_mode_the_adopter_gave_it(run_cli, tmp_path):
    """A writable target keeps its mode in both the filesystem and record."""
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
    """A file cannot satisfy a directory creation precondition.

    Init leaves the bytes unchanged and writes no observation or transaction."""
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
    """Each mutation has one nonempty ID shared by its two history halves.

    IDs remain distinct across mutations, modes are recorded, and no open
    transaction remains."""
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
    """An open transaction prevents its published path becoming an observation.

    The fixture ensures history has no record from which to infer ownership."""
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
    """Parking replaces a blob whose content does not match its digest name.

    The mutation completes against the repaired blob and leaves no temporary."""
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
    """A symlink to a file cannot satisfy a directory creation precondition.

    Init preserves the link and target and writes no record or transaction."""
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
# only this test's ability to reach the window does, and
# docs/design/2026-09-01-the-journal-core.md §6 is explicit that what the
# re-read buys is "a narrower window, not an atomic guarantee".
INTRUDER_WINDOW_BYTES = 8 * 1024 * 1024


def test_the_state_is_re_read_immediately_before_publishing(run_cli, tmp_path):
    """A raced write before the pre-publication re-read aborts the mutation.

    A second process reaches the seam by exploiting slow preimage parking.
    This proves refusal at that seam, not its width or the smaller race
    between the re-read and publication."""
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


# The same window as `OVERWRITE_WHILE_PARKING`, reached the same way, but the
# intruder takes the path away from the reader instead of changing it. The
# preimage is already in hand when this lands, so the run reaches the re-read
# with a path it can no longer `lstat` for a digest.
DENY_READS_WHILE_PARKING = """
import os
import sys
import time

trigger, target = sys.argv[1], sys.argv[2]
deadline = time.monotonic() + 60
while not os.path.exists(trigger):
    if time.monotonic() > deadline:
        raise SystemExit("the run never parked a preimage")
    time.sleep(0.0002)
os.chmod(target, 0o000)
"""


def test_a_path_that_stops_being_readable_before_publication_aborts(
    run_cli, tmp_path
):
    """An unreadable pre-publication re-read gates cleanly and aborts its transaction.

    The race fixture reaches only the post-parking seam; it does not prove
    fsync ordering or exclude the remaining re-read/publication race."""
    ignore = tmp_path / ".gitignore"
    ignore.write_text("build/\n" + "# filler\n" * (INTRUDER_WINDOW_BYTES // 9),
                      encoding="utf-8")

    intruder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            DENY_READS_WHILE_PARKING,
            str(tmp_path / ".validated-memory" / "preimages"),
            str(ignore),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        result = run_cli("init", cwd=tmp_path)
    finally:
        intruder.wait(timeout=90)
        ignore.chmod(0o644)
    assert intruder.returncode == 0, intruder.communicate()

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert ".gitignore" in result.stderr, result.stderr
    assert (
        "could not be read while its mutation was being prepared"
        in result.stderr
    ), result.stderr
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == ".gitignore"
    ]
    # Opened, so closed `aborted` and removed -- never left for a recovery
    # that has nothing to recover.
    assert not _transactions(tmp_path)


def test_a_creation_publishes_with_o_excl_rather_than_replacing():
    """Structurally pin exclusive creation and private staging permissions.

    The syscall-sized no-replace window is not reachable through the CLI
    seam, so assertions locate the sole publisher and inspect its flags."""
    # Found across the write path rather than opened by name, and asserted to
    # be exactly one: the guarantee below is a property of THE function that
    # publishes, and a second definition of it -- in another module of the
    # journal, added later -- would be a second answer to the same question,
    # with this pin green against whichever of the two it happened to read.
    publishers = [
        (relative, node)
        for relative in sorted(RAW_WRITE_MODULES)
        for node in ast.walk(
            ast.parse(
                (REPO_ROOT / "validated_memory" / relative).read_text(
                    encoding="utf-8"
                )
            )
        )
        if isinstance(node, ast.FunctionDef) and node.name == "_publish"
    ]
    assert len(publishers) == 1, [relative for relative, _ in publishers]
    publish = publishers[0][1]
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
    """Return a probed-unused PID, starting with a reaped child."""
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
    """Nested init and Run lock acquisition succeeds and releases the lock.

    Run takes the inner lock around its reads and `_bootstrap`."""
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert "another validated-memory process holds" not in result.stderr
    assert (tmp_path / "journal.jsonl").exists()
    # Taken and released, not leaked: the next run must not have to break it.
    assert not (tmp_path / ".validated-memory" / "lock").exists()


def test_a_lock_whose_owner_is_alive_is_never_broken(run_cli, tmp_path):
    """A live owner keeps an old lock until init refuses contention.

    The refusal identifies the lock and remedy, preserves its inode and PID,
    and creates neither journal.jsonl nor .gitignore."""
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
        # The refused run created neither the journal nor the ignore file.
        assert not (tmp_path / "journal.jsonl").exists()
        assert not (tmp_path / ".gitignore").exists()
    finally:
        holder.terminate()
        holder.wait(timeout=30)
        holder.stdout.close()


def test_a_lock_whose_owner_is_gone_is_broken_at_once(run_cli, tmp_path):
    """A fresh lock whose probed PID is gone is broken without waiting."""
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
    """A run releases only the inode it acquired, preserving a successor lock.

    Padding keeps the process inside its outer lock long enough to swap the
    file; it creates timing opportunity, not a guarantee about an inner call."""
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
    """Two trees sharing a journal both target the store's adjacent lock.

    Permission failures expose the selected lock path; this does not run two
    successful mutations concurrently."""
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
    """A broken journal symlink keeps locking inside the adopter root.

    `_bootstrap` then refuses the journal without creating its target parent."""
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
    """A journal symlink loop gates as an unreadable journal without traceback."""
    (tmp_path / "journal.jsonl").symlink_to("journal.jsonl")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert "journal could not be read" in result.stderr, result.stderr
    assert "journal.jsonl" in result.stderr, result.stderr


# --- recovery: what a run does with what an earlier run left open -------------


def _diverged(tree, before=None, kill_after="an adopter wrote this\n"):
    """Build a published residue, optionally diverge it, and return its ID.

    `before` creates a real preimage blob. `kill_after=None` leaves the
    published path recoverable instead of simulating an adopter overwrite."""
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
    """Build a published file creation with an absent preimage; return its ID.

    Callers alter the published path to make the transaction diverged."""
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
    """Write a transaction fixture in the writer's field shape."""
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


def test_a_creation_does_not_build_the_parent_the_same_run_refused_to_create(
    run_cli, tmp_path
):
    """A child creation cannot create a parent gated by another transaction.

    The failed child transaction closes; the gating ID remains the sole
    transaction, and parsed history records compare equal."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    (tmp_path / "memory" / "MEMORY.md").unlink()
    (tmp_path / "memory").rmdir()
    # A `prepared` transaction whose path matches neither of its states is
    # `unknown`, which is what gates the path: recovery may not close it,
    # and nothing may write over it until an operator does.
    _transaction_file(
        tmp_path,
        "8888888888888888",
        intention={
            "op": "replace",
            "purpose": "init",
            "path": "memory",
            "durability": "repo",
        },
        postimage={"kind": "directory"},
    )
    before = _records(tmp_path / "journal.jsonl")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert not (tmp_path / "memory").exists(), sorted(
        entry.name for entry in tmp_path.iterdir()
    )
    assert (
        "memory/MEMORY.md: create: file could not be created: "
        "memory/MEMORY.md could not be written: its parent directory memory "
        "does not exist" in result.stderr
    ), result.stderr
    assert "created memory/MEMORY.md" not in result.stdout, result.stdout
    # No history record is added. Only the gating transaction ID remains.
    assert _records(tmp_path / "journal.jsonl") == before
    assert [entry["transaction"] for entry in _transactions(tmp_path)] == [
        "8888888888888888"
    ], _transactions(tmp_path)


def test_recovery_leaves_a_transaction_it_cannot_account_for_untouched(
    run_cli, tmp_path
):
    """Repeated recovery leaves a diverged transaction and history unchanged."""
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
    """A single-path transaction gates only its path, not the rest of init.

    The fixture avoids the ignore-file whole-scaffold gate so other creations
    remain observable."""
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
    """Report recoverable, diverged, unknown and damaged for four residues.

    Assertions preserve journal bytes and transaction count, but do not compare
    transaction-file bytes or distinguish recovery actions within one class."""
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
    """Recovery removes an aborted transaction without history or its ID in output."""
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
    """--accept preserves the path and records one exact resolution observation."""
    transaction = _diverged(tmp_path)

    result = run_cli("journal", "--resolve", transaction, "--accept", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == "", result.stderr
    assert f"journal: resolved {transaction} (--accept)" in result.stdout
    assert not _transactions(tmp_path), _transactions(tmp_path)

    # The mutation's own pair goes in ahead of it, because the transaction
    # is `published` and the bytes are on disk -- asserted by the test
    # below. What this one is about is the last record, the resolution's.
    written = [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record["path"] == ".gitignore" and record["op"] == "observe"
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
        if record["path"] == ".gitignore" and record["op"] == "observe"
    ]
    assert len(written) == 1, written
    assert written[0]["op"] == "observe", written
    assert written[0]["note"] == (
        f"abandoned: transaction {transaction}, path left as found"
    ), written
    assert (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    ) == "an adopter wrote this\n"


def test_journal_resolve_over_a_published_transaction_keeps_its_record_pair(
    run_cli, tmp_path
):
    """Resolving a published divergence retains its mutation pair before observe.

    The pair keeps the crashed run and transaction IDs. Later checks and
    unchanged init runs add nothing."""
    _diverged(tmp_path, kill_after="/.validated-memory/\nbuild/\n")
    entry = _transactions(tmp_path)[0]
    transaction = entry["transaction"]

    result = run_cli("journal", "--resolve", transaction, "--accept", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not _transactions(tmp_path), _transactions(tmp_path)
    written = [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record["path"] == ".gitignore"
    ]
    assert [record["op"] for record in written] == [
        "create",
        "create",
        "observe",
    ], written
    assert [record["stage"] for record in written] == [
        "prepared",
        "committed",
        "committed",
    ], written
    # One act, filed under the run that wrote the bytes -- not under the
    # run that resolved it, which wrote none.
    assert [record.get("transaction") for record in written[:2]] == [
        transaction,
        transaction,
    ], written
    assert [record["run"] for record in written[:2]] == [
        entry["run"],
        entry["run"],
    ], (written, entry)
    assert written[2].get("transaction") is None, written
    assert written[2]["note"] == (
        f"accepted after divergence: transaction {transaction} found file"
    ), written

    # A closed pair is not reconciled again, so the next run has nothing to
    # complete and nothing to say about it.
    assert run_cli("journal", "--check", cwd=tmp_path).returncode == 0
    settled = run_cli("init", cwd=tmp_path)
    assert settled.returncode == 0, (settled.stdout, settled.stderr)
    assert "recovered" not in settled.stdout, settled.stdout
    records = _records(tmp_path / "journal.jsonl")
    assert [
        record for record in records if record["path"] == ".gitignore"
    ] == written, records
    again = run_cli("init", cwd=tmp_path)
    assert again.returncode == 0, again.stderr
    assert _records(tmp_path / "journal.jsonl") == records


def test_journal_resolve_restore_puts_the_preimage_back_and_records_nothing(
    run_cli, tmp_path
):
    """--restore reinstates preimage bytes and mode without appending history."""
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
    """--restore refuses a missing or digest-mismatched open-transaction blob.

    A mismatch preserves the current path; both cases leave the transaction
    unresolved. Missing blobs for closed history remain valid."""
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
    """--restore refuses once the mutation pair is committed to history."""
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
    """A well-formed unknown transaction ID is a gating state error."""
    assert run_cli("init", cwd=tmp_path).returncode == 0

    result = run_cli(
        "journal", "--resolve", "deadbeefdeadbeef", "--accept", cwd=tmp_path
    )

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "no unresolved transaction deadbeefdeadbeef" in result.stderr
    assert "Nothing has been changed." in result.stderr, result.stderr


def test_journal_resolve_needs_exactly_one_of_the_three_flags(run_cli, tmp_path):
    """Resolve requires one nonblank ID and exactly one resolution flag."""
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
    """Missing blobs named only by closed history do not damage a clone.

    Open-transaction blobs have a stricter rule pinned by the restore test."""
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
    """Two records sharing an ID must agree on mode; disagreement gates."""
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
    """Restoring absence parks discarded file bytes and reports their blob.

    Parking alone appends no history."""
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
    """Restoring absence refuses a nonempty directory, then removes it when empty.

    The refusal preserves contents and transaction; success reports no
    discarded bytes and records no observation."""
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
    """All operator flags refuse a recoverable transaction without changing it.

    Init remains the path that completes and removes this residue."""
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


# --- the two directories the plugin owns are real directories -----------------


def test_a_symlinked_transactions_directory_writes_nothing_outside_the_tree(
    run_cli, tmp_path, monkeypatch
):
    """A symlinked transaction directory gates before writing outside the tree.

    The fault point would preserve any attempted transaction, making the
    no-write assertion non-vacuous."""
    tree = tmp_path / "tree"
    outside = tmp_path / "outside"
    tree.mkdir()
    outside.mkdir()
    (tree / ".validated-memory").mkdir()
    (tree / ".validated-memory" / "transactions").symlink_to(outside)

    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-transaction")
    result = run_cli("init", cwd=tree)

    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert ".validated-memory/transactions" in result.stderr, result.stderr
    assert "is a symlink" in result.stderr, result.stderr
    assert not list(outside.iterdir()), sorted(p.name for p in outside.iterdir())


def test_a_plain_file_where_the_transactions_directory_goes_is_a_finding(
    run_cli, tmp_path
):
    """`--check` reports it, and does not raise `NotADirectoryError` at `iterdir`."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    transactions = tmp_path / ".validated-memory" / "transactions"
    for leftover in transactions.iterdir():
        leftover.unlink()
    transactions.rmdir()
    transactions.write_text("not a directory\n", encoding="utf-8")

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert result.stderr.startswith("ERROR: .validated-memory/transactions:"), (
        result.stderr
    )
    assert "not a directory" in result.stderr, result.stderr


def test_a_symlinked_preimage_store_parks_nothing_outside_the_tree(
    run_cli, tmp_path
):
    """A symlinked preimage store gates without outside writes or target changes."""
    tree = tmp_path / "tree"
    outside = tmp_path / "outside"
    tree.mkdir()
    outside.mkdir()
    (tree / ".gitignore").write_text("build/\n", encoding="utf-8")
    (tree / ".validated-memory").mkdir()
    (tree / ".validated-memory" / "preimages").symlink_to(outside)

    result = run_cli("init", cwd=tree)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert ".validated-memory/preimages" in result.stderr, result.stderr
    assert not list(outside.iterdir()), sorted(p.name for p in outside.iterdir())
    assert (tree / ".gitignore").read_text(encoding="utf-8") == "build/\n"


# --- a transaction file is damaged unless it is this project's ----------------


def test_a_transaction_file_that_is_not_text_is_damaged_and_not_a_traceback(
    run_cli, tmp_path
):
    """Non-UTF-8 transaction bytes gate as damaged and remain for inspection."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    residue = tmp_path / ".validated-memory" / "transactions" / "5555555555555555.json"
    residue.parent.mkdir(parents=True, exist_ok=True)
    residue.write_bytes(b"\xff\xfe not text at all")

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "damaged transaction 5555555555555555:" in result.stderr, result.stderr
    assert "not valid UTF-8" in result.stderr, result.stderr
    assert residue.exists(), "a damaged file is left where it is"


def test_a_transaction_whose_schema_this_reader_does_not_know_is_damaged(
    run_cli, tmp_path
):
    """Unknown, nonnumeric and absent transaction schemas are damaged.

    All transaction files remain unresolved rather than being executed."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    _transaction_file(tmp_path, "6666666666666666", schema=999)
    _transaction_file(tmp_path, "7777777777777777", schema="one")
    entry = _transaction_file(tmp_path, "8888888888888888")
    del entry["schema"]
    (
        tmp_path / ".validated-memory" / "transactions" / "8888888888888888.json"
    ).write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert (
        "damaged transaction 6666666666666666: its schema is 999 and this "
        "plugin reads up to 1" in result.stderr
    ), result.stderr
    for damaged in ("7777777777777777", "8888888888888888"):
        assert (
            f"damaged transaction {damaged}: it names no schema" in result.stderr
        ), result.stderr
    assert len(_transactions(tmp_path)) == 3, _transactions(tmp_path)


def test_a_transaction_that_is_not_the_id_its_file_is_named_is_damaged(
    run_cli, tmp_path
):
    """A transaction ID must equal its filename stem."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    _transaction_file(tmp_path, "9999999999999999", transaction="aaaabbbbccccdddd")

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        "damaged transaction 9999999999999999: it calls itself transaction "
        "aaaabbbbccccdddd and its file is named 9999999999999999"
        in result.stderr
    ), result.stderr


def test_a_transaction_filed_under_another_adoption_is_damaged(run_cli, tmp_path):
    """A mutation of somebody else's tree is not one this history may record."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    mine = _records(tmp_path / "journal.jsonl")[0]["adoption"]
    foreign = "f" * 16
    _transaction_file(tmp_path, "abababababababab", adoption=foreign)

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        f"damaged transaction abababababababab: it belongs to adoption "
        f"{foreign}, this project is {mine}" in result.stderr
    ), result.stderr


def test_another_trees_transaction_is_never_completed_into_this_history(
    run_cli, tmp_path, monkeypatch
):
    """A foreign adoption's transaction gates without changing local history.

    The damaged residue remains available for inspection."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-published")
    assert run_cli("init", cwd=first).returncode == 70
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
    assert run_cli("init", cwd=second).returncode == 0

    residue = sorted((first / ".validated-memory" / "transactions").glob("*.json"))
    assert len(residue) == 1, residue
    transaction = json.loads(residue[0].read_text(encoding="utf-8"))
    target = second / ".validated-memory" / "transactions" / residue[0].name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(residue[0].read_text(encoding="utf-8"), encoding="utf-8")
    before = (second / "journal.jsonl").read_text(encoding="utf-8")

    result = run_cli("init", cwd=second)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert (
        f"damaged transaction {transaction['transaction']}: it belongs to "
        f"adoption {transaction['adoption']}" in result.stderr
    ), result.stderr
    assert (second / "journal.jsonl").read_text(encoding="utf-8") == before
    assert target.exists(), "a damaged transaction is left for inspection"


def test_a_transaction_naming_an_operation_no_intention_carries_is_damaged(
    run_cli, tmp_path
):
    """Transactions reject legacy record-only operations and observations.

    Both damaged files stay unresolved."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    for transaction_id, op in (
        ("cdcdcdcdcdcdcdcd", "patch"),
        ("efefefefefefefef", "observe"),
    ):
        _transaction_file(
            tmp_path,
            transaction_id,
            intention={
                "op": op,
                "purpose": "init",
                "path": "validated-memory.md",
                "durability": "repo",
            },
        )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        "damaged transaction cdcdcdcdcdcdcdcd: its intention names no "
        "operation this plugin prepares" in result.stderr
    ), result.stderr
    assert (
        "damaged transaction efefefefefefefef: its intention is an "
        "observation" in result.stderr
    ), result.stderr
    assert len(_transactions(tmp_path)) == 2, _transactions(tmp_path)


def test_a_transaction_whose_states_are_not_states_is_damaged(run_cli, tmp_path):
    """Reject state envelopes whose digest or symlink target has the wrong type."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    _transaction_file(
        tmp_path,
        "1212121212121212",
        postimage={"kind": "file", "digest": 42, "mode": 420},
    )
    _transaction_file(
        tmp_path,
        "3434343434343434",
        preimage={"kind": "symlink", "target": ["memory"], "mode": 511},
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    for transaction_id in ("1212121212121212", "3434343434343434"):
        assert (
            f"damaged transaction {transaction_id}: its preimage or "
            "postimage is in no state this plugin knows" in result.stderr
        ), result.stderr


# --- bytes that cannot be read are `unknown`, never a traceback ---------------


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
def test_an_unreadable_path_says_which_stage_its_transaction_reached(
    run_cli, tmp_path, monkeypatch
):
    """Unreadable-path diagnostics distinguish prepared from published stages."""
    published = tmp_path / "published"
    prepared = tmp_path / "prepared"
    published.mkdir()
    prepared.mkdir()
    # An ignore file that is already there makes the killed intention an
    # `append` over a real file, so `prepared` has bytes at the path to
    # make unreadable. A `create` at the same stage has published nothing.
    (prepared / ".gitignore").write_text("build/\n", encoding="utf-8")

    for tree, fault, stage in (
        (published, "after-published", "published"),
        (prepared, "after-transaction", "prepared"),
    ):
        monkeypatch.setenv("VALIDATED_MEMORY_FAULT", fault)
        assert run_cli("init", cwd=tree).returncode == 70
        monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
        entry = _transactions(tree)[0]
        assert entry["stage"] == stage, entry
        (tree / ".gitignore").chmod(0o000)

        try:
            result = run_cli("init", cwd=tree)

            assert result.returncode == 1, (result.stdout, result.stderr)
            assert "Traceback" not in result.stderr, result.stderr
            if stage == "published":
                assert (
                    f"transaction {entry['transaction']} published .gitignore, "
                    "and .gitignore cannot be read" in result.stderr
                ), result.stderr
                assert (
                    "whether what it published is still there" in result.stderr
                ), result.stderr
            else:
                assert (
                    f"transaction {entry['transaction']} prepared a mutation "
                    "of .gitignore, and .gitignore cannot be read"
                    in result.stderr
                ), result.stderr
                assert "whether it ran" in result.stderr, result.stderr
        finally:
            (tree / ".gitignore").chmod(0o644)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
def test_a_path_whose_bytes_cannot_be_read_classifies_as_unknown(
    run_cli, tmp_path, monkeypatch
):
    """Unreadable file bytes classify as unknown across check, recovery and resolve.

    Each path gates without traceback and keeps the transaction unresolved."""
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-published")
    assert run_cli("init", cwd=tmp_path).returncode == 70
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
    transaction = _transactions(tmp_path)[0]["transaction"]
    (tmp_path / ".gitignore").chmod(0o000)

    try:
        result = run_cli("journal", "--check", cwd=tmp_path)

        assert result.returncode == 1, result.stdout
        assert "Traceback" not in result.stderr, result.stderr
        assert (
            f"open transaction {transaction} (published) on .gitignore: unknown"
            in result.stderr
        ), result.stderr

        # And the run that meets it reports it rather than raising, leaving
        # the transaction exactly where it was.
        recovered = run_cli("init", cwd=tmp_path)
        assert recovered.returncode == 1, (recovered.stdout, recovered.stderr)
        assert "Traceback" not in recovered.stderr, recovered.stderr
        assert ".gitignore cannot be read" in recovered.stderr, recovered.stderr
        assert len(_transactions(tmp_path)) == 1, _transactions(tmp_path)

        # As does the operator's way out: it cannot say what state it would
        # be closing over, so it closes nothing.
        refused = run_cli(
            "journal", "--resolve", transaction, "--accept", cwd=tmp_path
        )
        assert refused.returncode == 1, (refused.stdout, refused.stderr)
        assert "Traceback" not in refused.stderr, refused.stderr
        assert "could not be read" in refused.stderr, refused.stderr
        assert "Nothing has been changed." in refused.stderr, refused.stderr
        assert len(_transactions(tmp_path)) == 1, _transactions(tmp_path)
    finally:
        (tmp_path / ".gitignore").chmod(0o644)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
def test_the_executor_refuses_a_path_whose_state_it_cannot_read(run_cli, tmp_path):
    """An unreadable expected-state check gates with no transaction or path record."""
    (tmp_path / "knowledge").write_text("not a directory\n", encoding="utf-8")
    (tmp_path / "knowledge").chmod(0o000)

    try:
        result = run_cli("init", cwd=tmp_path)

        assert result.returncode == 1, (result.stdout, result.stderr)
        assert "Traceback" not in result.stderr, result.stderr
        assert (
            "knowledge could not be read, so nothing here can say what state "
            "it is in" in result.stderr
        ), result.stderr
        assert "Nothing has been written." in result.stderr, result.stderr
        assert not _transactions(tmp_path), "a refusal opens no transaction"
        assert not [
            entry
            for entry in _records(tmp_path / "journal.jsonl")
            if entry["path"] == "knowledge"
        ], "and records nothing"
    finally:
        (tmp_path / "knowledge").chmod(0o644)


# --- an id-carrying record is a half of exactly one act -----------------------


def _rewrite(path, lines):
    """Replace a journal with `lines` (records), in file order."""
    path.write_text(
        "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in lines),
        encoding="utf-8",
    )


def test_journal_check_reports_a_committed_half_with_no_prepared_half(
    run_cli, tmp_path
):
    """A committed record without its prepared half is a gating pair error."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    records = _records(journal)
    orphaned = next(
        entry
        for entry in records
        if entry.get("transaction") and entry["stage"] == "committed"
    )
    _rewrite(
        journal,
        [
            entry
            for entry in records
            if not (
                entry.get("transaction") == orphaned["transaction"]
                and entry["stage"] == "prepared"
            )
        ],
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        f"records of transaction {orphaned['transaction']}: committed "
        "without a prepared half" in result.stderr
    ), result.stderr


def test_journal_check_reports_a_transaction_recorded_more_than_twice(
    run_cli, tmp_path
):
    """A transaction ID occurring more than twice is a gating pair error."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    records = _records(journal)
    doubled = next(
        entry["transaction"] for entry in records if entry.get("transaction")
    )
    _rewrite(
        journal,
        records + [entry for entry in records if entry.get("transaction") == doubled],
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        f"transaction {doubled} is recorded 4 times" in result.stderr
    ), result.stderr


def test_journal_check_reports_two_halves_that_disagree_on_purpose(
    run_cli, tmp_path
):
    """Two records sharing an ID must also agree on purpose."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    records = _records(journal)
    forged = next(
        entry
        for entry in records
        if entry.get("transaction") and entry["stage"] == "committed"
    )
    _rewrite(
        journal,
        [
            {**entry, "purpose": "forged"} if entry is forged else entry
            for entry in records
        ],
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        f"records of transaction {forged['transaction']} disagree on purpose"
        in result.stderr
    ), result.stderr


def test_recovery_still_appends_exactly_one_pair_over_a_history_that_has_it(
    run_cli, tmp_path, monkeypatch
):
    """Recovery leaves an existing complete pair recorded exactly twice."""
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-history")
    assert run_cli("init", cwd=tmp_path).returncode == 70
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
    transaction = _transactions(tmp_path)[0]["transaction"]

    assert run_cli("init", cwd=tmp_path).returncode == 0

    checked = run_cli("journal", "--check", cwd=tmp_path)
    assert checked.returncode == 0, (checked.stdout, checked.stderr)
    written = [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry.get("transaction") == transaction
    ]
    assert len(written) == 2, written


# --- a refusal that says nothing changed has changed nothing ------------------


def test_resolving_an_id_nothing_carries_leaves_a_virgin_tree_virgin(
    run_cli, tmp_path
):
    """Resolving an unknown ID leaves a virgin tree filesystem-empty.

    The exact refusal, absent journal and absent vault pin preflight before
    constructing the adopting run."""
    result = run_cli(
        "journal", "--resolve", "deadbeefdeadbeef", "--accept", cwd=tmp_path
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert (
        "ERROR: .validated-memory/transactions/deadbeefdeadbeef.json: "
        "journal: there is no unresolved transaction deadbeefdeadbeef; "
        "'validated-memory journal --check' lists the ones there are. "
        "Nothing has been changed." in result.stderr
    ), result.stderr
    assert not (tmp_path / "journal.jsonl").exists(), "the journal was created"
    assert not (tmp_path / ".validated-memory").exists(), "the vault was created"
    assert not list(tmp_path.iterdir()), sorted(p.name for p in tmp_path.iterdir())


def test_resolving_an_id_nothing_carries_is_the_same_refusal_in_an_adopted_tree(
    run_cli, tmp_path
):
    """An adopted tree gets the unknown-ID refusal with journal text unchanged."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    before = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")

    result = run_cli(
        "journal", "--resolve", "deadbeefdeadbeef", "--abandon", cwd=tmp_path
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert (
        "there is no unresolved transaction deadbeefdeadbeef" in result.stderr
    ), result.stderr
    assert (tmp_path / "journal.jsonl").read_text(encoding="utf-8") == before
