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
any adopting effect, no condition or transaction on the harness path. Each
withheld case adds one obstruction to a healthy adopter, or replaces the
topology gate with the refusal it names, and its docstring says which rule it
pins.
"""

import ast
import json
import os
import select
import shutil
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

# Takes the lock exactly as `Lock` does, then releases it after the number of
# seconds in its second argument: a concurrent run that finishes.
HOLD_THEN_RELEASE = """
import os
import sys
import time

path = sys.argv[1]
os.makedirs(os.path.dirname(path), exist_ok=True)
descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
os.write(descriptor, ("%d\\n" % os.getpid()).encode("ascii"))
os.close(descriptor)
print(os.getpid(), flush=True)
time.sleep(float(sys.argv[2]))
os.unlink(path)
"""

# `LOCK_WAIT_SECONDS` as an outside observer sees it: the time a run that finds
# the lock held waits before it refuses.
LOCK_DEADLINE_SECONDS = 10

# What the hook passes as `--lock-wait`, and the slack a wall-clock bound on a
# whole run allows for interpreter start-up and a loaded machine.
HOOK_LOCK_WAIT_SECONDS = 3
RUN_SLACK_SECONDS = 2


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


def _retain_wal_on(adopter, harness_argument, fault="after-published"):
    """Kill a run at a fault seam: its WAL owns the path it was about to link.

    The link must already be stale so that the killed run has a mutation to
    make. `after-published` leaves it correct, so a caller that needs it wrong
    re-points it afterwards; `after-transaction` dies before publishing and
    leaves it as it was. Returns the retained transaction id.
    """
    killed = _cli(
        adopter,
        "init",
        "--harness-memory",
        str(harness_argument),
        env={"VALIDATED_MEMORY_FAULT": fault},
    )
    assert killed.returncode == 70, (killed.stdout, killed.stderr)
    transactions = sorted(
        (adopter / ".validated-memory" / "transactions").glob("*.json")
    )
    assert len(transactions) == 1, transactions
    return transactions[0].stem


def _link_target(harness):
    return os.readlink(harness)


def _rendezvous_run(adopter, harness, point, *arguments):
    """Start `init` so that it stops at `point` until the test lets it go."""
    ready_read, ready_write = os.pipe()
    continue_read, continue_write = os.pipe()
    process = subprocess.Popen(
        [sys.executable, "-P", "-m", "validated_memory", "init",
         "--harness-memory", str(harness), *arguments],
        cwd=adopter,
        env={
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT),
            "VALIDATED_MEMORY_TEST_RENDEZVOUS": point,
            "VALIDATED_MEMORY_TEST_READY_FD": str(ready_write),
            "VALIDATED_MEMORY_TEST_CONTINUE_FD": str(continue_read),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        pass_fds=(ready_write, continue_read),
    )
    os.close(ready_write)
    os.close(continue_read)
    readable, _, _ = select.select([ready_read], [], [], 30)
    assert readable, f"the run never reached {point}"
    assert os.read(ready_read, 32), f"the run ended before it reached {point}"
    return process, ready_read, continue_write


def _release(process, ready, proceed):
    """Let a stopped run go on, and return `(stdout, stderr)` when it ends."""
    try:
        os.write(proceed, b"x")
        return process.communicate(timeout=60)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:
            process.kill()
            process.communicate()


def _warnings(result):
    return [line for line in result.stderr.splitlines() if line.startswith("WARNING:")]


def _errors(result):
    return [line for line in result.stderr.splitlines() if line.startswith("ERROR:")]


def _start_holder(lock, script=HOLD_THE_LOCK, *arguments):
    """Start a process that holds `lock`, and return it once it does."""
    holder = subprocess.Popen(
        [sys.executable, "-c", script, str(lock), *map(str, arguments)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout.readline().strip(), "the holder died before locking"
    return holder


def _stop_holder(holder):
    holder.terminate()
    holder.wait(timeout=30)
    holder.stdout.close()


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


def test_a_relative_harness_path_is_joined_to_the_adopter_and_not_collapsed(tmp_path):
    """`--harness-memory` as typed relative reaches the journal absolute, `..` kept.

    The healthy run records a `local` transaction path, and a later refusal
    compares recorded paths with the harness path, so the path must be
    absolute. It is joined to the adopter and nothing else is done to it: what
    `..` names after a symlink is the operating system's to say."""
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
    assert link_paths == {f"{os.path.realpath(adopter)}/../harness/memory"}, recorded


def _dotdot_fixture(tmp_path):
    """A harness reachable as `<d>/alias/../harness/memory`, which is not `<d>/harness/memory`.

    `alias` is a symlink to a directory elsewhere, so the operating system
    reads `alias/..` as that directory's parent. Returns the adopter, the
    physical harness path and the spelling that goes through the alias.
    """
    (tmp_path / "elsewhere" / "deep").mkdir(parents=True)
    (tmp_path / "elsewhere" / "harness").mkdir()
    (tmp_path / "harness").mkdir()
    (tmp_path / "alias").symlink_to(
        tmp_path / "elsewhere" / "deep", target_is_directory=True
    )
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    physical = tmp_path / "elsewhere" / "harness" / "memory"
    return adopter, physical, f"{tmp_path}/alias/../harness/memory"


def test_a_dotdot_after_a_symlink_links_the_physical_path(tmp_path):
    """A healthy run creates the link where the operating system resolves `..`.

    Collapsing `alias/../harness` to `harness` lexically would create it in
    `<d>/harness`, which is not where a caller that typed the path meant."""
    adopter, physical, typed = _dotdot_fixture(tmp_path)

    result = _cli(adopter, "init", "--harness-memory", typed)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert physical.is_symlink()
    assert physical.resolve() == (adopter / "memory").resolve()
    assert not os.path.lexists(tmp_path / "harness" / "memory")
    assert f"init: created symlink {typed} -> " in result.stdout


def test_a_dotdot_after_a_symlink_in_a_recorded_path_still_names_the_entry(tmp_path):
    """A transaction recorded through `alias/..` owns the physical harness path.

    The retained WAL names `<d>/alias/../harness/memory`, which is the entry
    `<d>/elsewhere/harness/memory`. A refusal that names no path, run with the
    physical spelling, must still withhold the link."""
    adopter, physical, typed = _dotdot_fixture(tmp_path)
    assert _cli(adopter, "init", "--harness-memory", str(physical)).returncode == 0
    _point_at_stale(physical)
    transaction = _retain_wal_on(adopter, typed)
    _point_at_stale(physical, "diverged")
    _add_second_lineage(adopter)
    before = _snapshot(adopter)
    link_before = _link_target(physical)

    result = _cli(adopter, "init", "--harness-memory", str(physical))

    _assert_withheld(result, physical, link_before, f"transaction {transaction}")
    assert _snapshot(adopter) == before


def test_a_recorded_path_with_a_trailing_slash_names_the_entry(tmp_path):
    """The final name is what is compared, so a trailing `/` is not part of it.

    Only a hand-edited transaction can carry one, because the journal writes
    paths through `pathlib`; the edit is what makes the case reachable."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    transaction = _retain_wal_on(adopter, harness)
    _point_at_stale(harness, "diverged")
    wal = adopter / ".validated-memory" / "transactions" / f"{transaction}.json"
    entry = json.loads(wal.read_text("utf-8"))
    entry["intention"]["path"] += "/"
    wal.write_text(json.dumps(entry, sort_keys=True), encoding="utf-8")
    _add_second_lineage(adopter)
    link_before = _link_target(harness)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    _assert_withheld(result, harness, link_before, f"transaction {transaction}")


def test_two_names_of_one_link_are_one_entry(tmp_path):
    """A hard link to the harness symlink is the same entry under another name.

    The transaction was recorded for `other`; the run names `memory`, which is
    a second name of the same inode, so the real paths of the parents agree
    and the final names do not. Only the file identity ties them, which is
    what a filesystem that folds case relies on too. The killed run stops
    before publishing, so both names still hold the stale link."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert _cli(adopter, "init").returncode == 0
    (tmp_path / "harness").mkdir()
    harness = tmp_path / "harness" / "memory"
    other = tmp_path / "harness" / "other"
    _point_at_stale(other)
    try:
        os.link(other, harness, follow_symlinks=False)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"this platform does not hard-link symlinks: {error}")
    transaction = _retain_wal_on(adopter, other, fault="after-transaction")
    _add_second_lineage(adopter)
    link_before = _link_target(harness)
    assert _link_target(other) == link_before

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    _assert_withheld(result, harness, link_before, f"transaction {transaction}")
    assert _link_target(other) == link_before


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
    assert warning.endswith("; run journal --check"), warning
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

    A textual comparison misses all three spellings: a relative path, a path
    through a symlinked parent, and a WAL recorded through the symlink while
    the run names the real directory. Both entries exist here, so the file
    identity would catch them even without resolving the parents; the test
    after this one is the one that needs the parents resolved."""
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


def test_an_absent_link_is_compared_through_the_real_path_of_its_parent(tmp_path):
    """With no entry to compare, only the parents' real paths tie two spellings.

    The transaction was recorded for `<d>/harness/memory` and the link is
    gone; the run names it through a symlinked parent. Nothing exists to
    identify by file, so a comparison that did not resolve the parent would
    relink over the transaction's path."""
    adopter, harness = _adopted(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(harness.parent, target_is_directory=True)
    _point_at_stale(harness)
    transaction = _retain_wal_on(adopter, harness)
    harness.unlink()
    _add_second_lineage(adopter)
    before = _snapshot(adopter)

    result = _cli(adopter, "init", "--harness-memory", str(alias / "memory"))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert not os.path.lexists(harness)
    assert len(_warnings(result)) == 1, result.stderr
    assert f"transaction {transaction} names the harness path" in _warnings(result)[0]
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


@pytest.mark.parametrize(
    ("content", "reason"),
    (
        (b"{not json", "cannot be read"),
        (b"{}", "names no path"),
        (b'{"intention": {"path": 7}}', "names no path"),
    ),
)
def test_a_transaction_that_gives_no_path_withholds_the_link(
    tmp_path, content, reason
):
    """A transaction file that cannot say what it owns may have owned the link.

    Two rules apply, and each has its own reason: bytes that are not a JSON
    object are unreadable, and an object with no string path names nothing to
    compare against the harness path."""
    adopter, harness = _adopted(tmp_path)
    harness.unlink()
    directory = adopter / ".validated-memory" / "transactions"
    directory.mkdir(exist_ok=True)
    (directory / "0123456789abcdef.json").write_bytes(content)
    _add_second_lineage(adopter)
    before = _snapshot(adopter)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert not harness.exists() and not harness.is_symlink()
    assert len(_warnings(result)) == 1, result.stderr
    assert "the harness link was not restored" in _warnings(result)[0]
    assert f"transaction 0123456789abcdef {reason}" in _warnings(result)[0]
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
        "the journal refused this run and the refusal cannot be shown to "
        "leave the link alone",
    )
    assert _snapshot(adopter) == before


def test_a_refusal_the_gate_does_not_classify_says_nothing_of_a_current_link(
    tmp_path,
):
    """A link that already resolves to `memory/` has nothing left to restore.

    The same identity refusal, with the link already right: the ERROR is the
    only diagnostic, because a WARNING that the link was not restored would be
    false."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    (adopter / "memory").mkdir()
    (adopter / ".validated-memory").mkdir()
    (adopter / "journal.jsonl").write_bytes(b"")
    harness = tmp_path / "harness" / "memory"
    harness.parent.mkdir()
    harness.symlink_to(adopter / "memory", target_is_directory=True)
    link_before = _link_target(harness)
    before = _snapshot(adopter)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert len(_errors(result)) == 1, result.stderr
    assert _warnings(result) == [], result.stderr
    assert _link_target(harness) == link_before
    assert _snapshot(adopter) == before


def test_a_refusal_after_the_gate_withholds_the_link_however_the_gate_reads(
    tmp_path,
):
    """Only the two gates before any adopting effect may allow the repair.

    A topology gate that appears after the identity transition is refused
    like any late refusal, though the same gate before it would allow the
    link. The run is stopped between the two and given an unfinished legacy
    transaction on a path that is not the harness path; the link stays as it
    was, and the WARNING is the one for a refusal nothing can place."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    link_before = _link_target(harness)
    process, ready, proceed = _rendezvous_run(
        adopter, harness, "before-identity-transition"
    )
    journal = adopter / "journal.jsonl"
    adoption = json.loads(journal.read_text("utf-8").splitlines()[0])["adoption"]
    unfinished = {
        **_second_lineage_record(),
        "adoption": adoption,
        "op": "create",
        "path": "unfinished-directory",
        "stage": "prepared",
        "run": "late-run",
    }
    journal.write_bytes(
        journal.read_bytes()
        + (json.dumps(unfinished, sort_keys=True) + "\n").encode("utf-8")
    )
    stdout, stderr = _release(process, ready, proceed)

    assert process.returncode == 1, (stdout, stderr)
    assert "unresolved topology condition" in stderr
    assert _link_target(harness) == link_before
    warnings = [line for line in stderr.splitlines() if line.startswith("WARNING:")]
    assert len(warnings) == 1, stderr
    assert "the refusal cannot be shown to leave the link alone" in warnings[0]


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
    the unavailable-journal WARNING. A refusal that read the history first
    withholds instead: see the test after this one."""
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


def _root_cannot_be_locked_out():
    return os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0)


def test_an_unignored_vault_withholds_the_link_over_a_retained_transaction(tmp_path):
    """The vault gate reads the vault too: a WAL on the harness path owns it.

    Recovery leaves the transaction retained because the link no longer
    matches what it published, and `.gitignore` is a symlink `init` will not
    write through, so the run gates on the vault without any journal refusal.
    Relinking would destroy the preimage recovery needs, exactly as it would
    after a refusal, so the link stays and the WARNING says why."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    transaction = _retain_wal_on(adopter, harness)
    _point_at_stale(harness, "diverged")
    ignore = adopter / ".gitignore"
    ignore.unlink()
    (adopter / "unignored-target").write_bytes(b"adopter-owned ignore bytes\n")
    ignore.symlink_to("unignored-target")
    link_before = _link_target(harness)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "vault's ignore entry" in result.stderr
    assert _link_target(harness) == link_before
    warnings = _warnings(result)
    assert any(
        f"transaction {transaction} names the harness path" in line
        and "the harness link was not restored" in line
        for line in warnings
    ), result.stderr
    assert "re-pointed symlink" not in result.stdout


@pytest.mark.skipif(
    _root_cannot_be_locked_out(),
    reason="a read-only directory only stops a non-root user",
)
def test_a_lock_that_cannot_be_taken_still_reads_the_vault(tmp_path):
    """Being unable to lock is no licence to skip the vault.

    `.validated-memory` is read-only, so neither the run nor the repair can
    create the lock, and the journal cannot be opened. The vault can still be
    listed and read, and a transaction on the harness path in it withholds the
    link, as it would with the lock held."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    transaction = _retain_wal_on(adopter, harness)
    _point_at_stale(harness, "diverged")
    link_before = _link_target(harness)
    vault = adopter / ".validated-memory"
    lock = vault / "lock"
    if lock.exists():
        lock.unlink()
    vault.chmod(0o500)
    try:
        result = _cli(adopter, "init", "--harness-memory", str(harness))
    finally:
        vault.chmod(0o700)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "journal could not be opened" in result.stderr
    assert _link_target(harness) == link_before
    assert len(_warnings(result)) == 1, result.stderr
    assert f"transaction {transaction} names the harness path" in _warnings(result)[0]


@pytest.mark.skipif(
    _root_cannot_be_locked_out(),
    reason="a read-only directory only stops a non-root user",
)
def test_a_lock_that_cannot_be_taken_over_a_clean_vault_restores_the_link(tmp_path):
    """Control for the test above: nothing in the vault, so §4's promise stands.

    The vault was read without the lock and holds nothing that names the
    harness path, so the link is restored, and the WARNING is the
    unavailable-journal one."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    vault = adopter / ".validated-memory"
    vault.chmod(0o500)
    try:
        result = _cli(adopter, "init", "--harness-memory", str(harness))
    finally:
        vault.chmod(0o700)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert harness.resolve() == (adopter / "memory").resolve()
    assert len(_warnings(result)) == 1, result.stderr
    assert "the journal is unavailable" in _warnings(result)[0]


@pytest.mark.skipif(
    _root_cannot_be_locked_out(),
    reason="a read-only directory only stops a non-root user",
)
def test_a_lock_that_cannot_be_taken_after_a_readable_refusal_withholds_the_link(
    tmp_path,
):
    """A readable refusal has no exception for the lock either.

    The run is stopped between the gate and the repair while the vault turns
    read-only, so the lock cannot be created. The vault could still be read,
    but the history rules need the lock, and the link is withheld."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    _add_second_lineage(adopter)
    link_before = _link_target(harness)
    vault = adopter / ".validated-memory"
    process, ready, proceed = _rendezvous_run(
        adopter, harness, "before-harness-repair-lock"
    )
    vault.chmod(0o500)
    try:
        stdout, stderr = _release(process, ready, proceed)
    finally:
        vault.chmod(0o700)

    assert process.returncode == 1, (stdout, stderr)
    assert _link_target(harness) == link_before
    warnings = [line for line in stderr.splitlines() if line.startswith("WARNING:")]
    assert len(warnings) == 1, stderr
    assert "the lock or the vault could not be read" in warnings[0]


def test_a_final_name_that_differs_only_in_case_is_the_same_entry(tmp_path):
    """A filesystem that folds case names `Memory` and `memory` one entry.

    The transaction was recorded for `Memory` and stopped before publishing,
    so neither name exists and no file identity ties them; only the final
    names compared without regard to case do. On a filesystem that keeps case
    the comparison withholds more than it needs to, which is the direction
    it may err in."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert _cli(adopter, "init").returncode == 0
    directory = tmp_path / "harness"
    directory.mkdir()
    transaction = _retain_wal_on(
        adopter, directory / "Memory", fault="after-transaction"
    )
    _add_second_lineage(adopter)
    before = _snapshot(adopter)

    result = _cli(adopter, "init", "--harness-memory", str(directory / "memory"))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert not os.path.lexists(directory / "memory")
    assert not os.path.lexists(directory / "Memory")
    assert len(_warnings(result)) == 1, result.stderr
    assert f"transaction {transaction} names the harness path" in _warnings(result)[0]
    assert _snapshot(adopter) == before


@pytest.mark.skipif(
    not hasattr(os, "mkfifo"), reason="this platform has no named pipes"
)
@pytest.mark.parametrize("target", ("pipe", "regular-file"))
@pytest.mark.parametrize("where", ("transactions", "preimages"))
def test_a_vault_entry_that_is_not_a_regular_file_withholds_and_is_never_opened(
    tmp_path, where, target
):
    """A symlink in the vault is not a transaction, and opening it can hang.

    The entry points at a named pipe, which blocks whoever opens it for
    reading, or at a file outside the vault, which would be read as if the
    vault held it. The repair lists the vault by entry type, without
    following links, and withholds before opening anything, so the run ends,
    with the link as it was. The journal is unreadable here so that the run's
    own snapshot does not read the vault first: it would open the symlink
    itself, which is outside what the repair decides."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    _break_journal_with_a_loop(adopter)
    directory = adopter / ".validated-memory" / where
    directory.mkdir(exist_ok=True)
    outside = tmp_path / "outside"
    if target == "pipe":
        os.mkfifo(outside)
    else:
        outside.write_bytes(b"{}")
    name = "x.json" if where == "transactions" else "a" * 64
    (directory / name).symlink_to(outside)
    link_before = _link_target(harness)

    try:
        result = subprocess.run(
            [sys.executable, "-P", "-m", "validated_memory", "init",
             "--harness-memory", str(harness)],
            cwd=adopter,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("the run opened the pipe and hung")

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert _link_target(harness) == link_before
    assert len(_warnings(result)) == 1, result.stderr
    assert f"{where}/{name}" in _warnings(result)[0]
    assert "not a regular file" in _warnings(result)[0]


def test_a_vault_that_cannot_be_read_after_a_readable_refusal_withholds_the_link(
    tmp_path,
):
    """The exception for an unreadable journal does not reach a readable one.

    `transactions` is a file, so the vault cannot be read, and the history
    still says what is wrong with it: the rules cannot be shown to hold, and
    the link is withheld with the vault as the reason."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    _add_second_lineage(adopter)
    directory = adopter / ".validated-memory" / "transactions"
    if directory.exists():
        shutil.rmtree(directory)
    directory.write_text("not a directory")
    link_before = _link_target(harness)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert _link_target(harness) == link_before
    assert len(_warnings(result)) == 1, result.stderr
    assert "the lock or the vault could not be read" in _warnings(result)[0]
    assert _warnings(result)[0].endswith("; run journal --check")


@pytest.mark.skipif(
    os.name != "posix" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="an unlistable directory is only unlistable to a non-root user",
)
def test_a_preimage_directory_that_cannot_be_listed_restores_the_link_unguarded(
    tmp_path,
):
    """Both vault directories are listed before residue is looked for.

    `retained_residue` reports a directory it cannot list as residue, which
    would withhold the link; listing `preimages` first makes it the
    unreadable-vault case, as it is for `transactions`."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    _break_journal_with_a_loop(adopter)
    preimages = adopter / ".validated-memory" / "preimages"
    preimages.mkdir(exist_ok=True)
    preimages.chmod(0)
    try:
        result = _cli(adopter, "init", "--harness-memory", str(harness))
    finally:
        preimages.chmod(0o700)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert harness.resolve() == (adopter / "memory").resolve()
    assert len(_warnings(result)) == 1, result.stderr
    assert "the journal is unavailable" in _warnings(result)[0]


def test_a_transaction_shared_by_both_histories_withholds_the_link(tmp_path):
    """A damaged topology whose only reported conditions are gates is still damaged.

    The same transaction id in the repository journal and in the vault's is
    identity damage, but the history conditions that survive are the legacy
    ones, all of them topology gates. Only the unusable snapshot withholds the
    link here, and its reason says the topology is damaged, not that the
    history is."""
    adopter, harness = _adopted(tmp_path)
    harness.unlink()
    local = (adopter / ".validated-memory" / "local.jsonl").read_text("utf-8")
    shared = json.loads(local.splitlines()[0])
    duplicate = {
        **_second_lineage_record(),
        "adoption": shared["adoption"],
        "op": "create",
        "path": "duplicate-directory",
        "stage": "prepared",
        "run": "duplicate-run",
        "transaction": shared["transaction"],
    }
    journal = adopter / "journal.jsonl"
    journal.write_bytes(
        journal.read_bytes()
        + (json.dumps(duplicate, sort_keys=True) + "\n").encode("utf-8")
    )
    checked = _cli(adopter, "journal", "--check")
    assert "recorded across repo and local histories" in checked.stderr
    before = _snapshot(adopter)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert not harness.exists() and not harness.is_symlink()
    assert len(_warnings(result)) == 1, result.stderr
    assert "the history topology is damaged" in _warnings(result)[0]
    assert _snapshot(adopter) == before


def test_an_unconfirmed_effect_of_the_run_withholds_the_link_and_says_so(tmp_path):
    """A visible effect whose durability is unconfirmed is not a refusal to place.

    The first history append cannot confirm its directory, so the run may
    already have changed the project. The link is withheld, and the WARNING
    names the unconfirmed effect instead of the generic refusal."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    (adopter / "memory").mkdir()
    harness = tmp_path / "harness" / "memory"
    harness.parent.mkdir()
    _point_at_stale(harness)
    link_before = _link_target(harness)

    result = _cli(
        adopter,
        "init",
        "--harness-memory",
        str(harness),
        env={"VALIDATED_MEMORY_PERSISTENCE_FAULT": "append:journal.jsonl"},
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert _link_target(harness) == link_before
    assert len(_warnings(result)) == 1, result.stderr
    assert "its durability is unconfirmed" in _warnings(result)[0]
    assert "cannot be shown to leave the link alone" not in _warnings(result)[0]
    assert _warnings(result)[0].endswith("; run journal --check")


def test_the_guard_takes_the_run_wide_lock_after_the_gate(tmp_path):
    """The lock the gate released is taken again before the vault is read.

    The run is stopped between the gate and the repair while another process
    takes the lock, so the repair has to wait for it, and then withholds. A
    guard that read the vault without the lock would relink at once, and
    a transaction opened in that window would be relinked over."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    _add_second_lineage(adopter)
    link_before = _link_target(harness)
    process, ready, proceed = _rendezvous_run(
        adopter, harness, "before-harness-repair-lock"
    )
    lock = adopter / ".validated-memory" / "lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_THE_LOCK, str(lock)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip(), "the holder died before locking"
        started = time.monotonic()
        stdout, stderr = _release(process, ready, proceed)
        elapsed = time.monotonic() - started
    finally:
        holder.terminate()
        holder.wait(timeout=30)
        holder.stdout.close()

    assert process.returncode == 1, (stdout, stderr)
    assert _link_target(harness) == link_before
    warnings = [line for line in stderr.splitlines() if line.startswith("WARNING:")]
    assert len(warnings) == 1, stderr
    assert "run-wide lock" in warnings[0]
    assert elapsed < 2 * LOCK_DEADLINE_SECONDS, elapsed


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
    # Without `--lock-wait` the run still waits the lock's own deadline once.
    assert elapsed >= LOCK_DEADLINE_SECONDS - 1, elapsed
    assert elapsed < 2 * LOCK_DEADLINE_SECONDS, elapsed


# --- the lock wait a run is given ----------------------------------------------


def _write_the_lock_of_a_dead_process(lock):
    """Write into `lock` a pid that names no running process."""
    child = subprocess.Popen([sys.executable, "-c", ""])
    child.wait(timeout=30)
    for candidate in range(child.pid, child.pid + 10000):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text(f"{candidate}\n", encoding="ascii")
            return candidate
        except OSError:
            continue
    raise AssertionError("every probed pid was in use")


def test_lock_wait_bounds_the_wait_for_a_live_holder(tmp_path):
    """`--lock-wait 1` gives up after about a second, and says what it says today.

    Same outcome kind as the default wait, only sooner: the busy ERROR, exit 1,
    the link left as it was, and the one WARNING that names the run-wide lock."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    link_before = _link_target(harness)
    lock = adopter / ".validated-memory" / "lock"
    holder = _start_holder(lock)
    try:
        started = time.monotonic()
        result = _cli(
            adopter, "init", "--harness-memory", str(harness), "--lock-wait", "1"
        )
        elapsed = time.monotonic() - started
    finally:
        _stop_holder(holder)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "another validated-memory process holds" in result.stderr
    assert _link_target(harness) == link_before
    assert len(_warnings(result)) == 1, result.stderr
    assert "run-wide lock" in _warnings(result)[0]
    assert 0.9 <= elapsed < 3, elapsed


def test_lock_wait_zero_does_not_wait_for_a_live_holder(tmp_path):
    """A zero budget refuses on the first attempt: no waiting at all."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    link_before = _link_target(harness)
    lock = adopter / ".validated-memory" / "lock"
    holder = _start_holder(lock)
    try:
        started = time.monotonic()
        result = _cli(
            adopter, "init", "--harness-memory", str(harness), "--lock-wait", "0"
        )
        elapsed = time.monotonic() - started
    finally:
        _stop_holder(holder)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "another validated-memory process holds" in result.stderr
    assert _link_target(harness) == link_before
    assert len(_warnings(result)) == 1, result.stderr
    assert elapsed < RUN_SLACK_SECONDS, elapsed


def test_a_holder_that_finishes_inside_the_wait_does_not_withhold_the_link(tmp_path):
    """A brief collision between two starting sessions still relinks.

    The holder lets go after a second, well inside the hook's budget: the run
    takes the lock, restores the link and exits 0."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    lock = adopter / ".validated-memory" / "lock"
    holder = _start_holder(lock, HOLD_THEN_RELEASE, 1)
    try:
        result = _cli(
            adopter,
            "init",
            "--harness-memory",
            str(harness),
            "--lock-wait",
            str(HOOK_LOCK_WAIT_SECONDS),
        )
    finally:
        _stop_holder(holder)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert harness.resolve() == (adopter / "memory").resolve()
    assert _warnings(result) == [], result.stderr


def test_the_two_lock_acquisitions_of_one_run_share_one_wait(tmp_path):
    """The repair after a refusal spends what the run has left, not a new wait.

    The first holder keeps the lock for 2.5 s of a 6 s budget, so the run's
    own acquisition succeeds late. The run is then refused before any
    effect, and at the rendezvous a second holder takes the lock the run just
    released. A repair that started its own 6 s wait would end after about
    8.5 s; one that shares the deadline ends at about 6 s."""
    budget = 6
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    _add_second_lineage(adopter)
    link_before = _link_target(harness)
    lock = adopter / ".validated-memory" / "lock"
    holders = []
    process = None
    try:
        holders.append(_start_holder(lock, HOLD_THEN_RELEASE, 2.5))
        started = time.monotonic()
        process, ready, proceed = _rendezvous_run(
            adopter,
            harness,
            "before-harness-repair-lock",
            "--lock-wait",
            str(budget),
        )
        reached = time.monotonic() - started
        holders.append(_start_holder(lock))
        stdout, stderr = _release(process, ready, proceed)
        elapsed = time.monotonic() - started
    finally:
        for holder in holders:
            _stop_holder(holder)

    assert reached >= 2, f"the first acquisition did not wait: {reached}"
    assert process.returncode == 1, (stdout, stderr)
    assert "unresolved topology condition" in stderr
    assert _link_target(harness) == link_before
    warnings = [line for line in stderr.splitlines() if line.startswith("WARNING:")]
    assert len(warnings) == 1, stderr
    assert "run-wide lock" in warnings[0]
    assert elapsed < budget + 1.5, elapsed


@pytest.mark.parametrize("wait", ("0", str(HOOK_LOCK_WAIT_SECONDS)))
def test_a_lock_whose_owner_is_gone_is_broken_and_taken_by_the_same_run(
    tmp_path, wait
):
    """A lock left by a process that exited is broken and then taken, whatever the budget.

    With a zero budget the deadline has passed by the first attempt, and the
    run that broke the lock still takes it: the link is restored and the run
    exits 0."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    lock = adopter / ".validated-memory" / "lock"
    _write_the_lock_of_a_dead_process(lock)

    started = time.monotonic()
    result = _cli(
        adopter, "init", "--harness-memory", str(harness), "--lock-wait", wait
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert harness.resolve() == (adopter / "memory").resolve()
    assert not lock.exists()
    assert elapsed < RUN_SLACK_SECONDS + 1, elapsed


@pytest.mark.parametrize("wait", ("0", str(HOOK_LOCK_WAIT_SECONDS)))
def test_an_empty_lock_older_than_the_horizon_is_broken_and_taken_by_the_same_run(
    tmp_path, wait
):
    """A lock with no pid in it is broken on age alone, and taken, whatever the budget."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    lock = adopter / ".validated-memory" / "lock"
    lock.write_bytes(b"")
    ancient = time.time() - 3600
    os.utime(lock, (ancient, ancient))

    started = time.monotonic()
    result = _cli(
        adopter, "init", "--harness-memory", str(harness), "--lock-wait", wait
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert harness.resolve() == (adopter / "memory").resolve()
    assert not lock.exists()
    assert elapsed < RUN_SLACK_SECONDS + 1, elapsed


@pytest.mark.parametrize("wait", ("0", str(HOOK_LOCK_WAIT_SECONDS)))
def test_a_young_empty_lock_is_refused_within_the_wait(tmp_path, wait):
    """A lock with no pid that is not old enough is not broken.

    Nothing says who holds it, so the run waits its budget and withholds the
    link: the same outcome kind as for a live holder."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    link_before = _link_target(harness)
    lock = adopter / ".validated-memory" / "lock"
    lock.write_bytes(b"")

    started = time.monotonic()
    result = _cli(
        adopter, "init", "--harness-memory", str(harness), "--lock-wait", wait
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "another validated-memory process holds" in result.stderr
    assert _link_target(harness) == link_before
    assert len(_warnings(result)) == 1, result.stderr
    assert "run-wide lock" in _warnings(result)[0]
    assert lock.exists()
    assert float(wait) - 0.1 <= elapsed < float(wait) + RUN_SLACK_SECONDS, elapsed


# --- the harness path is read again before the relink -------------------------

# The two routes that relink under the guard: the locked one, reached here by a
# topology refusal, and the unlocked `_unreadable_repair`, reached by a journal
# that cannot be read together with a vault that cannot be listed.
LOCKED = "locked"
UNLOCKED = pytest.param(
    "unlocked",
    marks=pytest.mark.skipif(
        _root_cannot_be_locked_out(),
        reason="an unlistable directory is only unlistable to a non-root user",
    ),
)
UNIGNORED = "unignored"
CHANGED = "the harness path changed while the repair waited"

# Where the run is stopped so that the harness path can be swapped: before the
# repair takes any lock, and after the repair has made the parent directory and
# staged the temporary link, immediately before the rename that publishes it.
POINTS = (
    pytest.param("before-harness-repair-lock", id="before-lock"),
    pytest.param("after-harness-link-staged", id="staged"),
)


def _run_with_a_swap(
    tmp_path, route, swap, point="before-harness-repair-lock"
):
    """Stop `init` at `point`, change the harness path, and let it go.

    `swap(adopter, harness)` runs while the run waits at the rendezvous, after
    it has inspected the harness path. Returns
    `(adopter, harness, process, stdout, stderr)`.
    """
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    preimages = None
    if route == LOCKED:
        _add_second_lineage(adopter)
    elif route == UNIGNORED:
        ignore = adopter / ".gitignore"
        ignore.unlink()
        (adopter / "unignored-target").write_bytes(b"adopter-owned ignore bytes\n")
        ignore.symlink_to("unignored-target")
    else:
        _break_journal_with_a_loop(adopter)
        preimages = adopter / ".validated-memory" / "preimages"
        preimages.mkdir(exist_ok=True)
        preimages.chmod(0)
    try:
        process, ready, proceed = _rendezvous_run(adopter, harness, point)
        if point == "after-harness-link-staged":
            assert len(_staged_links(harness)) == 1, "the link is not staged yet"
        swap(adopter, harness)
        stdout, stderr = _release(process, ready, proceed)
    finally:
        if preimages is not None:
            preimages.chmod(0o700)
    return adopter, harness, process, stdout, stderr


def _staged_links(harness):
    """The temporary links a relink stages beside the harness path."""
    return sorted(
        name
        for name in os.listdir(harness.parent)
        if name.startswith(f".{harness.name}.") and name.endswith(".tmp")
    )


def _stderr_warnings(stderr):
    return [line for line in stderr.splitlines() if line.startswith("WARNING:")]


def _assert_withheld_as_changed(harness, process, stdout, stderr):
    assert process.returncode == 1, (stdout, stderr)
    warnings = _stderr_warnings(stderr)
    assert len(warnings) == 1, stderr
    assert str(harness) in warnings[0]
    assert "the harness link was not restored" in warnings[0]
    assert CHANGED in warnings[0], warnings[0]
    assert not warnings[0].endswith("; run journal --check"), warnings[0]
    assert "created symlink" not in stdout
    assert "re-pointed symlink" not in stdout
    assert _staged_links(harness) == []


@pytest.mark.parametrize("point", POINTS)
@pytest.mark.parametrize("route", (LOCKED, UNLOCKED))
@pytest.mark.parametrize("kind", ("file", "directory"))
def test_a_real_node_that_appears_at_the_harness_path_is_never_replaced(
    tmp_path, route, kind, point
):
    """The data at the path is the adopter's, and the link is not made over it.

    Something else puts a real file, or a real directory holding a file, where
    the run saw a symlink, after the run inspected the path and before it
    relinks. The run reads the path again, sees another kind of entry, and
    withholds: the content is intact, no link was made, and the WARNING says
    the path changed and does not send the reader to `journal --check`."""

    def swap(adopter, harness):
        harness.unlink()
        if kind == "file":
            harness.write_bytes(b"the adopter's own data\n")
        else:
            harness.mkdir()
            (harness / "keep.txt").write_bytes(b"the adopter's own data\n")

    adopter, harness, process, stdout, stderr = _run_with_a_swap(
        tmp_path, route, swap, point
    )

    _assert_withheld_as_changed(harness, process, stdout, stderr)
    assert not harness.is_symlink()
    if kind == "file":
        assert harness.read_bytes() == b"the adopter's own data\n"
    else:
        assert (harness / "keep.txt").read_bytes() == b"the adopter's own data\n"


@pytest.mark.parametrize("point", POINTS)
@pytest.mark.parametrize("route", (LOCKED, UNLOCKED))
def test_a_link_that_is_re_pointed_elsewhere_while_the_repair_waited_is_kept(
    tmp_path, route, point
):
    """Another process re-pointed the link on purpose; its target stays."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    def swap(adopter, harness):
        harness.unlink()
        harness.symlink_to(elsewhere, target_is_directory=True)

    adopter, harness, process, stdout, stderr = _run_with_a_swap(
        tmp_path, route, swap, point
    )

    _assert_withheld_as_changed(harness, process, stdout, stderr)
    assert os.readlink(harness) == str(elsewhere)


@pytest.mark.parametrize("point", POINTS)
@pytest.mark.parametrize("route", (LOCKED, UNLOCKED))
def test_a_correct_link_published_by_someone_else_is_left_and_not_reported(
    tmp_path, route, point
):
    """Another session already did the repair: nothing is left to restore.

    The run says nothing of it, as for a link that was correct at the start:
    the only finding is the refusal's own ERROR."""

    def swap(adopter, harness):
        harness.unlink()
        harness.symlink_to(
            (adopter / "memory").resolve(), target_is_directory=True
        )

    adopter, harness, process, stdout, stderr = _run_with_a_swap(
        tmp_path, route, swap, point
    )

    assert process.returncode == 1, (stdout, stderr)
    assert harness.resolve() == (adopter / "memory").resolve()
    assert _stderr_warnings(stderr) == [], stderr
    assert "created symlink" not in stdout
    assert "re-pointed symlink" not in stdout
    assert _staged_links(harness) == []


@pytest.mark.parametrize("point", POINTS)
def test_a_correct_link_published_by_someone_else_is_kept_when_the_vault_gated(
    tmp_path, point
):
    """A run that gated on the vault reports a link that is already correct.

    `.gitignore` is a symlink `init` will not write through, so the run gates
    on the vault without a journal refusal, and the repair runs unrecorded. As
    for a link that was correct at the start, the run says it kept the link
    and counts it, and raises no WARNING."""

    def swap(adopter, harness):
        harness.unlink()
        harness.symlink_to(
            (adopter / "memory").resolve(), target_is_directory=True
        )

    adopter, harness, process, stdout, stderr = _run_with_a_swap(
        tmp_path, UNIGNORED, swap, point
    )

    assert process.returncode == 1, (stdout, stderr)
    assert "vault's ignore entry" in stderr, stderr
    assert harness.resolve() == (adopter / "memory").resolve()
    assert f"init: kept symlink {harness}\n" in stdout, stdout
    assert stdout.endswith("init: 1 item(s) confirmed, 1 gate(s)\n"), stdout
    assert _stderr_warnings(stderr) == [], stderr
    assert _staged_links(harness) == []


@pytest.mark.parametrize("point", POINTS)
@pytest.mark.parametrize("route", (LOCKED, UNLOCKED))
def test_a_link_whose_target_became_memory_while_the_repair_waited_is_not_republished(
    tmp_path, route, point
):
    """Only where the path resolves to decides that nothing is left to restore.

    The link text is the same one the run inspected, but the directory it names
    was replaced by a link to `memory/`, so the harness path resolves to
    `memory/` by the time the run would relink. It is left as it is, nothing is
    reported, and the link text is not published again."""

    def swap(adopter, harness):
        stale = harness.parent / "stale"
        stale.rmdir()
        stale.symlink_to((adopter / "memory").resolve(), target_is_directory=True)

    adopter, harness, process, stdout, stderr = _run_with_a_swap(
        tmp_path, route, swap, point
    )

    assert process.returncode == 1, (stdout, stderr)
    assert harness.resolve() == (adopter / "memory").resolve()
    assert os.readlink(harness) == str(harness.parent / "stale")
    assert _stderr_warnings(stderr) == [], stderr
    assert "created symlink" not in stdout
    assert "re-pointed symlink" not in stdout
    assert _staged_links(harness) == []


@pytest.mark.parametrize("point", POINTS)
@pytest.mark.parametrize("route", (LOCKED, UNLOCKED))
def test_a_link_whose_target_chain_became_cyclic_while_the_repair_waited_is_blocked(
    tmp_path, route, point
):
    """A target chain that cannot be resolved is never taken for approval.

    The link text is the inspected one, but the directory it names was replaced
    by a link to itself, so resolving the harness path fails. That is a harness
    path that cannot be read: the relink is blocked, the link is left as it was,
    and the reason is not "changed"."""

    def swap(adopter, harness):
        stale = harness.parent / "stale"
        stale.rmdir()
        stale.symlink_to(stale.name)

    adopter, harness, process, stdout, stderr = _run_with_a_swap(
        tmp_path, route, swap, point
    )

    assert process.returncode == 1, (stdout, stderr)
    warnings = _stderr_warnings(stderr)
    assert len(warnings) == 1, stderr
    assert "the harness link was not restored" in warnings[0]
    assert "the harness path could not be read" in warnings[0], warnings[0]
    assert CHANGED not in warnings[0]
    assert not warnings[0].endswith("; run journal --check"), warnings[0]
    assert os.readlink(harness) == str(harness.parent / "stale")
    assert "created symlink" not in stdout
    assert "re-pointed symlink" not in stdout
    assert _staged_links(harness) == []


def test_a_staged_link_is_removed_when_the_second_reading_raises(tmp_path):
    """The staged link is cleaned up whatever ends the replacement.

    The run is left at the rendezvous with the link staged and is not let go,
    so the rendezvous times out and raises inside the callback that makes the
    second reading. The run ends in an error, and no staged link is left."""
    adopter, harness = _adopted(tmp_path)
    _point_at_stale(harness)
    _add_second_lineage(adopter)
    process, ready, proceed = _rendezvous_run(
        adopter, harness, "after-harness-link-staged"
    )
    try:
        assert len(_staged_links(harness)) == 1, "the link is not staged yet"
        stdout, stderr = process.communicate(timeout=60)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode != 0, (stdout, stderr)
    assert "test rendezvous at after-harness-link-staged timed out" in stderr
    assert _staged_links(harness) == []
    assert os.readlink(harness) == str(harness.parent / "stale")


@pytest.mark.parametrize("route", (LOCKED, UNLOCKED))
@pytest.mark.parametrize("recheck", ("approves", "declines"))
def test_a_staged_link_replaced_by_a_file_is_neither_published_nor_removed(
    tmp_path, route, recheck
):
    """The staged name is checked again before it is renamed or unlinked.

    Something replaces the temporary link, while the run waits with it staged,
    by a regular file with content. Whether the second reading of the harness
    path approves the relink or declines it, the run neither renames the file
    over the harness path nor unlinks it: the file is intact, the harness path is
    untouched, and the WARNING names the staged path."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    staged = {}

    def swap(adopter, harness):
        (name,) = _staged_links(harness)
        staged["path"] = harness.parent / name
        staged["path"].unlink()
        staged["path"].write_bytes(b"the adopter's own data\n")
        if recheck == "declines":
            harness.unlink()
            harness.symlink_to(elsewhere, target_is_directory=True)

    adopter, harness, process, stdout, stderr = _run_with_a_swap(
        tmp_path, route, swap, "after-harness-link-staged"
    )

    assert process.returncode == 1, (stdout, stderr)
    warnings = _stderr_warnings(stderr)
    assert len(warnings) == 1, stderr
    assert "the harness link was not restored" in warnings[0]
    assert str(staged["path"]) in warnings[0], warnings[0]
    assert "neither published nor removed" in warnings[0], warnings[0]
    assert not warnings[0].endswith("; run journal --check"), warnings[0]
    assert staged["path"].read_bytes() == b"the adopter's own data\n"
    assert not staged["path"].is_symlink()
    assert _staged_links(harness) == [staged["path"].name]
    expected = elsewhere if recheck == "declines" else harness.parent / "stale"
    assert os.readlink(harness) == str(expected)
    assert "created symlink" not in stdout
    assert "re-pointed symlink" not in stdout


@pytest.mark.parametrize("point", POINTS)
@pytest.mark.parametrize("route", (LOCKED, UNLOCKED))
def test_the_same_link_published_again_is_not_a_change(tmp_path, route, point):
    """Identity is the entry's type and link text, not its inode.

    A session that relinks to the same target publishes a new inode. The run
    that waited sees the entry it inspected and restores the link."""

    def swap(adopter, harness):
        target = os.readlink(harness)
        harness.unlink()
        harness.symlink_to(target, target_is_directory=True)

    adopter, harness, process, stdout, stderr = _run_with_a_swap(
        tmp_path, route, swap, point
    )

    assert process.returncode == 1, (stdout, stderr)
    assert harness.resolve() == (adopter / "memory").resolve()
    warnings = _stderr_warnings(stderr)
    assert len(warnings) == 1, stderr
    assert "restoring it anyway" in warnings[0]
    assert CHANGED not in stderr
    assert _staged_links(harness) == []


@pytest.mark.parametrize("refused", (False, True))
def test_a_harness_symlink_that_loops_is_left_alone_with_a_warning(tmp_path, refused):
    """A link that names itself cannot be resolved, so it is reported, not raised.

    `harness/memory -> memory` is outside the project and resolves to nothing:
    resolving it raises. The run leaves it as it is with the WARNING for a
    harness path that cannot be read, on a healthy run and on one the journal
    refused, and it does not end in a traceback."""
    adopter, harness = _adopted(tmp_path)
    harness.unlink()
    harness.symlink_to(harness.name)
    if refused:
        _add_second_lineage(adopter)

    result = _cli(adopter, "init", "--harness-memory", str(harness))

    assert result.returncode == (1 if refused else 0), (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert len(_warnings(result)) == 1, result.stderr
    assert "the harness path could not be read" in _warnings(result)[0]
    assert not _warnings(result)[0].endswith("; run journal --check")
    assert os.readlink(harness) == harness.name
    assert "created symlink" not in result.stdout
    assert "re-pointed symlink" not in result.stdout


@pytest.mark.skipif(
    _root_cannot_be_locked_out(),
    reason="an unsearchable directory is only unsearchable to a non-root user",
)
def test_a_harness_path_that_cannot_be_looked_at_is_not_relinked(tmp_path):
    """An `lstat` failure other than ENOENT withholds, at the inspection.

    The harness directory cannot be searched, so nothing can say what is at the
    path. The run reports that and leaves it: no traceback, no gate."""
    adopter, harness = _adopted(tmp_path)
    stale = _point_at_stale(harness)
    harness.parent.chmod(0)
    try:
        result = _cli(adopter, "init", "--harness-memory", str(harness))
    finally:
        harness.parent.chmod(0o700)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert len(_warnings(result)) == 1, result.stderr
    assert "the harness path could not be read" in _warnings(result)[0]
    assert not _warnings(result)[0].endswith("; run journal --check")
    assert os.readlink(harness) == str(stale)


@pytest.mark.skipif(
    _root_cannot_be_locked_out(),
    reason="an unsearchable directory is only unsearchable to a non-root user",
)
def test_a_harness_path_that_cannot_be_looked_at_before_the_relink_is_not_relinked(
    tmp_path,
):
    """The same failure at the second reading withholds too.

    The directory can be searched while the run stages the temporary link, and
    cannot be when the run reads the path again just before the rename. The
    staged link cannot be removed from it either, so it is left behind: the
    removal of a staged link is best effort."""

    def swap(adopter, harness):
        harness.parent.chmod(0)

    try:
        adopter, harness, process, stdout, stderr = _run_with_a_swap(
            tmp_path, LOCKED, swap, "after-harness-link-staged"
        )
    finally:
        (tmp_path / "harness").chmod(0o700)

    assert process.returncode == 1, (stdout, stderr)
    warnings = _stderr_warnings(stderr)
    assert len(warnings) == 1, stderr
    assert "the harness path could not be read" in warnings[0]
    assert not warnings[0].endswith("; run journal --check")
    assert os.readlink(harness) == str(tmp_path / "harness" / "stale")
    assert len(_staged_links(harness)) == 1


# --- what no subprocess can see -------------------------------------------------


def test_the_decision_and_the_relink_share_one_critical_section():
    """Structural pin: the extent of a lock is not observable from outside.

    ADR 0029 rejects "check first and relink after the lock is released": a
    transaction can be opened between the two. So `guarded_harness_repair`
    hands `relink` to `_relink_with_recheck` only inside the `try` whose `finally` releases
    the lock, in the same block that runs the vault and history check; its only
    other route to `relink` is `_unreadable_repair`, for a lock or a vault that
    cannot be read. `_relink_with_recheck` is the one place `relink` is called, with the
    check that reads the harness path again as its argument, and
    `replace_symlink` calls that check with the link staged, and only the second
    identification of the staged link and the check of the answer stand between
    it and the `os.replace` that publishes it. This proves the shape,
    not the exclusion itself: the held-lock tests are what show another
    process is kept out, and the swap tests, which stop the run before the lock
    and again after the link is staged, are what show the reading is acted
    on."""
    protocol = ast.parse(
        (REPO_ROOT / "validated_memory" / "journal" / "protocol.py").read_text(
            encoding="utf-8"
        )
    )

    def function(tree, name):
        return next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        )

    def calls(nodes):
        return [
            ast.unparse(node.func)
            for statement in nodes
            for node in ast.walk(statement)
            if isinstance(node, ast.Call)
        ]

    guarded = function(protocol, "guarded_harness_repair")
    releasing = [
        node
        for node in ast.walk(guarded)
        if isinstance(node, ast.Try)
        and "lock.__exit__" in calls(node.finalbody)
    ]
    assert len(releasing) == 1, "one try must release the lock"
    assert "_harness_repair_obstacle" in calls(releasing[0].body)
    assert calls(releasing[0].body).count("_relink_with_recheck") == 1
    assert calls(guarded.body).count("_relink_with_recheck") == 1, (
        "relink is handed over outside the block that holds the lock"
    )
    unreadable = function(protocol, "_unreadable_repair")
    assert calls(unreadable.body).count("_relink_with_recheck") == 1
    for route in (guarded, unreadable):
        assert "relink" not in calls(route.body), (
            f"{route.name} calls relink itself"
        )
        assert [
            ast.unparse(node)
            for node in ast.walk(route)
            if isinstance(node, ast.Call)
            and ast.unparse(node.func) == "_relink_with_recheck"
        ] == ["_relink_with_recheck(relink, recheck)"]

    publish = function(protocol, "_relink_with_recheck")
    assert [
        ast.unparse(node)
        for node in ast.walk(publish)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "relink"
    ] == ["relink(before_replace)"]
    before = function(publish, "before_replace")
    assert calls(before.body) == ["rendezvous_at", "recheck"]
    assert ast.unparse(before.body[-1]) == "return recheck()"
    handlers = [
        node for node in ast.walk(publish) if isinstance(node, ast.ExceptHandler)
    ]
    assert [ast.unparse(node.type) for node in handlers] == [
        "ReplaceDeclined",
        "StagedLinkChanged",
    ]

    durable = ast.parse(
        (REPO_ROOT / "validated_memory" / "journal" / "durable.py").read_text(
            encoding="utf-8"
        )
    )
    apply = function(function(durable, "replace_symlink"), "apply")
    (attempt,) = [node for node in apply.body if isinstance(node, ast.Try)]
    assert attempt.handlers == [] and attempt.finalbody, (
        "the staged link is cleaned up by a `finally`, whatever raises"
    )
    steps = [ast.unparse(statement) for statement in attempt.body]
    rename = steps.index("os.replace(temporary, path)")
    assert steps[rename + 1] == "published = True"
    assert steps[rename - 1] == (
        "if answer is not None:\n    raise ReplaceDeclined(answer)"
    )
    assert steps[rename - 2] == (
        "if not intact:\n    raise StagedLinkChanged(temporary)"
    )
    assert steps[rename - 3].startswith("try:\n")
    assert "_examine_staged(temporary, target, staged)" in steps[rename - 3]
    assert steps[rename - 4] == (
        "if before_replace is not None:\n    answer = before_replace()"
    )
    assert steps.index("os.symlink(target, temporary)") < rename - 4

    # Once `lstat` has shown the staged name to be that symlink, a failure to
    # read its text means the name changed under the examination: any `OSError`
    # is "not intact", never an error the caller may take for intact. No CLI
    # case reaches it, because it needs a swap between the `lstat` and the
    # `readlink`, so this is a pin on the shape.
    examine = function(durable, "_examine_staged")
    (reading,) = [
        node
        for node in ast.walk(examine)
        if isinstance(node, ast.Try)
        and any(
            isinstance(call, ast.Call)
            and ast.unparse(call.func) == "os.readlink"
            for statement in node.body
            for call in ast.walk(statement)
        )
    ]
    assert [ast.unparse(handler.type) for handler in reading.handlers] == [
        "OSError"
    ]
    assert [ast.unparse(statement) for statement in reading.handlers[0].body] == [
        "return False"
    ]


def test_the_condition_rule_reads_the_subject_and_the_pairing():
    """Structural pin: no reachable condition puts a path in its pairing.

    Every pairing item the history produces is a prefixed token
    (`transaction:`, `run:`, `reference:`, `adoption:`, ...) and the harness
    path cannot lie under the adopter, so no subprocess can make the pairing
    name the harness path. ADR 0029 still says "as its subject or in its
    pairing", so the source must compare both, and this is the only place that
    is checked. It proves the shape of the comparison, not that a path in a
    pairing would be caught."""
    source = (
        REPO_ROOT / "validated_memory" / "journal" / "protocol.py"
    ).read_text(encoding="utf-8")
    obstacle = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_harness_repair_obstacle"
    )
    compared = [
        [ast.unparse(item) for item in node.elts]
        for node in ast.walk(obstacle)
        if isinstance(node, ast.Tuple)
        and any(isinstance(item, ast.Starred) for item in node.elts)
    ]
    assert ["condition.subject", "*condition.pairing"] in compared, compared
