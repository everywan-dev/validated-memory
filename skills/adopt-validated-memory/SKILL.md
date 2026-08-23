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
     write; go on to the next question.
   - **Local, ignored** -- the layout stays in this clone and every remote
     sees only the ignore rule. Append to the repository's `.gitignore`:

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

## Next steps

- Declare adopter-specific fields by editing `knowledge-extension.md` -- see
  the `create-knowledge-unit` skill and the reference's "Declared extension"
  section.
- Register a probe for each anchor `kind` your units will use, by adding an
  entry under `probes:` in `validated-memory.md` -- see the `probe-freshness`
  skill and the reference's `probe` section (docs/reference/cli.md).
- Start writing curated knowledge with the `create-knowledge-unit` skill.
