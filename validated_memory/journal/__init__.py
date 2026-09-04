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
- `records` -- the record format, the digest, the two journals' paths, and
  the reader that refuses a journal it cannot account for.
- `paths` -- what is at one path, whether that is what a caller expected,
  whether a record may name it, and whether this user may write over it.
- `operations` -- the `Intention` a caller states and the `Outcome` it gets.
- `fault` -- the four crash seams, and the one reader of the variable that
  names them.
- `lock` -- the per-adopter exclusive lock, and where it lives.
- `transactions` -- the local write-ahead log: its four stages, its reader,
  and the classification a recovery acts on.
- `executor` -- the preimage store, `bootstrap`, and `Run`.
- `reconcile` -- the two histories read against each other and the tree.
- `command` -- the `journal` subcommand.

This file is the facade, and it is deliberately narrow: exactly the names
`init.py` and `cli.py` reach through `journal.`, and nothing kept in case
somebody wants it. Everything else -- the raw line-writer, the atomic
install, the record builder, the bootstrap, the transaction file's own
stages -- is the journal's own, and a caller that reaches one is
reimplementing the protocol `Run.execute` exists to own. A module of this
package is not a door either: it is imported whole, and reached by
attribute.
"""

from .executor import Run
from .lock import Lock
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
from .command import run

__all__ = [
    "ABSENT",
    "FILE",
    "JOURNAL_FILENAME",
    "JournalError",
    "LOCAL",
    "Lock",
    "OUTCOME_APPLIED",
    "OUTCOME_NOOP",
    "OUTCOME_REFUSED",
    "RECOVERED",
    "REPO",
    "RESOLUTIONS",
    "Run",
    "SYMLINK",
    "VAULT_DIRNAME",
    "append_to_file",
    "create_directory",
    "create_file",
    "digest",
    "link_to",
    "run",
]
