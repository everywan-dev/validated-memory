"""What a caller asks the executor for, and what it gets back.

Two frozen dataclasses and the vocabularies that bound them: the intention,
which is built by naming the mutation and so cannot be given a payload that
disagrees with it, and the outcome, which reports a refusal as a result
rather than as an exception. Neither touches the filesystem.
"""

from dataclasses import dataclass

from .records import APPEND, CREATE, DURABILITIES, LINK, REPLACE
from .paths import ABSENT


# What an executable mutation may be. `OBSERVE` is not one of them: an
# observation is a fact about a path, not a change to it, it opens no
# transaction and is recorded at `committed` alone
# (docs/design/2026-09-01-the-journal-core.md §4), and `Run.observe` owns
# it end to end. Keeping it here made a shape the executor had to refuse
# at the far end of the call; a vocabulary that cannot say it needs no
# refusal.
INTENTION_OPS = (CREATE, REPLACE, APPEND, LINK)


@dataclass(frozen=True)
class _Intention:
    """One validated, tagged mutation `Run.execute` will consume.

    Built by one of the five functions below and never directly: each names
    a mutation and fixes the fields the other four would need, so a payload
    that disagrees with its op has no spelling here. A frozen record rather
    than a dict, so what the executor reads three calls later is what the
    caller stated.

    The executor is the only reader of the fields, and three of them do not
    say themselves what they are:

    - `expected` -- the preimage state, in `current_state`'s vocabulary
      (`{"kind": ABSENT}`, `{"kind": FILE, "digest": ..., "mode": ...}`,
      ...): what the executor's expected-state check compares against
      before it touches anything.
    - `content` -- the full new bytes for a creation or a replacement, the
      bytes to add for an append. Never a diff, and never persisted:
      `_open_transaction` writes the transaction file's `preimage` and
      `postimage` STATE, never these bytes, so payload content never
      touches the local disk twice.
    - `note` -- the free-text annotation both of the mutation's records
      carry; `None` when there is nothing to say. It is the whole payload
      of a link, whose inverse is "restore the previous target" and whose
      note is the only place that target survives.

    `__post_init__` refuses what naming the mutation cannot fix: an op
    outside `INTENTION_OPS`, an unknown `durability`, a file mutation whose
    `content` is None and a link whose `target` is None. Every refusal is
    `ValueError`: nothing has been touched yet to reach it.
    """

    op: str
    purpose: str
    path: str
    durability: str
    expected: dict
    content: bytes | None = None
    target: str | None = None
    directory: bool = False
    note: str | None = None

    def __post_init__(self):
        if self.op not in INTENTION_OPS:
            raise ValueError(f"unknown op '{self.op}'")
        if self.durability not in DURABILITIES:
            raise ValueError(f"unknown durability '{self.durability}'")
        if (
            self.op in (CREATE, REPLACE, APPEND)
            and not self.directory
            and self.content is None
        ):
            raise ValueError(f"a {self.op} intention of a file must carry content")
        if self.op == LINK and self.target is None:
            raise ValueError("a link intention must carry its target")


def create_file(purpose, path, durability, content, note=None):
    """`content` written at a `path` where there is nothing.

    The expected state is `absent`, and it is not the caller's to state: a
    creation over something already there is not a creation but a
    replacement, and it has to say so, because the record is what a
    reversal reads and the inverse of a create is removal. "A creation is
    never a no-op" is true here by construction rather than by inspection
    of each caller.
    """
    return _Intention(
        op=CREATE,
        purpose=purpose,
        path=path,
        durability=durability,
        expected={"kind": ABSENT},
        content=content,
        note=note,
    )


def create_directory(purpose, path, durability, note=None):
    """A directory made at a `path` where there is nothing.

    No content, because a directory has none, and the same fixed `absent`
    expectation `create_file` gives its reason for.
    """
    return _Intention(
        op=CREATE,
        purpose=purpose,
        path=path,
        durability=durability,
        expected={"kind": ABSENT},
        directory=True,
        note=note,
    )


def replace_file(purpose, path, durability, expected, content, note=None):
    """`content` published over a file whose current state is `expected`.

    The caller states `expected` here, unlike a creation: what is being
    replaced is what the reversal has to put back, so the preimage is part
    of what is being asked for.
    """
    return _Intention(
        op=REPLACE,
        purpose=purpose,
        path=path,
        durability=durability,
        expected=expected,
        content=content,
        note=note,
    )


def append_to_file(purpose, path, durability, expected, content, note=None):
    """`content` added to the end of a file whose current state is `expected`."""
    return _Intention(
        op=APPEND,
        purpose=purpose,
        path=path,
        durability=durability,
        expected=expected,
        content=content,
        note=note,
    )


def link_to(purpose, path, durability, expected, target, note=None):
    """A symlink at `path` pointing at `target`, over the state `expected`.

    `expected` is `{"kind": ABSENT}` for a link that is not there yet and
    `{"kind": SYMLINK, "target": ...}` for one being repointed. The previous
    target survives nowhere else, which is what `note` is for.
    """
    return _Intention(
        op=LINK,
        purpose=purpose,
        path=path,
        durability=durability,
        expected=expected,
        target=target,
        note=note,
    )


OUTCOME_APPLIED = "applied"
OUTCOME_NOOP = "noop"
OUTCOME_REFUSED = "refused"
OUTCOME_STATUSES = (OUTCOME_APPLIED, OUTCOME_NOOP, OUTCOME_REFUSED)


@dataclass(frozen=True)
class Outcome:
    """What `Run.execute` did with one intention, and what it did not.

    A refusal is a RESULT here, not an exception.
    docs/design/2026-09-01-the-journal-core.md §5: a precondition that
    fails before anything is prepared "writes nothing anywhere -- it is a
    result the caller renders, not a transaction", and `init` renders one
    ERROR per item and carries on. `execute` raises only for what it
    cannot express in this shape: a journal that cannot be written at
    all.

    - `status` -- `applied` (the mutation happened and both records are in
      the history), `noop` (the path is already in the state the intention
      asks for, so nothing was written and nothing was recorded) or
      `refused`.
    - `op`, `path`, `durability` -- the intention's, with `path` spelled the
      way `authorise` normalised it and a record will carry it.
    - `transaction` -- the id of the transaction that carried the mutation,
      on an `applied` outcome. None otherwise: a noop and a refusal open no
      transaction, or close the one they opened before returning.
    - `message` -- None unless refused, when it is the sentence the caller
      renders. It always ends by saying what was left untouched, because a
      refusal a user cannot act on is a stopped session.
    - `mode` -- the target's mode after publication for an `applied`
      outcome, the mode it has now for a `noop` or a refusal that found a
      node there, and None where there is none to report -- which includes
      every published symlink, whose `lstat` mode is 0777 and means
      nothing.
    """

    status: str
    op: str
    path: str
    durability: str
    transaction: str | None = None
    message: str | None = None
    mode: int | None = None
