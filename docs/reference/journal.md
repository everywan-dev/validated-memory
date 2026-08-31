# Journal

The append-only record of what adoption did to this project, and the
`journal` subcommand that reports and reconciles it: [The two
artifacts](#the-two-artifacts) · [What is recorded, and what is not
yet](#what-is-recorded-and-what-is-not-yet) · [Common
fields](#common-fields) ·
[Operations and their inverses](#operations-and-their-inverses) ·
[Stages and unfinished transactions](#stages-and-unfinished-transactions) ·
[The `journal` subcommand](#the-journal-subcommand). Why the journal is split
this way is [ADR
0008](../adr/0008-the-journal-is-versioned-and-the-vault-is-local.md).

## The two artifacts

Durability is not one question, so the journal is two files, each append-only
-- one JSON object per line, never rewritten, never compacted, never sorted.

- **`journal.jsonl`**, at the adopter root, **always versioned**. Carries
  the repository-visible mutations that are recorded (see [the next
  section](#what-is-recorded-and-what-is-not-yet)): what `init` created,
  what it found already there, the line it added to the ignore file, the
  harness-symlink's own record when the target is inside the repository. It is not subject to the versioning question the adoption
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

## What is recorded, and what is not yet

**Recorded today: every mutation `init` performs.** The scaffold it creates
and the paths it finds already there (`create`, `observe`), the vault's
entry in the ignore file (`append`), and the harness symlink (`link`, in the
vault, since its path leaves the repository).

**Not recorded yet**, each because its artifact is derived -- the command
that wrote it rewrites it, so nothing is lost that cannot be recomputed:

| Write | By | Artifact |
|---|---|---|
| the knowledge index | `derive` | `knowledge-index.md` |
| a verdict | `probe` | `verdicts.jsonl` |
| the HTML views | `render`, `init --view` | `knowledge.html`, `memory.html` |

That reasoning is sound for reversal and thin for coverage: a derived file
is re-derivable, but "the plugin wrote here" is still a fact the record does
not carry. The same gap covers the writes the CLI does not perform at all
today -- curated-knowledge units, agent-memory entries, index lines and
supersessions, all written by skills in prose. Both close together, in the
transaction interface of [design
§3](../design/2026-08-30-the-journal-coverage-and-reversal-design.md):
every confirmed write goes through one CLI command that journals
inseparably from doing, because a journal a prose skill has to remember to
write is a journal that will be incomplete.

The completeness pin (`tests/test_journal.py`) holds the line in the
meantime: a write path in the package that does not reach the journal fails
it unless it is named exempt, with its reason, in the same file.

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
| `purpose` | Which part of the method performed the mutation. Two are emitted today: `init` for the scaffold, `ignore-rule` for the vault's entry in `.gitignore`. A future writer (`bootstrap-from-repo`, `render`, ...) names its own. |
| `path` | The path the record describes, relative to the adopter root. `Run.write` and `Run.append_text` refuse an absolute path or one containing `..`, whatever `durability`; `append_op` carries no such check, which is how today's harness-symlink record (outside the root by design) reaches the local journal. On the read side the rule is the file, not the method: a record in `journal.jsonl` whose path is absolute or climbs out with `..` is refused outright, and any record whose path *resolves* outside the root -- through a symlink, or because it is a vault record naming a path outside by design -- is refused before its bytes are read. Design §7: a path outside the root can never be authorised by the file itself. |
| `stage` | `prepared` or `committed`; see [below](#stages-and-unfinished-transactions). |

Two more fields appear only on a record whose `op` touches file bytes
(`create` or `replace`, written by `Run.write`; `append`, written by
`Run.append_text`): **`preimage`** -- the content digest before the write,
or `null` when the target did not exist -- and **`postimage`** -- the
content digest after. Both are `sha256:<hex>`. An `append` carries a third,
**`prior_bytes`**: the length the file had before, which is what its inverse
truncates back to. A `null` preimage on an `append` says the file did not
exist at all, so the inverse is removing it rather than truncating it to
nothing. A record with no file content to digest (`observe`, and today's
`append_op` uses such as a directory's creation or a symlink re-point)
instead carries a free-text **`note`**.

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

`observe` is the odd one out: it is written once, on first sight, and
records a fact about the state adoption found -- that a directory was
already there, that a file already existed, that a symlink already pointed
somewhere -- which nothing can re-derive after the fact. First sight is
keyed on the record itself: a path either journal already carries any
record for has been seen, so it is never observed again. That covers the
re-run (`init` is re-runnable at every session start, and a second
"already present" would say nothing the first did not) and, more
importantly, a path the plugin itself created or was interrupted while
creating -- observing that one would be a claim about the state before
adoption, written after the plugin had changed it, in an op that has no
inverse. `patch`,
`rename`, `remove` and `move` are declared here as part of the domain
(`journal.OPS`) but have no caller yet in this plugin; `create`, `replace`,
`observe`, `link` and `append` are the five `init` writes today -- `append`
for the one line it adds to the repository's ignore file.

## Stages and unfinished transactions

Every mutation is journalled in two steps, both flushed and fsynced before
the next one starts: a **`prepared`** record, the mutation itself, then a
**`committed`** record repeating the same fields. For a write of file bytes
(`Run.write`, `Run.append_text`) the records carry the preimage and the
expected postimage, and the mutation is atomic: a temporary file, `fsync`,
`os.replace`, then a fsync of the directory that now carries the name. For a
mutation with no bytes to digest -- a directory created by `_ensure_dir`, a
symlink re-pointed by `_sync_symlink` -- the two records carry the `note`
instead, and the `prepared` one is still written first: a `mkdir` recorded
only afterwards leaves, for the width of that window, a directory the
journal never mentions, and a re-pointed symlink destroys the previous
target its own record exists to carry.

An `observe` is the exception, and the only one: it is a fact about a path
rather than a change to one, so it is recorded at `committed` alone, having
nothing to prepare.

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
| `unknown` | The bytes could not be read at all (a permission denial, an I/O error) -- nothing can be said about the path. |

A record with no `postimage` -- a directory, a symlink: nothing to digest --
is reconciled on existence instead: the path is there (`applied`) or it is
not (`unapplied`). Comparing digests for those would read a missing path as
`applied`, since both sides would be absent.

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
