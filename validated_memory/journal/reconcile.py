"""The two permanent journals, read against each other and against the tree.

Which `prepared` record nothing ever closed, which closed pair disagrees
with itself, and which id is not a pair at all -- and, for each unfinished
one, what state its path is in. This reports; `Run.recover` repairs, from
the write-ahead log rather than from here.
"""

from pathlib import Path

from .paths import DIRECTORY, _resolves_below, current_state
from .records import COMMITTED, CREATE, DURABILITIES, PREPARED, STAGES, digest, read


UNAPPLIED = "unapplied"
APPLIED = "applied"
DIVERGED = "diverged"
UNKNOWN = "unknown"


# What both halves of one mutation must say identically. `at` is excluded
# because the two records are written in one `append` but stamped
# separately, `stage` because it is what tells them apart, and `run`,
# `adoption`, `schema` and `version` because `_record` fills them in from
# one source for both. What is left is everything the mutation itself
# decided -- `purpose` included, which is the tag every reader groups a
# mutation under and which `_record` takes from the intention for both
# halves -- and a `committed` half that disagrees with its `prepared` half
# describes a mutation nobody performed.
PAIRED_FIELDS = (
    "op",
    "purpose",
    "path",
    "durability",
    "preimage",
    "postimage",
    "note",
    "prior_bytes",
    "mode",
)


def reconcile(root=Path()):
    """Every unfinished transaction, disagreeing pair and broken pair.

    Returns `(unfinished, disagreements, anomalies)`: `(record, state)`
    pairs for the `prepared` records nothing ever closed, `(transaction,
    field, record)` triples for the closed pairs whose two halves do not
    say the same thing, and `(message, record)` pairs for the ids that are
    not a pair at all.

    An id-carrying record is a half of exactly one act, and there are two
    ways a history can say otherwise. A `committed` half with no `prepared`
    half before it describes a mutation whose write-ahead half was never
    written or has been removed -- no writer in this package produces one,
    so it is a hand edit or a torn merge, and it was accepted in silence. A
    transaction recorded more than twice is the doubled record recovery
    exists to avoid appending: the id is minted per mutation, so a third
    line under it is a mutation counted twice in a file nothing takes back.
    Both are reported, neither is repaired.

    Pairing is by TRANSACTION ID wherever both records carry one, which is
    every mutation the executor has written since it took over the protocol:
    the id is minted per mutation, so it says which `committed` closes which
    `prepared` without any inference at all. Records without the field --
    everything a history written before the executor holds -- keep the
    older rule: file order within a (run, path), so a `committed` record
    closes the ONE `prepared` record it follows and never every prepared
    record that happens to share its key.

    A pair that agrees on nothing but its id is not a pair. `PAIRED_FIELDS`
    is checked on every id-matched pair, because the two records are the
    only evidence a mutation left behind and a reader that averages them is
    a reader inventing a third mutation. A disagreement is reported; it is
    never resolved by preferring one half.

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
    component exists to remove. Repair is `Run.recover`, which reads the
    write-ahead log rather than these two journals, and only ever closes
    what that log accounts for.
    """
    root = Path(root)
    unfinished = []
    disagreements = []
    anomalies = []
    for durability in DURABILITIES:
        open_by_id = {}
        open_by_key = {}
        # Every record carrying each id, in file order: the count says
        # whether the id names one act, and the first of them is what an
        # anomaly about the id as a whole is reported against.
        by_id = {}
        for entry in read(root, durability):
            transaction = entry.get("transaction")
            if isinstance(transaction, str):
                by_id.setdefault(transaction, []).append(entry)
                if entry["stage"] == PREPARED:
                    open_by_id.setdefault(transaction, []).append(entry)
                elif entry["stage"] == COMMITTED:
                    if open_by_id.get(transaction):
                        prepared = open_by_id[transaction].pop(0)
                        disagreements.extend(
                            (transaction, field, entry)
                            for field in PAIRED_FIELDS
                            if prepared.get(field) != entry.get(field)
                        )
                    else:
                        anomalies.append(
                            (
                                f"records of transaction {transaction}: "
                                "committed without a prepared half",
                                entry,
                            )
                        )
                continue
            key = (entry["run"], entry["path"])
            if entry["stage"] == PREPARED:
                open_by_key.setdefault(key, []).append(entry)
            elif entry["stage"] == COMMITTED and open_by_key.get(key):
                open_by_key[key].pop(0)
        for transaction, entries in by_id.items():
            if len(entries) > len(STAGES):
                anomalies.append(
                    (
                        f"transaction {transaction} is recorded "
                        f"{len(entries)} times",
                        entries[0],
                    )
                )
        for group in (open_by_id, open_by_key):
            for entries in group.values():
                for entry in entries:
                    unfinished.append((entry, _state_of(root, entry)))
    return unfinished, disagreements, anomalies


def _state_of(root, entry):
    target = root / entry["path"]
    if not _resolves_below(root, target):
        # A path that resolves out of the root -- through a symlink, or by
        # being a vault record's absolute path, which design §7 allows --
        # may not be read: acting on such a path needs a fresh CLI argument
        # naming it, and reading its bytes is acting on it. Not reading them
        # is exactly what `unknown` says, so this is an answer and not an
        # error: one such record must not end a pass that has every other
        # unfinished transaction in the project still to report.
        return UNKNOWN
    if "postimage" not in entry:
        # A mutation with no bytes to digest -- a directory, a symlink.
        # `create` (a `mkdir`) is checked against `directory`, not mere
        # existence: `is_symlink()` is true whether or not the link
        # resolves, so a broken symlink would read as `applied` -- the false
        # `applied` design §6 names. `link` has no state word richer than
        # existence: the record's own subject IS the symlink, so a symlink
        # being there, resolvable or not, is what it describes as applied.
        if entry["op"] == CREATE:
            return (
                APPLIED
                if current_state(root, entry["path"])["kind"] == DIRECTORY
                else UNAPPLIED
            )
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
