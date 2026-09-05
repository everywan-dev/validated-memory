"""Command-line interface for validated-memory.

Exit code convention:
  0  clean run, or WARNING-level findings only (does not gate)
  1  ERROR-level findings or execution failure (gates)
  2  usage error (argparse)
"""

import argparse
import sys

from . import (
    __version__,
    derive,
    init,
    journal,
    lint,
    probe,
    render,
    status,
    validate,
)

SUBCOMMANDS = {
    "init": "Scaffold the validated-memory layout in an adopter project",
    "lint": "Lint the agent-memory layer: index sync, frontmatter, wikilinks, supersession",
    "validate": "Validate curated-knowledge units against the base contract",
    "derive": "Re-derive indexes and summaries from curated-knowledge units",
    "probe": "Run freshness probes and record ternary verdicts",
    "render": "Render static HTML views of the curated and agent-memory layers",
    "status": "Report project consistency and freshness; read-only, never probes",
    "journal": "Report the append-only record of what the plugin has written",
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="validated-memory",
        description="Enforcement CLI for the validated-memory plugin.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, metavar="<command>"
    )
    for name, help_text in SUBCOMMANDS.items():
        subparser = subparsers.add_parser(name, help=help_text, description=help_text)
        if name == "validate":
            subparser.add_argument(
                "path",
                nargs="?",
                metavar="PATH",
                help=(
                    "unit file or directory to validate "
                    f"(default: {validate.DEFAULT_KNOWLEDGE_DIR}/)"
                ),
            )
        if name == "derive":
            subparser.add_argument(
                "path",
                nargs="?",
                metavar="PATH",
                help=(
                    "unit file or directory to derive the index from "
                    f"(default: {validate.DEFAULT_KNOWLEDGE_DIR}/)"
                ),
            )
            subparser.add_argument(
                "--check",
                action="store_true",
                help=(
                    "recalculate without writing; fail if the on-disk index "
                    f"({derive.INDEX_FILENAME}) does not match"
                ),
            )
        if name == "lint":
            subparser.add_argument(
                "path",
                nargs="?",
                metavar="PATH",
                help=(
                    "agent-memory directory to lint "
                    f"(default: {lint.DEFAULT_MEMORY_DIR}/)"
                ),
            )
        if name == "probe":
            subparser.add_argument(
                "path",
                nargs="?",
                metavar="PATH",
                help=(
                    "unit file or directory to probe "
                    f"(default: {validate.DEFAULT_KNOWLEDGE_DIR}/)"
                ),
            )
        if name == "init":
            subparser.add_argument(
                "--harness-memory",
                metavar="PATH",
                help=(
                    "make PATH a move-proof symlink to this project's "
                    f"{lint.DEFAULT_MEMORY_DIR}/ directory"
                ),
            )
            subparser.add_argument(
                "--view",
                action="store_true",
                help=(
                    "create the static HTML views (knowledge.html, "
                    "memory.html) once each; never regenerates an "
                    "artifact that already exists"
                ),
            )
        if name == "render":
            subparser.add_argument(
                "--only-existing",
                action="store_true",
                help=(
                    "regenerate only the artifacts that already exist, and "
                    "create none (the startup hook's mode: fail-open)"
                ),
            )
        if name == "status":
            subparser.add_argument(
                "--skip-index",
                action="store_true",
                help=(
                    f"skip the {derive.INDEX_FILENAME} gate entirely, for an "
                    "adopter who does not version the derived index"
                ),
            )
            subparser.add_argument(
                "--fail-on",
                action="append",
                choices=(status.DRIFTED, status.UNKNOWN),
                metavar="{drifted,unknown}",
                dest="fail_on",
                help=(
                    "upgrade an active unit carrying this verdict to a "
                    "gating ERROR; repeatable"
                ),
            )
            subparser.add_argument(
                "--max-verdict-age",
                type=int,
                metavar="N",
                dest="max_verdict_age",
                help=(
                    "WARNING per active-unit anchor whose recorded verdict "
                    "is more than N day(s) old (UTC, strict), or whose age "
                    "cannot be determined"
                ),
            )
            subparser.add_argument(
                "--fail-on-aged",
                action="store_true",
                dest="fail_on_aged",
                help=(
                    "upgrade every finding --max-verdict-age emits -- aged "
                    "and age-unknown alike -- to a gating ERROR"
                ),
            )
            subparser.add_argument(
                "--as-of",
                type=status.parse_timestamp,
                metavar="TIMESTAMP",
                dest="as_of",
                help=(
                    "ISO 8601 timestamp (a trailing 'Z' accepted) to use as "
                    "'now' for age computation; default the real current UTC "
                    "time"
                ),
            )
            # Cross-flag errors must use this command's usage line.
            subparser.set_defaults(_status_subparser=subparser)
        if name == "journal":
            subparser.add_argument(
                "--check",
                action="store_true",
                help=(
                    "report every unfinished transaction and gate on it "
                    "(exit 1); without it, reporting never gates"
                ),
            )
            subparser.add_argument(
                "--resolve",
                metavar="ID",
                help=(
                    "close the unresolved transaction ID, with exactly one "
                    "of --accept, --restore or --abandon"
                ),
            )
            subparser.add_argument(
                "--accept",
                action="store_true",
                help=(
                    "--resolve: keep the state the path is in, recorded as "
                    "an observation of fact and never as a mutation"
                ),
            )
            subparser.add_argument(
                "--restore",
                action="store_true",
                help=(
                    "--resolve: put the preimage back from the vault, "
                    "refusing if its blob is missing or does not match"
                ),
            )
            subparser.add_argument(
                "--abandon",
                action="store_true",
                help="--resolve: leave the path as found, and record that",
            )
            # Cross-flag errors must use this command's usage line.
            subparser.set_defaults(_journal_subparser=subparser)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate":
        return validate.run(args.path, stdout=sys.stdout, stderr=sys.stderr)
    if args.command == "derive":
        return derive.run(
            args.path, args.check, stdout=sys.stdout, stderr=sys.stderr
        )
    if args.command == "lint":
        return lint.run(args.path, stdout=sys.stdout, stderr=sys.stderr)
    if args.command == "probe":
        return probe.run(args.path, stdout=sys.stdout, stderr=sys.stderr)
    if args.command == "render":
        return render.run(args.only_existing, stdout=sys.stdout, stderr=sys.stderr)
    if args.command == "status":
        # The age gate and clock override both require an age bound.
        if args.max_verdict_age is None:
            if args.fail_on_aged:
                args._status_subparser.error(
                    "--fail-on-aged requires --max-verdict-age"
                )
            if args.as_of is not None:
                args._status_subparser.error("--as-of requires --max-verdict-age")
        return status.run(
            args.skip_index,
            args.fail_on,
            args.max_verdict_age,
            args.fail_on_aged,
            args.as_of,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    if args.command == "journal":
        # Resolution requires an ID, exactly one action, and no --check.
        chosen = [
            name
            for name in journal.RESOLUTIONS
            if getattr(args, name.replace("-", "_"))
        ]
        if args.resolve is None:
            if chosen:
                args._journal_subparser.error(f"--{chosen[0]} requires --resolve")
        else:
            if not args.resolve.strip():
                # Empty IDs are usage errors, not project findings.
                args._journal_subparser.error(
                    "--resolve requires the id of a transaction"
                )
            if args.check:
                args._journal_subparser.error(
                    "--resolve may not be combined with --check, which is "
                    "read-only"
                )
            if len(chosen) != 1:
                args._journal_subparser.error(
                    "--resolve requires exactly one of --accept, --restore "
                    "or --abandon"
                )
        return journal.run(
            args.check,
            args.resolve,
            chosen[0] if chosen else None,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    return init.run(
        args.harness_memory, args.view, stdout=sys.stdout, stderr=sys.stderr
    )
