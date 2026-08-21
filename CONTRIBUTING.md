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
