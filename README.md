# validated-memory

A Claude Code plugin that packages a validated-memory method for agent projects
as a portable component: skills that make the convention invocable, a single CLI
with the enforcement modules (agent-memory lint, contract validator, index
deriver, freshness probes with a ternary verdict), and a bootstrap scaffold.

Adopter projects keep only Markdown data and configuration; all code lives in
the plugin, so fixes reach every adopter on update. The data stays readable
without the plugin installed.

## Status

Under construction (v1). `validate` enforces the base contract; `init`, `lint`,
`derive` and `probe` are still stubs.

## Layers

- **Agent memory** — one Markdown file per fact plus a one-line-per-entry index,
  versioned inside the adopter repo. Supersession is exercised by rewriting the
  entry's `description`; `lint` enforces the convention.
- **Curated knowledge** — Markdown units with a base contract: identity,
  evidence state (`measured | verifiable | hypothesis`), supersession without
  deletion (`supersedes`, many-to-one), optional anchors separated from
  provenance. `validate` enforces the contract plus the adopter's declared
  extension; `derive` re-derives indexes; `probe` runs freshness probes and
  records ternary verdicts (`current | drifted | unknown`, fail-explicit).

## CLI

```
python3 -m validated_memory <command>
```

Commands: `init`, `lint`, `validate`, `derive`, `probe`.

Exit codes: `0` = clean run or WARNING-only findings (does not gate);
non-zero = ERROR (gates).

### `validate`

```
python3 -m validated_memory validate [PATH ...]
```

Validates every `*.md` unit found under each PATH, recursively. With no PATH it
reads `knowledge/` relative to the working directory. Findings go to stderr as
`SEVERITY: <unit>:<line>: <field>: <message>`; a one-line summary goes to
stdout.

Supersession resolves against the validated set: validate the whole knowledge
directory, not a single file, or a `supersedes` entry pointing at a unit you
left out is reported as missing.

## Base contract

Every curated-knowledge unit is a Markdown file whose frontmatter carries:

```yaml
id: <stable-unique-id>          # required; letters, digits, '.', '_', '-'
evidence: measured              # required; measured | verifiable | hypothesis
supersedes: []                  # optional; ids this unit supersedes (many-to-one)
anchors:                        # optional; without anchors a unit cannot expire
  - system: <system-name>       # complete envelope: all four fields required
    kind: git_ref               # probe discriminator; no whitespace
    captured_at: 2026-08-11T10:00:00Z   # ISO-8601 date or timestamp
    payload: {}                 # mapping; interpreted by the probe, not here
provenance: []                  # optional; where the native artifact lives
```

An unknown top-level field is an ERROR: adopter-specific fields belong to a
declared extension. A unit with no anchors is a WARNING, not an ERROR.

### Frontmatter subset

The frontmatter parser is not a YAML parser. It accepts block mappings, block
lists, nested blocks, empty inline collections (`[]`, `{}`) and plain or quoted
scalars. Everything else -- tabs, block scalars (`|`, `>`), anchors and aliases,
non-empty inline collections, duplicate keys, a key with no value -- is an
ERROR. Scalars are always strings; no type is inferred. A unit whose
frontmatter fails to parse is never validated on a best-effort basis: the parse
error is the only finding reported for it.

## Development

Runtime code is Python 3, standard library only. pytest is the only
development dependency.

```
python3 -m pytest
```

Tests are end-to-end only: they invoke the CLI as a subprocess over fixture
adopter trees and assert on exit codes, output, and produced files. Tests never
import the package's internals.
