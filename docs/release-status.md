# Release status

## Published release

The current published release is **2.5.1**, tagged
[`v2.5.1`](https://github.com/everywan-dev/validated-memory/tree/v2.5.1).
The [GitHub release page](https://github.com/everywan-dev/validated-memory/releases/tag/v2.5.1)
is the distribution record. The plugin uses the version declared in its
manifest, so a commit on the default branch does not by itself update an
installed plugin; see [updating](installing.md#updating).

2.5.2 bounds the session-start run and stops a vault node that is not a regular
file from blocking it. `init --lock-wait SECONDS` (default 10) sets one deadline
for every lock a run takes, and the `SessionStart` hook passes 3, so a busy lock
costs a session about three seconds and leaves the harness link for the next
start; the bound covers waiting for the lock and not the work done under it. A
transaction entry, a preimage slot or a lock path that is a symlink, a named
pipe or a directory is no longer opened: the entry is a damaged transaction, the
slot refuses the mutation and the lock path is refused, and each ERROR names the
node, where 2.5.1 could hang. A symlink to a valid transaction file, which 2.5.1
read, is now a damaged transaction. The harness path is read again immediately
before the link is replaced, and a path that changed while the repair waited is
left alone with a WARNING; a path that cannot be looked at is a WARNING and no
longer an uncaught error. See
[ADR 0030](adr/0030-the-session-start-run-is-bounded-and-a-vault-node-that-is-not-a-regular-file-never-blocks-it.md).

2.5.1 keeps the harness-memory link through a journal refusal that does not
concern it. A readable history that `init` refuses with a topology gate no longer
leaves the session without project memory when neither the history nor the vault
names the link; the repair runs under the journal lock and is withheld, with a
WARNING, whenever a transaction or an unclassified vault entry could own the
link. The same check now guards the repair when the journal cannot be read and
when the vault is not ignored, and `status` reports the history conditions that
stop `init`. See [ADR 0029](adr/0029-the-harness-link-survives-a-refusal-that-does-not-name-it.md).

2.5.0 hardens `init` and its journal: crash-safe creation of the first
history, recovery of interrupted transactions, explicit repair of a torn
history append, and refusal of a harness path inside the adopter or a
project-memory target outside it. It also bounds `probe` command output. The
journal format stays at schema 1; see the [journal reference](reference/journal.md).

Since 2.4.0 the CLI includes exact-scope task resumption through `resume-use` and
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
