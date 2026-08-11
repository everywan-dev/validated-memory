"""Command-line interface for validated-memory.

Exit code convention:
  0  clean run, or WARNING-level findings only (does not gate)
  1  ERROR-level findings or execution failure (gates)
  2  usage error (argparse)
"""

import argparse
import sys

from . import __version__, validate

EXIT_OK = 0
EXIT_ERROR = 1

SUBCOMMANDS = {
    "init": "Scaffold the validated-memory layout in an adopter project",
    "lint": "Lint the agent-memory layer: index sync, frontmatter, wikilinks, supersession",
    "validate": "Validate curated-knowledge units against the base contract",
    "derive": "Re-derive indexes and summaries from curated-knowledge units",
    "probe": "Run freshness probes and record ternary verdicts",
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
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate":
        return validate.run(args.path, stdout=sys.stdout, stderr=sys.stderr)
    print(f"ERROR: '{args.command}' is not implemented yet", file=sys.stderr)
    return EXIT_ERROR
