# CLI reference

```
python3 -m validated_memory <command>
```

Commands: [`init`](#init), [`lint`](#lint), [`validate`](#validate),
[`derive`](#derive), [`probe`](#probe), [`render`](#render).

Exit codes: `0` = clean run or WARNING-only findings (does not gate);
`1` = ERROR (gates); `2` = usage error.

Running the module requires the package on `sys.path`: from a checkout,
prefix commands with `PYTHONPATH=<plugin root>`, or install it with pip —
see [Installing](../installing.md). The plugin's own hooks resolve this
themselves.

### `init`

```
python3 -m validated_memory init [--harness-memory PATH] [--view]
```

Scaffolds a new adopter project in the working directory: `knowledge/`
(empty), `memory/` with an empty index (`memory/MEMORY.md`), the adopter
configuration (`validated-memory.md`), and a valid, empty declared-extension
stub (`knowledge-extension.md`). Right after `init` on an empty directory,
`validate` and `lint` both pass clean -- the bootstrap is verified by the
enforcement it bootstraps, not by inspection. (An empty `knowledge/`
directory still reports its usual WARNING for having no units; that does not
gate.)

Each item is created only if missing. An existing item -- including one
already hand-edited -- is never touched: `init` reports `init: created
<path>` or `init: kept <path>` per item, so re-running it is idempotent and
says so. The only way `init` gates (exit 1) is an item it could not create at
all, e.g. no write permission on the target directory.

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

**`--harness-memory PATH`** makes PATH a move-proof symlink to this
project's `memory/` directory (absolute target), so the harness can read
agent memory from wherever it expects it while the data stays versioned
inside the adopter repo:

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
   project's copy is kept, and a WARNING says so -- the harness's version is
   still in the backup from step 4, for a human to reconcile.
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
on every session start forever -- is worse.

Every failure along the way is fail-open: a WARNING, exit 0, and a state that
still holds every file. The order is what guarantees it -- a failed copy
leaves the original in place and unparked, a failed park leaves the copies in
the project and the original intact, and a failed link leaves the backup path
named in the WARNING.

### `lint`

```
python3 -m validated_memory lint [PATH]
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
explicit PATH that does not exist each stop the run before any file is read.

### `validate`

```
python3 -m validated_memory validate [PATH]
```

Validates every `*.md` unit found under PATH, recursively; PATH may also be a
single unit file. With no PATH it reads `knowledge/` relative to the working
directory. A one-line summary goes to stdout; findings go to stderr as

```
SEVERITY: <unit>: <field>: <message>
SEVERITY: <unit>:<line>: <field>: <message>    # parse errors only
```

A contract rule speaks about the unit as a whole, so it reports no line. Only
the parser reports one, because only the parser knows where it stopped.

Supersession resolves against the validated set: validate the whole knowledge
directory, not a single file, or a `supersedes` entry pointing at a unit you
left out is reported as missing.

### `derive`

```
python3 -m validated_memory derive [PATH] [--check]
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
python3 -m validated_memory probe [PATH]
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
executable not found), a non-zero exit, stdout that does not parse as JSON,
or a verdict outside the three-value domain. Each such fallback is reported
to stderr as a WARNING finding, in the usual shape:

```
WARNING: <unit>: anchors[<i>]: <message>
```

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
usage error.

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

### `render`

```
python3 -m validated_memory render [--only-existing]
```

Writes two self-contained HTML pages to the working directory -- alongside
`knowledge-index.md` and `verdicts.jsonl`, never inside `knowledge/`:
`knowledge.html`, the curated layer, and `memory.html`, the agent-memory
layer. Two files rather than one because the two layers share neither
frontmatter, nor relations, nor the way each stops being true -- no single
name covers both without being false about one of them.

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

**A file whose content is unchanged is not rewritten**, and the output
carries no generation timestamp, so an unchanged corpus produces
byte-identical output run after run. Without this, the refresh hook (see [Startup
hooks](hooks.md)) would dirty `git status` on every session start, forever, in a repository
that treats that churn as a defect.

**Both pages are inert.** No JavaScript and no request to the network:
collapsing a section uses the browser's native `<details>`/`<summary>`, not
a script. The only attribute anywhere in either page that carries an
external URL is `href` on an `<a>` element, and only a `provenance` entry
with an `http://` or `https://` scheme becomes a link -- anything else (a
`javascript:` URI, a bare string, a mapping) is shown as escaped text
instead. A unit or memory body is shown verbatim, escaped and unrendered,
in a monospaced block; the one line extracted from it is the headline,
taken from the body's first heading and falling back to the unit's `id`
when there is none.

**`knowledge.html`** lists live conclusions ordered by `id` -- the one
ordering that does not move on its own, unlike freshness or recency, which
a routine `probe` would reshuffle on the next session. Each entry expands
to its body, every anchor's envelope and provenance, the anchor's probe
history, and the supersession chain that led to it, nested as deep as the
chain runs; a superseded unit never appears at the top level, only inside
the history of whatever replaced it. The page states how many records the
verdict log holds in total and how many belong to an anchor shown on the
page -- two totals, not one, because the log outlives the corpus (nothing
prunes a record whose unit or anchor is gone), so a single total could
never be reconciled by a reader against the histories in front of them.
Probe history itself shows at most 20 records per anchor, most recent
first, and each anchor's own history repeats the disclosure for itself --
`N record(s) for this anchor; showing M` -- which is what actually lets a
reader tell a full history from a truncated one. Two diagrams, both inline
SVG: a freshness strip per anchor (one band per probe, in log order,
coloured and labelled by verdict) and a many-to-one confluence, drawn only
when three or more units are superseded at once.

**`memory.html`** lists entries by filename, each with its outgoing and
incoming references -- the wikilink graph, walkable entry by entry rather
than drawn (a real corpus runs to hundreds of links, which draws as a
hairball nobody reads anything out of). A superseded entry is marked as
such and links to its successor. A wikilink inside a body is never turned
into a link: the body is verbatim, and linkifying it would be rendering it.

**`--only-existing`** regenerates only the artifacts already on disk and
creates neither -- it is what the refresh hook invokes, and it is what
makes activation and deactivation (see [Startup hooks](hooks.md)) mean
anything.
It is also fail-open: an invalid corpus, an unreadable verdict log, or a
missing memory directory or index is a WARNING and exit 0, leaving whatever
is already on disk exactly as it was, rather than an ERROR that a hook
would otherwise report unattended on every session start until someone
fixes the corpus. Run explicitly, without the flag, the same corpus is an
ERROR that gates -- a person asking for the views by hand is entitled to be
told they were not built.

Exit codes: `0` clean, or WARNING-only findings; `1` an ERROR finding; `2`
a usage error.
