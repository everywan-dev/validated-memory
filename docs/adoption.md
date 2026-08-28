# Adoption guide

How to bring a project onto validated-memory: install the plugin, bootstrap
the layout, declare an extension, register probes, gate CI on the derived
index, and optionally activate the HTML views. For the full command
reference, see [the reference](reference/cli.md). For a worked example end to
end, see [the walkthrough](walkthrough.md).

## 1. Install the plugin

Install `validated-memory` as a Claude Code plugin (however your harness
manages plugins -- a marketplace, a local plugin path, or a checkout
referenced directly). Once installed, its seven skills are discovered from
`skills/*/SKILL.md` by directory convention, and its two startup hooks from
`hooks/hooks.json` -- neither needs any registration inside the adopter
project.

## 2. Decide what this repository versions

One decision precedes the bootstrap, because the plugin makes it for you if
you do not: the first `init --harness-memory` -- which [the startup
hooks](#the-startup-hooks) run by themselves at the next session start of an
adopted project -- absorbs the harness's existing agent memory, `user` and
`feedback` facts included, into this project's `memory/`. In a repository
that versions the layout, that memory is in the next commit. Ignore rules
written after `init` but before that session start still hold; the deadline
is the session start, not `init`.

**Versioned** is the default and the method's premise -- knowledge and memory
travel with the repository, every clone and every CI run sees them, and
supersession is history the repository keeps. A repository that instead keeps
the layout **local to the clone** ignores it, either in the committed
`.gitignore` (every remote sees the rule, none sees the data) or in the
repository's exclude file (nothing reaches any remote, and each clone decides
for itself; `.git/info/exclude` in a plain checkout, `git rev-parse
--git-path info/exclude` in general, since a linked worktree's `.git` is a
file). Either way the list is the same, anchored at the root so that a
fixture or a package named `memory` deeper in the tree is not mistaken for
the layout:

```
# validated-memory layout, local to this clone
/knowledge/
/memory/
/validated-memory.md
/knowledge-extension.md
/knowledge-index.md
/verdicts.jsonl
/knowledge.html
/memory.html
```

Ignoring never untracks: a path already committed stays committed until
`git rm --cached` removes it, and `git check-ignore -v` is the check that a
rule took.

The last four lines are derived files; a repository that versions the layout
still chooses, separately, whether to version them -- `knowledge-index.md`
and `verdicts.jsonl` together or not at all (see [step 6](#6-gate-ci-on-the-derived-index);
a repository that does not version them runs `status` with `--skip-index`,
per [ADR 0002](adr/0002-status-gates-consistency-and-only-reports-freshness.md)),
and the two HTML views (see [step 7](#7-activate-the-html-views-optional)).

What git cannot express is a **per-remote** answer for the same commit: a
path is either in a commit or not, and every remote that receives the commit
receives the same answer. Wanting the data on one host and not on another
means two histories -- safely, a second repository holding the data and
pushed only where it belongs; unsafely, a private branch with the data and a
public one without, where one wrong push publishes the history. This plugin
orchestrates neither.

The `adopt-validated-memory` skill asks these questions before it runs
`init`, and writes the ignore list for the "local" answers; the decision to
keep the questions in the skill and `init` non-interactive is [ADR
0007](adr/0007-adoption-decisions-live-in-the-skill-not-in-init.md).

## 3. Bootstrap the layout

From the adopter project's root:

```
python3 -P -m validated_memory init
```

The commands in this guide invoke the module directly, which requires the
package on `sys.path`: from a plugin checkout, prefix each command with
`PYTHONPATH=<plugin root>`, or install the package once -- see
[Installing](installing.md). The plugin's own hooks and skills resolve this
themselves.

See the reference's [`init`](reference/cli.md#init) section for exactly what this
creates. It is idempotent and safe to re-run: existing files are never
touched.

If the harness needs to read agent memory from a fixed location outside this
project (see [Agent memory](reference/agent-memory.md)), also pass
`--harness-memory PATH`; see [the startup hooks](#the-startup-hooks) below for
how this stays in sync automatically after the first run.

If the harness has already been writing agent memory of its own for this
project, PATH is a real directory, not free. `init` merges it rather than
leaving two memories that cannot see each other: it copies the memory files
into this project's `memory/`, gives each one an entry in `MEMORY.md`, parks
the harness's original directory alongside as `PATH.bak`, and only then
creates the symlink. Nothing is overwritten and nothing is deleted -- see
[Absorbing an existing harness memory
directory](reference/cli.md#absorbing-an-existing-harness-memory-directory) for
the recognition rule, the conflict rule, and what happens on failure. After
it runs, `lint` is the check that the merge is sound.

Right after bootstrapping, confirm both enforcement commands pass clean:

```
python3 -P -m validated_memory validate
python3 -P -m validated_memory lint
```

Adoption does not stop at the scaffold. The `adopt-validated-memory` skill
then asks what existing knowledge this project already has and hands the
answer to `bootstrap-from-repo`, and it offers to write one managed block
into this project's agent-instruction file -- `CLAUDE.md`, and `AGENTS.md`
where one exists -- so that later sessions know the project practises the
method. The block is written only on confirmation, after the diff has been
shown; `init` never touches those files. This is the block, byte for byte:

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

Everything outside the two markers is preserved byte for byte; a file whose
markers are repeated, nested, reversed or unpaired is left untouched and
reported, not repaired.

## 4. Declare an extension (optional)

The base contract (see [Base contract](reference/curated-knowledge.md#base-contract)) is
deliberately small. If units in this project need adopter-specific fields on
top of it, declare them in `knowledge-extension.md` and point
`validated-memory.md`'s `extension:` block at it -- see
[Declared extension](reference/curated-knowledge.md#declared-extension) for the
field-declaration format and the versioning rule. Skip this step entirely if
the base contract is enough: `init` already scaffolds a valid, empty
extension (`fields: []`), and leaving it empty is a normal, supported end
state, not a stub that must be filled in.

## 5. Register probes

For every anchor `kind` curated-knowledge units in this project will use,
register the command that probes it under `probes:` in
`validated-memory.md` -- see [the probe contract](reference/cli.md#probe) for
the command's stdin/stdout shape. `init` already registers `git_ref`, the
plugin's bundled probe (see
[The bundled `git_ref` probe](reference/cli.md#the-bundled-git_ref-probe));
register any other `kind` this project's anchors use the same way.

## 6. Gate CI on the derived index

If this project versions `knowledge-index.md`, add a CI step that fails when
it has drifted from the units or from freshly recorded verdicts:

```
python3 -P -m validated_memory validate
python3 -P -m validated_memory lint
python3 -P -m validated_memory derive --check
```

`derive --check` recalculates the index in memory and compares it against
the committed file, ignoring only the `Derived:` timestamp -- see
[`derive`](reference/cli.md#derive). Run `validate` and `lint` first, so a
contract violation is reported with its own message rather than surfacing as
an opaque index mismatch. This plugin's own `.gitlab-ci.yml` runs `validate`,
`lint` and the full test suite as its gate; add `derive --check` to a
project's CI the same way once that project commits its index.

**Version `verdicts.jsonl` alongside `knowledge-index.md`** (the v1
persistence policy -- [ADR
0003](adr/0003-the-adopter-versions-the-verdict-log-alongside-the-index.md)).
The committed index bakes in verdicts read from the log, so a clean checkout
without the log re-derives every anchor as `unknown` and `derive --check`
fails for a reason no file in the checkout can explain. The log is
append-only history -- the audit trail of what was probed and what came back
-- so it belongs in the repository anyway. Two consequences to respect:

- The log and the index are one coupled artifact. A pipeline or session
  that probes must re-derive and commit **both together**; committing only
  the appended log leaves an index that no longer matches it, and the next
  clean checkout fails on exactly that mismatch.
- Everything a record carries becomes versioned history: the anchor's
  payload and the probe's `detail` output. Anchors must not carry secrets,
  and a probe must not emit them in `detail` either.

Run `python3 -P -m validated_memory probe` on whatever cadence fits the project
(a scheduled job, a pre-release check, ...) before the `derive --check` gate,
so the verdict column reflects current probing rather than going stale -- see
[`probe`](reference/cli.md#probe). When a probe run changes any verdict,
re-derive and commit the log and the index in the same commit, per the
policy above.

## 7. Activate the HTML views (optional)

`render` writes two self-contained, static HTML pages -- `knowledge.html`
for the curated layer and `memory.html` for the agent-memory layer -- so
someone with neither this repository nor Python installed can still read
what the project holds; see [`render`](reference/cli.md#render) for what each
page shows. Activation is the presence of the file, not a setting:

```
python3 -P -m validated_memory init --view
```

creates whichever of the two is missing and reports `created` / `kept` for
each, and -- like every other item `init` manages -- never touches one that
already exists. Deleting `knowledge.html` or `memory.html` deactivates it;
running `init --view` again brings it back. Like `knowledge-index.md` and
`verdicts.jsonl`, neither artifact is in `.gitignore`: both are derived
files, and this project decides whether to version them.

Once activated, a view stays current on its own -- see
["The startup hooks"](#the-startup-hooks) below for how.

## The startup hooks

Two `SessionStart` hooks run on every session start, wired in
`hooks/hooks.json`.

`hooks/restore-memory-symlink.sh` keeps a `--harness-memory` symlink alive
across renames, re-clones, and fresh sessions, without any manual step:

- Not an adopter project (no `validated-memory.md`, or no `memory/`, at the
  project root) -- does nothing.
- An adopter project -- computes the harness's per-project memory location
  and re-runs `init --harness-memory` against it, silencing its stdout.
  Idempotent and fail-open: any problem it hits (missing tools, a path it
  cannot touch, ...) is reported to stderr and the hook still exits clean,
  so it can never break session startup.

This is also where a pre-existing harness memory directory gets absorbed, on
the first session after adoption, without anyone running anything by hand.
The `.bak` it leaves behind is the only manual follow-up worth doing: once
`lint` passes and the merged memory looks right, that backup can be removed
whenever the adopter wants -- the plugin never touches it again.

`hooks/refresh-views.sh` keeps whichever HTML views this project has
activated (see [step 7](#7-activate-the-html-views-optional) above)
up to date, the same way: it re-runs `render --only-existing`, silencing
its stdout, which regenerates only the artifacts already on disk and
creates neither. A project that never ran `init --view` has no artifacts,
so this hook finds nothing to do and costs it nothing. Fail-open the same
way as the other hook: an invalid corpus, an unreadable verdict log, or a
missing memory directory or index is reported to stderr and the hook still
exits clean, leaving whatever view was already on disk untouched.

Nothing here needs to be invoked by hand in the common case: both hooks run
on every session start for every adopter project that has asked for a
harness-memory symlink, or activated a view, respectively.
