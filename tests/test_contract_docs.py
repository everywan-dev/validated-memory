"""The canonical contract enumerations must name exactly the base fields.

Same seam as `test_skills_structure.py`: this reads shipped content and never
imports the package's internals -- `BASE_FIELDS` is read out of the source as
text.

Two documents enumerate the whole base contract, and both are marked with
`<!-- canonical-base-contract -->`. The three other places that show a unit
(`README.md` and two blocks in `docs/walkthrough.md`) are partial by design:
none carries `provenance`. They are excluded by name, here, so the difference
is written down instead of guessed.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- canonical-base-contract -->"
CANONICAL = (
    REPO_ROOT / "docs" / "reference" / "curated-knowledge.md",
    REPO_ROOT / "skills" / "create-knowledge-unit" / "SKILL.md",
)
PARTIAL_BY_DESIGN = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "walkthrough.md",
)
MARKED_BLOCK = re.compile(
    re.escape(MARKER) + r"\s*\n```yaml\n(.*?)\n```", re.DOTALL
)
# The parser's own key grammar (`frontmatter.KEY_PATTERN`), anchored at
# column 0 so a nested key -- an anchor field, a rationale option -- never
# counts as a top-level one: a stray key is never silently ignored.
TOP_LEVEL_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*):", re.MULTILINE)


def _base_fields():
    source = (REPO_ROOT / "validated_memory" / "contract.py").read_text(
        encoding="utf-8"
    )
    declaration = re.search(r"^BASE_FIELDS = \((.*?)\)", source, re.DOTALL | re.MULTILINE)
    assert declaration, "BASE_FIELDS is no longer a parenthesised tuple literal"
    return set(re.findall(r'"([A-Za-z_][A-Za-z0-9_.-]*)"', declaration.group(1)))


def test_every_canonical_block_names_exactly_the_base_contract():
    fields = _base_fields()
    assert fields, "no BASE_FIELDS found"

    for path in CANONICAL:
        text = path.read_text(encoding="utf-8")
        blocks = MARKED_BLOCK.findall(text)
        assert len(blocks) == 1, (
            f"{path} carries {len(blocks)} {MARKER} block(s); expected exactly one"
        )
        keys = TOP_LEVEL_KEY.findall(blocks[0])
        assert len(keys) == len(set(keys)), (
            f"{path}: the canonical block repeats a key: {keys}"
        )
        assert sorted(keys) == sorted(fields), (
            f"{path}: the canonical block names {sorted(keys)}, "
            f"the contract declares {sorted(fields)}"
        )


def test_the_partial_examples_are_not_marked_canonical():
    for path in PARTIAL_BY_DESIGN:
        assert MARKER not in path.read_text(encoding="utf-8"), (
            f"{path} holds examples that legitimately omit optional fields; "
            "marking it canonical would force rewriting them"
        )


def test_the_extension_stub_names_every_base_field():
    stub = (REPO_ROOT / "validated_memory" / "init.py").read_text(encoding="utf-8")
    prose = re.search(r"EXTENSION_STUB = \"\"\"(.*?)\"\"\"", stub, re.DOTALL)
    assert prose, "EXTENSION_STUB is no longer a triple-quoted string"
    for field in _base_fields():
        assert f"`{field}`" in prose.group(1), (
            f"the extension stub does not mention the base field '{field}'"
        )
