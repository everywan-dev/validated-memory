"""Offline synthetic diagnostic runner for the M0 discovery benchmark."""
from __future__ import annotations

import argparse, hashlib, json, os, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("manifest.json")
WATCHDOG = 10.0
DISPOSITIONS = {"eligible", "excluded", "source-absent", "corpus-unavailable", "invalid-source", "negative-no-answer"}
RELEVANCE = {"required", "useful", "irrelevant", "excluded"}


def load_manifest(path=MANIFEST):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"manifest cannot be read as JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("manifest top level must be an object")
    families = data.get("families")
    if data.get("schema_version") != 1 or not isinstance(families, list):
        raise ValueError("manifest schema_version or families is invalid")
    counts = {s: sum(isinstance(f, dict) and f.get("split") == s for f in families) for s in ("development", "held-out")}
    if counts != {"development": 12, "held-out": 6}:
        raise ValueError("manifest must contain exactly 12 development and 6 held-out families")
    ids = set()
    for family in families:
        if not isinstance(family, dict) or not isinstance(family.get("id"), str) or not family["id"]:
            raise ValueError("each family needs a nonempty string id")
        fid = family["id"]
        if fid in ids or family.get("split") not in counts:
            raise ValueError("family ids must be unique and splits valid")
        ids.add(fid)
        keys = ("domain", "query", "files", "required_paths", "relevance", "route_availability", "disposition", "acceptable_uncertainty", "disallowed_inference", "expected_original_reads", "passages", "variants")
        if any(key not in family for key in keys):
            raise ValueError(f"{fid}: required field missing")
        if family["disposition"] not in DISPOSITIONS:
            raise ValueError(f"{fid}: invalid disposition")
        if not isinstance(family["files"], list) or not isinstance(family["required_paths"], list):
            raise ValueError(f"{fid}: files and required_paths must be lists")
        paths, bodies = set(), {}
        for item in family["files"]:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not item["path"] or not isinstance(item.get("content"), str):
                raise ValueError(f"{fid}: invalid fixture file")
            path = item["path"]
            if Path(path).is_absolute() or ".." in Path(path).parts or path in paths:
                raise ValueError(f"{fid}: unsafe or duplicate fixture path")
            paths.add(path)
            bodies[path] = item["content"].split("---\n", 2)[-1]
        required = family["required_paths"]
        if len(required) != len(set(required)) or any(not isinstance(p, str) or not p for p in required):
            raise ValueError(f"{fid}: required paths must be unique nonempty strings")
        relevance = family["relevance"]
        if not isinstance(relevance, dict) or set(relevance) != paths or any(v not in RELEVANCE for v in relevance.values()):
            raise ValueError(f"{fid}: every fixture path needs one valid relevance disposition")
        for path in required:
            if path in paths and relevance[path] not in {"required", "excluded"}:
                raise ValueError(f"{fid}: required fixture has inconsistent relevance")
            if path not in paths and family["disposition"] not in {"source-absent", "corpus-unavailable"}:
                raise ValueError(f"{fid}: required path is absent without an absence disposition")
        availability = family["route_availability"]
        if not isinstance(availability, dict) or set(availability) != {"raw", "hook"} or any(v not in {"available", "unavailable"} for v in availability.values()):
            raise ValueError(f"{fid}: invalid route availability")
        reads = family["expected_original_reads"]
        if not isinstance(reads, list) or len(reads) != len(set(reads)) or set(reads) - set(required):
            raise ValueError(f"{fid}: original reads must be unique required paths")
        if not isinstance(family["passages"], list):
            raise ValueError(f"{fid}: passages must be a list")
        for passage in family["passages"]:
            if (
                not isinstance(passage, dict)
                or set(passage) != {"path", "text"}
                or passage["path"] not in reads
                or not isinstance(passage["text"], str)
                or not passage["text"]
                or passage["text"] not in bodies.get(passage["path"], "")
            ):
                raise ValueError(
                    f"{fid}: required passage must bind exact text to its expected original body"
                )
        if not isinstance(family["variants"], list) or not family["variants"]:
            raise ValueError(f"{fid}: variants are required")
        for variant in family["variants"]:
            if not isinstance(variant, dict) or any(not isinstance(variant.get(k), str) or not variant[k] for k in ("query", "label")):
                raise ValueError(f"{fid}: each variant needs a nonempty query and label")
    return data


def _hash_tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(x for x in root.rglob("*") if x.is_file())}


def _run(command, cwd, env, stdin=None):
    started = time.monotonic()
    try:
        proc = subprocess.run(command, cwd=cwd, env=env, input=stdin, capture_output=True, timeout=WATCHDOG, check=False)
    except subprocess.TimeoutExpired as exc:
        return {"exit": None, "stdout": (exc.stdout or b"").decode("utf-8", "replace"), "stderr": (exc.stderr or b"").decode("utf-8", "replace"), "duration_seconds": round(time.monotonic()-started, 6), "error": "timeout"}
    return {"exit": proc.returncode, "stdout": proc.stdout.decode("utf-8", "replace"), "stderr": proc.stderr.decode("utf-8", "replace"), "duration_seconds": round(time.monotonic()-started, 6), "error": None}


def _resolve_package_root(path):
    detail = "must contain an ordinary in-root validated_memory/__init__.py"
    try:
        root = path.resolve(strict=True)
        package = root / "validated_memory"
        entry = package / "__init__.py"
        if (
            not root.is_dir()
            or package.is_symlink()
            or not package.is_dir()
            or entry.is_symlink()
            or not entry.is_file()
            or not package.resolve(strict=True).is_relative_to(root)
            or not entry.resolve(strict=True).is_relative_to(package)
        ):
            raise ValueError(detail)
    except (OSError, RuntimeError) as exc:
        raise ValueError(detail) from exc
    return root


def _failure(kind, detail):
    execution = kind in {"timeout", "unexpected-exit"}
    return {"status": "execution-failure" if execution else "invalid-output", "payload": None, "integrity": "failure", "failure": {"kind": kind, "detail": detail}}


def _validate_records(payload, key, counts, allow_null=False):
    records = payload.get(key)
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise ValueError(f"{key} must be a list of objects")
    paths = []
    for record in records:
        if any(not isinstance(record.get(k), str) or not record[k] for k in ("path", "identity")):
            raise ValueError("each returned record needs nonempty path and identity")
        paths.append(record["path"])
    if len(paths) != len(set(paths)):
        raise ValueError("returned paths must be unique")
    if allow_null and not records and any(value is None for value in counts):
        return records
    if any(type(value) is not int or value < 0 for value in counts):
        raise ValueError("lookup counts must be nonnegative integers")
    returned, limit, budget, matched = counts
    if returned != len(records) or matched != returned + limit + budget:
        raise ValueError("lookup counts are inconsistent with returned records")
    return records


def _raw_observation(run, expected):
    if run["error"]:
        return _failure("timeout", "raw recall exceeded the runner watchdog")
    wanted = 0 if expected == "available" else 1
    if run["exit"] != wanted:
        return _failure("unexpected-exit", f"raw recall exit {run['exit']}, expected {wanted}")
    try:
        payload = json.loads(run["stdout"])
        if not isinstance(payload, dict) or payload.get("schema_version") != 1 or payload.get("mode") != "query" or payload.get("complete") is not (expected == "available"):
            raise ValueError("raw envelope identity, mode, or complete status is invalid")
        coverage, selection = payload.get("coverage"), payload.get("selection")
        if not isinstance(coverage, dict) or not isinstance(selection, dict):
            raise ValueError("coverage and selection must be objects")
        records = _validate_records(payload, "results", (selection.get("returned"), selection.get("omitted_limit"), selection.get("omitted_budget"), coverage.get("matched")), expected == "unavailable")
        if expected == "unavailable" and records:
            raise ValueError("unavailable raw recall returned records")
    except (json.JSONDecodeError, ValueError) as exc:
        return _failure("malformed-raw", str(exc))
    return {"status": expected, "payload": payload, "integrity": "pass", "failure": None}


def _hook_observation(run, expected):
    if run["error"]:
        return _failure("timeout", "hook exceeded the runner watchdog")
    if run["exit"] != 0:
        return _failure("unexpected-exit", f"hook exit {run['exit']}, expected fail-open exit 0")
    try:
        outer = json.loads(run["stdout"])
        if not isinstance(outer, dict) or set(outer) != {"hookSpecificOutput"}:
            raise ValueError("hook outer envelope is invalid")
        hook = outer["hookSpecificOutput"]
        if not isinstance(hook, dict) or hook.get("hookEventName") != "UserPromptSubmit" or not isinstance(hook.get("additionalContext"), str) or not hook["additionalContext"]:
            raise ValueError("hook identity or additionalContext is invalid")
        context = json.loads(hook["additionalContext"])
        if not isinstance(context, dict):
            raise ValueError("hook context must be an object")
        actual_status = context.get("status")
        accepted = {"matched", "no_match"} if expected == "available" else {"unavailable"}
        if actual_status not in accepted:
            raise ValueError("hook status contradicts expected availability")
        lookup = context.get("lookup")
        if not isinstance(lookup, dict):
            raise ValueError("hook lookup must be an object")
        records = _validate_records(context, "candidates", (lookup.get("returned"), lookup.get("omitted_limit"), lookup.get("omitted_budget"), lookup.get("matched")), expected == "unavailable")
        if expected == "unavailable" and records:
            raise ValueError("unavailable hook returned candidates")
    except (json.JSONDecodeError, ValueError) as exc:
        return _failure("malformed-hook", str(exc))
    return {"status": expected, "payload": context, "integrity": "pass", "failure": None}


def _route_result(run, observation, family, route):
    payload = observation.get("payload") or {}
    records = payload.get("results", []) if route == "raw" else payload.get("candidates", [])
    returned = [r["path"] for r in records]
    labels = family["relevance"]
    unknown = [path for path in returned if path not in labels]
    if unknown and observation["integrity"] == "pass":
        observation, payload, returned = _failure("unlabelled-return", f"returned paths lack relevance disposition: {unknown}"), {}, []
    required = [p for p in family["required_paths"] if labels.get(p) == "required"]
    present = [p for p in required if p in returned]
    selection = payload.get("selection", {}) if route == "raw" else payload.get("lookup", {})
    scoreable = family["disposition"] in {"eligible", "excluded"}
    return {"execution": run, "status": observation["status"], "integrity": observation["integrity"], "failure": observation["failure"], "expected_disposition": family["disposition"], "output_bytes": len(run["stdout"].encode()), "returned_paths": returned, "required_returned": present, "required_total": len(required) if scoreable else 0, "coverage": len(present)/len(required) if scoreable and required and observation["integrity"] == "pass" else None, "relevance": {label: sum(labels.get(p) == label for p in returned) for label in ("required", "useful", "irrelevant")}, "ranks": {"within_window": {p: returned.index(p)+1 for p in present}}, "omissions": {"limit": selection.get("omitted_limit"), "budget": selection.get("omitted_budget")}}


def run_family(family, package_root=ROOT, repetitions=1):
    with tempfile.TemporaryDirectory(prefix="vm-m0-") as temp:
        adopter = Path(temp)
        env = dict(os.environ, PYTHONPATH=str(package_root), PYTHONDONTWRITEBYTECODE="1")
        init = _run([sys.executable, "-P", "-m", "validated_memory", "init"], adopter, env)
        if init["exit"] != 0 or init["error"]:
            return {"id": family["id"], "error": "init failed", "init": init}
        for item in family["files"]:
            path = adopter / item["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item["content"], encoding="utf-8")
        memories = [i["path"] for i in family["files"] if i["path"].startswith("memory/") and i["path"] != "memory/MEMORY.md"]
        (adopter/"memory"/"MEMORY.md").write_text(
            "# Agent memory\n\n" + "".join(
                f"- [{Path(p).stem}]({Path(p).relative_to('memory').as_posix()})\n"
                for p in sorted(memories)
            ), encoding="utf-8"
        )
        (adopter/"validated-memory-profile.md").write_text("---\nschema_version: 1\ndiscovery: automatic\nreliance: lightweight\n---\n", encoding="utf-8")
        before = _hash_tree(adopter)
        variants = []
        for variant in family["variants"]:
            raw_cmd = [sys.executable,"-P","-m","validated_memory","recall",variant["query"],"--format","json","--limit","5","--max-bytes","12288"]
            hook_cmd = [sys.executable,"-P","-m","validated_memory","agent","hook","--host","claude-code"]
            runs = []
            for _ in range(repetitions):
                raw = _run(raw_cmd, adopter, env)
                event = json.dumps({"hook_event_name":"UserPromptSubmit","cwd":str(adopter),"prompt":variant["query"]}).encode()
                hook = _run(hook_cmd, adopter, env, event)
                runs.append({"raw":_route_result(raw,_raw_observation(raw,family["route_availability"]["raw"]),family,"raw"), "hook":_route_result(hook,_hook_observation(hook,family["route_availability"]["hook"]),family,"hook")})
            variants.append({"query":variant["query"],"label":variant["label"],"raw":runs[0]["raw"],"hook":runs[0]["hook"],"repetitions":runs})
        return {"id":family["id"],"split":family["split"],"domain":family["domain"],"disposition":family["disposition"],"variants":variants,"fixture_unchanged":before == _hash_tree(adopter),"hashes":before}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--split",choices=("development","held-out"),default="development")
    parser.add_argument("--output",type=Path); parser.add_argument("--validate-only",action="store_true")
    parser.add_argument("--package-root",type=Path,default=ROOT); parser.add_argument("--manifest",type=Path,default=MANIFEST,help=argparse.SUPPRESS)
    parser.add_argument("--repetitions",type=int,choices=(1,2,3),default=1)
    args = parser.parse_args(argv)
    try: data = load_manifest(args.manifest)
    except ValueError as exc:
        print(json.dumps({"valid":False,"error":str(exc)},sort_keys=True),file=sys.stderr); return 1
    if args.validate_only:
        print(json.dumps({"valid":True,"development":12,"held-out":6},sort_keys=True)); return 0
    try:
        package_root = _resolve_package_root(args.package_root)
    except ValueError as exc:
        print(json.dumps({"error": f"package root {exc}"}, sort_keys=True), file=sys.stderr)
        return 1
    families = [run_family(f,package_root,args.repetitions) for f in data["families"] if f["split"] == args.split]
    rendered = json.dumps({"schema_version":1,"split":args.split,"families":families,"scored_semantics":False},ensure_ascii=True,indent=2,sort_keys=True)+"\n"
    args.output.write_text(rendered,encoding="utf-8") if args.output else print(rendered,end="")
    failed = any(f.get("error") or not f.get("fixture_unchanged") or any(r["integrity"] == "failure" for v in f.get("variants",[]) for rep in v["repetitions"] for r in rep.values()) for f in families)
    return int(failed)


if __name__ == "__main__": raise SystemExit(main())
