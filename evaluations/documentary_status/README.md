# P3 documentary-status fixture (Stage 1)

This directory contains a public, synthetic, provider-free fixture for P3
task-scoped documentary status. It freezes three target families, each with
one development and one held-out variant, plus two regression cases. All
content is invented English. No family is scored for agent behavior.

| Family | Distinction |
| --- | --- |
| D1 | An unrequested adjacent mechanism is documentarily closed; only a narrower connection remains open; a dated analysis still calls it open |
| D2 | A dated analysis's historical conclusion versus the later requested-current documentary status |
| D3 | Two records disagree with no declared precedence, and an explicitly named status source is absent |
| R1 | A later condition-specific feedback entry qualifies, but does not retire, the original claim |
| R2 | A declared same-layer successor exists while the native index lists the old entry without annotation |

Held-out variants change identifiers, domain vocabulary and sentence order,
never language or the semantic distinction. Validation enforces that each
held-out variant keeps its development twin's question keys, outcome codes,
source roles and route types while sharing no fixture path other than
`memory/MEMORY.md` and no recall query.

The manifest is frozen twice. Structural validation checks shape, roles,
routes, outcome codes and safe POSIX-relative locators for every file and
source, including absent sources. After that, `runner.py` compares the
SHA-256 of the complete raw manifest bytes with a reviewed digest kept outside
the manifest. Any byte change is rejected before execution, including a
structurally valid addition. There is no regeneration command: changing the
manifest means an explicit, reviewed edit to both the manifest and the digest.

Validate the frozen shape without running anything:

```sh
python3 evaluations/documentary_status/runner.py --validate-only
```

Run the development variants and regression cases through public subprocess
interfaces:

```sh
python3 evaluations/documentary_status/runner.py --package-root . \
  --output /tmp/p3-development.json
```

`--family D1` restricts execution to named families. Held-out routes run only
with an explicit `--split held-out`. Their labels may be inspected, but their
routes are frozen and are not executed by default or by the tests.

During Stage 1, leave the held-out routes unexecuted. A later authorized
comparison can select them explicitly with:

```sh
python3 evaluations/documentary_status/runner.py --split held-out \
  --package-root . --output /tmp/p3-held-out.json
```

## Report and failures

Without `--output`, the runner writes the JSON report to standard output. Its
top-level `selection` records the requested split, `executed_splits` records
the splits actually run, and `variants` contains the per-variant evidence.
`scored_semantics: false` and `agent_behavior_observed: false` are explicit
limits, not scores. The `labels` object explains the route, acquisition and
semantic planes used elsewhere in the report.

Exit `0` means every selected route and source-availability declaration
matched and the fixture bytes remained unchanged. Exit `1` means validation,
route integrity, an expectation, source availability or byte preservation
failed; the JSON report retains structured evidence when execution reached a
variant. Exit `2` is a usage error, including a selection with no variants.
Raw subprocess stdout and stderr are evidence, not semantic judgments.

The fixture contains only invented public data. The runner makes no provider
or external-source request: it invokes the selected local checkout in an
isolated temporary adopter and deletes that adopter afterward. Reports retain
the subprocess command output verbatim, so they should still be inspected
before being shared if the tested checkout has been locally modified to emit
environment-specific information.

## What a run observes

Each variant starts as its own temporary adopter. The runner invokes
`python3 -P -m validated_memory init` with an explicit `PYTHONPATH` and
`PYTHONDONTWRITEBYTECODE=1`, writes only that variant's declared files, then
hashes every adopter byte. Before writing, and again before reading a fixture,
it refuses any locator with an existing symlink component, or whose unresolved
or resolved target leaves the temporary adopter. That is reported as a
structured failure. This deterministic check does not protect against
concurrent writers. It then replays the declared routes:

- **recall**: `recall QUERY --format json --limit 20 --max-bytes 65536`,
  optionally with `--include-superseded`. The runner checks the envelope and
  selection counts, then compares the declared returned paths, states,
  successors and redirects. Any omission is a mismatch, so declared inclusion
  never depends on the ranking window. An exit-1 result is recorded as
  unavailable, never as an empty search.
- **native-index**: the exact bytes of `memory/MEMORY.md`, as a host would
  read them. Declared lines must appear verbatim and link to an existing
  fixture. The `presentation` label comes from the manifest; nothing parses
  index prose.
- **lint** and **validate**: exit code and the summary line's counts.

Every command keeps its exact stdout, stderr, exit and duration. After all
routes run, the runner hashes the adopter again and compares every declared
file with its manifest bytes. Any added, removed or changed byte fails the run.

## Label planes

The manifest keeps four kinds of label separate:

- **Route labels** are the declared recall, index, lint and validate
  expectations. They record what an interface delivers.
- **Acquisition labels** are source `role`, `availability`, `required_for`
  and exact `passages`. A run only checks that complete originals are present
  in the fixture, or absent when declared absent. It does not observe what an
  agent reads.
- **Semantic labels** are each question's `correct` and `harmful` outcome
  codes. They are frozen for a later adjudicated host baseline and are never
  scored here (`scored_semantics: false`).
- **Presentation labels** on index lines name a known reproduced gap. They
  are not derived from text.

A "documentary status source" is selected by the manifest's declared role and
the project's explicit pointers, never by recency. No code here decides
semantic truth, precedence or authority. D3's later-dated record is labelled
harmful to choose for that reason alone.

## What cannot be claimed

A passing run shows only that the frozen fixtures are valid, and that the
current public interfaces deliver the declared candidates, redirects and index
lines without changing input bytes. It does not show agent acquisition,
correct interpretation, semantic enforcement, live or external freshness,
causal use of a delivered candidate, general reliability or behavior outside
these synthetic cases. Documentary status is never external freshness.
