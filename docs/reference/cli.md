# CLI reference

```
python3 -P -m validated_memory <command>
```

`validated_memory.cli` is an internal implementation module, not a second
CLI. Executing it directly is a usage error; use only
`python3 -P -m validated_memory` as shown above.

Commands: [`init`](#init), [`lint`](#lint), [`validate`](#validate),
[`derive`](#derive), [`probe`](#probe), [`recall`](#recall), [`render`](#render),
[`status`](#status), [`journal`](#journal), [`consultation`](#consultation),
[`agent`](#agent).

Exit codes: `0` = clean run or WARNING-only findings (does not gate);
`1` = ERROR (gates); `2` = usage error.

Running the module requires the package on `sys.path`: from a checkout,
prefix commands with `PYTHONPATH=<plugin root>`, or install it with pip —
see [Installing](../installing.md). The plugin's own hooks resolve this
themselves. `-P` is not optional: without it, the current working
directory could resolve `validated_memory` ahead of `PYTHONPATH` — see
[ADR 0006](../adr/0006-the-cli-is-always-invoked-with-python-p.md).

### `agent`

```
python3 -P -m validated_memory agent profile
python3 -P -m validated_memory agent hook --host claude-code
```

`agent profile` reads optional agent-integration intent from
`validated-memory-profile.md` at the current exact project root. It emits one
canonical, sorted JSON line. For a configured explicit/lightweight profile:

```json
{"configured":true,"discovery":"explicit","host_support":{"delivery_verification":"not_checked","shipped":["claude-code"]},"operation":"profile","profile_path":"validated-memory-profile.md","reliance":"lightweight","schema_version":1}
```

A missing file is a successful unconfigured result: `configured` is false,
`discovery` is `off`, and `reliance` remains `lightweight`. A malformed,
unreadable, unsafe or unsupported profile emits no stdout, one bounded English
diagnostic on stderr and exit 1. Invalid arguments are usage exit 2.

The operation is read-only: it performs no discovery, host launch, store access
or write. `host_support.shipped` names the adapter included in the plugin;
`delivery_verification: not_checked` means this status did not run or verify the
installed host. See [Agent integration](agent-integration.md) for the closed
profile, setup/change/deactivation flow and smoke test.

`agent hook --host claude-code` is the machine-facing operation used by the
`UserPromptSubmit` hook. It consumes one UTF-8 JSON object on stdin, at most
65536 bytes, with `hook_event_name: UserPromptSubmit`, an absolute `cwd` string
and a `prompt` string; extra host fields are allowed. Invalid CLI arguments are
exit 2. Handled host input, configuration, acquisition and timeout failures are
fail-open exit 0, with bounded diagnostics and safe `additionalContext` when the
attempted lookup can be identified.

Invoked discovery returns this host envelope, bounded to 8192 UTF-8 bytes:

```json
{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"..."}}
```

There is no top-level blocking decision. Unconfigured/off, non-adopter and
explicit-mode non-invocation produce no output and acquire no corpus. The hook
never writes, records a prompt, certifies checked use or gates editing/delivery.
The exact query grammar, lexical limits, result qualification and privacy rules
are in [Agent integration](agent-integration.md); hook registration and failure
policy are in [Hooks](hooks.md#prompt-discovery).

### `init`

```
python3 -P -m validated_memory init [--harness-memory PATH] [--view [--app]] [--lock-wait SECONDS]
```

`--view` creates missing canonical knowledge.html and memory.html. Add `--app`
to select the optional knowledge-app.html too. `--app` without `--view` is usage
exit 2 before any write. Existing selected views, including empty or hand-edited
files, are kept; a broken view symlink is warned and left untouched. Regeneration
belongs to `render`, not initialization.

`--lock-wait SECONDS` bounds how long the whole run waits for another
validated-memory process to release the run-wide lock. The default is 10, the
wait every other locking command has. SECONDS is a finite number of zero or
more, and `0` does not wait; a negative number, `nan`, `inf` or a value that is
not a number is usage exit 2 before any write. The run fixes one deadline when
it starts, and every lock it takes -- the adopting scope and the guarded
harness repair below -- shares it, so a run that takes the lock twice waits no
longer than SECONDS in all. A lock whose owner is gone, or that is past the age
horizon, is still broken and taken once the deadline has passed. A lock that is
still held at the deadline ends the run with the busy ERROR and exit 1. The
bound covers waiting for the lock: the work the run does once it holds the lock,
`adopt.take_over` included, is not bounded by it. The plugin's `SessionStart`
hook passes `--lock-wait 3` (see [Startup hooks](hooks.md)), and
[ADR 0030](../adr/0030-the-session-start-run-is-bounded-and-a-vault-node-that-is-not-a-regular-file-never-blocks-it.md) records why.

Scaffolds a new adopter project in the working directory: `knowledge/`
(empty), `memory/` with an empty index (`memory/MEMORY.md`), the adopter
configuration (`validated-memory.md`), and a valid, empty declared-extension
stub (`knowledge-extension.md`). Right after `init` on an empty directory,
`validate` and `lint` both pass clean -- the bootstrap is verified by the
enforcement it bootstraps, not by inspection. (An empty `knowledge/`
directory still reports its usual WARNING for having no units; that does not
gate.) `init` does not create the optional `validated-memory-profile.md`;
the adoption skill authors it only after the user's agent-integration choice.

Each item is created only if missing. An existing item -- including one
already hand-edited -- is never touched: `init` reports `init: created
<path>` or `init: kept <path>` per item, so re-running it is idempotent and
says so. `init` gates (exit 1) on an item it could not create at all (e.g.
no write permission on the target directory, or a broken symlink standing
where the item goes -- installing over it would replace the link, and `init`
never destroys something already there), and on a journal it could not read
or write -- see [Journal](journal.md).

Managed directories and managed files deliberately have different node
rules. `memory/` and `knowledge/` may be real directories or symlinks that
resolve to real directories inside the adopter. Such an in-adopter directory
symlink is kept as a logical container; its first observation identifies it
as a symlink and names the resolved adopter-relative target. Its children are
still authorised and recorded separately. A directory symlink that is broken,
loops, resolves outside the adopter or resolves to a non-directory is refused
and preserved. At a managed file name, only the exact regular file is kept:
every symlink is refused and preserved rather than read through.

An item `init` cannot create is refused by the executor, which names what it
found rather than what it expected in the abstract. The three shapes an
adopter meets:

```
ERROR: .gitignore: ignore-rule: the vault's ignore entry (/.validated-memory/) could not be written: .gitignore is mode 0444, which denies writing to this user. Nothing has been written.
ERROR: memory: create: directory could not be created: memory is a file, and this create expects it to be absent. Nothing has been written.
ERROR: validated-memory.md: create: file could not be created: validated-memory.md is a symlink to 'real.md', and this create expects it to be absent. Nothing has been written.
```

The first is new in this release and is the one behaviour change: a target
whose mode denies writing to the current user is refused rather than
rewritten, so a `.gitignore` the adopter made read-only used to come back at
0644 and now gates every session until someone `chmod`s it, or until the
rule reaches `.git/info/exclude` -- see [Expected
states](journal.md#expected-states-and-what-a-precondition-promises). The
other two used to be reported `kept` and journalled as "already present",
which was a permanent, uninvertible claim about something that was not what
it said.

Before any of that, `init` closes what an earlier run left half done: it
recovers every open transaction in the write-ahead log, under its own lock
and ahead of its first intention (see [Recovery](journal.md#recovery)). A
mutation that had been published but not yet recorded gets its records a
session late, and says so:

```
init: recovered .gitignore from transaction 9e368fbaf699d836
```

That line is printed only when records were actually appended. A crash
between the append and the transaction file's removal leaves a history
that is already complete, so the recovery that finds it removes the file,
gains nothing and says nothing: "recovered" about it would announce a
mutation the journal already carried.

A retained valid append proof replays only its stored exact bytes: both lines
when absent, only the stored committed line after a prepared line (even with
valid descendants after it), and no line for an already complete pair. A
claimless WAL follows the released reconstruction rules. Before a proof whose
WAL durability was uncertain authorizes any recovery or targeted-resolution
history, target, observation or cleanup action, the selected workflow durably
re-installs that WAL's identical bytes and reads the proof back. A write or
directory-barrier failure is terminal: none of those actions follows and later
WALs remain untouched. Provisions are local: independently valid
current-adoption WALs bind only an exact prepared occurrence whose adoption,
transaction, run, path, durability, operation, purpose, preimage, postimage,
note, prior byte count and mode all match reconstructible evidence or an exact
stored claim. Conflicting, torn, unavailable and claimless occurrence evidence
provides no provision and cannot authorize another WAL's action. Every non-cleanup effect requires an available
topology inspection with no unprovided history error; an inspector exception is
fail closed here even though read-only journal reporting remains fail-open. A
pre-existing refusal remains per-item and independent cleanup-only WALs continue
in either order. Uncertainty
created by recovery itself is terminal: it retains the evidence, exits 1 and
stops the run; preserve it and rerun `init`.

Every recovered or selected append freezes its expected coherent successor.
Before cleanup or a later append, `init` verifies the selected history bytes,
identity and mode, the unchanged opposite history, and the exact expected
condition discharge with no new condition. Restore recovery likewise rereads
the pair and exact restored target before cleanup. A post-effect mismatch is
terminal, retains the selected WAL, and never reports success or advances to a
later transaction. Topology unavailability, a missing usable snapshot,
incompatibility or a wrong condition transition at that readback is a mismatch,
not an empty successful condition set.

A transaction recovery cannot account for is an ERROR that gates that one
path -- nothing may write over it until `journal --resolve` closes it -- and
the rest of the run proceeds. Confirmed item lines are printed before gates.
When any gate remains, the final stdout line is exactly `init: N item(s)
confirmed, M gate(s)`; a clean or warning-only run has no aggregate summary.
A confirmed `created`, `kept` or `re-pointed` harness-symlink line counts as
one item in that summary, including a confirmed fail-open link restoration.

A vault node that is not a regular file never blocks a run and is never
opened. `init` reads the vault's transaction entries, preimage slots and lock
without following a link and without opening a pipe:

- A transaction entry that is a symlink, a named pipe or a directory is a
  damaged transaction. The ERROR names the entry and says
  `it is not a regular file; it was not opened`, the entry is kept, and
  `journal --resolve` and `journal --repair` refuse it. A symlink to a valid
  transaction file is refused the same way, and its target is not parsed.
- A preimage slot that is not a regular file refuses the mutation that needs it
  before any effect. The ERROR names the slot and says to remove it by hand, and
  the slot is neither opened nor removed. A regular slot whose bytes differ is
  still replaced, only while a second `lstat` shows it is still the file that
  was examined; one that has changed kind or file in the meantime is refused the
  same way.
- A lock path that is not a regular file is held until the run's lock deadline,
  and then refused with an ERROR that names the path and says to remove it by
  hand. It is not broken, and it is not a holder: the harness repair below does
  not wait for it again.

A regular file that is not a valid transaction is reported as before. See
[Journal](journal.md) for the entries and slots, and [ADR 0030](../adr/0030-the-session-start-run-is-bounded-and-a-vault-node-that-is-not-a-regular-file-never-blocks-it.md).

The first repository history uses no-replace publication, and is published
only after its complete opening bytes and mode have been flushed under an
unpredictable private name in the
same directory. On POSIX the canonical `journal.jsonl` is created by a
hard-link operation; on Windows it is renamed only with the platform's
refusal-to-replace semantics. If that guarantee is unavailable, or another
artifact reaches the
canonical name first, `init` exits 1 before scaffold work and preserves the
canonical artifact. It never falls back to replacing the name or writing a
partial opening directly into it.

A complete lone opening left by an interrupted earlier bootstrap is retained
as established history. Before any later adopter effect, a fresh process
re-dirties its complete validated byte range through the same non-truncating
file descriptor, confirms the file and carrying directory, and coherently
reads both histories back. Any failure gates the run; `init` never unlinks or
replaces that canonical opening. Private staging cleanup is best-effort, and
uncertainty names the private residue without granting cleanup authority over
`journal.jsonl`.

When a retained WAL proves an exact complete history append whose durability
was not confirmed, `init` reconfirms only that byte range through a writable,
non-truncating descriptor. It freezes both coherent histories, their identities
and modes, the proven prefix and complete valid suffix, and the opposite artifact.
Without exact file-flush proof it writes the identical retained range
and confirms the file and directory; when the retained proof isolates only the
directory barrier, it skips the rewrite and confirms the directory. It then
reacquires the coherent pair before removing the WAL or performing a later
adopter effect. A mismatch before the action is refused; uncertainty after a
write or directory confirmation retains the WAL and gates the rest of the run.
No path is replaced or truncated, and a valid suffix is never overwritten.
An initial data or directory failure tells the operator to preserve the retained
transaction and rerun `init`. A pre-write refusal recommends waiting only when
another writer was observed, restoring the affected history from a trusted copy
for absent or mismatched evidence, or restoring access/removing an environmental
obstruction for an I/O failure. If the retained append is absent, incomplete, or
mismatched, reconfirmation writes nothing, preserves the transaction and both
histories, and does not complete or replay the append. If cleanup becomes
uncertain after confirmation, the append must not be repeated; preserve the
retained transaction and rerun `init`.

`init` also appends one line to the repository's ignore file (`.gitignore`,
created if missing): `/.validated-memory/`, the vault. That entry is not one
of the adoption questionnaire's answers and is written on every adoption,
whatever the project versions -- the vault holds preimages, which may carry
bytes the adopter deliberately kept local (ADR 0008). It is written once:
an ignore file that already carries the rule is left exactly as it is, and
the edit is journalled with purpose `ignore-rule` -- as a `create` when
there was no ignore file at all, and as an `append` when there was one, the
two having different inverses. It is reported on its own line
(`init: ignored /.validated-memory/ in .gitignore`) and is not counted
among the items created or kept, because it is a line appended to a file
the adopter owns rather than an item `init` manages. An ignore file that is
a symlink, or that cannot be read, is left untouched and reported as an
ERROR, unless `.git/info/exclude` already carries the rule --
that is the one other source git still consults when it cannot read
`.gitignore` itself, and a vault git already ignores needs no gate. A
symlinked `.gitignore` is never read through: git opens that file with
`O_NOFOLLOW` and ignores nothing it says, so reading the target would call
an exposed vault ignored.

That ERROR stops the run: no scaffold item is created, and no harness
memory is absorbed or parked. An unignored vault is the one thing the entry
exists to prevent, so nothing may write into it -- the vault is left
byte-for-byte as it was. The harness symlink is the exception, because
restoring it moves no data: it is restored without its record, with the
WARNING saying why, so a renamed project still finds its memory, unless the
vault holds a transaction that names the harness path or anything else the
repair cannot account for, which withholds it as it does after a journal
refusal. Nothing is linked when this project has no `memory/` of its own,
there being nothing to point at.

`validated-memory.md` declares the full adopter surface `extension.py`
validates: the declared extension (`schema`, `version`), the `id_prefix`,
and the probe registry, already mapping `git_ref` to its bundled probe
command (`python3 -m validated_memory.probes.git_ref`; see [The bundled
`git_ref` probe](#the-bundled-git_ref-probe)) -- see [Adopter
configuration](curated-knowledge.md#adopter-configuration). `knowledge-extension.md` declares no fields (`fields: []`, a valid,
empty extension) and its body documents, in prose, the field format (`name`,
`type`, `values`; types `string` and `enum`) and the versioning rule.
Both files are plain Markdown with a YAML-subset frontmatter: readable
without the plugin installed.

**`--harness-memory PATH`** requires PATH to be outside the adopter project
and makes it a move-proof symlink to this project's `memory/` directory
(absolute target), so the harness can read agent memory from wherever it
expects it while the data stays inside the adopter repo (versioned, if the
adopter versions the layout -- see [the adoption
guide](../adoption.md#2-decide-what-this-repository-versions)). A PATH at or
below the adopter root, including one reached through its parent symlink, is
an invalid invocation: `init` exits 2 before writing, absorbing or parking
anything. The final PATH component is not followed for this check, so an
existing external harness symlink can still be kept or re-pointed:

After PATH passes the preceding outside-adopter usage preflight, every harness
sync independently resolves the project's `memory/` before it inspects the
harness parent or leaf or creates any parent directory. That target must be a
real directory at or below the adopter root. A real in-adopter directory and
an in-adopter directory symlink are both eligible. If `memory/` resolves
outside the adopter, `init` reports an ERROR on PATH saying that project memory
resolves outside the adopter and that the harness path was left untouched; it
does not disclose the resolved host path. No harness parent or leaf is created
or changed, no native harness directory is absorbed or parked, and no local
link intention or record is formed. This check applies equally during healthy
initialization and after a journal refusal or an unignored vault. If the earlier PATH preflight
cannot resolve the supplied parent, that invalid invocation remains the B1
usage error (exit 2) and no project-memory diagnostic is produced. Missing,
broken, looping and non-directory project-memory nodes retain the existing
"no `memory/` to link to" no-action warning.

A relative PATH is joined to the current directory, which is the adopter root,
and nothing more is done to it: `init` neither collapses `..` nor resolves a
symlink, so the link is created where the operating system resolves the
spelling as typed. With `alias` a symlink to `/x/deep`, `alias/../harness/memory`
names `/x/harness/memory`, not the directory beside `alias`.

- PATH missing: `init` creates the symlink (making parent directories as
  needed).
- PATH already a symlink -- pointing at this project, elsewhere, or broken:
  `init` re-points it at this project's `memory/`. Re-pointing a symlink
  never destroys data, so restoring it after the adopter project is renamed
  or re-cloned is exactly re-running `init --harness-memory PATH` from the
  new location: the link moves, the memory files underneath are untouched.
  Already pointing at the right place is a no-op (`kept`).
- PATH already exists as a real directory holding the harness's own agent
  memory: `init` absorbs it -- see [Absorbing an existing harness memory
  directory](#absorbing-an-existing-harness-memory-directory).
- PATH already exists as anything else real (a file, or a directory that is
  not agent memory): fail-open. `init` reports a WARNING naming what it found
  and why the directory did not qualify, and leaves PATH exactly as it was --
  exit 0, nothing deleted, nothing moved.

The link is journalled like any other mutation, in the vault: a `link`
record pair in `.validated-memory/local.jsonl`, carrying the transaction
that published it and the previous target as its note (`no previous link`
when there was none), and no mode -- a symlink has none worth recording. It
is the one mutation the journal does not have the last word on. When the
journal refuses the run, the link is restored anyway, unrecorded, if and
only if the guarded repair of
[ADR 0029](../adr/0029-the-harness-link-survives-a-refusal-that-does-not-name-it.md)
allows it, and a WARNING carries the reason and the previous target, because
giving a session its memory back is the `SessionStart` hook's only job. The
repair takes the run-wide lock, reads the history and the vault, and relinks
in the same critical section:

- A **topology refusal before any adopting effect** (two adoption lineages in
  one legacy history, a fork of the frontier) allows it when the history
  snapshot is usable, every outstanding history condition is a topology gate,
  no condition names PATH, and the vault holds no residue, every entry of its
  transaction and preimage directories is a regular file (a symlink or any
  other node is never opened and withholds), and no transaction is unreadable,
  names no path or names PATH. When the vault cannot be read the link is
  withheld. The WARNING says the journal refused this run.
- A **journal that cannot be read** allows it under the vault rules alone: the
  history cannot be read, so only the vault is. When the lock cannot be taken
  for a reason other than another process holding it, the vault is still read,
  without the lock, and the same rules apply. Only when the vault itself cannot
  be listed is the link restored without them, with the same WARNING.
- **Every other refusal** -- damaged, unsupported, identity or bootstrap
  history, uncertainty after a current effect -- and a **lock held by another
  process** withhold it. `init` leaves PATH exactly as it was and adds a
  WARNING naming PATH and the reason, ending in `run journal --check`
  (`the harness link was not restored: ...; run journal --check`); the first
  run the journal allows restores it. A lock another process takes between
  the refusal and the repair is waited for only for what remains of the run's
  `--lock-wait`, and then withholds it.
- A **lock path that is not a regular file** blocks the repair without a wait:
  no process holds it. The WARNING names the lock path and says to remove it by
  hand, and it does not end in `run journal --check`.

Recorded paths are compared with PATH as directory entries, not as text, and
nothing is collapsed lexically: two paths name the same entry when the real
paths of their parent directories are equal and their final names are equal
without regard to case, or when both entries exist and are the same file,
which is how a filesystem that folds case names one entry twice. The exit code is 1 in every case, because the refusal is
still an ERROR. A link that already resolves to `memory/` needs nothing and
gets no WARNING, whichever refusal ended the run.

An unignored vault goes through the same repair, with the vault rules of an
unreadable journal.

What stands at PATH is read once, with one `lstat`, before `init` acts on it:
its file type and, for a symlink, its target. A path that cannot be looked at,
such as one under a directory that cannot be searched, is left as it is with a
WARNING that says `the harness path could not be read`, and the run's exit code
does not change. Whenever the repair relinks -- after its vault check or, when
the vault cannot be listed, without one -- it reads that identity again
immediately before it replaces PATH. A path that by then resolves to `memory/`
is left alone and gets no WARNING, whether or not what stands there changed.
Any other change, or a path that can no
longer be looked at, leaves PATH as it stands with a WARNING that says
`the harness path changed while the repair waited` or the unreadable reason; a
WARNING about PATH itself does not end in `run journal --check`. The second
reading is not an atomic guarantee: a process outside the plugin that replaces
PATH after that `lstat` and before the rename, including the parent-directory
check and the temporary link the relink makes first, is not guarded against,
and the relink never replaces a directory. The standard library has no
compare-and-swap on a pathname.

The unrecorded restoration after such a refusal, or when the vault's ignore
entry could not be established, first requires the same eligible in-adopter
project-memory target. It never absorbs or parks a real directory at PATH.
Only a missing path or an existing symlink can be restored. A real directory
is left byte-for-byte in place with the warning that absorbing it would move
the adopter's data; healthy initialization can recognize and absorb it on a
later run.

Computing PATH from the harness's own layout and calling `init
--harness-memory PATH` automatically on every session start is the plugin's
startup hook (`hooks/restore-memory-symlink.sh`, wired as `SessionStart` in
`hooks/hooks.json` -- see [Startup hooks](hooks.md)), and is **not** part of
`init` itself. `init` only guarantees the hook can call it repeatedly, from
any project state, without ever losing data.

#### Absorbing an existing harness memory directory

A project that adopts this plugin after the harness has already been writing
agent memory of its own finds PATH occupied by a real directory full of
memory files. Leaving it alone would leave two live memories that cannot see
each other: the harness reads its own directory, the plugin reads the
project's `memory/`, and neither shows the other's facts. So `init` absorbs
it, in this order -- nothing is parked until the copy is done:

1. **Recognize.** PATH qualifies only if every file under it is a `.md` file
   and every one of them except a top-level `MEMORY.md` carries the
   agent-memory frontmatter `lint` requires (see [Agent
   memory](agent-memory.md)). A
   top-level `MEMORY.md` alone is recognition enough. Anything else -- a
   stray non-Markdown file, a `.md` without that frontmatter -- disqualifies
   the whole directory, which is then left untouched with a WARNING naming
   the file that disqualified it. Hidden files count: a stray `.gitkeep` or
   `.DS_Store` blocks the merge until someone removes it. The bias is
   deliberate: a false negative costs a warning, a false positive moves a
   directory that belongs to something else.
2. **Copy in.** Every memory file is copied into the project's `memory/`,
   preserving subdirectories, **only where the destination does not exist**.
   A destination that already holds identical content is skipped silently, so
   re-running is quiet. A destination that differs is a real conflict: the
   project's copy is kept, and after parking a WARNING names the exact backup
   file holding the harness's version for a human to reconcile.
3. **Reconcile the index.** Every adopted file gets an entry in the project's
   `memory/MEMORY.md`: the line the harness's own index carried for it when
   there was one, synthesized from the file's `name` and `description`
   otherwise. Reconciling only ever appends -- entries already in the
   project's index are never rewritten or removed -- except for the `No
   entries yet.` placeholder `init` writes into a fresh index, which goes as
   soon as the index has real entries. The result passes `lint` clean, which
   is how the absorption is verified.
4. **Park.** The original directory is renamed alongside itself, to
   `<PATH>.bak` (or `.bak.1`, `.bak.2`, the first free slot -- an existing
   backup is never overwritten). Nothing is deleted: after the run there are
   two copies of every adopted file, one live inside the project and one in
   the backup.
5. **Link.** Only now is the symlink created, so the harness and the plugin
   read the same files from that point on.

The one exception to "nothing is deleted": PATH as an **empty** directory is
removed with `rmdir` and replaced by the symlink, with no backup. `rmdir` is
refused by the operating system on anything that is not empty, so it cannot
lose data, and the alternative -- an empty `.bak` on the side, or a WARNING
on every session start forever -- is worse. If ordinary link publication then
fails, the WARNING does not claim the session was unaffected: it says that the
empty directory was removed, no memory data was parked and PATH is absent, and
directs the operator to clear the publication error and rerun `init`.

An absorption failure is fail-open: it is a WARNING, exit 0, and the link is
not attempted. The order preserves every source file: a failed copy leaves the
original in place and unparked, while a later copy, reconciliation or park
failure may also leave project copies or index changes. The WARNING names the
original source and says when those earlier effects were not rolled back.

After a successful park, an ordinary pre-visibility link-publication failure is
also a WARNING and exit 0. It does not roll the merge back, and names the exact
parked backup directory so the recovery location remains visible even when the
SessionStart hook suppresses `init`'s stdout. The J2 durability boundary is
different: if the link may already be visible but its durability cannot be
confirmed, `init` reports a gating ERROR because it cannot truthfully reduce
that state to a clean retry. That ERROR likewise does not roll back the copies,
reconciled index or parked source.

### `lint`

```
python3 -P -m validated_memory lint [PATH]
```

Lints the agent-memory layer: every `*.md` file found under PATH, recursively,
except the index `MEMORY.md` itself. With no PATH it reads `memory/` relative
to the working directory. A one-line summary goes to stdout; findings go to
stderr in the same shape `validate` uses:

```
SEVERITY: <location>: <field>: <message>
SEVERITY: <location>:<line>: <field>: <message>    # parse errors only
```

`lint` resolves wikilinks and the supersession convention against the whole
memory set, so a missing `MEMORY.md`, a missing memory directory, or an
explicit PATH that does not exist each stop the run before any memory file is
read. `MEMORY.md` must resolve to a regular file; a directory, broken link or
other non-file node is one index ERROR and is left untouched. An OS failure
while opening or reading an otherwise file-shaped index is likewise one index
ERROR rather than a traceback. `status` uses this same acquisition path and
reports the same finding in its lint section.

### `validate`

```
python3 -P -m validated_memory validate [PATH]
```

Validates every `*.md` unit found under PATH, recursively; PATH may also be a
single unit file. With no PATH it reads `knowledge/` relative to the working
directory. A one-line summary goes to stdout; findings go to stderr as

```
SEVERITY: <unit>: <field>: <message>
SEVERITY: <unit>:<line>: <field>: <message>    # parse errors and the rationale quoting rule
```

Most contract rules speak about the unit as a whole and report no line. Two
report one: the parser, which knows where it stopped, and the `rationale`
quoting rule, which reads the raw frontmatter text (see the curated-knowledge
reference).

Supersession resolves against the validated set: validate the whole knowledge
directory, not a single file, or a `supersedes` entry pointing at a unit you
left out is reported as missing.

### `derive`

```
python3 -P -m validated_memory derive [PATH] [--check]
```

Re-derives the curated-knowledge index from the units under PATH, resolved
exactly like `validate`'s PATH (default `knowledge/`, single unit file or a
directory, same errors on a missing path). Deriving requires a valid source:
`derive` first runs the same validation as `validate` (base contract plus the
adopter's declared extension). An ERROR finding reports the findings to
stderr, in `validate`'s format, and stops -- nothing is written or checked.
A WARNING does not block.

The index is written to `knowledge-index.md` in the current working
directory, never inside `knowledge/`: anything ending in `.md` there is read
as a unit (see [Where the schema
lives](curated-knowledge.md#where-the-schema-lives) -- the same reason
applies to the index).

```markdown
# Knowledge index

Derived: 2026-08-12T10:00:00Z
Basis: 2 unit(s) under knowledge/

| id | state | evidence | verdict |
|----|-------|----------|---------|
| kb-0001 | superseded by kb-0002 | measured | unknown |
| kb-0002 | active | hypothesis | unknown |
```

- `Derived:` is the UTC ISO-8601 timestamp of the derivation run.
- `Basis:` is the recount basis: how many units, under which path.
- Rows are sorted by `id`. Nothing is omitted: a superseded unit is still
  listed, marked, never mutated.
- **state** is computed, never stored on the unit: `active`, or
  `superseded by <ids>` naming every unit that lists this one in its own
  `supersedes` (many-to-one), sorted and comma-separated.
- **verdict** reads the service view of `verdicts.jsonl` (the log `probe`
  writes -- see the `probe` section below): for each of the unit's anchors,
  the latest verdict recorded for that anchor, or `unknown` when it was never
  probed -- fail-explicit. A unit is graded by the worst of its
  anchors' verdicts (`drifted` > `unknown` > `current`):
  - no anchors: `unknown`, on its own.
  - the worst verdict is `unknown`: `unknown (<systems>)`, naming every
    system behind an `unknown` anchor, sorted and comma-separated -- this
    also covers a unit with anchors that was never probed at all.
  - the worst verdict is `drifted` and some anchors are also `unknown`:
    `drifted (unknown: <systems>)`.
  - otherwise: the verdict alone (`current` or `drifted`).

`--check` recalculates the index in memory instead of writing it, and
compares it against the `knowledge-index.md` already on disk, line by line.
The `Derived:` line must be there, but **its timestamp is ignored** -- it
changes on every run, so what has to match is the rest: `Basis:` and the
table. A missing index is an ERROR pointing at running `derive` first. Any
divergence -- `Basis:`, a row, a missing or extra line -- is an ERROR naming
the first line that does not match, numbered as on disk. `--check` never
writes. A match exits clean with a summary. This makes `derive --check` a
local or CI gate for adopters who version the derived index: hand-editing it,
or letting it drift from the units, fails the check. **The verdict column is
part of that content**: running `probe` between a `derive` and a
`derive --check` changes what the recalculated index says, so the check
correctly fails against the now-stale on-disk index -- run `derive` again to
pick up the new verdicts.

Exit codes: `0` clean, or WARNING-only validation findings; `1` an ERROR
finding (source validation, or a `--check` mismatch); `2` a usage error.

### `probe`

```
python3 -P -m validated_memory probe [PATH] [--timeout SECONDS]
```

Runs freshness probes over the anchors of every *active* curated-knowledge
unit found under PATH, resolved exactly like `validate`'s PATH (default
`knowledge/`), and records what each probe answered. "Active" excludes a unit
that appears in another unit's `supersedes` within the validated set -- a
superseded unit is not current, so its anchors are never probed. Probing
requires a valid source: `probe` first runs the same validation as `validate`
and `derive` (base contract plus the adopter's declared extension); an ERROR
finding stops the run before anything is probed. A WARNING does not block.

**Probe contract.** A probe is registered per anchor `kind` in the `probes`
map of `validated-memory.md` (see [Adopter
configuration](curated-knowledge.md#adopter-configuration)). The
registered command is split with `shlex.split` and run **without a shell**.

- It receives the anchor's envelope on **stdin**, as JSON:
  ```json
  {"system": "repo-a", "kind": "git_ref", "captured_at": "2026-08-11T10:00:00Z", "payload": {}}
  ```
  The unit's id is deliberately not included -- the envelope is the
  producer/store boundary, and a probe only needs to know what it is
  checking, not which unit cites it.
- It answers on **stdout**, as JSON, and exits `0`:
  ```json
  {"verdict": "current", "detail": "optional free-form note"}
  ```
  `verdict` is one of `current | drifted | unknown`; `detail` is optional.

Any failure falls back to `unknown`, with a note explaining why, and never
aborts the run: no probe registered for the anchor's `kind` (or no
`validated-memory.md` at all), a command that cannot be run (parse failure,
executable not found), a command still running at its deadline, a non-zero
exit, aggregate stdout and stderr beyond the fixed output limit, stdout that
cannot be decoded or does not parse as JSON, or a verdict outside the
three-value domain. Each such fallback is reported to stderr as a WARNING
finding, in the usual shape:

```
WARNING: <unit>: anchors[<i>]: <message>
```

**The deadline.** `--timeout SECONDS` bounds how long each registered
command may run, separately for every anchor. It defaults to `60` seconds,
which leaves room above the bundled `git_ref` probe's own 30-second limit;
pass a larger value to allow slower probes. It accepts a finite number above
`0` and at most `3600`; any other value is a usage error (exit `2`) reported
before validation, probing or any write. A command that reaches its deadline
is killed and its anchor records `unknown` with a `null` detail, with a
WARNING naming the timeout, and the run continues with the next anchor. There
is no retry. On POSIX the command runs in its own session, and on expiry its
whole process group is killed and the command itself reaped; a descendant
that moved to another process group escapes that cleanup. On Windows only the
command itself is killed and reaped: its descendants are not contained. Probe
output collection is supported on POSIX and Windows; another runtime platform
fails that invocation explicitly as `unknown` with a platform diagnostic and
continues later anchors. After a command exits normally, nothing it left
running is killed. The same cleanup runs when the run is interrupted, before
the interruption propagates.

Cleanup never waits indefinitely. If the kill fails for any reason other
than the command having already exited, the WARNING says so, and on POSIX the
command itself is then killed directly -- its descendants may survive.
Reaping waits at most 5 seconds after the kill; a command still not reaped is
named in the WARNING and left behind. For example:

```
WARNING: kb-0001: anchors[0]: probe command '<cmd>' timed out after 60 second(s); it was not reaped within 5 second(s)
```

The deadline covers the command, not the run: validation, starting the
process, filesystem or kernel stalls, and cleanup are outside it, so a run
with many anchors may take far longer than `--timeout`.

**The output limit.** Each command may emit at most **1,048,576 raw bytes in
aggregate across stdout and stderr**. The two pipes are drained concurrently
and share one budget; every byte counts before decoding, newline normalization
or JSON parsing, regardless of stream interleaving. Exactly 1,048,576 bytes are
permitted. Observing the next byte is overflow: the invocation is killed and
reaped with the same bounded, platform-specific cleanup used for a timeout,
the anchor records `unknown` with a `null` detail, and later anchors continue.
There is no retry and no option, configuration field or environment variable
that changes this limit.

Captured output is discarded semantically after overflow. In particular, a
complete valid JSON prefix is never parsed, and stderr is never quoted, even
when the command also exits non-zero. The primary WARNING is fixed; kill or
reap problems are appended with `; ` in the same way as a timeout:

```
WARNING: kb-0001: anchors[0]: probe output exceeded the 1,048,576-byte aggregate stdout/stderr limit
```

stdin is a temporary file managed by Python's `tempfile` module and closed after
the invocation. Whether it has a visible name while open is platform-dependent;
it is not persistent product state. stdout and stderr are live pipes so their
shared byte budget can be enforced without first spooling unbounded output. If
a command exits successfully while a surviving descendant holds a pipe open,
collection returns after draining the direct command's complete available
output; it does not wait for descendant EOF and does not kill the descendant.

The deadline and output ceiling are resource containment, not a subprocess
sandbox. They do not bound stdin, arguments, process memory, CPU, files,
network, validation, process startup, kernel stalls, the number of anchors or
total run time. On POSIX a timeout or overflow kills the process group, but a
descendant that moved to another group can escape. On Windows cleanup kills
only the direct command, so descendants are not contained. Closing a pipe does
not imply that a surviving writer was killed.

**The verdict log.** Every anchor probed -- successful or fallen back --
appends one JSON line to `verdicts.jsonl` in the current working directory,
never inside `knowledge/`, for the same reason `knowledge-index.md` lives
outside it (see [Where the schema
lives](curated-knowledge.md#where-the-schema-lives)). The log is **append-only**: a run never rewrites or removes a prior
line, so the full probing history accumulates. Each line:

```json
{"recorded_at": "2026-08-12T10:00:00Z", "unit": "kb-0001", "system": "repo-a", "kind": "git_ref", "payload": {"ref": "refs/heads/main"}, "verdict": "current", "detail": null}
```

**An anchor is identified by what it points at**: its `system`, its `kind`
and its `payload`. `captured_at` dates a capture; it does not identify one.
That is why the record carries the payload, and it is also what makes the log
a record of what was actually measured rather than of the fact that something
was.

The distinction is not academic. A unit may legitimately carry two anchors
sharing a `(system, kind)` -- two refs of the same repository are both
`git_ref` on the same system. Keyed on that pair alone they collapsed into
one entry, so the later verdict overwrote the earlier: `probe` would report
`1 current, 1 drifted` and the index would then say `current` about a unit
whose anchor had drifted, with the winner decided by the order the anchors
happened to be written in. A false "still true" is the one answer this tool
must never give.

A record written before payloads were recorded carries none, and **is never
attributed to an anchor**. It is not only that the log cannot say which anchor
it was about; it cannot say what that anchor pointed at when it was written,
and an anchor can be re-captured. A single-anchor unit is no exception: its
`payload` may have changed since, so reading the old record would again be
reporting `current` for something that has drifted. The record stays in the
log, because history is not rewritten, and it is ignored: the anchor reads
`unknown` until it is probed again, which repairs itself on the next `probe`.

The payload in a record is compared against the anchor's exactly as the
frontmatter parser produced it, and that parser infers no types -- every
scalar is a string (see [Frontmatter subset](curated-knowledge.md#frontmatter-subset)). A record hand-edited to carry
`true` or `3` where the anchor says `"true"` or `"3"` names a different
payload, so it is a different anchor. That is deliberate: coercing them would
be inferring a type, which nothing else here does.

The **service view** a reader wants -- and the one `derive` reads for its
verdict column -- is the latest record per anchor; re-probing
adds new lines, it never edits history.

A summary goes to stdout:

```
probe: 3 anchor(s) probed across 1 unit(s): 1 current, 1 drifted, 1 unknown
```

Exit codes: `0` clean, or WARNING-only findings -- **a `drifted` or
`unknown` verdict is data, not a finding, and never gates `probe`**; `1` an
ERROR (source validation, or the verdict log could not be written); `2` a
usage error, including an invalid `--timeout`.

#### The bundled `git_ref` probe

Ships with the plugin at `validated_memory/probes/git_ref.py`, invocable as
`python3 -m validated_memory.probes.git_ref` -- the command `init` already
registers for `git_ref` in the scaffolded `validated-memory.md` (see [Adopter
configuration](curated-knowledge.md#adopter-configuration)). It implements the probe contract above for
one `kind`: freshness of a git repository ref.

Its payload, interpreted by the probe -- the envelope itself does not know
its shape:

```yaml
payload:
  repo: .                       # local path or URL `git` understands
  ref: refs/heads/main          # full ref name
  commit: <sha at capture time> # what `ref` resolved to when the anchor
                                 # was captured
```

The live commit is resolved with `git ls-remote <repo> <ref>`, run as a
subprocess without a shell -- uniform for local paths and URLs, and `git` is
a system binary, not a pip dependency, so this keeps the stdlib-only rule.
`git` must be installed and on `PATH`.

The comparison is textual, against the full sha `git ls-remote` returns, so
the capture side must record exactly that: `commit` is the **full 40-hex
sha** the ref resolves to (`git rev-parse <ref>`). Two captures that read
naturally but never match: an abbreviated sha, and -- for an annotated tag --
the peeled commit (`v1^{commit}`), since the ref resolves to the tag
*object*. Both read as a permanent, misleading `drifted`; capture what the
ref resolves to, not what it points at.

- the live commit equals `commit` -- `current`.
- it differs -- `drifted`, with a detail naming the ref and both shas.
- the verdict cannot be determined -- `unknown`, with a detail explaining
  why: `repo`, `ref` or `commit` missing from the payload; a repo that
  cannot be reached; a ref that does not exist (`git ls-remote` exits clean
  with no output); or `git` not installed or not on `PATH`.

Like every probe, it never gates the run over its own verdict, and it holds
itself to the probe contract directly rather than leaning on the
framework's fallback: every failure it can anticipate is caught and turned
into `unknown` with a reason here, so it never raises, never prints a raw
traceback, and never exits non-zero.

### `recall`

```
python3 -P -m validated_memory recall QUERY [--layer {all,memory,knowledge}]
    [--limit N] [--max-bytes N] [--format {text,json}] [--include-superseded]
python3 -P -m validated_memory recall --map [same flags]
```

Read-only, bounded search over `memory/` and/or `knowledge/`: discovery, not
validation -- a match says a record exists and roughly why, never that its
claim still applies. Full contract, exact field-by-field reference, exit
codes and known limitations in [Recall](recall.md).

### `consultation`

```text
python3 -P -m validated_memory consultation --store PATH OPERATION
```

Opt-in checked use across local adopter projects: register roots, bind existing
knowledge units to support and dependencies, acquire a complete receipt, record
use of an exact consumer conclusion, and check its current eligibility read-only.
Canonical authoring remains separate; existing commands and hooks gain no
implicit publication gate. Historical use is not a present eligibility result.

The [consultation reference](consultation.md) contains the complete operation
surface, two-adopter workflow, two-line `read` protocol, maintenance, limits and
exit codes. The [storage schema](consultation-storage.md) defines retained events.
`read` emits inspection content before committing its receipt and returning its
`id` on a second JSON line; consume both lines and check exit 0 before using it.

Available since 2.3.0, the [incorporation lifecycle](incorporation.md) adds
`upgrade`, `submit`, `challenge`, `inspect`, `decide`, `renew`, `incorporate`,
`resolve`, `reconcile`, `address`, `track-publication` and `reflect` within
`consultation`. Acceptance, canonical incorporation and downstream observations
are separate retained outcomes. Schema 1 stores require explicit upgrade for
these additions; version 2.2.0 does not provide them. Schema 2 was an unreleased
incorporation format; 2.3.0 creates and upgrades to schema 3. Payload and
compatibility rules are in [incorporation storage](incorporation-storage.md).

Available since 2.4.0, `resume-use USE --scope KEY=VALUE [--scope KEY=VALUE ...]
[--max-bytes N]` adds a read-only task resumption report. Requested scope is required
and must exactly equal historical receipt scope; different scope requires a new
complete consumer `read` and `record-use`. The one-line bounded JSON report exits
0 for `current`, or 1 for `blocked` even when the report is complete; invalid
arguments exit 2. The byte limit defaults to 65,536 (range 2,048–1,048,576), with no
stdout on overflow. No event, receipt or inspection handle is created. It reports
known origin review without checking external freshness or importing update files.
Use the [trusted local update workflow](everyday-workflow.md#resume-a-task) and
[report fields and limitations](consultation.md#task-resumption-report); unavailable
or refused update inputs remain outstanding even after a `current` report.

### `render`

```
python3 -P -m validated_memory render [--only-existing]
```

Writes two canonical self-contained HTML pages to the working directory -- alongside
`knowledge-index.md` and `verdicts.jsonl`, never inside `knowledge/`:
`knowledge.html`, the curated layer, and `memory.html`, the agent-memory
layer. Two files rather than one because the two layers share neither
frontmatter, nor relations, nor the way each stops being true -- no single
name covers both without being false about one of them. If knowledge-app.html
already exists, render also refreshes that optional enhanced knowledge page;
it never activates an absent app. Create it with `init --view --app`.

`render` validates before rendering, exactly like `derive`: an ERROR
finding is reported in `validate`'s format and stops the run with nothing
written; a WARNING does not block. Each artifact is built entirely in
memory and written in one operation, so a validation failure or a crash
mid-build can never leave a half-written page on disk for a reader to open.
A summary goes to stdout, one line per artifact, in `init`'s idiom:

```
render: wrote knowledge.html
render: unchanged memory.html
```

If an artifact cannot be published, `render` emits one `write` finding for
that artifact and no success line for it. The primary `file could not be
written` error is reported first. Render exclusively creates a regular,
same-directory temporary and records its identity. Immediately before
publication or one non-recursive cleanup attempt, it checks that the temporary
name still identifies that file. If owned cleanup fails, the same finding
names the temporary and reports that it could not be removed. A pre-existing
or already-observed replacement is left alone, and the finding says that it
was not removed because it was not owned by this run. The later pathname
replace and unlink operations are not conditional on that check, so a
concurrent swap can still race and concurrent rendering retains the existing
last-writer-wins limitation. Explicit rendering reports the finding as an
ERROR and exits 1; `--only-existing` reports the same message as a WARNING and
remains exit 0. Other selected artifacts continue in the stable order above.

**A file whose content is unchanged is not rewritten**, and the output
carries no generation timestamp, so an unchanged corpus produces
byte-identical output run after run. Without this, the refresh hook (see [Startup
hooks](hooks.md)) would dirty `git status` on every session start, forever, in a repository
that treats that churn as a defect.

**Both canonical pages are inert.** No JavaScript and no request to the network:
collapsing a section uses the browser's native `<details>`/`<summary>`, not
a script. The only attribute anywhere in either page that carries an
external URL is `href` on an `<a>` element, and only a `provenance` entry
with an `http://` or `https://` scheme becomes a link -- anything else (a
`javascript:` URI, a bare string, a mapping) is shown as escaped text
instead. A unit or memory body is shown verbatim, escaped and unrendered,
in a monospaced block; the one line extracted from it is the headline,
taken from the body's first heading and falling back to the unit's `id`
when there is none.

**`knowledge.html`** opens with an overview and then lists live conclusions
ordered by `id` -- the one ordering that does not move on its own, unlike
freshness or recency, which a routine `probe` would reshuffle on the next
session. The overview counts active units by evidence state crossed with
verdict, reports superseded units as one separate figure rather than folding
them into the same table, maps the corpus by `anchors[].system` (a unit
anchored in several systems is a link in each group; units with no anchors
land in an explicit `unclassified (no anchors)` group, listed last), and
queues the anchors of active units with no verdict recorded under their
current key `(unit, system, kind, payload)` -- never probed, or probed before
the payload changed. The map is an index of links, never a second copy of the
cards, which is what lets one unit appear in several groups while its card is
rendered exactly once.

Each card carries, in this fixed order: the headline and `id`, badges for
evidence state and verdict, every anchor's envelope and probe history, the
unit's `rationale` where it has one, the supersession chain that led to it
nested as deep as the chain runs, `provenance`, and the body. A superseded
unit never appears at the top level, only inside the chain of whatever
replaced it. The page states how many records the verdict log holds in total
and how many belong to an anchor shown on the page -- two totals, not one,
because the log outlives the corpus (nothing prunes a record whose unit or
anchor is gone), so a single total could never be reconciled by a reader
against the histories in front of them. Probe history itself shows at most 20
records per anchor, most recent first, and each anchor's own history repeats
the disclosure for itself -- `N record(s) for this anchor; showing M` --
which is what actually lets a reader tell a full history from a truncated
one.

Three diagrams, all inline SVG, all generated from the data with no
third-party code, and each carrying a title, an accessible name and its own
uniquely identified description linked by `aria-describedby`: a freshness
strip per anchor (one band per probe, in log order, told apart by shape, mark
and colour, and never a time axis), a many-to-one confluence drawn only when
three or more units are superseded at once, and a rationale tree drawn only
for a unit that carries one. Nothing a diagram shows lives only in the
diagram: past 48 characters a question is drawn as `?` and a label as `#n`,
and once a rationale has more than eight options every label is drawn as `#n`
regardless of its length; either way, the full text is read from the card
beside the drawing.

**`memory.html`** lists entries by filename, each with its outgoing and
incoming references -- the wikilink graph, walkable entry by entry rather
than drawn (a real corpus runs to hundreds of links, which draws as a
hairball nobody reads anything out of). A superseded entry is marked as
such and links to its successor. A wikilink inside a body is never turned
into a link: the body is verbatim, and linkifying it would be rendering it.

**`knowledge-app.html`** is the canonical knowledge page plus one repository-owned
inline script. Removing that script yields knowledge.html byte-for-byte. The
script enhances existing content with combined text/state/evidence/verdict
filters, reset and matching counts, and keyboard-operable bounded diagram
pan/zoom. Overview totals describe the full corpus. Matching historical units
retain their enclosing history context; fragment navigation reveals its target
and clears filters when necessary. Printing shows the full content, then restores
the interactive state. With JavaScript disabled, all canonical content remains.

No third-party runtime, browser storage or network API is used. All pages carry
the same fixed CSP:
`default-src 'none'; connect-src 'none'; img-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'`.
Canonical pages independently remain script-free; only the app allows exactly
one attribute-less script. Source scans are structural checks, not a claim of
universal browser or assistive-technology behavior.

**`--only-existing`** refreshes present artifacts, with one deliberate dependency:
an existing app also creates or refreshes canonical knowledge.html. It never
activates an absent app or absent memory.html. Empty app files are active;
deleting the app deactivates it. With no active artifact it returns without
reading the corpus. This is what the refresh hook invokes; see [Startup hooks](hooks.md).
It is also fail-open: an invalid corpus, an unreadable verdict log, or a
missing memory directory or index is a WARNING and exit 0, leaving whatever
is already on disk exactly as it was, rather than an ERROR that a hook
would otherwise report unattended on every session start until someone
fixes the corpus. Run explicitly, without the flag, the same corpus is an
ERROR that gates -- a person asking for the views by hand is entitled to be
told they were not built.

Exit codes: `0` clean, or WARNING-only findings; `1` an ERROR finding; `2`
a usage error.

### `status`

```
python3 -P -m validated_memory status [--skip-index] [--fail-on {drifted,unknown}]
                                    [--max-verdict-age N] [--fail-on-aged]
                                    [--as-of TIMESTAMP]
```

Read-only. Answers precisely: is this project *structurally consistent*, and
what does its freshness look like -- not "is everything current", which the
verdict-is-data contract deliberately does not gate on (see
[ADR 0002](../adr/0002-status-gates-consistency-and-only-reports-freshness.md)).
**Never runs `probe`**: `status` reports what the log already says, and a
command that mutates the thing it reports on is not a status command.

It computes one internal pass over the curated layer, the agent-memory
layer, the derived index and the verdict log -- the same rules `validate`,
`lint` and `derive --check` already enforce, reused rather than re-run, and
the log read once -- and reports five sections, then one conditional line
about the journal:

- **`validate:`** the curated layer against the base contract plus the
  adopter's declared extension, exactly like `validate`.
- **`lint:`** the agent-memory layer, exactly like `lint`. Independent of
  the curated layer, so it runs and reports even when validation gates.
- **`index:`** `knowledge-index.md` against the recalculated index, exactly
  like `derive --check` -- a missing index is an ERROR pointing at running
  `derive` first. Skipped entirely, no finding at all, with **`--skip-index`**
  -- for an adopter who does not version the derived index. This section (and
  freshness, and age) only runs when the curated layer validates clean: they
  all need a valid source, the same precondition `derive` and `probe` share.
- **`freshness:`** verdict counts (`current`/`drifted`/`unknown`) across
  **active units only** -- a superseded unit's verdicts describe knowledge
  already retired. Reported, never gated, unless the adopter opts in with
  **`--fail-on drifted`** and/or **`--fail-on unknown`** (repeatable): an
  active unit carrying that verdict then becomes a gating ERROR naming the
  unit.
- **`coverage:`** five lines after freshness and any age summary, before the overall
  line: total, active and superseded units, then, for each evidence state
  (`measured`, `verifiable`, `hypothesis`, in that order), its active units
  split into `anchorless` (missing or empty `anchors`), `never-recorded`,
  `partially-recorded` and `fully-recorded`. A unit is fully recorded when
  every current anchor key `(unit, system, kind, payload)` has a record in the
  log, partially when only some do. Superseded units appear only in the
  totals. Recorded means a matching verdict record exists, whatever its verdict:
  this does not mean the anchor is current. A payload change creates a different
  key, so an old record does not count unless the log also contains a record
  matching the new key. Counts only, never gated, no flag changes them. Omitted
  entirely when source validation reports an error or the verdict log cannot
  be read; a missing log is an empty one.

When the read-only inspection that `journal --check` performs finds history
conditions -- the ones that stop `init`, such as a topology gate or damaged
history -- `status` adds one line just before the overall line, and a WARNING
against `journal.jsonl` that says the same:

```
status: journal: 1 history condition(s) stop init; run journal --check
```

An inspection that cannot be made reads `status: journal: unreadable; run
journal --check`, and so does a vault whose transaction or preimage directory
holds an entry that is not a regular file: entries are classified without
following links, and nothing in the vault is opened, because opening a symlink
can block on a pipe. `journal --check` answers for such an entry without
opening it: a transaction entry is a damaged transaction, and an entry of the
preimage directory is retained private residue. A history with no conditions,
or no journal at all, adds nothing. The inspection creates no file and needs no
vault, so a clone without `.validated-memory/` sees only the conditions of the
repository history. The exit code does not change: this is a WARNING, never a gate
([ADR 0029](../adr/0029-the-harness-link-survives-a-refusal-that-does-not-name-it.md),
[ADR 0002](../adr/0002-status-gates-consistency-and-only-reports-freshness.md)).

**Verdict age** (see
[ADR 0004](../adr/0004-verdict-age-belongs-to-status-never-to-the-derived-index.md)):
**`--max-verdict-age N`** (integer days) emits a WARNING per active unit's
anchor whose latest recorded verdict is more than `N` days old (UTC, strict
`age > N`), naming the unit, the anchor (`system/kind`) and the age. An
anchor whose latest record has no `recorded_at`, one that does not parse, or
one in the future reads as `age unknown`, also a WARNING under the flag --
without it, `recorded_at` is never read at all. An anchor never probed at
all is not reported here a second time: the freshness section above already
grades it `unknown`. **`--fail-on-aged`** upgrades every finding this check
emits -- aged and age-unknown alike -- to a gating ERROR: an enforced age
bound cannot be satisfied by an age that cannot be verified. **`--as-of
TIMESTAMP`** (ISO 8601, a trailing `Z` accepted) substitutes "now" for age
computation, for reproducible audits and tests; an invalid value is a usage
error (exit 2). "Latest verdict per anchor" is still the log's append
order, never re-sorted by `recorded_at` (see
[ADR 0004](../adr/0004-verdict-age-belongs-to-status-never-to-the-derived-index.md)).

```
status: validate: 2 unit(s) checked, 0 error(s), 0 warning(s)
status: lint: 3 memory file(s) checked, 0 error(s), 0 warning(s)
status: index: up to date
status: freshness: 2 active unit(s): 1 current, 1 drifted, 0 unknown
status: age: 1 aged, 0 age-unknown (max 30 day(s))
status: coverage: 2 unit(s): 2 active, 0 superseded
status: coverage: measured: 1 active unit(s): 0 anchorless, 0 never-recorded, 0 partially-recorded, 1 fully-recorded
status: coverage: verifiable: 1 active unit(s): 0 anchorless, 0 never-recorded, 1 partially-recorded, 0 fully-recorded
status: coverage: hypothesis: 0 active unit(s): 0 anchorless, 0 never-recorded, 0 partially-recorded, 0 fully-recorded
status: coverage: recorded means a matching verdict exists, not that it is current
status: 0 error(s), 0 warning(s) overall
```

A history condition adds its `status: journal:` line immediately before the
overall line, and the overall line counts its WARNING.

Exit codes: `0` clean, or WARNING-only findings (including a reported but
not gated `drifted`/`unknown`/aged verdict, and a journal history condition);
`1` an ERROR from any gate that ran (validation, lint, index, or an opted-in freshness/age upgrade); `2` a
usage error.

### `journal`

```
python3 -P -m validated_memory journal [--check]
python3 -P -m validated_memory journal --resolve ID (--accept | --restore | --abandon)
python3 -P -m validated_memory journal --repair TRANSACTION_ID
```

Reports the append-only record of what adoption did to this project --
`journal.jsonl` at the adopter root plus `.validated-memory/local.jsonl`,
read together (see [Journal](journal.md) for the record format and both
files' durability). The scaffold `init` writes is in it. Two things are not
yet: the derived artifacts `derive`, `probe`, `render` and `init --view`
write, and the harness take-over `init --harness-memory` performs when the
path it names is a real directory.
[Journal](journal.md#what-is-recorded-and-what-is-not-yet) names both and
the plan that records them. Read-only in both reporting modes: neither runs
`probe` and neither writes to either file. `--resolve` and `--repair` are
the two targeted modes that write.

Without `--check`, it reports the combined record count and never gates on
what it finds -- a reader can inspect a project's history without gating a
session on it:

```
journal: 13 record(s)
```

A transaction an earlier run left open is worth saying even here, so it is
counted on a second line, printed only when there is one:

```
journal: 1 record(s)
journal: 1 unresolved transaction(s)
```

**`--check`** additionally renders the protocol's authoritative deterministic
condition set: coherent-pair and topology conditions, permanent-history
reconciliation, every unresolved transaction in the write-ahead log
(`.validated-memory/transactions/`). It reports, it never repairs, and every
finding is an ERROR. On a quiescent tree, `init` consumes those same condition
identities: permanent-history and topology conditions stop the whole adopting
workflow, while a pre-existing WAL-1 gate remains local to its bound path:

```
ERROR: .gitignore: journal: open transaction 56eeba099c335aaa (published) on .gitignore: diverged
journal: 1 record(s), 1 error(s)
```

There are five shapes of finding:

```
ERROR: validated-memory.md: journal: unfinished transaction from run 6815e8b2323e4886: the path is applied
ERROR: knowledge: journal: records of transaction 7901cd24a8758b62 disagree on note
ERROR: .gitignore: journal: records of transaction 1ed016d9e88b5435: committed without a prepared half
ERROR: .gitignore: journal: transaction e85966eeb6de80ef is recorded 4 times
ERROR: .gitignore: journal: open transaction 56eeba099c335aaa (published) on .gitignore: diverged
ERROR: .validated-memory/transactions/deadbeefdeadbeef.json: journal: damaged transaction deadbeefdeadbeef: not valid JSON: Expecting value
```

In order: a `prepared` record with no matching `committed`
twin, reported with which of four states its bytes are in (`applied`,
`unapplied`, `diverged`, `unknown` -- see
[Journal](journal.md#stages-and-unfinished-transactions)); a closed pair
whose two halves disagree on a field the mutation itself decided; an id
that is not a pair at all, which has two messages -- a `committed` half
with no `prepared` half, and a transaction recorded more than twice; an
open transaction, with its file's own stage in brackets and the verdict
recovery would reach (`recoverable`, `diverged` or `unknown` -- see
[Journal](journal.md#recovery)); and a transaction file too damaged to name
a path, which is named by its own file instead -- a file that is not this
project's transaction at all, by its schema, its own id, its adoption, its
operation or its states (see [Journal](journal.md#recovery)).

**`--resolve ID`** closes one transaction the way the operator says, with
exactly one of `--accept` (keep the state the path is in, recorded as an
observation and never as a mutation), `--restore` (put the preimage back
from the vault) or `--abandon` (leave the path as found, and record that).
It applies only to a transaction recovery cannot account for -- one that is
`recoverable` or `damaged` is refused, with the reason -- and it recovers
nothing else on the way. Over a `diverged` transaction, whose bytes were
published before the crash, `--accept` and `--abandon` record the mutation's
own pair first and their `observe` after it: closing the divergence answers
for the path and does not take the write out of the history. See [Resolving
a transaction](journal.md#resolving-a-transaction) for what each flag
records and what `--restore` refuses.

An `unknown` selected WAL that exactly provisions its own prepared history
condition is refused for all three dispositions before target, history,
observation or cleanup effects. Neither a generic committed observation nor
another WAL's provision can discharge or lend that authority. Preserve the
selected WAL and histories, restore an accepted exact history state from a
trusted source, then run `journal --check`.

```
journal: resolved 51de77210788b0fd (--accept)
```

A `--restore` that discarded bytes says where they went, because no command
here destroys bytes without leaving a copy:

```
journal: resolved 56eeba099c335aaa (--restore); the discarded bytes are kept at .validated-memory/preimages/896206210afdc58bebda73324b1601db00749e6b365c8055352d4a85f45ffa1f
```

A `--repair TRANSACTION_ID` operation is the explicit proof-carrying path for
a torn final history append. It requires an existing history and a current
WAL claim. Before publication it constructs a candidate pair from the exact
repaired bytes plus the raw opposite artifact and inspects that pair. A
condition involving the selected transaction, transaction identity reuse, or
an adoption mismatch refuses without publishing. Unrelated pre-existing
conditions neither supply repair authority nor cancel the selected WAL's exact
byte proof.

The locked read freezes the selected WAL's exact bytes, mode, and identity.
Repair rechecks that evidence immediately before publication and again before
any temporary or WAL cleanup. It also validates any claimed staging name against
the WAL intention and postimage before publication: a symlink's
filesystem-byte target is compared directly, while a regular file must carry the postimage
digest. Parent, type, mode, identity, and exact content are frozen. A malformed,
unbound, or unsafe claim refuses without publication; a later replacement is
preserved and makes the visible repair retained.

After atomic publication, repair reacquires the coherent pair. The selected
condition must be discharged, no new condition may appear, and every remaining
condition must be a subset of the complete frozen pre-publication condition
domain, including unrelated retained-WAL gates. The candidate pair and all
frozen evidence are revalidated from a coherent read while the complete staged
bytes still await installation. Only then is selected-WAL cleanup allowed. A clean result prints the repaired line and
exits 0. If independent gates remain, it prints the repaired line, each gate,
and `journal: repair confirmed, N gate(s) remain`; it exits 1 and the operator
must address each named condition, run `journal --check`, and do not repeat the confirmed
repair. Publication or readback uncertainty prints no repaired line, preserves
the available evidence, and exits 1. The option is mutually exclusive with
`--check`, `--resolve`, and resolution flags; empty or invalid combinations are
exit 2.

Each remaining gate uses the same canonical checked-condition presentation as
`journal --check`, including its transaction, run, path state, disagreeing
field, or retained-evidence reason. Repair appends only its action sentence;
internal condition codes are not public output. After confirmed temporary and
private-duplicate cleanup, and while the exact selected WAL remains, repair
reacquires one final coherent snapshot. It reconfirms the published pair and
selected discharge, rejects a newly appeared condition, and derives gates only
from that final state. The selected WAL is removed only then, as the last
effect. An exact private duplicate removed during cleanup therefore cannot
remain as a stale gate; unproven residue remains a checked gate, and cleanup or
final-confirmation uncertainty is `Retained` with the selected WAL preserved or
identically re-established for the same targeted retry.

`journal --check` enumerates retained residue; targeted repair leaves
unclaimed residue untouched.

A refusal is an ERROR and exit 1, not a traceback and not a usage error: the
id was well formed and the flags were legal, and what could not be done is a
fact about this project's state. An id no unresolved transaction has is one
of those:

```
ERROR: .validated-memory/transactions/51de77210788b0fd.json: journal: transaction 51de77210788b0fd does not prove this repair: there is no unresolved transaction with that id. No target or permanent-history change was left by this operation. Preserve all evidence and select a transaction carrying the required valid proof, or restore exact trusted history.
```

That refusal is reached before a mutation lock or writable state is
materialized, so its terminal-state sentence is true of the tree as well as
of the log: run in a directory that has never been adopted, it leaves no
`journal.jsonl` and no `.validated-memory/` behind.

A journal that is present but cannot be parsed is refused with its line
number, in either reporting mode:

```
ERROR: journal.jsonl:5: journal: line is not valid JSON: Expecting value
journal: 0 record(s), 1 error(s)
```

So is a directory the plugin owns under the vault that is not a directory.
`.validated-memory/transactions` and `.validated-memory/preimages` are
written by name, and a symlink or a plain file standing where one of them
goes is refused in every mode rather than followed (see
[Journal](journal.md#the-write-ahead-log)):

```
ERROR: .validated-memory/transactions: journal: .validated-memory/transactions is not a directory, and this plugin writes what it owns only into a real directory of its own: everything under that name is created, written and removed by name, and a name that is somebody else's carries all of it somewhere this project promises nothing about. Move it aside.
journal: 13 record(s), 1 error(s)
```

Exit codes: `0` clean; with `--check`, `1` if anything was found and `0`
otherwise; with `--resolve`, `0` when the transaction was closed and `1`
when it was refused; `1` in every mode, `--check` or not, for a journal
that cannot be read as records at all or a vault directory the plugin owns
that is not a directory -- both of the ERRORs above; `2` a usage error --
a resolution flag with no `--resolve`, `--resolve` with none of the three
or with two of them, `--resolve` alongside the read-only `--check`, or an
empty id, which reaches no transaction and would name none in a refusal
either.

## Portable transfer operations (since 2.3.0)

Version 2.3.0 adds `export-transfer`, `import-transfer`, `show-transfer`,
`link-transfer` and `detach-transfer` under consultation. Version 2.2.0 provides
schema 1 checked consultation; schema 2 was an unreleased incorporation format.
Version 2.3.0 includes incorporation and transfer. Fresh stores use 3; complete 1/2 stores
keep ordinary consultation without implicit migration. Explicit upgrade preserves
old event fields and moves 1/2 to 3; transfer writes require 3.

[Transfer](transfer.md) explains whole-workspace-history disclosure, read-only
`--assess`, offline inspection and explicit dependency/predecessor mappings. An
import or foreign receipt is never local checked-use authorization. Link/detach
outputs complete source material before its committed handle; local proposal
inspection/acceptance/incorporation remains separate. [Transfer storage](transfer-storage.md)
defines closed capsules, prefix-compatible origins and receipt3 current-use gates.


Available since 2.4.0, `show-transfer IMPORT --origin PROJECT_UUID:UNIT_ID
--material [--max-bytes N]` returns complete selected semantic material before
proposal authoring. Both selector flags are required together, once each; with
neither, inventory output is unchanged. The origin must be an exact member of
the import's selected receipt. The one-line historical JSON includes
`selection_closures` for exact original/transitive receipt binding membership;
additional `context-binding` declarations may be earlier or later. Full history
remains in transport/storage; this view is not a replay capsule, acceptance or an
inspection handle. No foreign paths are opened and no store mutation occurs.
Default output bound is 1,048,576 bytes (range 2,048–1,048,576), with one shared
128-node/512-edge budget. Missing proof or overflow refuses before stdout, never
truncates. Exit 0 returns historical material, 1 refuses material acquisition,
and 2 reports invalid arguments. See [selected material](transfer.md#selected-material-before-proposal-authoring)
for closure, audit references and the mandatory complete two-line review protocols.
