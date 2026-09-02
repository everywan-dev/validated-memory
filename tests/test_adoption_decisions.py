"""The adoption decision: what the adopter repository versions (ADR 0007).

`adopt-validated-memory` asks, before `init`, whether the layout is versioned
in the adopter repository or kept local to the clone. The "local" answers
write an ignore list, and that list is pinned here against the CLI's fixed
root outputs that the ignore question actually covers -- every item `init`
(with and without `--view`), `derive` and `probe` write at the adopter's root
on a normal run, minus `journal.jsonl` -- so a new root artifact cannot
appear without the skill and the adoption guide learning to ignore it, and a
stale entry cannot linger after one is retired.
Not covered, on purpose: `journal.jsonl`, which `init` also writes at the
root but which ADR 0008 keeps outside this question entirely -- it is always
versioned and the skill deliberately never offers to ignore it, unlike
`.validated-memory/`, the vault half of that same split, which is always
ignored and so is always in the list; the `--harness-memory` side effects,
which live at PATH rather than in the project; and the temporary files
`render` leaves only after a hard kill.

The list is read from the skill and the guide as data (the same seam as
`test_skills_structure.py`); the artifacts come from driving the CLI as a
subprocess, never from the package.

The vault's own entry is not one of the answers, so what `init` does when it
cannot write that entry is pinned here too -- including through the real
`SessionStart` hook (`bash hooks/restore-memory-symlink.sh`), because the
gate's whole cost is measured on the one job that hook exists for.
"""

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ADOPT_SKILL = REPO_ROOT / "skills" / "adopt-validated-memory" / "SKILL.md"
ADOPTION_GUIDE = REPO_ROOT / "docs" / "adoption.md"
RESTORE_HOOK = REPO_ROOT / "hooks" / "restore-memory-symlink.sh"


def _git_version():
    """The local git's version, as a tuple, for the one fact that depends on it."""
    text = subprocess.run(
        ["git", "--version"], capture_output=True, text=True, check=True
    ).stdout
    numbers = re.search(r"(\d+)\.(\d+)", text)
    return tuple(int(part) for part in numbers.groups()) if numbers else (0, 0)

# The ignore list is the one fenced block whose first line is this comment;
# the entries are its remaining non-blank, non-comment lines.
IGNORE_LIST_MARKER = "# validated-memory layout, local to this clone"

CURRENT_PROBE = """\
import sys, json
sys.stdin.read()
print(json.dumps({"verdict": "current"}))
"""

# What `adopt.take_over` recognizes as the harness's own agent memory: the
# one shape whose absorption MOVES a directory the user owns.
HARNESS_MEMORY = """\
---
name: coffee-preference
description: Prefers oat milk.
metadata:
  type: user
---

Body.
"""

ANCHORED_UNIT = """\
---
id: kb-0001
evidence: measured
anchors:
  - system: repo-a
    kind: git_ref
    captured_at: 2026-08-01T00:00:00Z
    payload: {}
---

Unit body.
"""


def _fenced_blocks(text):
    """Yield the body lines of every fenced code block, read line by line.

    A fence opens on a line that, stripped, starts with ``` (an info string
    may follow) and closes on a line that, stripped, is exactly ```; an
    inline triple backtick or a fence that never closes is not a block.
    """
    body = None
    for line in text.splitlines():
        stripped = line.strip()
        if body is None:
            if stripped.startswith("```"):
                body = []
        elif stripped == "```":
            yield body
            body = None
        else:
            body.append(stripped)


def _ignore_entries(path):
    text = path.read_text(encoding="utf-8")
    blocks = [
        block
        for block in _fenced_blocks(text)
        if block and block[0] == IGNORE_LIST_MARKER
    ]
    assert len(blocks) == 1, (
        f"{path} must carry exactly one fenced block opening with "
        f"{IGNORE_LIST_MARKER!r}; found {len(blocks)}"
    )
    return {line for line in blocks[0] if line and not line.startswith("#")}


def _root_artifacts(adopter_dir, tmp_path_factory, run_cli):
    """Everything the CLI writes at the adopter's root, as ignore entries."""
    for args in (("init",), ("init", "--view")):
        result = run_cli(*args, cwd=adopter_dir)
        assert result.returncode == 0, result.stderr

    # A fake probe, kept outside the adopter tree so the tree holds only what
    # the CLI itself wrote; registered in place of the bundled git_ref probe.
    helper = tmp_path_factory.mktemp("probe-helper") / "current_probe.py"
    helper.write_text(CURRENT_PROBE, encoding="utf-8")
    # Quoted as `probe` splits it (shlex), so an interpreter path with a
    # space survives.
    command = shlex.join([sys.executable, helper.as_posix()])
    (adopter_dir / "validated-memory.md").write_text(
        f"---\nprobes:\n  git_ref: {command}\n---\n",
        encoding="utf-8",
    )
    (adopter_dir / "knowledge" / "kb-0001.md").write_text(
        ANCHORED_UNIT, encoding="utf-8"
    )
    for command in ("probe", "derive"):
        result = run_cli(command, cwd=adopter_dir)
        assert result.returncode == 0, result.stderr

    return {
        f"/{entry.name}/" if entry.is_dir() else f"/{entry.name}"
        for entry in adopter_dir.iterdir()
        # `journal.jsonl` and `.gitignore` are root artifacts too, and both
        # are outside this question on purpose: the journal because ADR 0008
        # keeps it always versioned and never offers to ignore it, and the
        # ignore file because it is where the answer is written -- a list
        # that ignored the file carrying it would ignore itself. See the
        # module docstring's "Not covered, on purpose".
        if entry.name not in ("journal.jsonl", ".gitignore")
    }


# --- the one entry that is not a question (ADR 0008) --------------------------

_GIT_IDENTITY_ENV = {
    "GIT_AUTHOR_NAME": "adoption",
    "GIT_AUTHOR_EMAIL": "adoption@test",
    "GIT_COMMITTER_NAME": "adoption",
    "GIT_COMMITTER_EMAIL": "adoption@test",
}


def _git(repo_dir, *args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        env={**os.environ, **_GIT_IDENTITY_ENV},
        check=check,
        capture_output=True,
        text=True,
    )


def _fixture_repo(repo_dir):
    """A one-commit git repository, the shape a real adopter runs `init` in."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git(repo_dir, "init", "-q", "-b", "main")
    (repo_dir / "file.txt").write_text("hello\n", encoding="utf-8")
    _git(repo_dir, "add", "file.txt")
    _git(repo_dir, "commit", "-q", "-m", "first commit")
    return repo_dir


def test_the_vault_is_ignored_and_the_journal_is_not(tmp_path, run_cli):
    """The default answer is "versioned", and the vault is ignored anyway.

    ADR 0008 splits durability precisely so the preimages and the
    out-of-root records never travel, and says the entry is `init`'s to
    write rather than the questionnaire's, "because it is not a choice".
    Measured before this test existed: on a default adoption in a real
    repository, `git status` showed `?? .validated-memory/` and
    `git check-ignore` exited 1, with the user's harness path inside the
    untracked file.

    The vault only fills on the first `init --harness-memory`, which is what
    the `SessionStart` hook runs -- so this drives that invocation rather
    than a bare `init`, which is why the gap reached production without a
    fixture ever showing it.
    """
    adopter = _fixture_repo(tmp_path / "repo")
    harness_memory = tmp_path / "harness" / "memory"

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter
    )

    assert result.returncode == 0, result.stderr
    vault_record = ".validated-memory/local.jsonl"
    assert (adopter / vault_record).is_file()
    assert _git(adopter, "check-ignore", "-q", vault_record, check=False).returncode == 0
    assert _git(adopter, "check-ignore", "-q", "journal.jsonl", check=False).returncode == 1
    status = _git(adopter, "status", "--porcelain", "-uall").stdout
    assert ".validated-memory" not in status, status
    assert "journal.jsonl" in status, status


def test_the_ignore_entry_is_written_once_and_never_duplicated(tmp_path, run_cli):
    """`init` is re-runnable at every session start, so it must not grow the file."""
    adopter = _fixture_repo(tmp_path / "repo")
    (adopter / ".gitignore").write_text("build/\n", encoding="utf-8")

    for _ in range(3):
        assert run_cli("init", cwd=adopter).returncode == 0

    ignore = (adopter / ".gitignore").read_text(encoding="utf-8")
    assert ignore.startswith("build/\n"), ignore
    assert ignore.count("/.validated-memory/") == 1, ignore


def test_an_ignore_file_init_must_not_replace_gates_and_is_left_alone(
    tmp_path, run_cli
):
    """A symlinked ignore file is not `init`'s to replace, and the vault is then bare.

    Installing over a symlink replaces the link, and `init` never destroys
    something already there. It cannot silently give up either: the entry
    exists so that preimages and harness paths never reach a remote, so the
    adopter has to be told, in the one place a failure is visible.
    """
    adopter = _fixture_repo(tmp_path / "repo")
    (tmp_path / "elsewhere").write_text("build/\n", encoding="utf-8")
    (adopter / ".gitignore").symlink_to(tmp_path / "elsewhere")

    result = run_cli("init", cwd=adopter)

    assert result.returncode == 1, result.stdout
    assert ".gitignore" in result.stderr, result.stderr
    assert "symlink" in result.stderr, result.stderr
    assert (adopter / ".gitignore").is_symlink()
    assert (tmp_path / "elsewhere").read_text(encoding="utf-8") == "build/\n"


def test_an_ignore_file_that_cannot_be_read_gates(tmp_path, run_cli):
    """A directory where the ignore file goes: the entry cannot be written, and it says so."""
    adopter = _fixture_repo(tmp_path / "repo")
    (adopter / ".gitignore").mkdir()

    result = run_cli("init", cwd=adopter)

    assert result.returncode == 1, result.stdout
    assert ".gitignore" in result.stderr, result.stderr
    assert (adopter / ".gitignore").is_dir()


def test_an_unwritable_ignore_entry_stops_the_run_before_the_harness_is_moved(
    tmp_path, run_cli
):
    """A gate that does not stop the run is not a gate.

    Measured before this test existed: the ERROR was reported and every
    later step ran anyway. The scaffold was created and the vault was
    written to while it was NOT ignored -- `local.jsonl` legitimately holds
    absolute paths, and `git add -A` picks it up -- and `_sync_symlink`
    reached `adopt.take_over`, which MOVED the harness's memory directory
    aside as a `.bak`. Moving a user's data after a check that gated is the
    one outcome nothing here may produce.
    """
    adopter = _fixture_repo(tmp_path / "repo")
    (tmp_path / "elsewhere").write_text("build/\n", encoding="utf-8")
    (adopter / ".gitignore").symlink_to(tmp_path / "elsewhere")
    harness_memory = tmp_path / "harness" / "memory"
    harness_memory.mkdir(parents=True)
    (harness_memory / "coffee.md").write_text(HARNESS_MEMORY, encoding="utf-8")

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter
    )

    assert result.returncode == 1, result.stdout
    assert ".gitignore" in result.stderr, result.stderr
    # Nothing of the scaffold, because the vault is written to from here on.
    for item in (
        "knowledge",
        "memory",
        "validated-memory.md",
        "knowledge-extension.md",
    ):
        assert not (adopter / item).exists(), item
    assert "init: created" not in result.stdout, result.stdout
    # The harness's memory is where the user left it: not absorbed, not
    # parked, not replaced by a link.
    assert harness_memory.is_dir() and not harness_memory.is_symlink()
    assert (harness_memory / "coffee.md").read_text(
        encoding="utf-8"
    ) == HARNESS_MEMORY
    assert not (tmp_path / "harness" / "memory.bak").exists()
    # The record of that link is the vault's, and the vault stays bare: an
    # empty directory is not something a commit can carry, which is what
    # makes stopping here enough on its own -- `journal.Lock` creates
    # `.validated-memory/` before the entry is even attempted.
    assert not (adopter / ".validated-memory" / "local.jsonl").exists()
    status = _git(adopter, "status", "--porcelain", "-uall").stdout
    assert ".validated-memory" not in status, status


def test_an_unreadable_ignore_file_stops_the_run_including_the_views(
    tmp_path, run_cli
):
    """The other way in gates the same run: the entry could not be written either.

    `--view` is here because it is the last thing `init` does and the only
    one outside the journalled block: the gate has to reach it too, or the
    run still writes at the adopter's root after refusing to.
    """
    adopter = _fixture_repo(tmp_path / "repo")
    (adopter / ".gitignore").mkdir()

    result = run_cli("init", "--view", cwd=adopter)

    assert result.returncode == 1, result.stdout
    assert ".gitignore" in result.stderr, result.stderr
    for item in ("knowledge", "memory", "knowledge.html", "memory.html"):
        assert not (adopter / item).exists(), item
    assert "init: created" not in result.stdout, result.stdout


def test_an_ignore_entry_the_adopter_already_wrote_is_left_alone(tmp_path, run_cli):
    """The "Local, ignored" answer writes the same entry; `init` must not repeat it."""
    adopter = _fixture_repo(tmp_path / "repo")
    (adopter / ".gitignore").write_text(
        "# validated-memory layout, local to this clone\n"
        "/knowledge/\n"
        "/.validated-memory/\n",
        encoding="utf-8",
    )
    before = (adopter / ".gitignore").read_text(encoding="utf-8")

    assert run_cli("init", cwd=adopter).returncode == 0

    assert (adopter / ".gitignore").read_text(encoding="utf-8") == before


# --- the gate fires only where the vault is really exposed --------------------


def test_a_clone_that_already_ignores_the_vault_does_not_gate(tmp_path, run_cli):
    """A gate protecting against nothing is not a gate; it is a stopped session.

    `.git/info/exclude` is per-clone, never versioned, and git reads it --
    a clone that carries the rule there has the vault ignored in fact, so
    there is nothing left for this entry to add and nothing to stop. It is
    consulted only here, where `init` cannot write to `.gitignore` at all:
    that is also where git cannot read `.gitignore` either, which makes the
    exclude file the highest-precedence source git still reads.
    """
    adopter = _fixture_repo(tmp_path / "repo")
    exclude = adopter / ".git" / "info" / "exclude"
    exclude.write_text(
        exclude.read_text(encoding="utf-8") + "/.validated-memory/\n",
        encoding="utf-8",
    )
    # A directory where the ignore file goes: `init` cannot write the entry.
    (adopter / ".gitignore").mkdir()

    result = run_cli("init", cwd=adopter)

    assert result.returncode == 0, result.stderr
    assert "ERROR" not in result.stderr, result.stderr
    # The run went on, because the vault it protects is ignored anyway.
    for item in ("knowledge", "memory", "validated-memory.md"):
        assert (adopter / item).exists(), item
    vault_record = ".validated-memory/local.jsonl"
    assert _git(
        adopter, "check-ignore", "-q", vault_record, check=False
    ).returncode == 0
    status = _git(adopter, "status", "--porcelain", "-uall").stdout
    assert ".validated-memory" not in status, status
    # And the ignore file the adopter owns is exactly as it was.
    assert (adopter / ".gitignore").is_dir()


@pytest.mark.skipif(
    _git_version() < (2, 43),
    reason="git only stopped following a symlinked ignore file in 2.43",
)
def test_a_symlinked_ignore_file_gates_even_when_its_target_carries_the_entry(
    tmp_path, run_cli
):
    """Reading through the link would call the vault ignored when it is not.

    Measured on git 2.43.0: an ignore file that is a symlink is not read at
    all -- git reports "unable to access '.gitignore': Too many levels of
    symbolic links" and the paths it would cover come back untracked. So the
    rule inside the link's target is not a rule, and `init` may not treat it
    as one: it would leave the vault exposed and say nothing. This is the
    one shape where "already ignored" has to be read somewhere other than
    the ignore file itself.
    """
    adopter = _fixture_repo(tmp_path / "repo")
    (tmp_path / "elsewhere").write_text(
        "build/\n/.validated-memory/\n", encoding="utf-8"
    )
    (adopter / ".gitignore").symlink_to(tmp_path / "elsewhere")

    result = run_cli("init", cwd=adopter)

    assert result.returncode == 1, result.stdout
    assert ".gitignore" in result.stderr, result.stderr
    # The premise the gate rests on, measured against the adopter's own git.
    vault_record = ".validated-memory/local.jsonl"
    assert _git(
        adopter, "check-ignore", "-q", vault_record, check=False
    ).returncode == 1, "this git reads a symlinked ignore file; the premise moved"


# --- the gate and the SessionStart hook ---------------------------------------


def _run_hook(project_dir, config_dir, home):
    """Drive the real `SessionStart` hook over a minimal environment.

    The same seam as `tests/test_restore_memory_symlink_hook.py`, repeated
    here rather than shared: these two cases are about what the ignore gate
    does to the hook's one job, and they belong beside the gate they pin.
    """
    return subprocess.run(
        ["bash", str(RESTORE_HOOK)],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(home),
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "CLAUDE_PROJECT_DIR": str(project_dir),
        },
    )


def _harness_memory(config_dir, project_dir):
    """Where the harness keeps a project's memory, in the hook's own layout."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(project_dir))
    return config_dir / "projects" / slug / "memory"


def test_the_hook_restores_the_link_on_an_adopted_project_whose_ignore_file_gates(
    tmp_path, run_cli
):
    """The gate must not take the hook's one job with it.

    Measured through the real hook on an adopted project that was then
    renamed -- the exact case the hook exists for -- with a symlinked
    `.gitignore`: before the gate reached this far the link came back; after
    it, the harness's per-project directory was never created at all, and
    the hook still reported success. A `SessionStart` hook that restores
    nothing, silently, every session, is the failure this path exists to
    prevent.

    Restoring the link moves no data. That is what separates it from the
    scaffold and from the take-over, which this same gate still refuses --
    and its record, which could only live in the exposed vault, is not
    written: the vault is byte-for-byte what it was.
    """
    old = _fixture_repo(tmp_path / "before-the-rename")
    config = tmp_path / "config"
    assert run_cli(
        "init", "--harness-memory", str(_harness_memory(config, old)), cwd=old
    ).returncode == 0
    new = tmp_path / "after-the-rename"
    old.rename(new)
    vault = new / ".validated-memory" / "local.jsonl"
    recorded = vault.read_bytes()
    (tmp_path / "elsewhere").write_text("build/\n", encoding="utf-8")
    (new / ".gitignore").unlink()
    (new / ".gitignore").symlink_to(tmp_path / "elsewhere")

    result = _run_hook(new, config, tmp_path / "home")

    assert result.returncode == 0, result.stderr
    assert ".gitignore" in result.stderr, result.stderr
    harness_memory = _harness_memory(config, new)
    assert harness_memory.is_symlink(), result.stderr
    assert harness_memory.resolve() == (new / "memory").resolve()
    assert (harness_memory / "MEMORY.md").is_file()
    assert vault.read_bytes() == recorded


def test_a_gated_run_on_an_adopted_project_restores_the_link_and_records_nothing(
    tmp_path, run_cli
):
    """Exit 1, the ERROR names the ignore file, and the link is back anyway.

    The three parts are one statement: the run refuses to write into a vault
    the next commit could carry, says so, and still does the one thing that
    writes nothing -- re-pointing the harness at this project's `memory/`.
    The vault is byte-for-byte what it was, so the record the link would
    normally leave is genuinely absent, and the WARNING on stderr is where
    the previous target is said instead.
    """
    adopter = _fixture_repo(tmp_path / "repo")
    harness_memory = tmp_path / "harness" / "memory"
    assert run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter
    ).returncode == 0
    vault = adopter / ".validated-memory" / "local.jsonl"
    recorded = vault.read_bytes()
    harness_memory.unlink()
    (tmp_path / "elsewhere").write_text("build/\n", encoding="utf-8")
    (adopter / ".gitignore").unlink()
    (adopter / ".gitignore").symlink_to(tmp_path / "elsewhere")

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter
    )

    assert result.returncode == 1, result.stdout
    assert ".gitignore" in result.stderr, result.stderr
    assert "could not be recorded" in result.stderr, result.stderr
    assert harness_memory.is_symlink(), result.stderr
    assert harness_memory.resolve() == (adopter / "memory").resolve()
    assert vault.read_bytes() == recorded


def test_a_gated_run_never_absorbs_the_harness_memory_directory(tmp_path, run_cli):
    """The link comes back; the directory holding the user's data does not move.

    These are two different acts on the same path. Re-pointing a link
    destroys nothing and is the hook's whole job; `adopt.take_over` copies
    the harness's agent memory into the project and renames the original to
    a `.bak`, which is the adopter's own data moving after a check that
    gated. So a real directory at the harness path is left exactly as it is,
    with a WARNING, and gets no link this run.
    """
    adopter = _fixture_repo(tmp_path / "repo")
    harness_memory = tmp_path / "harness" / "memory"
    assert run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter
    ).returncode == 0
    harness_memory.unlink()
    harness_memory.mkdir(parents=True)
    (harness_memory / "coffee.md").write_text(HARNESS_MEMORY, encoding="utf-8")
    (tmp_path / "elsewhere").write_text("build/\n", encoding="utf-8")
    (adopter / ".gitignore").unlink()
    (adopter / ".gitignore").symlink_to(tmp_path / "elsewhere")

    result = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter
    )

    assert result.returncode == 1, result.stdout
    assert ".gitignore" in result.stderr, result.stderr
    assert "left untouched" in result.stderr, result.stderr
    assert harness_memory.is_dir() and not harness_memory.is_symlink()
    assert (harness_memory / "coffee.md").read_text(
        encoding="utf-8"
    ) == HARNESS_MEMORY
    assert not (tmp_path / "harness" / "memory.bak").exists()
    assert not (adopter / "memory" / "coffee.md").exists()


def test_the_hook_leaves_no_link_for_a_project_that_was_never_adopted(
    tmp_path, run_cli
):
    """A link to a `memory/` that does not exist is worse than no link at all.

    The harness then has no memory rather than its own, and nothing later
    absorbs what it wrote in the meantime. The hook's own adopter check is
    the first line of defence here -- it runs `init` only where
    `validated-memory.md` and `memory/` are both present -- and
    `_sync_symlink` is the second, for the same call made by hand.
    """
    project = _fixture_repo(tmp_path / "repo")
    config = tmp_path / "config"
    (tmp_path / "elsewhere").write_text("build/\n", encoding="utf-8")
    (project / ".gitignore").symlink_to(tmp_path / "elsewhere")

    hook = _run_hook(project, config, tmp_path / "home")
    harness_memory = _harness_memory(config, project)
    by_hand = run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=project
    )

    assert hook.returncode == 0, hook.stderr
    assert by_hand.returncode == 1, by_hand.stdout
    assert ".gitignore" in by_hand.stderr, by_hand.stderr
    assert "no 'memory/' to link to" in by_hand.stderr, by_hand.stderr
    assert not harness_memory.is_symlink()
    assert not harness_memory.exists()


def test_the_skill_ignore_list_is_exactly_what_the_cli_creates_at_the_root(
    adopter_dir, tmp_path_factory, run_cli
):
    assert _ignore_entries(ADOPT_SKILL) == _root_artifacts(
        adopter_dir, tmp_path_factory, run_cli
    )


def test_the_adoption_guide_carries_the_same_ignore_list_as_the_skill():
    assert _ignore_entries(ADOPTION_GUIDE) == _ignore_entries(ADOPT_SKILL)


def test_the_skill_asks_the_versioning_question_before_init():
    # The decision is useless after `init --harness-memory` has already
    # absorbed the harness's memory into a repository that versions it, so
    # the question must precede the bootstrap command in the skill's text.
    text = ADOPT_SKILL.read_text(encoding="utf-8")
    decide = text.index("## Decide what this repository versions")
    bootstrap = text.index("## Bootstrap the layout")
    first_init = text.index("python3 -P -m validated_memory init")
    assert decide < bootstrap < first_init
    # Within the decision section: the two ways of keeping the layout local,
    # the deadline (the hook's own `init --harness-memory`), and the git
    # limit that rules out a per-remote answer -- an adopter who wants the
    # data on one host and not another must learn here that a commit cannot.
    section = text[decide:bootstrap]
    for needle in (
        ".gitignore",
        ".git/info/exclude",
        "git rev-parse --git-path info/exclude",
        "init --harness-memory",
        "per remote",
    ):
        assert needle in section, f"decision section does not mention {needle!r}"


# --- the import phase (spec section 1) ----------------------------------------

# Needles are matched against the skill with whitespace normalized to single
# spaces, so a needle can quote a whole sentence without depending on where
# the paragraph wraps.


def _normalized_skill():
    return " ".join(ADOPT_SKILL.read_text(encoding="utf-8").split())


def test_the_skill_imports_after_init_and_before_verify():
    # `init` has to have run (the layout must exist to import into), and the
    # answers have to be recorded before Verify reports on them.
    text = ADOPT_SKILL.read_text(encoding="utf-8")
    bootstrap = text.index("## Bootstrap the layout")
    first_init = text.index("python3 -P -m validated_memory init")
    import_phase = text.index("## Import existing knowledge")
    verify = text.index("## Verify the adoption")
    assert bootstrap < first_init < import_phase < verify


Q1_HEADING = "**Q1 -- Sources.**"
Q2_HEADING = "**Q2 -- Scan the declared sources.**"
Q1_DENIAL = (
    "Collect the answer **as text and nothing more**: nothing is resolved, "
    "opened or looked up at this point."
)
# Every sentence that describes resolving or opening a declared path. All of
# them belong under Q2, after the user has been shown what a path resolves
# to; none may appear under Q1.
RESOLUTION_SENTENCES = (
    "the realpath it resolves to (symlinks followed)",
    "**Refuse** a path that resolves to the filesystem root, to the user's "
    "home directory, to the harness's configuration directory, or to an "
    "ancestor of the repository root",
    "A path that resolves inside the repository root",
)


def test_q1_collects_text_only_and_every_resolution_happens_under_q2():
    # Needles alone are not enough here. Adding "Resolve and open every named
    # path immediately" to Q1 leaves every needle in this file green while
    # inverting the one rule this phase exists to enforce, so the assertion
    # is positional: Q1 carries the denial and nothing else about resolving
    # or opening, and every resolution sentence sits after the Q2 heading.
    text = _normalized_skill()
    q1_at = text.index(Q1_HEADING)
    q2_at = text.index(Q2_HEADING)
    assert q1_at < q2_at, "Q2 is asked before Q1"
    assert Q1_DENIAL in text, "Q1 no longer says the answer is collected as text only"
    assert q1_at < text.index(Q1_DENIAL) < q2_at, "the denial left Q1"

    for needle in RESOLUTION_SENTENCES:
        assert needle in text, f"the import phase no longer says: {needle!r}"
        assert text.index(needle) > q2_at, (
            f"{needle!r} appears before the Q2 heading: nothing is resolved "
            "or opened until the user has seen and consented at Q2"
        )

    # Q1's own text says nothing about resolving or opening, beyond the one
    # sentence that forbids both.
    q1_section = text[q1_at:q2_at].replace(Q1_DENIAL, "")
    for word in ("resolve", "open"):
        assert word not in q1_section.lower(), (
            f"Q1 mentions {word!r} outside the sentence that forbids it"
        )

    # The question itself, and the fixed notice Q2 carries with it.
    for needle in (
        "Does this project already have a knowledge system or a source of "
        "truth we should import?",
        "Scan these sources now? Nothing is written until you confirm the "
        "report.",
        "the whole repository is scanned as well",
        "anything else found is reported, and offered only under its own "
        "separate confirmation",
    ):
        assert needle in text, f"the import phase no longer says: {needle!r}"


def test_the_skill_says_why_those_resolutions_are_refused():
    text = _normalized_skill()
    for needle in (
        "they are not sources, they are everything",
        "which is what the harness-memory symlink resolves to after the first "
        "session, since it points into `memory/`",
        "keeps the exact scope declared; it is not widened to the repository",
    ):
        assert needle in text, f"the import phase no longer says: {needle!r}"


def test_the_skill_names_both_engine_modes_the_subagent_and_the_rendezvous():
    text = _normalized_skill()
    assert "`bootstrap-from-repo` in mode `declared+repo`" in text
    assert "`bootstrap-from-repo` in mode `repo`" in text
    assert "read-only subagent" in text
    assert "run the scan inline after the last question" in text
    # The "No" branch leaves a record rather than nothing at all.
    assert "`declared, not scanned`" in text
    # Q2 = Yes leaves no Q3, so the questionnaire must be told where to go
    # next -- and where the two threads meet again.
    assert "there is no Q3 left to ask" in text
    assert "there is a single rendezvous" in text
    assert "The instruction-file step never waits for the scan" in text


def test_verify_lists_the_sources_that_are_still_pending():
    text = ADOPT_SKILL.read_text(encoding="utf-8")
    verify = text.index("## Verify the adoption")
    next_steps = text.index("## Next steps")
    section = " ".join(text[verify:next_steps].split())
    assert "`memory/source-*.md`" in section
    assert "`declared, not scanned`" in section
    assert "`not located`" in section
    assert "declared and consented to again" in section


def test_the_alias_must_be_unique():
    text = _normalized_skill()
    assert (
        "An alias must be unique among the sources declared and the active "
        "`source-*` entries already in `memory/`; a duplicate is refused "
        "before anything else happens." in text
    )


Q3_QUESTION_SENTENCE = (
    '"Scan this repository for validated knowledge or agent memory worth '
    'importing?" Yes runs `bootstrap-from-repo` in mode `repo`.'
)
Q3_NO_BRANCH = (
    "**No** -- nothing is read: the phase ends with the record entries "
    "already proposed under Q2 (or with nothing to record when Q1 named "
    "nothing), and the questionnaire proceeds to the instruction-file step "
    "below."
)
Q3_CONDITIONAL_DISPATCH = "When a scan was consented to, dispatch it to a **read-only subagent**"


def test_q3_has_a_no_branch_and_the_scan_is_dispatched_only_on_consent():
    # Q3's "Yes" was already documented; a "No" that is never spelled out
    # reads as if declining left the phase in limbo. And the dispatch
    # paragraph used to fire unconditionally right after Q3 -- which is
    # wrong once Q2 = Yes already ran the scan and there is no Q3 left to
    # ask: the dispatch must be conditioned on consent having happened,
    # whichever question gave it.
    text = _normalized_skill()
    assert Q3_QUESTION_SENTENCE in text
    assert Q3_NO_BRANCH in text, "Q3 no longer has an explicit No branch"
    assert Q3_CONDITIONAL_DISPATCH in text, (
        "the scan dispatch is no longer conditioned on consent"
    )
    question_at = text.index(Q3_QUESTION_SENTENCE)
    no_at = text.index(Q3_NO_BRANCH)
    dispatch_at = text.index(Q3_CONDITIONAL_DISPATCH)
    assert question_at < no_at < dispatch_at, (
        "the No branch must sit between the Q3 question and the conditional "
        "dispatch paragraph"
    )


# --- the managed block (spec section 4.1) -------------------------------------

BEGIN_MARKER = "<!-- validated-memory:begin -->"
END_MARKER = "<!-- validated-memory:end -->"
SKILLS_DIR = REPO_ROOT / "skills"


def _managed_block(path):
    """The canonical managed block as `path` quotes it, verbatim.

    Delimited by its own two marker lines, each of which must appear exactly
    once in the file and alone on its line -- the same rule the skill's write
    rule imposes on the adopter's instruction file. Everything between them,
    markers included, is the block; the fence around it is not.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == BEGIN_MARKER]
    ends = [i for i, line in enumerate(lines) if line.strip() == END_MARKER]
    assert len(starts) == 1, f"{path}: expected one begin marker, found {len(starts)}"
    assert len(ends) == 1, f"{path}: expected one end marker, found {len(ends)}"
    assert starts[0] < ends[0], f"{path}: the end marker precedes the begin marker"
    return "\n".join(lines[starts[0] : ends[0] + 1])


CANONICAL_MANAGED_BLOCK = """\
<!-- validated-memory:begin -->
## Validated memory

This project practises the validated-memory method. Curated knowledge lives
in `knowledge/` (one unit per claim, with `evidence` declared and freshness
probed); agent memory lives in `memory/` (one fact per file, indexed in
`memory/MEMORY.md`); `knowledge-index.md` is derived and never hand-edited.

- Record a finding, decision or measured fact worth re-checking as a
  knowledge unit (`create-knowledge-unit`); a preference or a durable
  project fact as a memory entry (`maintain-agent-memory`).
- When the world changes a fact, do not edit it: write a successor and
  supersede the old record (`supersede-knowledge`). Only a defect `lint` can
  name is repaired in place.
- Before citing a curated fact that carries anchors, read its verdict in
  `knowledge-index.md` (run `derive` first if this clone does not version
  it); `drifted` or `unknown` means re-check first (`probe-freshness`).
- `memory/source-*.md` entries record sources of existing knowledge seen at
  adoption; one whose status is `declared, not scanned` is knowledge this
  project has not imported yet (`bootstrap-from-repo` imports it).
- Usage questions: `ask-validated-memory`.
<!-- validated-memory:end -->"""

# Every skill the block names: six of the seven, all but
# `adopt-validated-memory` itself, which is the skill that writes the block.
# Compared as an exact set, not a subset -- a block that quietly stopped
# naming `supersede-knowledge` would still pass a subset check while leaving
# a later session with no pointer to the one skill that retires a wrong fact.
MANAGED_BLOCK_SKILLS = {
    "create-knowledge-unit",
    "maintain-agent-memory",
    "supersede-knowledge",
    "probe-freshness",
    "bootstrap-from-repo",
    "ask-validated-memory",
}


def test_both_copies_of_the_managed_block_equal_the_canonical_one():
    # Comparing the two copies with each other is not enough: two identical
    # copies of a block that drifted from what the design specified would
    # pass that. The constant here is the third party both must match.
    assert _managed_block(ADOPT_SKILL) == CANONICAL_MANAGED_BLOCK
    assert _managed_block(ADOPTION_GUIDE) == CANONICAL_MANAGED_BLOCK


def test_the_managed_block_names_exactly_those_skills_and_they_all_exist():
    # A backticked token shaped like a skill name -- lower-case, hyphenated,
    # no dot and no slash -- is a skill reference. That shape excludes every
    # other backticked token in the block: paths (`memory/`,
    # `memory/MEMORY.md`, `memory/source-*.md`), filenames
    # (`knowledge-index.md`), single words (`lint`, `derive`, `evidence`,
    # `drifted`, `unknown`) and the quoted status literal.
    named = {
        token
        for token in re.findall(r"`([^`]+)`", CANONICAL_MANAGED_BLOCK)
        if re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+", token)
    }
    assert named == MANAGED_BLOCK_SKILLS
    on_disk = {path.name for path in SKILLS_DIR.iterdir() if path.is_dir()}
    assert MANAGED_BLOCK_SKILLS <= on_disk, (
        f"the managed block names skills that do not exist: "
        f"{sorted(MANAGED_BLOCK_SKILLS - on_disk)}"
    )


def test_the_managed_block_write_rule_is_closed():
    # The file belongs to the adopter, and the failure mode is losing
    # content this plugin does not own: every case the write can meet has an
    # answer, and two of them are "write nothing".
    text = _normalized_skill()
    for needle in (
        "**no marker in the file** -- append the block, on confirmation",
        "exactly one begin marker followed by exactly one end marker",
        "write nothing, name the lines, and leave the repair to the user",
        "**the file is a symlink**",
        "its realpath is outside the repository root",
        "Re-read the file immediately before writing",
        "preserved byte for byte",
    ):
        assert needle in text, f"the write rule no longer says: {needle!r}"


def test_the_caller_checks_coverage_against_an_inventory_it_obtains_itself():
    """The rendezvous gained a rejection criterion, and its own evidence.

    Measured on the first real adoption (2026-08-29): the engine skill was
    loaded in full, its packet said "the repository root, and each declared
    path", and eleven lines later the dispatch narrowed the roots to the four
    declared paths plus two hand-picked files, adding "read nothing outside
    these paths". The report came back with its section 2 present and holding
    those two files, so every layout check passed while 372 versioned
    Markdown files were never inventoried. The caller now has to contradict
    the ledger with a listing the scan did not produce.
    """
    text = _normalized_skill()
    assert text.index("check the returned report first, and present it only "
                      "if it passes") < text.index(
        "Check the report's coverage ledger against an inventory you obtain "
        "yourself."), "the gate no longer precedes presentation"
    for needle in (
        "Check the report's coverage ledger against an inventory you obtain "
        "yourself.",
        "check the returned report first, and present it only if it passes",
        "Nothing is shown and nothing is confirmed before the check below",
        "`git ls-files --others --exclude-standard`",
        "Equal, not close",
        "every path counted as `oversized` or `unreadable` is listed by path "
        "in section 3",
        "every exclusion appears as a scope with its rule and its count",
        "without them a scan can read two files of a thousand and book the "
        "other 998 as `unreadable`, or as `excluded`",
        "For a declared source **outside the repository**, `git ls-files` "
        "says nothing",
        "It cannot make **fabrication** impossible",
        "What the ledger buys is that the lie has to be specific and "
        "written down",
        "a scan that supplies both the coverage claim and its only evidence "
        "cannot be checked at all",
        "`git ls-files`",
        "the repository-remainder partition is one of them",
        "`discovered = classified + excluded + oversized + unreadable`",
        "A report failing any of these is not presented and nothing from it "
        "is written",
        "Section 2 being present is not evidence",
    ):
        assert needle in text, f"the rendezvous no longer says: {needle!r}"


def test_the_two_skills_agree_on_the_coverage_vocabulary():
    """The call contract across the delegation seam, as far as prose can pin it.

    Each skill was pinned separately before, so the caller could stop naming
    what the engine returns without any test noticing. These are the terms
    both halves must use for the check at the rendezvous to mean anything.
    """
    engine = REPO_ROOT / "skills" / "bootstrap-from-repo" / "SKILL.md"
    engine_text = " ".join(engine.read_text(encoding="utf-8").split())
    caller_text = _normalized_skill()
    for shared in (
        "`discovered = classified + excluded + oversized + unreadable`",
        "repository-remainder partition",
    ):
        assert shared in engine_text, f"the engine no longer says: {shared!r}"
        assert shared in caller_text, f"the caller no longer says: {shared!r}"
