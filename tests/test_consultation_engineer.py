"""Independent CLI regressions for bounded, retained conflict successor proofs."""

import hashlib
import json
import sqlite3

import pytest


ATTRIBUTION = ("--scope", "exercise=proof", "--actor", "fixture-agent",
               "--reason", "Inspected synthetic successor evidence.")


class ProofWorkspace:
    def __init__(self, root, run_cli):
        self.root = root
        self.project = root / "project"
        self.project.mkdir()
        self.store = root / "consultation.sqlite"
        self.run_cli = run_cli
        result = run_cli("init", cwd=self.project)
        assert result.returncode == 0, result.stderr
        (self.project / "support.txt").write_text("Synthetic evidence.\n", encoding="utf-8")
        self.unit("z-old")
        self.unit("other")
        self.command("register", "project", str(self.project), "--source", "fixture")
        self.bind("z-old")
        self.bind("other")
        self.prior = self.command("conflict", "--candidate", "project:z-old",
                                  "--candidate", "project:other", *ATTRIBUTION)["id"]

    def unit(self, name, predecessors=()):
        fields = f"id: {name}\nevidence: verifiable\n"
        if predecessors:
            fields += "supersedes:\n" + "".join(f"  - {name}\n" for name in predecessors)
        path = self.project / "knowledge" / f"{name}.md"
        path.write_text(f"---\n{fields}---\n\nSynthetic claim {name}.\n", encoding="utf-8")
        return path

    def call(self, *args):
        return self.run_cli("consultation", "--store", str(self.store), *args, cwd=self.root)

    def command(self, *args):
        result = self.call(*args)
        assert result.returncode == 0, (args, result.stdout, result.stderr)
        return json.loads(result.stdout)

    def bind(self, name):
        return self.command("bind", f"project:{name}", "--support", "support.txt",
                            "--authority", "fixture", *ATTRIBUTION)

    def replacement_args(self, name):
        return ("conflict", "--prior", self.prior, "--candidate", f"project:{name}",
                "--candidate", "project:other", "--replacement", f"project:z-old=project:{name}",
                *ATTRIBUTION)

    def show(self, handle):
        return self.command("show", handle)["artifact"]


@pytest.fixture
def proof_workspace(tmp_path, run_cli):
    return ProofWorkspace(tmp_path, run_cli)


@pytest.mark.parametrize("proof_length", [128, 129])
def test_lineage_bound_applies_to_goal_proof_not_earlier_frontier(proof_workspace, proof_length):
    workspace = proof_workspace
    workspace.unit("a-terminal")
    chain_length = proof_length - 1
    for index in range(chain_length):
        predecessors = ([f"n{index + 1:03}"] if index < chain_length - 1
                        else ["a-terminal", "z-old"])
        workspace.unit(f"n{index:03}", predecessors)
    workspace.bind("n000")
    before = workspace.store.read_bytes()
    result = workspace.call(*workspace.replacement_args("n000"))
    if proof_length == 129:
        assert result.returncode == 1, result.stdout
        assert "128-document" in result.stderr
        assert workspace.store.read_bytes() == before
        return
    assert result.returncode == 0, result.stderr
    handle = json.loads(result.stdout)["id"]
    proof = workspace.show(handle)["payload"]["lineage"][0]["proof"]
    assert len(proof) == 128
    assert proof[-1]["path"] == "knowledge/z-old.md"
    assert all(item["path"] != "knowledge/a-terminal.md" for item in proof)


@pytest.mark.parametrize("position,path", [
    (0, "support.txt"),
    (0, "knowledge/invented.md"),
    (1, "support.txt"),
    (2, "support.txt"),
])
def test_rehashed_lineage_path_corruption_refuses_whole_history(proof_workspace, position, path):
    workspace = proof_workspace
    workspace.unit("middle", ["z-old"])
    workspace.unit("new", ["middle"])
    workspace.bind("new")
    handle = workspace.command(*workspace.replacement_args("new"))["id"]
    with sqlite3.connect(workspace.store) as database:
        kind, prior, encoded = database.execute(
            "SELECT kind,prior,payload FROM events WHERE id=?", (handle,)
        ).fetchone()
        payload = json.loads(encoded)
        payload["lineage"][0]["proof"][position]["path"] = path

        def canonical(value):
            return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

        replacement = hashlib.sha256(canonical(dict(kind=kind, prior=prior, payload=payload)).encode()).hexdigest()
        database.execute("UPDATE events SET id=?,payload=? WHERE id=?",
                         (replacement, canonical(payload), handle))
    before = workspace.store.read_bytes()
    for target in (replacement, workspace.prior):
        result = workspace.call("show", target)
        assert result.returncode == 1, result.stdout
        assert not result.stdout
        assert "lineage" in result.stderr
        assert "Traceback" not in result.stderr
    assert workspace.store.read_bytes() == before


def test_retired_endpoint_can_move_inside_knowledge_with_unchanged_bytes(proof_workspace):
    workspace = proof_workspace
    old = workspace.project / "knowledge" / "z-old.md"
    retained = old.read_bytes()
    workspace.unit("new", ["z-old"])
    moved = old.with_name("retained-predecessor.md")
    old.rename(moved)
    workspace.bind("new")
    handle = workspace.command(*workspace.replacement_args("new"))["id"]
    proof = workspace.show(handle)["payload"]["lineage"][0]["proof"]
    assert proof[-1]["path"] == "knowledge/retained-predecessor.md"
    assert proof[-1]["text"].encode("utf-8") == retained
    workspace.command("choose", handle, "--candidate", "project:new", *ATTRIBUTION)
    result = workspace.call("read", "project:new", "--scope", "exercise=proof")
    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(rows) == 2 and rows[1]["status"] == "receipt recorded"
