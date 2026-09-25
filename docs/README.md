# Documentation

Choose a starting point by what you want to do. The detailed contracts are
organized below; the [CLI reference](reference/cli.md) is the source for exact
command syntax and exit behavior.

## Install and adopt

- [Install the plugin or run the CLI from a checkout](installing.md), including
  runtime requirements, compatibility and updates.
- [Adopt a project](adoption.md): choose what to version, bootstrap the layout,
  import existing knowledge and optionally configure the agent.
- [Walk through the core cycle](walkthrough.md) with real files and commands.

## Author, find and reuse knowledge

- [Curated knowledge](reference/curated-knowledge.md) and
  [agent memory](reference/agent-memory.md) define the two data layers.
- [Recall](reference/recall.md) finds candidates; it does not validate claims.
- [Requested learning](reference/learning.md) covers scoped capture and review.
- [Agent integration](reference/agent-integration.md) documents optional prompt
  discovery and reliance preferences.
- [Task lifecycle and trusted handoff](reference/task-lifecycle.md) covers
  continuation, interruption and delegation boundaries.
- [Everyday checked reuse](reference/everyday-workflow.md) links resumption,
  correction and portable contribution workflows.

## Operate and recover

- [CLI reference](reference/cli.md) covers every shipped command.
- [Startup hooks](reference/hooks.md) explains what the plugin runs at session
  start and on prompt submission.
- [Troubleshooting](troubleshooting.md) routes common symptoms to the exact
  check and canonical procedure.
- [Whole-project backup and recovery](reference/recovery.md) covers backup,
  restore and supported relocation rehearsals.
- [Journal](reference/journal.md) documents `init` history and transaction
  recovery.

## Checked consultation and portable contributions

- [Checked consultation](reference/consultation.md) describes explicit,
  attributed use across registered adopter projects.
- [Incorporation and correction](reference/incorporation.md) separates review,
  acceptance and later canonical observations.
- [Portable historical contributions](reference/transfer.md) covers explicit
  whole-history transfer and local origin mapping.
- Storage contracts: [consultation](reference/consultation-storage.md),
  [incorporation](reference/incorporation-storage.md), and
  [transfer](reference/transfer-storage.md).

## Architecture and project status

- [Architecture](architecture.md) describes the released component and data
  boundaries.
- [Release status](release-status.md) identifies the published baseline and
  explains how to distinguish it from future proposals.
- [Architecture decision records](adr/README.md) indexes the decisions in this
  release.
