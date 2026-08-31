---
name: adopt-validated-memory
description: Use when a project wants to adopt the validated-memory method -- bootstrapping curated knowledge and agent memory for the first time, wiring the harness's persistent memory to this project, or verifying an adoption is set up correctly. Triggers on requests like "adopt validated-memory here", "set up curated knowledge for this project", "bootstrap the memory layer", or "wire the harness memory symlink".
---

# Adopt validated-memory

Adopting the plugin in a project is one command, run from the project root
-- preceded by one decision the command cannot make for the adopter.

## Decide what this repository versions

Ask before running anything (with the harness's question tool when there is
one; otherwise in plain text, and wait for the answer). The deadline is not
`init` itself but the first `init --harness-memory`, which the plugin's
`SessionStart` hook runs by itself at the next session start of an adopted
project: that run absorbs the harness's existing agent memory -- `user` and
`feedback` facts included -- into this project's `memory/` (see "Wire the
harness's persistent memory" below). In a repository that versions the
layout, that memory is in the next commit, and in `memory.html` if the views
are activated. Ignore rules written after `init` but before that session
start still hold.

1. **Does this repository version the validated-memory layout?** Three
   answers, none of them reversible for free:
   - **Versioned** -- the default, and the method's premise: knowledge and
     memory travel with the repository, every clone and every CI run sees
     them, and supersession is history the repository keeps. Nothing to
     write; go on to the next question. (`init` still writes the one entry
     for `/.validated-memory/`, the vault, which is never part of this
     choice -- see the "Local, ignored" answer below for why.)
   - **Local, ignored** -- the layout stays in this clone and every remote
     sees only the ignore rule. Append to the repository's `.gitignore`:

     ```
     # validated-memory layout, local to this clone
     /knowledge/
     /memory/
     /validated-memory.md
     /knowledge-extension.md
     /.validated-memory/
     /knowledge-index.md
     /verdicts.jsonl
     /knowledge.html
     /memory.html
     ```

     `journal.jsonl` is **not** part of this choice and is never added to the
     ignore list. It is the record of what adoption did, it is not
     regenerable by anything, and a clone without it cannot reverse an
     adoption or diff one scan's coverage against the next. `.validated-memory/`
     is the other half of that split and is always ignored, whatever the
     answer here: it holds preimages, which may carry bytes this very
     question chose to keep local. `init` writes that one entry into
     `.gitignore` itself on every run, so it does not depend on this answer;
     it is listed here only so this list is complete, and `init` adds
     nothing when the entry is already there.

     Anchored at the root on purpose: a fixture or a package named `memory`
     deeper in the tree is not the layout.
   - **Local, excluded** -- the same list appended to the repository's
     exclude file instead. Nothing reaches any remote, not even the rule,
     and every clone of the repository decides again for itself. The file
     is `.git/info/exclude` in a plain checkout, but not in a linked
     worktree, where `.git` is a file; resolve it rather than spelling it:

     ```
     git rev-parse --git-path info/exclude
     ```

   Whichever file is written, confirm the rule took with
   `git check-ignore -v memory/` -- and remember that ignoring never
   untracks: a path already committed stays committed until
   `git rm --cached` removes it.

   What git cannot do is answer **per remote** for the same commit: a path
   is either in a commit or not, and every remote that receives the commit
   receives the same answer. An adopter who wants the data on one host and
   not on another needs two histories. The safe shape is a second
   repository, holding the data and pushed only where it belongs; the
   unsafe one is a private branch with the data and a public branch
   without, each pushed to its own remote, where one wrong push, tag or
   merge publishes the history. This plugin orchestrates neither. Say so
   rather than promising it.

2. **If versioned: are the derived files versioned too?** `knowledge-index.md`
   and `verdicts.jsonl` are derived by `derive` and `probe`, and are either
   committed **together** or not at all -- the index bakes in verdicts read
   from the log (ADR 0003, `docs/adr/0003-the-adopter-versions-the-verdict-log-alongside-the-index.md`).
   Versioning them is what lets CI gate on `derive --check`. Not versioning
   them has two consequences to state: every clone runs `derive` before it
   can read an index, and `status` must be invoked with `--skip-index`
   wherever it runs (CI included) -- a missing index is an ERROR otherwise
   (ADR 0002). To ignore them, append exactly these two lines of the list
   above to the same file chosen in question 1: `/knowledge-index.md` and
   `/verdicts.jsonl` -- both, or neither.

3. **Activate the HTML views?** `knowledge.html` and `memory.html` are
   derived too, refreshed at every session start once they exist, and
   `memory.html` shows every memory entry's body. Ask whether to create
   them; if the answer to the first question was "versioned", ask whether
   these two are versioned or ignored -- to ignore them, append
   `/knowledge.html` and `/memory.html` to the same file as above.

The ignore rules and the `status` flags are the record of these answers;
nothing else needs writing down.

## Bootstrap the layout

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m validated_memory init
```

This scaffolds, creating each item only if missing (existing items are never
touched):

- `knowledge/` -- empty; curated-knowledge units go here.
- `memory/` -- empty except for its index, `memory/MEMORY.md`.
- `validated-memory.md` -- the adopter configuration: declares the extension,
  the `id_prefix`, and the probe registry (already mapping `git_ref` to the
  bundled probe).
- `knowledge-extension.md` -- a valid, empty declared extension (`fields: []`).

`init` reports `init: created <path>` or `init: kept <path>` for each item,
and is safe to re-run: it is idempotent and never overwrites a hand-edited
file. See the reference's `init` section (docs/reference/cli.md) for the full contract, including
`--harness-memory` below.

## Import existing knowledge

`init` has run, so `knowledge/` and `memory/` exist. A project adopting this
method usually already practises one of its own: hypothesis registers,
research reports that close with a verdict, per-incident findings,
verification queries, agent memories, context files, decision records. This
phase imports what is worth importing and leaves a record of everything it
saw. Ask each question with the harness's question tool when there is one,
in plain text otherwise, and wait for the answer.

**Q1 -- Sources.** "Does this project already have a knowledge system or a
source of truth we should import? Name paths (inside or outside this
repository), context files, and databases."

Collect the answer **as text and nothing more**: nothing is resolved, opened
or looked up at this point. For each source named, propose an **alias** --
the last path component, or the database's name, normalized to the alias
grammar `[a-z0-9][a-z0-9-]{0,39}` -- and let the user approve or change it.
An alias must be unique among the sources declared and the active
`source-*` entries already in `memory/`; a duplicate is refused before
anything else happens. The alias is how the source is recorded; a path is
recorded only when it lies inside the repository.

**Q2 -- Scan the declared sources.** Asked only when Q1 named at least one
source. Before asking, show, per declared path: the realpath it resolves to
(symlinks followed), its type (file or directory), the scope -- a directory
is read recursively -- and the exclusions that apply. **Refuse** a path that
resolves to the filesystem root, to the user's home directory, to the
harness's configuration directory, or to an ancestor of the repository root:
they are not sources, they are everything. A path that resolves inside the
repository root -- which is what the harness-memory symlink resolves to after
the first session, since it points into `memory/` -- keeps the exact scope
declared; it is not widened to the repository.

Then ask: "Scan these sources now? Nothing is written until you confirm the
report." with this notice, unchanged: *the whole repository is scanned as
well; what you declared is proposed for import, and anything else found is
reported, and offered only under its own separate confirmation.*

- **Yes** -- run `bootstrap-from-repo` in mode `declared+repo`. This answer
  is the consent that turns each shown path into a read root.
- **No** -- the declared sources are not read. Propose one record entry per
  source with status `declared, not scanned`, shown in full like any other
  candidate, and write them only on confirmation. Say in one sentence that
  the sources are referenced rather than scanned, and that later sessions
  will be told.

**Q3 -- Repository scan.** Asked only when Q1 named nothing, or Q2 was No;
with Q2 = Yes the repository scan is already part of that run. "Scan this
repository for validated knowledge or agent memory worth importing?" Yes
runs `bootstrap-from-repo` in mode `repo`. **No** -- nothing is read: the
phase ends with the record entries already proposed under Q2 (or with
nothing to record when Q1 named nothing), and the questionnaire proceeds to
the instruction-file step below.

When a scan was consented to, dispatch it to a **read-only subagent** where
the harness offers one that can be denied execution, network and writes, and
carry on with the next question while it runs; where it cannot, run the scan
inline after the last question, under the same work packet. The order of
the questions does not change either way. Whichever way it ran, **check the
returned report first, and present it only if it passes.** Nothing is shown
and nothing is confirmed before the check below; a report that fails it is
never presented at all.

**Check the report's coverage ledger against an inventory you obtain
yourself.** Not from the scan: a scan that supplies both the coverage claim
and its only evidence cannot be checked at all. Take the inventory from the
same universe the scan works in -- tracked files plus untracked files that
are not ignored, which is `git ls-files` together with
`git ls-files --others --exclude-standard`; where the project is not a git
repository, every regular file under the root, not following symlinks out
of it. Count it per first-level directory, with root-level files under
`.`. Then verify:

- every partition the packet named has a block in the ledger, and in mode
  `declared+repo` the repository-remainder partition is one of them;
- `discovered = classified + excluded + oversized + unreadable` in every
  partition;
- each partition's `discovered` equals your own count for the same scope.
  Equal, not close: both sides are counting the same universe, so a
  difference is a disagreement about what exists and is resolved before
  anything else;
- **every path counted as `oversized` or `unreadable` is listed by path in
  section 3**, and their listed counts equal their ledger counts;
- **every exclusion appears as a scope with its rule and its count**, the
  scopes do not overlap, they sum to `excluded`, and each names something
  that exists in your inventory. These two are the checks with teeth:
  without them a scan can read two files of a thousand and book the other
  998 as `unreadable`, or as `excluded`, and every count above still
  balances.

For a declared source **outside the repository**, `git ls-files` says
nothing: list that path yourself the way the non-git case is listed, every
regular file under it without following symlinks out of it, and compare
that count with its partition. A partition you cannot inventory is a
partition you cannot check, and it is reported to the user as exactly that
rather than passed as if checked.

What this cannot do, said plainly so nobody relies on more: the ledger
makes an **omission** visible, because omitting a path leaves a count that
disagrees with a listing taken elsewhere. It cannot make **fabrication**
impossible -- a scan that claims to have read and judged a file it never
opened books it as `classified`, and no report can contradict that. What
the ledger buys is that the lie has to be specific and written down.

A report failing any of these is not presented and nothing from it is
written. Say which check failed and run the scan again. Section 2 being
present is not evidence: a section 2 listing two hand-picked files, in a
repository whose remainder holds hundreds, satisfies the layout and not the
scan.

With Q2 = Yes there is no Q3 left to ask, so the questionnaire proceeds
straight to the instruction-file step below while the scan runs. Whichever
question was the last one, and whether the scan ran in a subagent or inline,
there is a single rendezvous: the report is presented once the scan has
returned **and** the instruction-file step is done. The instruction-file step
never waits for the scan, and nothing from the report is written before that
rendezvous.

Close the phase by naming what was imported and what was left, in counts.
Every source seen has its `memory/source-<alias>.md` record entry, written
with its `memory/MEMORY.md` line like any other memory entry.

## Tell later sessions that this project practises the method

Offer to write a fixed block into the adopter's agent-instruction file --
`CLAUDE.md`, and `AGENTS.md` where one exists. Show the exact resulting diff
and write it only on confirmation. `init` never touches these files, and
neither does anything here without that confirmation: a file the adopter
owns, mutated unattended, is a file nobody reviews.

The block is delimited by two marker lines, written `<!-- validated-memory:begin -->`
and `<!-- validated-memory:end -->`. The write rule is closed, because the
file is the adopter's and the failure mode is losing content this plugin
does not own:

- **no marker in the file** -- append the block, on confirmation;
- **exactly one begin marker followed by exactly one end marker**, each on
  its own line, in that order -- replace what lies between them, on
  confirmation, after showing the diff; when the block on disk already
  equals the canonical one, do nothing and say so;
- **anything else** -- a marker repeated, nested, reversed or unpaired, or a
  marker inside a fenced code block -- write nothing, name the lines, and
  leave the repair to the user;
- **the file is a symlink**, or its realpath is outside the repository root
  -- write nothing, say so.

Re-read the file immediately before writing and compare it with what the
diff was built from; a file that changed in between is shown again.
Everything outside the markers is preserved byte for byte, including the
line-ending style and the presence or absence of a final newline.

The canonical block, which `docs/adoption.md` quotes and a test keeps equal
to this copy:

```markdown
<!-- validated-memory:begin -->
## Validated memory

This project practises the validated-memory method. Curated knowledge lives
in `knowledge/` (one unit per claim, with `evidence` declared and freshness
probed); agent memory lives in `memory/` (one fact per file, indexed in
`memory/MEMORY.md`); `knowledge-index.md` is derived and never hand-edited.

- Record a finding, decision or measured fact worth re-checking as a
  knowledge unit (`create-knowledge-unit`); a preference or a durable
  project fact as a memory entry (`maintain-agent-memory`).
- When the world changes a fact, do not edit it: write a successor and
  supersede the old record (`supersede-knowledge`). Only a defect `lint` can
  name is repaired in place.
- Before citing a curated fact that carries anchors, read its verdict in
  `knowledge-index.md` (run `derive` first if this clone does not version
  it); `drifted` or `unknown` means re-check first (`probe-freshness`).
- `memory/source-*.md` entries record sources of existing knowledge seen at
  adoption; one whose status is `declared, not scanned` is knowledge this
  project has not imported yet (`bootstrap-from-repo` imports it).
- Usage questions: `ask-validated-memory`.
<!-- validated-memory:end -->
```

## Wire the harness's persistent memory (optional)

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m validated_memory init --harness-memory PATH
```

Makes `PATH` a move-proof symlink to this project's `memory/` directory, so
the harness reads agent memory from wherever it expects it while the data
stays inside this project -- versioned, if the repository versions the
layout. Safe to call repeatedly, including after
the project is renamed or re-cloned -- it only ever re-points the symlink,
never deletes data. The plugin's `SessionStart` hook
(`hooks/restore-memory-symlink.sh`) already calls this automatically for an
adopted project on every session start; running it by hand is only needed to
wire a harness location the hook does not already know about.

## Activate the HTML views (optional)

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m validated_memory init --view
```

Creates `knowledge.html` and `memory.html` -- self-contained, static pages
of the curated and agent-memory layers, readable with no plugin and no
Python installed -- once each, reporting `created` / `kept` per item like
every other item `init` manages. Activation is the presence of the file,
not a setting: deleting one deactivates it, and running `init --view` again
brings it back. The plugin's `SessionStart` hooks already include one
(`hooks/refresh-views.sh`) that keeps whichever views are active fresh on
every session start, so nothing further needs to be invoked by hand after
this. See the reference's `render` section (docs/reference/cli.md) for what each page shows.

## Verify the adoption

Right after `init`, both enforcement commands must pass clean:

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m validated_memory validate
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m validated_memory lint
```

`validate` may still report a WARNING for an empty `knowledge/` (no units to
check) -- that does not gate. Any ERROR means the scaffold is broken; do not
proceed until both commands are clean.

Then list the active `memory/source-*.md` entries whose status is
`declared, not scanned` or `not located`, and name them: those are sources
this project knows about and has not imported. Re-running
`bootstrap-from-repo` is what imports them -- and a source outside the
repository has to be declared and consented to again, because its record
holds only an alias, never a path.

## Next steps

- Declare adopter-specific fields by editing `knowledge-extension.md` -- see
  the `create-knowledge-unit` skill and the reference's "Declared extension"
  section.
- Register a probe for each anchor `kind` your units will use, by adding an
  entry under `probes:` in `validated-memory.md` -- see the `probe-freshness`
  skill and the reference's `probe` section (docs/reference/cli.md).
- Start writing curated knowledge with the `create-knowledge-unit` skill.
