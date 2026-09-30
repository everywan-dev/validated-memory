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


def test_wal_one_recovery_and_selected_resolution_are_documented():
    """C1d documents local authority, exact replay and terminal uncertainty."""
    cli = (REPO_ROOT / "docs" / "reference" / "cli.md").read_text(
        encoding="utf-8"
    )
    journal = (REPO_ROOT / "docs" / "reference" / "journal.md").read_text(
        encoding="utf-8"
    )
    for text in (cli, journal):
        prose = " ".join(text.split())
        assert "stored exact" in prose
        assert "prepared" in prose and "descendants" in prose
        assert "claimless" in prose and "reconstruction" in prose
        assert "cannot authorize" in prose
        assert "terminal" in prose
    assert "fresh coherent-pair read" in " ".join(journal.split())
    assert "never recovers another transaction" in journal
    for text in (cli, journal):
        prose = " ".join(text.split())
        assert "independently valid current-adoption WALs" in prose
        assert "fail closed" in prose
        assert "identical bytes" in prose
        assert "unchanged opposite" in prose
        assert "exact restored target" in prose
        assert "claimless occurrence evidence" in prose
        assert "per-item" in prose
        assert "missing usable" in prose
        assert "recovery or targeted-resolution" in prose
        assert "target, history, observation or cleanup" in prose
        assert "later WALs remain untouched" in prose
        assert "unknown" in prose and "all three dispositions" in prose
        assert "another WAL's provision" in prose


def test_proof_bound_repair_successor_is_documented():
    """C1e documents candidate authority and closed successor outcomes."""
    cli = (REPO_ROOT / "docs" / "reference" / "cli.md").read_text(
        encoding="utf-8"
    )
    journal = (REPO_ROOT / "docs" / "reference" / "journal.md").read_text(
        encoding="utf-8"
    )
    for text in (cli, journal):
        prose = " ".join(text.split())
        assert "exact repaired bytes plus the raw opposite artifact" in prose
        assert "selected transaction" in prose
        assert "identity reuse" in prose
        assert "adoption mismatch" in prose
        assert "complete frozen pre-publication condition domain" in prose
        assert "exact bytes, mode, and identity" in prose
        assert "filesystem-byte target" in prose
        assert "before any temporary or WAL cleanup" in prose
        assert "journal: repair confirmed," in prose
        assert "do not repeat the confirmed repair" in prose
        assert "canonical checked-condition" in prose
        assert "selected-WAL cleanup" in prose
        assert "final coherent snapshot" in prose
        assert "exact private duplicate" in prose
        assert "selected WAL remains" in prose
        assert "selected WAL is removed" in prose
    assert "unrelated pre-existing conditions neither authorize nor forbid" in (
        " ".join(journal.split())
    )


ADR_0029 = "adr/0029-the-harness-link-survives-a-refusal-that-does-not-name-it.md"

# What each reference says of ADR 0029's guarded repair, word for word: a
# sentence that changes is a contract that changed, and this is where it is
# noticed. The whitespace of each document is normalised before the search.
GUARDED_REPAIR_SENTENCES = {
    "cli.md": (
        "`init` neither collapses `..` nor resolves a symlink",
        "nothing is collapsed lexically",
        "When the vault cannot be read the link is withheld.",
        "(a symlink or any other node is never opened and withholds)",
        "When the lock cannot be taken for a reason other than another "
        "process holding it, the vault is still read, without the lock, and "
        "the same rules apply.",
        "final names are equal without regard to case",
        "An unignored vault goes through the same repair, with the vault "
        "rules of an unreadable journal.",
        "(`the harness link was not restored: ...; run journal --check`)",
        "A lock another process takes between the refusal and the repair is "
        "waited for only for what remains of the run's `--lock-wait`, and then "
        "withholds it.",
        "A link that already resolves to `memory/` needs nothing and gets no "
        "WARNING, whichever refusal ended the run.",
        "and so does a vault whose transaction or preimage directory holds an "
        "entry that is not a regular file: entries are classified without "
        "following links, and nothing in the vault is opened",
    ),
    "journal.md": (
        "nothing is collapsed lexically",
        "When the lock or the vault cannot be read the link is withheld.",
        "(a symlink or any other node is never opened and withholds)",
        "When the lock cannot be taken for a reason other than another "
        "process holding it, the vault is still read, without the lock, and "
        "the same rules apply.",
        "final names are equal without regard to case",
        "**An unignored vault** gates the run without a journal refusal; its "
        "repair goes through the same guard with the vault rules of an "
        "unreadable journal.",
        "The lock serialises validated-memory processes only. A process "
        "outside the plugin that replaces the harness path after the `lstat` "
        "of that second reading and before the rename that publishes the link "
        "is not guarded against, nor is a process that replaces the staged "
        "link after its last identification and before the rename or the "
        "cleanup unlink that follows: the window runs from the last "
        "identification of the staged link, and from that `lstat`, to the "
        "rename or the cleanup unlink, the parent directory having been made "
        "and the temporary link staged before both, and the standard library "
        "has no compare-and-swap on a pathname. The relink never replaces a "
        "directory",
        "ends in `run journal --check`",
        "a lock another process takes between the refusal and the repair is "
        "waited for only for what remains of the run's `--lock-wait`, and then "
        "withholds it.",
        "A link that already resolves to `memory/` is not reported.",
    ),
    "hooks.md": (
        "a journal that cannot be read, or an unignored vault, restores it "
        "when the vault alone shows that no transaction can",
        "A corrupt journal therefore never allows the repair.",
    ),
}


def test_c1f_fail_open_boundary_and_protocol_ownership_are_documented():
    """Both references keep the two exceptions narrow and name policy owner."""
    docs = REPO_ROOT / "docs" / "reference"
    cli = (docs / "cli.md").read_text(encoding="utf-8")
    journal = (docs / "journal.md").read_text(encoding="utf-8")
    for text in (cli, journal):
        prose = " ".join(text.split())
        assert "cannot be read" in prose and "journal" in prose
        assert "vault's ignore entry" in prose
        assert "damaged" in prose
        assert "uncertainty after a current effect" in prose
        assert ADR_0029 in prose
        assert "takes the run-wide lock" in prose
    assert "unavailable or corrupt journal" not in cli
    assert "journal cannot be written at all, or refuses" not in cli

    for name, sentences in GUARDED_REPAIR_SENTENCES.items():
        prose = " ".join((docs / name).read_text(encoding="utf-8").split())
        for sentence in sentences:
            assert sentence in prose, f"{name} no longer says: {sentence!r}"
    hooks = " ".join((docs / "hooks.md").read_text(encoding="utf-8").split())
    assert ADR_0029 in hooks

    facade = (
        REPO_ROOT / "validated_memory" / "journal" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "the sole workflow-policy owner" in facade
    assert "it owns no history or topology policy" in facade
    assert "`init.py`, `cli.py` and `status.py` reach" in " ".join(facade.split())


ADR_0030 = (
    "adr/0030-the-session-start-run-is-bounded-and-a-vault-node-that-is-not-a-"
    "regular-file-never-blocks-it.md"
)

# What each document says of ADR 0030's bounded run and of a vault node that is
# not a regular file, word for word, under the same whitespace normalisation.
# The default of `--lock-wait` is not written here: `test_the_lock_wait_default_...`
# reads it from the source, so a document and the code cannot part.
LOCK_WAIT_AND_VAULT_NODE_SENTENCES = {
    "cli.md": (
        "`--lock-wait SECONDS` bounds how long the whole run waits for another "
        "validated-memory process to release the run-wide lock.",
        "SECONDS is a finite number of zero or more, and `0` does not wait; a "
        "negative number, `nan`, `inf` or a value that is not a number is "
        "usage exit 2 before any write.",
        "every lock it takes -- the adopting scope and the guarded harness "
        "repair below -- shares it",
        "The bound covers waiting for the lock: the work the run does once it "
        "holds the lock, `adopt.take_over` included, is not bounded by it.",
        "The plugin's `SessionStart` hook passes `--lock-wait 3`",
        "A vault node that is not a regular file never blocks a run and is "
        "never opened.",
        "`it is not a regular file; it was not opened`",
        "A preimage slot that is not a regular file refuses the mutation that "
        "needs it before any effect.",
        "A lock path that is not a regular file is held until the run's lock "
        "deadline, and then refused with an ERROR that names the path and "
        "says to remove it by hand.",
        "A **lock path that is not a regular file** blocks the repair without "
        "a wait: no process holds it.",
        "`the harness path changed while the repair waited`",
        "it reads that identity again once it has made the parent directory and "
        "staged the temporary link, immediately before the rename that replaces "
        "PATH.",
        "nor one that replaces the staged link after its last identification "
        "and before the rename or the cleanup unlink, the window running from "
        "that identification, and from that `lstat`, to the rename or the "
        "unlink",
        "A reading that stops the relink unlinks the staged link on a "
        "best-effort basis, and a staged link may remain if the parent cannot "
        "be written. If the staged link is replaced meanwhile, it is neither "
        "published nor removed, and a WARNING names it.",
        "A regular slot whose bytes differ, or which cannot be read, is still "
        "replaced, only while a second `lstat` shows it is still the file that "
        "was examined; one that has changed kind or file in the meantime is "
        "refused the same way.",
        "`the harness path could not be read`",
        "or a symlink that cannot be resolved because it loops, is left as it is",
        "`journal --check` answers for such an entry without opening it",
    ),
    "journal.md": (
        "**The wait for the lock ends at a deadline.**",
        "`init` fixes one deadline when it starts and every lock it takes in "
        "the run shares it, so taking the lock twice does not wait twice.",
        "A lock that an attempt broke, or found gone, earns one immediate "
        "retry past the deadline",
        "**A lock path that is not a regular file is never opened and never "
        "broken.**",
        "and then the run refuses with an ERROR that names the path and says "
        "to remove it by hand",
        "**A node in those directories that is not a regular file is never "
        "opened.**",
        "it is neither opened nor removed, because nothing proves whose it is",
        "only while a second `lstat` right before the removal shows it is "
        "still the regular file that was first examined",
        "it is not a regular file; it was not opened",
        "Two runs breaking one dead lock at the same instant can both end up "
        "holding it",
        "**The harness path is read again once the link is staged, immediately "
        "before the rename.**",
        "A staged link that the reading stops is unlinked on a best-effort "
        "basis, and one may remain if the parent cannot be written; a staged "
        "link replaced meanwhile is neither published nor removed, and a "
        "WARNING names it.",
        "or a symlink that loops, is likewise left as it is, with a WARNING",
        "once the link is staged and immediately before the rename that "
        "publishes it",
        "**A lock path that is not a regular file** blocks it without a wait, "
        "because no process holds it",
    ),
    "hooks.md": (
        "The hook runs `init --harness-memory` with `--lock-wait 3`.",
        "The bound covers waiting for the lock and not the work `init` does "
        "once it holds it",
        "leaves the link as it was",
    ),
}


def test_lock_wait_and_vault_node_contracts_are_documented():
    """The references state the run's lock bound and the vault-node rule."""
    docs = REPO_ROOT / "docs" / "reference"
    for name, sentences in LOCK_WAIT_AND_VAULT_NODE_SENTENCES.items():
        prose = " ".join((docs / name).read_text(encoding="utf-8").split())
        for sentence in sentences:
            assert sentence in prose, f"{name} no longer says: {sentence!r}"
        assert ADR_0030 in prose, f"{name} does not link ADR 0030"

    decision = " ".join(
        (REPO_ROOT / "docs" / "adr" / ADR_0030.removeprefix("adr/")).read_text(
            encoding="utf-8"
        ).split()
    )
    for sentence in (
        "`init --lock-wait SECONDS` takes a finite number, zero or more",
        "or a symlink that cannot be resolved because it loops, is reported as "
        "`the harness path could not be read`",
        "The hook passes `--lock-wait 3`.",
        "`lstat` the name before any open.",
        "`LOCK_NODE`",
        "`REPAIR_BLOCKED`",
        "The window between the re-read and the rename.",
        "The window runs from the last identification of the staged link, and "
        "from the recheck's `lstat`, to the rename or the cleanup unlink; the "
        "parent directory is made and the temporary link staged before both, "
        "not inside the window.",
        "and neither is a process that replaces the staged name after the last "
        "identification of the staged link and before the rename or the "
        "cleanup unlink that follows it: the identification does not protect "
        "the name it examined.",
        "A reading that stops the relink publishes nothing and unlinks the "
        "staged link on a best-effort basis: a staged link may remain if the "
        "parent cannot be written.",
        "A name that is no longer that link is neither published nor removed, "
        "whatever the reading answered, and the run says so with "
        "`REPAIR_BLOCKED`, naming the staged path.",
        "or whose target chain cannot be resolved (a loop included), is "
        "`REPAIR_BLOCKED`",
        "The window between the second `lstat` of a preimage slot and its "
        "removal.",
        "Two runs breaking the same dead lock.",
    ):
        assert sentence in decision, f"ADR 0030 no longer says: {sentence!r}"
    amended = " ".join(
        (REPO_ROOT / "docs" / "adr" / ADR_0029.removeprefix("adr/")).read_text(
            encoding="utf-8"
        ).split()
    )
    assert amended.count(f"]({ADR_0030.removeprefix('adr/')})") == 2


def test_the_lock_wait_default_in_the_documents_is_the_one_in_the_source():
    source = (REPO_ROOT / "validated_memory" / "journal" / "lock.py").read_text(
        encoding="utf-8"
    )
    default = re.search(r"^LOCK_WAIT_SECONDS = (\d+)$", source, re.MULTILINE)
    assert default, "LOCK_WAIT_SECONDS is no longer an integer literal"
    seconds = default.group(1)
    cli = " ".join(
        (REPO_ROOT / "docs" / "reference" / "cli.md").read_text(
            encoding="utf-8"
        ).split()
    )
    assert f"The default is {seconds}, the wait every other locking command has." in cli
    journal = " ".join(
        (REPO_ROOT / "docs" / "reference" / "journal.md").read_text(
            encoding="utf-8"
        ).split()
    )
    spelled = {"10": "ten"}.get(seconds, seconds)
    assert f"A run waits for a live holder for {spelled} seconds" in journal
    release = (REPO_ROOT / "docs" / "release-status.md").read_text(encoding="utf-8")
    assert f"(default {seconds})" in release


def test_the_restore_hook_header_states_its_lock_bound():
    script = " ".join(
        line.removeprefix("#").strip()
        for line in (REPO_ROOT / "hooks" / "restore-memory-symlink.sh")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith("#")
    )
    assert "`init` is called with `--lock-wait 3`" in script
    assert "The bound covers waiting for the lock and not the work `init` does" in script


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


def test_release_status_names_the_release_without_exposing_private_work():
    status = (REPO_ROOT / "docs" / "release-status.md").read_text(
        encoding="utf-8"
    )
    published = _markdown_section(status, "Published release")
    future = _markdown_section(status, "Future work")
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(
        encoding="utf-8"
    ))["project"]
    assert f"**{project['version']}**" in published
    assert f"tree/v{project['version']})" in published
    assert "resume-use" in published
    assert published.index("2.5.2 bounds the session-start run") < published.index(
        "2.5.1 keeps the harness-memory link"
    )
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
