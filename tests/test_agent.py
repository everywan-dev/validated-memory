"""Black-box tests for the opted-in prompt discovery adapter."""

import json
import hashlib
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_agent(cwd, *args, input_text=None, env=None):
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO_ROOT)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if env:
        environment.update(env)
    return subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "agent", *args],
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )


def _run_agent_bytes(cwd, *args, input_bytes=b"", env=None):
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO_ROOT)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if env:
        environment.update(env)
    return subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "agent", *args],
        cwd=cwd,
        input=input_bytes,
        capture_output=True,
        env=environment,
        check=False,
    )


def _copy_package_shim(tmp_path, source_text):
    package_parent = tmp_path / "shim"
    package_parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        REPO_ROOT / "validated_memory",
        package_parent / "validated_memory",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (package_parent / "validated_memory" / "recall.py").write_text(source_text, encoding="utf-8")
    return package_parent


def _run_agent_with_package(cwd, package_parent, *args, input_text=None):
    return _run_agent(cwd, *args, input_text=input_text, env={"PYTHONPATH": str(package_parent)})


def _event(root, prompt, event_name="UserPromptSubmit"):
    return json.dumps({"hook_event_name": event_name, "cwd": str(root), "prompt": prompt})


def _tree_snapshot(root):
    snapshot = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = os.lstat(path)
        kind = "directory" if stat.S_ISDIR(metadata.st_mode) else "symlink" if stat.S_ISLNK(metadata.st_mode) else "file" if stat.S_ISREG(metadata.st_mode) else "other"
        entry = {"kind": kind, "mode": metadata.st_mode, "size": metadata.st_size}
        if kind == "file":
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif kind == "symlink":
            entry["target"] = os.readlink(path)
        snapshot[relative] = entry
    return snapshot


@pytest.fixture
def prepared_adopter(tmp_path, run_cli):
    result = run_cli("init", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    (tmp_path / "validated-memory-profile.md").write_text(
        "---\nschema_version: 1\ndiscovery: explicit\nreliance: lightweight\n---\n\nRationale.\n",
        encoding="utf-8",
    )
    return tmp_path


def test_profile_wire_missing_and_valid_are_read_only(prepared_adopter, run_cli):
    profile = prepared_adopter / "validated-memory-profile.md"
    profile.unlink()
    missing = run_cli("agent", "profile", cwd=prepared_adopter)
    assert missing.returncode == 0
    assert json.loads(missing.stdout) == {
        "configured": False,
        "discovery": "off",
        "host_support": {
            "delivery_verification": "not_checked",
            "shipped": ["claude-code"],
        },
        "operation": "profile",
        "profile_path": "validated-memory-profile.md",
        "reliance": "lightweight",
        "schema_version": 1,
    }
    profile.write_text(
        "---\nschema_version: 1\ndiscovery: explicit\nreliance: reviewed\n---\n\nKeep this body.\n",
        encoding="utf-8",
    )
    before = _tree_snapshot(prepared_adopter)
    configured = run_cli("agent", "profile", cwd=prepared_adopter)
    after = _tree_snapshot(prepared_adopter)
    assert configured.returncode == 0
    payload = json.loads(configured.stdout)
    assert payload["configured"] is True
    assert payload["discovery"] == "explicit"
    assert payload["reliance"] == "reviewed"
    assert before == after
    assert configured.stderr == ""


@pytest.mark.parametrize("layout", ["empty", "memory-only", "knowledge-only", "ordinary-partial"])
def test_existing_non_adopter_is_silent_and_does_not_read_profile(tmp_path, layout):
    if layout == "memory-only":
        (tmp_path / "memory").mkdir()
    elif layout == "knowledge-only":
        (tmp_path / "knowledge").mkdir()
    elif layout == "ordinary-partial":
        (tmp_path / "validated-memory.md").write_text("user file\n", encoding="utf-8")
        (tmp_path / "memory").mkdir()
        (tmp_path / "validated-memory-profile.md").write_text(
            "this must not be read\n", encoding="utf-8"
        )
    result = _run_agent(
        tmp_path, "hook", "--host", "claude-code",
        input_text=_event(tmp_path, "Validated-memory: sentinel"),
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_malformed_profile_is_exit_one_with_no_partial_json(prepared_adopter, run_cli):
    (prepared_adopter / "validated-memory-profile.md").write_text(
        "---\nschema_version: 1\ndiscovery: automatic\ndiscovery: explicit\nreliance: lightweight\n---\n",
        encoding="utf-8",
    )
    before = _tree_snapshot(prepared_adopter)
    result = run_cli("agent", "profile", cwd=prepared_adopter)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("agent profile: profile is unavailable:")
    assert "Traceback" not in result.stderr
    assert _tree_snapshot(prepared_adopter) == before


def test_explicit_prefix_emits_candidates_and_ordinary_prompt_is_noop(prepared_adopter):
    memory = prepared_adopter / "memory" / "cache.md"
    memory.write_text(
        '---\nname: cache\ndescription: "cache timeout"\nmetadata:\n  type: reference\n---\n\nCache timeout sentinel.\n',
        encoding="utf-8",
    )
    (prepared_adopter / "memory" / "MEMORY.md").write_text(
        "# Agent memory\n\n- [cache](cache.md)\n", encoding="utf-8"
    )
    before = _tree_snapshot(prepared_adopter)
    ordinary = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "What did we learn about cache timeout?"),
    )
    assert ordinary.returncode == 0
    assert ordinary.stdout == ""
    explicit = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "  Validated-Memory: cache timeout"),
    )
    assert explicit.returncode == 0
    outer = json.loads(explicit.stdout)
    context = json.loads(outer["hookSpecificOutput"]["additionalContext"])
    assert context["status"] == "matched"
    assert context["lookup"]["matched"] == 1
    assert context["candidates"][0]["identity"] == "cache"
    assert context["candidates"][0]["title"] == "cache timeout"
    assert "Validated-Memory:" not in outer["hookSpecificOutput"]["additionalContext"]
    assert len(explicit.stdout.encode()) <= 8192
    assert _tree_snapshot(prepared_adopter) == before
    exact = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "Validated-memory: memory/cache.md"),
    )
    exact_context = json.loads(json.loads(exact.stdout)["hookSpecificOutput"]["additionalContext"])
    assert exact_context["candidates"][0]["match"]["exact"] is True


def test_automatic_and_spanish_prefixes_activate_once(prepared_adopter):
    (prepared_adopter / "validated-memory-profile.md").write_text(
        "---\nschema_version: 1\ndiscovery: automatic\nreliance: lightweight\n---\n",
        encoding="utf-8",
    )
    ordinary = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "retry timeout"),
    )
    assert ordinary.returncode == 0
    ordinary_context = json.loads(json.loads(ordinary.stdout)["hookSpecificOutput"]["additionalContext"])
    assert ordinary_context["status"] == "no_match"
    spanish = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "  BUSCA EN VA: retry timeout"),
    )
    assert spanish.returncode == 0
    spanish_context = json.loads(json.loads(spanish.stdout)["hookSpecificOutput"]["additionalContext"])
    assert spanish_context["status"] == "no_match"


def test_explicit_quoted_negated_and_incidental_mentions_do_not_activate(prepared_adopter):
    for prompt in (
        '"Validated-memory: sentinel"',
        "Do not use Validated-memory: sentinel",
        "The code string Validated-memory: sentinel is incidental",
    ):
        result = _run_agent(
            prepared_adopter, "hook", "--host", "claude-code",
            input_text=_event(prepared_adopter, prompt),
        )
        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""


def test_oversized_query_is_unavailable_and_never_truncated(prepared_adopter, tmp_path):
    marker = tmp_path / "child-ran"
    package = _copy_package_shim(
        tmp_path / "oversized-query",
        f"""from pathlib import Path\ndef run(*args, **kwargs):\n    Path({str(marker)!r}).write_text('ran')\n    raise RuntimeError('child must not run')\n""",
    )
    before = _tree_snapshot(prepared_adopter)
    prompt = "Validated-memory: " + ("x" * 4097)
    result = _run_agent_with_package(
        prepared_adopter, package, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, prompt),
    )
    assert result.returncode == 0
    context = json.loads(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
    assert context["status"] == "unavailable"
    assert "x" * 100 not in result.stdout
    assert not marker.exists()
    assert _tree_snapshot(prepared_adopter) == before


def test_invalid_unicode_query_has_distinct_guidance(prepared_adopter):
    event = json.dumps(
        {"hook_event_name": "UserPromptSubmit", "cwd": str(prepared_adopter),
         "prompt": "Validated-memory: \ud800"}
    )
    result = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code", input_text=event
    )
    assert result.returncode == 0
    context = json.loads(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
    assert context["status"] == "unavailable"
    assert "invalid Unicode" in context["diagnostic"]


def test_output_budget_omits_whole_candidates_and_keeps_counts(prepared_adopter):
    links = []
    for index in range(5):
        name = f"sentinel-{index}-" + ("x" * 60)
        relative = Path("nested")
        for component in range(10):
            relative /= (f"part-{component}-" + ("x" * 90))
        relative /= f"{name}.md"
        (prepared_adopter / "memory" / relative).parent.mkdir(parents=True, exist_ok=True)
        links.append(f"- [{name}]({relative.as_posix()})")
        (prepared_adopter / "memory" / relative).write_text(
            "---\n"
            f"name: {name}\n"
            f"description: \"sentinel {index} " + ("label " * 25) + "\"\n"
            "metadata:\n  type: reference\n---\n\n"
            + ("sentinel evidence " * 45)
            + "\n",
            encoding="utf-8",
        )
    (prepared_adopter / "memory" / "MEMORY.md").write_text(
        "# Agent memory\n\n" + "\n".join(links) + "\n", encoding="utf-8"
    )
    result = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "Validated-memory: sentinel"),
    )
    assert result.returncode == 0
    assert len(result.stdout.encode("utf-8")) <= 8192
    context = json.loads(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
    lookup = context["lookup"]
    assert lookup["matched"] == lookup["returned"] + lookup["omitted_limit"] + lookup["omitted_budget"]
    assert lookup["returned"] == len(context["candidates"])
    if lookup["omitted_budget"]:
        assert lookup["omitted_budget"] > 0


def test_malformed_and_oversized_child_envelopes_are_unavailable(prepared_adopter, tmp_path):
    malformed = _copy_package_shim(
        tmp_path,
        """import json\ndef run(*args, stdout, **kwargs):\n    stdout.write(json.dumps({'schema_version': 1, 'mode': 'query', 'complete': True, 'coverage': {'matched': 1}, 'selection': {'returned': 1, 'omitted_limit': 0, 'omitted_budget': 0}, 'results': [{'identity': 7}]}))\n    return 0\n""",
    )
    result = _run_agent_with_package(
        prepared_adopter, malformed, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "Validated-memory: sentinel"),
    )
    context = json.loads(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
    assert context["status"] == "unavailable"
    assert context["candidates"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        "'path': 'memory/../../outside'",
        "'path': 'memory/\\\\outside'",
        "'redirected_from': [{'identity': '', 'path': None}]",
        "'redirected_from': [{'identity': 'old', 'path': None}]",
        "'redirected_from': [{'identity': 'old', 'path': 'knowledge/old.md'}]",
        "'state': 'superseded'",
        "'evidence': 'hypothesis'",
    ],
)
def test_hostile_child_locators_and_memory_evidence_are_rejected(prepared_adopter, tmp_path, mutation):
    record = (
        "{'layer': 'memory', 'identity': 'safe', 'path': 'memory/safe.md', "
        "'label': 'safe', 'excerpt': 'safe', 'state': 'active', "
        "'match': {'exact': False, 'label': ['safe'], 'path': [], 'body': []}, "
        "'successors': [], 'redirected_from': []}"
    )
    record = record[:-1] + ", " + mutation + "}"
    shim = _copy_package_shim(
        tmp_path,
        """import json\ndef run(*args, stdout, **kwargs):\n    record = RECORD\n    payload = {'schema_version': 1, 'mode': 'query', 'complete': True, 'coverage': {'matched': 1}, 'selection': {'returned': 1, 'omitted_limit': 0, 'omitted_budget': 0}, 'results': [record]}\n    stdout.write(json.dumps(payload))\n    return 0\nRECORD = %s\n""" % record,
    )
    result = _run_agent_with_package(
        prepared_adopter, shim, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "Validated-memory: safe"),
    )
    context = json.loads(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
    assert context["status"] == "unavailable"
    assert context["candidates"] == []
    assert "outside" not in result.stdout
    oversized = _copy_package_shim(
        tmp_path / "oversized",
        """import sys\ndef run(*args, stdout, **kwargs):\n    stdout.write('x' * 100000)\n    return 0\n""",
    )
    result = _run_agent_with_package(
        prepared_adopter, oversized, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "Validated-memory: sentinel"),
    )
    context = json.loads(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
    assert context["status"] == "unavailable"
    assert context["candidates"] == []


def test_child_timeout_is_unavailable(prepared_adopter, tmp_path):
    shim = _copy_package_shim(
        tmp_path,
        """import time\ndef run(*args, stdout, **kwargs):\n    time.sleep(4)\n    return 0\n""",
    )
    result = _run_agent_with_package(
        prepared_adopter, shim, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "Validated-memory: sentinel"),
    )
    context = json.loads(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
    assert context["status"] == "unavailable"
    assert context["lookup"]["elapsed_ms"] >= 2900


def test_nofollow_marker_and_wrapper_launch_failure_are_fail_open(prepared_adopter, tmp_path):
    outside = tmp_path / "outside-memory"
    outside.mkdir()
    (outside / "sentinel.md").write_text("must not be read\n", encoding="utf-8")
    memory = prepared_adopter / "memory"
    memory.rename(prepared_adopter / "memory-real")
    memory.symlink_to(outside, target_is_directory=True)
    result = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "Validated-memory: sentinel"),
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert "symlink" in result.stderr
    wrapper = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "hooks" / "prompt-discovery.sh")],
        input="{}\n", text=True, capture_output=True,
        env={"PATH": ""}, cwd=prepared_adopter, check=False,
    )
    assert wrapper.returncode == 0
    assert "python3 not found" in wrapper.stderr


def test_profile_presence_does_not_change_existing_read_commands(prepared_adopter, run_cli):
    profile = prepared_adopter / "validated-memory-profile.md"
    profile.unlink()
    recall_without = run_cli("recall", "sentinel", "--format", "json", cwd=prepared_adopter)
    validate_without = run_cli("validate", cwd=prepared_adopter)
    profile.write_text(
        "---\nschema_version: 1\ndiscovery: automatic\nreliance: reviewed\n---\n",
        encoding="utf-8",
    )
    recall_with = run_cli("recall", "sentinel", "--format", "json", cwd=prepared_adopter)
    validate_with = run_cli("validate", cwd=prepared_adopter)
    assert (recall_with.returncode, recall_with.stdout, recall_with.stderr) == (
        recall_without.returncode, recall_without.stdout, recall_without.stderr
    )
    assert (validate_with.returncode, validate_with.stdout, validate_with.stderr) == (
        validate_without.returncode, validate_without.stdout, validate_without.stderr
    )


def test_profile_presence_does_not_change_checked_use(prepared_adopter, run_cli):
    profile = prepared_adopter / "validated-memory-profile.md"
    profile.unlink()
    (prepared_adopter / "sources").mkdir()
    (prepared_adopter / "sources" / "evidence.txt").write_text("support\n", encoding="utf-8")
    (prepared_adopter / "knowledge" / "kb-check.md").write_text(
        "---\nid: kb-check\nevidence: verifiable\n---\n\n# Check claim\n\nClaim.\n",
        encoding="utf-8",
    )
    store = prepared_adopter.parent / "consultation-check.sqlite"

    def command(*args):
        result = run_cli("consultation", "--store", str(store), *args, cwd=prepared_adopter)
        assert result.returncode == 0, (result.stdout, result.stderr)
        return result

    command("register", "demo", str(prepared_adopter), "--source", "demo")
    command(
        "bind", "demo:kb-check", "--support", "sources/evidence.txt",
        "--authority", "demo", "--scope", "exercise=parcel",
        "--actor", "fixture", "--reason", "Inspect the fixture evidence.",
    )
    read = command("read", "demo:kb-check", "--scope", "exercise=parcel")
    receipt = json.loads(read.stdout.splitlines()[-1])["id"]
    use = command("record-use", "demo:kb-check", "--receipt", receipt)
    use_id = json.loads(use.stdout)["id"]
    baseline = command("check-use", use_id).stdout
    profile.write_text(
        "---\nschema_version: 1\ndiscovery: automatic\nreliance: reviewed\n---\n",
        encoding="utf-8",
    )
    assert command("check-use", use_id).stdout == baseline


def test_activation_guards_do_not_lookup_a_corrupt_corpus(prepared_adopter):
    (prepared_adopter / "validated-memory.md").write_text("broken\n", encoding="utf-8")
    cases = [
        ("explicit", "ordinary prompt", "silent"),
        ("explicit", "Validated-memory: continue", "no_query"),
        ("off", "Validated-memory: sentinel", "silent"),
        (None, "Validated-memory: sentinel", "silent"),
    ]
    profile = prepared_adopter / "validated-memory-profile.md"
    for discovery, prompt, expected in cases:
        if discovery is None:
            profile.unlink(missing_ok=True)
        else:
            profile.write_text(
                f"---\nschema_version: 1\ndiscovery: {discovery}\nreliance: lightweight\n---\n",
                encoding="utf-8",
            )
        before = _tree_snapshot(prepared_adopter)
        result = _run_agent(
            prepared_adopter, "hook", "--host", "claude-code",
            input_text=_event(prepared_adopter, prompt),
        )
        assert result.returncode == 0
        if expected == "silent":
            assert result.stdout == ""
            assert result.stderr == ""
        else:
            context = json.loads(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
            assert context["status"] == expected
        assert _tree_snapshot(prepared_adopter) == before


def test_candidates_preserve_redirects_evidence_and_state(prepared_adopter):
    old = prepared_adopter / "memory" / "old.md"
    new = prepared_adopter / "memory" / "new.md"
    old.write_text(
        '---\nname: old\ndescription: "superseded by [[new]]"\nmetadata:\n  type: reference\n---\n\nOld sentinel.\n',
        encoding="utf-8",
    )
    new.write_text(
        '---\nname: new\ndescription: "retry sentinel"\nmetadata:\n  type: reference\n---\n\nNew sentinel evidence.\n',
        encoding="utf-8",
    )
    (prepared_adopter / "memory" / "MEMORY.md").write_text(
        "# Agent memory\n\n- [old](old.md)\n- [new](new.md)\n", encoding="utf-8"
    )
    (prepared_adopter / "knowledge" / "kb-1.md").write_text(
        "---\nid: kb-1\nevidence: hypothesis\nanchors:\n"
        "  - system: test\n    kind: source\n    captured_at: \"2026-01-01\"\n    payload: {}\n---\n\n# Sentinel knowledge\n\nKnowledge sentinel.\n",
        encoding="utf-8",
    )
    result = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "Validated-memory: sentinel"),
    )
    context = json.loads(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
    assert context["status"] == "matched"
    by_layer = {candidate["layer"]: candidate for candidate in context["candidates"]}
    assert by_layer["memory"]["state"] == "active"
    assert by_layer["memory"]["redirected_from"][0]["identity"] == "old"
    assert by_layer["knowledge"]["evidence"] == "hypothesis"


def test_long_unicode_root_has_bounded_unavailable_fallback(tmp_path, run_cli):
    long_root = tmp_path
    for _index in range(16):
        long_root = long_root / ("é" * 100)
        long_root.mkdir()
    result = run_cli("init", cwd=long_root)
    assert result.returncode == 0, result.stderr
    (long_root / "validated-memory-profile.md").write_text(
        "---\nschema_version: 1\ndiscovery: explicit\nreliance: lightweight\n---\n",
        encoding="utf-8",
    )
    hook = _run_agent(
        long_root, "hook", "--host", "claude-code",
        input_text=_event(long_root, "Validated-memory: sentinel"),
    )
    assert hook.returncode == 0
    assert len(hook.stdout.encode("utf-8")) <= 8192
    context = json.loads(json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"])
    assert context["status"] == "unavailable"


def test_no_query_and_wrong_event_fail_open_without_lookup(prepared_adopter):
    no_query = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "Validated-memory: continue"),
    )
    assert no_query.returncode == 0
    context = json.loads(json.loads(no_query.stdout)["hookSpecificOutput"]["additionalContext"])
    assert context["status"] == "no_query"
    wrong_event = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "Validated-memory: cache", "OtherEvent"),
    )
    assert wrong_event.returncode == 0
    assert wrong_event.stdout == ""
    assert "UserPromptSubmit" in wrong_event.stderr


def test_reviewed_reliance_requests_scoped_inspection_without_gating(prepared_adopter):
    profile = prepared_adopter / "validated-memory-profile.md"
    profile.write_text(
        "---\nschema_version: 1\ndiscovery: explicit\nreliance: reviewed\n---\n",
        encoding="utf-8",
    )
    result = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "Validated-memory: cache"),
    )
    assert result.returncode == 0
    context = json.loads(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
    assert "scoped support and applicability" in context["guidance"]
    assert "not approval" in context["guidance"]


def test_corrupt_corpus_is_unavailable_and_does_not_write(prepared_adopter):
    (prepared_adopter / "validated-memory.md").write_text("unknown: value\n", encoding="utf-8")
    before = _tree_snapshot(prepared_adopter)
    result = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "Validated-memory: cache"),
    )
    after = _tree_snapshot(prepared_adopter)
    assert result.returncode == 0
    context = json.loads(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
    assert context["status"] == "unavailable"
    assert before == after


def test_invalid_inputs_and_unavailable_cwd_fail_open_without_disclosure(prepared_adopter, tmp_path):
    secret = "PROFILE-BODY-SECRET"
    profile = prepared_adopter / "validated-memory-profile.md"
    profile.write_text(
        "---\nschema_version: 1\ndiscovery: automatic\nreliance: lightweight\n---\n\n"
        + secret + "\n",
        encoding="utf-8",
    )
    marker = tmp_path / "invalid-input-child-ran"
    package = _copy_package_shim(
        tmp_path / "invalid-input",
        f"""from pathlib import Path\ndef run(*args, **kwargs):\n    Path({str(marker)!r}).write_text('ran')\n    raise RuntimeError('child must not run')\n""",
    )
    before = _tree_snapshot(prepared_adopter)
    cases = [
        (b"not-json", "one UTF-8 JSON object"),
        (b'{"hook_event_name":"UserPromptSubmit","cwd":"/tmp","prompt":"\xff"}', "one UTF-8 JSON object"),
        (json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": "relative", "prompt": "RAW-PROMPT-SECRET"}).encode(), "hook cwd must be an absolute path"),
        (json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(prepared_adopter / "missing"), "prompt": "RAW-PROMPT-SECRET"}).encode(), "hook cwd is not a directory"),
    ]
    for raw, diagnostic in cases:
        result = _run_agent_bytes(
            prepared_adopter, "hook", "--host", "claude-code",
            input_bytes=raw, env={"PYTHONPATH": str(package)},
        )
        assert result.returncode == 0
        assert result.stdout == b""
        assert diagnostic in result.stderr.decode("utf-8")
        assert len(result.stderr) <= 300
        output = (result.stdout + result.stderr).decode("utf-8")
        assert secret not in output
        assert "RAW-PROMPT-SECRET" not in output
        assert not marker.exists()
        assert _tree_snapshot(prepared_adopter) == before


def test_exact_stopword_query_does_not_lookup_or_disclose_profile(prepared_adopter):
    secret = "PROFILE-BODY-SECRET"
    (prepared_adopter / "validated-memory-profile.md").write_text(
        "---\nschema_version: 1\ndiscovery: automatic\nreliance: lightweight\n---\n\n"
        + secret + "\n",
        encoding="utf-8",
    )
    (prepared_adopter / "validated-memory.md").write_text("broken\n", encoding="utf-8")
    before = _tree_snapshot(prepared_adopter)
    result = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "continue"),
    )
    context = json.loads(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
    assert context["status"] == "no_query"
    assert secret not in result.stdout + result.stderr
    assert _tree_snapshot(prepared_adopter) == before


def test_child_count_mismatch_is_unavailable_without_forwarding(prepared_adopter, tmp_path):
    shim = _copy_package_shim(
        tmp_path,
        """import json\ndef run(*args, stdout, **kwargs):\n    record = {'layer': 'memory', 'identity': 'raw-child-secret', 'path': 'memory/raw-child-secret.md', 'label': 'raw-child-secret', 'excerpt': 'raw-child-secret', 'state': 'active', 'match': {'exact': False, 'label': [], 'path': [], 'body': []}, 'successors': [], 'redirected_from': []}\n    payload = {'schema_version': 1, 'mode': 'query', 'complete': True, 'coverage': {'matched': 2}, 'selection': {'returned': 1, 'omitted_limit': 0, 'omitted_budget': 0}, 'results': [record]}\n    stdout.write(json.dumps(payload))\n    return 0\n""",
    )
    before = _tree_snapshot(prepared_adopter)
    result = _run_agent_with_package(
        prepared_adopter, shim, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, "Validated-memory: safe"),
    )
    context = json.loads(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])
    assert context["status"] == "unavailable"
    assert context["candidates"] == []
    assert "raw-child-secret" not in result.stdout + result.stderr
    assert _tree_snapshot(prepared_adopter) == before


def test_hook_rejects_oversized_input_and_profile_symlink(prepared_adopter):
    before_oversized = _tree_snapshot(prepared_adopter)
    oversized = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code",
        input_text="x" * 65537,
    )
    assert oversized.returncode == 0
    assert oversized.stdout == ""
    assert "65,536" in oversized.stderr
    assert _tree_snapshot(prepared_adopter) == before_oversized
    target = prepared_adopter / "real-profile.md"
    target.write_text(
        "---\nschema_version: 1\ndiscovery: automatic\nreliance: lightweight\n---\n",
        encoding="utf-8",
    )
    profile = prepared_adopter / "validated-memory-profile.md"
    profile.unlink()
    profile.symlink_to(target.name)
    before_symlink = _tree_snapshot(prepared_adopter)
    result = _run_agent(prepared_adopter, "profile")
    assert result.returncode == 1
    assert result.stdout == ""
    assert _tree_snapshot(prepared_adopter) == before_symlink


def test_child_package_isolation_and_shell_metacharacters(prepared_adopter):
    shadow = prepared_adopter / "validated_memory"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("raise RuntimeError('shadowed')\n", encoding="utf-8")
    marker = prepared_adopter / "created-by-prompt"
    prompt = "Validated-memory: ; touch created-by-prompt $(touch created-by-prompt)"
    result = _run_agent(
        prepared_adopter, "hook", "--host", "claude-code",
        input_text=_event(prepared_adopter, prompt),
    )
    assert result.returncode == 0
    assert not marker.exists()
    assert "shadowed" not in result.stdout + result.stderr
