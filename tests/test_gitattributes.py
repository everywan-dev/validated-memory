"""Structural checks over the repository's root `.gitattributes`.

Same seam as `test_hooks_manifest.py` and `test_skills_structure.py`: this
reads a file the repository ships and never imports the package's internals.

`journal.jsonl` is always versioned (ADR 0008) and strictly append-only, so
two clones of an adopted repository conflict on it as a matter of routine,
and a conflicted journal is one the CLI refuses to parse -- which gates
`init` every session until someone repairs it by hand. What is pinned here
is the effect rather than the spelling of the entry: git itself is asked
which merge driver the path resolves to, and a two-sided merge is then run
over a copy of the shipped file.
"""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GITATTRIBUTES = REPO_ROOT / ".gitattributes"

# An explicit identity, so the fixture repository never depends on the global
# git configuration (the convention `test_git_ref_probe.py` already follows).
_GIT_IDENTITY_ENV = {
    "GIT_AUTHOR_NAME": "attributes",
    "GIT_AUTHOR_EMAIL": "attributes@test",
    "GIT_COMMITTER_NAME": "attributes",
    "GIT_COMMITTER_EMAIL": "attributes@test",
}


def _git(cwd, *args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **_GIT_IDENTITY_ENV},
        check=check,
        capture_output=True,
        text=True,
    )


def test_the_repository_ships_a_root_gitattributes():
    assert GITATTRIBUTES.is_file()


def _governed_by_the_shipped_file(tmp_path):
    """A repository whose only attributes are the ones this repo ships.

    Asking `check-attr` in `REPO_ROOT` proves less than it appears to: with
    the working-tree file deleted git answers from the index, so the test
    passes on a file that is no longer there. Copying the shipped bytes into
    a fresh repository makes the answer depend on them and nothing else.
    """
    repository = tmp_path / "governed"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    (repository / ".gitattributes").write_text(
        GITATTRIBUTES.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return repository


def test_the_journal_resolves_to_the_union_merge_driver(tmp_path):
    repository = _governed_by_the_shipped_file(tmp_path)
    result = _git(repository, "check-attr", "merge", "--", "journal.jsonl")
    assert result.stdout.strip() == "journal.jsonl: merge: union"


def test_the_journal_pattern_is_not_anchored_to_the_repository_root(tmp_path):
    # An adopter root is not always a repository root, so the entry has to
    # match the file wherever the adopted project sits.
    repository = _governed_by_the_shipped_file(tmp_path)
    result = _git(
        repository, "check-attr", "merge", "--", "sub/project/journal.jsonl"
    )
    assert result.stdout.strip() == "sub/project/journal.jsonl: merge: union"


def test_two_clones_appending_to_one_journal_merge_without_conflict_markers(
    tmp_path,
):
    """The guarantee the entry exists for, run against the shipped file."""
    journal = tmp_path / "journal.jsonl"
    (tmp_path / ".gitattributes").write_text(
        GITATTRIBUTES.read_text(encoding="utf-8"), encoding="utf-8"
    )
    journal.write_text('{"record": "base"}\n', encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", ".gitattributes", "journal.jsonl")
    _git(tmp_path, "commit", "-q", "-m", "base")

    _git(tmp_path, "checkout", "-q", "-b", "alice")
    with journal.open("a", encoding="utf-8") as handle:
        handle.write('{"record": "alice"}\n')
    _git(tmp_path, "commit", "-q", "-am", "alice")

    _git(tmp_path, "checkout", "-q", "-b", "bob", "main")
    with journal.open("a", encoding="utf-8") as handle:
        handle.write('{"record": "bob"}\n')
    _git(tmp_path, "commit", "-q", "-am", "bob")

    merge = _git(tmp_path, "merge", "--no-edit", "alice", check=False)

    assert merge.returncode == 0, merge.stdout + merge.stderr
    lines = journal.read_text(encoding="utf-8").splitlines()
    assert lines == [
        '{"record": "base"}',
        '{"record": "bob"}',
        '{"record": "alice"}',
    ]
