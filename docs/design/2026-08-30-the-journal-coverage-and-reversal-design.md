# The journal: coverage, rejection and reversal — design (2026-08-30, rewritten 2026-08-31)

Two problems arrived at the same answer on the same day, so they are designed
together.

**The reliability problem.** Every failure this method has produced in the
field was a silent narrowing. A scan that never read the repository and said
nothing (1.5.0). A scan that inventoried 951 files and then dropped them
between the coverage ledger and the candidate list (1.5.1). Twenty candidates
triaged away with no list of what they were. A `classified` bucket
conflating "read and found nothing", "read and discarded" and "never
opened". In each case the system recorded what it accepted and recorded
nothing about what it rejected, so the narrowing had nowhere to become
visible. For a method whose whole promise is that knowledge is not lost, this
is the defect that matters.

**The reversal problem.** Adoption mutates a project — a layout at the root,
a managed block in files the adopter owns, ignore entries, an absorbed
harness memory directory — and nothing reverses it. The first design for
`uninstall` (`2026-08-30-uninstall-and-restore-design.md`, superseded by this
document) claimed the inventory could be computed at uninstall time. An
adversarial review showed that is false: the final state is computable, the
**preimage** is not. Whether `memory/` existed before adoption, what bytes
`render` overwrote, where the harness symlink pointed, which `.bak` belongs
to this adoption, whether a bare `/verdicts.jsonl` ignore line was written by
the skill or by the user — none of it survives.

Both problems are one problem: **the system does not write down what it did.**
So it cannot be contradicted later, and it cannot be undone.

The answer is an append-only record, owned by the CLI, from which coverage
and reversal are two readings. This document's first draft claimed that one
artifact could serve both. A second adversarial review (Codex SOL, xhigh,
2026-08-30) rejected that draft for implementation and was right on all ten
of its findings, each verified against the code. §9 records what it changed.
The load-bearing correction: **the record is two artifacts, not one**,
because portable history and local preimages have incompatible durability,
and **the CLI does not own the mutations today**, so the seam that makes any
of this true has to be built before the journal means anything.

The standing rules bind: Python 3 stdlib only, English everywhere, exit codes
0/1/2, end-to-end subprocess tests only, the CLI is enforcement and judgment
lives in skills, every invocation is `python3 -P -m validated_memory` (ADR
0006).

## 1. The two records

Durability is not one question. A record that must reach a fresh clone and a
preimage that must never leave this machine cannot live in the same file, and
the adopter cannot be asked to choose: the choice that is right for one is
wrong for the other.

**`journal.jsonl`, at the adopter root, always versioned.** Repository-visible
mutations, and the portable history of coverage and rejection. It is not
subject to the versioning question the adoption questionnaire asks about the
derived files (ADR 0002, ADR 0003), and the questionnaire says so: unlike
`knowledge-index.md` or the HTML views it is **not regenerable**, and unlike
`verdicts.jsonl` it is not a log that can be rebuilt by re-running anything.

**`.validated-memory/`, at the adopter root, always local to the clone.**
Preimages, and the records of mutations that never were repository-visible —
the harness symlink, an absorbed directory, anything whose path leaves the
repository root. Always in the ignore file, written by `init` rather than by
the adoption questionnaire, because it is not a choice.

Every record names its own domain in a `durability` field, `repo` or `local`,
so a reader knows which artifact holds its preimage and can say what it
cannot do when that artifact is absent. Required repository history that is
missing or corrupt is **exit 1**. It never selects the legacy algorithm: a
degraded reversal that reports success is the failure this whole design
exists to remove.

Both are append-only, one JSON object per line, never rewritten, never
compacted, never sorted. An appended log is the only shape that cannot lose
history by accident.

Every record carries `schema` (the record format version), `at` (ISO-8601
UTC), `version` (the plugin version that wrote it), `adoption` (an id minted
once, at adoption, stable across every later run), `run` (a per-invocation
id, so one command's records group), `durability`, `op` and `purpose`.

## 2. Operations and purposes

The first draft's kinds mixed three different things: file operations
(`overwrote`, `appended`), domain events (`absorbed`), and observations
(`existed`, `coverage`, `rejected`). `ignored` was an append whose only
distinguishing feature was why it happened. That taxonomy cannot be tested
against the write paths, because there is no way to say "every write path has
a kind" when a kind is sometimes a whole workflow.

Two independent axes instead.

**`op`** — what happened to a path, and what its inverse is:

| `op` | Inverse |
|---|---|
| `observe` | none; it is a fact about the pre-state, not a mutation |
| `create` | remove |
| `replace` | restore the preimage |
| `patch` | restore the preimage of the region |
| `append` | truncate to the recorded prior length |
| `link` | restore the previous target, or the previous absence |
| `rename` | rename back |
| `remove` | restore the preimage |
| `move` | move back |

**`purpose`** — which part of the method did it: `init`, `render`, `verdict`,
`knowledge-unit`, `memory-entry`, `memory-index`, `supersession`,
`source-record`, `instruction-block`, `ignore-rule`, `absorption`,
`coverage`, `rejection`, `uninstall`.

`observe` carries the pre-adoption facts that cannot be re-derived: that a
path already existed, that a directory was empty, that a symlink pointed
somewhere. Written once, on first sight.

This axis split is what makes §8's completeness test meaningful: every write
path in the package maps to an `op`, and the test enumerates them.

## 3. The transaction interface

The first draft asserted that the CLI owned every mutation except two prose
edits. That is false, and the review named the file that says so:
`skills/bootstrap-from-repo/SKILL.md:316` — source records are "written here
and never by `init` or any subcommand". The CLI has seven subcommands and not
one of them writes a knowledge unit, a memory entry, an index line or a
supersession. Every confirmed write in this method is performed by a skill,
in prose, today.

A journal a prose skill must remember to write is a journal that will be
incomplete. So the seam moves: **every confirmed write goes through one CLI
transaction interface**, which journals inseparably from doing.

The interface takes the approved content plus the expected pre-state, and
either applies the whole set or nothing:

- the unit or entry to write, as content, not as instructions;
- its index line, because an unindexed entry is a `lint` ERROR and the two
  are one change;
- a supersession, as the successor plus the marker written into the old
  entry's `description`, which is also one change;
- an expected preimage digest for every path it will touch.

A digest that does not match is exit 1 and nothing is written: the file
changed under the skill between the page it showed and the confirmation it
took, and applying anyway would overwrite a change the user never saw.

**What stays in the skill is judgment**: what to propose, how to page it,
which target file the instruction block goes into, and taking the
confirmation. What moves is the mechanical, canonical, exactly-invertible
edit — the part that can be tested end to end and that cannot forget to
record itself.

This reopens the 1.5.0 ruling against "CLI subcommands for the mechanics"
(§9 of `2026-08-28-import-existing-knowledge-design.md`), and the review
confirmed the reopening is justified rather than rationalised: that ruling
said "rejected for now" and named misapplication by skills as the reason to
revisit. ADR 0007 keeps adoption *decisions* out of `init`; it does not
prohibit a non-interactive mechanical command. The interface must not accept
caller-supplied markers, block bodies or an append-versus-replace mode:
those would smuggle policy back through the argument list. For the
instruction block it accepts only the target — constrained to root
`CLAUDE.md` or `AGENTS.md` — and the expected preimage digest from a
read-only plan.

New subcommands change the set pinned at `tests/test_skills_structure.py:29`,
and an uninstall skill changes the seven-skill set pinned at line 76. Both
pins move in the same change that adds the commands, deliberately.

## 4. Crash consistency

Neither one-record protocol works. Record first and a crash leaves the
journal claiming a state that never existed; mutate first and a crash leaves
an unjournaled mutation. Both are the silent narrowing this design exists to
remove, in a smaller window.

So every mutation is two records:

1. `prepared`, appended and flushed, carrying the preimage reference and the
   expected postimage digest;
2. the atomic mutation itself: write to a temporary file in the same
   directory, `fsync`, `rename`. `render` already writes this way with a
   pid-named temporary and `os.replace` (`render.py:284`); the `fsync` is
   the addition, and it is what makes a `committed` record mean the bytes
   are on disk rather than in a buffer;
3. `committed`, appended and flushed.

A `prepared` with no `committed` is what recovery reconciles: compare the
path's actual bytes against the recorded preimage and postimage digests, and
report which of the three states it is in. Recovery reports; it does not
guess.

**The journal's own creation** is the bootstrap case and cannot journal
itself. It is written complete to a temporary file, flushed, and atomically
installed as `journal.jsonl` before any adopter mutation. Bootstrap
temporaries, and the temporaries `init` and `render` already use, are
explicitly outside the recursive rule: a plugin-owned meta-path is not an
adopter mutation. Leftovers from a hard kill are removed on the next run and
that removal is not journaled either.

**A per-adopter lock** is required, not optional. `init` is deliberately
re-runnable at startup and concurrent renderers are already expected. Two
processes appending interleaved `prepared` records to the same file would
produce a journal that describes no state that ever existed.

## 5. Coverage

The coverage ledger of 1.5.1 was a section of a report that existed for one
message and then was gone. Appended to the journal it becomes falsifiable —
but the review was right that diffing one run against the next catches an
*inconsistent* scanner, not a consistently false `read_empty`. The first
draft called next-run diffing "the strongest guarantee available". It is not,
and the affordable stronger one is this:

**Deterministic CLI code enumerates.** The CLI walks the eligible tree,
opens every eligible file, and records path, byte count and digest. The
classifier does not get to decide what exists; it receives the enumeration.

**Every disposition is bound to a digest.** The classifier must return one
disposition per enumerated path, keyed by that path's digest. A missing
disposition is exit 1 and the scan does not close. This is precisely the
1.5.1 failure — 951 files inventoried, dropped between the ledger and the
candidate list — made impossible rather than merely reported.

**A sample is re-read independently.** A random fraction of the enumeration
is re-dispatched to a second read, and a disagreement is a finding.

This proves delivery and opening. It does not prove judgment, and the design
says so: a classifier that consistently lies about what it found in a file
it genuinely opened survives all of it. Closing that would need a second
independent classifier over every file — roughly twice the scan cost — and
is not taken.

The dispositions replace 1.5.1's `classified`, which conflated three things:

- **`read_proposed`** — opened, judged, yielded a candidate.
- **`read_empty`** — opened, judged, yielded nothing. A claim, not an absence.
- **`surveyed`** — enumerated by path without being opened. 1.5.1 declared
  498 of these inside `classified` and confessed it in prose.

with `excluded`, `oversized` and `unreadable` unchanged, and
`discovered = read_proposed + read_empty + surveyed + excluded + oversized +
unreadable`.

## 6. Rejection

A candidate that was read and not proposed, or proposed and declined — the
~20 that 1.5.1 dropped in triage without listing — gets a `rejection` record
carrying the claim in one sentence, its source path, the source digest, and
the reason.

The first draft also projected each rejection into a memory entry of type
`reference`, with an anchor, "and therefore a probe". Both halves are wrong
under the current contracts, and the review verified both: `probe` visits
anchors only on **active curated-knowledge units** (`probe.py:72`), so an
anchor on a memory entry would be inert and unvalidated; and a memory
supersession must resolve to **another memory entry**
(`docs/reference/agent-memory.md:104`), so an accepted rejection could not
point at the unit that accepted it. Beyond correctness, ~20 entries per run
means the rejection surface dominates the accepted surface on the first
scan, and `lint` parses every memory file on every run.

So rejections do not enter the memory layer. They live in the journal, and a
**derived view of unresolved rejections** is generated from it the way every
other view in this project is derived — regenerable, never authoritative.
Rejections deduplicate by a stable identity, source path plus claim digest,
so re-running a scan over an unchanged tree does not grow the history. When
a rejected claim is later accepted, the journal records the acceptance
against that identity and the claim leaves the unresolved view; nothing is
deleted.

## 7. Reversal

`uninstall` reads both records in reverse and inverts each `op` by the table
in §2: restore preimages, remove `create` paths, leave `observe` paths,
truncate `append` paths to their recorded prior length, restore `patch`
regions from the recorded preimage rather than guessing at separator
whitespace, and remove exactly the ignore lines the plugin appended.

Everything not restored is **moved, not deleted**, to a destination
directory (default `remove-valmem/`), with the manifest travelling inside it.

**The journal is not deleted and not moved first.** It is read, validated,
and moved into the destination **last**, after the manifest is durable. That
is the only ordering that leaves recovery evidence if reversal stops midway.

**Post-state conflicts refuse.** Before restoring a path, uninstall compares
its current bytes against the recorded postimage. A mismatch means the user
changed the file after adoption; uninstall relocates it rather than
overwriting a change the record cannot account for.

### A versioned journal is data, never instructions

`journal.jsonl` is repository content, and this project's own rule
(`skills/bootstrap-from-repo/SKILL.md:16`) is that repository content is data
and never instructions. A reversal that replayed paths out of a versioned
file would violate it. So:

- the schema is versioned and validated before anything is written;
- absolute paths, `..`, symlink ancestors and path-type changes are rejected;
- every repository-relative record must resolve below the resolved root
  without following a symlink out of it;
- preimage and postimage digests are required, and a missing blob is a
  refusal, not a skip;
- a truncated final line, an unknown `op`, or a duplicate record is a
  refusal;
- a path outside the repository root can never be authorised by the file
  itself. It lives in the local record, and acting on it requires a fresh CLI
  argument naming that same path.

A malformed or hostile journal fails **before** the first write.

### The harness stays out

The first draft promised to restore `linked` and `absorbed` state and, thirty
lines later, declared the harness out of scope. Both cannot be true. The
ruling is: **out.**

`uninstall` does not touch `~/.claude/projects/<slug>/`. The harness
mutations are still recorded — `link`, the `rmdir` of an empty directory
(`adopt.py:39`), the parent `mkdir` (`init.py:232`), the copies and the
`.bak` rename — because recording them costs nothing and they are exactly
what a later decision would need. They are simply not inverted.

The manifest's "Not touched" section names by path the dangling symlink and
the `.bak` that holds the user's pre-adoption harness memory, so the user can
act on it. Documenting a hazard is not discharging it, and the manifest says
which it is doing.

Note also that the first draft's `absorbed` kind described "the files moved".
The code copies (`adopt.py:108`), reconciles the index (`adopt.py:147`), then
renames the original aside (`adopt.py:212`). Three operations under §2's
axes, not one domain event.

### The destination and git

Moving an ignored layout to `remove-valmem/` breaks the root-anchored ignore
rules that were hiding it (`/knowledge/` does not match
`remove-valmem/knowledge/`), so a later `git add -A` would publish what the
adopter deliberately kept private. Uninstall therefore writes a `.gitignore`
containing `*` into the destination when the layout was ignored, and nothing
when it was versioned.

The review narrowed what that can promise, and the narrowed claim is the one
the design makes: it prevents **accidental** exposure through ordinary git
commands. A destination-local `*` does beat a parent `.gitignore` negation
and `core.excludesFile`, because the lower-level file wins. It does **not**
cover already-tracked paths, `git add -f`, or a gitlink. So uninstall
inspects the actual index state with `git ls-files`, including mode
`160000`, rather than inferring safety from "the layout was ignored", and
refuses a destination that is a registered submodule.

**`--plan` must not poison its own execution.** The plan writes the manifest
into the destination, so the emptiness check ignores a manifest written by a
plan of the same run and refuses on anything else.

### Before 1.6.0 there is no journal

A project adopted earlier has no record, so uninstall falls back to the
computed inventory, moves the layout, removes only marker-delimited regions,
and says plainly that it is **de-adopting rather than restoring**, naming
what it cannot know: whether paths pre-existed, what was overwritten, where a
symlink pointed. It exits **1**, not 0, so automation cannot read a degraded
reversal as a success.

## 8. What this does not fix

A scan can still book `read_empty` on a file it genuinely opened and
genuinely misjudged. §5 closes delivery and opening, not judgment. What the
journal buys beyond that is that the claim is specific, digest-bound,
attributable to a version, a run and an adoption, and diffable against the
next run.

Also unresolved and named rather than hidden: file modes and node types —
"byte for byte" does not restore an executable bit; multiple adoption epochs
on one repository, which the `adoption` id makes detectable but which
uninstall does not yet reason about; and schema migration across plugin
versions, for which `schema` exists but no migration is written.

## 9. What the review changed

Codex SOL, xhigh, 2026-08-30, against the first draft: verdict *reject for
implementation in its current form*, ten findings. Every one verified against
the code before acting on it. All ten accepted; four went to the architect as
rulings, and all four took the review's recommendation.

| Finding | Ruling |
|---|---|
| The "CLI owns every mutation" claim is false; `bootstrap-from-repo` writes units, entries, index lines and supersessions directly | §3: one transaction interface for every confirmed write, not just the two prose edits |
| No crash-consistent ordering, including for the journal's own creation | §4: `prepared`/`committed`, atomic install of the journal, meta-path exemption, per-adopter lock |
| One mixed artifact cannot satisfy the durability requirement | §1: two records, `journal.jsonl` always versioned, `.validated-memory/` always local, `durability` on every record, missing history is exit 1 |
| A versioned journal becomes untrusted executable instructions | §7: schema validation, path constraints, digests, fresh authorisation for an outside-root path |
| The harness is simultaneously restored and out of scope | §7: out. Recorded, not inverted; named by path in the manifest |
| "Next-run diffing is the strongest guarantee available" is false | §5: CLI enumerates, dispositions bound to digests, sampled re-read; the residue named in §8 |
| Rejection anchors are inert and the memory projection swamps the layer | §6: journal plus a derived view; no memory entry per rejection |
| The destination `.gitignore` guarantee is too broad | §7: claim narrowed to accidental exposure; `git ls-files` inspected; submodule refused |
| The kind taxonomy mixes operations, observations and domain purposes | §2: `op` and `purpose` axes |
| §4's reopening of the 1.5.0 ruling is justified, but the interface was incomplete | §3: interface constrained to target plus expected digest; no caller-supplied markers or block bodies |

Nothing was rejected. The one claim of the first draft the review called
correct and this rewrite keeps unchanged is the diagnosis: the system does
not write down what it did.

## 10. Testing

The project's only seam: the CLI as a subprocess over fixture adopter trees.

- Every write path in the package maps to an `op`, enumerated by a test the
  way the ignore list is already pinned against the CLI's root outputs. A
  mutation with no record fails it.
- Adopt → uninstall restores a tree byte for byte: a pre-existing `memory/`,
  a pre-existing HTML view, a `verdicts.jsonl` with a pre-adoption prefix.
- A `prepared` record with no `committed` is reconciled, and the three
  possible states are each reported correctly.
- Two concurrent invocations do not interleave records; the lock holds.
- A layout that was ignored comes back ignored; the destination carries its
  `.gitignore` and `git status` shows nothing publishable. A tracked
  destination and a submodule destination are refused.
- `--plan` then `uninstall` succeeds; `uninstall` onto an unrelated non-empty
  destination refuses.
- A malformed journal — absolute path, `..`, unknown `op`, truncated last
  line, missing preimage blob — fails before the first write.
- A post-state conflict relocates rather than overwrites.
- A legacy project with no journal de-adopts, says "de-adopted, not
  restored", lists what could not be known, and exits 1.
- The coverage buckets balance; a disposition missing for an enumerated
  digest fails the scan; the sampled re-read reports a disagreement.
- A rejection produces a journal record and no memory entry; the unresolved
  view lists it; a re-run over an unchanged tree does not duplicate it; a
  later acceptance removes it from the view without deleting the record.
