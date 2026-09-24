# Architecture decision records

This index groups the decisions shipped in this release by topic. Each link
uses the title of the decision record; the record contains its context, choice
and consequences.

## Identity and contracts

- [The filename is a memory entry's canonical identity](0001-filename-is-the-canonical-memory-identity.md)
- [Rationale is local structured metadata](0012-rationale-is-local-structured-metadata.md)
- [Code prose is a contract, a constraint or a verification argument](0010-code-prose-is-a-contract-a-constraint-or-a-verification-argument.md)
- [Inside the journal package, an underscore means no other module may depend on it](0011-inside-the-journal-package-an-underscore-means-no-other-module-may-depend-on-it.md)

## Validation, freshness and discovery

- [Status gates consistency and only reports freshness](0002-status-gates-consistency-and-only-reports-freshness.md)
- [The adopter versions the verdict log alongside the index](0003-the-adopter-versions-the-verdict-log-alongside-the-index.md)
- [Verdict age belongs to status, never to the derived index](0004-verdict-age-belongs-to-status-never-to-the-derived-index.md)
- [Retrieval is discovery, not validation](0016-retrieval-is-discovery-not-validation.md)

## CLI and adoption

- [The CLI is always invoked with python -P](0006-the-cli-is-always-invoked-with-python-p.md)
- [Adoption decisions live in the skill, not in init](0007-adoption-decisions-live-in-the-skill-not-in-init.md)

## Journal and storage

- [The journal is versioned and the vault is local](0008-the-journal-is-versioned-and-the-vault-is-local.md)
- [A refusal is never permanent history](0009-a-refusal-is-never-permanent-history.md)

## Views and releases

- [A release is one commit where the three versions agree](0005-a-release-is-one-commit-where-the-three-versions-agree.md)
- [Canonical views are inert and the app is opt-in](0013-canonical-views-are-inert-and-the-app-is-opt-in.md)
- [Generated views have no third-party runtime](0014-generated-views-have-no-third-party-runtime.md)
- [Major channels never cross major versions](0015-major-channels-never-cross-major-versions.md)

## Checked consultation and contributions

- [Checked consultation retains use outside canonical authoring](0017-checked-consultation-retains-use-outside-canonical-authoring.md)
- [Incorporation separates acceptance from canonical observation](0018-incorporation-separates-acceptance-from-canonical-observation.md)
- [Portable contributions preserve foreign history](0019-portable-contributions-preserve-foreign-history.md)
- [Task resumption separates updates from checked use](0020-task-resumption-separates-updates-from-checked-use.md)
- [Agent policy is separate from knowledge evidence](0021-agent-policy-is-separate-from-knowledge-evidence.md)
