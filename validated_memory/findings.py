"""The shared finding type: one reportable observation, ERROR or WARNING.

Both `validate` (curated knowledge) and `lint` (agent memory) report the same
shape of finding, so it lives here rather than under either enforcement
module.
"""

ERROR = "ERROR"
WARNING = "WARNING"


class Finding:
    """One reportable observation about one document.

    `line` is set only when the finding has one: the parser knows where it
    stopped, while a document-level rule usually speaks about the document as
    a whole.
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
