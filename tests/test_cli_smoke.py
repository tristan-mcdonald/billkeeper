"""Smoke tests for the billkeeper command-line interface."""

import re

from typer.testing import CliRunner

from billkeeper.cli import app

runner = CliRunner()

VERSION_RE = re.compile(r"\d+\.\d+\.\d+")

# Typer's help is drawn by Rich, which colours it whenever it thinks it is
# talking to a terminal — in CI as well as in a real one — and colours the two
# dashes of an option separately from its name. So the text is read back with
# the escapes taken out, or `--repo` would not be there to find.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """Return `text` with any colour Rich put in it removed."""
    return ANSI_RE.sub("", text)


def test_version_option() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert VERSION_RE.search(plain(result.output))


def test_no_arguments_shows_help() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code != 0
    assert "Usage" in plain(result.output)


def test_help_lists_the_global_repo_option_and_init() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--repo" in plain(result.output)
    assert "init" in plain(result.output)
