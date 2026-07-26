"""Release metadata has one authoritative version source."""

from pathlib import Path
import re

import filmot
from filmot._version import __version__
from filmot.cli import cli
from click.testing import CliRunner


ROOT = Path(__file__).resolve().parents[1]


def test_version_is_single_sourced_at_0_4_0():
    assert __version__ == "0.4.0"
    assert filmot.__version__ == __version__

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject
    assert 'version = {attr = "filmot._version.__version__"}' in pyproject
    assert not re.search(r"(?m)^version\s*=\s*['\"]\d", pyproject)


def test_cli_version_uses_the_package_version():
    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == "filmot, version {}".format(__version__)


def test_changelog_has_the_current_version_and_release_date():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert "## [0.4.0] - 2026-07-25" in changelog
    assert "Filmot API contract fixtures" in changelog
