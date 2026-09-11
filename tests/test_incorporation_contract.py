"""Independent end-to-end incorporation and correction acceptance scenarios."""

import hashlib
import json
import sqlite3

import pytest


SCOPE = ("--scope", "exercise=dispatch")
ACTOR = ("--actor", "fixture-reviewer", "--reason", "Reviewed the exact synthetic evidence.")


def document(name, claim="The synthetic queue closes at 16:00.", predecessors=()):
    fields = f"id: {name}\nevidence: verifiable\n"
    if predecessors:
        fields += "supersedes:\n" + "".join(f"  - {item}\n" for item in predecessors)
    return f"---\n{fields}---\n# {name}\n\n{claim}\n"


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


class Lifecycle:
    def __init__(self, root, run_cli):
        self.root = root
        self.run_cli = run_cli
        self.store = root / "consultation.sqlite"
        self.projects = {}
        for alias in ("policy", "planning"):
            project = root / alias
            project.mkdir()
            result = run_cli("init", cwd=project)
            assert result.returncode == 0, result.stderr
            (project / "sources").mkdir()
            (project / "sources/evidence.txt").write_text(
                "Synthetic evidence: 16:00 denotes queue admission, not departure.\n",
                encoding="utf-8",
            )
            self.projects[alias] = project
        self.unit("policy", "rule")
        self.unit("planning", "brief")
        for alias, project in self.projects.items():
            self.artifact("register", alias, str(project), "--source", alias)
        self.source = self.bind("policy", "rule")
        self.consumer = self.bind("planning", "brief", references=("policy:rule",))

    def call(self, *args, code=0):
        result = self.run_cli("consultation", "--store", str(self.store), *args, cwd=self.root)
        assert result.returncode == code, (args, result.stdout, result.stderr)
        assert "Traceback" not in result.stderr
        return result

    def artifact(self, *args):
        result = self.call(*args)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(rows) == (2 if args[0] in ("read", "inspect") else 1)
        assert all(row["schema_version"] == 1 for row in rows)
        return rows[-1]["id"]

    def show(self, handle):
        return json.loads(self.call("show", handle).stdout)["artifact"]

    def unit(self, alias, name, predecessors=()):
        path = self.projects[alias] / "knowledge" / f"{name}.md"
        path.write_text(document(name, predecessors=predecessors), encoding="utf-8")
        return path

    def bind(self, alias, name, references=(), support="sources/evidence.txt", prior=None):
        args = ["review-support" if prior else "bind", f"{alias}:{name}",
                "--support", support, "--authority", alias]
        if prior:
            args.extend(("--prior", prior))
        for reference in references:
            args.extend(("--reference", reference))
        return self.artifact(*args, *SCOPE, *ACTOR)

    def use(self, qualified="planning:brief"):
        receipt = self.artifact("read", qualified, *SCOPE)
        use = self.artifact("record-use", qualified, "--receipt", receipt)
        return receipt, use

    def challenge(self):
        statement = self.root / "question.txt"
        statement.write_text("Does 16:00 mean queue admission or carrier departure?\n", encoding="utf-8")
        return self.artifact("challenge", "policy:rule", "--statement", str(statement),
                             "--kind", "factual", *SCOPE, *ACTOR)

    def accept(self, handle, prior=None):
        inspection = self.artifact("inspect", handle)
        args = ["decide", handle, "--inspection", inspection, "--outcome", "accept"]
        if prior:
            args.extend(("--prior", prior))
        return self.artifact(*args, *ACTOR)

    def resolve(self, challenge, decision):
        inspected = self.artifact("inspect", self.source)
        return self.artifact("resolve", challenge, "--decision", decision,
                             "--review", self.source, "--inspection", inspected, *ACTOR)

    def report(self, challenge):
        return json.loads(self.call("reconcile", challenge).stdout)

    def proposal(self):
        candidate = self.root / "candidate.md"
        candidate.write_text(document("rule-next", predecessors=("rule",)), encoding="utf-8")
        proposal = self.artifact("submit", "policy", str(candidate),
                                 "--path", "knowledge/rule-next.md",
                                 "--support", "sources/evidence.txt", "--authority", "policy",
                                 *SCOPE, *ACTOR)
        return candidate, proposal, self.accept(proposal)


@pytest.fixture
def lifecycle(tmp_path, run_cli):
    return Lifecycle(tmp_path, run_cli)


def test_unchanged_review_does_not_resurrect_old_receipt_or_touch_adopters(lifecycle):
    w = lifecycle
    receipt, use = w.use()
    before = {name: tree(root) for name, root in w.projects.items()}
    early_inspection = w.artifact("inspect", w.source)
    challenge = w.challenge()
    w.call("check-use", use)
    decision = w.accept(challenge)
    w.call("check-use", use, code=1)
    w.call("resolve", challenge, "--decision", decision, "--review", w.source,
           "--inspection", early_inspection, *ACTOR, code=1)
    resolution = w.resolve(challenge, decision)
    assert w.show(resolution)["payload"]["remedy"]["binding"] == w.source
    w.call("record-use", "planning:brief", "--receipt", receipt, code=1)
    w.call("check-use", use, code=1)
    new_receipt, new_use = w.use()
    assert new_receipt != receipt and new_use != use
    assert w.show(new_receipt)["payload"]["review_frontier"] == [decision]
    address = w.artifact("address", challenge, "--decision", decision,
                         "--old-use", use, "--new-use", new_use, *ACTOR)
    row = next(row for row in w.report(challenge)["uses"] if row["id"] == use)
    assert row["addresses"][0]["id"] == address
    assert row["addresses"][0]["observation"]["status"] == "current"
    assert {name: tree(root) for name, root in w.projects.items()} == before
    assert w.show(receipt)["id"] == receipt


def test_preexisting_dependency_removed_use_can_be_explicitly_addressed(lifecycle):
    w = lifecycle
    _, old_use = w.use()
    w.unit("planning", "independent", predecessors=("brief",))
    w.bind("planning", "independent")
    replacement_receipt, replacement_use = w.use("planning:independent")
    challenge = w.challenge()
    decision = w.accept(challenge)
    w.resolve(challenge, decision)
    assert w.use("planning:independent") == (replacement_receipt, replacement_use)
    before = next(row for row in w.report(challenge)["uses"] if row["id"] == old_use)
    assert before["addresses"] == []
    address = w.artifact("address", challenge, "--decision", decision,
                         "--old-use", old_use, "--new-use", replacement_use,
                         "--mode", "dependency-removed", *ACTOR)
    assert w.show(replacement_use)["sequence"] < w.show(decision)["sequence"]
    after = next(row for row in w.report(challenge)["uses"] if row["id"] == old_use)
    assert [item["id"] for item in after["addresses"]] == [address]
    assert after["addresses"][0]["observation"]["status"] == "current"


def test_challenge_bookkeeping_and_historical_effects_survive_missing_root(lifecycle):
    w = lifecycle
    _, use = w.use()
    project = w.projects["policy"]
    project.rename(project.with_name("unavailable-policy"))
    challenge = w.challenge()
    decision = w.accept(challenge)
    assert w.show(decision)["payload"]["outcome"] == "accept"
    before_store = w.store.read_bytes()
    report = w.report(challenge)
    row = next(row for row in report["uses"] if row["id"] == use)
    assert row["observation"]["status"] == "unavailable"
    assert row["addresses"] == [] and report["review"] == "open"
    assert w.store.read_bytes() == before_store
    w.call("check-use", use, code=1)


@pytest.mark.parametrize("changed_support", [False, True])
def test_installed_candidate_renews_without_deleting_or_inventing_successor(lifecycle, changed_support):
    w = lifecycle
    candidate, proposal, decision = w.proposal()
    original = w.show(proposal)["payload"]
    predecessor = w.projects["policy"] / "knowledge/rule.md"
    predecessor_bytes = predecessor.read_bytes()
    destination = predecessor.with_name("rule-next.md")
    destination.write_bytes(candidate.read_bytes())
    binding = w.bind("policy", "rule-next")
    w.unit("planning", "unrelated")
    if changed_support:
        support = w.projects["policy"] / "sources/evidence.txt"
        replacement = support.with_name("reviewed.txt")
        replacement.write_text("Reviewed evidence for the unchanged queue policy.\n", encoding="utf-8")
        w.bind("policy", "rule-next", support="sources/reviewed.txt", prior=binding)
    w.call("incorporate", proposal, "--decision", decision, *ACTOR, code=1)
    args = ["renew", proposal]
    if changed_support:
        w.call("renew", proposal, *ACTOR, code=1)
        args.extend(("--support", "sources/reviewed.txt", *SCOPE))
    renewed = w.artifact(*args, *ACTOR)
    accepted = w.accept(renewed)
    incorporated = w.artifact("incorporate", renewed, "--decision", accepted, *ACTOR)
    assert w.show(incorporated)["payload"]["proposal"] == renewed
    assert w.show(renewed)["prior"] == proposal
    assert w.show(proposal)["payload"] == original
    assert predecessor.read_bytes() == predecessor_bytes
    assert destination.read_bytes() == candidate.read_bytes()
    destination.write_text(document("rule-next", "An unaccepted different claim.", ("rule",)), encoding="utf-8")
    w.call("renew", renewed, *ACTOR, code=1)


def test_reflection_requires_explicit_address_and_remains_observational(lifecycle):
    w = lifecycle
    _, old_use = w.use()
    output = w.projects["planning"] / "exports"
    output.mkdir()
    report_path = output / "brief.txt"
    report_path.write_text("The deadline is 16:00.\n", encoding="utf-8")
    challenge = w.challenge()
    tracked = w.artifact("track-publication", challenge, "--project", "planning",
                         "--path", "exports/brief.txt", "--use", old_use, *ACTOR)
    decision = w.accept(challenge)
    w.resolve(challenge, decision)
    _, new_use = w.use()
    address = w.artifact("address", challenge, "--decision", decision,
                         "--old-use", old_use, "--new-use", new_use, *ACTOR)
    w.call("reflect", tracked, "--address", address, *ACTOR, code=1)
    report_path.write_text("Queue admission closes at 16:00; departure is separate.\n", encoding="utf-8")
    reflected = w.artifact("reflect", tracked, "--address", address, *ACTOR)
    assert w.show(tracked)["payload"]["file"]["text"] == "The deadline is 16:00.\n"
    assert w.show(reflected)["payload"]["file"]["text"] == report_path.read_text(encoding="utf-8")
    rows = w.report(challenge)["uses"]
    publication = next(row for row in rows if row["id"] == old_use)["publications"][0]
    assert publication["reflections"][0]["observation"]["status"] == "current"
    report_path.write_text("A subsequent unreviewed report.\n", encoding="utf-8")
    publication = next(row for row in w.report(challenge)["uses"] if row["id"] == old_use)["publications"][0]
    assert publication["reflections"][0]["observation"]["status"] != "current"


def test_narrow_challenge_scope_does_not_invalidate_unmatched_use(lifecycle):
    w = lifecycle
    receipt, use = w.use()
    statement = w.root / "narrow-question.txt"
    statement.write_text("Review only the manual variant.\n", encoding="utf-8")
    challenge = w.artifact("challenge", "policy:rule", "--statement", str(statement),
                           "--kind", "factual", *SCOPE, "--scope", "variant=manual", *ACTOR)
    w.accept(challenge)
    w.call("check-use", use)
    assert w.use() == (receipt, use)
    w.call("read", "planning:brief", *SCOPE, "--scope", "variant=manual", code=1)
    assert w.report(challenge)["uses"] == []


def test_reversed_and_reaccepted_challenge_retains_every_acceptance_epoch(lifecycle):
    w = lifecycle
    original_receipt, original_use = w.use()
    challenge = w.challenge()
    first = w.accept(challenge)
    w.resolve(challenge, first)
    _, reviewed_use = w.use()
    inspection = w.artifact("inspect", challenge)
    rejected = w.artifact("decide", challenge, "--inspection", inspection,
                          "--outcome", "reject", "--prior", first, *ACTOR)
    w.call("check-use", reviewed_use)
    w.call("check-use", original_use, code=1)
    second = w.accept(challenge, prior=rejected)
    w.call("check-use", reviewed_use, code=1)
    w.resolve(challenge, second)
    newest_receipt, newest_use = w.use()
    assert w.show(newest_receipt)["payload"]["review_frontier"] == sorted((first, second))
    assert newest_receipt != original_receipt
    w.call("check-use", newest_use)
    w.call("check-use", reviewed_use, code=1)
    assert len(w.report(challenge)["resolutions"]) == 2


def test_rehashed_use_cannot_launder_a_receipt_across_an_open_review(lifecycle):
    w = lifecycle
    receipt = w.artifact("read", "planning:brief", *SCOPE)
    retained = w.show(receipt)["payload"]
    challenge = w.challenge()
    w.accept(challenge)
    payload = {"version": 1, "root": retained["root"], "receipt": receipt,
               "snapshot_sha256": retained["snapshot_sha256"],
               "content_sha256": retained["content_sha256"], "scope": retained["scope"]}

    def canonical(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    handle = hashlib.sha256(canonical({"kind": "use", "prior": None,
                                      "payload": payload}).encode()).hexdigest()
    with sqlite3.connect(w.store) as database:
        sequence = database.execute("SELECT max(sequence)+1 FROM events").fetchone()[0]
        database.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                         (sequence, handle, "use", None, canonical(payload),
                          "2026-09-11T00:00:00.000000Z"))
    before = w.store.read_bytes()
    for target in (handle, receipt):
        result = w.call("show", target, code=1)
        assert not result.stdout
    assert w.store.read_bytes() == before
