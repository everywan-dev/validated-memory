# Inside a package, `_` means no other module may depend on it

Raised by review unit J1 (2026-09-03) with ten names, recounted by J2 with
twenty, and decided in J3 (2026-09-04) with twenty-one — `_Intention`, which
J2 itself created. Decided after a challenge (Codex SOL) that rejected the
argument this ADR was going to be written on.

## The state that forced the question

`validated_memory/journal/` is nine modules behind one facade. Twenty-one
underscore-prefixed names cross module lines inside it: `transactions` is
imported for twelve of them, `paths` for six, `records` for one, `fault` for
one, `operations` for one. Each is used by one to three other modules of the
package.

So the marker that tells a reader arriving from ordinary Python "this is
mine, do not reach for it" told them nothing here, while still telling them
that. `from .transactions import _classify` is a sentence the language reads
as a violation and this package meant as ordinary.

## The decision

**Drop the underscore from every name another module of the package depends
on. Keep it for every name that is local to the module that defines it.**

Two rules, and they answer two different questions:

- **Is this public outside the package?** Only if the facade re-exports it,
  and `journal.__all__` is the whole list. Nothing else publishes a name:
  not the absence of an underscore, not being importable.
- **May another module of this package depend on it?** Only if it has no
  leading underscore. `_park_preimage` is the executor's own; `classify` is
  the write-ahead log's interface to the executor.

Two pins already enforce the first rule and are unaffected by the second:
`test_no_module_outside_the_journal_reaches_past_the_executor` scans the
text of every module outside the package for a fixed list of names, and
`test_nothing_outside_the_journal_reaches_a_name_it_does_not_export` reads
imports and attribute access with `ast`.

## The argument this ADR was going to be written on, and why it is wrong

The draft said the underscore was load-bearing for the text scan: a spelling
like `_open_transaction` never occurs in English prose, so a scan can look
for it without false positives, whereas `bootstrap`, `record`, `append` and
`install` had to be pinned as symbols instead. Dropping the underscore, the
argument went, would push twelve names from the stronger pin to the weaker
one.

It is false in its middle step. What makes those names scannable is
`snake_case`, not the prefix: `open_transaction`, `mark_published`,
`abort_transaction`, `resolve_transaction` and `write_denied` are no more
English prose than their underscored spellings, and they stay in the same
text scan. And only five of the twenty-one are in that list at all — the
argument was defending a property fifteen of them never had.

## Considered options

- **Write the convention down instead** — "inside this package `_` marks
  what the facade does not export". Rejected, and the challenge is what
  killed it: it is already false as a classification. `now`, `new_id`,
  `record`, `journal_path`, `append`, `artifact_name`, `read`,
  `current_state`, `report_word` and `bootstrap` are not exported by the
  facade and carry no underscore. The convention would have to be stated as
  an implication that predicts nothing, and every `from .x import _y` would
  still need the reader to remember it.
- **`__all__` in each module.** Rejected: it governs `import *` and
  documents nothing else, and giving each module a declared surface makes
  the modules doors, which is the opposite of what the facade says they are.
- **A different prefix (`internal_`).** Rejected: a convention nothing
  enforces, twice as long, and new to every reader.
- **Rename the files (`_transactions.py`) and leave their contents
  unprefixed.** Rejected: it is this decision moved to the filenames, with
  more churn and no pin that the file layout does not already give.

## Two names the rule could not simply un-prefix

- `_fault` becomes **`fault_at`**, not `fault`: its module is called
  `fault`, and `fault.py`'s own docstring rests on a property a common
  English word would destroy — that a grep for the name finds every line
  that can act on `VALIDATED_MEMORY_FAULT`.
- `_resolve_transaction` becomes **`remove_transaction_file`**, not
  `resolve_transaction`: `Run.resolve_transaction` already exists and is one
  of the four methods a module outside the package may call. Two names for
  two different things in one file is what this ADR exists to prevent, and
  the new name says what the function does — it is the only one that unlinks
  a transaction file.
- The three verdicts `_COMPLETE`, `_DISCARD` and `_REMOVE` become
  **`VERDICT_COMPLETE`**, `VERDICT_DISCARD` and `VERDICT_REMOVE`:
  `records.REMOVE` is an op of the record vocabulary with the same spelling
  and a different meaning, and both are imported into the executor.

## Consequences

- Twenty-one names lose a prefix, in one mechanical change gated by the
  suite. `PRIVATE_JOURNAL_NAMES` keeps the five it lists, respelled.
- A reader of `from .transactions import classify` learns something true:
  this is the write-ahead log's interface to the rest of the package.
- A reader of `_park_preimage` learns something that is now also true: no
  other module may call it.
- The findings files of J1 and J2 name these functions by their spellings of
  the day. They are dated records of what the code was, and are not
  rewritten.
