# `status` gates structural consistency and only reports freshness

An adopter wants one CI-friendly answer to "is this project sound?". But the
project records two different kinds of soundness: whether the repository's
knowledge is *structurally consistent* (frontmatter valid, links resolving,
index matching its source), and whether it is *current* (no anchor drifted).
The first is entirely the adopter's to keep; the second changes when the
world changes, without anyone touching the repository. A command that gates
on both at once turns every upstream movement into a red build the adopter
did not cause and cannot fix by reverting.

`status` is that one command, and it draws the line explicitly: structural
findings gate, freshness is reported. Its exit code is the worst of the gates
it ran; verdict counts — per verdict, across **active units only**, since a
superseded unit's verdicts describe knowledge already retired — are
information unless the adopter opts in.

## Considered options

- **Compose the three existing subcommands** (`validate`, `lint`,
  `derive --check`) by shelling out and merging exit codes — rejected.
  `validate` runs as part of `derive --check`'s gate already, so the
  composition validates the same source twice and reads the verdict log
  twice; and the composed output is three formats stapled together.
  Instead `status` computes one shared `ProjectCheckResult` internally and
  formats it per gate. The subcommands keep their exact behavior and stay
  the public seam — the structural gates enforce no rule the subcommands do
  not already enforce. The only rules `status` adds of its own are the
  freshness and age gates, and those exist solely behind explicit opt-in
  flags: without them, `status` can never fail a project the three
  subcommands would pass.
- **Read a missing `knowledge-index.md` as "the adopter chose not to
  version it"** — rejected. A deleted index and a deliberately unversioned
  index are indistinguishable on disk, so absence can never be read as
  policy. A missing index is an ERROR, exactly as `derive --check` defines
  it; the adopter who does not version the index says so explicitly with
  `--skip-index`.
- **Make skip-index a `validated-memory.md` field instead of a flag** —
  rejected. Older plugin versions reject unknown configuration fields, so
  the day the adopter writes the field, every one of their sessions running
  an older plugin breaks. A CLI flag is scoped to the invocation that
  understands it.
- **Gate on `drifted` by default** — rejected. Verdicts are data, not
  gates (the ternary domain is fail-explicit precisely so that "could not
  answer" is never conflated with "wrong"). Enforcement is opt-in and
  named: `--fail-on drifted`, `--fail-on unknown`, repeatable.
- **Have `status` refresh verdicts by running `probe` first** — rejected.
  `probe` has side effects (it appends to the log) and may touch the
  network; a status command that mutates the thing it reports on is not a
  status command, and CI should decide separately when probing happens.

## Consequences

- One new read-only subcommand; exit `0`/`1`/`2` per the repository
  convention, `1` only from the gates that ran.
- Freshness enforcement is a per-adopter, per-pipeline decision, visible in
  the CI configuration line that chose it.
- `status` is what the reusable CI action runs once it exists.
