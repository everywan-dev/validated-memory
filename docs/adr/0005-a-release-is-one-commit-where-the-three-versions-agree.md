# A release is one commit where the three versions agree

The plugin ships from its own repository: the repo is its own marketplace,
and the CI action runs the CLI from the action's checkout
(`PYTHONPATH=$GITHUB_ACTION_PATH`), so the code that runs is exactly the
code at the ref the consumer named — no installation step, no version skew.
That only stays safe if a "release" is a single, unambiguous point in
history. This ADR pins what that point is and what may refer to it.

**The invariant:** a release is one commit, tagged `vX.Y.Z`, in which
`pyproject.toml`, `validated_memory/__init__.py` and
`.claude-plugin/plugin.json` state that same `X.Y.Z` — the tag is part of
the equality, not a label beside it. The version-agreement test enforces the
three-file half, so a release commit cannot exist with the files
disagreeing; the tag half no test can see, because a checkout does not know
which refs point at it — it is enforced by the release procedure: before
pushing a tag, its version is compared against the three files at the tagged
commit, and a tag that disagrees is never pushed. The marketplace listing
never repeats the version; it points at the plugin manifest that owns it.
Both remotes (GitLab and GitHub) carry the same commit under the same tag.

**References, by decreasing rigor:** a full commit SHA (what the README
documents first, for CI that must not trust a mutable ref), an immutable
`vX.Y.Z` tag (the reference for everyone else), and a moving `v1` major tag
(the convenience channel, re-pointed at each `v1.Y.Z`). Internal uses of
third-party actions (`actions/setup-python`) are themselves SHA-pinned.

## Considered options

- **Publish to PyPI and `pip install` in the action** — rejected. A second
  artifact whose content can drift from the tagged commit, an install step
  that can fail independently, and packaging overhead for a stdlib-only
  tool whose whole distribution model is "the checkout is the program".
- **Only a moving major tag, GitHub-Actions style** — rejected as the sole
  reference. A mutable ref cannot answer "what exactly ran in March", which
  is the question an audit asks.
- **Only immutable tags, no `v1`** — rejected. It puts a version-bump chore
  in every adopter's CI file and breaks the three-line adoption promise for
  the consumers who explicitly chose convenience over rigor.

## Consequences

- Tagging is not releasing to plugin users: the harness updates plugins by
  `plugin.json` version, so publishing a fix means bumping
  `plugin.json.version` (with the other two, by the invariant). A moved
  tag alone reaches no plugin user.
- `v1` is re-pointed only at commits already carrying an immutable
  `vX.Y.Z`; it never points at anything that is not a release.
- The dual-remote rule becomes part of releasing: a tag pushed to one
  remote and not the other publishes two different truths.
