"""The `init` subcommand: scaffold the adopter layout.

`init` creates the minimal layout for both layers -- curated knowledge and
agent memory -- plus the adopter's configuration and a valid declared
extension stub, so that `validate` and `lint` pass clean right after a run on
an empty project. Every item is created only if missing: an existing item is
left untouched, never overwritten or deleted, and `init` says which of the
two happened.

With `--harness-memory PATH`, `init` also makes PATH a move-proof symlink to
this project's `memory/` directory (absolute target). That is the data half
of the harness integration: computing PATH from the harness's own layout and
calling `init --harness-memory PATH` on every session start is the plugin's
startup hook, wired in a later ticket. `init` only guarantees that calling it
again -- from the same checkout or from a renamed or re-cloned one -- restores
the link without ever deleting data.

When PATH is a real path that is not a symlink, `adopt` decides: a directory
recognizably holding the harness's own agent memory is absorbed into this
project's `memory/` and parked aside as a `.bak` before the link is created
(see `adopt.py`); anything else is left alone with a WARNING saying why. Both
outcomes are fail-open: the link is restored, or it is left alone and said
so, and the session is unaffected either way.

A project with no `memory/` is the one case where PATH is not touched at
all. The link's target is this project's agent memory, so a link made
before that directory exists points nowhere: the harness is left with no
memory, where an untouched PATH leaves it its own -- which a later run
absorbs into the project rather than losing.

A journal that cannot be read or written does gate -- a required record that
is missing or corrupt is exit 1 (ADR 0008), never a silent continuation --
but it does not take the symlink with it: the harness half runs outside the
journalled part of the run, and the record it could not write is reported as
a WARNING naming what was lost. So `init` can exit 1 while the link is back,
which is why the `SessionStart` hook reports success whatever the exit code
(`hooks/restore-memory-symlink.sh`).

`init` also writes one line into the repository's ignore file
(`.gitignore`): `/.validated-memory/`, the vault holding preimages and the
records of mutations whose path leaves the repository. That entry is not
part of the adoption questionnaire and never was -- ADR 0008 makes the
vault local by construction rather than by the adopter answering a question
one way -- so it is written on every run, whatever the project versions.
The entry is written once: an ignore file that already carries it is left
exactly as it is, and one that does not exist is created.

An entry that cannot be written is an ERROR that stops the journalled run
where it stands. Everything after it writes into a vault that is then
exposed to the next commit, and absorbing a harness directory -- which
moves the adopter's own data -- after a check that gated is further still
from anything `init` may do on its own. So the scaffold, the take-over and
the views do not run: the ERROR names the one line to add by hand, and the
next run picks up from a tree `init` has not touched.

An ignore file the adopter marked read-only is one of those: mode 0444
denies writing to this user, so the entry is refused before anything is
prepared and the ERROR names the file and its mode. That is a gated
adoption on every session start until one `chmod` fixes it: writing to a
file the adopter marked read-only is not an option this method takes.

The harness symlink is the one act that outlives that gate, because
restoring it moves no data and it is the whole job of the `SessionStart`
hook. Its record is the one that could only live in the vault, which is
precisely what is exposed, so it is not written and the loss is a WARNING
-- exactly the treatment a journal that cannot be written already gets. A
gated run ends with the link back, an ERROR naming the ignore file, and
exit 1.

The gate fires only where the vault is really exposed. Before it stops
anything, `init` reads `.git/info/exclude`: a clone that already ignores
the vault there has nothing this entry could add. That file is also the
highest-precedence ignore source git still reads when it cannot read
`.gitignore` -- the same situation `init` cannot write it in. What a
symlinked `.gitignore` points at is deliberately not read as if it were
the rule: git does not follow one (measured on git 2.43, which reports
"unable to access '.gitignore': Too many levels of symbolic links" and
leaves the paths untracked), so believing the target would call an exposed
vault ignored.

With `--view`, `init` also creates `knowledge.html` and `memory.html` --
once each. The views are optional, and activation is the presence of the
artifact, not a configuration key (an unknown field in `validated-memory.md`
is an ERROR that gates the other subcommands, so a key would brick an
adopter's project rather than just leave the view inactive). This is why
`--view` only ever creates: an artifact that already exists, even one
edited by hand, is reported `kept` and left untouched, same as every other
item `init` manages -- regenerating what is already there is `render`'s job
(`render --only-existing`, wired to a `SessionStart` hook), not `init`'s. A
corpus the renderer refuses is a WARNING here, never a gate: `init --view`
simply creates nothing this run.
"""

import os
from pathlib import Path

from . import adopt, journal, render
from .findings import ERROR, EXIT_ERROR, EXIT_OK, WARNING, Finding

# `Path.exists()` follows a symlink, so a broken one reads as absent and
# every write path in this module has to name it before it writes: the
# temporary-then-`os.replace` install does not follow the link either, it
# replaces it, which would destroy a link `init` did not create and could
# not put back.
BROKEN_SYMLINK = (
    "exists as a broken symlink; installing here would replace the link "
    "itself, and `init` never overwrites or deletes something that is "
    "already there, so it is left untouched"
)

IGNORE_FILENAME = ".gitignore"
# The clone's own ignore file: git reads it, no commit can carry it, and it
# is read here only to answer "is the vault ignored anyway?" -- never written
# to. A `.git` that is a file rather than a directory (a worktree, a
# submodule) puts it somewhere this does not look, so it reads as empty and
# the run gates: unsure is the side to be wrong on.
EXCLUDE_PATH = Path(".git") / "info" / "exclude"
IGNORE_ENTRY = f"/{journal.VAULT_DIRNAME}/"
IGNORE_BLOCK = f"""\
# The validated-memory vault: preimages, and the records of mutations whose
# path leaves the repository. Always local to this clone (ADR 0008), which is
# why `init` writes this entry itself rather than the adoption questionnaire
# asking for it.
{IGNORE_ENTRY}
"""

# Forms of the same rule a hand-written ignore file may already carry --
# including the one the questionnaire's "local" answers write. `init` adds
# nothing when any of them is already there: it writes the entry once, it
# does not keep the file in a shape of its own choosing.
IGNORE_EQUIVALENTS = {
    IGNORE_ENTRY,
    IGNORE_ENTRY.rstrip("/"),
    IGNORE_ENTRY.lstrip("/"),
    IGNORE_ENTRY.strip("/"),
}

# Why a mutation went unrecorded, as `_record_symlink` says it. The link is
# restored on both, because the failure is the record's and not the link's.
UNRECORDED_JOURNAL = "the journal is unavailable"
UNRECORDED_VAULT = (
    "the vault is not ignored, and this record can only live there"
)

# The harness path is left exactly as it is on both of these.
NO_PROJECT_MEMORY = (
    "this project has no 'memory/' to link to, so the harness path was left "
    "alone: a link made now would point at a directory that does not exist, "
    "leaving the harness no memory at all, where an untouched path leaves it "
    "its own"
)
UNABSORBED = (
    "already exists and is not a symlink; absorbing it moves the adopter's "
    "own data, which a run that gated may not do, so it was left untouched"
)

MEMORY_INDEX = """\
# Agent memory

No entries yet.
"""

CONFIG = """\
---
extension:
  schema: knowledge-extension.md
  version: "1"
id_prefix: kb-
probes:
  git_ref: python3 -m validated_memory.probes.git_ref
---

# Adopter configuration

This file configures how `validated-memory` treats this repository. It is
plain Markdown with a YAML-subset frontmatter, readable without the plugin
installed.

- `extension` names the adopter's declared extension: the schema file
  (`schema`) and the version of it this project is on (`version`). See
  `knowledge-extension.md`.
- `id_prefix` records the id scheme curated-knowledge units follow, for
  humans and skills; `validate` does not enforce it.
- `probes` maps an anchor `kind` to the command that probes it for
  freshness. `git_ref` ships with the plugin as
  `python3 -m validated_memory.probes.git_ref`.

Generated by `validated-memory init`. Safe to hand-edit -- `init` never
overwrites a file that already exists.
"""

EXTENSION_STUB = """\
---
fields: []
---

# Declared extension

This is the adopter's curated-knowledge schema: it declares the fields a
unit's frontmatter may carry, on top of the base contract (`id`, `evidence`,
`supersedes`, `anchors`, `provenance`, `rationale`). No fields are declared yet
(`fields: []`) -- `validate` still enforces the base contract alone until you
add some.

Add a field as an entry under `fields`:

```yaml
fields:
  - name: domain      # the frontmatter key a unit may carry
    type: enum        # 'string' (any non-empty scalar) or 'enum' (closed domain)
    values:           # required only for type 'enum'
      - network
      - storage
  - name: owner
    type: string
```

## Versioning

`version`, in `validated-memory.md`, records which schema version this
project is on. Additive changes (a new field, a new enum value) do not bump
the version; removing or narrowing does. Units already written are never
rewritten to a newer schema -- supersede them instead.

Generated by `validated-memory init`. Safe to hand-edit -- `init` never
overwrites a file that already exists.
"""


def run(harness_memory, view, stdout, stderr):
    """Scaffold the adopter layout under the working directory.

    Returns an exit code: 0 unless an item could not be created, or the
    journal could not be read or written. Even a harness-memory symlink
    `init` cannot restore is a WARNING, not an ERROR -- fail-open, so the
    caller (eventually a startup hook) never breaks the session over it.
    Same for `view`: a corpus the renderer refuses is a WARNING, never a
    gate -- see `_ensure_views`.

    A journal failure is the one ERROR that is not about a single item, and
    it is not fail-open: required history that cannot be read is exit 1 (ADR
    0008). What it gates is the journalled part of the run -- the scaffold,
    which must not mutate the adopter tree while nothing can record what it
    did. The harness symlink is not part of that: it runs afterwards, on its
    own, and reports the record it could not write.

    The vault's ignore entry is the other ERROR that is not about a single
    item (`_ensure_ignored`), and it gates the same journalled part plus the
    views: nothing that writes into the vault, into the adopter tree, or
    into a directory the adopter owns runs after it. The harness symlink
    still does, without its record, because restoring a link moves no data
    and the `SessionStart` hook has no other job.

    What that ordering guarantees, stated exactly: no `local` transaction
    file and no preimage is written while the vault is unignored. A `local`
    path is absolute by construction (ADR 0008), which is what a commit
    must never carry, and the run's only `local` intention -- the harness
    symlink's -- is dropped when this gate fires. The entry's OWN
    transaction is written before it, and may be: it names `.gitignore`,
    relative, with two digests and no bytes, which is what the lock file
    beside it already is. Recovery runs before the gate too, for the
    opposite reason: it only completes or closes what an earlier run began,
    and unlinks the files that said so, so it takes things off the disk
    rather than putting new ones there.
    """
    findings = []
    created = 0
    kept = 0
    unignored = False

    # Everything that journals -- the scaffold and the harness symlink --
    # runs under one lock for the whole run: `init` is deliberately
    # re-runnable at session start, and this is what serialises the work
    # `journal.Run` does not lock on its own, above all the harness
    # take-over in `_sync_symlink`, which moves an adopter's directory and
    # is not journalled at all. `journal.Lock` is re-entrant, so the lock
    # `journal.Run()` takes around its own reads is this one.
    journal_failure = None
    try:
        with journal.Lock():
            session = journal.Run()
            # Before anything this run intends: recovery only completes or
            # closes what an EARLIER run began, and unlinks the files that
            # said so, so it reduces what is on disk rather than adding to
            # it -- and an open transaction on `.gitignore` itself is
            # settled before the new `.gitignore` intention is formed. A
            # transaction it cannot account for is an ERROR that gates that
            # ONE path (`journal.Run._survey`), not the run.
            findings.extend(_report_recovery(session, stdout))
            # First, because it is what keeps the vault out of the
            # repository, and the vault is written to from here on.
            ignore_findings = _ensure_ignored(session, stdout)
            findings.extend(ignore_findings)
            unignored = any(
                finding.severity == ERROR for finding in ignore_findings
            )
            if not unignored:
                # Each call below runs (and creates its item, if missing)
                # immediately; this just names the completed results before
                # reporting them in order.
                steps = (
                    _ensure_dir(Path("knowledge"), session),
                    _ensure_dir(Path("memory"), session),
                    _ensure_file(
                        Path("memory") / "MEMORY.md", MEMORY_INDEX, session
                    ),
                    _ensure_file(Path("validated-memory.md"), CONFIG, session),
                    _ensure_file(
                        Path("knowledge-extension.md"), EXTENSION_STUB, session
                    ),
                )

                for item, outcome, finding in steps:
                    if finding is not None:
                        findings.append(finding)
                        continue
                    print(f"init: {outcome} {item}", file=stdout)
                    if outcome == "created":
                        created += 1
                    else:
                        kept += 1

                if harness_memory is not None:
                    findings.extend(
                        _sync_symlink(harness_memory, stdout, session)
                    )
    except journal.JournalError as error:
        journal_failure = Finding(
            ERROR, _journal_artifact(error), "journal", error.message
        )
    except OSError as error:
        # The lock and the journal's own opening record both need to create
        # `.validated-memory/` and `journal.jsonl` before any scaffold item
        # is attempted; an adopter root that cannot be written to at all
        # (e.g. read-only permissions) fails here first, ahead of any
        # per-item ERROR `_ensure_dir`/`_ensure_file` would otherwise
        # raise. The lock's own directory is not always this root's --
        # `journal.lock_path` puts it beside a journal that is a symlink
        # into a shared store -- which is why the filename the OS refused
        # is reported below rather than a path assembled here.
        journal_failure = Finding(
            ERROR,
            # The path the OS refused, when it named one: the lock, the
            # vault directory and the journal itself all fail through here,
            # and naming one of the others sends a reader to a file that is
            # not the problem.
            error.filename or journal.JOURNAL_FILENAME,
            "journal",
            f"journal could not be opened: {error}",
        )

    if journal_failure is not None:
        findings.append(journal_failure)
    elif view and not unignored:
        view_created, view_kept, view_findings = _ensure_views(stdout)
        created += view_created
        kept += view_kept
        findings.extend(view_findings)

    # Neither of the two whole-run ERRORs reached the symlink inside the
    # block, and both leave it to be restored here: the journal is the
    # record of what `init` did, not what a session needs to keep working,
    # and an unignored vault is a reason not to write a record, not a reason
    # to leave the harness pointing at a project it no longer names. Outside
    # the lock is safe on both -- there is no journal to serialise access
    # to, and re-pointing a symlink at the target it already has is
    # idempotent, which is what makes this harmless when the journal failed
    # after the link had already been restored above.
    if harness_memory is not None and (journal_failure is not None or unignored):
        findings.extend(
            _sync_symlink(
                harness_memory,
                stdout,
                None,
                # The take-over moves the adopter's own data, so it belongs
                # to the run that gated, not to the promise that survives
                # it: a real directory at the harness path is left alone.
                absorb=not unignored,
                unrecorded=UNRECORDED_VAULT if unignored else UNRECORDED_JOURNAL,
            )
        )

    errors = [finding for finding in findings if finding.severity == ERROR]
    warnings = [finding for finding in findings if finding.severity == WARNING]
    for finding in findings:
        print(finding.render(), file=stderr)
    print(
        f"init: {created} created, {kept} kept, "
        f"{len(errors)} error(s), {len(warnings)} warning(s)",
        file=stdout,
    )
    return EXIT_ERROR if errors else EXIT_OK


def _report_recovery(session, stdout):
    """Resolve what an earlier run left open; return the findings it raised.

    A COMPLETED recovery is printed WHEN IT APPENDED SOMETHING, because a
    mutation reaching the history a session late is a thing that happened
    to this project and the line is where the user sees it. Completing a
    transaction whose records were already there -- the residue of a crash
    between the append and the unlink -- appends nothing and prints
    nothing: the history is exactly what it was, and "recovered" said about
    it announces a mutation the file already carried. A discarded or
    removed one prints nothing either: the transaction is gone, the path is
    exactly as it was, and a line about a mutation that never happened is
    noise on every session start after a crash.

    A problem is an ERROR against the path it names, or against the
    transaction when it is too damaged to name one. The run carries on -- a
    stale transaction on one path is not a reason to stop scaffolding the
    others -- and the exit code gates, because `journal --check` reports
    exactly these and a caller told nothing by `init` would have to run it
    to find out.
    """
    findings = []
    for recovery in session.recover():
        if recovery.problem is not None:
            findings.append(
                Finding(
                    ERROR,
                    recovery.path or recovery.transaction,
                    "journal",
                    recovery.message,
                )
            )
        elif recovery.action == journal.RECOVERED and recovery.appended:
            print(
                f"init: recovered {recovery.path} from transaction "
                f"{recovery.transaction}",
                file=stdout,
            )
    return findings


def _journal_artifact(error):
    """Where a `JournalError` came from, as the location a Finding names.

    The error carries the artifact it was raised against -- there are two,
    and naming the wrong one sends a reader to a file that is perfectly
    valid -- plus the line, when the fault is a single line's rather than
    the whole file's.
    """
    where = error.artifact or journal.JOURNAL_FILENAME
    return where if error.lineno is None else f"{where}:{error.lineno}"


def _ensure_ignored(session, stdout):
    """Add the vault's ignore entry to the repository's ignore file.

    Returns findings. The entry is `/.validated-memory/`, anchored at the
    root the same way the questionnaire's list is, and it is not one of the
    questionnaire's answers: ADR 0008 fixes the vault as always local
    because it holds preimages, which may carry bytes the adopter
    deliberately kept out of the repository, and the harness paths that
    never were repository content. A vault the adopter has to remember to
    ignore is a vault that gets committed.

    It is not counted as an item `init` created or kept. Those counters are
    about the layout `init` owns; this is one line appended to a file the
    adopter owns, and the run reports it on its own line.

    Four shapes are left alone rather than written to: an ignore file that
    already carries the rule in any of its usual spellings, one that cannot
    be read, one that is a symlink -- installing over a symlink replaces
    the link itself, and destroying a link to record an ignore rule is not a
    trade `init` may make -- and one whose mode denies writing to this user,
    which the executor refuses before anything is prepared.

    The unreadable one and the symlinked one only gate when the vault is
    really exposed, which is not the same question. `.git/info/exclude`
    ignores the same paths, is never versioned, and -- because git will not
    read an ignore file it cannot open either -- is the highest-precedence
    source git still consults in exactly these two shapes, so a rule there
    settles it and the run goes on. The symlink's own target is not read:
    git does not follow a symlinked ignore file at all, so what it points
    at ignores nothing.

    This runs first, and what that buys is precise: while the vault is
    unignored, no `local` transaction file and no history record exists.
    Those are the two that carry what a commit must never carry -- a
    `local` path is absolute by construction (ADR 0008), and a record is
    versioned. This function's own intention opens a `repo` transaction,
    and may: that file names `.gitignore`, relative, with two digests and
    no bytes of the adopter's in it, no more exposed than the lock file
    already beside it. A parked preimage of `.gitignore` may exist too --
    an `append` over an existing file parks one before the re-read that
    can still abort the mutation -- and it holds bytes the adopter already
    versioned, which is why it is the vault content this gate can afford
    to be one race short about.
    """
    path = Path(IGNORE_FILENAME)
    missing = _write_entry(session, path, stdout)
    if missing is None:
        return []
    if _carries_entry(_read_text(EXCLUDE_PATH)):
        print(
            f"init: {IGNORE_ENTRY} already ignored by "
            f"{EXCLUDE_PATH.as_posix()}",
            file=stdout,
        )
        return []
    return [Finding(ERROR, IGNORE_FILENAME, "ignore-rule", missing)]


def _write_entry(session, path, stdout):
    """Put the vault's entry in `path`. Returns why it is not there, or None.

    None means the rule is in the file -- appended just now, or already
    there when `init` looked. Everything else is a reason, which is the
    ERROR's message when the caller finds nothing else ignoring the vault.

    The two shapes the entry can be added to get two different intentions,
    because their inverses differ and only the record says which was done:
    an ignore file that is not there at all is a `create` of the whole
    block, whose inverse is removing a file `init` made; one that is
    already there is an `append` of the addition alone, whose inverse is
    truncating the adopter's own file back to the length it had. The
    expected state is stated, not read on the caller's behalf: the digest
    of the bytes just read, so an ignore file rewritten between the read
    and the write is refused rather than appended to blind.

    A refusal comes back as the reason, exactly as an `OSError` did. This
    is where a read-only ignore file lands: mode 0444 denies writing to
    this user, the executor refuses before anything is prepared, and the
    run gates with an ERROR naming the file and its mode. Writing to a file
    the adopter marked read-only is not an option this method takes.
    """
    if path.is_symlink():
        return (
            f"the vault's ignore entry ({IGNORE_ENTRY}) is missing and "
            "cannot be added: the ignore file is a symlink, and writing it "
            "would replace the link. Add the entry by hand."
        )
    try:
        raw = path.read_bytes() if path.exists() else b""
        existing = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return (
            f"the ignore file could not be read, so the vault's entry "
            f"({IGNORE_ENTRY}) could not be added: {error}"
        )
    if _carries_entry(existing):
        return None
    addition = _ignore_addition(existing)
    if path.exists():
        intention = journal.append_to_file(
            purpose="ignore-rule",
            path=IGNORE_FILENAME,
            durability=journal.REPO,
            expected={"kind": journal.FILE, "digest": journal.digest(raw)},
            content=addition.encode("utf-8"),
        )
    else:
        intention = journal.create_file(
            purpose="ignore-rule",
            path=IGNORE_FILENAME,
            durability=journal.REPO,
            content=addition.encode("utf-8"),
        )
    outcome = session.execute(intention)
    refusal = _refusal(
        outcome,
        f"the vault's ignore entry ({IGNORE_ENTRY}) could not be written",
    )
    if refusal is not None:
        return refusal
    print(f"init: ignored {IGNORE_ENTRY} in {IGNORE_FILENAME}", file=stdout)
    return None


def _refusal(outcome, prefix):
    """The message an executor refusal carries, or None when it applied.

    `noop` is not a third answer here. Every intention `init` forms names a
    state the path is not in -- an entry the file does not carry, an item
    that is not there -- so an outcome saying the path already holds what
    was intended would mean the check that decided to write it read
    something else than the executor did. Reporting that as `created` or as
    a silent success is how a claim about the tree stops being true, so it
    comes back as a refusal like any other: the run gates on that item, and
    the message says the state is one nothing here can produce.

    It is a returned ERROR rather than a raise because `run` does not catch
    `AssertionError`: raising here would turn a contradiction between two
    readings of one path into a traceback out of the CLI, which is the one
    shape of failure this package does not ship. Nothing covers it -- from
    the CLI seam it takes a third party writing between the check and the
    execute -- which is why the rule is stated here and not in a test.
    """
    if outcome.status == journal.OUTCOME_REFUSED:
        return f"{prefix}: {outcome.message}"
    if outcome.status == journal.OUTCOME_NOOP:
        return (
            f"the executor reported no-op for a {outcome.op}, which cannot "
            "happen: every intention `init` forms names a state the path is "
            "not in. Nothing has been written."
        )
    return None


def _carries_entry(text):
    """Say whether `text` already carries the rule, in any of its spellings.

    Read line by line rather than matched the way git matches: this only
    ever decides whether `init` has anything to add, and the reader git
    would need is a gitignore engine.
    """
    return any(
        line.strip() in IGNORE_EQUIVALENTS for line in text.splitlines()
    )


def _read_text(path):
    """`path`'s text, or empty when it cannot be read: a file saying nothing."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _ignore_addition(existing):
    """The block to append, separated from whatever the file already holds."""
    prefix = ""
    if existing and not existing.endswith("\n"):
        prefix = "\n"
    if existing.strip():
        prefix += "\n"
    return prefix + IGNORE_BLOCK


def _ensure_dir(path, session):
    """Create `path` as a directory if missing. Returns `(item, outcome, finding)`.

    A directory that is already there is recorded as an observation: that it
    pre-existed is a fact about the state before adoption, and nothing can
    re-derive it later.

    A broken symlink is the same shape `_ensure_file` refuses, and it earns
    the same answer here: `mkdir` cannot create through it, and the
    reconciler reads the link itself as evidence the directory was created,
    so it would report `applied` for a `create` whose inverse is removing
    the adopter's own link.

    Anything else that is not a directory -- a plain file where `memory/`
    goes -- is an ERROR, not a `kept`. Calling it `kept` would journal
    "directory already present" about a file: a permanent, uninvertible
    claim, after which every command that reads the layout fails on the
    name it was told was a directory. Saying so is the executor's: the
    intention expects the name to be absent, and the state it finds is what
    the message names.
    """
    location = path.as_posix()
    if path.is_symlink() and not path.exists():
        return location, None, Finding(ERROR, location, "create", BROKEN_SYMLINK)
    if path.is_dir():
        finding = _observe(session, location, "directory already present")
        if finding is not None:
            return location, None, finding
        return location, "kept", None
    # A `mkdir` has no preimage to park, which is not the same as having no
    # transaction: §4 rejects "mutate first, record after", and a directory
    # created between the two records is one the journal would never
    # mention. The executor owns all of it -- the expected state, the
    # transaction file, the mkdir and both records -- so this states what it
    # wants and renders what came back.
    outcome = session.execute(
        journal.create_directory(
            purpose="init",
            path=location,
            durability=journal.REPO,
            note="directory created",
        )
    )
    refusal = _refusal(outcome, "directory could not be created")
    if refusal is not None:
        return location, None, Finding(ERROR, location, "create", refusal)
    return location, "created", None


def _ensure_file(path, content, session):
    """Write `content` to `path` if missing. Returns `(item, outcome, finding)`.

    A broken symlink is the one shape that reads as absent while something
    is plainly there. It is an item `init` cannot create without destroying
    what the adopter put in its place, so it gates, exactly as an item
    blocked by anything else real does -- and nothing is recorded, because
    nothing happened.

    A REGULAR file is what `kept` means, and nothing else. A directory
    where a file goes, or a symlink pointing at one, would otherwise be
    observed as "file already present" -- a permanent, uninvertible claim
    that adoption found a file, written about something that is not one,
    after which every command that reads the layout works on a name the
    journal describes wrongly. It is the mirror of the plain file where
    `memory/` goes, and it gets the same answer: the intention expects the
    name to be absent, and the executor's refusal names what is really
    there.
    """
    location = path.as_posix()
    if path.is_symlink() and not path.exists():
        return location, None, Finding(ERROR, location, "create", BROKEN_SYMLINK)
    if path.is_file() and not path.is_symlink():
        finding = _observe(session, location, "file already present")
        if finding is not None:
            return location, None, finding
        return location, "kept", None
    outcome = session.execute(
        journal.create_file(
            purpose="init",
            path=location,
            durability=journal.REPO,
            content=content.encode("utf-8"),
        )
    )
    refusal = _refusal(outcome, "file could not be created")
    if refusal is not None:
        return location, None, Finding(ERROR, location, "create", refusal)
    return location, "created", None


def _observe(session, location, note):
    """Record what adoption found at `location`. Returns a finding, or None.

    The refusal `observe` raises is a path that resolves out of the adopter
    root through a symlink, and it is about this ONE item: `authorise` gives
    it as an `OSError` precisely so a caller can gate the item that named it
    and carry on with the rest (`journal.authorise`). Left to reach
    `init.run`'s outer handler it would be reported as "the journal could
    not be opened" against `journal.jsonl` -- a whole-run failure naming a
    file that is perfectly valid, with the other items never attempted.
    """
    try:
        session.observe(location, note)
    except OSError as error:
        return Finding(ERROR, location, "journal", str(error))
    return None


def _sync_symlink(
    raw_path, stdout, session, absorb=True, unrecorded=UNRECORDED_JOURNAL
):
    """Make `raw_path` a symlink to this project's `memory/`, without deleting data.

    - No `memory/` in this project: nothing is touched. There is no target
      to point at, and a link to a directory that does not exist is worse
      than none -- the harness reads and writes through this path, so a
      dangling link costs it its memory, where an untouched path leaves it
      its own for a later run to absorb.
    - Missing: create the symlink (making parent directories as needed).
    - Already a symlink (even broken, even pointing elsewhere): re-point it --
      re-pointing a symlink never destroys data, unlike replacing a real path.
    - A real path: handed to `adopt.take_over`, which either frees it (by
      absorbing the agent memory it holds and parking the original aside) or
      refuses and says why. Only a freed path gets a symlink. `absorb` is
      False when the run has already gated: absorbing moves the adopter's
      own data, which restoring a link does not, so only the link survives.

    Any OS-level failure along this path (permissions, a dangling parent,
    ...) is reported the same way: a WARNING that never gates, because a
    startup hook built on `init` must never break the session over a symlink.

    `raw_path` is outside the repository root, so its record can only ever
    live in the vault (`durability=journal.LOCAL`) -- a repository record
    may never carry an absolute path (ADR 0008,
    docs/design/2026-08-30-the-journal-coverage-and-reversal-design.md
    §7). The previous target is read before the link is touched: once it
    is re-pointed, its former target is gone, which is the preimage
    problem in miniature.

    `session` is None when nothing may be written to the journal -- it
    failed earlier in the run, or the vault holding this record is not
    ignored -- and `unrecorded` says which. The link is restored anyway,
    that being the promise a startup hook rests on, and `_record_symlink`
    turns the missing record into a WARNING that names the previous target,
    so the one fact the mutation destroys is at least on stderr rather than
    nowhere.
    """
    path = Path(raw_path)
    location = path.as_posix()
    project_memory = Path("memory")
    if not project_memory.is_dir():
        return [Finding(WARNING, location, "symlink", NO_PROJECT_MEMORY)]
    target = project_memory.resolve()
    was_symlink = path.is_symlink()
    previous = os.readlink(path) if was_symlink else None

    def relink():
        """Point `path` at `target`, whatever it is now. Never deletes data.

        Atomic, the way the executor publishes the same link: the new link
        is built under a pid-named temporary beside `path` and renamed over
        it, so the path is never absent for an instant. An `unlink`
        followed by a `symlink_to` leaves a window in which the harness has
        no memory at all, and a process killed inside that window leaves it
        that way -- which is the one outcome this whole fail-open path
        exists to prevent.
        docs/design/2026-09-01-the-journal-core.md §4 asks of the link
        module exactly this: that it can publish that one symlink
        atomically and nothing else.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.unlink(missing_ok=True)
        try:
            os.symlink(target, temporary)
            os.replace(temporary, path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    try:
        if was_symlink and path.resolve() == target:
            print(f"init: kept symlink {location}", file=stdout)
            return []
        # A real path that is not a symlink: `adopt` decides whether it holds
        # agent memory this project can absorb, or must be left alone.
        findings = []
        if not was_symlink and path.exists():
            if not absorb:
                return [Finding(WARNING, location, "symlink", UNABSORBED)]
            freed, findings = adopt.take_over(path, target, stdout)
            if not freed:
                return findings
        findings.extend(
            _record_symlink(
                session, path, previous, target, relink, unrecorded
            )
        )
        verb = "re-pointed" if was_symlink else "created"
        print(f"init: {verb} symlink {location} -> {target}", file=stdout)
        return findings
    except OSError as error:
        message = f"could not be linked to '{target}': {error}; session unaffected"
        return [Finding(WARNING, location, "symlink", message)]


def _previous_target(previous):
    """How a symlink's former target is named, in a record and in a WARNING."""
    return f"previous target: {previous}" if previous else "no previous link"


def _record_symlink(session, path, previous, target, relink, unrecorded):
    """Publish the harness link through the executor, or fail open. Returns findings.

    The link is the one mutation `init` performs that the executor may not
    have the last word on.
    docs/design/2026-09-01-the-journal-core.md §4 declares it an
    exception and says exactly why: the contract requires the link to be
    restored when the journal cannot be read or written at all -- that
    is the `SessionStart` hook's only job -- and an executor that
    requires a working journal cannot serve that, while one that accepts
    "record nothing this time"
    is a general bypass wearing a flag. So the record goes through
    `execute` whenever the journal is healthy, and only the repair
    survives when it is not.

    Three answers, and the link is pointing at this project's `memory/`
    after all three:

    - `applied` -- the executor published the symlink itself, atomically,
      and both records are in the vault under one transaction. Nothing is
      reported: the caller prints the line.
    - `refused`, or a journal that cannot be written at all -- a WARNING
      carrying the executor's own message and the previous target, then
      `relink()`, which publishes the same link the same way and records
      nothing. That covers the refusal an unresolved transaction on this
      path produces too, and overriding it is the ruling rather than an
      oversight: the transaction file still holds the previous target, so
      the evidence an operator needs to resolve it is not what the relink
      destroys, and leaving a session without its memory to protect a file
      nothing has read yet is not a trade the `SessionStart` hook may
      make.
    - `session is None` -- nothing may be written to the journal at all
      (it failed earlier in the run, or the vault holding this record is
      not ignored, and `unrecorded` says which). The same WARNING, with
      that reason in place of a message, and the same repair.

    The previous target is what the WARNING carries, because it is what the
    mutation destroys: the `link` op's inverse is "restore the previous
    target", and once the link is re-pointed that fact exists nowhere else.
    The expected state carries it too, so a link something else re-pointed
    between the read and the write is refused rather than recorded against
    a target it never had.

    There is no window in which the path has no link: the executor renames
    a temporary over it, `relink` does the same, and nothing here unlinks
    anything.
    """
    location = path.as_posix()
    note = _previous_target(previous)
    if session is not None:
        expected = (
            {"kind": journal.SYMLINK, "target": previous}
            if previous is not None
            else {"kind": journal.ABSENT}
        )
        try:
            outcome = session.execute(
                journal.link_to(
                    purpose="init",
                    path=location,
                    durability=journal.LOCAL,
                    expected=expected,
                    target=str(target),
                    note=note,
                )
            )
        except (OSError, journal.JournalError) as error:
            unrecorded = getattr(error, "message", None) or str(error)
        else:
            # `noop` cannot be reached from here -- the caller returns
            # early when the link already resolves to `target`, and a link
            # whose own text is `target` resolves to it -- but it means the
            # link is already what this intention would make it, so there
            # is nothing to repair and nothing to report either.
            if outcome.status in (journal.OUTCOME_APPLIED, journal.OUTCOME_NOOP):
                return []
            unrecorded = outcome.message

    relink()
    return [
        Finding(
            WARNING,
            location,
            "journal",
            f"the symlink could not be recorded: {unrecorded} ({note}); "
            "restoring it anyway",
        )
    ]


def _ensure_views(stdout):
    """Create `knowledge.html` and `memory.html` once each.

    Returns `(created, kept, findings)`: the counters `run` adds to its own,
    and the findings it prints from its one central loop. Nothing here
    prints a finding, and `build_artifacts` returns them rather than
    printing them for the same reason -- a finding printed by whoever
    produced it is a finding printed twice, or in the wrong order.

    Same contract as every other item `init` manages: an artifact that
    already exists is reported `kept` and never touched, hand-edited or not.
    Only a missing artifact triggers a build, and that build goes through
    `render.build_artifacts` -- the one place page composition lives, so
    `init --view`, `render`, and the `--only-existing` startup hook never
    each grow their own copy of it. Both artifacts are built together (one
    `build_artifacts` call covers whichever are missing) before either is
    written, matching `render`'s own all-or-nothing write order.

    A corpus `build_artifacts` refuses is folded into the returned findings
    as a WARNING -- `downgrade=True`, the same fail-open mode `render
    --only-existing` uses -- and creates neither artifact; whichever of the
    two already existed is still reported `kept`.

    A write that fails at the OS level (permissions, a full disk, ...) is,
    like `--harness-memory`, a WARNING rather than a crash or a gate: an
    optional flag doing extra work must never break the rest of `init`'s
    run over it. The write itself is atomic -- a temporary file, then a
    rename, the same shape `render.write_if_changed` uses -- so a failure
    can never leave a truncated page that the next run reports `kept`.

    A broken symlink is the one shape `Path.exists()` reads as absent while
    something is plainly there, and installing over it would replace the
    link (see `BROKEN_SYMLINK`). It gets a WARNING and is left untouched --
    a WARNING rather than the ERROR the same shape earns in the scaffold,
    because the views are optional and this whole path is fail-open.
    """
    created = 0
    kept = 0
    findings = []
    missing = [
        name
        for name in render.ARTIFACTS
        if not (Path(name).is_symlink() or Path(name).exists())
    ]
    artifacts = {}
    if missing:
        artifacts, build_findings, ok = render.build_artifacts(downgrade=True)
        findings.extend(build_findings)
        if not ok:
            artifacts = {}
    for name in render.ARTIFACTS:
        path = Path(name)
        if path.is_symlink() and not path.exists():
            findings.append(Finding(WARNING, name, "create", BROKEN_SYMLINK))
            continue
        if path.exists():
            print(f"init: kept {name}", file=stdout)
            kept += 1
            continue
        if name not in artifacts:
            continue
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(artifacts[name], encoding="utf-8")
            os.replace(temporary, path)
        except OSError as error:
            try:
                temporary.unlink()
            except OSError:
                pass
            findings.append(
                Finding(
                    WARNING, name, "create",
                    f"file could not be created: {error}",
                )
            )
            continue
        print(f"init: created {name}", file=stdout)
        created += 1
    return created, kept, findings
