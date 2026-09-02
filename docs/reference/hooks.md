# Startup hooks

Three `SessionStart` hooks run, in that order, from `hooks/hooks.json`: one
restores the `--harness-memory` symlink, the second refreshes whichever HTML
views this project has activated, and the third injects one screen of live
status into the session. They are three separate scripts rather than one
because their contracts do not mix -- the first never loses data, the second
overwrites files, the third writes nothing at all -- and reviewing three
concerns in a single script would make it impossible to review any of them.

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

Every `init` run this hook makes -- what it created, what it found
already there, the symlink it wrote or re-pointed -- is recorded the same
way any other `init` run is: see [Journal](journal.md). The hook itself
never calls `journal`; it only makes the `init` calls that fill it.

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

**Injecting the project's current status.** `hooks/session-context.sh` is
the only one of the three that produces output the model reads. It prints
plain text on stdout, which `SessionStart` adds to the session's context
as-is; the JSON envelope is needed only to combine context with other
fields, and stdout is parsed as JSON only when its first non-blank character
is `{`, so the fixed sentence comes first and the first character is never
`{`. The harness caps hook output at 10,000 characters, and this context is
bounded by construction: one sentence, the `status` summary lines, and at
most one line of counts.

What it prints, in order:

1. One fixed sentence: this project practises validated-memory, the plugin's
   skills say how, and the lines that follow are machine-generated status
   rather than instructions. It does not mention the managed block in the
   adopter's instruction file: writing that block is confirmation-gated and
   skipping it is a supported answer, so a sentence asserting it would be
   false in every adoption that declined it.
2. The **stdout** of `status --skip-index`, whatever it is and whatever its
   exit code.
3. One line of counts over the active `memory/source-*.md` record entries,
   omitted when the project has none:
   `knowledge sources: <a> imported, <b> declared not scanned, <c> found not imported, <d> not located`.
   The value is read the way the frontmatter parser reads it, for every
   form written in ASCII. A quoted scalar is unquoted, so a record written
   `description: "knowledge source x: imported"` counts like the unquoted
   form the skill asks for; a trailing comment is cut from either form, the
   way `_cut_comment` cuts one; and spaces between the key and its colon are
   tolerated, the way the parser strips a key. `lint` reports the quoted
   form as a WARNING rather than the count vanishing. A value the parser
   would reject -- an unterminated quote -- counts nowhere, because a hook
   that guessed at it would report a source the CLI refuses.

   Two divergences are left, and the hook names them where they live. A tab
   is tolerated here and rejected by the parser anywhere in frontmatter, so
   an entry carrying one can count while `lint` reports it as an ERROR. And
   the padding the hook strips is ASCII space and tab, where the parser
   strips every character Python calls whitespace, so a value padded with a
   no-break space, a vertical tab or a form feed parses and counts nowhere.
   Neither is portable to recognise across gawk, mawk and busybox awk, and
   neither is a form the skill writes. Both are pinned by a test
   (`tests/test_session_context_hook.py`), so the divergence is a checked
   statement rather than prose either side can drift away from.

Two details carry the safety of this hook. First, `status` writes only its
`status:` summary lines to stdout and every `ERROR:`/`WARNING:` finding to
stderr, which this hook discards -- and a finding quotes adopter-written
text verbatim, a memory's `name` or a unit's id. Discarding stderr is what
closes that injection channel; nothing is escaped, because nothing quoted
arrives. Second, the counts are computed by the hook itself, from each
entry's first frontmatter block and its single `description` line, so the
digits are the hook's own and no text from any entry reaches the session. An
entry counts nowhere when its description is retired (`superseded by ...`),
when it matches none of the four status literals, when the block carries two
`description` lines, or when the frontmatter never closes.

Fail-open here has one wrinkle the other two hooks do not have, because this
one has something to say. **Printing nothing at all is reserved for three
cases**: no `$CLAUDE_PROJECT_DIR`, a project that has not adopted the method
(no `validated-memory.md`, or no `memory/` directory -- a dangling symlink
counts as no directory), and no `python3` on `PATH`. Every other problem is
a *degraded* run, not a silent one: the fixed sentence is printed, so is
whatever else succeeded, and one fixed line goes to stderr --
`session-context: could not compute part of the session context; continuing`.
That line never repeats the failing command's own output, which is text from
a program that has just misbehaved. The exit status is 0 in every case.

`--skip-index` is unconditional here: this context orients, it does not
gate, and the index gate stays where it belongs, in CI with the adopter's
own flags ([ADR 0002](../adr/0002-status-gates-consistency-and-only-reports-freshness.md)).
`status` is read-only and never probes, so the hook inherits both
properties. It also sets `PYTHONDONTWRITEBYTECODE=1`, so that a read-only
hook does not plant `__pycache__` inside the plugin it just ran: a snapshot
of the adopter tree *and* of the plugin, taken before and after a run, is
identical.

The hook is registered without a `matcher`, so it fires on every
`SessionStart` source -- startup, resume, clear, compact, fork. That is
wanted: a compaction is exactly when this context is lost and worth
re-injecting. And it is registered third on purpose: the first hook may
absorb the harness's memory directory and rewrite `memory/MEMORY.md`, which
is what this one then reports on.
