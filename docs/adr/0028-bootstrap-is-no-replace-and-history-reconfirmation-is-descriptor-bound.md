# Bootstrap is no-replace and history reconfirmation is descriptor-bound

## Context

ADR 0026 requires truthful handling when an effect is visible but its
durability is unconfirmed. Two of its recovery mechanisms are too broad for an
append-only permanent history.

First, bootstrap currently prepares a complete opening and installs it by
pathname replacement. A competing actor can create the canonical repository
history after preflight and have that history replaced. Conversely, writing
directly into an exclusively created canonical file avoids replacement but can
leave partial canonical bytes after a hard process death. Cleanup cannot make
that fallback safe: portable filesystems provide no pathname compare-and-delete
operation, so an identity check followed by `unlink` can remove a competing
replacement.

Second, ADR 0026 republishes exact history bytes after an append's data flush or
directory barrier was not confirmed. Replacing the pathname changes the
artifact identity even when every byte is identical, conflicts with append
successor checks that require the opened history to remain the same artifact,
and can overwrite an append made outside the journal lock. Calling `fsync` a
second time without a new data write would instead claim that the failed call
had become evidence after the fact.

The permanent history remains append-only semantic evidence. A durability
retry must neither invent a record nor gain ADR 0027's authority to repair a
torn or malformed history.

## Decision

### Bootstrap publishes a complete staged file without replacement

Bootstrap first creates an unpredictable private staging name in the canonical
history's directory. It writes the complete opening bytes, fixes the required
mode, flushes the file and confirms every newly created carrying directory
before it attempts canonical publication. The staging file and canonical name
are therefore on the same filesystem.

Canonical publication is one no-replace namespace operation:

- on POSIX, create the canonical name as a hard link to the flushed private
  staging file; and
- on Windows, rename the flushed private staging file to the canonical name
  with the platform semantics under which an existing destination raises
  `FileExistsError` rather than being replaced.

If the applicable primitive is unavailable or cannot provide those semantics,
bootstrap refuses before canonical publication. It does not fall back to
pathname replacement or to writing content into an exclusively created
canonical file.

After canonical publication, bootstrap confirms the carrying directory and
reacquires the coherent pair. It succeeds only when the canonical artifact has
the published identity, mode and exact opening bytes and the pair has the
expected semantic state. A barrier or readback failure after publication is a
visible or indeterminate outcome; it never authorizes a later mutation in that
run.

An existing canonical name always wins. Bootstrap preserves it and refuses;
it does not move, replace, truncate or inspect-and-delete it as cleanup. Once a
canonical opening becomes visible, automatic cleanup never unlinks it. A
complete valid lone opening is established identity-bearing history, not a
bootstrap target. Zero-byte, blank, partial, malformed, symlinked and
non-regular bootstrap names remain refusals under their applicable history and
node rules. Before a later mutation relies on a complete opening left by an
unconfirmed bootstrap, recovery applies the descriptor-bound reconfirmation and
coherent readback defined below. Directory-only reconfirmation is sufficient
only when retained evidence proves that the exact opening data was flushed and
only its namespace barrier remained uncertain. Without that proof, the complete
validated opening is re-dirtied and file-synchronized before directory
confirmation; recognizing the record does not retroactively prove that its
earlier file or directory barrier succeeded.

Cleanup of the unpredictable private staging name is best-effort. It is
limited to that private name and grants no authority over the canonical name.
No portable pathname compare-and-delete or preservation against a deliberate
concurrent replacement of the private name is claimed. Cleanup uncertainty
retains the private residue and cannot turn an unconfirmed canonical
publication into success.

### Complete history data is reconfirmed through its descriptor

Descriptor-bound reconfirmation has two authorities: retained exact evidence
that a complete history append is visible but its file-data flush was not
confirmed, or one complete validated lone opening whose current file-data
durability has no retained proof. Recovery first acquires the coherent
repository/local pair. Under the workflow lock it opens the affected history
through a writable, non-truncating descriptor and binds the operation to that
descriptor's identity.

For an append, recovery requires the expected identity and mode, the retained
exact append bytes at their proven range and the retained prefix proof. Bytes
after that range may be a later suffix, but the suffix must be complete,
compatibility parsing must accept the complete artifact, and coherent-pair
topology inspection must introduce no new semantic condition. For an opening,
recovery requires exactly one complete validated opening record and freezes its
complete bytes as the full authorized range together with the current identity
and mode. A partial, malformed or torn opening refuses and gains no repair
authority from this decision.

Recovery freezes the accepted current identities, modes, complete pair bytes
and semantic conditions as the reconfirmation pre-state. A malformed or
conflicting suffix therefore refuses before the identical-byte write or
directory confirmation. Recovery then writes the identical retained append
bytes at their proven range, or the identical complete opening over its full
authorized range. It neither truncates the artifact nor writes before or beyond
that range. This identical-byte write re-dirties the range and creates a new
data-persistence event; the subsequent file barrier therefore confirms that new
event rather than reinterpreting the earlier failed barrier. Recovery then
confirms the carrying directory.

Recovery omits the identical-byte rewrite only when retained evidence proves
that the exact file data was flushed and that the same invocation left only the
directory barrier uncertain. It then performs the new directory confirmation
required for the still-current artifact. A fresh process that finds a complete
legacy opening without that retained proof uses the full-range rewrite path.

After either path, recovery reacquires the coherent pair and requires:

- the affected artifact to retain the frozen descriptor-bound identity and mode;
- the authorized range to contain the exact retained append or opening bytes;
- every byte outside that range, including the frozen suffix, and every byte of
  the opposite artifact to remain unchanged from the reconfirmation pre-state;
  and
- the complete pair to have the expected semantic successor with no new
  condition.

Only that coherent identity, byte and semantic readback grants cleanup of
retained evidence or later reliance on the opening. A mismatch before the
reconfirmation action refuses without a protocol-owned history change. A
failure or mismatch after the write or directory confirmation is a visible or
indeterminate outcome, retains the available evidence and gates later mutation.
This includes any identity, mode, prefix, suffix, opposite-artifact or semantic
change after the pre-state was frozen.

This operation performs no pathname replacement, pathname compare-and-swap,
truncation, suffix overwrite or portable byte-range compare-and-swap. The
journal lock serializes plugin writers. A manual concurrent in-range writer is
outside that protocol and may race between validation and writing; final
readback detects the races that remain observable, but this decision claims no
stronger portable exclusion.

### Scope of the supersession

This ADR supersedes only these parts of ADR 0026:

- bootstrap installation by pathname replacement and automatic republication
  of a lone opening are replaced by staged no-replace publication and
  established-history recognition; and
- replacement-style republication of an already complete permanent history
  after unconfirmed history data durability is replaced by descriptor-bound
  exact-range reconfirmation.

ADR 0026 otherwise remains in force. In particular, it still owns the three
filesystem outcomes, truthful visible-effect reporting, durability anchors and
created-directory ordering, target republication, transaction and preimage
evidence, adoption identity, missing-history refusal, cleanup-last ordering and
the fail-open harness exception. Its portability limit also remains: on a known
platform or filesystem without directory barriers, content flushing and the
available namespace guarantee apply, but survival of names after power loss is
not claimed. Unexpected barrier failures on supported locations remain gating.

ADR 0027 is unchanged. Descriptor-bound append reconfirmation applies only to an
exact, completely parsed current history containing the proven range; opening
reconfirmation applies only to one complete validated opening record. Neither
can complete a torn tail, replace a malformed history, reconstruct missing
history or clean residue whose ownership is not proved. In particular, a
partial, malformed or torn opening receives no ADR 0027 repair authority from
this decision. The explicit proof-carrying repair remains the sole authority for
its existing proof cases and retains its atomic complete-snapshot publication
rule.

## Consequences

- A competing canonical bootstrap artifact is preserved rather than replaced.
- A hard death during staging can leave private residue, but cannot expose a
  protocol-created partial canonical opening.
- A complete visible opening is never automatically unlinked or republished by
  bootstrap recovery.
- A complete validated lone opening without retained file-data proof is
  re-dirtied over its full byte range and passes file and directory barriers
  before a later mutation may rely on it.
- Reconfirming identical schema-1 bytes changes their physical dirty state, not
  their semantic records, order or legacy-anchor bytes.
- History reconfirmation keeps the canonical artifact identity and protects an
  already present valid suffix by freezing the accepted coherent pair before the
  bounded write and requiring every byte outside the proven range to remain
  unchanged after the barriers.
- Unsupported no-replace publication refuses before canonical visibility
  rather than offering a weaker portability promise.
- This decision grants no implementation authority, enables no WAL-2 or
  schema-2 producer, and makes no release promise. Each behavior still requires
  its bounded implementation packet and black-box CLI acceptance.
