#!/usr/bin/env python3
"""Measure the surface the codebase review is trying to shrink.

Run from the repository root:

    python3 docs/plans/reviews/measure.py                 # print the table
    python3 docs/plans/reviews/measure.py --save baseline # and snapshot it
    python3 docs/plans/reviews/measure.py --against baseline

Every number in `ledger.md` comes from here, so a claim about what the review
gained can be re-derived rather than believed. Token counts are bytes/4: an
approximation, used only to compare one snapshot against another, never as an
exact cost.

A docstring is the first statement of a module, class or function body, and
it is counted by walking the AST rather than the token stream: the token
before a function's docstring is the `:` of its own `def`, so a token-stream
rule that looks backwards counts module docstrings and silently misses every
other one -- which is most of the prose in this package.
"""

import argparse
import ast
import io
import json
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT_DIR = Path(__file__).resolve().parent / "metrics"

# The units of docs/plans/2026-09-03-codebase-review.md, in review order.
UNITS = [
    ("J1", "journal: reporting and fault seams",
     ["journal/reconcile.py", "journal/command.py", "journal/fault.py",
      "journal/__init__.py"]),
    ("J2", "journal: the vocabulary",
     ["journal/records.py", "journal/paths.py", "journal/operations.py",
      "journal/durable.py"]),
    ("J3", "journal: write-ahead log and lock",
     ["journal/transactions.py", "journal/lock.py"]),
    ("J4", "journal: the executor", ["journal/executor.py"]),
    ("S1", "the scaffolder", ["init.py", "adopt.py"]),
    ("C1", "the contract",
     ["contract.py", "validate.py", "extension.py", "frontmatter.py",
      "findings.py"]),
    ("F1", "freshness",
     ["derive.py", "verdicts.py", "probe.py", "probes/git_ref.py",
      "status.py"]),
    ("M1", "agent memory and corpus", ["lint.py", "memory.py", "corpus.py"]),
    ("V1", "the view stack",
     ["render.py", "knowledge_view.py", "knowledge_overview.py",
      "memory_view.py", "svg.py", "styles.py", "html.py"]),
    ("X1", "the entry point",
     ["cli.py", "__main__.py", "__init__.py", "probes/__init__.py"]),
]

TEST_FILES = ["tests/test_journal.py", "tests/test_render.py"]


DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef,
                    ast.AsyncFunctionDef)


def _prose(source):
    """Comment bytes and docstring bytes in one source file."""
    comment = sum(len(token.string)
                  for token in tokenize.generate_tokens(
                      io.StringIO(source).readline)
                  if token.type == tokenize.COMMENT)
    doc = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, DOCSTRING_OWNERS) or not node.body:
            continue
        first = node.body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            doc += len(ast.get_source_segment(source, first.value) or "")
    return comment, doc


def _names(source):
    tree = ast.parse(source)
    top = [n for n in tree.body
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef))]
    public = [n.name for n in top if not n.name.startswith("_")]
    return len(public), len(top) - len(public)


def measure(paths, root):
    total = {"loc": 0, "bytes": 0, "comment": 0, "doc": 0, "public": 0,
             "private": 0, "files": 0}
    for rel in paths:
        path = root / rel
        source = path.read_text(encoding="utf-8")
        comment, doc = _prose(source)
        public, private = _names(source)
        total["loc"] += len(source.splitlines())
        total["bytes"] += len(source)
        total["comment"] += comment
        total["doc"] += doc
        total["public"] += public
        total["private"] += private
        total["files"] += 1
    return total


def snapshot(root=REPO_ROOT):
    """Measure one checkout: `root` is a repository root, not always this one.

    A baseline is re-derived from a past commit by extracting it somewhere
    and measuring that, which is the only way to recompute one after the
    measurement itself is corrected.
    """
    package = root / "validated_memory"
    units = {key: measure(paths, package) for key, _, paths in UNITS}
    units["T1"] = measure(TEST_FILES, root)
    return units


def _row(key, label, m):
    prose = m["comment"] + m["doc"]
    share = prose / m["bytes"] if m["bytes"] else 0
    return (f"| {key} | {label} | {m['files']} | {m['loc']:,} | "
            f"{m['bytes'] // 4:,} | {m['comment']:,} | {m['doc']:,} | "
            f"{share:.0%} | {m['public']} / {m['private']} |")


def _delta(key, label, now, was):
    def d(field):
        return now[field] - was[field]
    prose_now = now["comment"] + now["doc"]
    prose_was = was["comment"] + was["doc"]
    return (f"| {key} | {label} | {d('loc'):+,} | "
            f"{(now['bytes'] - was['bytes']) // 4:+,} | "
            f"{prose_now - prose_was:+,} | "
            f"{d('public'):+d} / {d('private'):+d} |")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--save", metavar="NAME",
                        help="write the snapshot to metrics/NAME.json")
    parser.add_argument("--against", metavar="NAME",
                        help="print deltas against metrics/NAME.json")
    parser.add_argument("--root", metavar="PATH", type=Path,
                        default=REPO_ROOT,
                        help="measure this checkout instead of this one")
    args = parser.parse_args()

    now = snapshot(args.root)
    labels = {key: label for key, label, _ in UNITS}
    labels["T1"] = "the test surface"

    if args.against:
        was = json.loads((SNAPSHOT_DIR / f"{args.against}.json").read_text())
        print("| # | unit | LOC | ~tok | prose bytes | pub / priv |")
        print("|---|---|---:|---:|---:|---:|")
        for key in list(labels):
            print(_delta(key, labels[key], now[key], was[key]))
        totals_now = {k: sum(u[k] for u in now.values()) for k in now["J1"]}
        totals_was = {k: sum(u[k] for u in was.values()) for k in was["J1"]}
        print(_delta("", "**total**", totals_now, totals_was))
        return

    print("| # | unit | files | LOC | ~tok | comment | docstring | prose | pub / priv |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for key in labels:
        print(_row(key, labels[key], now[key]))
    totals = {k: sum(u[k] for u in now.values()) for k in now["J1"]}
    print(_row("", "**total**", totals))

    if args.save:
        SNAPSHOT_DIR.mkdir(exist_ok=True)
        target = SNAPSHOT_DIR / f"{args.save}.json"
        target.write_text(json.dumps(now, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        print(f"\nsaved {target.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
