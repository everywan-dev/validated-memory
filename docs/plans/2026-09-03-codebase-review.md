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

**Precondition for axis B:** the comment rule must be an ADR before the
second unit is reviewed. Without it, every session re-argues taste. Axis A
does not wait for it.

## The units

Sizes measured 2026-09-03 on `a0f2e4e`: runtime LOC, and the approximate
tokens a session pays to read the unit (bytes/4). Reviewing a unit also means
reading its tests, which are larger than the code — see the session protocol
for how not to load them whole.

| # | Unit | Files | LOC | ~tok | Why it is one unit |
|---|---|---|---|---|---|
| J1 | Journal: reporting and fault seams | `journal/reconcile.py`, `journal/command.py`, `journal/fault.py`, `journal/__init__.py` | 572 | 6.1 K | The pilot. Smallest journal unit and the prose-heaviest code in the repository (27 %, 20 %, 22 % comment bytes). Calibrates both axes cheaply. |
| J2 | Journal: the vocabulary | `journal/records.py`, `journal/paths.py`, `journal/operations.py` | 802 | 8.2 K | What a record, a path and an operation *are*. `records.py` has 11 public names — the widest interface in the package, and the first depth question. |
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
   small enough to read at every session start.
6. Apply what is decided. Defer what needs a decision, and say where it went.
7. Run `python3 -m pytest`. **644 tests, all green, before any claim.** A
   comment whose deletion breaks a test was a pin in the wrong place — record
   that; it is an axis-B finding of the best kind.
8. Commit, merge to `main` when green, push to **both** remotes.
9. Tick the checkbox below with the commit, add a dated `SESSION.md` entry,
   and update `TODO.md` if anything was deferred.

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

- [ ] **ADR: what a comment is for in this repository** (precondition for
      axis B from J2 onwards). Owner: user, via `/grill-with-docs`.
- [ ] J1 — journal reporting and fault seams
- [ ] J2 — journal vocabulary
- [ ] J3 — write-ahead log and lock
- [ ] J4 — the executor
- [ ] S1 — the scaffolder
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
