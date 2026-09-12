"""Public selected-material contracts, without package-internal imports."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs/reference/transfer.md"
STORAGE = ROOT / "docs/reference/transfer-storage.md"
WORKFLOW = ROOT / "docs/reference/everyday-workflow.md"
CLI = ROOT / "docs/reference/cli.md"
SKILL = ROOT / "skills/consult-project-memory/SKILL.md"


def prose(path):
    return " ".join(path.read_text(encoding="utf-8").split())


@pytest.mark.parametrize("path", [REFERENCE, STORAGE, WORKFLOW, CLI, SKILL])
def test_selected_material_is_public_unreleased_and_linked(path):
    text = path.read_text(encoding="utf-8")
    assert "unreleased after 2.3.0" in text.lower()
    assert "--material" in text
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


def test_selected_view_grammar_and_fail_explicit_bounds():
    text = prose(REFERENCE)
    assert "show-transfer IMPORT --origin PROJECT_UUID:UNIT_ID --material [--max-bytes N]" in text
    for required in ("must occur together, once each", "exact member", "selected receipt",
                     "existing inventory", "1,048,576", "2,048–1,048,576",
                     "128-node/512-edge", "before stdout", "without truncation",
                     "Missing or ambiguous required proof", "cyclic canonical ancestry", "valid later reference cycle", "Exit 0",
                     "exit 1", "exit 2", "Known ineligibility alone does not prevent",
                     "No foreign paths are opened", "source_store_path", "read-only",
                     "no event, receipt or inspection handle", "no implicit upgrade"):
        assert required in text


def test_selected_membership_is_distinct_from_context_and_ancestry_categories():
    text = prose(STORAGE)
    for required in ("selection = {workspace, receipt, identity, unit_sha256, binding, scope}",
                     "known_heads = [{workspace, head}]", "`status: historical`", "selection_closures",
                     "via_links", "exact receipt-selected binding membership",
                     "Empty `via_links`", "qualified `via_links` merged",
                     "Additional context declarations", "without acquiring receipt-selected membership",
                     "{workspace, project, file}", "material.units", "material.predecessor_files",
                     "only canonical predecessor revisions without a retained binding",
                     "union of the predecessor roles across both arrays",
                     "`selected`", "`dependency`", "`predecessor`", "`successor`",
                     "`context-binding`", "`transitive`", "whether earlier or later",
                     "Support identity includes SHA as well as path"):
        assert required in text


def test_material_closure_and_opaque_audit_boundary_preserve_proof():
    text = prose(STORAGE)
    for required in ("every matching retained binding/review", "without receipts",
                     "connected supersession family", "sibling successors", "without binding/support evidence",
                     "independent and overridden dispositions", "proposals, challenges, incorporation",
                     "decisions, resolutions and choices", "Proposed bytes never become canonical proof",
                     "prior pointers", "inspection handles", "snapshot heads",
                     "registration/relocation IDs", "qualified opaque audit references",
                     "canonical remedy/successor material are not audit exceptions",
                     "not complete chronology or a self-contained replay capsule",
                     "Whole history remains retained", "helpers do not reset it",
                     "transfer_links.source_material", "inspection_text", "replay bytes remain unchanged"):
        assert required in text


@pytest.mark.parametrize("path", [WORKFLOW, SKILL])
def test_workflow_uses_material_before_proposals_without_replacing_review(path):
    text = prose(path).lower()
    for required in ("before proposal authoring", "show-transfer import --origin project_uuid:unit_id --material",
                     "selection_closures", "context-binding", "earlier or later",
                     "mandatory complete two-line", "`link-transfer`", "`inspect`", "`read`",
                     "whole history remains in transport/storage", "no foreign paths",
                     "empirical usefulness improvement"):
        assert required in text
    assert "selected agent-visible material remains follow-up work" not in text


def test_runnable_example_displays_material_and_checks_membership_before_authoring():
    text = REFERENCE.read_text()
    select = 'invoke b 0 show-transfer "$imported" --origin "$origin_project:kb-a2" --material'
    assert select in text
    assert text.index(select) < text.index("(root / 'basis-candidate.md').write_text")
    assert "view['selection']['binding'] in initial['bindings']" in text
    assert "sys.stdout.buffer.write(encoded + b'\\n')" in text
    assert "item['workspace'] == view['selection']['workspace']" in text
    assert "item['project'] == view['selection']['identity']['project']" in text
    assert "item['file']['path']" in text
    assert text.index('retain basis_link') < text.index('retain basis_inspection')
    assert text.index('retain plan_link') < text.index('retain plan_inspection')
    assert text.index('retain local_receipt') < text.index('retain local_use')


def test_measurement_is_attributed_to_fixture_without_token_or_size_guarantee():
    text = prose(REFERENCE)
    for required in ("tests/test_transfer_material.py", "same imported state",
                     "UTF-8 wire outputs", "one unit, one support file and one binding event",
                     "no predecessor files, corrections or links", "observed fixture sizes",
                     "not fixed output sizes", "every selected view is smaller",
                     "test_complete_everyday_handoff_material_matches_nested_history_and_measures_wire",
                     "every material file and event", "exact selected binding membership",
                     "all three stores and adopters remain unchanged",
                     "Bytes are not model tokens", "does not establish empirical usefulness"):
        assert required in text
