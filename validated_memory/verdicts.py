"""The append-only verdict log: `probe` writes it, `derive` reads it.

Each `probe` run appends one JSON line per anchor probed to `verdicts.jsonl`
in the working directory -- never under `knowledge/`, for the same reason
`knowledge-index.md` lives outside it (see `derive`). History is never
rewritten: the log only grows. The service view a reader wants is the latest
record per `(unit, system, kind)`, computed here rather than stored, since
storing it would mean rewriting the log on every probe.
"""

import json
from pathlib import Path

LOG_FILENAME = "verdicts.jsonl"

CURRENT = "current"
DRIFTED = "drifted"
UNKNOWN = "unknown"
VERDICTS = (CURRENT, DRIFTED, UNKNOWN)

# Worst-first severity for aggregation: drifted outranks unknown outranks
# current.
_SEVERITY = {DRIFTED: 2, UNKNOWN: 1, CURRENT: 0}


def worst(verdicts):
    """The most severe verdict among `verdicts` (drifted > unknown > current)."""
    return max(verdicts, key=lambda verdict: _SEVERITY[verdict])


def append(records, root=Path()):
    """Append `records` to the log, one JSON line each. Never rewrites."""
    path = Path(root) / LOG_FILENAME
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def service_view(root=Path()):
    """The latest verdict per `(unit, system, kind)`, or `{}` if never probed."""
    path = Path(root) / LOG_FILENAME
    if not path.exists():
        return {}
    view = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        key = (record["unit"], record["system"], record["kind"])
        view[key] = record["verdict"]
    return view
