# The journal: coverage, rejection and reversal — design (2026-08-30)

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
`uninstall` (`2026-08-30-uninstall-and-restore-design.md`, now superseded by
this document) claimed the inventory could be computed at uninstall time. An
adversarial review showed that is false: the final state is computable, the
**preimage** is not. Whether `memory/` existed before adoption, what bytes
`render` overwrote, where the harness symlink pointed, which `.bak` belongs
to this adoption, whether a bare `/verdicts.jsonl` ignore line was written by
the skill or by the user — none of it survives.

Both problems are one problem: **the system does not write down what it did.**
So it cannot be contradicted later, and it cannot be undone.

The answer is one artifact, owned by the CLI, from which coverage and
reversal are two readings.

The standing rules bind: Python 3 stdlib only, English everywhere, exit codes
0/1/2, end-to-end subprocess tests only, the CLI is enforcement and judgment
lives in skills, every invocation is `python3 -P -m validated_memory` (ADR
0006).

## 1. The journal

`journal.jsonl` at the adopter's root: append-only, one JSON object per line,
written by the CLI module that performs the mutation. Never rewritten, never
compacted, never sorted — an appended log is the only shape that cannot lose
history by accident.

It follows `verdicts.jsonl` in every structural respect, including the
versioning question the adopter already answers for the derived files (ADR
0002, ADR 0003): version it and CI can diff it; do not, and it stays local to
the clone. Unlike the derived files it is **not** regenerable, which the
adoption questionnaire must say out loud when it asks.

Every record carries `at` (ISO-8601 UTC), `version` (the plugin version that
wrote it), `run` (a per-invocation id, so one command's records group), and
`kind`. The kinds:

- **`created`** — a path the plugin brought into existence. Reversal removes
  it.
- **`existed`** — a path the plugin found and kept. Reversal leaves it.
  Written once, on first sight, because "it was already here" is a fact about
  the pre-adoption state and cannot be re-derived afterwards.
- **`overwrote`** — a path whose previous content the plugin replaced,
  carrying the preimage (§2).
- **`appended`** — a path the plugin added to rather than replaced, carrying
  the byte length before the first append, so the pre-adoption prefix stays
  identifiable. `verdicts.jsonl` is the case this exists for.
- **`linked`** — a symlink created or re-pointed, carrying its previous
  target or its previous absence.
- **`absorbed`** — a harness memory directory taken into the project,
  carrying the exact backup name chosen (`.bak`, `.bak.1`, …) and the files
  moved.
- **`edited`** — a region written into a file the adopter owns, carrying the
  file, the marker pair, and the preimage of the region including its
  surrounding whitespace (§4).
- **`ignored`** — an ignore entry the plugin appended, carrying the exact
  line and the file it went into. This is what makes a bare
  `/verdicts.jsonl` line attributable, which no computation can do.
- **`coverage`** — one scan's coverage ledger (§3).
- **`rejected`** — a candidate that was read and not proposed, or proposed
  and declined (§3).

## 2. Preimages

A `created` record needs no preimage: reversal is removal. An `existed`
record needs none either. `overwrote`, `edited` and `absorbed` do.

Preimages are parked once — on the **first** time the plugin overwrites a
given path, never again, because only that first copy is the pre-adoption
state. They live under `.validated-memory/preimages/<original path>`, a
plugin-owned directory that reversal empties and removes. A text region small
enough to fit goes inline in the journal record instead; the threshold is a
detail for the plan, not for this design.

## 3. Coverage and rejection, as journal kinds

The coverage ledger of 1.5.1 is a section of a report that exists for one
message and then is gone. Nothing can contradict it later, which is exactly
why "I inventoried 518 files" is an unfalsifiable claim. Appended to the
journal, it becomes falsifiable: the next scan's ledger is diffed against it,
and a disagreement about what exists in a directory is a finding.

A `coverage` record carries, per scan partition, the counts that must
balance, and the per-directory breakdown of the repository remainder. It
splits the bucket 1.5.1 got wrong. `classified` becomes three:

- **`read_proposed`** — opened, judged, yielded a candidate.
- **`read_empty`** — opened, judged, yielded nothing. This is the honest
  "nothing here" and it is a claim, not an absence.
- **`surveyed`** — inventoried by path without being opened. 1.5.1's report
  declared 498 of these inside `classified` and said so in prose; the bucket
  now exists so it does not have to be confessed in a sentence.

with `excluded`, `oversized` and `unreadable` unchanged, and
`discovered = read_proposed + read_empty + surveyed + excluded + oversized +
unreadable`.

A `rejected` record is written for a candidate that got as far as being one
and was not taken — the ~20 that 1.5.1 dropped in triage without listing. It
carries the claim in one sentence, its source file, and the reason. And,
because the journal is not where a human looks, each also becomes a **memory
entry of type `reference`**, `rejected-<slug>.md`, which is the shape
`source-<alias>.md` already established for a process fact: one fact per
file, indexed in `MEMORY.md`, policed by `lint`.

When a rejected claim is later accepted, nothing is deleted: the unit is
written and the rejection record is superseded the memory layer's way, a
successor plus the marker in the old entry's `description`. A rejection is a
correction like any other, and the trail says "we looked, we said no, then
the evidence moved".

A rejection is also **re-checkable**: "this document closes no verdict as of
D" stops being true when someone updates the document. So a rejection record
may carry an anchor, and the freshness machinery that already exists answers
the question a long-lived project actually asks — *has anything we discarded
changed?*

## 4. Closing the prose seam

Two mutations sit behind a skill rather than a CLI module: the managed block
in `CLAUDE.md`/`AGENTS.md`, and the ignore entries. A journal a prose skill
must remember to write is a journal that will be incomplete, and the review
that produced this design was right that the rest of the write paths do not
have that problem because they are CLI-owned.

So the two edits become CLI-owned as well: a subcommand applies the canonical
marker-delimited block, and a subcommand appends an ignore entry, each
journaling as an inseparable part of doing it. The skill keeps the judgment —
whether to write the block, into which file, showing the diff, taking the
confirmation — and stops performing the edit itself.

**This reopens a decision.** The 1.5.0 design rejected "CLI subcommands for
the mechanics" (§9 of `2026-08-28-import-existing-knowledge-design.md`), and
ADR 0007 keeps adoption decisions out of `init`. Neither is contradicted: the
judgment stays in the skill, and `init` is untouched. What changes is that a
mechanical, canonical edit with an exact inverse is performed by the
component that can be tested end to end and that cannot forget to record it.
The alternative — the skill edits and then calls a second command to record —
has a step that can be skipped, which is the failure mode being designed out.

## 5. Reversal

`uninstall` reads the journal in reverse and undoes what it finds: restore
preimages, remove `created` paths, leave `existed` paths, re-point `linked`
symlinks to their former targets, restore `absorbed` backups, remove `edited`
regions using the recorded preimage rather than guessing at separator
whitespace, and remove exactly the `ignored` lines the plugin appended.

Everything that is not restored is **moved, not deleted**, to a destination
directory (default `remove-valmem/`), and the manifest of what moved travels
inside it.

Three corrections the review forced, each a defect in the superseded design:

- **The destination must not become a git exposure.** A layout the adopter
  chose to keep local was ignored by root-anchored rules (`/knowledge/`);
  moved to `remove-valmem/knowledge/`, those rules stop matching and a later
  `git add -A` publishes what the adopter deliberately kept private. The
  journal knows whether the layout was ignored, so uninstall mirrors that
  state into the destination — a `.gitignore` containing `*` when the layout
  was ignored, and nothing when it was versioned, so a versioned layout
  records an ordinary rename.
- **`--plan` must not poison its own execution.** The plan writes the
  manifest into the destination; the emptiness check therefore ignores a
  manifest written by a plan of the same run, and refuses on anything else.
- **Restoration is claimed only where it is earned.** Journal-backed
  reversal restores. On a project adopted before 1.6.0 there is no journal,
  so uninstall falls back to the computed inventory, moves the layout,
  removes only marker-delimited regions, and **says plainly that it is
  de-adopting rather than restoring**, naming what it cannot know: whether
  paths pre-existed, what was overwritten, where a symlink pointed.

The harness side stays out of scope, as decided, with one change the review
earned: the manifest's "Not touched" section names the dangling symlink and
the `.bak` by path, because documenting a hazard is not the same as
discharging it and the user should be able to act on it.

## 6. What this does not fix

A scan can still claim to have opened a file it never opened, and book it
`read_empty`. No report and no journal can contradict that from inside a
single run. What the journal buys is that the claim is specific, attributable
to a version and a run, and **diffable against the next run** — which is the
strongest guarantee available and is strictly more than 1.5.1 had.

## 7. Testing

The project's only seam: the CLI as a subprocess over fixture adopter trees.

- Every mutation kind produces its journal record; a mutation with no record
  fails the test that enumerates them against the CLI's write paths, the way
  the ignore list is already pinned against the CLI's root outputs.
- Adopt → uninstall restores a tree byte for byte, including a pre-existing
  `memory/`, a pre-existing HTML view, a harness symlink with a former
  target, and a `verdicts.jsonl` with a pre-adoption prefix.
- A layout that was ignored comes back ignored; the destination carries its
  `.gitignore` and `git status` shows nothing publishable.
- `--plan` then `uninstall` succeeds; `uninstall` onto an unrelated non-empty
  destination refuses.
- A legacy project with no journal de-adopts, and the output says
  "de-adopted, not restored" with the list of what could not be known.
- The coverage buckets balance, and a second scan over an unchanged tree
  produces a ledger that diffs clean against the first.
- A rejected candidate produces both a journal record and a `rejected-*`
  memory entry; accepting it later supersedes that entry rather than editing
  it.
