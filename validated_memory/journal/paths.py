"""What is at one path, what may be written to it, and how it is described.

The state vocabulary and everything that reads or rules on a single path:
what `lstat` says is there, whether that satisfies what a caller expected,
whether a record may name it at all, whether this user may write over it,
whether a directory under the vault is really this plugin's own, and the
words a refusal uses for it. Four of these do I/O -- `current_state`,
`authorise`, `_write_denied` and `_own_directory` all touch the filesystem
-- so none of them is a pure helper. None of them writes.
"""

import os
import stat
from pathlib import Path

from .records import LINK, REPO, VAULT_DIRNAME, JournalError, _is_inside_path, digest


# The state a path is expected to be in, or found to be in -- lstat
# semantics throughout, so a symlink is a fact about itself, never about
# what it points at. `absent` needs no other field; `directory`, `file` and
# `symlink` may each carry a `mode` (see `satisfies`); `file` also carries a
# content `digest`, `symlink` also carries its `target` (a `readlink`).
ABSENT = "absent"
DIRECTORY = "directory"
FILE = "file"
SYMLINK = "symlink"
KINDS = (ABSENT, DIRECTORY, FILE, SYMLINK)


def current_state(root, path):
    """What is actually at `path` (under `root`), in the vocabulary above.

    `lstat`, never `stat`: the node itself is what is reported, so a symlink
    is `symlink` whether or not it resolves -- a broken one is `symlink`,
    never `absent` or `directory`. `directory` exists because a `create`
    record with no bytes to digest needs a check richer than "the name
    resolves to something", which a broken symlink also satisfies
    (docs/design/2026-09-01-the-journal-core.md §6).

    Anything `lstat` cannot see at all -- nothing there, a missing parent, a
    parent that denies traversal -- reads as `absent`; this function asks
    one question, "what is at this exact name", and every reason `lstat`
    has for not answering it collapses to the same "nothing was found"
    here. A regular file's bytes are read for its digest; a node that is
    neither a directory, a symlink nor a regular file (a FIFO, a socket, a
    device -- nothing this project ever creates) is reported as `file`
    without a `digest`, since reading through it could block forever and
    the vocabulary has no fifth word for it.

    Always carries `mode`, on every kind but `absent`: this is the actual
    side of a `satisfies` comparison, and the "any mode matches" case has to
    have something to compare away, not the absence of a comparison.
    """
    target = Path(root) / path
    try:
        info = os.lstat(target)
    except OSError:
        return {"kind": ABSENT}
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISDIR(info.st_mode):
        return {"kind": DIRECTORY, "mode": mode}
    if stat.S_ISLNK(info.st_mode):
        return {"kind": SYMLINK, "target": os.readlink(target), "mode": mode}
    if stat.S_ISREG(info.st_mode):
        return {"kind": FILE, "digest": digest(target.read_bytes()), "mode": mode}
    return {"kind": FILE, "mode": mode}


def satisfies(actual, expected):
    """Whether `actual` (from `current_state`) matches `expected`.

    Every field `expected` names must equal the same field of `actual`,
    except `mode`: an expected state that omits it matches any mode, and one
    that carries it must match exactly. This is one comparison for every
    kind rather than a branch per kind, because the fields that matter
    already differ by kind (`digest` for `file`, `target` for `symlink`) and
    `expected` only ever names the ones its own kind carries.
    """
    for field, value in expected.items():
        if field != "mode" and actual.get(field) != value:
            return False
    if "mode" in expected and actual.get("mode") != expected["mode"]:
        return False
    return True


def _describe(state):
    """A state in the words a refusal uses, not in the words a record uses.

    A digest and a mode are what the transaction file carries; a person
    reading an ERROR needs to know what is at the path, so this says the
    kind and, for a symlink, where it points -- the one field whose value
    changes what the reader should do next.
    """
    kind = state.get("kind")
    if kind == ABSENT:
        return "absent"
    if kind == DIRECTORY:
        return "a directory"
    if kind == SYMLINK:
        return f"a symlink to '{state.get('target')}'"
    return "a file"


def _postimage_state(intention, actual, data):
    """The state `intention` will leave at its path, in `current_state`'s words.

    Computed here rather than in `_open_transaction`, for the reason that
    function's docstring gives: an `append`'s digest needs the bytes already
    on disk, and only the executor has read them. `data` is the full new
    bytes publication will write, or None for a mutation that has none.

    `mode` is carried only where publication preserves one. A replacement
    keeps the target's mode
    (docs/design/2026-09-01-the-journal-core.md §7), so the postimage can
    name it and recovery can check it; a creation's mode is the umask's
    answer and is not known until the node exists, so the field is
    omitted and `satisfies` then matches whatever mode it turns out to
    have.
    """
    if intention.op == LINK:
        return {"kind": SYMLINK, "target": intention.target}
    if intention.directory:
        return {"kind": DIRECTORY}
    state = {"kind": FILE, "digest": digest(data)}
    if actual.get("kind") == FILE:
        state["mode"] = actual["mode"]
    return state


def _write_denied(root, location, actual):
    """Why this user may not write over `location`, or None when it may.

    The read-only bit is how an adopter says do not write here, and no
    other check in the install path sees it: `os.replace` needs write
    permission on the DIRECTORY, not on the file, so a file at mode 0444 is
    replaceable without anything noticing, and this is the one place that
    notices (docs/design/2026-09-01-the-journal-core.md §1, measured -- the
    mode came back 0644 too, which the mode-preserving install has since
    fixed).

    The question is asked of the file's own mode bits and the POSIX class
    this process falls in -- owner, else group, else other -- and of nothing
    else. `os.access` is not used: it answers for the REAL uid and its own
    documentation warns against using it to decide whether an operation will
    succeed. There is no exception for root: a process that can write
    anywhere still gets the refusal, because the bit is a statement of
    intent by the adopter and this is the one place that reads it.

    Only a regular file is asked about. An absent path has no mode to deny
    with, a symlink's mode means nothing on the platforms this runs on, and
    a directory is never published over.
    """
    if actual.get("kind") != FILE:
        return None
    try:
        info = os.lstat(Path(root) / location)
    except OSError:
        # The node went away between the check and here; the publication
        # below will fail or the re-read will refuse, both with a better
        # message than a guess made from nothing.
        return None
    if info.st_uid == os.geteuid():
        bit = stat.S_IWUSR
    elif info.st_gid in {os.getegid(), *os.getgroups()}:
        bit = stat.S_IWGRP
    else:
        bit = stat.S_IWOTH
    if stat.S_IMODE(info.st_mode) & bit:
        return None
    return (
        f"{location} is mode {stat.S_IMODE(info.st_mode):04o}, which denies "
        "writing to this user. Nothing has been written."
    )


def authorise(root, path, durability):
    """The location a record for `path` may carry, or refuse it.

    `local` asks neither question: the vault and the harness symlink
    legitimately name paths outside the adopter root (ADR 0008), so nothing
    here may narrow what a `local` record can say, and `path` comes back
    exactly as given.

    A `repo` intention asks both, in order:

    - Lexical -- `path` is relative and does not climb out with `..`
      (`_is_inside_path`). `ValueError`, because nothing was touched to
      find that out.
    - Resolved -- the location, joined to `root`, still resolves below
      `root` once every symlink on the way is followed (`_resolves_below`).
      `OSError`, because a caller may already have read something (a
      preimage, an existing record) to reach this line.

    The two exception types are load-bearing, not incidental: `init` catches
    `OSError` per item, so a path that escapes through a symlink gates the
    one item that named it and nothing else.

    Called once, at the very start of each of the two public `Run` methods
    that can reach the journal -- `observe` and `execute` -- before anything
    is parked, appended or written, so the resolved question is asked for an
    observation as well as for a write: a `memory/` symlinked to a directory
    outside the project is lexically fine, and an `observe` that skipped this
    would file it into the versioned journal as a fact about a tree whose
    bytes were never inside it.

    Deliberately NOT called from `_record`: that helper builds both halves
    of a mutation, and `execute` appends the two together AFTER publication.
    Asking the resolved question there would refuse a mutation that has
    already happened, which is not a refusal at all -- it is a published
    write with no record, the one state this protocol exists to rule out.

    What this does NOT provide: `dir_fd`-relative ancestor stabilisation.
    Both checks resolve `path` by name, and nothing stops a hostile process
    from swapping an ancestor directory for a symlink between this call
    returning and the action that follows it acting on the same name --
    that window is real, this project's test seam cannot demonstrate it,
    and closing it needs the executor's own descriptor-relative operations
    rather than a second `resolve()` here; it is the one precondition
    docs/design/2026-09-01-the-journal-core.md §6 names and this step does
    not build.
    """
    if durability != REPO:
        return path
    location = Path(path).as_posix()
    if not _is_inside_path(location):
        raise ValueError(
            f"{location} is not a path inside the adopter root; a "
            "repository record may only carry a relative path that stays "
            "below it. Nothing has been recorded."
        )
    if not _resolves_below(Path(root), Path(root) / location):
        raise OSError(
            f"{location} resolves outside the adopter root; a repository "
            "record may only name bytes that stay below it. Nothing has "
            "been written and nothing has been recorded."
        )
    return location


def _own_directory(root, name):
    """The vault directory `name`, refused unless it is a real directory.

    `lstat`, and asked BEFORE anything is created, written, replaced or
    unlinked through the name. The two directories this plugin owns under
    the vault -- the write-ahead log and the preimage store -- are written
    to by name, and `mkdir(exist_ok=True)`, `open` and `os.replace` all
    follow a symlink standing where one of them should be without a word:
    the transaction file, or the only copy of the bytes about to be
    overwritten, lands wherever the link points, outside the adopter root
    and outside everything this project promises. A plain file there is the
    other half of the same question, and it reached `iterdir` as a
    traceback.

    A name that is not there at all is fine: the directory is created on
    first use, by the caller this has just told the name is free.

    `.validated-memory/` itself is not checked here. It is the vault, whose
    own name an adopter may legitimately have made a link into a shared
    store -- the same freedom `journal.jsonl` has (`lock_path`) -- and what
    this refuses is a name inside it that the plugin alone writes.
    """
    path = Path(root) / VAULT_DIRNAME / name
    artifact = f"{VAULT_DIRNAME}/{name}"
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return path
    except OSError as error:
        raise JournalError(None, f"{artifact} could not be read: {error}", artifact)
    if not stat.S_ISDIR(info.st_mode):
        found = "a symlink" if stat.S_ISLNK(info.st_mode) else "not a directory"
        raise JournalError(
            None,
            f"{artifact} is {found}, and this plugin writes what it owns "
            "only into a real directory of its own: everything under that "
            "name is created, written and removed by name, and a name that "
            "is somebody else's carries all of it somewhere this project "
            "promises nothing about. Move it aside.",
            artifact,
        )
    return path


def _well_formed_state(state):
    """Whether `state` is a state dict in `current_state`'s own vocabulary.

    The kind, and the type of every field a kind carries. A transaction
    file is data
    (docs/design/2026-08-30-the-journal-coverage-and-reversal-design.md
    §7), and every reader downstream of this one -- `satisfies`,
    `_describe`, `_restore`, which puts a mode back and reads a `target`
    -- assumes types nothing had checked: a `digest` that is a number
    matches no state and silently diverges, a `target` that is a list
    reaches `symlink_to`, and `"mode": true` is not a mode.
    `bool` is excluded from `int` for the reason `FIELD_TYPES` gives.
    """
    if not isinstance(state, dict) or state.get("kind") not in KINDS:
        return False
    for field, expected in (("digest", str), ("target", str), ("mode", int)):
        value = state.get(field)
        if value is None:
            continue
        if not isinstance(value, expected) or isinstance(value, bool):
            return False
    return True


def _resolves_below(root, target):
    """Whether `target` is still inside `root` once every symlink is followed."""
    try:
        return target.resolve().is_relative_to(Path(root).resolve())
    except OSError:
        return False
