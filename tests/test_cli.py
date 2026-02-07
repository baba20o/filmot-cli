"""Tests for the filmot CLI commands (click integration tests)."""

import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from filmot.cli import cli  # The click.Group, not the main() wrapper


@pytest.fixture
def runner():
    return CliRunner()


class TestCLIEntryPoint:
    """Basic smoke tests for the CLI."""

    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Filmot" in result.output or "filmot" in result.output.lower()

    def test_version(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.3.0" in result.output

    def test_search_help(self, runner):
        result = runner.invoke(cli, ["search", "--help"])
        assert result.exit_code == 0
        assert "--bulk-download" in result.output
        assert "--fallback" in result.output

    def test_transcript_help(self, runner):
        result = runner.invoke(cli, ["transcript", "--help"])
        assert result.exit_code == 0
        assert "--fallback" in result.output


class TestMainModule:
    """Test python -m filmot entry point."""

    def test_module_importable(self):
        """The __main__ module can be imported without executing main()."""
        from filmot import __main__
        assert hasattr(__main__, 'main')
