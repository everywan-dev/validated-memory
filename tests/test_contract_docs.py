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
import tomllib
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


def test_journal_repair_synopsis_and_writing_modes_are_documented():
    cli = (REPO_ROOT / "docs" / "reference" / "cli.md").read_text(
        encoding="utf-8"
    )
    journal = (REPO_ROOT / "docs" / "reference" / "journal.md").read_text(
        encoding="utf-8"
    )
    command = (REPO_ROOT / "validated_memory" / "journal" / "command.py").read_text(
        encoding="utf-8"
    )
    synopsis = "python3 -P -m validated_memory journal --repair TRANSACTION_ID"
    assert synopsis in cli
    assert synopsis in journal
    for text in (cli, journal, command):
        assert "`--resolve` is the third mode and the only one that writes" not in text
        assert "`--resolve` is the third mode and the\nonly one that writes" not in text
    assert "--resolve` and `--repair`" in cli
    assert "--resolve` and `--repair`" in journal
    assert "`--resolve` and `--repair` are the two targeted modes" in command


def test_bootstrap_no_replace_and_opening_reconfirmation_are_documented():
    """The active C1b transition is explicit on both public reference paths."""
    cli = (REPO_ROOT / "docs" / "reference" / "cli.md").read_text(
        encoding="utf-8"
    )
    journal = (REPO_ROOT / "docs" / "reference" / "journal.md").read_text(
        encoding="utf-8"
    )
    for text in (cli, journal):
        assert "hard link" in text or "hard-link" in text
        assert "no-replace" in text
        assert "non-truncating" in text
        assert "coherent" in text
        assert "never unlinks" in text or "never unlink" in text
    assert "zero-byte" in journal
    assert "partial canonical opening" in journal


def test_append_range_reconfirmation_is_documented():
    """The active C1c transition is explicit on both public references."""
    cli = (REPO_ROOT / "docs" / "reference" / "cli.md").read_text(
        encoding="utf-8"
    )
    journal = (REPO_ROOT / "docs" / "reference" / "journal.md").read_text(
        encoding="utf-8"
    )
    for text in (cli, journal):
        assert "exact" in text
        assert "non-truncating" in text
        assert "directory" in text
        assert "coherent pair" in text
        assert "suffix" in text
        assert "opposite artifact" in text
    assert "directory-only" in journal
    assert "never replaces or truncates" in journal
    for text in (cli, journal):
        prose = " ".join(text.split())
        assert "absent, incomplete, or mismatched" in prose
        assert "does not complete or replay" in prose
        assert "trusted copy" in prose
        assert "environmental" in prose or "access obstruction" in prose
        assert "must not be repeated" in prose
    assert "complete claimed pair" not in journal
    assert "atomically republishes\nthe exact complete" not in journal


def test_journal_introduction_links_every_section():
    """The reader-journey navigation reaches every current H2 section."""
    journal = (REPO_ROOT / "docs" / "reference" / "journal.md").read_text(
        encoding="utf-8"
    )
    introduction, separator, body = journal.partition("\n## ")
    assert separator, "the journal reference has no H2 sections"
    headings = re.findall(r"^## (.+)$", "## " + body, re.MULTILINE)

    def _github_fragment(heading):
        words = re.sub(r"[^\w -]", "", heading.lower())
        return words.replace(" ", "-")

    section_fragments = [_github_fragment(heading) for heading in headings]
    assert len(section_fragments) == len(set(section_fragments)), (
        f"journal H2 headings derive duplicate fragments: {section_fragments}"
    )

    linked_fragments = re.findall(r"\]\(#([^)]+)\)", introduction)
    assert len(linked_fragments) == len(set(linked_fragments)), (
        f"journal introduction repeats section links: {linked_fragments}"
    )
    assert set(linked_fragments) == set(section_fragments), (
        "journal introduction fragments differ from H2 sections: "
        f"linked={linked_fragments}, sections={section_fragments}"
    )


def test_documentation_hub_links_every_reference_page_and_published_adr():
    docs = REPO_ROOT / "docs"
    hub = (docs / "README.md").read_text(encoding="utf-8")
    for target in ("installing.md", "adoption.md", "walkthrough.md",
                   "architecture.md", "troubleshooting.md", "release-status.md",
                   "adr/README.md"):
        assert f"]({target})" in hub, f"docs/README.md does not link {target}"
    for reference in sorted((docs / "reference").glob("*.md")):
        target = f"(reference/{reference.name})"
        assert target in hub, f"docs/README.md does not route to {reference.name}"

    adr_index = (docs / "adr" / "README.md").read_text(encoding="utf-8")
    for decision in sorted((docs / "adr").glob("*.md")):
        if decision.name == "README.md":
            continue
        assert f"({decision.name})" in adr_index, (
            f"docs/adr/README.md does not index {decision.name}"
        )


def test_release_status_names_2_4_capabilities_without_exposing_private_work():
    status = (REPO_ROOT / "docs" / "release-status.md").read_text(
        encoding="utf-8"
    )
    published = _markdown_section(status, "Published release")
    future = _markdown_section(status, "Future work")
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(
        encoding="utf-8"
    ))["project"]
    assert f"**{project['version']}**" in published
    assert "c398e77" in published
    assert "resume-use" in published
    assert "show-transfer IMPORT --origin PROJECT_UUID:UNIT_ID --material" in published
    assert "issue, design document or architecture decision" in future
    assert "does not establish that a feature is available" in future
    assert "public issue tracker" in future.lower()
    assert "sessions/" not in status
    assert "Packet C" not in status
    assert "J9" not in status


def _markdown_section(document, heading):
    sections = re.findall(
        rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)",
        document,
        re.MULTILINE | re.DOTALL,
    )
    assert len(sections) == 1, (
        f"expected exactly one '## {heading}' section, found {len(sections)}"
    )
    return sections[0]


def test_architecture_has_a_github_flow_and_equivalent_prose():
    architecture = (REPO_ROOT / "docs" / "architecture.md").read_text(
        encoding="utf-8"
    )
    diagram = re.search(r"```mermaid\s*\n(.*?)\n```", architecture, re.DOTALL)
    assert diagram, "architecture must contain a fenced Mermaid diagram"
    assert re.search(r"\bflowchart\s+LR\b", diagram.group(1))
    edges = set(re.findall(
        r"\b(Host|Plugin|CLI|Shell|Action|Project|Vault|Store)\s*"
        r"(?:\[[^\]]*\])?\s*-->\s*"
        r"(Host|Plugin|CLI|Shell|Action|Project|Vault|Store)\b",
        diagram.group(1),
        re.DOTALL,
    ))
    expected_edges = {
        ("Host", "Plugin"), ("Plugin", "CLI"),
        ("Shell", "CLI"), ("Action", "CLI"),
        ("CLI", "Project"), ("CLI", "Vault"), ("CLI", "Store"),
    }
    assert expected_edges <= edges, (
        f"architecture flow is missing edges: {sorted(expected_edges - edges)}"
    )

    prose = re.search(
        r"The diagram in words:\s*(.*?)(?=\n\n|\Z)", architecture, re.DOTALL
    )
    assert prose, "architecture must provide the diagram's prose equivalent"
    prose_text = " ".join(prose.group(1).replace("`", "").split()).lower()
    for relationship in (
        "claude code loads the plugin's skills and hooks, which invoke the python cli",
        "a shell, ci job or the github action can invoke the same cli independently",
        "the cli reads or writes the adopter repository",
        "init also uses a clone-local vault for recovery data",
        "checked consultation uses a separately chosen local sqlite store only when that workflow is explicitly invoked",
    ):
        assert relationship in prose_text, (
            f"diagram prose is missing the relationship: {relationship}"
        )
    assert "not semantic truth" in architecture
    assert "schema 2" not in architecture.lower()
    assert "group transaction" not in architecture.lower()
