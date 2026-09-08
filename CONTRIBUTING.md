# Contributing

Bug reports and questions go to
[issues](https://github.com/everywan-dev/validated-memory/issues). For a
change, open an issue first if it touches a contract (the base contract, a
CLI exit code, a finding's shape): those are the project's public promises,
and the discussion belongs before the diff.

## Ground rules

- Runtime code is Python 3, **standard library only**. pytest is the only
  development dependency.
- Everything in the repository — code, comments, CLI messages, docs — is
  written in English.
- Exit codes are a contract: `0` = clean or WARNING-only findings; `1` =
  ERROR (gates); `2` = usage error.
- Tests never import the package's internals. Enforcement is tested end to
  end — the CLI as a subprocess over fixture adopter trees, asserting on
  exit codes, output, and produced files; shipped content (docs, skills,
  assets) is checked structurally. New tests follow the same seam.
- Work on a `feature/*` branch; `main` is never force-pushed.

## Running the tests

```
python3 -m pytest
```

Everything must pass before a change is proposed; a behavior change comes
with the test that pins it.

## Releasing

A release is one commit where `pyproject.toml`,
`validated_memory/__init__.py` and `.claude-plugin/plugin.json` state the
same version, tagged with that same version — see
[ADR 0005](docs/adr/0005-a-release-is-one-commit-where-the-three-versions-agree.md).

1. Bump the version in the three files, in one commit. Bumping
   `plugin.json` is what reaches plugin users; a tag alone reaches none.
2. Run the full suite; merge to `main`.
3. Tag: `git tag vX.Y.Z`. Before pushing it, confirm the tag's version
   equals the three files' at the tagged commit — the half of the
   invariant no test can see.
4. Re-point only the matching major channel: for 2.x,
   `git tag -f v2 v2.Y.Z` — only ever at a commit already carrying that
   immutable version tag. Never point `v1` at 2.x; it stays on its 1.x release.
5. Develop and verify the release branch on GitLab first. After the full suite
   and required CI pass, merge without force-pushing `main`, then push the same
   release commit and tags to **both** remotes. Verify remote commit/tag targets
   and both CI results; a local success or a tag on one remote is not publication
   on both. Updating a moving major tag may require an explicit force update of
   that tag alone, never of `main` or an immutable version tag.

`vX.Y.Z` tags are immutable: a mistake in a release is fixed by the next
release, never by moving a versioned tag. Only major channels move, within their
own major version; see [ADR 0015](docs/adr/0015-major-channels-never-cross-major-versions.md).
