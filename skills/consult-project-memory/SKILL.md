---
name: consult-project-memory
description: Consult this project's agent memory and curated knowledge with `recall`. Use on an explicit request to look something up in prior memory or knowledge, or in a project whose own instruction file has opted into a consultation-first workflow before substantial work; this skill is not triggered automatically on every substantial task.
---

# Consult project memory

Status: P4 usefulness disposition complete. Independent scoring found 8/8
supported outcomes in both arms, no new unsafe reliance and no caveat loss,
but zero paired tasks improved, so the benefit gate was not met -- a broad or
default before-substantial-work recommendation is deferred. This skill
remains available for an explicit lookup request or a project that has opted
into consultation-first work. No measured time or context savings are
claimed; see [the usefulness report](../../docs/plans/memory-reuse/usefulness-report.md).

This skill answers "has this project already looked at this?" from `memory/`
and `knowledge/` before you invest in an investigation, a design or a
behavior change. `ask-validated-memory` answers questions about the plugin
itself; this skill answers questions about the adopter's own recorded facts
and findings.

## When to consult

Run this procedure on two triggers, and no others:

- **An explicit request** to look something up in this project's memory or
  knowledge -- "check what we know about X", "has this been looked at
  before", or a direct ask to run `recall`.
- **A project that has opted into a consultation-first workflow**, recorded
  in its own instruction file (see "Recall never mandates itself" below).
  There, follow that project's own cadence: before substantial
  investigation, a design decision or a behavior change, and again when the
  task's scope changes materially, after a context compaction, or when
  resuming a task in a new session -- a receipt from an earlier consultation
  is not evidence that its answer still holds.

Absent one of these two triggers, this is not a step to perform
automatically on every substantial task. Skip it for trivial formatting,
ordinary conversation, and tasks unrelated to this project's recorded
knowledge. Consulting is proportionate to the decision, not a ritual to
perform on every turn.

## Run recall

Resolved against the plugin, never against a same-named file in the current
project, and without creating any interpreter bytecode:

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m validated_memory recall "QUERY"
```

Form a brief query from the task's objective, the affected component and any
constraint already known. Read [the recall reference](../../docs/reference/recall.md)
for the full flag surface (`--layer`, `--limit`, `--max-bytes`, `--format`,
`--include-superseded`, `--map`); do not add a flag it does not document.

## Read the results, then read the sources

A match is a discovery aid, never a validated answer. For every result worth
relying on:

- Open the original file at the reported path and line, not just the
  excerpt. Read any condition attached to the claim and, when the record is
  superseded or the result was reached by redirect, its successor -- a
  redirected match exists because something retired the record you first
  found, and the reason it was retired can matter as much as the successor.
- Treat a `measured` or `verifiable` evidence state, and a `current` anchor
  verdict, as narrower than the whole claim: an anchor being current
  certifies that one checked thing, not everything the unit asserts. A
  `hypothesis` stays a hypothesis no matter how relevant the match. Consulting
  first is not trusting first.

## Recall never mandates itself

This plugin has no automatic consultation gate: no hook or tool check forces
a consultation step before an agent acts. An adopter's own instruction file
can still require the procedure for its project, and this skill is the
concrete step such a rule points to.

## Reading the outcome correctly

`recall` distinguishes four outcomes that must not be collapsed into one:

- **Exit 0, `matched` is 0.** A clean search in which no record shared a word
  with the query. That is the absence of a lexical match, not a conclusion
  that nothing relevant exists: recall does no stemming or synonym matching.
  Try different terms, then fall back to an ordinary source search.
- **Exit 0, `complete` is true but results were omitted.** Coverage was
  acquired and validated completely; some matching records did not fit the
  requested `--limit` or `--max-bytes`. Raise whichever bound the output
  names, within its documented range, or narrow the query -- do not read
  this as "no matches" or as a failure.
- **Exit 1, `complete` is false.** The corpus could not be safely acquired
  or validated -- unavailable or incomplete, not "nothing found." Read the
  diagnostics for what failed before drawing any conclusion from the
  (empty) results.
- **Exit 2.** A usage error in the invocation itself -- fix the command
  first; it says nothing about the corpus at all.

## Falling back safely

When recall is incomplete or unavailable, fall back to reading source files
directly -- but only within the scope this task is already authorized to
read. Never treat a path recall itself refused (an outside path, a symlink
it would not follow) as something safe to open manually as a workaround;
ordinary reading of files already inside the task's authorized scope remains
fine and is the normal fallback.
