# Code prose is a contract, a constraint or a verification argument, never a history

Written 2026-09-03 and rewritten the same day, after an adversarial challenge
(Codex SOL) found the first version's central sentence to be false and its
opening argument to be the wrong one. What changed: two classes became three,
the destination rule stopped asking whether a sentence is about the past,
tests came into scope with a clause of their own, and the rule acquired an
enforcement half. That the reasoning was tested this way is orientation, not
history: it tells the next reader which parts have already been attacked.

## What this is for

Four commits in a fortnight existed only to correct prose inside `.py` that
had stopped being true: `80fe538` ("two sentences that were not true"),
`e94c519` ("the docstrings catch up with the protocol that replaced them"),
`596f84f` ("lint stops overclaiming"), `a586229` ("the class says what is
true"), and `f18ab3d` for two test docstrings pointing at a task that no
longer existed. **The problem this ADR attacks is prose that goes false.**
Legibility is a secondary gain. Volume is a thermometer, not the target.

Volume is how it became visible, and the figures are worth stating exactly,
because the first version of this ADR stated them wrongly. Measured at
`b80325e` by [`measure.py`](../plans/reviews/measure.py) over
`git ls-tree`: all versioned Python is **36.9 %** prose (65 files); the
runtime package alone is **49.0 %**; all tests are 29.1 %; the population the
codebase review measures — the runtime plus the two largest test files — is
43.3 %. Prose here means comment bytes plus docstring bytes.

Two properties matter more than the size. **This is the only surface in the
repository with no pin**: `test_readme_currency`, `test_walkthrough`,
`test_docs_links` and `test_contract_docs` check the published prose, and
nothing at all checks a comment. **And it grows by construction**: sixteen
defects were closed in the journal, and each correction left behind a
paragraph explaining what it had answered.

## Three jobs, and a sentence that has none is deleted

- A **contract** tells a caller what to expect without reading the body:
  what this does, what it returns, what it refuses, what must hold before
  calling. Its question: *would a caller be surprised if this were false?*
- A **constraint** tells whoever changes these lines what they must not
  break. Its question: *would a competent modifier break something if this
  were missing?* The highest-value sentence in the repository is of this
  kind: the rejected alternative that looks correct to the next reader.
- A **verification argument** tells whoever audits the code or a test what
  the evidence is worth: the model a change has to fit, why these assertions
  prove the property, and what they do not prove. Its question: *would its
  absence force someone to reconstruct non-local information in order to
  verify this correctly?* `journal/__init__.py`'s map of the package and
  `tests/test_journal.py`'s statement of what a static pin cannot see are
  both of this kind, and both fail the other two questions.

A sentence that answers none of the three questions is deleted.

## History is what no longer binds

The test is not "is this about the past?" — it is **"does this still bind a
change someone could make today?"**

- **It still binds.** Then it is not history, it is a constraint, and it
  stays in the file, in a sentence. The journal reads records written before
  the executor existed and therefore keeps two pairing rules: that is the
  past, and it governs the next change to `reconcile`.
- **It was a decision.** It goes to an ADR, which is versioned, published,
  survives a file being split, and is found by name.
- **It is an incident** — a bug that was fixed, a docstring that lied, what
  a review found. It goes to the commit message, **and there it is allowed
  to become hard to find. That is the point, not a defect**: a closed
  incident must not cost every later reader a paragraph.

The commit message is a forensic archive, not a maintained destination, and
this repository proves the limit: `journal/executor.py`, 1 572 lines, has
**one** commit of history, and `git log --follow` recovers nothing, because
`eed02f6` created it by splitting `journal.py`. Anything that must still be
findable in two years is by definition still binding, so it is not in that
category. An archived plan is not a destination at all: executed plans move
to `sessions/`, which is gitignored, so a fresh clone never receives them.

## A claim about behaviour is pinned by assertions, not by prose

**A test name and a test docstring do not execute.** Only the fixture and
the assertions pin anything; the prose around them can go false without a
single test turning red. Moving a claim from a comment into a test docstring
leaves it exactly as unprotected as it was. The first version of this ADR
said the opposite, and that sentence was its pivot.

So, when prose states what the software does under some condition, the
destination is decided by looking first:

1. **Is it already pinned?** Find the test whose assertions fail if the
   claim stops being true. If it exists, the prose is redundant and goes —
   and the finding records which test pins it.
2. **Is nothing pinning it?** That is not a prose defect, it is a coverage
   gap, and it is recorded as one.

Closing that gap follows a ladder, because this project's test seam is the
CLI driven as a subprocess and tests never import the package's internals:

1. A black-box test at the seam. This is the only rung that needs no excuse.
2. If the property is not observable there, the finding is about
   **testability**, not prose: either the seam moves, or the gap is recorded
   with what it would take to close it.
3. If the mechanism *is* the guarantee — `O_EXCL` against a rename, the
   order of an `fsync`, the inode identity of a lock — a structural pin is
   accepted, labelled as structural, saying why nothing else reaches it.
   `tests/test_journal.py` already carries 44 of these.
4. If not even that reaches it, the claim survives as a verification
   argument **and must say that it is unpinned**. An unverified claim is
   marked as unverified; it is never dressed as a fact.

**"There is a docstring in `tests/`" never closes a gap.** A gap is closed
by assertions that have been seen to fail against the contrary behaviour.

## Tests are in scope, with one clause of their own

Test code is code, and 29 % of it is prose. But the contract/constraint
split is by **audience**, and a test has only one: whoever is about to
change or delete it. Nobody calls
`test_a_write_over_an_existing_file_parks_its_preimage()`.

So in a test, the prose always does the constraint's job and the
verification argument's — it says what property is pinned, why this fixture
and these assertions pin it, and what is left outside — and it **may cite
the failure it would have caught**, because that citation still binds: it is
what stops the next person weakening the pin.

The syntax is a convention, and this repository picks one: **the docstring**.
Today `tests/test_journal.py` uses a docstring on 99 of 99 tests and
`tests/test_render.py` a leading comment on 49 of 81, with 32 carrying
nothing. The docstring wins because it is attached to the function rather
than to a line, and because it is the only one `pytest` can show in a
failure report. Migrating `test_render.py` is the review's T1 unit, not a
big-bang change.

## Two halves of enforcement, and neither is a byte quota

**The review half.** Prose that is new or changed is classified in the
finding, or in the pull request, as contract, constraint or verification
argument, and every claim about behaviour names the test and the assertions
that pin it, or the coverage gap it opened. Recorded, classified judgement
is a different thing from unrecorded judgement, which is what produced the
figures above.

**The mechanical half.** A documentary reference inside a `.py` file must be
a versioned path that exists — with its heading, when it names one — or an
`ADR NNNN` that matches exactly one file, and it must never name a
destination that is not in the repository. This is pinned by test, alongside
the Markdown link checks that already exist. It is not decoration: replacing
duplicated prose with pointers introduces a second way to go false, and this
repository had 23 `design §N` references naming no document and three
pointing into a plan that had been archived out of the repository.

## Considered options

- **A maximum length per docstring or comment** — rejected, and the
  challenge agreed. Bytes do not predict falsehood, a limit rewards padding
  up to it, and it would be wrong for the one docstring that must be long:
  a package facade saying what the whole package is for.
- **A byte quota enforced in CI** — rejected. A number that gates is a
  number to optimise against, and the thermometer would stop measuring.
- **A test that fails on history words** (`used to`, `previously`) —
  rejected. It fires on legitimate sentences about the older record format
  the journal still reads, and misses every paraphrase, which is every
  sentence written after the test first fails.
- **Two classes, stretching "constraint" to cover the third** — rejected.
  Three distinct harms — a surprised caller, a broken change, a false belief
  — and a classification gate is only useful with honest buckets.
- **Keep docstrings, delete comments entirely** — rejected. The constraint a
  modifier needs has no other home.
- **Move the explanation to `docs/reference/`** — rejected. That document is
  for someone adopting the plugin, not for someone changing it.
- **Leave it to the reviewer's judgement** — rejected, which is why the
  enforcement section exists. The first version of this ADR rejected it and
  then offered nothing else, which was the same situation with new
  vocabulary.
- **Supersede this ADR with a new one rather than rewriting it** — rejected.
  It was hours old, no adopter had consumed it, the plugin version had not
  moved, and five of the ten ADRs here have already been edited after
  publication. A second number the same day would be bookkeeping, not
  record.

## Consequences

- **The review's prose axis has a rule from unit J2 onward.** J1 applied the
  history half before this existed and left 9 310 prose bytes in four files,
  which is where the rule is applied first.
- **Some prose will grow.** A docstring that never said what its function
  refuses gains a sentence, and an unpinned claim gains the admission that
  it is unpinned. This is not a reduction target.
- **The test surface may grow while the comment surface shrinks.** That is
  the trade: an assertion that runs, in place of one that is only read.
- **A long contract docstring is a design question before it is a prose
  question.** `operations.py` spends 45 lines on nine fields and seven
  invalid combinations of `Intention`; `reconcile.reconcile` spends 49 on
  three collections of tuples returned by a 50-line function. Length that
  enumerates a protocol or a set of illegal states is a signal about the
  interface — a case for a narrower constructor, a named result, a deeper
  module — and the review asks that question before it edits the sentence.
- **The ledger counts destinations, not only bytes.** History removed,
  claims found already pinned, coverage gaps opened, tests added, false
  claims corrected. Bytes stay as the secondary figure they are.
- **Nothing gates in CI except the mechanical half**, which checks
  references and not judgement.
