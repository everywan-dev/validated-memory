"""The base contract every curated-knowledge unit must satisfy.

The contract fixes identity, evidence state, supersession and the anchor
envelope. Anything outside it is an error here: adopter-specific fields belong
to a declared extension, validated separately.
"""

import datetime
import re

from .frontmatter import FrontmatterError, parse

ERROR = "ERROR"
WARNING = "WARNING"

EVIDENCE_STATES = ("measured", "verifiable", "hypothesis")
BASE_FIELDS = ("id", "evidence", "supersedes", "anchors", "provenance")
ANCHOR_FIELDS = ("system", "kind", "captured_at", "payload")

ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ISO_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2})"
    r"(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?$"
)


class Finding:
    """One reportable observation about one unit.

    `line` is set only when the finding has one: the parser knows where it
    stopped, while a contract rule usually speaks about the unit as a whole.
    """

    __slots__ = ("field", "line", "location", "message", "severity")

    def __init__(self, severity, location, field, message, line=None):
        self.severity = severity
        self.location = location
        self.field = field
        self.message = message
        self.line = line

    def render(self):
        where = self.location if self.line is None else f"{self.location}:{self.line}"
        return f"{self.severity}: {where}: {self.field}: {self.message}"


def validate_documents(documents, extension=None):
    """Validate `(location, text)` documents against the contract.

    Returns every finding, in document order. Parsing and per-unit rules run
    first; supersession is resolved afterwards, once every declared id is
    known. `extension` carries the adopter's declared fields, if any.
    """
    findings = []
    units = []
    for location, text in documents:
        try:
            data = parse(text)
        except FrontmatterError as error:
            findings.append(
                Finding(
                    ERROR, location, "frontmatter", error.message, line=error.lineno
                )
            )
            continue
        units.append((location, data))

    declared = {}
    for location, data in units:
        findings.extend(_check_unit(location, data, extension))
        unit_id = data.get("id")
        if not _is_valid_id(unit_id):
            continue
        if unit_id in declared:
            findings.append(
                Finding(
                    ERROR,
                    location,
                    "id",
                    f"duplicate id '{unit_id}', already declared by {declared[unit_id]}",
                )
            )
        else:
            declared[unit_id] = location

    for location, data in units:
        findings.extend(_check_supersedes(location, data, declared))
    return findings


def _check_unit(location, data, extension=None):
    extension_names = extension.names if extension else ()
    findings = []
    for field in data:
        if field in BASE_FIELDS or field in extension_names:
            continue
        message = "unknown field; the base contract declares " + ", ".join(BASE_FIELDS)
        if extension_names:
            message += " and the declared extension adds " + ", ".join(extension_names)
        findings.append(Finding(ERROR, location, field, message))

    findings.extend(_check_id(location, data))
    findings.extend(_check_evidence(location, data))
    findings.extend(_check_supersedes_shape(location, data))
    findings.extend(_check_anchors(location, data))
    findings.extend(_check_provenance(location, data))
    findings.extend(_check_extension_fields(location, data, extension))
    return findings


def _check_extension_fields(location, data, extension):
    """Apply the adopter's declared fields to a unit that carries them."""
    if extension is None:
        return []
    findings = []
    for name, declaration in extension.fields.items():
        if name not in data:
            continue
        value = data[name]
        if not isinstance(value, str):
            findings.append(
                Finding(
                    ERROR, location, name, f"{_describe(value)} is not a scalar value"
                )
            )
            continue
        if declaration["type"] != "enum":
            continue
        values = declaration["values"]
        if value not in values:
            findings.append(
                Finding(
                    ERROR,
                    location,
                    name,
                    f"{_describe(value)} is not one of " + ", ".join(values),
                )
            )
    return findings


def _check_id(location, data):
    if "id" not in data:
        return [Finding(ERROR, location, "id", "required field is missing")]
    unit_id = data["id"]
    if not _is_valid_id(unit_id):
        return [
            Finding(
                ERROR,
                location,
                "id",
                f"{_describe(unit_id)} is not a valid id; expected a non-empty "
                "string of letters, digits, '.', '_' or '-'",
            )
        ]
    return []


def _check_evidence(location, data):
    if "evidence" not in data:
        return [Finding(ERROR, location, "evidence", "required field is missing")]
    evidence = data["evidence"]
    if evidence not in EVIDENCE_STATES:
        return [
            Finding(
                ERROR,
                location,
                "evidence",
                f"{_describe(evidence)} is not one of "
                + ", ".join(EVIDENCE_STATES),
            )
        ]
    return []


def _check_supersedes_shape(location, data):
    if "supersedes" not in data:
        return []
    supersedes = data["supersedes"]
    if not isinstance(supersedes, list):
        return [
            Finding(
                ERROR,
                location,
                "supersedes",
                f"{_describe(supersedes)} is not a list of ids",
            )
        ]
    findings = []
    for entry in supersedes:
        if not _is_valid_id(entry):
            findings.append(
                Finding(
                    ERROR,
                    location,
                    "supersedes",
                    f"{_describe(entry)} is not a valid id",
                )
            )
    return findings


def _check_supersedes(location, data, declared):
    supersedes = data.get("supersedes")
    if not isinstance(supersedes, list):
        return []
    unit_id = data.get("id")
    findings = []
    for entry in supersedes:
        if not _is_valid_id(entry):
            continue
        if entry == unit_id:
            findings.append(
                Finding(
                    ERROR, location, "supersedes", f"unit '{entry}' supersedes itself"
                )
            )
        elif entry not in declared:
            findings.append(
                Finding(
                    ERROR,
                    location,
                    "supersedes",
                    f"superseded id '{entry}' does not exist in the validated set",
                )
            )
    return findings


def _check_anchors(location, data):
    if "anchors" not in data:
        return [
            Finding(
                WARNING,
                location,
                "anchors",
                "unit declares no anchors: it cannot be checked for freshness",
            )
        ]
    anchors = data["anchors"]
    if not isinstance(anchors, list):
        return [
            Finding(
                ERROR, location, "anchors", f"{_describe(anchors)} is not a list"
            )
        ]
    if not anchors:
        return [
            Finding(
                WARNING,
                location,
                "anchors",
                "unit declares no anchors: it cannot be checked for freshness",
            )
        ]

    findings = []
    for position, anchor in enumerate(anchors):
        field = f"anchors[{position}]"
        if not isinstance(anchor, dict):
            findings.append(
                Finding(ERROR, location, field, f"{_describe(anchor)} is not a mapping")
            )
            continue
        for name in ANCHOR_FIELDS:
            if name not in anchor:
                findings.append(
                    Finding(
                        ERROR,
                        location,
                        field,
                        f"incomplete anchor envelope: '{name}' is missing",
                    )
                )
        for name in anchor:
            if name not in ANCHOR_FIELDS:
                findings.append(
                    Finding(
                        ERROR,
                        location,
                        field,
                        f"unknown anchor field '{name}'; the envelope is "
                        + ", ".join(ANCHOR_FIELDS),
                    )
                )
        findings.extend(_check_anchor_values(location, field, anchor))
    return findings


def _check_anchor_values(location, field, anchor):
    findings = []
    for name, accepts, expectation in ANCHOR_VALUE_RULES:
        if name in anchor and not accepts(anchor[name]):
            findings.append(
                Finding(
                    ERROR,
                    location,
                    f"{field}.{name}",
                    f"{_describe(anchor[name])} is not {expectation}",
                )
            )
    return findings


def _check_provenance(location, data):
    if "provenance" not in data:
        return []
    provenance = data["provenance"]
    if not isinstance(provenance, list):
        return [
            Finding(
                ERROR,
                location,
                "provenance",
                f"{_describe(provenance)} is not a list; provenance is separate "
                "from anchors",
            )
        ]
    return []


def _is_valid_id(value):
    return isinstance(value, str) and bool(ID_PATTERN.match(value))


def _is_non_empty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _is_probe_kind(value):
    """A probe kind is the dispatch discriminator, so it carries no whitespace."""
    return _is_non_empty_string(value) and not any(
        character.isspace() for character in value
    )


def _is_iso8601(value):
    if not isinstance(value, str):
        return False
    match = ISO_PATTERN.match(value)
    if not match:
        return False
    date_part = match.group(1)
    try:
        datetime.date.fromisoformat(date_part)
    except ValueError:
        return False
    time_part = value[len(date_part) :]
    if not time_part:
        return True
    time_part = re.sub(r"\.\d+", "", time_part)
    if time_part.endswith("Z"):
        time_part = time_part[:-1] + "+00:00"
    try:
        datetime.datetime.fromisoformat(date_part + time_part)
    except ValueError:
        return False
    return True


# What each field of the anchor envelope accepts, and how to name the
# expectation when it does not. The envelope's shape is checked apart.
ANCHOR_VALUE_RULES = (
    ("system", _is_non_empty_string, "a system name"),
    ("kind", _is_probe_kind, "a probe kind (a name without whitespace)"),
    ("captured_at", _is_iso8601, "an ISO-8601 date or timestamp"),
    ("payload", lambda value: isinstance(value, dict), "a mapping"),
)


def _describe(value):
    if isinstance(value, str):
        return f"'{value}'"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a mapping"
    return "a missing value"
