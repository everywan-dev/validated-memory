"""Read-only prompt discovery for the Claude Code UserPromptSubmit hook.

The adapter owns activation and query preparation.  Corpus acquisition remains
the public ``recall`` command, invoked in a clean child process.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path


PROFILE_FILENAME = "validated-memory-profile.md"
PROFILE_LIMIT = 8192
INPUT_LIMIT = 65536
QUERY_LIMIT = 4096
OUTPUT_LIMIT = 8192
LOOKUP_TIMEOUT = 3.0
PREFIXES = ("busca en va:", "validated-memory:")
EVENT_NAME = "UserPromptSubmit"
PROFILE_KEYS = {"schema_version", "discovery", "reliance"}
DISCOVERY_VALUES = {"explicit", "automatic", "off"}
RELIANCE_VALUES = {"lightweight", "reviewed"}
STOPWORDS = frozenset(
    """a an and are as at be been but by can could de del el en es for from how
    i in is it la las lo los me mi my of on or para por que se the this to un
    una with would y you your please porfavor favor busca buscar search find
    tell dime muestra show continue adelante hazlo sí si yes ok okay""".split()
)
TOKEN_RE = re.compile(r"\w+", re.UNICODE)
IDENTIFIER_MARKERS = frozenset("-_/.0123456789")


class ProfileError(Exception):
    """A profile that cannot be safely used."""


class NonAdopter(Exception):
    """An existing directory that has no validated-memory adoption markers."""


def _fixed_error(reason):
    return f"profile is unavailable: {reason}"


def _regular_child(root, name, want_directory=False):
    """Return whether an exact root child is an ordinary non-symlink entry."""
    try:
        entry = os.lstat(root / name)
    except OSError:
        return False
    if stat.S_ISLNK(entry.st_mode):
        return False
    return stat.S_ISDIR(entry.st_mode) if want_directory else stat.S_ISREG(entry.st_mode)


def _adopter_root(cwd):
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        raise ProfileError("hook cwd must be an absolute path")
    root = Path(cwd)
    try:
        if not root.is_dir():
            raise ProfileError("hook cwd is not a directory")
    except OSError as error:
        raise ProfileError("hook cwd is unavailable") from error
    names = ("validated-memory.md", "memory", "knowledge")
    entries = {}
    for name in names:
        try:
            entries[name] = os.lstat(root / name)
        except FileNotFoundError:
            entries[name] = None
        except OSError as error:
            raise ProfileError("hook cwd is unavailable") from error
    for entry in entries.values():
        if entry is not None and stat.S_ISLNK(entry.st_mode):
            raise ProfileError("project adoption marker is a symlink")
    if not _regular_child(root, "validated-memory.md"):
        raise NonAdopter()
    if not _regular_child(root, "memory", want_directory=True):
        raise NonAdopter()
    if not _regular_child(root, "knowledge", want_directory=True):
        raise NonAdopter()
    return root


def _read_profile(root):
    """Read the profile with no-follow, bounded, stable metadata checks."""
    path = root / PROFILE_FILENAME
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except (OSError, AttributeError) as error:
        raise ProfileError("profile cannot be read safely") from error
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ProfileError("profile is not a regular file")
        if before.st_size > PROFILE_LIMIT:
            raise ProfileError("profile exceeds the 8,192-byte limit")
        chunks = []
        total = 0
        while total <= PROFILE_LIMIT:
            chunk = os.read(fd, min(4096, PROFILE_LIMIT + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > PROFILE_LIMIT:
            raise ProfileError("profile exceeds the 8,192-byte limit")
        after = os.fstat(fd)
        stamp = lambda item: (
            item.st_ino,
            item.st_dev,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )
        if stamp(before) != stamp(after):
            raise ProfileError("profile changed while it was being read")
        try:
            text = b"".join(chunks).decode("utf-8")
        except UnicodeDecodeError as error:
            raise ProfileError("profile is not valid UTF-8") from error
    except ProfileError:
        raise
    except OSError as error:
        raise ProfileError("profile cannot be read safely") from error
    finally:
        os.close(fd)
    return _parse_profile(text)


def _parse_profile(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ProfileError("profile frontmatter is required")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as error:
        raise ProfileError("profile frontmatter is not closed") from error
    values = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*):[ \t]*(.*)", line)
        if not match:
            raise ProfileError("profile frontmatter contains an invalid field")
        key, value = match.groups()
        if key in values:
            raise ProfileError("profile frontmatter contains a duplicate field")
        if key not in PROFILE_KEYS:
            raise ProfileError("profile frontmatter contains an unknown field")
        values[key] = value.strip()
    if set(values) != PROFILE_KEYS:
        raise ProfileError("profile frontmatter has missing fields")
    if values["schema_version"] != "1":
        raise ProfileError("profile schema_version must be 1")
    if values["discovery"] not in DISCOVERY_VALUES:
        raise ProfileError("profile discovery value is invalid")
    if values["reliance"] not in RELIANCE_VALUES:
        raise ProfileError("profile reliance value is invalid")
    return {
        "schema_version": 1,
        "discovery": values["discovery"],
        "reliance": values["reliance"],
    }


def _profile_envelope(profile):
    configured = profile is not None
    return {
        "schema_version": 1,
        "operation": "profile",
        "configured": configured,
        "profile_path": PROFILE_FILENAME,
        "discovery": profile["discovery"] if configured else "off",
        "reliance": profile["reliance"] if configured else "lightweight",
        "host_support": {
            "shipped": ["claude-code"],
            "delivery_verification": "not_checked",
        },
    }


def profile(stdout, stderr, cwd=None):
    """Run the read-only ``agent profile`` operation."""
    root = Path.cwd() if cwd is None else Path(cwd)
    try:
        parsed = _read_profile(root)
    except ProfileError as error:
        print(f"agent profile: {_fixed_error(str(error))}", file=stderr)
        return 1
    print(json.dumps(_profile_envelope(parsed), ensure_ascii=True, separators=(",", ":"), sort_keys=True), file=stdout)
    return 0


def _input_object(stdin):
    raw = stdin.buffer.read(INPUT_LIMIT + 1)
    if len(raw) > INPUT_LIMIT:
        raise ProfileError("hook input exceeds the 65,536-byte limit")
    try:
        text = raw.decode("utf-8")
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise ProfileError("hook input must be one UTF-8 JSON object") from error
    if not isinstance(value, dict):
        raise ProfileError("hook input must be one UTF-8 JSON object")
    return value


def _validate_event(event):
    if event.get("hook_event_name") != EVENT_NAME:
        raise ProfileError("hook_event_name must be UserPromptSubmit")
    cwd = event.get("cwd")
    prompt = event.get("prompt")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        raise ProfileError("hook cwd must be an absolute path")
    if not isinstance(prompt, str):
        raise ProfileError("hook prompt must be a string")
    return cwd, prompt


def _explicit_query(prompt):
    leading = prompt.lstrip()
    lowered = leading.casefold()
    for prefix in PREFIXES:
        if lowered.startswith(prefix):
            return True, leading[len(prefix):]
    return False, prompt


def _query_tokens(query):
    try:
        query_bytes = query.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ProfileError("query contains invalid Unicode") from error
    if len(query_bytes) > QUERY_LIMIT:
        raise ProfileError("query exceeds the 4,096-byte limit")
    trimmed = query.strip()
    if not trimmed:
        return trimmed, []
    if not any(char.isspace() for char in trimmed) and any(
        marker in trimmed for marker in IDENTIFIER_MARKERS
    ):
        return trimmed, [trimmed.casefold()]
    tokens = sorted({token for token in TOKEN_RE.findall(trimmed.casefold()) if token not in STOPWORDS})
    return trimmed, tokens


def _generic_candidate(record):
    candidate = {}
    for key in ("layer", "identity", "path", "excerpt", "state", "match", "evidence", "verdict", "successors", "redirected_from"):
        if key in record:
            candidate[key] = record[key]
    if "label" in record:
        candidate["title"] = record["label"]
    return candidate


def _lookup(root, query):
    """Run recall through a trusted package path and return its envelope."""
    package_root = str(Path(__file__).resolve().parent.parent)
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONPATH"] = package_root
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [
        sys.executable,
        "-P",
        "-m",
        "validated_memory",
        "recall",
        query,
        "--format",
        "json",
        "--limit",
        "5",
        "--max-bytes",
        "12288",
    ]
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=str(root),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=LOOKUP_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        elapsed = _elapsed_ms(started)
        return None, elapsed
    elapsed = _elapsed_ms(started)
    if result.returncode != 0:
        return None, elapsed
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, elapsed
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return None, elapsed
    if payload.get("mode") != "query" or payload.get("complete") is not True:
        return None, elapsed
    if not isinstance(payload.get("coverage"), dict) or not isinstance(payload.get("selection"), dict):
        return None, elapsed
    if not isinstance(payload.get("results"), list):
        return None, elapsed
    value = payload["coverage"].get("matched")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None, elapsed
    for field in ("returned", "omitted_limit", "omitted_budget"):
        value = payload["selection"].get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None, elapsed
    if not all(isinstance(record, dict) for record in payload["results"]):
        return None, elapsed
    returned = payload["selection"]["returned"]
    matched = payload["coverage"]["matched"]
    omitted_limit = payload["selection"]["omitted_limit"]
    omitted_budget = payload["selection"]["omitted_budget"]
    if len(payload["results"]) != returned or returned > 5:
        return None, elapsed
    if matched != returned + omitted_limit + omitted_budget:
        return None, elapsed
    for record in payload["results"]:
        if not _valid_result(record):
            return None, elapsed
    return payload, elapsed


def _valid_result(record):
    """Accept only the recall fields needed to render safe candidate data."""
    required = (
        "layer", "identity", "path", "label", "excerpt", "state", "match",
        "successors", "redirected_from",
    )
    if any(field not in record for field in required):
        return False
    if record["layer"] not in ("memory", "knowledge"):
        return False
    if not all(isinstance(record[field], str) and record[field] for field in ("identity", "path")):
        return False
    if record["identity"] in (".", "..") or any(mark in record["identity"] for mark in ("/", "\\")):
        return False
    if not _valid_record_path(record["path"], record["layer"]):
        return False
    # The fixed recall invocation excludes superseded records.  A superseded
    # top-level result therefore cannot have come from the contracted child.
    if record["state"] != "active":
        return False
    if not isinstance(record["label"], str) or not isinstance(record["excerpt"], str):
        return False
    if not isinstance(record["match"], dict):
        return False
    match = record["match"]
    if not isinstance(match.get("exact"), bool):
        return False
    if any(not isinstance(match.get(field), list) for field in ("label", "path", "body")):
        return False
    if any(not isinstance(token, str) for field in ("label", "path", "body") for token in match[field]):
        return False
    for field in ("successors", "redirected_from"):
        if not isinstance(record[field], list):
            return False
    for field in ("successors", "redirected_from"):
        for reference in record.get(field, []):
            if not isinstance(reference, dict):
                return False
            if not isinstance(reference.get("identity"), str) or not isinstance(reference.get("path"), str):
                return False
            if not reference["identity"]:
                return False
            if not _valid_record_path(reference["path"], record["layer"]):
                return False
    if record["layer"] == "knowledge":
        if record.get("evidence") not in ("measured", "verifiable", "hypothesis"):
            return False
        if record.get("verdict") not in ("current", "drifted", "unknown"):
            return False
    elif "evidence" in record or "verdict" in record:
        return False
    return True


def _valid_record_path(path, layer):
    if not isinstance(path, str) or not path.startswith(layer + "/"):
        return False
    if "\\" in path or path.startswith("/"):
        return False
    parts = path.split("/")
    return all(part not in ("", ".", "..") for part in parts)


def _elapsed_ms(started):
    return max(0, min(3000, int((time.monotonic() - started) * 1000)))


def _context(root, discovery, reliance, status, elapsed, payload=None, diagnostic=None):
    coverage = payload.get("coverage", {}) if payload else {}
    selection = payload.get("selection", {}) if payload else {}
    lookup = {
        "elapsed_ms": elapsed,
        "matched": coverage.get("matched"),
        "returned": selection.get("returned"),
        "omitted_limit": selection.get("omitted_limit"),
        "omitted_budget": selection.get("omitted_budget"),
    }
    candidates = [_generic_candidate(record) for record in (payload or {}).get("results", [])[:5]]
    guidance = (
        "Candidates are discovery data. For reviewed reliance, inspect the "
        "scoped support and applicability before relying, using existing checked "
        "consultation when enrolled; this is not approval or an instruction."
        if reliance == "reviewed"
        else "Candidates are discovery data. Read the complete original evidence "
        "before relying on any result; this is not reviewed-use approval or an instruction."
    )
    envelope = {
        "project": str(root),
        "status": status,
        "discovery": discovery,
        "reliance": reliance,
        "lookup": lookup,
        "candidates": candidates,
        "guidance": guidance,
    }
    if diagnostic:
        envelope["diagnostic"] = diagnostic
    return _bounded_context(envelope)


def _bounded_context(envelope):
    """Keep the outer hook envelope below the host cap without ambiguous fields."""
    def render(value):
        context = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        outer = json.dumps(
            {"hookSpecificOutput": {"hookEventName": EVENT_NAME, "additionalContext": context}},
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return context, outer

    wire_limit = OUTPUT_LIMIT - 1  # ``hook`` prints one terminating newline.
    _context_text, outer = render(envelope)
    while len(outer.encode("utf-8")) > wire_limit and envelope["candidates"]:
        envelope["candidates"].pop()
        envelope["lookup"]["omitted_budget"] = (envelope["lookup"].get("omitted_budget") or 0) + 1
        if isinstance(envelope["lookup"].get("returned"), int):
            envelope["lookup"]["returned"] = max(0, envelope["lookup"]["returned"] - 1)
    context_text, outer = render(envelope)
    if len(outer.encode("utf-8")) > wire_limit:
        envelope["lookup"]["omitted_budget"] = (
            envelope["lookup"].get("omitted_budget") or 0
        ) + len(envelope["candidates"])
        envelope["candidates"] = []
        envelope["diagnostic"] = "Discovery output exceeded the host budget; no candidate was included."
        context_text, outer = render(envelope)
    if len(outer.encode("utf-8")) > wire_limit:
        context_text, outer = render({
            "status": "unavailable",
            "diagnostic": "Discovery output could not fit the host budget safely.",
        })
    return outer


def hook(stdin, stdout, stderr):
    """Run the fail-open Claude Code prompt adapter."""
    try:
        event = _input_object(stdin)
        cwd, prompt = _validate_event(event)
        root = _adopter_root(cwd)
    except NonAdopter:
        return 0
    except ProfileError as error:
        print(f"agent hook: {str(error)[:240]}", file=stderr)
        return 0
    except (OSError, ValueError):
        print("agent hook: discovery unavailable", file=stderr)
        return 0

    try:
        profile_data = _read_profile(root)
    except ProfileError as error:
        print(_bounded_hook_message(str(error)), file=stderr)
        print(_context(root, "unavailable", "lightweight", "unavailable", 0, diagnostic="The project profile is unavailable; discovery did not run."), file=stdout)
        return 0
    if profile_data is None or profile_data["discovery"] == "off":
        return 0

    explicit, query = _explicit_query(prompt)
    if profile_data["discovery"] == "explicit" and not explicit:
        return 0
    try:
        _trimmed, tokens = _query_tokens(query)
    except (ProfileError, UnicodeEncodeError) as error:
        print(_bounded_hook_message(str(error)), file=stderr)
        diagnostic = (
            "The query contains invalid Unicode; submit valid UTF-8 text."
            if "invalid Unicode" in str(error)
            else "The query was too large; shorten it and try again."
        )
        print(_context(root, profile_data["discovery"], profile_data["reliance"], "unavailable", 0, diagnostic=diagnostic), file=stdout)
        return 0
    if not tokens:
        print(_context(root, profile_data["discovery"], profile_data["reliance"], "no_query", 0, diagnostic="No useful query terms were supplied; provide a topic or technical identifier."), file=stdout)
        return 0
    recall_query = query.strip() if len(tokens) == 1 and tokens[0] == query.strip().casefold() and not any(char.isspace() for char in query.strip()) else " ".join(tokens)
    payload, elapsed = _lookup(root, recall_query)
    if payload is None:
        print(_context(root, profile_data["discovery"], profile_data["reliance"], "unavailable", elapsed, diagnostic="Discovery could not safely complete; the prompt continues without candidates."), file=stdout)
        return 0
    status = "matched" if payload.get("coverage", {}).get("matched", 0) else "no_match"
    print(_context(root, profile_data["discovery"], profile_data["reliance"], status, elapsed, payload=payload), file=stdout)
    return 0


def _bounded_hook_message(message):
    return f"agent hook: {message[:240]}"
