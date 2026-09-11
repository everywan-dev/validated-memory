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
  before", or a direct ask to run `recall`. An explicit request for checked use follows
  the additional opt-in path below.
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

## Select the requested operation

Ordinary lookup uses recall below. Checked use requires an explicit request or an
explicit checked-use requirement in the adopter's instructions; opting into
lookup alone does not enable registrations or persistent checked-use records.
For checked use, read [the consultation reference](../../docs/reference/consultation.md)
and follow the additional path at the end of this skill. Preserve established
project scope and authorization; ask only about unresolved material judgments.

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

## Explicit checked-use path

Run only when checked use itself is opted into. Resolve the plugin as above:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m validated_memory consultation --store STORE OPERATION
```

The store is explicit, local and outside registered adopter roots. Register only
the intended projects. Supply semantic declarations through CLI arguments:
qualified unit identities, support paths, authority/source label, scope,
dependencies, actor and reason. The CLI generates mechanical hashes and storage
JSON; do not write them yourself. Actor/reason records an assertion, not human
approval or authenticated identity. Do not attribute an agent review to a human.

1. Inspect relevant source content and limitations. Author the consumer conclusion
   as ordinary knowledge-unit Markdown and validate it through the existing
   workflow. Bind its exact revision with all declared support and references.
   Canonical authoring is separate: a failed use can leave an authored conclusion.
2. After all authoring and binding changes, `read` the **consumer conclusion as
   root**, with the requested scope. Consume exactly two JSON lines and check
   exit 0: the first has status `inspected`, complete `content`, `root`, `scope`
   and `limitation`, with no `id`; the second has status `receipt recorded` and
   the committed receipt `id`. Parse each line separately using JSON, as shown
   in the reference's capture helper. Inspect the full returned content. One
   inspection line followed by a refusal is not a receipt. Never use a source-root
   receipt for a different consumer or carry an earlier receipt past authoring.
3. Pass that returned handle to `record-use ALIAS:ID --receipt HANDLE`. Report
   `checked-use recorded`, with the exact consumer and scope. Reading alone does
   not create checked use; no operation establishes understanding or entailment.
4. Before claiming current eligibility in a later session, use `check-use HANDLE`.
   It is read-only and current only at invocation. `show HANDLE` is historical
   inspection and cannot replace a current check. Unavailable means unavailable,
   never current. Direct source reading remains possible within authorized scope,
   but does not satisfy a refused checked-use operation.

On support drift or rename, inspect retained old bytes and proposed new inputs.
For unchanged canonical claim bytes, `review-support` names the current binding
via `--prior` and repeats the complete intended declaration. A renamed old path
can be replaced even when missing; another active binding still naming it remains
blocking. If meaning changed, author and bind a canonical successor instead.
Keep predecessors. Update consumer references with attributed review if its claim
is unchanged, or author its own successor when meaning changes.

An applicable conflict needs an explicit attributed choice. Route unresolved
material choices to the appropriate human. Stale candidates require a retained
conflict successor via `--prior`, with `--replacement OLD=NEW` for changed
same-project successor identities, then a fresh choice; old choices do not carry
forward. Before moving a project, obtain a `checkpoint`; explicit `relocate`
requires that latest checkpoint, unavailable old root and identical content.
Follow the reference for recovery and exact command options.

Finish maintenance before acquiring the final consumer again. All enrolled
semantic inputs and membership affect the current snapshot, including unrelated
changes. Name the changed paths and resulting reacquisition honestly; do not
silently narrow the check or claim saved context. Bound reads with `--max-bytes`;
never truncate evidence into a usable receipt. Track returned bytes and repeated
acquisitions when assessing usefulness. Checked consultation does not probe
anchors, rank evidence states, discover undeclared conflicts or gate arbitrary
responses. Existing hooks remain fail-open.

## Optional challenge and incorporation history

When the requested work includes contributions or correction effects, consult the
[incorporation reference](../../docs/reference/incorporation.md). Its lifecycle and
schema 2 are an unreleased source-tree addition; published 2.2.0 does not supply
them. An existing schema 1 store requires explicit `upgrade`, never an implicit
migration during ordinary lookup. Preserve the task's authorized participation.

Keep proposal acceptance, observed canonical incorporation, challenge resolution,
consumer address and publication reflection separate. `inspect` emits complete
historical material before its second-line inspection handle; consume both lines
and check exit 0. Retained content may be old and is not a current evidence check.
A later binding inspection permits an unchanged-evidence resolution without
inventing a support-review revision.

A matching accepted challenge stales prior affected receipts through its review
frontier, even after resolution or reversal; reacquire the consumer. A current
unaffected replacement/removal use can retain its old ID: `address` validates it
now, and does not require artificially inserting a new use. The explicit
`dependency-removed` mode requires an attributed explanation; disappearance alone
never addresses historical effects.

Use read-only `reconcile` to enumerate declared historical effects and separate
current observations. A complete report exits 0 even when rows are unresolved or
unavailable. Do not turn that exit code into an all-clear. Report the affected use,
address and tracked publication/reflection separately, preserving unknown
undeclared dependencies and unobserved external distribution as limitations.
