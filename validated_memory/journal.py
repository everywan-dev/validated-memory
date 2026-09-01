"""The append-only record of what `init` does to an adopter project.

Every mutation `init` performs is recorded here as it happens. That is not
yet every mutation the plugin performs: `derive`, `probe`, `render` and
`init --view` write derived artifacts their own commands regenerate, and
they are not recorded -- see "What is recorded, and what is not yet" in
`docs/reference/journal.md` for the list and the plan that closes it.

Two artifacts, because durability is not one question (ADR 0008). The
repository journal `journal.jsonl` travels with the project: it carries the
mutations a clone can see and the history a later run diffs against. The
vault `.validated-memory/` never leaves this clone: it carries preimages,
which may hold bytes the adopter deliberately kept local, and the record of
mutations whose path leaves the repository root.

Both are append-only, one JSON object per line, never rewritten, never
compacted, never sorted -- the same shape `verdicts.jsonl` already uses, for
the same reason: an appended log is the only one that cannot lose history by
accident.

Unlike the verdict log, a journal is NOT regenerable. Nothing re-derives a
preimage or the fact that a path already existed before adoption, so a
reader that cannot parse it must fail loudly rather than serve a partial
answer computed from the lines it happened to understand.
"""

import hashlib
import json
import os
import secrets
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

JOURNAL_FILENAME = "journal.jsonl"
VAULT_DIRNAME = ".validated-memory"
VAULT_JOURNAL = "local.jsonl"
PREIMAGE_DIRNAME = "preimages"
LOCK_FILENAME = "lock"

# The record format. A reader that meets a higher number refuses rather than
# guessing at fields it does not know.
SCHEMA = 1

REPO = "repo"
LOCAL = "local"
DURABILITIES = (REPO, LOCAL)

OBSERVE = "observe"
CREATE = "create"
REPLACE = "replace"
PATCH = "patch"
APPEND = "append"
LINK = "link"
RENAME = "rename"
REMOVE = "remove"
MOVE = "move"
OPS = (OBSERVE, CREATE, REPLACE, PATCH, APPEND, LINK, RENAME, REMOVE, MOVE)

PREPARED = "prepared"
COMMITTED = "committed"
STAGES = (PREPARED, COMMITTED)

COMMON_FIELDS = (
    "schema",
    "at",
    "version",
    "adoption",
    "run",
    "durability",
    "op",
    "purpose",
    "path",
    "stage",
)

# What each field must hold. A journal is repository content, which this
# project's rule makes data and never instructions (design §7): checking that
# a field is present says nothing about what is in it, and every later reader
# -- the schema comparison here, the path the reconciler builds -- assumes a
# type nothing had checked. `bool` is excluded from `int` deliberately:
# `isinstance(True, int)` is true, and `"schema": true` is not a schema.
FIELD_TYPES = {
    "schema": int,
    "at": str,
    "version": str,
    "adoption": str,
    "run": str,
    "durability": str,
    "op": str,
    "purpose": str,
    "path": str,
    "stage": str,
}

# Fields only some ops carry, checked when present for the same reason.
OPTIONAL_FIELD_TYPES = {
    "preimage": (str, type(None)),
    "postimage": (str, type(None)),
    "note": (str,),
    "prior_bytes": (int,),
}


class JournalError(Exception):
    """Raised when a journal cannot be read as records.

    `lineno` is None when the fault is the file's rather than a line's: it
    could not be opened or decoded at all.

    `artifact` is the file the fault is in, relative to the adopter root.
    There are two journals and a lock, and a diagnostic that names the wrong
    one sends a reader to a file that is perfectly valid; None means the
    raiser did not say, and the caller falls back to the repository journal.
    """

    def __init__(self, lineno, message, artifact=None):
        super().__init__(message)
        self.lineno = lineno
        self.message = message
        self.artifact = artifact


def digest(data):
    """The content digest of `data` (bytes), as `sha256:<hex>`."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def now():
    """The current UTC time, ISO-8601 with a trailing 'Z'.

    Same shape `probe` writes into the verdict log, so a reader that already
    parses one parses the other.
    """
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return stamp.replace("+00:00", "Z")


def new_id():
    """A short, collision-resistant identifier for a run or an adoption."""
    return secrets.token_hex(8)


def record(op, purpose, path, durability=REPO, stage=COMMITTED, **extra):
    """One journal record, with its common fields filled in.

    `adoption` and `run` are supplied by the caller through `extra`, because
    an adoption id outlives the process and a run id groups one invocation's
    records: neither is this function's to invent.
    """
    if op not in OPS:
        raise ValueError(f"unknown op '{op}'")
    if durability not in DURABILITIES:
        raise ValueError(f"unknown durability '{durability}'")
    if stage not in STAGES:
        raise ValueError(f"unknown stage '{stage}'")
    entry = {
        "schema": SCHEMA,
        "at": now(),
        "version": __version__,
        "durability": durability,
        "op": op,
        "purpose": purpose,
        "path": path,
        "stage": stage,
    }
    entry.update(extra)
    return entry


def journal_path(root=Path(), durability=REPO):
    """Where the journal of `durability` lives, relative to the adopter root."""
    root = Path(root)
    if durability == LOCAL:
        return root / VAULT_DIRNAME / VAULT_JOURNAL
    return root / JOURNAL_FILENAME


def append(records, root=Path(), durability=REPO):
    """Append `records` to the journal of `durability`, one JSON line each.

    The handle is flushed and fsynced before returning: a `prepared` record
    that is still in a buffer when the process dies is a record that never
    existed, which is precisely the state the two-record protocol exists to
    rule out.
    """
    path = journal_path(root, durability)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for entry in records:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def install(temporary, target):
    """Atomically move `temporary` onto `target`, durably.

    `os.replace` publishes the new bytes under the old name, but the
    directory entry carrying that name is itself buffered. Without the
    directory fsync, a `committed` record that was flushed to disk can
    outlive the rename it describes -- "a record describes a state that
    never existed", one power cut down -- so design §4's claim that a
    `committed` record means the bytes are on disk would hold for a process
    crash and not for a power loss.
    """
    os.replace(temporary, target)
    fsync_directory(Path(target).parent)


def fsync_directory(path):
    """Flush a directory's own entries to disk.

    A platform where a directory cannot be opened for reading skips the
    barrier rather than failing the write it was protecting: the bytes are
    already fsynced and renamed at this point, and refusing here would turn
    a durability improvement into a lost mutation.
    """
    try:
        handle = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)


def artifact_name(durability):
    """The journal of `durability`, named the way a finding names a file."""
    return journal_path(Path(), durability).as_posix()


def read(root=Path(), durability=REPO):
    """Every record in the journal of `durability`, in file order.

    A missing journal reads as no records. A journal that is there but
    cannot be parsed raises: see the module docstring for why a partial
    answer is not offered.

    "Cannot be parsed" is the whole of design §7, not just JSON: a record
    whose field holds the wrong type, whose `durability` disagrees with the
    file it is in, or whose repository-durability path leaves the adopter
    root, is refused here -- before any reader acts on it -- rather than
    crashing one layer down or being read as an instruction.

    "Missing" is exactly one thing: nothing that can be opened at that
    name. `Path.exists()` answers a wider question and answers it wrongly
    for this one -- it raises on a permission denial, which is a stack
    trace out of the check for a missing file. So the journal is OPENED
    first and every question is then asked of the DESCRIPTOR: whether it is
    a regular file, and what bytes it holds. `lstat` then `read_text` are
    two operations on a name, and a name can be repointed between them --
    the bytes read, and afterwards appended to, would then be whatever the
    name meant by the second call, past a check the first one passed.

    A symlink that resolves to a regular file is read like any other
    journal: nothing about reading it is unsafe, `append` writes through it
    by name, and an adopter who keeps the file in a store outside the
    project has a working adoption. A BROKEN symlink reads as absent, which
    is honest -- there is nothing to read through it -- and it is
    `bootstrap` that must not then install over the link, where the
    replacement it would destroy actually happens.
    """
    path = journal_path(root, durability)
    where = artifact_name(durability)
    try:
        # `O_NONBLOCK` so the open cannot hang on the very shape the check
        # below refuses: opening a FIFO for reading waits for a writer, and
        # a reader that never returns is worse than one that refuses. Asked
        # for by name because the flag is POSIX-only, and a platform without
        # it has no FIFOs to hang on either.
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return []
    except OSError as error:
        raise JournalError(
            None, f"journal could not be read: {error}", where
        ) from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise JournalError(
                None,
                "journal is not a regular file; a directory, a device or a "
                "pipe holds no records and nothing here can read one from it",
                where,
            )
        # `closefd=False`: the descriptor has one owner, the `finally` below,
        # so a failure between the two closes it exactly once.
        with open(descriptor, "rb", closefd=False) as handle:
            text = handle.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise JournalError(
            None, f"journal could not be read: {error}", where
        ) from error
    finally:
        os.close(descriptor)
    records = []
    for offset, line in enumerate(text.splitlines()):
        lineno = offset + 1
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as error:
            raise JournalError(
                lineno, f"line is not valid JSON: {error.msg}", where
            )
        if not isinstance(entry, dict):
            raise JournalError(lineno, "record is not a JSON object", where)
        missing = [field for field in COMMON_FIELDS if field not in entry]
        if missing:
            raise JournalError(
                lineno, f"record is missing {', '.join(missing)}", where
            )
        _check_types(lineno, entry, where)
        if entry["schema"] > SCHEMA:
            raise JournalError(
                lineno,
                f"record uses schema {entry['schema']}, newer than this "
                f"plugin understands ({SCHEMA}); upgrade the plugin",
                where,
            )
        if entry["op"] not in OPS:
            raise JournalError(
                lineno, f"record has unknown op '{entry['op']}'", where
            )
        if entry["stage"] not in STAGES:
            raise JournalError(
                lineno, f"record has unknown stage '{entry['stage']}'", where
            )
        if entry["durability"] != durability:
            # `append()` derives the file from the same field `record()`
            # stamps, so the two can only disagree by a hand edit -- and
            # trusting the field would let a `local` record, which may
            # legitimately carry a path outside the root, be smuggled into
            # the versioned journal to lift the check below.
            raise JournalError(
                lineno,
                f"record claims durability '{entry['durability']}' in the "
                f"'{durability}' journal; a record's durability is the file "
                "it lives in",
                where,
            )
        if durability == REPO and not _is_inside_path(entry["path"]):
            raise JournalError(
                lineno,
                f"record path '{entry['path']}' is not inside the adopter "
                "root; a repository record may only carry a relative path "
                "that stays below it",
                where,
            )
        records.append(entry)
    return records


def _check_types(lineno, entry, where):
    """Refuse a record whose field holds something of the wrong type."""
    for field, expected in FIELD_TYPES.items():
        value = entry[field]
        if expected is int and isinstance(value, bool):
            value = None
        if not isinstance(value, expected):
            raise JournalError(
                lineno,
                f"record field '{field}' holds {type(entry[field]).__name__}, "
                f"not {expected.__name__}",
                where,
            )
    for field, expected in OPTIONAL_FIELD_TYPES.items():
        if field in entry and not isinstance(entry[field], expected):
            raise JournalError(
                lineno,
                f"record field '{field}' holds "
                f"{type(entry[field]).__name__}, which it may not",
                where,
            )


def _is_inside_path(path):
    """Whether `path` is relative and names nothing above the adopter root.

    Lexical, because this runs on every record read: `Run.write` applies the
    same rule to what it writes. The filesystem question -- whether the path
    resolves below the root once symlinks are followed -- is asked where
    something is about to be touched: `Run._location` before a write,
    `_state_of` before a read.
    """
    candidate = Path(path)
    return not candidate.is_absolute() and ".." not in candidate.parts


# A lock older than this is broken rather than waited on -- by age alone.
# Nothing checks whether the process that took it is still running, so this
# is a bet that no legitimate holder is slower than five minutes, not a
# liveness test. It holds today because every caller holds the lock for
# milliseconds; a caller that held it across a tree walk would have its lock
# broken under it, and would need the liveness check (`os.kill(pid, 0)` on
# the pid in the file) that is not written yet. Without it, a process killed
# between taking the lock and releasing it would wedge every later run of a
# startup hook.
STALE_LOCK_SECONDS = 300


class Lock:
    """A per-adopter exclusive lock, taken for the duration of a mutation.

    `init` is deliberately re-runnable at session start and concurrent
    renderers are already expected, so two processes appending interleaved
    `prepared` records to one journal is a real state, not a theoretical
    one -- and it would produce a journal describing a state that never
    existed.

    The lock is a file created with `O_CREAT | O_EXCL`, which is atomic. Its
    contents are the owning pid, for a person looking at a lock file that
    should not be there; no code reads them, and the contention message names
    the path rather than the holder.

    Releasing is unconditional: `__exit__` unlinks the path whether or not
    the file there is still the one this object created. A lock broken as
    stale (see `STALE_LOCK_SECONDS`) and then taken by a third process would
    therefore be released twice, once by each. Nothing can reach that state
    today, because no caller holds the lock long enough to be broken.
    """

    def __init__(self, root=Path()):
        self.path = Path(root) / VAULT_DIRNAME / LOCK_FILENAME
        self._fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 10
        while True:
            try:
                self._fd = os.open(
                    self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
                )
                try:
                    os.write(self._fd, f"{os.getpid()}\n".encode("ascii"))
                except OSError:
                    # `__exit__` never runs when `__enter__` raises, so the
                    # descriptor and the lock file have to be released here or
                    # they outlive the failure by the whole stale window.
                    os.close(self._fd)
                    self._fd = None
                    self.path.unlink(missing_ok=True)
                    raise
                return self
            except FileExistsError:
                if self._break_if_stale():
                    continue
                if time.monotonic() >= deadline:
                    raise JournalError(
                        None,
                        f"another validated-memory process holds "
                        f"{self.path.as_posix()}; retry when it finishes",
                        f"{VAULT_DIRNAME}/{LOCK_FILENAME}",
                    )
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, traceback):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self.path.unlink(missing_ok=True)
        return False

    def _break_if_stale(self):
        """Remove the lock when it is older than the stale window.

        Returns whether it broke one. The owner is not consulted: see
        `STALE_LOCK_SECONDS` for what that costs and what would fix it.
        """
        try:
            age = time.time() - self.path.stat().st_mtime
        except FileNotFoundError:
            return True  # It went away on its own; try again immediately.
        if age < STALE_LOCK_SECONDS:
            return False
        self.path.unlink(missing_ok=True)
        return True


def bootstrap(root=Path(), run=None, records=None, local=None):
    """Ensure the journal exists, and return this adoption's id.

    This is the one write that cannot journal itself: a record describing
    the journal's own creation would have nowhere to go until the journal
    exists. So the opening record is written complete to a temporary file,
    flushed, and atomically installed -- before any adopter mutation, so
    there is no window in which a mutation has happened and no journal
    exists to describe it. The temporary is plugin-owned and is not itself
    journalled.

    `run` is the invocation's run id, so the opening record -- minted only
    the first time a project ever bootstraps -- carries the same run id as
    every other record that invocation writes, rather than a run of its own.
    A caller that does not have one yet (there is none besides `Run`) gets
    one minted here, so the record is always complete.

    The caller must already hold `Lock`. This does not take it: `init` holds
    it for the whole run, and re-entering would need a re-entrant lock. Two
    processes bootstrapping the same new adopter without it would mint two
    adoption ids, and the second install would win in silence.

    `records` and `local` are the two journals the caller has already read,
    so a caller that needs the records for something else does not read the
    files twice. Each must be exactly what `read(root, ...)` returned for
    its durability; anything else would mint a second adoption id over a
    journal that already has one.

    Both artifacts are consulted, because only one of them is versioned.
    `journal.jsonl` is tracked and the vault is ignored, so an ordinary
    `git checkout` of a commit from before the adoption takes the journal
    away and leaves the vault: minting again there would file this run's
    records under an id the vault's preimages know nothing about, split one
    adoption in two, and report clean throughout, since no record is
    missing or malformed.
    """
    path = journal_path(root, REPO)
    existing = read(root, REPO) if records is None else records
    kept = read(root, LOCAL) if local is None else local
    adoption = _adoption_id(existing, kept)
    if existing:
        return adoption

    # Nothing was read, so the install below is about to publish the opening
    # record under this NAME -- and `os.replace` replaces a symlink rather
    # than following it. A link here is the adopter's: a broken one reads as
    # absent and a resolvable one carries no records yet, and replacing
    # either destroys something `init` did not create and cannot put back,
    # which is exactly the trade `init.BROKEN_SYMLINK` refuses everywhere
    # else. A link to a journal that HAS records never reaches this line.
    if path.is_symlink():
        raise JournalError(
            None,
            f"{JOURNAL_FILENAME} is a symlink and holds no records; "
            "installing the journal here would replace the link itself, "
            "which is the adopter's and cannot be put back -- point it at a "
            "journal or remove it",
            artifact_name(REPO),
        )

    opening = record(
        OBSERVE,
        "init",
        JOURNAL_FILENAME,
        durability=REPO,
        stage=COMMITTED,
        adoption=adoption,
        run=run if run is not None else new_id(),
        note="journal opened",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(opening, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        install(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return adoption


def _adoption_id(repository, vault):
    """This project's adoption id, from whichever journal still carries one.

    A fresh one only when neither does. Two artifacts carrying DIFFERENT
    ids is a state a user can reach -- a vault copied into another tree, a
    `journal.jsonl` restored from a different clone -- and it is the one
    case nothing here can resolve: the preimages in the vault belong to one
    of the two adoptions and no record says which, so attaching this run to
    either would file it against somebody else's pre-adoption state. It
    refuses and names both, which is the only answer that leaves the user
    able to decide.
    """
    minted = repository[0]["adoption"] if repository else None
    kept = vault[0]["adoption"] if vault else None
    if minted is not None and kept is not None and minted != kept:
        raise JournalError(
            None,
            f"the vault is filed under adoption '{kept}' while "
            f"{JOURNAL_FILENAME} is filed under '{minted}'; one project has "
            "one adoption id, and nothing here can say which of the two is "
            "this project's",
            artifact_name(LOCAL),
        )
    if minted is not None:
        return minted
    return kept if kept is not None else new_id()


class Run:
    """One invocation's journalling context.

    Holds the adoption id and this run's id, and turns a mutation into the
    three steps §4 of the design requires: a flushed `prepared` record
    carrying the preimage and the expected postimage, the atomic mutation,
    then a flushed `committed` record. A `prepared` with no `committed` is
    what `journal --check` reconciles; nothing here guesses at one.

    It does NOT hold the lock. `bootstrap`, which `__init__` calls, requires
    the caller to hold `Lock` already, and so does every mutation below it.
    """

    def __init__(self, root=Path()):
        self.root = Path(root)
        self.run = new_id()
        records = read(self.root, REPO)
        local = read(self.root, LOCAL)
        self.adoption = bootstrap(self.root, self.run, records, local)
        # Every path either journal already carries a record for. `observe`
        # is written on first sight (§2), and first sight is exactly this:
        # a path the record has never mentioned. Keying it on every op
        # rather than on `observe` alone is what stops a path the plugin
        # itself created -- or was interrupted while creating -- from being
        # observed later as a fact about the state adoption found.
        #
        # Both journals are read here, so a vault that cannot be parsed
        # refuses the run rather than being written to blind; `init` keeps
        # the harness symlink working over that failure (`init.run`).
        self._seen = {
            (entry["durability"], entry["path"])
            for entry in records + local
        }

    def _record(self, op, purpose, path, durability, stage, **extra):
        if durability == REPO and not _is_inside_path(path):
            # `read` refuses a repository record whose path leaves the root,
            # so writing one would produce a journal that cannot be read
            # back -- and the whole point of refusing on the read side is
            # that such a record must never be in the file. `write` and
            # `append_text` check the same rule earlier, on the path they
            # are about to touch; this catches the note-only methods, which
            # have no path to touch and so had no check at all. Nothing in
            # the CLI can reach it today: every repository path `init`
            # passes is a literal.
            raise ValueError(
                f"{path} is not a path inside the adopter root; a repository "
                "record may only carry a relative path that stays below it. "
                "Nothing has been recorded."
            )
        return record(
            op,
            purpose,
            path,
            durability=durability,
            stage=stage,
            adoption=self.adoption,
            run=self.run,
            **extra,
        )

    def observe(self, path, note, durability=REPO):
        """Record a pre-adoption fact about `path`, on first sight only.

        Never inverted, and never written twice: a path this journal already
        mentions is not one adoption found there. A second `observe` would
        be a claim about the state before adoption written after the plugin
        had already changed it, and `observe` has no inverse, so nothing
        would ever take it back.
        """
        if (durability, path) in self._seen:
            return
        append(
            [self._record(OBSERVE, "init", path, durability, COMMITTED, note=note)],
            self.root,
            durability,
        )
        self._seen.add((durability, path))

    def park_preimage(self, path):
        """Copy the current bytes of `path` into the vault; return the reference.

        Returns None when `path` does not exist, which is what distinguishes
        a `create` from a `replace`. A preimage is parked only the first
        time a given path is written, because only that copy is the
        pre-adoption state -- a second copy would record an intermediate
        state as if it were the original.
        """
        target = self.root / path
        if not target.exists() or target.is_dir():
            return None
        data = target.read_bytes()
        reference = digest(data)
        blob = (
            self.root
            / VAULT_DIRNAME
            / PREIMAGE_DIRNAME
            / reference.replace("sha256:", "")
        )
        if not blob.exists():
            blob.parent.mkdir(parents=True, exist_ok=True)
            temporary = blob.with_name(f"{blob.name}.{os.getpid()}.tmp")
            # The blob is named after its own digest, so a torn write would
            # leave bytes that silently disagree with the name every later
            # reader trusts. Every other atomic write in this module fsyncs
            # before the rename; this one has to as well.
            with temporary.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            install(temporary, blob)
        return reference

    def _location(self, path):
        """`path` as a record will carry it: relative to the adopter root.

        Written to the journal exactly as given, so a record never carries
        an absolute path -- which §7 of the design refuses to act on later.
        Refusing to write such a record is what keeps it out of the history;
        `read` applies the same rule to what it finds there.

        The lexical rule is only half of it. §7 requires a
        repository-relative record to resolve below the resolved root
        WITHOUT following a symlink out of it, and a lexically perfect
        `memory/MEMORY.md` lands outside the project the moment `memory/`
        is a symlink pointing there -- bytes written outside the repository
        under a record that says they are in it. Nothing later notices:
        `read` is lexical by design, and `_state_of`'s filesystem check
        only ever runs on an UNFINISHED transaction, so a completed escape
        is invisible for the life of the journal. Asked here, before the
        preimage is parked and before the `prepared` record is appended,
        so a refusal leaves nothing written and nothing recorded.

        The refusal is an `OSError` because that is what the caller already
        catches per item (`init._ensure_file`, `init._ensure_ignored`): one
        item gets an ERROR finding and the rest of the run continues, which
        is what a whole-run abort or a traceback would take away.
        """
        location = Path(path).as_posix()
        if not _is_inside_path(location):
            raise ValueError(
                f"{location} is not a path inside the adopter root; a "
                "repository record may only carry a relative path that stays "
                "below it. Nothing has been recorded."
            )
        if not _resolves_below(self.root, self.root / location):
            raise OSError(
                f"{location} resolves outside the adopter root; a repository "
                "record may only name bytes that stay below it. Nothing has "
                "been written and nothing has been recorded."
            )
        if (self.root / location).is_dir():
            raise IsADirectoryError(
                f"{location} is a directory; refusing to journal a file write "
                "against it. Nothing has been recorded."
            )
        return location

    def _write_bytes(self, location, data):
        """Put `data` at `location`, atomically: temporary, fsync, install.

        The temporary is plugin-owned and pid-named, so it is not itself an
        adopter mutation and is not journalled (§4). A failure removes it
        and raises, leaving the target exactly as it was -- which is what
        makes the open `prepared` record the only trace, and an honest one.
        """
        target = self.root / location
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            install(temporary, target)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    def write(self, path, content, purpose, durability=REPO):
        """Create or replace the text file at `path`, journalling both stages."""
        location = self._location(path)
        data = content.encode("utf-8")
        preimage = self.park_preimage(location)
        op = CREATE if preimage is None else REPLACE
        postimage = digest(data)

        append(
            [
                self._record(
                    op,
                    purpose,
                    location,
                    durability,
                    PREPARED,
                    preimage=preimage,
                    postimage=postimage,
                )
            ],
            self.root,
            durability,
        )

        self._write_bytes(location, data)

        append(
            [
                self._record(
                    op,
                    purpose,
                    location,
                    durability,
                    COMMITTED,
                    preimage=preimage,
                    postimage=postimage,
                )
            ],
            self.root,
            durability,
        )
        self._seen.add((durability, location))

    def append_text(self, path, content, purpose, durability=REPO):
        """Append `content` to the text file at `path`, journalling both stages.

        The inverse of an `append` is "truncate to the recorded prior
        length" (§2), so the record carries `prior_bytes` alongside the
        preimage and postimage digests. `preimage` is null when the file did
        not exist at all, exactly as it is for a `create`: the inverse is
        then removing the file, not truncating it to nothing, and only the
        record can say which of the two this was.

        The append is a read-modify-write into a temporary file and an
        atomic install, not an `open(..., "a")`: a torn append would leave
        bytes no postimage describes, which is the state the two-record
        protocol exists to rule out.
        """
        location = self._location(path)
        target = self.root / location
        preimage = self.park_preimage(location)
        existing = target.read_bytes() if preimage is not None else b""
        data = existing + content.encode("utf-8")
        fields = {
            "preimage": preimage,
            "postimage": digest(data),
            "prior_bytes": len(existing),
        }

        append(
            [
                self._record(
                    APPEND, purpose, location, durability, PREPARED, **fields
                )
            ],
            self.root,
            durability,
        )

        self._write_bytes(location, data)

        append(
            [
                self._record(
                    APPEND, purpose, location, durability, COMMITTED, **fields
                )
            ],
            self.root,
            durability,
        )
        self._seen.add((durability, location))

    def prepare_op(self, op, purpose, path, note, durability=REPO):
        """Record that a mutation with no file preimage is about to happen.

        The `committed` half is `append_op`. §4 admits no "mutate first"
        protocol, and a mutation with no bytes to digest is not an
        exception: a `mkdir` that crashes before its record leaves a
        directory nothing knows about, and a re-pointed symlink destroys the
        one fact its own record carries -- the previous target, which is
        what its inverse restores.
        """
        append(
            [self._record(op, purpose, path, durability, PREPARED, note=note)],
            self.root,
            durability,
        )
        self._seen.add((durability, path))

    def append_op(self, op, purpose, path, note, durability=REPO):
        """Close a mutation with no file preimage: the `committed` half."""
        append(
            [self._record(op, purpose, path, durability, COMMITTED, note=note)],
            self.root,
            durability,
        )
        self._seen.add((durability, path))


UNAPPLIED = "unapplied"
APPLIED = "applied"
DIVERGED = "diverged"
UNKNOWN = "unknown"


def reconcile(root=Path()):
    """Every unfinished transaction, paired with the state its path is in.

    Records are paired in file order, not by set membership: one run may
    write the same path more than once, and a `committed` record closes the
    ONE `prepared` record it follows, never every prepared record that
    happens to share its (run, path).

    `unapplied` -- the bytes still match the preimage, so the mutation never
    happened. `applied` -- they match the postimage, so it happened and only
    the closing record was lost. `diverged` -- neither, so something else
    wrote the path afterwards. `unknown` -- the bytes could not be read at
    all, or must not be, so nothing can be said.

    Every unfinished transaction is reported, including the ones this
    reader refuses to follow: a record it may not read is a fact about that
    record, not the end of the pass.

    This reports. It does not repair: choosing for the user between the
    states the record cannot distinguish is exactly the guessing this
    component exists to remove.
    """
    root = Path(root)
    unfinished = []
    for durability in DURABILITIES:
        # Records are paired in file order, not by set membership: one run
        # may write the same path more than once, and a `committed` record
        # closes the ONE `prepared` record it follows, never every prepared
        # record that happens to share its (run, path).
        open_by_key = {}
        for entry in read(root, durability):
            key = (entry["run"], entry["path"])
            if entry["stage"] == PREPARED:
                open_by_key.setdefault(key, []).append(entry)
            elif entry["stage"] == COMMITTED and open_by_key.get(key):
                open_by_key[key].pop(0)
        for entries in open_by_key.values():
            for entry in entries:
                unfinished.append((entry, _state_of(root, entry)))
    return unfinished


def _state_of(root, entry):
    target = root / entry["path"]
    if not _resolves_below(root, target):
        # `read()` already refused a repository record naming a path outside
        # the root, and `Run.write` refuses to write one. What is left is the
        # filesystem's half of the same question: a lexically fine path that
        # resolves out of the root through a symlink, and a vault record,
        # whose path may legitimately leave the root -- design §7 is explicit
        # that such a path "can never be authorised by the file itself" and
        # that acting on it needs a fresh CLI argument naming it. Reading the
        # bytes is acting on it.
        #
        # So it is not read -- and that is precisely what `unknown` says.
        # Raising here ended the whole pass instead, hiding every other
        # unfinished transaction in the project behind one line, and the
        # `local` record of the harness symlink, whose path is absolute BY
        # DESIGN, reached it on any ordinary crash between its two records.
        return UNKNOWN
    if "postimage" not in entry:
        # A mutation with no bytes to digest -- a directory, a symlink.
        # Existence is the whole of its state, so that is what is compared;
        # comparing digests here would read a missing path as `applied`,
        # since both sides would be None.
        return APPLIED if target.exists() or target.is_symlink() else UNAPPLIED
    try:
        actual = digest(target.read_bytes())
    except FileNotFoundError:
        # Genuinely absent. Against a `create` record, whose preimage is
        # null, that is an honest `unapplied`.
        actual = None
    except OSError:
        # A directory, a permission denial, an I/O error: the bytes could
        # not be read, so nothing is known about this path. Saying
        # `unapplied` here would assert the mutation never happened.
        return UNKNOWN
    if actual == entry.get("postimage"):
        return APPLIED
    if actual == entry.get("preimage"):
        return UNAPPLIED
    return DIVERGED


def _resolves_below(root, target):
    """Whether `target` is still inside `root` once every symlink is followed."""
    try:
        return target.resolve().is_relative_to(Path(root).resolve())
    except OSError:
        return False


def run(check, stdout, stderr):
    """The `journal` subcommand: report the record, and optionally reconcile.

    Read-only in both modes. Without `--check` it summarises and exits 0
    whatever it finds, so a reader can look at a project without gating on
    it; with `--check` an unfinished transaction is an ERROR, because a
    caller that asked to be told cannot be told by an exit code of 0.
    """
    from .findings import ERROR, EXIT_ERROR, EXIT_OK, Finding

    root = Path()
    # Accumulated one artifact at a time so the summary below can say how
    # many records were actually read when a later one is refused. Printing
    # a hardcoded 0 there described a project with no history at all, which
    # is a different and much worse fault than the one that happened.
    records = []
    try:
        for durability in DURABILITIES:
            records.extend(read(root, durability))
        # `reconcile` reads both journals again, so it belongs inside this
        # handler: a journal a concurrent writer left unreadable between the
        # two reads must be reported the same way as anything else the
        # reader cannot accept.
        unfinished = reconcile(root) if check else []
    except JournalError as error:
        where = error.artifact or JOURNAL_FILENAME
        location = where if error.lineno is None else f"{where}:{error.lineno}"
        print(Finding(ERROR, location, "journal", error.message).render(), file=stderr)
        print(f"journal: {len(records)} record(s), 1 error(s)", file=stdout)
        return EXIT_ERROR

    if not check:
        print(f"journal: {len(records)} record(s)", file=stdout)
        return EXIT_OK

    for entry, state in unfinished:
        print(
            Finding(
                ERROR,
                entry["path"],
                "journal",
                f"unfinished transaction from run {entry['run']}: "
                f"the path is {state}",
            ).render(),
            file=stderr,
        )
    print(
        f"journal: {len(records)} record(s), {len(unfinished)} error(s)",
        file=stdout,
    )
    return EXIT_ERROR if unfinished else EXIT_OK
