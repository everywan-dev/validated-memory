# Uninstall and restore — design (2026-08-30)

Adopting validated-memory is a severe change to a project. It creates a
layout at the root, writes a managed block into files the adopter owns, and
installs three startup hooks that run in every project on the machine. A user
who tries the method and does not want it has, today, no way back: nothing in
the plugin reverses any of it, and no record says what it did.

This design adds a reversal: `uninstall`, a CLI subcommand that relocates the
layout and restores the host files, driven by a questionnaire in a new skill.
Its governing property is the project's own: **nothing is ever deleted**. The
layout is moved, not removed, and the move is itself recorded well enough to
be undone.

The standing rules bind everything here: Python 3 stdlib only, English
everywhere, exit codes 0/1/2, end-to-end subprocess tests only, the CLI is
enforcement and judgment lives in skills, `init` and every subcommand stay
non-interactive and never touch git (ADR 0007), every invocation is
`python3 -P -m validated_memory` (ADR 0006).

## Scope

**In:** the adopter project. The root layout, the managed block in
`CLAUDE.md` and `AGENTS.md`, and the ignore-list entries.

**Out, decided deliberately:**

- **The harness side.** The symlink at `~/.claude/projects/<slug>/memory` and
  the `PATH.bak` that `adopt` parks when it absorbs a pre-existing harness
  memory directory. Writing outside the repository is a different class of
  action and needs its own confirmation; and, as §6 shows, leaving the
  symlink costs nothing because it goes inert on its own.
- **Git.** No commit, no revert, no `git rm`. The move is a working-tree
  change the user commits, or does not.
- **Authorship.** No attempt to tell a unit the plugin proposed from one the
  user wrote by hand. The distinction is not recoverable, and it stops
  mattering once nothing is deleted.

## 1. The inventory is computed, never journaled

The obvious design is a manifest maintained as the plugin writes: every
skill, every subcommand, appending what it created. It is rejected.

A journal has to be kept current by prose skills, and prose skills forget:
measured on this project on 2026-08-29, an adoption skipped a question whose
fixed text was loaded in its own context. A journal that misses writes is
worse than none, because it is trusted precisely when files are about to
move. It also puts a new mutation on every write path, including inside
confirmation-gated flows where the user approved something else.

So the inventory is **derived at uninstall time**, by rule:

- **The root layout** is a fixed set of paths. It is the same set the "local
  to this clone" ignore list names, and
  `test_the_skill_ignore_list_is_exactly_what_the_cli_creates_at_the_root`
  already pins that list against every root artifact `init` (with and without
  `--view`), `derive` and `probe` write on a normal run. `uninstall` derives
  its inventory from that same set in code, and the same test is extended to
  assert the three agree. A new root artifact therefore cannot appear without
  the ignore list, the guide and the uninstall inventory all learning about
  it.
- **The host-file regions** are delimited by markers already in the files.
  Nothing needs to have been stored at write time.

This has a property a journal could not have: it works on a project adopted
by any earlier version, including versions that never anticipated an
uninstall.

## 2. What moves, and where

Everything in the root layout moves to a destination directory, default
`remove-valmem/`, keeping its structure:

```
remove-valmem/
  uninstall-valmem.md      <- the manifest (§4)
  knowledge/
  memory/
  validated-memory.md
  knowledge-extension.md
  knowledge-index.md
  verdicts.jsonl
  knowledge.html
  memory.html
```

Only what exists is moved; a project that never activated the views has no
HTML to move, and that is not an error.

**Refusals**, before anything moves: a destination outside the repository
root, resolved by realpath; a destination that exists and is not empty; a
destination inside the layout being moved. Each refuses with exit 1, names
the reason, and changes nothing.

## 3. What is restored, and how

Two host surfaces, both by marker, neither needing a stored copy.

**The managed block** in `CLAUDE.md`, and in `AGENTS.md` where one exists.
The removal rule mirrors the write rule exactly, because the failure mode is
the same — losing content this plugin does not own:

- exactly one begin marker followed by exactly one end marker, each on its
  own line, in that order: remove the markers and everything between them,
  plus the blank line that separated the block from what precedes it if the
  write added one;
- no marker: nothing to do, said out loud;
- anything else — a marker repeated, nested, reversed, unpaired, or inside a
  fenced code block: **write nothing**, name the lines, leave the repair to
  the user, and refuse the whole uninstall rather than doing half of it;
- the file is a symlink, or its realpath is outside the repository root:
  write nothing, say so.

Everything outside the markers is preserved byte for byte, including
line-ending style and the presence or absence of a final newline. The file is
re-read immediately before writing and compared with what the diff was built
from; a file that changed in between is shown again.

**The ignore-list entries**, in the committed `.gitignore` or in the
repository's exclude file, are the one fenced block whose first line is
`# validated-memory layout, local to this clone`. The block is removed whole.
Two such blocks, or entries of the list scattered outside a block, refuse the
same way a broken marker does.

## 4. The manifest travels with what moved

`uninstall-valmem.md` is written **inside the destination directory**, not at
the project root. A manifest at the root would be one more artifact to clean
up, and it would be the wrong place: what needs explaining is the folder full
of relocated material, to a reader who finds it in six months.

It records, in fixed sections:

- **What this is** — one paragraph naming the plugin, the version that ran
  the uninstall, and the date.
- **Moved** — one row per path: where it is now, where it was, and its size.
- **Restored** — one section per host file, with the exact removed region
  quoted in full and the line range it occupied.
- **Not touched** — the harness symlink and its `.bak` if either exists,
  named with their paths, because they are out of scope and the user should
  know they remain.
- **To undo this** — the inverse operation, stated concretely: move each path
  back, re-run the adoption skill's instruction-file step to restore the
  block.

`uninstall --plan` writes this file and nothing else, so it can be read
before anything moves. `uninstall` executes and writes the same file as the
record of what happened.

## 5. The questionnaire lives in the skill

A new skill, `uninstall-validated-memory`. The skill count in `README.md`
moves from seven to eight, which
`tests/test_readme_currency.py::SKILL_COUNT_PATTERN` enforces.

It asks, with the harness's question tool where there is one and in plain
text otherwise:

1. **Where to put it** — default `remove-valmem/`, showing the resolved
   realpath and the refusals of §2 before accepting.
2. **Which host files to restore** — showing the exact diff of each, one at a
   time. Declining one leaves its block in place, and the manifest records
   that it was left.
3. **The plan** — `uninstall --plan` has run by now; its manifest is
   presented whole, and a single confirmation executes exactly it.

## 6. Consequences, stated before the user confirms

**The hooks go inert on their own.** All three test the same condition —
`validated-memory.md` and `memory/` both present at the project root — and
exit 0 otherwise (`refresh-views.sh:36`, `restore-memory-symlink.sh:50`,
`session-context.sh:58`). Once the layout moves, all three no-op without the
plugin being uninstalled from the harness.

**The harness symlink dangles, harmlessly**, for the same reason: the hook
that would restore it exits before it looks.

**Git shows a rename.** The plan states what `git status` will look like.
`uninstall` runs no git command.

**Uninstall is reversible.** Nothing was deleted and the manifest holds every
original path, so moving the tree back restores the project.

## 7. Testing

The project's only seam: the CLI as a subprocess over fixture adopter trees,
asserting on exit codes, output, and files.

- A full adopt → uninstall cycle leaves the root with no layout artifact.
- Host files come back byte for byte, including CRLF and a missing final
  newline.
- The destination holds every moved path and the manifest.
- A broken marker refuses, exits 1, and changes nothing — asserted by
  comparing the whole tree before and after.
- A destination that exists non-empty, or resolves outside the repository,
  refuses.
- A second uninstall on an already-uninstalled project does nothing, says so,
  and exits 0.
- `--plan` writes the manifest and moves nothing.
- The uninstall inventory, the skill's ignore list and the CLI's root outputs
  are asserted equal, extending the existing test rather than adding a
  parallel one.
