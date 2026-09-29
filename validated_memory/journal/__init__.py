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

This package is the write path, one module per seam, each importing only
from the ones before it:

- `durable` -- the atomic publication of a file, and the barrier that makes
  the directory entry carrying its name survive a power cut.
- `fault` -- the four crash seams and private bounded test rendezvous.
- `records` -- the record format, the digest, the two journals' paths, and
  the reader that refuses a journal it cannot account for.
- `topology` -- pure semantic inspection of a coherent pair, including
  schema dispatch, exact legacy anchors, frontiers, heads, and lineage.
- `paths` -- what is at one path, whether that is what a caller expected,
  whether a record may name it, and whether this user may write over it.
- `operations` -- the five functions a caller states a mutation with, and
  the `Outcome` it gets back.
- `lock` -- the per-adopter exclusive lock, and where it lives.
- `transactions` -- the local write-ahead log: its four stages, its reader,
  and the classification a recovery acts on.
- `executor` -- target, WAL and record-construction mechanics selected by the
  protocol; it owns no history or topology policy.
- `protocol` -- the sole workflow-policy owner for adoption, inspection,
  recovery, resolution and repair.
- `reconcile` -- the two histories read against each other and the tree.
- `command` -- the `journal` subcommand.

This file is the facade, and it is deliberately narrow: exactly the names
`init.py` and `cli.py` reach through `journal.`, and nothing kept in case
somebody wants it. Everything else -- the raw line-writer, the atomic
install, the record builder, the bootstrap, the transaction file's own
stages -- is the journal's own, and a caller that reaches one is
reimplementing the workflow the protocol module exists to own. A module of this
package is not a door either: it is imported whole, and reached by
attribute. The one non-session write is `repair_harness_link`: a high-level
fail-open operation used only when the journal cannot serve the startup hook.
It exposes neither persistence primitives nor their exception types and turns
post-visibility uncertainty into a gating `JournalError`. A run the journal
refused restores the link through `guarded_harness_repair`, which takes the
run-wide lock, decides, and calls the caller's relink inside it (ADR 0029);
`harness_repair_regime` names the regime of the failure it is given.
`history_condition_count` is the read-only count of the history conditions that
stop an adopting run.
"""

from .executor import repair_harness_link
from .operations import (
    OUTCOME_APPLIED,
    OUTCOME_NOOP,
    OUTCOME_REFUSED,
    append_to_file,
    create_directory,
    create_file,
    link_to,
)
from .paths import ABSENT, FILE, SYMLINK
from .records import (
    JOURNAL_FILENAME,
    LOCAL,
    REPO,
    VAULT_DIRNAME,
    JournalError,
    digest,
)
from .transactions import RECOVERED, RESOLUTIONS
from .protocol import (
    LOCK_BUSY,
    PRE_EFFECT_GATE,
    REPAIR_RELINKED,
    REPAIR_RELINKED_UNGUARDED,
    REPAIR_WITHHELD,
    UNAVAILABLE,
    adopting_run,
    guarded_harness_repair,
    harness_repair_regime,
    history_condition_count,
    resolve_transaction,
)
from .command import run

__all__ = [
    "ABSENT",
    "FILE",
    "JOURNAL_FILENAME",
    "JournalError",
    "LOCAL",
    "LOCK_BUSY",
    "OUTCOME_APPLIED",
    "OUTCOME_NOOP",
    "OUTCOME_REFUSED",
    "PRE_EFFECT_GATE",
    "RECOVERED",
    "REPAIR_RELINKED",
    "REPAIR_RELINKED_UNGUARDED",
    "REPAIR_WITHHELD",
    "REPO",
    "RESOLUTIONS",
    "SYMLINK",
    "UNAVAILABLE",
    "VAULT_DIRNAME",
    "adopting_run",
    "append_to_file",
    "create_directory",
    "create_file",
    "digest",
    "guarded_harness_repair",
    "harness_repair_regime",
    "history_condition_count",
    "link_to",
    "repair_harness_link",
    "resolve_transaction",
    "run",
]
