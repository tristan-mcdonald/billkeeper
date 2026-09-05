"""Smoke tests for the billkeeper command-line interface."""

import re

from typer.testing import CliRunner

from billkeeper.cli import app

runner = CliRunner()

VERSION_RE = re.compile(r"\d+\.\d+\.\d+")


def test_hello_prints_version() -> None:
    result = runner.invoke(app, ["hello"])
    assert result.exit_code == 0
    assert "billkeeper" in result.output
    assert VERSION_RE.search(result.output)


def test_version_option() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert VERSION_RE.search(result.output)


def test_no_arguments_shows_help() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code != 0
    assert "Usage" in result.output
