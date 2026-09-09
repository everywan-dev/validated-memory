# Recall

Read-only, bounded search over the agent-memory and curated-knowledge layers
of an adopted project: [Invocation](#invocation) ·
[Query mode and map mode](#query-mode-and-map-mode) ·
[Matching](#matching) · [Supersession, redirects and
`--include-superseded`](#supersession-redirects-and---include-superseded) ·
[JSON envelope](#json-envelope) · [Text format](#text-format) ·
[Coverage and selection counts](#coverage-and-selection-counts) ·
[Exit codes](#exit-codes) · [Limits and platform
requirements](#limits-and-platform-requirements) ·
[Known limitations](#known-limitations).

Recall is discovery, not validation: a match tells you a record exists and
roughly why it matched, never that its claim still applies. Read the
original file before relying on anything it returns. See [ADR
0016](../adr/0016-retrieval-is-discovery-not-validation.md) for why, and the
[implementation specification](../plans/memory-reuse/specification.md) for
the exact rules this page summarizes for a user; this page is meant to stand
on its own.

## Invocation

```
python3 -P -m validated_memory recall QUERY [--layer {all,memory,knowledge}]
    [--limit N] [--max-bytes N] [--format {text,json}] [--include-superseded]
python3 -P -m validated_memory recall --map [--layer {all,memory,knowledge}]
    [--limit N] [--max-bytes N] [--format {text,json}] [--include-superseded]
```

From inside a Claude Code skill, resolve the plugin explicitly and suppress
interpreter bytecode, since recall's own contract is to create no files:

```
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${PYTHONPATH:+:$PYTHONPATH}" python3 -P -m validated_memory recall "QUERY"
```

`QUERY` and `--map` are mutually exclusive: exactly one of them is required.
The working directory is the adopter root; recall reads no configuration
outside it and accepts no other path.

| Flag | Range | Default | Meaning |
|---|---|---|---|
| `QUERY` | at least one `\w+` token, max 4,096 UTF-8 bytes | -- | search text |
| `--map` | -- | -- | list every active record with no query |
| `--layer` | `all`, `memory`, `knowledge` | `all` | which layer(s) to search |
| `--limit` | 1-100 | 8 | maximum results returned |
| `--max-bytes` | 2,048-65,536 | 12,288 | maximum serialized output size |
| `--format` | `text`, `json` | `text` | output rendering |
| `--include-superseded` | -- | off | include superseded records directly |

A value outside its range, or `QUERY` together with `--map`, is a usage
error (exit 2), reported before anything is read.

## Query mode and map mode

**Query mode** searches identity, relative path, the record's label (a
memory entry's `description`; a knowledge unit's first Markdown heading
outside fenced code blocks, or its `id` if it has none) and the full body,
and ranks matches. **Map mode** (`--map`) lists every currently active
record with no relevance ranking at all, grouped by layer then by parent
directory, then sorted by identity and path -- a directory listing of what
exists, not a search result.

## Matching

A query is split into its **tokens**: the unique, sorted results of
`re.findall(r'\w+', query.casefold())`. Matching is **token-set
intersection**, not substring search and not free text: a record matches
when at least one query token appears, as a whole word after casefolding,
in its identity, path, label or body. There is no stemming, no fuzzy
matching, and no cross-language translation -- a query in one language does
not match an equivalent claim written in another.

A query whose casefolded, whitespace-stripped text equals a record's
identity or full relative path is an **exact** match, which always ranks
first. Otherwise, results rank by the number of distinct query tokens
matched in the label, then in identity/path, then in the body -- ties break
by layer, then identity, then path, all ascending.

## Supersession, redirects and `--include-superseded`

By default, a query that matches a **superseded** record does not return
that record: it returns every **active** record reachable from it by
following successors (a chain of `supersedes`/description-wikilink edges),
labelled `redirected_from` the retired record(s) that led there. An active
endpoint can be reached this way and also match the query directly; both
reasons are reported on the one returned result. A record with no reachable
active successor yields no redirected result at all.

`--include-superseded` turns this off: it returns superseded records that
match the query **directly**, each carrying its own recorded `successors`,
and does not expand or follow redirects for them. Use it to look up a
retired record's own text and history, not to get both behaviors at once.

## JSON envelope

`--format json` prints one JSON object (schema `1`) with these top-level
fields: `schema_version`, `mode` (`query`/`map`), `layers`, `complete`,
`coverage`, `selection`, `results`, `diagnostics`, `guidance`, and, only
when some were dropped for space, `diagnostics_omitted`. The raw query text
is never echoed back.

Each result carries: `layer`, `identity`, `path`, `line`, `sha256`, `label`,
`label_truncated`, `excerpt`, `excerpt_truncated`, `state`
(`active`/`superseded`), `successors`, `redirected_from`, `match` (`exact`
plus the matched token lists for `label`/`path`/`body`), `group` (the
parent directory), `group_count` (how many candidates share that group).
A memory result adds `memory_type`; a knowledge result adds `evidence`,
`verdict` and `anchors` (each with `system`, `kind`, `verdict` and
`checked_at`).

**`checked_at` is the anchor's `recorded_at` from the last matching verdict-log
record**, read by the anchor's own key -- it is not the moment recall ran,
and it is `null` when no verdict has ever been recorded for that anchor.

`sha256` is the digest of the exact bytes recall read for that document
during this invocation (see [Known limitations](#known-limitations) for
what that snapshot does and does not promise).

## Text format

`--format text` (the default) renders the same information as `json`,
readably: one summary line, a `coverage:` line, a `selection:` line, one
block per result showing every field the JSON carries (including evidence,
verdict, anchors and their `checked_at`), then diagnostics and guidance
lines. C0 and C1 control characters and bidirectional-override characters
are rendered as `\uXXXX` escapes rather than printed raw, and paths and
excerpts are escaped the same way: a result's path is shown escaped after its
identity, and labels, excerpts and group paths are shown escaped inside
quotes. The same characters that would be dangerous printed raw in a
terminal are exactly the ones escaped here.

## Coverage and selection counts

**Coverage** describes what recall could read, independent of the query:
`enumerated`, `read`, `invalid` and `unreadable` count Markdown documents
in the selected layer(s) only (the root memory index is not itself
counted); `auxiliary_read` counts the configuration, schema, memory index
and verdict-log files successfully read. `eligible` counts active documents
(or all of them, with `--include-superseded`); `matched` is the deduplicated
candidate count after redirect expansion. On a failed run these are the
counts observed before failure, with `null` for whatever was never reached.
A document counts as `read` once its bytes are in hand, so a ceiling tripped
part way through a layer still reports how many documents were enumerated and
how many were read; `unreadable` stays `null` while no document has been
decoded yet, and enumeration that never finished leaves `enumerated` `null`.

**Selection** describes what was actually returned out of what matched:
`returned`, `omitted_limit` (matches beyond `--limit`), `omitted_budget`
(matches that would fit the limit but not `--max-bytes`), `excerpt_truncated`
(how many returned excerpts were cut), `groups_total` and `groups_returned`.
`returned + omitted_limit + omitted_budget` equals `matched` on a successful
run. `complete: true` can still coexist with a nonzero `omitted_limit` or
`omitted_budget` -- that is a budget choice, not a failure; the text format
says so explicitly, and the fix is to raise `--limit` and/or `--max-bytes`
within their documented ranges, or to narrow the query.

## Exit codes

- **0** -- the corpus was fully acquired and validated. This covers a
  genuine zero-match search (`matched: 0`, with guidance to try different
  terms or an ordinary source search) exactly as much as a search that
  matched plenty but had to omit some for `--limit` or `--max-bytes`. Both
  are `complete: true`; neither is a failure.
- **1** -- the corpus could not be safely and completely acquired or
  validated: a missing selected directory, an unreadable or invalid
  document, index, configuration, schema or verdict log, a detected change
  during acquisition, a supersession cycle, or an internal failure. `results`
  is always empty and `complete` is `false`. This is "unavailable or
  incomplete," never "no matches" -- do not read an exit-1 empty result as a
  clean zero-match search.
- **2** -- a usage error (an out-of-range flag, or `QUERY` together with
  `--map`), reported before anything is read.

## Limits and platform requirements

**POSIX only.** Recall requires directory-descriptor-relative opening
(`O_DIRECTORY`, `O_NOFOLLOW`, `dir_fd` support) to refuse symlinks safely
while walking the selected trees. On a platform that lacks these
capabilities it fails explicitly (exit 1, a platform-limitation diagnostic)
rather than falling back to a less safe check.

**Resource ceilings**, each an exit-1 failure with no results when
exceeded, never a silent partial scan: 20,000 Markdown documents; 1 MiB per
Markdown, index, configuration or schema file; 16 MiB verdict log; 64 MiB
total input bytes across everything read; directory depth 64; 50,000
directory entries across the selected trees.

**Bytecode.** Recall's own execution creates no files. The Python
interpreter can still write `.pyc` bytecode caches during module import,
before recall's own code ever runs; `PYTHONDONTWRITEBYTECODE=1` (used
together with `python3 -P`) is what suppresses that, and is why the skill
invocation above sets it. Nothing inside recall's dispatch can retroactively
prevent bytes an import already wrote.

## Known limitations

- **No stemming, fuzzy matching or translation.** Matching is exact-token,
  after Unicode casefolding only. A query that does not share a word with a
  record's identity, path, label or body will not find it.
- **Strict, whole-corpus failure.** An invalid or unreadable document
  anywhere in a selected layer, an invalid configuration or declared
  schema, or a malformed verdict log fails the entire request -- recall
  never returns results computed from a corpus it could not fully validate,
  and never silently reinterprets a malformed log as merely "unknown"
  verdicts.
- **Only the selected layer's documents are validated against its own
  rules.** Selecting `--layer memory` still reads and validates the
  adopter configuration and its declared schema (a knowledge unit anchor
  kind may be referenced there), but it does not read or validate
  `knowledge/` at all; the reverse holds for `--layer knowledge`.
- **The digest is a read-time snapshot, not a cross-file transaction.**
  `sha256` identifies the exact bytes recall read for one document; recall
  re-checks every selected file's identity and metadata once acquisition
  finishes and fails if anything changed, but no atomic snapshot across the
  whole corpus is taken or promised. It also does not hold a journal lock
  and does not coordinate with a concurrent write beyond that re-check.
- **Excerpts and labels are truncated, never the diagnosis of why.** A
  label over 160 characters or an excerpt over 320 sets its `_truncated`
  flag; identifiers, paths, successor and redirect references are never
  truncated -- a result that cannot fit whole in the byte budget is omitted
  entirely rather than cut.
