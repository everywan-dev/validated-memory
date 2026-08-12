# validated-memory

A Claude Code plugin that packages a validated-memory method for agent projects
as a portable component: skills that make the convention invocable, a single CLI
with the enforcement modules (agent-memory lint, contract validator, index
deriver, freshness probes with a ternary verdict), and a bootstrap scaffold.

Adopter projects keep only Markdown data and configuration; all code lives in
the plugin, so fixes reach every adopter on update. The data stays readable
without the plugin installed.

## Status

Under construction (v1). `validate` enforces the base contract and the
adopter's declared extension; `lint` enforces the agent-memory layer;
`derive` re-derives the knowledge index, with a `--check` gate; `init` and
`probe` are still stubs.

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

### `lint`

```
python3 -m validated_memory lint [PATH]
```

Lints the agent-memory layer: every `*.md` file found under PATH, recursively,
except the index `MEMORY.md` itself. With no PATH it reads `memory/` relative
to the working directory. A one-line summary goes to stdout; findings go to
stderr in the same shape `validate` uses:

```
SEVERITY: <location>: <field>: <message>
SEVERITY: <location>:<line>: <field>: <message>    # parse errors only
```

`lint` resolves wikilinks and the supersession convention against the whole
memory set, so a missing `MEMORY.md`, a missing memory directory, or an
explicit PATH that does not exist each stop the run before any file is read.

### `validate`

```
python3 -m validated_memory validate [PATH]
```

Validates every `*.md` unit found under PATH, recursively; PATH may also be a
single unit file. With no PATH it reads `knowledge/` relative to the working
directory. A one-line summary goes to stdout; findings go to stderr as

```
SEVERITY: <unit>: <field>: <message>
SEVERITY: <unit>:<line>: <field>: <message>    # parse errors only
```

A contract rule speaks about the unit as a whole, so it reports no line. Only
the parser reports one, because only the parser knows where it stopped.

Supersession resolves against the validated set: validate the whole knowledge
directory, not a single file, or a `supersedes` entry pointing at a unit you
left out is reported as missing.

### `derive`

```
python3 -m validated_memory derive [PATH] [--check]
```

Re-derives the curated-knowledge index from the units under PATH, resolved
exactly like `validate`'s PATH (default `knowledge/`, single unit file or a
directory, same errors on a missing path). Deriving requires a valid source:
`derive` first runs the same validation as `validate` (base contract plus the
adopter's declared extension). An ERROR finding reports the findings to
stderr, in `validate`'s format, and stops -- nothing is written or checked.
A WARNING does not block.

The index is written to `knowledge-index.md` in the current working
directory, never inside `knowledge/`: anything ending in `.md` there is read
as a unit (see "Keep the schema outside the curated-knowledge directory"
above -- the same reason applies to the index).

```markdown
# Knowledge index

Derived: 2026-08-12T10:00:00Z
Basis: 2 unit(s) under knowledge/

| id | state | evidence | verdict |
|----|-------|----------|---------|
| kb-0001 | superseded by kb-0002 | measured | unknown |
| kb-0002 | active | hypothesis | unknown |
```

- `Derived:` is the UTC ISO-8601 timestamp of the derivation run.
- `Basis:` is the recount basis: how many units, under which path.
- Rows are sorted by `id`. Nothing is omitted: a superseded unit is still
  listed, marked, never mutated.
- **state** is computed, never stored on the unit: `active`, or
  `superseded by <ids>` naming every unit that lists this one in its own
  `supersedes` (many-to-one), sorted and comma-separated.
- **verdict** is present from the first derived index and is always `unknown`
  in this version -- fail-explicit in the absence of freshness probes, which
  land in a later version.

`--check` recalculates the index in memory instead of writing it, and
compares it against the `knowledge-index.md` already on disk, line by line.
The `Derived:` line must be there, but **its timestamp is ignored** -- it
changes on every run, so what has to match is the rest: `Basis:` and the
table. A missing index is an ERROR pointing at running `derive` first. Any
divergence -- `Basis:`, a row, a missing or extra line -- is an ERROR naming
the first line that does not match, numbered as on disk. `--check` never
writes. A match exits clean with a summary. This makes `derive --check` a
local or CI gate for adopters who version the derived index: hand-editing it,
or letting it drift from the units, fails the check.

Exit codes: `0` clean, or WARNING-only validation findings; `1` an ERROR
finding (source validation, or a `--check` mismatch); `2` a usage error.

## Agent memory

The agent-memory layer is one Markdown file per fact (`memory/*.md`), plus a
one-line-per-entry index at `memory/MEMORY.md`. `lint` enforces four things.

**Frontmatter.** Every memory file's frontmatter carries the shape the Claude
Code harness gives it -- `lint` does not redefine it, only requires it be
complete:

```yaml
name: short-kebab-slug        # required; a non-empty string
description: one-line summary # required; a non-empty string
metadata:
  type: user                  # required; user | project | feedback | reference
```

`name`, `description` or `metadata.type` missing, or `metadata.type` outside
its domain, is an ERROR. Additional keys the harness may add are tolerated
without being checked. A `name` must be unique across the memory set: a
duplicate is an ERROR, since wikilinks resolve by `name` and a duplicate
makes that resolution ambiguous.

**Index.** `MEMORY.md`, at the root of the memory directory, lists one entry
per fact as a Markdown bullet with a link to the file, relative to the
directory:

```markdown
- [Coffee preference](coffee-preference.md) — oat milk in coffee
```

Only bullet lines shaped `- [Title](file.md)` count as entries; headers and
prose are ignored. The index and the memory files must agree in both
directions: an entry whose file does not exist, and a memory file with no
entry in the index, are each an ERROR. A missing `MEMORY.md` stops the run,
pointing at `validated-memory init`.

**Wikilinks.** A `[[name]]` reference in `description` or in a file's body
names another memory by its `name`. A wikilink whose target does not exist is
a WARNING -- it marks something pending to write, and does not gate.

**Supersession.** A memory is marked superseded by rewriting its
`description` to start with the literal prefix `superseded by ` followed by a
wikilink, e.g. `superseded by [[coffee-preference]]`. Well formed -- the
wikilink resolves to a different memory that exists -- it is recognized and
raises no finding. Malformed is an ERROR: the prefix with no parseable
wikilink after it, a wikilink pointing at a memory that does not exist, or a
wikilink pointing at the memory itself.

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

An `id` must be unique and stable. `validate` enforces form and uniqueness
across the validated set; stability is a convention no single run can check,
since nothing records what the id was before. Reuse of an id across time is
caught by supersession, not by the validator: correct a unit by writing a new
one that supersedes it, never by editing its id.

## Adopter configuration

The adopter's configuration is a file named `validated-memory.md`, read from
the working directory. This version declares three fields, each optional:

```yaml
extension:                         # the declared extension (next section)
  schema: knowledge-extension.md
  version: "1"
id_prefix: kb-                     # the id scheme the adopter's units follow
probes:                            # probe registry: kind -> command
  git_ref: run-git-ref-probe
```

`id_prefix` records the id scheme for humans and skills; `validate` does not
enforce it. `probes` maps an anchor `kind` to the command that probes it (the
`probe` subcommand consumes it). An unknown configuration field, an empty
`id_prefix`, or a probe entry whose command is not a non-empty string each
stop every run with an ERROR naming the configuration file: the configuration
is one document, and a malformed key gates even the subcommands that do not
consume it.

## Declared extension

An adopter extends the contract without forking it. The `extension` block of
the configuration names a versioned schema:

```yaml
extension:
  schema: knowledge-extension.md   # path, relative to the configuration file
  version: "1"                     # the schema version this project is on
```

The schema declares the fields the adopter's units may carry:

```yaml
fields:
  - name: domain
    type: enum                     # closed domain: 'values' is required
    values:
      - network
      - storage
  - name: owner
    type: string                   # any non-empty scalar
```

A declared field carrying a valid value passes. A value outside a closed
domain, and a field neither the base contract nor the schema declares, are
ERRORs naming the unit and the field. A declared field is permitted, not
required: v1 has no way to demand one.

Keep the schema outside the curated-knowledge directory. Anything ending in
`.md` under `knowledge/` is read as a unit, and a schema is not one.

With no configuration file, or with one that declares no `extension` block,
only the base contract applies. With an extension declared, loading is
fail-loud: an unreadable or malformed configuration, a schema that does not
exist, an unknown field type, an enum with no values, a field that redeclares a
base contract field -- each stops the run with an ERROR against the offending
document. Nothing here degrades to base-contract-only validation, because an
extension ignored in silence validates nothing while appearing to pass.

### Versioning the schema

`version` records which schema version the project is on. Adding a field, or
adding a value to a closed domain, is additive and does not bump it. Removing
or narrowing anything does. Units already written are never rewritten to match
a newer schema: correct a unit by writing a new one that supersedes it.

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
