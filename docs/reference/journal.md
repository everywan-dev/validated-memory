# Journal

The append-only record of what adoption did to this project, and the
`journal` subcommand that reports, reconciles and resolves it. Follow the
journey that matches what you need:

- **Understand the record:** [The three records](#the-three-records), [The
  two artifacts](#the-two-artifacts), [The write-ahead
  log](#the-write-ahead-log), [Expected states, and what a precondition
  promises](#expected-states-and-what-a-precondition-promises), [What is
  recorded, and what is not yet](#what-is-recorded-and-what-is-not-yet), and
  [Common fields](#common-fields).
- **Follow a mutation:** [Operations and their
  inverses](#operations-and-their-inverses) and [Stages and unfinished
  transactions](#stages-and-unfinished-transactions).
- **Operate and recover:** [The `journal`
  subcommand](#the-journal-subcommand), [Recovery](#recovery), [Resolving a
  transaction](#resolving-a-transaction), [Recovery after an unconfirmed
  barrier](#recovery-after-an-unconfirmed-barrier), and [Explicit repair of a
  torn history append](#explicit-repair-of-a-torn-history-append).
- **Develop and test:** [The fault-injection
  seam](#the-fault-injection-seam).

Why the journal is split by durability is
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
  Not the harness symlink: `init` writes that record to the vault. New
  invocations require `--harness-memory` to name a path outside the adopter
  and refuse an in-project path before writing. Historical records for
  in-project harness paths remain valid and readable. The link record is not
  subject to the versioning question the adoption questionnaire asks about
  the derived files (ADR 0002, ADR 0003) -- unlike
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
adopter who keeps an established `journal.jsonl` in a shared store and links
it back is read through and appended through, which works because the journal
is the one file this plugin appends to in place rather than replacing. What
is refused instead, and at the point it matters, is bootstrapping through a
symlink that holds no records: the symlink is the adopter's canonical artifact
and bootstrap has no authority to replace it or publish through it.

**Bootstrap never replaces the canonical history name.** It writes and
flushes the complete opening under an unpredictable private regular-file name
in the same directory, fixes its mode, and confirms newly created carrying
directories before publication. POSIX publication hard-links that staging
file to `journal.jsonl`; Windows uses rename only where an existing
destination is refused. A missing no-replace primitive gates before canonical
visibility. A competitor at `journal.jsonl` always wins and is preserved;
there is no exclusive in-place write fallback and cleanup never unlinks the
canonical name.

After publication, the carrying directory and a coherent readback of both
histories must confirm the published inode, mode, exact opening bytes and
semantic pair before any scaffold effect. Private staging cleanup is
best-effort and limited to the unpredictable private name. A hard death may
therefore leave private residue, or a complete canonical opening plus that
residue, but it cannot expose a protocol-created partial canonical opening.

A complete validated lone opening is established identity-bearing history.
Because recognizing its bytes does not prove the interrupted writer's earlier
flush, a fresh process rewrites the identical complete range through a
writable non-truncating descriptor, confirms the file and its directory, then
reacquires the coherent pair. Identity, mode, both histories and every byte
must match the frozen pre-state before later adopter effects. A zero-byte,
blank, partial, malformed, symlinked or non-regular bootstrap name is instead
preserved and refused; it is never repaired, replaced, truncated or removed
by bootstrap.

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
instant can both end up holding it: each checks that the lock is still the
file it examined and then unlinks it by name, and the file can be replaced
between the two calls. Neither the pid nor the inode check closes that race,
which `--lock-wait` neither causes nor widens; what they do guarantee is that
releasing a lock never deletes a file this run did not create.

**The wait for the lock ends at a deadline.** A run waits for a live holder
for ten seconds, and `init` for the `--lock-wait SECONDS` it was given (see
[`init`](cli.md#init)). `init` fixes one deadline when it starts and every
lock it takes in the run shares it, so taking the lock twice does not wait
twice. Each attempt takes the lock, or breaks it when its owner is gone, before
it compares the clock with the deadline, so a spent budget still recovers a
breakable lock. A lock that an attempt broke, or found gone, earns one
immediate retry past the deadline, and that retry can lose the race for the new
file, in which case the run refuses. A break that leaves the name in place makes
the run sleep and try again until the deadline, as it does for a live holder.
The bound covers waiting for the lock and not the work done once it is held
([ADR 0030](../adr/0030-the-session-start-run-is-bounded-and-a-vault-node-that-is-not-a-regular-file-never-blocks-it.md)).

**A lock path that is not a regular file is never opened and never broken.** A
symlink, even a dangling one, a named pipe or a directory at
`.validated-memory/lock` is checked with `lstat` before any open, because
opening a pipe waits for a writer and opening a link follows it. No process
holds it, but it is waited for like a held lock until the deadline, which is
checked on every iteration of the wait, and then the run refuses with an ERROR
that names the path and says to remove it by hand:

```
ERROR: .validated-memory/lock: journal: /path/to/adopter/.validated-memory/lock is not a regular file, so it was neither opened nor broken; remove it by hand and run again
```

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

**A node in those directories that is not a regular file is never opened.** A
transaction or a preimage is read by one rule: `lstat` the name before any
open and refuse what is not a regular file, so a link is not followed even when
its target is a regular file; open with `O_NONBLOCK` and `O_NOFOLLOW` where the
platform has them; then `fstat` the descriptor and require a regular file again.
Opening a pipe for reading blocks until a writer appears, and the `lstat`
describes the name only at the moment it ran. A transaction entry that is not a
regular file is a [damaged transaction](#recovery). A preimage slot that is not
a regular file, a dangling symlink included, refuses the mutation that needs it
before any effect: it is neither opened nor removed, because nothing proves
whose it is, and the ERROR names the slot and says to remove it by hand:

```
ERROR: .gitignore: ignore-rule: the vault's ignore entry (/.validated-memory/) could not be written: the preimage of .gitignore could not be parked, so the mutation was not attempted: .validated-memory/preimages/181314065df2f2fdaf920b1a8b5311daa216a2d6489a06ada5b49cc514d89417 is not a regular file, so it was neither opened nor removed; remove it by hand and run again. Nothing has been written.
```

A regular slot whose bytes do not match the digest it is filed under, or cannot
be read, is still replaced, but only while a second `lstat` right before the
removal shows it is still the regular file that was first examined; a slot that
has become another kind of node or another file by then is refused as a slot
that is not a regular file is, and a process that replaces it between that
`lstat` and the removal is not guarded against ([ADR 0030](../adr/0030-the-session-start-run-is-bounded-and-a-vault-node-that-is-not-a-regular-file-never-blocks-it.md)).

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
| `unconfirmed` | Present only after a reported post-visibility failure: `target`, `history-claim`, `history`, `cleanup` or `restore`. |
| `unconfirmed_reason` | The I/O failure and phase context retained for recovery. |

`prepared` means the entry was fsynced and nothing has been published.
`published` means publication completed and the history had not been
appended yet. Executor-created no-ops open no transaction, so a `prepared`
transaction at its exact postimage remains recoverable after a hard crash
before the marker. The marker proves publication after a later writer makes
the target diverge. `aborted` is closed: it published nothing, it is never
inverted by a reversal, and it disappears with its file. `unconfirmed` is a
local recovery fact, never a permanent-history field. Target phases use a
fresh namespace operation. A complete exact history append instead uses the
descriptor-bound range reconfirmation below; it never treats a second bare
`fsync` as proof about the first one.

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

`init` has one higher-level policy distinction that does not change this
machine vocabulary. An existing managed-directory symlink that resolves to a
real directory inside the adopter is accepted as a logical container. Its
first-sight `observe` record says `directory symlink already present; resolves
inside the adopter to '<target>'`, with an adopter-relative POSIX target. That
note is a truthful historical annotation, not a structured state predicate or
a promise about the symlink's current target; the journal still sees the node
as `symlink(target)`. Historical observations, including older generic notes,
are never rewritten, and each child mutation through the logical container is
authorised and recorded separately. Managed files have no such policy:
keeping one requires the exact regular-file node.

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
  needs descriptor-relative operations, the one precondition [the design
  names and this step does not
  build](../design/2026-09-01-the-journal-core.md#6-preconditions-what-they-can-and-cannot-promise).

**Publication is atomic, with conditional power-loss durability**, in one of
four shapes chosen by what is expected to be there rather than by the op's
name. On a platform and filesystem that supports directory barriers, success
means the file and every required carrying directory barrier completed. A
known unsupported directory barrier preserves atomic visibility but does not
promise that the name survives power loss. An unexpected directory open or
sync error gates instead; it is never treated as unsupported. A failure before
visibility may say nothing was published. A failure after visibility says the
state is visible or may be visible and that durability is unconfirmed; it does
not claim rollback. Windows, where this implementation cannot open directories
for a barrier, is the narrow known-unsupported platform case; access or I/O
errors on supported POSIX filesystems still gate. A directory is
`os.mkdir`, never with `parents=True` -- creating an ancestor nobody asked
for would be a second mutation with no intention and no record, so a missing
parent is a refusal that names it. A creation over an absent name is
`O_CREAT | O_EXCL`, written and fsynced, and refuses a missing parent for
the same reason. A replacement is a temporary file, fsynced and given the
target's mode, then `os.replace`. A symlink is built under a temporary
beside the path and renamed over it, so the link is never absent for an
instant; it is the one shape that still creates its parent, because the
only link this plugin publishes is the harness one, whose path is absolute
and outside the adopter root by construction, so the directory it goes in
is the harness's rather than a mutation of this project. Each journal-owned
directory chain is confirmed from its leaf back to the adopter root before a
durable child is trusted. A missing external harness-parent chain is likewise
created and confirmed before a link transaction is opened. Its durability
anchor is the lexical common ancestor of the adopter root and the supplied
harness path (or the target volume root when those paths are on different
Windows drives), not the nearest directory that happens to exist. The anchor
therefore remains stable if a failed run leaves part of the chain visible,
while creation remains confined to the supplied harness-parent chain. If a
later mkdir fails after an earlier ancestor became visible, the run reports
partial visibility rather than claiming that nothing was written; the
documented fail-open link repair still applies. That repair uses the same
private persistence path for ancestry and atomic link replacement. A later run
that finds the correct link republishes it and reconfirms the full chain to the
stable anchor, so visible residue cannot become an unconfirmed clean no-op.
Unexpected post-visibility confirmation failure is an ERROR even on this
fail-open path; an ordinary failure before the link becomes visible remains a
WARNING. Every
publication then requests a barrier on the directory that carries its name.

This also fixes the public parent policy at the intention boundary. A
repository directory intention creates only its named directory with one
`mkdir`, and both repository directory and file intentions refuse a missing
parent rather than creating it implicitly. The external harness link is the
sole public exception: its supplied external parent chain is created and
confirmed before the link transaction begins. Journal-owned lock,
transaction and preimage directories are private persistence storage, not
implicit adopter scaffold parents. These guarantees do not claim that an
ancestor is descriptor-stabilised; the race described above remains.

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

After the requested harness PATH passes the CLI's outside-adopter usage
preflight, the harness record is formed only after each sync independently
resolves project `memory/` to a real directory at or below the adopter root.
An in-root logical directory symlink remains eligible. An outside-root target
is an ERROR on the requested harness path before sync inspects its parent or
leaf, before take-over can absorb or park anything, and before a LOCAL
intention exists. The harness path and any existing local history are therefore
unchanged, including after a journal-unavailable or vault-unignored gate. A
PATH whose parent the earlier preflight cannot resolve remains an invalid
invocation (exit 2), before `init.run`; it does not reach this project-memory
diagnostic. Neither diagnostic stores or prints the resolved host target. This
ordering does not descriptor-stabilize ancestors and is not a whole-run
rollback promise.

**Two mutations bypass the executor by decision**, and they are the only
two. Both are declared in the design and pinned by name in
`tests/test_journal.py`, so a third cannot be added quietly:

| Write | By | Why it is an exception |
|---|---|---|
| the fail-open repair of the harness symlink | `init.relink` | After the CLI usage preflight accepts PATH, an eligible in-adopter project-memory target is required before every sync action. The contract requires the link back when the journal cannot serve the `SessionStart` hook, whose only job it is. After a refusal, `journal.guarded_harness_repair` takes the run-wide lock, reads the history and the vault, has the harness path read again, and calls the repair inside the same critical section, or withholds it and says why ([ADR 0029](../adr/0029-the-harness-link-survives-a-refusal-that-does-not-name-it.md)); when the vault's ignore entry cannot be established the repair reads only the vault, with the rules of an unreadable journal. An outside-root project target reaches no repair. The repair creates only the supplied parent chain, republishes the link atomically and requests the same durability barriers. A pre-visibility failure remains a WARNING naming the previous target; a visible effect whose barrier fails is an ERROR and cannot become a clean retry. |
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
and a module outside the `journal` package that so much as names the
stage-writing surface -- or imports one of that package's own modules,
or a name its facade does not export -- fails it too.

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
so it says which `committed` closes which `prepared` with no inference at all.
That identity is project-wide across both histories: one intention has one
durability, so the same id appearing in both `journal.jsonl` and
`.validated-memory/local.jsonl` is an ERROR rather than a pair spanning the
artifacts. Records without the field, which is everything a history written
before the executor holds, keep the older rule: file order within a `(run,
path)`, so a `committed` record closes the one `prepared` record it immediately
follows for that pair, never every `prepared` record that happens to share it.

A pair that agrees on nothing but its id is not a pair. These fields must
say the same thing in both halves, and a disagreement is reported as its own
ERROR, never resolved by preferring one half:

`op`, `purpose`, `path`, `durability`, `preimage`, `postimage`, `note`,
`prior_bytes`, `mode`.

`at` is excluded because the two records are written in one append but
stamped separately; `stage` because it is what tells them apart; and `run`,
`adoption`, `schema` and `version` because both halves are filled in from
one source.

**An id is a half of exactly one act**, and three shapes say otherwise. The
same id in both permanent histories is one project-wide identity violation,
reported once against its first record even if either artifact also carries
more than two lines under it:

```
ERROR: .gitignore: journal: transaction 5e2da6723399541b is recorded across repo and local histories
```

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

All three are reported and none is repaired, like every other finding here.

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

It acquires the two histories as one coherent pair and classifies every WAL
against that snapshot. A valid WAL provision suppresses only the unfinished
condition bound to that WAL; it never lends append or cleanup authority to a
different transaction. The binding is exact in adoption, transaction, run,
path, durability, operation, purpose, preimage, postimage, note, prior byte
count, mode and prepared occurrence. It is derived only from independently
valid current-adoption WALs: either every reconstructible field matches the
prepared history record or an exact stored claim proves it. Conflicting, torn,
unavailable and claimless occurrence evidence provides no provision.
Before any transition, topology inspection must be available and every
permanent-history error condition not discharged by its exact provision must be
absent. Topology implementation failures remain fail-open for an unchecked
report but fail closed for checked inspection and before adoption effects. A
permanent-history or topology gate stops the adopting workflow before recovery,
scaffold, no-op target decisions, harness mutation, or cleanup. This is distinct
from the documented fail-open harness-link repair when the journal itself is
unavailable: a usable journal that proves a semantic gate grants no such
exception. A pre-existing
WAL-1 gate whose permanent-history condition is absent or exactly provisioned
remains a per-item, path-local gate: recovery continues to independent eligible items in either
WAL order. Confirmed item lines precede every gate, and an adopting run with a
surviving gate ends stdout with `init: N item(s) confirmed, M gate(s)`; clean
and warning-only adoption has no aggregate summary. Each printed successful
harness-symlink action counts as one confirmed item, including a confirmed
fail-open restoration. Each semantic transition is followed by a fresh
coherent-pair read before the next transition:

| Verdict | Reached when | What recovery does |
|---|---|---|
| `recoverable` -- *complete* | The file says `published` and the path is in the postimage state; or it says `prepared` and the path is in the postimage state and not in the preimage state | A claimless WAL uses the released reconstruction. A valid claim replays its stored exact pair at zero occurrence, only its stored exact committed line after a prepared occurrence (including after valid descendants), or no bytes after a complete occurrence. `init` prints `init: recovered <path> from transaction <id>` only when records were appended |
| `recoverable` -- *discard* | The file says `prepared` and the path is in the preimage state and not in the postimage state | Removes the file. Nothing happened and nothing is recorded, so nothing is printed |
| `recoverable` -- *remove* | The file says `aborted` | Removes the file. It published nothing |
| `diverged` | The file says `published` and the path is not in the postimage state -- something wrote it afterwards | Nothing. The file stays, and an ERROR names the path and the way out |
| `unknown` | The file says `prepared` and the path matches neither state -- or matches both, which only a hand-written file can do; or the path's bytes cannot be read at all, whatever the stage, in which case the message names the stage the transaction reached and carries the reason instead of the state | Nothing. The file stays, and an ERROR names the path and the way out |
| `damaged` | The file is not a well-formed transaction **of this project**: it is not a regular file (a symlink, even to a valid transaction, a named pipe or a directory, none of which is opened), it could not be read, is not valid UTF-8, is not JSON, is not an object, names a `schema` this reader does not know, calls itself an id that is not its own file's name, is filed under another `adoption`, names an operation no intention carries (which includes `observe`, since an observation opens no transaction), or holds a preimage or postimage that is not a state | Nothing. The file stays for inspection, and the ERROR names the file rather than a path, because it names none |

`journal --check` renders the authoritative deterministic condition set used by
adoption, including coherent-pair, topology, permanent-history, WAL and retained
residue conditions. It uses the same classification and condition identities,
so what `--check` promises and what the next quiescent `init` gates on cannot
drift apart. The `damaged` reasons read as sentences about the file:

```
ERROR: .validated-memory/transactions/1111111111111111.json: journal: damaged transaction 1111111111111111: its schema is 999 and this plugin reads up to 1; a reader that meets a higher number refuses rather than guessing at fields it does not know
ERROR: .validated-memory/transactions/2222222222222222.json: journal: damaged transaction 2222222222222222: it belongs to adoption ffffffffffffffff, this project is 9784e63239c5a8e6; a mutation of somebody else's tree is not one this history may record
ERROR: .validated-memory/transactions/3333333333333333.json: journal: damaged transaction 3333333333333333: it calls itself transaction aaaabbbbccccdddd and its file is named 3333333333333333; the two are one id, and nothing here can say which of them the history should carry
ERROR: .validated-memory/transactions/4444444444444444.json: journal: damaged transaction 4444444444444444: its intention names no operation this plugin prepares
ERROR: .validated-memory/transactions/5555555555555555.json: journal: damaged transaction 5555555555555555: its preimage or postimage is in no state this plugin knows
ERROR: .validated-memory/transactions/6666666666666666.json: journal: damaged transaction 6666666666666666: it is not valid UTF-8: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte
ERROR: .validated-memory/transactions/7777777777777777.json: journal: damaged transaction 7777777777777777: it is not a regular file; it was not opened
```

The last is what a symlink, a named pipe or a directory in the place of a
transaction file reads as. `journal --resolve` and `journal --repair` refuse it
as they refuse any damaged transaction, and it stays where it is. A symlink
to a valid transaction file is refused too, and its target is not parsed
([ADR 0030](../adr/0030-the-session-start-run-is-bounded-and-a-vault-node-that-is-not-a-regular-file-never-blocks-it.md)).

`adoption` is compared only when the journals say what this project's id is.
When both permanent histories are empty, any transaction artifact instead
gates `init` and `journal --check` before a new identity or scaffold write.
The artifact is restoration evidence, not authority to mint an adoption
identity. A valid claim may re-establish its own exact zero occurrence only
after the other permanent history establishes the adopter; otherwise restore
matching repository or local history before normal recovery.

**Exactly one pair of records per mutation, whatever happens.** Completing a
transaction appends only the records that are not already there, checked by
transaction id *and* stage, so a crash between the append and the unlink
leaves a `published` file whose records exist and a second pass adds
nothing. Recovery is idempotent in the other direction too: a transaction it
resolves leaves the disk, so a second pass never sees it again.

**Pre-existing gates stay path-local; current uncertainty is terminal.** A path
an unresolved transaction names is
refused by the executor -- writing over it would destroy the evidence the
operator needs and would record a preimage that was already gone -- and the
refusal names the transaction and the three flags. Independent items may
proceed after that pre-existing gate. If recovery itself leaves a target,
history append, restore or cleanup effect visible or indeterminate, the WAL is
retained and the mutating run stops; rerun `init` rather than starting a later
intention. Either condition is an ERROR and exits 1.

The harness symlink's unrecorded fail-open repair is narrower than a journal
refusal, and it is one guarded operation
([ADR 0029](../adr/0029-the-harness-link-survives-a-refusal-that-does-not-name-it.md)).
After the project-memory target has independently been found eligible inside
the adopter, the repair takes the run-wide lock, reads the history snapshot and
the vault, and relinks or withholds in the same critical section, so a
transaction cannot appear between the decision and the link.

- **A topology refusal before any adopting effect** allows it only if the
  snapshot is usable, every outstanding `history.*` condition is
  `history.topology_gate`, no condition names the harness path as its subject
  or in its pairing, every entry of the vault's transaction and preimage
  directories is a regular file (a symlink or any other node is never opened
  and withholds), those directories hold only canonical artifacts, and every
  transaction file parses, names a path, and does not name the harness path.
  When the lock or the vault cannot be read the link is withheld.
- **A journal that cannot be read** allows it under the vault rules alone,
  because the history cannot be read. When the lock cannot be taken for a
  reason other than another process holding it, the vault is still read,
  without the lock, and the same rules apply. Only when the vault cannot be
  listed at all does it run unguarded, which is the declared exception of the
  journal core.
- **Every other refusal** withholds it: damaged, unsupported, identity and
  bootstrap refusals cannot prove the absence of authority over the harness
  path, and neither can uncertainty after a current effect. A lock held by
  another process withholds it too, without waiting for the lock a second time
  when it was already held on the first attempt; a lock another process takes
  between the refusal and the repair is waited for only for what remains of the
  run's `--lock-wait`, and then withholds it.
- **A lock path that is not a regular file** blocks it without a wait, because
  no process holds it: the WARNING names the lock path and says to remove it by
  hand, and it does not end in `run journal --check`.

Recorded paths are compared with the harness path as directory entries, and
nothing is collapsed lexically, because `..` after a symlink names the parent
of the symlink's target: two paths name the same entry when the real paths of
their parent directories are equal and their final names are equal without
regard to case, or when both entries exist and are the same file. The final
component is never followed.

**An unignored vault** gates the run without a journal refusal; its repair goes
through the same guard with the vault rules of an unreadable journal.

**The harness path is read again once the link is staged, immediately
before the rename.** `init` records what stands at it with one `lstat` --
the file type and, for a symlink, its target, and no inode number, because
a session that publishes the same link again creates a new inode and inode
numbers are not stable on every filesystem -- and both routes that relink
under the guard read that identity again, once the link is staged and
immediately before the rename that publishes it: the repair after its vault
check and the one that runs when the vault cannot be listed. A staged link
that the reading stops is unlinked on a best-effort basis, and one may
remain if the parent cannot be written; a staged link replaced meanwhile is
neither published nor removed, and a WARNING names it. A path that by then
resolves to `memory/` is left alone and is not reported, whether or not
what stands there changed. Otherwise an identity that is unchanged relinks,
and any other change, or a path that can no longer be looked at or
resolved, withholds the repair with the reason `the harness path changed
while the repair waited` or the unreadable one, and a withheld link that is
about the harness path itself does not end in `run journal --check`,
because the journal has nothing to say about it. A harness path that cannot
be looked at when `init` first reads it, or a symlink that loops, is
likewise left as it is, with a WARNING, without changing the exit code.

The lock serialises validated-memory processes only. A process outside the
plugin that replaces the harness path after the `lstat` of that second reading
and before the rename that publishes the link is not guarded against: the
window is from that `lstat` to the rename, the parent directory having been
made and the temporary link staged before it, and the standard library has no
compare-and-swap on a pathname. The relink never replaces a directory
([ADR 0030](../adr/0030-the-session-start-run-is-bounded-and-a-vault-node-that-is-not-a-regular-file-never-blocks-it.md)).

A restored link is not recorded, and a WARNING says so and names the previous
target. A withheld link is a WARNING that names the harness path and the reason
and ends in `run journal --check`, and the first run the journal allows
restores it. A link that already resolves to `memory/` is not reported. The
refusal is still an ERROR and exits 1 in every case. `status` reports the history conditions that cause a
refusal without gating on them (see [`status`](cli.md#status)).

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

Resolution never establishes an adoption identity. The exact transaction
artifact is checked before the filesystem lock is acquired, because acquiring
that lock creates its parent directory. When the artifact is present it is
read again under the lock, and both histories must already establish the one
adoption it belongs to. An artifact with neither repository nor local adoption
history is contradictory residue: it is refused, left byte-for-byte unchanged,
and no `journal.jsonl` is created from it. Only `init`'s adopting run may create
that opening history. Resolution gives action authority only to the named
artifact and never recovers another transaction. It classifies the coherent
pair and all provisions so independent gates remain visible after a confirmed
selected effect, but another WAL's provision cannot authorize the selected
append or cleanup.

An `unknown` selected WAL that exactly provisions its own prepared history
condition is refused for all three dispositions (`--accept`, `--abandon` and
`--restore`) before any
target, history, observation or cleanup effect. A resolution observation's
generic committed shape cannot discharge that prepared condition, and another
WAL's provision cannot be borrowed. Preserve the selected WAL and both
histories, restore an accepted exact history state from a trusted source, then
run `journal --check`.

- **`--accept`** -- the state the path is in is what the user wants. It
  writes **one `observe`** whose note says it was accepted after divergence
  and which transaction found what, and removes the transaction file.
- **`--abandon`** -- the path is left as found. **One `observe`** saying so,
  and the file goes. Nothing is published and nothing is undone.
- **`--restore`** -- the preimage goes back, and **nothing is recorded**: a
  path returned to the state a record would have described the departure
  from is not a fact about the project.

Over a `diverged` transaction, `--accept` and `--abandon` write the
**mutation's own record pair first**, and their `observe` after it. A valid
stored claim supplies those exact bytes; a claimless legacy WAL uses the
released reconstruction. That
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
  message says which. A blob that is not a regular file is unavailable and is
  not opened.
- It **parks what it discards**. The operator has chosen to throw the
  current state away, but a regular file at the path is bytes somebody
  wrote, and no command here destroys bytes without leaving a copy: they go
  into the same content-addressed store, and the success line names the
  blob. A symlink at the path is not parked and its target is **not kept**
  -- a link is a name and a target rather than bytes, there is nothing for
  a content store to hold, and the bytes it resolved to belong to the path
  it named, which `--restore` does not touch. The transaction file does not
  hold that target either: what it records is the preimage's, and a link
  something else put there afterwards is the case being resolved. The
  target is in the finding that reported the divergence -- `journal
  --check` and `init` describe a symlink by where it points -- and nowhere
  after that. A non-empty directory is refused rather than removed.
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
python3 -P -m validated_memory journal --repair TRANSACTION_ID
```

Read-only in both reporting modes -- neither runs `probe`, neither writes to
either journal file, and their own record-reading failures are the only
thing they can report on themselves. `--resolve` and `--repair` are the two
targeted modes that write: `--resolve` records an operator's decision, and
`--repair` performs a proof-carrying history-tail repair.

A reporting pass acquires the repository and local histories as one coherent
descriptor-bound pair. If either name changes while that pair is being read,
the command retries the complete pair a bounded number of times. If the names
do not stabilize, both reporting modes refuse with `journal histories changed
during inspection; rerun the command`; they report zero accepted records and
write nothing. Here, `0 record(s)` means that no coherent pair was accepted for
counting; it does not mean that either history is empty, corrupt or lost. A
replacement that stabilizes within the bound is silent, and the count and
reconciliation use that one accepted pair rather than rereading either history.

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
id that is not a pair at all, which has three messages -- a `committed` half
with no `prepared` half, a transaction recorded more than twice in one
artifact, and one id recorded across both histories; an open transaction,
with its file's own stage in brackets and the verdict from the [recovery
table](#recovery); and a transaction file too damaged to name a path, which
is named by its own file instead.

```
ERROR: validated-memory.md: journal: unfinished transaction from run 6815e8b2323e4886: the path is applied
ERROR: knowledge: journal: records of transaction 7901cd24a8758b62 disagree on note
ERROR: .gitignore: journal: transaction 5e2da6723399541b is recorded across repo and local histories
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

The hard-crash environment variable is **`VALIDATED_MEMORY_FAULT`**.
Set to the name of a protocol seam, the process dies there with `os._exit`
-- no `finally` clause runs, no lock is released, no temporary is cleaned
up, which is what a real crash looks like and what makes an assertion about
the residue honest. The four seams are the whole of the protocol-selected
mechanical mutation sequence:

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

Tests of ordinary I/O errors use the separate private
`VALIDATED_MEMORY_PERSISTENCE_FAULT` seam. It raises a caught directory
open/sync or post-visibility barrier error without imitating process death;
`unsupported` simulates only the explicitly recognised missing capability.
Comma-separated points can model a protected effect and a recovery-fact
barrier failing in one run; `fact:target`, `fact:history`, `fact:cleanup` and
`fact:restore` address only that retained local fact. It is not part of the
crash-point vocabulary above.

The private `mark-published` persistence point raises a caught I/O error after
target publication and before the WAL's published-marker rewrite. It exercises
the retained target-uncertainty path; it is not a fifth hard-crash seam.

Storage-only crash tests use the separate private
`VALIDATED_MEMORY_STORAGE_CRASH` seam. `history-prefix:N` writes and fsyncs
exactly the selected byte prefix before `os._exit`, while
`staged-before-install:NAME` stops after a staging file or link is fsynced and
before its atomic install. The seam is inert unless explicitly selected and
does not add a fifth protocol crash point.

## Recovery after an unconfirmed barrier

A target barrier failure retains the WAL and writes no permanent history.
Recovery requires the exact postimage, republishes that state through a fresh
namespace operation, confirms it, then completes the history exactly once. A
changed or unreadable target remains gated. File bytes are digested and checked
again together with regular-file kind and applicable mode immediately before
the validated snapshot is installed. A same-byte symlink or mode change is not
the state the recovery validated. Because a
directory postimage records kind but not membership, a directory that acquired
a child after the failure remains gated instead of being replaced. An empty
directory is recovered only after a separate staging directory and its carrying
entry are confirmed, deepest first. Windows does not provide this implementation
with a portable atomic empty-directory replacement, so automatic recovery stays
gated there with the WAL and an actionable unsupported-recovery diagnostic. If an
exclusive file creation fails while writing its content, absence is claimed
only after removal of the visible name and its carrying-directory barrier are
confirmed; uncertain cleanup retains a target-phase WAL instead.

A history barrier failure also retains the WAL. Descriptor-bound append
reconfirmation is available only when that WAL carries valid exact retained
evidence and the complete retained append is present at its proven range. Recovery acquires and
parses both histories coherently, freezes their identities, modes and complete
bytes, and requires the proven prefix plus a complete compatibility-valid
suffix with no conflicting semantic condition. A changed prefix or digest,
malformed or torn suffix, duplicate or conflicting record, changed mode or
identity, or changed opposite artifact refuses before the range action.

If the retained append is absent, incomplete, or mismatched in the current
history, reconfirmation writes nothing and does not complete or replay it. The
transaction and both histories are preserved, and the diagnostic directs the
operator to restore the affected history from a trusted copy before rerunning
`init`. Other pre-write refusals distinguish an observed competing writer
(wait for it to finish) from an I/O or access obstruction (restore access or
remove the obstruction). They do not recommend removing a writer unless a race
was actually observed.

When file-data durability was not confirmed, recovery writes the identical
claimed bytes at their exact offset through a writable, non-truncating
descriptor, without touching the frozen prefix or suffix, then confirms the
file and carrying directory. Only an exact retained proof that the file-data
barrier completed permits the directory-only path, which performs no byte
rewrite. Both paths reacquire the coherent pair and require the selected
identity and mode, exact prefix/range/suffix, opposite artifact and semantic
successor to match the frozen pre-state. Only then may the selected WAL be
removed. A failure after the first byte write or directory confirmation retains
the WAL and gates later adopter effects. Recovery never replaces or truncates a
history, overwrites a valid suffix, reconstructs a missing history from one WAL,
or treats a torn tail as an incomplete pair; torn-tail repair belongs to the
separate maintenance operation below.

A cleanup barrier failure occurs only after target and history confirmation.
The WAL is republished as recovery evidence when its first removal is
unconfirmed; the next run repeats cleanup without duplicating the history
pair. An operator restore uses the same rule for its exact restored preimage.
If retaining the phase fact is itself unconfirmed, the diagnostic reports both
failures and promises only that every artifact still available was kept.
Before a WAL retained with uncertain history-claim durability authorizes any
recovery or targeted-resolution history, target, observation or cleanup action,
the selected workflow durably re-installs that WAL's identical bytes and reads
the same valid claim back against the unchanged coherent pair. A WAL write or
carrying-directory barrier failure is terminal: no target, history, observation
or cleanup action follows, and later WALs remain untouched. Each ordinary
recovery or resolution append freezes the exact successor
history bytes, selected identity and mode, unchanged opposite artifact, and
expected condition discharge before acting; cleanup and any later transition
require the reacquired pair to match that successor with no new condition.
Restore recovery applies the same boundary to both the exact restored target
and the unchanged history pair. A race at any of these post-effect boundaries
retains the selected WAL, reports no success, and leaves later WALs untouched.
An unavailable topology inspector, missing usable topology snapshot,
incompatible pair or wrong condition transition after an effect is the same
terminal retained result; absence of reported conditions never counts as
success when inspection itself was unavailable.
For append reconfirmation, an initial data or directory failure tells the
operator to preserve the retained transaction and rerun `init`. If cleanup is
uncertain after the append was confirmed, the diagnostic says that the append
must not be repeated and again directs the operator to preserve the retained
transaction and rerun `init`.

## Explicit repair of a torn history append

History is strict JSONL: a non-empty file whose final byte is not `LF` is
malformed. Reporting, `init`, and `journal --check` refuse it without writes;
`init` never repairs it implicitly. A mutation WAL records one proof-carrying
`history_append` claim before its append: the selected artifact, exact prefix
length and SHA-256 digest, the canonical sorted-key UTF-8 record pair, and the
append length and digest.

An operator may target that one transaction:

```
python3 -P -m validated_memory journal --repair TRANSACTION_ID
```

The command accepts only a current published/history-uncertain WAL carrying a
valid claim. It requires an existing authoritative history, an unchanged
claimed prefix, and a tail that is only a strict prefix or complete copy of the
claimed append. Before publication, the protocol constructs the candidate pair
from the exact repaired bytes plus the raw opposite artifact and performs the
same topology inspection used by the other mutating workflows. Any condition
involving the selected transaction, transaction identity reuse, or adoption
mismatch refuses before publication. Other unrelated pre-existing conditions
neither authorize nor forbid the proof-bound byte repair; they are frozen for
successor comparison. That frozen domain includes damaged, newer, diverged,
unknown, and recoverable unrelated WAL conditions; together they form the
complete frozen pre-publication condition domain. Only the selected repair
WAL's own condition is excluded.

The selected WAL is read under the lock through one descriptor and its exact
bytes, mode, and identity are frozen. They are rechecked before publication and
before any temporary or WAL cleanup. A claimed staging symlink must bind its
filesystem-byte target and digest to the WAL intention and postimage; a claimed
regular file must bind its digest to the WAL postimage. Parent, type, mode,
identity, exact bytes or link target are frozen before publication. Malformed,
unbound, or unsafe claims refuse without a history change. Cleanup removes only
the same frozen name; a replacement is preserved and the repair is retained.

The complete `prefix + append` snapshot is built in an unpredictable exclusive
temporary, fsynced, revalidated, and atomically installed. A symlink history's
resolved backing file and mode are preserved. Immediately before installation,
the staging verifier reacquires the frozen pair, selected WAL, opposite raw
artifact, staged bytes, and complete condition domain; a mismatch refuses
before publication. Repair then reacquires both
histories as one coherent pair. The selected condition must be discharged, no
new condition may appear, and the remaining set must be a subset of the frozen
pre-publication conditions. A clean successor and confirmed selected-WAL
cleanup print the repaired line and exit 0. When only frozen independent gates
remain, repair also prints each gate and `journal: repair confirmed, N gate(s)
remain`, exits 1, and directs the operator to address the gates, run `journal
--check`, and do not repeat the confirmed repair. Gate text names the human
condition and never exposes internal condition identifiers. A visible or indeterminate
publication whose successor or cleanup cannot be confirmed is retained,
prints no repaired line, and keeps the available proof for the same targeted
retry.

The gate renderer is the same canonical checked-condition renderer used by
`journal --check`. Its message retains the relevant transaction, run, path
state, disagreeing field, or retained-evidence reason; repair adds only the
instruction to address that condition, rerun `journal --check`, and not repeat
the confirmed repair. After temporary cleanup and exact private duplicate
cleanup, repair reacquires a final coherent snapshot while the exact selected
WAL remains. The published pair and selected discharge must still hold and no
new condition may have appeared. Only this final state supplies the reported
gates: an exact duplicate already removed is not a stale gate, foreign residue
remains under the normal residue contract, and cleanup or final-confirmation
uncertainty is `Retained` with the selected WAL preserved. The selected WAL is
removed last, only after that confirmation; an unconfirmed removal identically
re-establishes it for the same targeted retry.

Missing histories, legacy or foreign WALs, interior corruption, changed
prefixes, unrelated tails, competing claims, unsafe artifact types, and
retargeted links are refusals with no writes. A complete final snapshot is
idempotently confirmed and the selected WAL is removed only after cleanup is
confirmed. `--check` remains read-only and does not perform this operation. It
enumerates non-canonical entries retained in the private `transactions/` and
`preimages/` directories. Targeted repair leaves unclaimed residue untouched;
it does not recursively scan the adopter tree and does not remove a candidate
without proof of an exact canonical duplicate. Unclaimed residue stays
byte-, type-, and mode-identical.
