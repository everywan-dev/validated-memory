# Code prose is a contract or a constraint, never a history

Measured on 2026-09-03 at `b80325e`, with
[`docs/plans/reviews/measure.py`](../plans/reviews/measure.py): **43 % of
this repository's Python is prose** — 84 439 bytes of comments and 224 102
of docstrings against the code they sit in. Per unit of the codebase review
it runs 53–60 % across every module of `validated_memory/journal/`, 58 % in
the scaffolder, 35 % in the two large test files, and 15 % at the entry point.

It nearly went unmeasured. The first version of that script matched
docstrings on the token stream, and the token before a function's docstring
is the `:` of its own `def`, so it counted module docstrings only and
reported 16 %. The number above comes from walking the AST, and the baseline
was re-derived from the commit before any of this was touched.

Two things about that prose matter more than its size. **It is the only
surface here with no pin.** Published prose is checked by test —
`test_readme_currency`, `test_walkthrough`, `test_docs_links`,
`test_contract_docs` — and a claim in a comment is checked by nobody, so it
is the one place in this repository where an assertion can quietly stop
being true. **And it grows by construction.** The journal's two fix waves
and three adversarial reviews closed sixteen defects, and each correction
left behind a paragraph explaining what it had answered: what the code used
to do, which reading was wrong, what a past reviewer found. Every one of
those sentences was true when written. None of them tells the next reader
how to change the code safely, and they are read on every visit, by a person
or by an agent, for as long as the file exists.

The decision, in five parts.

**There are two kinds of code prose and each has one job.** A **docstring**
states the contract: what this does, what it returns, what it refuses, what
a caller must hold true before calling. A **comment** states the constraint:
what a person changing these lines must know in order not to break
something. Anything that is neither is not code prose and does not go in the
file.

**Two questions decide, and a sentence that fails both is deleted.** Of a
docstring sentence: *would a caller be surprised if this were false?* Of a
comment: *would a competent modifier break something if this were missing?*
The highest-value comment in the repository is the one that answers the
second with a rejected alternative — the approach that looks correct to the
next reader and is not. Those stay, and they are the reason this decision is
not "write fewer comments".

**History goes to the commit message, the ADR, or the archived plan.** What
the code used to do, which bug this answered, what an older protocol was
called, which review found it: all true, all worth keeping, none of it in
the file. A fix does not leave a monument where it landed. This repository
can afford the rule because its commit messages already carry that weight —
they are the record, and a terse one is now a real loss rather than a style
choice.

**A sentence that asserts behaviour belongs in a test.** A test name and a
test docstring execute; a comment does not. When prose in a file states what
the software does under some condition, the destination is a test that fails
if it stops being true, and the comment goes. This is the same rule the
published documentation already lives under, applied to the surface that had
been exempt from it.

**Behaviour already stated in `docs/reference/` is named, not restated.**
That reference is the authority on what the CLI does and it is pinned; a
docstring that repeats twenty lines of it duplicates a claim, and duplicated
claims drift apart. One sentence and the section name.

## Considered options

- **A maximum length per docstring or per comment** — rejected. Any number
  is arbitrary, it rewards padding up to the limit as much as it punishes
  running past it, and it would be wrong for the one docstring that has to
  be long: a package facade stating what the whole package is for. The two
  questions above cut the same prose without a number, and they say *which*
  sentence goes rather than *how many*.
- **A test that fails on history words** (`used to`, `previously`, `before
  this fix`) — rejected. It is a textual denylist: it fires on legitimate
  sentences about a supported older format — the journal reads records
  written before the executor existed and has to say so — and it misses any
  paraphrase, which is every sentence written after the first time the test
  fails. A pin that is trivial to evade and expensive in false positives
  buys nothing.
- **Keep docstrings, delete comments entirely** — rejected. The constraint a
  modifier needs has no other home: a docstring is read by a caller, and the
  reason a `mkdir` is checked against `directory` rather than existence is
  not a caller's business.
- **Move the explanation to `docs/reference/`** — rejected. That document is
  written for someone adopting the plugin, not for someone changing it, and
  growing it with implementation reasoning would put internal detail on the
  published surface and make the reference itself the thing nobody reads.
- **Leave it to the reviewer's judgement** — rejected as the status quo that
  produced 43 %. Every one of those paragraphs was written by someone
  exercising judgement in good faith.

## Consequences

- **The codebase review's prose axis has a rule from unit J2 onward.** J1
  applied the history half before this ADR existed, on the user's own
  statement of it, and left 9 310 prose bytes standing in four files — 59 %
  — because the rest is contract. Those two long docstrings
  (`reconcile.reconcile`, 49 lines for a 50-line function, and the package
  facade, 51) are the first thing this rule is applied to.
- **Some prose will grow.** A docstring that never said what the function
  refuses gains a sentence. The rule is not a reduction target.
- **A comment that a test could pin means a missing test.** Applying this
  will add tests while removing prose, so the test surface may grow as the
  comment surface shrinks. That is the trade being made: an assertion that
  runs, in place of one that is merely read.
- **The effect is measured, not gated.** `docs/plans/reviews/ledger.md`
  records prose bytes per unit before and after. No CI check counts them: a
  number that gates would be a number to game, and the two questions are not
  mechanically decidable.
- **A docstring longer than the body it documents is a review question**,
  not an error. It is often the right shape — a small function guarding an
  invariant that takes a paragraph to state — and it is often a narration
  that survived. Asking is cheap; forbidding is wrong.
