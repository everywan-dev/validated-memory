"""Independent acceptance of checked consultation through the public CLI.

Fixtures author ordinary documents; the CLI creates every consultation artifact.
Tests inspect command output and on-disk bytes, never package internals.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCOPE = ("--scope", "exercise=parcel")
ATTRIBUTION = ("--actor", "fixture-agent", "--reason", "Inspected the fixture evidence.")


def _tree(path):
    return {
        item.relative_to(path).as_posix(): ("directory" if item.is_dir() else item.read_bytes())
        for item in sorted(path.rglob("*"))
    }


class Workspace:
    def __init__(self, root, run_cli):
        self.root = root
        self.run_cli = run_cli
        self.store = root / "consultation.sqlite"
        self.projects = {}

    def command(self, *args, code=0):
        result = self.run_cli(
            "consultation", "--store", str(self.store), *args, cwd=self.root
        )
        assert result.returncode == code, (args, result.stdout, result.stderr)
        assert "Traceback" not in result.stderr
        return result

    def artifact(self, *args):
        result = self.command(*args)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        assert all(row["schema_version"] == 1 for row in rows)
        assert len(rows) == (2 if args[0] == "read" else 1)
        if args[0] == "read":
            assert rows[0]["status"] == "inspected" and "id" not in rows[0]
            assert rows[1]["status"] == "receipt recorded"
        return rows[-1]["id"]

    def project(self, alias):
        path = self.root / alias
        path.mkdir()
        result = self.run_cli("init", cwd=path)
        assert result.returncode == 0, result.stderr
        (path / "sources").mkdir()
        self.projects[alias] = path
        return path

    def unit(self, alias, name, text="The fixture claim.", supersedes=()):
        fields = f"id: {name}\nevidence: verifiable\n"
        if supersedes:
            fields += "supersedes:\n" + "".join(f"  - {old}\n" for old in supersedes)
        path = self.projects[alias] / "knowledge" / f"{name}.md"
        path.write_text(f"---\n{fields}---\n# {name}\n\n{text}\n", encoding="utf-8")
        return path

    def support(self, alias, name="evidence.txt", text="Fixture supporting evidence.\n"):
        path = self.projects[alias] / "sources" / name
        path.write_text(text, encoding="utf-8")
        return path

    def register(self, alias):
        return self.artifact("register", alias, str(self.projects[alias]), "--source", alias)

    def bind(self, alias, unit, *, support="evidence.txt", references=(), prior=None):
        options = ["review-support" if prior else "bind", f"{alias}:{unit}"]
        if prior:
            options.extend(("--prior", prior))
        options.extend(("--support", f"sources/{support}", "--authority", alias))
        for reference in references:
            options.extend(("--reference", reference))
        return self.artifact(*options, *SCOPE, *ATTRIBUTION)

    def acquire_use(self, qualified):
        receipt = self.artifact("read", qualified, *SCOPE)
        use = self.artifact("record-use", qualified, "--receipt", receipt)
        return receipt, use


@pytest.fixture
def workspace(tmp_path, run_cli):
    result = Workspace(tmp_path, run_cli)
    for alias in ("policy", "planning"):
        result.project(alias)
        result.unit(alias, "kb-same")
        result.support(alias)
        result.register(alias)
    return result


def test_equal_local_ids_stay_distinct_and_checks_preserve_all_bytes(workspace):
    w = workspace
    source_binding = w.bind("policy", "kb-same")
    w.bind("planning", "kb-same", references=("policy:kb-same",))
    before_adopters = {alias: _tree(root) for alias, root in w.projects.items()}
    receipt, use = w.acquire_use("planning:kb-same")
    source_receipt = w.artifact("read", "policy:kb-same", *SCOPE)
    w.command("record-use", "planning:kb-same", "--receipt", source_receipt, code=1)
    inspected = json.loads(w.command("show", receipt).stdout)["artifact"]["payload"]
    units = inspected["content"]["units"]
    assert {unit["identity"]["unit"] for unit in units} == {"kb-same"}
    assert len({unit["identity"]["project"] for unit in units}) == 2
    assert source_binding in {unit["binding"] for unit in units}
    before = _tree(w.root)
    assert json.loads(w.command("check-use", use).stdout)["status"] == "current"
    assert "historical" in json.loads(w.command("show", use).stdout)["status"]
    assert w.artifact("record-use", "planning:kb-same", "--receipt", receipt) == use
    assert _tree(w.root) == before
    assert {alias: _tree(root) for alias, root in w.projects.items()} == before_adopters


def test_support_rename_is_reviewable_and_retains_old_bytes(workspace):
    w = workspace
    old = w.bind("policy", "kb-same")
    w.bind("planning", "kb-same", references=("policy:kb-same",))
    receipt, use = w.acquire_use("planning:kb-same")
    source = w.projects["policy"] / "sources/evidence.txt"
    prior_bytes = source.read_text(encoding="utf-8")
    source.rename(source.with_name("replacement.txt"))
    frozen_store = w.store.read_bytes()
    w.command("read", "planning:kb-same", *SCOPE, code=1)
    w.command("check-use", use, code=1)
    assert w.store.read_bytes() == frozen_store
    reviewed = w.bind("policy", "kb-same", support="replacement.txt", prior=old)
    assert reviewed != old
    assert w.bind("policy", "kb-same", support="replacement.txt", prior=old) == reviewed
    historical = json.loads(w.command("show", old).stdout)["artifact"]["payload"]
    assert historical["support"][0]["path"] == "sources/evidence.txt"
    assert historical["support"][0]["text"] == prior_bytes
    w.command("record-use", "planning:kb-same", "--receipt", receipt, code=1)
    new_receipt, new_use = w.acquire_use("planning:kb-same")
    assert new_receipt != receipt and new_use != use
    w.command("check-use", new_use)
    w.command("check-use", use, code=1)


def test_new_successor_retires_support_inventory_without_erasing_history(workspace):
    w = workspace
    old = w.bind("policy", "kb-same")
    w.bind("planning", "kb-same", references=("policy:kb-same",))
    receipt, use = w.acquire_use("planning:kb-same")
    predecessor = (w.projects["policy"] / "knowledge/kb-same.md").read_bytes()
    w.unit("policy", "kb-next", supersedes=("kb-same",))
    (w.projects["policy"] / "sources/evidence.txt").unlink()
    w.support("policy", "new.txt", "Evidence for the successor.\n")
    w.bind("policy", "kb-next", support="new.txt")
    w.command("check-use", use, code=1)
    w.command("record-use", "planning:kb-same", "--receipt", receipt, code=1)
    w.acquire_use("policy:kb-next")
    assert (w.projects["policy"] / "knowledge/kb-same.md").read_bytes() == predecessor
    assert json.loads(w.command("show", old).stdout)["artifact"]["payload"]["support"][0]["text"]


def test_conflict_successor_keeps_other_candidate_usable_after_new_choice(workspace):
    w = workspace
    w.unit("policy", "kb-other", "An alternative claim.")
    w.bind("policy", "kb-same")
    w.bind("policy", "kb-other")
    conflict = w.artifact(
        "conflict", "--candidate", "policy:kb-same", "--candidate", "policy:kb-other",
        *SCOPE, *ATTRIBUTION,
    )
    w.command("read", "policy:kb-other", *SCOPE, code=1)
    old_choice = w.artifact("choose", conflict, "--candidate", "policy:kb-other", *SCOPE, *ATTRIBUTION)
    _, old_use = w.acquire_use("policy:kb-other")
    w.unit("policy", "kb-next", "A successor claim.", supersedes=("kb-same",))
    w.bind("policy", "kb-next")
    w.command("read", "policy:kb-other", *SCOPE, code=1)
    successor = w.artifact(
        "conflict", "--prior", conflict, "--candidate", "policy:kb-next",
        "--candidate", "policy:kb-other", "--replacement", "policy:kb-same=policy:kb-next",
        *SCOPE, *ATTRIBUTION,
    )
    w.command("read", "policy:kb-other", *SCOPE, code=1)
    w.artifact("choose", successor, "--candidate", "policy:kb-other", *SCOPE, *ATTRIBUTION)
    _, new_use = w.acquire_use("policy:kb-other")
    assert new_use != old_use
    for handle in (conflict, old_choice, old_use):
        assert "historical" in json.loads(w.command("show", handle).stdout)["status"]


def test_checkpoint_publication_preserves_receipt_but_relocation_invalidates_it(workspace):
    w = workspace
    w.bind("policy", "kb-same")
    w.bind("planning", "kb-same", references=("policy:kb-same",))
    receipt, use = w.acquire_use("planning:kb-same")
    checkpoint = w.artifact("checkpoint", "policy", *ATTRIBUTION)
    assert w.artifact("record-use", "planning:kb-same", "--receipt", receipt) == use
    moved = w.root / "moved-policy"
    w.projects["policy"].rename(moved)
    w.artifact("relocate", "policy", str(moved), "--checkpoint", checkpoint, *ATTRIBUTION)
    w.command("check-use", use, code=1)
    _, new_use = w.acquire_use("planning:kb-same")
    old_identity = json.loads(w.command("show", use).stdout)["artifact"]["payload"]["root"]
    new_identity = json.loads(w.command("show", new_use).stdout)["artifact"]["payload"]["root"]
    assert old_identity == new_identity


def test_acquisition_exact_wire_bound_counts_both_lines_and_utf8(workspace):
    w = workspace
    w.unit("policy", "kb-same", "Multibyte evidence: " + "é" * 1800)
    w.bind("policy", "kb-same")
    emitted = w.command("read", "policy:kb-same", *SCOPE).stdout
    byte_count = len(emitted.encode("utf-8"))
    assert byte_count >= 2048
    w.command("read", "policy:kb-same", *SCOPE, "--max-bytes", str(byte_count))
    before = _tree(w.root)
    refused = w.command("read", "policy:kb-same", *SCOPE, "--max-bytes", str(byte_count - 1), code=1)
    assert not refused.stdout
    assert _tree(w.root) == before


def test_failed_content_delivery_cannot_publish_receipt(workspace):
    w = workspace
    w.bind("policy", "kb-same")
    before = _tree(w.root)
    environment = dict(os.environ, PYTHONPATH=str(REPO))
    with open("/dev/full", "wb") as blocked_output:
        result = subprocess.run(
            [sys.executable, "-P", "-m", "validated_memory", "consultation",
             "--store", str(w.store), "read", "policy:kb-same", *SCOPE],
            cwd=w.root, env=environment, stdout=blocked_output, stderr=subprocess.PIPE,
            timeout=15, check=False,
        )
    assert result.returncode == 1, result.stderr.decode()
    assert b"Traceback" not in result.stderr
    assert _tree(w.root) == before
    w.acquire_use("policy:kb-same")


def test_diamond_can_return_to_consumer_project_but_real_cycle_refuses(workspace):
    w = workspace
    w.unit("planning", "kb-leaf")
    w.unit("planning", "kb-middle")
    leaf = w.bind("planning", "kb-leaf")
    w.bind("policy", "kb-same", references=("planning:kb-leaf",))
    w.bind("planning", "kb-middle", references=("planning:kb-leaf",))
    w.bind("planning", "kb-same", references=("policy:kb-same", "planning:kb-middle"))
    receipt, use = w.acquire_use("planning:kb-same")
    content = json.loads(w.command("show", receipt).stdout)["artifact"]["payload"]["content"]
    assert len(content["units"]) == 4
    assert len(content["support"]) == 2
    w.bind("planning", "kb-leaf", references=("planning:kb-same",), prior=leaf)
    before = w.store.read_bytes()
    refused = w.command("read", "planning:kb-same", *SCOPE, code=1)
    assert "cycl" in refused.stderr.lower()
    w.command("check-use", use, code=1)
    assert w.store.read_bytes() == before


def test_hash_valid_but_mismatched_use_invalidates_whole_store(workspace):
    w = workspace
    w.bind("policy", "kb-same")
    receipt, use = w.acquire_use("policy:kb-same")
    with sqlite3.connect(w.store) as connection:
        row = connection.execute(
            "SELECT kind, prior, payload FROM events WHERE id = ?", (use,)
        ).fetchone()
        kind, prior, encoded = row
        payload = json.loads(encoded)
        payload["scope"] = {"exercise": "different"}
        canonical_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        material = json.dumps(
            {"kind": kind, "prior": prior, "payload": payload},
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        replacement = hashlib.sha256(material).hexdigest()
        connection.execute(
            "UPDATE events SET id = ?, payload = ? WHERE id = ?",
            (replacement, canonical_payload, use),
        )
    before = _tree(w.root)
    w.command("show", receipt, code=1)
    w.command("show", replacement, code=1)
    w.command("read", "policy:kb-same", *SCOPE, code=1)
    assert _tree(w.root) == before


def test_drift_in_unchosen_conflict_support_requires_a_new_review(workspace):
    w = workspace
    w.unit("policy", "kb-other", "An alternative claim.")
    evidence = w.support("policy", "alternative.txt", "Original alternative support.\n")
    w.bind("policy", "kb-same")
    w.bind("policy", "kb-other", support="alternative.txt")
    conflict = w.artifact(
        "conflict", "--candidate", "policy:kb-same", "--candidate", "policy:kb-other",
        *SCOPE, *ATTRIBUTION,
    )
    w.artifact("choose", conflict, "--candidate", "policy:kb-same", *SCOPE, *ATTRIBUTION)
    w.acquire_use("policy:kb-same")
    evidence.write_text("Changed alternative support.\n", encoding="utf-8")
    before = _tree(w.root)
    w.command("choose", conflict, "--candidate", "policy:kb-same", *SCOPE, *ATTRIBUTION, code=1)
    refused = w.command("read", "policy:kb-same", *SCOPE, code=1)
    assert "alternative.txt" in refused.stderr
    assert _tree(w.root) == before


def test_database_hardlink_cannot_turn_store_writes_into_adopter_writes(workspace):
    w = workspace
    os.link(w.store, w.projects["policy"] / "database-alias.sqlite")
    before = _tree(w.root)
    refused = w.command(
        "bind", "policy:kb-same", "--support", "sources/evidence.txt",
        "--authority", "policy", *SCOPE, *ATTRIBUTION, code=1,
    )
    assert "link" in refused.stderr.lower()
    assert _tree(w.root) == before
