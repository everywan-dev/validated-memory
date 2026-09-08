# Migrating to v2

## Resolve memory identity conflicts before switching CI

Two 1.x migration warnings become errors in 2.0.0: a declared memory `name`
that differs from its filename, and duplicate memory filenames across
subdirectories. `lint` and `status` now gate on them with exit 1. Wikilinks still
resolve by declared `name`; the finding shape, usage exit 2 and unrelated
warnings are unchanged.

Run `python3 -P -m validated_memory lint` with the installed package (or prefix
with `PYTHONPATH=<plugin root>` from a checkout). Repair a divergent `name` to
match the filename, not the reverse. For a duplicate filename, choose a unique
filename for one entry and update its name, index and references coherently.
Preserve the recorded facts and supersession history; an identity repair is not
permission to delete outdated knowledge. Re-run lint before upgrading gating
CLI, Action and CI references to v2.

## Opt into interaction separately

Existing canonical views stay script-free. To create the optional app, run
`python3 -P -m validated_memory init --view --app`. `--app` without `--view`
is a usage error before writes. Existing selected files are kept; `render`
refreshes them. An existing app activates its refresh and requires canonical
knowledge.html, even if that file was removed. It does not activate missing
memory.html under `--only-existing`. Delete the app to deactivate it.

Decide whether to version or ignore knowledge-app.html just as for the other
views; the installer never makes that repository-policy decision. No new
configuration field, browser storage or third-party runtime is required.

## Rationale compatibility is older than v2

The optional base `rationale` field and quoted-source enforcement first shipped
in v1.5.0. The original design's claim that every 1.x reader rejects it is no
longer true. Readers before v1.5.0 do reject it; update all consumers before
writing such units. No backfill or inferred rationale is required.

An older adopter extension already declaring `rationale` collides with the base
field. Rename that extension key both in its schema and in every affected unit,
and record the schema narrowing in extension.version. This explicit mechanical
schema migration does not authorize changing historical claims. The CLI does
not perform it automatically; consult the [curated contract](reference/curated-knowledge.md).

## Pin the intended major

Prefer a full release commit SHA, then an immutable version tag. `v2` is the
moving convenience channel for 2.x; `v1` remains on 1.x and never advances to
this breaking release. CLI, hooks and the Action must use the intended checkout;
the three manifest/package versions agree at every published release commit.
