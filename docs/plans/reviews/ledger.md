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

## Per unit

| Unit | Commit | LOC | ~tok | prose bytes | pub / priv | Tests |
|---|---|---:|---:|---:|---:|---|
| J1 | `db5700b` | −42 | −530 | −1,487 | ±0 | 644 green |
| J1 → J3 (cost of the fix) | same | +35 | +351 | +929 | +2 / ±0 | — |
| **running total** | | **−7** | **−179** | **−558** | **+2 / ±0** | |

J1 left 9 310 prose bytes standing in its own four files, 59 % of them. What
came out was history — what a past bug did, what an older protocol was called
— which is the one rule already settled. What stays is contract prose, and how
much of it survives is what the comment ADR decides; the four files are the
worked example that decision can be taken against.
