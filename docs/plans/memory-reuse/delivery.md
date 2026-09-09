# Memory retrieval delivery preparation

## Accepted scope

Add the `recall` command and opt-in `consult-project-memory` skill. Retrieval
searches original memory entries and knowledge units, reports distinct layer
semantics, redirects historical matches to active successors, and preserves
source pointers, declared evidence and recorded freshness limitations.
Read-only acquisition and output have explicit ceilings. Invalid or unsafe
selected input yields an explicit bounded failure, not usable partial evidence.
No cache, embeddings, automatic probe, mandatory gate or hook expansion is added.

The [usefulness disposition](usefulness-report.md) accepts the capability but
defers broad consultation-first recommendation. The implemented workflow is
opt-in. Neither correctness tests nor the synthetic comparison demonstrate
long-project productivity or savings.

## Compatibility

- Nine CLI subcommands, eight plugin skills; existing commands retain their
  behavior and the exit-code contract remains 0 / 1 / 2.
- No base entity-schema change, migration, persistent index format change,
  version change or new dependency. Python 3.11+ and the standard library remain
  the runtime requirements; pytest remains the sole development dependency.
- Retrieval's safe-acquisition platform requirements and documented lexical
  limitations are explicit in [the reference](../../reference/recall.md).
- Existing adopter instruction files are not rewritten automatically. The
  managed-block-only upgrade shows a diff and requires confirmation, preserves
  outside bytes, and stops before adoption/import questionnaires.
- Clone-local adoption data and evaluation/session state remain ignored. No
  knowledge unit or memory entry was deleted to implement this feature.

## Acceptance evidence

Architect specification preceded programmer implementation and subprocess
tests. Two independent review rounds were followed by terminal engineering;
there was no reset or fourth correction round. The engineer's changes were
independently inspected by the architect, who accepted the corrected candidate
after 781 full-suite tests passed. After final workflow/disposition changes,
the architect's complete suite passed **782 tests in 85.85 seconds**; the final
documentation-only acceptance record does not change the tested product.
Scale evidence and independent usefulness scoring
are linked from the [plan](../2026-09-09-memory-reuse.md).

## Prepared change description

Suggested Conventional Commit subject:

`feat(recall): add bounded retrieval and opt-in project consultation`

Suggested release-note text:

> Add read-only `recall` search and map navigation across agent memory and curated
> knowledge, including supersession-aware discovery and explicit coverage and
> output limits. Add an opt-in consultation skill and a confirmed managed-block
> upgrade path. Retrieval relevance does not establish truth or applicability;
> the initial bounded comparison did not demonstrate a workflow benefit.

This is local delivery preparation on `feature/memory-reuse-plan`. No commit,
push, version bump, tag or release was performed. A release requires its own
authorized scope and the full CONTRIBUTING procedure.
