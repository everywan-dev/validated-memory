"""End-to-end tests for the validated-memory CLI surface.

The only testing seam is the CLI invoked as a subprocess over a fixture
adopter tree. Tests assert on exit codes and output; they never import the
package's internals.
"""

import pytest

SUBCOMMANDS = ["init", "lint", "validate", "derive", "probe"]

UNIMPLEMENTED = ["init"]


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


@pytest.mark.parametrize("name", UNIMPLEMENTED)
def test_unimplemented_subcommand_gates_with_explicit_error(name, adopter_dir, run_cli):
    result = run_cli(name, cwd=adopter_dir)
    assert result.returncode == 1
    assert "ERROR" in result.stderr
    assert "not implemented" in result.stderr
    assert name in result.stderr


def test_unknown_subcommand_fails_as_usage_error(adopter_dir, run_cli):
    result = run_cli("frobnicate", cwd=adopter_dir)
    assert result.returncode == 2


def test_no_arguments_fails_with_usage(adopter_dir, run_cli):
    result = run_cli(cwd=adopter_dir)
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()
