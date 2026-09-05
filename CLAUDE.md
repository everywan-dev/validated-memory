# validated-memory — development conventions

- Runtime code is Python 3, **standard library only**. pytest is the only
  development dependency.
- All content in this repo (code, comments, CLI messages, docs, skills) is
  written in **English**.
- This repo is self-contained: no references to internal company projects.
- Exit code convention: `0` = clean or WARNING-only findings; `1` = ERROR
  (gates); `2` = usage error.
- Code prose follows **ADR 0010**: every sentence in a file is a contract
  (what a caller must expect), a constraint (what a modifier must not break)
  or a verification argument (what the evidence is worth), and one that is
  none of the three is deleted. History is what no longer binds: what still
  binds stays, a decision goes to an ADR, an incident goes to the commit
  message. A claim about behaviour is pinned by assertions -- a test name and
  docstring do not execute -- and a documentary reference inside a `.py` is a
  versioned path or an `ADR NNNN`, pinned by `test_docs_links.py`.
- Testing seam: the CLI invoked as a subprocess over fixture adopter trees,
  asserting on exit codes, output, and produced files. Tests never import the
  package's internals.
- Work on `feature/*` branches; never force-push `main`.
- Commit messages: Conventional Commits, written in **English** (this
  repository is public and English-only, overriding the inherited
  Spanish-commits norm).

## Agent skills

The engineering-skill configuration is local to this checkout under
`.codex/docs/` and is not distributed with the repository. Read the following
files when present; their absence in another clone is expected.

### Issue tracker

Development issues, specifications, and testing are managed in GitLab;
validated work is subsequently published to GitHub. See
`.codex/docs/issue-tracker.md` for the workflow and tracker operations.

### Triage labels

Use the default Matt Pocock triage labels, mapped in
`.codex/docs/triage-labels.md`.

### Domain docs

This is a single-context repository: `CONTEXT.md` defines the vocabulary and
`docs/adr/` records decisions. See `.codex/docs/domain.md` for consumer rules.
