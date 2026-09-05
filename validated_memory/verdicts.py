"""The append-only verdict log: `probe` writes it, `derive` reads it.

Each `probe` run appends one JSON line per anchor probed to `verdicts.jsonl`
in the working directory -- never under `knowledge/`, for the same reason
`knowledge-index.md` lives outside it (see `derive`). History is never
rewritten: the log only grows. The service view a reader wants is the latest
record per anchor -- an anchor being what its `(system, kind, payload)` names,
see `anchor_key` -- computed here rather than stored, since storing it would
mean rewriting the log on every probe.

A reader calls `read` once and takes the projections it needs off the
`LogSnapshot` it gets back.
"""

import json
from pathlib import Path

LOG_FILENAME = "verdicts.jsonl"


class VerdictLogError(Exception):
    """Raised when the log cannot be read as verdict records.

    A reader must never guess about a log it cannot parse: a truncated write
    or a hand edit is reported with its line, fail-loud, instead of serving
    verdicts computed from a record that was silently skipped.

    `lineno` is None when the fault is the file's rather than a line's -- it
    could not be opened or decoded at all.
    """

    def __init__(self, lineno, message):
        super().__init__(message)
        self.lineno = lineno
        self.message = message

CURRENT = "current"
DRIFTED = "drifted"
UNKNOWN = "unknown"
VERDICTS = (CURRENT, DRIFTED, UNKNOWN)

# The order a unit is graded by, published in `docs/reference/cli.md#derive`:
# reordering it changes what the index and `status` say about every unit
# whose anchors disagree.
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


KEY_FIELDS = ("unit", "system", "kind")

# The key a record written before payloads were recorded lands under. Such a
# record is never read by an anchor: the log cannot say which anchor it was
# about, nor what that anchor pointed at when it was written. Attributing it
# would risk reporting `current` for something that has since drifted, which
# is the failure the payload was added to prevent. It is kept, because the log
# is history and history is not rewritten, and it is ignored.
NO_PAYLOAD = None


def anchor_key(unit_id, system, kind, payload):
    """The key one anchor's verdicts are recorded and read under.

    An anchor is identified by what it points at -- its system, its kind and
    its payload. `captured_at` dates a capture, it does not identify one, and
    the payload is part of the key because one unit may declare distinct
    anchors sharing a `(system, kind)`: two refs of the same repository, both
    `git_ref` on the same system.
    """
    return (
        unit_id, system, kind,
        NO_PAYLOAD if payload is None else canonical_payload(payload),
    )


def canonical_payload(payload):
    """Return JSON for anchor keys and displays: sorted mapping keys, ordered lists.

    Preserve default JSON spacing and ASCII escapes; these bytes identify anchors.
    """
    return json.dumps(payload, sort_keys=True)


def _records(root):
    """Yield `(lineno, record)` for every line of the log, fail-loud.

    Reading the file is part of the promise: a log that cannot be opened or
    decoded is a log that cannot be read, and letting an `OSError` or a
    `UnicodeDecodeError` past here would surface as a traceback rather than
    as the finding `read` promises.
    """
    path = Path(root) / LOG_FILENAME
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise VerdictLogError(None, f"cannot be read: {error}") from error
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise VerdictLogError(
                lineno, f"not a JSON record: {error.msg}"
            ) from error
        if not isinstance(record, dict):
            raise VerdictLogError(lineno, "record is not a JSON object")
        yield lineno, record


def _keyed(lineno, record):
    """Return `(key, verdict)` for one record, or raise `VerdictLogError`.

    The key fields are checked to be strings before they are used as one: a
    hand-edited log can carry a list there, and an unhashable value reaching
    the dictionary raises `TypeError` instead of naming the line at fault.
    """
    try:
        fields = tuple(record[field] for field in KEY_FIELDS)
        verdict = record["verdict"]
    except KeyError as error:
        raise VerdictLogError(lineno, f"record is missing the {error} field") from error
    for field, value in zip(KEY_FIELDS, fields):
        if not isinstance(value, str):
            raise VerdictLogError(lineno, f"the '{field}' field is not a string")
    # Presence, not value: only an absent field means "written before
    # payloads were recorded". An explicit `null` is a malformed record, and
    # letting it pass would file it under the same key as an absent one.
    payload = record.get("payload")
    if "payload" in record and not isinstance(payload, dict):
        raise VerdictLogError(lineno, "the 'payload' field is not a mapping")
    if verdict not in VERDICTS:
        raise VerdictLogError(
            lineno,
            f"'{verdict}' is not one of " + ", ".join(VERDICTS),
        )
    return anchor_key(*fields, payload), verdict


class LogSnapshot:
    """One validated reading of the log, and every projection of it.

    The three projections come from the same bytes and the same pass, so a
    reader that needs more than one of them neither reads the file twice nor
    has to know which call is the one that validates.

    - `records`: every record, in file order, uncollapsed. Windowing is the
      renderer's business, not this module's: truncating here would decide
      for every consumer at once.
    - `latest`: the latest full record per anchor. A caller needing a field
      the verdict alone does not carry -- `status`'s age check needs
      `recorded_at` -- reads this.
    - `view`: the latest verdict per anchor, which is what grades a unit.
    """

    __slots__ = ("latest", "records", "view")

    def __init__(self, records, latest):
        self.records = records
        self.latest = latest
        self.view = {key: record["verdict"] for key, record in latest.items()}


def read(root=Path()):
    """Read and validate the whole log once. Returns a `LogSnapshot`.

    Every record is checked, whichever projection the caller goes on to use:
    a malformed one is refused here rather than by whichever call happened to
    be made first. Never probed, or no log at all, is an empty snapshot.

    Raises `VerdictLogError` on a log that cannot be read as records.
    """
    collected = []
    latest = {}
    for lineno, record in _records(root):
        key, _verdict = _keyed(lineno, record)
        latest[key] = record
        collected.append(record)
    return LogSnapshot(tuple(collected), latest)
