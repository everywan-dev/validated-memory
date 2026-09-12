---
name: maintain-agent-memory
description: Use when recording or updating a persistent agent-memory fact -- a user preference, a project fact, feedback, or a reference note the harness should remember across sessions. Triggers on requests like "remember that I prefer X", "note this project fact", "update this memory", or "this preference changed". Do not use for curated knowledge with evidence and freshness tracking; that belongs to create-knowledge-unit.
---

# Maintain agent memory

Agent memory is one Markdown file per fact under `memory/`, plus a
one-line-per-entry index at `memory/MEMORY.md`. Write and edit the files
directly; `lint` only checks what is there.

## Write a memory file

```yaml
---
name: short-kebab-slug        # required; unique; must match the filename
description: one-line summary # required
metadata:
  type: user                  # required; user | project | feedback | reference
---

Body, in prose. May reference another memory with a [[wikilink]].
```

**Name the file first, then copy that name into `name`.** The filename
without `.md` is the memory's canonical identity, so the file
`memory/short-kebab-slug.md` carries `name: short-kebab-slug`. Choose a slug, not a title: `name` is an
identifier that wikilinks resolve against, not a label -- the human-readable
title belongs in the index entry. `lint` reports an ERROR when the two disagree, and the
repair is always to rewrite `name`, never to rename the file.

Since the filename is the identity, no two memories may carry the same one --
including across subdirectories. There the repair *is* renaming: what may
never be renamed is a file being made to match its own `name`.

Add a matching bullet to `memory/MEMORY.md`:

```markdown
- [Title](short-kebab-slug.md) — one-line summary
```

Only bullets shaped `- [Title](file.md)` count as index entries; the index
and the files must agree in both directions.

## Retain a requested lesson or review

An explicit request to remember a lesson authorizes that bounded memory edit;
show the exact entry and index change, then write it without another generic
confirmation. A suggestion made at the end of another task is different: show a
concise proposed lesson and wait for acceptance before retaining it. Never archive
a transcript or infer capture merely because recall returned something.

Use a `feedback`, `project`, or `reference` entry as the content requires. Keep
the reported observation and its attribution, any tentative interpretation, the
conditions where it applied, traceable sources, and the next useful check clear
in body prose. Generated wording does not corroborate the observation. Use
`create-knowledge-unit` instead when the requested result is a scoped claim whose
evidence state or freshness must be represented.

A non-superseding selective review is normally an indexed `feedback` entry. Name
the reviewed layer, canonical identity, project-relative locator, evidence
actually inspected, scoped outcome (`supported`, `qualified`, `unresolved`, or
`contradicted`) and its limits. Those words describe the review; they are not new
evidence states. See [Requested learning and selective review](../../docs/reference/learning.md)
for outcome routing and the complete example.

## Supersede a memory

To correct or retire a memory, do **not** delete the file. Rewrite its
`description` to start with the literal prefix `superseded by ` followed by
a wikilink to the memory that replaces it:

```yaml
description: superseded by [[coffee-preference-v2]]
```

Record the assessment, evidence and limits in the successor body. In
`memory/MEMORY.md`, keep the old link and annotate its label or trailing summary
as superseded by the successor; add the successor's own bullet. This keeps the
old file discoverable without presenting its advice as current. Recall redirects
ordinary matches to the successor; `--include-superseded` exposes history.

The wikilink must resolve to a different memory that exists; pointing at
itself -- by `name` or by this memory's own filename -- or at a name that
does not exist, is malformed.

Unlike an ordinary wikilink, a successor cannot be left pending: this is an
ERROR and it gates. If `lint` says the target does not resolve by `name` but
names a file that exists, repair that file's `name` rather than re-point the
supersession.

## Wikilinks

`[[name]]` in a `description` or a file's body names another memory by its
`name`. A wikilink to a memory not written yet is a WARNING, not an ERROR --
it marks something pending.

Wikilinks and `superseded by [[...]]` are memory-layer relationships only. Name
a knowledge unit from memory as an explicit layer, canonical ID and ordinary
path, for example `knowledge` ID `kb-retry-scoped` at
`knowledge/kb-retry-scoped.md`; do not create a cross-layer wikilink or
supersession edge.

Since `name` matches the filename, writing `[[short-kebab-slug]]` for the
memory in `short-kebab-slug.md` is correct by construction. If `lint` says a
wikilink has no matching memory but names a file that declares a different
`name`, the link is fine and that file's `name` is what needs repairing.

## Verify

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m validated_memory lint
```

Enforces, over the whole memory set: the index and the files agree in both
directions, every file's frontmatter is complete, every wikilink either
resolves or is flagged pending, and the supersession marker (if any) is well
formed. A `name` differing from its filename or duplicate filenames across
subdirectories are **ERRORs** from 2.0.0; the 1.x migration warnings have ended.
Repair a divergent `name` to match its filename; for duplicate filenames,
rename one and update its index/references without losing the fact or history.
See the agent-memory reference (docs/reference/agent-memory.md) for the
exact rules. Run it after any edit to memory files
or the index.

If authoring was interrupted, inspect the actual file and index. Complete a
missing index bullet only when the intended entry bytes and authorized scope are
still retained. If the body is partial or its intended content is unavailable,
report what is missing instead of reconstructing it or claiming success from a
clean lint result. Do not re-request authorization that is still retained.
