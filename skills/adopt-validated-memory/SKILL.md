---
name: adopt-validated-memory
description: Use when a project wants to adopt the validated-memory method -- bootstrapping curated knowledge and agent memory for the first time, wiring the harness's persistent memory to this project, or verifying an adoption is set up correctly. Triggers on requests like "adopt validated-memory here", "set up curated knowledge for this project", "bootstrap the memory layer", or "wire the harness memory symlink".
---

# Adopt validated-memory

Adopting the plugin in a project is one command, run from the project root.

## Bootstrap the layout

```
python3 -m validated_memory init
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
python3 -m validated_memory init --harness-memory PATH
```

Makes `PATH` a move-proof symlink to this project's `memory/` directory, so
the harness reads agent memory from wherever it expects it while the data
stays versioned inside this repo. Safe to call repeatedly, including after
the project is renamed or re-cloned -- it only ever re-points the symlink,
never deletes data. The plugin's `SessionStart` hook
(`hooks/restore-memory-symlink.sh`) already calls this automatically for an
adopted project on every session start; running it by hand is only needed to
wire a harness location the hook does not already know about.

## Activate the HTML views (optional)

```
python3 -m validated_memory init --view
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
python3 -m validated_memory validate
python3 -m validated_memory lint
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
