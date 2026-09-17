"""Provider-free Stage 1 fixture runner for P3 task-scoped documentary status."""
from __future__ import annotations

import argparse, hashlib, json, os, re, subprocess, sys, tempfile, time
from collections import Counter
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("manifest.json")
# Reviewed digest of the complete shipped manifest bytes. Structural checks accept
# additions; this freeze does not. Changing the manifest is an explicit reviewed edit.
MANIFEST_SHA256 = "60c1a7806676146bdc609d628326a92bd24541a8cd0dfa19fa4b5d593a600c5d"
WATCHDOG = 30.0
INDEX = "memory/MEMORY.md"
RECALL_LIMIT, RECALL_BYTES = "20", "65536"
ROLES = {
    "native-index", "status-pointer", "requested-original", "adjacent-historical-analysis",
    "status-unit", "project-status-document", "conflicting-document", "unavailable-status-source",
    "original-claim", "non-retiring-qualification", "superseded-entry", "declared-successor",
}
SCOPES = {"requested", "adjacent-unrequested", "historical", "current-documentary", "regression"}
ROUTE_TYPES = {"native-index", "recall", "lint", "validate"}
PRESENTATIONS = {"ordinary", "unannotated-superseded-entry"}
STATES = {"active", "superseded"}
# Frozen from the P3 specification: what each family must declare, independent of wording.
FAMILIES = {
    "D1": {"kind": "target", "splits": ["development", "held-out"],
           "roles": {"requested-original": 2, "adjacent-historical-analysis": 1, "status-unit": 1, "project-status-document": 1},
           "routes": {"native-index", "recall"}, "questions": {"requested", "adjacent-volunteered"},
           "correct": {"omit-adjacent-claim", "adjacent-closed-narrower-link-open"},
           "harmful": {"adjacent-mechanism-open", "narrower-link-closed", "status-from-dated-analysis"}},
    "D2": {"kind": "target", "splits": ["development", "held-out"],
           "roles": {"adjacent-historical-analysis": 1, "status-unit": 1, "project-status-document": 1},
           "routes": {"recall"}, "questions": {"historical", "current-documentary"},
           "correct": {"historical-open-at-that-date", "closed-narrower-link-open-not-live"},
           "harmful": {"old-state-as-current", "later-state-projected-backward", "documentary-as-external-freshness"}},
    "D3": {"kind": "target", "splits": ["development", "held-out"],
           "roles": {"conflicting-document": 2, "unavailable-status-source": 1},
           "routes": {"native-index", "recall"}, "questions": {"conflict", "unavailable"},
           "correct": {"insufficient-evidence-missing-precedence", "insufficient-evidence-missing-source"},
           "harmful": {"newest-document-authority", "invented-successor", "arbitrary-authority"}},
    "R1": {"kind": "regression", "splits": ["regression"],
           "roles": {"original-claim": 1, "non-retiring-qualification": 1},
           "routes": {"recall"}, "questions": {"qualified-use"},
           "correct": {"both-conditions-preserved"}, "harmful": {"qualification-as-successor", "condition-dropped"}},
    "R2": {"kind": "regression", "splits": ["regression"],
           "roles": {"superseded-entry": 1, "declared-successor": 1},
           "routes": {"native-index", "recall"}, "questions": {"successor-use"},
           "correct": {"follow-declared-successor"}, "harmful": {"unannotated-index-entry-as-current", "redirect-ignored"}},
}
SUMMARY = re.compile(r"^(lint|validate): (\d+) .+ checked, (\d+) error\(s\), (\d+) warning\(s\)$")


def _text(value):
    return isinstance(value, str) and bool(value)


def _refs(value, allow_null_path=False):
    return isinstance(value, list) and all(
        isinstance(r, dict) and set(r) == {"identity", "path"} and _text(r["identity"])
        and (_text(r["path"]) or (allow_null_path and r["path"] is None))
        for r in value
    )


def _safe_locator(path):
    """A POSIX-relative locator with no absolute root, backslash, empty, `.` or `..` part."""
    return _text(path) and not PurePosixPath(path).is_absolute() and "\\" not in path and "\0" not in path and all(
        part not in {"", ".", ".."} for part in path.split("/")
    )


def load_manifest(path=MANIFEST):
    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"manifest cannot be read as JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("manifest top level must be an object")
    families = data.get("families")
    if data.get("schema_version") != 1 or not isinstance(families, list):
        raise ValueError("manifest schema_version or families is invalid")
    ids = [f.get("id") if isinstance(f, dict) else None for f in families]
    if len(ids) != len(set(ids)) or set(ids) != set(FAMILIES):
        raise ValueError("family ids must be unique and exactly D1, D2, D3, R1, R2")
    variant_ids = set()
    for family in families:
        _check_family(family, variant_ids)
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError("manifest bytes do not match the reviewed SHA-256 freeze")
    return data


def _check_family(family, variant_ids):
    fid, need = family["id"], FAMILIES[family["id"]]
    if family.get("kind") != need["kind"] or not _text(family.get("distinction")):
        raise ValueError(f"{fid}: kind or distinction is invalid")
    variants = family.get("variants")
    if not isinstance(variants, list) or any(not isinstance(v, dict) for v in variants):
        raise ValueError(f"{fid}: variants must be a list of objects")
    if sorted(str(v.get("split")) for v in variants) != sorted(need["splits"]):
        raise ValueError(f"{fid}: split membership must be exactly {need['splits']}")
    for variant in variants:
        if not _text(variant.get("id")) or variant["id"] in variant_ids:
            raise ValueError(f"{fid}: variant ids must be unique nonempty strings")
        variant_ids.add(variant["id"])
        _check_variant(fid, variant)
        _check_requirements(fid, need, variant)
    if len(variants) == 2:
        by_split = {v["split"]: v for v in variants}
        _check_held_out(fid, by_split["development"], by_split["held-out"])


def _check_variant(fid, variant):
    vid = variant["id"]
    if not _text(variant.get("domain")):
        raise ValueError(f"{vid}: domain is required")
    files = variant.get("files")
    if not isinstance(files, list):
        raise ValueError(f"{vid}: files must be a list")
    contents = {}
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "content"} or not _text(item["path"]) or not isinstance(item["content"], str):
            raise ValueError(f"{vid}: invalid fixture file")
        path = item["path"]
        if not _safe_locator(path):
            raise ValueError(f"{vid}: unsafe fixture path {path!r}")
        if path in contents:
            raise ValueError(f"{vid}: duplicate fixture path {path}")
        contents[path] = item["content"]
    if INDEX not in contents:
        raise ValueError(f"{vid}: {INDEX} fixture is required")

    questions = variant.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError(f"{vid}: questions must be a nonempty list")
    keys = set()
    for question in questions:
        if not isinstance(question, dict) or not _text(question.get("key")) or question["key"] in keys:
            raise ValueError(f"{vid}: question keys must be unique nonempty strings")
        keys.add(question["key"])
        if question.get("scope") not in SCOPES or not isinstance(question.get("asked"), bool) or not _text(question.get("text")):
            raise ValueError(f"{vid}: question {question['key']} needs scope, asked and text")
        codes = []
        for side in ("correct", "harmful"):
            outcomes = question.get(side)
            if not isinstance(outcomes, list) or not outcomes or any(
                not isinstance(o, dict) or set(o) != {"code", "text"} or not _text(o["code"]) or not _text(o["text"]) for o in outcomes
            ):
                raise ValueError(f"{vid}: question {question['key']} needs a nonempty {side} outcome list")
            codes += [o["code"] for o in outcomes]
        if len(codes) != len(set(codes)):
            raise ValueError(f"{vid}: question {question['key']} correct and harmful outcome codes must be distinct")

    sources = variant.get("sources")
    if not isinstance(sources, list):
        raise ValueError(f"{vid}: sources must be a list")
    declared, roles, required = set(), {}, set()
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"path", "role", "availability", "required_for", "passages"} or not _text(source["path"]):
            raise ValueError(f"{vid}: invalid source declaration")
        path = source["path"]
        if not _safe_locator(path):
            raise ValueError(f"{vid}: unsafe source locator {path!r}")
        if path in declared:
            raise ValueError(f"{vid}: duplicate source path {path}")
        declared.add(path)
        if source["role"] not in ROLES:
            raise ValueError(f"{vid}: unknown source role for {path}")
        roles[path] = source["role"]
        if (source["role"] == "unavailable-status-source") != (source["availability"] == "absent") or source["availability"] not in {"present", "absent"}:
            raise ValueError(f"{vid}: {path}: only an unavailable-status-source is absent, and it must be absent")
        if (path == INDEX) != (source["role"] == "native-index"):
            raise ValueError(f"{vid}: the native-index role belongs exactly to {INDEX}")
        need = source["required_for"]
        if not isinstance(need, list) or len(need) != len(set(need)) or not set(need) <= keys:
            raise ValueError(f"{vid}: {path}: required_for must name unique question keys")
        required |= set(need)
        passages = source["passages"]
        if not isinstance(passages, list) or any(not _text(p) for p in passages):
            raise ValueError(f"{vid}: {path}: passages must be nonempty strings")
        if source["availability"] == "present":
            if path not in contents:
                raise ValueError(f"{vid}: present source {path} is missing from files")
            if any(p not in contents[path] for p in passages):
                raise ValueError(f"{vid}: {path}: passage must bind exact text in the source")
        elif path in contents or passages:
            raise ValueError(f"{vid}: absent source {path} must have no file and no passages")
    if set(contents) - declared:
        raise ValueError(f"{vid}: every fixture file needs one source declaration")
    if keys - required:
        raise ValueError(f"{vid}: each question needs at least one required source")

    routes = variant.get("routes")
    if not isinstance(routes, list):
        raise ValueError(f"{vid}: routes must be a list")
    route_ids = set()
    for route in routes:
        if not isinstance(route, dict) or not _text(route.get("id")) or route["id"] in route_ids:
            raise ValueError(f"{vid}: route ids must be unique nonempty strings")
        route_ids.add(route["id"])
        kind, expect = route.get("type"), route.get("expect")
        if kind not in ROUTE_TYPES or not isinstance(expect, dict):
            raise ValueError(f"{vid}: route {route['id']} has an unknown type or no expectation")
        if kind in {"lint", "validate"}:
            if set(expect) != {"exit"} or expect["exit"] not in (0, 1):
                raise ValueError(f"{vid}: {kind} expectation must declare exit 0 or 1")
        elif kind == "native-index":
            _check_index_route(vid, expect, contents, roles)
        else:
            _check_recall_route(vid, route, keys, contents)
    if sum(r["type"] == "lint" for r in routes) != 1:
        raise ValueError(f"{vid}: exactly one lint route is required")
    has_units = any(p.startswith("knowledge/") for p in contents)
    if has_units != any(r["type"] == "validate" for r in routes):
        raise ValueError(f"{vid}: a validate route is required exactly when knowledge fixtures exist")


def _check_index_route(vid, expect, contents, roles):
    lines = expect.get("lines")
    index_lines = contents[INDEX].splitlines()
    if set(expect) != {"lines"} or not isinstance(lines, list) or not lines:
        raise ValueError(f"{vid}: native-index expectation must declare lines")
    for item in lines:
        if not isinstance(item, dict) or set(item) != {"line", "target", "presentation"} or item["presentation"] not in PRESENTATIONS:
            raise ValueError(f"{vid}: invalid native-index line declaration")
        target = item["target"]
        if item["line"] not in index_lines:
            raise ValueError(f"{vid}: declared index line is not an exact line of {INDEX}")
        if not _text(target) or not target.startswith("memory/") or target not in contents or f"]({target[len('memory/'):]})" not in item["line"]:
            raise ValueError(f"{vid}: index line target must be the linked memory fixture")
        if (item["presentation"] == "unannotated-superseded-entry") != (roles.get(target) == "superseded-entry"):
            raise ValueError(f"{vid}: unannotated-superseded-entry labels exactly the superseded-entry targets")


def _check_recall_route(vid, route, keys, contents):
    expect = route["expect"]
    if route.get("question") not in keys or not _text(route.get("query")) or not isinstance(route.get("include_superseded"), bool):
        raise ValueError(f"{vid}: recall route {route['id']} needs a question key, query and include_superseded")
    if set(expect) != {"exit", "returned_includes", "returned_excludes"} or expect["exit"] not in (0, 1):
        raise ValueError(f"{vid}: recall expectation must declare exit, returned_includes and returned_excludes")
    includes, excludes = expect["returned_includes"], expect["returned_excludes"]
    if not isinstance(includes, list) or not isinstance(excludes, list) or any(not _text(p) for p in excludes):
        raise ValueError(f"{vid}: recall expectation lists are invalid")
    paths = []
    for item in includes:
        if (not isinstance(item, dict) or set(item) != {"path", "state", "successors", "redirected_from"}
                or item["state"] not in STATES or not _refs(item["successors"]) or not _refs(item["redirected_from"])):
            raise ValueError(f"{vid}: recall expectation entry is invalid")
        paths.append(item["path"])
    layered = [p for p in contents if p != INDEX and p.split("/", 1)[0] in {"memory", "knowledge"}]
    refs = [r["path"] for item in includes for r in item["successors"] + item["redirected_from"]]
    if len(paths) != len(set(paths)) or set(paths) & set(excludes) or not set(paths + excludes + refs) <= set(layered):
        raise ValueError(f"{vid}: recall expectation paths must be unique recall-layer fixtures")


def _check_requirements(fid, need, variant):
    vid = variant["id"]
    counts = Counter(s["role"] for s in variant["sources"])
    for role, minimum in need["roles"].items():
        if counts[role] < minimum:
            raise ValueError(f"{vid}: role {role} requires at least {minimum} source(s)")
    if not need["routes"] <= {r["type"] for r in variant["routes"]}:
        raise ValueError(f"{vid}: route types must include {sorted(need['routes'])}")
    if {q["key"] for q in variant["questions"]} != need["questions"]:
        raise ValueError(f"{vid}: question keys must be exactly {sorted(need['questions'])}")
    for side in ("correct", "harmful"):
        codes = {o["code"] for q in variant["questions"] for o in q[side]}
        if not need[side] <= codes:
            raise ValueError(f"{vid}: {side} outcome codes must include {sorted(need[side] - codes)}")


def _shape(variant):
    return {
        "questions": sorted((q["key"], q["scope"], q["asked"], tuple(sorted(o["code"] for o in q["correct"])), tuple(sorted(o["code"] for o in q["harmful"]))) for q in variant["questions"]),
        "roles": sorted(s["role"] for s in variant["sources"]),
        "routes": sorted((r["type"], r.get("include_superseded")) for r in variant["routes"]),
    }


def _check_held_out(fid, development, held_out):
    if _shape(development) != _shape(held_out):
        raise ValueError(f"{fid}: held-out variant must preserve question keys, outcome codes, source roles and route types")
    shared = {f["path"] for f in development["files"]} & {f["path"] for f in held_out["files"]}
    if shared - {INDEX}:
        raise ValueError(f"{fid}: held-out paths must differ from development paths: {sorted(shared - {INDEX})}")
    queries = {r["query"] for r in development["routes"] if r["type"] == "recall"}
    if queries & {r["query"] for r in held_out["routes"] if r["type"] == "recall"}:
        raise ValueError(f"{fid}: held-out recall queries must differ from development queries")


def _hash_tree(root):
    hashes = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            key = path.relative_to(root).as_posix()
            if path.is_symlink():
                hashes[key] = "symlink:" + os.readlink(path)
            elif path.is_file():
                hashes[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(hashes.items()))


def _contained(adopter, locator):
    """The adopter path for a validated locator, or None when an existing component
    is a symlink or the unresolved/resolved target leaves the adopter. This is a
    deterministic check for this runner, not protection against concurrent writers."""
    current = adopter
    for part in locator.split("/"):
        current = current / part
        if current.is_symlink():
            return None
        if not current.exists():
            break
    target = adopter / locator
    try:
        resolved = target.resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    if adopter not in target.parents or not resolved.is_relative_to(adopter.resolve(strict=True)):
        return None
    return target


def _cli(*args):
    return [sys.executable, "-P", "-m", "validated_memory", *args]


def _run(command, cwd, env):
    started = time.monotonic()
    argv = command[1:]
    try:
        proc = subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=WATCHDOG, check=False)
    except subprocess.TimeoutExpired as exc:
        return {"argv": argv, "exit": None, "stdout": (exc.stdout or b"").decode("utf-8", "replace"), "stderr": (exc.stderr or b"").decode("utf-8", "replace"), "duration_seconds": round(time.monotonic() - started, 6), "error": "timeout"}
    return {"argv": argv, "exit": proc.returncode, "stdout": proc.stdout.decode("utf-8", "replace"), "stderr": proc.stderr.decode("utf-8", "replace"), "duration_seconds": round(time.monotonic() - started, 6), "error": None}


def _failure(kind, detail):
    return {"integrity": "failure", "failure": {"kind": kind, "detail": detail}, "delivery": None, "expectation": {"status": "not-evaluated", "mismatches": []}}


def _judged(delivery, mismatches):
    return {"integrity": "pass", "failure": None, "delivery": delivery, "expectation": {"status": "mismatch" if mismatches else "pass", "mismatches": mismatches}}


def _observe_recall(run, expect):
    if run["error"]:
        return _failure("timeout", "recall exceeded the runner watchdog")
    if run["exit"] not in (0, 1):
        return _failure("unexpected-exit", f"recall exit {run['exit']}")
    try:
        payload = json.loads(run["stdout"])
        if not isinstance(payload, dict) or payload.get("schema_version") != 1 or payload.get("mode") != "query" or payload.get("complete") is not (run["exit"] == 0):
            raise ValueError("recall envelope identity, mode, or complete status is invalid")
        results, selection = payload.get("results"), payload.get("selection")
        if not isinstance(results, list) or not isinstance(selection, dict):
            raise ValueError("results must be a list and selection an object")
        returned = []
        for record in results:
            if (not isinstance(record, dict) or not _text(record.get("identity")) or not _text(record.get("path"))
                    or record.get("state") not in STATES or not _refs(record.get("successors"), True) or not _refs(record.get("redirected_from"))):
                raise ValueError("each recall result needs identity, path, state, successors and redirected_from")
            returned.append({k: record.get(k) for k in ("layer", "identity", "path", "state", "successors", "redirected_from")})
        if len({r["path"] for r in returned}) != len(returned):
            raise ValueError("returned paths must be unique")
        omitted = None
        if run["exit"] == 1:
            if returned:
                raise ValueError("unavailable recall returned results")
        else:
            counts = [selection.get(k) for k in ("returned", "omitted_limit", "omitted_budget")]
            if any(type(c) is not int or c < 0 for c in counts) or counts[0] != len(returned):
                raise ValueError("selection counts are inconsistent with returned results")
            omitted = counts[1] + counts[2]
    except (json.JSONDecodeError, ValueError) as exc:
        return _failure("malformed-recall", str(exc))
    mismatches = []
    if run["exit"] != expect["exit"]:
        mismatches.append(f"recall exit {run['exit']}, expected {expect['exit']}")
    if omitted:
        mismatches.append(f"{omitted} match(es) omitted; declared inclusion is not window-independent")
    by_path = {r["path"]: r for r in returned}
    for item in expect["returned_includes"]:
        got = by_path.get(item["path"])
        if got is None:
            mismatches.append(f"expected {item['path']} to be returned")
            continue
        for key in ("state", "successors", "redirected_from"):
            if got[key] != item[key]:
                mismatches.append(f"{item['path']}: {key} is {got[key]!r}, expected {item[key]!r}")
    mismatches += [f"expected {p} not to be returned" for p in expect["returned_excludes"] if p in by_path]
    delivery = {"status": "available" if run["exit"] == 0 else "unavailable", "returned": returned, "omitted": omitted}
    return _judged(delivery, mismatches)


def _observe_summary(run, command, expect):
    if run["error"]:
        return _failure("timeout", f"{command} exceeded the runner watchdog")
    if run["exit"] not in (0, 1):
        return _failure("unexpected-exit", f"{command} exit {run['exit']}")
    lines = run["stdout"].strip().splitlines()
    match = SUMMARY.match(lines[-1]) if lines else None
    if not match or match.group(1) != command or (int(match.group(3)) == 0) != (run["exit"] == 0):
        return _failure("malformed-summary", f"{command} summary is missing or contradicts its exit")
    delivery = {"checked": int(match.group(2)), "errors": int(match.group(3)), "warnings": int(match.group(4))}
    mismatches = [] if run["exit"] == expect["exit"] else [f"{command} exit {run['exit']}, expected {expect['exit']}"]
    return _judged(delivery, mismatches)


def _observe_index(adopter, expect):
    index = _contained(adopter, INDEX)
    if index is None:
        return _failure("unsafe-index-path", f"{INDEX} has a symlink component or leaves the adopter")
    try:
        data = index.read_bytes()
        lines = data.decode("utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return _failure("index-unreadable", str(exc))
    mismatches = []
    for item in expect["lines"]:
        if item["line"] not in lines:
            mismatches.append(f"index line not delivered: {item['line']!r}")
        target = _contained(adopter, item["target"])
        if target is None or not target.is_file():
            mismatches.append(f"index target missing: {item['target']}")
    delivery = {"sha256": hashlib.sha256(data).hexdigest(), "lines": lines}
    return _judged(delivery, mismatches)


def _execute_route(route, adopter, env):
    kind, expect = route["type"], route["expect"]
    base = {"id": route["id"], "type": kind, "question": route.get("question"), "declared": expect}
    if kind == "native-index":
        # The native route is the host reading the index file; no subprocess or parsing is involved.
        return {**base, "execution": None, **_observe_index(adopter, expect)}
    if kind == "recall":
        args = ["recall", route["query"], "--format", "json", "--limit", RECALL_LIMIT, "--max-bytes", RECALL_BYTES]
        if route["include_superseded"]:
            args.append("--include-superseded")
        run = _run(_cli(*args), adopter, env)
        return {**base, "query": route["query"], "include_superseded": route["include_superseded"], "execution": run, **_observe_recall(run, expect)}
    run = _run(_cli(kind), adopter, env)
    return {**base, "execution": run, **_observe_summary(run, kind, expect)}


def _declared_bytes_match(adopter, variant):
    for item in variant["files"]:
        target = _contained(adopter, item["path"])
        if target is None or not target.is_file() or target.read_bytes() != item["content"].encode("utf-8"):
            return False
    return True


def _availability(adopter, variant):
    rows = []
    for source in variant["sources"]:
        target = _contained(adopter, source["path"])
        observed = "unsafe" if target is None else "present" if target.is_file() else "absent"
        passages = None
        if observed == "present":
            text = target.read_bytes().decode("utf-8", "replace")
            passages = all(p in text for p in source["passages"])
        ok = observed == source["availability"] and passages is not False
        rows.append({"path": source["path"], "role": source["role"], "required_for": source["required_for"], "declared": source["availability"], "observed": observed, "passages_present": passages, "status": "pass" if ok else "mismatch"})
    return rows


def run_variant(family, variant, package_root=ROOT):
    base = {"family": family["id"], "kind": family["kind"], "variant": variant["id"], "split": variant["split"], "domain": variant["domain"]}
    with tempfile.TemporaryDirectory(prefix="vm-p3-") as temp:
        adopter = Path(temp) / "adopter"
        adopter.mkdir()
        env = dict(os.environ, PYTHONPATH=str(package_root), PYTHONDONTWRITEBYTECODE="1")
        init = _run(_cli("init"), adopter, env)
        if init["error"] or init["exit"] != 0:
            return {**base, "error": "init failed", "init": init}
        for item in variant["files"]:
            target = _contained(adopter, item["path"])
            if target is None:
                failure = {"kind": "unsafe-fixture-write", "detail": f"{item['path']} has a symlink component or leaves the adopter"}
                return {**base, "error": "unsafe fixture write", "failure": failure, "init": init}
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(item["content"].encode("utf-8"))
        before = _hash_tree(adopter)
        declared_before = _declared_bytes_match(adopter, variant)
        routes = [_execute_route(route, adopter, env) for route in variant["routes"]]
        after = _hash_tree(adopter)
        changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
        unchanged = not changed and declared_before and _declared_bytes_match(adopter, variant)
        return {**base, "init": init, "routes": routes, "source_availability": _availability(adopter, variant), "fixture_unchanged": unchanged, "changed_paths": changed, "hashes": before}


def _failed(result):
    return bool(
        result.get("error") or not result.get("fixture_unchanged")
        or any(r["integrity"] != "pass" or r["expectation"]["status"] != "pass" for r in result.get("routes", []))
        or any(s["status"] != "pass" for s in result.get("source_availability", []))
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("development", "held-out"), default="development", help="development also runs the regression cases; held-out runs only when selected explicitly")
    parser.add_argument("--family", action="append", choices=sorted(FAMILIES), help="restrict execution to these families")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--package-root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path, default=MANIFEST, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        data = load_manifest(args.manifest)
    except ValueError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    variants = [(f, v) for f in data["families"] for v in f["variants"]]
    if args.validate_only:
        counts = Counter(v["split"] for _, v in variants)
        print(json.dumps({"valid": True, "families": len(data["families"]), **{s: counts[s] for s in ("development", "held-out", "regression")}}, sort_keys=True))
        return 0
    splits = {"development", "regression"} if args.split == "development" else {"held-out"}
    selected = [(f, v) for f, v in variants if v["split"] in splits and (not args.family or f["id"] in args.family)]
    if not selected:
        print(json.dumps({"error": "no variants selected"}, sort_keys=True), file=sys.stderr)
        return 2
    results = [run_variant(f, v, args.package_root.resolve()) for f, v in selected]
    report = {
        "schema_version": 1, "selection": args.split, "executed_splits": sorted(splits), "variants": results,
        "scored_semantics": False, "agent_behavior_observed": False,
        "labels": {
            "route": "declared recall, native-index, lint and validate expectations checked against public output",
            "acquisition": "complete-original availability in the fixture only; no agent acquisition is observed",
            "semantic": "correct and harmful outcome codes are frozen labels and are not scored",
        },
    }
    rendered = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    args.output.write_text(rendered, encoding="utf-8") if args.output else print(rendered, end="")
    return int(any(_failed(r) for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
