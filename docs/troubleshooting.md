# Troubleshooting

Use the check below to find the relevant contract. Keep the complete diagnostic
when asking for help; the detailed references describe safe recovery and the
state each command may change.

| Symptom | Check | Next step |
|---|---|---|
| `No module named validated_memory` | Confirm Python is 3.11 or newer and the installed package or source checkout is importable. | Follow [running the CLI outside the plugin](installing.md#running-the-cli-outside-the-plugin). |
| A hook produces no status or prompt context | Confirm the project adopted the layout. Prompt discovery also needs a valid profile at the exact project root and a matching activation mode. | Check [startup hooks](reference/hooks.md) and [agent integration](reference/agent-integration.md). Hooks fail open and do not gate the session. |
| No HTML views appear | Views are opt-in; check whether the project activated them with `init --view`. | Run `render` for selected outputs; see [render](reference/cli.md#render). |
| A probe reports `unknown` | Read its warning and inspect the anchor's configured probe and inputs. `unknown` records that the probe could not establish a verdict. | See [probe limits and outcomes](reference/cli.md#probe); resolve the cause, then run `probe` again. |
| `status` exits 1 | Read each `ERROR` finding and identify the command, file or configured freshness condition it names. `status` reports freshness; it does not run probes. | Resolve the reported condition, then rerun `status`; see [status](reference/cli.md#status). |
| `journal --check` reports an open or inconsistent transaction | Keep the full finding and transaction ID. Do not edit journal or vault files to clear it. | Follow [journal recovery](reference/journal.md#recovery) and the documented `journal --resolve` route when recovery cannot account for it. |
| Consultation refuses a store operation | Keep the full diagnostic and the whole SQLite store set, including sidecars. | Follow [consultation recovery](reference/consultation-storage.md#sqlite-format); do not manually remove sidecars. |
| A restore or multi-project relocation is refused | Preserve the backup, registered roots and consultation store. A refusal can indicate that the current identity set cannot be reconstructed safely. | Follow [whole-project backup and recovery](reference/recovery.md); its limits are explicit. |

For exact command syntax and exit codes, use the [CLI reference](reference/cli.md).
