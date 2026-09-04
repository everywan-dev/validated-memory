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
| **total** | | **9** | **5** | **3** | **6** | **30** | **5** |

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
six "claims already pinned" are the sentences deleted because a test
asserts the same thing; the three coverage gaps are the facade's layering,
the schema refusal (both closed here, each seen red first) and the ordering
inside `install`, which stays open and now says so in its own docstring.

The +8 LOC in J3 and S1 are the cost of the fixes: a comment in
`transactions` explaining an order that now matters, and the four call
sites in `init.py` that name their mutation instead of assembling one.
