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
so. A take-over that fails after copying may leave those copies in the project;
its warning names that retained state rather than claiming no effect.

A project with no usable `memory/` leaves PATH untouched. A missing, broken,
looping or non-directory node has no directory to point at. A directory that
resolves outside the adopter is ineligible: exposing it would make external
bytes this project's harness memory. The former is a WARNING and the latter an
ERROR, but both are decided before any harness-path action.

A journal that refuses the run does gate -- a required record that is missing
or corrupt is exit 1 (ADR 0008), never a silent continuation -- but it does not
always take an eligible symlink with it: the harness half runs outside the
journalled part of the run, and the record it could not write is reported as a
WARNING naming what was lost. So `init` can exit 1 while the link is back,
which is why the `SessionStart` hook reports success whatever the exit code
(`hooks/restore-memory-symlink.sh`). Whether the link is restored is decided by
`journal.guarded_harness_repair` (ADR 0029), never here: a topology refusal
before any adopting effect, or a journal that cannot be read, restores it when
the vault and the history show that no transaction or condition can own the
harness path; any other refusal withholds it, and the WARNING says why. This
fail-open path restores only missing or existing symlinks. A real harness
directory is never absorbed or parked after the journal gate, because that
moves data rather than restoring a link.

`init` also puts the vault's line in the repository's ignore file, first
and before anything else it does. What that line is, which shapes of ignore
file are left alone, and why `.git/info/exclude` settles two of them are
`ignore.py`'s; what belongs here is what a failure to write it stops.

It is an ERROR that stops the journalled run where it stands. Everything
after it writes into a vault that is then exposed to the next commit, and
absorbing a harness directory -- which moves the adopter's own data --
after a check that gated is further still from anything `init` may do on
its own. So the scaffold, the take-over and the views do not run: the ERROR
names the one line to add by hand, and the next run picks up from a tree
`init` has not touched.

The harness symlink is the one act that outlives that gate when project memory
first resolves inside the adopter, because restoring it moves no data and it is
the whole job of the `SessionStart` hook. Its record is the one that could only
live in the vault, which is precisely what is exposed, so it is not written and
the loss is a WARNING -- exactly the treatment a journal that cannot be written
already gets. An ineligible outside-root project target instead leaves the
harness path untouched with an ERROR. As with a journal failure, this exception
never absorbs or parks a real directory.

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
import stat
import tempfile
import time
from pathlib import Path

from . import adopt, ignore, journal, render
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

# Why a mutation went unrecorded, as `_unrecorded_warning` says it. The link is
# restored in every case, because the failure is the record's and not the
# link's.
UNRECORDED_JOURNAL = "the journal is unavailable"
UNRECORDED_REFUSAL = "the journal refused this run"
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
PROJECT_MEMORY_OUTSIDE = (
    "project memory resolves outside the adopter, so the harness path was "
    "left untouched"
)
UNABSORBED = (
    "already exists and is not a symlink; absorbing it moves the adopter's "
    "own data, which a run that gated may not do, so it was left untouched"
)
# What a withheld link says when `journal.harness_repair_regime` allowed no
# repair at all: a refusal it cannot place before every adopting effect.
UNATTEMPTED_REPAIR = (
    "the journal refused this run and the refusal cannot be shown to leave "
    "the link alone"
)
UNCONFIRMED_EFFECT = (
    "an effect of this run is visible or may be visible, and its durability "
    "is unconfirmed"
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


def run(
    harness_memory,
    view,
    stdout,
    stderr,
    app=False,
    lock_wait=journal.LOCK_WAIT_SECONDS,
):
    """Scaffold the adopter layout under the working directory.

    Returns an exit code: 0 unless an item could not be created, or the
    journal could not be read or written. An OS failure while restoring an
    eligible harness-memory symlink is a WARNING, not an ERROR -- fail-open,
    so the caller (eventually a startup hook) never breaks the session over it.
    Project memory that resolves outside the adopter is instead an ERROR and
    reaches no harness action.
    Same for `view`: a corpus the renderer refuses is a WARNING, never a
    gate -- see `_ensure_views`.

    A journal failure is the one ERROR that is not about a single item, and
    required history that cannot be read is exit 1 (ADR 0008). The exit code
    is the same whether or not the harness link is restored afterwards.
    `journal.harness_repair_regime` classifies the failure and
    `journal.guarded_harness_repair` decides (ADR 0029): only a refusal that
    preceded every adopting effect, a journal that cannot be read, or a lock
    that another process holds or whose path is not a regular file reach it,
    and the last two are always withheld. Any other refusal -- identity,
    bootstrap, uncertainty after an effect -- is withheld without calling it.
    A withheld link is a WARNING naming the harness path and the reason,
    except when the harness step itself raised (its ERROR already names the
    path) or when the link already resolves to `memory/` (nothing is left to
    restore). A real harness directory is never a symlink restoration and
    remains untouched after either outcome.

    The vault's ignore entry is the other ERROR that is not about a single
    item (`_ensure_ignored`), and it gates the same journalled part plus the
    views: nothing that writes into the vault, into the adopter tree, or
    into a directory the adopter owns runs after it. The harness symlink
    still does, without its record, because restoring a link moves no data
    and the `SessionStart` hook has no other job. As with a journal failure,
    this exception never absorbs or parks a real directory.

    What that ordering guarantees, stated exactly: no `local` transaction
    file and no preimage is written while the vault is unignored. A `local`
    path is absolute (ADR 0008), which is what a commit must never carry:
    `run` joins a relative `--harness-memory` to the current directory before
    anything reads it, and neither collapses `..` nor resolves a symlink,
    because the operating system alone knows what `..` after a symlink
    names. The run's only `local` intention -- the harness symlink's -- is
    dropped when this gate fires.
    The entry's OWN transaction is written before it, and may be: it names
    `.gitignore`, relative, with two digests and no bytes, which is what the
    lock file beside it already is. Recovery runs before the gate too, for the
    opposite reason: it only completes or closes what an earlier run began,
    and unlinks the files that said so, so it takes things off the disk
    rather than putting new ones there.

    `lock_wait` is the number of seconds the whole run may spend waiting for
    the run-wide lock. The deadline it fixes is computed here, once, and the
    adopting scope and the harness repair after a refusal are both given that
    same instant, so a run that takes the lock twice does not wait twice. It
    bounds the wait for the lock, not the work done once the lock is held.
    """
    lock_deadline = time.monotonic() + lock_wait
    findings = []
    confirmed = 0
    unignored = False
    adoption_stopped = False
    if harness_memory is not None:
        # Joined, never normalised: `os.path.abspath` would collapse `..` after
        # a symlink to a path the link is not created at.
        harness_memory = os.path.join(os.getcwd(), harness_memory)

    # Everything that journals -- the scaffold and the harness symlink --
    # runs in one adopting scope: `init` is deliberately re-runnable at
    # session start, and the scope serialises the whole run, above all the
    # harness take-over in `_sync_symlink`, which moves an adopter's
    # directory and is not journalled at all.
    journal_failure = None
    # What ended the journalled part of the run, for the harness fallback
    # below, and whether that was the harness step itself, whose ERROR names
    # the harness path.
    refusal = None
    harness_step_failed = False
    try:
        with journal.adopting_run(deadline=lock_deadline) as session:
            # Before anything this run intends: recovery only completes or
            # closes what an EARLIER run began, and unlinks the files that
            # said so, so it reduces what is on disk rather than adding to
            # it -- and an open transaction on `.gitignore` itself is
            # settled before the new `.gitignore` intention is formed. A
            # transaction it cannot account for is an ERROR that gates that
            # ONE path in the recovery report, not the run.
            recovery_findings, recovery_confirmed = _report_recovery(
                session, stdout
            )
            findings.extend(recovery_findings)
            confirmed += recovery_confirmed
            # First, because it is what keeps the vault out of the
            # repository, and the vault is written to from here on.
            if session.path_is_gated(ignore.FILENAME, journal.REPO):
                ignore_findings = []
                unignored = not _vault_is_ignored()
            else:
                ignore_findings, ignore_confirmed = _ensure_ignored(
                    session, stdout
                )
                confirmed += ignore_confirmed
            findings.extend(ignore_findings)
            unignored = unignored or any(
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
                    confirmed += 1

                if harness_memory is not None:
                    link_findings, link_confirmed = _sync_symlink(
                        harness_memory, stdout, session
                    )
                    findings.extend(link_findings)
                    confirmed += link_confirmed
    except _HarnessSyncFailure as failure:
        findings.extend(failure.findings)
        error = failure.error
        harness_step_failed = True
        adoption_stopped = getattr(error, "stops_adoption", False)
        if not getattr(error, "already_reported", False):
            journal_failure = Finding(
                ERROR, _journal_artifact(error), "journal", error.message
            )
    except journal.JournalError as error:
        refusal = error
        adoption_stopped = getattr(error, "stops_adoption", False)
        journal_failure = Finding(
            ERROR, _journal_artifact(error), "journal", error.message
        )
    except OSError as error:
        refusal = error
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
        view_created, view_kept, view_findings = _ensure_views(stdout, app)
        confirmed += view_created + view_kept
        findings.extend(view_findings)

    # The harness link outlives a run that gated, without its record, when the
    # journal's own protocol allows it (ADR 0029). An unignored vault gates the
    # run without a journal refusal: its repair reads the vault the way an
    # unreadable journal's does, and no history. `_sync_symlink`
    # independently validates the project target and never absorbs a real
    # directory here.
    if harness_memory is not None:
        if unignored and not adoption_stopped:
            link_findings, link_confirmed = _sync_symlink(
                harness_memory,
                stdout,
                None,
                # The take-over moves the adopter's own data, so it belongs
                # to the run that gated, not to the promise that survives
                # it: a real directory at the harness path is left alone.
                absorb=False,
                unrecorded=UNRECORDED_VAULT,
                regime=journal.UNAVAILABLE,
                lock_deadline=lock_deadline,
            )
            findings.extend(link_findings)
            confirmed += link_confirmed
        elif journal_failure is not None and not harness_step_failed:
            regime = journal.harness_repair_regime(refusal)
            if regime is None:
                reason = (
                    UNCONFIRMED_EFFECT
                    if getattr(refusal, "visibility_unconfirmed", False)
                    else UNATTEMPTED_REPAIR
                )
                if not _link_is_current(harness_memory):
                    findings.append(_withheld_link(harness_memory, reason))
            else:
                link_findings, link_confirmed = _sync_symlink(
                    harness_memory,
                    stdout,
                    None,
                    absorb=False,
                    unrecorded=(
                        UNRECORDED_REFUSAL
                        if regime == journal.PRE_EFFECT_GATE
                        else UNRECORDED_JOURNAL
                    ),
                    regime=regime,
                    lock_deadline=lock_deadline,
                )
                findings.extend(link_findings)
                confirmed += link_confirmed

    errors = [finding for finding in findings if finding.severity == ERROR]
    for finding in findings:
        print(finding.render(), file=stderr)
    if errors:
        print(
            f"init: {confirmed} item(s) confirmed, {len(errors)} gate(s)",
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
    confirmed = 0
    result = session.recover()
    conditions = {}
    for condition in result.inspection.conditions:
        for pairing in condition.pairing:
            if pairing.startswith("transaction:"):
                conditions.setdefault(pairing, []).append(condition)
    for recovery in result.value:
        if recovery.problem is not None:
            candidates = conditions.get(
                f"transaction:{recovery.transaction}", ()
            )
            condition = next(
                (
                    item for item in candidates
                    if recovery.message.startswith("history reconfirmation")
                    and item.identity.startswith("history.")
                ),
                candidates[-1] if candidates else None,
            )
            subject = condition.subject if condition is not None else (
                recovery.path or recovery.transaction
            )
            if recovery.message.startswith("history reconfirmation"):
                subject = (
                    journal.JOURNAL_FILENAME
                    if recovery.durability == journal.REPO
                    else f"{journal.VAULT_DIRNAME}/local.jsonl"
                )
            findings.append(
                Finding(
                    ERROR,
                    subject,
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
            confirmed += 1
    return findings, confirmed


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

    Returns findings. What the entry is, which shapes are left alone and why
    `.git/info/exclude` settles two of them are `ignore`'s; what is here is
    the order the three answers are asked in, the no-op rule this command
    applies to every mutation it orders (`_refusal`), and the two lines the
    run prints about it.

    It is not counted as an item `init` created or kept. Those counters are
    about the layout `init` owns; this is one line appended to a file the
    adopter owns, and the run reports it on its own line.

    This runs first, and what that buys is precise: while the vault is
    unignored, no `local` transaction file and no history record exists.
    Those are the two that carry what a commit must never carry -- a `local`
    path is absolute by construction (ADR 0008), and a record is versioned.
    The entry's OWN intention opens a `repo` transaction, and may: that file
    names `.gitignore`, relative, with two digests and no bytes of the
    adopter's in it, no more exposed than the lock file already beside it. A
    parked preimage of `.gitignore` may exist too -- an `append` over an
    existing file parks one before the re-read that can still abort the
    mutation -- and it holds bytes the adopter already versioned, which is
    why it is the vault content this gate can afford to be one race short
    about.
    """
    missing, outcome = ignore.write_entry(session)
    if outcome is not None:
        missing = _refusal(
            outcome,
            f"the vault's ignore entry ({ignore.ENTRY}) could not be written",
        )
        if missing is None:
            print(
                f"init: ignored {ignore.ENTRY} in {ignore.FILENAME}",
                file=stdout,
            )
            return [], 1
    if missing is None:
        return [], 0
    if ignore.ignored_elsewhere():
        print(
            f"init: {ignore.ENTRY} already ignored by "
            f"{ignore.EXCLUDE_PATH.as_posix()}",
            file=stdout,
        )
        return [], 1
    return [Finding(ERROR, ignore.FILENAME, "ignore-rule", missing)], 0


def _vault_is_ignored():
    """Read-only safety check used when a WAL gate owns `.gitignore`."""
    try:
        current = Path(ignore.FILENAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        current = ""
    return ignore.carries_entry(current) or ignore.ignored_elsewhere()


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


def _ensure_dir(path, session):
    """Create `path` as a directory if missing. Returns `(item, outcome, finding)`.

    A directory that is already there is recorded as an observation: that it
    pre-existed is a fact about the state before adoption, and nothing can
    re-derive it later. A symlink resolving to a directory inside the adopter
    is an accepted logical container, but the observation names the symlink
    and its resolved adopter-relative target truthfully. The journal's machine
    state remains lstat-based, so this note does not turn the node itself into
    a directory or bypass authorisation of the path and its children.

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
        note = "directory already present"
        if path.is_symlink():
            try:
                relative_target = os.path.relpath(
                    path.resolve(), Path.cwd().resolve()
                )
            except ValueError:
                # A cross-volume target cannot be relative to the adopter.
                # The journal authorisation below still owns the refusal; this
                # placeholder can never be recorded for an authorised path.
                relative_target = "../outside-adopter"
            target = Path(relative_target).as_posix()
            note = (
                "directory symlink already present; resolves inside the "
                f"adopter to '{target}'"
            )
        finding = _observe(session, location, note)
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
    try:
        path.resolve()
    except RuntimeError:
        return location, None, Finding(
            ERROR,
            location,
            "create",
            "the path contains a symlink loop, so it was left untouched",
        )
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
    raw_path,
    stdout,
    session,
    absorb=True,
    unrecorded=UNRECORDED_JOURNAL,
    regime=None,
    lock_deadline=None,
):
    """Make `raw_path` a symlink to this project's `memory/`, without deleting data.

    - No `memory/` in this project: nothing is touched. There is no target
      to point at, and a link to a directory that does not exist is worse
      than none -- the harness reads and writes through this path, so a
      dangling link costs it its memory, where an untouched path leaves it
      its own for a later run to absorb.
    - `memory/` resolves outside this adopter: refuse before inspecting or
      creating the harness path. An in-root directory symlink remains a valid
      logical container, but an outside one must never become the target the
      harness exposes.
    - Missing: create the symlink (making parent directories as needed).
    - Already a symlink (even broken, even pointing elsewhere): re-point it --
      re-pointing a symlink never destroys data, unlike replacing a real path.
    - A real path: handed to `adopt.take_over`, which either frees it (by
      absorbing the agent memory it holds and parking the original aside) or
      refuses and says why. Only a freed path gets a symlink. `absorb` is
      False when the run has already gated: absorbing moves the adopter's
      own data, which restoring a link does not, so only the link survives.

    An ordinary OS-level failure before link publication (permissions, a
    dangling parent, ...) is a WARNING that never gates, because a startup
    hook built on `init` must never break the session over a symlink. The J2
    executor's distinct post-visibility durability uncertainty remains an
    ERROR: the link may already be visible and a clean retry cannot be claimed.

    `raw_path` is absolute and outside the repository root, so its record can
    only ever live in the vault (`durability=journal.LOCAL`) -- a repository
    record may never carry an absolute path (ADR 0008,
    docs/design/2026-08-30-the-journal-coverage-and-reversal-design.md
    §7). The previous target is read before the link is touched: once it
    is re-pointed, its former target is gone, which is the preimage
    problem in miniature.

    `session` is None when nothing may be written to the journal -- it
    failed earlier in the run, or the vault holding this record is not
    ignored -- and `unrecorded` says which. The link is restored anyway,
    that being the promise a startup hook rests on, and the missing record
    becomes a WARNING that names the previous target, so the one fact the
    mutation destroys is at least on stderr rather than nowhere.

    `regime` is set for a run the journal refused, and for one that gated on
    an unignored vault, and hands the decision to
    `journal.guarded_harness_repair` (ADR 0029): it calls `relink`, or does
    not, and a withheld link is a WARNING with the reason and a count of
    zero. A link that already resolves to the target is left alone: there is
    nothing to restore, so there is nothing to guard, and a WARNING that the
    link was not restored would be false. A refused run says nothing of it,
    and a run that gated on the vault reports it `kept`. `lock_deadline` is
    the run's shared lock deadline (`run`), which the guarded repair waits
    for the lock until; it is not read when `regime` is None.

    What stands at the harness path is read once, by `_harness_identity`,
    before anything is done to it, and a path that cannot be looked at, or a
    symlink that cannot be resolved because it loops, is left alone with a
    WARNING. `journal.guarded_harness_repair` has that identity
    read again immediately before it relinks (`_reread_harness`): a link that
    resolves to `memory/` by then is left as it is and not reported, like one
    that was correct at the start, and any other change withholds the repair.
    """
    path = Path(raw_path)
    location = path.as_posix()
    parked = None
    take_over_effect = adopt.TakeOverEffect.NONE
    findings = []
    project_memory = Path("memory")
    try:
        if not project_memory.is_dir():
            return [Finding(WARNING, location, "symlink", NO_PROJECT_MEMORY)], 0
        target = project_memory.resolve()
        adopter_root = Path().resolve()
    except (OSError, RuntimeError):
        return [Finding(WARNING, location, "symlink", NO_PROJECT_MEMORY)], 0
    if not target.is_relative_to(adopter_root):
        return [Finding(ERROR, location, "symlink", PROJECT_MEMORY_OUTSIDE)], 0

    # Recovery has already rendered the one canonical finding for this exact
    # path. Consult that retained authority before even inspecting the harness
    # leaf: a directory must not be copied, indexed, parked or removed, and an
    # already-correct link must not be republished as a no-op.
    if session is not None and session.path_is_gated(location, journal.LOCAL):
        stopped = journal.JournalError(
            None,
            f"{location} has an unresolved transaction; no harness action "
            "was attempted",
            location,
        )
        stopped.stops_adoption = True
        stopped.already_reported = True
        raise _HarnessSyncFailure(stopped, ())

    # The target is eligible before the harness leaf is inspected. That
    # ordering is the rule: an invalid project target may not create the
    # external parent, read or replace its leaf, begin take-over, or form a
    # LOCAL intention.
    try:
        identity = _harness_identity(path)
    except OSError as error:
        return [
            _withheld_link(
                location, f"{HARNESS_UNREADABLE}: {error}", journal_hint=False
            )
        ], 0
    was_symlink = identity[0] == stat.S_IFLNK
    previous = identity[1]
    resolved = None
    if was_symlink:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError) as error:
            return [
                _withheld_link(
                    location, f"{HARNESS_UNREADABLE}: {error}", journal_hint=False
                )
            ], 0

    def recheck():
        return _reread_harness(path, identity)

    def relink(before_replace=None):
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

        `before_replace` is what the guarded repair passes so that the
        harness path is read again once the link is staged, immediately
        before the rename (`journal.repair_harness_link`); a run that holds
        the lock through its own session passes none.
        """
        journal.repair_harness_link(path, target, Path(), before_replace)

    try:
        if was_symlink and resolved == target:
            if regime is None:
                relink()
            elif unrecorded != UNRECORDED_VAULT:
                return [], 0
            print(f"init: kept symlink {location}", file=stdout)
            return [], 1
        # A real path that is not a symlink: `adopt` decides whether it holds
        # agent memory this project can absorb, or must be left alone.
        if identity != NOTHING_THERE and not was_symlink:
            if not absorb:
                return [Finding(WARNING, location, "symlink", UNABSORBED)], 0
            take_over = adopt.take_over(path, target, stdout)
            findings = list(take_over.findings)
            parked = take_over.parked
            take_over_effect = take_over.effect
            if not take_over.freed:
                return findings, 0
        if regime is None:
            findings.extend(_record_symlink(session, path, previous, target))
        else:
            outcome, reason = journal.guarded_harness_repair(
                Path(), path, regime, relink, recheck, lock_deadline
            )
            if outcome == journal.REPAIR_CURRENT:
                if unrecorded != UNRECORDED_VAULT:
                    return [], 0
                print(f"init: kept symlink {location}", file=stdout)
                return [], 1
            if outcome == journal.REPAIR_WITHHELD:
                return [_withheld_link(location, reason)], 0
            if outcome == journal.REPAIR_BLOCKED:
                return [_withheld_link(location, reason, journal_hint=False)], 0
            findings.append(_unrecorded_warning(location, previous, unrecorded))
        verb = "re-pointed" if was_symlink else "created"
        print(f"init: {verb} symlink {location} -> {target}", file=stdout)
        return findings, 1
    except journal.JournalError as error:
        if (
            getattr(error, "visibility_unconfirmed", False)
            or getattr(error, "stops_adoption", False)
        ):
            if session is not None:
                if take_over_effect is not adopt.TakeOverEffect.NONE:
                    stopped = journal.JournalError(
                        None,
                        _link_failure_message(
                            target, error, take_over_effect, parked
                        ),
                        location,
                    )
                    stopped.stops_adoption = True
                    stopped.already_reported = getattr(
                        error, "already_reported", False
                    )
                    error = stopped
                raise _HarnessSyncFailure(error, findings) from error
            return [
                *findings,
                Finding(ERROR, location, "symlink", error.message),
            ], 0
        message = _link_failure_message(
            target, error, take_over_effect, parked
        )
        return [*findings, Finding(WARNING, location, "symlink", message)], 0
    except OSError as error:
        message = _link_failure_message(
            target, error, take_over_effect, parked
        )
        return [*findings, Finding(WARNING, location, "symlink", message)], 0


class _HarnessSyncFailure(Exception):
    """Carry pre-publication findings through a gating journal failure."""

    def __init__(self, error, findings):
        super().__init__(error.message)
        self.error = error
        self.findings = tuple(findings)


def _link_failure_message(target, error, take_over_effect, parked):
    """Name recovery truth after take-over, or retain the ordinary warning."""
    detail = str(error).rstrip(".")
    if take_over_effect is not adopt.TakeOverEffect.NONE:
        detail = detail.replace(
            "Nothing has been published", "The harness link was not published"
        )
    prefix = f"could not be linked to '{target}': {detail}"
    if take_over_effect is adopt.TakeOverEffect.PARKED:
        return (
            f"{prefix}; harness memory was parked at '{parked}' and remains "
            "there for recovery"
        )
    if take_over_effect is adopt.TakeOverEffect.EMPTY_REMOVED:
        return (
            f"{prefix}; the empty harness directory was removed, no memory "
            "data was parked, and the harness path is absent; clear the link "
            "publication error and rerun init"
        )
    return f"{prefix}; session unaffected"


def _previous_target(previous):
    """How a symlink's former target is named, in a record and in a WARNING."""
    return f"previous target: {previous}" if previous else "no previous link"


def _unrecorded_warning(location, previous, unrecorded):
    """The WARNING for a link restored without its record."""
    return Finding(
        WARNING,
        location,
        "journal",
        f"the symlink could not be recorded: {unrecorded} "
        f"({_previous_target(previous)}); restoring it anyway",
    )


def _withheld_link(location, reason, journal_hint=True):
    """The WARNING for a link left as it was, with what left it.

    A reason that comes from the journal ends by sending the reader to
    `journal --check`. One about the harness path itself does not, because the
    journal has nothing to say about it (`journal_hint=False`).
    """
    hint = "; run journal --check" if journal_hint else ""
    return Finding(
        WARNING,
        Path(location).as_posix(),
        "symlink",
        f"the harness link was not restored: {reason}{hint}",
    )


# What stands at the harness path when nothing does, in `_harness_identity`'s
# words.
NOTHING_THERE = (None, None)
HARNESS_UNREADABLE = "the harness path could not be read"
HARNESS_CHANGED = "the harness path changed while the repair waited"


def _harness_identity(path):
    """What stands at `path`, from one `lstat`: `(file type, link target)`.

    The file type is `stat.S_IFMT` of that `lstat`, and the target is the
    link's text when the type is a symlink and None otherwise; `NOTHING_THERE`
    is a path with no entry. No device or inode number is part of it: a
    session that publishes the same link again creates a new inode, which is
    no change here, and inode numbers are not stable on every filesystem.
    Raises `OSError` when the entry cannot be looked at for a reason other
    than being absent.
    """
    try:
        kind = stat.S_IFMT(os.lstat(path).st_mode)
        return kind, (os.readlink(path) if kind == stat.S_IFLNK else None)
    except FileNotFoundError:
        return NOTHING_THERE


def _reread_harness(path, inspected):
    """Read the harness path again for `journal.guarded_harness_repair`.

    Called immediately before the relink, it compares what stands at `path`
    with the `inspected` identity the run recorded before it waited for the
    lock. A path that resolves to `memory/` has nothing left to restore
    (`REPAIR_CURRENT`), whether or not what stands there changed: the link text
    can be the inspected one while the directory it names now leads to
    `memory/`. Otherwise None while the identities are equal, and any other
    difference, or a path that cannot be looked at, blocks the relink
    (`REPAIR_BLOCKED`).
    """
    try:
        now = _harness_identity(path)
    except OSError as error:
        return journal.REPAIR_BLOCKED, f"{HARNESS_UNREADABLE}: {error}"
    if _link_is_current(path):
        return journal.REPAIR_CURRENT, None
    if now == inspected:
        return None
    return journal.REPAIR_BLOCKED, HARNESS_CHANGED


def _link_is_current(raw_path):
    """Whether `raw_path` is a symlink that already resolves to `memory/`.

    A link that is current has nothing to restore, so a refused run says
    nothing about it.
    """
    path = Path(raw_path)
    project_memory = Path("memory")
    try:
        return (
            project_memory.is_dir()
            and path.is_symlink()
            and path.resolve() == project_memory.resolve()
        )
    except (OSError, RuntimeError):
        return False


def _record_symlink(session, path, previous, target):
    """Publish the link through the live session, recording it.

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
    survives when it is not. A run the journal refused, or one that gated on
    an unignored vault, never comes here: `_sync_symlink` gives it to
    `journal.guarded_harness_repair` (ADR 0029), which narrows §4 and reads
    the vault under the lock.

    There are two outcomes:

    - `applied` -- the executor published the symlink itself, atomically,
      and both records are in the vault under one transaction. Nothing is
      reported: the caller prints the line.
    - `refused` is a gate. In particular, an unresolved transaction on this
      exact path retains authority over its preimage; neither the link nor
      the WAL is changed and no unrecorded repair runs.

    The previous target is what the record carries, because it is what the
    mutation destroys: the `link` op's inverse is "restore the previous
    target", and once the link is re-pointed that fact exists nowhere else.
    The expected state carries it too, so a link something else re-pointed
    between the read and the write is refused rather than recorded against
    a target it never had.

    There is no window in which the path has no link: the executor renames
    a temporary over it, and nothing here unlinks anything.
    """
    location = path.as_posix()
    note = _previous_target(previous)
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
        if isinstance(error, journal.JournalError):
            error.stops_adoption = True
            raise
        stopped = journal.JournalError(
            None,
            f"the harness link record was refused before publication: {error}",
            location,
        )
        stopped.stops_adoption = True
        raise stopped from error
    # `noop` cannot be reached from here -- the caller returns early when the
    # link already resolves to `target`, and a link whose own text is
    # `target` resolves to it -- but it means the link is already what this
    # intention would make it, so there is nothing to repair and nothing to
    # report either.
    if outcome.status in (journal.OUTCOME_APPLIED, journal.OUTCOME_NOOP):
        return []
    stopped = journal.JournalError(None, outcome.message, location)
    stopped.stops_adoption = True
    stopped.already_reported = session.path_is_gated(location, journal.LOCAL)
    raise stopped


def _ensure_views(stdout, app=False):
    """Create canonical pages and the selected optional app once each.

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
    each grow their own copy of it. Selected artifacts are built together (one
    `build_artifacts` call covers whichever are missing) before publication.
    Writes are atomic per artifact, not across the selected set.

    A corpus `build_artifacts` refuses is folded into the returned findings
    as a WARNING -- `downgrade=True`, the same fail-open mode `render
    --only-existing` uses -- and creates no artifacts; existing selected pages
    are still reported `kept`.

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
    selected = render.ARTIFACTS + ((render.APP_ARTIFACT,) if app else ())
    missing = [
        name
        for name in selected
        if not (Path(name).is_symlink() or Path(name).exists())
    ]
    artifacts = {}
    if missing:
        artifacts, build_findings, ok = render.build_artifacts(
            downgrade=True, include_app=app
        )
        findings.extend(build_findings)
        if not ok:
            artifacts = {}
    for name in selected:
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
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
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
