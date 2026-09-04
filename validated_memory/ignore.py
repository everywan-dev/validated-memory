"""Keeping the vault out of the repository: one line in the ignore file.

`init` writes `/.validated-memory/` into `.gitignore`. That entry is not part
of the adoption questionnaire and never was -- ADR 0008 makes the vault local
by construction rather than by the adopter answering a question one way -- so
it is written on every run, whatever the project versions. It is written
once: a file that already carries the rule in any of its usual spellings is
left exactly as it is, and one that does not exist is created.

Four shapes are left alone rather than written to: a file that already
carries the rule, one that cannot be read, one that is a symlink --
installing over a symlink replaces the link itself, and destroying a link to
record an ignore rule is not a trade `init` may make -- and one whose mode
denies writing to this user, which the executor refuses before anything is
prepared.

The unreadable one and the symlinked one only gate when the vault is really
exposed, which is not the same question. `.git/info/exclude` ignores the same
paths, is never versioned, and -- because git will not read an ignore file it
cannot open either -- is the highest-precedence source git still consults in
exactly these two shapes, so a rule there settles it and the run goes on.

What a symlinked ignore file points at is deliberately not read as if it were
the rule: git does not follow one (measured on git 2.43, which reports
"unable to access '.gitignore': Too many levels of symbolic links" and leaves
the paths untracked), so believing the target would call an exposed vault
ignored.

This module decides and writes; it does not report. `write_entry` hands its
executor `Outcome` back rather than reading it, because whether a `noop` is
possible is a fact about the caller's own preconditions and not about the
ignore file (`init._refusal`), and nothing here prints: `init` owns the run's
narration and prints every line of it from one place.
"""

from pathlib import Path

from . import journal

FILENAME = ".gitignore"
# The clone's own ignore file: git reads it, no commit can carry it, and it
# is read here only to answer "is the vault ignored anyway?" -- never written
# to. A `.git` that is a file rather than a directory (a worktree, a
# submodule) puts it somewhere this does not look, so it reads as empty and
# the run gates: unsure is the side to be wrong on.
EXCLUDE_PATH = Path(".git") / "info" / "exclude"
ENTRY = f"/{journal.VAULT_DIRNAME}/"
BLOCK = f"""\
# The validated-memory vault: preimages, and the records of mutations whose
# path leaves the repository. Always local to this clone (ADR 0008), which is
# why `init` writes this entry itself rather than the adoption questionnaire
# asking for it.
{ENTRY}
"""

# Forms of the same rule a hand-written ignore file may already carry --
# including the one the questionnaire's "local" answers write. `init` adds
# nothing when any of them is already there: it writes the entry once, it
# does not keep the file in a shape of its own choosing.
EQUIVALENTS = {
    ENTRY,
    ENTRY.rstrip("/"),
    ENTRY.lstrip("/"),
    ENTRY.strip("/"),
}


def write_entry(session):
    """Try to put the vault's entry in the ignore file. Returns `(reason, outcome)`.

    At most one of the two is set. `(None, None)` means the rule is already
    in the file and nothing was attempted. A `reason` is why the entry could
    not be added without an executor ever being asked -- a symlink, an
    unreadable file -- and it is NOT yet a finding, because the caller asks
    `ignored_elsewhere` next and a rule there settles the question. An
    `outcome` is the executor's, for the caller to read: whether a `noop`
    from it is possible depends on the caller's own preconditions.

    The two shapes the entry can be added to get two different intentions,
    because their inverses differ and only the record says which was done: an
    ignore file that is not there at all is a `create` of the whole block,
    whose inverse is removing a file `init` made; one that is already there
    is an `append` of the addition alone, whose inverse is truncating the
    adopter's own file back to the length it had. The expected state is
    stated, not read on the caller's behalf: the digest of the bytes just
    read, so an ignore file rewritten between the read and the write is
    refused rather than appended to blind.

    A read-only ignore file lands in the outcome: mode 0444 denies writing to
    this user and the executor refuses before anything is prepared. Writing
    to a file the adopter marked read-only is not an option this method
    takes.
    """
    path = Path(FILENAME)
    if path.is_symlink():
        return (
            f"the vault's ignore entry ({ENTRY}) is missing and cannot be "
            "added: the ignore file is a symlink, and writing it would "
            "replace the link. Add the entry by hand."
        ), None
    try:
        raw = path.read_bytes() if path.exists() else b""
        existing = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return (
            f"the ignore file could not be read, so the vault's entry "
            f"({ENTRY}) could not be added: {error}"
        ), None
    if carries_entry(existing):
        return None, None
    addition = _addition(existing)
    if path.exists():
        intention = journal.append_to_file(
            purpose="ignore-rule",
            path=FILENAME,
            durability=journal.REPO,
            expected={"kind": journal.FILE, "digest": journal.digest(raw)},
            content=addition.encode("utf-8"),
        )
    else:
        intention = journal.create_file(
            purpose="ignore-rule",
            path=FILENAME,
            durability=journal.REPO,
            content=addition.encode("utf-8"),
        )
    return None, session.execute(intention)


def ignored_elsewhere():
    """Whether `.git/info/exclude` already ignores the vault."""
    return carries_entry(_read_text(EXCLUDE_PATH))


def carries_entry(text):
    """Say whether `text` already carries the rule, in any of its spellings.

    Read line by line rather than matched the way git matches: this only ever
    decides whether `init` has anything to add, and the reader git would need
    is a gitignore engine.
    """
    return any(line.strip() in EQUIVALENTS for line in text.splitlines())


def _read_text(path):
    """`path`'s text, or empty when it cannot be read: a file saying nothing."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _addition(existing):
    """The block to append, separated from whatever the file already holds."""
    prefix = ""
    if existing and not existing.endswith("\n"):
        prefix = "\n"
    if existing.strip():
        prefix += "\n"
    return prefix + BLOCK
