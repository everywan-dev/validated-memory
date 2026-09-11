"""Independent subprocess acceptance for portable origin and correction contracts."""

import hashlib
import json

import pytest


ATTR = ("--actor", "fixture-reviewer", "--reason", "Reviewed this synthetic correspondence.")
SCOPE = ("--scope", "exercise=dispatch")
EVIDENCE = "Synthetic source: admission closes at 16:00; departure is scheduled separately.\n"


def doc(identity, supersedes=()):
    fields = f"id: {identity}\nevidence: verifiable\n"
    if supersedes:
        fields += "supersedes:\n" + "".join(f"  - {item}\n" for item in supersedes)
    return f"---\n{fields}---\n# {identity}\n\nAdmission closes at 16:00.\n"


def tree(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


class Workspace:
    def __init__(self, tmp_path, run_cli, name):
        self.base = tmp_path
        self.run_cli = run_cli
        self.name = name
        self.root = tmp_path / name
        self.root.mkdir()
        result = run_cli("init", cwd=self.root)
        assert result.returncode == 0, result.stderr
        (self.root / "sources").mkdir()
        (self.root / "sources/evidence.txt").write_text(EVIDENCE, encoding="utf-8")
        self.store = tmp_path / f"{name}.sqlite"
        event = self.artifact("register", name, str(self.root), "--source", name)
        self.project = self.show(event)["payload"]["project"]

    def call(self, *args, code=0):
        result = self.run_cli("consultation", "--store", str(self.store), *args, cwd=self.base)
        assert result.returncode == code, (args, result.stdout, result.stderr)
        assert "Traceback" not in result.stderr
        return result

    def artifact(self, *args):
        return json.loads(self.call(*args).stdout.splitlines()[-1])["id"]

    def show(self, handle):
        return json.loads(self.call("show", handle).stdout)["artifact"]

    def install(self, name, supersedes=()):
        path = self.root / "knowledge" / f"{name}.md"
        path.write_text(doc(name, supersedes), encoding="utf-8")
        return path

    def bind(self, name, references=()):
        args = ["bind", f"{self.name}:{name}", "--support", "sources/evidence.txt",
                "--authority", self.name, *SCOPE, *ATTR]
        for reference in references:
            args.extend(("--reference", f"{self.name}:{reference}"))
        return self.artifact(*args)

    def read(self, name):
        return self.artifact("read", f"{self.name}:{name}", *SCOPE)

    def use(self, name):
        receipt = self.read(name)
        return receipt, self.artifact("record-use", f"{self.name}:{name}", "--receipt", receipt)

    def export(self, receipt, filename):
        before = self.store.read_bytes()
        result = self.call("export-transfer", receipt, "--include-workspace-history")
        assert self.store.read_bytes() == before
        path = self.base / filename
        path.write_text(result.stdout, encoding="utf-8")
        return path

    def import_(self, path):
        before = tree(self.root)
        handle = self.artifact("import-transfer", str(path), *ATTR)
        assert tree(self.root) == before
        return handle

    def proposal(self, name, references=(), supersedes=()):
        candidate = self.base / f"{self.name}-{name}-candidate.md"
        candidate.write_text(doc(name, supersedes), encoding="utf-8")
        args = ["submit", self.name, str(candidate), "--path", f"knowledge/{name}.md",
                "--support", "sources/evidence.txt", "--authority", self.name, *SCOPE, *ATTR]
        for reference in references:
            args.extend(("--reference", f"{self.name}:{reference}"))
        return self.artifact(*args), candidate

    def link(self, proposal, imported, source, name, dependencies=(), predecessors=(), origin_only=()):
        args = ["link-transfer", proposal, "--import", imported,
                "--origin", f"{source.project}:{name}", *ATTR]
        for old, new in dependencies:
            args.extend(("--dependency", f"{source.project}:{old}={self.name}:{new}"))
        for old, new in predecessors:
            args.extend(("--predecessor", f"{source.project}:{old}={self.name}:{new}"))
        for old in origin_only:
            args.extend(("--origin-only-predecessor", f"{source.project}:{old}"))
        before = tree(self.root)
        result = self.call(*args)
        assert tree(self.root) == before
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(rows) == 2 and rows[0]["status"] == "inspected"
        return rows[-1]["id"]

    def accept(self, proposal, inspection=None, prior=None):
        inspection = inspection or self.artifact("inspect", proposal)
        args = ["decide", proposal, "--inspection", inspection, "--outcome", "accept", *ATTR]
        if prior:
            args.extend(("--prior", prior))
        return self.artifact(*args)

    def incorporate(self, proposal, candidate, name, references=()):
        decision = self.accept(proposal)
        (self.root / "knowledge" / f"{name}.md").write_bytes(candidate.read_bytes())
        self.bind(name, references)
        return self.artifact("incorporate", proposal, "--decision", decision, *ATTR)

    def challenge(self, name):
        statement = self.base / f"{self.name}-challenge.txt"
        statement.write_text("The declared admission time requires reconsideration.\n", encoding="utf-8")
        handle = self.artifact("challenge", f"{self.name}:{name}", "--statement", str(statement),
                               "--kind", "factual", *SCOPE, *ATTR)
        return handle, self.accept(handle)


def simple_transfer(tmp_path, run_cli):
    source = Workspace(tmp_path, run_cli, "source")
    source.install("rule")
    source.bind("rule")
    receipt = source.read("rule")
    destination = Workspace(tmp_path, run_cli, "destination")
    imported = destination.import_(source.export(receipt, "first.json"))
    proposal, candidate = destination.proposal("local-rule")
    link = destination.link(proposal, imported, source, "rule")
    destination.incorporate(proposal, candidate, "local-rule")
    return source, destination, receipt, imported, link


def test_offline_successor_without_predecessor_receipt_and_dependency_mapping(tmp_path, run_cli):
    a = Workspace(tmp_path, run_cli, "a")
    a.install("basis")
    a.bind("basis")
    a.install("old-rule")
    a.bind("old-rule")
    a.install("new-rule", ("old-rule",))
    a.bind("new-rule", ("basis",))
    receipt = a.read("new-rule")
    capsule = a.export(receipt, "successor.json")
    # No live A filesystem exists during receiving-project authoring and review.
    a.root.rename(tmp_path / "unavailable-a")
    b = Workspace(tmp_path, run_cli, "b")
    imported = b.import_(capsule)
    before_repeat = b.store.read_bytes()
    assert b.import_(capsule) == imported
    assert b.store.read_bytes() == before_repeat
    proposal, candidate = b.proposal("local-basis")
    b.link(proposal, imported, a, "basis")
    b.incorporate(proposal, candidate, "local-basis")
    proposal, candidate = b.proposal("local-rule", ("local-basis",))
    before = b.store.read_bytes()
    b.call("link-transfer", proposal, "--import", imported,
           "--origin", f"{a.project}:new-rule", "--origin-only-predecessor",
           f"{a.project}:old-rule", *ATTR, code=1)
    assert b.store.read_bytes() == before
    link = b.link(proposal, imported, a, "new-rule", dependencies=(("basis", "local-basis"),),
                  origin_only=("old-rule",))
    inspected = b.show(link)["payload"]["inspection_text"]
    assert "# old-rule" in inspected and "origin-only" in inspected
    b.incorporate(proposal, candidate, "local-rule", ("local-basis",))
    local_receipt, use = b.use("local-rule")
    b.call("check-use", use)
    retained = b.show(local_receipt)["payload"]
    assert retained["version"] == 3
    assert retained["transfer"]["origins"]
    assert all(row["live_origin"] == "not-checked" for row in retained["transfer"]["origins"])
    assert "old-rule" not in {p.stem for p in (b.root / "knowledge").glob("*.md")}


def test_first_acceptance_needs_new_local_inspection_after_source_link(tmp_path, run_cli):
    a = Workspace(tmp_path, run_cli, "a")
    a.install("rule")
    a.bind("rule")
    receipt = a.read("rule")
    b = Workspace(tmp_path, run_cli, "b")
    imported = b.import_(a.export(receipt, "material.json"))
    proposal, _candidate = b.proposal("local")
    stale = b.artifact("inspect", proposal)
    b.link(proposal, imported, a, "rule")
    before = b.store.read_bytes()
    b.call("decide", proposal, "--inspection", stale, "--outcome", "accept", *ATTR, code=1)
    assert b.store.read_bytes() == before
    assert b.show(b.accept(proposal))["payload"]["outcome"] == "accept"


def test_ancestor_only_update_blocks_intermediary_use_without_new_middle_export(tmp_path, run_cli):
    a, b, receipt, _imported, _link = simple_transfer(tmp_path, run_cli)
    b_receipt = b.read("local-rule")
    c = Workspace(tmp_path, run_cli, "third")
    imported_b = c.import_(b.export(b_receipt, "middle.json"))
    proposal, candidate = c.proposal("third-rule")
    c.link(proposal, imported_b, b, "local-rule")
    c.incorporate(proposal, candidate, "third-rule")
    _, use = c.use("third-rule")
    c.call("check-use", use)
    a.challenge("rule")
    # Export remains an honest historical operation even though this old receipt
    # can no longer authorize current source use.
    c.import_(a.export(receipt, "ancestor-correction.json"))
    c.call("check-use", use, code=1)
    before = c.store.read_bytes()
    assert c.import_(tmp_path / "middle.json") == imported_b
    assert c.store.read_bytes() == before
    c.call("check-use", use, code=1)
    assert c.show(use)["kind"] == "use"


def test_unbound_successor_chain_cannot_drop_origin_and_explicit_detachment_can(tmp_path, run_cli):
    a, b, receipt, _imported, link = simple_transfer(tmp_path, run_cli)
    # The intermediate canonical unit deliberately has no binding event.
    b.install("intermediate", ("local-rule",))
    proposal, candidate = b.proposal("independent", supersedes=("intermediate",))
    before = b.store.read_bytes()
    inspection = b.artifact("inspect", proposal)
    after_inspection = b.store.read_bytes()
    b.call("decide", proposal, "--inspection", inspection, "--outcome", "accept", *ATTR, code=1)
    assert b.store.read_bytes() == after_inspection
    assert before != after_inspection
    detached = b.artifact("detach-transfer", proposal, "--from", link, *ATTR)
    assert b.show(detached)["payload"]["mode"] == "independent"
    b.incorporate(proposal, candidate, "independent")
    local_receipt, use = b.use("independent")
    lineage = b.show(local_receipt)["payload"]["transfer"]["lineage"]
    assert {row["identity"]["unit"] for row in lineage} == {"intermediate", "local-rule"}
    a.challenge("rule")
    b.import_(a.export(receipt, "detached-origin-update.json"))
    b.call("check-use", use)
    origin_rows = b.show(local_receipt)["payload"]["transfer"]["origins"]
    assert any(row["mode"] == "independent" for row in origin_rows)


def test_whole_history_disclosure_preflight_and_same_tip_metadata_fork_refusal(tmp_path, run_cli):
    a = Workspace(tmp_path, run_cli, "a")
    a.install("rule")
    a.bind("rule")
    receipt = a.read("rule")
    # A distinct unrelated statement must still be included by honest full-history export.
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("UNRELATED-HISTORY-FIXTURE\n", encoding="utf-8")
    a.artifact("challenge", "a:rule", "--statement", str(unrelated), "--kind", "policy", *SCOPE, *ATTR)
    a.call("export-transfer", receipt, code=2)
    assessed = json.loads(a.call("export-transfer", receipt, "--include-workspace-history", "--assess").stdout)
    path = a.export(receipt, "intact.json")
    assert assessed["bytes"] == len(path.read_bytes())
    assert "UNRELATED-HISTORY-FIXTURE" in path.read_text(encoding="utf-8")
    b = Workspace(tmp_path, run_cli, "b")
    b.import_(path)
    capsule = json.loads(path.read_text(encoding="utf-8"))
    capsule["events"][0]["created_at"] = "2001-01-01T00:00:00.000000Z"
    capsule.pop("sha256")
    canonical = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    capsule["sha256"] = hashlib.sha256(canonical(capsule).encode("utf-8")).hexdigest()
    altered = tmp_path / "same-tip-metadata-fork.json"
    altered.write_text(canonical(capsule) + "\n", encoding="utf-8")
    before = b.store.read_bytes(), tree(b.root)
    b.call("import-transfer", str(altered), *ATTR, code=1)
    assert (b.store.read_bytes(), tree(b.root)) == before


def test_two_origins_share_bytes_without_self_invalidating_sequential_links(tmp_path, run_cli):
    sources = []
    for name in ("alpha", "beta"):
        source = Workspace(tmp_path, run_cli, name)
        source.install("rule")
        binding = source.bind("rule")
        receipt = source.read("rule")
        sources.append((source, binding, receipt, source.export(receipt, f"{name}.json")))
    destination = Workspace(tmp_path, run_cli, "combined")
    imports = [destination.import_(item[3]) for item in sources]
    proposal, candidate = destination.proposal("conclusion")
    links = [destination.link(proposal, imported, source, "rule")
             for imported, (source, _binding, _receipt, _path) in zip(imports, sources)]
    destination.incorporate(proposal, candidate, "conclusion")
    receipt, use = destination.use("conclusion")
    payload = destination.show(receipt)["payload"]
    rows = payload["transfer"]["origins"]
    assert len({row["origin"]["workspace"] for row in rows}) == 2
    assert len({sha for row in rows for sha in row["evidence_sha256"]}) == 1
    destination.call("check-use", use)
    alpha, binding, original_receipt, _path = sources[0]
    alpha.artifact("inspect", binding)  # Conservative metadata-only origin update.
    newer_import = destination.import_(alpha.export(original_receipt, "alpha-newer.json"))
    destination.call("check-use", use, code=1)
    renewed = destination.artifact("renew", proposal, *ATTR)
    updated_link = destination.artifact("link-transfer", renewed, "--import", newer_import,
                                       "--origin", f"{alpha.project}:rule", "--prior", links[0], *ATTR)
    assert updated_link != links[0]
    # The other independent origin requires no new link just because alpha changed.
    decision = destination.accept(renewed)
    destination.artifact("incorporate", renewed, "--decision", decision, *ATTR)
    fresh_receipt, fresh_use = destination.use("conclusion")
    assert fresh_receipt != receipt
    destination.call("check-use", fresh_use)
    destination.call("check-use", use, code=1)
    before = destination.store.read_bytes()
    assert destination.artifact("link-transfer", renewed, "--import", newer_import,
                                "--origin", f"{alpha.project}:rule", "--prior", links[0], *ATTR) == updated_link
    assert destination.store.read_bytes() == before


def test_corrected_source_replaces_active_local_counterpart_without_reactivating_retired_one(tmp_path, run_cli):
    a, b, _receipt, imported, _link = simple_transfer(tmp_path, run_cli)
    proposal, candidate = b.proposal("local-next", supersedes=("local-rule",))
    b.link(proposal, imported, a, "rule")
    b.incorporate(proposal, candidate, "local-next")
    b.use("local-next")
    a.install("rule-next", ("rule",))
    a.bind("rule-next")
    updated = b.import_(a.export(a.read("rule-next"), "corrected-successor.json"))
    # Only local-next is active. Requiring local-rule as a direct predecessor
    # would violate E1, which correctly refuses re-superseding retired units.
    proposal, candidate = b.proposal("corrected-local", supersedes=("local-next",))
    link = b.link(proposal, updated, a, "rule-next", predecessors=(("rule", "local-next"),))
    material = b.show(link)["payload"]["inspection_text"]
    assert "# rule" in material and "local-rule" in material
    b.incorporate(proposal, candidate, "corrected-local")
    receipt, use = b.use("corrected-local")
    b.call("check-use", use)
    origins = b.show(receipt)["payload"]["transfer"]["origins"]
    assert any(row["origin"]["identity"]["unit"] == "rule-next" for row in origins)
    assert all(row["origin"]["identity"]["unit"] != "rule" for row in origins if row["mode"] == "retain")
    assert (b.root / "knowledge/local-rule.md").exists()
    assert (b.root / "knowledge/local-next.md").exists()


@pytest.mark.parametrize("mutation", ("receipt3-in-schema2", "missing-newline", "duplicate-key", "symlink"))
def test_capsule_refusal_is_atomic_before_foreign_history_admission(tmp_path, run_cli, mutation):
    a = Workspace(tmp_path, run_cli, "a")
    a.install("rule")
    a.bind("rule")
    receipt = a.read("rule")
    path = a.export(receipt, "valid.json")
    b = Workspace(tmp_path, run_cli, "b")
    invalid = tmp_path / "invalid.json"
    if mutation == "receipt3-in-schema2":
        capsule = json.loads(path.read_text(encoding="utf-8"))
        capsule["storage_version"] = 2
        capsule.pop("sha256")
        canonical = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        capsule["sha256"] = hashlib.sha256(canonical(capsule).encode("utf-8")).hexdigest()
        invalid.write_text(canonical(capsule) + "\n", encoding="utf-8")
    elif mutation == "missing-newline":
        invalid.write_bytes(path.read_bytes()[:-1])
    elif mutation == "duplicate-key":
        invalid.write_text('{"version":1,' + path.read_text(encoding="utf-8")[1:], encoding="utf-8")
    else:
        invalid.symlink_to(path)
    before = b.store.read_bytes(), tree(b.root)
    result = b.call("import-transfer", str(invalid), *ATTR, code=1)
    assert not result.stdout
    assert (b.store.read_bytes(), tree(b.root)) == before


def test_small_export_limit_assesses_without_printing_a_partial_capsule(tmp_path, run_cli):
    a = Workspace(tmp_path, run_cli, "a")
    a.install("rule")
    a.bind("rule")
    receipt = a.read("rule")
    before = a.store.read_bytes(), tree(a.root)
    summary = json.loads(a.call("export-transfer", receipt, "--include-workspace-history",
                                "--max-bytes", "2048", "--assess").stdout)
    assert summary["supported"] is False and summary["bytes"] > 2048
    result = a.call("export-transfer", receipt, "--include-workspace-history",
                    "--max-bytes", "2048", code=1)
    assert result.stdout == ""
    assert (a.store.read_bytes(), tree(a.root)) == before


def test_transitive_independence_inspection_includes_newly_known_ancestor_correction(tmp_path, run_cli):
    a, b, receipt, _imported, b_link = simple_transfer(tmp_path, run_cli)
    c = Workspace(tmp_path, run_cli, "third")
    imported_b = c.import_(b.export(b.read("local-rule"), "middle-review.json"))
    proposal, candidate = c.proposal("third-rule")
    c_link = c.link(proposal, imported_b, b, "local-rule")
    c.incorporate(proposal, candidate, "third-rule")
    challenge, decision = a.challenge("rule")
    c.import_(a.export(receipt, "ancestor-review.json"))
    successor, _candidate = c.proposal("independent", supersedes=("third-rule",))
    detached = c.artifact("detach-transfer", successor, "--from", c_link, *ATTR)
    review = json.loads(c.show(detached)["payload"]["inspection_text"])["review"]
    corrections = review["material"]["corrections"]
    capsule = json.loads((tmp_path / "ancestor-review.json").read_text(encoding="utf-8"))
    origin_workspace = capsule["workspace"]
    actual = {(row["workspace"], row["event"]["id"]) for row in corrections}
    assert (origin_workspace, challenge) in actual
    assert (origin_workspace, decision) in actual
    assert any(row["event"]["id"] == b_link for row in corrections)
    # Review includes exact statement material, not merely a latest-origin digest.
    assert "The declared admission time requires reconsideration." in json.dumps(corrections)
