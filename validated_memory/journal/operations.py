"""What a caller asks the executor for, and what it gets back.

Two frozen dataclasses and the vocabularies that bound them: the intention,
refused at construction when its payload disagrees with its op, and the
outcome, which reports a refusal as a result rather than as an exception.
Neither touches the filesystem.
"""

from dataclasses import dataclass

from .records import APPEND, CREATE, DURABILITIES, LINK, OBSERVE, REPLACE
from .paths import ABSENT


INTENTION_OPS = (OBSERVE, CREATE, REPLACE, APPEND, LINK)


@dataclass(frozen=True)
class Intention:
    """One validated, tagged mutation `Run.execute` will consume.

    A frozen dataclass rather than a dict, so a caller building one gets
    the invalid combinations below refused at construction, not discovered
    by the executor three calls later. Field by field:

    - `op` -- one of `INTENTION_OPS`: `OBSERVE`, `CREATE`, `REPLACE`,
      `APPEND` or `LINK`. `PATCH`, `RENAME`, `REMOVE` and `MOVE` are in
      `OPS` for the journal record vocabulary but have no intention shape
      yet; they are not accepted here.
    - `purpose` -- the same free-text word every record already carries
      (`"init"`, `"ignore-rule"`, ...).
    - `path` -- relative to the adopter root for `REPO`, unrestricted for
      `LOCAL` (`authorise`'s rule, ADR 0008).
    - `durability` -- one of `DURABILITIES`.
    - `expected` -- the preimage state, in `current_state`'s vocabulary
      (`{"kind": ABSENT}`, `{"kind": FILE, "digest": ..., "mode": ...}`,
      ...): what the executor's expected-state check compares against.
    - `content` -- `bytes`, or `None`. The full new bytes for `CREATE` of a
      file and `REPLACE`; the bytes to add for `APPEND`. Always `None` for
      `OBSERVE`, `LINK` and `CREATE` of a directory -- this is never a
      diff or a patch, and it is never persisted: `_open_transaction`
      writes the transaction file's `preimage`/`postimage` STATE, never
      these bytes, so payload content never touches the local disk twice.
    - `target` -- the new symlink target, for `LINK`; `None` otherwise.
    - `directory` -- `True` for `CREATE` of a directory; `False` (the
      default) otherwise, including for every op that has no notion of one.
    - `note` -- the free-text annotation both of the mutation's records
      carry; `None` when there is nothing to say. It is the whole payload
      of a `LINK`, whose inverse is "restore the previous target" and
      whose note is the only place that target survives.

    `__post_init__` refuses seven combinations, each a way the payload could
    silently disagree with `op`: a `LINK` carrying `content`, a directory
    `CREATE` carrying `content`, a file `CREATE`/`REPLACE`/`APPEND` carrying
    no `content`, a `LINK` carrying no `target`, an `OBSERVE` carrying any
    payload (`content`, `target` or `directory=True`), a `CREATE` expecting
    anything but `{"kind": ABSENT}`, and an unknown `op` or `durability`.
    Every refusal is `ValueError`: nothing has been touched yet to reach it.

    The `CREATE` rule is what makes "a creation is never a no-op" true by
    construction rather than by inspection of each caller. A create over
    something already there is not a creation -- it is a replacement, and it
    has to say so, because the record is what a reversal reads and the
    inverse of a create is removal.
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
        if self.op == LINK and self.content is not None:
            raise ValueError("a link intention carries no content")
        if self.op == CREATE and self.directory and self.content is not None:
            raise ValueError("a directory creation carries no content")
        if (
            self.op in (CREATE, REPLACE, APPEND)
            and not self.directory
            and self.content is None
        ):
            raise ValueError(f"a {self.op} intention of a file must carry content")
        if self.op == LINK and self.target is None:
            raise ValueError("a link intention must carry its target")
        if self.op == OBSERVE and (
            self.content is not None or self.target is not None or self.directory
        ):
            raise ValueError("an observe intention carries no payload")
        if self.op == CREATE and self.expected != {"kind": ABSENT}:
            raise ValueError(
                "a create intention must expect the path to be absent; "
                f"this one expects {self.expected}"
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
