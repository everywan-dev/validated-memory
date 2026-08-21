# Curated knowledge

The contract every curated-knowledge unit meets, and how an adopter
configures and extends it: [Base contract](#base-contract) ·
[Adopter configuration](#adopter-configuration) ·
[Declared extension](#declared-extension) ·
[Frontmatter subset](#frontmatter-subset). The subcommands that enforce it
are documented in the [CLI reference](cli.md).

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

The three evidence states say how a claim is backed, and never mix planes:

- **`measured`** -- directly observed or computed by executing something,
  with a way to re-check it.
- **`verifiable`** -- not directly measured, but checkable without executing,
  by someone who follows the provenance.
- **`hypothesis`** -- a claim not yet checked. A unit is never promoted out
  of it by conviction: promotion is a new unit with better evidence that
  supersedes this one.

An unknown top-level field is an ERROR: adopter-specific fields belong to a
declared extension. A unit with no anchors is a WARNING, not an ERROR.

An `id` must be unique and stable. `validate` enforces form and uniqueness
across the validated set; stability is a convention no single run can check,
since nothing records what the id was before. Reuse of an id across time is
caught by supersession, not by the validator: correct a unit by writing a new
one that supersedes it, never by editing its id.

**A supersession chain has to end.** Supersession retires a fact by naming
what replaces it, so a chain that closes on itself -- `kb-0001` superseding
`kb-0002` while `kb-0002` supersedes `kb-0001`, at any length -- leaves every
unit in it superseded and none live. An ERROR, not a note, because the
consequence is silent: the whole group drops out of the index's active view
**and `probe` stops probing it**, since only active units are probed. Nothing
was deleted, yet the knowledge stops being checked, which is the failure this
contract exists to prevent. There is no migration case to protect -- a cycle
is never intentional and never correct.

```
ERROR: knowledge/kb-0001.md: supersedes: supersession cycle: kb-0001 ->
kb-0002 -> kb-0001; every unit in it is superseded, so none is live and none
is probed
```

A unit superseding itself is a cycle of one and has its own rule; it is not
also reported here.

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
[`probe`](cli.md#probe) subcommand consumes it). An unknown configuration field, an empty
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

### Where the schema lives

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
