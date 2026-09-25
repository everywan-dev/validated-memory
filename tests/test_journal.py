"""End-to-end tests for the journal: the durable record of every mutation.

Like every test in this suite these drive the CLI as a subprocess over a
fixture adopter tree, and never import the package's internals. What a
record means is `docs/reference/journal.md`; what it is for is
`docs/design/2026-08-30-the-journal-coverage-and-reversal-design.md`.
"""

import ast
import hashlib
import json
import os
import re
import select
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _final_tree_snapshot(root):
    """Capture the final namespace, content and modes without following links.

    Inode and time fields are deliberately absent. Equality proves exact final
    tree preservation, including transaction bytes, not that no transient read
    or write occurred while the command ran.
    """
    snapshot = []

    def visit(directory):
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
        for entry in children:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            info = entry.stat(follow_symlinks=False)
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                snapshot.append((relative, "symlink", os.readlink(path), mode))
            elif stat.S_ISREG(info.st_mode):
                snapshot.append((relative, "file", path.read_bytes(), mode))
            elif stat.S_ISDIR(info.st_mode):
                snapshot.append((relative, "directory", mode))
                visit(path)
            else:
                snapshot.append(
                    (relative, "other", stat.S_IFMT(info.st_mode), mode)
                )

    visit(root)
    return snapshot


def _external_harness_path(adopter, *parts):
    return adopter.parent / f"{adopter.name}-harness" / Path(*parts)

# Path methods that mutate, whatever the receiver.
PATH_MUTATORS = {
    "write_text", "write_bytes", "mkdir", "symlink_to", "hardlink_to",
    "touch", "chmod", "lchmod", "rmdir", "unlink", "rename",
}
# Qualified `os` mutations. Path.replace is detected separately by its
# one-argument shape so ordinary string replacement does not count.
OS_MUTATORS = {
    "replace", "rename", "renames", "remove", "removedirs", "symlink",
    "link", "unlink", "makedirs", "mkdir", "rmdir", "truncate", "chmod",
    "utime", "write",
}
# `shutil` functions that mutate.
SHUTIL_MUTATORS = {
    "copy", "copy2", "copyfile", "copytree", "copymode", "copystat", "move",
    "rmtree", "make_archive", "unpack_archive",
}
# Journal primitives remain writes in non-exempt implementation modules.
JOURNAL_MUTATORS = {"install"}
DURABLE_MUTATORS = {
    "install", "install_bytes", "create_exclusive", "create_directory",
    "replace_symlink", "append_bytes", "remove_name", "ensure_owned_directory",
    "ensure_external_directory", "repair_symlink", "republish_file",
    "republish_directory", "publish_no_replace", "reconfirm_exact_file",
    "reconfirm_exact_range",
}
FACADE = "validated_memory.journal"


def _import_module(node, relative):
    if not node.level:
        return node.module or ""
    package = ["validated_memory", *relative.split("/")[:-1]]
    return ".".join(package[:len(package) - node.level + 1]
                    + ([node.module] if node.module else []))


def _origins(node, bindings):
    """Resolve only static names, attributes and literal/computed getattr.

    A '*' member retains unknown reflection on a proven receiver. Unresolved
    receivers use '?' so path-method vocabulary still works without claiming
    to infer types. Dynamic imports, eval and interprocedural flows are outside
    this structural check.
    """
    if isinstance(node, ast.Name):
        return bindings.get(node.id, {node.id})
    if isinstance(node, ast.Attribute):
        return {base + "." + node.attr for base in _origins(node.value, bindings)}
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "getattr" and len(node.args) >= 2):
        member = node.args[1]
        member = member.value if isinstance(member, ast.Constant) and isinstance(member.value, str) else "*"
        origins = {base + "." + member for base in _origins(node.args[0], bindings)}
        # Preserve optional-flag fallback safety through assignments as well
        # as inline expressions; the flag's name alone is insufficient.
        if origins & {"os." + flag for flag in READ_ONLY_OPEN_FLAGS}:
            if not (len(node.args) == 3 and isinstance(node.args[2], ast.Constant)
                    and node.args[2].value == 0):
                origins.add("?")
        return origins
    return {"?"}


def _bound_nodes(tree, relative):
    """Pair source nodes with bounded lexical provenance, never sibling state.

    Local bindings mask enclosing names, including parameters. Multiple local
    assignments are conservatively unioned regardless of ordering/branches:
    rebinding a sensitive alias cannot make its earlier uses disappear. This
    intentionally can reject a read after a sensitive name was reassigned.
    Comprehension locals are isolated; function headers use the enclosing
    scope and method free names skip class namespaces. Class-body assignments
    conservatively retain enclosing fallback origins. No execution, control
    flow, dynamic import or arbitrary Python reflection is inferred.
    """
    functions = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
    comprehensions = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    boundaries = (*functions, ast.ClassDef, *comprehensions)

    def parameters(node):
        return [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                *([node.args.vararg] if node.args.vararg else []),
                *([node.args.kwarg] if node.args.kwarg else [])]

    def headers(node):
        if isinstance(node, functions):
            return [*getattr(node, "decorator_list", []), *node.args.defaults,
                    *(v for v in node.args.kw_defaults if v is not None),
                    *(a.annotation for a in parameters(node) if a.annotation),
                    *([node.returns] if getattr(node, "returns", None) else [])]
        if isinstance(node, ast.ClassDef):
            return [*node.decorator_list, *node.bases, *node.keywords]
        return [node.generators[0].iter]

    def contents(node):
        if isinstance(node, functions):
            body = [node.body] if isinstance(node, ast.Lambda) else node.body
            return [*parameters(node), *body]
        if isinstance(node, comprehensions):
            values = ([node.key, node.value] if isinstance(node, ast.DictComp)
                      else [node.elt])
            for index, generator in enumerate(node.generators):
                values.extend([generator.target, *generator.ifs])
                if index:
                    values.append(generator.iter)
            return values
        return node.body

    def visit(scope, inherited):
        own, nested = [], []
        def collect(node):
            own.append(node)
            if isinstance(node, boundaries):
                nested.append(node)
                for header in headers(node):
                    collect(header)
            elif not isinstance(node, ast.arg):
                for child in ast.iter_child_nodes(node):
                    collect(child)
        for child in contents(scope):
            collect(child)
        bindings = {name: set(values) for name, values in inherited.items()}
        local = {node.id for node in own if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))}
        local.update(node.arg for node in own if isinstance(node, ast.arg))
        local.update(node.name for node in nested if hasattr(node, "name"))
        enclosing_assignments = {
            name
            for node in own
            if isinstance(node, (ast.Global, ast.Nonlocal))
            for name in node.names
        }
        imports = []
        for node in own:
            if isinstance(node, ast.Import):
                imports.extend((a.asname or a.name.split(".")[0], a.name if a.asname else a.name.split(".")[0]) for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = _import_module(node, relative)
                imports.extend((a.asname or a.name, module + "." + a.name) for a in node.names)
        local.update(name for name, _ in imports)
        for name in local:
            # Class-body LOAD_NAME falls back to enclosing bindings before a
            # local assignment (notably `alias = alias`). Keep both possible
            # origins conservatively, without passing this namespace to methods.
            bindings[name] = (
                set(inherited.get(name, ()))
                if isinstance(scope, ast.ClassDef) or name in enclosing_assignments
                else set()
            )
        for node in own:
            if isinstance(node, ast.arg):
                bindings[node.arg].add("?")
        for name, origin in imports:
            bindings[name].add(origin)
        assignments = []
        for node in own:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                assignments.extend((t.id, node.value) for t in targets if isinstance(t, ast.Name))
        # Bound alias propagation by the number of assignments; recursive
        # attribute constructions cannot create an unbounded fixed point.
        for _ in range(len(assignments) + 1):
            previous = {k: set(v) for k, v in bindings.items()}
            for name, value in assignments:
                bindings[name].update(_origins(value, previous))
            if bindings == previous:
                break
        for node in own:
            yield node, bindings
        for child in nested:
            # A class body executes in its own namespace, but methods and
            # comprehension bodies resolve free names outside that namespace.
            yield from visit(child, inherited if isinstance(scope, ast.ClassDef) else bindings)
    yield from visit(tree, {})


# Require a journal receiver so unrelated append/write calls cannot count.
RECORDERS = {"session", "journal"}
# The complete adopting-session method allowlist. Module exports are pinned
# separately; the session is obtained only from `adopting_run`.
PERMITTED_RUN_METHODS = ("execute", "observe", "recover")
RECORDING_METHODS = set(PERMITTED_RUN_METHODS)

# Paths relative to validated_memory identify modules; basenames do not.
JOURNAL_SOURCE = "journal"

# Explicit modules allowed raw writes. Other journal modules remain scanned;
# new modules do not inherit an exemption from their directory.
RAW_WRITE_MODULES = {
    "journal/durable.py",
    "journal/executor.py",
    "journal/fault.py",
    "journal/lock.py",
    "journal/records.py",
    "journal/protocol.py",
    "journal/transactions.py",
}


def _inside_journal(relative):
    """Whether `relative` is the journal's own source, or a module of it."""
    return relative.startswith(JOURNAL_SOURCE + "/")

# --- the two exception sets, and why they are two ------------------------------
#
# Keys are (relative module path, function); `*` covers a module. Each entry
# carries its reason because the two sets permit different behavior.

# Approved adopter-tree mutations outside the executor.
EXECUTOR_EXCEPTIONS = {
    ("init.py", "relink"): (
        "the fail-open harness link repair: the contract requires the link "
        "back when the journal cannot be read or written at all, which is "
        "the SessionStart hook's only job, and an executor that requires a "
        "working journal cannot serve it "
        "(docs/design/2026-09-01-the-journal-core.md §4). This closure is "
        "the whole of it -- `_sync_symlink`, which builds it, mutates "
        "nothing itself and so is not listed"
    ),
    ("adopt.py", "take_over"): (
        "the harness absorption: it recognises a tree, copies "
        "conditionally, reconciles an index and renames the source, and "
        "tolerates a per-file conflict -- which needs its own planner "
        "before the executor can apply it "
        "(docs/design/2026-09-01-the-journal-core.md §4)"
    ),
    ("adopt.py", "_absorb"): "the same absorption, one function of it",
    ("adopt.py", "_reconcile_index"): "the same absorption, one function of it",
    ("adopt.py", "_park"): "the same absorption, one function of it",
}

# Writes that reach no journal because what they write is not adopter data:
# a derived artifact its own command regenerates, or another append-only log.
# These are not executor exceptions -- there is nothing about them for the
# executor to own.
UNRECORDED_WRITES = {
    ("consultation/store.py", "*"): (
        "ADR 0017 approves only the external SQLite workspace writer and its "
        "test rendezvous; canonical adopter mutations remain forbidden"
    ),
    ("render.py", "*"): "writes only derived artifacts, which `render` rebuilds",
    ("derive.py", "*"): "writes only derived artifacts, which `derive` rebuilds",
    ("init.py", "_ensure_views"): (
        "`init --view` builds the same derived artifacts `render` does"
    ),
    ("verdicts.py", "append"): (
        "`verdicts.jsonl` is the other append-only log: `probe` writes it "
        "and re-running `probe` rebuilds it, which is exactly what a "
        "journal is not (see `docs/reference/journal.md`, \"What is "
        "recorded, and what is not yet\")"
    ),
}

# Private protocol spellings matched as text, including prose. `prepare_op`
# and `append_op` are retained as anti-reintroduction guards for the removed
# two-record API; the remaining names cover the current private write path.
#
# `_bootstrap` is absent because the symbol scan below catches it precisely.
# `record`, `append` and `install` are ordinary English and Python, so a text
# scan would reject unrelated prose or calls. The symbol and call scans below
# pin them without that false-positive surface.
PRIVATE_JOURNAL_NAMES = (
    "prepare_op",
    "append_op",
    "open_transaction",
    "mark_published",
    "abort_transaction",
    "remove_transaction_file",
    "_write_transaction_file",
    "write_denied",
    "_park_preimage",
    "_publish",
)

# Complete top-level export allowlist. Failure messages are built from this
# tuple so the advertised and enforced surfaces cannot drift.
PERMITTED_JOURNAL_EXPORTS = (
    "ABSENT",
    "FILE",
    "JOURNAL_FILENAME",
    "JournalError",
    "LOCAL",
    "OUTCOME_APPLIED",
    "OUTCOME_NOOP",
    "OUTCOME_REFUSED",
    "RECOVERED",
    "REPO",
    "RESOLUTIONS",
    "SYMLINK",
    "VAULT_DIRNAME",
    "adopting_run",
    "append_to_file",
    "create_directory",
    "create_file",
    "digest",
    "link_to",
    "repair_harness_link",
    "resolve_transaction",
    "run",
)

# These ordinary names are matched as calls to avoid prose false positives.
PRIVATE_JOURNAL_CALLS = ("record", "install")


def _writing_mode(call, index=None):
    """Recognize literal write modes; unknown expressions count as writes.

    Account for the different mode positions in open(path, mode) and
    path.open(mode); an omitted mode is read-only."""
    if index is None:
        index = 1 if isinstance(call.func, ast.Name) else 0
    mode = call.args[index] if len(call.args) > index else None
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    if mode is None:
        return False
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return any(character in mode.value for character in "wax+")
    return True


# `os.open` takes flags, not a mode string. Only these flags prove a read-only
# descriptor; O_WRONLY, O_RDWR, O_CREAT, O_TRUNC and O_APPEND are absent by
# design, and so is anything the scanner cannot evaluate.
READ_ONLY_OPEN_FLAGS = frozenset(
    {
        "O_RDONLY",
        "O_DIRECTORY",
        "O_NOFOLLOW",
        "O_NONBLOCK",
        "O_CLOEXEC",
        "O_PATH",
        "O_NOCTTY",
    }
)


def _read_only_flags(node, bindings=None):
    """Whether a flags expression is a proven read-only `os.O_*` combination.

    Recursive over `|` alone. The leaves are a literal 0, an allowed `os.O_*`
    attribute and `getattr(os, "O_...", 0)` over the same set, including static
    aliases. An unresolved variable, arithmetic expression or unlisted flag
    is not proven."""
    bindings = bindings or {}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _read_only_flags(node.left, bindings) and _read_only_flags(node.right, bindings)
    origins = _origins(node, bindings)
    if origins and all(origin in {"os." + flag for flag in READ_ONLY_OPEN_FLAGS} for origin in origins):
        if isinstance(node, ast.Call):
            return len(node.args) == 3 and isinstance(node.args[2], ast.Constant) and node.args[2].value == 0
        return True
    if isinstance(node, ast.Constant):
        return node.value == 0
    return False


def _os_open_writes(call, bindings=None):
    """Whether an `os.open(...)` call may create or modify its target.

    A missing flags argument, a computed one, or any flag outside the
    read-only allowlist counts as a write."""
    flags = call.args[1] if len(call.args) > 1 else None
    for keyword in call.keywords:
        if keyword.arg == "flags":
            flags = keyword.value
    if flags is None:
        return True
    return not _read_only_flags(flags, bindings)


def _mutating_call(call, bindings=None):
    """Recognize the bounded mutation vocabulary through static provenance."""
    bindings = bindings or {}
    for origin in sorted(_origins(call.func, bindings)):
        receiver, _, member = origin.rpartition(".")
        if origin == "os.open":
            if _os_open_writes(call, bindings):
                return "os.open(...) for writing"
            continue
        if origin == "open" or member == "open":
            if _writing_mode(call, 1 if origin == "open" else 0):
                return "open(...) for writing"
        if (receiver == "os" and member in OS_MUTATORS
                or receiver == "shutil" and member in SHUTIL_MUTATORS
                or receiver == FACADE + ".durable" and member in DURABLE_MUTATORS
                or receiver == FACADE + ".records" and member == "append"
                or receiver == "journal" and member in JOURNAL_MUTATORS):
            return origin
        if member == "*" and (receiver in {"os", "shutil"} or receiver.startswith(FACADE + ".")):
            return origin + " (computed member)"
        if member in PATH_MUTATORS:
            return member
        if member == "replace" and len(call.args) == 1 and not call.keywords:
            return "replace"
    return None


def _recording_call(call):
    """Whether this call reaches the journal."""
    function = call.func
    return (
        isinstance(function, ast.Attribute)
        and function.attr in RECORDING_METHODS
        and isinstance(function.value, ast.Name)
        and function.value.id in RECORDERS
    )


def _scopes(tree):
    """Return (name, direct calls) for each function and the module.

    Nested functions are independent scopes. Duplicate function names share
    an exception, including identically named closures or methods; this
    deliberately over-covers rather than resolving that ambiguity. Decorators,
    defaults and annotations execute in the enclosing scope, not the function
    body they describe, so a recorder in that body cannot cover them."""
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    lambdas = [node for node in ast.walk(tree) if isinstance(node, ast.Lambda)]

    def _headers(node):
        if isinstance(node, ast.Lambda):
            return [
                *node.args.defaults,
                *(value for value in node.args.kw_defaults if value is not None),
            ]
        return [
            *node.decorator_list,
            *node.args.defaults,
            *(value for value in node.args.kw_defaults if value is not None),
            *(arg.annotation for arg in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
                *([node.args.vararg] if node.args.vararg else []),
                *([node.args.kwarg] if node.args.kwarg else []),
            ) if arg.annotation is not None),
            *([node.returns] if node.returns is not None else []),
        ]

    def _calls(nodes):
        calls = []

        def visit(node):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for header in _headers(node):
                    visit(header)
                return
            if isinstance(node, ast.Call):
                calls.append(node)
            for child in ast.iter_child_nodes(node):
                visit(child)

        for node in nodes:
            visit(node)
        return calls

    scopes = [(function.name, _calls(function.body)) for function in functions]
    scopes.extend(("<lambda>", _calls([function.body])) for function in lambdas)
    scopes.append(("<module>", _calls(tree.body)))
    return scopes


def _records(path):
    """Every JSON record in a `.jsonl` file, in file order."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_init_writes_a_journal_whose_records_carry_the_common_fields(
    run_cli, tmp_path
):
    """Pin nonempty attribution fields, schema and operation vocabulary."""
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    journal = tmp_path / "journal.jsonl"
    assert journal.is_file(), sorted(p.name for p in tmp_path.iterdir())
    records = _records(journal)
    assert records, "the journal is empty"
    for entry in records:
        assert entry["schema"] == 1, entry
        assert entry["at"].endswith("Z"), entry
        assert entry["version"], entry
        assert entry["adoption"], entry
        assert entry["run"], entry
        assert entry["durability"] == "repo", entry
        assert entry["op"] in (
            "observe",
            "create",
            "replace",
            "patch",
            "append",
            "link",
            "rename",
            "remove",
            "move",
        ), entry


def test_the_adoption_id_is_minted_once_and_survives_later_runs(run_cli, tmp_path):
    """Rerunning init keeps the same single adoption ID."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    first = {entry["adoption"] for entry in _records(tmp_path / "journal.jsonl")}
    assert len(first) == 1, first

    assert run_cli("init", cwd=tmp_path).returncode == 0
    second = {entry["adoption"] for entry in _records(tmp_path / "journal.jsonl")}
    assert second == first, (first, second)


def test_each_invocation_groups_its_records_under_one_run_id(run_cli, tmp_path):
    """Removing an item forces the second run to record under a new run ID."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    (tmp_path / "knowledge-extension.md").unlink()

    assert run_cli("init", cwd=tmp_path).returncode == 0

    runs = [entry["run"] for entry in _records(tmp_path / "journal.jsonl")]
    assert len(set(runs)) == 2, runs


def test_a_journal_that_cannot_be_parsed_is_refused_with_its_line(run_cli, tmp_path):
    """Malformed JSON gates init with the journal and parse fault identified."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    journal.write_text(
        journal.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8"
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "journal.jsonl" in result.stderr, result.stderr
    assert "not valid JSON" in result.stderr, result.stderr


def test_a_created_file_records_its_postimage_and_both_stages(run_cli, tmp_path):
    """Pin file postimage digests and matching prepared/committed sequences."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    records = _records(tmp_path / "journal.jsonl")
    # `create` also covers a directory (no preimage or postimage: a
    # directory has no content to digest), so file creates are the ones that
    # carry a `postimage`.
    creates = [
        e
        for e in records
        if e["op"] == "create" and e["stage"] == "committed" and "postimage" in e
    ]
    assert creates, records
    for entry in creates:
        assert entry["postimage"].startswith("sha256:"), entry

    # Each mutation contributes matching prepared and committed op/path
    # sequences. This filtered comparison does not pin their interleaving.
    # Only `observe` stands alone because it records a fact, not a mutation.
    prepared = [
        (e["op"], e["path"]) for e in records if e["stage"] == "prepared"
    ]
    committed = [
        (e["op"], e["path"])
        for e in records
        if e["stage"] == "committed" and e["op"] != "observe"
    ]
    assert prepared == committed, records


def test_init_records_create_for_what_it_made_and_observe_for_what_it_kept(
    run_cli, tmp_path
):
    """Distinguish the directory present before adoption from created paths."""
    (tmp_path / "knowledge").mkdir()

    assert run_cli("init", cwd=tmp_path).returncode == 0

    records = _records(tmp_path / "journal.jsonl")
    committed = [e for e in records if e["stage"] == "committed"]
    by_path = {e["path"]: e["op"] for e in committed}
    assert by_path["knowledge"] == "observe", by_path
    assert by_path["memory"] == "create", by_path
    assert by_path["validated-memory.md"] == "create", by_path
    assert by_path["memory/MEMORY.md"] == "create", by_path
    assert by_path["knowledge-extension.md"] == "create", by_path


def test_a_re_run_that_creates_nothing_records_nothing(run_cli, tmp_path):
    """Two unchanged reruns leave journal bytes identical: no re-observations."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    first = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")

    assert run_cli("init", cwd=tmp_path).returncode == 0
    assert run_cli("init", cwd=tmp_path).returncode == 0

    assert (tmp_path / "journal.jsonl").read_text(encoding="utf-8") == first


def test_a_pre_existing_path_is_observed_once_and_only_once(run_cli, tmp_path):
    """The fact that adoption found `knowledge/` there is recorded exactly once."""
    (tmp_path / "knowledge").mkdir()

    assert run_cli("init", cwd=tmp_path).returncode == 0
    assert run_cli("init", cwd=tmp_path).returncode == 0

    records = _records(tmp_path / "journal.jsonl")
    observed = [
        e for e in records if e["op"] == "observe" and e["path"] == "knowledge"
    ]
    assert len(observed) == 1, records


def test_a_directory_is_recorded_before_it_is_created(run_cli, tmp_path):
    """Pin the directory history pair and stage order, not write-ahead timing.

    Both history records follow publication. Fault-seam tests exercise the
    observable recovery residues, not transaction fsync ordering."""
    assert run_cli("init", cwd=tmp_path).returncode == 0

    records = _records(tmp_path / "journal.jsonl")
    stages = [
        e["stage"] for e in records if e["path"] == "memory" and e["op"] == "create"
    ]
    assert stages == ["prepared", "committed"], records


def test_a_path_the_journal_already_knows_is_never_observed_as_pre_existing(
    run_cli, tmp_path
):
    """Removing the committed half leaves an open mutation, not an observation.

    The next init must not claim the directory predated adoption; --check
    must still report its applied, unfinished mutation."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    kept = [
        line
        for line in journal.read_text(encoding="utf-8").splitlines()
        if not (
            json.loads(line)["path"] == "knowledge"
            and json.loads(line)["stage"] == "committed"
        )
    ]
    journal.write_text("\n".join(kept) + "\n", encoding="utf-8")

    assert run_cli("init", cwd=tmp_path).returncode == 0

    records = _records(journal)
    assert not [
        e for e in records if e["op"] == "observe" and e["path"] == "knowledge"
    ], records
    # The interrupted transaction is still open, and still reported.
    result = run_cli("journal", "--check", cwd=tmp_path)
    assert result.returncode == 1, result.stdout
    assert "knowledge" in result.stderr, result.stderr
    assert "applied" in result.stderr, result.stderr


def test_journal_reports_the_log_and_exits_clean(run_cli, tmp_path):
    """A valid log prints its count and exits 0; malformed logs can still gate."""
    assert run_cli("init", cwd=tmp_path).returncode == 0

    result = run_cli("journal", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "journal:" in result.stdout, result.stdout
    assert "record(s)" in result.stdout, result.stdout


def test_journal_check_reconciles_an_unfinished_transaction(run_cli, tmp_path):
    """An unmatched prepared record reports a diverged, unfinished mutation."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    orphan = {
        "schema": 1,
        "at": "2026-08-31T00:00:00Z",
        "version": "1.6.0",
        "adoption": json.loads(journal.read_text().splitlines()[0])["adoption"],
        "run": "0000000000000000",
        "durability": "repo",
        "op": "replace",
        "purpose": "init",
        "path": "validated-memory.md",
        "stage": "prepared",
        "preimage": "sha256:" + "0" * 64,
        "postimage": "sha256:" + "1" * 64,
    }
    journal.write_text(
        journal.read_text(encoding="utf-8")
        + json.dumps(orphan, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "validated-memory.md" in result.stderr, result.stderr
    assert "unfinished" in result.stderr, result.stderr
    assert "diverged" in result.stderr, result.stderr


def test_journal_check_reports_each_of_the_four_states(run_cli, tmp_path):
    """Pin applied, unapplied and unknown; the preceding test pins diverged.

    Distinct orphan run IDs prevent pairing with init history. A directory
    makes the file unreadable even for root, unlike permission bits."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    written = _records(journal)
    config = next(
        entry
        for entry in written
        if entry["path"] == "validated-memory.md"
        and entry["stage"] == "committed"
    )

    # `applied`: the bytes on disk are the postimage, so the mutation
    # happened and only the closing record was lost.
    _append_record(
        journal,
        _record(
            journal,
            run="1111111111111111",
            op="create",
            path="validated-memory.md",
            preimage=None,
            postimage=config["postimage"],
        ),
    )
    # `unapplied`: a `create` whose path is genuinely absent.
    _append_record(
        journal,
        _record(
            journal,
            run="2222222222222222",
            op="create",
            path="never-written.md",
            preimage=None,
            postimage="sha256:" + "3" * 64,
        ),
    )
    # `unknown`: the bytes cannot be read at all -- here a directory, which
    # binds every user including root, unlike a permission bit.
    _append_record(
        journal,
        _record(journal, run="4444444444444444", path="knowledge"),
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    reported = {
        line.split(": journal: ")[0].removeprefix("ERROR: "): line
        for line in result.stderr.splitlines()
    }
    assert "the path is applied" in reported["validated-memory.md"], reported
    assert "the path is unapplied" in reported["never-written.md"], reported
    assert "the path is unknown" in reported["knowledge"], reported


def test_a_broken_symlink_where_a_directory_was_expected_is_not_applied(
    run_cli, tmp_path
):
    """A broken symlink does not satisfy an expected directory.

    The legacy create record without image digests denotes mkdir. Testing
    exists() or is_symlink() would falsely classify this path as applied."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    orphan = _record(
        journal,
        run="6666666666666666",
        op="create",
        path="never-created-dir",
    )
    del orphan["preimage"]
    del orphan["postimage"]
    orphan["note"] = "directory created"
    _append_record(journal, orphan)
    (tmp_path / "never-created-dir").symlink_to(tmp_path / "does-not-exist")

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "never-created-dir" in result.stderr, result.stderr
    assert "the path is unapplied" in result.stderr, result.stderr


def test_a_write_over_an_existing_file_parks_its_preimage(run_cli, tmp_path):
    """Appending the ignore entry parks the original bytes under their digest.

    Repeating that append pins deduplication by blob name and inode, not
    merely equal content. This test does not establish fsync ordering."""
    import hashlib

    before = "build/\n"
    (tmp_path / ".gitignore").write_text(before, encoding="utf-8")

    assert run_cli("init", cwd=tmp_path).returncode == 0

    reference = hashlib.sha256(before.encode("utf-8")).hexdigest()
    blob = tmp_path / ".validated-memory" / "preimages" / reference
    assert blob.is_file(), sorted(
        p.name for p in (tmp_path / ".validated-memory").iterdir()
    )
    # Named after its own digest, and holding exactly the bytes that were
    # there before the append.
    assert blob.read_text(encoding="utf-8") == before
    record = next(
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["op"] == "append" and entry["stage"] == "committed"
    )
    assert record["preimage"] == f"sha256:{reference}", record
    assert record["prior_bytes"] == len(before.encode("utf-8")), record

    # Parked only the first time: the same bytes park the same digest, and
    # the blob that is already there is left alone rather than rewritten.
    identity = blob.stat().st_ino
    (tmp_path / ".gitignore").write_text(before, encoding="utf-8")
    assert run_cli("init", cwd=tmp_path).returncode == 0
    blobs = sorted((tmp_path / ".validated-memory" / "preimages").iterdir())
    assert [entry.name for entry in blobs] == [reference], blobs
    assert blob.stat().st_ino == identity, "the blob was rewritten"


def test_journal_check_catches_a_second_write_to_one_path_in_one_run(
    run_cli, tmp_path
):
    """A second prepared half cannot reuse the first write's committed half.

    Sharing run and path must not let set-based pairing hide interruption."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    first_write = next(
        entry
        for entry in records
        if entry["path"] == "validated-memory.md" and entry["stage"] == "committed"
    )

    second_write = dict(first_write)
    second_write["stage"] = "prepared"
    second_write["op"] = "replace"
    second_write["preimage"] = "sha256:" + "2" * 64
    second_write["postimage"] = "sha256:" + "3" * 64

    journal.write_text(
        journal.read_text(encoding="utf-8")
        + json.dumps(second_write, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "validated-memory.md" in result.stderr, result.stderr
    assert "unfinished" in result.stderr, result.stderr


# --- a journal is data, never instructions -------------------------------
# (docs/design/2026-08-30-the-journal-coverage-and-reversal-design.md §7)


def _append_record(path, entry):
    """Append one hand-built record to a journal file, as a hostile edit would."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(
        existing + json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8"
    )


def _record(journal, **overrides):
    """A complete record, copied from one the CLI wrote, with fields replaced."""
    first = _records(journal)[0]
    entry = {
        "schema": 1,
        "at": "2026-08-31T00:00:00Z",
        "version": first["version"],
        "adoption": first["adoption"],
        "run": "0000000000000000",
        "durability": "repo",
        "op": "replace",
        "purpose": "init",
        "path": "validated-memory.md",
        "stage": "prepared",
        "preimage": "sha256:" + "0" * 64,
        "postimage": "sha256:" + "1" * 64,
    }
    entry.update(overrides)
    return entry


def test_a_field_of_the_wrong_type_is_a_finding_not_a_traceback(run_cli, tmp_path):
    """A string schema is valid JSON but gates all three commands cleanly."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    _append_record(journal, _record(journal, schema="1"))

    for arguments in (("journal",), ("journal", "--check"), ("init",)):
        result = run_cli(*arguments, cwd=tmp_path)

        assert result.returncode == 1, (arguments, result.stdout)
        assert "Traceback" not in result.stderr, (arguments, result.stderr)
        assert "ERROR" in result.stderr, (arguments, result.stderr)
        assert "schema" in result.stderr, (arguments, result.stderr)


def test_a_boolean_where_a_number_goes_is_refused_in_an_optional_field_too(
    run_cli, tmp_path
):
    """Reject a boolean mode even though Python treats bool as an int subtype."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    _append_record(journal, _record(journal, mode=True))

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert (
        "record field 'mode' holds bool, which it may not" in result.stderr
    ), result.stderr


def test_a_record_from_a_newer_schema_is_refused_rather_than_read_in_part(
    run_cli, tmp_path
):
    """An unknown schema gates with upgrade advice and reports zero records.

    Good lines precede the bad one: these assertions forbid a partial count,
    but do not prove refusal occurred before those lines were parsed."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    _append_record(journal, _record(journal, schema=2))

    for arguments in (("journal",), ("journal", "--check"), ("init",)):
        result = run_cli(*arguments, cwd=tmp_path)

        assert result.returncode == 1, (arguments, result.stdout)
        assert "Traceback" not in result.stderr, (arguments, result.stderr)
        assert "newer than this plugin understands" in result.stderr, (
            arguments,
            result.stderr,
        )
        assert "upgrade the plugin" in result.stderr, (arguments, result.stderr)
    assert "journal: 0 record(s)" in run_cli("journal", cwd=tmp_path).stdout


def test_a_path_that_is_not_a_string_is_a_finding_not_a_traceback(run_cli, tmp_path):
    """The reconciler builds a path out of the record, so its type is load-bearing."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    _append_record(journal, _record(journal, path=123))

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "path" in result.stderr, result.stderr


def test_a_repository_record_may_not_send_the_reader_outside_the_root(
    run_cli, tmp_path
):
    """Reject an absolute repository path rather than report its content state."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    _append_record(journal, _record(journal, path="/etc/passwd"))

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "diverged" not in result.stderr, result.stderr
    assert "unfinished" not in result.stderr, result.stderr
    assert "/etc/passwd" in result.stderr, result.stderr
    assert "adopter root" in result.stderr, result.stderr


def test_a_repository_record_may_not_climb_out_with_dot_dot(run_cli, tmp_path):
    """The same refusal for the relative way out of the root."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert run_cli("init", cwd=adopter).returncode == 0
    journal = adopter / "journal.jsonl"
    _append_record(journal, _record(journal, path="../outside.md"))

    result = run_cli("journal", "--check", cwd=adopter)

    assert result.returncode == 1, result.stdout
    assert "adopter root" in result.stderr, result.stderr


def test_a_record_in_the_wrong_artifact_is_refused(run_cli, tmp_path):
    """A local-durability record in the repository journal must be refused."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    _append_record(
        journal, _record(journal, durability="local", path="/etc/passwd")
    )

    result = run_cli("journal", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "durability" in result.stderr, result.stderr


def test_a_corrupt_vault_journal_is_reported_against_the_vault(run_cli, tmp_path):
    """The diagnostic identifies the corrupt local log, not journal.jsonl."""
    harness_memory = tmp_path / "harness" / "memory"
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert (
        run_cli(
            "init", "--harness-memory", str(harness_memory), cwd=adopter
        ).returncode
        == 0
    )
    vault = adopter / ".validated-memory" / "local.jsonl"
    vault.write_text(
        vault.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8"
    )

    result = run_cli("journal", cwd=adopter)

    assert result.returncode == 1, result.stdout
    assert ".validated-memory/local.jsonl:" in result.stderr, result.stderr


def _package_modules():
    """Return sorted (relative path, path) pairs; basenames are not identities."""
    root = REPO_ROOT / "validated_memory"
    return [
        (path.relative_to(root).as_posix(), path)
        for path in sorted(root.rglob("*.py"))
    ]


def _write_offenders(source, relative):
    exempt = {**EXECUTOR_EXCEPTIONS, **UNRECORDED_WRITES}
    if relative in RAW_WRITE_MODULES or (relative, "*") in exempt:
        return []
    tree = ast.parse(source)
    bindings = {id(node): values for node, values in _bound_nodes(tree, relative)}
    offenders = []
    for name, calls in _scopes(tree):
        if (relative, name) in exempt or any(_recording_call(call) for call in calls):
            continue
        for call in calls:
            mutation = _mutating_call(call, bindings[id(call)])
            if mutation:
                offenders.append(f"{relative}:{call.lineno}: {name} calls {mutation} and never reaches the journal")
    return offenders


def test_every_write_in_the_package_goes_through_the_journal():
    """Structurally require a recorder beside each recognized filesystem write.

    EXECUTOR_EXCEPTIONS permits specified adopter mutations without the
    executor; UNRECORDED_WRITES covers derived artifacts and the verdict log.
    RAW_WRITE_MODULES explicitly permits the journal implementation.

    This bounded vocabulary resolves static aliases and getattr. New write
    idioms and arbitrary reflection remain outside it. A recorder
    and mutation merely coexist in one scope: that does not prove guarding
    or ordering. Receiver matching excludes unrelated append/write calls.
    Mutation tests of known idioms establish only this vocabulary; extend
    it when the package introduces a different idiom."""
    offenders = []
    for relative, path in _package_modules():
        offenders.extend(_write_offenders(path.read_text(encoding="utf-8"), relative))
    assert not offenders, (
        "these mutate without reaching the journal; route them through "
        "`Run.execute` or add them to EXECUTOR_EXCEPTIONS / "
        "UNRECORDED_WRITES with the reason:\n" + "\n".join(offenders)
    )


READ_ONLY_OS_OPEN_SNIPPETS = (
    'os.open(".", os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)',
    "os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)",
    "os.open(name, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)",
    "os.open(name, flags=os.O_RDONLY)",
    'os.open(name, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))',
    "os.open(name, 0)",
)

WRITING_OS_OPEN_SNIPPETS = (
    "os.open(p, os.O_WRONLY | os.O_CREAT)",
    "os.open(p, flags)",
    "os.open(p, os.O_RDONLY | os.O_TRUNC)",
    "os.open(p, os.O_RDWR)",
    "os.open(p, os.O_WRONLY | os.O_APPEND)",
    "os.open(p)",
    'os.open(p, getattr(os, "O_CREAT", 0))',
    "os.open(p, os.O_RDONLY | mode)",
    "os.open(p, flags=os.O_RDONLY | os.O_CREAT)",
)


def _only_call(source):
    return ast.parse(source).body[0].value


@pytest.mark.parametrize("source", READ_ONLY_OS_OPEN_SNIPPETS)
def test_read_only_os_open_is_not_counted_as_a_write(source):
    """Pin the read-only side of the `os.open` branch.

    Before it existed, the generic mode check read the flags expression as a
    mode string, so every descriptor-relative read counted as a write."""
    assert _mutating_call(_only_call(source)) is None


@pytest.mark.parametrize("source", WRITING_OS_OPEN_SNIPPETS)
def test_creating_or_unknown_os_open_is_still_a_write(source):
    """Pin the write side: only the proven read-only allowlist is exempt.

    A creating or truncating flag, an unlisted flag, a computed expression
    and a missing flags argument each stay a write, so a genuine write added
    to a read-only module cannot slip past this scanner."""
    assert _mutating_call(_only_call(source)) == "os.open(...) for writing"


def test_ordinary_open_modes_are_unaffected_by_the_os_open_branch():
    """The new branch must not change plain `open` or `path.open` verdicts."""
    assert _mutating_call(_only_call('open(p, "w")')) == "open(...) for writing"
    assert _mutating_call(_only_call('open(p, "r")')) is None
    assert _mutating_call(_only_call('path.open("a")')) == "open(...) for writing"
    assert _mutating_call(_only_call("path.open()")) is None


def test_recall_acquisition_opens_are_all_reads_as_written():
    """Every `os.open` in the acquisition module is read-only as written."""
    source = (REPO_ROOT / "validated_memory" / "recall_io.py").read_text(
        encoding="utf-8"
    )
    opens = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "open"
    ]
    assert len(opens) >= 3
    assert [_mutating_call(node) for node in opens] == [None] * len(opens)


def test_no_module_outside_the_journal_reaches_past_the_executor():
    """Forbid private protocol spellings in source, including prose.

    The text denylist also prevents reintroducing prepare_op/append_op.
    Ordinary record/install names are matched as calls, not English words.
    Diagnostics use PERMITTED_JOURNAL_EXPORTS and PERMITTED_RUN_METHODS;
    aliases and unlisted spellings remain outside this structural pin."""
    offenders = []
    for relative, path in _package_modules():
        if _inside_journal(relative):
            continue
        source = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(source.splitlines(), start=1):
            for name in PRIVATE_JOURNAL_NAMES:
                if re.search(r"\b" + re.escape(name) + r"\b", line):
                    offenders.append(f"{relative}:{lineno}: names `{name}`")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            name = None
            if isinstance(called, ast.Name):
                name = called.id
            elif isinstance(called, ast.Attribute):
                name = called.attr
            if name in PRIVATE_JOURNAL_CALLS:
                offenders.append(f"{relative}:{node.lineno}: calls `{name}(...)`")
    assert not offenders, (
        "these reach past the adopting session into the journal's own protocol; "
        "the whole of the journal surface a module outside it may touch is "
        + ", ".join(f"`{name}`" for name in PERMITTED_JOURNAL_EXPORTS)
        + ", plus "
        + ", ".join(f"`session.{name}`" for name in PERMITTED_RUN_METHODS)
        + ":\n"
        + "\n".join(offenders)
    )


def _facade_offenders(source, relative):
    if _inside_journal(relative):
        return []
    offenders = []
    for node, bindings in _bound_nodes(ast.parse(source), relative):
        origins = set()
        if isinstance(node, ast.Import):
            origins.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _import_module(node, relative)
            origins.update(module + "." + a.name for a in node.names)
        elif isinstance(node, (ast.Attribute, ast.Call)):
            origins.update(_origins(node, bindings))
        for origin in sorted(origins):
            if origin.startswith(FACADE + "."):
                member = origin[len(FACADE) + 1:].split(".")[0]
                if member not in PERMITTED_JOURNAL_EXPORTS:
                    offenders.append(f"{relative}:{node.lineno}: non-exported journal member `{origin}`")
    return offenders


def test_nothing_outside_the_journal_reaches_a_name_it_does_not_export():
    """Check journal imports and literal journal.X access against the exports.

    Unlike the text denylist, this rejects _bootstrap and ordinary names
    such as append as symbols, without rejecting prose. Direct submodule
    imports bypass the facade and are refused. Static aliases and getattr
    are resolved; arbitrary reflection and dynamic imports are not."""
    offenders = []
    for relative, path in _package_modules():
        offenders.extend(_facade_offenders(path.read_text(encoding="utf-8"), relative))
    assert not offenders, (
        "the whole of the journal a module outside it may reach is "
        + ", ".join(f"`{name}`" for name in PERMITTED_JOURNAL_EXPORTS)
        + ", plus "
        + ", ".join(f"`session.{name}`" for name in PERMITTED_RUN_METHODS)
        + " on the session yielded by `adopting_run`; the journal is "
        "imported whole and reached by "
        "attribute, never by one of its own modules:\n"
        + "\n".join(offenders)
    )


def test_the_facade_exports_exactly_the_surface_the_pin_permits():
    """Pin sorted __all__ to PERMITTED_JOURNAL_EXPORTS without importing it."""
    source = (
        REPO_ROOT / "validated_memory" / JOURNAL_SOURCE / "__init__.py"
    ).read_text(encoding="utf-8")
    _assert_facade_exports(source)


def _assert_facade_exports(source):
    tree = ast.parse(source)
    declarations = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if len(targets) == 1 and isinstance(targets[0], ast.Name) and targets[0].id == "__all__":
                declarations.append((node, targets[0]))
    assert len(declarations) == 1, "require one module-level __all__ Assign or AnnAssign"
    declaration, target = declarations[0]
    assert all(node is target for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "__all__"), "additional __all__ references or mutations are forbidden"
    value = declaration.value
    assert isinstance(value, (ast.List, ast.Tuple)) and all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in value.elts), "__all__ must be a literal list/tuple of literal strings"
    exports = [item.value for item in value.elts]
    assert exports == sorted(exports), "`__all__` is not sorted"
    assert exports == list(PERMITTED_JOURNAL_EXPORTS), (
        "the facade and the surface this suite pins have drifted:\n"
        "  only in `__all__`: "
        f"{sorted(set(exports) - set(PERMITTED_JOURNAL_EXPORTS))}\n"
        "  only in PERMITTED_JOURNAL_EXPORTS: "
        f"{sorted(set(PERMITTED_JOURNAL_EXPORTS) - set(exports))}"
    )


@pytest.mark.parametrize("source", [
    "from os import replace as publish\npublish(a, b)",
    "import os as fs\npublish = fs.replace\npublish(a, b)",
    "import shutil as files\ncopy = files.copyfile\ncopy(a, b)",
    "from shutil import move as relocate\nrelocate(a, b)",
    "from .durable import install\ninstall(a, b)",
    "from .durable import install as publish\npublish(a, b)",
    "from .records import append\nappend(a, b)",
    "from .records import append as record\nrecord(a, b)",
    "from validated_memory.journal import records as history\nwrite = history.append\nwrite(a, b)",
    'import os as fs\ngetattr(fs, "replace")(a, b)',
    'import shutil as files\ngetattr(files, method)(a, b)',
    'from . import durable\ngetattr(durable, method)(a, b)',
    'from . import records\nwrite = getattr(records, method)\nwrite(a, b)',
    'getattr(path, "write_text")("data")',
    'write = getattr(path, "write_bytes")\nwrite(data)',
    'import os as fs\nwrite = getattr(fs, "replace")\nwrite(a, b)',
    'import os as fs\nwrite = getattr(fs, method)\nwrite(a, b)',
    'import os as fs\nfs.open(p, fs.O_RDONLY | fs.O_CREAT)',
    'from os import open as descriptor\ndescriptor(p, flags)',
    'read = path.open\nread("w")',
    'read = open\nread(p, mode)',
])
def test_journal_write_scan_rejects_bound_mutations(source):
    # A journal submodule gets no directory-wide raw-write exemption.
    offenders = _write_offenders(source, "journal/inspection.py")
    assert offenders and "journal/inspection.py:" in offenders[0]


@pytest.mark.parametrize("writer", sorted(DURABLE_MUTATORS))
def test_journal_write_scan_rejects_every_current_durable_writer(writer):
    source = f"from ..durable import {writer} as write\nwrite(a, b)"
    assert _write_offenders(source, "journal/nested/inspection.py")


@pytest.mark.parametrize("source", [
    "import os as fs\nfs.open(p, fs.O_RDONLY | fs.O_NOFOLLOW)",
    'import os as fs\nread = fs.open\nread(p, fs.O_RDONLY | getattr(fs, "O_CLOEXEC", 0))',
    'from os import open as descriptor, O_RDONLY as read_only\ndescriptor(p, flags=read_only)',
    'import os as fs\ngetattr(fs, "open")(p, fs.O_RDONLY)',
    'read = open\nread(p, "rb")',
    'read = path.open\nread("r")',
    'getattr(path, "open")("r")',
    'items.append(value)\ndatabase.append(value)',
    'from . import verdicts\nverdicts.append(value)',
    'text.replace("old", "new")',
    'getattr(receiver, "inspect")()\ngetattr(receiver, method)()',
    'from . import journal as history\nhistory.create_directory(path)',
])
def test_journal_write_scan_preserves_read_only_and_unrelated_calls(source):
    assert _write_offenders(source, "consumer.py") == []


def test_journal_write_scan_keeps_lexical_scopes_separate():
    source = '''
def first():
    from os import replace as action
    action(a, b)
def second():
    action(a, b)
'''
    offenders = _write_offenders(source, "consumer.py")
    assert len(offenders) == 1 and "first calls os.replace" in offenders[0]


def test_journal_write_scan_masks_enclosing_names_for_parameters_and_locals():
    source = '''
import os as fs
def parameter(fs):
    fs.replace("old", "new")
def local():
    fs = text
    fs.replace("old", "new")
'''
    assert _write_offenders(source, "consumer.py") == []


def test_journal_write_scan_conservatively_retains_sensitive_rebindings():
    source = '''
import os as fs
def change(flag):
    action = fs.replace
    if flag:
        action = unrelated
    action(a, b)
    action = other
'''
    offenders = _write_offenders(source, "consumer.py")
    assert len(offenders) == 1 and "os.replace" in offenders[0]


@pytest.mark.parametrize("source", [
    'from . import journal as history\nhistory._bootstrap()',
    'from . import journal\nhistory = journal\nhistory.records.append(a)',
    'import validated_memory.journal as history\ngetattr(history, "install")(a)',
    'from . import journal as history\ngetattr(history, name)',
    'import validated_memory.journal\nvalidated_memory.journal._bootstrap()',
    'import validated_memory.journal\ngetattr(validated_memory.journal, name)',
    'from validated_memory.journal import _bootstrap as bootstrap',
    'import validated_memory.journal.records as records',
    'from .journal.records import append',
])
def test_journal_facade_scan_rejects_private_bound_access(source):
    offenders = _facade_offenders(source, "consumer.py")
    assert offenders and "non-exported journal member" in offenders[0]


@pytest.mark.parametrize("source", [
    'from . import journal as history\nhistory.adopting_run(root)',
    'from . import journal\nhistory = journal\ngetattr(history, "create_file")(path)',
    'import validated_memory.journal\nvalidated_memory.journal.digest(data)',
    '"journal._bootstrap is ordinary prose"',
    'from .verdicts import append\nappend(value)',
    'getattr(unrelated, member)',
])
def test_journal_facade_scan_preserves_public_and_unrelated_access(source):
    assert _facade_offenders(source, "consumer.py") == []


def test_journal_facade_scan_keeps_scopes_and_shadowing_explicit():
    source = '''
from . import journal as history
def shadow(history):
    history.private()
def first():
    from . import journal as local_history
    local_history.private()
def second():
    local_history.private()
'''
    offenders = _facade_offenders(source, "consumer.py")
    assert len(offenders) == 1 and ":7:" in offenders[0]


@pytest.mark.parametrize("prefix, call", [
    ("import os as sensitive", "sensitive.replace(a, b)"),
    ("from .durable import install as sensitive", "sensitive(a, b)"),
    ("from . import records as sensitive", "sensitive.append(a, b)"),
])
@pytest.mark.parametrize("body", [
    "def f():\n    [sensitive for sensitive in ()]\n    {call}",
    "class C:\n    sensitive = text\n    def f(self):\n        {call}",
    "class C:\n    sensitive = sensitive\n    {call}",
    "def f(sensitive={call}):\n    pass",
])
def test_journal_write_scan_preserves_enclosing_sensitive_bindings(prefix, call, body):
    source = prefix + "\n" + body.format(call=call)
    assert _write_offenders(source, "journal/inspection.py")


@pytest.mark.parametrize("header", [
    "@fs.replace(a, b)\ndef f():",
    "def f(value=fs.replace(a, b)):",
    "def f(value: fs.replace(a, b)):",
    "def f() -> fs.replace(a, b):",
])
def test_journal_write_scan_keeps_function_headers_in_enclosing_scope(header):
    source = f"import os as fs\n{header}\n    session.execute(op)"
    offenders = _write_offenders(source, "consumer.py")
    assert len(offenders) == 1 and "<module> calls os.replace" in offenders[0]


def test_journal_write_scan_keeps_lambda_defaults_in_enclosing_scope():
    source = "import os as fs\nf = lambda value=fs.replace(a, b): session.execute(op)"
    offenders = _write_offenders(source, "consumer.py")
    assert len(offenders) == 1 and "<module> calls os.replace" in offenders[0]


def test_journal_write_scan_preserves_global_sensitive_origin_on_rebinding():
    source = '''
import os as fs
def f():
    global fs
    fs.replace(a, b)
    fs = unrelated
'''
    offenders = _write_offenders(source, "consumer.py")
    assert len(offenders) == 1 and "f calls os.replace" in offenders[0]


@pytest.mark.parametrize("body", [
    "def f():\n    [api for api in ()]\n    api._bootstrap()",
    "class C:\n    api = text\n    def f(self):\n        api._bootstrap()",
    "class C:\n    api = api\n    api._bootstrap()",
    "def f(api=api._bootstrap()):\n    pass",
])
def test_journal_facade_scan_preserves_enclosing_sensitive_bindings(body):
    assert _facade_offenders("from . import journal as api\n" + body, "consumer.py")


def test_journal_facade_scan_preserves_global_sensitive_origin_on_rebinding():
    source = '''
from . import journal as api
def f():
    global api
    api._bootstrap()
    api = unrelated
'''
    assert _facade_offenders(source, "consumer.py")


@pytest.mark.parametrize("expression", [
    "[fs.replace(a, b) for fs in items]",
    "{fs.replace(a, b) for fs in items}",
    "{fs: fs.replace(a, b) for fs in items}",
    "(fs.replace(a, b) for fs in items)",
])
def test_journal_write_scan_comprehension_targets_are_local(expression):
    assert _write_offenders("import os as fs\n" + expression, "consumer.py") == []


def test_journal_write_scan_comprehension_first_iterable_uses_enclosing_scope():
    assert _write_offenders("import os as fs\n[fs for fs in fs.replace(a, b)]", "consumer.py")


@pytest.mark.parametrize("fallback", ["fs.O_CREAT", "flags", "fs.O_WRONLY", "make_flags()"])
@pytest.mark.parametrize("assigned", [False, True])
def test_journal_write_scan_refuses_unknown_getattr_flag_fallback(fallback, assigned):
    expression = f'getattr(fs, "O_NOFOLLOW", {fallback})'
    source = "import os as fs\n"
    if assigned:
        source += f"flags = {expression}\nalias = flags\nfs.open(p, alias)"
    else:
        source += f"fs.open(p, {expression})"
    assert _write_offenders(source, "consumer.py")


def test_journal_write_scan_retains_safe_optional_flag_alias():
    source = 'import os as fs\nflags = getattr(fs, "O_NOFOLLOW", 0)\nalias = flags\nfs.open(p, alias)'
    assert _write_offenders(source, "consumer.py") == []


@pytest.mark.parametrize("declaration", [
    "__all__ = {exports}", "__all__: list[str] = {exports}",
    "__all__ = tuple_placeholder",
])
def test_journal_facade_export_literal_shapes(declaration):
    if "tuple_placeholder" in declaration:
        source = declaration.replace("tuple_placeholder", repr(PERMITTED_JOURNAL_EXPORTS))
    else:
        source = declaration.format(exports=repr(list(PERMITTED_JOURNAL_EXPORTS)))
    _assert_facade_exports(source)


@pytest.mark.parametrize("suffix", [
    '__all__ = []', '__all__ += ["private"]', '__all__[0] = "private"',
    'del __all__[0]', '__all__.append("private")', 'alias = __all__',
    'getattr(__all__, "append")("private")',
    'def change():\n    __all__.clear()',
])
def test_journal_facade_export_amendments_are_refused(suffix):
    source = f"__all__ = {list(PERMITTED_JOURNAL_EXPORTS)!r}\n{suffix}"
    with pytest.raises(AssertionError, match="__all__"):
        _assert_facade_exports(source)


@pytest.mark.parametrize("source", [
    'def declare():\n    __all__ = []', '__all__ = build_exports()',
    '__all__ = [name]', '__all__: list[str]', 'alias = __all__ = []',
    '__all__ = [] + []', '__all__ = [*names]',
])
def test_journal_facade_unsupported_export_construction_is_explicit(source):
    with pytest.raises(AssertionError, match="module-level|literal list/tuple"):
        _assert_facade_exports(source)


def test_journal_source_boundary_is_a_package_prefix():
    assert _inside_journal("journal/__init__.py")
    assert _inside_journal("journal/nested/inspection.py")
    assert not _inside_journal("journal.py")
    assert not _inside_journal("journalish/module.py")


def test_the_adopting_session_exposes_only_its_three_operations():
    """The opaque session cannot grow a second resolution or lock interface."""
    source = (
        REPO_ROOT / "validated_memory" / JOURNAL_SOURCE / "executor.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    run_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Run"
    )
    methods = sorted(
        node.name
        for node in run_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    )
    assert methods == sorted(PERMITTED_RUN_METHODS)


def test_adoption_and_resolution_keep_their_distinct_protocols():
    """Only adoption bootstraps; targeted resolution never surveys or recovers."""
    root = REPO_ROOT / "validated_memory" / JOURNAL_SOURCE
    executor = ast.parse((root / "executor.py").read_text(encoding="utf-8"))
    protocol = ast.parse((root / "protocol.py").read_text(encoding="utf-8"))
    executor_functions = {
        node.name: node for node in ast.walk(executor)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    protocol_functions = {
        node.name: node for node in ast.walk(protocol)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def calls(function):
        names = []
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            target = call.func
            if isinstance(target, ast.Name):
                names.append(target.id)
            elif isinstance(target, ast.Attribute):
                names.append(target.attr)
        return names

    adopting_calls = calls(executor_functions["adopting_session"])
    resolving_calls = calls(protocol_functions["resolve_one"])
    assert adopting_calls.count("identity_transition") == 1
    assert resolving_calls.count("has_transaction") == 1
    assert resolving_calls.count("read_transaction") == 1
    assert not {
        "identity_transition",
        "_survey",
        "open_transactions",
        "recover",
    }.intersection(resolving_calls)
    assert not {
        "_recover_one", "_complete", "_resolve_one", "_restore"
    }.intersection(executor_functions)


def test_c1b_bootstrap_adapter_has_no_legacy_publication_policy():
    """Protocol alone selects mechanics; executor consumes every closed result."""
    root = REPO_ROOT / "validated_memory" / JOURNAL_SOURCE
    executor_source = (root / "executor.py").read_text(encoding="utf-8")
    executor = ast.parse(executor_source)
    executor_functions = {
        node.name: node for node in ast.walk(executor)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_bootstrap" not in executor_functions
    assert "_republish_opening" not in executor_functions
    session = executor_functions["adopting_session"]
    session_names = {
        node.id for node in ast.walk(session) if isinstance(node, ast.Name)
    }
    assert {"IdentityConfirmed", "IdentityRefused", "IdentityRetained"} <= session_names
    assert not {
        "publish_opening", "reconfirm_opening", "NoReplaceUnavailable",
        "StagingCleanupUnconfirmed", "BootstrapPreparationFailed",
    }.intersection(session_names)

    protocol_source = (root / "protocol.py").read_text(encoding="utf-8")
    protocol = ast.parse(protocol_source)
    transition = next(
        node for node in ast.walk(protocol)
        if isinstance(node, ast.FunctionDef) and node.name == "_identity_transition"
    )
    transition_calls = {
        node.func.id for node in ast.walk(transition)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"publish_opening", "reconfirm_opening"} <= transition_calls
    for module in root.glob("*.py"):
        if module.name == "protocol.py":
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"))
        called = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not {"publish_opening", "reconfirm_opening"}.intersection(called)

    records = (root / "records.py").read_text(encoding="utf-8")
    assert "def publish_opening(" in records
    assert "publish_no_replace(path, data, 0o644, verify)" in records
    durable = (root / "durable.py").read_text(encoding="utf-8")
    publication = durable.split("def publish_no_replace(", 1)[1].split("\ndef ", 1)[0]
    assert "NoReplacePublication" not in durable
    assert "os.replace(" not in publication
    assert "create_exclusive(" not in publication
    assert "os.link(" in publication
    assert publication.index("try:") < publication.index("_temporary_path(")
    assert publication.index(
        'preparation_phase = "canonical publication"'
    ) < publication.index("os.link(staging, path")
    publish_node = next(
        node for node in ast.parse(durable).body
        if isinstance(node, ast.FunctionDef) and node.name == "publish_no_replace"
    )
    assert not any(isinstance(node, ast.Return) for node in ast.walk(publish_node))


def test_c1d_recovery_and_resolution_have_one_policy_owner():
    """Protocol owns WAL decisions; executor exposes only bounded adapters."""
    root = REPO_ROOT / "validated_memory" / JOURNAL_SOURCE
    sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in root.glob("*.py")
    }
    trees = {name: ast.parse(source) for name, source in sources.items()}
    executor_functions = {
        node.name: node
        for node in ast.walk(trees["executor.py"])
        if isinstance(node, ast.FunctionDef)
    }
    assert not {
        "_recover_one", "_complete", "_resolve_one", "_restore", "_recover_history"
    }.intersection(executor_functions)
    recover = executor_functions["recover"]
    recover_calls = {
        node.func.attr
        for node in ast.walk(recover)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert recover_calls == {"_recover_all", "_survey"}

    protocol_functions = {
        node.name: node
        for node in ast.walk(trees["protocol.py"])
        if isinstance(node, ast.FunctionDef)
    }
    assert {"_recover_all", "_recover_item", "_recover_claimed", "resolve_one"} <= (
        set(protocol_functions)
    )
    transition = protocol_functions["_append_reconfirmation"]
    protocol_calls = {
        node.func.id
        for node in ast.walk(transition)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"classify_evidence", "confirm_histories", "reconfirm_append"} <= (
        protocol_calls
    )
    assert "include_stored_claim=True" in ast.get_source_segment(
        sources["protocol.py"], transition
    )
    successor = ast.get_source_segment(
        sources["protocol.py"], protocol_functions["_append_successor"]
    )
    assert 'record.get("transaction") == evidence.transaction' in successor
    execute_source = ast.get_source_segment(
        sources["executor.py"], executor_functions["_execute"]
    )
    assert execute_source.index("mark_history_append(") < execute_source.index(
        "append(history_records,"
    )
# The journal's modules in the order `journal/__init__.py` lists them, which
# is also the order they may import in. The facade itself is not here: it is
# the one file that reaches every module, which is what makes it the door.
JOURNAL_LAYERS = (
    "durable",
    "fault",
    "records",
    "topology",
    "paths",
    "operations",
    "lock",
    "transactions",
    "executor",
    "reconcile",
    "protocol",
    "command",
)


def test_the_journal_package_imports_only_downhill():
    """Pin module membership and static imports to JOURNAL_LAYERS.

    Relative and absolute imports are scanned even inside functions;
    dynamic imports and indirect attribute access remain invisible.
    Importing the facade by its absolute import name is also refused."""
    root = REPO_ROOT / "validated_memory" / JOURNAL_SOURCE
    present = {
        path.stem for path in sorted(root.glob("*.py")) if path.stem != "__init__"
    }
    assert present == set(JOURNAL_LAYERS), (
        "the package and the order it is pinned in have drifted:\n"
        f"  no place in the order: {sorted(present - set(JOURNAL_LAYERS))}\n"
        f"  in the order, not in the package: "
        f"{sorted(set(JOURNAL_LAYERS) - present)}"
    )
    package = f"validated_memory.{JOURNAL_SOURCE}."
    facade = package.rstrip(".")
    offenders = []
    for rank, name in enumerate(JOURNAL_LAYERS):
        tree = ast.parse((root / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            reached = []
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == facade:
                        offenders.append(
                            f"journal/{name}.py:{node.lineno}: imports the "
                            "package itself, which imports every module"
                        )
                    elif alias.name.startswith(package):
                        reached.append(alias.name.removeprefix(package))
            elif isinstance(node, ast.ImportFrom) and node.level == 1:
                reached = (
                    [alias.name for alias in node.names]
                    if node.module is None
                    else [node.module]
                )
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                module = node.module or ""
                if module.startswith(package):
                    reached = [module.removeprefix(package)]
                elif module == facade:
                    reached = [alias.name for alias in node.names]
            for module in reached:
                if module in JOURNAL_LAYERS and JOURNAL_LAYERS.index(module) >= rank:
                    offenders.append(
                        f"journal/{name}.py:{node.lineno}: imports "
                        f"`{module}`, which comes after it"
                    )
    assert not offenders, (
        "these import uphill or sideways, and the facade says the package "
        "does not; the order is "
        + " -> ".join(JOURNAL_LAYERS)
        + ":\n"
        + "\n".join(offenders)
    )


def test_every_named_exception_exists_and_says_why_it_is_one():
    """Each exception must name an existing scope or module and carry a reason."""
    stale = []
    unexplained = []
    for label, entries in (
        ("EXECUTOR_EXCEPTIONS", EXECUTOR_EXCEPTIONS),
        ("UNRECORDED_WRITES", UNRECORDED_WRITES),
    ):
        for (module, function), reason in entries.items():
            path = REPO_ROOT / "validated_memory" / module
            if not path.is_file():
                stale.append(f"{label}: {module} is not a module of the package")
                continue
            if function != "*":
                defined = {
                    node.name
                    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                if function not in defined:
                    stale.append(f"{label}: {module} defines no `{function}`")
            if not reason.strip():
                unexplained.append(f"{label}: ({module}, {function})")
    assert not stale, "\n".join(stale)
    assert not unexplained, (
        "an exception with no reason is a decision nobody has to defend:\n"
        + "\n".join(unexplained)
    )


# --- the root a record names, and the root the filesystem agrees with ---------


def test_an_observation_that_escapes_the_root_through_a_symlink_is_refused(
    run_cli, tmp_path
):
    """Refuse an observation that resolves outside the adopter root.

    The assertions also pin no outside write and no false `memory` record."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    outside = tmp_path / "outside" / "other"
    outside.mkdir(parents=True)
    (adopter / "memory").symlink_to(
        Path("..") / "outside" / "other", target_is_directory=True
    )

    result = run_cli("init", cwd=adopter)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "memory" in result.stderr, result.stderr
    assert "adopter root" in result.stderr, result.stderr
    # Nothing outside the root was created, and nothing claims it was.
    assert not (outside / "MEMORY.md").exists(), sorted(
        p.name for p in outside.iterdir()
    )
    records = _records(adopter / "journal.jsonl")
    assert not [e for e in records if e["path"] == "memory"], records


def test_one_record_the_reader_may_not_follow_does_not_hide_the_others(
    run_cli, tmp_path
):
    """One unsafe record is unknown without hiding other unfinished records.

    Plain and checking modes must also report the same total record count."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    (adopter / "knowledge").symlink_to(tmp_path / "nonexistent" / "elsewhere")

    # `init` refuses the link and records nothing, so both open records are
    # written here. The first names a path that resolves out of the root --
    # the shape this test is about -- and the second is an ordinary
    # unfinished transaction that must survive the first one's refusal.
    assert run_cli("init", cwd=adopter).returncode == 1
    journal = adopter / "journal.jsonl"
    for run, path in (("1111111111111111", "knowledge"),
                      ("2222222222222222", "never-written.md")):
        _append_record(
            journal,
            _record(
                journal,
                run=run,
                op="create",
                path=path,
                preimage=None,
                postimage="sha256:" + "3" * 64,
            ),
        )

    result = run_cli("journal", "--check", cwd=adopter)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    reported = {
        line.split(": journal: ")[0].removeprefix("ERROR: "): line
        for line in result.stderr.splitlines()
    }
    assert "the path is unknown" in reported["knowledge"], reported
    assert "the path is unapplied" in reported["never-written.md"], reported
    # The two modes agree about how many records the file holds.
    plain = run_cli("journal", cwd=adopter)
    assert plain.stdout.split()[1] == str(len(_records(journal))), plain.stdout
    assert f"{len(_records(journal))} record(s)" in result.stdout, result.stdout


@pytest.mark.parametrize("arguments", [(), ("--check",)])
def test_a_refused_journal_still_reports_the_records_it_did_read(
    run_cli, tmp_path, arguments
):
    """A corrupt local log does not erase the readable repository record count."""
    harness_memory = tmp_path / "harness" / "memory"
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert (
        run_cli(
            "init", "--harness-memory", str(harness_memory), cwd=adopter
        ).returncode
        == 0
    )
    vault = adopter / ".validated-memory" / "local.jsonl"
    vault.write_text(
        vault.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8"
    )

    result = run_cli("journal", *arguments, cwd=adopter)

    assert result.returncode == 1, result.stdout
    read_back = len(_records(adopter / "journal.jsonl"))
    assert f"journal: {read_back} record(s), 1 error(s)" in result.stdout, (
        result.stdout,
        read_back,
    )


# --- one adoption, one id, whatever a checkout leaves behind ------------------


def test_the_adoption_id_survives_a_journal_a_checkout_took_away(
    run_cli, tmp_path
):
    """A surviving local log supplies the adoption ID after journal loss."""
    harness_memory = tmp_path / "harness" / "memory"
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert (
        run_cli(
            "init", "--harness-memory", str(harness_memory), cwd=adopter
        ).returncode
        == 0
    )
    journal = adopter / "journal.jsonl"
    minted = _records(journal)[0]["adoption"]
    journal.unlink()

    assert (
        run_cli(
            "init", "--harness-memory", str(harness_memory), cwd=adopter
        ).returncode
        == 0
    )

    adopted = {entry["adoption"] for entry in _records(journal)}
    assert adopted == {minted}, (adopted, minted)


def test_bootstrap_barrier_failure_gates_before_scaffold_and_keeps_identity(
    run_cli, tmp_path, monkeypatch
):
    """A visible opening record is never described as absent or rolled back."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "bootstrap-publish:journal.jsonl"
    )

    failed = run_cli("init", cwd=tmp_path)

    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    assert "Traceback" not in failed.stderr, failed.stderr
    journal = tmp_path / "journal.jsonl"
    opening = _records(journal)
    assert len(opening) == 1, opening
    assert opening[0]["note"] == "journal opened", opening
    staging = list(tmp_path.glob(".journal.jsonl.*.bootstrap"))
    if os.name == "nt":
        assert staging == []
        residue = "; no private staging residue is known"
    else:
        assert len(staging) == 1
        residue = f"; private staging residue {staging[0].name} remains"
    assert failed.stderr == (
        "ERROR: journal.jsonl: journal: the canonical opening is visible or "
        "may be visible, but publication or coherent readback is unconfirmed; "
        f"preserve the canonical name{residue}\n"
    )
    assert not (tmp_path / "knowledge").exists()
    assert not (tmp_path / ".gitignore").exists()
    assert not list((tmp_path / ".validated-memory").glob("transactions/*.json"))

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    retried = run_cli("init", cwd=tmp_path)

    assert retried.returncode == 0, (retried.stdout, retried.stderr)
    assert {record["adoption"] for record in _records(journal)} == {
        opening[0]["adoption"]
    }


def test_bootstrap_competitor_wins_without_replacement(run_cli, tmp_path, monkeypatch):
    """The canonical competitor is preserved byte-for-byte before scaffold."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        "bootstrap-competitor:journal.jsonl",
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert result.stderr == (
        "ERROR: journal.jsonl: journal: another artifact reached the "
        "canonical name before bootstrap; it was preserved and not replaced\n"
    )
    assert (tmp_path / "journal.jsonl").read_bytes() == b"competitor\n"
    assert not (tmp_path / "knowledge").exists()
    assert not list(tmp_path.glob(".journal.jsonl.*.bootstrap"))


@pytest.mark.parametrize(
    "point",
    (
        "no-replace-unavailable",
        "no-replace-not-implemented",
        "no-replace-enosys",
        "no-replace-eopnotsupp",
        "no-replace-eperm",
    ),
)
def test_bootstrap_refuses_when_no_replace_is_unavailable(
    run_cli, tmp_path, monkeypatch, point
):
    """A missing publication primitive leaves no canonical opening."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        f"{point}:journal.jsonl",
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert result.stderr == (
        "ERROR: journal.jsonl: journal: journal bootstrap requires no-replace "
        "publication, which is unavailable on this filesystem. Nothing has "
        "been published at the canonical name\n"
    )
    assert not (tmp_path / "journal.jsonl").exists()
    assert not (tmp_path / "knowledge").exists()
    assert not list(tmp_path.glob(".journal.jsonl.*.bootstrap"))


@pytest.mark.parametrize(
    ("point", "phase"),
    (
        ("bootstrap-staging-create", "staging creation"),
        ("bootstrap-write", "staging write"),
        ("bootstrap-mode", "staging mode"),
        ("bootstrap-file-fsync", "staging file flush"),
        ("bootstrap-canonical-publication", "canonical publication"),
    ),
)
def test_bootstrap_staging_failure_is_a_clean_prepublication_refusal(
    run_cli, tmp_path, monkeypatch, point, phase
):
    """Confirmed private cleanup permits an exact unchanged-canonical claim."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", f"{point}:journal.jsonl"
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert result.stderr == (
        "ERROR: journal.jsonl: journal: bootstrap stopped before canonical "
        f"publication because {phase} failed: [Errno 5] injected persistence "
        "failure: 'journal.jsonl'. The canonical name was not published by "
        "this operation. Remove the environmental obstruction and rerun init\n"
    )
    assert not (tmp_path / "journal.jsonl").exists()
    assert not list(tmp_path.glob(".journal.jsonl.*.bootstrap"))
    assert not (tmp_path / "knowledge").exists()
    assert [item[0] for item in _final_tree_snapshot(tmp_path)] == [
        ".validated-memory"
    ]


def test_bootstrap_identity_failure_retains_the_named_private_residue(
    run_cli, tmp_path, monkeypatch
):
    """A post-create identity failure cannot claim confirmed private cleanup."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        "bootstrap-staging-identity:journal.jsonl",
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    staging = list(tmp_path.glob(".journal.jsonl.*.bootstrap"))
    assert len(staging) == 1
    residue = staging[0].name
    assert result.stderr == (
        "ERROR: journal.jsonl: journal: bootstrap stopped before canonical "
        "publication; the canonical name was not published by this operation, "
        f"but cleanup of private staging residue {residue} is unconfirmed\n"
    )
    assert staging[0].read_bytes() == b""
    assert not (tmp_path / "journal.jsonl").exists()
    assert not (tmp_path / "knowledge").exists()
    assert [item[0] for item in _final_tree_snapshot(tmp_path)] == [
        residue,
        ".validated-memory",
    ]


def test_bootstrap_mode_is_set_before_the_staging_file_barrier():
    """The descriptor barrier covers both complete bytes and required mode."""
    source = (
        REPO_ROOT / "validated_memory" / "journal" / "durable.py"
    ).read_text(encoding="utf-8")
    publication = source.split("def publish_no_replace(", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert publication.index("handle.write(data)") < publication.index(
        "_chmod_staging(descriptor, staging, mode)"
    )
    assert publication.index(
        "_chmod_staging(descriptor, staging, mode)"
    ) < publication.index("os.fsync(handle.fileno())")
    assert publication.index("os.fsync(handle.fileno())") < publication.index(
        "os.link(staging, path"
    )


@pytest.mark.parametrize(
    "shape", ("zero", "blank", "partial", "malformed", "symlink", "directory")
)
def test_foreign_bootstrap_shapes_are_preserved_and_refused(
    run_cli, tmp_path, shape
):
    """No invalid or foreign canonical bootstrap node becomes an opening."""
    journal = tmp_path / "journal.jsonl"
    if shape == "zero":
        journal.write_bytes(b"")
    elif shape == "blank":
        journal.write_bytes(b" \n\n")
    elif shape == "partial":
        journal.write_bytes(b'{"schema":1')
    elif shape == "malformed":
        journal.write_bytes(b"{not json}\n")
    elif shape == "symlink":
        target = tmp_path / "foreign.jsonl"
        target.write_bytes(b"")
        journal.symlink_to(target.name)
    else:
        journal.mkdir()
    local = tmp_path / ".validated-memory" / "local.jsonl"
    local.parent.mkdir()
    local.write_text(
        json.dumps({
            "schema": 1,
            "at": "2026-09-25T00:00:00Z",
            "version": "2.4.0",
            "adoption": "edededededededed",
            "run": "efefefefefefefef",
            "durability": "local",
            "op": "observe",
            "purpose": "init",
            "path": "/external/harness-memory",
            "stage": "committed",
            "note": "local witness",
        }, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    local.chmod(0o640)
    before = _final_tree_snapshot(tmp_path)

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    expected = {
        "zero": (
            "ERROR: journal.jsonl: journal: the existing bootstrap name is "
            "not one complete validated opening; it was preserved\n"
        ),
        "blank": (
            "ERROR: journal.jsonl: journal: the existing bootstrap name is "
            "not one complete validated opening; it was preserved\n"
        ),
        "partial": (
            "ERROR: journal.jsonl: journal: non-empty history does not end "
            "with a line feed; the final record is not appendable without "
            "explicit targeted repair\n"
        ),
        "malformed": (
            "ERROR: journal.jsonl:1: journal: line is not valid JSON: "
            "Expecting property name enclosed in double quotes\n"
        ),
        "symlink": (
            "ERROR: journal.jsonl: journal: journal.jsonl is a symlink and "
            "holds no records; it was preserved. Restore the correct canonical "
            "artifact under operator control, then rerun journal --check and init\n"
        ),
        "directory": (
            "ERROR: journal.jsonl: journal: journal is not a regular file; a "
            "directory, a device or a pipe holds no records and nothing here "
            "can read one from it\n"
        ),
    }
    assert result.stderr == expected[shape]
    assert not (tmp_path / "knowledge").exists()
    after = _final_tree_snapshot(tmp_path)
    assert after == before


@pytest.mark.parametrize(
    ("point", "canonical"),
    (("bootstrap-staged", False), ("bootstrap-visible", True)),
)
def test_hard_death_never_exposes_a_partial_bootstrap(
    tmp_path, point, canonical
):
    """Staging death is private; post-publication death leaves complete bytes."""
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "VALIDATED_MEMORY_STORAGE_CRASH": f"{point}:*",
    }

    result = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
        check=False,
    )

    assert result.returncode == 71, (result.stdout, result.stderr)
    journal = tmp_path / "journal.jsonl"
    assert journal.exists() is canonical
    if canonical:
        opening = _records(journal)
        assert len(opening) == 1 and opening[0]["note"] == "journal opened"
    staging = list(tmp_path.glob(".journal.jsonl.*.bootstrap"))
    expected_staging = 0 if canonical and os.name == "nt" else 1
    assert len(staging) == expected_staging
    if staging:
        assert staging[0].read_bytes()
    assert not (tmp_path / "knowledge").exists()


@pytest.mark.parametrize(
    "published",
    (
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                os.name == "nt",
                reason="Windows rename consumes the private staging name",
            ),
        ),
    ),
)
def test_bootstrap_cleanup_uncertainty_gates_later_effects(
    run_cli, tmp_path, monkeypatch, published
):
    """Unconfirmed private cleanup is retained on either side of publication."""
    fault = "bootstrap-cleanup:*"
    if not published:
        fault += ",bootstrap-competitor:journal.jsonl"
    monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", fault)

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    staging = list(tmp_path.glob(".journal.jsonl.*.bootstrap"))
    assert len(staging) == 1
    residue = staging[0].name
    assert not (tmp_path / "knowledge").exists()
    if published:
        canonical = tmp_path / "journal.jsonl"
        assert len(_records(canonical)) == 1
        assert canonical.stat().st_ino == staging[0].stat().st_ino
        assert stat.S_IMODE(canonical.stat().st_mode) == 0o644
        assert result.stderr == (
            "ERROR: journal.jsonl: journal: the canonical opening is visible "
            "and coherently confirmed, but cleanup of private staging residue "
            f"{residue} is unconfirmed; preserve the canonical name and the "
            "named private residue\n"
        )
    else:
        canonical = tmp_path / "journal.jsonl"
        assert canonical.read_bytes() == b"competitor\n"
        assert stat.S_IMODE(canonical.stat().st_mode) == 0o640
        assert canonical.stat().st_ino != staging[0].stat().st_ino
        assert len(_records(staging[0])) == 1
        assert result.stderr == (
            "ERROR: journal.jsonl: journal: bootstrap stopped before canonical "
            "publication; the canonical name was not published by this "
            "operation, but cleanup of private staging residue "
            f"{residue} is unconfirmed\n"
        )


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows rename")
def test_windows_bootstrap_rename_consumes_staging_before_cleanup(
    run_cli, tmp_path, monkeypatch
):
    """Native Windows success leaves complete canonical bytes and no residue."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "bootstrap-cleanup:*"
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ""
    assert len(_records(tmp_path / "journal.jsonl")) > 1
    assert not list(tmp_path.glob(".journal.jsonl.*.bootstrap"))
    assert (tmp_path / "knowledge").is_dir()


def test_windows_bootstrap_branch_is_one_consuming_no_replace_rename():
    """Pin the Windows primitive while native black-box tests own its effects."""
    source = (
        REPO_ROOT / "validated_memory" / "journal" / "durable.py"
    ).read_text(encoding="utf-8")
    publication = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "publish_no_replace"
    )
    platform_branch = next(
        node
        for node in ast.walk(publication)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "os.name == 'nt'"
    )
    windows_calls = [
        ast.unparse(node)
        for statement in platform_branch.body
        for node in ast.walk(statement)
        if isinstance(node, ast.Call)
    ]
    posix_calls = [
        ast.unparse(node)
        for statement in platform_branch.orelse
        for node in ast.walk(statement)
        if isinstance(node, ast.Call)
    ]
    assert windows_calls == ["os.rename(staging, path)"]
    assert "os.link(staging, path, follow_symlinks=False)" in posix_calls
    text = ast.unparse(publication)
    assert text.index("_bootstrap_competitor_for_test(path)") < text.index(
        "os.rename(staging, path)"
    )
    assert text.index("os.rename(staging, path)") < text.index("published = True")
    assert text.index("published = True") < text.index(
        "storage_crash('bootstrap-visible', path)"
    )


@pytest.mark.parametrize(
    "failure",
    (
        "opening-file-fsync:journal.jsonl",
        "opening-directory:journal.jsonl",
        "opening-after-barriers:journal.jsonl",
    ),
)
def test_established_opening_is_reconfirmed_before_scaffold(
    run_cli, tmp_path, monkeypatch, failure
):
    """A fresh process re-dirties the full lone opening before relying on it."""
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "VALIDATED_MEMORY_STORAGE_CRASH": "bootstrap-visible:*",
    }
    crashed = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
        check=False,
    )
    assert crashed.returncode == 71
    journal = tmp_path / "journal.jsonl"
    before = journal.read_bytes()
    identity = journal.stat().st_ino
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        failure,
    )

    failed = run_cli("init", cwd=tmp_path)

    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    assert "complete canonical opening was re-dirtied" in failed.stderr
    assert journal.read_bytes() == before
    assert journal.stat().st_ino == identity
    assert not (tmp_path / "knowledge").exists()

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    completed = run_cli("init", cwd=tmp_path)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert journal.stat().st_ino == identity
    assert journal.read_bytes().startswith(before)


def test_short_opening_rewrite_failure_is_retained_and_terminal(
    run_cli, tmp_path, monkeypatch
):
    """The first successful byte write makes every later failure retained."""
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "VALIDATED_MEMORY_STORAGE_CRASH": "bootstrap-visible:*",
    }
    crashed = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
        check=False,
    )
    assert crashed.returncode == 71
    journal = tmp_path / "journal.jsonl"
    (tmp_path / ".validated-memory" / "lock").unlink()
    before = _final_tree_snapshot(tmp_path)
    identity = journal.stat().st_ino
    mode = stat.S_IMODE(journal.stat().st_mode)
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        "opening-short-write:journal.jsonl",
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    expected = (
        "ERROR: journal.jsonl: journal: the complete canonical opening was "
        "re-dirtied or its directory was reconfirmed, but coherent successor "
        "confirmation is incomplete: opening-reconfirmation of "
        "journal.jsonl is visible, but its durability is unconfirmed: "
        "[Errno 5] injected failure after a short opening write. Preserve the "
        "canonical opening; no private staging residue is known\n"
    )
    assert result.stderr == expected
    assert _final_tree_snapshot(tmp_path) == before
    assert journal.stat().st_ino == identity
    assert stat.S_IMODE(journal.stat().st_mode) == mode
    assert not (tmp_path / "knowledge").exists()


def _rendezvous_init(adopter, point):
    ready_read, ready_write = os.pipe()
    continue_read, continue_write = os.pipe()
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "VALIDATED_MEMORY_TEST_RENDEZVOUS": point,
        "VALIDATED_MEMORY_TEST_READY_FD": str(ready_write),
        "VALIDATED_MEMORY_TEST_CONTINUE_FD": str(continue_read),
    }
    process = subprocess.Popen(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        cwd=adopter,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        pass_fds=(ready_write, continue_read),
    )
    os.close(ready_write)
    os.close(continue_read)
    return process, ready_read, continue_write


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
def test_opening_reconfirmation_rejects_identical_opposite_replacement(tmp_path):
    """Equal opposite bytes cannot conceal a changed descriptor identity."""
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "VALIDATED_MEMORY_STORAGE_CRASH": "bootstrap-visible:*",
    }
    crashed = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert crashed.returncode == 71
    (tmp_path / ".validated-memory" / "lock").unlink()
    repository = tmp_path / "journal.jsonl"
    opening = _records(repository)[0]
    local = tmp_path / ".validated-memory" / "local.jsonl"
    local_entry = {
        **opening,
        "durability": "local",
        "path": "/external/harness-memory",
        "note": "local witness",
    }
    local.write_text(json.dumps(local_entry, sort_keys=True) + "\n", encoding="utf-8")
    local.chmod(0o640)
    repo_before = (repository.read_bytes(), repository.stat().st_ino)
    local_before = (local.read_bytes(), local.stat().st_ino, stat.S_IMODE(local.stat().st_mode))
    process, ready, proceed = _rendezvous_init(
        tmp_path, "after-opening-reconfirmation"
    )
    try:
        assert _await_history_attempt(ready) == 1
        replacement = local.with_name("local.replacement")
        replacement.write_bytes(local_before[0])
        replacement.chmod(local_before[2])
        os.replace(replacement, local)
        os.write(proceed, b"x")
        stdout, stderr = process.communicate(timeout=30)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode == 1, (stdout, stderr)
    assert stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert "coherent successor confirmation is incomplete" in stderr
    assert "complete opening reconfirmation did not produce" in stderr
    assert (repository.read_bytes(), repository.stat().st_ino) == repo_before
    assert local.read_bytes() == local_before[0]
    assert local.stat().st_ino != local_before[1]
    assert stat.S_IMODE(local.stat().st_mode) == local_before[2]
    assert not (tmp_path / "knowledge").exists()


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
def test_identity_transition_reacquires_after_executor_stale_reads(tmp_path):
    """Survey and adoption identity come from the callback's confirmed pair."""
    process, ready, proceed = _rendezvous_init(
        tmp_path, "before-identity-transition"
    )
    adoption = "abababababababab"
    opening = {
        "schema": 1,
        "at": "2026-09-25T00:00:00Z",
        "version": "2.4.0",
        "adoption": adoption,
        "run": "cdcdcdcdcdcdcdcd",
        "durability": "repo",
        "op": "observe",
        "purpose": "init",
        "path": "journal.jsonl",
        "stage": "committed",
        "note": "journal opened",
    }
    try:
        assert _await_history_attempt(ready) == 1
        journal = tmp_path / "journal.jsonl"
        journal.write_text(json.dumps(opening, sort_keys=True) + "\n", encoding="utf-8")
        journal.chmod(0o644)
        os.write(proceed, b"x")
        stdout, stderr = process.communicate(timeout=30)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode == 0, (stdout, stderr)
    assert stderr == ""
    assert {entry["adoption"] for entry in _records(tmp_path / "journal.jsonl")} == {
        adoption
    }
    assert (tmp_path / "knowledge").is_dir()


def _orphan_transaction(tree, transaction_id, adoption="aaaaaaaaaaaaaaaa"):
    """Write a WAL fixture without either authoritative history."""
    directory = tree / ".validated-memory" / "transactions"
    directory.mkdir(parents=True, exist_ok=True)
    entry = {
        "schema": 1,
        "at": "2026-09-01T00:00:00Z",
        "version": "1.6.0",
        "adoption": adoption,
        "run": "7777777777777777",
        "transaction": transaction_id,
        "intention": {
            "op": "create",
            "purpose": "init",
            "path": "knowledge",
            "durability": "repo",
            "directory": True,
        },
        "preimage": {"kind": "absent"},
        "postimage": {"kind": "directory"},
        "preimage_blob": None,
        "mode": None,
        "prior_bytes": None,
        "stage": "prepared",
    }
    path = directory / f"{transaction_id}.json"
    path.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "shape", ("one", "empty-history-files", "conflicting", "damaged")
)
def test_wal_without_history_refuses_init_before_any_write(
    run_cli, tmp_path, shape
):
    """WAL residue is restoration evidence, never authority to adopt."""
    first = _orphan_transaction(tmp_path, "1111111111111111")
    if shape == "empty-history-files":
        (tmp_path / "journal.jsonl").write_bytes(b"")
        (tmp_path / ".validated-memory" / "local.jsonl").write_bytes(b"")
    elif shape == "conflicting":
        _orphan_transaction(
            tmp_path, "2222222222222222", adoption="bbbbbbbbbbbbbbbb"
        )
    elif shape == "damaged":
        first.write_text("{not json\n", encoding="utf-8")
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert "neither permanent history" in result.stderr, result.stderr
    assert "restore" in result.stderr, result.stderr
    if shape == "conflicting":
        assert "aaaaaaaaaaaaaaaa, bbbbbbbbbbbbbbbb" in result.stderr
    elif shape == "damaged":
        assert "damaged artifact(s): 1111111111111111" in result.stderr
    else:
        assert "artifact adoption id(s): aaaaaaaaaaaaaaaa" in result.stderr
    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
    if shape == "empty-history-files":
        assert (tmp_path / "journal.jsonl").read_bytes() == b""
    else:
        assert not (tmp_path / "journal.jsonl").exists()


def test_journal_check_uses_the_same_orphan_wal_truth(run_cli, tmp_path):
    """Reporting cannot call a historyless WAL recoverable under a new ID."""
    artifact = _orphan_transaction(tmp_path, "3333333333333333")
    before = artifact.read_bytes()

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "neither permanent history" in result.stderr, result.stderr
    assert artifact.read_bytes() == before
    assert not (tmp_path / "journal.jsonl").exists()


def test_transaction_directory_barrier_failure_precedes_target_and_history(
    run_cli, tmp_path, monkeypatch
):
    """An unconfirmed WAL directory cannot be trusted with a child artifact."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    target = tmp_path / "knowledge-extension.md"
    target.unlink()
    transactions = tmp_path / ".validated-memory" / "transactions"
    transactions.rmdir()
    journal = tmp_path / "journal.jsonl"
    before = journal.read_bytes()
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "confirm:transactions"
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert "durability is unconfirmed" in result.stderr, result.stderr
    assert not target.exists()
    assert journal.read_bytes() == before
    assert not list(transactions.glob("*.json"))


def test_preimage_directory_barrier_failure_precedes_wal_and_publication(
    run_cli, tmp_path, monkeypatch
):
    """The only old bytes are protected before any transaction can publish."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    ignore = tmp_path / ".gitignore"
    ignore.write_text("kept by adopter\n", encoding="utf-8")
    original = ignore.read_bytes()
    preimages = tmp_path / ".validated-memory" / "preimages"
    if preimages.exists():
        for child in preimages.iterdir():
            child.unlink()
        preimages.rmdir()
    journal = tmp_path / "journal.jsonl"
    before = journal.read_bytes()
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "confirm:preimages"
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert "durability is unconfirmed" in result.stderr, result.stderr
    assert ignore.read_bytes() == original
    assert journal.read_bytes() == before
    assert not list(
        (tmp_path / ".validated-memory" / "transactions").glob("*.json")
    )


@pytest.mark.parametrize("point", ("open", "fsync"))
def test_unexpected_bootstrap_directory_errors_gate_without_claiming_absence(
    run_cli, tmp_path, monkeypatch, point
):
    """EACCES/EIO-like barrier failures are not portable-unsupported results."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", f"{point}:."
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert "publication or coherent readback is unconfirmed" in result.stderr
    opening = _records(tmp_path / "journal.jsonl")
    assert len(opening) == 1 and opening[0]["note"] == "journal opened"
    assert not (tmp_path / "knowledge").exists()


def test_declared_unsupported_directory_barrier_keeps_portable_atomicity(
    run_cli, tmp_path, monkeypatch
):
    """A known missing capability differs from an unexpected I/O failure."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "unsupported:."
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert (tmp_path / "journal.jsonl").is_file()
    assert (tmp_path / "knowledge").is_dir()


def test_windows_skips_unsupported_directory_open_before_attempting_it():
    """Windows capability absence is narrow and precedes directory open."""
    source = (
        REPO_ROOT / "validated_memory" / "journal" / "durable.py"
    ).read_text(encoding="utf-8")
    confirm = source.split("def _confirm_directory(", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert 'os.name == "nt"' in confirm
    assert confirm.index('os.name == "nt"') < confirm.index("os.open(")


def test_persistence_result_and_visibility_error_stay_private_and_disjoint():
    """The private seam cannot regress post-visibility failure into OSError."""
    source = (
        REPO_ROOT / "validated_memory" / "journal" / "durable.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    classes = {
        node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
    }
    bases = [
        base.id
        for base in classes["VisibilityUnconfirmed"].bases
        if isinstance(base, ast.Name)
    ]
    assert bases == ["Exception"]
    assert "Persistence" in classes
    assert "_Operation" in classes
    assert any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "persist"
        for node in tree.body
    )
    facade = (
        REPO_ROOT / "validated_memory" / "journal" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "VisibilityUnconfirmed" not in facade
    assert "Persistence" not in facade


def test_owned_ancestry_is_confirmed_before_durable_children_are_written():
    """Structurally pin the power-ordering that the CLI cannot observe."""
    sources = {
        name: (
            REPO_ROOT / "validated_memory" / "journal" / name
        ).read_text(encoding="utf-8")
        for name in ("records.py", "transactions.py", "executor.py")
    }
    records_append = sources["records.py"].split("def append(", 1)[1].split(
        "\ndef ", 1
    )[0]
    transaction_write = sources["transactions.py"].split(
        "def _write_transaction_file(", 1
    )[1].split("\ndef ", 1)[0]
    preimage_park = sources["executor.py"].split(
        "def _park_preimage(", 1
    )[1].split("\n    def ", 1)[0]

    assert records_append.index("ensure_owned_directory(") < records_append.index(
        "append_bytes("
    )
    assert transaction_write.index(
        "ensure_owned_directory("
    ) < transaction_write.index("install_bytes(")
    assert preimage_park.index("ensure_owned_directory(") < preimage_park.index(
        "install_bytes("
    )


def test_missing_harness_parent_is_confirmed_before_a_link_transaction(
    run_cli, tmp_path, monkeypatch
):
    """A partial external parent chain never becomes a trusted WAL target."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    harness = _external_harness_path(tmp_path, "nested", "memory")
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "confirm:nested"
    )

    result = run_cli(
        "init", "--harness-memory", str(harness), cwd=tmp_path
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "durability is unconfirmed" in result.stderr
    assert harness.parent.is_dir()
    assert harness.is_symlink()
    assert harness.resolve() == (tmp_path / "memory").resolve()
    assert not _transactions(tmp_path)
    local = tmp_path / ".validated-memory" / "local.jsonl"
    assert not local.exists()


def test_partial_harness_ancestry_creation_is_reported_as_visible(
    run_cli, tmp_path, monkeypatch
):
    """A later mkdir failure cannot erase an earlier visible ancestor."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    first = _external_harness_path(tmp_path)
    harness = first / "nested" / "memory"
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "mkdir:nested"
    )

    result = run_cli(
        "init", "--harness-memory", str(harness), cwd=tmp_path
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert first.is_dir()
    assert "may be visible" in result.stderr
    assert "Nothing has been written" not in result.stderr
    assert not _transactions(tmp_path)
    local = tmp_path / ".validated-memory" / "local.jsonl"
    assert not local.exists()


def test_partial_harness_repair_cannot_become_a_clean_unconfirmed_noop(
    run_cli, tmp_path, monkeypatch
):
    """A fail-open repair retains one stable ancestry obligation on retry."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    harness = _external_harness_path(tmp_path, "nested", "memory")
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "mkdir:nested"
    )

    first = run_cli(
        "init", "--harness-memory", str(harness), cwd=tmp_path
    )

    assert first.returncode == 1, (first.stdout, first.stderr)
    assert harness.is_symlink()
    assert harness.resolve() == (tmp_path / "memory").resolve()
    assert "durability is unconfirmed" in first.stderr
    assert not _transactions(tmp_path)
    local = tmp_path / ".validated-memory" / "local.jsonl"
    assert not local.exists()

    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        f"confirm:{tmp_path.name}-harness",
    )
    retry = run_cli(
        "init", "--harness-memory", str(harness), cwd=tmp_path
    )

    assert retry.returncode == 1, (retry.stdout, retry.stderr)
    assert "durability is unconfirmed" in retry.stderr
    assert "kept symlink" not in retry.stdout
    assert harness.is_symlink()
    assert harness.resolve() == (tmp_path / "memory").resolve()
    assert not _transactions(tmp_path)
    assert not local.exists()

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    repaired = run_cli(
        "init", "--harness-memory", str(harness), cwd=tmp_path
    )

    assert repaired.returncode == 0, (repaired.stdout, repaired.stderr)
    assert "kept symlink" in repaired.stdout
    assert not _transactions(tmp_path)
    assert not local.exists()


def test_link_publication_does_not_create_ancestry_inside_publish():
    """External ancestry is prepared before WAL, outside target publication."""
    source = (
        REPO_ROOT / "validated_memory" / "journal" / "executor.py"
    ).read_text(encoding="utf-8")
    publish = source.split("    def _publish(", 1)[1].split("\n    def ", 1)[0]
    assert ".mkdir(parents=True" not in publish


def test_directory_publication_requires_its_named_parent_and_one_mkdir():
    """A repo directory intention never creates an unrecorded ancestor."""
    executor = (
        REPO_ROOT / "validated_memory" / "journal" / "executor.py"
    ).read_text(encoding="utf-8")
    publish = executor.split("    def _publish(", 1)[1].split("\n    def ", 1)[0]
    directory_branch = publish.split("if intention.directory:", 1)[1].split(
        "elif intention.op == LINK:", 1
    )[0]
    assert "if not target.parent.is_dir():" in directory_branch
    assert directory_branch.count("create_directory(target)") == 1

    durable = (
        REPO_ROOT / "validated_memory" / "journal" / "durable.py"
    ).read_text(encoding="utf-8")
    helper = durable.split("def create_directory(path):", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert helper.count(' _injected_error("mkdir", path)'.lstrip()) == 1
    assert helper.count("os.mkdir(path)") == 1
    assert helper.index('_injected_error("mkdir", path)') < helper.index(
        "os.mkdir(path)"
    )
    assert "parents=True" not in helper


def test_failed_target_mkdir_after_wal_aborts_without_history(
    run_cli, tmp_path, monkeypatch
):
    """A target mkdir failure closes its WAL and a retry records one pair."""
    monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", "mkdir:knowledge")

    failed = run_cli("init", cwd=tmp_path)

    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    assert "Traceback" not in failed.stderr, failed.stderr
    assert not (tmp_path / "knowledge").exists()
    assert not _transactions(tmp_path)
    assert "Nothing has been published" in failed.stderr, failed.stderr
    assert not [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record["path"] == "knowledge" and record["op"] == "create"
    ]

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    retried = run_cli("init", cwd=tmp_path)

    assert retried.returncode == 0, (retried.stdout, retried.stderr)
    records = _records(tmp_path / "journal.jsonl")
    pair = [
        record
        for record in records
        if record["path"] == "knowledge" and record["op"] == "create"
    ]
    assert [record["stage"] for record in pair] == ["prepared", "committed"]
    assert pair[0]["transaction"] == pair[1]["transaction"]

    again = run_cli("init", cwd=tmp_path)
    assert again.returncode == 0, (again.stdout, again.stderr)
    assert _records(tmp_path / "journal.jsonl") == records


def test_visible_file_create_barrier_failure_recovers_exactly_once(
    run_cli, tmp_path, monkeypatch
):
    """A visible create keeps WAL, writes no history, and is republished."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "create-exclusive:.gitignore"
    )

    failed = run_cli("init", cwd=tmp_path)

    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    assert "Traceback" not in failed.stderr, failed.stderr
    assert (tmp_path / ".gitignore").is_file()
    assert "visible" in failed.stderr, failed.stderr
    assert "Nothing has been published" not in failed.stderr
    transactions = _transactions(tmp_path)
    assert len(transactions) == 1, transactions
    transaction = transactions[0]
    assert transaction["unconfirmed"] == "target", transaction
    assert not [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record.get("transaction") == transaction["transaction"]
    ]

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    recovered = run_cli("init", cwd=tmp_path)

    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    pair = [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record.get("transaction") == transaction["transaction"]
    ]
    assert [record["stage"] for record in pair] == ["prepared", "committed"]
    assert not _transactions(tmp_path)

    again = run_cli("init", cwd=tmp_path)
    assert again.returncode == 0, (again.stdout, again.stderr)
    assert len(
        [
            record
            for record in _records(tmp_path / "journal.jsonl")
            if record.get("transaction") == transaction["transaction"]
        ]
    ) == 2


@pytest.mark.parametrize("point", ("write", "file-fsync"))
def test_failed_exclusive_content_write_confirms_name_cleanup(
    run_cli, tmp_path, monkeypatch, point
):
    """A pre-publication content failure may claim absence only after a barrier."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", f"{point}:.gitignore"
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr
    assert not (tmp_path / ".gitignore").exists()
    assert not _transactions(tmp_path)
    assert "Nothing has been published" in result.stderr
    assert not [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record["path"] == ".gitignore"
    ]

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    retried = run_cli("init", cwd=tmp_path)

    assert retried.returncode == 0, (retried.stdout, retried.stderr)
    records = _records(tmp_path / "journal.jsonl")
    pair = [
        record
        for record in records
        if record["path"] == ".gitignore" and record["op"] == "create"
    ]
    assert [record["stage"] for record in pair] == ["prepared", "committed"]
    assert pair[0]["transaction"] == pair[1]["transaction"]

    again = run_cli("init", cwd=tmp_path)
    assert again.returncode == 0, (again.stdout, again.stderr)
    assert _records(tmp_path / "journal.jsonl") == records


@pytest.mark.parametrize("point", ("write", "file-fsync", "atomic-install"))
def test_existing_file_previsibility_failure_preserves_append_target(
    run_cli, tmp_path, monkeypatch, point
):
    """The shared existing-file publisher fails before replacing an append target."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    target = tmp_path / ".gitignore"
    original = b"build/\n"
    target.write_bytes(original)
    target.chmod(0o640)
    identity = target.stat().st_ino
    history = tmp_path / "journal.jsonl"
    before_history = history.read_bytes()
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", f"{point}:.gitignore"
    )

    failed = run_cli("init", cwd=tmp_path)

    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    assert "Traceback" not in failed.stderr, failed.stderr
    assert "Nothing has been published" in failed.stderr, failed.stderr
    assert target.read_bytes() == original
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert target.stat().st_ino == identity
    assert history.read_bytes() == before_history
    assert not _transactions(tmp_path)
    assert not list(tmp_path.glob("..gitignore.*.tmp"))

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    retried = run_cli("init", cwd=tmp_path)

    assert retried.returncode == 0, (retried.stdout, retried.stderr)
    records = _records(history)
    pair = [
        record
        for record in records
        if record["path"] == ".gitignore" and record["op"] == "append"
    ]
    assert [record["stage"] for record in pair] == ["prepared", "committed"]
    assert pair[0]["transaction"] == pair[1]["transaction"]
    assert [record["prior_bytes"] for record in pair] == [len(original)] * 2

    again = run_cli("init", cwd=tmp_path)
    assert again.returncode == 0, (again.stdout, again.stderr)
    assert _records(history) == records


def test_failed_exclusive_write_with_uncertain_cleanup_retains_target_fact(
    run_cli, tmp_path, monkeypatch
):
    """An unconfirmed removal cannot be reported as a clean content failure."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        "write:.gitignore,remove:.gitignore",
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr
    assert not (tmp_path / ".gitignore").exists()
    transactions = _transactions(tmp_path)
    assert len(transactions) == 1, transactions
    assert transactions[0]["unconfirmed"] == "target", transactions[0]
    assert "durability is unconfirmed" in result.stderr
    assert "Nothing has been published" not in result.stderr
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry.get("transaction") == transactions[0]["transaction"]
    ]


@pytest.mark.parametrize("shape", ("replace", "directory", "link"))
def test_each_visible_target_shape_retains_recoverable_uncertainty(
    run_cli, tmp_path, monkeypatch, shape
):
    """Replace, mkdir and symlink barriers share the target recovery rule."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    harness = _external_harness_path(tmp_path, "memory")
    if shape == "replace":
        target = tmp_path / ".gitignore"
        original = b"adopter line\n"
        target.write_bytes(original)
        fault = "install:.gitignore"
        durability = "repo"
    elif shape == "directory":
        target = tmp_path / "knowledge"
        target.rmdir()
        fault = "create-directory:knowledge"
        durability = "repo"
    else:
        target = harness
        target.parent.mkdir(parents=True)
        fault = "replace-symlink:memory"
        durability = "local"
    monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", fault)

    arguments = ("init", "--harness-memory", str(harness)) if shape == "link" else ("init",)
    failed = run_cli(*arguments, cwd=tmp_path)

    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    assert "Traceback" not in failed.stderr, failed.stderr
    transaction = _transactions(tmp_path)[0]
    assert transaction["unconfirmed"] == "target", transaction
    if shape == "replace":
        assert transaction["intention"]["op"] == "append", transaction
        assert transaction["prior_bytes"] == len(original), transaction
    assert target.exists() or target.is_symlink()
    history = (
        tmp_path / "journal.jsonl"
        if durability == "repo"
        else tmp_path / ".validated-memory" / "local.jsonl"
    )
    existing = _records(history) if history.exists() else []
    assert not [
        entry
        for entry in existing
        if entry.get("transaction") == transaction["transaction"]
    ]

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    recovered = run_cli(*arguments, cwd=tmp_path)

    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    pair = [
        entry
        for entry in _records(history)
        if entry.get("transaction") == transaction["transaction"]
    ]
    assert [entry["stage"] for entry in pair] == ["prepared", "committed"]
    if shape == "replace":
        assert [entry["op"] for entry in pair] == ["append", "append"]
        assert [entry["prior_bytes"] for entry in pair] == [len(original)] * 2
    assert not _transactions(tmp_path)


def test_nonempty_unconfirmed_directory_is_not_replaced_by_recovery(
    run_cli, tmp_path, monkeypatch
):
    """Directory-kind postimages do not prove later child membership is exact."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    target = tmp_path / "knowledge"
    target.rmdir()
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "create-directory:knowledge"
    )
    assert run_cli("init", cwd=tmp_path).returncode == 1
    transaction = _transactions(tmp_path)[0]["transaction"]
    child = target / "added-after-failure.md"
    child.write_text("keep me\n", encoding="utf-8")
    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "not empty" in result.stderr
    assert child.read_text(encoding="utf-8") == "keep me\n"
    assert _transactions(tmp_path)[0]["transaction"] == transaction


def test_directory_republication_never_renames_a_path_to_itself():
    """A same-source/destination rename is not a fresh namespace operation."""
    source = (
        REPO_ROOT / "validated_memory" / "journal" / "durable.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "republish_directory"
    )
    for call in (
        node for node in ast.walk(function) if isinstance(node, ast.Call)
    ):
        if ast.unparse(call.func) in ("os.rename", "os.replace"):
                assert len(call.args) >= 2
                assert ast.unparse(call.args[0]) != ast.unparse(call.args[1])


def test_directory_republication_confirms_staging_and_gates_windows():
    """Staging is durable before replace; unsupported Windows never attempts it."""
    source = (
        REPO_ROOT / "validated_memory" / "journal" / "durable.py"
    ).read_text(encoding="utf-8")
    republish = source.split("def republish_directory(", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert 'os.name == "nt"' in republish
    assert republish.index('os.name == "nt"') < republish.index("tempfile.mkdtemp(")
    assert republish.index("ensure_owned_directory(") < republish.index("persist(")

    ensure = source.split("def ensure_owned_directory(", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert ensure.index("current = path") < ensure.index("current = current.parent")


def test_changed_target_unconfirmed_state_remains_gated(
    run_cli, tmp_path, monkeypatch
):
    """Recovery never republishes over a target that lost the exact postimage."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "create-exclusive:.gitignore"
    )
    assert run_cli("init", cwd=tmp_path).returncode == 1
    transaction = _transactions(tmp_path)[0]["transaction"]
    (tmp_path / ".gitignore").write_text("changed afterwards\n", encoding="utf-8")
    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert transaction in result.stderr
    assert "exact postimage" in result.stderr
    assert _transactions(tmp_path)[0]["transaction"] == transaction


def test_target_snapshot_swap_during_recovery_is_not_overwritten(
    run_cli, tmp_path, monkeypatch
):
    """Recovery installs only the bytes it validated immediately beforehand."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "create-exclusive:.gitignore"
    )
    assert run_cli("init", cwd=tmp_path).returncode == 1
    transaction = _transactions(tmp_path)[0]["transaction"]
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "swap-target:.gitignore"
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "changed after its validated snapshot" in result.stderr
    assert (tmp_path / ".gitignore").read_bytes() == b"adversarial swap\n"
    artifact = _transactions(tmp_path)[0]
    assert artifact["transaction"] == transaction
    assert artifact["unconfirmed"] == "target"
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry.get("transaction") == transaction
    ]


def test_same_byte_symlink_swap_during_target_recovery_is_gated(
    run_cli, tmp_path, monkeypatch
):
    """Matching bytes cannot disguise a different target kind."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "create-exclusive:.gitignore"
    )
    assert run_cli("init", cwd=tmp_path).returncode == 1
    transaction = _transactions(tmp_path)[0]["transaction"]
    expected = (tmp_path / ".gitignore").read_bytes()
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        "swap-target-symlink:.gitignore",
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "validated snapshot" in result.stderr
    assert (tmp_path / ".gitignore").is_symlink()
    assert (tmp_path / ".gitignore").read_bytes() == expected
    artifact = _transactions(tmp_path)[0]
    assert artifact["transaction"] == transaction
    assert artifact["unconfirmed"] == "target"


def test_different_mode_swap_during_target_recovery_is_gated(
    run_cli, tmp_path, monkeypatch
):
    """A replacement postimage's mode belongs to its validated snapshot."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    target = tmp_path / ".gitignore"
    target.write_text("adopter line\n", encoding="utf-8")
    target.chmod(0o640)
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "install:.gitignore"
    )
    assert run_cli("init", cwd=tmp_path).returncode == 1
    transaction = _transactions(tmp_path)[0]["transaction"]
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "swap-target-mode:.gitignore"
    )

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "validated snapshot" in result.stderr
    assert stat.S_IMODE(target.stat().st_mode) != 0o640
    artifact = _transactions(tmp_path)[0]
    assert artifact["transaction"] == transaction
    assert artifact["unconfirmed"] == "target"


@pytest.mark.parametrize("history_kind", ("first-local", "existing-repo"))
def test_history_barrier_failure_reconfirms_range_without_replacement(
    run_cli, tmp_path, monkeypatch, history_kind
):
    """A complete retained append is reconfirmed through the same artifact."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    if history_kind == "first-local":
        harness = _external_harness_path(tmp_path, "memory")
        arguments = ("init", "--harness-memory", str(harness))
        history = tmp_path / ".validated-memory" / "local.jsonl"
        monkeypatch.setenv(
            "VALIDATED_MEMORY_PERSISTENCE_FAULT", "append:local.jsonl"
        )
    else:
        (tmp_path / ".gitignore").write_text("adopter line\n", encoding="utf-8")
        arguments = ("init",)
        history = tmp_path / "journal.jsonl"
        monkeypatch.setenv(
            "VALIDATED_MEMORY_PERSISTENCE_FAULT", "append:journal.jsonl"
        )

    failed = run_cli(*arguments, cwd=tmp_path)

    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    assert "Traceback" not in failed.stderr, failed.stderr
    transaction = _transactions(tmp_path)[0]
    assert transaction["stage"] == "published", transaction
    assert transaction["unconfirmed"] == "history", transaction
    visible = [
        entry
        for entry in _records(history)
        if entry.get("transaction") == transaction["transaction"]
    ]
    assert [entry["stage"] for entry in visible] == ["prepared", "committed"]
    identity = (history.stat().st_dev, history.stat().st_ino)
    mode = stat.S_IMODE(history.stat().st_mode)

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    recovered = run_cli(*arguments, cwd=tmp_path)

    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    pair = [
        entry
        for entry in _records(history)
        if entry.get("transaction") == transaction["transaction"]
    ]
    assert [entry["stage"] for entry in pair] == ["prepared", "committed"]
    assert (history.stat().st_dev, history.stat().st_ino) == identity
    assert stat.S_IMODE(history.stat().st_mode) == mode
    assert not _transactions(tmp_path)


def test_append_reconfirmation_preserves_a_supported_history_symlink(
    run_cli, tmp_path, monkeypatch
):
    """Descriptor reconfirmation follows an established shared-store history."""
    store = tmp_path / "store"
    store.mkdir()
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert run_cli("init", cwd=adopter).returncode == 0
    link = adopter / "journal.jsonl"
    history = store / "journal.jsonl"
    link.rename(history)
    link.symlink_to(history)
    (adopter / "validated-memory.md").unlink()
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        "history-file-fsync:journal.jsonl",
    )

    failed = run_cli("init", cwd=adopter)

    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    transaction = _transactions(adopter)[0]
    transaction_id = transaction["transaction"]
    matching_before = [
        entry
        for entry in _records(history)
        if entry.get("transaction") == transaction_id
    ]
    assert [entry["stage"] for entry in matching_before] == [
        "prepared",
        "committed",
    ]
    link_target = os.readlink(link)
    identity = (history.stat().st_dev, history.stat().st_ino)
    bytes_before = history.read_bytes()
    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")

    recovered = run_cli("init", cwd=adopter)

    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    assert recovered.stderr == ""
    assert link.is_symlink()
    assert os.readlink(link) == link_target
    assert (history.stat().st_dev, history.stat().st_ino) == identity
    assert history.read_bytes() == bytes_before
    matching_after = [
        entry
        for entry in _records(history)
        if entry.get("transaction") == transaction_id
    ]
    assert matching_after == matching_before
    assert not _transactions(adopter)


def test_append_file_barrier_failure_uses_full_range_reconfirmation(
    run_cli, tmp_path, monkeypatch
):
    """Absent file-flush proof re-dirties exactly the retained append range."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    (tmp_path / ".gitignore").write_text("adopter line\n", encoding="utf-8")
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        "history-file-fsync:journal.jsonl",
    )

    failed = run_cli("init", cwd=tmp_path)

    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    transaction = _transactions(tmp_path)[0]
    transaction_id = transaction["transaction"]
    assert failed.stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert failed.stderr == (
        "ERROR: journal.jsonl: journal: the exact append effect may be visible, "
        "but file-data durability is unconfirmed. Transaction "
        f"{transaction_id} was retained. Preserve the retained transaction "
        "and rerun init\n"
    )
    assert transaction["unconfirmed_reason"] == (
        "append file-data durability unconfirmed"
    )
    history = tmp_path / "journal.jsonl"
    before = _final_tree_snapshot(tmp_path)
    identity = (history.stat().st_dev, history.stat().st_ino)

    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        "append-reconfirmation-rewrite:journal.jsonl",
    )
    refused = run_cli("init", cwd=tmp_path)

    assert refused.returncode == 1, (refused.stdout, refused.stderr)
    assert refused.stderr == (
        "ERROR: journal.jsonl: journal: history reconfirmation wrote nothing "
        "because [Errno 5] injected persistence failure: 'journal.jsonl'. The "
        "retained transaction and both histories were preserved. Restore access "
        "or remove the environmental obstruction, then rerun init\n"
    )
    assert _final_tree_snapshot(tmp_path) == before
    assert (history.stat().st_dev, history.stat().st_ino) == identity

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    recovered = run_cli("init", cwd=tmp_path)
    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    assert recovered.stderr == ""
    assert not _transactions(tmp_path)
    assert (history.stat().st_dev, history.stat().st_ino) == identity


def test_append_directory_barrier_proof_skips_range_rewrite(
    run_cli, tmp_path, monkeypatch
):
    """Exact retained file-flush proof permits directory-only confirmation."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    (tmp_path / ".gitignore").write_text("adopter line\n", encoding="utf-8")
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "append:journal.jsonl"
    )
    failed = run_cli("init", cwd=tmp_path)
    transaction = _transactions(tmp_path)[0]
    transaction_id = transaction["transaction"]
    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    assert failed.stderr == (
        "ERROR: journal.jsonl: journal: the exact append data is confirmed, "
        "but its carrying-directory durability is unconfirmed. Transaction "
        f"{transaction_id} was retained. Preserve the retained transaction "
        "and rerun init\n"
    )
    assert transaction["unconfirmed_reason"] == (
        "append file data confirmed; carrying-directory durability unconfirmed"
    )
    history = tmp_path / "journal.jsonl"
    identity = (history.stat().st_dev, history.stat().st_ino)
    modified = history.stat().st_mtime_ns

    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        "append-reconfirmation-rewrite:journal.jsonl",
    )
    recovered = run_cli("init", cwd=tmp_path)

    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    assert recovered.stderr == ""
    assert not _transactions(tmp_path)
    assert (history.stat().st_dev, history.stat().st_ino) == identity
    assert history.stat().st_mtime_ns == modified


def test_only_exact_directory_barrier_proof_skips_range_rewrite(
    run_cli, tmp_path, monkeypatch
):
    """A generic legacy reason cannot silently acquire directory-only authority."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    (tmp_path / ".gitignore").write_text("adopter line\n", encoding="utf-8")
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "append:journal.jsonl"
    )
    assert run_cli("init", cwd=tmp_path).returncode == 1
    transaction_path = next(
        (tmp_path / ".validated-memory" / "transactions").glob("*.json")
    )
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    transaction["unconfirmed_reason"] = "legacy history uncertainty"
    transaction_path.write_text(
        json.dumps(transaction, sort_keys=True) + "\n", encoding="utf-8"
    )
    before = _final_tree_snapshot(tmp_path)
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        "append-reconfirmation-rewrite:journal.jsonl",
    )

    refused = run_cli("init", cwd=tmp_path)

    assert refused.returncode == 1, (refused.stdout, refused.stderr)
    assert refused.stderr == (
        "ERROR: journal.jsonl: journal: history reconfirmation wrote nothing "
        "because [Errno 5] injected persistence failure: 'journal.jsonl'. The "
        "retained transaction and both histories were preserved. Restore access "
        "or remove the environmental obstruction, then rerun init\n"
    )
    assert _final_tree_snapshot(tmp_path) == before


def test_directory_only_reconfirmation_failure_is_terminal_and_retained(
    run_cli, tmp_path, monkeypatch
):
    """A failed fresh directory barrier retains exact proof and gates scaffold."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    (tmp_path / ".gitignore").write_text("adopter line\n", encoding="utf-8")
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "append:journal.jsonl"
    )
    assert run_cli("init", cwd=tmp_path).returncode == 1
    transaction = _transactions(tmp_path)[0]
    history = tmp_path / "journal.jsonl"
    before = _final_tree_snapshot(tmp_path)
    identity = (history.stat().st_dev, history.stat().st_ino)
    modified = history.stat().st_mtime_ns
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        "append-reconfirmation-directory:journal.jsonl",
    )

    retained = run_cli("init", cwd=tmp_path)

    assert retained.returncode == 1, (retained.stdout, retained.stderr)
    assert retained.stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert "selected append range was re-dirtied or its directory was reconfirmed" in retained.stderr
    assert f"Transaction {transaction['transaction']} was retained" in retained.stderr
    assert _final_tree_snapshot(tmp_path) == before
    assert (history.stat().st_dev, history.stat().st_ino) == identity
    assert history.stat().st_mtime_ns == modified
    assert _transactions(tmp_path)[0]["unconfirmed_reason"] == (
        "append file data confirmed; carrying-directory durability unconfirmed"
    )


def test_append_reconfirmation_cleanup_uncertainty_keeps_wal_and_stops_run(
    run_cli, tmp_path, monkeypatch
):
    """Confirmed history alone cannot turn uncertain WAL cleanup into success."""
    transaction, history = _retained_complete_append(
        run_cli, tmp_path, monkeypatch
    )
    transaction_id = transaction["transaction"]
    later = tmp_path / "knowledge-extension.md"
    later.unlink()
    history_before = history.read_bytes()
    identity = (history.stat().st_dev, history.stat().st_ino)
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        f"remove:{transaction_id}.json",
    )

    retained = run_cli("init", cwd=tmp_path)

    assert retained.returncode == 1, (retained.stdout, retained.stderr)
    assert retained.stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert retained.stderr.startswith(
        "ERROR: journal.jsonl: journal: the selected append range was coherently "
        "confirmed, but transaction cleanup is incomplete: "
    )
    assert retained.stderr.endswith(
        f"Transaction {transaction_id} was retained. The append is already "
        "confirmed and must not be repeated. Preserve the retained transaction "
        "and rerun init\n"
    )
    kept = _transactions(tmp_path)
    assert len(kept) == 1
    assert kept[0]["transaction"] == transaction_id
    assert kept[0]["unconfirmed"] == "cleanup"
    assert not later.exists()
    assert history.read_bytes() == history_before
    assert (history.stat().st_dev, history.stat().st_ino) == identity


def _retained_complete_append(run_cli, tree, monkeypatch):
    assert run_cli("init", cwd=tree).returncode == 0
    (tree / ".gitignore").write_text("adopter line\n", encoding="utf-8")
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        "history-file-fsync:journal.jsonl",
    )
    failed = run_cli("init", cwd=tree)
    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    transaction = _transactions(tree)[0]
    return transaction, tree / "journal.jsonl"


@pytest.mark.parametrize("occurrence", ("zero", "prepared"))
def test_hard_death_with_incomplete_claim_replays_stored_exact_bytes(
    run_cli, tmp_path, monkeypatch, occurrence
):
    """C1d completes zero/prepared claims without rebuilding timestamps."""
    prefix_length = 0
    if occurrence == "prepared":
        calibration = tmp_path / "calibration"
        calibration.mkdir()
        assert run_cli("init", cwd=calibration).returncode == 0
        (calibration / ".gitignore").write_text(
            "adopter line\n", encoding="utf-8"
        )
        monkeypatch.setenv(
            "VALIDATED_MEMORY_STORAGE_CRASH", "history-prefix:0"
        )
        assert run_cli("init", cwd=calibration).returncode == 71
        claim = _transactions(calibration)[0]["history_append"]
        prefix_length = len(
            (json.dumps(claim["records"][0], sort_keys=True) + "\n").encode(
                "utf-8"
            )
        )
        monkeypatch.delenv("VALIDATED_MEMORY_STORAGE_CRASH")

    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert run_cli("init", cwd=adopter).returncode == 0
    (adopter / ".gitignore").write_text("adopter line\n", encoding="utf-8")
    monkeypatch.setenv(
        "VALIDATED_MEMORY_STORAGE_CRASH",
        f"history-prefix:{prefix_length}",
    )

    crashed = run_cli("init", cwd=adopter)

    assert crashed.returncode == 71, (crashed.stdout, crashed.stderr)
    transaction_path = next(
        (adopter / ".validated-memory" / "transactions").glob("*.json")
    )
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    transaction_id = transaction["transaction"]
    claim = transaction["history_append"]
    history = adopter / "journal.jsonl"
    matching = [
        entry
        for entry in _records(history)
        if entry.get("transaction") == transaction_id
    ]
    expected = [] if occurrence == "zero" else [claim["records"][0]]
    assert matching == expected
    history_before = history.read_bytes()
    monkeypatch.delenv("VALIDATED_MEMORY_STORAGE_CRASH")

    retried = run_cli("init", cwd=adopter)

    assert retried.returncode == 0, (retried.stdout, retried.stderr)
    assert retried.stdout == (
        f"init: recovered .gitignore from transaction {transaction_id}\n"
        "init: kept knowledge\n"
        "init: kept memory\n"
        "init: kept memory/MEMORY.md\n"
        "init: kept validated-memory.md\n"
        "init: kept knowledge-extension.md\n"
        "init: 0 created, 5 kept, 0 error(s), 0 warning(s)\n"
    )
    assert retried.stderr == ""
    assert not transaction_path.exists()
    replay = b"".join(
        (json.dumps(entry, sort_keys=True) + "\n").encode("utf-8")
        for entry in claim["records"][(1 if occurrence == "prepared" else 0):]
    )
    assert history.read_bytes() == history_before + replay
    assert [
        entry
        for entry in _records(history)
        if entry.get("transaction") == transaction_id
    ] == claim["records"]


def test_append_reconfirmation_preserves_complete_valid_suffix(
    run_cli, tmp_path, monkeypatch
):
    """A later complete pair remains byte-identical outside the claimed range."""
    transaction, history = _retained_complete_append(
        run_cli, tmp_path, monkeypatch
    )
    claim = transaction["history_append"]
    descendants = []
    for entry in claim["records"]:
        descendants.append(
            {
                **entry,
                "transaction": "descendant-transaction",
                "run": "descendant-run",
                "path": "descendant-path",
            }
        )
    suffix = b"".join(
        (json.dumps(entry, sort_keys=True) + "\n").encode("utf-8")
        for entry in descendants
    )
    history.write_bytes(history.read_bytes() + suffix)
    before = history.read_bytes()
    identity = (history.stat().st_dev, history.stat().st_ino)

    recovered = run_cli("init", cwd=tmp_path)

    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    assert recovered.stderr == ""
    assert history.read_bytes() == before
    assert history.read_bytes().endswith(suffix)
    assert (history.stat().st_dev, history.stat().st_ino) == identity
    assert not _transactions(tmp_path)


@pytest.mark.parametrize(
    ("suffix", "expected_stderr"),
    (
        (
            b'{"schema":',
            "ERROR: journal.jsonl: journal: non-empty history does not end "
            "with a line feed; the final record is not appendable without "
            "explicit targeted repair\n",
        ),
        (
            b"{\n",
            "ERROR: journal.jsonl:{line}: journal: line is not valid JSON: "
            "Expecting property name enclosed in double quotes\n",
        ),
    ),
)
def test_append_reconfirmation_refuses_malformed_or_torn_suffix(
    run_cli, tmp_path, monkeypatch, suffix, expected_stderr
):
    """A WAL grants no authority over an incomplete or malformed suffix."""
    transaction, history = _retained_complete_append(
        run_cli, tmp_path, monkeypatch
    )
    line = history.read_bytes().count(b"\n") + 1
    history.write_bytes(history.read_bytes() + suffix)
    before = _final_tree_snapshot(tmp_path)

    refused = run_cli("init", cwd=tmp_path)

    assert refused.returncode == 1, (refused.stdout, refused.stderr)
    assert refused.stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert refused.stderr == expected_stderr.format(line=line)
    assert transaction["transaction"] in (
        path.stem for path in (tmp_path / ".validated-memory" / "transactions").glob("*.json")
    )
    assert _final_tree_snapshot(tmp_path) == before


def test_append_reconfirmation_refuses_conflicting_suffix(
    run_cli, tmp_path, monkeypatch
):
    """A complete but conflicting suffix is preserved without a range write."""
    transaction, history = _retained_complete_append(
        run_cli, tmp_path, monkeypatch
    )
    conflicting = (
        json.dumps(transaction["history_append"]["records"][0], sort_keys=True)
        + "\n"
    ).encode("utf-8")
    history.write_bytes(history.read_bytes() + conflicting)
    before = _final_tree_snapshot(tmp_path)

    refused = run_cli("init", cwd=tmp_path)

    assert refused.returncode == 1, (refused.stdout, refused.stderr)
    assert refused.stderr == (
        "ERROR: journal.jsonl: journal: history reconfirmation wrote nothing "
        "because the retained append is mismatched in the current history. "
        f"Transaction {transaction['transaction']} and both histories were "
        "preserved. This operation does not complete or replay an append. "
        "Restore the affected history from a trusted copy, then rerun init\n"
    )
    assert _final_tree_snapshot(tmp_path) == before


@pytest.mark.parametrize("change", ("prefix-bytes", "prefix-digest"))
def test_append_reconfirmation_requires_exact_prefix_proof(
    run_cli, tmp_path, monkeypatch, change
):
    """Neither current bytes nor retained digest may change before authority."""
    transaction, history = _retained_complete_append(
        run_cli, tmp_path, monkeypatch
    )
    transaction_path = next(
        (tmp_path / ".validated-memory" / "transactions").glob("*.json")
    )
    if change == "prefix-digest":
        entry = json.loads(transaction_path.read_text(encoding="utf-8"))
        entry["history_append"]["prefix"]["digest"] = "sha256:" + "0" * 64
        transaction_path.write_text(
            json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8"
        )
    else:
        records = _records(history)
        records[0] = {**records[0], "note": "changed prefix evidence"}
        _rewrite(history, records)
    before = _final_tree_snapshot(tmp_path)

    refused = run_cli("init", cwd=tmp_path)

    assert refused.returncode == 1, (refused.stdout, refused.stderr)
    assert refused.stderr == (
        "ERROR: journal.jsonl: journal: history reconfirmation wrote nothing "
        "because the retained append is mismatched in the current history. "
        f"Transaction {transaction['transaction']} and both histories were "
        "preserved. This operation does not complete or replay an append. "
        "Restore the affected history from a trusted copy, then rerun init\n"
    )
    assert _final_tree_snapshot(tmp_path) == before
    assert _transactions(tmp_path)[0]["transaction"] == transaction["transaction"]


@pytest.mark.parametrize(
    "fault",
    (
        "append-reconfirmation-short-write",
        "append-reconfirmation-file-fsync",
        "append-reconfirmation-after-barriers",
    ),
)
def test_post_write_append_reconfirmation_failure_is_terminal_and_retained(
    run_cli, tmp_path, monkeypatch, fault
):
    """Any failure after the first range write retains the WAL and gates scaffold."""
    transaction, history = _retained_complete_append(
        run_cli, tmp_path, monkeypatch
    )
    before = _final_tree_snapshot(tmp_path)
    identity = (history.stat().st_dev, history.stat().st_ino)
    mode = stat.S_IMODE(history.stat().st_mode)
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        f"{fault}:journal.jsonl",
    )

    retained = run_cli("init", cwd=tmp_path)

    transaction_id = transaction["transaction"]
    assert retained.returncode == 1, (retained.stdout, retained.stderr)
    assert retained.stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert retained.stderr.startswith(
        "ERROR: journal.jsonl: journal: the selected append range was "
        "re-dirtied or its directory was reconfirmed, but coherent successor "
        "confirmation is incomplete: append-reconfirmation of journal.jsonl "
        "is visible, but its durability is unconfirmed: "
    )
    assert retained.stderr.endswith(
        f"Transaction {transaction_id} was retained. Preserve the selected WAL "
        "and both histories; rerun init. Do not replace or truncate either "
        "history\n"
    )
    assert _final_tree_snapshot(tmp_path) == before
    assert (history.stat().st_dev, history.stat().st_ino) == identity
    assert stat.S_IMODE(history.stat().st_mode) == mode
    assert _transactions(tmp_path)[0]["transaction"] == transaction_id


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
@pytest.mark.parametrize("change", ("identity", "mode", "opposite"))
def test_append_reconfirmation_refuses_pair_change_before_range_action(
    run_cli, tmp_path, monkeypatch, change
):
    """Frozen identities and modes are checked again before the first write."""
    transaction, history = _retained_complete_append(
        run_cli, tmp_path, monkeypatch
    )
    local = tmp_path / ".validated-memory" / "local.jsonl"
    opening = _records(history)[0]
    local_entry = {
        **opening,
        "durability": "local",
        "path": "/external/harness-memory",
        "note": "local witness",
    }
    local.write_text(json.dumps(local_entry, sort_keys=True) + "\n", encoding="utf-8")
    local.chmod(0o640)
    process, ready, proceed = _rendezvous_init(
        tmp_path, "before-append-reconfirmation"
    )
    try:
        assert _await_history_attempt(ready) == 1
        if change == "identity":
            replacement = history.with_name("journal.replacement")
            replacement.write_bytes(history.read_bytes())
            replacement.chmod(stat.S_IMODE(history.stat().st_mode))
            os.replace(replacement, history)
        elif change == "mode":
            history.chmod(0o600)
        else:
            replacement = local.with_name("local.replacement")
            replacement.write_bytes(local.read_bytes())
            replacement.chmod(stat.S_IMODE(local.stat().st_mode))
            os.replace(replacement, local)
        frozen_after_race = [
            item for item in _final_tree_snapshot(tmp_path)
            if item[0] != ".validated-memory/lock"
        ]
        selected_identity = (history.stat().st_dev, history.stat().st_ino)
        selected_mode = stat.S_IMODE(history.stat().st_mode)
        os.write(proceed, b"x")
        stdout, stderr = process.communicate(timeout=30)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode == 1, (stdout, stderr)
    assert stdout == (
        "init: kept knowledge\n"
        "init: kept memory\n"
        "init: kept memory/MEMORY.md\n"
        "init: kept validated-memory.md\n"
        "init: kept knowledge-extension.md\n"
        "init: 0 created, 5 kept, 1 error(s), 0 warning(s)\n"
    )
    assert stderr == (
        "ERROR: journal.jsonl: journal: history reconfirmation wrote nothing "
        "because the coherent history pair changed before append "
        "reconfirmation. The retained transaction and both histories were "
        "preserved. Wait for the other writer to finish, then rerun init\n"
    )
    assert [
        item for item in _final_tree_snapshot(tmp_path)
        if item[0] != ".validated-memory/lock"
    ] == frozen_after_race
    assert (history.stat().st_dev, history.stat().st_ino) == selected_identity
    assert stat.S_IMODE(history.stat().st_mode) == selected_mode
    assert _transactions(tmp_path)[0]["transaction"] == transaction["transaction"]


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
def test_append_reconfirmation_gates_opposite_replacement_during_readback(
    run_cli, tmp_path, monkeypatch
):
    """An identical-byte opposite replacement after action is retained."""
    transaction, history = _retained_complete_append(
        run_cli, tmp_path, monkeypatch
    )
    local = tmp_path / ".validated-memory" / "local.jsonl"
    opening = _records(history)[0]
    local_entry = {
        **opening,
        "durability": "local",
        "path": "/external/harness-memory",
        "note": "local witness",
    }
    local.write_text(json.dumps(local_entry, sort_keys=True) + "\n", encoding="utf-8")
    local.chmod(0o640)
    selected_identity = (history.stat().st_dev, history.stat().st_ino)
    process, ready, proceed = _rendezvous_init(
        tmp_path, "after-append-reconfirmation"
    )
    try:
        assert _await_history_attempt(ready) == 1
        former_identity = (local.stat().st_dev, local.stat().st_ino)
        replacement = local.with_name("local.replacement")
        replacement.write_bytes(local.read_bytes())
        replacement.chmod(stat.S_IMODE(local.stat().st_mode))
        os.replace(replacement, local)
        after_race = [
            item for item in _final_tree_snapshot(tmp_path)
            if item[0] != ".validated-memory/lock"
        ]
        os.write(proceed, b"x")
        stdout, stderr = process.communicate(timeout=30)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode == 1, (stdout, stderr)
    assert stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert "coherent successor confirmation is incomplete" in stderr
    assert "append reconfirmation did not produce the exact coherent successor" in stderr
    assert f"Transaction {transaction['transaction']} was retained" in stderr
    assert [
        item for item in _final_tree_snapshot(tmp_path)
        if item[0] != ".validated-memory/lock"
    ] == after_race
    assert (history.stat().st_dev, history.stat().st_ino) == selected_identity
    assert (local.stat().st_dev, local.stat().st_ino) != former_identity
    assert _transactions(tmp_path)[0]["transaction"] == transaction["transaction"]
    assert not (tmp_path / "new-later-effect").exists()


def test_valid_claim_replays_exact_pair_when_history_occurrence_is_zero(
    run_cli, tmp_path, monkeypatch
):
    """A valid stored claim, unlike reconstruction, owns its exact zero replay."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    harness = _external_harness_path(tmp_path, "memory")
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "append:local.jsonl"
    )
    assert (
        run_cli("init", "--harness-memory", str(harness), cwd=tmp_path).returncode
        == 1
    )
    retained = _transactions(tmp_path)[0]
    transaction = retained["transaction"]
    expected = b"".join(
        (json.dumps(entry, sort_keys=True) + "\n").encode("utf-8")
        for entry in retained["history_append"]["records"]
    )
    history = tmp_path / ".validated-memory" / "local.jsonl"
    history.unlink()
    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")

    result = run_cli("init", "--harness-memory", str(harness), cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ""
    assert f"recovered {retained['intention']['path']} from transaction {transaction}" in result.stdout
    assert history.read_bytes() == expected
    assert not _transactions(tmp_path)


@pytest.mark.parametrize("damage", ("duplicate", "disagreement"))
def test_inconsistent_history_after_unconfirmed_append_remains_gated(
    run_cli, tmp_path, monkeypatch, damage
):
    """Recovery refuses a duplicated transaction instead of blessing it."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    (tmp_path / ".gitignore").write_text("adopter line\n", encoding="utf-8")
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "append:journal.jsonl"
    )
    assert run_cli("init", cwd=tmp_path).returncode == 1
    transaction = _transactions(tmp_path)[0]["transaction"]
    history = tmp_path / "journal.jsonl"
    lines = history.read_text(encoding="utf-8").splitlines()
    matching = [
        line
        for line in lines
        if json.loads(line).get("transaction") == transaction
    ]
    if damage == "duplicate":
        lines.append(matching[0])
    else:
        committed = next(
            index
            for index, line in enumerate(lines)
            if json.loads(line).get("transaction") == transaction
            and json.loads(line)["stage"] == "committed"
        )
        changed = json.loads(lines[committed])
        changed["purpose"] = "different"
        lines[committed] = json.dumps(changed, sort_keys=True)
    history.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stderr == (
        "ERROR: journal.jsonl: journal: history reconfirmation wrote nothing "
        "because the retained append is mismatched in the current history. "
        f"Transaction {transaction} and both histories were preserved. This "
        "operation does not complete or replay an append. Restore the affected "
        "history from a trusted copy, then rerun init\n"
    )
    assert _transactions(tmp_path)[0]["transaction"] == transaction


def test_cleanup_barrier_failure_retries_without_duplicate_history(
    run_cli, tmp_path, monkeypatch
):
    """Confirmed target/history survive an uncertain WAL unlink exactly once."""
    monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", "remove:*")

    failed = run_cli("init", cwd=tmp_path)

    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    assert "Traceback" not in failed.stderr, failed.stderr
    transaction = _transactions(tmp_path)[0]
    assert transaction["unconfirmed"] == "cleanup", transaction
    pair_before = [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry.get("transaction") == transaction["transaction"]
    ]
    assert [entry["stage"] for entry in pair_before] == ["prepared", "committed"]

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    recovered = run_cli("init", cwd=tmp_path)

    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    pair_after = [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry.get("transaction") == transaction["transaction"]
    ]
    assert pair_after == pair_before
    assert not _transactions(tmp_path)


def test_target_recovery_history_failure_terminalizes_before_later_effect(
    run_cli, tmp_path, monkeypatch
):
    """A current unconfirmed recovery effect stops before another intention."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "create-exclusive:.gitignore"
    )
    assert run_cli("init", cwd=tmp_path).returncode == 1
    transaction = _transactions(tmp_path)[0]["transaction"]

    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "append:journal.jsonl"
    )
    history_failed = run_cli("init", cwd=tmp_path)
    assert history_failed.returncode == 1, (
        history_failed.stdout,
        history_failed.stderr,
    )
    retained = _transactions(tmp_path)
    assert len(retained) == 1
    artifact = next(
        item for item in retained if item["transaction"] == transaction
    )
    assert artifact["transaction"] == transaction
    assert artifact["unconfirmed"] == "history", artifact
    assert "history_append" not in artifact
    assert "history_timestamps" not in artifact
    assert "history" in history_failed.stderr
    assert not (tmp_path / "knowledge").exists()
    assert not (tmp_path / "memory").exists()
    pair = [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry.get("transaction") == transaction
    ]
    assert [entry["stage"] for entry in pair] == ["prepared", "committed"]


@pytest.mark.parametrize("phase", ("target", "restore", "cleanup"))
def test_current_recovery_uncertainty_stops_before_an_independent_wal(
    run_cli, tmp_path, monkeypatch, phase
):
    """Current effect uncertainty is terminal; a later WAL remains untouched."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    if phase in {"target", "restore"}:
        (tmp_path / ".gitignore").write_text(
            "adopter line\n", encoding="utf-8"
        )
        monkeypatch.setenv(
            "VALIDATED_MEMORY_PERSISTENCE_FAULT", "install:.gitignore"
        )
        assert run_cli("init", cwd=tmp_path).returncode == 1
    else:
        (tmp_path / ".gitignore").write_text(
            "adopter line\n", encoding="utf-8"
        )
        monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", "remove:*")
        assert run_cli("init", cwd=tmp_path).returncode == 1

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    primary_path = next(
        (tmp_path / ".validated-memory" / "transactions").glob("*.json")
    )
    primary = json.loads(primary_path.read_text(encoding="utf-8"))
    if phase == "restore":
        blob = (
            tmp_path
            / ".validated-memory"
            / "preimages"
            / primary["preimage_blob"].removeprefix("sha256:")
        )
        restored = tmp_path / "restored-independent.txt"
        restored.write_bytes(blob.read_bytes())
        restored.chmod(primary["preimage"]["mode"])
        primary["intention"] = {
            **primary["intention"],
            "path": restored.name,
        }
        primary["unconfirmed"] = "restore"
        primary["unconfirmed_reason"] = "fixture"
        primary_path.write_text(
            json.dumps(primary, sort_keys=True) + "\n", encoding="utf-8"
        )
    assert primary["unconfirmed"] == phase
    transaction_id = primary["transaction"]
    secondary_id = "f" * 16
    assert transaction_id < secondary_id
    secondary = {
        **primary,
        "transaction": secondary_id,
        "stage": "aborted",
        "reason": "independent fixture",
        "intention": {
            **primary["intention"],
            "path": "independent-path",
        },
    }
    for key in (
        "history_append",
        "history_timestamps",
        "temporary",
        "unconfirmed",
        "unconfirmed_reason",
    ):
        secondary.pop(key, None)
    secondary_path = primary_path.with_name(f"{secondary_id}.json")
    secondary_path.write_text(
        json.dumps(secondary, sort_keys=True) + "\n", encoding="utf-8"
    )
    histories_before = (
        (tmp_path / "journal.jsonl").read_bytes(),
        (tmp_path / ".validated-memory" / "local.jsonl").read_bytes()
        if (tmp_path / ".validated-memory" / "local.jsonl").exists()
        else None,
    )
    target_before = (tmp_path / ".gitignore").read_bytes()
    recovery_path = primary["intention"]["path"]

    if phase == "cleanup":
        fault = f"remove:{primary_path.name}"
        operation = "remove"
        artifact = primary_path.relative_to(tmp_path).as_posix()
    else:
        fault = f"install:{Path(recovery_path).name}"
        operation = "install"
        artifact = recovery_path
    monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", fault)

    result = run_cli("init", cwd=tmp_path)

    failure = (
        f"{operation} of {artifact} is visible, but its durability is "
        f"unconfirmed: [Errno 5] injected persistence failure: '{artifact}'"
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert result.stderr == (
        f"ERROR: .validated-memory/transactions/{transaction_id}.json: journal: "
        f"recovery of {recovery_path} left its {phase} effect visible or "
        f"indeterminate: {failure}. Transaction {transaction_id} and all "
        "available evidence were retained; preserve them and rerun init\n"
    )
    assert secondary_path.exists()
    assert json.loads(primary_path.read_text(encoding="utf-8"))["unconfirmed"] == phase
    assert (tmp_path / ".gitignore").read_bytes() == target_before
    if phase == "restore":
        restored = tmp_path / recovery_path
        assert restored.read_bytes() == blob.read_bytes()
    assert (tmp_path / "journal.jsonl").read_bytes() == histories_before[0]
    local = tmp_path / ".validated-memory" / "local.jsonl"
    assert (local.read_bytes() if local.exists() else None) == histories_before[1]


def test_visible_restore_barrier_failure_never_claims_nothing_was_restored(
    run_cli, tmp_path, monkeypatch
):
    """An exact visible restore retains a fact and finishes on the next init."""
    transaction = _diverged(tmp_path, before="before adoption\n")
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "install:.gitignore"
    )

    failed = run_cli(
        "journal", "--resolve", transaction, "--restore", cwd=tmp_path
    )

    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    assert "Traceback" not in failed.stderr, failed.stderr
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == (
        "before adoption\n"
    )
    assert "Nothing has been restored" not in failed.stderr
    artifact = _transactions(tmp_path)[0]
    assert artifact["unconfirmed"] == "restore", artifact

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    recovered = run_cli("init", cwd=tmp_path)

    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    final = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert final.startswith("before adoption\n")
    assert final.count("/.validated-memory/") == 1
    assert not _transactions(tmp_path)


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
@pytest.mark.parametrize("race", ("target", "opposite-history"))
def test_restore_recovery_requires_exact_target_and_pair_before_cleanup(
    run_cli, tmp_path, monkeypatch, race
):
    """A post-restore race retains both the selected and later WALs."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    (tmp_path / ".gitignore").write_text("adopter line\n", encoding="utf-8")
    monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", "install:.gitignore")
    assert run_cli("init", cwd=tmp_path).returncode == 1
    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    primary_path = next(
        (tmp_path / ".validated-memory" / "transactions").glob("*.json")
    )
    primary = json.loads(primary_path.read_text(encoding="utf-8"))
    blob = (
        tmp_path / ".validated-memory" / "preimages"
        / primary["preimage_blob"].removeprefix("sha256:")
    )
    restored = tmp_path / "restored-race.txt"
    restored.write_bytes(blob.read_bytes())
    restored.chmod(primary["preimage"]["mode"])
    primary["intention"] = {**primary["intention"], "path": restored.name}
    primary["unconfirmed"] = "restore"
    primary["unconfirmed_reason"] = "fixture"
    primary_path.write_text(json.dumps(primary, sort_keys=True) + "\n", encoding="utf-8")
    secondary_id = "f" * 16
    secondary = {
        **primary,
        "transaction": secondary_id,
        "stage": "aborted",
        "reason": "independent fixture",
    }
    secondary.pop("unconfirmed", None)
    secondary.pop("unconfirmed_reason", None)
    secondary_path = primary_path.with_name(f"{secondary_id}.json")
    secondary_path.write_text(json.dumps(secondary, sort_keys=True) + "\n", encoding="utf-8")
    secondary_before = secondary_path.read_bytes()
    process, ready, proceed = _rendezvous_init(
        tmp_path, "after-selected-restore-recovery"
    )
    try:
        assert os.read(ready, 2) == b"1\n"
        if race == "target":
            restored.write_text("raced target\n", encoding="utf-8")
        else:
            (tmp_path / ".validated-memory" / "local.jsonl").write_bytes(b"")
        os.write(proceed, b"1")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:
            process.kill()
            process.wait()

    assert process.returncode == 1, (stdout, stderr)
    assert stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert "exact restored target and coherent history pair" in stderr
    assert primary_path.exists()
    assert secondary_path.read_bytes() == secondary_before


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
def test_post_restore_topology_failure_is_terminal_and_retains_later_wal(
    run_cli, tmp_path, monkeypatch
):
    """Unavailable topology cannot confirm a published restore successor."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    (tmp_path / ".gitignore").write_text("adopter line\n", encoding="utf-8")
    monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", "install:.gitignore")
    assert run_cli("init", cwd=tmp_path).returncode == 1
    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    primary_path = next(
        (tmp_path / ".validated-memory" / "transactions").glob("*.json")
    )
    primary = json.loads(primary_path.read_text(encoding="utf-8"))
    blob = (
        tmp_path / ".validated-memory" / "preimages"
        / primary["preimage_blob"].removeprefix("sha256:")
    )
    restored = tmp_path / "restored-topology.txt"
    restored.write_bytes(blob.read_bytes())
    restored.chmod(primary["preimage"]["mode"])
    primary["intention"] = {**primary["intention"], "path": restored.name}
    primary["unconfirmed"] = "restore"
    primary["unconfirmed_reason"] = "fixture"
    primary_path.write_text(json.dumps(primary, sort_keys=True) + "\n", encoding="utf-8")
    later = primary_path.with_name("ffffffffffffffff.json")
    later_entry = {**primary, "transaction": "ffffffffffffffff", "stage": "aborted"}
    later_entry.pop("unconfirmed", None)
    later_entry.pop("unconfirmed_reason", None)
    later.write_text(json.dumps(later_entry, sort_keys=True) + "\n", encoding="utf-8")
    later_before = later.read_bytes()
    marker = tmp_path / "fail-restore-topology"
    mutant = _topology_marker_mutant(tmp_path, marker)
    process, ready, proceed = _rendezvous_mutant_init(
        tmp_path, mutant, "after-selected-restore-recovery"
    )
    try:
        assert os.read(ready, 2) == b"1\n"
        marker.write_text("fail\n", encoding="utf-8")
        os.write(proceed, b"1")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:
            process.kill()
            process.wait()

    assert process.returncode == 1, (stdout, stderr)
    assert stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert "Traceback" not in stderr
    assert "exact restored target and coherent history pair" in stderr
    assert primary_path.exists()
    assert later.read_bytes() == later_before
    assert restored.read_bytes() == blob.read_bytes()


@pytest.mark.parametrize("damage", ("unknown-phase", "missing-reason"))
def test_unconfirmed_transaction_extension_is_strictly_validated(
    run_cli, tmp_path, damage
):
    """Unknown or incomplete recovery facts are damaged, never guessed at."""
    transaction = _diverged(tmp_path, kill_after=None)
    path = (
        tmp_path / ".validated-memory" / "transactions" / f"{transaction}.json"
    )
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["unconfirmed"] = "future" if damage == "unknown-phase" else "target"
    if damage == "unknown-phase":
        entry["unconfirmed_reason"] = "fixture"
    path.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "damaged transaction" in result.stderr
    expected = "unknown unconfirmed phase" if damage == "unknown-phase" else "no recorded reason"
    assert expected in result.stderr


@pytest.mark.parametrize(
    "damage",
    ("unknown-phase", "missing-reason", "invalid-reason", "orphan-reason"),
)
def test_aborted_transaction_extension_damage_is_retained(
    run_cli, tmp_path, damage
):
    """The aborted outcome cannot bypass validation of recovery facts."""
    transaction = _diverged(tmp_path, kill_after=None)
    path = (
        tmp_path / ".validated-memory" / "transactions" / f"{transaction}.json"
    )
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["stage"] = "aborted"
    entry["reason"] = "fixture abort"
    if damage == "unknown-phase":
        entry["unconfirmed"] = "future"
        entry["unconfirmed_reason"] = "fixture"
    elif damage == "missing-reason":
        entry["unconfirmed"] = "target"
        entry.pop("unconfirmed_reason", None)
    elif damage == "invalid-reason":
        entry["unconfirmed"] = "target"
        entry["unconfirmed_reason"] = 3
    else:
        entry.pop("unconfirmed", None)
        entry["unconfirmed_reason"] = "orphan"
    path.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    before = path.read_bytes()

    checked = run_cli("journal", "--check", cwd=tmp_path)
    initialized = run_cli("init", cwd=tmp_path)

    assert checked.returncode == 1, (checked.stdout, checked.stderr)
    assert initialized.returncode == 1, (initialized.stdout, initialized.stderr)
    assert "damaged transaction" in checked.stderr
    assert "damaged transaction" in initialized.stderr
    assert path.read_bytes() == before


def test_failure_fact_uncertainty_reports_both_failures_and_retains_wal(
    run_cli, tmp_path, monkeypatch
):
    """A failing recovery-fact barrier never hides the protected failure."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    (tmp_path / ".gitignore").write_text("adopter line\n", encoding="utf-8")
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        "install:.gitignore,fact:target",
    )

    failed = run_cli("init", cwd=tmp_path)

    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    assert "durability is unconfirmed" in failed.stderr
    assert "recording that recovery fact was itself unconfirmed" in failed.stderr
    artifact = _transactions(tmp_path)[0]
    assert artifact["unconfirmed"] == "target", artifact
    assert "/.validated-memory/" in (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    )


def test_two_artifacts_holding_different_adoption_ids_are_refused(
    run_cli, tmp_path
):
    """Conflicting adoption IDs gate with both IDs and recovery advice."""
    harness_memory = tmp_path / "harness" / "memory"
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert (
        run_cli(
            "init", "--harness-memory", str(harness_memory), cwd=adopter
        ).returncode
        == 0
    )
    vault = adopter / ".validated-memory" / "local.jsonl"
    foreign = "f" * 16
    vault.write_text(
        "".join(
            json.dumps({**json.loads(line), "adoption": foreign}, sort_keys=True)
            + "\n"
            for line in vault.read_text(encoding="utf-8").splitlines()
        ),
        encoding="utf-8",
    )
    mine = _records(adopter / "journal.jsonl")[0]["adoption"]

    result = run_cli("init", cwd=adopter)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "adoption" in result.stderr, result.stderr
    assert foreign in result.stderr, result.stderr
    assert mine in result.stderr, result.stderr
    assert "restore" in result.stderr, result.stderr
    assert "adopt afresh" in result.stderr, result.stderr


@pytest.mark.parametrize("durability", ("repo", "local"))
def test_a_later_record_with_another_adoption_id_gates_init(
    run_cli, tmp_path, durability
):
    """Every history record, not only the first, must name one adoption."""
    harness_memory = tmp_path / "harness" / "memory"
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert run_cli(
        "init", "--harness-memory", str(harness_memory), cwd=adopter
    ).returncode == 0
    repository = adopter / "journal.jsonl"
    local = adopter / ".validated-memory" / "local.jsonl"
    artifact = repository if durability == "repo" else local
    lines = artifact.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 1, lines
    mine = json.loads(lines[0])["adoption"]
    foreign = "f" * 16
    lines[-1] = json.dumps(
        {**json.loads(lines[-1]), "adoption": foreign}, sort_keys=True
    )
    artifact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    repository_before = repository.read_bytes()
    local_before = local.read_bytes()

    result = run_cli("init", cwd=adopter)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert "one project has one adoption id" in result.stderr, result.stderr
    assert mine in result.stderr, result.stderr
    assert foreign in result.stderr, result.stderr
    assert repository.read_bytes() == repository_before
    assert local.read_bytes() == local_before


# --- a journal that is there is never treated as one that is not --------------


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
@pytest.mark.parametrize("arguments", [(), ("--check",)])
def test_a_journal_that_cannot_be_read_is_a_finding_not_a_traceback(
    run_cli, tmp_path, arguments
):
    """An unreadable local log gates with its path, count and no traceback."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    vault = tmp_path / ".validated-memory"
    vault.chmod(0o000)
    try:
        result = run_cli("journal", *arguments, cwd=tmp_path)
    finally:
        vault.chmod(0o755)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "ERROR" in result.stderr, result.stderr
    assert ".validated-memory/local.jsonl" in result.stderr, result.stderr
    expected = len(_records(tmp_path / "journal.jsonl"))
    assert result.stdout == f"journal: {expected} record(s), 1 error(s)\n"


def test_a_journal_symlinked_to_a_regular_file_is_read_and_appended_to(
    run_cli, tmp_path
):
    """A journal symlink to a regular file is read and appended through.

    The link remains a link and all resulting records keep the adoption ID."""
    store = tmp_path / "store"
    store.mkdir()
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert run_cli("init", cwd=adopter).returncode == 0
    link = adopter / "journal.jsonl"
    kept = store / "journal.jsonl"
    link.rename(kept)
    link.symlink_to(kept)
    before = _records(kept)
    # A re-run over an untouched tree keeps every item and records nothing,
    # so one scaffold file is removed: its `create` is what proves the
    # append reached the store through the link.
    (adopter / "validated-memory.md").unlink()

    reported = run_cli("journal", cwd=adopter)
    again = run_cli("init", cwd=adopter)

    assert reported.returncode == 0, reported.stderr
    counted = f"journal: {len(before)} record(s)"
    assert counted in reported.stdout, reported.stdout
    assert again.returncode == 0, again.stderr
    after = _records(kept)
    assert link.is_symlink(), "the adopter's link was replaced"
    assert len(after) > len(before), "nothing was appended through the link"
    assert {entry["adoption"] for entry in after} == {before[0]["adoption"]}


def test_a_journal_that_is_a_broken_symlink_is_never_replaced(
    run_cli, tmp_path
):
    """Init refuses a broken journal symlink without replacing or following it."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    journal.unlink()
    journal.symlink_to(tmp_path / "nowhere" / "journal.jsonl")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "journal.jsonl" in result.stderr, result.stderr
    assert journal.is_symlink(), "the adopter's symlink was replaced"
    assert not journal.exists(), "the symlink was followed and written through"


# --- the transaction log, and the fault seam ------------------------------


def test_journal_check_reports_a_readable_open_transaction(run_cli, tmp_path):
    """A hand-written writer-shaped transaction is counted and named by --check.

    The fixture copies the schema manually; this test does not derive it from
    the writer."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    adoption = json.loads(journal.read_text(encoding="utf-8").splitlines()[0])[
        "adoption"
    ]

    transactions = tmp_path / ".validated-memory" / "transactions"
    transactions.mkdir(parents=True, exist_ok=True)
    entry = {
        "schema": 1,
        "at": "2026-09-01T00:00:00Z",
        "version": "1.6.0",
        "adoption": adoption,
        "run": "7777777777777777",
        "transaction": "aaaaaaaaaaaaaaaa",
        "intention": {
            "op": "replace",
            "purpose": "init",
            "path": "validated-memory.md",
            "durability": "repo",
        },
        "preimage": {"kind": "file", "digest": "sha256:" + "0" * 64, "mode": 420},
        "postimage": {"kind": "file", "digest": "sha256:" + "1" * 64, "mode": 420},
        "preimage_blob": "sha256:" + "0" * 64,
        "mode": 420,
        "stage": "prepared",
    }
    (transactions / "aaaaaaaaaaaaaaaa.json").write_text(
        json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8"
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        "open transaction aaaaaaaaaaaaaaaa (prepared) on validated-memory.md"
        in result.stderr
    ), result.stderr
    read_back = len(_records(journal))
    assert (
        f"journal: {read_back} record(s), 1 error(s)" in result.stdout
    ), result.stdout


def test_journal_check_reports_a_damaged_transaction_file(run_cli, tmp_path):
    """Invalid transaction JSON gates with its ID and no traceback."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    transactions = tmp_path / ".validated-memory" / "transactions"
    transactions.mkdir(parents=True, exist_ok=True)
    (transactions / "bbbbbbbbbbbbbbbb.json").write_text(
        "{not json", encoding="utf-8"
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "bbbbbbbbbbbbbbbb" in result.stderr, result.stderr
    assert "damaged" in result.stderr, result.stderr


def test_journal_reports_unresolved_transactions_only_when_nonzero(
    run_cli, tmp_path
):
    """Plain journal prints only nonzero unresolved counts without gating on them.

    Malformed journal logs can still gate; this isolates transaction-count
    reporting by damaging only a transaction file."""
    assert run_cli("init", cwd=tmp_path).returncode == 0

    clean = run_cli("journal", cwd=tmp_path)
    assert clean.returncode == 0, clean.stdout
    assert "unresolved transaction" not in clean.stdout, clean.stdout

    transactions = tmp_path / ".validated-memory" / "transactions"
    transactions.mkdir(parents=True, exist_ok=True)
    (transactions / "cccccccccccccccc.json").write_text(
        "{not json", encoding="utf-8"
    )

    result = run_cli("journal", cwd=tmp_path)

    assert result.returncode == 0, result.stdout
    assert "journal: 1 unresolved transaction(s)" in result.stdout, result.stdout


def _transactions(root):
    """Read transactions in lexicographic filename order."""
    directory = root / ".validated-memory" / "transactions"
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def test_a_kill_at_after_transaction_leaves_the_path_untouched(
    run_cli, tmp_path, monkeypatch
):
    """After-transaction kills leave a prepared create transaction.

    The path and history stay untouched, the fault seam exits 70, and a later
    run recovers exactly one pair. This proves the staged residue observed at
    the seam, not the fsync implementation itself."""
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-transaction")
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 70, (result.returncode, result.stdout, result.stderr)
    assert not (tmp_path / ".gitignore").exists()
    records = _records(tmp_path / "journal.jsonl")
    assert not [e for e in records if e["path"] == ".gitignore"], records

    open_transactions = _transactions(tmp_path)
    assert len(open_transactions) == 1, open_transactions
    entry = open_transactions[0]
    assert entry["stage"] == "prepared", entry
    assert entry["intention"]["path"] == ".gitignore", entry
    assert entry["intention"]["op"] == "create", entry

    # `os._exit` skips every `finally`, including `Lock.__exit__`, so the
    # lock file this run took is still there, with the pid of a process that
    # no longer exists inside it. It is left exactly where the kill left it:
    # `journal --check` reads and takes no lock, and the next run that does
    # take one breaks a lock whose owner is gone
    # (`test_a_lock_whose_owner_is_gone_is_broken_at_once`).
    assert (tmp_path / ".validated-memory" / "lock").exists()

    checked = run_cli("journal", "--check", cwd=tmp_path)
    assert checked.returncode == 1, checked.stdout
    assert ".gitignore" in checked.stderr, checked.stderr
    assert f"open transaction {entry['transaction']} (prepared)" in checked.stderr

    _recovers_to_exactly_one_pair(run_cli, tmp_path, ".gitignore", monkeypatch)


def test_a_kill_at_after_publish_leaves_bytes_the_transaction_has_not_claimed(
    run_cli, tmp_path, monkeypatch
):
    """After-publish kills leave new bytes with a prepared transaction.

    No history names the mutation; recovery classifies the postimage and
    produces exactly one record pair."""
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-publish")
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 70, (result.returncode, result.stdout, result.stderr)
    ignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "/.validated-memory/" in ignore, ignore
    records = _records(tmp_path / "journal.jsonl")
    assert not [e for e in records if e["path"] == ".gitignore"], records

    open_transactions = _transactions(tmp_path)
    assert len(open_transactions) == 1, open_transactions
    assert open_transactions[0]["stage"] == "prepared", open_transactions

    _recovers_to_exactly_one_pair(run_cli, tmp_path, ".gitignore", monkeypatch)


def test_a_caught_publish_marker_failure_recovers_exactly_once(
    run_cli, tmp_path, monkeypatch
):
    """A failed WAL marker retains the visible target for exact recovery."""
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "mark-published"
    )

    failed = run_cli("init", cwd=tmp_path)

    assert failed.returncode == 1, (
        failed.returncode,
        failed.stdout,
        failed.stderr,
    )
    assert "Traceback" not in failed.stderr, failed.stderr
    assert (
        "target barrier completed, but the published marker's durability "
        "is unconfirmed"
    ) in failed.stderr, failed.stderr
    assert "Nothing has been published" not in failed.stderr, failed.stderr
    ignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "/.validated-memory/" in ignore, ignore

    open_transactions = _transactions(tmp_path)
    assert len(open_transactions) == 1, open_transactions
    entry = open_transactions[0]
    transaction = entry["transaction"]
    assert entry["stage"] == "prepared", entry
    assert entry["unconfirmed"] == "target", entry
    assert entry["intention"]["path"] == ".gitignore", entry
    assert not [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record.get("transaction") == transaction
    ]

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    recovered = run_cli("init", cwd=tmp_path)

    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    assert not _transactions(tmp_path), _transactions(tmp_path)
    records = _records(tmp_path / "journal.jsonl")
    pair = [
        record
        for record in records
        if record.get("transaction") == transaction
    ]
    assert [record["stage"] for record in pair] == [
        "prepared",
        "committed",
    ], pair
    assert all(record["path"] == ".gitignore" for record in pair), pair

    again = run_cli("init", cwd=tmp_path)
    assert again.returncode == 0, (again.stdout, again.stderr)
    assert _records(tmp_path / "journal.jsonl") == records


def test_a_kill_at_after_history_leaves_records_the_transaction_outlived(
    run_cli, tmp_path, monkeypatch
):
    """After-history kills leave a published transaction and one history pair.

    Recovery removes the residue without duplicating either record."""
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-history")
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 70, (result.returncode, result.stdout, result.stderr)
    open_transactions = _transactions(tmp_path)
    assert len(open_transactions) == 1, open_transactions
    entry = open_transactions[0]
    assert entry["stage"] == "published", entry

    written = [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record.get("transaction") == entry["transaction"]
    ]
    assert {record["stage"] for record in written} == {
        "prepared",
        "committed",
    }, written

    _recovers_to_exactly_one_pair(run_cli, tmp_path, ".gitignore", monkeypatch)


def _recovers_to_exactly_one_pair(run_cli, tree, path, monkeypatch):
    """Recover `path` to one ID-matched pair and prove the next run adds none."""
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT", raising=False)
    recovered = run_cli("init", cwd=tree)
    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    assert not _transactions(tree), _transactions(tree)

    checked = run_cli("journal", "--check", cwd=tree)
    assert checked.returncode == 0, (checked.stdout, checked.stderr)

    records = _records(tree / "journal.jsonl")
    pair = [
        record
        for record in records
        if record["path"] == path and record["op"] != "observe"
    ]
    assert len(pair) == 2, pair
    assert {record["stage"] for record in pair} == {"prepared", "committed"}, pair
    # Asserted before the comparison, so two records carrying no id at all
    # cannot pass this as agreement.
    assert pair[0]["transaction"], pair
    assert pair[0]["transaction"] == pair[1]["transaction"], pair

    again = run_cli("init", cwd=tree)
    assert again.returncode == 0, again.stderr
    assert _records(tree / "journal.jsonl") == records
    return records


def test_legacy_claimless_recovery_completes_a_prepared_half(
    run_cli, tmp_path, monkeypatch
):
    """Released claimless recovery still appends its missing committed half.

    The rebuilt pair preserves stage order plus the original transaction and
    run IDs."""
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-history")
    assert run_cli("init", cwd=tmp_path).returncode == 70
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
    entry = _transactions(tmp_path)[0]

    journal = tmp_path / "journal.jsonl"
    lines = journal.read_text(encoding="utf-8").splitlines(keepends=True)
    last = json.loads(lines[-1])
    assert (last["path"], last["stage"]) == (".gitignore", "committed"), last
    journal.write_text("".join(lines[:-1]), encoding="utf-8")
    transaction_path = next(
        (tmp_path / ".validated-memory" / "transactions").glob("*.json")
    )
    claimless = json.loads(transaction_path.read_text(encoding="utf-8"))
    claimless.pop("history_append")
    claimless.pop("history_timestamps")
    transaction_path.write_text(
        json.dumps(claimless, sort_keys=True) + "\n", encoding="utf-8"
    )

    recovered = _recovers_to_exactly_one_pair(
        run_cli, tmp_path, ".gitignore", monkeypatch
    )
    rebuilt = [
        record
        for record in recovered
        if record["path"] == ".gitignore" and record["op"] != "observe"
    ]
    # The half that survived and the half that was appended are one act,
    # and both belong to the run that wrote the bytes.
    assert [record["stage"] for record in rebuilt] == [
        "prepared",
        "committed",
    ], rebuilt
    assert [record["transaction"] for record in rebuilt] == [
        entry["transaction"],
        entry["transaction"],
    ], rebuilt
    assert [record["run"] for record in rebuilt] == [
        entry["run"],
        entry["run"],
    ], (rebuilt, entry)


def test_init_announces_a_recovery_only_when_the_history_gained_one(
    run_cli, tmp_path, monkeypatch
):
    """Init announces recovery only when it adds missing history.

    Both published and already-recorded residues are removed and leave
    --check clean."""
    published = tmp_path / "published"
    history = tmp_path / "history"
    published.mkdir()
    history.mkdir()

    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-published")
    assert run_cli("init", cwd=published).returncode == 70
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-history")
    assert run_cli("init", cwd=history).returncode == 70
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")

    announced = run_cli("init", cwd=published)
    silent = run_cli("init", cwd=history)

    assert announced.returncode == 0, (announced.stdout, announced.stderr)
    assert silent.returncode == 0, (silent.stdout, silent.stderr)
    assert (
        "init: recovered .gitignore from transaction" in announced.stdout
    ), announced.stdout
    assert "recovered" not in silent.stdout, silent.stdout
    # Both are closed either way: what differs is what was said about them.
    assert not _transactions(published), _transactions(published)
    assert not _transactions(history), _transactions(history)
    for tree in (published, history):
        assert run_cli("journal", "--check", cwd=tree).returncode == 0


def test_a_kill_at_after_published_leaves_bytes_with_no_history(
    run_cli, tmp_path, monkeypatch
):
    """After-published kills leave bytes plus a published transaction, no history.

    The shared recovery check then pins exactly one resulting record pair."""
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-published")
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 70, (result.returncode, result.stdout, result.stderr)
    ignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "/.validated-memory/" in ignore, ignore
    records = _records(tmp_path / "journal.jsonl")
    assert not [e for e in records if e["path"] == ".gitignore"], records

    open_transactions = _transactions(tmp_path)
    assert len(open_transactions) == 1, open_transactions
    entry = open_transactions[0]
    assert entry["stage"] == "published", entry
    assert entry["intention"]["path"] == ".gitignore", entry

    # Left where the kill left it, as in the sibling test above: nothing
    # removes a dead owner's lock by hand, and the next run to take one
    # breaks it.
    assert (tmp_path / ".validated-memory" / "lock").exists()

    checked = run_cli("journal", "--check", cwd=tmp_path)
    assert checked.returncode == 1, checked.stdout
    assert ".gitignore" in checked.stderr, checked.stderr
    assert f"open transaction {entry['transaction']} (published)" in checked.stderr

    recovered = _recovers_to_exactly_one_pair(
        run_cli, tmp_path, ".gitignore", monkeypatch
    )
    # The two records recovery rebuilt are filed under the run that wrote
    # the bytes, not the run that found them: the mutation happened in the
    # run the kill ended, and a record saying otherwise would put a write
    # in a session that performed none.
    rebuilt = [
        item
        for item in recovered
        if item["path"] == ".gitignore" and item["op"] != "observe"
    ]
    assert rebuilt[0]["run"] == entry["run"], (rebuilt, entry)
    assert rebuilt[0]["transaction"] == entry["transaction"], (rebuilt, entry)


def test_the_fault_and_sleep_variables_are_inert_when_unset_or_unreached(
    run_cli, tmp_path, monkeypatch
):
    """Only the selected sleeping seam adds one bounded pause.

    Three equivalent fresh adopters distinguish no pause, an unreached
    selector and one selected pause. Outputs match exactly. Journal comparison
    removes timestamps and minted IDs, so it proves equal operation data rather
    than byte-identical logs."""
    baseline = tmp_path / "baseline"
    unreached = tmp_path / "unreached"
    selected = tmp_path / "selected"
    baseline.mkdir()
    unreached.mkdir()
    selected.mkdir()

    def _timed_init(root):
        started = time.monotonic()
        result = run_cli("init", cwd=root)
        return result, time.monotonic() - started

    monkeypatch.delenv("VALIDATED_MEMORY_FAULT", raising=False)
    monkeypatch.delenv("VALIDATED_MEMORY_TEST_SEAM", raising=False)
    baseline_initial, baseline_elapsed = _timed_init(baseline)
    monkeypatch.setenv("VALIDATED_MEMORY_TEST_SEAM", "not-a-seam")
    unreached_initial, unreached_elapsed = _timed_init(unreached)
    monkeypatch.setenv("VALIDATED_MEMORY_TEST_SEAM", "during-lock")
    selected_initial, selected_elapsed = _timed_init(selected)
    monkeypatch.delenv("VALIDATED_MEMORY_TEST_SEAM")

    assert baseline_initial.returncode == 0, baseline_initial.stderr
    assert unreached_initial.returncode == 0, unreached_initial.stderr
    assert selected_initial.returncode == 0, selected_initial.stderr
    assert baseline_initial.stdout == unreached_initial.stdout
    assert baseline_initial.stdout == selected_initial.stdout
    assert baseline_initial.stderr == unreached_initial.stderr
    assert baseline_initial.stderr == selected_initial.stderr

    # Ordinary subprocess and filesystem variance may move either control,
    # so compare the selected run with the slower one. The lower bound rejects
    # an absent/unconditional pause; the upper bound rejects two fixed pauses.
    assert abs(unreached_elapsed - baseline_elapsed) < 1.5, (
        baseline_elapsed,
        unreached_elapsed,
    )
    selected_increment = selected_elapsed - max(
        baseline_elapsed, unreached_elapsed
    )
    assert 1.25 <= selected_increment < 3.5, (
        baseline_elapsed,
        unreached_elapsed,
        selected_elapsed,
    )
    selected_transactions = {
        entry["transaction"]
        for entry in _records(selected / "journal.jsonl")
        if entry.get("transaction")
    }
    assert len(selected_transactions) > 1, selected_transactions

    control = run_cli("init", cwd=baseline)

    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-transaction")
    faulted = run_cli("init", cwd=unreached)

    assert control.returncode == 0, control.stderr
    assert faulted.returncode == 0, faulted.stderr
    assert control.stdout == faulted.stdout
    assert control.stderr == faulted.stderr
    selected_control = run_cli("init", cwd=selected)
    assert selected_control.returncode == 0, selected_control.stderr
    assert control.stdout == selected_control.stdout
    assert control.stderr == selected_control.stderr

    def _stripped(path):
        # Normalize timestamps and minted identities. The comparison pins all
        # remaining operation fields, not byte-identical journal output.
        return [
            {
                k: v
                for k, v in entry.items()
                if k not in ("at", "adoption", "run", "transaction")
            }
            for entry in _records(path)
        ]

    assert _stripped(baseline / "journal.jsonl") == _stripped(
        unreached / "journal.jsonl"
    )
    assert _stripped(baseline / "journal.jsonl") == _stripped(
        selected / "journal.jsonl"
    )


def test_the_sleeping_seam_is_separate_from_the_four_crash_points():
    """The private delay cannot expand the hard-crash protocol vocabulary."""
    fault_path = REPO_ROOT / "validated_memory" / "journal" / "fault.py"
    fault_source = fault_path.read_text(encoding="utf-8")
    fault_tree = ast.parse(fault_source)
    crash_assignment = next(
        node
        for node in fault_tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "FAULT_POINTS"
            for target in node.targets
        )
    )
    assert ast.literal_eval(crash_assignment.value) == (
        "after-transaction",
        "after-publish",
        "after-published",
        "after-history",
    )

    readers = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "validated_memory").rglob("*.py")
        if "VALIDATED_MEMORY_TEST_SEAM" in path.read_text(encoding="utf-8")
    ]
    assert readers == ["validated_memory/journal/fault.py"]

    executor = ast.parse(
        (REPO_ROOT / "validated_memory" / "journal" / "executor.py").read_text(
            encoding="utf-8"
        )
    )
    sleep_calls = [
        node
        for node in ast.walk(executor)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "sleep_at"
    ]
    assert len(sleep_calls) == 1
    assert ast.literal_eval(sleep_calls[0].args[0]) == "during-lock"
    run_class = next(
        node
        for node in executor.body
        if isinstance(node, ast.ClassDef) and node.name == "Run"
    )
    execute = next(
        node
        for node in run_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "_execute"
    )

    def _named_calls(name):
        return [
            node
            for node in ast.walk(execute)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
        ]

    opened = _named_calls("open_transaction")
    assert len(opened) == 1
    rereads = [
        node
        for node in _named_calls("current_state")
        if node.lineno > opened[0].lineno
    ]
    assert len(rereads) == 1
    assert opened[0].end_lineno < sleep_calls[0].lineno
    assert sleep_calls[0].end_lineno < rereads[0].lineno


# --- the executor: what it refuses, what it preserves, what it records ---------


def test_a_read_only_file_is_refused_and_left_exactly_as_it_was(run_cli, tmp_path):
    """A read-only target gates with exact bytes and mode left unchanged.

    Refusal writes no target record, transaction or preimage."""
    before = "build/\n"
    ignore = tmp_path / ".gitignore"
    ignore.write_text(before, encoding="utf-8")
    ignore.chmod(0o444)

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert ".gitignore" in result.stderr, result.stderr
    # The whole sentence, because a refusal that does not say what was left
    # alone is a refusal the reader cannot act on. The mode is printed with
    # four digits so that a mode below 0o100 reads as `0040` and not `040`;
    # no path `init` can reach produces one, since a file the process cannot
    # read never gets this far, so the width is a guard rather than a
    # behaviour this test can drive.
    assert (
        ".gitignore is mode 0444, which denies writing to this user. "
        "Nothing has been written." in result.stderr
    ), result.stderr
    assert ignore.read_text(encoding="utf-8") == before
    assert oct(ignore.stat().st_mode & 0o777) == "0o444"
    # No target record, transaction file or preimage accompanies the refusal.
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == ".gitignore"
    ]
    assert not _transactions(tmp_path)
    assert not (tmp_path / ".validated-memory" / "preimages").exists()


def test_a_writable_file_keeps_the_mode_the_adopter_gave_it(run_cli, tmp_path):
    """A writable target keeps its mode in both the filesystem and record."""
    ignore = tmp_path / ".gitignore"
    ignore.write_text("build/\n", encoding="utf-8")
    ignore.chmod(0o640)

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "/.validated-memory/" in ignore.read_text(encoding="utf-8")
    assert oct(ignore.stat().st_mode & 0o777) == "0o640"
    # And the mode it kept is what the record says it kept, so a reversal
    # has something to restore.
    committed = next(
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == ".gitignore" and entry["stage"] == "committed"
    )
    assert committed["mode"] == 0o640, committed


def test_a_plain_file_where_a_directory_goes_is_refused_not_kept(run_cli, tmp_path):
    """A file cannot satisfy a directory creation precondition.

    Init leaves the bytes unchanged and writes no observation or transaction."""
    placeholder = tmp_path / "memory"
    placeholder.write_text("notes\n", encoding="utf-8")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "memory: create" in result.stderr, result.stderr
    assert "expects it to be absent" in result.stderr, result.stderr
    assert "init: kept memory" not in result.stdout, result.stdout
    assert placeholder.read_text(encoding="utf-8") == "notes\n"
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == "memory"
    ]
    assert not _transactions(tmp_path)


def test_both_records_of_one_mutation_carry_one_transaction_id(run_cli, tmp_path):
    """Each mutation has one nonempty ID shared by its two history halves.

    IDs remain distinct across mutations, modes are recorded, and no open
    transaction remains."""
    assert run_cli("init", cwd=tmp_path).returncode == 0

    records = [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["op"] != "observe"
    ]
    assert records, "init recorded no mutation"
    by_stage = {}
    for entry in records:
        by_stage.setdefault(entry["path"], {})[entry["stage"]] = entry
    for path, stages in by_stage.items():
        assert set(stages) == {"prepared", "committed"}, (path, stages)
        assert stages["prepared"]["transaction"], (path, stages)
        assert (
            stages["prepared"]["transaction"] == stages["committed"]["transaction"]
        ), (path, stages)
        # And the mode the path ended up with, on both halves.
        assert isinstance(stages["committed"]["mode"], int), stages

    # One id per mutation, never one per run: two mutations of one run that
    # shared an id could not be told apart either.
    ids = {stages["committed"]["transaction"] for stages in by_stage.values()}
    assert len(ids) == len(by_stage), by_stage

    # Every transaction the run opened was resolved, so none is left behind.
    assert not _transactions(tmp_path)
    assert run_cli("journal", "--check", cwd=tmp_path).returncode == 0


def test_a_path_left_by_an_open_transaction_is_never_observed_as_pre_existing(
    run_cli, tmp_path, monkeypatch
):
    """An open transaction prevents its published path becoming an observation.

    The fixture ensures history has no record from which to infer ownership."""
    (tmp_path / ".gitignore").write_text("/.validated-memory/\n", encoding="utf-8")

    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-published")
    killed = run_cli("init", cwd=tmp_path)
    assert killed.returncode == 70, (killed.stdout, killed.stderr)
    assert (tmp_path / "knowledge").is_dir()
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == "knowledge"
    ]

    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "init: kept knowledge" in result.stdout, result.stdout
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["op"] == "observe" and entry["path"] == "knowledge"
    ], _records(tmp_path / "journal.jsonl")


def test_a_corrupt_preimage_blob_is_replaced_rather_than_wedging_the_run(
    run_cli, tmp_path
):
    """Parking replaces a blob whose content does not match its digest name.

    The mutation completes against the repaired blob and leaves no temporary."""
    import hashlib

    before = "build/\n"
    ignore = tmp_path / ".gitignore"
    ignore.write_text(before, encoding="utf-8")
    reference = hashlib.sha256(before.encode("utf-8")).hexdigest()
    preimages = tmp_path / ".validated-memory" / "preimages"
    preimages.mkdir(parents=True)
    blob = preimages / reference
    blob.write_text("not the preimage at all\n", encoding="utf-8")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert blob.read_text(encoding="utf-8") == before
    # The mutation the preimage was taken for went through and is recorded
    # against the blob that now holds the true bytes.
    committed = next(
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == ".gitignore" and entry["stage"] == "committed"
    )
    assert committed["preimage"] == f"sha256:{reference}", committed
    # No pid-named temporary is left in the store either.
    assert sorted(p.name for p in preimages.iterdir()) == [reference]


def test_verified_preimage_staging_corruption_preserves_every_input(
    run_cli, tmp_path, monkeypatch
):
    """Same-inode corruption is rejected before a preimage becomes canonical."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    target = tmp_path / ".gitignore"
    original = b"known adopter bytes\n"
    target.write_bytes(original)
    target.chmod(0o640)
    target_identity = target.stat().st_ino
    digest_name = hashlib.sha256(original).hexdigest()
    preimages = tmp_path / ".validated-memory" / "preimages"
    preimages.mkdir(exist_ok=True)
    canonical = preimages / digest_name

    def vault_state():
        state = []
        vault = tmp_path / ".validated-memory"
        for path in sorted(vault.rglob("*")):
            relative = path.relative_to(vault).as_posix()
            if path.is_symlink():
                state.append((relative, "symlink", os.readlink(path)))
            elif path.is_dir():
                state.append((relative, "directory", None))
            else:
                state.append(
                    (
                        relative,
                        "file",
                        path.read_bytes(),
                        stat.S_IMODE(path.stat().st_mode),
                    )
                )
        return state

    repo_history = (tmp_path / "journal.jsonl").read_bytes()
    local_history_path = tmp_path / ".validated-memory" / "local.jsonl"
    local_history = (
        local_history_path.read_bytes() if local_history_path.exists() else None
    )
    before_vault = vault_state()
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        f"corrupt-staging:{digest_name}",
    )

    failed = run_cli("init", cwd=tmp_path)

    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    assert "Traceback" not in failed.stderr, failed.stderr
    assert "could not be parked" in failed.stderr, failed.stderr
    assert target.read_bytes() == original
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert target.stat().st_ino == target_identity
    assert (tmp_path / "journal.jsonl").read_bytes() == repo_history
    assert (
        local_history_path.read_bytes() if local_history_path.exists() else None
    ) == local_history
    assert vault_state() == before_vault
    assert not canonical.exists()
    assert not list(preimages.glob(f".{digest_name}.*.tmp"))
    assert not _transactions(tmp_path)
    assert not (tmp_path / ".validated-memory" / "lock").exists()

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    retried = run_cli("init", cwd=tmp_path)

    assert retried.returncode == 0, (retried.stdout, retried.stderr)
    assert canonical.read_bytes() == original
    pair = [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record["path"] == ".gitignore" and record["op"] == "append"
    ]
    assert [record["stage"] for record in pair] == ["prepared", "committed"]
    assert [record["preimage"] for record in pair] == [
        f"sha256:{digest_name}",
        f"sha256:{digest_name}",
    ]
    checked = run_cli("journal", "--check", cwd=tmp_path)
    assert checked.returncode == 0, (checked.stdout, checked.stderr)


def test_verified_staging_corruption_is_after_fsync_and_before_verification():
    """The corruption seam mutates only verifier-bound, already-flushed staging."""
    source = (
        REPO_ROOT / "validated_memory" / "journal" / "durable.py"
    ).read_text(encoding="utf-8")
    installer = source.split("def install_bytes(", 1)[1].split("\ndef ", 1)[0]
    initial_fsync = installer.index("os.fsync(handle.fileno())")
    mode = installer.index("_chmod_staging(descriptor, temporary, mode)")
    verify_branch = installer.index("if verify is not None:")
    corruption = installer.index(
        "_corrupt_staging_for_test(path, descriptor, data)"
    )
    verification = installer.index("verify(temporary)")
    assert initial_fsync < mode < verify_branch < corruption < verification


def test_a_symlink_where_a_directory_goes_is_refused_not_kept(run_cli, tmp_path):
    """A symlink to a file cannot satisfy a directory creation precondition.

    Init preserves the link and target and writes no record or transaction."""
    (tmp_path / "elsewhere.md").write_text("notes\n", encoding="utf-8")
    (tmp_path / "memory").symlink_to(tmp_path / "elsewhere.md")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "memory: create" in result.stderr, result.stderr
    assert "expects it to be absent" in result.stderr, result.stderr
    assert "init: kept memory" not in result.stdout, result.stdout
    assert (tmp_path / "memory").is_symlink()
    assert (tmp_path / "elsewhere.md").read_text(encoding="utf-8") == "notes\n"
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == "memory"
    ]
    assert not _transactions(tmp_path)


def test_the_state_is_re_read_immediately_before_publishing(run_cli, tmp_path):
    """A raced write before the pre-publication re-read aborts the mutation.

    The transaction is observable only after the initial state is captured;
    the bounded test pause then keeps the run before its second read. This
    proves refusal at that seam, not its width or the smaller race between the
    re-read and publication."""
    ignore = tmp_path / ".gitignore"
    ignore.write_text("build/\n", encoding="utf-8")
    intruder_text = "build/\ndist/\n"

    running = _run_init_in_background(tmp_path, test_seam="during-lock")
    try:
        transaction = _wait_for_transaction(running, tmp_path, ".gitignore")
        temporary = ignore.with_suffix(".intruder")
        temporary.write_text(intruder_text, encoding="utf-8")
        os.replace(temporary, ignore)
        stdout, stderr = running.communicate(timeout=30)
    finally:
        if running.poll() is None:  # pragma: no cover - only on a timeout
            running.kill()
            running.communicate()

    assert transaction["stage"] == "prepared", transaction
    assert running.returncode == 1, (stdout, stderr)
    assert ".gitignore" in stderr, stderr
    assert "changed while its mutation was being prepared" in stderr
    # The intruder's bytes, exactly: not the original, and not the original
    # with the ignore entry appended to it.
    assert ignore.read_text(encoding="utf-8") == intruder_text
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == ".gitignore"
    ]
    # The transaction was opened, so it is closed `aborted` and removed --
    # not left open for a recovery that has nothing to recover.
    assert not _transactions(tmp_path)


def test_a_path_that_stops_being_readable_before_publication_aborts(
    run_cli, tmp_path
):
    """An unreadable pre-publication re-read gates cleanly and aborts its transaction.

    The transaction rendezvous reaches only the post-parking seam; it does not
    prove fsync ordering or exclude the remaining re-read/publication race."""
    ignore = tmp_path / ".gitignore"
    ignore.write_text("build/\n", encoding="utf-8")

    running = _run_init_in_background(tmp_path, test_seam="during-lock")
    try:
        _wait_for_transaction(running, tmp_path, ".gitignore")
        ignore.chmod(0o000)
        stdout, stderr = running.communicate(timeout=30)
    finally:
        ignore.chmod(0o644)
        if running.poll() is None:  # pragma: no cover - only on a timeout
            running.kill()
            running.communicate()

    assert running.returncode == 1, (stdout, stderr)
    assert "Traceback" not in stderr, stderr
    assert ".gitignore" in stderr, stderr
    assert (
        "could not be read while its mutation was being prepared"
        in stderr
    ), stderr
    assert not [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry["path"] == ".gitignore"
    ]
    # Opened, so closed `aborted` and removed -- never left for a recovery
    # that has nothing to recover.
    assert not _transactions(tmp_path)


def test_a_creation_publishes_with_o_excl_rather_than_replacing():
    """Structurally pin exclusive creation and private staging permissions.

    The syscall-sized no-replace window is not reachable through the CLI
    seam, so assertions locate the sole publisher and inspect its flags."""
    # Found across the write path rather than opened by name, and asserted to
    # be exactly one: the guarantee below is a property of THE function that
    # publishes, and a second definition of it -- in another module of the
    # journal, added later -- would be a second answer to the same question,
    # with this pin green against whichever of the two it happened to read.
    publishers = [
        (relative, node)
        for relative in sorted(RAW_WRITE_MODULES)
        for node in ast.walk(
            ast.parse(
                (REPO_ROOT / "validated_memory" / relative).read_text(
                    encoding="utf-8"
                )
            )
        )
        if isinstance(node, ast.FunctionDef) and node.name == "_publish"
    ]
    assert len(publishers) == 1, [relative for relative, _ in publishers]
    publish = publishers[0][1]
    exclusive_calls = [
        node
        for node in ast.walk(publish)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "create_exclusive"
    ]
    assert len(exclusive_calls) == 1
    assert ast.unparse(exclusive_calls[0].args[0]) == "target"

    durable_tree = ast.parse(
        (REPO_ROOT / "validated_memory" / "journal" / "durable.py").read_text(
            encoding="utf-8"
        )
    )
    exclusive = next(
        node
        for node in durable_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_exclusive"
    )
    opens = [
        node
        for node in ast.walk(exclusive)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "os.open"
        and node.args
    ]
    published = [node for node in opens if ast.unparse(node.args[0]) == "path"]
    assert len(published) == 1, [ast.unparse(node) for node in opens]
    flags = ast.unparse(published[0].args[1])
    assert "os.O_CREAT" in flags and "os.O_EXCL" in flags, (
        "a creation must be published with os.O_CREAT | os.O_EXCL, which "
        f"fails when the name is taken; this one opens with {flags}"
    )

    # And the temporary a replacement is built in is created 0600, so an
    # adopter's bytes are never briefly readable by anyone the target's own
    # mode excludes. Same kind of guarantee, same reason it is pinned here:
    # the window is the length of one write.
    installer = next(
        node
        for node in durable_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "install_bytes"
    )
    publish_opens = [
        node
        for node in ast.walk(installer)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "os.open"
        and node.args
    ]
    staged = [
        node
        for node in publish_opens
        if ast.unparse(node.args[0]) == "temporary"
    ]
    assert len(staged) == 1, [ast.unparse(node) for node in opens]
    assert ast.literal_eval(staged[0].args[2]) == 0o600, ast.unparse(staged[0])


# --- the lock: who holds it, who may break it, and where it lives -------------

# A lock holder that is not this plugin: it takes the lock file exactly as
# `Lock` does -- `O_CREAT | O_EXCL`, its own pid inside -- announces the pid
# it wrote, and then stays alive until the test kills it. Standard library
# only, and it imports nothing from the package: these tests drive the CLI
# from the outside and this holder is part of the outside.
HOLD_THE_LOCK = """
import os
import sys
import time

path = sys.argv[1]
os.makedirs(os.path.dirname(path), exist_ok=True)
descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
os.write(descriptor, ("%d\\n" % os.getpid()).encode("ascii"))
os.close(descriptor)
print(os.getpid(), flush=True)
time.sleep(600)
"""


def _hold_the_lock(path):
    """Start the holder above on `path` and return it, already holding."""
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_THE_LOCK, str(path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    pid = holder.stdout.readline().strip()
    assert pid, "the lock holder died before it took the lock"
    return holder, pid


def _a_pid_that_is_gone():
    """Return a probed-unused PID, starting with a reaped child."""
    child = subprocess.Popen([sys.executable, "-c", ""])
    child.wait(timeout=30)
    reaped = child.pid
    for offset in range(10000):
        candidate = reaped + offset
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except OSError:
            continue
    raise AssertionError("every probed pid was in use")


def _run_init_in_background(cwd, *, test_seam=None):
    """Start `init` as a subprocess the test can interfere with while it runs."""
    environment = dict(os.environ)
    environment.setdefault("PYTHONPATH", str(REPO_ROOT))
    if test_seam is not None:
        environment["VALIDATED_MEMORY_TEST_SEAM"] = test_seam
    return subprocess.Popen(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=environment,
    )


def _wait_for_transaction(process, root, path):
    """Return the durable prepared transaction for `path`, within a bound."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        matches = [
            entry
            for entry in _transactions(root)
            if entry["intention"]["path"] == path
        ]
        if matches:
            assert len(matches) == 1, matches
            return matches[0]
        assert process.poll() is None, (
            "the run ended before opening its transaction"
        )
        time.sleep(0.005)
    raise AssertionError(f"the run never opened a transaction for {path}")


def test_an_adopting_run_holds_and_releases_its_lock(run_cli, tmp_path):
    """The adopting scope and its nested session operations share one lock."""
    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert "another validated-memory process holds" not in result.stderr
    assert (tmp_path / "journal.jsonl").exists()
    # Taken and released, not leaked: the next run must not have to break it.
    assert not (tmp_path / ".validated-memory" / "lock").exists()


def test_a_lock_whose_owner_is_alive_is_never_broken(run_cli, tmp_path):
    """A live owner keeps an old lock until init refuses contention.

    The refusal identifies the lock and remedy, preserves its inode and PID,
    and creates neither journal.jsonl nor .gitignore."""
    lock = tmp_path / ".validated-memory" / "lock"
    holder, pid = _hold_the_lock(lock)
    try:
        ancient = time.time() - 100 * 300
        os.utime(lock, (ancient, ancient))
        before = lock.stat().st_ino
        tree_before = _final_tree_snapshot(tmp_path)

        result = run_cli("init", cwd=tmp_path)

        assert result.returncode == 1, (result.stdout, result.stderr)
        assert "another validated-memory process holds" in result.stderr
        assert os.path.realpath(lock) in result.stderr, result.stderr
        # An alive pid is never broken, and a pid the system has since
        # handed to something else is still alive: the message has to say
        # what an operator can do about a lock nothing will ever release.
        assert (
            f"if no validated-memory process is running, delete "
            f"{os.path.realpath(lock)}" in result.stderr
        ), result.stderr
        # The finding names the lock itself. Inside the root, that is the
        # relative path a reader can act on directly.
        assert result.stderr.startswith(
            "ERROR: .validated-memory/lock: journal: "
        ), result.stderr
        # The holder's own file, untouched: same inode, same pid inside.
        assert lock.stat().st_ino == before
        assert lock.read_text(encoding="ascii").strip() == pid
        assert _final_tree_snapshot(tmp_path) == tree_before
        # The refused run created neither the journal nor the ignore file.
        assert not (tmp_path / "journal.jsonl").exists()
        assert not (tmp_path / ".gitignore").exists()
    finally:
        holder.terminate()
        holder.wait(timeout=30)
        holder.stdout.close()


def test_a_lock_whose_owner_is_gone_is_broken_at_once(run_cli, tmp_path):
    """A fresh lock whose probed PID is gone is broken without waiting."""
    lock = tmp_path / ".validated-memory" / "lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(f"{_a_pid_that_is_gone()}\n", encoding="ascii")

    started = time.monotonic()
    result = run_cli("init", cwd=tmp_path)
    elapsed = time.monotonic() - started

    assert result.returncode == 0, (result.stdout, result.stderr)
    # Well inside the ten-second deadline: broken on the first attempt, not
    # waited on and then given up.
    assert elapsed < 5, elapsed
    assert (tmp_path / "journal.jsonl").exists()
    assert not lock.exists()


def test_a_run_whose_lock_was_broken_leaves_its_successor_alone(tmp_path):
    """A run releases only the inode it acquired, preserving a successor lock.

    A durable transaction proves the run holds its outer lock and has reached
    the bounded pause before the test swaps the lock file."""
    seed = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        check=False,
    )
    assert seed.returncode == 0, seed.stderr
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")

    lock = tmp_path / ".validated-memory" / "lock"
    assert not lock.exists()
    running = _run_init_in_background(tmp_path, test_seam="during-lock")
    try:
        transaction = _wait_for_transaction(running, tmp_path, ".gitignore")
        assert lock.exists(), "the run opened a transaction without its lock"
        broken = lock.stat().st_ino
        lock.unlink()
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        successor = lock.stat().st_ino
        assert successor != broken
        assert running.poll() is None, "the run finished before the lock was swapped"
        stdout, stderr = running.communicate(timeout=120)
    finally:
        if running.poll() is None:  # pragma: no cover - only on a timeout
            running.kill()
            running.communicate()

    assert running.returncode == 0, (stdout, stderr)
    assert transaction["stage"] == "prepared", transaction
    assert lock.exists(), "the run deleted a lock it no longer owned"
    assert lock.stat().st_ino == successor
    assert lock.read_text(encoding="ascii").strip() == str(os.getpid())


def test_two_trees_sharing_one_journal_take_one_lock(run_cli, tmp_path):
    """Two successful shared-journal mutations serialize at one store lock."""
    store = tmp_path / "store"
    first = tmp_path / "first"
    second = tmp_path / "second"
    for tree in (store, first, second):
        tree.mkdir()

    for tree in (first, second):
        seeded = run_cli("init", cwd=tree)
        assert seeded.returncode == 0, (tree, seeded.stdout, seeded.stderr)
    (first / "journal.jsonl").rename(store / "journal.jsonl")
    for tree in (first, second):
        journal = tree / "journal.jsonl"
        journal.unlink(missing_ok=True)
        journal.symlink_to(Path("..") / "store" / "journal.jsonl")
        (tree / ".gitignore").write_bytes(b"build/\n")

    shared_lock = store / ".validated-memory" / "lock"
    first_run = None
    second_run = None
    try:
        first_run = _run_init_in_background(first, test_seam="during-lock")
        first_transaction = _wait_for_transaction(
            first_run, first, ".gitignore"
        )
        assert shared_lock.read_text(encoding="ascii").strip() == str(
            first_run.pid
        )
        assert not (first / ".validated-memory" / "lock").exists()
        assert not (second / ".validated-memory" / "lock").exists()

        second_run = _run_init_in_background(second, test_seam="during-lock")
        second_transaction = _wait_for_transaction(
            second_run, second, ".gitignore"
        )
        assert not _transactions(first), (
            "the second WAL opened before the first WAL was removed"
        )
        shared_records = _records(store / "journal.jsonl")
        first_pair = [
            (index, record)
            for index, record in enumerate(shared_records)
            if record.get("transaction") == first_transaction["transaction"]
        ]
        assert len(first_pair) == 2, first_pair
        assert [record["stage"] for _, record in first_pair] == [
            "prepared",
            "committed",
        ]
        assert first_pair[1][0] == first_pair[0][0] + 1, first_pair
        assert shared_lock.read_text(encoding="ascii").strip() == str(
            second_run.pid
        )
        first_stdout, first_stderr = first_run.communicate(timeout=30)
        second_stdout, second_stderr = second_run.communicate(timeout=30)
    finally:
        for process in (first_run, second_run):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()

    assert first_run.returncode == 0, (first_stdout, first_stderr)
    assert second_run.returncode == 0, (second_stdout, second_stderr)
    assert first_stderr == ""
    assert second_stderr == ""
    update = "init: ignored /.validated-memory/ in .gitignore"
    assert first_stdout.count(update) == 1, first_stdout
    assert second_stdout.count(update) == 1, second_stdout
    assert first_transaction["transaction"] != second_transaction["transaction"]
    assert first_transaction["run"] != second_transaction["run"]

    expected = b"""build/

# The validated-memory vault: preimages, and the records of mutations whose
# path leaves the repository. Always local to this clone (ADR 0008), which is
# why `init` writes this entry itself rather than the adoption questionnaire
# asking for it.
/.validated-memory/
"""
    assert (first / ".gitignore").read_bytes() == expected
    assert (second / ".gitignore").read_bytes() == expected
    records = [
        record
        for record in _records(store / "journal.jsonl")
        if record["path"] == ".gitignore" and record["op"] == "append"
    ]
    assert [record["stage"] for record in records] == [
        "prepared",
        "committed",
        "prepared",
        "committed",
    ]
    transactions = [record["transaction"] for record in records]
    assert transactions == [
        first_transaction["transaction"],
        first_transaction["transaction"],
        second_transaction["transaction"],
        second_transaction["transaction"],
    ]
    assert len({record["run"] for record in records}) == 2
    preimage = "sha256:" + hashlib.sha256(b"build/\n").hexdigest()
    postimage = "sha256:" + hashlib.sha256(expected).hexdigest()
    assert {record["preimage"] for record in records} == {preimage}
    assert {record["postimage"] for record in records} == {postimage}

    assert not _transactions(first)
    assert not _transactions(second)
    assert not shared_lock.exists()
    assert not (first / ".validated-memory" / "lock").exists()
    assert not (second / ".validated-memory" / "lock").exists()
    assert not list(first.glob("..gitignore.*.tmp"))
    assert not list(second.glob("..gitignore.*.tmp"))
    for tree in (first, second):
        checked = run_cli("journal", "--check", cwd=tree)
        assert checked.returncode == 0, (tree, checked.stdout, checked.stderr)


def test_a_broken_journal_symlink_locks_inside_the_root(run_cli, tmp_path):
    """A broken journal symlink keeps locking inside the adopter root.

    The identity transition preserves it without creating its target parent."""
    root = tmp_path / "adopter"
    elsewhere = tmp_path / "elsewhere"
    root.mkdir()
    (root / "journal.jsonl").symlink_to(elsewhere / "journal.jsonl")

    result = run_cli("init", cwd=root)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "symlink" in result.stderr, result.stderr
    assert not elsewhere.exists(), "the lock was taken outside the adopter root"
    assert (root / ".validated-memory").is_dir()


def test_a_journal_symlink_that_cannot_be_resolved_refuses_cleanly(
    run_cli, tmp_path
):
    """A journal symlink loop gates as an unreadable journal without traceback."""
    (tmp_path / "journal.jsonl").symlink_to("journal.jsonl")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert "journal could not be read" in result.stderr, result.stderr
    assert "journal.jsonl" in result.stderr, result.stderr


# --- recovery: what a run does with what an earlier run left open -------------


def _diverged(tree, before=None, kill_after="an adopter wrote this\n"):
    """Build a published residue, optionally diverge it, and return its ID.

    `before` creates a real preimage blob. `kill_after=None` leaves the
    published path recoverable instead of simulating an adopter overwrite."""
    if before is not None:
        (tree / ".gitignore").write_text(before, encoding="utf-8")
    environment = dict(os.environ, VALIDATED_MEMORY_FAULT="after-published")
    killed = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        capture_output=True,
        text=True,
        cwd=tree,
        env={**environment, "PYTHONPATH": str(REPO_ROOT)},
        check=False,
    )
    assert killed.returncode == 70, (killed.stdout, killed.stderr)
    if kill_after is not None:
        (tree / ".gitignore").write_text(kill_after, encoding="utf-8")
    open_transactions = _transactions(tree)
    assert len(open_transactions) == 1, open_transactions
    return open_transactions[0]["transaction"]


def _created_then_diverged(tree):
    """Build a published file creation with an absent preimage; return its ID.

    Callers alter the published path to make the transaction diverged."""
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    first = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        capture_output=True, text=True, cwd=tree, env=environment, check=False,
    )
    assert first.returncode == 0, (first.stdout, first.stderr)
    (tree / "knowledge-extension.md").unlink()
    killed = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        capture_output=True,
        text=True,
        cwd=tree,
        env={**environment, "VALIDATED_MEMORY_FAULT": "after-published"},
        check=False,
    )
    assert killed.returncode == 70, (killed.stdout, killed.stderr)
    open_transactions = _transactions(tree)
    assert len(open_transactions) == 1, open_transactions
    assert open_transactions[0]["preimage"] == {"kind": "absent"}, open_transactions
    assert open_transactions[0]["intention"]["path"] == "knowledge-extension.md"
    return open_transactions[0]["transaction"]


def _transaction_file(tree, transaction_id, **overrides):
    """Write a transaction fixture in the writer's field shape."""
    adoption = _records(tree / "journal.jsonl")[0]["adoption"]
    entry = {
        "schema": 1,
        "at": "2026-09-01T00:00:00Z",
        "version": "1.6.0",
        "adoption": adoption,
        "run": "7777777777777777",
        "transaction": transaction_id,
        "intention": {
            "op": "replace",
            "purpose": "init",
            "path": "validated-memory.md",
            "durability": "repo",
        },
        "preimage": {"kind": "file", "digest": "sha256:" + "0" * 64, "mode": 420},
        "postimage": {"kind": "file", "digest": "sha256:" + "1" * 64, "mode": 420},
        "preimage_blob": "sha256:" + "0" * 64,
        "mode": 420,
        "prior_bytes": None,
        "stage": "prepared",
    }
    entry.update(overrides)
    directory = tree / ".validated-memory" / "transactions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{transaction_id}.json").write_text(
        json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8"
    )
    return entry


def test_a_creation_does_not_build_the_parent_the_same_run_refused_to_create(
    run_cli, tmp_path
):
    """A child creation cannot create a parent gated by another transaction.

    The failed child transaction closes; the gating ID remains the sole
    transaction, and parsed history records compare equal."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    (tmp_path / "memory" / "MEMORY.md").unlink()
    (tmp_path / "memory").rmdir()
    # A `prepared` transaction whose path matches neither of its states is
    # `unknown`, which is what gates the path: recovery may not close it,
    # and nothing may write over it until an operator does.
    _transaction_file(
        tmp_path,
        "8888888888888888",
        intention={
            "op": "replace",
            "purpose": "init",
            "path": "memory",
            "durability": "repo",
        },
        postimage={"kind": "directory"},
    )
    before = _records(tmp_path / "journal.jsonl")
    tree_before = _final_tree_snapshot(tmp_path)

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert not (tmp_path / "memory").exists(), sorted(
        entry.name for entry in tmp_path.iterdir()
    )
    assert (
        "memory/MEMORY.md: create: file could not be created: "
        "memory/MEMORY.md could not be written: its parent directory memory "
        "does not exist" in result.stderr
    ), result.stderr
    assert "created memory/MEMORY.md" not in result.stdout, result.stdout
    # No history record is added. Only the gating transaction ID remains.
    assert _records(tmp_path / "journal.jsonl") == before
    assert [entry["transaction"] for entry in _transactions(tmp_path)] == [
        "8888888888888888"
    ], _transactions(tmp_path)
    assert _final_tree_snapshot(tmp_path) == tree_before


def test_recovery_leaves_a_transaction_it_cannot_account_for_untouched(
    run_cli, tmp_path
):
    """Repeated recovery leaves a diverged transaction and history unchanged."""
    transaction = _diverged(tmp_path)
    residue = (
        tmp_path / ".validated-memory" / "transactions" / f"{transaction}.json"
    ).read_text(encoding="utf-8")

    first = run_cli("init", cwd=tmp_path)
    records = _records(tmp_path / "journal.jsonl")
    second = run_cli("init", cwd=tmp_path)

    assert first.returncode == 1, (first.stdout, first.stderr)
    assert second.returncode == 1, (second.stdout, second.stderr)
    assert transaction in first.stderr, first.stderr
    assert first.stderr == second.stderr, (first.stderr, second.stderr)
    assert _records(tmp_path / "journal.jsonl") == records
    assert (
        tmp_path / ".validated-memory" / "transactions" / f"{transaction}.json"
    ).read_text(encoding="utf-8") == residue


def test_only_the_path_a_transaction_names_is_gated(
    run_cli, tmp_path, monkeypatch
):
    """A single-path transaction gates only its path, not the rest of init.

    The fixture avoids the ignore-file whole-scaffold gate so other creations
    remain observable."""
    (tmp_path / ".gitignore").write_text("/.validated-memory/\n", encoding="utf-8")
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-published")
    assert run_cli("init", cwd=tmp_path).returncode == 70
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
    # The directory the kill published is taken away, so the transaction's
    # postimage describes a state that is no longer there and recovery can
    # say neither that the mutation happened nor that it did not.
    (tmp_path / "knowledge").rmdir()
    transaction = _transactions(tmp_path)[0]["transaction"]

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (tmp_path / "memory" / "MEMORY.md").exists()
    assert (tmp_path / "validated-memory.md").exists()
    assert "init: created memory" in result.stdout, result.stdout
    assert not (tmp_path / "knowledge").exists()
    assert (
        f"has an unresolved transaction {transaction}" in result.stderr
    ), result.stderr
    for flag in ("--accept", "--restore", "--abandon"):
        assert flag in result.stderr, result.stderr


def test_journal_check_says_what_recovery_would_do_with_each_transaction(
    run_cli, tmp_path
):
    """Report four residue classes without changing any final tree state."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    # `published`, and the path is the postimage: recovery completes it.
    _transaction_file(
        tmp_path,
        "1111111111111111",
        stage="published",
        postimage={
            "kind": "file",
            "digest": _records(tmp_path / "journal.jsonl")[-1]["postimage"],
        },
        intention={
            "op": "create",
            "purpose": "init",
            "path": "knowledge-extension.md",
            "durability": "repo",
        },
    )
    # `published`, and the path is something else entirely.
    _transaction_file(
        tmp_path,
        "2222222222222222",
        stage="published",
        intention={
            "op": "replace",
            "purpose": "init",
            "path": "memory/MEMORY.md",
            "durability": "repo",
        },
    )
    # `prepared`, and the path matches neither state.
    _transaction_file(tmp_path, "3333333333333333")
    (tmp_path / ".validated-memory" / "transactions" / "4444444444444444.json").write_text(
        "{not json", encoding="utf-8"
    )
    before = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")
    tree_before = _final_tree_snapshot(tmp_path)

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        "open transaction 1111111111111111 (published) on "
        "knowledge-extension.md: recoverable" in result.stderr
    ), result.stderr
    assert (
        "open transaction 2222222222222222 (published) on "
        "memory/MEMORY.md: diverged" in result.stderr
    ), result.stderr
    assert (
        "open transaction 3333333333333333 (prepared) on "
        "validated-memory.md: unknown" in result.stderr
    ), result.stderr
    assert "damaged transaction 4444444444444444:" in result.stderr, result.stderr
    assert "journal: 13 record(s), 4 error(s)" in result.stdout, result.stdout
    # Read-only: nothing was completed, discarded or removed.
    assert (tmp_path / "journal.jsonl").read_text(encoding="utf-8") == before
    left = sorted(
        entry.name
        for entry in (tmp_path / ".validated-memory" / "transactions").iterdir()
    )
    assert len(left) == 4, left
    assert _final_tree_snapshot(tmp_path) == tree_before


def test_an_aborted_transaction_is_reported_and_removed(run_cli, tmp_path):
    """Recovery removes an aborted transaction without history or its ID in output."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    _transaction_file(
        tmp_path,
        "5555555555555555",
        stage="aborted",
        reason="validated-memory.md changed while its mutation was prepared",
    )
    before = _records(tmp_path / "journal.jsonl")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not _transactions(tmp_path), _transactions(tmp_path)
    assert _records(tmp_path / "journal.jsonl") == before
    assert "5555555555555555" not in result.stdout, result.stdout
    assert run_cli("journal", "--check", cwd=tmp_path).returncode == 0


def test_journal_resolve_accept_records_an_observation_never_a_mutation(
    run_cli, tmp_path
):
    """--accept preserves the path and records one exact resolution observation."""
    transaction = _diverged(tmp_path)

    result = run_cli("journal", "--resolve", transaction, "--accept", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == "", result.stderr
    assert f"journal: resolved {transaction} (--accept)" in result.stdout
    assert not _transactions(tmp_path), _transactions(tmp_path)

    # The mutation's own pair goes in ahead of it, because the transaction
    # is `published` and the bytes are on disk -- asserted by the test
    # below. What this one is about is the last record, the resolution's.
    written = [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record["path"] == ".gitignore" and record["op"] == "observe"
    ]
    assert len(written) == 1, written
    assert written[0]["op"] == "observe", written
    assert written[0]["stage"] == "committed", written
    assert written[0]["note"] == (
        f"accepted after divergence: transaction {transaction} found file"
    ), written
    # The path is untouched, and the run that follows is clean.
    assert (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    ) == "an adopter wrote this\n"
    assert run_cli("journal", "--check", cwd=tmp_path).returncode == 0


def test_journal_resolve_abandon_records_that_the_path_was_left_as_found(
    run_cli, tmp_path
):
    """`--abandon`: nothing is published and nothing is undone, and it says so."""
    transaction = _diverged(tmp_path)

    result = run_cli("journal", "--resolve", transaction, "--abandon", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert f"journal: resolved {transaction} (--abandon)" in result.stdout
    assert not _transactions(tmp_path), _transactions(tmp_path)
    written = [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record["path"] == ".gitignore" and record["op"] == "observe"
    ]
    assert len(written) == 1, written
    assert written[0]["op"] == "observe", written
    assert written[0]["note"] == (
        f"abandoned: transaction {transaction}, path left as found"
    ), written
    assert (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    ) == "an adopter wrote this\n"


def test_resolving_one_transaction_does_not_recover_another(run_cli, tmp_path):
    """Targeted resolution leaves another transaction and its path untouched."""
    target = _diverged(tmp_path)
    other = "3434343434343434"
    other_path = tmp_path / "other.txt"
    other_path.write_text("untouched\n", encoding="utf-8")
    _transaction_file(
        tmp_path,
        other,
        intention={
            "op": "replace",
            "purpose": "init",
            "path": "other.txt",
            "durability": "repo",
        },
    )
    artifact = (
        tmp_path / ".validated-memory" / "transactions" / f"{other}.json"
    )
    artifact_before = artifact.read_bytes()
    path_before = other_path.read_bytes()

    result = run_cli("journal", "--resolve", target, "--accept", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == f"journal: resolved {target} (--accept)\n"
    assert result.stderr == (
        "ERROR: other.txt: journal: unknown. This condition was left "
        "untouched; the confirmed effect is reported separately.\n"
    )
    assert artifact.read_bytes() == artifact_before
    assert other_path.read_bytes() == path_before
    assert [entry["transaction"] for entry in _transactions(tmp_path)] == [other]
    assert not [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record.get("transaction") == other
    ]


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
def test_resolution_locked_reread_does_not_adopt_a_new_unrelated_transaction(
    tmp_path,
):
    """A WAL appearing after the inert probe supplies no selected authority."""
    selected = _diverged(tmp_path)
    ready_read, ready_write = os.pipe()
    continue_read, continue_write = os.pipe()
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "VALIDATED_MEMORY_TEST_RENDEZVOUS": "before-resolution-lock",
        "VALIDATED_MEMORY_TEST_READY_FD": str(ready_write),
        "VALIDATED_MEMORY_TEST_CONTINUE_FD": str(continue_read),
    }
    process = subprocess.Popen(
        [
            sys.executable, "-P", "-m", "validated_memory", "journal",
            "--resolve", selected, "--accept",
        ],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        pass_fds=(ready_write, continue_read),
    )
    os.close(ready_write)
    os.close(continue_read)
    try:
        assert os.read(ready_read, 2) == b"1\n"
        unrelated = "ffffffffffffffff"
        _transaction_file(
            tmp_path,
            unrelated,
            intention={
                "op": "replace",
                "purpose": "init",
                "path": "unrelated.txt",
                "durability": "repo",
            },
        )
        artifact = (
            tmp_path / ".validated-memory" / "transactions" / f"{unrelated}.json"
        )
        before = artifact.read_bytes()
        os.write(continue_write, b"1")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(ready_read)
        os.close(continue_write)
        if process.poll() is None:
            process.kill()
            process.communicate()
    assert process.returncode == 1, (stdout, stderr)
    assert stdout == f"journal: resolved {selected} (--accept)\n"
    assert artifact.read_bytes() == before
    assert [item["transaction"] for item in _transactions(tmp_path)] == [unrelated]
    assert not [
        entry for entry in _records(tmp_path / "journal.jsonl")
        if entry.get("transaction") == unrelated
    ]


def test_resolution_refuses_a_later_conflicting_history_identity(run_cli, tmp_path):
    """A named transaction cannot mutate through a mixed-identity history."""
    transaction = _diverged(tmp_path)
    journal = tmp_path / "journal.jsonl"
    lines = journal.read_text(encoding="utf-8").splitlines()
    mine = json.loads(lines[0])["adoption"]
    foreign = "f" * 16
    lines.append(
        json.dumps({**json.loads(lines[0]), "adoption": foreign}, sort_keys=True)
    )
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    artifact = (
        tmp_path / ".validated-memory" / "transactions" / f"{transaction}.json"
    )
    journal_before = journal.read_bytes()
    artifact_before = artifact.read_bytes()
    path_before = (tmp_path / ".gitignore").read_bytes()

    result = run_cli(
        "journal", "--resolve", transaction, "--accept", cwd=tmp_path
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert "one project has one adoption id" in result.stderr, result.stderr
    assert mine in result.stderr, result.stderr
    assert foreign in result.stderr, result.stderr
    assert journal.read_bytes() == journal_before
    assert artifact.read_bytes() == artifact_before
    assert (tmp_path / ".gitignore").read_bytes() == path_before


def test_journal_resolve_over_a_published_transaction_keeps_its_record_pair(
    run_cli, tmp_path
):
    """Resolving a published divergence retains its mutation pair before observe.

    The pair keeps the crashed run and transaction IDs. Later checks and
    unchanged init runs add nothing."""
    _diverged(tmp_path, kill_after="/.validated-memory/\nbuild/\n")
    entry = _transactions(tmp_path)[0]
    transaction = entry["transaction"]

    result = run_cli("journal", "--resolve", transaction, "--accept", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not _transactions(tmp_path), _transactions(tmp_path)
    written = [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record["path"] == ".gitignore"
    ]
    assert [record["op"] for record in written] == [
        "create",
        "create",
        "observe",
    ], written
    assert [record["stage"] for record in written] == [
        "prepared",
        "committed",
        "committed",
    ], written
    # One act, filed under the run that wrote the bytes -- not under the
    # run that resolved it, which wrote none.
    assert [record.get("transaction") for record in written[:2]] == [
        transaction,
        transaction,
    ], written
    assert [record["run"] for record in written[:2]] == [
        entry["run"],
        entry["run"],
    ], (written, entry)
    assert written[2].get("transaction") is None, written
    assert written[2]["note"] == (
        f"accepted after divergence: transaction {transaction} found file"
    ), written

    # A closed pair is not reconciled again, so the next run has nothing to
    # complete and nothing to say about it.
    assert run_cli("journal", "--check", cwd=tmp_path).returncode == 0
    settled = run_cli("init", cwd=tmp_path)
    assert settled.returncode == 0, (settled.stdout, settled.stderr)
    assert "recovered" not in settled.stdout, settled.stdout
    records = _records(tmp_path / "journal.jsonl")
    assert [
        record for record in records if record["path"] == ".gitignore"
    ] == written, records
    again = run_cli("init", cwd=tmp_path)
    assert again.returncode == 0, again.stderr
    assert _records(tmp_path / "journal.jsonl") == records


def test_journal_resolve_restore_puts_the_preimage_back_and_records_nothing(
    run_cli, tmp_path
):
    """--restore reinstates preimage bytes and mode without appending history."""
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / ".gitignore").chmod(0o640)
    transaction = _diverged(tmp_path, before="build/\n")
    before = _records(tmp_path / "journal.jsonl")

    result = run_cli("journal", "--resolve", transaction, "--restore", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert f"journal: resolved {transaction} (--restore)" in result.stdout
    assert not _transactions(tmp_path), _transactions(tmp_path)
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "build/\n"
    assert (tmp_path / ".gitignore").stat().st_mode & 0o777 == 0o640
    assert _records(tmp_path / "journal.jsonl") == before
    # And the path is writable again: the next run ignores the vault.
    assert run_cli("init", cwd=tmp_path).returncode == 0
    assert "/.validated-memory/" in (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    )


def test_journal_resolve_restore_refuses_a_blob_that_is_not_the_preimage(
    run_cli, tmp_path
):
    """--restore refuses a missing or digest-mismatched open-transaction blob.

    A mismatch preserves the current path; both cases leave the transaction
    unresolved. Missing blobs for closed history remain valid."""
    transaction = _diverged(tmp_path, before="build/\n")
    preimages = tmp_path / ".validated-memory" / "preimages"
    blob = next(iter(preimages.iterdir()))
    blob.write_text("not the bytes that were parked\n", encoding="utf-8")

    mismatched = run_cli(
        "journal", "--resolve", transaction, "--restore", cwd=tmp_path
    )

    assert mismatched.returncode == 1, mismatched.stdout
    assert "does not digest to" in mismatched.stderr, mismatched.stderr
    assert "Nothing has been restored." in mismatched.stderr, mismatched.stderr
    assert (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    ) == "an adopter wrote this\n"
    assert len(_transactions(tmp_path)) == 1, _transactions(tmp_path)

    blob.unlink()
    missing = run_cli("journal", "--resolve", transaction, "--restore", cwd=tmp_path)

    assert missing.returncode == 1, missing.stdout
    assert "is not in .validated-memory/preimages/" in missing.stderr, missing.stderr
    assert "damaged log" in missing.stderr, missing.stderr
    assert len(_transactions(tmp_path)) == 1, _transactions(tmp_path)


def test_journal_resolve_restore_refuses_once_the_mutation_is_history(
    run_cli, tmp_path, monkeypatch
):
    """--restore refuses once the mutation pair is committed to history."""
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-history")
    assert run_cli("init", cwd=tmp_path).returncode == 70
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
    (tmp_path / ".gitignore").write_text("an adopter wrote this\n", encoding="utf-8")
    transaction = _transactions(tmp_path)[0]["transaction"]

    result = run_cli("journal", "--resolve", transaction, "--restore", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "already recorded in journal.jsonl" in result.stderr, result.stderr
    assert "--accept or --abandon" in result.stderr, result.stderr
    assert len(_transactions(tmp_path)) == 1, _transactions(tmp_path)
    assert (tmp_path / ".gitignore").read_text(
        encoding="utf-8"
    ) == "an adopter wrote this\n"

    accepted = run_cli("journal", "--resolve", transaction, "--accept", cwd=tmp_path)
    assert accepted.returncode == 0, (accepted.stdout, accepted.stderr)
    assert run_cli("journal", "--check", cwd=tmp_path).returncode == 0


def test_nonterminated_history_is_refused_without_writes(run_cli, tmp_path):
    """A valid final object without LF is not an appendable history."""
    initial = run_cli("init", cwd=tmp_path)
    assert initial.returncode == 0, initial.stderr
    history = tmp_path / "journal.jsonl"
    original = history.read_bytes()
    history.write_bytes(original[:-1])
    before = sorted(
        (path.relative_to(tmp_path).as_posix(), path.read_bytes())
        for path in tmp_path.rglob("*")
        if path.is_file() and not path.is_symlink()
    )

    checked = run_cli("journal", "--check", cwd=tmp_path)
    assert checked.returncode == 1
    assert "does not end with a line feed" in checked.stderr
    assert history.read_bytes() == original[:-1]

    refused = run_cli("init", cwd=tmp_path)
    assert refused.returncode == 1
    assert "does not end with a line feed" in refused.stderr
    after = sorted(
        (path.relative_to(tmp_path).as_posix(), path.read_bytes())
        for path in tmp_path.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    assert after == before


def test_repair_usage_is_rejected_before_filesystem_materialization(run_cli, tmp_path):
    """The explicit repair ID is required and mutually exclusive with read-only mode."""
    assert not any(tmp_path.iterdir())
    empty = run_cli("journal", "--repair", "", cwd=tmp_path)
    assert empty.returncode == 2
    assert not any(tmp_path.iterdir())
    mixed = run_cli("journal", "--repair", "x", "--check", cwd=tmp_path)
    assert mixed.returncode == 2
    assert not any(tmp_path.iterdir())
    help_result = run_cli("journal", "--help", cwd=tmp_path)
    assert help_result.returncode == 0
    assert "repair one torn history append from a WAL proof" in help_result.stdout
    assert "get ID from journal --check" in help_result.stdout


def test_repair_unknown_id_is_inert_on_a_virgin_tree(run_cli, tmp_path):
    """A valid targeted request does not materialize state for an absent WAL."""
    before = _final_tree_snapshot(tmp_path)

    result = run_cli("journal", "--repair", "unknown", cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "ERROR: .validated-memory/transactions/unknown.json: journal: "
        "transaction unknown does not prove this repair: there is no unresolved "
        "transaction with that id. No target or permanent-history change was "
        "left by this operation. Preserve all evidence and select a transaction "
        "carrying the required valid proof, or restore exact trusted history.\n"
    )
    assert _final_tree_snapshot(tmp_path) == before


@pytest.mark.parametrize("evidence", ("damaged", "unsupported"))
def test_repair_classifies_unusable_selected_wal_without_writes(
    run_cli, tmp_path, monkeypatch, evidence
):
    """Damaged and newer selected WALs retain their closed result category."""
    transaction, _claim, _payload, _history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    if evidence == "damaged":
        transaction.write_bytes(b"not json\n")
    else:
        entry = json.loads(transaction.read_text(encoding="utf-8"))
        entry["schema"] = 2
        transaction.write_text(
            json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8"
        )
    before = _final_tree_snapshot(tmp_path)

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    if evidence == "damaged":
        assert result.stderr == (
            f"ERROR: .validated-memory/transactions/{transaction.name}: journal: "
            "damaged transaction evidence: not valid JSON: Expecting value. "
            "No target or permanent-history change was left by this operation. "
            "Preserve this evidence and restore the exact artifact from a trusted "
            "source before rerunning journal --repair "
            f"{transaction.stem}.\n"
        )
    else:
        assert result.stderr == (
            f"ERROR: .validated-memory/transactions/{transaction.name}: journal: "
            "protocol 2 is newer than this reader (maximum 1). No target or "
            "permanent-history change was left by this operation. Install a "
            "compatible validated-memory version and rerun journal --repair "
            f"{transaction.stem}.\n"
        )
    assert _final_tree_snapshot(tmp_path) == before


def _history_repair_fixture(run_cli, tmp_path, monkeypatch):
    """Create one local history WAL whose append barrier is unconfirmed."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", "append:local.jsonl")
    harness = _external_harness_path(tmp_path, "h")
    result = run_cli("init", "--harness-memory", str(harness), cwd=tmp_path)
    assert result.returncode == 1
    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    transaction = next((tmp_path / ".validated-memory" / "transactions").glob("*.json"))
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    claim = entry["history_append"]
    payload = b"".join(
        (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
        for record in claim["records"]
    )
    history = tmp_path / ".validated-memory" / "local.jsonl"
    return transaction, claim, payload, history


@pytest.mark.parametrize("field", ["path", "adoption", "run", "postimage", "mode"])
def test_repair_refuses_claims_forged_against_the_wal(
    run_cli, tmp_path, monkeypatch, field
):
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    original = history.read_bytes()
    changed = json.loads(transaction.read_text(encoding="utf-8"))
    record = changed["history_append"]["records"][0]
    if field == "adoption":
        record[field] = "foreign"
    elif field == "run":
        record[field] = "foreign"
    elif field == "postimage":
        record[field] = "sha256:" + "0" * 64
    elif field == "mode":
        record[field] = 1
    else:
        record[field] = "foreign/path"
    transaction.write_text(json.dumps(changed, sort_keys=True) + "\n", encoding="utf-8")
    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)
    assert result.returncode == 1
    assert history.read_bytes() == original
    assert transaction.exists()


def test_repair_refuses_retimed_history_claim_even_with_recomputed_digest(
    run_cli, tmp_path, monkeypatch
):
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    changed = json.loads(transaction.read_text(encoding="utf-8"))
    changed["history_append"]["records"][0]["at"] = "2099-01-01T00:00:00Z"
    retimed = b"".join(
        (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
        for record in changed["history_append"]["records"]
    )
    changed["history_append"]["append"] = {
        "length": len(retimed),
        "digest": "sha256:" + hashlib.sha256(retimed).hexdigest(),
    }
    transaction.write_text(json.dumps(changed, sort_keys=True) + "\n", encoding="utf-8")
    before = history.read_bytes()

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert result.returncode == 1
    assert "timestamps" in result.stderr
    assert history.read_bytes() == before
    assert transaction.exists()


@pytest.mark.parametrize("tail_size", [None, 1])
def test_repair_accepts_partial_prepared_or_committed_suffix(
    run_cli, tmp_path, monkeypatch, tail_size
):
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    prepared_end = payload.find(b"\n") + 1
    history.write_bytes(payload[:prepared_end] if tail_size is None else payload[:prepared_end + tail_size])
    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert history.read_bytes() == payload
    assert not transaction.exists()


def test_repair_uses_claim_boundary_for_nonempty_prefix_and_partial_committed_tail(
    run_cli, tmp_path, monkeypatch
):
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    # Carry one valid local record before the claimed append.  The existing
    # parser tail then starts in the middle of the claimed append, so repair
    # must use the proof's byte boundary rather than that parser offset.
    prefix_entry = json.loads(
        (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    prefix_entry["durability"] = "local"
    prefix = (json.dumps(prefix_entry, sort_keys=True) + "\n").encode("utf-8")
    prepared_end = payload.find(b"\n") + 1
    history.write_bytes(prefix + payload[: prepared_end + 1])
    changed = json.loads(transaction.read_text(encoding="utf-8"))
    changed["history_append"]["prefix"] = {
        "length": len(prefix),
        "digest": "sha256:" + hashlib.sha256(prefix).hexdigest(),
    }
    transaction.write_text(
        json.dumps(changed, sort_keys=True) + "\n", encoding="utf-8"
    )

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert history.read_bytes() == prefix + payload
    assert not transaction.exists()


@pytest.mark.parametrize("forged_length", [True, 0])
def test_repair_refuses_boolean_or_lower_self_consistent_prefix(
    run_cli, tmp_path, monkeypatch, forged_length
):
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    prefix = json.loads(
        (tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    prefix["durability"] = "local"
    prefix_bytes = (json.dumps(prefix, sort_keys=True) + "\n").encode("utf-8")
    history.write_bytes(prefix_bytes + payload[: payload.find(b"\n") + 1])
    changed = json.loads(transaction.read_text(encoding="utf-8"))
    if forged_length is True:
        length = True
        digest_bytes = prefix_bytes
    else:
        length = len(prefix_bytes) - 1
        digest_bytes = prefix_bytes[:-1]
    changed["history_append"]["prefix"] = {
        "length": length,
        "digest": "sha256:" + hashlib.sha256(digest_bytes).hexdigest(),
    }
    transaction.write_text(
        json.dumps(changed, sort_keys=True) + "\n", encoding="utf-8"
    )
    before = history.read_bytes()

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert result.returncode == 1
    assert history.read_bytes() == before
    assert transaction.exists()


def test_exact_final_repair_republishes_and_retries_after_confirmation_failure(
    run_cli, tmp_path, monkeypatch
):
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    assert history.read_bytes() == payload
    monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", "install:local.jsonl")
    failed = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)
    assert failed.returncode == 1
    assert transaction.exists()
    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    repaired = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)
    assert repaired.returncode == 0, repaired.stderr
    assert ".validated-memory/local.jsonl" in repaired.stdout
    assert not transaction.exists()
    assert history.read_bytes() == payload
    assert len(history.read_bytes().splitlines()) == 2


@pytest.mark.parametrize(
    ("fault", "visible"),
    (
        ("write:local.jsonl", False),
        ("atomic-install:local.jsonl", False),
        ("install:local.jsonl", True),
    ),
)
def test_repair_distinguishes_prepublication_and_visible_storage_faults(
    run_cli, tmp_path, monkeypatch, fault, visible
):
    """Publication faults retain proof without overstating history visibility."""
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    prepared = payload[: payload.find(b"\n") + 1]
    history.write_bytes(prepared)
    repository_before = (tmp_path / "journal.jsonl").read_bytes()
    monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", fault)

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    if visible:
        assert result.stderr.startswith(
            "ERROR: .validated-memory/local.jsonl: journal: the selected repair "
            "effect is visible or may be visible, but coherent successor "
            "confirmation failed: "
        )
        assert history.read_bytes() == payload
    else:
        assert result.stderr.startswith(
            "ERROR: .validated-memory/local.jsonl: journal: transaction "
            f"{transaction.stem} does not prove this repair: "
        )
        assert "No target or permanent-history change was left" in result.stderr
        assert history.read_bytes() == prepared
    assert transaction.exists()
    assert (tmp_path / "journal.jsonl").read_bytes() == repository_before


def test_repair_cleanup_visibility_failure_retains_wal_until_retry(
    run_cli, tmp_path, monkeypatch
):
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", f"remove:{transaction.name}"
    )
    failed = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)
    assert failed.returncode == 1
    assert "selected repair effect is visible or may be visible" in failed.stderr
    assert f"Transaction {transaction.stem} was retained" in failed.stderr
    assert transaction.exists()
    assert history.read_bytes() == payload

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    retried = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)
    assert retried.returncode == 0, retried.stderr
    assert not transaction.exists()
    assert history.read_bytes() == payload


def test_repair_refuses_forged_symlink_temporary_claim_before_publication(
    run_cli, tmp_path, monkeypatch
):
    """A self-consistent foreign staging link is not bound repair evidence."""
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    history.write_bytes(payload[: payload.find(b"\n") + 1])
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    claim = entry["temporary"]
    target = Path(entry["intention"]["path"])
    candidate = target.parent / claim["name"]
    candidate.symlink_to("foreign-target")
    claim["digest"] = "sha256:" + hashlib.sha256(b"foreign-target").hexdigest()
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    before = _final_tree_snapshot(tmp_path.parent)

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "does not prove this repair" in result.stderr
    assert _final_tree_snapshot(tmp_path.parent) == before


def test_repair_refuses_forged_file_temporary_claim_before_publication(
    run_cli, tmp_path, monkeypatch
):
    """A self-consistent foreign staging file cannot borrow the WAL claim."""
    transaction_entry, history = _retained_complete_append(
        run_cli, tmp_path, monkeypatch
    )
    transaction = (
        tmp_path / ".validated-memory" / "transactions"
        / f"{transaction_entry['transaction']}.json"
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    claim = entry["temporary"]
    target = tmp_path / entry["intention"]["path"]
    candidate = target.parent / claim["name"]
    candidate.write_bytes(b"foreign temporary bytes\n")
    candidate.chmod(claim["mode"])
    claim["digest"] = "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest()
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    before = _final_tree_snapshot(tmp_path)

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "does not prove this repair" in result.stderr
    assert _final_tree_snapshot(tmp_path) == before


def test_repair_refuses_unsafe_temporary_kind_before_publication(
    run_cli, tmp_path, monkeypatch
):
    """An unsafe temporary kind refuses without first publishing history."""
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    history.write_bytes(payload[: payload.find(b"\n") + 1])
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry["temporary"]["kind"] = "directory"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    before = _final_tree_snapshot(tmp_path)

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "does not prove this repair" in result.stderr
    assert _final_tree_snapshot(tmp_path) == before


def test_private_duplicate_cleanup_visibility_failure_retains_wal(
    run_cli, tmp_path, monkeypatch
):
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    private = tmp_path / ".validated-memory" / "transactions"
    duplicate = private / f".{transaction.name}.{'a' * 32}.tmp"
    duplicate.write_bytes(transaction.read_bytes())
    duplicate.chmod(transaction.stat().st_mode & 0o777)
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", f"remove:{duplicate.name}"
    )

    failed = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert failed.returncode == 1
    assert transaction.exists()
    assert not duplicate.exists()
    assert "selected repair effect is visible or may be visible" in failed.stderr

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    retried = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)
    assert retried.returncode == 0, retried.stderr
    assert not transaction.exists()


def test_repair_derives_clean_result_after_exact_private_duplicate_cleanup(
    run_cli, tmp_path, monkeypatch
):
    """A removed exact private duplicate cannot survive as a stale gate."""
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    directory = transaction.parent
    duplicate = directory / f".{transaction.name}.{'a' * 32}.tmp"
    duplicate.write_bytes(transaction.read_bytes())
    duplicate.chmod(stat.S_IMODE(transaction.stat().st_mode))

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        "journal: repaired .validated-memory/local.jsonl for transaction "
        f"{transaction.stem}\n",
        "",
    )
    assert history.read_bytes() == payload
    assert not transaction.exists()
    assert not duplicate.exists()


def test_repair_reports_unproven_private_residue_from_final_snapshot(
    run_cli, tmp_path, monkeypatch
):
    """Foreign residue remains one final canonical checked gate."""
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    residue = transaction.parent / ".foreign.tmp"
    residue.write_bytes(b"foreign residue\n")

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == (
        "journal: repaired .validated-memory/local.jsonl for transaction "
        f"{transaction.stem}\n"
        "journal: repair confirmed, 1 gate(s) remain\n"
    )
    assert result.stderr == (
        "ERROR: .validated-memory/transactions/.foreign.tmp: journal: retained "
        "private residue; ownership was not proven. Address this condition, "
        "then run journal --check; do not repeat the confirmed repair.\n"
    )
    assert history.read_bytes() == payload
    assert not transaction.exists()
    assert residue.read_bytes() == b"foreign residue\n"


def test_repair_uses_wal_published_mode_after_target_chmod(
    run_cli, tmp_path, monkeypatch
):
    assert run_cli("init", cwd=tmp_path).returncode == 0
    target = tmp_path / ".gitignore"
    target.write_text("adopter change\n", encoding="utf-8")
    monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", "append:journal.jsonl")
    failed = run_cli("init", cwd=tmp_path)
    assert failed.returncode == 1
    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    transaction = next((tmp_path / ".validated-memory" / "transactions").glob("*.json"))
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    published_mode = entry["published_mode"]
    target.chmod(0o600 if published_mode != 0o600 else 0o640)

    before = (tmp_path / "journal.jsonl").read_bytes()
    repaired = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert repaired.returncode == 1, repaired.stderr
    assert "WAL postimage" in repaired.stderr
    assert transaction.exists()
    assert (tmp_path / "journal.jsonl").read_bytes() == before


@pytest.mark.parametrize("conflict", ("selected-reuse", "adoption-mismatch"))
def test_repair_refuses_candidate_identity_conflicts_before_publication(
    run_cli, tmp_path, monkeypatch, conflict
):
    """Candidate topology cannot bless selected reuse or foreign adoption."""
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    history.write_bytes(payload[: payload.find(b"\n") + 1])
    repository = tmp_path / "journal.jsonl"
    records = _records(repository)
    if conflict == "selected-reuse":
        extra = dict(records[-2])
        extra["transaction"] = transaction.stem
        extra["stage"] = "prepared"
        repository.write_bytes(
            repository.read_bytes()
            + (json.dumps(extra, sort_keys=True) + "\n").encode("utf-8")
        )
    else:
        changed = [{**record, "adoption": "foreign-adoption"} for record in records]
        repository.write_bytes(_jsonl(*changed))
    before = _final_tree_snapshot(tmp_path)

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == ""
    assert "does not prove this repair" in result.stderr
    assert (
        "exactly one claimed record pair" in result.stderr
        if conflict == "selected-reuse"
        else "adoption mismatch" in result.stderr
    )
    assert _final_tree_snapshot(tmp_path) == before


def test_repair_confirms_with_only_frozen_unrelated_gate_remaining(
    run_cli, tmp_path, monkeypatch
):
    """An unrelated candidate gate survives without lending repair authority."""
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    history.write_bytes(payload[: payload.find(b"\n") + 1])
    repository = tmp_path / "journal.jsonl"
    gate = dict(_records(repository)[-2])
    gate.update(
        transaction="unrelated-gate",
        run="unrelated-run",
        stage="prepared",
    )
    repository.write_bytes(
        repository.read_bytes()
        + (json.dumps(gate, sort_keys=True) + "\n").encode("utf-8")
    )
    repository_before = repository.read_bytes()

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == (
        "journal: repaired .validated-memory/local.jsonl for transaction "
        f"{transaction.stem}\n"
        "journal: repair confirmed, 2 gate(s) remain\n"
    )
    assert result.stderr == (
        "ERROR: repo: journal: transaction unrelated-gate has an unresolved "
        "topology condition. "
        "Address this condition, then run journal --check; do not repeat the "
        "confirmed repair.\n"
        "ERROR: knowledge-extension.md: journal: unfinished transaction from "
        "run unrelated-run: the path is applied. Address this condition, then "
        "run journal --check; do not repeat the confirmed repair.\n"
    )
    assert history.read_bytes() == payload
    assert repository.read_bytes() == repository_before
    assert not transaction.exists()


def test_repair_does_not_borrow_or_revoke_for_unrelated_topology_damage(
    run_cli, tmp_path, monkeypatch
):
    """Frozen unrelated damage survives as gates beside a confirmed repair."""
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    history.write_bytes(payload[: payload.find(b"\n") + 1])
    repository = tmp_path / "journal.jsonl"
    prepared = dict(_records(repository)[-2])
    prepared.update(transaction="unrelated-damage", stage="prepared")
    committed = {**prepared, "stage": "committed", "purpose": "different"}
    repository.write_bytes(
        repository.read_bytes() + _jsonl(prepared, committed)
    )
    repository_before = repository.read_bytes()

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout.startswith(
        "journal: repaired .validated-memory/local.jsonl for transaction "
        f"{transaction.stem}\n"
    )
    assert result.stdout.endswith("gate(s) remain\n")
    assert "records of transaction unrelated-damage disagree on purpose" in result.stderr
    assert "do not repeat the confirmed repair" in result.stderr
    assert history.read_bytes() == payload
    assert repository.read_bytes() == repository_before
    assert not transaction.exists()


@pytest.mark.parametrize("order", ("before", "after"))
@pytest.mark.parametrize(
    "gate",
    ("damaged", "newer", "diverged", "unknown", "unreadable", "recoverable"),
)
def test_repair_confirms_with_every_unrelated_wal_gate_in_either_order(
    run_cli, tmp_path, monkeypatch, order, gate
):
    """The full frozen WAL domain survives without lending repair authority."""
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    history.write_bytes(payload[: payload.find(b"\n") + 1])
    other_id = "0000000000000000" if order == "before" else "ffffffffffffffff"
    other = tmp_path / ".validated-memory" / "transactions" / f"{other_id}.json"
    if gate == "damaged":
        other.write_bytes(b"not json\n")
    elif gate == "newer":
        _transaction_file(tmp_path, other_id, schema=2)
    elif gate in {"diverged", "unknown"}:
        _transaction_file(
            tmp_path,
            other_id,
            stage="published" if gate == "diverged" else "prepared",
        )
    else:
        target = tmp_path / ("unreadable.txt" if gate == "unreadable" else "validated-memory.md")
        if gate == "unreadable":
            target.write_bytes(b"unreadable target\n")
        target_bytes = target.read_bytes()
        target_digest = "sha256:" + hashlib.sha256(target_bytes).hexdigest()
        entry = _transaction_file(tmp_path, other_id)
        entry["intention"]["path"] = target.relative_to(tmp_path).as_posix()
        entry["preimage"] = {
            "kind": "file",
            "digest": target_digest,
            "mode": stat.S_IMODE(target.stat().st_mode),
        }
        entry["preimage_blob"] = target_digest
        other.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
        if gate == "unreadable":
            target.chmod(0)
    other_bytes = other.read_bytes()
    checked = run_cli("journal", "--check", cwd=tmp_path)
    checked_gate = next(
        line for line in checked.stderr.splitlines() if other_id in line
    )

    try:
        result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)
    finally:
        if gate == "unreadable":
            target.chmod(0o644)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout.startswith(
        "journal: repaired .validated-memory/local.jsonl for transaction "
        f"{transaction.stem}\n"
    )
    assert result.stdout.endswith("gate(s) remain\n")
    assert "do not repeat the confirmed repair" in result.stderr
    assert (
        checked_gate
        + ". Address this condition, then run journal --check; do not repeat "
        "the confirmed repair."
    ) in result.stderr
    assert "wal." not in result.stderr
    assert "_" not in result.stderr
    assert other.read_bytes() == other_bytes
    assert not transaction.exists()
    assert history.read_bytes() == payload


def _rendezvous_repair(adopter, transaction, point="after-repair-publication"):
    """Start one repair paused at a named proof/publication boundary."""
    ready_read, ready_write = os.pipe()
    continue_read, continue_write = os.pipe()
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "VALIDATED_MEMORY_TEST_RENDEZVOUS": point,
        "VALIDATED_MEMORY_TEST_READY_FD": str(ready_write),
        "VALIDATED_MEMORY_TEST_CONTINUE_FD": str(continue_read),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-P",
            "-m",
            "validated_memory",
            "journal",
            "--repair",
            transaction,
        ],
        cwd=adopter,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        pass_fds=(ready_write, continue_read),
    )
    os.close(ready_write)
    os.close(continue_read)
    return process, ready_read, continue_write


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
@pytest.mark.parametrize(
    "raced", ("selected", "opposite", "wal-bytes", "wal-identity", "wal-mode")
)
def test_repair_refuses_frozen_evidence_race_before_publication(
    run_cli, tmp_path, monkeypatch, raced
):
    """Every selected proof input is revalidated at staging verification."""
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    prepared = payload[: payload.find(b"\n") + 1]
    history.write_bytes(prepared)
    repository = tmp_path / "journal.jsonl"
    process, ready, proceed = _rendezvous_repair(
        tmp_path, transaction.stem, "after-repair-inspection"
    )
    try:
        readable, _, _ = select.select([ready], [], [], 10)
        assert readable
        assert os.read(ready, 32).strip() == b"1"
        target = (
            history
            if raced == "selected"
            else repository
            if raced == "opposite"
            else transaction
        )
        if raced == "wal-identity":
            replacement = transaction.with_suffix(".replacement")
            replacement.write_bytes(transaction.read_bytes())
            os.replace(replacement, transaction)
        elif raced == "wal-bytes":
            transaction.write_bytes(transaction.read_bytes() + b" ")
        elif raced == "wal-mode":
            transaction.chmod(0o640)
        else:
            target.write_bytes(target.read_bytes() + b"\n")
        raced_bytes = target.read_bytes()
        os.write(proceed, b"1")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:
            process.kill()
            process.wait()

    assert process.returncode == 1, (stdout, stderr)
    assert stdout == ""
    assert "does not prove this repair" in stderr
    assert transaction.exists()
    assert target.read_bytes() == raced_bytes
    if raced != "selected":
        assert history.read_bytes() == prepared


@pytest.mark.skipif(os.name != "posix", reason="raw symlink bytes are POSIX-only")
def test_repair_handles_non_utf8_symlink_temporary_without_traceback(
    run_cli, tmp_path, monkeypatch
):
    """Filesystem-byte symlink proof never crosses a strict UTF-8 boundary."""
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    history.write_bytes(payload[: payload.find(b"\n") + 1])
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    target = Path(entry["intention"]["path"])
    target.unlink()
    os.symlink(b"\xff", os.fsencode(target))
    raw_target = os.fsdecode(b"\xff")
    entry["intention"]["target"] = raw_target
    entry["postimage"]["target"] = raw_target
    claim = entry["temporary"]
    claim["digest"] = "sha256:" + hashlib.sha256(b"\xff").hexdigest()
    candidate = target.parent / claim["name"]
    os.symlink(b"\xff", os.fsencode(candidate))
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        "journal: repaired .validated-memory/local.jsonl for transaction "
        f"{transaction.stem}\n",
        "",
    )
    assert history.read_bytes() == payload
    assert not transaction.exists()
    assert not os.path.lexists(candidate)


@pytest.mark.skipif(os.name != "posix", reason="raw symlink bytes are POSIX-only")
def test_repair_refuses_unbound_non_utf8_symlink_before_publication(
    run_cli, tmp_path, monkeypatch
):
    """A different invalid-byte staging target preserves the whole tree."""
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    history.write_bytes(payload[: payload.find(b"\n") + 1])
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    target = Path(entry["intention"]["path"])
    target.unlink()
    os.symlink(b"\xff", os.fsencode(target))
    raw_target = os.fsdecode(b"\xff")
    entry["intention"]["target"] = raw_target
    entry["postimage"]["target"] = raw_target
    claim = entry["temporary"]
    claim["digest"] = "sha256:" + hashlib.sha256(b"\xff").hexdigest()
    candidate = target.parent / claim["name"]
    os.symlink(b"\xfe", os.fsencode(candidate))
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    before = _final_tree_snapshot(tmp_path.parent)

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "ERROR: .validated-memory/local.jsonl: journal: transaction "
        f"{transaction.stem} does not prove this repair: temporary candidate "
        "does not match its claim. No target or permanent-history change was "
        "left by this operation. Preserve all evidence and select a transaction "
        "carrying the required valid proof, or restore exact trusted history.\n"
    )
    assert _final_tree_snapshot(tmp_path.parent) == before


def _repair_inspector_failure_mutant(tmp_path, failing_call):
    """Copy the package and fail one numbered topology inspection."""
    mutant = tmp_path / f"repair-inspector-{failing_call}"
    shutil.copytree(REPO_ROOT / "validated_memory", mutant / "validated_memory")
    topology = mutant / "validated_memory" / "journal" / "topology.py"
    source = topology.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "inspect"
    )
    lines = source.splitlines(keepends=True)
    lines.insert(function.lineno - 1, "_repair_inspection_calls = 0\n\n")
    offset = 2
    insertion = function.body[0].lineno - 1 + offset
    lines[insertion:insertion] = [
        "    global _repair_inspection_calls\n",
        "    _repair_inspection_calls += 1\n",
        f"    if _repair_inspection_calls == {failing_call}:\n",
        "        raise RuntimeError('repair topology unavailable')\n",
    ]
    topology.write_text("".join(lines), encoding="utf-8")
    return mutant


@pytest.mark.parametrize("failing_call", (1, 3, 4))
def test_repair_inspector_failure_refuses_before_or_retains_after_publication(
    run_cli, tmp_path, monkeypatch, failing_call
):
    """Candidate and successor inspection failures never become success."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, adopter, monkeypatch
    )
    prepared = payload[: payload.find(b"\n") + 1]
    history.write_bytes(prepared)
    repository_before = (adopter / "journal.jsonl").read_bytes()
    mutant = _repair_inspector_failure_mutant(tmp_path, failing_call)

    result = _run_mutant_journal(
        mutant, adopter, "--repair", transaction.stem
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    if failing_call == 1:
        assert "does not prove this repair" in result.stderr
        assert "candidate topology inspection is unavailable" in result.stderr
        assert history.read_bytes() == prepared
    elif failing_call == 3:
        assert "selected repair effect is visible or may be visible" in result.stderr
        assert "coherent successor inspection is unavailable" in result.stderr
        assert history.read_bytes() == payload
        assert transaction.exists()
    else:
        assert "selected repair effect is visible or may be visible" in result.stderr
        assert "final coherent snapshot is unavailable" in result.stderr
        assert history.read_bytes() == payload
        assert transaction.exists()
    if failing_call == 1:
        assert transaction.exists()
    assert (adopter / "journal.jsonl").read_bytes() == repository_before


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
@pytest.mark.parametrize("raced", ("opposite", "selected"))
def test_repair_retains_wal_when_pair_changes_after_publication(
    run_cli, tmp_path, monkeypatch, raced
):
    """Post-publication artifact races cannot become repaired success."""
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    history.write_bytes(payload[: payload.find(b"\n") + 1])
    repository = tmp_path / "journal.jsonl"
    repository_before = repository.read_bytes()
    process, ready, proceed = _rendezvous_repair(tmp_path, transaction.stem)
    try:
        readable, _, _ = select.select([ready], [], [], 10)
        assert readable, "repair did not reach post-publication rendezvous"
        assert os.read(ready, 32).strip() == b"1"
        target = repository if raced == "opposite" else history
        target.write_bytes(target.read_bytes() + b"\n")
        os.write(proceed, b"1")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:
            process.kill()
            process.wait()

    assert process.returncode == 1, (stdout, stderr)
    assert stdout == ""
    assert stderr.startswith(
        "ERROR: .validated-memory/local.jsonl: journal: the selected repair "
        "effect is visible or may be visible, but coherent successor "
        "confirmation failed: "
    )
    assert f"Transaction {transaction.stem} was retained" in stderr
    assert transaction.exists()
    assert history.read_bytes().startswith(payload)
    if raced == "opposite":
        assert repository.read_bytes() == repository_before + b"\n"
    else:
        assert repository.read_bytes() == repository_before


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
@pytest.mark.parametrize(
    "raced", ("wal-bytes", "wal-identity", "wal-mode", "temporary")
)
def test_repair_preserves_replacement_raced_before_cleanup(
    run_cli, tmp_path, monkeypatch, raced
):
    """Cleanup removes only the still-identical frozen WAL and temporary."""
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    history.write_bytes(payload[: payload.find(b"\n") + 1])
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    target = Path(entry["intention"]["path"])
    claim = entry["temporary"]
    temporary = target.parent / claim["name"]
    temporary.symlink_to(entry["intention"]["target"])
    process, ready, proceed = _rendezvous_repair(
        tmp_path, transaction.stem, "before-repair-cleanup"
    )
    try:
        readable, _, _ = select.select([ready], [], [], 10)
        assert readable
        assert os.read(ready, 32).strip() == b"1"
        replaced = transaction if raced.startswith("wal-") else temporary
        if raced == "wal-identity":
            replacement = transaction.with_suffix(".replacement")
            replacement.write_bytes(transaction.read_bytes())
            os.replace(replacement, transaction)
        elif raced == "wal-bytes":
            transaction.write_bytes(transaction.read_bytes() + b" ")
        elif raced == "wal-mode":
            transaction.chmod(0o640)
        else:
            temporary.unlink()
            temporary.symlink_to("foreign-replacement")
        replacement_state = (
            replaced.read_bytes()
            if raced.startswith("wal-")
            else os.readlink(replaced)
        )
        os.write(proceed, b"1")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:
            process.kill()
            process.wait()

    assert process.returncode == 1, (stdout, stderr)
    assert stdout == ""
    assert "selected repair effect is visible or may be visible" in stderr
    assert transaction.exists()
    if raced.startswith("wal-"):
        assert transaction.read_bytes() == replacement_state
        if raced == "wal-mode":
            assert stat.S_IMODE(transaction.stat().st_mode) == 0o640
    else:
        assert temporary.is_symlink()
        assert os.readlink(temporary) == replacement_state
    assert history.read_bytes() == payload


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
def test_repair_retains_wal_for_new_gate_after_auxiliary_cleanup(
    run_cli, tmp_path, monkeypatch
):
    """A gate before proof cleanup terminalizes with the selected WAL intact."""
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    process, ready, proceed = _rendezvous_repair(
        tmp_path, transaction.stem, "after-repair-auxiliary-cleanup"
    )
    residue = transaction.parent / ".late-foreign.tmp"
    try:
        readable, _, _ = select.select([ready], [], [], 10)
        assert readable
        assert os.read(ready, 32).strip() == b"1"
        residue.write_bytes(b"late foreign residue\n")
        os.write(proceed, b"1")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:
            process.kill()
            process.wait()

    assert process.returncode == 1, (stdout, stderr)
    assert stdout == ""
    assert stderr == (
        "ERROR: .validated-memory/local.jsonl: journal: the selected repair "
        "effect is visible or may be visible, but coherent successor "
        "confirmation failed: a new condition appeared before selected WAL "
        f"cleanup. Transaction {transaction.stem} was retained. Preserve both "
        "histories and the WAL; rerun journal --repair "
        f"{transaction.stem}.\n"
    )
    assert history.read_bytes() == payload
    assert transaction.exists()
    assert residue.read_bytes() == b"late foreign residue\n"


def test_claimless_zero_history_claim_uses_released_reconstruction(
    run_cli, tmp_path, monkeypatch
):
    """Prove released fields rebuild one exact pair, then clean only its WAL.

    A real history-claim fault removes stored claim metadata while keeping the
    target postimage. The exact two stages and selected cleanup distinguish
    released reconstruction from replay, refusal, or unrelated mutation.
    """
    assert run_cli("init", cwd=tmp_path).returncode == 0
    target = tmp_path / ".gitignore"
    target.write_text("adopter change\n", encoding="utf-8")
    before = (tmp_path / "journal.jsonl").read_bytes()
    monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", "history-claim")

    failed = run_cli("init", cwd=tmp_path)

    assert failed.returncode == 1
    transaction = next((tmp_path / ".validated-memory" / "transactions").glob("*.json"))
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    assert entry["unconfirmed"] == "history-claim"
    assert "history_append" not in entry
    assert (tmp_path / "journal.jsonl").read_bytes() == before

    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    recovered = run_cli("init", cwd=tmp_path)
    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    assert recovered.stderr == ""
    assert f"recovered .gitignore from transaction {entry['transaction']}" in recovered.stdout
    matching = [
        record for record in _records(tmp_path / "journal.jsonl")
        if record.get("transaction") == entry["transaction"]
    ]
    assert [record["stage"] for record in matching] == ["prepared", "committed"]
    assert not transaction.exists()


def _uncertain_resolution_fixture(run_cli, adopter, monkeypatch, occurrence):
    """Build one diverged valid claim at the requested history occurrence."""
    assert run_cli("init", cwd=adopter).returncode == 0
    target = adopter / ".gitignore"
    target.write_text("preimage bytes\n", encoding="utf-8")
    monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", "append:journal.jsonl")
    failed = run_cli("init", cwd=adopter)
    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    transaction = next(
        (adopter / ".validated-memory" / "transactions").glob("*.json")
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    claim = entry["history_append"]
    payload = b"".join(
        (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
        for record in claim["records"]
    )
    history = adopter / "journal.jsonl"
    entry["unconfirmed"] = "history-claim"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    prefix = history.read_bytes()[:claim["prefix"]["length"]]
    prepared_end = payload.find(b"\n") + 1
    history.write_bytes(
        prefix
        + {
            "zero": b"",
            "prepared": payload[:prepared_end],
            "complete": payload,
        }[occurrence]
    )
    target.write_text("operator divergence\n", encoding="utf-8")
    _transaction_file(
        adopter,
        "ffffffffffffffff",
        stage="aborted",
        reason="independent retained fixture",
    )
    return transaction


@pytest.mark.parametrize("occurrence", ("zero", "prepared", "complete"))
@pytest.mark.parametrize("resolution", ("accept", "abandon", "restore"))
@pytest.mark.parametrize("fault", ("write:{wal}", "install:transactions"))
def test_resolution_reestablishes_uncertain_claim_before_any_disposition(
    run_cli, tmp_path, monkeypatch, occurrence, resolution, fault
):
    """An uncertain proof-WAL failure precedes every resolution effect.

    The three stored-claim occurrences and both WAL persistence boundaries
    prove that target, histories, observation, cleanup, and an unrelated WAL
    remain exact when re-establishment cannot be confirmed.
    """
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction = _uncertain_resolution_fixture(
        run_cli, adopter, monkeypatch, occurrence
    )
    before = _final_tree_snapshot(tmp_path)
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        fault.format(wal=transaction.name),
    )

    result = run_cli(
        "journal", "--resolve", transaction.stem, f"--{resolution}", cwd=adopter
    )

    failed_path = (
        f".validated-memory/transactions/{transaction.name}"
        if fault.startswith("write:")
        else ".validated-memory/transactions"
    )
    reason = (
        f"[Errno 5] injected persistence failure: '{failed_path}'"
        if fault.startswith("write:")
        else "install of .validated-memory/transactions/"
        f"{transaction.name} is visible, but its durability is unconfirmed: "
        f"[Errno 5] injected persistence failure: '{failed_path}'"
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        1,
        "",
        f"ERROR: .validated-memory/transactions/{transaction.name}: journal: "
        "the selected resolution cannot continue because retained evidence "
        "could not be durably and coherently confirmed: "
        f"{reason}. Transaction "
        f"{transaction.stem} and all available evidence were retained; preserve "
        "them and rerun init\n",
    )
    assert _final_tree_snapshot(tmp_path) == before


def _unknown_selected_provision(adopter, other_order):
    """Make an unknown claimless WAL and an independent exact provision."""
    transaction, prepared, history = _claimless_prepared_provision(adopter)
    (adopter / ".validated-memory" / "lock").unlink(missing_ok=True)
    selected = json.loads(transaction.read_text(encoding="utf-8"))
    selected["stage"] = "prepared"
    transaction.write_text(
        json.dumps(selected, sort_keys=True) + "\n", encoding="utf-8"
    )
    target = adopter / selected["intention"]["path"]
    if target.is_dir():
        target.rmdir()
    elif target.exists() or target.is_symlink():
        target.unlink()
    target.write_text("selected unknown state\n", encoding="utf-8")
    other_id = "0000000000000000" if other_order == "before" else "ffffffffffffffff"
    other = {
        **selected,
        "at": (
            "1900-01-01T00:00:00Z"
            if other_order == "before"
            else "2999-01-01T00:00:00Z"
        ),
        "run": "independent-run",
        "transaction": other_id,
        "intention": {**selected["intention"], "path": "independent.txt"},
    }
    transaction.with_name(f"{other_id}.json").write_text(
        json.dumps(other, sort_keys=True) + "\n", encoding="utf-8"
    )
    other_prepared = {
        **prepared,
        "at": other["at"],
        "run": other["run"],
        "transaction": other_id,
        "path": "independent.txt",
    }
    records = _records(history)
    selected_index = next(
        index
        for index, record in enumerate(records)
        if record.get("transaction") == transaction.stem
    )
    records.insert(
        selected_index if other_order == "before" else selected_index + 1,
        other_prepared,
    )
    _rewrite(history, records)
    return transaction


@pytest.mark.parametrize("resolution", ("accept", "abandon", "restore"))
@pytest.mark.parametrize("other_order", ("before", "after"))
def test_unknown_selected_provision_refuses_before_every_disposition(
    run_cli, tmp_path, resolution, other_order
):
    """A selected prepared gap cannot borrow or be discharged by other work.

    Both orders include an independent exact provision. Whole-tree equality
    proves every disposition refuses before target, observation, cleanup, or
    an unrelated provision changes.
    """
    transaction = _unknown_selected_provision(tmp_path, other_order)
    before = _final_tree_snapshot(tmp_path)

    result = run_cli(
        "journal", "--resolve", transaction.stem, f"--{resolution}", cwd=tmp_path
    )

    assert (result.returncode, result.stdout, result.stderr) == (
        1,
        "",
        f"ERROR: .gitignore: journal: transaction {transaction.stem} cannot be "
        "resolved because its exact prepared history provision remains "
        "unfinished while its target state is unknown. Preserve the WAL and "
        "histories; restore an accepted exact history state from a trusted "
        "source, then run journal --check. Nothing has been changed.\n",
    )
    assert _final_tree_snapshot(tmp_path) == before


def test_symlink_staging_collision_preserves_foreign_entry(
    run_cli, tmp_path, monkeypatch
):
    assert run_cli("init", cwd=tmp_path).returncode == 0
    harness = _external_harness_path(tmp_path, "h")
    harness.parent.mkdir(parents=True)
    sentinel = harness.parent / ".h.collision.tmp"
    sentinel.write_bytes(b"foreign")
    sentinel.chmod(0o640)
    before = (sentinel.read_bytes(), stat.S_IMODE(sentinel.stat().st_mode), sentinel.is_symlink())
    monkeypatch.setenv("VALIDATED_MEMORY_SYMLINK_TEMP_NAME", sentinel.name)
    result = run_cli("init", "--harness-memory", str(harness), cwd=tmp_path)
    assert result.returncode == 0
    assert "could not be linked" in result.stderr
    assert (sentinel.read_bytes(), stat.S_IMODE(sentinel.stat().st_mode), sentinel.is_symlink()) == before


def test_reserved_regular_staging_replacement_does_not_mutate_foreign_entry(
    run_cli, tmp_path, monkeypatch
):
    """Descriptor metadata cannot chmod a replacement at the staging name."""
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, tmp_path, monkeypatch
    )
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT", "swap-staging:local.jsonl"
    )

    result = run_cli("journal", "--repair", transaction.stem, cwd=tmp_path)

    assert result.returncode == 1, result.stderr
    foreign = list(
        (tmp_path / ".validated-memory").glob(".local.jsonl.*.tmp.foreign")
    )
    assert len(foreign) == 1
    foreign = foreign[0]
    assert foreign.is_file() and not foreign.is_symlink()
    assert foreign.read_bytes() == b"foreign staging entry\n"
    assert stat.S_IMODE(foreign.stat().st_mode) == 0o640
    assert transaction.exists()
    assert "does not prove this repair" in result.stderr
    assert "No target or permanent-history change was left" in result.stderr


def test_private_storage_crash_seam_leaves_prefix_and_claimed_staging(
    run_cli, tmp_path, monkeypatch
):
    monkeypatch.setenv("VALIDATED_MEMORY_STORAGE_CRASH", "history-prefix:3")
    crashed = run_cli("init", cwd=tmp_path)
    assert crashed.returncode == 71
    assert (tmp_path / "journal.jsonl").read_bytes()
    assert list((tmp_path / ".validated-memory" / "transactions").glob("*.json"))

    monkeypatch.delenv("VALIDATED_MEMORY_STORAGE_CRASH")
    fresh = tmp_path / "staging"
    monkeypatch.setenv("VALIDATED_MEMORY_STORAGE_CRASH", "staged-before-install:*")
    # The existing partial history gates this second invocation, so use a
    # separate adopter for the staged-before-install subprocess assertion.
    other = tmp_path / "other"
    other.mkdir()
    assert run_cli("init", cwd=other).returncode == 0
    harness = _external_harness_path(other, "h")
    staged = run_cli("init", "--harness-memory", str(harness), cwd=other)
    assert staged.returncode == 71
    assert list(harness.parent.glob(".h.*.tmp"))


def test_check_reports_private_residue_without_removing_it(run_cli, tmp_path):
    assert run_cli("init", cwd=tmp_path).returncode == 0
    residue = tmp_path / ".validated-memory" / "transactions" / "legacy.tmp"
    residue.write_bytes(b"foreign")
    residue.chmod(0o640)
    before = (residue.read_bytes(), stat.S_IMODE(residue.stat().st_mode), residue.is_symlink())
    result = run_cli("journal", "--check", cwd=tmp_path)
    assert result.returncode == 1
    assert "retained private residue" in result.stderr
    assert (residue.read_bytes(), stat.S_IMODE(residue.stat().st_mode), residue.is_symlink()) == before


def test_journal_resolve_refuses_an_id_no_transaction_carries(run_cli, tmp_path):
    """A well-formed unknown transaction ID is a gating state error."""
    assert run_cli("init", cwd=tmp_path).returncode == 0

    result = run_cli(
        "journal", "--resolve", "deadbeefdeadbeef", "--accept", cwd=tmp_path
    )

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "no unresolved transaction deadbeefdeadbeef" in result.stderr
    assert "Nothing has been changed." in result.stderr, result.stderr


def test_journal_resolve_needs_exactly_one_of_the_three_flags(run_cli, tmp_path):
    """Journal's product-authored usage errors follow their validation order."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    before = _final_tree_snapshot(tmp_path)
    cases = (
        (
            ("--resolve", "aaaaaaaaaaaaaaaa"),
            "--resolve requires exactly one of --accept, --restore or --abandon",
        ),
        (
            ("--resolve", "aaaaaaaaaaaaaaaa", "--accept", "--abandon"),
            "--resolve requires exactly one of --accept, --restore or --abandon",
        ),
        (("--accept",), "--accept requires --resolve"),
        # RESOLUTIONS order, not command-line order, selects the first error.
        (("--abandon", "--restore"), "--restore requires --resolve"),
        (
            ("--check", "--resolve", "aaaaaaaaaaaaaaaa", "--accept"),
            "--resolve may not be combined with --check, which is read-only",
        ),
        # A blank resolve ID precedes both check and resolution-count errors.
        (
            ("--resolve", "", "--check", "--accept", "--abandon"),
            "--resolve requires the id of a transaction",
        ),
        (
            ("--resolve", "   ", "--accept"),
            "--resolve requires the id of a transaction",
        ),
        # Repair conflicts precede its blank-ID check in implemented order.
        (
            (
                "--repair", "", "--resolve", "aaaaaaaaaaaaaaaa", "--check",
                "--accept",
            ),
            "--repair may not be combined with --resolve",
        ),
        (
            ("--repair", "", "--check", "--accept"),
            "--repair may not be combined with --check",
        ),
        (
            ("--repair", "", "--accept"),
            "--repair may not be combined with a resolution flag",
        ),
        (("--repair", ""), "--repair requires the id of a transaction"),
    )

    for arguments, expected in cases:
        result = run_cli("journal", *arguments, cwd=tmp_path)
        assert result.returncode == 2, (arguments, result.stdout, result.stderr)
        assert result.stdout == "", (arguments, result.stdout)
        lines = result.stderr.splitlines()
        assert lines[0].startswith("usage: validated-memory journal "), (
            arguments,
            result.stderr,
        )
        assert lines[-1] == f"validated-memory journal: error: {expected}", (
            arguments,
            result.stderr,
        )
        assert _final_tree_snapshot(tmp_path) == before, arguments


def test_a_missing_preimage_blob_for_a_closed_record_is_never_an_error(
    run_cli, tmp_path
):
    """Missing blobs named only by closed history do not damage a clone.

    Open-transaction blobs have a stricter rule pinned by the restore test."""
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    assert run_cli("init", cwd=tmp_path).returncode == 0
    replaced = [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record["path"] == ".gitignore" and record.get("preimage")
    ]
    assert replaced, "init recorded no preimage for the ignore file"
    for blob in (tmp_path / ".validated-memory" / "preimages").iterdir():
        blob.unlink()

    checked = run_cli("journal", "--check", cwd=tmp_path)
    again = run_cli("init", cwd=tmp_path)

    assert checked.returncode == 0, (checked.stdout, checked.stderr)
    assert "preimage" not in checked.stderr, checked.stderr
    assert again.returncode == 0, (again.stdout, again.stderr)
    assert again.stderr == "", again.stderr


def test_two_halves_of_one_transaction_that_disagree_are_reported(
    run_cli, tmp_path
):
    """Two records sharing an ID must agree on mode; disagreement gates."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    path = tmp_path / "journal.jsonl"
    rewritten = []
    for record in _records(path):
        if record["path"] == "validated-memory.md" and record["stage"] == "committed":
            record = dict(record, mode=0o600)
            transaction = record["transaction"]
        rewritten.append(json.dumps(record, sort_keys=True))
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        f"records of transaction {transaction} disagree on mode" in result.stderr
    ), result.stderr
    assert "validated-memory.md" in result.stderr, result.stderr
    assert "journal: 13 record(s), 1 error(s)" in result.stdout, result.stdout


def test_journal_resolve_restore_keeps_the_bytes_it_discards(run_cli, tmp_path):
    """Restoring absence parks discarded file bytes and reports their blob.

    Parking alone appends no history."""
    transaction = _created_then_diverged(tmp_path)
    intruder = "the adopter's own words\n"
    (tmp_path / "knowledge-extension.md").write_text(intruder, encoding="utf-8")
    digest = hashlib.sha256(intruder.encode("utf-8")).hexdigest()
    before = _records(tmp_path / "journal.jsonl")

    result = run_cli("journal", "--resolve", transaction, "--restore", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not (tmp_path / "knowledge-extension.md").exists()
    blob = tmp_path / ".validated-memory" / "preimages" / digest
    assert blob.read_text(encoding="utf-8") == intruder
    assert (
        f"journal: resolved {transaction} (--restore); the discarded bytes "
        f"are kept at .validated-memory/preimages/{digest}" in result.stdout
    ), result.stdout
    # A copy in the vault is not a record: nothing was appended.
    assert _records(tmp_path / "journal.jsonl") == before
    assert not _transactions(tmp_path), _transactions(tmp_path)


def test_journal_resolve_restore_takes_away_what_an_absent_preimage_names(
    run_cli, tmp_path
):
    """Restoring absence refuses a nonempty directory, then removes it when empty.

    The refusal preserves contents and transaction; success reports no
    discarded bytes and records no observation."""
    transaction = _created_then_diverged(tmp_path)
    # The path diverged into something that is not a file at all.
    (tmp_path / "knowledge-extension.md").unlink()
    (tmp_path / "knowledge-extension.md").mkdir()
    (tmp_path / "knowledge-extension.md" / "left-behind.md").write_text(
        "kept\n", encoding="utf-8"
    )

    occupied = run_cli("journal", "--resolve", transaction, "--restore", cwd=tmp_path)

    assert occupied.returncode == 1, occupied.stdout
    assert "could not be put back" in occupied.stderr, occupied.stderr
    assert (tmp_path / "knowledge-extension.md" / "left-behind.md").exists()
    assert len(_transactions(tmp_path)) == 1, _transactions(tmp_path)

    (tmp_path / "knowledge-extension.md" / "left-behind.md").unlink()
    result = run_cli("journal", "--resolve", transaction, "--restore", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not (tmp_path / "knowledge-extension.md").exists()
    assert "discarded bytes" not in result.stdout, result.stdout
    assert not _transactions(tmp_path), _transactions(tmp_path)
    assert not [
        record
        for record in _records(tmp_path / "journal.jsonl")
        if record["path"] == "knowledge-extension.md"
        and record["op"] == "observe"
    ]


def test_a_transaction_the_next_run_resolves_is_not_an_operator_s_to_close(
    run_cli, tmp_path
):
    """All operator flags refuse a recoverable transaction without changing it.

    Init remains the path that completes and removes this residue."""
    transaction = _diverged(tmp_path, kill_after=None)
    before = _records(tmp_path / "journal.jsonl")
    residue = (
        tmp_path / ".validated-memory" / "transactions" / f"{transaction}.json"
    ).read_text(encoding="utf-8")

    for flag in ("--accept", "--abandon", "--restore"):
        result = run_cli("journal", "--resolve", transaction, flag, cwd=tmp_path)

        assert result.returncode == 1, (flag, result.stdout, result.stderr)
        assert "is recoverable" in result.stderr, (flag, result.stderr)
        assert "validated-memory init" in result.stderr, (flag, result.stderr)
        assert "Nothing has been changed." in result.stderr, (flag, result.stderr)
        assert _records(tmp_path / "journal.jsonl") == before, flag
        assert (
            tmp_path / ".validated-memory" / "transactions" / f"{transaction}.json"
        ).read_text(encoding="utf-8") == residue, flag

    # And the run that IS its resolution finishes it.
    assert run_cli("init", cwd=tmp_path).returncode == 0
    assert not _transactions(tmp_path), _transactions(tmp_path)


# --- the two directories the plugin owns are real directories -----------------


def test_a_symlinked_transactions_directory_writes_nothing_outside_the_tree(
    run_cli, tmp_path, monkeypatch
):
    """A symlinked transaction directory gates before writing outside the tree.

    The fault point would preserve any attempted transaction, making the
    no-write assertion non-vacuous."""
    tree = tmp_path / "tree"
    outside = tmp_path / "outside"
    tree.mkdir()
    outside.mkdir()
    (tree / ".validated-memory").mkdir()
    (tree / ".validated-memory" / "transactions").symlink_to(outside)

    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-transaction")
    result = run_cli("init", cwd=tree)

    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert ".validated-memory/transactions" in result.stderr, result.stderr
    assert "is a symlink" in result.stderr, result.stderr
    assert not list(outside.iterdir()), sorted(p.name for p in outside.iterdir())


def test_a_plain_file_where_the_transactions_directory_goes_is_a_finding(
    run_cli, tmp_path
):
    """`--check` reports it, and does not raise `NotADirectoryError` at `iterdir`."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    transactions = tmp_path / ".validated-memory" / "transactions"
    for leftover in transactions.iterdir():
        leftover.unlink()
    transactions.rmdir()
    transactions.write_text("not a directory\n", encoding="utf-8")

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert result.stderr.startswith("ERROR: .validated-memory/transactions:"), (
        result.stderr
    )
    assert "not a directory" in result.stderr, result.stderr


def test_a_symlinked_preimage_store_parks_nothing_outside_the_tree(
    run_cli, tmp_path
):
    """A symlinked preimage store gates without outside writes or target changes."""
    tree = tmp_path / "tree"
    outside = tmp_path / "outside"
    tree.mkdir()
    outside.mkdir()
    (tree / ".gitignore").write_text("build/\n", encoding="utf-8")
    (tree / ".validated-memory").mkdir()
    (tree / ".validated-memory" / "preimages").symlink_to(outside)

    result = run_cli("init", cwd=tree)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert ".validated-memory/preimages" in result.stderr, result.stderr
    assert not list(outside.iterdir()), sorted(p.name for p in outside.iterdir())
    assert (tree / ".gitignore").read_text(encoding="utf-8") == "build/\n"


# --- a transaction file is damaged unless it is this project's ----------------


def test_a_transaction_file_that_is_not_text_is_damaged_and_not_a_traceback(
    run_cli, tmp_path
):
    """Non-UTF-8 transaction bytes gate as damaged and remain for inspection."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    residue = tmp_path / ".validated-memory" / "transactions" / "5555555555555555.json"
    residue.parent.mkdir(parents=True, exist_ok=True)
    residue.write_bytes(b"\xff\xfe not text at all")

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert "damaged transaction 5555555555555555:" in result.stderr, result.stderr
    assert "not valid UTF-8" in result.stderr, result.stderr
    assert residue.exists(), "a damaged file is left where it is"


def test_a_transaction_whose_schema_this_reader_does_not_know_is_damaged(
    run_cli, tmp_path
):
    """Unknown, nonnumeric and absent transaction schemas are damaged.

    All transaction files remain unresolved rather than being executed."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    _transaction_file(tmp_path, "6666666666666666", schema=999)
    _transaction_file(tmp_path, "7777777777777777", schema="one")
    entry = _transaction_file(tmp_path, "8888888888888888")
    del entry["schema"]
    (
        tmp_path / ".validated-memory" / "transactions" / "8888888888888888.json"
    ).write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert (
        "damaged transaction 6666666666666666: its schema is 999 and this "
        "plugin reads up to 1" in result.stderr
    ), result.stderr
    for damaged in ("7777777777777777", "8888888888888888"):
        assert (
            f"damaged transaction {damaged}: it names no schema" in result.stderr
        ), result.stderr
    assert len(_transactions(tmp_path)) == 3, _transactions(tmp_path)


def test_a_transaction_that_is_not_the_id_its_file_is_named_is_damaged(
    run_cli, tmp_path
):
    """A transaction ID must equal its filename stem."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    _transaction_file(tmp_path, "9999999999999999", transaction="aaaabbbbccccdddd")

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        "damaged transaction 9999999999999999: it calls itself transaction "
        "aaaabbbbccccdddd and its file is named 9999999999999999"
        in result.stderr
    ), result.stderr


def test_a_transaction_filed_under_another_adoption_is_damaged(run_cli, tmp_path):
    """A mutation of somebody else's tree is not one this history may record."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    mine = _records(tmp_path / "journal.jsonl")[0]["adoption"]
    foreign = "f" * 16
    _transaction_file(tmp_path, "abababababababab", adoption=foreign)

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        f"damaged transaction abababababababab: it belongs to adoption "
        f"{foreign}, this project is {mine}" in result.stderr
    ), result.stderr


def test_another_trees_transaction_is_never_completed_into_this_history(
    run_cli, tmp_path, monkeypatch
):
    """A foreign adoption's transaction gates without changing local history.

    The damaged residue remains available for inspection."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-published")
    assert run_cli("init", cwd=first).returncode == 70
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
    assert run_cli("init", cwd=second).returncode == 0

    residue = sorted((first / ".validated-memory" / "transactions").glob("*.json"))
    assert len(residue) == 1, residue
    transaction = json.loads(residue[0].read_text(encoding="utf-8"))
    target = second / ".validated-memory" / "transactions" / residue[0].name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(residue[0].read_text(encoding="utf-8"), encoding="utf-8")
    before = (second / "journal.jsonl").read_text(encoding="utf-8")

    result = run_cli("init", cwd=second)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert (
        f"damaged transaction {transaction['transaction']}: it belongs to "
        f"adoption {transaction['adoption']}" in result.stderr
    ), result.stderr
    assert (second / "journal.jsonl").read_text(encoding="utf-8") == before
    assert target.exists(), "a damaged transaction is left for inspection"


def test_a_transaction_naming_an_operation_no_intention_carries_is_damaged(
    run_cli, tmp_path
):
    """Transactions reject legacy record-only operations and observations.

    Both damaged files stay unresolved."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    for transaction_id, op in (
        ("cdcdcdcdcdcdcdcd", "patch"),
        ("efefefefefefefef", "observe"),
    ):
        _transaction_file(
            tmp_path,
            transaction_id,
            intention={
                "op": op,
                "purpose": "init",
                "path": "validated-memory.md",
                "durability": "repo",
            },
        )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        "damaged transaction cdcdcdcdcdcdcdcd: its intention names no "
        "operation this plugin prepares" in result.stderr
    ), result.stderr
    assert (
        "damaged transaction efefefefefefefef: its intention is an "
        "observation" in result.stderr
    ), result.stderr
    assert len(_transactions(tmp_path)) == 2, _transactions(tmp_path)


def test_a_transaction_whose_states_are_not_states_is_damaged(run_cli, tmp_path):
    """Reject state envelopes whose digest or symlink target has the wrong type."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    _transaction_file(
        tmp_path,
        "1212121212121212",
        postimage={"kind": "file", "digest": 42, "mode": 420},
    )
    _transaction_file(
        tmp_path,
        "3434343434343434",
        preimage={"kind": "symlink", "target": ["memory"], "mode": 511},
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    for transaction_id in ("1212121212121212", "3434343434343434"):
        assert (
            f"damaged transaction {transaction_id}: its preimage or "
            "postimage is in no state this plugin knows" in result.stderr
        ), result.stderr


# --- bytes that cannot be read are `unknown`, never a traceback ---------------


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
def test_an_unreadable_path_says_which_stage_its_transaction_reached(
    run_cli, tmp_path, monkeypatch
):
    """Unreadable-path diagnostics distinguish prepared from published stages."""
    published = tmp_path / "published"
    prepared = tmp_path / "prepared"
    published.mkdir()
    prepared.mkdir()
    # An ignore file that is already there makes the killed intention an
    # `append` over a real file, so `prepared` has bytes at the path to
    # make unreadable. A `create` at the same stage has published nothing.
    (prepared / ".gitignore").write_text("build/\n", encoding="utf-8")

    for tree, fault, stage in (
        (published, "after-published", "published"),
        (prepared, "after-transaction", "prepared"),
    ):
        monkeypatch.setenv("VALIDATED_MEMORY_FAULT", fault)
        assert run_cli("init", cwd=tree).returncode == 70
        monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
        entry = _transactions(tree)[0]
        assert entry["stage"] == stage, entry
        (tree / ".gitignore").chmod(0o000)

        try:
            result = run_cli("init", cwd=tree)

            assert result.returncode == 1, (result.stdout, result.stderr)
            assert "Traceback" not in result.stderr, result.stderr
            if stage == "published":
                assert (
                    f"transaction {entry['transaction']} published .gitignore, "
                    "and .gitignore cannot be read" in result.stderr
                ), result.stderr
                assert (
                    "whether what it published is still there" in result.stderr
                ), result.stderr
            else:
                assert (
                    f"transaction {entry['transaction']} prepared a mutation "
                    "of .gitignore, and .gitignore cannot be read"
                    in result.stderr
                ), result.stderr
                assert "whether it ran" in result.stderr, result.stderr
        finally:
            (tree / ".gitignore").chmod(0o644)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
def test_a_path_whose_bytes_cannot_be_read_classifies_as_unknown(
    run_cli, tmp_path, monkeypatch
):
    """Unreadable file bytes classify as unknown across check, recovery and resolve.

    Each path gates without traceback and keeps the transaction unresolved."""
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-published")
    assert run_cli("init", cwd=tmp_path).returncode == 70
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
    transaction = _transactions(tmp_path)[0]["transaction"]
    (tmp_path / ".gitignore").chmod(0o000)

    try:
        result = run_cli("journal", "--check", cwd=tmp_path)

        assert result.returncode == 1, result.stdout
        assert "Traceback" not in result.stderr, result.stderr
        assert (
            f"open transaction {transaction} (published) on .gitignore: unknown"
            in result.stderr
        ), result.stderr

        # And the run that meets it reports it rather than raising, leaving
        # the transaction exactly where it was.
        recovered = run_cli("init", cwd=tmp_path)
        assert recovered.returncode == 1, (recovered.stdout, recovered.stderr)
        assert "Traceback" not in recovered.stderr, recovered.stderr
        assert ".gitignore cannot be read" in recovered.stderr, recovered.stderr
        assert len(_transactions(tmp_path)) == 1, _transactions(tmp_path)

        # As does the operator's way out: it cannot say what state it would
        # be closing over, so it closes nothing.
        refused = run_cli(
            "journal", "--resolve", transaction, "--accept", cwd=tmp_path
        )
        assert refused.returncode == 1, (refused.stdout, refused.stderr)
        assert "Traceback" not in refused.stderr, refused.stderr
        assert "could not be read" in refused.stderr, refused.stderr
        assert "Nothing has been changed." in refused.stderr, refused.stderr
        assert len(_transactions(tmp_path)) == 1, _transactions(tmp_path)
    finally:
        (tmp_path / ".gitignore").chmod(0o644)


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
def test_the_executor_refuses_a_path_whose_state_it_cannot_read(run_cli, tmp_path):
    """An unreadable expected-state check gates with no transaction or path record."""
    (tmp_path / "knowledge").write_text("not a directory\n", encoding="utf-8")
    (tmp_path / "knowledge").chmod(0o000)

    try:
        result = run_cli("init", cwd=tmp_path)

        assert result.returncode == 1, (result.stdout, result.stderr)
        assert "Traceback" not in result.stderr, result.stderr
        assert (
            "knowledge could not be read, so nothing here can say what state "
            "it is in" in result.stderr
        ), result.stderr
        assert "Nothing has been written." in result.stderr, result.stderr
        assert not _transactions(tmp_path), "a refusal opens no transaction"
        assert not [
            entry
            for entry in _records(tmp_path / "journal.jsonl")
            if entry["path"] == "knowledge"
        ], "and records nothing"
    finally:
        (tmp_path / "knowledge").chmod(0o644)


# --- an id-carrying record is a half of exactly one act -----------------------


def _rewrite(path, lines):
    """Replace a journal with `lines` (records), in file order."""
    path.write_text(
        "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in lines),
        encoding="utf-8",
    )


def _seed_repository_and_local_histories(run_cli, tmp_path):
    """Create one adopter with ordinary valid records in both histories."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    harness = tmp_path / "harness" / "memory"
    result = run_cli(
        "init", "--harness-memory", str(harness), cwd=adopter
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    repository = adopter / "journal.jsonl"
    local = adopter / ".validated-memory" / "local.jsonl"
    return adopter, repository, local


def _complete_pair(records, *, fields=()):
    """Return one prepared/committed ID pair carrying every named field."""
    by_id = {}
    for entry in records:
        transaction = entry.get("transaction")
        if transaction:
            by_id.setdefault(transaction, []).append(entry)
    for entries in by_id.values():
        if (
            [entry["stage"] for entry in entries] == ["prepared", "committed"]
            and all(field in entry for field in fields for entry in entries)
        ):
            return entries
    raise AssertionError(f"no complete transaction pair carrying {fields}")


def test_journal_check_rejects_one_id_in_both_complete_histories(
    run_cli, tmp_path
):
    """One project-wide transaction ID cannot name two durability acts."""
    adopter, repository, local = _seed_repository_and_local_histories(
        run_cli, tmp_path
    )
    repository_records = _records(repository)
    local_records = _records(local)
    repository_pair = _complete_pair(repository_records)
    local_pair = _complete_pair(local_records)
    shared = repository_pair[0]["transaction"]
    _rewrite(
        local,
        [
            {**entry, "transaction": shared}
            if entry in local_pair
            else entry
            for entry in local_records
        ],
    )
    repository_before = repository.read_bytes()
    local_before = local.read_bytes()
    total = len(repository_records) + len(local_records)

    plain = run_cli("journal", cwd=adopter)
    checked = run_cli("journal", "--check", cwd=adopter)

    message = (
        f"transaction {shared} is recorded across repo and local histories"
    )
    assert plain.returncode == 0, (plain.stdout, plain.stderr)
    assert plain.stdout == f"journal: {total} record(s)\n"
    assert plain.stderr == ""
    assert checked.returncode == 1, (checked.stdout, checked.stderr)
    assert checked.stderr.count(message) == 1, checked.stderr
    assert checked.stderr.startswith(
        f"ERROR: {repository_pair[0]['path']}: journal: {message}\n"
    ), checked.stderr
    assert f"journal: {total} record(s), 1 error(s)" in checked.stdout
    assert repository.read_bytes() == repository_before
    assert local.read_bytes() == local_before


def test_journal_check_reports_split_cross_history_halves(run_cli, tmp_path):
    """Split halves retain truthful local findings beside global ID reuse."""
    adopter, repository, local = _seed_repository_and_local_histories(
        run_cli, tmp_path
    )
    repository_records = _records(repository)
    local_records = _records(local)
    repository_pair = _complete_pair(repository_records)
    local_pair = _complete_pair(local_records)
    shared = repository_pair[0]["transaction"]
    split_repository = [
        entry for entry in repository_records if entry is not repository_pair[1]
    ]
    split_local = [
        {**entry, "transaction": shared}
        if entry is local_pair[1]
        else entry
        for entry in local_records
        if entry not in local_pair or entry is local_pair[1]
    ]
    _rewrite(repository, split_repository)
    _rewrite(local, split_local)
    total = len(split_repository) + len(split_local)

    result = run_cli("journal", "--check", cwd=adopter)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stderr.count(
        f"transaction {shared} is recorded across repo and local histories"
    ) == 1, result.stderr
    assert "unfinished transaction from run" in result.stderr, result.stderr
    assert (
        f"records of transaction {shared}: committed without a prepared half"
        in result.stderr
    ), result.stderr
    assert f"journal: {total} record(s), 3 error(s)" in result.stdout


def test_distinct_transaction_ids_across_histories_remain_clean(
    run_cli, tmp_path
):
    """Ordinary repo and local pairs remain independent when IDs differ."""
    adopter, repository, local = _seed_repository_and_local_histories(
        run_cli, tmp_path
    )
    repository_ids = {
        entry["transaction"]
        for entry in _records(repository)
        if entry.get("transaction")
    }
    local_ids = {
        entry["transaction"]
        for entry in _records(local)
        if entry.get("transaction")
    }
    assert repository_ids.isdisjoint(local_ids)

    result = run_cli("journal", "--check", cwd=adopter)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ""
    assert "0 error(s)" in result.stdout


# --- one coherent generation across both permanent histories ----------------


def _rendezvous_journal(adopter, *arguments):
    ready_read, ready_write = os.pipe()
    continue_read, continue_write = os.pipe()
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "VALIDATED_MEMORY_TEST_RENDEZVOUS": "after-first-history-read",
        "VALIDATED_MEMORY_TEST_READY_FD": str(ready_write),
        "VALIDATED_MEMORY_TEST_CONTINUE_FD": str(continue_read),
    }
    process = subprocess.Popen(
        [sys.executable, "-P", "-m", "validated_memory", "journal", *arguments],
        cwd=adopter,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        pass_fds=(ready_write, continue_read),
    )
    os.close(ready_write)
    os.close(continue_read)
    return process, ready_read, continue_write


def _await_history_attempt(ready):
    readable, _, _ = select.select([ready], [], [], 10)
    assert readable, "the history acquisition rendezvous did not become ready"
    return int(os.read(ready, 32).strip())


def _records_read_failure_mutant(tmp_path, failing_calls):
    """Copy the package and fail selected full descriptor reads after byte two."""
    mutant_root = tmp_path / "mutant"
    shutil.copytree(REPO_ROOT / "validated_memory", mutant_root / "validated_memory")
    records = mutant_root / "validated_memory" / "journal" / "records.py"
    source = records.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_read_to_eof"
    )
    replacement = f'''_MUTANT_READ_CALLS = 0


def _read_to_eof(descriptor):
    global _MUTANT_READ_CALLS
    import errno
    _MUTANT_READ_CALLS += 1
    call = _MUTANT_READ_CALLS
    chunks = []
    size = 0
    while True:
        chunk = os.read(descriptor, 1)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if call in {set(failing_calls)!r} and size >= 2:
            raise OSError(errno.EIO, "injected descriptor read failure")
'''
    lines = source.splitlines(keepends=True)
    lines[function.lineno - 1:function.end_lineno] = [replacement]
    records.write_text("".join(lines), encoding="utf-8")
    return mutant_root


def _records_fstat_failure_mutant(tmp_path, failing_calls):
    """Copy the package and fail selected acquisition fstat operations."""
    mutant_root = tmp_path / "mutant"
    shutil.copytree(REPO_ROOT / "validated_memory", mutant_root / "validated_memory")
    records = mutant_root / "validated_memory" / "journal" / "records.py"
    source = records.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_descriptor_stat"
    )
    replacement = f'''_MUTANT_FSTAT_CALLS = 0


def _descriptor_stat(descriptor):
    global _MUTANT_FSTAT_CALLS
    import errno
    _MUTANT_FSTAT_CALLS += 1
    if _MUTANT_FSTAT_CALLS in {set(failing_calls)!r}:
        raise OSError(errno.EIO, "injected descriptor fstat failure")
    return os.fstat(descriptor)
'''
    lines = source.splitlines(keepends=True)
    lines[function.lineno - 1:function.end_lineno] = [replacement]
    records.write_text("".join(lines), encoding="utf-8")
    return mutant_root


def _run_mutant_journal(mutant_root, adopter, *arguments):
    return subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "journal", *arguments],
        cwd=adopter,
        env={**os.environ, "PYTHONPATH": str(mutant_root)},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(os.name != "posix", reason="open-name replacement is POSIX-only")
def test_journal_retries_a_transient_pair_replacement_without_mixing_generations(
    run_cli, tmp_path
):
    adopter, repository, local = _seed_repository_and_local_histories(
        run_cli, tmp_path
    )
    old_total = len(_records(repository)) + len(_records(local))
    repository_line = repository.read_bytes().splitlines(keepends=True)[0]
    local_line = local.read_bytes().splitlines(keepends=True)[0]
    process, ready, proceed = _rendezvous_journal(adopter)
    try:
        assert _await_history_attempt(ready) == 1
        replacement = repository.with_name("repository.next")
        replacement.write_bytes(repository.read_bytes() + repository_line)
        os.replace(replacement, repository)
        replacement = local.with_name("local.next")
        replacement.write_bytes(local.read_bytes() + local_line)
        os.replace(replacement, local)
        os.write(proceed, b"1")

        assert _await_history_attempt(ready) == 2
        os.write(proceed, b"1")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:  # pragma: no cover - only on a failure
            process.kill()
            process.communicate()

    assert process.returncode == 0, (stdout, stderr)
    assert stdout == f"journal: {old_total + 2} record(s)\n"
    assert stderr == ""


@pytest.mark.skipif(os.name != "posix", reason="open-name replacement is POSIX-only")
def test_journal_check_retries_a_transient_pair_replacement_cleanly(
    run_cli, tmp_path
):
    adopter, repository, local = _seed_repository_and_local_histories(
        run_cli, tmp_path
    )
    total = len(_records(repository)) + len(_records(local))
    process, ready, proceed = _rendezvous_journal(adopter, "--check")
    try:
        assert _await_history_attempt(ready) == 1
        for history in (repository, local):
            replacement = history.with_name(history.name + ".next")
            replacement.write_bytes(history.read_bytes())
            os.replace(replacement, history)
        os.write(proceed, b"1")

        assert _await_history_attempt(ready) == 2
        os.write(proceed, b"1")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:  # pragma: no cover - only on a failure
            process.kill()
            process.communicate()

    assert process.returncode == 0, (stdout, stderr)
    assert stdout == f"journal: {total} record(s), 0 error(s)\n"
    assert stderr == ""


@pytest.mark.skipif(os.name != "posix", reason="open-name replacement is POSIX-only")
@pytest.mark.parametrize("arguments", [(), ("--check",)])
def test_journal_refuses_three_unstable_pair_attempts_exactly(
    run_cli, tmp_path, arguments
):
    adopter, repository, local = _seed_repository_and_local_histories(
        run_cli, tmp_path
    )
    process, ready, proceed = _rendezvous_journal(adopter, *arguments)
    try:
        for expected in (1, 2, 3):
            assert _await_history_attempt(ready) == expected
            for history in (repository, local):
                replacement = history.with_name(history.name + f".{expected}.next")
                replacement.write_bytes(history.read_bytes() + b"\n")
                os.replace(replacement, history)
            os.write(proceed, b"1")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:  # pragma: no cover - only on a failure
            process.kill()
            process.communicate()

    assert process.returncode == 1, (stdout, stderr)
    assert stdout == "journal: 0 record(s), 1 error(s)\n"
    assert stderr == (
        "ERROR: journal.jsonl + .validated-memory/local.jsonl: journal: "
        "journal histories changed during inspection; rerun the command\n"
    )


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
def test_journal_retries_when_an_absent_history_name_appears(run_cli, tmp_path):
    assert run_cli("init", cwd=tmp_path).returncode == 0
    local = tmp_path / ".validated-memory" / "local.jsonl"
    assert not local.exists()
    expected = len(_records(tmp_path / "journal.jsonl"))
    process, ready, proceed = _rendezvous_journal(tmp_path)
    try:
        assert _await_history_attempt(ready) == 1
        local.write_bytes(b"")
        os.write(proceed, b"1")
        assert _await_history_attempt(ready) == 2
        os.write(proceed, b"1")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:  # pragma: no cover - only on a failure
            process.kill()
            process.communicate()

    assert process.returncode == 0, (stdout, stderr)
    assert stdout == f"journal: {expected} record(s)\n"
    assert stderr == ""


@pytest.mark.parametrize("arguments", [(), ("--check",)])
@pytest.mark.parametrize(
    ("durability", "failing_calls"),
    [("repo", {1, 3}), ("local", {2, 3})],
)
def test_repeatable_full_descriptor_read_errors_keep_artifact_precedence(
    run_cli, tmp_path, arguments, durability, failing_calls
):
    adopter, repository, local = _seed_repository_and_local_histories(
        run_cli, tmp_path
    )
    mutant_root = _records_read_failure_mutant(tmp_path, failing_calls)
    result = _run_mutant_journal(mutant_root, adopter, *arguments)
    expected = 0 if durability == "repo" else len(_records(repository))
    artifact = (
        "journal.jsonl" if durability == "repo"
        else ".validated-memory/local.jsonl"
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == f"journal: {expected} record(s), 1 error(s)\n"
    assert result.stderr.startswith(f"ERROR: {artifact}: journal: ")
    assert "injected descriptor read failure" in result.stderr
    assert "histories changed during inspection" not in result.stderr


def test_nonrepeatable_descriptor_read_error_retries_the_pair(run_cli, tmp_path):
    adopter, repository, local = _seed_repository_and_local_histories(
        run_cli, tmp_path
    )
    mutant_root = _records_read_failure_mutant(tmp_path, {1})
    result = _run_mutant_journal(mutant_root, adopter, "--check")
    total = len(_records(repository)) + len(_records(local))

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == f"journal: {total} record(s), 0 error(s)\n"
    assert result.stderr == ""


def test_lseek_failure_cannot_confirm_a_repeated_read_error(run_cli, tmp_path):
    adopter, repository, local = _seed_repository_and_local_histories(
        run_cli, tmp_path
    )
    mutant_root = _records_read_failure_mutant(tmp_path, {1, 3})
    records = mutant_root / "validated_memory" / "journal" / "records.py"
    source = records.read_text(encoding="utf-8")
    source = source.replace(
        "def _repeat_descriptor_failure(opened):",
        '''def _mutant_lseek(descriptor):
    import errno
    raise OSError(errno.EIO, "injected descriptor seek failure")


def _repeat_descriptor_failure(opened):''',
        1,
    ).replace(
        "os.lseek(opened.descriptor, 0, os.SEEK_SET)",
        "_mutant_lseek(opened.descriptor)",
        1,
    )
    records.write_text(source, encoding="utf-8")
    result = _run_mutant_journal(mutant_root, adopter, "--check")
    total = len(_records(repository)) + len(_records(local))

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == f"journal: {total} record(s), 0 error(s)\n"
    assert result.stderr == ""


@pytest.mark.parametrize("arguments", [(), ("--check",)])
@pytest.mark.parametrize(
    ("durability", "stage", "failing_calls"),
    [
        ("repo", "initial", {1, 5, 6}),
        ("local", "initial", {2, 8, 9}),
        ("repo", "pre", {3, 6}),
        ("local", "pre", {5, 9}),
        ("repo", "post", {4, 7}),
        ("local", "post", {6, 10}),
    ],
)
def test_repeatable_descriptor_fstat_errors_keep_artifact_precedence(
    run_cli, tmp_path, arguments, durability, stage, failing_calls
):
    adopter, repository, local = _seed_repository_and_local_histories(
        run_cli, tmp_path
    )
    mutant_root = _records_fstat_failure_mutant(tmp_path, failing_calls)
    result = _run_mutant_journal(mutant_root, adopter, *arguments)
    expected = 0 if durability == "repo" else len(_records(repository))
    artifact = (
        "journal.jsonl" if durability == "repo"
        else ".validated-memory/local.jsonl"
    )

    assert result.returncode == 1, (stage, result.stdout, result.stderr)
    assert result.stdout == f"journal: {expected} record(s), 1 error(s)\n"
    assert result.stderr.startswith(f"ERROR: {artifact}: journal: ")
    assert "injected descriptor fstat failure" in result.stderr
    assert "histories changed during inspection" not in result.stderr


def test_nonrepeatable_descriptor_fstat_error_retries_the_pair(run_cli, tmp_path):
    adopter, repository, local = _seed_repository_and_local_histories(
        run_cli, tmp_path
    )
    mutant_root = _records_fstat_failure_mutant(tmp_path, {1})
    result = _run_mutant_journal(mutant_root, adopter, "--check")
    total = len(_records(repository)) + len(_records(local))

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == f"journal: {total} record(s), 0 error(s)\n"
    assert result.stderr == ""


@pytest.mark.parametrize("arguments", [(), ("--check",)])
@pytest.mark.parametrize("durability", ["repo", "local"])
@pytest.mark.parametrize("damage", ["corrupt", "directory"])
def test_stable_artifact_errors_keep_repository_first_count_precedence(
    run_cli, tmp_path, arguments, durability, damage
):
    adopter, repository, local = _seed_repository_and_local_histories(
        run_cli, tmp_path
    )
    target = repository if durability == "repo" else local
    if damage == "corrupt":
        target.write_bytes(b"{not json\n")
    else:
        target.unlink()
        target.mkdir()
    expected = 0 if durability == "repo" else len(_records(repository))

    result = run_cli("journal", *arguments, cwd=adopter)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == f"journal: {expected} record(s), 1 error(s)\n"
    artifact = (
        "journal.jsonl" if durability == "repo"
        else ".validated-memory/local.jsonl"
    )
    assert result.stderr.startswith(f"ERROR: {artifact}"), result.stderr
    assert "Traceback" not in result.stderr


def test_missing_and_present_empty_histories_keep_the_same_public_report(
    run_cli, tmp_path
):
    missing = tmp_path / "missing"
    empty = tmp_path / "empty"
    missing.mkdir()
    empty.mkdir()
    (empty / "journal.jsonl").write_bytes(b"")
    (empty / ".validated-memory").mkdir()
    (empty / ".validated-memory" / "local.jsonl").write_bytes(b"")

    missing_result = run_cli("journal", "--check", cwd=missing)
    empty_result = run_cli("journal", "--check", cwd=empty)

    assert missing_result.returncode == empty_result.returncode == 0
    assert missing_result.stdout == empty_result.stdout == (
        "journal: 0 record(s), 0 error(s)\n"
    )
    assert missing_result.stderr == empty_result.stderr == ""


def test_history_schema_guard_refuses_before_the_first_adoption_write(tmp_path):
    mutant_root = tmp_path / "mutant"
    shutil.copytree(REPO_ROOT / "validated_memory", mutant_root / "validated_memory")
    records = mutant_root / "validated_memory" / "journal" / "records.py"
    source = records.read_text(encoding="utf-8")
    records.write_text(
        source.replace("HISTORY_WRITE_SCHEMA = 1", "HISTORY_WRITE_SCHEMA = 2", 1),
        encoding="utf-8",
    )
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    result = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        cwd=adopter,
        env={**os.environ, "PYTHONPATH": str(mutant_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert (
        "journal history write schema 2 exceeds read schema 1; the history "
        "upgrade is incomplete"
    ) in result.stderr
    assert not (adopter / "journal.jsonl").exists()
    assert not list((adopter / ".validated-memory").rglob("*.json"))


def test_history_schema_guard_precedes_selected_resolution(tmp_path, run_cli):
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert run_cli("init", cwd=adopter).returncode == 0
    (adopter / "validated-memory.md").unlink()
    crashed = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        cwd=adopter,
        env={
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT),
            "VALIDATED_MEMORY_FAULT": "after-transaction",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert crashed.returncode == 70
    transaction = next(
        (adopter / ".validated-memory" / "transactions").glob("*.json")
    ).stem

    mutant_root = tmp_path / "mutant"
    shutil.copytree(REPO_ROOT / "validated_memory", mutant_root / "validated_memory")
    records = mutant_root / "validated_memory" / "journal" / "records.py"
    records.write_text(
        records.read_text(encoding="utf-8").replace(
            "HISTORY_WRITE_SCHEMA = 1", "HISTORY_WRITE_SCHEMA = 2", 1
        ),
        encoding="utf-8",
    )
    watched = [
        adopter / "journal.jsonl",
        adopter / ".validated-memory" / "local.jsonl",
        adopter / ".validated-memory" / "transactions" / f"{transaction}.json",
    ]
    before = {path: path.read_bytes() for path in watched if path.exists()}
    result = subprocess.run(
        [
            sys.executable,
            "-P",
            "-m",
            "validated_memory",
            "journal",
            "--resolve",
            transaction,
            "--abandon",
        ],
        cwd=adopter,
        env={**os.environ, "PYTHONPATH": str(mutant_root)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert (
        "journal history write schema 2 exceeds read schema 1; the history "
        "upgrade is incomplete"
    ) in result.stderr
    assert {path: path.read_bytes() for path in watched if path.exists()} == before
    assert not (adopter / "validated-memory.md").exists()


def test_paired_history_acquisition_has_one_private_owner_and_separate_schemas():
    journal_root = REPO_ROOT / "validated_memory" / "journal"
    records_source = (journal_root / "records.py").read_text(encoding="utf-8")
    records_tree = ast.parse(records_source)
    constants = {
        target.id: ast.literal_eval(node.value)
        for node in records_tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id in {
            "HISTORY_READ_SCHEMA",
            "HISTORY_WRITE_SCHEMA",
            "WAL_SCHEMA",
        }
    }
    assert constants == {
        "HISTORY_READ_SCHEMA": 1,
        "HISTORY_WRITE_SCHEMA": 1,
        "WAL_SCHEMA": 1,
    }
    assert not any(
        isinstance(node, ast.Name) and node.id == "SCHEMA"
        for path in journal_root.glob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
    )
    assert '"schema": HISTORY_WRITE_SCHEMA' in records_source
    # Two history validators plus the write/read compatibility guard.
    assert records_source.count("> HISTORY_READ_SCHEMA") == 3
    transactions_source = (
        journal_root / "transactions.py"
    ).read_text(encoding="utf-8")
    assert '"schema": WAL_SCHEMA' in transactions_source
    assert transactions_source.count("> WAL_SCHEMA") == 1

    facade_source = (journal_root / "__init__.py").read_text(encoding="utf-8")
    assert "acquire_history_pair" not in facade_source
    protocol_tree = ast.parse(
        (journal_root / "protocol.py").read_text(encoding="utf-8")
    )
    # Workflow inspection, repair candidate acquisition, and repair's staged
    # preinstall verifier are the three protocol-owned coherent pair reads.
    assert sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "acquire_history_pair"
        for node in ast.walk(protocol_tree)
    ) == 3
    raw_reachers = [
        path.name
        for path in journal_root.glob("*.py")
        if path.name != "records.py"
        and any(
            name in path.read_text(encoding="utf-8")
            for name in ("RawHistory", "acquire_history_pair")
        )
    ]
    assert sorted(raw_reachers) == ["protocol.py", "topology.py"]
    parse_calls = [
        node
        for node in ast.walk(protocol_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "parse_acquired_history"
    ]
    assert len(parse_calls) == 4
    parsed_arguments = [ast.unparse(call.args[0]) for call in parse_calls]
    assert parsed_arguments.count("raw") == 2
    assert set(parsed_arguments) >= {
        "candidate_pair.repository",
        "candidate_pair.local",
    }
    reconcile_source = (journal_root / "reconcile.py").read_text(encoding="utf-8")
    assert "from .records import" in reconcile_source
    assert not re.search(r"\bread\s*\(", reconcile_source)

    raw_class = next(
        node
        for node in records_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RawHistory"
    )
    decorator = raw_class.decorator_list[0]
    assert isinstance(decorator, ast.Call)
    assert ast.unparse(decorator) == "dataclass(frozen=True)"
    acquisition = next(
        node
        for node in records_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "acquire_history_pair"
    )
    ranges = [
        node
        for node in ast.walk(acquisition)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "range"
    ]
    assert len(ranges) == 1
    assert [ast.literal_eval(arg) for arg in ranges[0].args] == [1, 4]
    assert "return RawHistory(None, None, None)" in records_source
    assert "return RawHistory(opened.data, mode, opened.generation" in records_source

    executor_tree = ast.parse(
        (journal_root / "executor.py").read_text(encoding="utf-8")
    )
    executor_functions = {
        node.name: node
        for node in executor_tree.body
        if isinstance(node, ast.FunctionDef)
    }
    protocol_functions = {
        node.name: node
        for node in protocol_tree.body
        if isinstance(node, ast.FunctionDef)
    }
    for function in (
        executor_functions["adopting_session"],
        protocol_functions["resolve_one"],
    ):
        lock = next(
            node
            for node in ast.walk(function)
            if isinstance(node, ast.With)
            and any("Lock(" in ast.unparse(item.context_expr) for item in node.items)
        )
        assert ast.unparse(lock.body[0].value) == "ensure_history_compatibility()"
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ensure_history_compatibility"
        for node in ast.walk(executor_functions["repair_transaction"])
    )


# --- private schema-2 topology shadow ---------------------------------------


def _topology_adapter(tmp_path):
    mutant_root = tmp_path / "topology-adapter"
    shutil.copytree(REPO_ROOT / "validated_memory", mutant_root / "validated_memory")
    command = mutant_root / "validated_memory" / "journal" / "command.py"
    source = command.read_text(encoding="utf-8")
    shadow = '''\
    inspection = result.inspection
'''
    adapter = '''\
        inspection = result.inspection
        topology_inspection = inspection.topology
        import json

        def topology_reference(value):
            if value is None:
                return None
            return {
                "kind": value.kind,
                "artifact": value.artifact,
                "digest": value.digest,
            }

        snapshot = topology_inspection.snapshot
        projection = {
            "snapshot_present": snapshot is not None,
            "conditions": [
                {
                    "code": value.code,
                    "level": value.level,
                    "artifact": value.artifact,
                    "locations": [
                        {"artifact": location.artifact, "line": location.line}
                        for location in value.locations
                    ],
                    "node": value.node,
                    "transaction": value.transaction,
                    "reference": topology_reference(value.reference),
                    "reason": value.reason,
                    "fields": list(value.fields),
                    "subjects": list(value.subjects),
                }
                for value in topology_inspection.conditions
            ],
            "capabilities": {
                "appendable": topology_inspection.capabilities.appendable,
                "reversal_ready": topology_inspection.capabilities.reversal_ready,
                "identity_available": topology_inspection.capabilities.identity_available,
                "topology_known_before": topology_inspection.capabilities.topology_known_before,
                "ancestry_available": topology_inspection.capabilities.ancestry_available,
            },
        }
        if snapshot is not None:
            projection.update({
                "artifacts": [
                    {
                        "artifact": value.artifact,
                        "present": value.present,
                        "length": value.length,
                        "digest": value.digest,
                        "physical_count": value.physical_count,
                    }
                    for value in snapshot.artifacts
                ],
                "anchors": [
                    {
                        **topology_reference(value.reference),
                        "length": value.length,
                        "physical_count": value.physical_count,
                        "adoption_ids": list(value.adoption_ids),
                    }
                    for value in snapshot.anchors
                ],
                "heads": [topology_reference(value) for value in snapshot.heads],
                "physical_count": snapshot.physical_count,
                "adoption_ids": list(snapshot.adoption_ids),
                "active_adoption": snapshot.active_adoption,
            })
        stdout.write(json.dumps(projection, sort_keys=True, separators=(",", ":")) + "\\n")
        return EXIT_OK
'''
    adapter = "".join(
        line[4:] if line.strip() else line
        for line in adapter.splitlines(keepends=True)
    )
    assert source.count(shadow) == 1
    command.write_text(source.replace(shadow, adapter), encoding="utf-8")
    return mutant_root


def _run_topology(adapter, adopter):
    result = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "journal", "--check"],
        cwd=adopter,
        env={**os.environ, "PYTHONPATH": str(adapter)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ""
    projection = json.loads(result.stdout)
    assert result.stdout == json.dumps(
        projection, sort_keys=True, separators=(",", ":")
    ) + "\n"
    return projection


def _topology_history(root, repository=None, local=None):
    root.mkdir()
    if repository is not None:
        (root / "journal.jsonl").write_bytes(repository)
    if local is not None:
        (root / ".validated-memory").mkdir()
        (root / ".validated-memory" / "local.jsonl").write_bytes(local)


def _legacy_record(adoption="A", **updates):
    value = {
        "schema": 1,
        "at": "2026-01-01T00:00:00Z",
        "version": "2.4.0",
        "adoption": adoption,
        "run": "run",
        "durability": "repo",
        "op": "observe",
        "purpose": "fixture",
        "path": "memory",
        "stage": "committed",
    }
    value.update(updates)
    return value


def _jsonl(*records, crlf=False):
    ending = b"\r\n" if crlf else b"\n"
    return b"".join(
        json.dumps(record, sort_keys=True, allow_nan=True).encode("utf-8") + ending
        for record in records
    )


def _frontier(kind, artifact, digest):
    return {"kind": kind, "artifact": artifact, "digest": digest}


def _schema2_record(
    *,
    artifact="repo",
    adoption="A",
    kind="observation",
    frontier=(),
    at="2026-01-02T00:00:00Z",
    unknown=False,
    **updates,
):
    value = {
        "schema": 2,
        "at": at,
        "version": "2.4.0",
        "adoption": adoption,
        "run": "run-2",
        "durability": artifact,
        "op": "observe",
        "purpose": "fixture",
        "path": "memory",
        "stage": "committed",
        "kind": kind,
        "frontier": list(frontier),
        "topology_unknown_before": unknown,
        "note": "observed",
    }
    value.update(updates)
    projection = dict(value)
    value["node"] = "sha256:" + hashlib.sha256(
        json.dumps(
            projection,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return value


def _mutation_pair(*, artifact="repo", adoption="A", frontier=(), **updates):
    committed = _schema2_record(
        artifact=artifact,
        adoption=adoption,
        kind="mutation",
        frontier=frontier,
        op="create",
        transaction=updates.pop("transaction", "tx"),
        preimage=None,
        postimage="sha256:" + "1" * 64,
        **updates,
    )
    committed.pop("note", None)
    # The node was calculated before the observation-only field was removed.
    projection = {key: value for key, value in committed.items() if key != "node"}
    committed["node"] = "sha256:" + hashlib.sha256(
        json.dumps(projection, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    prepared = dict(committed)
    prepared["stage"] = "prepared"
    prepared["at"] = "2026-01-01T23:59:59Z"
    return prepared, committed


def _computed_schema2_node(record):
    projection = {key: value for key, value in record.items() if key != "node"}
    return "sha256:" + hashlib.sha256(
        json.dumps(
            projection,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _condition(projection, code):
    return [item for item in projection["conditions"] if item["code"] == code]


def _semantic_topology_projection(projection):
    normalized = json.loads(json.dumps(projection))
    for artifact in normalized.get("artifacts", []):
        artifact.pop("length", None)
        artifact.pop("digest", None)
    for condition in normalized["conditions"]:
        condition.pop("locations", None)
    return normalized


def test_topology_snapshot_distinguishes_absent_empty_blank_and_exact_legacy(tmp_path):
    adapter = _topology_adapter(tmp_path)
    missing = tmp_path / "missing"
    empty = tmp_path / "empty"
    blank = tmp_path / "blank"
    legacy = tmp_path / "legacy"
    _topology_history(missing)
    _topology_history(empty, b"", b"")
    _topology_history(blank, b"\n\r\n")
    legacy_bytes = _jsonl(_legacy_record(), crlf=True)
    _topology_history(legacy, legacy_bytes)

    absent = _run_topology(adapter, missing)
    present = _run_topology(adapter, empty)
    blank_result = _run_topology(adapter, blank)
    legacy_result = _run_topology(adapter, legacy)

    assert absent["artifacts"][0] == {
        "artifact": "repo", "present": False, "length": None,
        "digest": None, "physical_count": 0,
    }
    assert present["artifacts"][0]["present"] is True
    assert present["artifacts"][0]["digest"] == "sha256:" + hashlib.sha256(b"").hexdigest()
    assert absent["capabilities"]["reversal_ready"] is True
    assert blank_result["anchors"][0]["physical_count"] == 0
    assert blank_result["conditions"] == []
    assert legacy_result["anchors"][0]["digest"] == "sha256:" + hashlib.sha256(legacy_bytes).hexdigest()
    assert legacy_result["anchors"][0]["adoption_ids"] == ["A"]
    assert _condition(legacy_result, "topology_unknown_before")[0]["locations"] == [
        {"artifact": "repo", "line": 1}
    ]


def test_topology_counts_equal_physical_duplicates_before_semantic_collapse(tmp_path):
    adapter = _topology_adapter(tmp_path)
    record = _schema2_record()
    adopter = tmp_path / "adopter"
    _topology_history(adopter, _jsonl(record, record))

    result = _run_topology(adapter, adopter)

    assert result["physical_count"] == 2
    assert result["artifacts"][0]["physical_count"] == 2
    assert len(result["heads"]) == 1
    assert result["adoption_ids"] == ["A"]


def test_topology_node_digest_uses_complete_canonical_closing_projection(tmp_path):
    adapter = _topology_adapter(tmp_path)
    record = _schema2_record(note="café", at="2026-01-02T03:04:05Z")
    # Physical encoding deliberately differs from the node projection:
    # whitespace, non-ASCII bytes and CRLF are all outside the digest.
    physical = json.dumps(
        record, ensure_ascii=False, sort_keys=False, separators=(", ", ": ")
    ).encode("utf-8") + b"\r\n"
    adopter = tmp_path / "accepted"
    _topology_history(adopter, physical)
    accepted = _run_topology(adapter, adopter)
    assert accepted["snapshot_present"] is True
    assert not _condition(accepted, "node_digest_mismatch")

    for field, value in (
        ("at", "2026-01-02T03:04:06Z"),
        ("frontier", [_frontier("node", "local", "sha256:" + "7" * 64)]),
        ("topology_unknown_before", True),
    ):
        changed = dict(record)
        changed[field] = value
        root = tmp_path / field
        _topology_history(root, _jsonl(changed))
        result = _run_topology(adapter, root)
        assert _condition(result, "node_digest_mismatch"), field


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "other"),
        ("adoption", "B"),
        ("run", "other"),
        ("op", "replace"),
        ("purpose", "other"),
        ("path", "other"),
        ("transaction", "other"),
        (
            "frontier",
            [
                {
                    "kind": "node",
                    "artifact": "local",
                    "digest": "sha256:" + "8" * 64,
                }
            ],
        ),
        ("topology_unknown_before", True),
        ("preimage", "sha256:" + "2" * 64),
        ("postimage", "sha256:" + "3" * 64),
        ("prior_bytes", 3),
        ("mode", 0o600),
    ],
)
def test_topology_mutation_pair_discriminates_every_variable_paired_field(
    field, value, tmp_path
):
    adapter = _topology_adapter(tmp_path)
    prepared, committed = _mutation_pair()
    prepared[field] = value
    adopter = tmp_path / "adopter"
    _topology_history(adopter, _jsonl(prepared, committed))

    result = _run_topology(adapter, adopter)

    disagreements = _condition(result, "mutation_pair_disagreement")
    assert disagreements and field in disagreements[0]["fields"]


@pytest.mark.parametrize(
    ("bytes_value", "reason"),
    [
        (b'{"schema":2,"schema":2}\n', "duplicate_key"),
        (b'{"schema":2,"at":NaN}\n', "nonstandard_constant"),
        (b'{"schema":3}\n', "invalid_domain"),
    ],
)
def test_topology_schema2_dispatch_is_strict(bytes_value, reason, tmp_path):
    adapter = _topology_adapter(tmp_path)
    adopter = tmp_path / "adopter"
    _topology_history(adopter, bytes_value)

    result = _run_topology(adapter, adopter)

    assert result["snapshot_present"] is False
    assert _condition(result, "malformed_record")[0]["reason"] == reason


def test_topology_legacy_dispatch_keeps_last_key_and_ignored_nan_compatibility(tmp_path):
    adapter = _topology_adapter(tmp_path)
    adopter = tmp_path / "adopter"
    record = _legacy_record()
    encoded = json.dumps(record, sort_keys=True)[:-1] + ',"ignored":NaN,"ignored":1}\n'
    _topology_history(adopter, encoded.encode())

    result = _run_topology(adapter, adopter)

    assert result["snapshot_present"] is True
    assert result["physical_count"] == 1


@pytest.mark.parametrize("damage", ["digest", "missing", "pair", "node", "duplicate"])
def test_topology_mutation_representation_damage_is_exact(damage, tmp_path):
    adapter = _topology_adapter(tmp_path)
    prepared, committed = _mutation_pair()
    expected = {
        "digest": "node_digest_mismatch",
        "missing": "invalid_stage_multiplicity",
        "pair": "mutation_pair_disagreement",
        "node": "mutation_pair_disagreement",
        "duplicate": "duplicate_stage_conflict",
    }[damage]
    if damage == "digest":
        prepared["node"] = committed["node"] = "sha256:" + "0" * 64
        records = (prepared, committed)
    elif damage == "missing":
        records = (prepared,)
    elif damage == "pair":
        prepared["path"] = "other"
        records = (prepared, committed)
    elif damage == "node":
        prepared["node"] = "sha256:" + "9" * 64
        records = (prepared, committed)
    else:
        other = dict(committed)
        other["note"] = "unequal"
        records = (prepared, committed, other)
    adopter = tmp_path / "adopter"
    _topology_history(adopter, _jsonl(*records))

    result = _run_topology(adapter, adopter)

    assert result["snapshot_present"] is False
    assert _condition(result, expected), result
    if damage == "node":
        assert "node" in _condition(result, expected)[0]["fields"]


def test_topology_same_false_digest_stays_artifact_qualified(tmp_path):
    adapter = _topology_adapter(tmp_path)
    false_digest = "sha256:" + "0" * 64
    repo_observation = _schema2_record(artifact="repo")
    local_observation = _schema2_record(artifact="local")
    repo_observation["node"] = local_observation["node"] = false_digest
    observations = tmp_path / "observations"
    _topology_history(
        observations, _jsonl(repo_observation), _jsonl(local_observation)
    )
    result = _run_topology(adapter, observations)
    mismatches = _condition(result, "node_digest_mismatch")
    assert [(item["artifact"], item["locations"]) for item in mismatches] == [
        ("repo", [{"artifact": "repo", "line": 1}]),
        ("local", [{"artifact": "local", "line": 1}]),
    ]
    assert not _condition(result, "duplicate_stage_conflict")
    assert not _condition(result, "invalid_stage_multiplicity")

    repo_pair = _mutation_pair(artifact="repo", transaction="shared")
    local_pair = _mutation_pair(artifact="local", transaction="shared")
    for record in (*repo_pair, *local_pair):
        record["node"] = false_digest
    mutations = tmp_path / "mutations"
    _topology_history(mutations, _jsonl(*repo_pair), _jsonl(*local_pair))
    result = _run_topology(adapter, mutations)
    assert len(_condition(result, "node_digest_mismatch")) == 2
    identity = _condition(result, "transaction_identity_damage")[0]
    assert identity["subjects"] == [
        f"node:local:{false_digest}",
        f"node:repo:{false_digest}",
    ]
    assert not _condition(result, "duplicate_stage_conflict")
    assert not _condition(result, "invalid_stage_multiplicity")


def test_topology_physical_order_does_not_choose_a_head(tmp_path):
    adapter = _topology_adapter(tmp_path)
    first = _schema2_record(at="2026-01-02T00:00:00Z")
    second = _schema2_record(
        frontier=(_frontier("node", "repo", first["node"]),),
        at="2026-01-03T00:00:00Z",
    )
    left = tmp_path / "left"
    right = tmp_path / "right"
    _topology_history(left, _jsonl(first, second))
    _topology_history(right, _jsonl(second, first))

    left_result = _run_topology(adapter, left)
    right_result = _run_topology(adapter, right)

    for result in (left_result, right_result):
        assert result["heads"] == [_frontier("node", "repo", second["node"])]
        assert result["capabilities"]["appendable"] is True


def test_topology_alternating_artifact_ancestry_is_permutation_invariant(tmp_path):
    adapter = _topology_adapter(tmp_path)
    repository_root = _schema2_record(artifact="repo", at="2026-01-02T00:00:00Z")
    local_child = _schema2_record(
        artifact="local",
        frontier=(_frontier("node", "repo", repository_root["node"]),),
        at="2026-01-03T00:00:00Z",
    )
    repository_head = _schema2_record(
        artifact="repo",
        frontier=(_frontier("node", "local", local_child["node"]),),
        at="2026-01-04T00:00:00Z",
    )
    ordered = tmp_path / "ordered"
    permuted = tmp_path / "permuted"
    _topology_history(
        ordered, _jsonl(repository_root, repository_head), _jsonl(local_child)
    )
    _topology_history(
        permuted, _jsonl(repository_head, repository_root), _jsonl(local_child)
    )

    ordered_projection = _run_topology(adapter, ordered)
    permuted_projection = _run_topology(adapter, permuted)

    assert _semantic_topology_projection(ordered_projection) == (
        _semantic_topology_projection(permuted_projection)
    )
    assert ordered_projection["heads"] == [
        _frontier("node", "repo", repository_head["node"])
    ]


def test_topology_missing_ancestry_distinguishes_same_and_cross_artifacts(tmp_path):
    adapter = _topology_adapter(tmp_path)
    missing = "sha256:" + "4" * 64
    same = _schema2_record(frontier=(_frontier("node", "repo", missing),))
    cross = _schema2_record(frontier=(_frontier("node", "local", missing),))
    same_root = tmp_path / "same"
    cross_root = tmp_path / "cross"
    _topology_history(same_root, _jsonl(same))
    _topology_history(cross_root, _jsonl(cross))

    same_result = _run_topology(adapter, same_root)
    cross_result = _run_topology(adapter, cross_root)

    assert same_result["snapshot_present"] is False
    assert _condition(same_result, "same_artifact_ancestry_missing")
    assert cross_result["snapshot_present"] is True
    assert _condition(cross_result, "cross_artifact_ancestry_unavailable")
    assert cross_result["capabilities"]["appendable"] is True
    assert cross_result["capabilities"]["ancestry_available"] is False


@pytest.mark.parametrize("missing_artifact", ["repo", "local"])
def test_topology_digest_invalid_nodes_do_not_classify_missing_ancestry(
    missing_artifact, tmp_path
):
    adapter = _topology_adapter(tmp_path)
    missing = _frontier("node", missing_artifact, "sha256:" + "4" * 64)
    record = _schema2_record(frontier=(missing,))
    record["node"] = "sha256:" + "0" * 64
    adopter = tmp_path / "adopter"
    _topology_history(adopter, _jsonl(record))

    result = _run_topology(adapter, adopter)

    assert _condition(result, "node_digest_mismatch")
    assert not _condition(result, "same_artifact_ancestry_missing")
    assert not _condition(result, "cross_artifact_ancestry_unavailable")


def test_topology_digest_invalid_activation_gets_no_activation_verdict(tmp_path):
    adapter = _topology_adapter(tmp_path)
    record = _schema2_record(
        kind="activation",
        frontier=(
            _frontier("node", "repo", "sha256:" + "4" * 64),
        ),
    )
    record["node"] = "sha256:" + "0" * 64
    adopter = tmp_path / "adopter"
    _topology_history(adopter, _jsonl(record))

    result = _run_topology(adapter, adopter)

    assert _condition(result, "node_digest_mismatch")
    assert not _condition(result, "invalid_frontier")
    assert not _condition(result, "same_artifact_ancestry_missing")


def test_topology_schema_order_unknown_flag_and_cycle_damage_are_independent(tmp_path):
    adapter = _topology_adapter(tmp_path)
    legacy = _legacy_record()
    legacy_bytes = _jsonl(legacy)
    anchor = _frontier("legacy", "repo", "sha256:" + hashlib.sha256(legacy_bytes).hexdigest())
    wrong_flag = _schema2_record(frontier=(anchor,), unknown=False)
    flag_root = tmp_path / "flag"
    _topology_history(flag_root, legacy_bytes + _jsonl(wrong_flag))
    flag = _run_topology(adapter, flag_root)
    assert _condition(flag, "topology_unknown_flag_damage")

    order_root = tmp_path / "order"
    _topology_history(order_root, _jsonl(_schema2_record(), legacy))
    order = _run_topology(adapter, order_root)
    assert _condition(order, "schema_order_damage")
    assert not _condition(order, "node_digest_mismatch")

    one_id = "sha256:" + "1" * 64
    two_id = "sha256:" + "2" * 64
    one = _schema2_record(frontier=(_frontier("node", "repo", two_id),))
    one["node"] = one_id
    two = _schema2_record(frontier=(_frontier("node", "repo", one_id),))
    two["node"] = two_id
    cycle_root = tmp_path / "cycle"
    _topology_history(cycle_root, _jsonl(one, two))
    cycle = _run_topology(adapter, cycle_root)
    assert _condition(cycle, "node_digest_mismatch")
    assert _condition(cycle, "topology_cycle")[0]["subjects"] == [
        f"reference:node:repo:{one_id}",
        f"reference:node:repo:{two_id}",
    ]


def test_topology_multi_reference_precedence_is_stable_during_unavailability(tmp_path):
    adapter = _topology_adapter(tmp_path)
    first = "sha256:" + "1" * 64
    second = "sha256:" + "2" * 64
    record = _schema2_record(
        frontier=(
            _frontier("node", "local", first),
            _frontier("node", "local", second),
        )
    )
    adopter = tmp_path / "adopter"
    _topology_history(adopter, _jsonl(record))

    result = _run_topology(adapter, adopter)

    assert _condition(result, "invalid_frontier")[0]["reason"] == "implicit_join"
    assert len(_condition(result, "cross_artifact_ancestry_unavailable")) == 2
    assert result["snapshot_present"] is False


@pytest.mark.parametrize("kind", ["observation", "mutation"])
def test_topology_invalid_ordinary_join_leaves_legacy_anchor_fork(kind, tmp_path):
    adapter = _topology_adapter(tmp_path)
    repository = _jsonl(_legacy_record("A"))
    local = _jsonl(_legacy_record("A", durability="local", path="/outside"))
    references = (
        _frontier("legacy", "repo", "sha256:" + hashlib.sha256(repository).hexdigest()),
        _frontier("legacy", "local", "sha256:" + hashlib.sha256(local).hexdigest()),
    )
    if kind == "mutation":
        records = _mutation_pair(frontier=references)
    else:
        records = (_schema2_record(frontier=references),)
    adopter = tmp_path / "adopter"
    _topology_history(adopter, repository + _jsonl(*records), local)

    result = _run_topology(adapter, adopter)

    invalid = _condition(result, "invalid_frontier")
    assert len(invalid) == 1 and invalid[0]["reason"] == "implicit_join"
    assert _condition(result, "fork")[0]["subjects"] == [
        f"reference:legacy:repo:{references[0]['digest']}",
        f"reference:legacy:local:{references[1]['digest']}",
    ]
    assert result["snapshot_present"] is False


def test_topology_prepared_only_join_still_exposes_implicit_join_and_fork(tmp_path):
    adapter = _topology_adapter(tmp_path)
    repository = _jsonl(_legacy_record("A"))
    local = _jsonl(_legacy_record("A", durability="local", path="/outside"))
    references = (
        _frontier("legacy", "repo", "sha256:" + hashlib.sha256(repository).hexdigest()),
        _frontier("legacy", "local", "sha256:" + hashlib.sha256(local).hexdigest()),
    )
    prepared, _committed = _mutation_pair(frontier=references)
    adopter = tmp_path / "adopter"
    _topology_history(adopter, repository + _jsonl(prepared), local)

    result = _run_topology(adapter, adopter)

    multiplicity = _condition(result, "invalid_stage_multiplicity")
    assert len(multiplicity) == 1
    assert multiplicity[0]["subjects"] == ["prepared:1", "committed:0"]
    invalid = _condition(result, "invalid_frontier")
    assert len(invalid) == 1 and invalid[0]["reason"] == "implicit_join"
    assert _condition(result, "fork")[0]["subjects"] == [
        f"reference:legacy:repo:{references[0]['digest']}",
        f"reference:legacy:local:{references[1]['digest']}",
    ]
    assert result["snapshot_present"] is False


def test_topology_frontier_duplicate_order_and_redundancy_are_distinct(tmp_path):
    adapter = _topology_adapter(tmp_path)
    missing = _frontier("node", "local", "sha256:" + "6" * 64)
    duplicate = _schema2_record(frontier=(missing, missing))
    duplicate_root = tmp_path / "duplicate"
    _topology_history(duplicate_root, _jsonl(duplicate))
    assert _condition(_run_topology(adapter, duplicate_root), "invalid_frontier")[0]["reason"] == "duplicate_reference"

    repo = _frontier("node", "repo", "sha256:" + "5" * 64)
    noncanonical = _schema2_record(frontier=(missing, repo))
    order_root = tmp_path / "order"
    _topology_history(order_root, _jsonl(noncanonical))
    noncanonical_condition = _condition(
        _run_topology(adapter, order_root), "invalid_frontier"
    )[0]
    assert noncanonical_condition["reason"] == "noncanonical_order"
    assert noncanonical_condition["subjects"] == [
        f"reference:node:repo:{repo['digest']}",
        f"reference:node:local:{missing['digest']}",
    ]

    ancestor = _schema2_record(at="2026-01-02T00:00:00Z")
    descendant = _schema2_record(
        frontier=(_frontier("node", "repo", ancestor["node"]),),
        at="2026-01-03T00:00:00Z",
    )
    references = tuple(
        sorted(
            (
                _frontier("node", "repo", ancestor["node"]),
                _frontier("node", "repo", descendant["node"]),
            ),
            key=lambda value: value["digest"],
        )
    )
    redundant = _schema2_record(frontier=references, at="2026-01-04T00:00:00Z")
    redundant_root = tmp_path / "redundant"
    _topology_history(redundant_root, _jsonl(ancestor, descendant, redundant))
    condition = _condition(_run_topology(adapter, redundant_root), "invalid_frontier")[0]
    assert condition["reason"] == "redundant_reference"


def test_topology_payloads_scope_mixed_locations_and_semantic_stage_counts(tmp_path):
    adapter = _topology_adapter(tmp_path)
    mixed_repository = _jsonl(_legacy_record("A"), _legacy_record("B"))
    clean_local = _jsonl(_legacy_record("A", durability="local", path="/outside"))
    mixed_root = tmp_path / "mixed"
    _topology_history(mixed_root, mixed_repository, clean_local)
    mixed = _condition(_run_topology(adapter, mixed_root), "legacy_mixed_lineage")[0]
    assert mixed["locations"] == [
        {"artifact": "repo", "line": 1},
        {"artifact": "repo", "line": 2},
    ]

    prepared, _committed = _mutation_pair()
    unequal = dict(prepared, purpose="unequal")
    stages_root = tmp_path / "stages"
    _topology_history(stages_root, _jsonl(prepared, unequal))
    stages = _condition(
        _run_topology(adapter, stages_root), "invalid_stage_multiplicity"
    )[0]
    assert stages["subjects"] == ["prepared:2", "committed:0"]
    assert stages["locations"] == [
        {"artifact": "repo", "line": 1},
        {"artifact": "repo", "line": 2},
    ]
    duplicate = _condition(
        _run_topology(adapter, stages_root), "duplicate_stage_conflict"
    )[0]
    assert duplicate["locations"] == stages["locations"]


def test_topology_conflicted_committed_stage_is_permutation_independent(tmp_path):
    adapter = _topology_adapter(tmp_path)
    shared = "sha256:" + "f" * 64
    first = _schema2_record(note="first")
    first["node"] = shared
    second = _schema2_record(
        note="second",
        frontier=(_frontier("node", "local", "sha256:" + "3" * 64),),
    )
    second["node"] = shared
    expected = {
        f"computed:{_computed_schema2_node(first)}",
        f"computed:{_computed_schema2_node(second)}",
    }
    left = tmp_path / "committed-left"
    right = tmp_path / "committed-right"
    _topology_history(left, _jsonl(first, second))
    _topology_history(right, _jsonl(second, first))

    left_result = _run_topology(adapter, left)
    right_result = _run_topology(adapter, right)

    assert _semantic_topology_projection(left_result) == (
        _semantic_topology_projection(right_result)
    )
    conflict = _condition(left_result, "duplicate_stage_conflict")
    assert len(conflict) == 1
    assert conflict[0]["locations"] == [
        {"artifact": "repo", "line": 1},
        {"artifact": "repo", "line": 2},
    ]
    multiplicity = _condition(left_result, "invalid_stage_multiplicity")
    assert len(multiplicity) == 1
    assert multiplicity[0]["subjects"] == ["prepared:0", "committed:2"]
    assert multiplicity[0]["locations"] == conflict[0]["locations"]
    mismatches = _condition(left_result, "node_digest_mismatch")
    assert {item["subjects"][0] for item in mismatches} == expected
    assert {item["locations"][0]["line"] for item in mismatches} == {1, 2}
    assert not _condition(left_result, "cross_artifact_ancestry_unavailable")
    assert not _condition(left_result, "invalid_frontier")


def test_topology_conflicted_prepared_stage_is_permutation_independent(tmp_path):
    adapter = _topology_adapter(tmp_path)
    prepared, committed = _mutation_pair()
    first = dict(
        prepared,
        purpose="first-purpose",
        frontier=[_frontier("node", "local", "sha256:" + "4" * 64)],
    )
    second = dict(
        prepared,
        path="second-path",
        frontier=[_frontier("node", "local", "sha256:" + "5" * 64)],
    )
    left = tmp_path / "prepared-left"
    right = tmp_path / "prepared-right"
    _topology_history(left, _jsonl(first, second, committed))
    _topology_history(right, _jsonl(committed, second, first))

    left_result = _run_topology(adapter, left)
    right_result = _run_topology(adapter, right)

    assert _semantic_topology_projection(left_result) == (
        _semantic_topology_projection(right_result)
    )
    conflict = _condition(left_result, "duplicate_stage_conflict")
    assert len(conflict) == 1
    assert conflict[0]["locations"] == [
        {"artifact": "repo", "line": 1},
        {"artifact": "repo", "line": 2},
    ]
    multiplicity = _condition(left_result, "invalid_stage_multiplicity")
    assert len(multiplicity) == 1
    assert multiplicity[0]["subjects"] == ["prepared:2", "committed:1"]
    assert multiplicity[0]["locations"] == [
        {"artifact": "repo", "line": 1},
        {"artifact": "repo", "line": 2},
        {"artifact": "repo", "line": 3},
    ]
    disagreements = _condition(left_result, "mutation_pair_disagreement")
    assert {tuple(item["fields"]) for item in disagreements} == {
        ("frontier", "purpose"),
        ("frontier", "path"),
    }
    assert {
        tuple(location["line"] for location in item["locations"])
        for item in disagreements
    } == {(1, 3), (2, 3)}
    assert not _condition(left_result, "cross_artifact_ancestry_unavailable")
    assert not _condition(left_result, "invalid_frontier")


@pytest.mark.parametrize("prepared_kind", ["mutation", "observation"])
def test_topology_mixed_kind_pair_disagreement_is_permutation_independent(
    prepared_kind, tmp_path
):
    adapter = _topology_adapter(tmp_path)
    mutation_prepared, mutation_committed = _mutation_pair(transaction="mixed-tx")
    observation_committed = _schema2_record()
    if prepared_kind == "mutation":
        prepared = dict(mutation_prepared, node=observation_committed["node"])
        committed = observation_committed
    else:
        prepared = dict(
            observation_committed,
            stage="prepared",
            node=mutation_committed["node"],
        )
        committed = mutation_committed
    left = tmp_path / f"mixed-{prepared_kind}-left"
    right = tmp_path / f"mixed-{prepared_kind}-right"
    _topology_history(left, _jsonl(prepared, committed))
    _topology_history(right, _jsonl(committed, prepared))

    left_result = _run_topology(adapter, left)
    right_result = _run_topology(adapter, right)

    assert _semantic_topology_projection(left_result) == (
        _semantic_topology_projection(right_result)
    )
    for result in (left_result, right_result):
        disagreements = _condition(result, "mutation_pair_disagreement")
        assert len(disagreements) == 1
        assert disagreements[0]["transaction"] == "mixed-tx"
        assert disagreements[0]["fields"] == [
            "kind",
            "note",
            "op",
            "postimage",
            "preimage",
            "transaction",
        ]
        assert disagreements[0]["locations"] == [
            {"artifact": "repo", "line": 1},
            {"artifact": "repo", "line": 2},
        ]
        multiplicity = _condition(result, "invalid_stage_multiplicity")
        assert len(multiplicity) == 1
        assert multiplicity[0]["subjects"] == ["prepared:1", "committed:1"]
        assert multiplicity[0]["locations"] == disagreements[0]["locations"]


def test_topology_rejects_an_in_lineage_identity_change(tmp_path):
    adapter = _topology_adapter(tmp_path)
    parent = _schema2_record(adoption="A")
    child = _schema2_record(
        adoption="B",
        frontier=(_frontier("node", "repo", parent["node"]),),
        at="2026-01-03T00:00:00Z",
    )
    adopter = tmp_path / "adopter"
    _topology_history(adopter, _jsonl(child, parent))

    result = _run_topology(adapter, adopter)

    condition = _condition(result, "lineage_transition")[0]
    assert condition["subjects"] == ["adoption:A", "adoption:B"]
    assert result["snapshot_present"] is False


def test_topology_fork_lineage_and_implicit_join_are_not_ordered(tmp_path):
    adapter = _topology_adapter(tmp_path)
    left = _schema2_record(adoption="A", at="2026-01-02T00:00:00Z")
    right = _schema2_record(adoption="B", artifact="local", at="2026-01-03T00:00:00Z")
    fork_root = tmp_path / "fork"
    _topology_history(fork_root, _jsonl(left), _jsonl(right))
    fork = _run_topology(adapter, fork_root)
    assert _condition(fork, "fork")
    assert _condition(fork, "unreconciled_lineages")
    assert fork["active_adoption"] is None
    assert fork["capabilities"]["appendable"] is False

    join = _schema2_record(
        frontier=tuple(sorted((
            _frontier("node", "repo", left["node"]),
            _frontier("node", "local", right["node"]),
        ), key=lambda value: (value["artifact"] == "local", value["kind"], value["digest"]))),
    )
    join_root = tmp_path / "join"
    _topology_history(join_root, _jsonl(left, join), _jsonl(right))
    joined = _run_topology(adapter, join_root)
    assert joined["snapshot_present"] is False
    assert _condition(joined, "invalid_frontier")[0]["reason"] == "implicit_join"


def test_topology_activation_is_causal_and_preserves_omitted_inputs(tmp_path):
    adapter = _topology_adapter(tmp_path)
    repo_legacy = _jsonl(_legacy_record())
    local_legacy = _jsonl(_legacy_record(durability="local", path="/outside"))
    repo_ref = _frontier("legacy", "repo", "sha256:" + hashlib.sha256(repo_legacy).hexdigest())
    local_ref = _frontier("legacy", "local", "sha256:" + hashlib.sha256(local_legacy).hexdigest())
    activation = _schema2_record(kind="activation", frontier=(repo_ref,), unknown=True)
    adopter = tmp_path / "adopter"
    _topology_history(adopter, repo_legacy + _jsonl(activation), local_legacy)

    result = _run_topology(adapter, adopter)

    assert result["snapshot_present"] is True
    assert _condition(result, "fork")
    assert {item["kind"] for item in result["heads"]} == {"legacy", "node"}
    assert result["active_adoption"] == "A"


def test_topology_activation_accepts_fresh_blank_and_missing_cross_roots(tmp_path):
    adapter = _topology_adapter(tmp_path)
    fresh = _schema2_record(kind="activation", adoption="Y")
    fresh_root = tmp_path / "fresh"
    _topology_history(fresh_root, _jsonl(fresh))
    fresh_result = _run_topology(adapter, fresh_root)
    assert fresh_result["active_adoption"] == "Y"
    assert fresh_result["conditions"] == []

    blank_bytes = b"\n"
    blank_ref = _frontier("legacy", "repo", "sha256:" + hashlib.sha256(blank_bytes).hexdigest())
    blank_activation = _schema2_record(kind="activation", adoption="Y", frontier=(blank_ref,))
    blank_root = tmp_path / "blank"
    _topology_history(blank_root, blank_bytes + _jsonl(blank_activation))
    blank_result = _run_topology(adapter, blank_root)
    assert blank_result["active_adoption"] == "Y"
    assert not _condition(blank_result, "topology_unknown_before")

    missing_ref = _frontier("legacy", "local", "sha256:" + "8" * 64)
    missing_activation = _schema2_record(
        kind="activation", adoption="Y", frontier=(missing_ref,), unknown=True
    )
    missing_root = tmp_path / "missing"
    _topology_history(missing_root, _jsonl(missing_activation))
    missing_result = _run_topology(adapter, missing_root)
    assert missing_result["active_adoption"] == "Y"
    assert _condition(missing_result, "cross_artifact_ancestry_unavailable")
    assert missing_result["snapshot_present"] is True

    swapped = _schema2_record(
        kind="activation",
        artifact="local",
        adoption="Y",
        frontier=(
            _frontier("legacy", "repo", "sha256:" + "9" * 64),
        ),
        unknown=False,
    )
    swapped_root = tmp_path / "swapped"
    _topology_history(swapped_root, None, _jsonl(swapped))
    swapped_result = _run_topology(adapter, swapped_root)
    warning = _condition(
        swapped_result, "cross_artifact_ancestry_unavailable"
    )[0]
    assert warning["artifact"] == "local"
    assert warning["reference"]["artifact"] == "repo"
    assert swapped_result["active_adoption"] == "Y"


def test_topology_multiple_activation_roots_remain_a_fork(tmp_path):
    adapter = _topology_adapter(tmp_path)
    first = _schema2_record(kind="activation", adoption="A", at="2026-01-02T00:00:00Z")
    second = _schema2_record(
        kind="activation", artifact="local", adoption="B", at="2026-01-03T00:00:00Z"
    )
    adopter = tmp_path / "adopter"
    _topology_history(adopter, _jsonl(first), _jsonl(second))

    result = _run_topology(adapter, adopter)

    assert result["snapshot_present"] is True
    assert len(result["heads"]) == 2
    assert _condition(result, "fork")
    assert _condition(result, "unreconciled_lineages")


def test_topology_activation_descendant_and_mixed_anchor_are_distinct(tmp_path):
    adapter = _topology_adapter(tmp_path)
    activation = _schema2_record(kind="activation", adoption="A")
    descendant = _schema2_record(
        frontier=(_frontier("node", "repo", activation["node"]),),
        at="2026-01-03T00:00:00Z",
    )
    clean_root = tmp_path / "clean"
    _topology_history(clean_root, _jsonl(descendant, activation))
    clean = _run_topology(adapter, clean_root)
    assert clean["heads"] == [_frontier("node", "repo", descendant["node"])]
    assert not _condition(clean, "fork")

    first = _legacy_record("A", op="create", stage="prepared", run="one")
    second = _legacy_record("B", op="create", stage="prepared", run="two")
    legacy = _jsonl(first, second)
    reference = _frontier("legacy", "repo", "sha256:" + hashlib.sha256(legacy).hexdigest())
    mixed_activation = _schema2_record(
        kind="activation", adoption="A", frontier=(reference,), unknown=True
    )
    mixed_root = tmp_path / "mixed"
    _topology_history(mixed_root, legacy + _jsonl(mixed_activation))
    mixed = _run_topology(adapter, mixed_root)
    invalid = _condition(mixed, "invalid_frontier")
    assert invalid[0]["reason"] == "invalid_activation"
    assert invalid[0]["subjects"] == ["mixed_anchor_lineage"]


def test_topology_result_types_are_frozen_and_one_inspect_seam_is_shipped():
    source = (
        REPO_ROOT / "validated_memory" / "journal" / "topology.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    ] == ["inspect"]
    returned = {
        "FrontierReference", "SourceLocation", "Condition", "Capabilities",
        "ArtifactSnapshot", "LegacyAnchor", "HistorySnapshot", "Inspection",
    }
    classes = {
        node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
    }
    assert returned <= set(classes)
    for name in returned:
        assert any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "dataclass"
            and any(
                keyword.arg == "frozen"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in decorator.keywords
            )
            for decorator in classes[name].decorator_list
        ), name


@pytest.mark.parametrize(
    ("frontier_kind", "anchor_adoption", "record_adoption", "subject"),
    [
        ("node", "A", "A", "node_reference"),
        ("legacy", "A", "B", "adoption_mismatch:A:B"),
    ],
)
def test_topology_invalid_activation_subjects_are_frozen(
    frontier_kind, anchor_adoption, record_adoption, subject, tmp_path
):
    adapter = _topology_adapter(tmp_path)
    legacy = _jsonl(_legacy_record(anchor_adoption))
    digest = "sha256:" + hashlib.sha256(legacy).hexdigest()
    activation = _schema2_record(
        kind="activation",
        adoption=record_adoption,
        frontier=(_frontier(frontier_kind, "repo", digest),),
        unknown=frontier_kind == "legacy",
    )
    adopter = tmp_path / "adopter"
    _topology_history(adopter, legacy + _jsonl(activation))

    result = _run_topology(adapter, adopter)

    condition = _condition(result, "invalid_frontier")[0]
    assert condition["reason"] == "invalid_activation"
    assert condition["subjects"] == [subject]
    assert result["snapshot_present"] is False


def test_topology_transaction_identity_spans_legacy_and_schema2(tmp_path):
    adapter = _topology_adapter(tmp_path)
    legacy_prepared = _legacy_record(
        op="create", stage="prepared", transaction="shared", postimage="x"
    )
    legacy_committed = dict(legacy_prepared, stage="committed")
    prepared, committed = _mutation_pair(artifact="local", transaction="shared")
    adopter = tmp_path / "adopter"
    _topology_history(
        adopter,
        _jsonl(legacy_prepared, legacy_committed),
        _jsonl(prepared, committed),
    )

    result = _run_topology(adapter, adopter)

    condition = _condition(result, "transaction_identity_damage")[0]
    assert condition["transaction"] == "shared"
    assert condition["subjects"] == [
        "legacy:repo:transaction:shared",
        f"node:local:{committed['node']}",
    ]
    assert {tuple(item.values()) for item in condition["locations"]} == {
        ("repo", 1), ("repo", 2), ("local", 1), ("local", 2)
    }

    repo_pair = _mutation_pair(
        artifact="repo", transaction="two-nodes", purpose="repository"
    )
    local_pair = _mutation_pair(
        artifact="local", transaction="two-nodes", purpose="local"
    )
    two_nodes = tmp_path / "two-nodes"
    _topology_history(two_nodes, _jsonl(*repo_pair), _jsonl(*local_pair))
    reuse = _condition(
        _run_topology(adapter, two_nodes), "transaction_identity_damage"
    )[0]
    assert reuse["subjects"] == sorted(
        [
            f"node:repo:{repo_pair[1]['node']}",
            f"node:local:{local_pair[1]['node']}",
        ]
    )


def test_topology_permutation_comparison_removes_only_locations_and_raw_summaries(
    tmp_path,
):
    adapter = _topology_adapter(tmp_path)
    first = _schema2_record(
        at="2026-01-02T00:00:00Z",
        frontier=(
            _frontier("node", "local", "sha256:" + "7" * 64),
        ),
    )
    second = _schema2_record(at="2026-01-03T00:00:00Z")
    left = tmp_path / "left"
    right = tmp_path / "right"
    _topology_history(left, _jsonl(first, second))
    _topology_history(right, _jsonl(second, first))

    left_projection = _run_topology(adapter, left)
    right_projection = _run_topology(adapter, right)

    assert _condition(left_projection, "fork")
    assert _semantic_topology_projection(left_projection) == (
        _semantic_topology_projection(right_projection)
    )
    assert _condition(left_projection, "cross_artifact_ancestry_unavailable")[0][
        "locations"
    ] != _condition(right_projection, "cross_artifact_ancestry_unavailable")[0][
        "locations"
    ]


def test_topology_shadow_is_private_pure_and_the_only_production_caller():
    """Pin pure topology ownership behind one private workflow caller.

    Static source inspection reaches import direction and forbidden schema
    construction; it does not prove the subprocess behavior pinned separately.
    """
    root = REPO_ROOT / "validated_memory" / "journal"
    command_source = (root / "command.py").read_text(encoding="utf-8")
    protocol_source = (root / "protocol.py").read_text(encoding="utf-8")
    topology_source = (root / "topology.py").read_text(encoding="utf-8")
    facade_source = (root / "__init__.py").read_text(encoding="utf-8")
    assert command_source.count("history.perform(Inspect(check))") == 1
    assert protocol_source.count("inspect_topology(acquired)") == 1
    assert "except Exception as error:" in protocol_source
    assert "topology" not in facade_source.split("__all__ =", 1)[1]
    assert not re.search(r"\b(open|Path|os\.|environ|getenv)\b", topology_source)
    callers = [
        path.name
        for path in root.glob("*.py")
        if path.name != "topology.py" and re.search(r"\binspect\s*\(", path.read_text(encoding="utf-8"))
    ]
    assert callers == []
    assert "from .topology import inspect as inspect_topology" in protocol_source
    for path in root.glob("*.py"):
        if path.name == "topology.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert "FrontierReference(" not in source, path.name
        assert not re.search(r"['\"]frontier['\"]\s*:", source), path.name
        assert not re.search(r"['\"]node['\"]\s*:", source), path.name


def test_protocol_vocabulary_is_private_and_command_has_one_inspection_seam():
    """Pin the closed private vocabulary and its sole production seam.

    The AST assertions reach import/call structure that CLI output cannot
    distinguish; they do not prove runtime dispatch semantics.
    """
    root = REPO_ROOT / "validated_memory" / "journal"
    protocol = (root / "protocol.py").read_text(encoding="utf-8")
    command = (root / "command.py").read_text(encoding="utf-8")
    facade = (root / "__init__.py").read_text(encoding="utf-8")
    for name in (
        "Inspect", "Adopt", "Observe", "Mutate", "RecoverAll",
        "ResolveOne", "RepairOne", "Completed", "Noop", "Reported",
        "Warning", "ConfirmedWithGates", "Refused", "Retained",
        "Unsupported", "Damaged",
    ):
        assert f"class {name}" in protocol
        assert name not in facade.split("__all__ =", 1)[1]
    assert command.count("history.perform(Inspect(check))") == 1
    assert command.count("history.perform(RepairOne(transaction_id))") == 1
    for private_policy in (
        "acquire_history_pair(", "parse_acquired_history(",
        "inspect_topology(", "open_transactions(", "classify_evidence(",
        "reconcile(",
    ):
        assert private_policy not in command
    assert protocol.count("acquire_history_pair(self._root)") == 1
    assert protocol.count("inspect_topology(acquired)") == 1
    assert "history_workflow" not in facade
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "facts"
        for source in (protocol, command)
        for node in ast.walk(ast.parse(source))
    )
    protocol_tree = ast.parse(protocol)
    aliases = {
        node.targets[0].id: ast.unparse(node.value)
        for node in protocol_tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"Operation", "Result"}
    }
    assert set(re.findall(r"\b[A-Z][A-Za-z]+\b", aliases["Operation"])) == {
        "Inspect", "Adopt", "Observe", "Mutate", "RecoverAll",
        "ResolveOne", "RepairOne",
    }
    assert set(re.findall(r"\b[A-Z][A-Za-z]+\b", aliases["Result"])) == {
        "Completed", "Noop", "Reported", "Warning", "ConfirmedWithGates",
        "Refused", "Retained", "Unsupported", "Damaged",
    }
    offenders = []
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                offenders.extend(
                    f"{path.name}:{node.lineno}:{alias.name}"
                    for alias in node.names
                    if alias.name.startswith("_")
                )
    assert offenders == []


def test_repair_policy_has_one_protocol_owner():
    """Pin RepairOne policy in protocol and leave executor as an adapter."""
    journal = REPO_ROOT / "validated_memory" / "journal"
    executor = (journal / "executor.py").read_text(encoding="utf-8")
    protocol = (journal / "protocol.py").read_text(encoding="utf-8")
    executor_tree = ast.parse(executor)
    functions = {
        node.name: node
        for node in executor_tree.body
        if isinstance(node, ast.FunctionDef)
    }
    repair = functions["repair_transaction"]
    assert ast.unparse(repair.body[-1].value).startswith("_protocol_repair_one(")
    for obsolete in (
        "_repair_refusal",
        "_expected_history_pair",
        "_repair_history_target",
        "_cleanup_temporary_claim",
    ):
        assert obsolete not in functions
    for policy_call in (
        "read_transaction(",
        "open_transactions(",
        "validate_snapshot(",
        "current_state(",
        "remove_transaction_file(",
    ):
        assert policy_call not in ast.unparse(repair)
    assert "def repair_one(" in protocol


def _protocol_projection_adapter(tmp_path):
    mutant_root = tmp_path / "protocol-adapter"
    shutil.copytree(REPO_ROOT / "validated_memory", mutant_root / "validated_memory")
    command = mutant_root / "validated_memory" / "journal" / "command.py"
    source = command.read_text(encoding="utf-8")
    seam = "    inspection = result.inspection\n"
    projection = '''\
    inspection = result.inspection
    import json
    payload = {
        "conditions": [
            {
                "identity": value.identity,
                "subject": value.subject,
                "pairing": list(value.pairing),
                "detail": value.detail,
            }
            for value in inspection.conditions
        ],
        "wal": [
            {
                "type": type(value.evidence).__name__,
                "target": type(value.evidence.target).__name__
                    if hasattr(value.evidence, "target") else None,
                "claim": getattr(value.evidence, "claim", None),
                "occurrence": getattr(value.evidence, "occurrence", None),
            }
            for value in inspection.wal
        ],
        "immutable": [],
    }
    attempts = []
    if inspection.compatibility.records:
        attempts.append(lambda: inspection.compatibility.records[0].__setitem__("x", 1))
    if inspection.wal:
        attempts.append(lambda: inspection.wal[0].raw["intention"].__setitem__("path", "changed"))
        if "history_append" in inspection.wal[0].raw:
            attempts.append(
                lambda: inspection.wal[0].raw["history_append"]["records"][0].__setitem__("path", "changed")
            )
        evidence = inspection.wal[0].evidence
        target = evidence.target if hasattr(evidence, "target") else evidence
        if hasattr(target, "actual"):
            attempts.append(lambda: target.actual.__setitem__("kind", "changed"))
    for attempt in attempts:
        try:
            attempt()
        except (AttributeError, TypeError):
            payload["immutable"].append(True)
        else:
            payload["immutable"].append(False)
    stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\\n")
    return EXIT_OK
'''
    assert source.count(seam) == 1
    command.write_text(source.replace(seam, projection), encoding="utf-8")
    return mutant_root


def _run_protocol_projection(adapter, adopter):
    result = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "journal", "--check"],
        cwd=adopter,
        env={**os.environ, "PYTHONPATH": str(adapter)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ""
    return json.loads(result.stdout)


def _legacy_classification_adapter(tmp_path):
    """Expose the unchanged tuple adapter through an isolated CLI subprocess."""
    mutant_root = tmp_path / "legacy-classification-adapter"
    shutil.copytree(REPO_ROOT / "validated_memory", mutant_root / "validated_memory")
    command = mutant_root / "validated_memory" / "journal" / "command.py"
    source = command.read_text(encoding="utf-8")
    seam = "    inspection = result.inspection\n"
    projection = '''\
    inspection = result.inspection
    import json
    from .transactions import classify, open_transactions
    item = open_transactions(root)[0]
    verdict, facts = classify(root, item)
    stdout.write(json.dumps({
        "verdict": verdict,
        "facts": facts,
        "schema": item["schema"],
    }, sort_keys=True, separators=(",", ":")) + "\\n")
    return EXIT_OK
'''
    assert source.count(seam) == 1
    command.write_text(source.replace(seam, projection), encoding="utf-8")
    return mutant_root


def test_history_unconfirmed_typed_evidence_preserves_legacy_tuple_and_bytes(
    run_cli, tmp_path, monkeypatch
):
    """The C1c evidence extension changes no WAL-1 or released check contract."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction, history = _retained_complete_append(
        run_cli, adopter, monkeypatch
    )
    transaction_path = next(
        (adopter / ".validated-memory" / "transactions").glob("*.json")
    )
    wal_before = transaction_path.read_bytes()
    history_before = history.read_bytes()
    tree_before = _final_tree_snapshot(adopter)
    adapter = _legacy_classification_adapter(tmp_path)

    projection = _run_protocol_projection(
        _protocol_projection_adapter(tmp_path), adopter
    )
    legacy = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "journal", "--check"],
        cwd=adopter,
        env={**os.environ, "PYTHONPATH": str(adapter)},
        capture_output=True,
        text=True,
        check=False,
    )
    checked = run_cli("journal", "--check", cwd=adopter)

    assert projection["wal"] == [{
        "type": "HistoryClaimWal",
        "target": "ReadableTargetWal",
        "claim": "valid",
        "occurrence": "complete",
    }]
    assert legacy.returncode == 0, (legacy.stdout, legacy.stderr)
    assert legacy.stderr == ""
    legacy_payload = json.loads(legacy.stdout)
    assert legacy_payload["verdict"] == "confirm-history"
    assert legacy_payload["schema"] == 1
    assert legacy_payload["facts"]["actual"] == transaction["postimage"]
    assert "history_append" not in legacy_payload["facts"]
    assert checked.returncode == 1
    assert checked.stderr == (
        f"ERROR: {transaction['intention']['path']}: journal: open transaction "
        f"{transaction['transaction']} (published) on "
        f"{transaction['intention']['path']}: recoverable\n"
    )
    record_count = len(_records(adopter / "journal.jsonl"))
    local = adopter / ".validated-memory" / "local.jsonl"
    if local.exists():
        record_count += len(_records(local))
    assert checked.stdout == f"journal: {record_count} record(s), 1 error(s)\n"
    assert transaction_path.read_bytes() == wal_before
    assert history.read_bytes() == history_before
    assert _final_tree_snapshot(adopter) == tree_before


@pytest.mark.parametrize(
    ("shape", "expected"),
    (("zero", "zero"), ("prepared", "prepared"), ("complete", "complete"),
     ("torn", "conflicting"), ("conflicting", "conflicting"),
     ("complete-malformed-suffix", "conflicting"),
     ("complete-torn-suffix", "conflicting"),
     ("complete-conflicting-suffix", "conflicting")),
)
def test_claimed_history_evidence_is_total_and_deterministic(
    run_cli, tmp_path, monkeypatch, shape, expected
):
    """Prove valid claims classify every byte occurrence without mutation.

    Two fresh CLI subprocesses expose the private projection deterministically;
    the ordinary CLI pins released behavior.  Unreadability is exercised by a
    separate permission-sensitive test because this matrix uses readable paths.
    """
    adapter = _protocol_projection_adapter(tmp_path)
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, adopter, monkeypatch
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry["unconfirmed"] = "history-claim"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    prefix_length = claim["prefix"]["length"]
    prefix = history.read_bytes()[:prefix_length]
    prepared_end = payload.find(b"\n") + 1
    suffix = {
        "zero": b"",
        "prepared": payload[:prepared_end],
        "complete": payload,
        "torn": payload[:-5],
        "conflicting": b"{}\n",
        "complete-malformed-suffix": payload + b"{}\n",
        "complete-torn-suffix": payload + b'{"schema":',
        "complete-conflicting-suffix": payload + payload[:prepared_end],
    }[shape]
    history.write_bytes(prefix + suffix)
    before = _final_tree_snapshot(adopter)

    first = _run_protocol_projection(adapter, adopter)
    second = _run_protocol_projection(adapter, adopter)
    released = run_cli("journal", "--check", cwd=adopter)

    assert first == second
    assert first["immutable"] == [True, True, True, True]
    assert first["wal"] == [{
        "type": "HistoryClaimWal",
        "target": "ReadableTargetWal",
        "claim": "valid",
        "occurrence": expected,
    }]
    assert released.returncode == 1
    assert "Traceback" not in released.stderr
    base_records = len(_records(adopter / "journal.jsonl")) + len(prefix.splitlines())
    location = entry["intention"]["path"]
    wal_line = (
        f"ERROR: {location}: journal: open transaction {transaction.stem} "
        f"(published) on {location}: unknown\n"
    )
    if shape == "zero":
        expected_output = (f"journal: {base_records} record(s), 1 error(s)\n", wal_line)
    elif shape == "prepared":
        expected_output = (
            f"journal: {base_records + 1} record(s), 2 error(s)\n",
            f"ERROR: {location}: journal: unfinished transaction from run "
            f"{entry['run']}: the path is applied\n" + wal_line,
        )
    elif shape == "complete":
        expected_output = (f"journal: {base_records + 2} record(s), 1 error(s)\n", wal_line)
    elif shape in {"torn", "complete-torn-suffix"}:
        expected_output = (
            f"journal: {base_records} record(s), 1 error(s)\n",
            "ERROR: .validated-memory/local.jsonl: journal: non-empty history "
            "does not end with a line feed; the final record is not appendable "
            "without explicit targeted repair\n",
        )
    elif shape in {"conflicting", "complete-malformed-suffix"}:
        line = 1 if shape == "conflicting" else 3
        expected_output = (
            f"journal: {base_records} record(s), 1 error(s)\n",
            f"ERROR: .validated-memory/local.jsonl:{line}: journal: record is "
            "missing schema, at, version, adoption, run, durability, op, "
            "purpose, path, stage\n",
        )
    else:
        expected_output = (
            f"journal: {base_records + 3} record(s), 3 error(s)\n",
            f"ERROR: {location}: journal: unfinished transaction from run "
            f"{entry['run']}: the path is applied\n"
            f"ERROR: {location}: journal: transaction {transaction.stem} is "
            "recorded 3 times\n" + wal_line,
        )
    assert (released.stdout, released.stderr) == expected_output
    assert _final_tree_snapshot(adopter) == before
    assert transaction.exists()


@pytest.mark.parametrize("descendant_count", (1, 2))
def test_prepared_claim_remains_local_before_unrelated_valid_descendants(
    run_cli, tmp_path, monkeypatch, descendant_count
):
    """Prove later valid transactions do not consume a prepared claim.

    Genuine claim bytes followed by one or two complete, compatibility-valid
    transaction pairs exercise the local occurrence boundary.  The private
    projection remains shadow-only; exact released output and a whole-tree
    snapshot prove that checked inspection neither recovers nor rewrites.
    """
    adapter = _protocol_projection_adapter(tmp_path)
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, adopter, monkeypatch
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry["unconfirmed"] = "history-claim"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    prefix = history.read_bytes()[:claim["prefix"]["length"]]
    prepared = payload[:payload.find(b"\n") + 1]
    descendants = []
    for index in range(descendant_count):
        for record in claim["records"]:
            descendant = dict(record)
            descendant["transaction"] = f"unrelated-{index}"
            descendant["run"] = f"unrelated-run-{index}"
            descendant["path"] = f"unrelated-{index}.txt"
            descendants.append(descendant)
    history.write_bytes(prefix + prepared + _jsonl(*descendants))
    before = _final_tree_snapshot(adopter)

    first = _run_protocol_projection(adapter, adopter)
    second = _run_protocol_projection(adapter, adopter)
    checked = run_cli("journal", "--check", cwd=adopter)

    assert first == second
    assert first["wal"] == [{
        "type": "HistoryClaimWal",
        "target": "ReadableTargetWal",
        "claim": "valid",
        "occurrence": "prepared",
    }]
    assert first["conditions"] == second["conditions"]
    record_count = (
        len(_records(adopter / "journal.jsonl"))
        + len(prefix.splitlines()) + 1 + 2 * descendant_count
    )
    location = entry["intention"]["path"]
    assert (checked.returncode, checked.stdout, checked.stderr) == (
        1,
        f"journal: {record_count} record(s), 2 error(s)\n",
        f"ERROR: {location}: journal: unfinished transaction from run "
        f"{entry['run']}: the path is applied\n"
        f"ERROR: {location}: journal: open transaction {transaction.stem} "
        f"(published) on {location}: unknown\n",
    )
    assert _final_tree_snapshot(adopter) == before


def test_prepared_claim_replays_stored_commit_after_valid_descendants(
    run_cli, tmp_path, monkeypatch
):
    """Recovery appends only the stored committed line after descendants."""
    adopter = tmp_path / "adopter-replay"
    adopter.mkdir()
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, adopter, monkeypatch
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry["unconfirmed"] = "history-claim"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    prefix = history.read_bytes()[:claim["prefix"]["length"]]
    prepared_end = payload.find(b"\n") + 1
    prepared = payload[:prepared_end]
    descendants = []
    for record in claim["records"]:
        descendant = {
            **record,
            "transaction": "independent-descendant",
            "run": "independent-run",
            "path": "independent.txt",
        }
        descendants.append(descendant)
    descendant_bytes = _jsonl(*descendants)
    history.write_bytes(prefix + prepared + descendant_bytes)
    before = history.read_bytes()
    committed = (
        json.dumps(claim["records"][1], sort_keys=True) + "\n"
    ).encode("utf-8")

    recovered = run_cli("init", cwd=adopter)

    assert recovered.returncode == 0, (recovered.stdout, recovered.stderr)
    assert recovered.stderr == ""
    assert history.read_bytes() == before + committed
    assert history.read_bytes()[len(prefix + prepared):].startswith(descendant_bytes)
    assert not transaction.exists()


@pytest.mark.parametrize("shape", ("zero", "prepared", "complete"))
@pytest.mark.parametrize("fault", ("write:{wal}", "install:transactions"))
def test_uncertain_claim_requires_identical_wal_reestablishment_before_history(
    run_cli, tmp_path, monkeypatch, shape, fault
):
    """A failed proof-WAL barrier permits no append or cleanup."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, adopter, monkeypatch
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry["unconfirmed"] = "history-claim"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    prefix = history.read_bytes()[:claim["prefix"]["length"]]
    prepared_end = payload.find(b"\n") + 1
    history.write_bytes(
        prefix
        + {
            "zero": b"",
            "prepared": payload[:prepared_end],
            "complete": payload,
        }[shape]
    )
    history_before = history.read_bytes()
    wal_before = transaction.read_bytes()
    monkeypatch.setenv(
        "VALIDATED_MEMORY_PERSISTENCE_FAULT",
        fault.format(wal=transaction.name),
    )

    result = run_cli("init", cwd=adopter)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert "retained evidence could not be durably and coherently confirmed" in result.stderr
    assert history.read_bytes() == history_before
    assert transaction.read_bytes() == wal_before


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
@pytest.mark.parametrize("replacement", (b"\xff\n", b'{"broken":\n'))
def test_claim_reestablishment_rejects_substituted_invalid_wal_without_traceback(
    run_cli, tmp_path, monkeypatch, replacement
):
    """Invalid replacement bytes retain both histories and later work."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction, claim, _payload, history = _history_repair_fixture(
        run_cli, adopter, monkeypatch
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry["unconfirmed"] = "history-claim"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    history.write_bytes(history.read_bytes()[:claim["prefix"]["length"]])
    history_before = history.read_bytes()
    repository_before = (adopter / "journal.jsonl").read_bytes()
    later = transaction.with_name("ffffffffffffffff.json")
    later_entry = {**entry, "transaction": "ffffffffffffffff", "stage": "aborted"}
    later_entry.pop("unconfirmed", None)
    later_entry.pop("unconfirmed_reason", None)
    later.write_text(json.dumps(later_entry, sort_keys=True) + "\n", encoding="utf-8")
    later_before = later.read_bytes()
    process, ready, proceed = _rendezvous_init(
        adopter, "before-claim-reestablishment"
    )
    try:
        assert os.read(ready, 2) == b"1\n"
        transaction.write_bytes(replacement)
        os.write(proceed, b"1")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:
            process.kill()
            process.wait()

    assert process.returncode == 1, (stdout, stderr)
    assert stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert "Traceback" not in stderr
    assert "retained evidence could not be durably and coherently confirmed" in stderr
    assert transaction.read_bytes() == replacement
    assert later.read_bytes() == later_before
    assert history.read_bytes() == history_before
    assert (adopter / "journal.jsonl").read_bytes() == repository_before


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
def test_recovery_retains_wal_when_appended_history_identity_changes(
    run_cli, tmp_path, monkeypatch
):
    """An exact-byte name replacement is not the frozen append successor."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, adopter, monkeypatch
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry["unconfirmed"] = "history-claim"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    history.write_bytes(history.read_bytes()[:claim["prefix"]["length"]])
    process, ready, proceed = _rendezvous_init(
        adopter, "after-selected-history-append"
    )
    try:
        assert os.read(ready, 2) == b"1\n"
        appended = history.read_bytes()
        assert appended.endswith(payload)
        replacement = history.with_name("history.replacement")
        replacement.write_bytes(appended)
        os.replace(replacement, history)
        os.write(proceed, b"1")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:
            process.kill()
            process.wait()

    assert process.returncode == 1, (stdout, stderr)
    assert stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert "exact frozen coherent successor" in stderr
    assert transaction.exists()
    assert history.read_bytes() == appended


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
def test_resolution_stops_before_observation_when_opposite_history_changes(
    run_cli, tmp_path
):
    """The mutation append must confirm its frozen opposite before observe."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    harness = _external_harness_path(tmp_path, "resolution-opposite")
    assert run_cli(
        "init", "--harness-memory", str(harness), cwd=tmp_path
    ).returncode == 0
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    transaction_id = _diverged(tmp_path)
    transaction = (
        tmp_path / ".validated-memory" / "transactions" / f"{transaction_id}.json"
    )
    repository = tmp_path / "journal.jsonl"
    local = tmp_path / ".validated-memory" / "local.jsonl"
    observations_before = sum(
        record["op"] == "observe" for record in _records(repository)
    )
    process_env_point = "after-selected-history-append"
    ready_read, ready_write = os.pipe()
    continue_read, continue_write = os.pipe()
    process = subprocess.Popen(
        [
            sys.executable, "-P", "-m", "validated_memory", "journal",
            "--resolve", transaction_id, "--accept",
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT),
            "VALIDATED_MEMORY_TEST_RENDEZVOUS": process_env_point,
            "VALIDATED_MEMORY_TEST_READY_FD": str(ready_write),
            "VALIDATED_MEMORY_TEST_CONTINUE_FD": str(continue_read),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        pass_fds=(ready_write, continue_read),
    )
    os.close(ready_write)
    os.close(continue_read)
    try:
        assert os.read(ready_read, 2) == b"1\n"
        repository_after_mutation = repository.read_bytes()
        local_before = local.read_bytes()
        replacement = local.with_name("local.replacement")
        replacement.write_bytes(local_before)
        os.replace(replacement, local)
        os.write(continue_write, b"1")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(ready_read)
        os.close(continue_write)
        if process.poll() is None:
            process.kill()
            process.wait()

    assert process.returncode == 1, (stdout, stderr)
    assert stdout == ""
    assert "exact frozen coherent successor" in stderr
    assert transaction.exists()
    assert repository.read_bytes() == repository_after_mutation
    assert local.read_bytes() == local_before
    assert sum(
        record["op"] == "observe" for record in _records(repository)
    ) == observations_before


def _topology_marker_mutant(tmp_path, marker):
    """Return a package whose topology inspector fails after marker creation."""
    mutant = tmp_path / "topology-marker-mutant"
    shutil.copytree(REPO_ROOT / "validated_memory", mutant / "validated_memory")
    topology = mutant / "validated_memory" / "journal" / "topology.py"
    source = topology.read_text(encoding="utf-8")
    source = source.replace(
        "import json\n",
        "import json\nfrom pathlib import Path\n",
        1,
    )
    function = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "inspect"
    )
    lines = source.splitlines(keepends=True)
    lines.insert(
        function.body[0].lineno - 1,
        f"    if Path({str(marker)!r}).exists():\n"
        "        raise RuntimeError('post-effect topology failure')\n",
    )
    topology.write_text("".join(lines), encoding="utf-8")
    return mutant


def _rendezvous_mutant_init(adopter, mutant, point):
    """Start mutant init paused at one protocol successor boundary."""
    ready_read, ready_write = os.pipe()
    continue_read, continue_write = os.pipe()
    process = subprocess.Popen(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        cwd=adopter,
        env={
            **os.environ,
            "PYTHONPATH": str(mutant),
            "VALIDATED_MEMORY_TEST_RENDEZVOUS": point,
            "VALIDATED_MEMORY_TEST_READY_FD": str(ready_write),
            "VALIDATED_MEMORY_TEST_CONTINUE_FD": str(continue_read),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        pass_fds=(ready_write, continue_read),
    )
    os.close(ready_write)
    os.close(continue_read)
    return process, ready_read, continue_write


@pytest.mark.skipif(os.name != "posix", reason="descriptor rendezvous is POSIX-only")
def test_post_append_topology_failure_is_terminal_and_retains_later_wal(
    run_cli, tmp_path, monkeypatch
):
    """Unavailable topology cannot masquerade as a valid append successor."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, adopter, monkeypatch
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry["unconfirmed"] = "history-claim"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    history.write_bytes(history.read_bytes()[:claim["prefix"]["length"]])
    later = transaction.with_name("ffffffffffffffff.json")
    later_entry = {**entry, "transaction": "ffffffffffffffff", "stage": "aborted"}
    later_entry.pop("unconfirmed", None)
    later_entry.pop("unconfirmed_reason", None)
    later.write_text(json.dumps(later_entry, sort_keys=True) + "\n", encoding="utf-8")
    later_before = later.read_bytes()
    marker = tmp_path / "fail-topology"
    mutant = _topology_marker_mutant(tmp_path, marker)
    process, ready, proceed = _rendezvous_mutant_init(
        adopter, mutant, "after-selected-history-append"
    )
    try:
        assert os.read(ready, 2) == b"1\n"
        marker.write_text("fail\n", encoding="utf-8")
        os.write(proceed, b"1")
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(ready)
        os.close(proceed)
        if process.poll() is None:
            process.kill()
            process.wait()

    assert process.returncode == 1, (stdout, stderr)
    assert stdout == "init: 0 created, 0 kept, 1 error(s), 0 warning(s)\n"
    assert "Traceback" not in stderr
    assert "exact frozen coherent successor" in stderr
    assert transaction.exists()
    assert later.read_bytes() == later_before
    assert history.read_bytes().endswith(payload)


@pytest.mark.parametrize("suffix_kind", ("malformed", "same-transaction"))
def test_prepared_claim_rejects_invalid_or_same_transaction_suffix(
    run_cli, tmp_path, monkeypatch, suffix_kind
):
    """Prove only complete unrelated descendants preserve prepared status.

    One malformed line and one compatibility-valid but duplicate transaction
    record cover the two adversarial suffix classes.  Projection and released
    checked output remain read-only; this test grants no recovery authority.
    """
    adapter = _protocol_projection_adapter(tmp_path)
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, adopter, monkeypatch
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry["unconfirmed"] = "history-claim"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    prefix = history.read_bytes()[:claim["prefix"]["length"]]
    prepared = payload[:payload.find(b"\n") + 1]
    if suffix_kind == "malformed":
        suffix = b"{}\n"
    else:
        conflicting = dict(claim["records"][0])
        conflicting["at"] = "2099-01-01T00:00:00Z"
        suffix = _jsonl(conflicting)
    history.write_bytes(prefix + prepared + suffix)
    before = _final_tree_snapshot(adopter)

    projection = _run_protocol_projection(adapter, adopter)
    checked = run_cli("journal", "--check", cwd=adopter)

    assert projection["wal"] == [{
        "type": "HistoryClaimWal",
        "target": "ReadableTargetWal",
        "claim": "valid",
        "occurrence": "conflicting",
    }]
    location = entry["intention"]["path"]
    if suffix_kind == "malformed":
        expected = (
            1,
            f"journal: {len(_records(adopter / 'journal.jsonl')) + len(prefix.splitlines())} record(s), 1 error(s)\n",
            "ERROR: .validated-memory/local.jsonl:2: journal: record is missing "
            "schema, at, version, adoption, run, durability, op, purpose, path, stage\n",
        )
    else:
        expected = (
            1,
            f"journal: {len(_records(adopter / 'journal.jsonl')) + len(prefix.splitlines()) + 2} record(s), 3 error(s)\n",
            f"ERROR: {location}: journal: unfinished transaction from run "
            f"{entry['run']}: the path is applied\n"
            f"ERROR: {location}: journal: unfinished transaction from run "
            f"{entry['run']}: the path is applied\n"
            f"ERROR: {location}: journal: open transaction {transaction.stem} "
            f"(published) on {location}: unknown\n",
        )
    assert (checked.returncode, checked.stdout, checked.stderr) == expected
    assert _final_tree_snapshot(adopter) == before


def test_claimless_history_claim_is_total_and_publicly_unchanged(
    run_cli, tmp_path, monkeypatch
):
    """Pin claimless zero-occurrence refusal and exact released rendering.

    The fixture removes only claim metadata from a real retained WAL; it proves
    read-only classification and selected refusal, not recovery authority.
    """
    adapter = _protocol_projection_adapter(tmp_path)
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction, claim, _payload, history = _history_repair_fixture(
        run_cli, adopter, monkeypatch
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry.pop("history_append")
    entry.pop("history_timestamps")
    entry["unconfirmed"] = "history-claim"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    history.write_bytes(history.read_bytes()[:claim["prefix"]["length"]])
    before = _final_tree_snapshot(adopter)

    projection = _run_protocol_projection(adapter, adopter)
    checked = run_cli("journal", "--check", cwd=adopter)
    resolved = run_cli(
        "journal", "--resolve", transaction.stem, "--accept", cwd=adopter
    )

    assert projection["wal"] == [{
        "type": "HistoryClaimWal",
        "target": "ReadableTargetWal",
        "claim": "claimless",
        "occurrence": "zero",
    }]
    assert projection["immutable"] == [True, True, True]
    assert checked.returncode == 1
    assert checked.stdout == f"journal: {len(_records(adopter / 'journal.jsonl')) + len(_records(history))} record(s), 1 error(s)\n"
    location = entry["intention"]["path"]
    assert checked.stderr == (
        f"ERROR: {location}: journal: open transaction {transaction.stem} "
        f"(published) on {location}: unknown\n"
    )
    assert resolved.returncode == 1
    assert resolved.stdout == ""
    assert "Traceback" not in resolved.stderr
    assert "Nothing has been changed." in resolved.stderr
    assert _final_tree_snapshot(adopter) == before


def test_invalid_history_claim_is_conflicting_and_publicly_unchanged(
    run_cli, tmp_path, monkeypatch
):
    """Prove a claim unbound from its WAL cannot describe an occurrence.

    The projection observes one forged field in otherwise genuine retained
    evidence; exact CLI output and the full tree snapshot prove read-only
    refusal, not that every possible forgery has the same diagnostic.
    """
    adapter = _protocol_projection_adapter(tmp_path)
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction, _claim, _payload, history = _history_repair_fixture(
        run_cli, adopter, monkeypatch
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry["unconfirmed"] = "history-claim"
    entry["history_append"]["records"][0]["path"] = "foreign/path"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    before = _final_tree_snapshot(adopter)

    projection = _run_protocol_projection(adapter, adopter)
    checked = run_cli("journal", "--check", cwd=adopter)

    assert projection["wal"] == [{
        "type": "HistoryClaimWal",
        "target": "ReadableTargetWal",
        "claim": "invalid",
        "occurrence": "conflicting",
    }]
    assert projection["immutable"] == [True, True, True, True]
    records = len(_records(adopter / "journal.jsonl")) + len(_records(history))
    location = entry["intention"]["path"]
    assert (checked.returncode, checked.stdout, checked.stderr) == (
        1,
        f"journal: {records} record(s), 1 error(s)\n",
        f"ERROR: {location}: journal: open transaction {transaction.stem} "
        f"(published) on {location}: unknown\n",
    )
    assert _final_tree_snapshot(adopter) == before


def test_complete_claim_ignores_an_unrelated_opposite_history_gate(
    run_cli, tmp_path, monkeypatch
):
    """Prove occurrence authority is local while topology gates stay visible.

    A valid complete local claim is paired with a foreign adoption only in a
    later repository record.  The projection must retain that separate gate;
    it does not prove the future operation may act through the gate.
    """
    adapter = _protocol_projection_adapter(tmp_path)
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction, _claim, _payload, _history = _history_repair_fixture(
        run_cli, adopter, monkeypatch
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry["unconfirmed"] = "history-claim"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    repository = adopter / "journal.jsonl"
    records = _records(repository)
    assert len(records) > 1
    records[-1]["adoption"] = "unrelated-foreign-adoption"
    _rewrite(repository, records)
    before = _final_tree_snapshot(adopter)

    projection = _run_protocol_projection(adapter, adopter)

    assert projection["wal"] == [{
        "type": "HistoryClaimWal",
        "target": "ReadableTargetWal",
        "claim": "valid",
        "occurrence": "complete",
    }]
    assert any(
        condition["identity"].startswith("history.topology_")
        for condition in projection["conditions"]
    )
    assert _final_tree_snapshot(adopter) == before


@pytest.mark.parametrize("field", ("note", "path"))
def test_claimless_incidental_transaction_text_is_not_an_occurrence(
    run_cli, tmp_path, monkeypatch, field
):
    """Prove claimless evidence uses parsed transaction fields, not raw text.

    A compatible unrelated record contains the exact WAL id in one ordinary
    text field.  This covers those two false-positive surfaces, not arbitrary
    damaged bytes, which have their own unavailable-state test.
    """
    adapter = _protocol_projection_adapter(tmp_path)
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction, claim, _payload, history = _history_repair_fixture(
        run_cli, adopter, monkeypatch
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry.pop("history_append")
    entry.pop("history_timestamps")
    entry["unconfirmed"] = "history-claim"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    prefix = history.read_bytes()[:claim["prefix"]["length"]]
    unrelated = _legacy_record(
        adoption=entry["adoption"],
        durability="local",
        at="2099-01-01T00:00:00Z",
    )
    unrelated[field] = transaction.stem
    history.write_bytes(
        prefix + (json.dumps(unrelated, sort_keys=True) + "\n").encode("utf-8")
    )
    before = _final_tree_snapshot(adopter)

    projection = _run_protocol_projection(adapter, adopter)

    assert projection["wal"] == [{
        "type": "HistoryClaimWal",
        "target": "ReadableTargetWal",
        "claim": "claimless",
        "occurrence": "zero",
    }]
    assert _final_tree_snapshot(adopter) == before


@pytest.mark.parametrize("shape", ("prepared", "complete"))
def test_claimless_semantic_transaction_occurrence_is_conflicting(
    run_cli, tmp_path, monkeypatch, shape
):
    """Prove parsed transaction fields conflict without a stored claim.

    The fixture retains a genuine prepared half or pair but removes only its
    claim metadata.  It distinguishes semantic occurrence from incidental
    text; malformed histories remain outside this compatible-artifact slice.
    """
    adapter = _protocol_projection_adapter(tmp_path)
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction, claim, payload, history = _history_repair_fixture(
        run_cli, adopter, monkeypatch
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry.pop("history_append")
    entry.pop("history_timestamps")
    entry["unconfirmed"] = "history-claim"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    prepared_end = payload.find(b"\n") + 1
    occurrence = payload[:prepared_end] if shape == "prepared" else payload
    history.write_bytes(history.read_bytes()[:claim["prefix"]["length"]] + occurrence)
    before = _final_tree_snapshot(adopter)

    projection = _run_protocol_projection(adapter, adopter)

    assert projection["wal"] == [{
        "type": "HistoryClaimWal",
        "target": "ReadableTargetWal",
        "claim": "claimless",
        "occurrence": "conflicting",
    }]
    assert _final_tree_snapshot(adopter) == before


def test_claimless_damaged_history_does_not_infer_zero_from_raw_bytes(
    run_cli, tmp_path, monkeypatch
):
    """Prove incomplete parsing yields unavailable, not a byte-scan verdict.

    A torn object contains the exact WAL id but establishes no complete local
    artifact.  The private projection exposes unavailability while released
    output remains the independent history-damage refusal.
    """
    adapter = _protocol_projection_adapter(tmp_path)
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction, claim, _payload, history = _history_repair_fixture(
        run_cli, adopter, monkeypatch
    )
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry.pop("history_append")
    entry.pop("history_timestamps")
    entry["unconfirmed"] = "history-claim"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    prefix = history.read_bytes()[:claim["prefix"]["length"]]
    history.write_bytes(
        prefix + f'{{"transaction":{json.dumps(transaction.stem)}'.encode("utf-8")
    )
    before = _final_tree_snapshot(adopter)

    projection = _run_protocol_projection(adapter, adopter)
    released = run_cli("journal", "--check", cwd=adopter)

    assert projection["wal"] == [{
        "type": "HistoryClaimWal",
        "target": "ReadableTargetWal",
        "claim": "claimless",
        "occurrence": "unavailable",
    }]
    base_records = len(_records(adopter / "journal.jsonl")) + len(prefix.splitlines())
    assert (released.returncode, released.stdout, released.stderr) == (
        1,
        f"journal: {base_records} record(s), 1 error(s)\n",
        "ERROR: .validated-memory/local.jsonl: journal: non-empty history does "
        "not end with a line feed; the final record is not appendable without "
        "explicit targeted repair\n",
    )
    assert _final_tree_snapshot(adopter) == before


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
def test_history_claim_unreadable_target_has_its_own_total_variant(
    run_cli, tmp_path, monkeypatch
):
    """Prove an unreadable target is explicit and preserves every artifact.

    Mode bits provide the black-box read failure on non-root POSIX runs; the
    skip records that root ignores this mechanism rather than weakening the
    classification assertion.
    """
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert run_cli("init", cwd=adopter).returncode == 0
    target = adopter / ".gitignore"
    target.write_text("adopter change\n", encoding="utf-8")
    monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", "history-claim")
    assert run_cli("init", cwd=adopter).returncode == 1
    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    adapter = _protocol_projection_adapter(tmp_path)
    before = _final_tree_snapshot(adopter)
    target.chmod(0o000)
    try:
        projection = _run_protocol_projection(adapter, adopter)
        released = run_cli("journal", "--check", cwd=adopter)
        assert projection["wal"] == [{
            "type": "HistoryClaimWal",
            "target": "UnreadableTargetWal",
            "claim": "claimless",
            "occurrence": "zero",
        }]
        assert projection["immutable"] == [True, True]
        local = adopter / ".validated-memory" / "local.jsonl"
        records = len(_records(adopter / "journal.jsonl")) + (
            len(_records(local)) if local.exists() else 0
        )
        transaction = next(
            (adopter / ".validated-memory" / "transactions").glob("*.json")
        )
        assert (released.returncode, released.stdout, released.stderr) == (
            1,
            f"journal: {records} record(s), 1 error(s)\n",
            f"ERROR: .gitignore: journal: open transaction {transaction.stem} "
            "(published) on .gitignore: unknown\n",
        )
    finally:
        target.chmod(0o644)
    assert _final_tree_snapshot(adopter) == before


@pytest.mark.skipif(
    os.geteuid() == 0, reason="permission bits do not bind root (CI container)"
)
@pytest.mark.parametrize("claim_state", ("valid", "invalid"))
def test_claimed_history_unreadable_target_preserves_exact_public_result(
    run_cli, tmp_path, monkeypatch, claim_state
):
    """Pin valid and invalid claims independently of target readability.

    A genuine append-uncertain WAL supplies the valid proof, and one changed
    record supplies the invalid case.  POSIX mode bits cannot produce this
    read failure for root, so that platform condition is an explicit skip.
    """
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert run_cli("init", cwd=adopter).returncode == 0
    target = adopter / ".gitignore"
    target.write_text("adopter change\n", encoding="utf-8")
    monkeypatch.setenv("VALIDATED_MEMORY_PERSISTENCE_FAULT", "append:journal.jsonl")
    assert run_cli("init", cwd=adopter).returncode == 1
    monkeypatch.delenv("VALIDATED_MEMORY_PERSISTENCE_FAULT")
    transaction = next((adopter / ".validated-memory" / "transactions").glob("*.json"))
    entry = json.loads(transaction.read_text(encoding="utf-8"))
    entry["unconfirmed"] = "history-claim"
    if claim_state == "invalid":
        entry["history_append"]["records"][0]["path"] = "foreign/path"
    transaction.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    adapter = _protocol_projection_adapter(tmp_path)
    before = _final_tree_snapshot(adopter)
    target.chmod(0o000)
    try:
        projection = _run_protocol_projection(adapter, adopter)
        released = run_cli("journal", "--check", cwd=adopter)
        expected_occurrence = "complete" if claim_state == "valid" else "conflicting"
        assert projection["wal"] == [{
            "type": "HistoryClaimWal",
            "target": "UnreadableTargetWal",
            "claim": claim_state,
            "occurrence": expected_occurrence,
        }]
        assert projection["immutable"] == [True, True, True]
        local = adopter / ".validated-memory" / "local.jsonl"
        records = len(_records(adopter / "journal.jsonl")) + (
            len(_records(local)) if local.exists() else 0
        )
        assert (released.returncode, released.stdout, released.stderr) == (
            1,
            f"journal: {records} record(s), 1 error(s)\n",
            f"ERROR: .gitignore: journal: open transaction {transaction.stem} "
            "(published) on .gitignore: unknown\n",
        )
    finally:
        target.chmod(0o644)
    assert _final_tree_snapshot(adopter) == before


def test_topology_shadow_fails_open_for_ordinary_exceptions(run_cli, tmp_path):
    """Prove a topology implementation error cannot alter released reporting.

    A copied-package mutant raises at the production call and is compared with
    an ordinary subprocess.  This proves fail-open rendering, not recovery.
    """
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert run_cli("init", cwd=adopter).returncode == 0
    mutant = tmp_path / "raising-shadow"
    shutil.copytree(REPO_ROOT / "validated_memory", mutant / "validated_memory")
    topology = mutant / "validated_memory" / "journal" / "topology.py"
    source = topology.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "inspect"
    )
    lines = source.splitlines(keepends=True)
    lines.insert(function.body[0].lineno - 1, '    raise RuntimeError("distinctive shadow failure")\n')
    topology.write_text("".join(lines), encoding="utf-8")
    before = _final_tree_snapshot(adopter)

    for arguments in ((), ("--check",)):
        control = run_cli("journal", *arguments, cwd=adopter)
        raised = _run_mutant_journal(mutant, adopter, *arguments)
        assert (raised.returncode, raised.stdout, raised.stderr) == (
            control.returncode,
            control.stdout,
            control.stderr,
        )
        assert "distinctive shadow failure" not in raised.stderr
        assert _final_tree_snapshot(adopter) == before

    protocol_tree = ast.parse(
        (REPO_ROOT / "validated_memory" / "journal" / "protocol.py").read_text(
            encoding="utf-8"
        )
    )
    shadow_tries = [
        node
        for node in ast.walk(protocol_tree)
        if isinstance(node, ast.Try)
        and any(
            isinstance(statement, ast.Assign)
            and any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "inspect_topology"
                for call in ast.walk(statement)
            )
            for statement in node.body
        )
    ]
    assert len(shadow_tries) == 2
    reporting = next(
        node for node in shadow_tries if "TopologyUnavailable" in ast.unparse(node)
    )
    candidate = next(node for node in shadow_tries if node is not reporting)
    assert len(reporting.handlers) == 1
    handler_type = reporting.handlers[0].type
    assert isinstance(handler_type, ast.Name)
    assert handler_type.id == "Exception"
    assert "raise ValueError" in ast.unparse(candidate.handlers[0])


def test_topology_inspector_exception_fails_closed_before_recovery_effect(
    run_cli, tmp_path
):
    """A shadow failure remains fail-open only for read-only inspection."""
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction = _diverged(adopter, kill_after=None)
    mutant = tmp_path / "raising-recovery-shadow"
    shutil.copytree(REPO_ROOT / "validated_memory", mutant / "validated_memory")
    topology = mutant / "validated_memory" / "journal" / "topology.py"
    source = topology.read_text(encoding="utf-8")
    function = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "inspect"
    )
    lines = source.splitlines(keepends=True)
    lines.insert(
        function.body[0].lineno - 1,
        '    raise RuntimeError("distinctive recovery shadow failure")\n',
    )
    topology.write_text("".join(lines), encoding="utf-8")
    transaction_path = (
        adopter / ".validated-memory" / "transactions" / f"{transaction}.json"
    )
    wal_before = transaction_path.read_bytes()
    target_before = (adopter / ".gitignore").read_bytes()

    result = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory", "init"],
        cwd=adopter,
        env={**os.environ, "PYTHONPATH": str(mutant)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "distinctive recovery shadow failure" in result.stderr
    assert f"transaction {transaction}" in result.stderr
    assert "visible or may be visible" not in result.stderr
    assert transaction_path.read_bytes() == wal_before
    assert (adopter / ".gitignore").read_bytes() == target_before
    assert not any(
        record.get("transaction") == transaction
        for record in _records(adopter / "journal.jsonl")
    )


def _claimless_prepared_provision(adopter):
    """Create one claimless WAL and its independently reconstructible gap."""
    transaction_id = _diverged(adopter, kill_after=None)
    transaction = (
        adopter / ".validated-memory" / "transactions" / f"{transaction_id}.json"
    )
    wal = json.loads(transaction.read_text(encoding="utf-8"))
    intention = wal["intention"]
    prepared = {
        "schema": 1,
        "at": "2000-01-01T00:00:00Z",
        "version": wal["version"],
        "adoption": wal["adoption"],
        "run": wal["run"],
        "transaction": transaction_id,
        "durability": intention["durability"],
        "op": intention["op"],
        "purpose": intention["purpose"],
        "path": intention["path"],
        "stage": "prepared",
        "mode": stat.S_IMODE((adopter / intention["path"]).stat().st_mode),
        "preimage": wal["preimage_blob"],
        "postimage": wal["postimage"]["digest"],
    }
    if wal["prior_bytes"] is not None:
        prepared["prior_bytes"] = wal["prior_bytes"]
    if intention.get("note") is not None:
        prepared["note"] = intention["note"]
    history = adopter / "journal.jsonl"
    history.write_bytes(history.read_bytes() + _jsonl(prepared))
    return transaction, prepared, history


@pytest.mark.parametrize("other_at", ("1900-01-01T00:00:00Z", "2999-01-01T00:00:00Z"))
def test_exact_claimless_provision_is_order_independent(run_cli, tmp_path, other_at):
    """WAL sort order cannot change exact prepared-gap authorization."""
    transaction, _prepared, _history = _claimless_prepared_provision(tmp_path)
    selected = json.loads(transaction.read_text(encoding="utf-8"))
    other = {**selected, "at": other_at, "transaction": "ffffffffffffffff", "stage": "aborted"}
    other["reason"] = "independent fixture"
    other_path = transaction.with_name("ffffffffffffffff.json")
    other_path.write_text(json.dumps(other, sort_keys=True) + "\n", encoding="utf-8")

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not transaction.exists()
    assert not other_path.exists()


@pytest.mark.parametrize(
    "field,replacement",
    (
        ("adoption", "foreign"),
        ("run", "foreign-run"),
        ("path", "foreign.txt"),
        ("durability", "local"),
        ("op", "append"),
        ("purpose", "foreign-purpose"),
        ("note", "foreign note"),
        ("preimage", "sha256:" + "1" * 64),
        ("postimage", "sha256:" + "2" * 64),
        ("prior_bytes", 999),
        ("mode", 0o600),
    ),
)
def test_mismatched_claimless_gap_never_becomes_a_provision(
    run_cli, tmp_path, field, replacement
):
    """Every reconstructible field participates in provision identity."""
    transaction, prepared, history = _claimless_prepared_provision(tmp_path)
    records = _records(history)
    records[-1] = {**prepared, field: replacement}
    _rewrite(history, records)
    wal_before = transaction.read_bytes()

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert transaction.read_bytes() == wal_before
    assert not any(
        record.get("transaction") == transaction.stem
        and record["stage"] == "committed"
        for record in _records(history)
    )


@pytest.mark.parametrize("cleanup_at", ("1900-01-01T00:00:00Z", "2999-01-01T00:00:00Z"))
def test_unprovided_gate_is_per_item_and_cleanup_order_independent(
    run_cli, tmp_path, cleanup_at
):
    """A refused append neither terminalizes nor lends authority to cleanup."""
    transaction, prepared, history = _claimless_prepared_provision(tmp_path)
    records = _records(history)
    records[-1] = {**prepared, "purpose": "mismatched-purpose"}
    _rewrite(history, records)
    selected = json.loads(transaction.read_text(encoding="utf-8"))
    cleanup = {
        **selected,
        "at": cleanup_at,
        "transaction": "ffffffffffffffff",
        "stage": "aborted",
        "reason": "cleanup-only fixture",
    }
    cleanup_path = transaction.with_name("ffffffffffffffff.json")
    cleanup_path.write_text(json.dumps(cleanup, sort_keys=True) + "\n", encoding="utf-8")
    wal_before = transaction.read_bytes()

    result = run_cli("init", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "visible or may be visible" not in result.stderr
    assert transaction.read_bytes() == wal_before
    assert not cleanup_path.exists()
    assert not any(
        record.get("transaction") == transaction.stem
        and record["stage"] == "committed"
        for record in _records(history)
    )


def test_topology_shadow_observes_fresh_and_idempotent_adoption_without_authority(
    run_cli, tmp_path
):
    adapter = _topology_adapter(tmp_path)
    adopter = tmp_path / "adopter"
    _topology_history(adopter)
    before = _run_topology(adapter, adopter)
    assert before["capabilities"]["appendable"] is True
    assert before["active_adoption"] is None

    first = run_cli("init", cwd=adopter)
    assert first.returncode == 0, (first.stdout, first.stderr)
    assert first.stdout == (
        "init: ignored /.validated-memory/ in .gitignore\n"
        "init: created knowledge\n"
        "init: created memory\n"
        "init: created memory/MEMORY.md\n"
        "init: created validated-memory.md\n"
        "init: created knowledge-extension.md\n"
        "init: 5 created, 0 kept, 0 error(s), 0 warning(s)\n"
    )
    assert first.stderr == ""
    after = _run_topology(adapter, adopter)
    assert after["snapshot_present"] is True
    assert after["active_adoption"] is not None
    assert _condition(after, "topology_unknown_before")
    stable_tree = _final_tree_snapshot(adopter)

    second = run_cli("init", cwd=adopter)
    assert second.returncode == 0, (second.stdout, second.stderr)
    assert second.stdout == (
        "init: kept knowledge\n"
        "init: kept memory\n"
        "init: kept memory/MEMORY.md\n"
        "init: kept validated-memory.md\n"
        "init: kept knowledge-extension.md\n"
        "init: 0 created, 5 kept, 0 error(s), 0 warning(s)\n"
    )
    assert second.stderr == ""
    assert _final_tree_snapshot(adopter) == stable_tree
    assert _run_topology(adapter, adopter) == after


def test_topology_shadow_observes_refused_adoption_without_claiming_the_decision(
    run_cli, tmp_path
):
    adapter = _topology_adapter(tmp_path)
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    assert run_cli("init", cwd=adopter).returncode == 0
    target = adopter / "validated-memory.md"
    target.unlink()
    target.mkdir()
    before_projection = _run_topology(adapter, adopter)
    before_tree = _final_tree_snapshot(adopter)

    result = run_cli("init", cwd=adopter)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == (
        "init: kept knowledge\n"
        "init: kept memory\n"
        "init: kept memory/MEMORY.md\n"
        "init: kept knowledge-extension.md\n"
        "init: 0 created, 4 kept, 1 error(s), 0 warning(s)\n"
    )
    assert result.stderr == (
        "ERROR: validated-memory.md: create: file could not be created: "
        "validated-memory.md is a directory, and this create expects it to be "
        "absent. Nothing has been written.\n"
    )
    assert _final_tree_snapshot(adopter) == before_tree
    assert _run_topology(adapter, adopter) == before_projection


def test_topology_shadow_observes_successful_and_refused_recovery(
    run_cli, tmp_path
):
    adapter = _topology_adapter(tmp_path)
    recovered = tmp_path / "recovered"
    recovered.mkdir()
    transaction = _diverged(recovered, kill_after=None)
    before = _run_topology(adapter, recovered)

    result = run_cli("init", cwd=recovered)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == (
        f"init: recovered .gitignore from transaction {transaction}\n"
        "init: created knowledge\n"
        "init: created memory\n"
        "init: created memory/MEMORY.md\n"
        "init: created validated-memory.md\n"
        "init: created knowledge-extension.md\n"
        "init: 5 created, 0 kept, 0 error(s), 0 warning(s)\n"
    )
    assert result.stderr == ""
    assert not _transactions(recovered)
    after = _run_topology(adapter, recovered)
    assert before["snapshot_present"] is True
    assert after["snapshot_present"] is True
    assert after["physical_count"] > before["physical_count"]

    refused = tmp_path / "refused"
    refused.mkdir()
    transaction = _diverged(refused)
    before_projection = _run_topology(adapter, refused)
    watched = [
        refused / "journal.jsonl",
        refused / ".validated-memory" / "local.jsonl",
        refused / ".validated-memory" / "transactions" / f"{transaction}.json",
        refused / ".gitignore",
    ]
    before_bytes = {path: path.read_bytes() for path in watched if path.exists()}
    result = run_cli("init", cwd=refused)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == "init: 0 created, 0 kept, 2 error(s), 0 warning(s)\n"
    assert result.stderr == (
        f"ERROR: .gitignore: journal: transaction {transaction} published "
        ".gitignore, but .gitignore is a file now and not what was published; "
        "nothing here can say whether that is wanted -- run 'validated-memory "
        f"journal --resolve {transaction}' with one of --accept, --restore or "
        "--abandon\n"
        "ERROR: .gitignore: ignore-rule: the vault's ignore entry "
        "(/.validated-memory/) could not be written: .gitignore has an "
        f"unresolved transaction {transaction} that recovery could neither "
        "complete nor discard; nothing may write to it until it is closed -- "
        f"run 'validated-memory journal --resolve {transaction}' with one of "
        "--accept, --restore or --abandon. Nothing has been written.\n"
    )
    assert {path: path.read_bytes() for path in watched if path.exists()} == before_bytes
    assert _run_topology(adapter, refused) == before_projection


@pytest.mark.parametrize("resolution", ["accept", "abandon", "restore"])
def test_topology_shadow_observes_each_successful_resolution(
    run_cli, tmp_path, resolution
):
    adapter = _topology_adapter(tmp_path)
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    if resolution == "restore":
        transaction = _diverged(adopter, before="build/\n")
    else:
        transaction = _diverged(adopter)
    before = _run_topology(adapter, adopter)
    before_records = _records(adopter / "journal.jsonl")
    before_tree = _final_tree_snapshot(adopter)
    transaction_path = (
        adopter / ".validated-memory" / "transactions" / f"{transaction}.json"
    )
    wal = json.loads(transaction_path.read_text(encoding="utf-8"))

    result = run_cli(
        "journal", "--resolve", transaction, f"--{resolution}", cwd=adopter
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    if resolution == "restore":
        kept = hashlib.sha256(b"an adopter wrote this\n").hexdigest()
        assert result.stdout == (
            f"journal: resolved {transaction} (--restore); the discarded bytes "
            f"are kept at .validated-memory/preimages/{kept}\n"
        )
    else:
        assert result.stdout == f"journal: resolved {transaction} (--{resolution})\n"
    assert result.stderr == ""
    assert not _transactions(adopter)
    after_records = _records(adopter / "journal.jsonl")
    after_tree = _final_tree_snapshot(adopter)
    changed = {
        entry[0]
        for entry in before_tree + after_tree
        if next((other for other in before_tree if other[0] == entry[0]), None)
        != next(
            (other for other in after_tree if other[0] == entry[0]),
            None,
        )
    }
    assert transaction_path.relative_to(adopter).as_posix() in changed
    if resolution == "restore":
        assert after_records == before_records
        assert (adopter / ".gitignore").read_text(encoding="utf-8") == "build/\n"
        assert changed == {
            ".gitignore",
            ".validated-memory/lock",
            transaction_path.relative_to(adopter).as_posix(),
            f".validated-memory/preimages/{kept}",
        }
    else:
        assert after_records[: len(before_records)] == before_records
        mutation = {
            "schema": wal["schema"],
            "version": wal["version"],
            "run": wal["run"],
            "adoption": wal["adoption"],
            "transaction": transaction,
            **wal["intention"],
            "preimage": None,
            "postimage": wal["postimage"]["digest"],
        }
        assert [
            {key: value for key, value in record.items() if key not in {"at", "stage"}}
            for record in after_records[len(before_records):len(before_records) + 2]
        ] == [mutation, mutation]
        assert [
            record["stage"]
            for record in after_records[len(before_records):len(before_records) + 2]
        ] == ["prepared", "committed"]
        observation = after_records[-1]
        assert len(after_records) == len(before_records) + 3
        assert set(observation) == {
            "schema", "version", "at", "run", "adoption", "durability",
            "op", "purpose", "path", "stage", "note",
        }
        assert observation["path"] == ".gitignore"
        assert observation["op"] == "observe"
        assert observation["stage"] == "committed"
        expected_note = (
            f"accepted after divergence: transaction {transaction} found file"
            if resolution == "accept"
            else f"abandoned: transaction {transaction}, path left as found"
        )
        assert observation["note"] == expected_note
        assert (adopter / ".gitignore").read_text(encoding="utf-8") == (
            "an adopter wrote this\n"
        )
        assert changed == {
            ".validated-memory/lock",
            "journal.jsonl",
            transaction_path.relative_to(adopter).as_posix(),
        }
    after = _run_topology(adapter, adopter)
    assert before["snapshot_present"] is True
    assert after["snapshot_present"] is True


def test_topology_shadow_observes_refused_resolution_without_wal_authority(
    run_cli, tmp_path
):
    adapter = _topology_adapter(tmp_path)
    adopter = tmp_path / "adopter"
    adopter.mkdir()
    transaction = _diverged(adopter, before="build/\n")
    blob = next((adopter / ".validated-memory" / "preimages").iterdir())
    blob.write_text("wrong bytes\n", encoding="utf-8")
    before_projection = _run_topology(adapter, adopter)
    watched = [
        adopter / "journal.jsonl",
        adopter / ".validated-memory" / "local.jsonl",
        adopter / ".validated-memory" / "transactions" / f"{transaction}.json",
        adopter / ".gitignore",
        blob,
    ]
    before_bytes = {path: path.read_bytes() for path in watched if path.exists()}

    result = run_cli(
        "journal", "--resolve", transaction, "--restore", cwd=adopter
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    reference = "sha256:" + blob.name
    assert result.stdout == ""
    assert result.stderr == (
        "ERROR: .gitignore: journal: the preimage of .gitignore in "
        ".validated-memory/preimages/ does not digest to "
        f"{reference}, the name it is filed under, so it is not the bytes this "
        "transaction parked. Nothing has been restored.\n"
    )
    assert {path: path.read_bytes() for path in watched if path.exists()} == before_bytes
    assert _run_topology(adapter, adopter) == before_projection


def test_topology_shadow_observes_successful_and_refused_targeted_repair(
    run_cli, tmp_path, monkeypatch
):
    adapter = _topology_adapter(tmp_path)
    repaired = tmp_path / "repaired"
    repaired.mkdir()
    transaction, _claim, payload, history = _history_repair_fixture(
        run_cli, repaired, monkeypatch
    )
    prepared_end = payload.find(b"\n") + 1
    history.write_bytes(payload[:prepared_end])
    before = _run_topology(adapter, repaired)

    result = run_cli("journal", "--repair", transaction.stem, cwd=repaired)

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == (
        f"journal: repaired .validated-memory/local.jsonl for transaction "
        f"{transaction.stem}\n"
    )
    assert result.stderr == ""
    assert history.read_bytes() == payload
    assert not transaction.exists()
    assert before["snapshot_present"] is True
    assert _run_topology(adapter, repaired)["snapshot_present"] is True

    refused = tmp_path / "repair-refused"
    refused.mkdir()
    transaction, _claim, _payload, history = _history_repair_fixture(
        run_cli, refused, monkeypatch
    )
    changed = json.loads(transaction.read_text(encoding="utf-8"))
    changed["history_append"]["records"][0]["path"] = "foreign/path"
    transaction.write_text(
        json.dumps(changed, sort_keys=True) + "\n", encoding="utf-8"
    )
    before_projection = _run_topology(adapter, refused)
    before_tree = _final_tree_snapshot(refused)
    result = run_cli("journal", "--repair", transaction.stem, cwd=refused)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stdout == ""
    assert result.stderr == (
        "ERROR: .validated-memory/local.jsonl: journal: transaction "
        f"{transaction.stem} does not prove this repair: the history claim is "
        "not bound to the selected WAL. No target or permanent-history change "
        "was left by this operation. Preserve all evidence and select a "
        "transaction carrying the required valid proof, or restore exact "
        "trusted history.\n"
    )
    assert history.exists() and transaction.exists()
    assert _final_tree_snapshot(refused) == before_tree
    assert _run_topology(adapter, refused) == before_projection


def test_topology_shadow_cannot_change_the_public_schema2_refusal(run_cli, tmp_path):
    adopter = tmp_path / "adopter"
    record = _schema2_record()
    _topology_history(adopter, _jsonl(record))
    before = _final_tree_snapshot(adopter)

    plain = run_cli("journal", cwd=adopter)
    checked = run_cli("journal", "--check", cwd=adopter)

    for result in (plain, checked):
        assert result.returncode == 1
        assert result.stdout == "journal: 0 record(s), 1 error(s)\n"
        assert result.stderr == (
            "ERROR: journal.jsonl:1: journal: record uses schema 2, newer than "
            "this plugin understands (1); upgrade the plugin\n"
        )
    assert _final_tree_snapshot(adopter) == before


def _assert_acquired_parser_uses_only_supplied_bytes(source):
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "parse_acquired_history"
    )
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
    parse_calls = [
        call
        for call in calls
        if isinstance(call.func, ast.Name)
        and call.func.id == "_parse_history_bytes"
    ]
    assert len(parse_calls) == 1
    assert all(
        isinstance(call.func, ast.Name)
        and call.func.id in {"_parse_history_bytes", "artifact_name"}
        for call in calls
    )
    call = parse_calls[0]
    assert isinstance(call.func, ast.Name)
    assert call.func.id == "_parse_history_bytes"
    assert ast.unparse(call.args[0]) == "raw.data"
    forbidden = {"open", "read", "read_bytes", "read_text", "journal_path"}
    assert not {
        node.id
        for node in ast.walk(function)
        if isinstance(node, ast.Name) and node.id in forbidden
    }
    assert not {
        node.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and node.attr in forbidden
    }


def test_acquired_history_parser_cannot_reread_a_name():
    path = REPO_ROOT / "validated_memory" / "journal" / "records.py"
    source = path.read_text(encoding="utf-8")
    _assert_acquired_parser_uses_only_supplied_bytes(source)

    mutant = source.replace(
        "return _parse_history_bytes(raw.data, durability, artifact_name(durability))",
        "return _parse_history_bytes(journal_path().read_bytes(), durability, artifact_name(durability))",
        1,
    )
    assert mutant != source
    with pytest.raises(AssertionError):
        _assert_acquired_parser_uses_only_supplied_bytes(mutant)


def test_history_rendezvous_has_one_reader_and_bounded_call_sites():
    sources = {
        path.relative_to(REPO_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (REPO_ROOT / "validated_memory").rglob("*.py")
    }
    readers = [
        relative
        for relative, source in sources.items()
        if "VALIDATED_MEMORY_TEST_RENDEZVOUS" in source
    ]
    assert readers == ["validated_memory/journal/fault.py"]
    calls = []
    for relative in (
        "validated_memory/journal/records.py",
        "validated_memory/journal/executor.py",
    ):
        tree = ast.parse(sources[relative])
        calls.extend(
            (relative, node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "rendezvous_at"
        )
    observed = {
        (relative, ast.literal_eval(call.args[0]), ast.unparse(call.args[1]))
        for relative, call in calls
    }
    assert observed == {
        (
            "validated_memory/journal/records.py",
            "after-first-history-read",
            "attempt",
        ),
        (
            "validated_memory/journal/records.py",
            "after-opening-reconfirmation",
            "1",
        ),
        (
            "validated_memory/journal/records.py",
            "before-append-reconfirmation",
            "1",
        ),
        (
            "validated_memory/journal/records.py",
            "after-append-reconfirmation",
            "1",
        ),
        (
            "validated_memory/journal/executor.py",
            "before-identity-transition",
            "1",
        ),
    }


def test_cross_history_reuse_precedes_same_artifact_multiplicity(
    run_cli, tmp_path
):
    """Global ID reuse replaces, rather than duplicates, the count anomaly."""
    adopter, repository, local = _seed_repository_and_local_histories(
        run_cli, tmp_path
    )
    repository_records = _records(repository)
    local_records = _records(local)
    repository_pair = _complete_pair(repository_records)
    local_pair = _complete_pair(local_records)
    shared = repository_pair[0]["transaction"]
    _rewrite(repository, repository_records + repository_pair)
    _rewrite(
        local,
        [
            {**entry, "transaction": shared}
            if entry in local_pair
            else entry
            for entry in local_records
        ],
    )

    result = run_cli("journal", "--check", cwd=adopter)

    message = (
        f"transaction {shared} is recorded across repo and local histories"
    )
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert result.stderr.count(message) == 1, result.stderr
    assert f"transaction {shared} is recorded 4 times" not in result.stderr
    assert "1 error(s)" in result.stdout, result.stdout


@pytest.mark.parametrize("field", ("preimage", "postimage"))
def test_journal_check_reports_exact_state_field_disagreement(
    run_cli, tmp_path, field
):
    """Each state field independently binds the two halves of one act."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    records = _records(journal)
    prepared, committed = _complete_pair(
        records, fields=("preimage", "postimage")
    )
    replacement = "sha256:" + ("7" if field == "preimage" else "8") * 64
    assert committed[field] != replacement
    forged = {**committed, field: replacement}
    ordinary_fields = set(prepared) | set(forged)
    assert {
        key
        for key in ordinary_fields
        if key not in {"at", "stage"} and prepared.get(key) != forged.get(key)
    } == {field}
    _rewrite(
        journal,
        [forged if entry is committed else entry for entry in records],
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert (
        f"records of transaction {prepared['transaction']} disagree on {field}"
        in result.stderr
    ), result.stderr
    assert "1 error(s)" in result.stdout, result.stdout


def test_journal_check_reports_a_committed_half_with_no_prepared_half(
    run_cli, tmp_path
):
    """A committed record without its prepared half is a gating pair error."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    records = _records(journal)
    orphaned = next(
        entry
        for entry in records
        if entry.get("transaction") and entry["stage"] == "committed"
    )
    _rewrite(
        journal,
        [
            entry
            for entry in records
            if not (
                entry.get("transaction") == orphaned["transaction"]
                and entry["stage"] == "prepared"
            )
        ],
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        f"records of transaction {orphaned['transaction']}: committed "
        "without a prepared half" in result.stderr
    ), result.stderr


def test_journal_check_reports_a_transaction_recorded_more_than_twice(
    run_cli, tmp_path
):
    """A transaction ID occurring more than twice is a gating pair error."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    records = _records(journal)
    doubled = next(
        entry["transaction"] for entry in records if entry.get("transaction")
    )
    _rewrite(
        journal,
        records + [entry for entry in records if entry.get("transaction") == doubled],
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        f"transaction {doubled} is recorded 4 times" in result.stderr
    ), result.stderr


def test_journal_check_reports_two_halves_that_disagree_on_purpose(
    run_cli, tmp_path
):
    """Two records sharing an ID must also agree on purpose."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    journal = tmp_path / "journal.jsonl"
    records = _records(journal)
    forged = next(
        entry
        for entry in records
        if entry.get("transaction") and entry["stage"] == "committed"
    )
    _rewrite(
        journal,
        [
            {**entry, "purpose": "forged"} if entry is forged else entry
            for entry in records
        ],
    )

    result = run_cli("journal", "--check", cwd=tmp_path)

    assert result.returncode == 1, result.stdout
    assert (
        f"records of transaction {forged['transaction']} disagree on purpose"
        in result.stderr
    ), result.stderr


def test_recovery_still_appends_exactly_one_pair_over_a_history_that_has_it(
    run_cli, tmp_path, monkeypatch
):
    """Recovery leaves an existing complete pair recorded exactly twice."""
    monkeypatch.setenv("VALIDATED_MEMORY_FAULT", "after-history")
    assert run_cli("init", cwd=tmp_path).returncode == 70
    monkeypatch.delenv("VALIDATED_MEMORY_FAULT")
    transaction = _transactions(tmp_path)[0]["transaction"]

    assert run_cli("init", cwd=tmp_path).returncode == 0

    checked = run_cli("journal", "--check", cwd=tmp_path)
    assert checked.returncode == 0, (checked.stdout, checked.stderr)
    written = [
        entry
        for entry in _records(tmp_path / "journal.jsonl")
        if entry.get("transaction") == transaction
    ]
    assert len(written) == 2, written


# --- a refusal that says nothing changed has changed nothing ------------------


@pytest.mark.parametrize(
    "transaction_id",
    ("../../escape", r"folder\escape")
    + (() if os.name == "nt" else ("C:escape",)),
)
def test_path_bearing_resolution_ids_never_reach_a_transaction_file(
    run_cli, tmp_path, transaction_id
):
    """POSIX and Windows separators cannot escape the transaction namespace."""
    genuine = _diverged(tmp_path)
    transactions = tmp_path / ".validated-memory" / "transactions"
    genuine_artifact = transactions / f"{genuine}.json"
    forged = json.loads(genuine_artifact.read_text(encoding="utf-8"))
    forged["transaction"] = transaction_id
    if transaction_id.startswith("../"):
        outside = tmp_path / "escape.json"
    elif os.name == "nt":
        outside = transactions / "folder" / "escape.json"
    else:
        outside = transactions / f"{transaction_id}.json"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text(json.dumps(forged, sort_keys=True) + "\n", encoding="utf-8")
    journal = tmp_path / "journal.jsonl"
    outside_before = outside.read_bytes()
    journal_before = journal.read_bytes()
    genuine_before = genuine_artifact.read_bytes()
    path_before = (tmp_path / ".gitignore").read_bytes()

    result = run_cli(
        "journal", "--resolve", transaction_id, "--abandon", cwd=tmp_path
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert f"there is no unresolved transaction {transaction_id}" in result.stderr
    assert outside.read_bytes() == outside_before
    assert journal.read_bytes() == journal_before
    assert genuine_artifact.read_bytes() == genuine_before
    assert (tmp_path / ".gitignore").read_bytes() == path_before


def test_an_absolute_resolution_id_does_not_materialize_a_virgin_tree(
    run_cli, tmp_path
):
    """An absolute external JSON is not a transaction and is never opened."""
    adopter = tmp_path / "virgin"
    adopter.mkdir()
    outside_stem = tmp_path / "outside"
    outside = Path(f"{outside_stem}.json")
    outside.write_bytes(b"external bytes that are not a transaction\n")
    before = outside.read_bytes()

    result = run_cli(
        "journal", "--resolve", str(outside_stem), "--accept", cwd=adopter
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert f"there is no unresolved transaction {outside_stem}" in result.stderr
    assert outside.read_bytes() == before
    assert not list(adopter.iterdir()), sorted(path.name for path in adopter.iterdir())


def test_a_safe_nonhex_filename_stem_remains_a_resolvable_transaction_id(
    run_cli, tmp_path
):
    """Target validation excludes paths, not historical arbitrary safe stems."""
    generated = _diverged(tmp_path)
    transaction_id = "release-1.alpha"
    directory = tmp_path / ".validated-memory" / "transactions"
    generated_artifact = directory / f"{generated}.json"
    entry = json.loads(generated_artifact.read_text(encoding="utf-8"))
    entry["transaction"] = transaction_id
    artifact = directory / f"{transaction_id}.json"
    artifact.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    generated_artifact.unlink()

    result = run_cli(
        "journal", "--resolve", transaction_id, "--accept", cwd=tmp_path
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert f"journal: resolved {transaction_id} (--accept)" in result.stdout
    assert not artifact.exists()


@pytest.mark.parametrize("resolution", ("accept", "abandon", "restore"))
def test_resolving_an_id_nothing_carries_leaves_a_virgin_tree_virgin(
    run_cli, tmp_path, resolution
):
    """Resolving an unknown ID leaves a virgin tree filesystem-empty.

    The exact refusal, absent journal and absent vault pin the resolver's
    non-materializing missing-transaction path."""
    result = run_cli(
        "journal", "--resolve", "deadbeefdeadbeef", f"--{resolution}", cwd=tmp_path
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert (
        "ERROR: .validated-memory/transactions/deadbeefdeadbeef.json: "
        "journal: there is no unresolved transaction deadbeefdeadbeef; "
        "'validated-memory journal --check' lists the ones there are. "
        "Nothing has been changed." in result.stderr
    ), result.stderr
    assert not (tmp_path / "journal.jsonl").exists(), "the journal was created"
    assert not (tmp_path / ".validated-memory").exists(), "the vault was created"
    assert not list(tmp_path.iterdir()), sorted(p.name for p in tmp_path.iterdir())


def test_a_transaction_without_adoption_history_does_not_adopt_the_tree(
    run_cli, tmp_path
):
    """Resolution refuses contradictory residue without opening a journal."""
    transaction_id = "abababababababab"
    directory = tmp_path / ".validated-memory" / "transactions"
    directory.mkdir(parents=True)
    entry = {
        "schema": 1,
        "at": "2026-09-01T00:00:00Z",
        "version": "1.6.0",
        "adoption": "1111111111111111",
        "run": "2222222222222222",
        "transaction": transaction_id,
        "intention": {
            "op": "replace",
            "purpose": "init",
            "path": "validated-memory.md",
            "durability": "repo",
        },
        "preimage": {"kind": "absent"},
        "postimage": {
            "kind": "file",
            "digest": "sha256:" + "1" * 64,
            "mode": 420,
        },
        "preimage_blob": None,
        "mode": None,
        "prior_bytes": None,
        "stage": "prepared",
    }
    artifact = directory / f"{transaction_id}.json"
    artifact.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    before = artifact.read_bytes()

    result = run_cli(
        "journal", "--resolve", transaction_id, "--abandon", cwd=tmp_path
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Traceback" not in result.stderr, result.stderr
    assert "no adoption history" in result.stderr, result.stderr
    assert not (tmp_path / "journal.jsonl").exists(), "resolution adopted the tree"
    assert artifact.read_bytes() == before


def test_resolving_an_id_nothing_carries_is_the_same_refusal_in_an_adopted_tree(
    run_cli, tmp_path
):
    """An adopted tree gets the unknown-ID refusal with journal text unchanged."""
    assert run_cli("init", cwd=tmp_path).returncode == 0
    before = (tmp_path / "journal.jsonl").read_text(encoding="utf-8")
    tree_before = _final_tree_snapshot(tmp_path)

    result = run_cli(
        "journal", "--resolve", "deadbeefdeadbeef", "--abandon", cwd=tmp_path
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert (
        "there is no unresolved transaction deadbeefdeadbeef" in result.stderr
    ), result.stderr
    assert (tmp_path / "journal.jsonl").read_text(encoding="utf-8") == before
    assert _final_tree_snapshot(tmp_path) == tree_before


def test_sqlite_connections_belong_only_to_consultation_store():
    owners = []
    leaks = []
    for relative, path in _package_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                owners.extend(relative for alias in node.names if alias.name == "sqlite3")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").startswith("sqlite3"):
                    owners.append(relative)
                elif any(alias.name == "sqlite3" for alias in node.names):
                    leaks.append(relative)
            if relative != "consultation/store.py":
                if isinstance(node, ast.Attribute) and node.attr == "sqlite3":
                    leaks.append(relative)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    receiver = node.func.value
                    if node.func.attr == "connect" and isinstance(receiver, ast.Name) and receiver.id == "sqlite3":
                        leaks.append(relative)
    assert owners == ["consultation/store.py"]
    assert not leaks, leaks
