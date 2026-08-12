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
name: short-kebab-slug        # required; unique across the memory set
description: one-line summary # required
metadata:
  type: user                  # required; user | project | feedback | reference
---

Body, in prose. May reference another memory with a [[wikilink]].
```

Add a matching bullet to `memory/MEMORY.md`:

```markdown
- [Title](short-kebab-slug.md) — one-line summary
```

Only bullets shaped `- [Title](file.md)` count as index entries; the index
and the files must agree in both directions.

## Supersede a memory

To correct or retire a memory, do **not** delete the file. Rewrite its
`description` to start with the literal prefix `superseded by ` followed by
a wikilink to the memory that replaces it:

```yaml
description: superseded by [[coffee-preference-v2]]
```

The wikilink must resolve to a different memory that exists; pointing at
itself, or at a name that does not exist, is malformed.

## Wikilinks

`[[name]]` in a `description` or a file's body names another memory by its
`name`. A wikilink to a memory not written yet is a WARNING, not an ERROR --
it marks something pending.

## Verify

```
python3 -m validated_memory lint
```

Enforces, over the whole memory set: the index and the files agree in both
directions, every file's frontmatter is complete, every wikilink either
resolves or is flagged pending, and the supersession marker (if any) is well
formed. See the README's "Agent memory" section for the exact rules. Run it
after any edit to memory files or the index.
