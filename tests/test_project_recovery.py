"""Black-box recovery rehearsal for complete adopter and consultation state."""

import hashlib
import json
import shutil
import sqlite3
import stat

import pytest

from test_transfer_contract import ATTR, SCOPE, Workspace


PROFILE = """---
schema_version: 1
discovery: explicit
reliance: reviewed
---

Fixture recovery profile.
"""
MEMORY = """---
name: recovery-owner
description: The fixture recovery owner is the operator.
metadata:
  type: project
---

Retained across the recovery rehearsal.
"""


def inventory(path):
    """Return backup-integrity evidence without following links."""
    rows = {}
    for item in sorted((path, *path.rglob("*"))):
        relative = "." if item == path else item.relative_to(path).as_posix()
        mode = stat.S_IMODE(item.lstat().st_mode)
        if item.is_symlink():
            rows[relative] = ("link", mode, item.readlink().as_posix())
        elif item.is_dir():
            rows[relative] = ("directory", mode, None)
        else:
            data = item.read_bytes()
            rows[relative] = ("file", mode, hashlib.sha256(data).hexdigest())
    return rows


def prepare_recovery(tmp_path, run_cli, *, second_root=True):
    original = tmp_path / "original"
    original.mkdir()
    primary = Workspace(original, run_cli, "primary")
    secondary = None
    if second_root:
        secondary = original / "secondary"
        secondary.mkdir()
        assert run_cli("init", cwd=secondary).returncode == 0
        (secondary / "sources").mkdir()
        (secondary / "sources/evidence.txt").write_text("Secondary retained support.\n")
        (secondary / "knowledge/dependency.md").write_text(
            "---\nid: dependency\nevidence: verifiable\n---\n# dependency\n\nRetained dependency.\n"
        )
        secondary_registration = primary.artifact(
            "register", "secondary", str(secondary), "--source", "secondary"
        )
        assert primary.show(secondary_registration)["payload"]["project"] != primary.project
        primary.artifact(
            "bind", "secondary:dependency", "--support", "sources/evidence.txt",
            "--authority", "secondary", *SCOPE, *ATTR,
        )

    primary.install("conclusion")
    binding = ["bind", "primary:conclusion", "--support", "sources/evidence.txt",
               "--authority", "primary"]
    if second_root:
        binding.extend(("--reference", "secondary:dependency"))
    primary.artifact(*binding, *SCOPE, *ATTR)
    (primary.root / "memory/recovery-owner.md").write_text(MEMORY, encoding="utf-8")
    (primary.root / "memory/MEMORY.md").write_text(
        "# Agent memory\n\n- [Recovery owner](recovery-owner.md) — fixture operator\n",
        encoding="utf-8",
    )
    (primary.root / "validated-memory-profile.md").write_text(PROFILE, encoding="utf-8")
    (primary.root / "verdicts.jsonl").write_bytes(b"")
    assert run_cli("derive", cwd=primary.root).returncode == 0
    assert run_cli("lint", cwd=primary.root).returncode == 0
    assert run_cli("validate", cwd=primary.root).returncode == 0
    if secondary is not None:
        assert run_cli("lint", cwd=secondary).returncode == 0
        assert run_cli("validate", cwd=secondary).returncode == 0
    profile = run_cli("agent", "profile", cwd=primary.root)
    assert profile.returncode == 0 and json.loads(profile.stdout)["reliance"] == "reviewed"

    receipt, use = primary.use("conclusion")
    primary_checkpoint = primary.artifact("checkpoint", "primary", *ATTR)
    secondary_checkpoint = primary.artifact("checkpoint", "secondary", *ATTR) if second_root else None
    with sqlite3.connect(primary.store) as database:
        assert database.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"

    workspace = original / "task-workspace"
    workspace.mkdir()
    (workspace / "task-handoff.md").write_text(
        "Task: recovery-rehearsal\nStatus: active\nRoot: primary\n"
        f"Scope: exercise=dispatch\nReceipt: {receipt}\nUse: {use}\n",
        encoding="utf-8",
    )
    baseline = {
        "primary": inventory(primary.root),
        "workspace": inventory(workspace),
        "store": (
            stat.S_IMODE(primary.store.stat().st_mode),
            hashlib.sha256(primary.store.read_bytes()).hexdigest(),
        ),
    }
    if second_root:
        baseline["secondary"] = inventory(secondary)
    backup = tmp_path / "untouched-backup"
    shutil.copytree(original, backup, copy_function=shutil.copy2)
    store_backup = tmp_path / "consultation-backup.sqlite"
    shutil.copy2(primary.store, store_backup)
    assert inventory(backup / "primary") == baseline["primary"]
    assert inventory(backup / "task-workspace") == baseline["workspace"]
    copied_store = (
        stat.S_IMODE(store_backup.stat().st_mode),
        hashlib.sha256(store_backup.read_bytes()).hexdigest(),
    )
    assert copied_store == baseline["store"]
    if second_root:
        assert inventory(backup / "secondary") == baseline["secondary"]
    return {
        "primary": primary, "secondary": secondary,
        "receipt": receipt, "use": use, "checkpoints": (primary_checkpoint, secondary_checkpoint),
        "backup": backup, "store_backup": store_backup, "baseline": baseline,
    }


def restore_and_relocate(case, tmp_path, run_cli):
    hidden = tmp_path / "hidden-primary"
    case["primary"].root.rename(hidden)
    (case["primary"].base / "task-workspace").rename(tmp_path / "hidden-task-workspace")
    restored = tmp_path / "restored"
    restored.mkdir()
    # Keep the other registered root available while each exact-content relocation is recorded.
    old_secondary = case["secondary"]
    new_primary = restored / "primary"
    shutil.copytree(case["backup"] / "primary", new_primary, copy_function=shutil.copy2)
    new_workspace = restored / "task-workspace"
    shutil.copytree(case["backup"] / "task-workspace", new_workspace, copy_function=shutil.copy2)
    restored_store = restored / "consultation.sqlite"
    shutil.copy2(case["store_backup"], restored_store)
    assert hashlib.sha256(restored_store.read_bytes()).hexdigest() == case["baseline"]["store"][1]

    runner = Workspace.__new__(Workspace)
    runner.base, runner.run_cli, runner.name = restored, run_cli, "primary"
    runner.root, runner.store = new_primary, restored_store
    assert runner.call("check-use", case["use"], code=1).stdout == ""
    runner.artifact("relocate", "primary", str(new_primary), "--checkpoint", case["checkpoints"][0], *ATTR)
    new_secondary = restored / "secondary"
    old_secondary.rename(new_secondary)
    runner.artifact("relocate", "secondary", str(new_secondary), "--checkpoint", case["checkpoints"][1], *ATTR)
    return runner, new_secondary, new_workspace


def test_staged_multi_root_migration_preserves_complete_project_and_reacquires_use(tmp_path, run_cli):
    case = prepare_recovery(tmp_path, run_cli)
    backup_before = inventory(case["backup"]), case["store_backup"].read_bytes()
    restored, secondary, workspace = restore_and_relocate(case, tmp_path, run_cli)

    assert inventory(restored.root) == case["baseline"]["primary"]
    assert inventory(secondary) == case["baseline"]["secondary"]
    assert inventory(workspace) == case["baseline"]["workspace"]
    assert stat.S_IMODE(restored.store.stat().st_mode) == case["baseline"]["store"][0]
    assert restored.show(case["receipt"])["kind"] == "receipt"
    assert restored.show(case["use"])["kind"] == "use"
    assert restored.show(case["use"])["payload"]["root"]["project"] == case["primary"].project
    assert restored.call("check-use", case["use"], code=1).stdout == ""
    new_receipt, new_use = restored.use("conclusion")
    assert (new_receipt, new_use) != (case["receipt"], case["use"])
    assert json.loads(restored.call("check-use", new_use).stdout)["status"] == "current"
    assert json.loads(restored.call("resume-use", new_use, *SCOPE).stdout)["status"] == "current"
    assert run_cli("lint", cwd=restored.root).returncode == 0
    assert run_cli("validate", cwd=restored.root).returncode == 0
    assert json.loads(run_cli("agent", "profile", cwd=restored.root).stdout)["reliance"] == "reviewed"
    assert (inventory(case["backup"]), case["store_backup"].read_bytes()) == backup_before


def test_whole_workspace_recovery_restores_single_registered_project_from_backup(tmp_path, run_cli):
    case = prepare_recovery(tmp_path, run_cli, second_root=False)
    backup_before = inventory(case["backup"]), case["store_backup"].read_bytes()
    case["primary"].base.rename(tmp_path / "unavailable-original")
    restored = tmp_path / "restored"
    shutil.copytree(case["backup"], restored, copy_function=shutil.copy2)
    restored_store = tmp_path / "restored-consultation.sqlite"
    shutil.copy2(case["store_backup"], restored_store)
    restored_store_manifest = (
        stat.S_IMODE(restored_store.stat().st_mode),
        hashlib.sha256(restored_store.read_bytes()).hexdigest(),
    )
    assert restored_store_manifest == case["baseline"]["store"]
    runner = Workspace.__new__(Workspace)
    runner.base, runner.run_cli, runner.name = tmp_path, run_cli, "primary"
    runner.root, runner.store = restored / "primary", restored_store

    assert runner.call("check-use", case["use"], code=1).stdout == ""
    runner.artifact(
        "relocate", "primary", str(runner.root), "--checkpoint", case["checkpoints"][0], *ATTR
    )
    assert inventory(runner.root) == case["baseline"]["primary"]
    assert inventory(restored / "task-workspace") == case["baseline"]["workspace"]
    assert runner.show(case["receipt"])["kind"] == "receipt"
    assert runner.show(case["use"])["payload"]["root"]["project"] == case["primary"].project
    assert run_cli("lint", cwd=runner.root).returncode == 0
    assert run_cli("validate", cwd=runner.root).returncode == 0
    assert json.loads(run_cli("agent", "profile", cwd=runner.root).stdout)["reliance"] == "reviewed"
    receipt, use = runner.use("conclusion")
    assert (receipt, use) != (case["receipt"], case["use"])
    assert json.loads(runner.call("resume-use", use, *SCOPE).stdout)["status"] == "current"
    assert (inventory(case["backup"]), case["store_backup"].read_bytes()) == backup_before


@pytest.mark.parametrize("damage", ("missing-support", "wrong-content", "missing-second-root"))
def test_incomplete_or_changed_restore_refuses_relocation(tmp_path, run_cli, damage):
    case = prepare_recovery(tmp_path, run_cli, second_root=damage == "missing-second-root")
    backup_before = inventory(case["backup"]), case["store_backup"].read_bytes()
    case["primary"].base.rename(tmp_path / "hidden-original")
    restored = tmp_path / "restored"
    shutil.copytree(case["backup"], restored, copy_function=shutil.copy2)
    store = tmp_path / "restored.sqlite"
    shutil.copy2(case["store_backup"], store)
    if damage == "missing-support":
        (restored / "primary/sources/evidence.txt").unlink()
    elif damage == "wrong-content":
        unit = restored / "primary/knowledge/conclusion.md"
        unit.write_text(
            unit.read_text(encoding="utf-8").replace(
                "Admission closes at 16:00.", "Admission closes at 17:00."
            ),
            encoding="utf-8",
        )
    else:
        shutil.rmtree(restored / "secondary")
    runner = Workspace.__new__(Workspace)
    runner.base, runner.run_cli, runner.name = tmp_path, run_cli, "primary"
    runner.root, runner.store = restored / "primary", store
    before = store.read_bytes()
    result = runner.call(
        "relocate", "primary", str(runner.root), "--checkpoint", case["checkpoints"][0], *ATTR, code=1
    )
    assert result.stdout == ""
    if damage == "missing-support":
        assert "sources/evidence.txt" in result.stderr and "unavailable" in result.stderr
    elif damage == "wrong-content":
        assert "differs from checkpoint" in result.stderr
    else:
        assert "secondary" in result.stderr and "root" in result.stderr
    assert store.read_bytes() == before
    assert (inventory(case["backup"]), case["store_backup"].read_bytes()) == backup_before


def test_missing_external_store_refuses_without_creating_history(tmp_path, run_cli):
    case = prepare_recovery(tmp_path, run_cli, second_root=False)
    backup_before = inventory(case["backup"]), case["store_backup"].read_bytes()
    case["primary"].base.rename(tmp_path / "unavailable-original")
    restored = tmp_path / "restored"
    shutil.copytree(case["backup"], restored, copy_function=shutil.copy2)
    absent_store = tmp_path / "missing-consultation.sqlite"
    result = run_cli(
        "consultation", "--store", str(absent_store), "check-use", case["use"], cwd=tmp_path
    )
    assert result.returncode == 1 and result.stdout == ""
    assert "store unavailable; register a project" in result.stderr
    assert not absent_store.exists()
    assert (inventory(case["backup"]), case["store_backup"].read_bytes()) == backup_before


def test_handoff_reuse_is_mechanical_for_same_scope_and_refuses_different_scope(tmp_path, run_cli):
    case = prepare_recovery(tmp_path, run_cli)
    restored, _secondary, workspace = restore_and_relocate(case, tmp_path, run_cli)
    task_scope = (*SCOPE, "--scope", "task=task-a")
    receipt = restored.artifact("read", "primary:conclusion", *task_scope)
    use = restored.artifact("record-use", "primary:conclusion", "--receipt", receipt)
    copied = workspace / "other-prose-task.md"
    copied.write_text(
        f"Task: another-task\nScope: exercise=dispatch, task=task-a\nUse: {use}\n",
        encoding="utf-8",
    )
    same = restored.call("resume-use", use, *task_scope)
    assert json.loads(same.stdout)["status"] == "current"
    before = restored.store.read_bytes(), inventory(restored.root)
    different = restored.call(
        "resume-use", use, *SCOPE, "--scope", "task=task-b", code=1
    )
    report = json.loads(different.stdout)
    assert report["status"] == "blocked" and report["scope"]["matches"] is False
    assert (restored.store.read_bytes(), inventory(restored.root)) == before
    (restored.root / "sources/evidence.txt").write_text("Changed after acquisition.\n", encoding="utf-8")
    assert restored.call("check-use", use, code=1).stdout == ""
    assert json.loads(restored.call("resume-use", use, *task_scope, code=1).stdout)["status"] == "blocked"
