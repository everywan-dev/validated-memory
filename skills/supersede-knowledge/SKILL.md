---
name: supersede-knowledge
description: Use when a curated-knowledge unit turns out to be wrong, outdated, or replaced by better evidence. Triggers on requests like "correct kb-0003", "update this finding, it changed", "this knowledge unit is no longer true", or "supersede X with Y". Never use this to justify editing a unit's frontmatter or body in place.
---

# Supersede a curated-knowledge unit

Correcting curated knowledge is never an edit. It is always a new unit.

## The rule

1. Write a **new** unit file with its own new `id`, never reusing the old one.
2. Its frontmatter declares `supersedes: [<old-id>]` (a list -- one unit may
   supersede several at once, many-to-one).
3. The superseded unit is **never** edited and **never** deleted. Its file,
   its `evidence`, its `anchors` -- all of it stays exactly as written.
   History is not rewritten.
4. If the correction is itself a chain (kb-0001 was already superseded by
   kb-0002, and kb-0002 also needs correcting), the new unit supersedes the
   *latest* unit in the chain, not the original.

## Why

`derive` computes each unit's effective state from `supersedes` across the
whole validated set -- it is never stored on the unit itself. A superseded
unit still appears in `knowledge-index.md`, marked `superseded by <ids>`,
never omitted and never mutated. Editing a unit in place would destroy the
record of what was believed and when; superseding preserves it.

## Steps

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m validated_memory validate
```

Validate the whole `knowledge/` directory (not just the new file) so the
`supersedes` reference resolves against the unit it points at.

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m validated_memory derive
```

Re-derive `knowledge-index.md` afterwards, so its `state` column reflects
the new supersession. If the project gates on a versioned index
(`derive --check` in CI), re-deriving is required before that gate passes
again.

If the new unit carries its own anchors, its freshness verdict starts at
`unknown` until `probe-freshness` runs against it -- the superseded unit
keeps whatever verdict it last had recorded, since `probe` only probes
active units.

## Optional challenge and correction effects

For explicitly requested retained correction review, follow
[incorporation](../../docs/reference/incorporation.md). It is available since
2.3.0, unavailable in version 2.2.0; an existing schema 1 store
requires explicit `upgrade`. This does not replace the normal authoring steps.

A factual or policy `challenge` records a question about an exact retained bound
claim without fabricating its successor. Inspect before an attributed disposition.
An accepted challenge gates matching current consultation, not ordinary authoring,
and does not itself prove the claim false. If evidence already supports unchanged
meaning, inspect the exact current binding after acceptance and `resolve --review`;
the resolution supplies the fresh review, so do not manufacture a support change.

When meaning changes, propose and incorporate the actual canonical successor,
retaining predecessor bytes. Inspect its current binding after challenge acceptance
and resolve through that incorporation. Then author/review all consumer changes
before final acquisition. Explicitly `address` each affected historical use with
its current replacement or intentional `--mode dependency-removed` use. Omission
of a dependency is not an address, and a current use may keep its deduplicated ID.

Track declared publications before changing them and separately `reflect` their
changed bytes with the matching address. Regenerate derived artifacts through
their usual generator. `reconcile` keeps historical effects visible while showing
current, stale or unavailable observations; review closure never silently rewrites
consumers, publications or evidence history.

## Successors with retained foreign origins

[Portable transfer](../../docs/reference/transfer.md) is available since 2.3.0 with
schema 3, unavailable in version 2.2.0. Its origin obligations follow
explicit local supersedes ancestry, including unbound intermediate canonical
units. Authoring a successor cannot silently shed a retained origin.

Inspect newest known origin history and every inherited path before choosing a
successor's declaration. Retaining successors require the appropriate explicit
origin link and post-link local proposal inspection/acceptance/incorporation.
If the successor is independently justified, use detach-transfer on its proposal
with the inherited link and an attributed removal reason; consume complete source
material/corrections and both output lines. This disposition applies only to a
genuine canonical successor, never an identical local revision edited in place.

At a merge, an independent sibling does not cancel a retaining sibling. Discharge
an inherited origin across the merged successor only through the explicit review
covering all inherited paths. Retain original claims, links and source history.
Mapped source predecessors are active installed retaining counterparts before
candidate insertion; do not reactivate retired predecessors merely to map them.
Origin-only preserves foreign predecessor history when no active retaining local
counterpart remains. An attributed independence assertion does not prove absence
of semantic dependence; the contract does not detect unrelated manual copies.

When a source successor replaces A1 with A2, map the exact source predecessor to
the active local retaining predecessor that the new local candidate supersedes.
This explicit correspondence replaces the inherited A1 obligation on mapped paths
with A2, while retaining A1 bytes/history. Do not require retired A1 to become
current again or mislabel this correction as independence. Unmapped retaining
siblings and unrelated origins remain obligations; origin-only cannot discharge
an existing local retaining path.
