# Installing

From GitHub, two commands inside Claude Code:

```
/plugin marketplace add everywan-dev/validated-memory
/plugin install validated-memory@validated-memory
```

The repository is its own marketplace: `.claude-plugin/marketplace.json`
lists the plugin `.claude-plugin/plugin.json` defines, with `source: "./"`.
A manifest alone is only installable by someone who already has the directory
on disk (`claude --plugin-dir ./`); the listing is what makes it installable
from the repository URL.

## Requirements and compatibility

The runtime requires **Python 3.11 or newer** and uses only the Python
standard library. `pytest` is the only development dependency. Source installs
follow the packaging metadata in `pyproject.toml`; this guide does not set an
additional minimum version for build tools.

Claude Code is required to install the plugin and run its hooks and skills.
The standalone CLI can also run from a shell or CI when the package is
installed or the checkout is on `PYTHONPATH`. The GitHub Action defaults to
Python 3.12; set its Python input to 3.11 or newer.

Some commands have narrower platform requirements than the package as a
whole. The `recall` command requires POSIX filesystem APIs; checked
consultation is supported on a trusted POSIX host. The journal reference
documents its platform-specific durability and recovery behavior. The bundled
`git_ref` probe requires Git on `PATH`; other probes do not. See
[recall limits](reference/recall.md#limits-and-platform-requirements),
[checked consultation](reference/consultation.md), and
[journal recovery](reference/journal.md#recovery).

## What installing activates

Installing the plugin registers three `SessionStart` hooks that run on every
session start in every project. All three are fail-open no-ops in a project
that has not adopted validated-memory. In an adopted project, the first keeps
the harness-memory symlink alive — and, on the first session after adoption,
may absorb the harness's pre-existing memory directory into the project,
parking the original as a `.bak`; the second refreshes whichever HTML views
the project has activated; and the third writes nothing at all, injecting a
few lines of the project's current status into the session. What each one
writes, and the recognition rule that gates the absorption, are documented in
[Startup hooks](reference/hooks.md).

Installation also registers one fail-open `UserPromptSubmit` hook. It is a
no-op unless the exact project root is an adopter and has explicitly enabled
prompt discovery in `validated-memory-profile.md`. It returns bounded lexical
candidates and never blocks prompt submission or establishes that a candidate is
true. See [Agent integration](reference/agent-integration.md) for setup, limits
and installed-host verification.

## Other Git hosts

Any Git remote works, not only GitHub: a self-hosted GitLab, Bitbucket or
Azure DevOps URL is fine (`/plugin marketplace add
https://<host>/<group>/validated-memory.git`), and a command you run
yourself authenticates through the ordinary Git credential helpers. **Give
every host except GitHub the full repository URL, scheme included** — the
bare `owner/repo` shorthand is a GitHub-only form, and a URL missing its
scheme is rejected as an invalid shorthand rather than guessed.

## Updating

**Updating is not automatic, and this is the part to get right.** Auto-update
is off by default for a marketplace that is not Anthropic's, so an adopter
picks up a fix by running `/plugin marketplace update validated-memory`, or
by turning auto-update on for this marketplace once, under
`/plugin marketplace`. One caveat on auto-update: the background refresh
disables Git credential helpers for its pull, so over HTTPS it cannot
authenticate to a private repository — add a private marketplace over SSH
instead (a key in `ssh-agent` authenticates unattended), or stay on manual
updates. And because `plugin.json` declares a `version`, the
plugin is pinned to it: an adopter sees a change only when that number
changes. Publishing a fix therefore means bumping the version, not only
merging it — a commit on the default branch reaches nobody on its own.

Before switching a gating CLI, CI job or Action to the v2 channel, follow the
[v2 migration notes](migration-2.md): memory identity conflicts now gate, while
the enhanced HTML app remains an explicit opt-in. The v1 channel stays on 1.x.

## Installing for a team

To install it for a whole team without each person running the two commands,
a project can declare the marketplace and enable the plugin in its own
`.claude/settings.json`, and an administrator can do the same for an
organisation. That is a decision about other people's sessions, so this
repository does not ship such a file: it is left to whoever adopts it.

## Running the CLI outside the plugin

The enforcement CLI has no third-party runtime dependencies, so it also runs
without Claude Code — in CI, or from a shell.
Two ways to make `python3 -P -m validated_memory` importable:

- **From a checkout, via `PYTHONPATH`** — no installation at all:

  ```
  git clone https://github.com/everywan-dev/validated-memory.git
  PYTHONPATH=./validated-memory python3 -P -m validated_memory --version
  ```

  This is how the plugin's own hooks invoke it, and how an adopter's CI can
  gate on `validate`, `lint` and `derive --check` from a pinned checkout.

- **Installed with pip**, straight from the repository:

  ```
  pip install git+https://github.com/everywan-dev/validated-memory.git
  ```

Inside a Claude Code session the plugin's hooks resolve its location
themselves; these two forms exist for everything outside one.
