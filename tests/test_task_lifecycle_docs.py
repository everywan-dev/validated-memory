"""Structural pins for public task-lifecycle and recovery guidance."""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ROOT / "docs/reference/task-lifecycle.md"
RECOVERY = ROOT / "docs/reference/recovery.md"
WORKFLOW = ROOT / "docs/reference/everyday-workflow.md"
INTEGRATION = ROOT / "docs/reference/agent-integration.md"
LEARNING = ROOT / "docs/reference/learning.md"
CONSULT_SKILL = ROOT / "skills/consult-project-memory/SKILL.md"
ASK_SKILL = ROOT / "skills/ask-validated-memory/SKILL.md"


def prose(path):
    return " ".join(path.read_text(encoding="utf-8").split()).lower()


def prose_links(text):
    text = re.sub(r"^(```|~~~).*?^\1\s*$", "", text, flags=re.M | re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    return re.findall(r"\]\(([^)]+)\)", text)


@pytest.mark.parametrize(
    "path", [LIFECYCLE, RECOVERY, WORKFLOW, INTEGRATION, LEARNING, CONSULT_SKILL, ASK_SKILL]
)
def test_p3_references_are_shipped_and_links_resolve(path):
    text = path.read_text(encoding="utf-8")
    assert "sessions/" not in text
    for target in prose_links(text):
        if "://" in target:
            continue
        destination, _, fragment = target.partition("#")
        linked = path.parent / destination if destination else path
        assert linked.is_file(), target
        if fragment:
            headings = re.findall(r"^#{1,6} (.+)$", linked.read_text(encoding="utf-8"), re.M)
            anchors = {
                re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-")
                for heading in headings
            }
            assert fragment in anchors, target


def test_minimal_handoff_and_mechanical_boundary_are_explicit():
    text = LIFECYCLE.read_text(encoding="utf-8")
    template, = re.findall(r"```text\n(.*?)\n```", text, re.S)
    for field in (
        "Task identity:", "Adopter root:", "Outcome and authorized work:",
        "Applicability:", "Status:", "Sources selected:", "Sources inspected:",
        "Pending:", "Next action:",
    ):
        assert field in template
    normalized = prose(LIFECYCLE)
    for required in (
        "cli does not own a task handle", "same scope remains mechanically valid",
        "stable task identity", "explicit receipt scope pair", "new complete `read`",
        "`record-use`", "not automatic host enforcement", "no automatic lifecycle",
    ):
        assert required in normalized


def test_scenario_matrix_covers_all_task_dispositions_and_delegation_limits():
    text = prose(LIFECYCLE)
    for required in (
        "one active handoff", "no handoff or more than one", "completed or cancelled",
        "conversational context was lost", "a correction is pending",
        "work goes to a subagent", "cancelled after some authorized writes",
        "observations differ under different conditions", "freshly inspect",
        "bounded authorized subset", "parent recheck", "unaccepted learning proposals",
        "do not infer contradiction or supersession from recency",
    ):
        assert required in text


def test_recovery_inventory_quiescence_and_restore_limits_are_explicit():
    text = prose(RECOVERY)
    for required in (
        "every registered adopter root", "complete `knowledge/`", "complete `memory/`",
        "external consultation workspace", "trusted task handoff", "harness target",
        "trusted pre-backup manifest", "not proof of authenticity or semantic truth",
        "sqlite `delete` journal mode", "stop all writers", "hot rollback journal",
        "never discard a sidecar", "live main database alone", "sole backup",
        "backup untouched", "preserve the original bytes", "latest checkpoint",
        "old root unavailable", "single-project workspace",
        "complete `read` and `record-use`", "do not run `init` as silent repair",
    ):
        assert required in text


def test_multi_root_recovery_is_staged_and_collective_identity_loss_refuses():
    text = prose(RECOVERY)
    for required in (
        "multiple registered roots", "relocation is staged", "one root at a time",
        "every other registered root remains available at its original inode identity",
        "cannot bootstrap from restored copies", "two or more required root identities",
        "current cli refuses recovery", "report the workspace as unsupported",
        "do not edit the database", "re-register projects", "byte-identical copies",
    ):
        assert required in text


def test_capsule_omissions_and_checkpoint_scope_are_not_overclaimed():
    text = prose(RECOVERY)
    for required in (
        "capsule omits memory entries and their index", "agent profile",
        "adopter configuration", "verdicts", "journal and vault state",
        "reconstructable current consultation workspace", "historical contribution transport",
        "not a project backup", "content-only cli `checkpoint`",
        "not filesystem backup integrity", "does not establish evidence truth",
        "external freshness", "automatic host recovery",
    ):
        assert required in text


def test_final_backup_manifest_follows_preparation_and_precedes_copy():
    text = prose(RECOVERY)
    steps = (
        "first inventory", "explicit `recover` operation before checkpointing",
        "obtain its latest content-only cli", "stop all writers",
        "create the final trusted pre-backup manifest", "copy the complete roots",
        "compare the backup against the final manifest",
    )
    positions = [text.index(step) for step in steps]
    assert positions == sorted(positions)
    assert "no checkpoint or other write may intervene" in text
