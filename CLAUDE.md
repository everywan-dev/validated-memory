# validated-memory — development conventions

- Runtime code is Python 3, **standard library only**. pytest is the only
  development dependency.
- All content in this repo (code, comments, CLI messages, docs, skills) is
  written in **English**.
- This repo is self-contained: no references to internal company projects.
- Exit code convention: `0` = clean or WARNING-only findings; `1` = ERROR
  (gates); `2` = usage error.
- Code prose follows **ADR 0010**: a docstring states the contract a caller
  needs, a comment states the constraint a modifier needs, and history --
  what the code used to do, which bug a change answered -- goes to the commit
  message, never to the file. A sentence that asserts behaviour belongs in a
  test.
- Testing seam: the CLI invoked as a subprocess over fixture adopter trees,
  asserting on exit codes, output, and produced files. Tests never import the
  package's internals.
- Work on `feature/*` branches; never force-push `main`.
- Commit messages: Conventional Commits, written in **English** (this
  repository is public and English-only, overriding the inherited
  Spanish-commits norm).
