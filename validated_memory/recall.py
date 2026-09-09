"""The `recall` subcommand: bounded, read-only, discovery-only retrieval.

Recall never certifies applicability: a match is a discovery aid, not a
validation result, per the "retrieval is discovery, not validation" decision
and the recall implementation specification this module implements.
"""

import hashlib
import json
import re
from pathlib import PurePosixPath

from . import extension as extension_module
from . import frontmatter
from . import lint
from . import memory as memory_module
from . import recall_io
from . import verdicts
from .contract import BIDI_CONTROLS, validate_documents
from .findings import ERROR

SCHEMA_VERSION = 1
MAX_QUERY_BYTES = 4096
LABEL_LIMIT = 160
EXCERPT_LIMIT = 320
DIAGNOSTIC_LIMIT = 160
FAILURE_ENVELOPE_CAP = 2048
MAX_REDIRECT_EXPANSIONS = 100_000
MAX_DIAGNOSTICS = 8
FALLBACK_MESSAGE = (
    "recall-internal: the corpus could not be read or processed safely"
)
GUIDANCE = (
    "Read the original files before relying on a match: recall finds "
    "candidates, it does not certify applicability."
)

WORD = re.compile(r"\w+")
HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
CONTROL_CHARS = frozenset(
    chr(c) for c in list(range(0x00, 0x20)) + [0x7F] + list(range(0x80, 0xA0))
)
ESCAPE_CHARS = CONTROL_CHARS | set(BIDI_CONTROLS)


class UsageError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class _RedirectBudgetExceeded(Exception):
    pass


def word_tokens(text):
    return WORD.findall(text.casefold())


def validate_arguments(query, map_flag):
    """Validate usage-level rules. Returns the query's tokens, or None for map."""
    if map_flag:
        if query is not None:
            raise UsageError("--map is mutually exclusive with a supplied query")
        return None
    if query is None:
        raise UsageError("a query is required unless --map is given")
    if len(query.encode("utf-8")) > MAX_QUERY_BYTES:
        raise UsageError(f"query exceeds the {MAX_QUERY_BYTES} UTF-8 byte limit")
    tokens = sorted(set(word_tokens(query)))
    if not tokens:
        raise UsageError("query must contain at least one word (\\w+) token")
    return tokens


def run(query, map_flag, layer, limit, max_bytes, fmt, include_superseded, stdout, stderr):
    try:
        query_tokens = validate_arguments(query, map_flag)
    except UsageError as error:
        print(f"recall: {error.message}", file=stderr)
        return 2

    layers = ("memory", "knowledge") if layer == "all" else (layer,)
    mode = "map" if map_flag else "query"
    try:
        return _execute(
            query, query_tokens, mode, layers, limit, max_bytes, fmt,
            include_superseded, stdout, stderr,
        )
    except (OSError, RecursionError, MemoryError):
        # A bounded envelope with a fixed code and message, never a traceback:
        # an unexpected input-handling failure is still a failure to report.
        return _fail(
            layers, mode, fmt, max_bytes,
            [_diag("ERROR", "", "internal", FALLBACK_MESSAGE)], stdout, stderr,
        )


def _execute(query, query_tokens, mode, layers, limit, max_bytes, fmt,
             include_superseded, stdout, stderr):
    """Acquire, validate and answer, in the specified precedence order."""
    try:
        corpus = recall_io.acquire(
            set(layers), need_config=True, need_log="knowledge" in layers
        )
    except recall_io.AcquisitionError as error:
        return _fail(
            layers, mode, fmt, max_bytes,
            [_diag("ERROR", "", "acquisition", error.message)], stdout, stderr,
            observed=error.observed,
        )

    observed = {
        "enumerated": corpus.enumerated,
        "read": corpus.enumerated - corpus.unreadable,
        "invalid": 0,
        "unreadable": corpus.unreadable,
        "auxiliary_read": corpus.auxiliary_read,
    }

    extension = None
    if corpus.config_text is not None:
        try:
            extension = extension_module.extension_from_texts(
                corpus.config_location,
                corpus.config_text,
                corpus.schema_location,
                corpus.schema_text,
            )
        except extension_module.ExtensionError as error:
            return _fail(
                layers, mode, fmt, max_bytes,
                [_diag("ERROR", error.location, error.field, error.message)],
                stdout, stderr, observed=observed,
            )

    # Document and index validation first, then verdict validation, then the
    # supersession graph: a cycle is never reported over a corrupt document.
    errors = []
    warnings = []
    if "memory" in layers:
        memory_errors, memory_warnings, invalid = _validate_memory(corpus)
        errors.extend(memory_errors)
        warnings.extend(memory_warnings)
        observed["invalid"] += invalid
    if "knowledge" in layers:
        unit_errors, unit_warnings, invalid = _validate_knowledge(corpus, extension)
        errors.extend(unit_errors)
        warnings.extend(unit_warnings)
        observed["invalid"] += invalid
    if errors:
        return _fail(
            layers, mode, fmt, max_bytes, errors, stdout, stderr, observed=observed
        )

    snapshot = None
    if "knowledge" in layers:
        try:
            snapshot = verdicts.parse_text(corpus.log_text or "")
        except verdicts.VerdictLogError as error:
            where = (
                recall_io.LOG_FILENAME if error.lineno is None
                else f"{recall_io.LOG_FILENAME}:{error.lineno}"
            )
            return _fail(
                layers, mode, fmt, max_bytes,
                [_diag("ERROR", where, "log", error.message)], stdout, stderr,
                observed=observed,
            )

    records = []
    if "memory" in layers:
        memory_records, cycle = _memory_records(corpus)
        if cycle is not None:
            return _fail(
                layers, mode, fmt, max_bytes, [cycle], stdout, stderr,
                observed=observed,
            )
        records.extend(memory_records)
    if "knowledge" in layers:
        records.extend(_knowledge_records(corpus, snapshot))

    eligible = [
        record for record in records
        if include_superseded or record["state"] == "active"
    ]

    if mode == "map":
        for record in eligible:
            _finalize_match(record, set(), False)
        candidates = sorted(
            eligible,
            key=lambda r: (r["layer"], r["group"], r["identity"], r["path"]),
        )
        matched = len(candidates)
    else:
        try:
            candidates, matched = _search(
                records, query, query_tokens, include_superseded
            )
        except _RedirectBudgetExceeded:
            return _fail(
                layers, mode, fmt, max_bytes,
                [_diag(
                    "ERROR", "", "supersedes",
                    "redirect expansion exceeded the 100,000-association bound",
                )],
                stdout, stderr, observed=observed,
            )

    coverage = _coverage(observed, eligible=len(eligible), matched=matched)
    guidance = [GUIDANCE]
    if matched == 0:
        guidance.append("Try different search terms or an ordinary source search.")

    _envelope, payload = _select_and_render(
        layers, coverage, candidates, limit, max_bytes, fmt, mode, guidance, warnings,
    )
    print(payload, file=stdout)
    return 0


def _coverage(observed, eligible=None, matched=None):
    """Coverage counts, with null reserved for what was never determined."""
    observed = observed or {}
    coverage = {
        name: observed.get(name)
        for name in ("enumerated", "read", "invalid", "unreadable", "auxiliary_read")
    }
    coverage["eligible"] = eligible
    coverage["matched"] = matched
    return coverage


def _split_findings(findings, index_location=None):
    """Split findings into error and warning diagnostics plus an invalid count.

    A document with several error findings counts once; the index, the
    configuration and the schema are diagnostic failures, not invalid
    documents, so the index location never adds to the count.
    """
    errors = []
    warnings = []
    invalid = set()
    for finding in findings:
        if finding.severity == ERROR:
            errors.append(_diag("ERROR", finding.location, finding.field, finding.message))
            if finding.location != index_location:
                invalid.add(finding.location)
        else:
            warnings.append(
                _diag("WARNING", finding.location, finding.field, finding.message)
            )
    return errors, warnings, len(invalid)


def _diag(severity, path, field, message):
    return {
        "severity": severity,
        "path": _bound(path),
        "field": _bound(field),
        "message": _bound(message),
    }


def _bound(value):
    if len(value) <= DIAGNOSTIC_LIMIT:
        return value
    return value[: DIAGNOSTIC_LIMIT - 1] + "…"


def _truncate(value, limit):
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _body_positions(text):
    lines = text.split("\n")
    start = len(lines)
    for index in range(1, len(lines)):
        if lines[index].rstrip() == "---":
            start = index + 1
            break
    return [(start + offset + 1, line) for offset, line in enumerate(lines[start:])]


def _first_heading(body):
    in_fence = False
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING.match(stripped)
        if match:
            heading = match.group(2).strip()
            if heading:
                return heading
    return None


def _make_record(layer, identity, path, text, label, state, successors):
    body_positions = _body_positions(text)
    label_text, label_truncated = _truncate(label, LABEL_LIMIT)
    body_tokens = set()
    for _lineno, line in body_positions:
        body_tokens.update(word_tokens(line))
    return {
        "layer": layer,
        "identity": identity,
        "path": path,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "label": label_text,
        "label_truncated": label_truncated,
        "state": state,
        "successors": successors,
        "redirected_from": [],
        "group": PurePosixPath(path).parent.as_posix(),
        "_identity_tokens": set(word_tokens(identity)),
        "_path_tokens": set(word_tokens(identity)) | set(word_tokens(path)),
        "_label_tokens": set(word_tokens(label)),
        "_body_tokens": body_tokens,
        "_body_positions": body_positions,
    }


def _validate_memory(corpus):
    """Validate the acquired memory documents and index. No graph rules here."""
    documents = [
        memory_module.Document(location=f"memory/{relpath}", relpath=relpath, text=text)
        for relpath, text in corpus.memory_documents
    ]
    index_location = f"memory/{recall_io.INDEX_FILENAME}"
    findings = lint.validate_in_memory(
        index_location, corpus.memory_index_text, documents
    )
    return _split_findings(findings, index_location)


def _memory_records(corpus):
    """Build memory records. Returns `(records, cycle_diagnostic_or_None)`."""
    parsed = {}
    for relpath, text in corpus.memory_documents:
        data = frontmatter.parse(text)
        parsed[data["name"]] = (relpath, text, data)

    successor_of = {}
    for identity, (_relpath, _text, data) in parsed.items():
        marker = memory_module.supersession(data["description"])
        if marker is not None and marker.target is not None:
            successor_of[identity] = marker.target

    cycle = _find_chain_cycle(successor_of)
    if cycle:
        return [], _diag(
            "ERROR", "memory", "supersedes",
            "supersession cycle: " + " -> ".join(cycle + [cycle[0]]),
        )

    records = []
    for identity, (relpath, text, data) in parsed.items():
        target = successor_of.get(identity)
        state = "superseded" if target is not None else "active"
        successors = []
        if target is not None:
            # The successor's path comes from the parsed set, never from its
            # identity: a nested entry does not live at `memory/<name>.md`.
            entry = parsed.get(target)
            successors = [{
                "identity": target,
                "path": f"memory/{entry[0]}" if entry is not None else None,
            }]
        record = _make_record(
            "memory", identity, f"memory/{relpath}", text, data["description"], state, successors,
        )
        record["memory_type"] = data["metadata"]["type"]
        records.append(record)
    return records, None


def _find_chain_cycle(successor_of):
    visited = {}
    for start in sorted(successor_of):
        if start in visited:
            continue
        chain = []
        seen_here = {}
        node = start
        while node in successor_of and node not in visited:
            if node in seen_here:
                index = chain.index(node)
                return chain[index:]
            seen_here[node] = True
            chain.append(node)
            node = successor_of[node]
        for member in chain:
            visited[member] = True
    return None


def _validate_knowledge(corpus, extension):
    """Validate the acquired curated units with the shared contract validator."""
    documents = [
        (f"knowledge/{relpath}", text) for relpath, text in corpus.knowledge_documents
    ]
    return _split_findings(validate_documents(documents, extension))


def _knowledge_records(corpus, snapshot):
    parsed = {}
    for relpath, text in corpus.knowledge_documents:
        data = frontmatter.parse(text)
        parsed[data["id"]] = (relpath, text, data)

    reverse = {}
    for identity, (_relpath, _text, data) in parsed.items():
        for target in data.get("supersedes") or []:
            reverse.setdefault(target, []).append(identity)

    records = []
    for identity, (relpath, text, data) in parsed.items():
        successor_ids = sorted(reverse.get(identity, []))
        state = "superseded" if successor_ids else "active"
        successors = [
            {"identity": target, "path": f"knowledge/{parsed[target][0]}"}
            for target in successor_ids
        ]
        body = memory_module.body(text)
        label = _first_heading(body) or identity
        anchors = []
        verdict_values = []
        for anchor in data.get("anchors") or []:
            if not isinstance(anchor, dict):
                continue
            key = verdicts.anchor_key(
                identity, anchor.get("system"), anchor.get("kind"), anchor.get("payload")
            )
            anchor_verdict = snapshot.view.get(key, verdicts.UNKNOWN)
            latest = snapshot.latest.get(key)
            anchors.append(
                {
                    "system": anchor.get("system"),
                    "kind": anchor.get("kind"),
                    "verdict": anchor_verdict,
                    "checked_at": latest.get("recorded_at") if latest else None,
                }
            )
            verdict_values.append(anchor_verdict)
        overall_verdict = verdicts.worst(verdict_values) if verdict_values else verdicts.UNKNOWN
        record = _make_record(
            "knowledge", identity, f"knowledge/{relpath}", text, label, state, successors,
        )
        record["evidence"] = data["evidence"]
        record["verdict"] = overall_verdict
        record["anchors"] = anchors
        records.append(record)
    return records


def _select_excerpt(body_positions, query_set):
    first_nonempty = None
    for lineno, line in body_positions:
        if not line.strip():
            continue
        if first_nonempty is None:
            first_nonempty = (lineno, line.strip())
        if query_set and (query_set & set(word_tokens(line))):
            return lineno, line.strip()
    if first_nonempty is not None:
        return first_nonempty
    return 1, ""


def _finalize_match(record, query_set, exact):
    lineno, line = _select_excerpt(record["_body_positions"], query_set)
    excerpt, excerpt_truncated = _truncate(line, EXCERPT_LIMIT)
    record["line"] = lineno
    record["excerpt"] = excerpt
    record["excerpt_truncated"] = excerpt_truncated
    label_hits = sorted(query_set & record["_label_tokens"])
    path_hits = sorted(query_set & record["_path_tokens"])
    body_hits = sorted(query_set & record["_body_tokens"])
    record["match"] = {
        "exact": exact,
        "label": label_hits,
        "path": path_hits,
        "body": body_hits,
    }
    score = (exact, len(label_hits), len(path_hits), len(body_hits))
    record["_score"] = score
    record["_is_match"] = exact or any(score[1:])
    return score


def _reachable_active(record, by_key, memo, budget):
    """The active endpoints reachable from `record`, iteratively and memoized.

    An explicit stack, not recursion: a supersession chain thousands of hops
    long is well inside the document ceiling and must not exhaust the
    interpreter stack. `in_progress` keeps a chain that loops back on itself
    from spinning here; cycles are rejected by validation before this runs.
    """
    start = (record["layer"], record["identity"])
    if start in memo:
        return memo[start]
    stack = [(start, False)]
    in_progress = set()
    while stack:
        key, expanded = stack.pop()
        if key in memo:
            continue
        current = by_key.get(key)
        if expanded:
            reached = set()
            for successor in current["successors"]:
                reached |= memo.get((key[0], successor["identity"]), frozenset())
            memo[key] = frozenset(reached)
            in_progress.discard(key)
            continue
        if current is None:
            memo[key] = frozenset()
            continue
        if current["state"] == "active":
            memo[key] = frozenset((key,))
            continue
        in_progress.add(key)
        stack.append((key, True))
        for successor in current["successors"]:
            budget["count"] += 1
            if budget["count"] > MAX_REDIRECT_EXPANSIONS:
                raise _RedirectBudgetExceeded()
            successor_key = (key[0], successor["identity"])
            if successor_key not in memo and successor_key not in in_progress:
                stack.append((successor_key, False))
    return memo[start]


def _rank_sort_key(entry):
    record, score = entry
    exact, label_hits, path_hits, body_hits = score
    return (
        0 if exact else 1,
        -label_hits,
        -path_hits,
        -body_hits,
        record["layer"],
        record["identity"],
        record["path"],
    )


def _search(records, query_text, query_tokens, include_superseded):
    query_set = set(query_tokens)
    normalized_query = query_text.strip().casefold()
    for record in records:
        exact = (
            normalized_query == record["identity"].casefold()
            or normalized_query == record["path"].casefold()
        )
        _finalize_match(record, query_set, exact)

    if include_superseded:
        matches = [(record, record["_score"]) for record in records if record["_is_match"]]
        ranked = sorted(matches, key=_rank_sort_key)
        return [record for record, _score in ranked], len(matches)

    by_key = {(record["layer"], record["identity"]): record for record in records}
    endpoints = {}
    for record in records:
        if record["state"] == "active":
            endpoints[(record["layer"], record["identity"])] = {
                "record": record,
                "score": record["_score"] if record["_is_match"] else (False, 0, 0, 0),
                "origins": set(),
            }

    memo = {}
    budget = {"count": 0}
    for record in records:
        if record["state"] != "superseded" or not record["_is_match"]:
            continue
        reached = _reachable_active(record, by_key, memo, budget)
        for endpoint_key in reached:
            slot = endpoints.get(endpoint_key)
            if slot is None:
                continue
            slot["origins"].add(record["identity"])
            if record["_score"] > slot["score"]:
                slot["score"] = record["_score"]

    matched = []
    for endpoint_key, slot in endpoints.items():
        if not slot["origins"] and not slot["record"]["_is_match"]:
            continue
        slot["record"]["redirected_from"] = sorted(
            (
                {"identity": origin, "path": by_key[(endpoint_key[0], origin)]["path"]}
                for origin in slot["origins"]
            ),
            key=lambda item: (item["identity"], item["path"]),
        )
        matched.append((slot["record"], slot["score"]))

    ranked = sorted(matched, key=_rank_sort_key)
    return [record for record, _score in ranked], len(matched)


PUBLIC_FIELDS = (
    "layer", "identity", "path", "line", "sha256", "label", "label_truncated",
    "excerpt", "excerpt_truncated", "state", "successors", "redirected_from",
    "match", "group", "group_count", "memory_type", "evidence", "verdict", "anchors",
)


def _public_result(record, group_counts):
    result = {
        field: record[field]
        for field in PUBLIC_FIELDS
        if field in record and not field.startswith("_")
    }
    result["group_count"] = group_counts.get(record["group"], 0)
    return {field: result[field] for field in PUBLIC_FIELDS if field in result}


def _cap_diagnostics(diagnostics):
    """Keep at most `MAX_DIAGNOSTICS`; report how many were dropped by count."""
    return diagnostics[:MAX_DIAGNOSTICS], max(0, len(diagnostics) - MAX_DIAGNOSTICS)


def _select_and_render(
    layers, coverage, candidates, limit, max_bytes, fmt, mode, guidance, warnings
):
    group_counts = {}
    for record in candidates:
        group_counts[record["group"]] = group_counts.get(record["group"], 0) + 1
    groups_total = len(group_counts)

    limited = candidates[:limit]
    omitted_limit = max(0, len(candidates) - limit)
    public_results = [_public_result(record, group_counts) for record in limited]

    kept_warnings, warnings_omitted = _cap_diagnostics(warnings)
    returned = len(public_results)
    payload = None
    while True:
        chosen = public_results[:returned]
        selection = {
            "returned": returned,
            "omitted_limit": omitted_limit,
            "omitted_budget": len(limited) - returned,
            "excerpt_truncated": sum(1 for r in chosen if r["excerpt_truncated"]),
            "groups_total": groups_total,
            "groups_returned": len({r["group"] for r in chosen}),
        }
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "mode": mode,
            "layers": sorted(layers),
            "complete": True,
            "coverage": coverage,
            "selection": selection,
            "results": chosen,
            "diagnostics": kept_warnings,
            "guidance": guidance,
        }
        if warnings_omitted:
            envelope["diagnostics_omitted"] = warnings_omitted
        payload = _render(envelope, fmt)
        if len((payload + "\n").encode("utf-8")) <= max_bytes or returned == 0:
            break
        returned -= 1
    return envelope, payload


def _render(envelope, fmt):
    if fmt == "json":
        return json.dumps(envelope, ensure_ascii=True, separators=(",", ":"))
    return _render_text(envelope)


def _escape(value):
    return "".join(
        f"\\u{ord(ch):04x}" if ch in ESCAPE_CHARS else ch for ch in value
    )


def _scalar(value):
    """Render a JSON scalar for the text format, unambiguously."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return f'"{_escape(value)}"'
    return str(value)


def _reference_list(items):
    return ", ".join(
        f'{_escape(item["identity"])} ({_scalar(item["path"])})' for item in items
    )


def _render_text(envelope):
    """Render the same information the JSON envelope carries, readably.

    Every field of a result is shown, including the metadata that decides
    whether a match is worth reading: digest, evidence, verdict, anchors and
    their check times, group membership and which tokens matched where.
    """
    lines = [
        f"recall: mode={envelope['mode']} layers={','.join(envelope['layers'])} "
        f"complete={'true' if envelope['complete'] else 'false'}"
    ]
    coverage = envelope["coverage"]
    lines.append(
        "coverage: " + ", ".join(f"{k}={_scalar(v)}" for k, v in coverage.items())
    )
    selection = envelope["selection"]
    lines.append(
        "selection: " + ", ".join(f"{k}={_scalar(v)}" for k, v in selection.items())
    )
    omitted = selection["omitted_limit"] + selection["omitted_budget"]
    if envelope["complete"] and omitted:
        lines.append(
            f"note: inputs were acquired and validated completely; {omitted} "
            "matching record(s) are omitted from this output. Raise --limit or "
            "--max-bytes, or narrow the query, to see them."
        )
    if not envelope["complete"]:
        lines.append(
            "note: the corpus could not be acquired or validated; no results are "
            "returned and the counts below are what was observed before failing."
        )
    for result in envelope["results"]:
        lines.append(
            f"- [{result['layer']}] {_escape(result['identity'])} "
            f"({_escape(result['path'])}:{result['line']}) state={result['state']}"
        )
        label_mark = " (truncated)" if result["label_truncated"] else ""
        lines.append(f"  label: \"{_escape(result['label'])}\"{label_mark}")
        excerpt_mark = " (truncated)" if result["excerpt_truncated"] else ""
        lines.append(f"  excerpt: \"{_escape(result['excerpt'])}\"{excerpt_mark}")
        lines.append(f"  sha256: {result['sha256']}")
        lines.append(
            f"  group: {_scalar(result['group'])} "
            f"(group_count={result['group_count']})"
        )
        match = result["match"]
        lines.append(
            "  match: exact={0} label=[{1}] path=[{2}] body=[{3}]".format(
                "true" if match["exact"] else "false",
                ", ".join(_escape(token) for token in match["label"]),
                ", ".join(_escape(token) for token in match["path"]),
                ", ".join(_escape(token) for token in match["body"]),
            )
        )
        if "memory_type" in result:
            lines.append(f"  memory_type: {_scalar(result['memory_type'])}")
        if "evidence" in result:
            lines.append(f"  evidence: {_scalar(result['evidence'])}")
        if "verdict" in result:
            lines.append(f"  verdict: {result['verdict']}")
        for anchor in result.get("anchors", []):
            lines.append(
                f"  anchor: system={_scalar(anchor['system'])} "
                f"kind={_scalar(anchor['kind'])} verdict={anchor['verdict']} "
                f"checked_at={_scalar(anchor['checked_at'])}"
            )
        if result["successors"]:
            lines.append(f"  successors: {_reference_list(result['successors'])}")
        if result["redirected_from"]:
            lines.append(
                f"  redirected_from: {_reference_list(result['redirected_from'])}"
            )
    for diagnostic in envelope["diagnostics"]:
        lines.append(
            f"{diagnostic['severity']}: {_escape(diagnostic['path'])}: "
            f"{diagnostic['field']}: {_escape(diagnostic['message'])}"
        )
    if "diagnostics_omitted" in envelope:
        lines.append(
            f"diagnostics_omitted: {envelope['diagnostics_omitted']} "
            "(run `validated-memory validate` and `lint` for the full list)"
        )
    for note in envelope["guidance"]:
        lines.append(f"guidance: {note}")
    return "\n".join(lines)


def _fail(layers, mode, fmt, max_bytes, diagnostics, stdout, stderr, observed=None):
    """Print the bounded failure envelope and the fixed stderr summary."""
    cap = min(max_bytes, FAILURE_ENVELOPE_CAP)
    kept, omitted = _cap_diagnostics(list(diagnostics))
    payload = None
    envelope = None
    while True:
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "mode": mode,
            "layers": sorted(layers),
            "complete": False,
            "coverage": _coverage(observed),
            "selection": {
                "returned": 0, "omitted_limit": 0, "omitted_budget": 0,
                "excerpt_truncated": 0, "groups_total": 0, "groups_returned": 0,
            },
            "results": [],
            "diagnostics": kept,
            "guidance": [
                "Recall returns no results when the corpus cannot be acquired or "
                "validated: fix the reported input, then run recall again.",
            ],
        }
        if omitted:
            envelope["diagnostics_omitted"] = omitted
        payload = _render(envelope, fmt)
        if len((payload + "\n").encode("utf-8")) <= cap or not kept:
            break
        kept = kept[:-1]
        omitted += 1
    print(payload, file=stdout)
    print("recall: acquisition or validation failed; see diagnostics", file=stderr)
    return 1
