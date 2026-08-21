# Verdict age belongs to `status`, never to the derived index

A verdict is a statement about the moment it was recorded; the index renders
it indefinitely. Six months later the column still says `current`, and
nothing in the method says "this answer is old". Staleness needs surfacing —
but *age is a function of now*, and the derived index must stay a pure
function of its inputs — the same units and the same log always yield the
same content, up to the `Derived:` stamp line that `--check` deliberately
ignores — or `--check` stops being a usable gate: an index that encodes
"how old is this today" changes checked content every day with no commit.

So age lives in `status`, the command already allowed to talk about the
present: `status --max-verdict-age N` (days, UTC, strict `age > N`) emits
WARNING findings naming unit, anchor and age; `--fail-on-aged` upgrades
**every finding the age check emits** — aged and `age unknown` alike — to a
gate: an enforced age bound cannot be satisfied by an age that cannot be
verified, and a rule that let `age unknown` pass would make an invalid
timestamp the way around the gate. `--as-of TIMESTAMP` substitutes "now" so
audits and tests are reproducible, defaulting to the real clock.

## Considered options

- **`derive --max-verdict-age`** — rejected. It injects the clock into the
  one artifact whose value is being deterministic. Every scheme to keep
  `--check` working around that (recording the cutoff in the index,
  re-deriving with the recorded time) reintroduces the age it was supposed
  to expire.
- **A fourth verdict value, `aged`** — rejected. The verdict domain is
  ternary and fail-explicit because it records *what the probe answered*.
  Age is not an answer; it is a property of when the answer was recorded.
  Widening the domain would force every reader of the log to reinterpret
  history each time the threshold changes.
- **Ordering "latest per anchor" by `recorded_at` instead of append
  order** — rejected. `recorded_at` is what `probe` happens to write, not
  something a reader can demand: records with an absent or invalid
  timestamp cannot be ordered at all, and re-sorting history means the same
  log reads differently once a bad timestamp appears. Append order never
  lies about what the log physically says.
- **Age computed in `status` from the log's `recorded_at`** — chosen, with
  the read contract extended explicitly: a record whose `recorded_at` is
  absent, invalid, or in the future yields `age unknown`, reported as a
  WARNING under the flag — and without the flag, nothing about reading the
  log changes at all.

## Consequences

- "Latest verdict per anchor" remains defined by append order, not by
  timestamp. A record appended later with an older `recorded_at` still
  wins; the age flag then reports it as aged instead of silently trusting
  it — surfaced, not hidden. The same holds after a git merge of two
  branches that both probed (the log is versioned, per the persistence
  policy): whichever physical order the merge produced decides the
  effective verdict, and the age check is the tool that surfaces a stale
  winner — not a reason to re-sort the log.
- The derived index never mentions age; its checked content stays a pure
  function of units and log.
- `--as-of` makes the aging rule testable end to end without a clock in the
  test seam.
