"""End-to-end tests: a vault node that is not a regular file never blocks a run.

The vault's transaction entries, its preimage slots and its lock are read by
runs that a session hook bounds. Opening a symlink to a named pipe blocks for
good, and a lock that is a dangling symlink kept the waiting loop from ever
reaching its deadline. Every case drives the CLI as a subprocess over a
fixture adopter tree, with a subprocess timeout, so that a hang is a failure.
A test here proves that a run returns and what it says; it does not prove that
nothing opened the node. Nothing imports the package.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VAULT = ".validated-memory"

# Long enough for a healthy run on a loaded machine, short enough that a hang
# fails the test rather than the suite.
RUN_TIMEOUT_SECONDS = 30

# `LOCK_WAIT_SECONDS` as an outside observer sees it: how long a run that finds
# the lock in the way waits before it refuses.
LOCK_DEADLINE_SECONDS = 10

needs_fifo = pytest.mark.skipif(
    not hasattr(os, "mkfifo"), reason="this platform has no named pipes"
)


def _cli(cwd, *args, env=None, timeout=RUN_TIMEOUT_SECONDS):
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT), **(env or {})}
    try:
        return subprocess.run(
            [sys.executable, "-P", "-m", "validated_memory", *args],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"`validated-memory {' '.join(args)}` did not return within "
            f"{timeout} s"
        )


def _node_state(path):
    """What stands at `path`: its file type and, for a link, its target."""
    info = os.lstat(path)
    target = os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
    return stat.S_IFMT(info.st_mode), target


def _make_node(tmp_path, path, kind):
    """Put a node of `kind` at `path`, a symlink pointing outside the tree."""
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "symlink-to-fifo":
        pipe = tmp_path / "pipe"
        os.mkfifo(pipe)
        path.symlink_to(pipe)
    elif kind == "dangling-symlink":
        path.symlink_to(tmp_path / "does-not-exist")
    elif kind == "directory":
        path.mkdir()
    else:
        raise AssertionError(kind)


def _initialised_tree(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    assert _cli(tree, "init").returncode == 0
    return tree


# --- transaction entries ------------------------------------------------------

ENTRY_KINDS = (
    pytest.param("symlink-to-fifo", marks=needs_fifo),
    pytest.param("fifo", marks=needs_fifo),
    "directory",
    "symlink-to-transaction",
)


def _plant_transaction_entry(tmp_path, kind):
    """Return `(tree, entry name)`: `transactions/<name>` is a `kind` node.

    `symlink-to-transaction` links to a transaction a crashed run really left,
    so that parsing it would give a different answer from refusing it.
    """
    if kind == "symlink-to-transaction":
        tree = tmp_path / "tree"
        tree.mkdir()
        crashed = _cli(tree, "init", env={"VALIDATED_MEMORY_FAULT": "after-transaction"})
        assert crashed.returncode == 70, (crashed.stdout, crashed.stderr)
        directory = tree / VAULT / "transactions"
        (original,) = directory.glob("*.json")
        moved = tmp_path / "elsewhere.json"
        shutil.move(original, moved)
        original.symlink_to(moved)
        return tree, original.name
    tree = _initialised_tree(tmp_path)
    directory = tree / VAULT / "transactions"
    directory.mkdir(exist_ok=True)
    _make_node(tmp_path, directory / "x.json", kind)
    return tree, "x.json"


@pytest.mark.parametrize("kind", ENTRY_KINDS)
def test_init_and_check_report_a_transaction_entry_that_is_not_a_regular_file(
    tmp_path, kind
):
    """A transaction entry that is not a regular file is a damaged transaction.

    `init` and `journal --check` return, exit 1, name the entry and say why,
    leave it where it is, and do not parse a link's target: the target of the
    `symlink-to-transaction` case is a transaction they would otherwise have
    recovered or reported as open."""
    tree, name = _plant_transaction_entry(tmp_path, kind)
    entry = tree / VAULT / "transactions" / name
    before = _node_state(entry)
    identifier = name.removesuffix(".json")

    for arguments in (("init",), ("journal", "--check")):
        result = _cli(tree, *arguments)

        assert result.returncode == 1, (arguments, result.stdout, result.stderr)
        assert "Traceback" not in result.stderr, result.stderr
        assert (
            f"ERROR: {VAULT}/transactions/{name}: journal: damaged transaction "
            f"{identifier}: it is not a regular file; it was not opened"
        ) in result.stderr, result.stderr
        assert "open transaction" not in result.stderr, result.stderr
        assert "recovered" not in result.stdout, result.stdout
        assert _node_state(entry) == before
    if kind == "symlink-to-transaction":
        assert json.loads((tmp_path / "elsewhere.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind", ENTRY_KINDS)
def test_resolve_and_repair_report_a_transaction_entry_that_is_not_a_regular_file(
    tmp_path, kind
):
    """Resolution and repair refuse such an entry as damaged, and keep it."""
    tree, name = _plant_transaction_entry(tmp_path, kind)
    entry = tree / VAULT / "transactions" / name
    before = _node_state(entry)
    identifier = name.removesuffix(".json")

    for arguments in (
        ("journal", "--resolve", identifier, "--accept"),
        ("journal", "--resolve", identifier, "--restore"),
        ("journal", "--resolve", identifier, "--abandon"),
        ("journal", "--repair", identifier),
    ):
        result = _cli(tree, *arguments)

        assert result.returncode == 1, (arguments, result.stdout, result.stderr)
        assert "Traceback" not in result.stderr, result.stderr
        assert "not a regular file" in result.stderr, (arguments, result.stderr)
        assert _node_state(entry) == before, arguments


def test_a_regular_transaction_file_that_is_not_json_reports_as_it_always_did(
    tmp_path,
):
    """A regular damaged file keeps its output, line for line."""
    tree = _initialised_tree(tmp_path)
    directory = tree / VAULT / "transactions"
    directory.mkdir(exist_ok=True)
    (directory / "x.json").write_text("{not json\n", encoding="utf-8")
    try:
        json.loads("{not json\n")
    except json.JSONDecodeError as error:
        reason = error.msg

    initialised = _cli(tree, "init")
    checked = _cli(tree, "journal", "--check")

    assert initialised.returncode == 1, initialised.stderr
    assert (
        f"ERROR: {VAULT}/transactions/x.json: journal: damaged transaction x: "
        f"not valid JSON: {reason}; its artifact is left for inspection\n"
    ) in initialised.stderr, initialised.stderr
    assert checked.returncode == 1, checked.stderr
    assert (
        f"ERROR: {VAULT}/transactions/x.json: journal: damaged transaction x: "
        f"not valid JSON: {reason}\n"
    ) in checked.stderr, checked.stderr


@needs_fifo
def test_the_session_start_hook_returns_over_a_transaction_entry_that_is_a_pipe_link(
    tmp_path,
):
    """The hook finishes well under its 15 s timeout and the link stays.

    The harness link exists before the entry is planted; what the run shows is
    that reading the vault did not block and that the entry was named."""
    project = tmp_path / "project"
    (project / "memory").mkdir(parents=True)
    (project / "validated-memory.md").write_text(
        "---\nid_prefix: kb-\n---\n\nAdopter configuration.\n", encoding="utf-8"
    )
    (project / "memory" / "MEMORY.md").write_text("# Agent memory\n", encoding="utf-8")
    config = tmp_path / "config"
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path / "home"),
        "CLAUDE_CONFIG_DIR": str(config),
        "CLAUDE_PROJECT_DIR": str(project),
    }
    hook = REPO_ROOT / "hooks" / "restore-memory-symlink.sh"

    def run_hook():
        return subprocess.run(
            ["bash", str(hook)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_SECONDS,
            check=False,
        )

    first = run_hook()
    assert first.returncode == 0, first.stderr
    slug = "".join(c if c.isalnum() else "-" for c in str(project))
    link = config / "projects" / slug / "memory"
    assert link.is_symlink(), first.stderr
    (project / VAULT / "transactions").mkdir(parents=True, exist_ok=True)
    _make_node(tmp_path, project / VAULT / "transactions" / "x.json", "symlink-to-fifo")

    started = time.monotonic()
    try:
        second = run_hook()
    except subprocess.TimeoutExpired:
        pytest.fail("the hook did not return")
    elapsed = time.monotonic() - started

    assert second.returncode == 0, second.stderr
    assert elapsed < 12, elapsed
    assert link.is_symlink(), second.stderr
    assert link.resolve() == (project / "memory").resolve()
    assert "not a regular file" in second.stderr, second.stderr


# --- preimage slots -----------------------------------------------------------

SLOT_KINDS = (
    pytest.param("symlink-to-fifo", marks=needs_fifo),
    pytest.param("fifo", marks=needs_fifo),
    "dangling-symlink",
    "directory",
)


@pytest.mark.parametrize("kind", SLOT_KINDS)
def test_a_preimage_slot_that_is_not_a_regular_file_refuses_the_mutation(
    tmp_path, kind
):
    """`init` refuses before any effect and names the slot and the remedy.

    `.gitignore` needs an update, so the run parks its bytes under their
    digest, and that name is taken by a node that is not a regular file. The
    run returns, exits 1 and says to remove the slot by hand; the slot is kept,
    `.gitignore` is unchanged and no lock is left behind."""
    tree = tmp_path / "tree"
    tree.mkdir()
    original = b"build/\n"
    (tree / ".gitignore").write_bytes(original)
    slot = tree / VAULT / "preimages" / hashlib.sha256(original).hexdigest()
    slot.parent.mkdir(parents=True)
    _make_node(tmp_path, slot, kind)
    before = _node_state(slot)

    result = _cli(tree, "init")

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert f"{VAULT}/preimages/{slot.name}" in result.stderr, result.stderr
    assert "is not a regular file" in result.stderr, result.stderr
    assert "remove it by hand" in result.stderr, result.stderr
    assert (tree / ".gitignore").read_bytes() == original
    assert _node_state(slot) == before
    assert not os.path.lexists(tree / VAULT / "lock")


def _diverged_with_preimage(tree):
    """Leave a published `.gitignore` transaction, diverged; return its ID.

    The preimage of `build/\\n` is parked in the vault under its digest."""
    (tree / ".gitignore").write_text("build/\n", encoding="utf-8")
    killed = _cli(tree, "init", env={"VALIDATED_MEMORY_FAULT": "after-published"})
    assert killed.returncode == 70, (killed.stdout, killed.stderr)
    (tree / ".gitignore").write_text("an adopter wrote this\n", encoding="utf-8")
    (path,) = (tree / VAULT / "transactions").glob("*.json")
    return path.stem


@pytest.mark.parametrize(
    "kind", (pytest.param("symlink-to-fifo", marks=needs_fifo), "directory")
)
def test_restore_treats_a_preimage_that_is_not_a_regular_file_as_unavailable(
    tmp_path, kind
):
    """`--restore` refuses, says the preimage is unavailable, and restores nothing."""
    tree = tmp_path / "tree"
    tree.mkdir()
    transaction = _diverged_with_preimage(tree)
    (blob,) = (tree / VAULT / "preimages").iterdir()
    blob.unlink()
    _make_node(tmp_path, blob, kind)
    before = _node_state(blob)

    result = _cli(tree, "journal", "--resolve", transaction, "--restore")

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert "is unavailable: it is not a regular file" in result.stderr, result.stderr
    assert "Nothing has been restored." in result.stderr, result.stderr
    assert (tree / ".gitignore").read_text(encoding="utf-8") == (
        "an adopter wrote this\n"
    )
    assert _node_state(blob) == before
    assert (tree / VAULT / "transactions" / f"{transaction}.json").exists()


# --- the lock -----------------------------------------------------------------


LOCK_KINDS = (pytest.param("fifo", marks=needs_fifo), "dangling-symlink")


@pytest.mark.parametrize("kind", LOCK_KINDS)
def test_a_lock_that_is_not_a_regular_file_is_refused_by_name_within_the_deadline(
    tmp_path, kind
):
    """`init` gives up at the lock's deadline, names the lock and leaves it.

    A lock that is not a regular file is never opened and never broken: it is
    in the way, so the run waits its deadline and refuses, saying to remove it
    by hand. Following the dangling link would create its target, and it does
    not."""
    tree = tmp_path / "tree"
    (tree / VAULT).mkdir(parents=True)
    lock = tree / VAULT / "lock"
    _make_node(tmp_path, lock, kind)
    before = _node_state(lock)

    started = time.monotonic()
    result = _cli(
        tree, "init", timeout=LOCK_DEADLINE_SECONDS + 10
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert elapsed >= LOCK_DEADLINE_SECONDS - 1, elapsed
    assert f"{VAULT}/lock" in result.stderr, result.stderr
    assert "is not a regular file" in result.stderr, result.stderr
    assert "remove it by hand" in result.stderr, result.stderr
    assert _node_state(lock) == before
    assert not os.path.lexists(tmp_path / "does-not-exist")


@pytest.mark.parametrize("kind", LOCK_KINDS)
def test_a_lock_that_is_not_a_regular_file_withholds_the_harness_link_by_name(
    tmp_path, kind
):
    """The withheld link says the lock path is in the way, and what to do.

    No process holds the lock, so the WARNING must not say one does, and the
    journal has nothing to say about a node at the lock path, so it must not
    send the reader to `journal --check`. It names the path and says to remove
    it by hand; the link stays where it was."""
    tree = _initialised_tree(tmp_path)
    harness = tmp_path / "harness" / "memory"
    assert _cli(tree, "init", "--harness-memory", str(harness)).returncode == 0
    stale = tmp_path / "harness" / "stale"
    stale.mkdir()
    harness.unlink()
    harness.symlink_to(stale, target_is_directory=True)
    lock = tree / VAULT / "lock"
    _make_node(tmp_path, lock, kind)
    lock_before = _node_state(lock)
    link_before = _node_state(harness)

    result = _cli(
        tree, "init", "--harness-memory", str(harness), "--lock-wait", "1"
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    warnings = [
        line for line in result.stderr.splitlines() if line.startswith("WARNING:")
    ]
    assert len(warnings) == 1, result.stderr
    assert str(harness) in warnings[0]
    assert (
        f"the harness link was not restored: the lock path "
        f"{lock.parent.resolve() / lock.name} is not a regular file; remove "
        "it by hand"
    ) in warnings[0], warnings[0]
    assert "run journal --check" not in warnings[0], warnings[0]
    assert "another validated-memory process" not in warnings[0], warnings[0]
    assert _node_state(harness) == link_before
    assert _node_state(lock) == lock_before
