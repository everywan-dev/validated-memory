"""Public resumption guidance contracts; no package-internal imports."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "docs/reference/everyday-workflow.md"
REFERENCE = ROOT / "docs/reference/consultation.md"
CLI = ROOT / "docs/reference/cli.md"
SKILL = ROOT / "skills/consult-project-memory/SKILL.md"


def prose(path):
    return " ".join(path.read_text(encoding="utf-8").split())


@pytest.mark.parametrize("path", [WORKFLOW, REFERENCE, CLI, SKILL])
def test_resumption_surfaces_mark_release_boundary_and_local_links(path):
    text = path.read_text(encoding="utf-8")
    assert "Unreleased after 2.3.0" in text
    assert "resume-use" in text
    assert "sessions/" not in text
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if "://" in target:
            continue
        destination, _, fragment = target.partition("#")
        linked = path.parent / destination if destination else path
        assert linked.is_file(), target
        if fragment:
            headings = re.findall(r"^#{1,6} (.+)$", linked.read_text(), re.M)
            anchors = {re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-")
                       for heading in headings}
            assert fragment in anchors, target


@pytest.mark.parametrize("path", [WORKFLOW, SKILL])
def test_resumption_workflows_preserve_partial_completion_and_trusted_routes(path):
    text = prose(path)
    for required in ("trusted local", "Before the first import", "show USE", "show RECEIPT",
                     "snapshot.workspace", "before mutating the store",
                     "source_store_path", "not an update route",
                     "Stop", "refusal", "earlier successful imports remain committed",
                     "not-attempted", "partial completion", "identical capsule",
                     "ambiguous output", "idempotence", "per-file", "unknown",
                     "unavailable", "unqualified ready-to-resume", "not-checked"):
        assert required.lower() in text.lower()
    assert "import-transfer FILE --actor ACTOR --reason REASON" in text
    assert "do not ask the user to transcribe hashes" in text or "user need not copy hashes" in text


def test_handoff_template_records_context_and_each_input_outcome():
    text = WORKFLOW.read_text()
    template, = re.findall(r"```text\n(.*?)\n```", text, re.S)
    for field in ("Task:", "Store:", "Workspace:", "Consumer:", "Checked use:",
                  "Historical scope:", "Requested task scope:", "Actor:",
                  "Trusted local capsule routes:", "Update outcomes:", "Pending decisions:"):
        assert field in template
    assert "pending/imported/refused/unavailable/not attempted" in template
    assert "not a new configuration format" in prose(WORKFLOW)


def test_report_fields_and_status_semantics_are_documented():
    text = prose(REFERENCE)
    for field in ("schema_version", "operation", "status", "workspace", "id",
                  "receipt", "root", "identity", "unit_sha256", "scope", "historical",
                  "requested", "matches", "historical_check", "diagnostic",
                  "origin_analysis", "complete", "failures", "origins", "link",
                  "disposition", "membership", "reviewed_head", "known_head", "statuses",
                  "live_origin", "actions", "action", "links", "requires_semantic_judgment",
                  "limitations"):
        assert f"`{field}`" in text
    for required in ("Exit **0**", "Exit **1**", "Exit **2**", "complete `blocked` JSON report",
                     "2,048–1,048,576", "65,536", "no stdout", "no implicit upgrade",
                     "undetermined", "retaining sibling", "Null reviewed/known heads",
                     "unrelated imports are excluded", "incomplete analysis blocks",
                     "no prerequisite IDs", "subsequent operations revalidate"):
        assert required in text
    for action in ("inspect-origin", "inspect-and-reconsult", "read-for-requested-scope",
                   "restore-local-inputs"):
        assert f"`{action}`" in text


@pytest.mark.parametrize("path", [WORKFLOW, SKILL])
def test_task_scope_and_semantic_judgments_remain_explicit(path):
    text = prose(path).lower()
    for required in ("broad historical", "narrow scope", "known narrow challenge",
                     "different scope always requires", "complete consumer `read`",
                     "`record-use`", "correspondence", "acceptance", "independence",
                     "successor wording", "challenge disposition", "publication reflection",
                     "dependency-first", "complete", "two-line `read`", "preserve old",
                     "selected agent-visible material remains follow-up work",
                     "empirical usefulness improvement"):
        assert required in text
