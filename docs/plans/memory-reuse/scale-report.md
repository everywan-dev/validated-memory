# Scale and ceiling evidence

Terminal engineering repeated the same check after fixing failure-progress
accounting. [Final results](scale-results-final.json) identify the successor
runtime: 10,000 units in 0.4268 seconds at 67,840 KiB peak RSS. Document-count
failure now reports enumerated 20,001/read 20,000; aggregate-byte failure
reports enumerated 65/read 65. Entry enumeration failure retains unknown totals.
All four refusals still returned valid bounded JSON and no results. Differences
between timings are ordinary single-run observations, not an optimization claim.

The initial evidence below is retained, including the then-incorrect null
progress counters, so the correction and its effect remain auditable.

Architect-run CLI subprocess checks, 2026-09-09. Runtime digests, platform,
raw timings, RSS and coverage are in [scale-results.json](scale-results.json).
The reproducible standard-library test is [check_scale.py](check_scale.py);
run it from this checkout with Python. It uses `/usr/bin/time` for per-child
maximum RSS and creates/cleans its own temporary synthetic adopter trees.

| Synthetic knowledge units | CLI elapsed seconds | Peak RSS KiB |
| --- | ---: | ---: |
| 100 | 0.0612 | 20,780 |
| 1,000 | 0.0918 | 25,276 |
| 10,000 | 0.4536 | 67,664 |

Each query matched every unit and returned bounded JSON within 12,288 bytes.
These are single observations on the recorded local Linux WSL2 execution host,
with Python 3.12.3 and freshly written filesystem-cached fixtures. Timing
includes CLI startup but excludes fixture creation; other local work was
running. No cold-cache, sustained-load or production latency claim follows.

All four independently exercised ceilings returned exit 1, valid bounded JSON,
no usable results and the expected diagnostic: 20,001 documents; 50,001 ordinary
directory entries; 65 one-MiB documents exceeding 64 MiB total; and a 318-by-318
supersession graph exceeding 100,000 redirect associations. Deep-chain, depth
64/65 and one-MiB/16-MiB cases also belong to the permanent CLI test suite.

The large fixtures completed promptly; the implementation handoff's suggestion
that these checks necessarily require minutes was not supported by this run.
The initial input-limit failure coverage lost known totals before a tree was
returned. Reviewer finding C1 identified that mismatch; the successor above
resolves it and permanent tests pin progress through partial acquisition.
