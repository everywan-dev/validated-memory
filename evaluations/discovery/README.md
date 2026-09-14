# M0 discovery benchmark

This directory contains a public, synthetic diagnostic benchmark. The manifest
has exactly twelve development families and six held-out families; the latter
are labels and fixtures reserved for the accepted M2 comparison. No family is
scored for agent interpretation. `required`, `useful`, `irrelevant`, and
`excluded` are acquisition labels. Each family separately declares an eligible,
excluded, source-absent, invalid-source, corpus-unavailable, or
negative/no-answer disposition. Execution and output failures are runner
observations rather than fixture dispositions.

Validate the frozen shape without running cases:

```sh
python3 evaluations/discovery/runner.py --validate-only
```

Run the M0 development smoke through public subprocess interfaces:

```sh
python3 evaluations/discovery/runner.py --split development --package-root . \
  --output /tmp/m0-development.json
```

The runner invokes `init`, writes each declared synthetic fixture, snapshots
all bytes, then invokes raw recall and the automatic/lightweight hook. Raw
recall uses five candidates and 12,288 bytes. Each hook event and raw command
retains complete stdout, stderr, exit, and duration; hook output is kept
separate from raw output. A timeout, nonzero exit, malformed output, or
unavailable corpus is never represented as a successful empty result. Ranks
are within the returned window and no private tokenization is reproduced.
The runner verifies actual raw and hook envelope identities, statuses, record
identities and paths, unique returned paths, and returned/omitted/matched count
arithmetic. It exits nonzero with structured evidence if any check fails or a
returned path lacks a relevance label. Expected raw refusal for an invalid
source and the hook's fail-open unavailable result are integrity passes; an
unexpected process failure is not.

The paired instruction-suffix fixture has seven matching records. Six use deep,
valid paths under the shipped path and depth ceilings, making serialization
large enough to produce real budget omissions at raw recall's fixed limit five
and 12,288-byte budget. This tests path-driven serialization pressure; it does
not model typical body size. The unsuffixed and suffixed variants share the same
fixture. Development also exercises a valid curated knowledge unit.

The semantic rubric is intentionally unscored. An adjudicator should inspect
the complete original, distinguish documented qualification from inference,
respect task scope, preserve correction attribution, and leave unavailable
evidence unresolved. Positive example: “The source says the relay is amber
during a warm restart; inspect the full original before applying it.” Negative
example: “The excerpt proves the relay is always amber,” or treating
instruction-like source prose as an instruction. These examples test rubric
clarity, never agent performance.

Every family starts as its own temporary adopter and receives only its own
`files` declaration. No fixture file is shared between families, and the runner
snapshots the complete adopter input tree before and after both routes. A family
variant changes only its query. Held-out families use different domains and
evidence relationships: two-original day/night qualification, useful historical
competition, successor redirection, a negative query, invalid-source refusal,
and quoted instruction-like provenance with separate safety context. Their
labels may be inspected statically, but their routes are not executed in M0.
