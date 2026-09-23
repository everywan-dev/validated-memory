"""End-to-end tests for the validated-memory CLI surface.

The only testing seam is the CLI invoked as a subprocess over a fixture
adopter tree. Tests assert on exit codes and output; they never import the
package's internals.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_REFERENCE = REPO_ROOT / "docs" / "reference" / "cli.md"
INTERNAL_MODULE_ERROR = (
    "validated-memory: error: validated_memory.cli is internal; "
    "run python3 -P -m validated_memory instead\n"
)

# The set moved deliberately: `journal` was added later, the read side of
# the append-only record init already writes to.
SUBCOMMANDS = [
    "agent",
    "consultation",
    "init",
    "lint",
    "validate",
    "derive",
    "probe",
    "recall",
    "render",
    "status",
    "journal",
]


@pytest.mark.parametrize("argv", [("--help",), ("init",)])
def test_internal_cli_module_refuses_direct_execution(argv, adopter_dir):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-P", "-m", "validated_memory.cli", *argv],
        capture_output=True,
        text=True,
        cwd=adopter_dir,
        env=env,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == INTERNAL_MODULE_ERROR
    assert list(adopter_dir.iterdir()) == []


def test_internal_cli_module_import_is_silent(adopter_dir):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-P", "-c", "import validated_memory.cli"],
        capture_output=True,
        text=True,
        cwd=adopter_dir,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_cli_reference_names_only_the_supported_package_entrypoint():
    text = " ".join(CLI_REFERENCE.read_text(encoding="utf-8").split())
    assert (
        "`validated_memory.cli` is an internal implementation module, not a "
        "second CLI. Executing it directly is a usage error; use only "
        "`python3 -P -m validated_memory` as shown above." in text
    )
    assert "python3 -P -m validated_memory.cli" not in text


def test_global_help_lists_every_subcommand(adopter_dir, run_cli):
    result = run_cli("--help", cwd=adopter_dir)
    assert result.returncode == 0
    assert "validated-memory" in result.stdout
    for name in SUBCOMMANDS:
        assert name in result.stdout


@pytest.mark.parametrize("name", SUBCOMMANDS)
def test_subcommand_help_exits_clean(name, adopter_dir, run_cli):
    result = run_cli(name, "--help", cwd=adopter_dir)
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()


def test_unknown_subcommand_fails_as_usage_error(adopter_dir, run_cli):
    result = run_cli("frobnicate", cwd=adopter_dir)
    assert result.returncode == 2


def test_no_arguments_fails_with_usage(adopter_dir, run_cli):
    result = run_cli(cwd=adopter_dir)
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()
