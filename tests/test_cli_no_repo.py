"""What every command that is not `init` does when there is no data repo.

The answer has to be the same everywhere and it has to be short: one line
naming `billkeeper init`, and a non-zero exit. The commands that will need a
repo do not exist yet, so the tests stand a probe command in the same shell
they will be built in, which is the part being tested anyway.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from billkeeper import cli
from billkeeper.cli import BillkeeperGroup, callback, get_repo
from billkeeper.config import REPO_ENV_VAR, XDG_CONFIG_HOME_ENV
from billkeeper.errors import RepoNotFoundError, ValidationError

runner = CliRunner()

MINIMAL_CONFIG = """\
[issuer]
name = "Example Consulting"

[defaults]
currency = "EUR"
"""


@pytest.fixture(autouse=True)
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give the test a home with no data repo and no user config in it."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv(XDG_CONFIG_HOME_ENV, str(home / ".config"))
    monkeypatch.delenv(REPO_ENV_VAR, raising=False)
    return home.resolve()


def probe_app() -> typer.Typer:
    """Return a CLI shaped exactly like billkeeper's, with two things to run.

    `where` is any command that needs a data repo, and `boom` is any command
    that fails for a reason of its own.
    """
    app = typer.Typer(cls=BillkeeperGroup)
    app.callback()(callback)

    @app.command()
    def where(ctx: typer.Context) -> None:
        repo, cfg = get_repo(ctx)
        typer.echo(f"{repo.root} {cfg.issuer.name}")

    @app.command()
    def boom() -> None:
        raise ValidationError("That will not do.")

    return app


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "config.toml").write_text(MINIMAL_CONFIG, encoding="utf-8")
    return root


def only_line(text: str) -> str:
    """Return the one line `text` holds, failing if it holds any other number."""
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected one line, got {lines}"
    return lines[0]


def test_a_command_with_no_repo_says_to_run_init() -> None:
    result = runner.invoke(probe_app(), ["where"])

    assert result.exit_code == 1
    assert "billkeeper init" in only_line(result.stderr)
    assert result.stdout == ""


def test_the_line_is_all_the_user_sees() -> None:
    result = runner.invoke(probe_app(), ["where"])

    assert "Traceback" not in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_a_repo_option_pointing_nowhere_names_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "not-a-repo"

    result = runner.invoke(probe_app(), ["--repo", str(missing), "where"])

    assert result.exit_code == 1
    line = only_line(result.stderr)
    assert str(missing) in line
    assert "billkeeper init" in line


def test_any_other_billkeeper_error_is_one_line_too(tmp_path: Path) -> None:
    result = runner.invoke(probe_app(), ["boom"])

    assert result.exit_code == 1
    assert only_line(result.stderr) == "That will not do."


def test_a_command_that_finds_a_repo_gets_it_and_its_config(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "data")

    result = runner.invoke(probe_app(), ["--repo", str(root), "where"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == f"{root} Example Consulting"


def test_the_environment_variable_is_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_repo(tmp_path / "data")
    monkeypatch.setenv(REPO_ENV_VAR, str(root))

    result = runner.invoke(probe_app(), ["where"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip().startswith(str(root))


def test_main_reports_an_error_raised_before_a_command_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main` is the last net, for the errors dispatch never sees."""

    def raise_error() -> None:
        raise RepoNotFoundError()

    monkeypatch.setattr(cli, "app", raise_error)

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    assert exit_info.value.code == 1
    assert "billkeeper init" in only_line(capsys.readouterr().err)
