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

## What installing activates

Installing the plugin registers two `SessionStart` hooks that run on every
session start in every project. Both are fail-open no-ops in a project that
has not adopted validated-memory; in an adopted project, one keeps the
harness-memory symlink alive — and, on the first session after adoption, may
absorb the harness's pre-existing memory directory into the project, parking
the original as a `.bak` — and the other refreshes whichever HTML views the
project has activated. What each one writes, and the recognition rule that
gates the absorption, are documented in
[Startup hooks](reference/hooks.md).

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

## Installing for a team

To install it for a whole team without each person running the two commands,
a project can declare the marketplace and enable the plugin in its own
`.claude/settings.json`, and an administrator can do the same for an
organisation. That is a decision about other people's sessions, so this
repository does not ship such a file: it is left to whoever adopts it.

## Running the CLI outside the plugin

The enforcement CLI is an ordinary Python package with no third-party
dependencies, so it also runs without Claude Code — in CI, or from a shell.
Two ways to make `python3 -m validated_memory` importable:

- **From a checkout, via `PYTHONPATH`** — no installation at all:

  ```
  git clone https://github.com/everywan-dev/validated-memory.git
  PYTHONPATH=./validated-memory python3 -m validated_memory --version
  ```

  This is how the plugin's own hooks invoke it, and how an adopter's CI can
  gate on `validate`, `lint` and `derive --check` from a pinned checkout.

- **Installed with pip**, straight from the repository:

  ```
  pip install git+https://github.com/everywan-dev/validated-memory.git
  ```

Inside a Claude Code session the plugin's hooks resolve its location
themselves; these two forms exist for everything outside one.
