"""End-to-end tests for the `recall` subcommand.

Every test drives the CLI as a subprocess over a fixture adopter tree and
asserts on exit codes, output and (non-)mutation of the tree. No package
internals are imported.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SCHEMA_DOCUMENT = "fields:\n  - name: domain\n    type: enum\n    values:\n      - network\n"


def _memory_frontmatter(description, kind="reference"):
    return f'name: {{name}}\ndescription: "{description}"\nmetadata:\n  type: {kind}\n'


def _write_memory_entry(write_memory, name, description, body="Body text.\n"):
    frontmatter = (
        f"name: {name}\n"
        f'description: "{description}"\n'
        "metadata:\n"
        "  type: reference\n"
    )
    write_memory(f"{name}.md", frontmatter, body)


def _write_unit(write_unit, unit_id, heading, body_extra="", supersedes=None, anchors=True):
    lines = [f"id: {unit_id}\n", "evidence: measured\n"]
    if supersedes:
        lines.append("supersedes:\n")
        for target in supersedes:
            lines.append(f"  - {target}\n")
    if anchors:
        lines.append("anchors:\n")
        lines.append("  - system: redis\n")
        lines.append("    kind: config\n")
        lines.append('    captured_at: "2026-01-01"\n')
        lines.append("    payload: {}\n")
    frontmatter = "".join(lines)
    body = f"# {heading}\n\n{body_extra or heading + ' body text.'}\n"
    write_unit(f"{unit_id}.md", frontmatter, body)


@pytest.fixture
def basic_corpus(write_index, write_memory, write_unit):
    write_index("# Agent memory\n\n- [cache-timeout](cache-timeout.md)\n")
    _write_memory_entry(write_memory, "cache-timeout", "cache timeout")
    _write_unit(write_unit, "k-cache", "Cache retry budget", "Cache retry budget details.")


def test_valid_both_layers_match(adopter_dir, run_cli, basic_corpus):
    result = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["complete"] is True
    identities = {(r["layer"], r["identity"]) for r in payload["results"]}
    assert ("memory", "cache-timeout") in identities
    assert ("knowledge", "k-cache") in identities
    for record in payload["results"]:
        assert record["match"]["exact"] is False
        assert "cache" in record["match"]["label"] + record["match"]["path"] + record["match"]["body"]


def test_no_match_is_a_clean_empty_result(adopter_dir, run_cli, basic_corpus):
    result = run_cli("recall", "absentword", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["complete"] is True
    assert payload["coverage"]["matched"] == 0
    assert payload["results"] == []
    assert any("different" in g.lower() for g in payload["guidance"])


def test_missing_selected_directory_fails(adopter_dir, run_cli):
    (adopter_dir / "knowledge").mkdir()
    result = run_cli("recall", "x", "--layer", "memory", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    assert "Traceback" not in result.stderr
    # Which failure occurred, not merely that one did.
    assert [d["field"] for d in payload["diagnostics"]] == ["acquisition"]
    assert "no 'memory' directory found" in payload["diagnostics"][0]["message"]


def test_empty_directory_is_valid_with_no_matches(adopter_dir, run_cli, write_index):
    write_index("# Agent memory\n")
    (adopter_dir / "knowledge").mkdir()
    result = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["coverage"]["matched"] == 0


def test_malformed_knowledge_frontmatter_fails_closed(adopter_dir, run_cli, basic_corpus):
    (adopter_dir / "knowledge" / "broken.md").write_text("not frontmatter\n", encoding="utf-8")
    result = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    assert payload["diagnostics"]


def test_malformed_config_fails_closed(adopter_dir, run_cli, basic_corpus, write_document):
    write_document("validated-memory.md", "unknown_field: 1\n")
    result = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    diagnostic = payload["diagnostics"][0]
    assert diagnostic["path"] == "validated-memory.md"
    assert diagnostic["field"] == "unknown_field"
    assert "unknown configuration field" in diagnostic["message"]


def test_malformed_index_fails_closed(adopter_dir, run_cli, write_index, write_memory):
    write_index("# Agent memory\n\n- [cache](cache.md)\n- [ghost](ghost.md)\n")
    _write_memory_entry(write_memory, "cache", "cache timeout")
    (adopter_dir / "knowledge").mkdir()
    result = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False


def test_malformed_verdict_log_fails_closed(adopter_dir, run_cli, basic_corpus):
    (adopter_dir / "verdicts.jsonl").write_text("not json\n", encoding="utf-8")
    result = run_cli("recall", "cache", "--layer", "knowledge", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False


def test_missing_verdict_log_is_safe(adopter_dir, run_cli, basic_corpus):
    assert not (adopter_dir / "verdicts.jsonl").exists()
    result = run_cli("recall", "cache", "--layer", "knowledge", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["results"][0]["anchors"][0]["verdict"] == "unknown"
    assert payload["results"][0]["anchors"][0]["checked_at"] is None


def test_memory_supersession_redirects_to_active_endpoint(
    adopter_dir, run_cli, write_index, write_memory
):
    write_index("# Agent memory\n\n- [cache](cache.md)\n- [cache v2](cache-v2.md)\n")
    _write_memory_entry(write_memory, "cache", "superseded by [[cache-v2]]", "Old cache note.\n")
    _write_memory_entry(write_memory, "cache-v2", "cache timeout, revised", "New cache note.\n")
    (adopter_dir / "knowledge").mkdir()
    result = run_cli("recall", "cache", "--layer", "memory", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert len(payload["results"]) == 1
    endpoint = payload["results"][0]
    assert endpoint["identity"] == "cache-v2"
    assert endpoint["state"] == "active"
    assert endpoint["redirected_from"] == [{"identity": "cache", "path": "memory/cache.md"}]


def test_knowledge_multiple_successors_both_redirect(adopter_dir, run_cli, write_unit):
    (adopter_dir / "memory").mkdir()
    (adopter_dir / "memory" / "MEMORY.md").write_text("# Agent memory\n", encoding="utf-8")
    _write_unit(write_unit, "k-old", "Old cache policy")
    _write_unit(write_unit, "k-new-a", "New cache policy A", supersedes=["k-old"])
    _write_unit(write_unit, "k-new-b", "New cache policy B", supersedes=["k-old"])
    result = run_cli("recall", "cache", "--layer", "knowledge", "--limit", "20", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    identities = {r["identity"] for r in payload["results"]}
    assert identities == {"k-new-a", "k-new-b"}
    for record in payload["results"]:
        assert record["redirected_from"] == [{"identity": "k-old", "path": "knowledge/k-old.md"}]


def test_include_superseded_returns_history_with_direct_successor(
    adopter_dir, run_cli, write_index, write_memory
):
    write_index("# Agent memory\n\n- [cache](cache.md)\n- [cache v2](cache-v2.md)\n")
    _write_memory_entry(write_memory, "cache", "superseded by [[cache-v2]]", "Old cache note.\n")
    _write_memory_entry(write_memory, "cache-v2", "cache timeout, revised", "New cache note.\n")
    (adopter_dir / "knowledge").mkdir()
    result = run_cli(
        "recall", "cache", "--layer", "memory", "--include-superseded",
        "--format", "json", cwd=adopter_dir,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    by_identity = {r["identity"]: r for r in payload["results"]}
    assert by_identity["cache"]["state"] == "superseded"
    assert by_identity["cache"]["successors"] == [{"identity": "cache-v2", "path": "memory/cache-v2.md"}]
    assert by_identity["cache"]["redirected_from"] == []


def test_memory_supersession_cycle_fails_closed(adopter_dir, run_cli, write_index, write_memory):
    write_index("# Agent memory\n\n- [a](a.md)\n- [b](b.md)\n")
    _write_memory_entry(write_memory, "a", "superseded by [[b]]")
    _write_memory_entry(write_memory, "b", "superseded by [[a]]")
    (adopter_dir / "knowledge").mkdir()
    result = run_cli("recall", "a", "--layer", "memory", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert any("cycle" in d["message"] for d in payload["diagnostics"])


def test_map_lists_active_records_without_query(adopter_dir, run_cli, basic_corpus):
    result = run_cli("recall", "--map", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["mode"] == "map"
    assert payload["coverage"]["matched"] == payload["coverage"]["eligible"]
    for record in payload["results"]:
        assert record["match"] == {"exact": False, "label": [], "path": [], "body": []}


def test_query_and_map_are_mutually_exclusive(adopter_dir, run_cli, basic_corpus):
    result = run_cli("recall", "cache", "--map", cwd=adopter_dir)
    assert result.returncode == 2


def test_query_required_without_map(adopter_dir, run_cli, basic_corpus):
    result = run_cli("recall", cwd=adopter_dir)
    assert result.returncode == 2


def test_query_without_word_token_is_a_usage_error(adopter_dir, run_cli, basic_corpus):
    result = run_cli("recall", "!!!", cwd=adopter_dir)
    assert result.returncode == 2


def test_query_over_byte_limit_is_a_usage_error(adopter_dir, run_cli, basic_corpus):
    result = run_cli("recall", "a" * 4097, cwd=adopter_dir)
    assert result.returncode == 2


def test_limit_out_of_range_is_a_usage_error(adopter_dir, run_cli, basic_corpus):
    result = run_cli("recall", "cache", "--limit", "0", cwd=adopter_dir)
    assert result.returncode == 2


def test_max_bytes_out_of_range_is_a_usage_error(adopter_dir, run_cli, basic_corpus):
    result = run_cli("recall", "cache", "--max-bytes", "100", cwd=adopter_dir)
    assert result.returncode == 2


def test_ranking_prefers_label_over_body_matches(adopter_dir, run_cli, write_index, write_memory):
    write_index("# Agent memory\n\n- [a](a.md)\n- [b](b.md)\n")
    _write_memory_entry(write_memory, "a", "unrelated label", "This note mentions widget in the body.\n")
    _write_memory_entry(write_memory, "b", "widget label", "Nothing relevant here.\n")
    (adopter_dir / "knowledge").mkdir()
    result = run_cli("recall", "widget", "--layer", "memory", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert [r["identity"] for r in payload["results"]] == ["b", "a"]


def test_or_semantics_match_any_query_token(adopter_dir, run_cli, write_index, write_memory):
    write_index("# Agent memory\n\n- [a](a.md)\n")
    _write_memory_entry(write_memory, "a", "alpha only", "Body about alpha.\n")
    (adopter_dir / "knowledge").mkdir()
    result = run_cli("recall", "alpha beta", "--layer", "memory", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["coverage"]["matched"] == 1


def test_line_and_digest_reflect_the_source_document(adopter_dir, run_cli, basic_corpus):
    import hashlib

    result = run_cli("recall", "cache", "--layer", "memory", "--format", "json", cwd=adopter_dir)
    payload = json.loads(result.stdout)
    record = payload["results"][0]
    text = (adopter_dir / "memory" / "cache-timeout.md").read_text(encoding="utf-8")
    assert record["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    lines = text.split("\n")
    assert lines[record["line"] - 1].strip() == record["excerpt"]


def test_byte_budget_omits_low_priority_results(adopter_dir, run_cli, write_index, write_memory):
    entries = "\n".join(f"- [n{i}](n{i}.md)" for i in range(10))
    write_index(f"# Agent memory\n\n{entries}\n")
    for i in range(10):
        _write_memory_entry(write_memory, f"n{i}", "cache entry", "cache " * 400 + "\n")
    (adopter_dir / "knowledge").mkdir()
    result = run_cli(
        "recall", "cache", "--layer", "memory", "--limit", "10",
        "--max-bytes", "2048", "--format", "json", cwd=adopter_dir,
    )
    assert result.returncode == 0
    assert len(result.stdout.encode("utf-8")) <= 2048 + 1
    payload = json.loads(result.stdout)
    selection = payload["selection"]
    assert payload["coverage"]["matched"] == 10
    # Exact counts: a disjunction would hold whenever anything at all dropped.
    assert selection["returned"] == 2
    assert selection["omitted_limit"] == 0
    assert selection["omitted_budget"] == 8
    assert len(payload["results"]) == 2
    assert selection["excerpt_truncated"] == 2


def test_control_characters_are_escaped_in_text_output(adopter_dir, run_cli, write_index, write_memory):
    write_index("# Agent memory\n\n- [ctl](ctl.md)\n")
    _write_memory_entry(write_memory, "ctl", "cache control", "cache line with a bell \x07 in it.\n")
    (adopter_dir / "knowledge").mkdir()
    result = run_cli("recall", "cache", "--layer", "memory", "--format", "text", cwd=adopter_dir)
    assert result.returncode == 0
    assert "\x07" not in result.stdout
    assert "\\u0007" in result.stdout


def test_no_symlinked_layer_root_is_read(adopter_dir, run_cli, tmp_path, basic_corpus):
    outside = tmp_path / "outside-memory"
    outside.mkdir()
    (outside / "MEMORY.md").write_text("# OUTSIDECONTENT memory\n", encoding="utf-8")
    import shutil

    shutil.rmtree(adopter_dir / "memory")
    os.symlink(outside, adopter_dir / "memory")
    result = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    # Refused as a link, not read and then rejected by validation.
    assert any("symlink" in d["message"] for d in payload["diagnostics"])
    assert "OUTSIDECONTENT" not in result.stdout


def test_no_symlinked_member_file_is_read(adopter_dir, run_cli, write_index, write_memory, tmp_path):
    write_index("# Agent memory\n\n- [cache](cache.md)\n")
    _write_memory_entry(write_memory, "cache", "cache timeout")
    outside_target = tmp_path / "outside.md"
    outside_target.write_text("OUTSIDECONTENT: not part of the tree\n", encoding="utf-8")
    os.symlink(outside_target, adopter_dir / "memory" / "linked.md")
    (adopter_dir / "knowledge").mkdir()
    result = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    assert any(
        "symlink" in d["message"] and "linked.md" in d["message"]
        for d in payload["diagnostics"]
    )
    assert "OUTSIDECONTENT" not in result.stdout


def test_no_symlinked_config_is_read(adopter_dir, run_cli, basic_corpus, tmp_path):
    outside_config = tmp_path / "outside-config.md"
    outside_config.write_text(
        "---\nextension:\n  schema: OUTSIDECONTENT.md\n  version: \"1\"\n---\n",
        encoding="utf-8",
    )
    os.symlink(outside_config, adopter_dir / "validated-memory.md")
    result = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    assert any(
        "symlink" in d["message"] and "validated-memory.md" in d["message"]
        for d in payload["diagnostics"]
    )
    assert "OUTSIDECONTENT" not in result.stdout


def test_no_symlinked_log_is_read(adopter_dir, run_cli, basic_corpus, tmp_path):
    outside_log = tmp_path / "outside.jsonl"
    outside_log.write_text('{"OUTSIDECONTENT": true}\n', encoding="utf-8")
    os.symlink(outside_log, adopter_dir / "verdicts.jsonl")
    result = run_cli("recall", "cache", "--layer", "knowledge", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    assert any(
        "symlink" in d["message"] and "verdicts.jsonl" in d["message"]
        for d in payload["diagnostics"]
    )
    assert "OUTSIDECONTENT" not in result.stdout


def test_deterministic_repeated_invocations_match(adopter_dir, run_cli, basic_corpus):
    first = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    second = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    assert first.stdout == second.stdout
    assert first.returncode == second.returncode == 0


def test_no_mutation_with_bytecode_disabled(adopter_dir, basic_corpus):
    """Nothing is created, in the adopter tree or beside the package itself.

    `PYTHONDONTWRITEBYTECODE` is set here because `-P` does not disable
    bytecode: the package lives outside the adopter tree, so a snapshot of
    that tree alone could never have caught an interpreter-created file."""
    package_dir = REPO_ROOT / "validated_memory"
    before = _snapshot(adopter_dir)
    package_before = _snapshot(package_dir)
    result = _run_env(
        adopter_dir, {"PYTHONDONTWRITEBYTECODE": "1"},
        "recall", "cache", "--format", "json",
    )
    assert result.returncode == 0
    assert _snapshot(adopter_dir) == before
    assert _snapshot(package_dir) == package_before


def _snapshot(directory):
    return sorted(
        path.relative_to(directory).as_posix() for path in directory.rglob("*")
    )


def _run_env(adopter_dir, extra_env, *args):
    """Run the CLI as a subprocess with additional environment variables."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", *args],
        capture_output=True, text=True, cwd=adopter_dir, env=env, check=False,
    )


def _run_with_audit_hook(adopter_dir, hook_source, *args):
    """Run the CLI with a standard-library audit hook that acts mid-run.

    The hook is an ordinary `sitecustomize` module on `PYTHONPATH`, entirely
    outside the package: no product flag, seam or import is involved. It fires
    on the interpreter's own `os.scandir` audit event, so the change lands at
    a fixed point of acquisition instead of racing a sleep."""
    hook_dir = adopter_dir.parent / "audit-hook"
    hook_dir.mkdir(exist_ok=True)
    (hook_dir / "sitecustomize.py").write_text(hook_source, encoding="utf-8")
    return _run_env(
        adopter_dir,
        {
            "PYTHONPATH": os.pathsep.join([str(hook_dir), str(REPO_ROOT)]),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        *args,
    )


def _act_on_second_scandir(body):
    """An audit-hook module that runs `body` at the re-enumeration pass.

    The first `os.scandir` is acquisition's enumeration, the second is the
    re-verification pass: acting between them is exactly the concurrent change
    the specification requires recall to detect."""
    return (
        "import sys\n"
        "_state = {'seen': 0, 'done': False}\n"
        "def _hook(event, args):\n"
        "    if event != 'os.scandir' or _state['done']:\n"
        "        return\n"
        "    _state['seen'] += 1\n"
        "    if _state['seen'] < 2:\n"
        "        return\n"
        "    _state['done'] = True\n"
        + "".join(f"    {line}\n" for line in body.strip().split("\n"))
        + "sys.addaudithook(_hook)\n"
    )


def _act_on_nth_scandir(index, body):
    """An audit-hook module that runs `body` just before the nth `os.scandir`.

    Same mechanism as `_act_on_second_scandir`, with the enumeration pass
    chosen explicitly: a tree that holds a subdirectory is scanned more than
    once per pass, so re-verification does not always start at the second."""
    return (
        "import sys\n"
        f"_state = {{'seen': 0, 'done': False, 'want': {index}}}\n"
        "def _hook(event, args):\n"
        "    if event != 'os.scandir' or _state['done']:\n"
        "        return\n"
        "    _state['seen'] += 1\n"
        "    if _state['seen'] < _state['want']:\n"
        "        return\n"
        "    _state['done'] = True\n"
        + "".join(f"    {line}\n" for line in body.strip().split("\n"))
        + "sys.addaudithook(_hook)\n"
    )


def _act_on_nth_open(name, index, body):
    """An audit-hook module that runs `body` just before the nth `open` of `name`.

    Re-verification opens the selected root and stamps the descriptor before it
    enumerates, so acting at the enumeration event is already too late to move
    the root out from under it. The interpreter's `open` event fires with the
    descriptor-relative name, which is what this counts."""
    return (
        "import sys\n"
        f"_state = {{'seen': 0, 'done': False, 'want': {index}, 'name': {name!r}}}\n"
        "def _hook(event, args):\n"
        "    if event != 'open' or _state['done'] or args[0] != _state['name']:\n"
        "        return\n"
        "    _state['seen'] += 1\n"
        "    if _state['seen'] < _state['want']:\n"
        "        return\n"
        "    _state['done'] = True\n"
        + "".join(f"    {line}\n" for line in body.strip().split("\n"))
        + "sys.addaudithook(_hook)\n"
    )


def _record_opened_paths(record_path):
    """An audit-hook module that logs every path the run opens or scans.

    The interpreter raises `open` for `os.open` and `io.open` and `os.scandir`
    for enumeration, so the log is an independent record of what the process
    actually touched: if a file under a refused symlink were read, its real
    path would appear here even though nothing about it reached stdout."""
    return (
        "import sys\n"
        f"_record = {record_path!r}\n"
        "_state = {'busy': False}\n"
        "def _hook(event, args):\n"
        "    if event not in ('open', 'os.scandir') or _state['busy']:\n"
        "        return\n"
        "    target = args[0]\n"
        "    if not isinstance(target, (str, bytes)):\n"
        "        return\n"
        "    if isinstance(target, bytes):\n"
        "        target = target.decode('utf-8', 'replace')\n"
        "    _state['busy'] = True\n"
        "    try:\n"
        "        with open(_record, 'a', encoding='utf-8') as handle:\n"
        "            handle.write(target + '\\n')\n"
        "    finally:\n"
        "        _state['busy'] = False\n"
        "sys.addaudithook(_hook)\n"
    )


def _write_nested_memory(write_memory, directory, name, description, body):
    """Write `memory/<directory>/<name>.md`; identity is the file's own stem."""
    frontmatter = (
        f"name: {name}\n"
        f'description: "{description}"\n'
        "metadata:\n"
        "  type: reference\n"
    )
    write_memory(f"{directory}/{name}.md", frontmatter, body)


def test_missing_memory_index_is_a_bounded_envelope_not_a_traceback(
    adopter_dir, run_cli, basic_corpus
):
    (adopter_dir / "memory" / "MEMORY.md").unlink()
    result = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    assert "Traceback" not in result.stderr
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    assert any("MEMORY.md" in d["message"] for d in payload["diagnostics"])
    assert result.stderr.strip() == (
        "recall: acquisition or validation failed; see diagnostics"
    )


def test_config_comment_is_read_the_way_validate_reads_it(
    adopter_dir, run_cli, basic_corpus, write_document
):
    """One parser for the configuration, so recall and validate cannot differ."""
    write_document(
        "validated-memory.md",
        'extension:\n  schema: schema.md  # the schema\n  version: "1"\n',
    )
    write_document("schema.md", SCHEMA_DOCUMENT)
    result = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    assert "Traceback" not in result.stderr
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["complete"] is True
    # Index, configuration and schema: every auxiliary file that was read.
    assert payload["coverage"]["auxiliary_read"] == 3
    assert payload["results"]
    validated = run_cli("validate", cwd=adopter_dir)
    assert validated.returncode == 0


def _write_chain(write_index, write_memory, count, body_for):
    entries = "\n".join(f"- [n{i}](n{i}.md)" for i in range(count))
    write_index(f"# Agent memory\n\n{entries}\n")
    for i in range(count):
        description = (
            f"superseded by [[n{i + 1}]]" if i < count - 1 else "the active endpoint"
        )
        _write_memory_entry(write_memory, f"n{i}", description, body_for(i))


def test_deep_supersession_chain_redirects_to_the_active_endpoint(
    adopter_dir, run_cli, write_index, write_memory
):
    """1,200 hops resolve iteratively; a recursive traversal raised here."""
    _write_chain(
        write_index, write_memory, 1200,
        lambda i: ("Body mentioning zqxoldest.\n" if i == 0 else "Body text.\n"),
    )
    (adopter_dir / "knowledge").mkdir()
    result = run_cli(
        "recall", "zqxoldest", "--layer", "memory", "--format", "json", cwd=adopter_dir
    )
    assert "Traceback" not in result.stderr
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["complete"] is True
    assert payload["coverage"]["matched"] == 1
    assert len(payload["results"]) == 1
    endpoint = payload["results"][0]
    assert endpoint["identity"] == "n1199"
    assert endpoint["state"] == "active"
    assert endpoint["redirected_from"] == [{"identity": "n0", "path": "memory/n0.md"}]


def test_deep_chain_matching_everywhere_omits_the_oversized_record(
    adopter_dir, run_cli, write_index, write_memory
):
    """1,199 redirect origins do not fit: the record is omitted whole.

    Identifiers and redirect origins are never cut and the byte cap is never
    exceeded, so a candidate that cannot fit is reported as omitted instead."""
    _write_chain(
        write_index, write_memory, 1200, lambda i: "Body mentioning zqxchain.\n"
    )
    (adopter_dir / "knowledge").mkdir()
    result = run_cli(
        "recall", "zqxchain", "--layer", "memory", "--max-bytes", "12288",
        "--format", "json", cwd=adopter_dir,
    )
    assert "Traceback" not in result.stderr
    assert result.returncode == 0
    assert len(result.stdout.encode("utf-8")) <= 12288 + 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is True
    assert payload["coverage"]["eligible"] == 1
    assert payload["coverage"]["matched"] == 1
    assert payload["results"] == []
    assert payload["selection"]["returned"] == 0
    assert payload["selection"]["omitted_limit"] == 0
    assert payload["selection"]["omitted_budget"] == 1


def test_unreadable_document_fails_with_the_counts_it_observed(
    adopter_dir, run_cli, write_unit
):
    """A document that cannot be decoded is a corpus that cannot be validated."""
    _write_unit(write_unit, "k-cache", "Cache retry budget")
    (adopter_dir / "knowledge" / "broken.md").write_bytes(b"---\nid: x\n---\n\n\xff\xfe\n")
    result = run_cli(
        "recall", "cache", "--layer", "knowledge", "--format", "json", cwd=adopter_dir
    )
    assert "Traceback" not in result.stderr
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    assert payload["coverage"]["enumerated"] == 2
    assert payload["coverage"]["read"] == 1
    assert payload["coverage"]["unreadable"] == 1
    message = payload["diagnostics"][0]["message"]
    assert "knowledge/broken.md" in message and "UTF-8" in message
    assert "changed during acquisition" not in message


def test_oversize_document_reports_progress_observed_before_the_ceiling(
    adopter_dir, run_cli, write_unit
):
    """A ceiling tripped mid-tree reports what the run had already observed."""
    _write_unit(write_unit, "k-cache", "Cache retry budget")
    _write_unit(write_unit, "k-other", "Other budget")
    # Sorts last, so both valid documents are enumerated and read before it.
    (adopter_dir / "knowledge" / "z-oversize.md").write_bytes(b"x" * (1024 * 1024 + 1))
    result = run_cli(
        "recall", "cache", "--layer", "knowledge", "--format", "json", cwd=adopter_dir
    )
    assert "Traceback" not in result.stderr
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    coverage = payload["coverage"]
    assert coverage["enumerated"] == 3
    assert coverage["read"] == 2
    # Decoding never ran for this tree, so decodability was never determined.
    assert coverage["unreadable"] is None
    message = payload["diagnostics"][0]["message"]
    assert "knowledge/z-oversize.md" in message
    assert "1 MiB per-Markdown ceiling" in message


def test_partial_tree_failure_keeps_the_completed_tree_counts(
    adopter_dir, run_cli, write_index, write_memory, write_unit
):
    """Counts an earlier layer established survive a later layer's ceiling."""
    write_index(
        "# Agent memory\n\n- [cache-timeout](cache-timeout.md)\n"
        "- [other-fact](other-fact.md)\n"
    )
    _write_memory_entry(write_memory, "cache-timeout", "cache timeout")
    _write_memory_entry(write_memory, "other-fact", "other fact")
    _write_unit(write_unit, "k-cache", "Cache retry budget")
    (adopter_dir / "knowledge" / "z-oversize.md").write_bytes(b"x" * (1024 * 1024 + 1))
    result = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    assert "Traceback" not in result.stderr
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    coverage = payload["coverage"]
    # Two memory documents plus two enumerated knowledge documents, of which
    # only the oversize one was never read; the memory index is auxiliary.
    assert coverage["enumerated"] == 4
    assert coverage["read"] == 3
    assert coverage["unreadable"] == 0
    assert coverage["auxiliary_read"] == 1
    assert "z-oversize.md" in payload["diagnostics"][0]["message"]


def test_symlinked_schema_ancestor_is_refused_as_a_link(
    adopter_dir, run_cli, basic_corpus, write_document, tmp_path
):
    """`O_NOFOLLOW` guards the last component only, so every one is walked."""
    outside = tmp_path / "outside-schema-dir"
    outside.mkdir()
    (outside / "schema.md").write_text(
        "---\nfields:\n  - name: OUTSIDECONTENT\n    type: string\n---\n",
        encoding="utf-8",
    )
    os.symlink(outside, adopter_dir / "linked")
    write_document(
        "validated-memory.md",
        'extension:\n  schema: linked/schema.md\n  version: "1"\n',
    )
    result = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    message = payload["diagnostics"][0]["message"]
    assert "symlink" in message and "linked" in message
    assert "Not a directory" not in message
    assert "OUTSIDECONTENT" not in result.stdout


def test_configuration_created_during_acquisition_is_detected(
    adopter_dir, run_cli, basic_corpus
):
    """Absence is re-checked: an input that appears mid-run is a failure."""
    (adopter_dir / "validated-memory.md").unlink(missing_ok=True)
    hook = _act_on_second_scandir(
        "import io\n"
        "handle = io.open('validated-memory.md', 'w', encoding='utf-8')\n"
        "handle.write('---\\nextension:\\n  schema: s.md\\n  version: \"1\"\\n---\\n')\n"
        "handle.close()\n"
    )
    result = _run_with_audit_hook(
        adopter_dir, hook, "recall", "cache", "--layer", "memory", "--format", "json"
    )
    assert "Traceback" not in result.stderr
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    message = payload["diagnostics"][0]["message"]
    assert "validated-memory.md" in message and "appeared during acquisition" in message


def test_document_changed_during_acquisition_is_detected(
    adopter_dir, run_cli, basic_corpus
):
    """A read document that changes before re-verification fails the run."""
    hook = _act_on_second_scandir(
        "import io\n"
        "handle = io.open('memory/cache-timeout.md', 'a', encoding='utf-8')\n"
        "handle.write('appended during acquisition\\n')\n"
        "handle.close()\n"
    )
    result = _run_with_audit_hook(
        adopter_dir, hook, "recall", "cache", "--layer", "memory", "--format", "json"
    )
    assert "Traceback" not in result.stderr
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    assert any(
        "cache-timeout.md" in d["message"] and "during acquisition" in d["message"]
        for d in payload["diagnostics"]
    )


def test_map_failure_reports_the_map_mode(adopter_dir, run_cli):
    (adopter_dir / "knowledge").mkdir()
    result = run_cli("recall", "--map", "--layer", "memory", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["mode"] == "map"
    assert payload["complete"] is False


def test_validation_failure_keeps_the_counts_acquisition_reached(
    adopter_dir, run_cli, write_unit
):
    _write_unit(write_unit, "k-cache", "Cache retry budget")
    (adopter_dir / "knowledge" / "broken.md").write_text(
        "---\nid: 4\nevidence: nonsense\n---\n\n# Broken\n", encoding="utf-8"
    )
    result = run_cli(
        "recall", "cache", "--layer", "knowledge", "--format", "json", cwd=adopter_dir
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    coverage = payload["coverage"]
    assert coverage["enumerated"] == 2
    assert coverage["read"] == 2
    assert coverage["unreadable"] == 0
    # One invalid document however many findings it carries.
    assert coverage["invalid"] == 1
    # Totals that were never determined stay null rather than zero.
    assert coverage["eligible"] is None
    assert coverage["matched"] is None


def test_nested_memory_successor_path_comes_from_the_parsed_set(
    adopter_dir, run_cli, write_index, write_memory
):
    write_index("# Agent memory\n\n- [old](sub/old.md)\n- [new](sub/new.md)\n")
    _write_nested_memory(write_memory, "sub", "old", "superseded by [[new]]", "Old widget.\n")
    _write_nested_memory(write_memory, "sub", "new", "widget label", "New widget.\n")
    (adopter_dir / "knowledge").mkdir()
    result = run_cli(
        "recall", "widget", "--layer", "memory", "--include-superseded",
        "--format", "json", cwd=adopter_dir,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    by_identity = {r["identity"]: r for r in payload["results"]}
    assert by_identity["old"]["successors"] == [
        {"identity": "new", "path": "memory/sub/new.md"}
    ]
    assert by_identity["old"]["group"] == "memory/sub"


def test_document_validation_precedes_supersession_cycle_detection(
    adopter_dir, run_cli, write_index, write_memory, write_unit
):
    write_index("# Agent memory\n\n- [a](a.md)\n- [b](b.md)\n")
    _write_memory_entry(write_memory, "a", "superseded by [[b]]")
    _write_memory_entry(write_memory, "b", "superseded by [[a]]")
    _write_unit(write_unit, "k-cache", "Cache retry budget")
    (adopter_dir / "knowledge" / "broken.md").write_text(
        "---\nid: k-broken\n---\n\n# Broken\n", encoding="utf-8"
    )
    result = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    paths = {d["path"] for d in payload["diagnostics"]}
    assert "knowledge/broken.md" in paths
    assert not any("cycle" in d["message"] for d in payload["diagnostics"])


def test_warnings_are_visible_on_the_success_path(adopter_dir, run_cli, write_unit):
    """A unit with no anchors is a warning under validate; recall says so too."""
    (adopter_dir / "memory").mkdir()
    (adopter_dir / "memory" / "MEMORY.md").write_text("# Agent memory\n", encoding="utf-8")
    _write_unit(write_unit, "k-cache", "Cache retry budget", anchors=False)
    result = run_cli("recall", "cache", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["complete"] is True
    assert payload["results"]
    assert [d["severity"] for d in payload["diagnostics"]] == ["WARNING"]
    assert payload["diagnostics"][0]["path"] == "knowledge/k-cache.md"


def test_layer_knowledge_ignores_a_missing_memory_directory(
    adopter_dir, run_cli, write_unit
):
    _write_unit(write_unit, "k-cache", "Cache retry budget")
    assert not (adopter_dir / "memory").exists()
    result = run_cli(
        "recall", "cache", "--layer", "knowledge", "--format", "json", cwd=adopter_dir
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["layers"] == ["knowledge"]
    assert [r["identity"] for r in payload["results"]] == ["k-cache"]


def test_map_orders_two_directories_and_counts_groups(
    adopter_dir, run_cli, write_index, write_memory
):
    write_index("# Agent memory\n\n- [x](b/x.md)\n- [y](a/y.md)\n")
    _write_nested_memory(write_memory, "b", "x", "second directory", "X body.\n")
    _write_nested_memory(write_memory, "a", "y", "first directory", "Y body.\n")
    (adopter_dir / "knowledge").mkdir()
    result = run_cli(
        "recall", "--map", "--layer", "memory", "--format", "json", cwd=adopter_dir
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert [r["path"] for r in payload["results"]] == ["memory/a/y.md", "memory/b/x.md"]
    assert [r["group"] for r in payload["results"]] == ["memory/a", "memory/b"]
    assert [r["group_count"] for r in payload["results"]] == [1, 1]
    assert payload["selection"]["groups_total"] == 2
    assert payload["selection"]["groups_returned"] == 2


def test_text_output_carries_the_same_metadata_as_json(
    adopter_dir, run_cli, write_unit, write_index, write_memory
):
    """Text is a readable rendering of the same information, not a summary."""
    write_index("# Agent memory\n\n- [note](note.md)\n")
    _write_memory_entry(
        write_memory, "note", "timeout note", "The anchored fact is recorded here.\n"
    )
    anchor = (
        "anchors:\n"
        "  - system: redis\n"
        "    kind: config\n"
        '    captured_at: "2026-01-01"\n'
        "    payload:\n"
        "      key: timeout\n"
    )
    write_unit(
        "k-anchored.md",
        "id: k-anchored\nevidence: hypothesis\n" + anchor,
        "# Timeout policy\n\nThe anchored fact records the timeout policy.\n",
    )
    write_unit(
        "k-unknown.md",
        "id: k-unknown\nevidence: measured\n" + anchor.replace("redis", "postgres"),
        "# Unverified policy\n\nThis anchored fact was never probed.\n",
    )
    (adopter_dir / "verdicts.jsonl").write_text(
        json.dumps(
            {
                "unit": "k-anchored", "system": "redis", "kind": "config",
                "payload": {"key": "timeout"}, "verdict": "drifted",
                "recorded_at": "2026-02-03T04:05:06Z",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    text = run_cli("recall", "anchored", "--limit", "5", "--format", "text", cwd=adopter_dir)
    assert text.returncode == 0
    as_json = run_cli("recall", "anchored", "--limit", "5", "--format", "json", cwd=adopter_dir)
    payload = json.loads(as_json.stdout)
    rendered = text.stdout

    assert "- [knowledge] k-anchored (knowledge/k-anchored.md:" in rendered
    assert 'label: "Timeout policy"' in rendered
    assert 'evidence: "hypothesis"' in rendered
    assert "verdict: drifted" in rendered
    assert (
        'anchor: system="redis" kind="config" verdict=drifted '
        'checked_at="2026-02-03T04:05:06Z"' in rendered
    )
    assert (
        'anchor: system="postgres" kind="config" verdict=unknown checked_at=null'
        in rendered
    )
    assert 'memory_type: "reference"' in rendered
    assert "match: exact=false label=[] path=[anchored] body=[anchored]" in rendered
    for record in payload["results"]:
        assert f"sha256: {record['sha256']}" in rendered
        assert f'group: "{record["group"]}" (group_count={record["group_count"]})' in rendered
    assert "guidance: Read the original files" in rendered


def test_text_output_reports_omissions_and_failures_readably(
    adopter_dir, run_cli, write_index, write_memory
):
    entries = "\n".join(f"- [n{i}](n{i}.md)" for i in range(10))
    write_index(f"# Agent memory\n\n{entries}\n")
    for i in range(10):
        _write_memory_entry(write_memory, f"n{i}", "cache entry", "cache body.\n")
    (adopter_dir / "knowledge").mkdir()
    omitted = run_cli(
        "recall", "cache", "--layer", "memory", "--limit", "2", "--format", "text",
        cwd=adopter_dir,
    )
    assert omitted.returncode == 0
    assert "complete=true" in omitted.stdout
    assert "omitted_limit=8" in omitted.stdout
    assert "8 matching record(s) are omitted" in omitted.stdout
    assert "--limit" in omitted.stdout

    (adopter_dir / "memory" / "MEMORY.md").unlink()
    failed = run_cli("recall", "cache", "--layer", "memory", "--format", "text", cwd=adopter_dir)
    assert failed.returncode == 1
    assert "Traceback" not in failed.stderr
    assert "complete=false" in failed.stdout
    assert "no results are returned" in failed.stdout
    assert "ERROR: : acquisition: " in failed.stdout
    assert "enumerated=null" in failed.stdout


def test_missing_descriptor_capability_is_an_explicit_platform_limitation(
    adopter_dir, basic_corpus
):
    """Constants alone do not prove `dir_fd` support, so capability is checked.

    The capability is withdrawn from outside the package, by a `sitecustomize`
    module that empties `os.supports_dir_fd` before dispatch; without the
    check this surfaced as `NotImplementedError` mid-acquisition."""
    hook_dir = adopter_dir.parent / "no-dir-fd"
    hook_dir.mkdir(exist_ok=True)
    (hook_dir / "sitecustomize.py").write_text(
        "import os\nos.supports_dir_fd = frozenset()\n", encoding="utf-8"
    )
    result = _run_env(
        adopter_dir,
        {
            "PYTHONPATH": os.pathsep.join([str(hook_dir), str(REPO_ROOT)]),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        "recall", "cache", "--format", "json",
    )
    assert "Traceback" not in result.stderr
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    message = payload["diagnostics"][0]["message"]
    assert "platform limitation" in message
    assert "directory-relative open" in message


def test_diagnostics_dropped_by_count_are_reported(adopter_dir, run_cli, write_unit):
    """Ten warnings do not fit the eight-diagnostic bound; the rest are counted."""
    (adopter_dir / "memory").mkdir()
    (adopter_dir / "memory" / "MEMORY.md").write_text("# Agent memory\n", encoding="utf-8")
    for index in range(10):
        _write_unit(write_unit, f"k-{index}", f"Unit {index}", anchors=False)
    result = run_cli("recall", "absentword", "--format", "json", cwd=adopter_dir)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["complete"] is True
    assert len(payload["diagnostics"]) == 8
    assert {d["severity"] for d in payload["diagnostics"]} == {"WARNING"}
    assert payload["diagnostics_omitted"] == 2


def test_ceiling_boundaries_are_read_at_most_one_byte_past_the_limit(
    adopter_dir, run_cli, write_index, write_memory
):
    """A document at the ceiling is fine; one byte over fails with no results."""
    write_index("# Agent memory\n\n- [big](big.md)\n")
    _write_memory_entry(write_memory, "big", "cache entry", "cache\n")
    (adopter_dir / "knowledge").mkdir()
    path = adopter_dir / "memory" / "big.md"
    base = path.read_text(encoding="utf-8")
    path.write_text(base + "x" * (1024 * 1024 - len(base.encode("utf-8"))), encoding="utf-8")
    assert path.stat().st_size == 1024 * 1024
    at_ceiling = run_cli(
        "recall", "cache", "--layer", "memory", "--format", "json", cwd=adopter_dir
    )
    assert at_ceiling.returncode == 0

    path.write_text(path.read_text(encoding="utf-8") + "x", encoding="utf-8")
    over_ceiling = run_cli(
        "recall", "cache", "--layer", "memory", "--format", "json", cwd=adopter_dir
    )
    assert over_ceiling.returncode == 1
    payload = json.loads(over_ceiling.stdout)
    assert payload["results"] == []
    message = payload["diagnostics"][0]["message"]
    assert "memory/big.md" in message and "ceiling" in message


def test_index_read_counts_as_an_auxiliary_read(
    adopter_dir, run_cli, write_index, write_memory
):
    """`memory/MEMORY.md` is an auxiliary file and is counted as one."""
    write_index("# Agent memory\n\n- [cache](cache.md)\n")
    _write_memory_entry(write_memory, "cache", "cache entry")
    result = run_cli(
        "recall", "cache", "--layer", "memory", "--format", "json", cwd=adopter_dir
    )
    assert result.returncode == 0
    coverage = json.loads(result.stdout)["coverage"]
    assert coverage["auxiliary_read"] == 1
    assert coverage["enumerated"] == 1 and coverage["read"] == 1


def test_index_read_is_counted_even_when_acquisition_later_fails(
    adopter_dir, run_cli, write_index, write_memory
):
    """Observed counts keep the index read that already happened."""
    write_index("# Agent memory\n\n- [cache](cache.md)\n")
    _write_memory_entry(write_memory, "cache", "cache entry")
    (adopter_dir / "memory" / "broken.md").write_bytes(b"name: broken\n\xff\xfe body\n")
    result = run_cli(
        "recall", "cache", "--layer", "memory", "--format", "json", cwd=adopter_dir
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    assert payload["coverage"]["auxiliary_read"] == 1
    assert payload["coverage"]["unreadable"] == 1


def test_empty_nested_directory_replaced_by_an_identical_one_is_detected(
    adopter_dir, run_cli, write_index, write_memory
):
    """Same path, same (empty) contents, different directory: still a change.

    Acquisition scans `memory` then `memory/empty`; re-verification starts at
    the third scan, so the replacement lands between the two passes. Nothing
    but the directory's own identity distinguishes the two states."""
    write_index("# Agent memory\n\n- [cache](cache.md)\n")
    _write_memory_entry(write_memory, "cache", "cache entry")
    (adopter_dir / "memory" / "empty").mkdir()
    result = _run_with_audit_hook(
        adopter_dir,
        _act_on_nth_scandir(
            3,
            "import os\n"
            f"os.rmdir({str(adopter_dir / 'memory' / 'empty')!r})\n"
            f"os.mkdir({str(adopter_dir / 'memory' / 'empty')!r})\n",
        ),
        "recall", "cache", "--layer", "memory", "--format", "json",
    )
    assert "Traceback" not in result.stderr
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    assert "changed during acquisition" in payload["diagnostics"][0]["message"]


def test_selected_root_replaced_by_an_identical_one_is_detected(
    adopter_dir, run_cli
):
    """The selected tree itself is part of the identity that is re-verified.

    An empty `knowledge` tree has no members and no files to re-stat, so the
    only thing that can detect the swap is the root descriptor's own stamp: the
    replacement happens just before re-verification opens the tree, so it opens
    a different directory with the same path and the same (empty) contents."""
    (adopter_dir / "knowledge").mkdir()
    clean = run_cli(
        "recall", "cache", "--layer", "knowledge", "--format", "json", cwd=adopter_dir
    )
    assert clean.returncode == 0
    result = _run_with_audit_hook(
        adopter_dir,
        _act_on_nth_open(
            "knowledge",
            2,
            "import os\n"
            f"os.rmdir({str(adopter_dir / 'knowledge')!r})\n"
            f"os.mkdir({str(adopter_dir / 'knowledge')!r})\n",
        ),
        "recall", "cache", "--layer", "knowledge", "--format", "json",
    )
    assert "Traceback" not in result.stderr
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert "changed during acquisition" in payload["diagnostics"][0]["message"]


def test_no_file_outside_the_adopter_root_is_opened(adopter_dir, run_cli):
    """A layer root symlinked out of the tree: the outside file is never read.

    The corpus lives in a separate temporary directory that is not under the
    adopter root, and an audit hook outside the package records every path the
    process opens or scans. Because reads are descriptor-relative, the record
    holds the *names* the process opened; the outside member has a name that
    exists nowhere else, so its absence from the record is evidence that it was
    never opened, not merely that its content stayed out of stdout."""
    with tempfile.TemporaryDirectory() as outside:
        outside_dir = Path(outside)
        assert adopter_dir not in outside_dir.parents
        secret_name = "outside-only-6f1c2b.md"
        (outside_dir / "MEMORY.md").write_text(
            f"# Agent memory\n\n- [outside](.{secret_name})\n", encoding="utf-8"
        )
        (outside_dir / secret_name).write_text(
            "name: outside\n"
            'description: "cache outside"\n'
            "metadata:\n  type: reference\n"
            "---\nOUTSIDE-CONTENT-MARKER cache\n",
            encoding="utf-8",
        )
        (adopter_dir / "memory").symlink_to(outside_dir, target_is_directory=True)
        record = adopter_dir.parent / "opened-paths.log"
        result = _run_with_audit_hook(
            adopter_dir,
            _record_opened_paths(str(record)),
            "recall", "cache", "--layer", "memory", "--format", "json",
        )
        assert "Traceback" not in result.stderr
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert payload["complete"] is False
        assert payload["results"] == []
        assert "symlink" in payload["diagnostics"][0]["message"]
        assert "OUTSIDE-CONTENT-MARKER" not in result.stdout
        opened = record.read_text(encoding="utf-8")
        assert secret_name not in opened
        assert str(outside_dir) not in opened


def test_a_standard_library_io_fault_stays_inside_the_envelope(
    adopter_dir, run_cli, write_index, write_memory
):
    """An `OSError` raised by the interpreter itself is sanitized, not raised."""
    write_index("# Agent memory\n\n- [cache](cache.md)\n")
    _write_memory_entry(write_memory, "cache", "cache entry")
    result = _run_with_audit_hook(
        adopter_dir,
        _act_on_nth_scandir(
            2, "raise OSError(5, 'Input/output error')\n"
        ),
        "recall", "cache", "--layer", "memory", "--format", "json",
    )
    assert "Traceback" not in result.stderr
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["results"] == []
    message = payload["diagnostics"][0]["message"]
    assert "cannot acquire" in message and "Input/output error" in message
    assert str(adopter_dir) not in message
    coverage = payload["coverage"]
    assert coverage["auxiliary_read"] == 1
    assert coverage["enumerated"] == 1 and coverage["read"] == 1


def test_directory_depth_ceiling_admits_64_and_refuses_65(
    adopter_dir, run_cli, write_index, write_memory
):
    """Depth 64 is acquired; the 65th level fails with no results."""
    write_index("# Agent memory\n\n- [cache](cache.md)\n")
    _write_memory_entry(write_memory, "cache", "cache entry")
    deepest = adopter_dir / "memory"
    for level in range(64):
        deepest = deepest / f"d{level}"
    deepest.mkdir(parents=True)
    at_ceiling = run_cli(
        "recall", "cache", "--layer", "memory", "--format", "json", cwd=adopter_dir
    )
    assert at_ceiling.returncode == 0
    assert json.loads(at_ceiling.stdout)["results"]

    (deepest / "d64").mkdir()
    over_ceiling = run_cli(
        "recall", "cache", "--layer", "memory", "--format", "json", cwd=adopter_dir
    )
    assert "Traceback" not in over_ceiling.stderr
    assert over_ceiling.returncode == 1
    payload = json.loads(over_ceiling.stdout)
    assert payload["results"] == []
    assert "depth exceeds the 64 ceiling" in payload["diagnostics"][0]["message"]


def test_verdict_log_uses_the_16_mib_ceiling_not_the_markdown_one(
    adopter_dir, run_cli, write_unit
):
    """A 16 MiB + 1 log fails, and the diagnostic names the log's own ceiling."""
    _write_unit(write_unit, "k-cache", "Cache retry budget", "Cache retry budget.")
    log = adopter_dir / "verdicts.jsonl"
    log.write_bytes(b"x" * (16 * 1024 * 1024 + 1))
    result = run_cli(
        "recall", "cache", "--layer", "knowledge", "--format", "json", cwd=adopter_dir
    )
    assert "Traceback" not in result.stderr
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["results"] == []
    message = payload["diagnostics"][0]["message"]
    assert "verdicts.jsonl" in message and "16 MiB verdict-log ceiling" in message
