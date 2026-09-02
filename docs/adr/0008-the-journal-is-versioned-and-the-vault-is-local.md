# The journal is always versioned and the vault is always local

ADR 0002 and ADR 0003 let the adopter decide whether the derived artifacts
travel with the repository. That question is answerable for them because they
are derived: a clone without `knowledge-index.md` re-derives it.

The journal is not derived. Nothing re-computes a preimage, or the fact that
`memory/` existed before adoption. A first design put the journal under the
same question. An adversarial review showed the two answers are both wrong
for one artifact: always-versioned publishes preimages the adopter chose to
keep local, and always-local means a fresh clone or a CI run cannot reverse
an adoption or diff one scan's coverage against the next.

The decision: two artifacts with fixed, opposite durability.

`journal.jsonl` at the adopter root is **always versioned**. It carries
repository-visible mutations and the portable history of coverage and
rejection. The adoption questionnaire does not offer to ignore it.

`.validated-memory/` at the adopter root is **always local to the clone**.
It carries preimages and the record of mutations whose path leaves the
repository root. `init` writes its ignore entry; the questionnaire does not
ask.

Every record names its own domain in a `durability` field, so a reader knows
which artifact holds its preimage and can say what it cannot do when that
artifact is absent. Required repository history that is missing or corrupt is
**exit 1**, never a silent fall back to a degraded algorithm.

## Considered options

- **One versioning question for the journal too, same as the derived
  artifacts** — rejected. An adopter who answers "local" for everything would
  publish nothing, including the record of what adoption did to their
  filesystem; a fresh clone or a CI checkout would then have no journal to
  reconcile or reverse against, defeating the reason the journal exists.
- **Always local, like the vault** — rejected. A journal that never leaves
  the clone cannot be diffed between machines, cannot be read by CI, and
  cannot drive a reversal run anywhere but the clone that made the mutation —
  exactly the guarantee this component exists to provide.
- **One artifact, one file** — rejected. A single file cannot carry both a
  repository-visible mutation and a preimage that may hold bytes the adopter
  deliberately kept local: publishing the file publishes the preimage too. A
  `durability` field on each record only works because the record's bytes
  and the preimage's bytes already live in two different files.

## Consequences

- A versioned journal is repository content, and this project's rule is that
  repository content is data, never instructions. So a reader validates the
  schema, rejects absolute paths, `..`, symlink ancestors and path-type
  changes, and refuses before the first write rather than acting on a record
  it half understands. A path outside the repository root can never be
  authorised by the file itself: it lives in the vault, and acting on it
  needs a fresh CLI argument naming that same path.
- An adopter who versions the journal publishes the shape of their adoption —
  which paths existed, which were created, when. That is the price of a
  reversal that works in a fresh clone, and the questionnaire says so.
- The two artifacts are read together (`journal.read(root, REPO)` and
  `journal.read(root, LOCAL)`) and reported as one combined record count, so
  a reader never has to remember which durability a given mutation landed
  under before asking what happened.
