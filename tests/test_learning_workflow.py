"""Black-box lifecycle checks for requested learning capture and review.

These fixtures model the files an authoring skill leaves behind.  The tests
check CLI validation, indexing, recall, and preservation; they do not claim
that a language model authored the files or that authoring is transactional.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _memory(name, description, body, *, kind="reference"):
    return (
        "---\n"
        f"name: {name}\n"
        f'description: "{description}"\n'
        "metadata:\n"
        f"  type: {kind}\n"
        "---\n\n"
        f"{body}"
    )


def _unit(unit_id, heading, body, *, supersedes=()):
    lines = [f"id: {unit_id}\n", "evidence: hypothesis\n"]
    if supersedes:
        lines.append("supersedes:\n")
        lines.extend(f"  - {item}\n" for item in supersedes)
    lines.append("anchors: []\n")
    return "---\n" + "".join(lines) + f"---\n\n# {heading}\n\n{body}"


def _write_memory(root, name, description, body, *, kind="reference"):
    path = root / "memory" / f"{name}.md"
    path.write_text(_memory(name, description, body, kind=kind), encoding="utf-8")
    return path


def _write_unit(root, unit_id, heading, body, *, supersedes=(), provenance=()):
    path = root / "knowledge" / f"{unit_id}.md"
    text = _unit(unit_id, heading, body, supersedes=supersedes)
    if provenance:
        text = text.replace("anchors: []\n", "anchors: []\nprovenance:\n" + "".join(f"  - {item}\n" for item in provenance))
    path.write_text(text, encoding="utf-8")
    return path


def _index(root, entries):
    text = "# Agent memory\n\n" + "".join(
        f"- [{label}]({name}.md) — {label}\n" for name, label in entries
    )
    (root / "memory" / "MEMORY.md").write_text(text, encoding="utf-8")


def _profile(root):
    return root / "validated-memory-profile.md"


def _write_default_profile(root):
    profile = _profile(root)
    profile.write_text(
        "---\nschema_version: 1\ndiscovery: explicit\nreliance: lightweight\n---\n",
        encoding="utf-8",
    )
    return profile


def _run_hook(root, prompt):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "agent", "hook", "--host", "claude-code"],
        cwd=root,
        input=json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(root), "prompt": prompt}),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_requested_tentative_capture_is_indexed_and_recalled(adopter_dir, run_cli):
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    _write_memory(
        adopter_dir,
        "failed-retry",
        "failed retry attempt; tentative lesson",
        "Observation: retrying before the service was ready failed under condition A.\n"
        "Tentative lesson: wait for readiness before retrying; the cause is unverified.\n"
        "Useful next check: run the same attempt with readiness measured.\n",
    )
    _index(adopter_dir, [("failed-retry", "failed retry attempt")])

    lint = run_cli("lint", cwd=adopter_dir)
    assert lint.returncode == 0, lint.stderr
    recalled = run_cli("recall", "tentative lesson", "--layer", "memory", "--format", "json", cwd=adopter_dir)
    assert recalled.returncode == 0, recalled.stderr
    result = json.loads(recalled.stdout)
    assert result["results"][0]["identity"] == "failed-retry"
    assert "the cause is unverified" in (
        adopter_dir / "memory" / "failed-retry.md"
    ).read_text(encoding="utf-8")


def test_p1_adapter_returns_tentative_candidate_before_full_inspection(adopter_dir, run_cli):
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    _write_default_profile(adopter_dir)
    original = _write_memory(
        adopter_dir,
        "failed-retry",
        "failed retry attempt; tentative lesson",
        "Observation: retrying before readiness failed.\n"
        "Tentative lesson: wait for readiness; the cause is unverified.\n",
    )
    _index(adopter_dir, [("failed-retry", "failed retry attempt; tentative lesson")])
    before = original.read_bytes()
    hook = _run_hook(adopter_dir, "Validated-memory: tentative lesson")
    assert hook.returncode == 0, hook.stderr
    context = json.loads(json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"])
    candidate = context["candidates"][0]
    assert candidate["identity"] == "failed-retry"
    assert candidate["path"] == "memory/failed-retry.md"
    assert "tentative" in candidate["title"].lower()
    # The adapter supplies a candidate; the caller must inspect the original.
    located = adopter_dir / candidate["path"]
    assert located == original
    assert located.read_bytes() == before
    assert "cause is unverified" in located.read_text(encoding="utf-8")


@pytest.mark.parametrize("outcome", ["supported", "qualified", "unresolved", "contradicted"])
def test_non_superseding_review_is_indexed_without_profile_or_claim_mutation(
    adopter_dir, run_cli, outcome
):
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    outcome_details = {
        "supported": (
            "Outcome: supported. Scope: the observed failure under condition A. "
            "Inspected evidence supports that scoped observation. Limit: no conclusion "
            "is established for other conditions."
        ),
        "qualified": (
            "Outcome: qualified. Scope: the observed failure under condition A. "
            "Inspected evidence supports the observation, but not the proposed cause. "
            "Limit: the cause remains unresolved without a condition-A diagnostic trace."
        ),
        "unresolved": (
            "Outcome: unresolved. Scope: the proposed cause of the condition-A failure. "
            "Inspected evidence lacks the diagnostic trace needed to distinguish causes. "
            "Limit: no causal support is established until that check is available."
        ),
        "contradicted": (
            "Outcome: contradicted. Scope: the proposed interpretation of the condition-A "
            "failure. Inspected evidence rejects that interpretation. Limit: the attributed "
            "failure observation remains retained and its cause remains unresolved."
        ),
    }
    profile = _write_default_profile(adopter_dir)
    before_profile = profile.read_bytes()
    original = _write_memory(
        adopter_dir,
        "failed-retry",
        "failed retry attempt under condition A",
        "The attempt failed under condition A; the proposed cause remains unverified.\n",
    )
    original_bytes = original.read_bytes()
    _write_memory(
        adopter_dir,
        "review-failed-retry",
        "review of failed retry",
        "Reviewed layer: memory. Identity: failed-retry. Locator: memory/failed-retry.md.\n"
        "Inspected evidence: the complete original entry and its attributed recorded output.\n"
        f"{outcome_details[outcome]}\n"
        "This is descriptive feedback, not an evidence-state change or checked-use record.\n",
        kind="feedback",
    )
    _index(
        adopter_dir,
        [("failed-retry", "failed retry attempt"), ("review-failed-retry", "review")],
    )
    result = run_cli("lint", cwd=adopter_dir)
    assert result.returncode == 0, result.stderr
    assert profile.read_bytes() == before_profile
    assert original.read_bytes() == original_bytes
    assert "superseded by" not in original.read_text(encoding="utf-8")
    recalled = run_cli("recall", "review failed retry", "--layer", "memory", "--format", "json", cwd=adopter_dir)
    assert recalled.returncode == 0
    review = next(
        item for item in json.loads(recalled.stdout)["results"]
        if item["identity"] == "review-failed-retry"
    )
    assert review["memory_type"] == "feedback"
    assessment = adopter_dir.joinpath("memory/review-failed-retry.md").read_text(encoding="utf-8")
    assert "Reviewed layer: memory. Identity: failed-retry. Locator: memory/failed-retry.md." in assessment
    assert "Inspected evidence: the complete original entry and its attributed recorded output." in assessment
    assert outcome_details[outcome] in assessment
    assert f"Outcome: {outcome}." in assessment


def test_success_under_condition_b_coexists_with_condition_a(adopter_dir, run_cli):
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    profile = _write_default_profile(adopter_dir)
    profile_bytes = profile.read_bytes()
    original = _write_memory(
        adopter_dir,
        "failed-retry",
        "failed retry under condition A",
        "Observation: retry failed when readiness was absent.\n",
    )
    original_bytes = original.read_bytes()
    _write_memory(
        adopter_dir,
        "retry-success-condition-b",
        "retry succeeds under condition B",
        "Observation: retry succeeded after readiness was measured under condition B.\n",
    )
    _write_memory(
        adopter_dir,
        "review-failed-retry",
        "review of failed retry",
        "Reviewed layer: memory; identity: failed-retry; locator: memory/failed-retry.md.\n"
        "Inspected source: the original attempt and its recorded output.\n"
        "Outcome: qualified; the original observation remains scoped to condition A.\n"
        "The success under condition B does not supersede the original observation.\n",
        kind="feedback",
    )
    _index(
        adopter_dir,
        [
            ("failed-retry", "failed retry"),
            ("retry-success-condition-b", "retry success"),
            ("review-failed-retry", "review"),
        ],
    )
    assert run_cli("lint", cwd=adopter_dir).returncode == 0
    assert profile.read_bytes() == profile_bytes
    assert original.read_bytes() == original_bytes
    assert "superseded by" not in original.read_text(encoding="utf-8")
    recalled = run_cli("recall", "retry", "--layer", "memory", "--format", "json", cwd=adopter_dir)
    identities = {item["identity"] for item in json.loads(recalled.stdout)["results"]}
    assert {"failed-retry", "retry-success-condition-b"}.issubset(identities)
    review = run_cli("recall", "review-failed-retry", "--layer", "memory", "--format", "json", cwd=adopter_dir)
    review_record = json.loads(review.stdout)["results"][0]
    assert review_record["memory_type"] == "feedback"
    assert "superseded by" not in original.read_text(encoding="utf-8")


def test_changed_advice_uses_same_layer_successor_and_keeps_history(adopter_dir, run_cli):
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    old = _write_memory(
        adopter_dir,
        "retry-advice",
        "retry advice",
        "Original advice: retry immediately.\n",
    )
    old_bytes = old.read_bytes()
    _write_memory(
        adopter_dir,
        "retry-advice-scoped",
        "retry advice scoped after review",
        "Review outcome: contradicted for condition A.\n"
        "Inspected evidence: the complete original advice and the condition-A trace.\n"
        "Success remains possible under condition B; this successor narrows the advice.\n",
    )
    _index(
        adopter_dir,
        [("retry-advice", "retry advice"), ("retry-advice-scoped", "scoped advice")],
    )
    # The successor is authored first; retiring the old advice then changes
    # only its description and annotates its existing index line.
    old.write_text(
        _memory("retry-advice", "superseded by [[retry-advice-scoped]]", "Original advice: retry immediately.\n"),
        encoding="utf-8",
    )
    index = adopter_dir / "memory" / "MEMORY.md"
    index.write_text(
        index.read_text(encoding="utf-8").replace(
            "- [retry advice](retry-advice.md) — retry advice",
            "- [retry advice](retry-advice.md) — superseded by retry-advice-scoped",
        ),
        encoding="utf-8",
    )
    assert run_cli("lint", cwd=adopter_dir).returncode == 0
    expected_retired_bytes = old_bytes.replace(
        b'description: "retry advice"',
        b'description: "superseded by [[retry-advice-scoped]]"',
        1,
    )
    assert expected_retired_bytes != old_bytes
    assert old.read_bytes() == expected_retired_bytes
    assert "retry-advice.md" in index.read_text(encoding="utf-8")
    index_text = index.read_text(encoding="utf-8")
    assert "- [retry advice](retry-advice.md) — superseded by retry-advice-scoped" in index_text
    assert "- [scoped advice](retry-advice-scoped.md)" in index_text
    active = run_cli("recall", "retry advice", "--layer", "memory", "--format", "json", cwd=adopter_dir)
    payload = json.loads(active.stdout)
    assert [item["identity"] for item in payload["results"]] == ["retry-advice-scoped"]
    assert payload["results"][0]["redirected_from"] == [
        {"identity": "retry-advice", "path": "memory/retry-advice.md"}
    ]
    successor_body = adopter_dir.joinpath("memory/retry-advice-scoped.md").read_text(encoding="utf-8")
    assert "contradicted" in successor_body
    assert "condition A" in successor_body and "condition B" in successor_body
    assert "Inspected evidence: the complete original advice and the condition-A trace." in successor_body
    history = run_cli(
        "recall", "retry advice", "--layer", "memory", "--include-superseded", "--format", "json", cwd=adopter_dir
    )
    by_identity = {item["identity"]: item for item in json.loads(history.stdout)["results"]}
    assert by_identity["retry-advice"]["state"] == "superseded"
    assert by_identity["retry-advice"]["successors"][0]["identity"] == "retry-advice-scoped"


def test_cross_layer_provenance_is_a_path_not_a_supersession(adopter_dir, run_cli):
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    memory = _write_memory(
        adopter_dir,
        "failed-retry",
        "failed retry observation",
        "Observation retained from the failed attempt.\n",
    )
    _index(adopter_dir, [("failed-retry", "failed retry")])
    memory_bytes = memory.read_bytes()
    unit = _write_unit(
        adopter_dir,
        "kb-retry-scoped",
        "Retry guidance scoped by readiness",
        "This unit is a hypothesis after inspecting the failed-attempt observation; "
        "it remains pending the readiness check.\n",
        provenance=("memory/failed-retry.md",),
    )
    assert run_cli("lint", cwd=adopter_dir).returncode == 0
    assert run_cli("validate", cwd=adopter_dir).returncode == 0
    unit_text = unit.read_text(encoding="utf-8")
    assert "\nprovenance:\n  - memory/failed-retry.md\n---\n" in unit_text
    unit_body = unit_text.split("---\n", 2)[2]
    assert "memory/failed-retry.md" not in unit_body
    assert "[[" not in unit_text
    assert "supersedes:" not in unit_text
    assert "knowledge/" not in memory.read_text(encoding="utf-8")
    assert memory.read_bytes() == memory_bytes


def test_interrupted_file_requires_index_then_recovers_with_known_bytes(adopter_dir, run_cli):
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    note = _write_memory(
        adopter_dir,
        "interrupted-note",
        "interrupted requested lesson",
        "Known intended body retained before interruption.\n",
    )
    intended = note.read_bytes()
    missing_index = run_cli("lint", cwd=adopter_dir)
    assert missing_index.returncode == 1
    assert "interrupted-note" in missing_index.stdout + missing_index.stderr
    _index(adopter_dir, [("interrupted-note", "interrupted requested lesson")])
    resumed = run_cli("lint", cwd=adopter_dir)
    assert resumed.returncode == 0, resumed.stderr
    assert note.read_bytes() == intended

    note.write_bytes(intended[:30])
    truncated = run_cli("lint", cwd=adopter_dir)
    assert truncated.returncode == 1
    assert "interrupted-note" in truncated.stdout + truncated.stderr

    # A syntactically valid file with a shortened body can pass lint. That
    # result does not recover or establish the retained intended content.
    valid_prefix = _memory(
        "interrupted-note",
        "interrupted requested lesson",
        "",
    )
    note.write_text(valid_prefix + "Shortened body only.\n", encoding="utf-8")
    syntactically_valid = run_cli("lint", cwd=adopter_dir)
    assert syntactically_valid.returncode == 0, syntactically_valid.stderr
    assert note.read_bytes() != intended


def test_profile_change_does_not_rewrite_prior_facts_or_history(adopter_dir, run_cli):
    assert run_cli("init", cwd=adopter_dir).returncode == 0
    fact = _write_memory(adopter_dir, "failed-retry", "failed retry", "Original fact.\n")
    _index(adopter_dir, [("failed-retry", "failed retry")])
    fact_bytes = fact.read_bytes()
    index = adopter_dir / "memory" / "MEMORY.md"
    index_bytes = index.read_bytes()
    profile = _profile(adopter_dir)
    profile.write_text(
        "---\nschema_version: 1\ndiscovery: automatic\nreliance: reviewed\n---\n",
        encoding="utf-8",
    )
    assert run_cli("agent", "profile", cwd=adopter_dir).returncode == 0
    assert fact.read_bytes() == fact_bytes
    assert index.read_bytes() == index_bytes
    assert run_cli("lint", cwd=adopter_dir).returncode == 0
