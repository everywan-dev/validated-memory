# validated-memory

A Claude Code plugin that keeps an agent project's knowledge trustworthy: what
is recorded, how it is proven, and how it stops being true without ever being
erased.

## Language

### The two layers

**Memory entry**:
One Markdown file recording one fact the agent should carry between sessions.
Its frontmatter is fixed and identical in every project (`name`,
`description`, `metadata.type`). Its identity is its filename; `name` is the
identifier wikilinks resolve against, and gives way to the filename when the
two disagree (ADR 0001).
_Avoid_: memory item, note, record

**Knowledge unit**:
One Markdown document in the curated layer, carrying identity, an evidence
state, and optional anchors separated from provenance. Its frontmatter is the
base contract plus whatever the project's own declared extension adds.
_Avoid_: unit of knowledge, article, doc

**Rationale**:
A knowledge unit's recorded question, considered options and reasons, including
the single chosen option. An option is not a knowledge unit; rejecting one does
not say that it is false or superseded.

There is deliberately **no umbrella term** covering both. They share neither
frontmatter, nor relations, nor the way each stops being true, so a single word
for both would hide three differences at once.

### Ceasing to be true

**Supersession**:
The relation that retires a fact by naming what replaces it. In the memory
layer it is written on the retired entry, by rewriting its `description` to
`superseded by [[name]]`; in the curated layer it is written on the replacing
unit, as `supersedes`, and one unit may supersede several.
_Avoid_: deletion, removal, archiving

**Successor**:
The memory entry or knowledge unit that a superseded one points at. It always
exists and is always a different one: supersession is a relation between two
records, never a flag on one.

Nothing is ever deleted, and there is **no obsolescence state**: this project's
language cannot say "this is no longer true" on its own, only "this is replaced
by that". A fact that simply stopped holding is retired by writing a successor
that records the change, and superseding the old one onto it.

**Repair**:
Correcting a record that was written wrong — a wikilink that resolves to
nothing, an index entry with no file, a duplicate `name`, a field outside its
domain. A repair never changes what a fact says; only what the world says
changes a fact, and that is supersession. The two are not interchangeable, and
the boundary between them is exactly what `lint` can already point at: if `lint`
cannot name it, it is not a repair.
_Avoid_: fix, edit, correction

### Proof

**Evidence state**:
How a knowledge unit is known: `measured`, `verifiable`, or `hypothesis`.
Declared by the author; not inferred.

**Anchor**:
The external thing a knowledge unit's claim depends on, named so it can be
re-checked later. Kept separate from provenance, which records where the claim
came from.

**Verdict**:
What a freshness probe returned for an anchor: `current`, `drifted`, or
`unknown`. Ternary and fail-explicit — a probe that could not answer says so
rather than guessing.

### Where memory lives

**Adopter project**:
A repository that has run `init`: it holds the Markdown data and configuration,
while all code stays in the plugin.

**Source**:
A body of existing knowledge a scan can be pointed at: a path the adopter
declared, a database named by the location of its definition, or a coherent
body found during the repository scan. It is what gets an alias and a
`memory/source-<alias>.md` record; it is never a single candidate's
provenance file, and never the claim a candidate makes.
_Avoid_: origin, input, knowledge base

**Harness memory location**:
The fixed path outside the project where Claude Code expects to read a
project's agent memory. The plugin makes it a symlink into the project, so the
data stays versioned in the repo while the harness reads it where it always
did.

### Checked consultation

**Binding**:
An attributed declaration connecting a knowledge-unit revision to its support,
qualified dependencies and scope of applicability.

**Consultation receipt**:
A retained account of the complete content returned for a knowledge-unit
consultation and the inputs against which it was checked. It does not establish
that a reader understood or correctly interpreted the content.

**Checked-use record**:
A historical account of a consumer conclusion's eligibility against a particular
consultation receipt. It does not establish perpetual validity or semantic truth.

**Current-use check**:
An observation of whether a historical checked use remains eligible against the
inputs available now. Earlier observations do not establish present eligibility.

### Incorporation and review

**Contribution proposal**:
A retained proposed knowledge unit with its evidence, applicability and intended
place in an adopter project. Submission does not make it canonical knowledge.

**Challenge**:
An attributed request to reconsider an exact knowledge-unit revision within a
stated scope. A factual challenge disputes justification; a policy challenge
requests reconsideration of a declared decision. Neither proves a replacement.

**Disposition**:
An attributed acceptance, rejection or deferral of a contribution proposal or
challenge. A later disposition retains the earlier decision rather than erasing it.

**Incorporation observation**:
A recorded check that an accepted proposal's exact content and declared evidence
have become canonical knowledge. Acceptance alone is not incorporation.

**Publication reflection**:
An attributed observation that a declared downstream artifact has been updated
to reflect a reviewed conclusion. Captured bytes do not prove semantic correctness
or delivery to external readers.

### Portable contributions

**Transfer capsule**:
A bounded portable representation of selected historical consultation material
and complete recorded workspace history. Receipt selection identifies the
contribution; it does not narrow the history disclosed by the capsule.

**Transfer origin**:
The original workspace, project, knowledge-unit identity and revision retained
alongside a locally incorporated contribution. It records historical lineage,
not authenticated authorship or a new adopter Source alias.

**Transfer link**:
An inspected, attributed correspondence between an imported origin and a local
contribution proposal, including evidence, dependency and predecessor mappings.
It does not establish semantic equivalence or replace local acceptance.

**Independent successor disposition**:
An inspected, attributed declaration that a local canonical successor no longer
depends on a named inherited transfer origin. It preserves the predecessor's
history; omission alone cannot discharge the dependency.
