# Journal

The append-only record of every mutation the plugin performs, and the
`journal` subcommand that reports and reconciles it: [The two
artifacts](#the-two-artifacts) · [Common fields](#common-fields) ·
[Operations and their inverses](#operations-and-their-inverses) ·
[Stages and unfinished transactions](#stages-and-unfinished-transactions) ·
[The `journal` subcommand](#the-journal-subcommand). Why the journal is split
this way is [ADR
0008](../adr/0008-the-journal-is-versioned-and-the-vault-is-local.md).

## The two artifacts

Durability is not one question, so the journal is two files, each append-only
-- one JSON object per line, never rewritten, never compacted, never sorted.

- **`journal.jsonl`**, at the adopter root, **always versioned**. Carries
  every repository-visible mutation: what `init` created, what it found
  already there, the harness-symlink's own record when the target is inside
  the repository. It is not subject to the versioning question the adoption
  questionnaire asks about the derived files (ADR 0002, ADR 0003) --
  unlike `knowledge-index.md` or the HTML views, nothing regenerates it, so
  the questionnaire never offers to leave it unversioned.
- **`.validated-memory/local.jsonl`**, under `.validated-memory/` at the
  adopter root, **always local to the clone**. Carries preimages
  (`.validated-memory/preimages/<digest>`) and the record of any mutation
  whose path leaves the repository root -- today, the `--harness-memory`
  symlink. `init` writes the ignore entry for the whole `.validated-memory/`
  directory itself; it is never a question the adopter answers.

Every record names which of the two it belongs to in its own `durability`
field (`repo` or `local`), so a reader knows which artifact holds a given
record's preimage and can say what it cannot do when that artifact is
absent. A journal that is present but cannot be parsed -- truncated, not
JSON, a schema newer than this plugin understands -- is refused outright
(`JournalError`, surfaced as an ERROR): nothing regenerates a journal, so a
reader that skipped a line it did not understand would silently narrow the
record, which is the exact failure mode this component exists to remove. A
missing journal reads as no records, not as an error -- a brand-new project
has not adopted yet.

## Common fields

Every record, whichever file it lands in, carries:

| Field | Meaning |
|---|---|
| `schema` | The record format version (currently `1`). A reader that meets a higher number refuses rather than guessing at fields it does not know. |
| `at` | UTC timestamp, ISO-8601 with a trailing `Z` -- the same shape `verdicts.jsonl` already uses. |
| `version` | The plugin version (`validated_memory.__version__`) that wrote the record. |
| `adoption` | This project's adoption id, minted once when the journal is first bootstrapped and stable across every later run -- so records from different sessions still belong together. |
| `run` | This invocation's id, so every record one command wrote groups under it. |
| `durability` | `repo` or `local` -- which of the two artifacts holds this record. |
| `op` | What happened to `path`; see [the table below](#operations-and-their-inverses). |
| `purpose` | Which part of the method performed the mutation. Only `init` is emitted today; a future writer (`bootstrap-from-repo`, `render`, ...) names its own. |
| `path` | The path the record describes, relative to the adopter root. `Run.write` refuses an absolute path or one containing `..`, whatever `durability` -- it may only carry a path that stays below the root; `append_op` carries no such check, which is how today's harness-symlink record (outside the root by design) reaches the local journal. |
| `stage` | `prepared` or `committed`; see [below](#stages-and-unfinished-transactions). |

Two more fields appear only on a record whose `op` touches file bytes
(`create` or `replace`, written by `Run.write`): **`preimage`** -- the
content digest before the write, or `null` for a `create`, whose target did
not exist -- and **`postimage`** -- the content digest after. Both are
`sha256:<hex>`. A record with no file content to digest (`observe`, and
today's `append_op` uses such as a directory's creation or a symlink
re-point) instead carries a free-text **`note`**.

## Operations and their inverses

`op` says what happened to `path`, independently of *why* (`purpose`). Every
op names its own inverse, which is what a later reversal would apply:

| `op` | Inverse |
|---|---|
| `observe` | none; it is a fact about the pre-state, not a mutation |
| `create` | remove |
| `replace` | restore the preimage |
| `patch` | restore the preimage of the region |
| `append` | truncate to the recorded prior length |
| `link` | restore the previous target, or the previous absence |
| `rename` | rename back |
| `remove` | restore the preimage |
| `move` | move back |

`observe` is the odd one out: it is written once, the first time a path is
seen, and records a fact about the state adoption found -- that a directory
was already there, that a file already existed, that a symlink already
pointed somewhere -- which nothing can re-derive after the fact. `patch`,
`append`, `rename`, `remove` and `move` are declared here as part of the
domain (`journal.OPS`) but have no caller yet in this plugin; `create`,
`replace`, `observe` and `link` are the four `init` writes today.

## Stages and unfinished transactions

A file write (`Run.write`) is journalled in two steps, both flushed and
fsynced before the next one starts: a **`prepared`** record carrying the
preimage and the expected postimage, then the atomic mutation itself (a
temporary file, then `os.replace`), then a **`committed`** record repeating
the same preimage and postimage. A mutation with no file bytes to prepare
for -- a directory made by `_ensure_dir`, a symlink written by
`_sync_symlink`, an `observe` -- is recorded directly at `committed`: there
is no intermediate state to crash inside.

A `prepared` record with no matching `committed` is an **unfinished
transaction**: the process died between the two appends, or between the
first append and the mutation itself. `journal.reconcile()` finds these by
pairing records **in file order**, not by set membership -- one run can
write the same path more than once, and a `committed` record closes only the
one `prepared` record it immediately follows for that `(run, path)`, never
every `prepared` record that happens to share the pair.

For each unfinished transaction, `reconcile()` reads the current bytes at
`path` and reports one of four states -- it never guesses between them, and
it never repairs:

| State | What it means |
|---|---|
| `unapplied` | The bytes still match the preimage (or the path is genuinely absent and the preimage was `null`, i.e. a `create` that never happened) -- the mutation never happened. |
| `applied` | The bytes match the postimage -- the mutation happened; only the closing `committed` record was lost. |
| `diverged` | The bytes match neither -- something else wrote the path afterwards. |
| `unknown` | The bytes could not be read at all (a directory, a permission denial, an I/O error) -- nothing can be said about the path. |

Reporting these four states, and stopping there, is deliberate: choosing for
the user between states the record cannot distinguish is exactly the
guessing this component exists to remove.

## The `journal` subcommand

```
python3 -P -m validated_memory journal [--check]
```

Read-only in both modes -- it never runs `probe`, never writes to either
journal file, and its own record-reading failures are the only thing it can
report on itself.

**Without `--check`**, it reads both artifacts (`journal.jsonl` and
`.validated-memory/local.jsonl`) and reports the combined count. It never
gates on what it finds:

```
$ python3 -P -m validated_memory journal
journal: 7 record(s)
```

Exit `0`, whatever the count -- a reader can look at a project's history
without gating a session on it.

**With `--check`**, it additionally runs `reconcile()` and reports every
unfinished transaction it finds, each as an ERROR naming the path and the
state its bytes are in:

```
$ python3 -P -m validated_memory journal --check
ERROR: validated-memory.md: journal: unfinished transaction from run a1b2c3d4e5f6a7b8: the path is diverged
journal: 8 record(s), 1 error(s)
```

Exit `1` if `reconcile()` found any unfinished transaction, `0` otherwise --
`--check` is the one mode where an unfinished transaction is not just
reported but gates, because a caller that explicitly asked to be told cannot
be told by an exit code of `0`.

**Either mode**, a journal that is present but cannot be parsed is reported
the same way -- one ERROR naming the file (and the line, when the fault is a
single line's rather than the file's) and the count folded into the
summary -- and always exits `1`, `--check` or not:

```
$ python3 -P -m validated_memory journal
ERROR: journal.jsonl:5: journal: line is not valid JSON: Expecting value
journal: 0 record(s), 1 error(s)
```
