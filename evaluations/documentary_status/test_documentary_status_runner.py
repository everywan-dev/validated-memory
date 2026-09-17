"""Black-box checks for the P3 documentary-status fixture runner."""
import json
import subprocess
import sys
from pathlib import Path

import pytest


HERE = Path(__file__).parent
RUNNER = HERE / "runner.py"
MANIFEST = HERE / "manifest.json"
DEVELOPMENT_IDS = {"D1-development", "D2-development", "D3-development", "R1-regression", "R2-regression"}


def run_runner(*args):
    return subprocess.run([sys.executable, str(RUNNER), *map(str, args)], capture_output=True, text=True, check=False)


def manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def variant(data, variant_id):
    return next(v for f in data["families"] for v in f["variants"] if v["id"] == variant_id)


def route(item, route_id):
    return next(r for r in item["routes"] if r["id"] == route_id)


def source(item, role):
    return next(s for s in item["sources"] if s["role"] == role)


def test_manifest_shape_and_validation():
    result = run_runner("--validate-only")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"development": 3, "families": 5, "held-out": 3, "regression": 2, "valid": True}


def _held_out_variants(data):
    return [v for f in data["families"] for v in f["variants"] if v["split"] == "held-out"]


def test_held_out_labels_are_inspectable_and_differ_from_development():
    data = manifest()
    held_out = _held_out_variants(data)
    assert [v["id"] for v in held_out] == ["D1-held-out", "D2-held-out", "D3-held-out"]
    for item in held_out:
        development = variant(data, item["id"].replace("held-out", "development"))
        assert item["domain"] != development["domain"]
        assert {f["path"] for f in item["files"]} & {f["path"] for f in development["files"]} == {"memory/MEMORY.md"}


def _mutate(tmp_path, mutation):
    data = manifest()
    mutation(data)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _drop_code(data, variant_id, code):
    for question in variant(data, variant_id)["questions"]:
        question["harmful"] = [o for o in question["harmful"] if o["code"] != code]


MUTATIONS = [
    (lambda d: d["families"][1].update(id="D1"), "family ids"),
    (lambda d: d["families"].pop(), "family ids"),
    (lambda d: variant(d, "D1-held-out").update(split="development"), "split membership"),
    (lambda d: variant(d, "R1-regression").update(split="held-out"), "split membership"),
    (lambda d: variant(d, "D2-held-out").update(id="D2-development"), "variant ids"),
    (lambda d: variant(d, "D1-development")["files"].append(dict(variant(d, "D1-development")["files"][1])), "duplicate fixture path"),
    (lambda d: variant(d, "D1-development")["files"][1].update(path="../escape.md"), "unsafe fixture path"),
    (lambda d: source(variant(d, "D1-development"), "status-unit")["passages"].append("invented passage"), "passage must bind exact text"),
    (lambda d: source(variant(d, "D1-development"), "status-unit").update(role="status-pointer"), "role status-unit"),
    (lambda d: source(variant(d, "D3-development"), "unavailable-status-source").update(availability="present"), "unavailable-status-source"),
    (lambda d: variant(d, "D3-development")["files"].append({"path": "docs/status/oven-preheat-fault.md", "content": "Closed.\n"}), "absent source"),
    (lambda d: variant(d, "D1-development")["sources"].pop(1), "every fixture file needs one source declaration"),
    (lambda d: _drop_code(d, "D1-development", "narrower-link-closed"), "harmful outcome codes must include"),
    (lambda d: _drop_code(d, "D3-held-out", "newest-document-authority"), "harmful outcome codes must include"),
    (lambda d: variant(d, "D2-development")["questions"][0].update(correct=[]), "nonempty correct outcome"),
    (lambda d: variant(d, "D1-development")["routes"].pop(0), "route types must include"),
    (lambda d: variant(d, "D1-development")["routes"].pop(), "validate route"),
    (lambda d: route(variant(d, "D1-development"), "recall-adjacent").update(type="probe"), "unknown type"),
    (lambda d: route(variant(d, "D1-development"), "recall-adjacent")["expect"]["returned_includes"][0].update(path="docs/STATUS.md"), "recall-layer fixtures"),
    (lambda d: route(variant(d, "R2-regression"), "native-index")["expect"]["lines"][0].update(line="- [Gallery lighting advice](gallery-lighting-advice.md) - superseded"), "exact line"),
    (lambda d: route(variant(d, "R2-regression"), "native-index")["expect"]["lines"][0].update(presentation="ordinary"), "unannotated-superseded-entry"),
    (lambda d: variant(d, "D2-held-out")["questions"][1].update(key="present-documentary"), "required_for must name unique question keys"),
    (lambda d: variant(d, "D1-held-out")["questions"][1].update(asked=True), "held-out variant must preserve"),
    (lambda d: variant(d, "D2-held-out")["files"][1].update(path="memory/humidity-drift-status.md") or source(variant(d, "D2-held-out"), "status-pointer").update(path="memory/humidity-drift-status.md") or route(variant(d, "D2-held-out"), "recall-historical")["expect"]["returned_includes"][0].update(path="memory/humidity-drift-status.md") or route(variant(d, "D2-held-out"), "recall-current")["expect"]["returned_includes"][1].update(path="memory/humidity-drift-status.md"), "held-out paths must differ"),
    (lambda d: route(variant(d, "D3-held-out"), "recall-conflict").update(query="flour delivery window"), "held-out recall queries must differ"),
    (lambda d: source(variant(d, "D3-development"), "unavailable-status-source").update(path="/etc/passwd"), "unsafe source locator"),
    (lambda d: source(variant(d, "D3-development"), "unavailable-status-source").update(path="../outside"), "unsafe source locator"),
    (lambda d: source(variant(d, "D1-development"), "status-unit").update(path="/etc/passwd"), "unsafe source locator"),
    (lambda d: source(variant(d, "D1-development"), "status-unit").update(path="../outside"), "unsafe source locator"),
]


@pytest.mark.parametrize("mutation,detail", MUTATIONS)
def test_manifest_rejects_contrary_or_malformed_declarations(tmp_path, mutation, detail):
    result = run_runner("--validate-only", "--manifest", _mutate(tmp_path, mutation))
    assert result.returncode == 1
    assert detail in json.loads(result.stderr)["error"]


def test_unsafe_source_locator_is_rejected_before_any_execution(tmp_path):
    path = _mutate(tmp_path, lambda d: source(variant(d, "D3-development"), "unavailable-status-source").update(path="../outside"))
    package = _fake_package(tmp_path / "package", 'raise SystemExit("recall must not run")', init_source='raise SystemExit("init must not run")')
    result = run_runner("--family", "D3", "--manifest", path, "--package-root", package)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "unsafe source locator" in json.loads(result.stderr)["error"]


def _add_source(data):
    item = variant(data, "R1-regression")
    item["files"].append({"path": "docs/laminator-notes.md", "content": "Extra note.\n"})
    item["sources"].append({"path": "docs/laminator-notes.md", "role": "status-pointer", "availability": "present", "required_for": [], "passages": []})


def _add_status_locator(data):
    variant(data, "R1-regression")["sources"].append({"path": "docs/status/laminator.md", "role": "unavailable-status-source", "availability": "absent", "required_for": ["qualified-use"], "passages": []})


def _add_route(data):
    item = variant(data, "R2-regression")
    item["routes"].append(dict(route(item, "recall-redirect"), id="recall-redirect-extra"))


FREEZE_ADDITIONS = [
    _add_source,
    _add_status_locator,
    lambda d: variant(d, "R1-regression")["questions"][0]["correct"].append({"code": "extra-correct", "text": "An added correct outcome."}),
    lambda d: variant(d, "R1-regression")["questions"][0]["harmful"].append({"code": "extra-harmful", "text": "An added harmful outcome."}),
    _add_route,
]


@pytest.mark.parametrize("addition", FREEZE_ADDITIONS)
def test_structurally_valid_additions_fail_the_manifest_freeze(tmp_path, addition):
    result = run_runner("--validate-only", "--manifest", _mutate(tmp_path, addition))
    assert result.returncode == 1
    assert json.loads(result.stderr)["error"] == "manifest bytes do not match the reviewed SHA-256 freeze"


def test_any_byte_change_fails_the_freeze_before_execution(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_bytes(MANIFEST.read_bytes() + b"\n")
    validation = run_runner("--validate-only", "--manifest", path)
    assert validation.returncode == 1
    assert "SHA-256 freeze" in json.loads(validation.stderr)["error"]
    package = _fake_package(tmp_path / "package", 'raise SystemExit("recall must not run")', init_source='raise SystemExit("init must not run")')
    execution = run_runner("--family", "R1", "--manifest", path, "--package-root", package)
    assert execution.returncode == 1 and execution.stdout == ""


def test_manifest_rejects_a_non_object_top_level(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("[]", encoding="utf-8")
    result = run_runner("--validate-only", "--manifest", path)
    assert result.returncode == 1
    assert json.loads(result.stderr)["error"] == "manifest top level must be an object"


def test_development_run_checks_declared_routes_and_preserves_bytes(tmp_path):
    output = tmp_path / "result.json"
    result = run_runner("--output", output, "--package-root", HERE.parents[1])
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["scored_semantics"] is False and payload["agent_behavior_observed"] is False
    assert payload["executed_splits"] == ["development", "regression"]
    results = {item["variant"]: item for item in payload["variants"]}
    assert set(results) == DEVELOPMENT_IDS
    for item in results.values():
        assert item["fixture_unchanged"] and item["changed_paths"] == []
        assert all(r["integrity"] == "pass" and r["expectation"]["status"] == "pass" for r in item["routes"])
        assert all(s["status"] == "pass" for s in item["source_availability"])
        for recall in (r for r in item["routes"] if r["type"] == "recall"):
            assert recall["execution"]["argv"][:3] == ["-P", "-m", "validated_memory"]
            assert isinstance(recall["execution"]["duration_seconds"], float)

    redirect = route(results["R2-regression"], "recall-redirect")["delivery"]["returned"]
    assert [r["path"] for r in redirect] == ["memory/gallery-lighting-advice-dimmed.md"]
    assert redirect[0]["redirected_from"] == [{"identity": "gallery-lighting-advice", "path": "memory/gallery-lighting-advice.md"}]
    index = route(results["R2-regression"], "native-index")
    assert "- [Gallery lighting advice](gallery-lighting-advice.md) - keep gallery lights at full brightness" in index["delivery"]["lines"]
    assert index["declared"]["lines"][0]["presentation"] == "unannotated-superseded-entry"

    absent = next(s for s in results["D3-development"]["source_availability"] if s["role"] == "unavailable-status-source")
    assert absent["declared"] == absent["observed"] == "absent"
    conflict = route(results["D3-development"], "recall-conflict")["delivery"]["returned"]
    assert all(r["state"] == "active" and r["successors"] == [] and r["redirected_from"] == [] for r in conflict)

    qualified = route(results["R1-regression"], "recall-qualified")["delivery"]["returned"]
    assert {r["path"] for r in qualified} >= {"memory/laminator-heat-setting.md", "memory/laminator-humid-day-qualification.md"}
    assert all(r["state"] == "active" and r["successors"] == [] for r in qualified)

    status = next(s for s in results["D1-development"]["source_availability"] if s["role"] == "status-unit")
    assert status["required_for"] == ["adjacent-volunteered"] and status["passages_present"] is True


def test_held_out_routes_are_not_executed_without_explicit_selection(tmp_path):
    output = tmp_path / "result.json"
    result = run_runner("--family", "D1", "--output", output, "--package-root", HERE.parents[1])
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert [item["variant"] for item in payload["variants"]] == ["D1-development"]
    assert "held-out" not in output.read_text(encoding="utf-8")


def test_contrary_route_delivery_is_reported_as_mismatch(tmp_path):
    ignored_redirect = (
        'print(json.dumps({"schema_version": 1, "mode": "query", "complete": True, '
        '"selection": {"returned": 1, "omitted_limit": 0, "omitted_budget": 0}, '
        '"results": [{"layer": "memory", "identity": "gallery-lighting-advice", "path": "memory/gallery-lighting-advice.md", '
        '"state": "active", "successors": [], "redirected_from": []}]}))'
    )
    result = run_runner("--family", "R2", "--package-root", _fake_package(tmp_path / "package", ignored_redirect))
    assert result.returncode == 1
    observed = route(json.loads(result.stdout)["variants"][0], "recall-redirect")
    assert observed["integrity"] == "pass"
    assert observed["expectation"] == {"status": "mismatch", "mismatches": [
        "expected memory/gallery-lighting-advice-dimmed.md to be returned",
        "expected memory/gallery-lighting-advice.md not to be returned",
    ]}


DEFAULT_INIT = '''(root / "memory").mkdir()
    (root / "knowledge").mkdir()
    (root / "memory" / "MEMORY.md").write_text("# Agent memory\\n")
    raise SystemExit(0)'''


def _fake_package(tmp_path, recall_source, init_source=DEFAULT_INIT):
    package = tmp_path / "validated_memory"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(f'''import json, os, pathlib, sys
root = pathlib.Path.cwd()
command = sys.argv[1]
if command == "init":
    {init_source}
if command in ("lint", "validate"):
    print(command + ": 0 file(s) checked, 0 error(s), 0 warning(s)")
    raise SystemExit(0)
{recall_source}
''', encoding="utf-8")
    return tmp_path


EMPTY_RECALL = 'print(json.dumps({"schema_version": 1, "mode": "query", "complete": True, "selection": {"returned": 0, "omitted_limit": 0, "omitted_budget": 0}, "results": []}))'


def test_fixture_byte_change_is_detected(tmp_path):
    mutate = 'open(root / "memory" / "laminator-heat-setting.md", "a").write("changed\\n")\n' + EMPTY_RECALL
    result = run_runner("--family", "R1", "--package-root", _fake_package(tmp_path / "package", mutate))
    assert result.returncode == 1
    item = json.loads(result.stdout)["variants"][0]
    assert item["fixture_unchanged"] is False
    assert item["changed_paths"] == ["memory/laminator-heat-setting.md"]


def test_created_file_is_detected_as_fixture_change(tmp_path):
    create = '(root / "memory" / "scratch.md").write_text("x")\n' + EMPTY_RECALL
    result = run_runner("--family", "R1", "--package-root", _fake_package(tmp_path / "package", create))
    assert result.returncode == 1
    assert json.loads(result.stdout)["variants"][0]["changed_paths"] == ["memory/scratch.md"]


@pytest.mark.parametrize("recall_source,kind", [
    ('print("{")', "malformed-recall"),
    ('print("[]")', "malformed-recall"),
    ('print(json.dumps({"schema_version": 1, "mode": "query", "complete": True, "selection": {"returned": 1, "omitted_limit": 0, "omitted_budget": 0}, "results": [{"identity": "x"}]}))', "malformed-recall"),
    ('print(json.dumps({"schema_version": 1, "mode": "query", "complete": False, "selection": {}, "results": []}))', "malformed-recall"),
    ('raise SystemExit(3)', "unexpected-exit"),
])
def test_malformed_recall_output_is_structured_failure(tmp_path, recall_source, kind):
    result = run_runner("--family", "R1", "--package-root", _fake_package(tmp_path / "package", recall_source))
    assert result.returncode == 1
    observed = route(json.loads(result.stdout)["variants"][0], "recall-qualified")
    assert observed["integrity"] == "failure"
    assert observed["failure"]["kind"] == kind
    assert observed["expectation"]["status"] == "not-evaluated"


def test_init_symlink_escape_is_a_structured_failure_that_writes_nothing_outside(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "MEMORY.md"
    sentinel.write_bytes(b"external sentinel\n")
    before = {p.name: p.read_bytes() for p in outside.iterdir()}
    escape = f'''os.symlink({str(outside)!r}, root / "memory")
    (root / "knowledge").mkdir()
    raise SystemExit(0)'''
    result = run_runner("--family", "R1", "--package-root", _fake_package(tmp_path / "package", EMPTY_RECALL, init_source=escape))
    assert result.returncode == 1
    item = json.loads(result.stdout)["variants"][0]
    assert item["error"] == "unsafe fixture write"
    assert item["failure"]["kind"] == "unsafe-fixture-write"
    assert "routes" not in item
    assert {p.name: p.read_bytes() for p in outside.iterdir()} == before


def test_runner_surfaces_subprocess_setup_failure(tmp_path):
    result = run_runner("--family", "R1", "--package-root", tmp_path)
    assert result.returncode == 1
    assert json.loads(result.stdout)["variants"][0]["error"] == "init failed"
