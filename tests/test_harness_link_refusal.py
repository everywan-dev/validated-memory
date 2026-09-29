"""End-to-end tests for ADR 0029: the harness link survives a refusal that does not name it.

`init --harness-memory PATH` is what the `SessionStart` hook runs. When the
journal refuses the run, the link is restored only if the refusal cannot own
the harness path: the guarded repair reads the history and the vault under the
run-wide lock, decides, and relinks in the same critical section. Every test
here drives the CLI as a subprocess over fixture adopters and asserts exit
codes, output and the bytes left behind; none imports the package.

The refusal fixture is a healthy adopter whose repository journal then gains a
record of a second adoption lineage. That is the topology gate ADR 0029 calls a
refusal that does not name the harness path: readable history, refused before
any adopting effect, no condition or transaction on the harness path. The
withheld cases add exactly one obstruction to that fixture, so each test pins
the rule that obstruction trips and no other.
"""

import ast
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Holds the run-wide lock exactly as `Lock` takes it and stays alive until
# killed. The same outside-the-package holder `tests/test_journal.py` uses.
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

# `LOCK_WAIT_SECONDS` as an outside observer sees it: the time a run that finds
# the lock held waits before it refuses.
LOCK_DEADLINE_SECONDS = 10


def _cli(cwd, *args, env=None):
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT), **(env or {})}
    return subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", *args],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _snapshot(root):
    """Final nodes, contents, link targets and modes under `root`, never following.

    The lock file is left out: a killed run leaves it behind and the next run
    breaks it, so its presence says nothing about what a refusal wrote.
    """
    nodes = []

    def visit(directory):
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
        for entry in children:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            if relative == ".validated-memory/lock":
                continue
            info = entry.stat(follow_symlinks=False)
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                nodes.append((relative, "symlink", os.readlink(path), mode))
            elif stat.S_ISREG(info.st_mode):
                nodes.append((relative, "file", path.read_bytes(), mode))
            elif stat.S_ISDIR(info.st_mode):
                nodes.append((relative, "directory", mode))
                visit(path)
            else:
                nodes.append((relative, "other", stat.S_IFMT(info.st_mode), mode))

    visit(root)
    return nodes


def _second_lineage_record():
    """A record of adoption "B": the second lineage of a legacy history."""
    return {
        "schema": 1,
        "at": "2026-01-01T00:00:00Z",
        "version": "2.4.0",
        "adoption": "B",
        "run": "run",
        "durability": "repo",
        "op": "observe",
        "purpose": "fixture",
        "path": "memory",
        "stage": "committed",
    }


def _add_second_lineage(adopter):
    """Make the readable history carry two adoption lineages: a topology gate."""
    journal = adopter / "journal.jsonl"
    line = json.dumps(_second_lineage_record(), sort_keys=True) + "\n"
    journal.write_bytes(journal.read_bytes() + line.encode("utf-8"))


def _adopted(tmp_path):
    """A healthy adopter whose harness link exists; return (adopter, harness)."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    harness = tmp_path / "harness" / "memory"
    result = _cli(adopter, "init", "--harness-memory", str(harness))
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert harness.resolve() == (adopter / "memory").resolve()
    return adopter, harness


def _point_at_stale(harness, name="stale"):
    """Re-point the harness link at a directory that is not `memory/`."""
    stale = harness.parent / name
    stale.mkdir(exist_ok=True)
    harness.unlink(missing_ok=True)
    harness.symlink_to(stale, target_is_directory=True)
    return stale


def _retain_wal_on(adopter, harness_argument):
    """Kill a run after it published the link: its WAL owns that path.

    The link must already be stale so that the killed run has a mutation to
    make; the killed run leaves it correct, so a caller that needs it wrong
    re-points it afterwards. Returns the retained transaction id.
    """
    killed = _cli(
        adopter,
        "init",
        "--harness-memory",
        str(harness_argument),
        env={"VALIDATED_MEMORY_FAULT": "after-published"},
    )
    assert killed.returncode == 70, (killed.stdout, killed.stderr)
    transactions = sorted(
        (adopter / ".validated-memory" / "transactions").glob("*.json")
    )
    assert len(transactions) == 1, transactions
    return transactions[0].stem


def _link_target(harness):
    return os.readlink(harness)


def _warnings(result):
    return [line for line in result.stderr.splitlines() if line.startswith("WARNING:")]


def _errors(result):
    return [line for line in result.stderr.splitlines() if line.startswith("ERROR:")]


# --- a refusal that does not name the harness path: the link is restored ------


def test_a_topology_refusal_restores_a_missing_harness_link(tmp_path):
    """The gate names no harness path, so the link is created, and only the link.

    Pins the exit code (the refusal stays an ERROR), the link, the WARNING that
    names the harness path and says the journal refused the run, and that the
    refusal wrote nothing: the whole adopter tree, including `journal.jsonl`
    and every vault file, is byte-identical afterwards."""
    adopter, harness = _adopted(tmp_path)
    harness.unlink()
    _add_second_lineage(adopter)
    before = _snapshot(adopter)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert harness.is_symlink()
    assert harness.resolve() == (adopter / "memory").resolve()
    assert len(_errors(result)) == 1, result.stderr
    assert "unresolved topology condition" in _errors(result)[0]
    assert len(_warnings(result)) == 1, result.stderr
    warning = _warnings(result)[0]
    assert str(harness) in warning
    assert "the journal refused this run" in warning
    assert "restoring it anyway" in warning
    assert f"init: created symlink {harness}" in result.stdout
    assert result.stdout.endswith("init: 1 item(s) confirmed, 1 gate(s)\n")
    assert _snapshot(adopter) == before


def test_a_topology_refusal_repoints_a_stale_harness_link(tmp_path):
    """A link pointing elsewhere is re-pointed, and the warning names the old target."""
    adopter, harness = _adopted(tmp_path)
    stale = _point_at_stale(harness)
    _add_second_lineage(adopter)
    before = _snapshot(adopter)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert harness.resolve() == (adopter / "memory").resolve()
    assert len(_warnings(result)) == 1, result.stderr
    assert f"previous target: {stale}" in _warnings(result)[0]
    assert f"init: re-pointed symlink {harness}" in result.stdout
    assert _snapshot(adopter) == before


def test_a_topology_refusal_leaves_a_correct_harness_link_alone(tmp_path):
    """A link that already resolves to `memory/` needs no repair and no warning.

    Pins the common case: the session starts with the link intact and a
    history that is refused. The refusal is the only diagnostic, and the tree
    is untouched."""
    adopter, harness = _adopted(tmp_path)
    _add_second_lineage(adopter)
    before = _snapshot(adopter)
    link_before = _link_target(harness)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert len(_errors(result)) == 1, result.stderr
    assert _warnings(result) == [], result.stderr
    assert _link_target(harness) == link_before
    assert _snapshot(adopter) == before


def test_a_relative_harness_path_is_recorded_and_restored_as_an_absolute_one(tmp_path):
    """`--harness-memory` as typed relative reaches the journal path absolute.

    The healthy run records a `local` transaction path; a later refusal
    compares recorded paths with the harness path. Both are only comparable if
    the typed spelling is made absolute first."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    (tmp_path / "harness").mkdir()
    result = _cli(adopter, "init", "--harness-memory", "../harness/memory")
    assert result.returncode == 0, (result.stdout, result.stderr)
    absolute = tmp_path / "harness" / "memory"
    assert absolute.is_symlink()
    local = (adopter / ".validated-memory" / "local.jsonl").read_text("utf-8")
    recorded = [json.loads(line) for line in local.splitlines()]
    link_paths = {entry["path"] for entry in recorded if entry["op"] == "link"}
    assert link_paths == {str(absolute)}, recorded


# --- a refusal that may own the harness path: the link is withheld ------------


def _assert_withheld(result, harness, link_before, reason):
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert _link_target(harness) == link_before
    assert len(_errors(result)) == 1, result.stderr
    assert len(_warnings(result)) == 1, result.stderr
    warning = _warnings(result)[0]
    assert str(harness) in warning
    assert "the harness link was not restored" in warning
    assert reason in warning, warning
    assert "restored by the first run the journal allows" in warning
    assert "created symlink" not in result.stdout
    assert "re-pointed symlink" not in result.stdout


def test_a_retained_transaction_on_the_harness_path_withholds_the_link(tmp_path):
    """A WAL that owns the path owns its preimage; relinking over it destroys it.

    The killed run leaves a `published` transaction whose target is the
    harness link. The refusal that follows names no path, yet the link, the
    WAL, the journal and the vault stay byte-identical, and the WARNING names
    the transaction."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    transaction = _retain_wal_on(adopter, harness)
    _point_at_stale(harness, "diverged")
    _add_second_lineage(adopter)
    before = _snapshot(adopter)
    link_before = _link_target(harness)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    _assert_withheld(result, harness, link_before, f"transaction {transaction}")
    assert _snapshot(adopter) == before


@pytest.mark.parametrize(
    "spelling", ("relative", "symlinked-parent", "recorded-through-alias")
)
def test_the_harness_path_is_compared_as_a_directory_entry_not_as_text(
    tmp_path, spelling
):
    """One entry with two spellings is one path: the repair is still withheld.

    Pins path equivalence: the harness path is made absolute, parents are
    resolved, and the final name is compared without following it. A textual
    comparison misses all three spellings: a relative path, a path through a
    symlinked parent, and a WAL recorded through the symlink while the run
    names the real directory."""
    adopter, harness = _adopted(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(harness.parent, target_is_directory=True)
    _point_at_stale(harness)
    recorded_as = alias / "memory" if spelling == "recorded-through-alias" else harness
    transaction = _retain_wal_on(adopter, recorded_as)
    _point_at_stale(harness, "diverged")
    _add_second_lineage(adopter)
    before = _snapshot(adopter)
    link_before = _link_target(harness)
    typed = {
        "relative": "../harness/memory",
        "symlinked-parent": str(alias / "memory"),
        "recorded-through-alias": str(harness),
    }[spelling]

    result = _cli(adopter, "init", "--harness-memory", typed)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert _link_target(harness) == link_before
    assert len(_warnings(result)) == 1, result.stderr
    assert "the harness link was not restored" in _warnings(result)[0]
    assert f"transaction {transaction}" in _warnings(result)[0]
    assert _snapshot(adopter) == before


@pytest.mark.parametrize("where", ("transactions", "preimages"))
def test_an_unclassified_vault_file_withholds_the_link(tmp_path, where):
    """A file the vault cannot account for may be the residue of the harness path.

    The vault's transaction and preimage directories hold only canonical
    artifacts; anything else is retained private residue whose ownership is
    not proven, so it blocks the relink whichever directory it sits in."""
    adopter, harness = _adopted(tmp_path)
    harness.unlink()
    directory = adopter / ".validated-memory" / where
    directory.mkdir(exist_ok=True)
    (directory / "stray.txt").write_bytes(b"not a canonical artifact\n")
    _add_second_lineage(adopter)
    before = _snapshot(adopter)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert not harness.exists() and not harness.is_symlink()
    assert len(_warnings(result)) == 1, result.stderr
    assert "the harness link was not restored" in _warnings(result)[0]
    assert f".validated-memory/{where}/stray.txt" in _warnings(result)[0]
    assert _snapshot(adopter) == before


def test_a_transaction_that_cannot_be_parsed_withholds_the_link(tmp_path):
    """A transaction file that is not JSON may have named the harness path."""
    adopter, harness = _adopted(tmp_path)
    harness.unlink()
    directory = adopter / ".validated-memory" / "transactions"
    directory.mkdir(exist_ok=True)
    (directory / "0123456789abcdef.json").write_bytes(b"{not json")
    _add_second_lineage(adopter)
    before = _snapshot(adopter)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert not harness.exists() and not harness.is_symlink()
    assert len(_warnings(result)) == 1, result.stderr
    assert "the harness link was not restored" in _warnings(result)[0]
    assert "0123456789abcdef" in _warnings(result)[0]
    assert _snapshot(adopter) == before


def test_a_condition_on_the_harness_path_withholds_the_link(tmp_path):
    """A history condition whose subject is the harness path withholds the link.

    Dropping the committed half of the link's own record leaves an unfinished
    legacy transaction. It is a `history.topology_gate` like the fixture's
    other one, and the vault holds no transaction and no residue, so the
    subject rule is the only one that can withhold here."""
    adopter, harness = _adopted(tmp_path)
    harness.unlink()
    local = adopter / ".validated-memory" / "local.jsonl"
    kept = [
        line
        for line in local.read_text("utf-8").splitlines()
        if json.loads(line)["stage"] != "committed"
    ]
    local.write_bytes(("\n".join(kept) + "\n").encode("utf-8"))
    checked = _cli(adopter, "journal", "--check")
    assert checked.returncode == 1, (checked.stdout, checked.stderr)
    assert f"ERROR: {harness}: journal: unfinished transaction" in checked.stderr
    before = _snapshot(adopter)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert not harness.exists() and not harness.is_symlink()
    assert len(_warnings(result)) == 1, result.stderr
    assert "the harness link was not restored" in _warnings(result)[0]
    assert "names the harness path" in _warnings(result)[0]
    assert _snapshot(adopter) == before


def test_a_real_directory_at_the_harness_path_is_never_absorbed_or_parked(tmp_path):
    """The repair restores links; a directory moves data, which a refusal may not do."""
    adopter, harness = _adopted(tmp_path)
    harness.unlink()
    harness.mkdir()
    (harness / "native.md").write_bytes(b"native harness memory\n")
    _add_second_lineage(adopter)
    before = _snapshot(adopter)
    harness_before = _snapshot(harness.parent)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert len(_warnings(result)) == 1, result.stderr
    assert "already exists and is not a symlink" in _warnings(result)[0]
    assert _snapshot(harness.parent) == harness_before
    assert _snapshot(adopter) == before
    assert "adopted" not in result.stdout and "parked" not in result.stdout


def test_a_refusal_the_gate_does_not_classify_withholds_the_link_with_a_warning(
    tmp_path,
):
    """An identity refusal is not a readable-history gate, so the repair is not attempted.

    An empty `journal.jsonl` is refused by the identity transition, after the
    adoption gate. The link stays as it was, and the run still says so: the
    session must not lose its project memory silently."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    (adopter / "memory").mkdir()
    (adopter / ".validated-memory").mkdir()
    (adopter / "journal.jsonl").write_bytes(b"")
    harness = tmp_path / "harness" / "memory"
    harness.parent.mkdir()
    _point_at_stale(harness)
    link_before = _link_target(harness)
    before = _snapshot(adopter)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "not one complete validated opening" in _errors(result)[0]
    _assert_withheld(
        result,
        harness,
        link_before,
        "cannot show that it is unrelated",
    )
    assert _snapshot(adopter) == before


# --- a journal that cannot be read: the exception of the journal core §4 ------


def _break_journal_with_a_loop(adopter):
    history = adopter / "journal.jsonl"
    history.unlink()
    history.symlink_to("journal.jsonl")
    return history


def test_an_unreadable_journal_still_restores_the_link_when_the_vault_is_clean(
    tmp_path,
):
    """Unavailable history with nothing retained is the exception §4 always allowed."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    history = _break_journal_with_a_loop(adopter)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert harness.resolve() == (adopter / "memory").resolve()
    assert len(_errors(result)) == 1, result.stderr
    assert len(_warnings(result)) == 1, result.stderr
    assert "the journal is unavailable" in _warnings(result)[0]
    assert "restoring it anyway" in _warnings(result)[0]
    assert history.is_symlink() and os.readlink(history) == "journal.jsonl"


def test_an_unreadable_journal_withholds_the_link_over_a_retained_transaction(
    tmp_path,
):
    """Availability does not license relinking over a WAL that owns the path.

    The lock can be taken and the vault read, so the vault conditions apply
    even though the history cannot be read."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    transaction = _retain_wal_on(adopter, harness)
    _point_at_stale(harness, "diverged")
    _break_journal_with_a_loop(adopter)
    before = _snapshot(adopter)
    link_before = _link_target(harness)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    _assert_withheld(result, harness, link_before, f"transaction {transaction}")
    assert _snapshot(adopter) == before


@pytest.mark.skipif(
    os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="an unlistable directory is only unlistable to a non-root user",
)
def test_a_vault_that_cannot_be_listed_restores_the_link_unguarded(tmp_path):
    """When the vault cannot be read at all, §4's promise stands unguarded.

    The transaction directory is unlistable, so neither the residue nor the
    transactions can be read: nothing can show that the harness path is not
    owned, and nothing can show that it is. The link is restored anyway, with
    the unavailable-journal WARNING. Reaching a topology refusal with an
    unreadable vault takes a fault between the gate and the repair, which no
    fixture produces, so the withheld counterpart of this case is unpinned."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    transactions = adopter / ".validated-memory" / "transactions"
    transactions.mkdir(exist_ok=True)
    transactions.chmod(0)
    try:
        result = _cli(adopter, "init", "--harness-memory", str(harness))
    finally:
        transactions.chmod(0o700)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert harness.resolve() == (adopter / "memory").resolve()
    assert len(_errors(result)) == 1, result.stderr
    assert len(_warnings(result)) == 1, result.stderr
    assert "the journal is unavailable" in _warnings(result)[0]


def test_a_lock_held_by_another_live_process_withholds_the_link_without_waiting_twice(
    tmp_path,
):
    """The holder may be writing a transaction for the harness path right now.

    The run waits out the lock once, refuses, and withholds the repair at
    once instead of waiting for the lock a second time. The bound below is
    twice the lock deadline: a second wait would exceed it."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    link_before = _link_target(harness)
    lock = adopter / ".validated-memory" / "lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_THE_LOCK, str(lock)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip(), "the holder died before locking"
        started = time.monotonic()
        result = _cli(adopter, "init", "--harness-memory", str(harness))
        elapsed = time.monotonic() - started
    finally:
        holder.terminate()
        holder.wait(timeout=30)
        holder.stdout.close()

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "another validated-memory process holds" in result.stderr
    assert _link_target(harness) == link_before
    assert len(_warnings(result)) == 1, result.stderr
    assert "the harness link was not restored" in _warnings(result)[0]
    assert "run-wide lock" in _warnings(result)[0]
    assert elapsed < 2 * LOCK_DEADLINE_SECONDS, elapsed


# --- what no subprocess can see -------------------------------------------------


def test_the_decision_and_the_relink_share_one_critical_section():
    """Structural pin: the extent of a lock is not observable from outside.

    ADR 0029 rejects "check first and relink after the lock is released": a
    transaction can be opened between the two. So `relink` may be called by
    `guarded_harness_repair` only inside the `try` whose `finally` releases
    the lock, in the same block that runs the vault and history check. This
    proves the shape, not the exclusion itself; the held-lock test above is
    what shows another process is kept out."""
    source = (
        REPO_ROOT / "validated_memory" / "journal" / "protocol.py"
    ).read_text(encoding="utf-8")
    guarded = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "guarded_harness_repair"
    )

    def calls(nodes):
        return [
            ast.unparse(node.func)
            for statement in nodes
            for node in ast.walk(statement)
            if isinstance(node, ast.Call)
        ]

    releasing = [
        node
        for node in ast.walk(guarded)
        if isinstance(node, ast.Try)
        and "lock.__exit__" in calls(node.finalbody)
    ]
    assert len(releasing) == 1, "one try must release the lock"
    assert "_harness_repair_obstacle" in calls(releasing[0].body)
    assert calls(releasing[0].body).count("relink") == 1
    assert calls(guarded.body).count("relink") == 1, (
        "relink is called outside the block that holds the lock"
    )
