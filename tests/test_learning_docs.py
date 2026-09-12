"""Structural pins for the shipped learning workflow reference."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_learning_reference_exists_and_routes_to_existing_contracts():
    path = ROOT / "docs/reference/learning.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# ")
    links = dict(re.findall(r"\[([^]]+)\]\(([^)]+)\)", text))
    required = {
        "agent-memory.md",
        "curated-knowledge.md",
        "recall.md",
        "../../skills/maintain-agent-memory/SKILL.md",
        "../../skills/create-knowledge-unit/SKILL.md",
    }
    assert required.issubset(links.values())
    for target in required:
        assert (path.parent / target).resolve().is_file(), target
    assert "review" in text.lower()
    assert "supersed" in text.lower()
    assert "interrupted" in text.lower()


def test_learning_reference_pins_capture_review_and_provenance_boundaries():
    text = (ROOT / "docs/reference/learning.md").read_text(encoding="utf-8").lower()
    for phrase in (
        "explicit request",
        "tentative",
        "supported",
        "qualified",
        "unresolved",
        "contradicted",
        "provenance",
        "same-layer successor",
        "not an atomic",
    ):
        assert phrase in text
