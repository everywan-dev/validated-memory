"""Black-box checks for the synthetic benchmark runner."""
import json
import subprocess
import sys
from pathlib import Path

import pytest


HERE = Path(__file__).parent
RUNNER = HERE / "runner.py"
MANIFEST = HERE / "manifest.json"


def run_runner(*args):
    return subprocess.run([sys.executable, str(RUNNER), *map(str, args)], capture_output=True, text=True, check=False)


def test_manifest_shape_and_validation():
    result = run_runner("--validate-only")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"development": 12, "held-out": 6, "valid": True}


def test_held_out_labels_exercise_pressure_language_and_suffix_without_execution():
    families = json.loads(MANIFEST.read_text(encoding="utf-8"))["families"]
    held_out = [family for family in families if family["split"] == "held-out"]

    assert len(held_out) == 6
    assert all(len(family["files"]) >= 6 for family in held_out)
    assert any(
        variant["label"] == "spanish-accent"
        for family in held_out
        for variant in family["variants"]
    )
    assert any(
        variant["label"] == "bilingual"
        for family in held_out
        for variant in family["variants"]
    )
    assert sum(
        variant["label"] == "instruction-suffix"
        for family in held_out
        for variant in family["variants"]
    ) >= 2


@pytest.mark.parametrize("mutation,detail", [
    (lambda f: f.update(disposition="mystery"), "invalid disposition"),
    (lambda f: f["files"].append(dict(f["files"][0])), "duplicate fixture path"),
    (lambda f: f["passages"].append({"path": f["expected_original_reads"][0], "text": "invented passage"}), "required passage"),
    (lambda f: f["relevance"].pop(next(iter(f["relevance"]))), "relevance disposition"),
])
def test_manifest_rejects_invalid_labels_paths_and_passages(tmp_path, mutation, detail):
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mutation(data["families"][0])
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = run_runner("--validate-only", "--manifest", path)
    assert result.returncode == 1
    assert detail in json.loads(result.stderr)["error"]


def test_manifest_rejects_a_non_object_top_level(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("[]", encoding="utf-8")

    result = run_runner("--validate-only", "--manifest", path)

    assert result.returncode == 1
    assert json.loads(result.stderr)["error"] == "manifest top level must be an object"


def test_manifest_rejects_a_passage_moved_to_the_wrong_required_original(tmp_path):
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    family = next(f for f in data["families"] if len(f["expected_original_reads"]) == 2)
    passage = family["passages"][0]
    passage["path"] = next(
        path for path in family["expected_original_reads"] if path != passage["path"]
    )
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = run_runner("--validate-only", "--manifest", path)

    assert result.returncode == 1
    assert "required passage must bind exact text" in json.loads(result.stderr)["error"]


def test_development_smoke_preserves_fixtures_and_scores_dispositions(tmp_path):
    output = tmp_path / "result.json"
    result = run_runner("--split", "development", "--output", output, "--package-root", HERE.parents[1])
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload["families"]) == 12
    assert all(item["fixture_unchanged"] for item in payload["families"])
    assert all(route["integrity"] == "pass" for family in payload["families"] for variant in family["variants"] for route in (variant["raw"], variant["hook"]))
    absent = next(item for item in payload["families"] if item["id"] == "dev-missing-original")
    assert absent["disposition"] == "source-absent"
    assert absent["variants"][0]["raw"]["coverage"] is None
    malformed = next(item for item in payload["families"] if item["id"] == "dev-malformed")
    assert malformed["disposition"] == "invalid-source"
    assert malformed["variants"][0]["raw"]["status"] == "unavailable"
    pressure = next(item for item in payload["families"] if item["id"] == "dev-instruction-suffix")
    assert [v["label"] for v in pressure["variants"]] == ["unsuffixed", "instruction-suffix"]
    assert all(v["raw"]["omissions"]["limit"] + v["raw"]["omissions"]["budget"] > 0 for v in pressure["variants"])
    caveat = next(item for item in payload["families"] if item["id"] == "dev-caveat")
    assert "Full body caveat" not in caveat["variants"][0]["raw"]["execution"]["stdout"].split('"excerpt":', 1)[-1].split('"', 2)[1]
    assert "Full body caveat: green applies only after a dry-run." in next(f["content"] for f in json.loads(MANIFEST.read_text())["families"] if f["id"] == "dev-caveat" for f in f["files"])


def _fake_package(tmp_path, raw_expression, hook_context=None):
    package = tmp_path / "validated_memory"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    if hook_context is None:
        hook_context = {"status":"matched","lookup":{"matched":0,"returned":0,"omitted_limit":0,"omitted_budget":0},"candidates":[]}
    source = f'''import json, pathlib, sys
root=pathlib.Path.cwd()
if sys.argv[1:] == ["init"]:
 root.joinpath("memory").mkdir(); root.joinpath("knowledge").mkdir()
 root.joinpath("validated-memory.md").write_text("config")
 root.joinpath("knowledge-extension.md").write_text("schema")
 root.joinpath("memory/MEMORY.md").write_text("# Agent memory\\n")
 raise SystemExit(0)
if sys.argv[1] == "recall":
 value={raw_expression}
 print(value if isinstance(value,str) else json.dumps(value)); raise SystemExit(0)
context={hook_context!r}
print(json.dumps({{"hookSpecificOutput":{{"hookEventName":"UserPromptSubmit","additionalContext":json.dumps(context)}}}}))
'''
    (package / "__main__.py").write_text(source, encoding="utf-8")
    return tmp_path


GOOD_RAW = "{'schema_version':1,'mode':'query','complete':True,'coverage':{'matched':0},'selection':{'returned':0,'omitted_limit':0,'omitted_budget':0},'results':[]}"


@pytest.mark.parametrize("raw_expression,kind", [
    ("''", "malformed-raw"),
    ("'{'", "malformed-raw"),
    ("[]", "malformed-raw"),
    ("{'schema_version':1,'mode':'query','complete':True,'coverage':{'matched':1},'selection':{'returned':1,'omitted_limit':0,'omitted_budget':0},'results':[{'identity':'x'}]}", "malformed-raw"),
    ("{'schema_version':1,'mode':'query','complete':True,'coverage':{'matched':2},'selection':{'returned':1,'omitted_limit':0,'omitted_budget':0},'results':[{'identity':'x','path':'memory/x.md'}]}", "malformed-raw"),
    ("{'schema_version':1,'mode':'query','complete':True,'coverage':{'matched':2},'selection':{'returned':2,'omitted_limit':0,'omitted_budget':0},'results':[{'identity':'x','path':'memory/x.md'},{'identity':'y','path':'memory/x.md'}]}", "malformed-raw"),
])
def test_fake_cli_raw_malformed_output_is_structured_failure(tmp_path, raw_expression, kind):
    package_root = _fake_package(tmp_path / "package", raw_expression)
    result = run_runner("--split", "development", "--package-root", package_root)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    failure = payload["families"][0]["variants"][0]["raw"]
    assert failure["integrity"] == "failure"
    assert failure["failure"]["kind"] == kind
    assert failure["coverage"] is None


@pytest.mark.parametrize("context", [
    [],
    {},
    {"status":"matched","lookup":{"matched":1,"returned":1,"omitted_limit":0,"omitted_budget":0},"candidates":[{"identity":"x"}]},
    {"status":"matched","lookup":{"matched":2,"returned":1,"omitted_limit":0,"omitted_budget":0},"candidates":[{"identity":"x","path":"memory/x.md"}]},
    {"status":"matched","lookup":{"matched":2,"returned":2,"omitted_limit":0,"omitted_budget":0},"candidates":[{"identity":"x","path":"memory/x.md"},{"identity":"y","path":"memory/x.md"}]},
])
def test_fake_cli_hook_malformed_output_is_structured_failure(tmp_path, context):
    package_root = _fake_package(tmp_path / "package", GOOD_RAW, context)
    result = run_runner("--split", "development", "--package-root", package_root)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    failure = payload["families"][0]["variants"][0]["hook"]
    assert failure["integrity"] == "failure"
    assert failure["failure"]["kind"] == "malformed-hook"
    assert failure["coverage"] is None


def test_runner_surfaces_subprocess_setup_failure(tmp_path):
    result = run_runner("--split", "development", "--package-root", tmp_path)
    assert result.returncode == 1
    assert '"error": "init failed"' in result.stdout
