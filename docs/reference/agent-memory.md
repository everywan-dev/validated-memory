# Agent memory

The agent-memory layer is one Markdown file per fact (`memory/*.md`), plus a
one-line-per-entry index at `memory/MEMORY.md`. `lint` enforces five things.

**Frontmatter.** Every memory file's frontmatter carries the shape the Claude
Code harness gives it -- `lint` does not redefine it, only requires it be
complete:

```yaml
name: short-kebab-slug        # required; a non-empty string
description: one-line summary # required; a non-empty string
metadata:
  type: user                  # required; user | project | feedback | reference
```

`name`, `description` or `metadata.type` missing, or `metadata.type` outside
its domain, is an ERROR. Additional keys the harness may add are tolerated
without being checked. A `name` must be unique across the memory set: a
duplicate is an ERROR, since wikilinks resolve by `name` and a duplicate
makes that resolution ambiguous.

**Identity.** A memory's canonical identity is its **filename** without
`.md`. `name` is the identifier wikilinks resolve against, and it gives way
to the filename when the two disagree: the repair is to rewrite `name`, never
to rename the file. The reason is measured rather than aesthetic -- in the
corpus behind ADR 0001, a third of the `name` values were titles carrying
spaces, dots and capitals, for which no rename exists at all.

`lint` reports a divergence against the file, naming both sides and the
direction of the repair:

```
WARNING: memory/coffee-preference.md: name: 'Coffee Preference' does not match
the filename 'coffee-preference'; the filename is the canonical identity --
repair 'name' to match it
```

**This is a WARNING purely as a migration concession**, so that a project
whose memory was written before the rule can adopt the plugin without being
gated on its whole backlog. It is not the norm: the rule is that they match,
and the finding **becomes an ERROR in 2.0.0**. The memory layer carries no
version of its own, so it versions with the plugin. A memory whose `name` is
missing or empty is not also reported as diverging -- that defect already has
its own ERROR, and reporting it twice would say the same thing in two places.

Because the filename is the identity, two memories **carrying the same
filename** are two memories claiming the same identity. Two files in one
directory cannot share a name, so this only arises across subdirectories, and
it is a fact about the files: it is reported even when neither one's
frontmatter parses.

```
WARNING: memory/beta/shared.md: filename: the filename 'shared' is also
carried by memory/alpha/shared.md; the filename is the canonical identity,
so these are two memories with the same identity -- rename one
```

Renaming is the repair here, and it does not contradict the rule above: what
that rule forbids is renaming a file to match its `name`, and what collides
here is two files, not a file and its `name`. Reporting it matters now
because otherwise `lint` tells both files to repair `name` towards the same
value, and following that advice lands on the duplicate-name ERROR with no
warning it was coming. It is a WARNING for the same migration reason, and
**becomes an ERROR in 2.0.0** alongside the divergence rule.

Resolution itself is unchanged: still by `name`. What ADR 0001 settles is
only which of the two fields gives way when they disagree -- see
[ADR 0001](../adr/0001-filename-is-the-canonical-memory-identity.md).

**Index.** `MEMORY.md`, at the root of the memory directory, lists one entry
per fact as a Markdown bullet with a link to the file, relative to the
directory:

```markdown
- [Coffee preference](coffee-preference.md) — oat milk in coffee
```

Only bullet lines shaped `- [Title](file.md)` count as entries; headers and
prose are ignored. The index and the memory files must agree in both
directions: an entry whose file does not exist, and a memory file with no
entry in the index, are each an ERROR. A missing `MEMORY.md` stops the run,
pointing at `validated-memory init`.

**Wikilinks.** A `[[name]]` reference in `description` or in a file's body
names another memory by its `name`. A wikilink whose target does not exist is
a WARNING -- it marks something pending to write, and does not gate.

When the target does not resolve but a file **of that name** exists, the
warning names that instead, because "pending to write" would point at the
wrong repair -- the memory is right there, and what does not resolve is its
`name`:

```
WARNING: memory/notes.md: body: wikilink to 'coffee-preference' has no
matching memory; 'coffee-preference.md' declares name 'Coffee Preference'
```

This is the ordinary consequence of a divergence, since people writing
wikilinks reach for the filename. The cause is named only when it is certain:
if two memories in different subdirectories share a filename, either could be
meant, so the generic warning stands rather than guess one.

**Supersession.** A memory is marked superseded by rewriting its
`description` to start with the literal prefix `superseded by ` followed by a
wikilink, e.g. `superseded by [[coffee-preference]]`. Well formed -- the
wikilink resolves to a different memory that exists -- it is recognized and
raises no finding. Malformed is an ERROR: the prefix with no parseable
wikilink after it, a wikilink pointing at a memory that does not exist, or a
wikilink pointing at the memory itself.

A successor is not allowed to stay pending the way an ordinary wikilink is,
so this gates. That is precisely why it names the cause when the target is a
diverging file, rather than reporting a memory that is plainly there as
missing:

```
ERROR: memory/old-note.md: description: supersession points at
'coffee-preference', which does not resolve by name;
'coffee-preference.md' declares name 'Coffee Preference'
```

Pointing at **this memory's own filename** counts as pointing at itself,
whatever its `name` currently says -- the filename is the identity. A target
that does resolve to another memory is a valid supersession even when it
happens to equal this file's filename, so resolution is settled first.

**The `source-*` convention.** Entries named `source-<alias>.md`, of type
`reference`, are written by the `bootstrap-from-repo` and
`adopt-validated-memory` skills to record one fact each about a source of
existing knowledge seen at adoption: its alias, its type, where it is (a
repository-relative path, or the literal `outside the repository`), its
status, the date, and what was written from it. `lint` knows nothing about
them: they are ordinary memory entries, validated by the rules above and by
nothing else, and a status that changes is retired by the same supersession
every other fact uses -- a successor under a new filename, and `superseded
by [[...]]` written into the old one's `description`, which is the only
change that entry ever receives. The convention lives in the skills, and the
startup hook `hooks/session-context.sh` counts the active entries by status
at every session start. A database's definition is *not* one of these: it is
an ordinary `reference` entry named `<alias>-definition.md`, carrying no
status and outside the `source-*` glob.
