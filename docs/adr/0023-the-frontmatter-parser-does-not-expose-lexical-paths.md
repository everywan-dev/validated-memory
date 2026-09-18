# 0023: The frontmatter parser does not expose lexical paths

## Context

The base contract requires a rationale's `question` and each option's
`label` and `reason` to be written quoted, because a plain scalar loses
everything from ` #` onward. `parse` returns the same string for a quoted and
a plain scalar, so the rule needs two lexical facts about the source: whether
a value was quoted, and on which line it was written.

`unquoted_values(text, block, keys)` answered this with a line scan beside the
parser, and its grammar for keys and comments was not the parser's. It
reported `- reason:x`, which the parser reads as a scalar list item, and
`question: # comment` followed by an indented block, which the parser reads as
a key with a block value. Both documents already fail structural validation,
so these false findings changed the report, not the exit code.

The alternative considered was `parse_with_source(text) -> ParsedFrontmatter`:
the parsed data plus per-path line and quoted metadata, with `parse` returning
only the data. It would give the parser an addressing vocabulary and a public
result type to serve one rule over three keys.

## Decision

There is no `parse_with_source`, no `ParsedFrontmatter` and no public metadata
or path interface. `parse(text)` and `unquoted_values(text, block, keys)` keep
their interfaces: the first returns the mapping, the second returns
`[(key, lineno)]` in document order.

The lexical facts are private to `validated_memory/frontmatter.py` and come
from the traversal `parse` itself performs. They exist only for entries the
parser recognises as mapping keys, including the first line of a list item
that opens a mapping. The line is the parser's own line number. A value is
quoted when the parser's inline value text, once a trailing comment is
removed, begins with `"` or `'`: an empty inline collection is therefore
unquoted, and a key with a block value has no inline value to judge.

The quoting rule keeps its scope: every recognised `question`, `label` or
`reason` key at any depth under the top-level `rationale` block, including a
key the envelope rejects. A malformed but parseable document still receives
those findings. Text the parser does not read as a key, and a key whose
remainder is only a comment introducing a block, no longer produce one.
Findings keep the field `rationale.<key>` and carry the source line; the line,
not an index, locates the occurrence.

If a path is ever needed, it is a tuple of keys and list indices. A dotted
string cannot be one: `.` is legal inside a key, so a literal dotted key and a
nested mapping are different documents with the same dotted spelling, `a.b`:

```yaml
a.b: "x"
```

```yaml
a:
  b: "x"
```

No rule needs a path now, so no path seam exists.

## Consequences

- One grammar decides what a key, a comment and a block are; the quoting rule
  cannot disagree with `parse` about them.
- Every caller of `parse` is unchanged, and a valid document produces the same
  findings as before.
- `unquoted_values` parses the whole document and raises `FrontmatterError` on
  anything `parse` rejects; its caller passes only documents that already
  parsed. Duplicate keys and other parse errors are reported by the parser and
  never reach the rule.
- Reporting an exact path such as `rationale.options[1].reason` would change a
  finding's shape, and needs its own issue and decision.
- `tests/test_validate.py` pins both removed false findings, the misplaced-key
  scope, empty collections, list-opening keys, the non-indexed field and the
  source lines.
