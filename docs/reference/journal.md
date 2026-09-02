# Journal

The append-only record of what adoption did to this project, and the
`journal` subcommand that reports, reconciles and resolves it: [The three
records](#the-three-records) · [The two artifacts](#the-two-artifacts) ·
[The write-ahead log](#the-write-ahead-log) · [Expected states, and what a
precondition promises](#expected-states-and-what-a-precondition-promises) ·
[What is recorded, and what is not
yet](#what-is-recorded-and-what-is-not-yet) · [Common
fields](#common-fields) ·
[Operations and their inverses](#operations-and-their-inverses) ·
[Stages and unfinished transactions](#stages-and-unfinished-transactions) ·
[Recovery](#recovery) · [Resolving a
transaction](#resolving-a-transaction) ·
[The `journal` subcommand](#the-journal-subcommand) · [The fault-injection
seam](#the-fault-injection-seam). Why the journal is split by durability is
[ADR 0008](../adr/0008-the-journal-is-versioned-and-the-vault-is-local.md);
why a refusal never reaches it is [ADR
0009](../adr/0009-a-refusal-is-never-permanent-history.md).

## The three records

One mutation touches three records with three different lifetimes, and a
reader has to know which one they are looking at.

- **The permanent history** -- `journal.jsonl` at the adopter root and
  `.validated-memory/local.jsonl` in the vault. Consummated facts only: an
  observation of a state adoption found, and a mutation that happened. It is
  append-only, never compacted, and the repository half is always versioned,
  so anything written into it is written for the life of the project.
- **The write-ahead log** -- one file per unresolved transaction under
  `.validated-memory/transactions/`. It says what a mutation intended and
  how far it got; it is read by the executor, by the recovery that runs
  ahead of it, by `journal --check` and by `journal --resolve`, and by
  nothing else. A resolved transaction leaves it. It is not history.
- **The derived publisher** -- no record at all. `derive`, `render` and
  `init --view` write artifacts their own command rebuilds, and `probe`
  appends to `verdicts.jsonl`, the other append-only log. All of them write
  through their own writers rather than through the executor -- only
  `render` and `init --view` publish by atomic rename; `derive` writes the
  index in place and `probe` appends -- and none of them puts a line in
  either journal. They are named in [what is not
  recorded](#what-is-recorded-and-what-is-not-yet) rather than recorded.

So: a line in either `.jsonl` file is history; a file under
`.validated-memory/transactions/` is a transaction nothing has closed yet; a
derived artifact is described nowhere.

**A refusal never enters the permanent history.** A precondition that fails
before anything is prepared writes nothing anywhere -- no transaction file,
no preimage, no record: it is a result the caller renders, and `init`
renders one ERROR for that item and carries on. A failure after the
transaction file exists closes that file `aborted`, with the reason, and
then removes it; if the process dies between the two, the next run's
recovery removes it. Either way the history gains nothing. The reason is
arithmetic rather than taste: `init` runs at every session start and the
history is never compacted, so a project that is broken in some way `init`
refuses would otherwise append one refusal per session, for ever, to a
versioned file ([ADR
0009](../adr/0009-a-refusal-is-never-permanent-history.md)).

## The two artifacts

Durability is not one question, so the permanent history is two files, each
append-only -- one JSON object per line, never rewritten, never compacted,
never sorted.

- **`journal.jsonl`**, at the adopter root, **always versioned**. Carries
  the repository-visible mutations that are recorded (see [the section
  below](#what-is-recorded-and-what-is-not-yet)): what `init` created,
  what it found already there, and the line it added to the ignore file.
  Not the harness symlink: `init` writes that record to the vault whatever
  path `--harness-memory` names, including one inside the repository. It is
  not subject to the versioning question the adoption questionnaire asks
  about the derived files (ADR 0002, ADR 0003) -- unlike
  `knowledge-index.md` or the HTML views, nothing regenerates it, so the
  questionnaire never offers to leave it unversioned.
- **`.validated-memory/local.jsonl`**, under `.validated-memory/` at the
  adopter root, **always local to the clone**. Carries the record of any
  mutation whose path leaves the repository root -- today, the
  `--harness-memory` symlink. The rest of the vault is local for the same
  reason: preimages (`.validated-memory/preimages/<digest>`) and the
  write-ahead log live there too. `init` writes the ignore entry for the
  whole `.validated-memory/` directory itself; it is never a question the
  adopter answers.

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

Each journal is opened once and every question is asked of that descriptor,
never of the name again, so nothing can be swapped underneath between the
check and the read. What is refused there is what cannot hold records at
all: a directory, a device, a pipe. A symlink is not on that list -- an
adopter who keeps `journal.jsonl` in a shared store and links it back is
read through and appended through, which works because the journal is the
one file this plugin appends to in place rather than replacing. What is
refused instead, and at the point it matters, is bootstrapping a journal
over a symlink that holds no records: `os.replace` would put a regular file
where the adopter's link was, and nothing can put that link back.

**The lock is taken beside the journal that is really there.** A mutating
run holds `.validated-memory/lock` under the directory `journal.jsonl`
resolves into, not under the tree the command was run in, so two adopters
linked at one shared store exclude each other instead of taking two local
locks and serialising nothing. For an adopter whose journal is a plain file
the two are the same directory. A `journal.jsonl` symlink that resolves to
anything but a regular file -- a broken link, a directory -- keeps the local
lock: there is no store to share, and nothing is created outside the adopter
root. Locking beside a store creates a `.validated-memory/` directory next
to it on first use; the lock file inside it is removed when the run ends,
the directory is not, and nothing removes it later.

**A lock is broken only when its owner is provably gone.** A lock whose
owning pid is still running is never broken, whatever its age; one whose pid
names no process is broken at once, so a run that was killed does not wedge
the next session; and the five-minute age horizon is what is left for a lock
file whose pid cannot be read at all. The case that needs a person is pid
reuse: if the operating system has given the dead run's pid to an unrelated
process, the lock reads as held forever and every `init` refuses. That is
what the message says to do -- when no validated-memory process is running,
delete the lock file it names. Two runs breaking one dead lock at the same
instant is a narrow race that neither the pid nor the inode check closes;
what they do guarantee is that releasing a lock never deletes a file this
run did not create.

**The owner check is a single-host promise.** The pid in the file is a fact
about the machine and pid namespace that wrote it. A store shared over a
network filesystem is shared between hosts, where that pid may name an
unrelated local process or none at all: mutual exclusion still holds, since
it rests on `O_CREAT | O_EXCL`, but breaking a lock left behind by a dead
run does not travel between hosts, and neither does the age horizon's
assumption about who is slow.

Versioned and strictly append-only is also a standing merge conflict: two
clones append at the same end of the same file, and an ordinary merge leaves
conflict markers there, which is exactly the unparseable case above -- every
later `init` gates until someone repairs the file by hand. Git's built-in
union merge keeps both sides' lines instead, and a repository that shares a
journal wants `journal.jsonl merge=union` in its `.gitattributes`; `init`
does not write that entry into an adopter's repository today. Union
concatenates: it neither orders nor de-duplicates, so a merged journal can
carry records whose `at` runs backwards at the seam, and the same line twice
when it reached the two branches by routes the merge cannot align. Nothing
reads the file in timestamp order -- `reconcile()` pairs a mutation's two
records by their `transaction` id, and falls back to file order only for
records written before the executor existed -- but a reader of a merged
journal should not assume the file is one clone's history.

## The write-ahead log

The log lives at `.validated-memory/transactions/<transaction-id>.json`, one
file per transaction, written and fsynced **before** the mutation and
unlinked when the transaction is resolved. Its presence *is* the definition
of an open transaction: "what was left half done" is a directory listing
rather than a scan of a permanent file.

**It never holds payload bytes.** The file records the state either side of
the mutation, never the content the mutation would write, so a torn or
truncated transaction file can never rewrite data on recovery -- it can only
say what the mutation intended and what it should have changed. The bytes
that *are* kept are the preimage's, in the content-addressed store
(`.validated-memory/preimages/<digest>`), verified against the digest they
are filed under before they are installed.

**It never grows without bound.** One file exists per unresolved
transaction, and a run that completes leaves none: the executor removes its
own on success, and recovery removes every one it can account for. The
`transactions/` directory itself stays behind, empty.

**Both directories the plugin owns under the vault must be real
directories.** `.validated-memory/transactions` and
`.validated-memory/preimages` are created, written and unlinked *by name*,
and `mkdir`, `open` and `os.replace` follow a symlink standing there
without a word: a link pointing out of the adopter root would put the
write-ahead entry, or the only copy of the bytes a mutation is about to
overwrite, somewhere this project promises nothing about. So each is
`lstat`ed where it is resolved, before anything is created, written,
replaced or unlinked through it, and a symlink or a non-directory is an
ERROR naming the artifact -- exit `1` from `init`, from `journal --check`
and from `journal --resolve`, never a traceback:

```
ERROR: .validated-memory/transactions: journal: .validated-memory/transactions is not a directory, and this plugin writes what it owns only into a real directory of its own: everything under that name is created, written and removed by name, and a name that is somebody else's carries all of it somewhere this project promises nothing about. Move it aside.
```

The check is where the name is *used*, so the preimage store is checked by
a run that parks or reads back a preimage rather than by every command.
`.validated-memory/` itself is not checked: the vault's own name may be a
link into a shared store, exactly as `journal.jsonl` may, and what this
refuses is a name inside it that the plugin alone writes.

Each file holds:

| Field | Holds |
|---|---|
| `schema` | The same `schema` a journal record uses (currently `1`). |
| `at` | When the transaction was opened, ISO-8601 with a trailing `Z`. |
| `version` | The plugin version that opened it. |
| `adoption` | This project's adoption id. |
| `run` | The invocation's run id. |
| `transaction` | This transaction's own id, which is also the filename stem. |
| `intention` | `{op, purpose, path, durability}`, plus `note`, `directory` and `target` where the op has them. |
| `preimage` | The preimage **state**, in the [expected-state vocabulary](#expected-states-and-what-a-precondition-promises) -- what was actually at the path, not what the caller hoped. |
| `postimage` | The postimage **state**: what the mutation will leave there. |
| `preimage_blob` | The parked preimage's `sha256:...` reference, or `null` when there was nothing to park. |
| `mode` | The mode the target had before the mutation, when it was a regular file; `null` for every other kind. |
| `prior_bytes` | An `append`'s prior length, or `null` for every other op. |
| `stage` | `prepared`, `published` or `aborted`. |
| `reason` | Present only once `stage` is `aborted`: why it will never publish. |

`prepared` means the entry was fsynced and nothing has been published.
`published` means publication completed and the history had not been
appended yet -- it is not decoration: a `replace` whose new bytes equal the
old, an `append` of nothing, and every mutation with no bytes at all satisfy
both states at once, so without that marker the filesystem alone cannot say
whether the mutation ran. `aborted` is closed: it published nothing, it is
never inverted by a reversal, and it disappears with its file.

`prior_bytes` is in the file for recovery alone. The inverse of an `append`
is "truncate to the recorded prior length", and recovery, rebuilding that
record from this file and the current state, has nowhere else to read it
from: the bytes it describes have already been appended to.

**A missing preimage blob is two different things.** For a *closed* history
record it is normal, not corruption: the journal travels with the repository
and the vault does not, so a fresh clone has records whose preimages stayed
behind, and what that means is only that *this clone cannot reverse that
mutation*. For an *open* transaction it is a damaged log: that blob is the
sole copy of the bytes the plugin was about to overwrite, parked and
verified moments before, and `journal --resolve --restore` refuses rather
than writing something else over the path.

## Expected states, and what a precondition promises

Every intention carries the state it expects to find, and the executor
refuses if what is there is anything else. The vocabulary is `lstat`'s
throughout, so a symlink is a fact about itself and never about what it
points at:

| State | What it names |
|---|---|
| `absent` | Nothing at that exact name -- including a name whose parent is missing or denies traversal. |
| `directory` | A directory. Not "the name resolves": a broken symlink is `symlink`, never `directory` or `absent`. |
| `file(digest)` | A regular file with those exact bytes. A node that is neither a directory, a symlink nor a regular file -- a FIFO, a socket, a device -- is reported as a file with no digest and is never read through. |
| `symlink(target)` | A symlink whose own `readlink` is that target, resolvable or not. |
| `mode(bits)` | Combined with any of the three above. An expected state that omits the mode matches whatever mode is there; one that names it must match exactly. |

That `directory` means a directory is the whole point of the word: checking
existence alone is what let a broken symlink stand in for a created
directory and produced the false `applied` this core removed. A `create`
intention may only ever expect `absent` -- a creation over something already
there is a replacement, and it has to say so, because the inverse of a
create is removal.

What the check buys, stated exactly, because overstating it would be the
same class of defect this core exists to remove:

- **Full serialisation against other validated-memory processes.** The
  executor takes the lock and everything below happens inside it, the
  re-read included.
- **A strong no-replace guarantee for a creation.** A creation over an
  absent name is published with `O_CREAT | O_EXCL`, which fails if the name
  is taken; check-then-rename is not that primitive.
- **Optimistic rejection for a replacement.** The state is re-read, under
  the same lock, immediately before `os.replace`. There is no portable POSIX
  compare-and-swap on a pathname, so a third party writing in that window is
  detected only if it also changes the digest before the read. This is a
  narrower window, not an atomic guarantee.
- **Ancestors are *not* stabilised by descriptor.** Both the lexical and the
  resolved authorisation check work on the name, and nothing stops another
  process from swapping an ancestor directory for a symlink between the
  check and the action on that same name. That window is real; closing it
  needs descriptor-relative operations, which belong to step 1b.

**Publication is atomic and durable**, in one of four shapes chosen by what
is expected to be there rather than by the op's name. A directory is
`os.mkdir`, never with `parents=True` -- creating an ancestor nobody asked
for would be a second mutation with no intention and no record, so a missing
parent is a refusal that names it. A creation over an absent name is
`O_CREAT | O_EXCL`, written and fsynced. A replacement is a temporary file,
fsynced and given the target's mode, then `os.replace`. A symlink is built
under a temporary beside the path and renamed over it, so the link is never
absent for an instant. Every shape ends with an `fsync` of the directory
that now carries the name, and a failure anywhere leaves the target as it
was: the temporary is removed, a partial creation is unlinked, and the
aborted transaction is the only trace.

**Metadata means the mode, and nothing else.** A replacement copies the
target's mode onto the temporary before the rename, so an adopter's 0640
does not come back 0644, and both history records carry the mode the path
ended up with, so a reversal can put it back. Ownership, timestamps, ACLs,
extended attributes and hardlink identity are **not** preserved -- and
`os.replace` breaks hardlink identity by construction, since the name ends
up pointing at a different inode. A symlink's record carries **no** mode at
all: its `lstat` mode is 0777 on every platform this runs on, nobody chose
it, and recording it would invite a reversal to `chmod` the directory the
link points at.

**A target whose mode denies writing to the current user is refused.** The
question is asked of the file's own mode bits and of the POSIX class this
process falls in -- owner, else group, else other, by effective uid and
group -- and of nothing else. `os.access` is not used: it answers for the
real uid, and its own documentation warns against using it to decide whether
an operation will succeed. There is no exception for root. The read-only bit
is how an adopter says do not write here, and `os.replace` needs write
permission on the *directory*, not on the file, so nothing else in the
install path would ever have stopped.

The refusal names the file and its mode, and this is the one `init`
behaviour that changed with this core:

```
ERROR: .gitignore: ignore-rule: the vault's ignore entry (/.validated-memory/) could not be written: .gitignore is mode 0444, which denies writing to this user. Nothing has been written.
```

An ignore file the adopter made read-only used to be rewritten in silence
and handed back at 0644. It now gates the whole journalled part of the run,
and because `init` is what the session hook runs at every session start, it
gates **every session** until someone `chmod`s the file or puts
`/.validated-memory/` into `.git/info/exclude` by hand. That is the cost of
never writing through a bit an adopter set deliberately.

## What is recorded, and what is not yet

**Recorded today: the scaffold `init` writes.** The directories and files
it creates and the paths it finds already there (`create`, `observe`), the
vault's entry in the ignore file (`create` or `append`), and the harness
symlink (`link`, always in the vault). That is not every mutation `init`
performs.

**Two mutations bypass the executor by decision**, and they are the only
two. Both are declared in the design and pinned by name in
`tests/test_journal.py`, so a third cannot be added quietly:

| Write | By | Why it is an exception |
|---|---|---|
| the fail-open repair of the harness symlink | `init.relink` | The contract requires the link back when the journal cannot be read or written **at all** -- that is the `SessionStart` hook's only job -- and an executor that requires a working journal cannot serve it. The record goes through the executor whenever the journal is healthy; only the repair survives when it is not, with a WARNING naming the previous target. |
| the harness take-over | `adopt.take_over`, and its `_absorb`, `_reconcile_index` and `_park` | It recognises a tree, copies conditionally, reconciles an index and renames the source, and its published contract tolerates a per-file conflict and continues. That needs its own planner before the executor can apply it. |

**Not recorded at all**, because what is written is not adopter data: a
derived artifact its own command regenerates, or another append-only log.

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

The take-over is four mutations, none of them derived:

| Write | By |
|---|---|
| `rmdir` of PATH, when it is empty | `take_over` |
| a copy of each memory file into `memory/` | `_absorb` |
| a rewrite of `memory/MEMORY.md`, giving the copied files their index entries | `_reconcile_index` |
| the rename of PATH aside to `PATH.bak` | `_park` |

These are deferred whole to the reversal plan ([design
§7](../design/2026-08-30-the-journal-coverage-and-reversal-design.md#the-harness-stays-out)),
which records them and deliberately does not invert them. Until it lands,
what a reader must not conclude: **a reversal driven by today's record
cannot restore a harness memory directory.** The copied bytes are in no
preimage and the parked `.bak` is named in no record; the only trace of the
take-over is the `link` record in the vault, whose note ("no previous link")
is true of the symlink and silent about the directory that was moved out of
its way. The index rewrite also outdates a record the same run already
wrote: `memory/MEMORY.md` is journalled early, as a `create` carrying a
`postimage` or as an `observe` saying it was already present, and
`_reconcile_index` then changes the file, so the run ends with a record that
no longer describes it.

The completeness pin (`tests/test_journal.py`) holds the line in the
meantime: a write path in the package that does not reach the journal fails
it unless it is named -- with its reason -- in one of the two sets above,
and a module outside `journal.py` that so much as names the stage-writing
surface fails it too.

## Common fields

Every record, whichever file it lands in, carries:

| Field | Meaning |
|---|---|
| `schema` | The record format version (currently `1`). A reader that meets a higher number refuses rather than guessing at fields it does not know. |
| `at` | UTC timestamp, ISO-8601 with a trailing `Z` -- the same shape `verdicts.jsonl` already uses. |
| `version` | The plugin version (`validated_memory.__version__`) that wrote the record. |
| `adoption` | This project's adoption id, minted once when the journal is first bootstrapped and stable across every later run -- so records from different sessions still belong together. Both artifacts carry it, and either can supply it: `journal.jsonl` is versioned, so an ordinary checkout of a pre-adoption commit takes it away while the ignored vault stays, and the id is then read back from the vault rather than minted afresh. |
| `run` | This invocation's id, so every record one command wrote groups under it. A record recovery appends for an earlier run carries **that** run's id, because it is the run that wrote the bytes. |
| `durability` | `repo` or `local` -- which of the two artifacts holds this record. |
| `op` | What happened to `path`; see [the table below](#operations-and-their-inverses). |
| `purpose` | Which part of the method performed the mutation. Two are emitted today: `init` for the scaffold, `ignore-rule` for the vault's entry in `.gitignore`. A future writer (`bootstrap-from-repo`, `render`, ...) names its own. |
| `path` | The path the record describes, relative to the adopter root. No `repo`-durability record can carry an absolute path or one containing `..`: `authorise` asks that question lexically, and then asks whether the path still resolves below the root once its symlinks are followed, at the start of every `observe` and every `execute` -- before anything is parked, written or appended. A `local` record may name a path outside the root, which is how today's harness-symlink record reaches the vault. On the read side the rule is the file, not the method: a record in `journal.jsonl` whose path is absolute or climbs out with `..` is refused outright. A record whose path *resolves* outside the root -- through a symlink, or because it is a vault record naming a path outside by design -- has its bytes left unread, so its state is [`unknown`](#stages-and-unfinished-transactions) rather than a refusal that would end the whole pass. Design §7: a path outside the root can never be authorised by the file itself. |
| `stage` | `prepared` or `committed`; see [below](#stages-and-unfinished-transactions). |

Two artifacts filed under **different** adoption ids is a state a user can
reach -- a vault copied into another tree, a `journal.jsonl` restored from
a different clone -- and `init` refuses it rather than choosing. Nothing in
either file says which adoption is this project's, and the vault's
preimages belong to exactly one of them, so attaching the run to either
would file it against somebody else's pre-adoption state. The two ways out
are in the message: restore the `journal.jsonl` the vault's id names, or
move `.validated-memory/` aside and adopt afresh -- which costs the
preimages it holds, since they belong to the adoption it names.

Both halves of a mutation carry two more fields:

- **`transaction`** -- the id of the transaction that carried it. The
  transaction *file* is local and is unlinked as soon as the mutation is
  recorded, so this id is the only thing that survives to say the two lines
  are one act, and it is what `reconcile()` pairs on. An `observe` carries
  none: it opens no transaction.
- **`mode`** -- the mode the path ended up with, so a reversal can restore
  it. Omitted where there is none to report: a `link` record never carries
  one, and neither does a record whose published node could not be
  `lstat`ed back (a mode nobody measured is not one to write down).

Three more appear only on a record whose `op` touches file bytes (`create`
or `replace` of a file, and `append`): **`preimage`** -- the content digest
before the write, or `null` when the target did not exist -- and
**`postimage`** -- the content digest after. Both are `sha256:<hex>`. An
`append` carries a third, **`prior_bytes`**: the length the file had before,
which is what its inverse truncates back to. A `null` preimage on an
`append` says the file did not exist at all, so the inverse is removing it
rather than truncating it to nothing. A record with no file content to
digest -- an `observe`, a directory's creation, a symlink re-point --
instead carries a free-text **`note`**.

## Operations and their inverses

`op` says what happened to `path`, independently of *why* (`purpose`). Every
op names its own inverse, which is what a later reversal would apply:

| `op` | Inverse |
|---|---|
| `observe` | none; it is a fact about a state, not a mutation |
| `create` | remove |
| `replace` | restore the preimage |
| `patch` | restore the preimage of the region |
| `append` | truncate to the recorded prior length |
| `link` | restore the previous target, or the previous absence |
| `rename` | rename back |
| `remove` | restore the preimage |
| `move` | move back |

`observe` is the odd one out: it records **a fact about a state the plugin
found and did not produce**, which nothing can re-derive after the fact. It
is written in two places, and in no third.

The first is adoption's own first sight: that a directory was already there,
that a file already existed, that a symlink already pointed somewhere. First
sight is keyed on the record itself -- a path either journal already carries
any record for has been seen, so it is never observed again, and a path an
unresolved transaction names counts as seen too. That covers the re-run
(`init` runs at every session start, and a second "already present" would
say nothing the first did not) and, more importantly, a path the plugin
itself created or was interrupted while creating: observing that one would
be a claim about the state before adoption, written after the plugin had
changed it, in an op that has no inverse.

The second is [`journal --resolve`](#resolving-a-transaction) with
`--accept` or `--abandon`: the operator says the state a diverged path is in
is what they want, or that it is to be left alone. The plugin did not
produce that state either, which is why it is an observation rather than a
`create` or a `replace` -- a mutation record would claim it did, and offer a
reversal that would undo somebody else's work. It is the one `observe`
written about a path the journal already mentions, and its note is what
keeps it honest (`accepted after divergence: transaction <id> found <kind>`,
`abandoned: transaction <id>, path left as found`).

Five ops can be *intended*: `observe`, `create`, `replace`, `append` and
`link`. `patch`, `rename`, `remove` and `move` are declared as part of the
domain (`journal.OPS`) so a reader knows the vocabulary a reversal will
speak, but nothing can ask for them yet. `create`, `observe`, `link` and
`append` are what `init` writes today -- `append` for the line it adds to an
ignore file that already exists, `create` for the whole block when there is
no ignore file at all, the two having different inverses. `replace` has no
writer that records one today: it is the op the public write interface will
reach first, and `journal --resolve --restore` already builds one internally
to put a preimage back, recording nothing.

## Stages and unfinished transactions

Every mutation is journalled as **two** records -- a `prepared` and a
`committed` repeating the same fields -- and **both are appended together,
after the mutation has succeeded**. That is the one thing about the
protocol that changed: the write-ahead guarantee is held by [the transaction
file](#the-write-ahead-log), not by a `prepared` line in a versioned journal
that no later run could ever close. The history holds consummated facts, so
neither record is written until there is one.

The two records are kept, rather than collapsed into one, because they are
what a reader already parses and what `reconcile()` checks against each
other -- and because a history written before this core holds `prepared`
records with no twin, which still have to be reconciled. Nothing in this
package writes another.

An `observe` is the exception, and the only one: it is a fact about a path
rather than a change to one, so it is recorded at `committed` alone, having
nothing to prepare.

A `prepared` record with no matching `committed` is an **unfinished
transaction**. `journal.reconcile()` pairs the two halves **by their
`transaction` id** wherever both carry one -- the id is minted per mutation,
so it says which `committed` closes which `prepared` with no inference at
all. Records without the field, which is everything a history written before
the executor holds, keep the older rule: file order within a `(run, path)`,
so a `committed` record closes the one `prepared` record it immediately
follows for that pair, never every `prepared` record that happens to share
it.

A pair that agrees on nothing but its id is not a pair. These fields must
say the same thing in both halves, and a disagreement is reported as its own
ERROR, never resolved by preferring one half:

`op`, `purpose`, `path`, `durability`, `preimage`, `postimage`, `note`,
`prior_bytes`, `mode`.

`at` is excluded because the two records are written in one append but
stamped separately; `stage` because it is what tells them apart; and `run`,
`adoption`, `schema` and `version` because both halves are filled in from
one source.

**An id is a half of exactly one act**, and two shapes say otherwise.
Nothing in this package writes a `committed` record without the `prepared`
one before it -- the executor appends both in one call, and recovery
rebuilds both -- so a lone one is a hand edit or a torn merge, and it is
its own ERROR:

```
ERROR: .gitignore: journal: records of transaction 1ed016d9e88b5435: committed without a prepared half
```

And the id is minted per mutation, so a third line carrying it counts one
mutation twice in a file nothing takes back -- the exact residue recovery's
idempotency rule exists to avoid appending:

```
ERROR: .gitignore: journal: transaction e85966eeb6de80ef is recorded 4 times
```

Both are reported and neither is repaired, like every other finding here.

For each unfinished transaction, `reconcile()` reads the current bytes at
`path` and reports one of four states -- it never guesses between them, and
it never repairs:

| State | What it means |
|---|---|
| `unapplied` | The bytes still match the preimage (or the path is genuinely absent and the preimage was `null`, i.e. a `create` that never happened) -- the mutation never happened. |
| `applied` | The bytes match the postimage -- the mutation happened; only the closing `committed` record was lost. |
| `diverged` | The bytes match neither -- something else wrote the path afterwards. |
| `unknown` | The bytes could not be read at all (a permission denial, an I/O error), or must not be read because the path resolves outside the adopter root -- nothing can be said about the path. |

A record with no `postimage` -- a directory, a symlink: nothing to digest --
is reconciled on existence instead, and a `create` is checked for a
**directory** rather than for a name that resolves, because a broken symlink
resolves to nothing and would otherwise read as `applied`.

Reporting these four states, and stopping there, is deliberate: choosing for
the user between states the record cannot distinguish is exactly the
guessing this component exists to remove. Repair is [recovery](#recovery),
which reads the write-ahead log rather than these two journals, and only
ever closes what that log accounts for.

## Recovery

Recovery is what a run does with what an earlier run left open. It runs at
the start of `init` -- the command the session hook re-runs at every session
start -- under the run-wide lock, before the ignore gate and before
`init`'s own first intention. It only ever completes or closes what an
earlier run began, and unlinks the files that said so, so it reduces what is
on disk rather than adding to it, which is why it may run ahead of the gate
that keeps the vault out of the repository. `journal --resolve` is the only
other command that writes through the executor, and it deliberately does
**not** recover: an operator answering for one transaction must not have the
others closed underneath them.

It reads the write-ahead log, not the two journals. Each unresolved
transaction is classified by the file's own record of how far it got, not
inferred from a filesystem some later process may have changed:

| Verdict | Reached when | What recovery does |
|---|---|---|
| `recoverable` -- *complete* | The file says `published` and the path is in the postimage state; or it says `prepared` and the path is in the postimage state and not in the preimage state | Appends whichever of the two history records is missing, removes the file, and `init` prints `init: recovered <path> from transaction <id>` |
| `recoverable` -- *discard* | The file says `prepared` and the path is in the preimage state and not in the postimage state | Removes the file. Nothing happened and nothing is recorded, so nothing is printed |
| `recoverable` -- *remove* | The file says `aborted` | Removes the file. It published nothing |
| `diverged` | The file says `published` and the path is not in the postimage state -- something wrote it afterwards | Nothing. The file stays, and an ERROR names the path and the way out |
| `unknown` | The file says `prepared` and the path matches neither state -- or matches both, which only a hand-written file can do; or the path's bytes cannot be read at all, whatever the stage, in which case the message carries the reason instead of the state | Nothing. The file stays, and an ERROR names the path and the way out |
| `damaged` | The file is not a well-formed transaction **of this project**: it could not be read, is not valid UTF-8, is not JSON, is not an object, names a `schema` this reader does not know, calls itself an id that is not its own file's name, is filed under another `adoption`, names an operation no intention carries (which includes `observe`, since an observation opens no transaction), or holds a preimage or postimage that is not a state | Nothing. The file stays for inspection, and the ERROR names the file rather than a path, because it names none |

`journal --check` reports exactly these verdicts, from the same
classification, so what `--check` promises and what the next `init` does
cannot drift apart. The `damaged` reasons read as sentences about the file:

```
ERROR: .validated-memory/transactions/1111111111111111.json: journal: damaged transaction 1111111111111111: its schema is 999 and this plugin reads up to 1; a reader that meets a higher number refuses rather than guessing at fields it does not know
ERROR: .validated-memory/transactions/2222222222222222.json: journal: damaged transaction 2222222222222222: it belongs to adoption ffffffffffffffff, this project is 9784e63239c5a8e6; a mutation of somebody else's tree is not one this history may record
ERROR: .validated-memory/transactions/3333333333333333.json: journal: damaged transaction 3333333333333333: it calls itself transaction aaaabbbbccccdddd and its file is named 3333333333333333; the two are one id, and nothing here can say which of them the history should carry
ERROR: .validated-memory/transactions/4444444444444444.json: journal: damaged transaction 4444444444444444: its intention names no operation this plugin prepares
ERROR: .validated-memory/transactions/5555555555555555.json: journal: damaged transaction 5555555555555555: its preimage or postimage is in no state this plugin knows
ERROR: .validated-memory/transactions/6666666666666666.json: journal: damaged transaction 6666666666666666: it is not valid UTF-8: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte
```

`adoption` is compared only when the journals say what this project's id
is: a tree whose history is gone has none to compare against, and inventing
one there would call every transaction foreign.

**Exactly one pair of records per mutation, whatever happens.** Completing a
transaction appends only the records that are not already there, checked by
transaction id *and* stage, so a crash between the append and the unlink
leaves a `published` file whose records exist and a second pass adds
nothing. Recovery is idempotent in the other direction too: a transaction it
resolves leaves the disk, so a second pass never sees it again.

**Only the affected path gates.** A path an unresolved transaction names is
refused by the executor -- writing over it would destroy the evidence the
operator needs and would record a preimage that was already gone -- and the
refusal names the transaction and the three flags. The rest of the run
proceeds: a single-path transaction can be reasoned about piecewise, and
blocking every mutation would brick the session hook over one stale file.
The refusal is an ERROR, so the run's exit code gates even though the other
items were created.

The harness symlink is the one thing that overrides that refusal. The
`SessionStart` hook's only job is to give a session its memory back, the
transaction file still holds the previous target the record would have
carried, and leaving a session with no memory to protect a file nothing has
read yet is not a trade this hook may make. The link is restored, and a
WARNING says it was not recorded and what the previous target was.

## Resolving a transaction

`diverged`, `unknown` and `damaged` are the three states nothing may decide
for the user, and "refuse" is not a terminal state: a transaction nothing
will ever clear is a project stuck at its session hook. So the operator has
three ways out.

```
python3 -P -m validated_memory journal --resolve ID (--accept | --restore | --abandon)
```

Exactly one flag, and only for a transaction recovery cannot account for.
One that is `recoverable` is refused and told to let the next `init` close
it -- closing it by hand would throw away the record pair of a mutation that
happened. One that is `damaged` is refused too: nothing there says what it
did or what to record about it, so the file is left to be inspected and
removed by hand. And one whose path cannot be READ is refused whichever
flag was given: all three need to know what is there -- `--accept` records
the state it accepted, `--abandon` that the path was left as found, and
`--restore` parks what it is about to discard -- and none of them may be
answered out of a state nothing established.

An id no unresolved transaction has is refused before anything is opened,
so the refusal's own last sentence is true of the tree as well as of the
log: a directory that had never been adopted is left without a
`journal.jsonl` and without a `.validated-memory/`.

- **`--accept`** -- the state the path is in is what the user wants. It
  writes **one `observe`** whose note says it was accepted after divergence
  and which transaction found what, and removes the transaction file.
- **`--abandon`** -- the path is left as found. **One `observe`** saying so,
  and the file goes. Nothing is published and nothing is undone.
- **`--restore`** -- the preimage goes back, and **nothing is recorded**: a
  path returned to the state a record would have described the departure
  from is not a fact about the project.

Over a `diverged` transaction, `--accept` and `--abandon` write the
**mutation's own record pair first**, and their `observe` after it. That
transaction is `published`: its bytes reached the disk, only the two history
records were lost, and the divergence says something wrote the path
*afterwards* -- not that the write never ran. The pair is the one recovery
would have appended, carrying the crashed run's id, the transaction's id and
the state the transaction published, and only the halves that are not
already there are added, so a resolution never doubles a record. `unknown`
gets no pair: it is a `prepared` transaction whose path matches neither of
its states, and nothing there says the mutation ever ran.

`--restore` is the only one that writes bytes, and it is hedged accordingly:

- It applies **only while no history record for that transaction exists**. A
  transaction already in the history is refused -- the `committed` record
  means the mutation happened, an append-only history is not taken back, and
  putting the bytes back without the record would make the journal describe
  a state that is not there. `--accept` or `--abandon` is the answer.
- It **verifies the blob**: present in the preimage store, and digesting to
  the name it is filed under. A blob that is missing here is [a damaged
  log](#the-write-ahead-log), not a clone whose vault stayed behind, and the
  message says which.
- It **parks what it discards**. The operator has chosen to throw the
  current state away, but a regular file at the path is bytes somebody
  wrote, and no command here destroys bytes without leaving a copy: they go
  into the same content-addressed store, and the success line names the
  blob. A symlink at the path is not parked -- its target is a fact the
  transaction file already holds, and the bytes it points at are not this
  path's -- and a non-empty directory is refused rather than removed.
- It restores through the executor's own publication, so it is as atomic and
  as durable as the mutation it reverses, and it takes the mode from the
  transaction file rather than from whatever is at the path now. The
  read-only bit is **not** consulted here: the refusal above exists so a
  mutation never quietly overwrites what an adopter marked unwritable, and
  this is the opposite -- an explicit instruction to put that adopter's own
  bytes back.
- A preimage that was a **directory** is refused: its contents were never
  parked, and nothing here rebuilds one.

## The `journal` subcommand

```
python3 -P -m validated_memory journal [--check]
python3 -P -m validated_memory journal --resolve ID (--accept | --restore | --abandon)
```

Read-only in both reporting modes -- neither runs `probe`, neither writes to
either journal file, and their own record-reading failures are the only
thing they can report on themselves. `--resolve` is the third mode and the
only one that writes.

**Without `--check`**, it reads both artifacts (`journal.jsonl` and
`.validated-memory/local.jsonl`) and reports the combined count. It never
gates on what it finds:

```
$ python3 -P -m validated_memory journal
journal: 13 record(s)
```

Exit `0`, whatever the count -- a reader can look at a project's history
without gating a session on it. An unresolved transaction is still worth
saying, so it is counted on a second line, printed only when there is one:

```
$ python3 -P -m validated_memory journal
journal: 1 record(s)
journal: 1 unresolved transaction(s)
```

**With `--check`**, it additionally runs `reconcile()` and classifies every
unresolved transaction, and reports each finding as an ERROR:

```
$ python3 -P -m validated_memory journal --check
ERROR: .gitignore: journal: open transaction 56eeba099c335aaa (published) on .gitignore: diverged
journal: 1 record(s), 1 error(s)
```

There are five shapes of finding. In order: a `prepared` record with no
matching `committed` twin, with which of the four states its bytes are in; a
closed pair whose halves disagree on a field the mutation itself decided; an
id that is not a pair at all, which has two messages -- a `committed` half
with no `prepared` half, and a transaction recorded more than twice; an
open transaction, with its file's own stage in brackets and the verdict from
the [recovery table](#recovery); and a transaction file too damaged to name
a path, which is named by its own file instead.

```
ERROR: validated-memory.md: journal: unfinished transaction from run 6815e8b2323e4886: the path is applied
ERROR: knowledge: journal: records of transaction 7901cd24a8758b62 disagree on note
ERROR: .gitignore: journal: records of transaction 1ed016d9e88b5435: committed without a prepared half
ERROR: .gitignore: journal: transaction e85966eeb6de80ef is recorded 4 times
ERROR: .gitignore: journal: open transaction 56eeba099c335aaa (published) on .gitignore: diverged
ERROR: .validated-memory/transactions/deadbeefdeadbeef.json: journal: damaged transaction deadbeefdeadbeef: not valid JSON: Expecting value
```

Exit `1` if anything was found, `0` otherwise -- `--check` is the one
reporting mode where an unfinished transaction gates, because a caller that
explicitly asked to be told cannot be told by an exit code of `0`.

**With `--resolve`**, one transaction is closed the way the flag says. The
success line names the flag as it was typed, because a resolution is a
decision someone made:

```
$ python3 -P -m validated_memory journal --resolve 51de77210788b0fd --accept
journal: resolved 51de77210788b0fd (--accept)
```

and a `--restore` that discarded bytes says where they went:

```
$ python3 -P -m validated_memory journal --resolve 56eeba099c335aaa --restore
journal: resolved 56eeba099c335aaa (--restore); the discarded bytes are kept at .validated-memory/preimages/896206210afdc58bebda73324b1601db00749e6b365c8055352d4a85f45ffa1f
```

A refusal -- an id no unresolved transaction has, a transaction recovery can
account for, a damaged one, a missing or mismatched preimage -- is an ERROR
and exit `1`, not a traceback and not a usage error: the id was well formed
and the flags were legal, and what could not be done is a fact about this
project's state.

**Any mode**, a journal that is present but cannot be parsed -- or a
directory the plugin owns under the vault that is not a directory -- is
reported the same way: one ERROR naming the artifact (and the line, when
the fault is a single line's rather than the file's) and the count folded
into the summary, always exiting `1`, `--check` or not:

```
$ python3 -P -m validated_memory journal
ERROR: journal.jsonl:5: journal: line is not valid JSON: Expecting value
journal: 0 record(s), 1 error(s)
```

Exit codes: `0` clean; `1` for an ERROR from any of the three modes; `2` for
a usage error -- a resolution flag with no `--resolve`, `--resolve` with no
flag or with two, `--resolve` alongside the read-only `--check`, or an empty
id, which reaches no transaction and names none in a refusal either.

## The fault-injection seam

One environment variable exists, for tests: **`VALIDATED_MEMORY_FAULT`**.
Set to the name of a protocol seam, the process dies there with `os._exit`
-- no `finally` clause runs, no lock is released, no temporary is cleaned
up, which is what a real crash looks like and what makes an assertion about
the residue honest. The four seams are the whole of the executor's protocol:

| Point | Where the process dies |
|---|---|
| `after-transaction` | The transaction file is fsynced and nothing is published |
| `after-publish` | The new bytes are published and the transaction is not yet marked |
| `after-published` | The transaction is marked `published` and the history is not yet appended |
| `after-history` | The history is appended and the transaction file is not yet removed |

Unset, or naming a point a run never reaches, it changes nothing: one
function reads it, nothing else in the package may, and a test asserts that
two `init` runs -- one with the variable set to an unreached point, one
without -- produce byte-identical output and, the per-run ids aside
(`at`, `adoption`, `run`, `transaction`), identical journals.
