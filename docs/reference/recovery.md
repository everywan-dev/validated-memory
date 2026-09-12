# Whole-project backup and recovery

This guide is for a deliberate local backup and restore rehearsal while the
project and consultation workspace are quiescent. It does not add a backup
engine, remote synchronization, uninstall, or reversal command.

A Git clone is insufficient when ignored local state exists. A transfer capsule
is historical contribution transport, not a project backup. In particular, a
capsule omits memory entries and their index, the agent profile, adopter
configuration, verdicts, journal and vault state, and a reconstructable current
consultation workspace.

## Inventory and prepare

First inventory the complete set that the backup must include:

- every registered adopter root, not only the task's consumer, with complete
  `knowledge/`, complete `memory/` and `memory/MEMORY.md`, configuration and
  extension files, `validated-memory-profile.md`, support sources, and evidence
  or provenance prerequisites;
- local indexes, verdicts, journal and vault state where present;
- the trusted task handoff and other workspace state needed to resume work;
- the explicit consultation SQLite workspace outside adopter roots, including
  the complete store set if recovery is unresolved; and
- the installed harness target and symlink state, recorded separately because
  absolute registrations and harness links do not relocate with a Git clone.

The consultation store uses SQLite `DELETE` journal mode. Resolve a hot rollback
journal through the normal explicit `recover` operation before checkpointing,
or preserve the entire unresolved store set for separate recovery and report it
unavailable. Never discard a sidecar, copy a live main database alone, or run
recovery against the sole backup.

For every registered root that might move, obtain its latest content-only CLI
`checkpoint` before backup. This checkpoint authorizes an exact-content
relocation; it is not filesystem backup integrity. If recovery or a required
checkpoint is refused, retain the incomplete preparation status.

Stop all writers and close every SQLite connection after these preparations.
Create the final trusted pre-backup manifest over this exact frozen source set.
List each path, type, relevant mode, size, and digest. It is an integrity baseline,
not proof of authenticity or semantic truth. No checkpoint or other write may
intervene between this final manifest and the copy; restart preparation if inputs
change.

Copy the complete roots, external workspace, handoff, and harness information as
one controlled rehearsal and compare the backup against the final manifest.
Keep the backup untouched and preserve the original
bytes. A relocation rehearsal may deliberately move an original root so its old
path becomes unavailable; record that path change and retain the moved tree.

## Restore and verify

1. Restore the registered root and all required support, followed by the external
   consultation workspace and trusted handoff. A complete restore to new paths is
   supported for a single-project workspace.
2. Compare the restored inventory, digests, file types, and relevant modes with
   the trusted manifest. Verify that evidence is readable and that canonical
   bytes, identities, profile, configuration, indexes, verdicts, and retained
   history match the intended baseline.
3. Run the ordinary `lint` and `validate` checks for both canonical layers. Check
   consultation integrity and history with documented read-only operations. Do
   not run `init` as silent repair.
4. If the single registered path changed, keep its old root unavailable and use
   `relocate` with its latest checkpoint. The restored content must match exactly.
5. Inspect the harness target and symlink separately. Restore it through the
   existing safe adoption procedure only when authorized and after reviewing its
   plan; do not assume or perform unattended symlink repair.
6. After relocation or any changed root, revision, or scope, reacquire the final
   consumer through complete `read` and `record-use`. Preserve prior handles and
   history.

For a workspace with multiple registered roots, relocation is staged. Move and
relocate one root while every other registered root remains available at its
original inode identity; then continue one root at a time. Each operation captures
the other registered roots and therefore cannot bootstrap from restored copies
after several original identities have already been lost. If two or more required
root identities are lost together, the current CLI refuses recovery. Preserve the
backup and store, report the workspace as unsupported for current use, and do not
edit the database, re-register projects, or pretend that byte-identical copies
satisfy the retained identities. Restoring each original root identity, when
possible, is required before staged relocation can continue.

Missing evidence or workspace, mismatched identity or digest, unavailable other
registered roots, a hot or corrupt store, and incomplete support all stop current
eligibility claims. Recover the missing input or use documented safe recovery;
do not delete history, invent
handles, bless changed content, or narrow the registered set silently. See
[checked consultation](consultation.md#checkpoint-before-relocation) and its
[storage recovery contract](consultation-storage.md) for exact commands and
SQLite behavior.

The rehearsal establishes only that the inspected backup can reconstruct the
recorded local state under these checks. It does not establish evidence truth,
external freshness, automatic host recovery, or restore coverage for anything
omitted from the manifest.
