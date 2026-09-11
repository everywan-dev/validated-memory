# validated-memory

[![CI](https://github.com/everywan-dev/validated-memory/actions/workflows/ci.yml/badge.svg)](https://github.com/everywan-dev/validated-memory/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Validated memory for agent projects: evidence states, supersession without
deletion, freshness probes with a ternary verdict.

[Installing](#installing) · [Quickstart](#quickstart) ·
[CLI](#the-cli-at-a-glance) · [Skills](#skills) · [Documentation](#documentation)

**Agent memory rots silently.** An agent writes down a fact in March; by June
the world has moved on, and nothing tells you. Recall is a crowded
competition; whether what is recalled is *still true* is the neglected half
of the problem.

validated-memory makes knowledge expiry the first-class problem. Every fact
states how it is known (`measured | verifiable | hypothesis`) and what it
depends on — re-checkable anchors — and freshness probes answer with a
ternary verdict, `current | drifted | unknown`, that says "could not tell"
rather than guess. A verdict means *as of the last probe you ran*: probing is
an explicit act you schedule, not background magic. Nothing is ever deleted:
a fact stops being true only by naming what replaced it. A false "still
true" is the one answer this tool must never give.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/model-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/model-light.svg">
  <img src="docs/assets/model-light.svg" alt="The two layers: agent memory (one Markdown file per fact, linted) and curated knowledge (units with evidence states and anchors, validated and gated in CI); nothing is deleted — a fact retires only by naming its successor." width="940">
</picture>

Two layers, one discipline. **Agent memory** — one Markdown file per fact
plus an index, kept in the adopter repo; the harness reads it through a
symlink the plugin maintains ([reference](docs/reference/agent-memory.md)).
**Curated knowledge** — units carrying identity, an evidence state, and
anchors separated from provenance
([reference](docs/reference/curated-knowledge.md)). Adopter projects hold
only Markdown data and configuration; all code stays in the plugin, so a fix
reaches every adopter that updates.

- **Enforced, not promised** — `validate`, `lint` and `derive --check` are
  CI gates; drift from the contract fails the build.
- **No third-party dependencies** — Python standard library only, and the
  data is plain Markdown, readable without the plugin installed.
- **Easy exit** — abandon the tool and you keep ordinary Markdown files.

## Installing

Two commands inside Claude Code:

```
/plugin marketplace add everywan-dev/validated-memory
/plugin install validated-memory@validated-memory
```

Two things to know before you run them. **Installing activates three
`SessionStart` hooks** — fail-open no-ops until a project adopts the method,
after which the first maintains the harness-memory symlink (on first
adoption it may absorb the harness's existing memory directory, parking the
original as a `.bak`), the second refreshes any activated HTML views, and the
third injects the project's current status into the session; what each writes
is documented in [Startup hooks](docs/reference/hooks.md). And **updating is
not automatic**: the plugin is pinned to its declared version, and picking up
a fix means running `/plugin marketplace update validated-memory` (or
enabling auto-update for this marketplace once).

Other Git hosts, team-wide installs, and running the CLI without Claude
Code — in CI, or from a shell — are covered in
**[Installing](docs/installing.md)**.

## Quickstart

From an empty directory (`python3 -P -m validated_memory` must be importable —
see [Installing](docs/installing.md#running-the-cli-outside-the-plugin)):

```console
$ python3 -P -m validated_memory init
init: ignored /.validated-memory/ in .gitignore
init: created knowledge
init: created memory
init: created memory/MEMORY.md
init: created validated-memory.md
init: created knowledge-extension.md
init: 5 created, 0 kept, 0 error(s), 0 warning(s)
```

Write one fact as `knowledge/kb-0001.md`, anchoring it to what a git ref
resolves to today (`git rev-parse main`):

```yaml
---
id: kb-0001
evidence: measured
anchors:
  - system: this-repo
    kind: git_ref
    captured_at: 2026-08-21T10:00:00Z
    payload:
      repo: .
      ref: refs/heads/main
      commit: <the sha git rev-parse printed>
---

# The deploy branch is main, gated by the smoke suite
```

Then enforce, probe, and derive:

```console
$ python3 -P -m validated_memory validate
validate: 1 unit(s) checked, 0 error(s), 0 warning(s)

$ python3 -P -m validated_memory probe
probe: 1 anchor(s) probed across 1 unit(s): 1 current, 0 drifted, 0 unknown

$ python3 -P -m validated_memory derive
derive: 1 unit(s) indexed
```

The derived index now grades every unit by the worst verdict among its
anchors:

```markdown
| id      | state  | evidence | verdict |
|---------|--------|----------|---------|
| kb-0001 | active | measured | current |
```

The **[walkthrough](docs/walkthrough.md)** runs the full cycle — including
drift and supersession — with real file contents; the
**[adoption guide](docs/adoption.md)** is the checklist for wiring a real
project, CI gate included.

## The CLI at a glance

| Command | What it does |
|---------|--------------|
| [`init`](docs/reference/cli.md#init) | Scaffold an adopter project; wire the harness-memory symlink; activate views |
| [`lint`](docs/reference/cli.md#lint) | Enforce the agent-memory layer: index sync, frontmatter, wikilinks, supersession |
| [`validate`](docs/reference/cli.md#validate) | Enforce the base contract plus the adopter's declared extension |
| [`derive`](docs/reference/cli.md#derive) | Re-derive the knowledge index; `--check` gates CI against drift |
| [`probe`](docs/reference/cli.md#probe) | Run freshness probes; append ternary verdicts to the log |
| [`recall`](docs/reference/cli.md#recall) | Search memory and/or knowledge read-only; discovery, not validation |
| [`consultation`](docs/reference/consultation.md) | Opt-in checked use of an exact consumer conclusion; retain receipts and check current eligibility |
| [`render`](docs/reference/cli.md#render) | Write self-contained, inert HTML views of both layers |
| [`status`](docs/reference/cli.md#status) | Read-only report: structural gates plus a reported (opt-in gated) freshness summary |
| [`journal`](docs/reference/cli.md#journal) | Report the append-only record of what `init` did; `--check` gates on an unfinished record pair, a pair that disagrees or an open transaction, and `--resolve` closes one |

Exit codes: `0` = clean or WARNING-only findings; `1` = ERROR (gates);
`2` = usage error. Full contracts in the
**[CLI reference](docs/reference/cli.md)**.

The source tree also includes an **unreleased** opt-in
[incorporation and correction lifecycle](docs/reference/incorporation.md): retain
proposals and challenges, inspect and decide, then separately observe canonical
incorporation, addressed uses and changed tracked publications. Existing schema 1
stores require explicit upgrade; published 2.2.0 does not include these commands.
Canonical authoring and ordinary lookup remain separate.

Unreleased [portable historical contributions](docs/reference/transfer.md) add
explicit whole-history export/import and reviewed local origin mappings. Current
source stores use schema 3; existing schema 1/2 stores require explicit upgrade
for transfer writes. Exported historical evidence does not establish live origin
freshness or local authorization. Assess disclosure and size before exporting.

### Gate CI in three lines

The repository is also a reusable GitHub Action that runs `status` — the
CLI runs straight from the action's checkout, so the code that gates is
exactly the code at the ref you name. Pin to a full commit SHA — copy it
from the [release tag](https://github.com/everywan-dev/validated-memory/tags)
you are adopting — for CI that must not trust a mutable ref:

```yaml
- uses: everywan-dev/validated-memory@<full commit SHA>
  with:
    args: --fail-on drifted
```

Less rigorous but more convenient, the moving `v2` major tag works the
same way:

```yaml
- uses: everywan-dev/validated-memory@v2
  with:
    args: --fail-on drifted
```

Pinning, by decreasing rigor: a full commit SHA for CI that must not trust
a mutable ref, an immutable `vX.Y.Z` tag, or the moving `v2` major tag
shown above for convenience. `args` is passed to
[`status`](docs/reference/cli.md#status) verbatim; without it, structural
consistency gates and freshness is only reported.

Freshness is a loop, not a flag:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/lifecycle-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/lifecycle-light.svg">
  <img src="docs/assets/lifecycle-light.svg" alt="An anchor is probed; the verdict — current, drifted, or unknown — is appended to verdicts.jsonl; drift is answered by a successor unit that supersedes the stale one; history is never edited." width="940">
</picture>

A `drifted` or `unknown` verdict is data, not a failure: `probe` never gates
on what it finds, and the index reports it so a person — or an agent — can
answer drift by writing a successor unit.

## The views

`render` writes two canonical, self-contained HTML pages — no JavaScript, no network —
showing live conclusions, each anchor's probe history as a freshness strip,
and the supersession chain that led to every fact:

<img src="docs/assets/knowledge-view.png" alt="knowledge-app.html with search and state/evidence/verdict filters, full-corpus overview and navigation, and a synthetic unit's probe history with keyboard-operable diagram controls." width="830">

For optional search, filters and diagram pan/zoom, activate the separate app:

```sh
python3 -P -m validated_memory init --view --app
```

`knowledge-app.html` adds one local inline script; it uses no network or browser
storage. Remove the script and its content is the canonical knowledge page
byte-for-byte. Delete the app file to stop regenerating it. As with other CLI
commands, use the installed package or set `PYTHONPATH` to the plugin checkout.

See [`render`](docs/reference/cli.md#render) for the pages' contracts, and
[Startup hooks](docs/reference/hooks.md) for how the views stay fresh across
sessions once activated.

## Skills

Eight skills make the method invocable from an agent session, each naming the
exact CLI invocation and the data discipline to follow — never reimplementing
a rule the CLI already enforces:

- **`adopt-validated-memory`** — decide what the repository versions,
  bootstrap a project, import whatever knowledge it already has, offer the
  managed block for its instruction file, wire the symlink, verify with
  `validate` and `lint`.
- **`create-knowledge-unit`** — write a unit field by field, with the
  evidence-state discipline.
- **`supersede-knowledge`** — correct knowledge with a successor, never by
  editing the superseded unit.
- **`probe-freshness`** — probe, re-derive, read the ternary verdict.
- **`maintain-agent-memory`** — record or supersede a memory fact, verify
  with `lint`.
- **`ask-validated-memory`** — answer usage questions from the plugin's own
  docs and `--help`, quoting exact invocations, never inventing a flag; points
  questions about the adopter's own data to `consult-project-memory`.
- **`consult-project-memory`** — on an explicit lookup request, or in a
  project that has opted into a consultation-first workflow, search this
  project's own memory and knowledge with `recall`, read the sources behind a
  match, and tell an unavailable search apart from a clean zero-match one.
  An explicit checked-use opt-in adds final consumer acquisition, retained use
  and current checks through `consultation`; lookup alone never enables it.
- **`bootstrap-from-repo`** — scan the repository, and any source the
  adopter declared and consented to, and propose starting facts for both
  layers under an explicit security perimeter; only what a confirmed report
  page showed is written, and every source seen is recorded.

## Requirements and compatibility

- **The v2 surface includes** every subcommand in the [CLI
  reference](docs/reference/cli.md), every skill listed above, all three
  startup hooks, canonical static HTML views and an opt-in app. The version this clone ships is
  declared in `pyproject.toml` and the plugin manifest, not restated here.
- **Python ≥ 3.11**, standard library only; pytest is the only development
  dependency.
- **Claude Code** to run it as a plugin; the CLI stands alone everywhere
  else (CI, shell).
- **Git on `PATH`** for the bundled `git_ref` probe; no other probe needs it.
- Updates are version-pinned — see
  [Updating](docs/installing.md#updating).
- Before moving a gating CLI or CI job to v2, resolve memory identity conflicts;
  see [migration to v2](docs/migration-2.md). The v1 channel stays on 1.x.

## Documentation

| | |
|---|---|
| **[Installing](docs/installing.md)** | Hosts, updating, team installs, CLI without Claude Code |
| **[Adoption guide](docs/adoption.md)** | The checklist for a real project, CI gate included |
| **[Walkthrough](docs/walkthrough.md)** | Every layer end to end, with real file contents |
| **[CLI reference](docs/reference/cli.md)** | The full contract of each subcommand |
| **[Incorporation](docs/reference/incorporation.md)** | Unreleased proposals, challenge review, consumer effects and tracked publication observations |
| **[Incorporation storage](docs/reference/incorporation-storage.md)** | Unreleased schema-2 lifecycle contracts preserved by schema 3 |
| **[Portable transfer](docs/reference/transfer.md)** | Unreleased offline historical contributions and explicit local incorporation |
| **[Transfer storage](docs/reference/transfer-storage.md)** | Unreleased schema 3, whole-history capsules and origin-aware current checks |
| **[Curated knowledge](docs/reference/curated-knowledge.md)** | Base contract, adopter configuration, declared extension |
| **[Agent memory](docs/reference/agent-memory.md)** | The memory layer's rules, identity, and supersession |
| **[Startup hooks](docs/reference/hooks.md)** | What runs at session start, and what it writes |
| **[Journal](docs/reference/journal.md)** | The append-only record of what adoption did, and the `journal` subcommand |
| **[Recall](docs/reference/recall.md)** | The `recall` command's full field reference, exit codes and known limitations |
| **[Checked consultation](docs/reference/consultation.md)** | Local cross-project checked use, maintenance and honest limits |
| **[Consultation storage](docs/reference/consultation-storage.md)** | Strict schema and retained artifact relationships |
| **[ADRs](docs/adr)** | Decisions of record |

## Development

Runtime code is Python 3, standard library only.

```
python3 -m pytest
```

Tests never import the package's internals: enforcement is tested end to
end, driving the CLI as a subprocess over fixture adopter trees; shipped
content — docs, skills, assets — is checked structurally. Contributions follow
[CONTRIBUTING.md](CONTRIBUTING.md); bugs and questions go to
[issues](https://github.com/everywan-dev/validated-memory/issues).

Licensed under [Apache-2.0](LICENSE).
