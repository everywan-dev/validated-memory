# Startup hooks

Two `SessionStart` hooks run, in that order, from `hooks/hooks.json`: one
restores the `--harness-memory` symlink, the other refreshes whichever HTML
views this project has activated. They are two separate scripts rather than
one because their contracts do not mix -- the first never loses data, the
second overwrites files -- and reviewing both concerns in a single script
would make it impossible to review either.

**Restoring the harness-memory symlink.** `hooks/restore-memory-symlink.sh`
restores a project's `--harness-memory` symlink automatically on every
session start -- the wiring [`init`'s `--harness-memory` section](cli.md#init) defers to
it. It computes the harness's per-project memory location the same way
Claude Code lays out `~/.claude/projects/` -- one directory per project,
keyed by the project's own path with **every character that is not a letter
or a digit** replaced by `-` -- and re-runs `init --harness-memory` against
it, with its stdout silenced. That rule covers `_` and `.`, not only `/`:
`/home/u/Claude/data_tools.v2/import_jobs` is keyed
`-home-u-Claude-data-tools-v2-import-jobs`. Getting it wrong is the one
failure here that is silent rather than fail-open -- `init` reports success
against a directory the harness never reads, and the memory simply never
shows up -- so the rule is pinned by a test rather than left to the
substitution being "obviously" about slashes.

This hook is fail-open throughout, matching `init`'s own contract: no
`$CLAUDE_PROJECT_DIR`, a project that has not adopted validated-memory (no
`validated-memory.md`, or no `memory/`, at its root), no `python3` on
`PATH`, or any other problem along the way is a clean no-op -- it never
gates or breaks session startup, and it never deletes data.

Because it runs unattended, it is also where [Absorbing an existing harness memory
directory](cli.md#absorbing-an-existing-harness-memory-directory) normally
happens: the first session after a
project adopts the plugin merges the harness's pre-existing memory into the
project and parks the original as a `.bak`. That merge is deliberately part
of `init` rather than a flag the hook passes, so it happens once, by itself,
on the deployment path -- gated by the recognition rule, which is what keeps
it from touching anything that is not agent memory. See [the adoption
guide](../adoption.md) ("The startup hooks") for the adopter-facing summary.

**Activating and refreshing the HTML views.** Activation of `knowledge.html`
and `memory.html` is the presence of the artifact, not a configuration key:

```
python3 -P -m validated_memory init --view
```

creates whichever of the two is missing and reports `created` / `kept` per
item, the same idiom every other item `init` manages -- and, like every
other item, it never regenerates an artifact that already exists, hand-edited
or not. That is `init`'s documented contract (see [`init`](cli.md#init)); a view
generator inside `init` would break the very command that defines it, so
regeneration belongs to `render` and to the second hook below. Deleting a
file deactivates it; running `init --view` again reactivates it.

A configuration key was considered for this and rejected: an unknown field
in `validated-memory.md` is an ERROR that gates every other subcommand, so
an adopter who added a `view` key and then worked from a machine with an
older copy of the plugin would find `validate`, `derive` and `probe` all
dead, not merely the view disabled. Presence-based activation needs no
change to the configuration schema and cannot break an older plugin, which
sees an `.html` file it does not understand and ignores it.

The second hook, `hooks/refresh-views.sh`, keeps whichever views are active
fresh by running `render --only-existing` (see [`render`](cli.md#render)): it
regenerates only the artifacts already on disk and creates none, so an
adopter who never activated the views pays nothing at session start. It is
fail-open on every path it can fail on -- an invalid corpus, an unreadable
verdict log, a missing memory directory or index, or a write that fails at
the OS level (permissions, a full disk) -- and always exits 0, the same
discipline the first hook follows.

Neither `knowledge.html` nor `memory.html` is added to `.gitignore`: like
`knowledge-index.md` and `verdicts.jsonl`, they are derived files, and this
project decides whether to version them. Versioning them means a fresh
clone has the views immediately; not versioning them means running `init
--view` once after cloning.
