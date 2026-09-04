# Codebase review — plan (multi-session)

**Status:** in progress. Started 2026-09-03, after releasing 1.6.0 (`d20fc9b`).
This file is the state: the checklist at the bottom is what a new session
reads to know where the work stands.

**Goal:** the code is correct and released; this plan asks whether it is
*shaped* well and whether its prose earns its place. It is upkeep, not
feature work, and it produces three kinds of output — refactors that make a
module deeper, prose deleted or moved to where it can be verified, and
decisions recorded as ADRs.

## The two axes

Every unit is reviewed along the same two axes, and a finding is always
attributed to one of them.

**A — Shape.** Is this a deep module: a lot of behaviour behind a small
interface, at a seam that does not leak? The vocabulary is the one in
`/codebase-design` (module, interface, depth, seam, adapter, leverage,
locality). Signals worth chasing: a module with many public names and little
behaviour behind each; a private helper that is really a second module; a
caller that has to know the callee's internals to use it; a seam that every
change crosses.

**B — Prose.** A comment says what you must know *now* to change this code
safely. It is not the history of how the code got here — that belongs to the
commit message, to an ADR, or to the archived plan. Three paragraphs
reconstructing the reasoning before arriving at what a function does is the
defect this axis exists to remove. Docstrings state the contract; comments
state the non-obvious constraint.

A finding on axis B has three possible destinations, in this order:

1. **Delete** — it restates what the code says.
2. **Move to a test** — it asserts behaviour. A test name or docstring
   executes; a comment does not. This repository already pins its published
   prose by test (`test_readme_currency`, `test_walkthrough`,
   `test_docs_links`, `test_contract_docs`); comments are the one prose
   surface with no pin, which is why they drift.
3. **Keep, shortened** — it states a constraint the code cannot state: a
   platform quirk, an ordering that matters, a rejected alternative that
   would look correct to the next reader.

**The rule for axis B is [ADR 0010](../adr/0010-code-prose-is-a-contract-a-constraint-or-a-verification-argument.md)**, and a finding cites the class it
assigns and the question that decided it:

- **contract** — would a caller be surprised if this were false?
- **constraint** — would a competent modifier break something if this were
  missing?
- **verification argument** — would its absence force someone to reconstruct
  non-local information to verify this correctly?

A sentence that answers none of the three is deleted. History is what no
longer binds a change someone could make today: what still binds stays as a
constraint, a decision goes to an ADR, an incident goes to the commit
message. A claim about behaviour is pinned by assertions, never by a
docstring, and the ladder for a claim that the CLI seam cannot observe is in
the ADR.

## The units

Sizes measured 2026-09-03 on `a0f2e4e`: runtime LOC, and the approximate
tokens a session pays to read the unit (bytes/4). Reviewing a unit also means
reading its tests, which are larger than the code — see the session protocol
for how not to load them whole.

| # | Unit | Files | LOC | ~tok | Why it is one unit |
|---|---|---|---|---|---|
| J1 | Journal: reporting and fault seams | `journal/reconcile.py`, `journal/command.py`, `journal/fault.py`, `journal/__init__.py` | 572 | 6.1 K | The pilot. Smallest journal unit and the prose-heaviest code in the repository (27 %, 20 %, 22 % comment bytes). Calibrates both axes cheaply. |
| J2 | Journal: the vocabulary | `journal/records.py`, `journal/paths.py`, `journal/operations.py`, plus `journal/durable.py`, which the unit split out of the first | 802 | 8.2 K | What a record, a path and an operation *are*. `records.py` has 11 public names — the widest interface in the package, and the first depth question. |
| J3 | Journal: the write-ahead log and the lock | `journal/transactions.py`, `journal/lock.py` | 1 010 | 11.2 K | Both own crash behaviour; both were fixed repeatedly in the 2026-09-01/02 waves. |
| J4 | Journal: the executor | `journal/executor.py` | 1 572 | 18.4 K | The one path every mutation takes. Two public names over 1 572 lines: either the deepest module here or a bag with a narrow door. Depends on J1–J3 being settled. |
| S1 | The scaffolder | `init.py`, `adopt.py` | 1 236 | 13.8 K | `init.py` is 1 016 lines behind one public function and fifteen private ones, and it is the only command that mutates an adopter tree. |
| C1 | The contract | `contract.py`, `validate.py`, `extension.py`, `frontmatter.py`, `findings.py` | 1 295 | 11.2 K | The base contract, the adopter extension, the frontmatter subset and the finding type: one vocabulary, four files. |
| F1 | Freshness | `derive.py`, `verdicts.py`, `probe.py`, `probes/git_ref.py`, `status.py` | 1 048 | 9.8 K | Probe → verdict → index → gate. The probe interface is the extension point that will grow first. |
| M1 | Agent memory and corpus | `lint.py`, `memory.py`, `corpus.py` | 1 031 | 10.2 K | The memory layer's rules, and the corpus both views read. |
| V1 | The view stack | `render.py`, `knowledge_view.py`, `knowledge_overview.py`, `memory_view.py`, `svg.py`, `styles.py`, `html.py` | 1 415 | 15.8 K | Seven files, one output. Layering question: does `render` know about HTML, or only about pages? |
| X1 | The entry point | `cli.py`, `__main__.py`, `__init__.py`, `probes/__init__.py` | 318 | 3.0 K | Reviewed last: the shape of the CLI follows from the shape of everything under it. |
| T1 | The test surface | `tests/test_journal.py` (4 186 LOC), `tests/test_render.py` (2 574 LOC) | 6 760 | 70 K | Not code under review but a surface of its own: 13.6 % comment bytes, and two files carrying a third of all test lines. Are they pinning behaviour or transcribing it? Sampled, never loaded whole. |

Total runtime under review: 10 299 LOC, ~108 K tokens — more than one session's
smart zone, which is why this is eleven units and not one review.

## Order, and why

J1 → J2 → J3 → J4 → S1 → C1 → F1 → M1 → V1 → X1 → T1.

The journal first because it is the newest code, the least settled, and the
part the user named. Inside it, smallest and prose-heaviest first: J1 is the
pilot that turns the two axes into concrete rulings, and J4 — the unit with
the most to gain — is reviewed only once the vocabulary beneath it has been
judged. `cli.py` last because its shape is a consequence. The test surface
last of all, because what a test should pin is a question the nine code units
will have answered.

An interesting finding may reorder what follows; record the reason in the
log when it does.

## Session protocol

1. Read this file, then `TODO.md` and the top of `SESSION.md`.
2. Pick the first unit whose checkbox is unticked. **One unit per session**;
   two only if the first was trivial and the context is still clean.
3. Branch: `feature/review-<unit>` (e.g. `feature/review-j1-reporting`).
4. Read the unit's files in full. Read its tests **by name, not whole**: find
   the relevant ones with `rg 'def test_' tests/<file>.py` and read only
   those. `tests/test_journal.py` is 44 K tokens and must never be loaded
   entire.
5. Review along axis A, then axis B. Write the findings into
   `docs/plans/reviews/<unit>.md` — one file per unit, so this plan stays
   small enough to read at every session start. **Every prose finding names
   the class it assigned** (contract, constraint, verification argument) and
   **every claim about behaviour names the test and the assertions that pin
   it**, or the coverage gap it opened. That classification is the review
   half of ADR 0010's enforcement; the mechanical half is already a test.
6. Apply what is decided. Defer what needs a decision, and say where it went.
7. Run `python3 -m pytest`. **The whole suite green before any claim**
   — 650 tests at the end of J2, and the number moves as units add
   pins. A unit that lowers it has deleted a test, and says which one
   and why. A comment whose deletion breaks a test was a pin in the
   wrong place — record that; it is an axis-B finding of the best kind.
8. Commit, merge to `main` when green, push to **both** remotes.
9. Tick the checkbox below with the commit, fill the unit's row in
   `reviews/ledger.md` — the semantic counts first, bytes second — add a
   dated `SESSION.md` entry, and update `TODO.md` if anything was deferred.

## Guardrails

- **No behaviour change without a test that fails first.** A refactor that
  changes what the CLI prints is not a refactor; it is a change, and it needs
  its own reasoning and its documentation update.
- **The version files stay untouched.** A review unit is not a release. If a
  unit does change behaviour, that is a separate decision and follows ADR
  0005.
- **Deleting a comment is a change like any other.** It goes through the same
  suite and the same review.
- **What the reviewer cannot verify, the reviewer does not assert.** The
  repository rule («evidence before asserting») applies to findings too: a
  claim about behaviour is checked by running something, not by reading.
- **One unit, one branch, one merge.** No unit is left half-applied across
  sessions.

## Where findings go

| Kind | Destination |
|---|---|
| Applied now | a commit on the unit's branch |
| Needs a decision | an ADR under `docs/adr/`, numbered next |
| Real but out of scope | a dated entry in `TODO.md`, with its evidence |
| Rejected | the unit's file under `docs/plans/reviews/`, with the reason |

## Progress

- [x] **ADR: what a comment is for in this repository** (precondition for
      axis B from J2 onwards) — [ADR 0010](../adr/0010-code-prose-is-a-contract-a-constraint-or-a-verification-argument.md),
      written 2026-09-03 from J1's worked example.
- [x] **ADR: what `_` means inside a package** — [ADR 0011](../adr/0011-inside-the-journal-package-an-underscore-means-no-other-module-may-depend-on-it.md), decided in J3 with twenty-one names. It drops the
      underscore from every name another module depends on; the argument
      this review was going to write it on was refuted first.
- [x] J1 — journal reporting and fault seams ([findings](reviews/j1.md))
- [x] J2 — journal vocabulary ([findings](reviews/j2.md))
- [x] J3 — write-ahead log and lock ([findings](reviews/j3.md))
- [x] J4 — the executor ([findings](reviews/j4.md))
- [x] S1 — the scaffolder ([findings](reviews/s1.md))
- [ ] C1 — the contract
- [ ] F1 — freshness
- [ ] M1 — agent memory and corpus
- [ ] V1 — the view stack
- [ ] X1 — the entry point
- [ ] T1 — the test surface
- [ ] Close: archive this plan to `sessions/plans/`, and record in `TODO.md`
      what the review changed overall.

## Log

One line per session, most recent last: date, unit, commit, outcome.

- 2026-09-03 — plan written, no unit reviewed yet.
- 2026-09-03 — the counter: `reviews/measure.py` and `reviews/ledger.md`.
  Correcting the tool moved the headline number from 16 % prose to **43 %**
  (60 % in the journal package): the first version matched docstrings on the
  token stream and saw only module docstrings.
- 2026-09-03 — ADR 0010 written, then rewritten the same day after an
  eight-question interview and an adversarial challenge (Codex SOL) that
  found its pivot sentence false: a test name and docstring do not execute.
  Two classes became three (contract, constraint, verification argument),
  the destination rule now asks whether a sentence still binds rather than
  whether it is about the past, tests came into scope with the docstring as
  the chosen syntax, and enforcement gained two halves — classification in
  the finding, and a test pinning every documentary reference inside Python.
- 2026-09-03 — the reference pins (`ba3d461`, merged `de744a7`): 23 ambiguous
  `design §N` citations given a versioned path and section, three pointers
  into a plan archived out of the repository repaired, and four new tests in
  `test_docs_links.py` over the Python surface. 648 tests green.
- 2026-09-04 — J3 reviewed, `reviews/j3.md`. It took the deferral J1
  opened and J2 recounted: ADR 0011 drops the underscore from the
  twenty-one names other modules of the package depend on, and the
  argument this review had for the opposite was refuted by the
  challenge before it was written down. `own_directory` moved to
  `paths.py` for the reason `install` did in J2. Two questions leave
  the unit open on purpose: the publish marker is the one write in
  the protocol with no guard (`TODO.md`), and whether a transaction
  should be an object is a question **J4 inherits**, because the
  executor is the only caller that would gain and its review is next.
  650 tests green.
- 2026-09-04 — J2 reviewed, `reviews/j2.md`: three structural fixes, two
  new pins, and two rounds of review corrections on top of them. `Intention` was asked ADR 0010's interface question, and the
  docstring turned out to be stronger than the code: three combinations
  it called impossible were constructible. Construction became five
  named factories over a private dataclass (`a664597`); `OBSERVE` left
  `INTENTION_OPS`, taking a refusal and an executor guard with it
  (`ebc474e`); `install` and `fsync_directory` left the record format
  for `journal/durable.py` (`7a286ee`). Two unpinned claims became
  tests, each seen red against the contrary behaviour first: the
  facade's layering and the schema refusal (`74e02e1`). The unit is
  +94 LOC and +1 496 prose bytes, the first to grow, and the ledger
  says why. 650 tests green.
- 2026-09-03 — J1 reviewed (`db5700b`), `reviews/j1.md`. Applied: the reporting module
  stopped reimplementing the unknown-id refusal and the recoverable word
  (both now `transactions.missing_resolution` / `report_word`), two
  function-local imports hoisted, and history removed from four comments.
  −42 LOC and −1 487 prose bytes in J1 against +35 and +929 in J3, which is
  what the fix cost. Deferred to an ADR: ten underscore names cross module
  lines inside `journal/`, so `_` marks nothing there. 644 tests green.
- 2026-09-04 — J4, the executor, `f3d1def` … `e078864`. J3's inherited
  question answered in a different shape: not the `(root, id)` pair but two
  dicts, since every caller of `classify` re-read the raw transaction file
  for six more fields. `facts` now carries all of them and `_complete` and
  `_restore` take no file. `bootstrap` became `_bootstrap` under ADR 0011.
  Six sentences of history out, one unpinned claim turned into a test seen
  red first (651). One finding recorded and not fixed, and it goes to S1:
  building a `Run` adopts the tree, and `journal --resolve` carries a
  preflight to work around it — the preflight defends against `Lock`
  creating the vault as much as against the bootstrap, which is what makes
  the obvious fix unavailable.
- 2026-09-04 — S1, the scaffolder, `d6d9d70` … `6fc9968`. The vault's ignore
  rule became `ignore.py` (J2's `durable.py` argument); `init.py` 1 016 →
  859, and the first unit since J1 to shrink its prose, because the same
  contract had been written twice. Four incidents out, all pinned; two
  unpinned claims closed, one seen red. J4's inherited question answered
  yes -- adopting should be an explicit step -- and recorded as an ADR
  question rather than applied, since it changes the journal's public
  surface. The unit's most serious finding is a behaviour one and goes to
  `TODO.md`: on the journal-failure path the harness take-over runs outside
  the run-wide lock and still moves the adopter's data, where the unignored
  gate refuses the same act.
