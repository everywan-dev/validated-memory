"""End-to-end tests for the validated-memory CLI.

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

SUBCOMMANDS = ["init", "lint", "validate", "derive", "probe"]


def run_cli(*args, cwd):
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "validated_memory", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )


@pytest.fixture
def adopter_dir(tmp_path):
    """Minimal adopter fixture: an empty project directory."""
    return tmp_path


def test_global_help_lists_every_subcommand(adopter_dir):
    result = run_cli("--help", cwd=adopter_dir)
    assert result.returncode == 0
    assert "validated-memory" in result.stdout
    for name in SUBCOMMANDS:
        assert name in result.stdout


@pytest.mark.parametrize("name", SUBCOMMANDS)
def test_subcommand_help_exits_clean(name, adopter_dir):
    result = run_cli(name, "--help", cwd=adopter_dir)
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()


@pytest.mark.parametrize("name", SUBCOMMANDS)
def test_unimplemented_subcommand_gates_with_explicit_error(name, adopter_dir):
    result = run_cli(name, cwd=adopter_dir)
    assert result.returncode == 1
    assert "ERROR" in result.stderr
    assert "not implemented" in result.stderr
    assert name in result.stderr


def test_unknown_subcommand_fails(adopter_dir):
    result = run_cli("frobnicate", cwd=adopter_dir)
    assert result.returncode != 0


def test_no_arguments_fails_with_usage(adopter_dir):
    result = run_cli(cwd=adopter_dir)
    assert result.returncode != 0
    assert "usage" in result.stderr.lower()
