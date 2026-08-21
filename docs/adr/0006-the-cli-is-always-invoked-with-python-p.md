# The CLI is always invoked with `python3 -P -m`

Verified: under `python3 -m validated_memory`, `sys.path[0]` is the current
working directory, inserted *before* `PYTHONPATH`. An adopter repository
that happens to contain its own `validated_memory/` directory -- an
unrelated package, a stray experiment, a name collision nobody chose on
purpose -- shadows the plugin's package. The adopter's code runs in its
place, silently: nothing about `-m`'s output tells you which package
answered.

Two things make this worse than an ordinary import bug. The plugin's own
`SessionStart` hooks run this invocation automatically, in the adopter's
own working directory, on every session start -- so a shadowing package
there is code execution with no confirmation prompt, triggered by opening
a project. And the reusable GitHub Action runs the same invocation from
the checked-out repository it is gating: a pull request that adds a
`validated_memory/` directory to that checkout would run *inside* the
gate, before the gate's own code gets a chance to object, bypassing every
check the Action exists to enforce.

The decision: every documented and executed invocation of the CLI --
hooks, the Action, skills, docs, and the test suite -- is
`python3 -P -m validated_memory`. `-P` (`-safe-path`) excludes that
implicit current-directory entry from `sys.path` for `-m`, `-c` and a bare
interpreter; it does not touch `PYTHONPATH`, so an explicit `PYTHONPATH`
still resolves the real package. `-P` was added in Python 3.11 (PEP 706),
so `requires-python` is raised to `>=3.11`.

## Considered options

- **`python3 -P -m` on every invocation** -- chosen. One flag, on the
  command line every invocation already carries, closes the shadow without
  changing how the package is found (`PYTHONPATH` still does that). It is
  visible in the command itself, so a reviewer reading a hook, the Action,
  or a doc's copy-pastable example sees the protection is there, rather
  than having to know about an environment variable set somewhere else.
- **`PYTHONSAFEPATH=1` as an environment variable** -- rejected. Same
  effect, same 3.11 floor, but strictly worse to keep true everywhere: a
  flag baked into the documented command line cannot be dropped by
  copy-pasting only the command, while an environment variable can --
  a shell that does not export it, a CI step that does not forward it, a
  skill invocation reproduced without its surrounding setup. This repo
  invokes the CLI from a dozen places (two hooks, one Action, seven
  skills, several docs); a flag in the command is the one form that
  travels with every one of them.
- **An isolated launcher script, `-I` plus manual `sys.path` surgery** --
  rejected. `-I` implies `-P` (among other isolations) and works back to
  Python 3.9, preserving the lower floor this repo started at. But it adds
  a second entry point: a script to version, document, and test
  independently of `python3 -m validated_memory`, and a second place for
  the two to drift apart. Rejected because no supported consumer runs
  below 3.11 in practice, so the floor it would preserve buys nothing.

## Consequences

- `pyproject.toml` declares `requires-python = ">=3.11"`; a `-P`-invoked
  `python3 -m validated_memory` on 3.9 or 3.10 is no longer a target this
  repo claims to support.
- Every place that invokes the CLI carries `-P`: both `hooks/*.sh`,
  `action.yml`'s run line, every `SKILL.md` command line, and every doc
  example. `tests/test_skills_structure.py` and `tests/test_action.py`
  gate the invocation form itself -- a documented invocation without `-P`
  fails the test suite, not just a manual review.
- `tests/conftest.py`'s `run_cli` fixture invokes the CLI with `-P`, so the
  whole end-to-end suite exercises the same mode every real consumer uses,
  and a dedicated regression test reproduces the shadow with a hostile
  fixture package to prove `-P` closes it.
