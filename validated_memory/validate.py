"""The `validate` subcommand: curated-knowledge units against the contract."""

from pathlib import Path

from . import extension as extension_module
from .contract import ERROR, WARNING, Finding, validate_documents

DEFAULT_KNOWLEDGE_DIR = "knowledge"
UNIT_SUFFIX = ".md"

EXIT_OK = 0
EXIT_ERROR = 1


def run(path, stdout, stderr):
    """Validate every unit under `path` and report findings. Returns an exit code."""
    try:
        extension = extension_module.load(Path())
    except extension_module.ExtensionError as error:
        # An extension that cannot be loaded stops the run: validating units
        # against the base contract alone would report a pass the adopter did
        # not ask for.
        documents = []
        findings = [
            Finding(ERROR, error.location, error.field, error.message, line=error.line)
        ]
    else:
        target = Path(path) if path else Path(DEFAULT_KNOWLEDGE_DIR)
        documents, findings = _collect(target, explicit=bool(path))
        findings.extend(validate_documents(documents, extension))

    errors = [finding for finding in findings if finding.severity == ERROR]
    warnings = [finding for finding in findings if finding.severity == WARNING]
    for finding in findings:
        print(finding.render(), file=stderr)
    print(
        f"validate: {len(documents)} unit(s) checked, "
        f"{len(errors)} error(s), {len(warnings)} warning(s)",
        file=stdout,
    )
    return EXIT_ERROR if errors else EXIT_OK


def _collect(target, explicit):
    location = target.as_posix()
    if not target.exists():
        if explicit:
            message = "no such file or directory"
        else:
            message = (
                f"no curated-knowledge directory found at '{location}'; pass a PATH "
                "or run 'validated-memory init'"
            )
        return [], [Finding(ERROR, location, "target", message)]

    if target.is_dir():
        files = sorted(
            path for path in target.rglob(f"*{UNIT_SUFFIX}") if path.is_file()
        )
        if not files:
            return [], [
                Finding(
                    WARNING,
                    location,
                    "target",
                    f"no curated-knowledge units (*{UNIT_SUFFIX}) found",
                )
            ]
    else:
        files = [target]

    documents = []
    findings = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            findings.append(
                Finding(ERROR, path.as_posix(), "file", f"cannot be read: {error}")
            )
            continue
        documents.append((path.as_posix(), text))
    return documents, findings
