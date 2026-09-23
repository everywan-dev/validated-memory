# Journal history uses cross-artifact frontiers

## Context

`journal.jsonl` is versioned and is intended to use Git's union merge driver.
Union merge preserves lines, but it does not establish a serial history. Two
branches can start from the same recorded state, mutate the same path
independently, and merge into syntactically valid JSONL even though neither
mutation can follow the other. File order, timestamps and adoption ids do not
resolve that contradiction, so reversal must not use any of them as an
implicit ordering rule.

The history is split between the repository journal and the clone-local vault
journal. A parent chain per artifact is therefore insufficient: a repository
mutation can follow a local mutation, an ordinary checkout can temporarily
remove the repository half while retaining the local half, and a fresh clone
can lack local history that a repository record observed. Independently
adopted branches can also merge histories carrying different adoption ids.
That is explainable provenance, not automatically corruption, but it is not
automatically one reversible adoption either.

## Decision

Schema 2 history nodes carry a **frontier**: the complete set of repository
and local history heads known immediately before the node was written. A node
advances the history from that frontier and has a digest over the canonical
serialization of its closing record. A mutation's prepared and committed
records are the two sanctioned stage representations of one node and carry
the same frontier and node digest. Their stage-specific fields may differ;
every field the transaction-pairing contract requires to agree must still
agree. Observations and explicit reconciliation records are single closing
nodes.

Every frontier reference identifies its artifact (`repo` or `local`) and the
digest of its node. The node digest is SHA-256 over the UTF-8 bytes produced by
`json.dumps` from the complete closing record **without** its `node` field,
using `ensure_ascii=True`, sorted keys and separators `(',', ':')`; no trailing
newline participates. The closing timestamp and frontier do participate. The
writer constructs the closing projection first, then puts its digest on both
halves of a mutation, so the definition is not circular. Schema 1 anchors are
different: they digest the exact prefix bytes, including key order, spaces,
blank lines and line endings, because that prefix is retained as an opaque
physical artifact rather than reinterpreted as schema 2 nodes.

The frontier, rather than physical line order, defines causality. The topology
reader validates canonical digests, references, transaction pairs, adoption
lineage and acyclicity, and calculates the current heads. Identical duplicate
records introduced by a union merge are idempotent. The expected prepared and
committed representations sharing one digest are not conflicting duplicates.
Damage is two committed records whose projections claim one digest but differ,
unequal duplicates of the same stage, invalid stage multiplicity, or a
prepared/committed pair that disagrees on frontier, transaction, path,
operation, adoption lineage or another paired field. A cycle or an unexplained
identity transition is damaged too. A missing reference to the same artifact
as the record that carries it is also damage: that retained stream claims an
ancestor which its own artifact has lost. A missing cross-artifact reference
is instead an availability boundary. This covers both a local record whose
repository ancestor disappeared in a checkout and a repository record
observed without its clone-local ancestor. It retains the known adoption
lineage and permits later history to name the boundary, but reversal across it
is unavailable.

More than one unjoined head is a fork. `journal --check` reports it and every
topology-dependent mutation and reversal refuses it before writing. The
implementation never chooses a branch by line order, timestamp, path state or
adoption id. This is deliberately conservative: even apparently disjoint
mutations remain a fork until an explicit reconciliation establishes the
relationship that the original records did not contain.

Reconciliation is a future explicit journal operation, not a side effect of
`init` or recovery. It names every current head, selects the adoption lineage
for later records, and appends a join node without rewriting or deleting any
branch. Selecting that identity does **not** select one branch and discard the
others for reversal. The reconciliation must either record a complete,
path-state-continuous reversal order that accounts for every branch effect, or
record unresolved obligations for the effects it cannot order or whose
preimages are unavailable. The latter can make later appends unambiguous, but
reversal across the join still refuses. A successful reversal route includes
every branch effect and revalidates its recorded post-state against the
filesystem; a join node alone never makes reversal safe.

Adoption ids identify the lineage in which records originated. One ordinary,
unforked lineage still has one stable adoption id. Different ids are valid in
one merged repository history only when the topology retains their separate
roots and an explicit reconciliation joins them. A later id change inside a
lineage is damaged history. Before reconciliation, mixed lineages gate both
`init` and `journal --check`; a repository and vault that merely carry
different legacy ids retain the existing foreign-vault refusal. After
reconciliation, the selected lineage supplies the active id for later records.

The topology is owned by one private deep module. Its interface is conceptually
`inspect`, `append` and `join`: callers receive an immutable history
snapshot or a structured refusal and never construct frontiers, node digests
or adoption transitions. `adopting_run`, `journal --check`, targeted
resolution and the future reversal planner use that same inspection result,
so mutation and reporting cannot apply different identity or fork rules. The
public facade accepted by ADR 0024 does not change as part of this decision.

Schema 1 records are never rewritten. Each artifact's complete schema 1 prefix
becomes an opaque legacy anchor, identified by its exact byte digest; the first
schema 2 node observes both available anchors in its frontier. A schema 1
record after a schema 2 node is invalid. Every such snapshot permanently
carries `topology_unknown_before` for the anchored region: an ordinary first
schema 2 mutation can extend a clean single-id prefix, but it neither
reconciles that prefix nor makes it reversal-ready. `journal --check` and
`init` report the same bounded legacy condition, and reversal across it
refuses unless a future explicit checkpoint accounts for the old effects and
preimages. Multiple-id legacy prefixes still require explicit reconciliation
before a new mutation. No operation infers old causality from line order or
silently clears the unknown condition.

`init` will ensure the adopter's `.gitattributes` contains the canonical rule
`journal.jsonl merge=union`. It remains deliberately unanchored so an adopter
root nested below the repository root is covered, as the existing Git fixture
requires. Creating or appending that rule is an
ordinary journalled mutation through the executor, under a distinct merge-rule
purpose; an existing conflicting exact assignment is refused rather than
silently overridden. The attribute prevents textual conflict only. The
frontier still decides whether the merged result is semantically usable.

No union rule is added for `verdicts.jsonl`. Its current value is selected by
last record per anchor in file order, so union merging could change which
verdict wins. Verdict ordering needs its own decision.

## Discriminating cases

- Two clones advance the same frontier, then union-merge their repository
  journals: reporting names both heads, `--check` exits 1, and `init` and
  reversal perform no topology-dependent or journalled mutation. The declared
  fail-open harness-link repair keeps its existing narrow contract.
- Reordering schema 2 nodes after their legacy anchor does not change the
  calculated heads or findings.
- Exact duplicate stage records are counted once topologically. One valid
  prepared/committed pair shares a node digest; disagreement in their paired
  fields, unequal same-stage duplicates, or conflicting committed projections
  claiming that digest are damaged history.
- Separate adoption ids on independent roots are retained as distinct
  lineages. They become appendable only after a reconciliation naming every
  head; an unexplained later id switch remains damaged.
- A pre-adoption checkout with a surviving local anchor does not mint a new
  adoption id or call the missing repository reference corruption. A fresh
  clone lacking a local reference remains inspectable, but neither state can
  claim reversal capability it does not have. A missing same-artifact
  reference is damaged history.
- A clean single-id schema 1 prefix can be anchored without rewriting it, but
  retains `topology_unknown_before` and never becomes reversal-ready merely by
  running `init`. A mixed-id prefix and a schema 1 record after schema 2 gate
  before mutation; a topology cycle always gates.
- Reconciliation of branches that created disjoint paths either records a
  reversal order accounting for both paths or leaves obligations that make
  reversal refuse. It never reports successful reversal while leaving one
  branch's plugin-created path behind.
- The `.gitattributes` rule is written once through the journal and produces
  the expected union behavior in a real temporary Git repository. No rule for
  `verdicts.jsonl` is produced.

## Consequences

Parent hashes, file order and a project-wide equality test over every adoption
id are rejected as the history model. Frontiers cost additional metadata and
graph validation, but concentrate merge, identity and reversal-readiness rules
behind one interface. They detect accidental topology damage; because a writer
can recompute hashes, they are not authentication or tamper evidence.

This ADR decides the model and the implementation seam. It does not implement
schema 2, reconciliation or reversal, does not add group transactions, and does
not change current runtime behavior. Those changes require their own bounded
plan and black-box CLI tests over fixture adopter trees.
