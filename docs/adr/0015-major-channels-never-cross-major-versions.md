# Major channels never cross major versions

Accepted; supersedes ADR 0005 only on its single moving `v1` channel. Each major
may have its own convenience channel, pointing only to a commit already tagged
with an immutable release of that major: `v2` starts at `v2.0.0`, while `v1`
stays on 1.x. Reusing `v1` for a breaking release would silently change adopters'
gating behavior; the one-commit/three-version equality, immutable version tags,
SHA-first pinning and identical release targets on both remotes remain unchanged.
