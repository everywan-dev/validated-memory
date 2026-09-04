# What the review has gained, unit by unit

The counter for [the codebase review](../2026-09-03-codebase-review.md). Every
number here is produced by [`measure.py`](measure.py), never typed by hand:

```
python3 docs/plans/reviews/measure.py                    # the table now
python3 docs/plans/reviews/measure.py --against baseline # what has changed
```

Tokens are bytes/4 — an approximation, used to compare snapshots against each
other and never as an exact cost. "Prose" is comment bytes plus docstring
bytes.

**A gain is not only prose deleted.** A structural fix can add lines and still
be a gain, and this ledger shows both columns rather than the flattering one:
J1 removed 1 487 prose bytes from its own files and added 929 to J3, because
the fix that made the reporting module shallower gave the write-ahead-log
module two functions it should always have had.

## Baseline

Measured on `b80325e`, the commit before the first unit was reviewed. The
whole runtime plus the two large test files.

| # | unit | files | LOC | ~tok | comment | docstring | prose | pub / priv |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| J1 | journal: reporting and fault seams | 4 | 572 | 6,141 | 4,891 | 9,798 | 60% | 2 / 3 |
| J2 | journal: the vocabulary | 3 | 802 | 8,246 | 2,427 | 17,245 | 60% | 16 / 7 |
| J3 | journal: write-ahead log and lock | 2 | 1,010 | 11,159 | 5,722 | 21,157 | 60% | 4 / 15 |
| J4 | journal: the executor | 1 | 1,572 | 18,355 | 8,797 | 30,047 | 53% | 2 / 3 |
| S1 | the scaffolder | 2 | 1,236 | 13,840 | 5,621 | 26,754 | 58% | 2 / 23 |
| C1 | the contract | 5 | 1,295 | 11,157 | 1,784 | 7,844 | 22% | 14 / 35 |
| F1 | freshness | 5 | 1,048 | 9,829 | 1,473 | 14,125 | 40% | 18 / 20 |
| M1 | agent memory and corpus | 3 | 1,031 | 10,221 | 4,178 | 16,727 | 51% | 19 / 15 |
| V1 | the view stack | 7 | 1,415 | 15,821 | 7,192 | 23,637 | 49% | 12 / 23 |
| X1 | the entry point | 4 | 318 | 2,995 | 1,159 | 615 | 15% | 2 / 0 |
| T1 | the test surface | 2 | 6,760 | 70,254 | 41,195 | 56,153 | 35% | 180 / 28 |
| | **total** | 38 | 17,059 | 178,024 | 84,439 | 224,102 | **43%** | 271 / 172 |

**43 % of this code is prose**, and 53–60 % of every module in the journal
package. That is the number the review exists to move, and it is worth
recording how nearly it went unmeasured: the first version of `measure.py`
matched docstrings on the token stream and counted only module docstrings,
because the token before a function's docstring is the `:` of its own `def`.
It reported 16 %. The baseline above was re-derived from `b80325e` after the
tool was corrected to walk the AST, which is why a baseline column exists at
all rather than a "before" typed from memory.

## Per unit — what the review found and where it went

The primary count, because the problem the rule attacks is prose that goes
false (ADR 0010), not prose that is long. Filled by hand from each unit's
findings file; bytes are the secondary table below.

| Unit | Commit | history removed | claims already pinned | coverage gaps opened | tests added | false claims corrected | structural fixes |
|---|---|---:|---:|---:|---:|---:|---:|
| J1 | `db5700b` | 4 | 0 checked | 0 | 0 | 0 (1 claim verified true) | 2 |
| references | `ba3d461` | 0 | — | 0 | 4 pins | **26** | 0 |
| J2 | `74e02e1` … `a664597` | 5 | 5 | 3 | 2 | **4** | 3 |
| J3 | `db9edc6` … `7bbf8d8` | 3 | 3 | 0 | 0 | 0 | 3 |
| J4 | `f3d1def` … `e078864` | 6 | 3 | 0 | 1 | **5** | 4 |
| S1 | `d6d9d70` … `6fc9968` | 4 | 4 | 0 | 1 (+1 strengthened) | 2 | 2 |
| **total** | | **22** | **15** | **3** | **8** | **37** | **14** |

"claims already pinned" counts prose deleted because a test was found that
fails when the claim stops being true; J1 predates that step, so it was not
asked; J2's five are the five sentences it deleted, each against the test
named in `j2.md`. "false claims corrected" for the reference work counts 23
`design §N`
citations that named no document out of two candidates, and three pointers
into a plan archived out of the repository.

## Per unit — bytes

| Unit | Commit | LOC | ~tok | prose bytes | pub / priv | Tests |
|---|---|---:|---:|---:|---:|---|
| J1 | `db5700b` | −42 | −530 | −1,487 | ±0 | 644 green |
| J1 → J3 (cost of the fix) | same | +35 | +351 | +929 | +2 / ±0 | — |
| references | `ba3d461` | +~50 | +~500 | +~1,400 | ±0 | 648 green |
| J2 | `74e02e1` … `a664597` | +94 | +540 | +1,496 | +4 / +1 | 650 green |
| J2 → J3, S1 (cost of the fix) | same | +8 | +116 | +406 | ±0 | — |
| J3 | `db9edc6` … `7bbf8d8` | +8 | +109 | +751 | +11 / −10 | 650 green |
| J4 | `f3d1def` … `e078864` | +20 | +180 | +812 | −1 / +1 | 651 green |
| J4 → J3, J1 (cost of the fix) | same | +29 | +372 | +983 | ±0 | — |
| S1 | `d6d9d70` … `6fc9968` | +3 | **−130** | **−524** | +3 / −2 | 652 green |
| **running total** | | | | | | |

The reference work adds prose and is a gain: a citation that resolves is
longer than one that does not, and four new pins carry their own docstrings.
A ledger that only counted bytes would score it as a loss, which is why the
semantic table is the one above.

J1 left 9 310 prose bytes standing in its own four files, 59 % of them. What
came out was history. What stays is contract, and it is measured against the
three classes from J2 onward.

## J2, the first unit that grew

J2 is +94 LOC and +1 496 prose bytes, and it is the clearest case yet for
why the semantic table is the primary one. Five sentences of history came
out. What went in: five factory docstrings and a module docstring for
`journal/durable.py`, which between them replaced enforcement-by-paragraph
with enforcement-by-signature — three combinations `Intention`'s docstring
called impossible were constructible, and now have no spelling — and two
new tests, whose docstrings are prose that runs.

The four false claims are the three `Intention` combinations plus "step
1b", a pointer that survived the reference pass in two files because it is
neither a path nor an `ADR NNNN` and `test_docs_links` looks for those. The
five "claims already pinned" are those same deleted sentences, each
against a test that fails when its claim stops being true; the three coverage gaps are the facade's layering,
the schema refusal (both closed here, each seen red first) and the ordering
inside `install`, which stays open and now says so in its own docstring.

The +8 LOC in J3 and S1 are the cost of the fixes: a comment in
`transactions` explaining an order that now matters, and the four call
sites in `init.py` that name their mutation instead of assembling one.


## S1, the first unit since J1 to come out smaller

S1 is +3 LOC, −130 tokens and −524 prose bytes, and it is the only unit so
far where an extraction *deleted* prose instead of adding it. `ignore.py`
took about 175 lines of "does git ignore the vault, and how do I make it"
out of a module whose docstring says it scaffolds the adopter layout — and
the same contract had been written twice, once in `init.py`'s module
docstring and once in `_ensure_ignored`. Explaining it once is where the
bytes went.

The `pub / priv` column moves +3 / −2 and nothing new is reachable from
outside the package: `ignore.py` exports three names against the five
private ones it took out of `init.py`.

Its most serious finding is not in either column, because it is a behaviour
question and the unit did not answer it: on the journal-failure path the
harness take-over runs after the run-wide lock has been released and still
moves the adopter's data, where the other gate refuses the same act. See
`s1.md` A4 for the reproduction.

## J4, the second unit that grew, and the largest cost-of-the-fix so far

J4 is +20 LOC in its own file and +29 in two others, and the second number
is the interesting one. `classify` promised its callers "what the file and
the filesystem said, so the caller neither re-reads nor re-decides", and all
three of them re-read the raw transaction file for six more fields — two of
which they then type-checked a second time, against states `classify`
already refuses. Making the promise true is +26 LOC in `transactions.py`,
and it takes the raw file out of `_complete` and `_restore` entirely.

Its five false claims are the widest of any unit: one contract claim in the
module docstring measurably false ("recovery and resolution share every one
of those steps"), one promise in `classify`'s docstring that its own callers
disproved, and three copies of one constraint that named half its cause —
the `journal --resolve` preflight, which defends against `Lock` creating the
vault as well as against `_bootstrap` creating the journal. That third one
was found by measuring rather than reading, and it refuted the fix a
challenge had proposed for it.

The one test added is the best kind this axis produces: a comment that
described a traceback it had replaced, and nothing asserted the guard was
still there. It was seen red first.

## J3, and what the `pub / priv` column stopped meaning

J3's row reads +11 public and −10 private, and nothing became reachable from
outside the package. [ADR 0011](../../adr/0011-inside-the-journal-package-an-underscore-means-no-other-module-may-depend-on-it.md)
took the leading underscore off the twenty-one names that other modules of
the journal already depended on, so `measure.py`'s split — which counts a
name as private when it starts with `_` — now measures what a module keeps
to itself rather than what the package keeps to itself. What the package
publishes is `journal.__all__`, twenty-one names, unchanged by J3 and pinned
by `test_the_facade_exports_exactly_the_surface_the_pin_permits`.

J3 removed three sentences and added none of its own beyond two docstrings
that state a refusal five callers were not told about. The +751 prose bytes
are those two, the ADR pointer in the test constant, and the paragraph that
turned a magic ten into `LOCK_WAIT_SECONDS`.
