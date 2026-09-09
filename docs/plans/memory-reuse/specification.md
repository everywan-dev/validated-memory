# Recall implementation specification

Architect freeze: 2026-09-09. This specification resolves P0 of the
[implementation plan](../2026-09-09-memory-reuse.md). The user authorized plan
execution. Existing storage schemas, indexes, verdict meaning and exit codes
remain unchanged. Interface described here is the implementation target.

## Acquisition and validation

The working directory is the adopter root. Selected layers are `memory` and/or
`knowledge`. Read configuration even for memory-only requests; read the verdict
log only for knowledge requests. Config absence means base contract, as today.
Validate a declared schema even for memory-only selection. Reject missing
selected directories, allow existing empty directories; memory still requires
its valid index. Never read the derived knowledge index.

Use a small acquisition module with descriptor-relative opening on POSIX:
pin the root directory descriptor; walk each relative component with
`O_DIRECTORY | O_NOFOLLOW`; open files with `O_NOFOLLOW | O_NONBLOCK`, then
require regular-file type with fstat before reading. Refuse symlinks, including
internal links, selected-root links, config/schema/log links and directory links
within selected trees. Do not follow links while enumerating. Reject absolute,
empty or `..` schema paths. Provenance is never opened. Unsupported descriptor
capabilities return exit 1 with explicit platform limitation; do not silently
fall back to unsafe path checks. The initial implementation targets POSIX.

Resource ceilings: 20,000 Markdown documents; 1 MiB per Markdown, index, config
or schema; 16 MiB verdict log; 64 MiB total input bytes; directory depth 64;
50,000 directory entries across selected trees. Read at most limit+1 bytes to
detect oversize. Exceeding a ceiling is exit 1 with no results, not omission.
Ignore ordinary non-Markdown files in layer trees. Refuse special nodes and
symlinks there rather than following or silently treating a partial scan as
complete. Stable relative paths sort by Unicode codepoint.

Capture inode/device/size/mtime_ns/ctime_ns before and after reads, and
re-enumerate selected inventories plus config/schema/log existence after
acquisition. Detect replacements/additions/deletions as failure. Retain file
descriptors or reopen component-wise safely for verification. No atomic
cross-file snapshot is promised; digest identifies actual read bytes. The
command does not acquire a journal lock or stage a temporary tree.

Expose narrow in-memory adapters in extension/lint/verdicts as needed so old
filesystem entry points delegate to identical validators. Do not duplicate
schema, index, identity or verdict-log rules, monkeypatch filesystem globals,
or call old filesystem loaders after safe acquisition. Existing commands must
retain diagnostics and behavior. Use contract.validate_documents for curated
bytes and an in-memory lint seam for memory documents/index. Detect memory
supersession cycles locally in recall without changing lint's public rules.
Knowledge supersession cycles are already rejected by
`contract.validate_documents` before retrieval; do not bypass that validation.

Recall's own execution creates no files. Python can create bytecode before
command dispatch; callers requiring no interpreter-created files must use
`PYTHONDONTWRITEBYTECODE=1` with the standard
`python3 -P -m validated_memory` invocation in the consultation skill and strict
snapshot tests. Do not claim a setting inside dispatch prevents earlier import
writes. This replaces the plan's technically infeasible pre-import dispatch
requirement. Other commands' import behavior is unchanged.

## Arguments and matching

Command `recall`: one optional positional query string; `--map` is mutually
exclusive with a supplied query. Query is required otherwise; require at least
one Unicode word token and maximum 4,096 UTF-8 bytes. Flags:
`--layer {all,memory,knowledge}` default all; `--limit` integer 1–100 default 8;
`--max-bytes` integer 2,048–65,536 default 12,288;
`--format {text,json}` default text; `--include-superseded` boolean.
Usage validation happens before corpus acquisition. Map is retained because it
offers a query-free entry point without another stored index.

Query tokens are unique sorted results of `re.findall(r'\w+', query.casefold())`.
Field matching is token-set intersection (OR, not substring). Search identity,
relative path, literal label and full body. Memory label is description;
knowledge label is first Markdown heading outside fenced blocks, else id.
No stemming, fuzzy matching, translation or extension-field interpretation.
Whitespace-stripped casefolded query equal to identity or full relative path
is an exact match. Ranked tuple descending: exact flag, distinct label-token
matches, distinct identity/path-token matches, distinct body-token matches.
Ties ascending: layer, identity, path. Match requires a positive component.
Map includes all active records sorted by layer, parent directory, identity,
path; include-superseded includes history in the same ordering.

Knowledge successors are reverse supersedes edges; memory successor is its
validated description wikilink. Validate cycles across every selected memory
entry even when unmatched. In normal query mode redirect retired matches to
all reachable active endpoints. Deduplicate endpoints by (layer, identity),
keep the best originating match score and the sorted set of matching retired
origins. An endpoint's source excerpt/label belongs to the endpoint, never its
retired origin. Direct and redirected matches can coexist on one endpoint.
With include-superseded, return directly matching historical records without
automatic redirect expansion, labelled with direct successors. Traversals are
iterative and memoized; bound distinct expanded redirect associations at
100,000, failing explicitly if exceeded. Do not expand every possible path.

## Version 1 JSON and text

Top-level fields:
`schema_version` (1), `mode` (query/map), `layers` (sorted array),
`complete` (input acquisition+validation succeeded), `coverage`, `selection`,
`results`, `diagnostics`, `guidance`. Do not echo the raw query in the envelope.

Coverage fields: `enumerated`, `read`, `invalid`, `unreadable` count Markdown
documents only (root memory index excluded); `auxiliary_read` counts config,
schema,index,log files successfully read. `eligible` counts active documents
by default or all with include-superseded. `matched` is the deduplicated candidate
count after redirects (same as eligible for map). On acquisition failure,
counts are observed-so-far and complete=false; unknown totals use null.
An invalid document is counted once irrespective of findings. Invalid config,
schema,index or log is diagnostic failure, not an invalid document count.

Selection fields: `returned`, `omitted_limit`, `omitted_budget`,
`excerpt_truncated`, `groups_total`, `groups_returned`.
With M candidates and limit L, omitted_limit=max(0,M-L), then omitted_budget
counts candidates among the first min(M,L) excluded for byte budget.
returned+omitted_limit+omitted_budget=M on success. Groups are distinct
(layer,parent-directory) across candidates and returned results, in both modes.
excerpt_truncated counts returned truncated excerpts. No per-group array can
overflow the envelope; each result carries its group path and candidate count.

Common result fields: `layer`, `identity`, `path`, `line`, `sha256`, `label`,
`excerpt`, `excerpt_truncated` (bool), `state` (active/superseded),
`successors` (sorted identity/path objects), `redirected_from` (same objects),
`match` (exact bool and label/path/body sorted matching token arrays),
`group` (parent relative path), `group_count`.
Memory includes `memory_type`. Knowledge includes `evidence`, `verdict` and
`anchors`: each anchor's system, kind, verdict, checked_at (null if absent).
No borrowed evidence/verdict fields on memory; no applicability/confidence flag.
Read check timestamps from the exact keyed last log record, not capture date.

Excerpt: first body line with a matched token, else first nonempty body line,
else empty string; maximum 320 characters, prefix truncation with flag. Source
line is the selected line's actual 1-based document position, or 1 if empty.
Label is literal up to 160 characters, with separate `label_truncated` flag.
All excerpts are incomplete discovery aids even when excerpt_truncated=false.
Identifiers, paths, successors and redirect origins are never cut; omit the
whole result if it does not fit. Full digest is for the source document bytes.

Success guidance always says to read original files and check applicability;
zero matches additionally recommend different terms and ordinary source search.
Diagnostics are sanitized, bounded objects with severity, path, field, message;
include `diagnostics_omitted` at top level if not all fit. Do not return usable
results on failure. Limit diagnostic fields to 160 characters with visible
ellipsis, keep a fixed error code/message for envelope fallback. Reserve a
minimum failure envelope under 2,048 bytes for all supported parameters.

Serialize JSON deterministically with ensure_ascii=true, no timestamps or hash
ordering; newline counts in byte budget. Text is a readable rendering of the
same information with visible escaped C0/C1 and bidi controls, escaped paths
and quoted literal excerpts. Budget the actual requested serialization. Keep
the largest ranked prefix that fits with final counts, removing whole records
and lower-priority diagnostics as necessary; never truncate serialized bytes.
complete=true can coexist with selection omissions and must say so in text.

Exit 0 for valid complete inputs (including no matches or budget omissions),
1 for acquisition/validation/execution failure, 2 for usage errors. Exit-1 stdout
still follows the bounded envelope; stderr has a fixed sanitized summary.
Precedence: usage, platform/acquisition, configuration/schema, document/index
validation, verdict validation, supersession graph, result production. No
probes, writes, derived index refresh or execution of corpus text.

## Worked acceptance examples

Fixtures use valid current schemas; ids below are illustrative:

- Memory m-cache label "cache timeout" and active knowledge k-cache label
  "Cache retry budget", query "cache": both return, exact=false, correct
  layer-specific metadata, paths/digests and source lines. Label/path/body
  matching counts decide order, then knowledge precedes memory on a full tie.
- Query "absentword" on those fixtures: complete=true, matched=returned=0,
  exit 0, source-search guidance; absent memory directory instead gives exit 1.
- Retired knowledge old-timeout is the only "legacy" hit and new-timeout
  supersedes it: normal output contains new-timeout with redirected_from old;
  include-superseded contains old-timeout with successor new, state superseded.
- Corrupt unrelated knowledge frontmatter: complete=false, results=[], exit 1;
  malformed log likewise fails rather than changing all verdicts to unknown.
- Ten matches, limit 8, budget fitting three: returned=3, omitted_limit=2,
  omitted_budget=5; valid JSON inside cap. A giant first record may yield zero
  returned records despite matched>0, explicitly distinguished from zero-match.
- Map over two directories: no relevance ranking; stable directory ordering,
  groups_total=2, groups_returned reflects actual selected results. No invented
  topics and no rewritten memory index.

## P4 freeze

Retain eight tasks/two arms/16-run ceiling and all gates in the parent plan.
Usefulness gate is frozen now: no new unsafe reliance or decisive caveat loss,
supported outcomes >= baseline, at least two paired tasks improve a supported
outcome or avoid a preregistered unnecessary investigation. Record time/context
tradeoffs. Unnecessary investigation means re-executing a specific investigation
already answered by an applicable source; ordinary verification/read is never
counted as waste. Define task-specific examples in evaluator-only keys before
constructing curated documents. Separate fixture author and independent scorer
from the implementation session. No held-out tuning, infrastructure repair loop,
automatic retry, API fallback or claim of statistically proven productivity.
