# Release status

## Published release

The current published release is **2.4.0**, tagged at commit
[`c398e77`](https://github.com/everywan-dev/validated-memory/tree/v2.4.0).
The [GitHub release page](https://github.com/everywan-dev/validated-memory/releases/tag/v2.4.0)
is the distribution record. The plugin uses the version declared in its
manifest, so a commit on the default branch does not by itself update an
installed plugin; see [updating](installing.md#updating).

The 2.4.0 CLI includes exact-scope task resumption through `resume-use` and
selected historical transfer material through
`show-transfer IMPORT --origin PROJECT_UUID:UNIT_ID --material`. These build on
the checked consultation, incorporation and portable-transfer capabilities
documented in the [reference](README.md#checked-consultation-and-portable-contributions).
See the [everyday workflow](reference/everyday-workflow.md) for how resumption
and selected material fit into review and reuse.

## Future work

An issue, design document or architecture decision describes discussion or an
intended direction; it does not establish that a feature is available in the
published package. Treat a capability as released only when the published
version includes it; release notes summarize that shipped surface. Check the
[public issue tracker](https://github.com/everywan-dev/validated-memory/issues)
and [releases](https://github.com/everywan-dev/validated-memory/releases) for
current status.

The version declarations in `pyproject.toml`, `validated_memory/__init__.py`
and `.claude-plugin/plugin.json` identify the checkout's package version. The
tagged release and its notes identify what was actually published.
