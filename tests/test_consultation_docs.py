"""Structural contracts for shipped consultation guidance, without internals."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs/reference/consultation.md"
STORAGE = ROOT / "docs/reference/consultation-storage.md"
SKILL = ROOT / "skills/consult-project-memory/SKILL.md"


@pytest.mark.parametrize("path", [REFERENCE, STORAGE, SKILL])
def test_consultation_guidance_has_no_local_state_dependencies(path):
    text = path.read_text(encoding="utf-8")
    assert "sessions/" not in text
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if "://" in target or target.startswith("#"):
            continue
        destination = target.split("#", 1)[0]
        assert (path.parent / destination).is_file(), target


def test_walkthrough_shell_examples_are_syntactically_complete():
    blocks = re.findall(r"```bash\n(.*?)\n```", REFERENCE.read_text(), re.S)
    assert blocks
    for block in blocks:
        result = subprocess.run(["bash", "-n"], input=block, text=True,
                                capture_output=True)
        assert result.returncode == 0, result.stderr


def test_public_surfaces_link_to_consultation_contract():
    for relative in ("README.md", "docs/reference/cli.md",
                     "skills/consult-project-memory/SKILL.md"):
        assert "consultation.md" in (ROOT / relative).read_text()
    assert "consultation-storage.md" in REFERENCE.read_text()


def test_receipt_parser_requires_two_lines_and_success_before_handle():
    text = REFERENCE.read_text()
    # The runnable handoff must not treat an inspection line as a committed ID.
    assert 'if ! consult "$@" > "$response_path"; then' in text
    assert 'assert len(rows) == 2' in text
    assert 'rows[0]["status"] == "inspected" and "id" not in rows[0]' in text
    assert 'rows[1]["status"] == "receipt recorded"' in text
    assert 'handle = rows[-1]["id"]' in text
    assert 'capture plan_receipt read planning:kb-plan' in text
    assert 'capture plan_use record-use planning:kb-plan --receipt "$plan_receipt"' in text


def test_checked_use_guidance_retains_semantic_boundaries():
    text = " ".join(SKILL.read_text().split())
    for required in ("checked use itself is opted into", "consumer conclusion as",
                     "check exit 0", "One inspection line followed by a refusal",
                     "unchanged canonical claim bytes", "--replacement OLD=NEW",
                     "old choices do not carry forward", "checkpoint",
                     "does not probe anchors", "fail-open"):
        assert required in text
    assert "no automatic consultation gate" in text


def test_storage_reference_exposes_closed_payloads_and_retention_boundary():
    text = STORAGE.read_text()
    for required in ("Every object below is closed", "CREATE TABLE workspace",
                     "CREATE TABLE events", "binding_or_support_review =",
                     "inspection_text", "inspection_sha256", "Snapshot",
                     "lineage", "checkpoint =", "no final newline",
                     "No UPDATE or DELETE", "mode=rw", "5000 ms",
                     "does not authenticate a store", "Operation-scoped prospective capture"):
        assert required in text
