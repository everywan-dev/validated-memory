# The adopter versions the verdict log alongside the index

The derived `knowledge-index.md` bakes in a verdict column read from the
service view of `verdicts.jsonl`. That creates a dependency the repository
must resolve on a clean checkout: if CI checks the index (`derive --check`)
but the log is not in the repository, every anchor collapses to `unknown`,
the re-derived content disagrees with the committed index, and the check
fails for a reason no file in the checkout can explain.

Policy for v1, documented in the adoption guide: **version `verdicts.jsonl`
alongside `knowledge-index.md`**. The log is append-only history — it is the
audit trail of what was probed and what came back — so versioning it is not
a workaround but where an audit trail belongs.

## Considered options

- **Version the log** — chosen. `derive --check` becomes deterministic on a
  clean checkout, and the freshness history travels with the knowledge it
  describes.
- **Leave the log unversioned and accept `unknown` in CI** — rejected. The
  committed index would then never match a clean-checkout re-derivation
  after the first probe, making `derive --check` unusable as a CI gate,
  which is the gate's whole purpose.
- **Split a versionable structural index from an operational freshness
  report** — not attempted. It removes the dependency instead of resolving
  it, but costs a second derived artifact, a second `--check` mode, and a
  migration for every adopter. Noted as a possible v2 if the single-file
  coupling starts to hurt; deciding it now would be speculation.

## Consequences

- Everything a record carries lands in the repository: the anchor's payload
  **and the probe's `detail` output**. The existing rule gains teeth and
  widens: anchors must not carry secrets, and a probe must not emit them in
  `detail` either — a diagnostic string containing a token becomes
  versioned history the moment the log is committed.
- The log only grows. At v1 scale (one JSON line per anchor per probe run)
  this is acceptable; if it becomes a burden, that is the v2 split's
  problem, not a reason to rewrite history.
- The log and the index are one coupled artifact: a CI pipeline that probes
  must re-derive and commit **both together**. Committing only the appended
  log leaves an index that no longer matches it, and the next clean
  checkout's `derive --check` fails on exactly that mismatch; committing
  neither hides the new verdicts from every other checkout.
