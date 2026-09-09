"""The command-line shell every billkeeper command hangs off.

Two things live here rather than in the commands themselves. Finding the data
repo is one of them: `get_repo` is the single place `--repo`, `$BILLKEEPER_REPO`,
the user config and `~/billkeeper` are consulted, so every command agrees about
where the invoices are and says the same thing when there are none yet.

Reporting failures is the other. Everything billkeeper raises on purpose is a
`BillkeeperError` carrying a sentence written for the person at the terminal, so
dispatch is wrapped to print that one line to stderr and exit 1. A traceback is
a bug report, and mistyping a client name is not a bug; anything that is not a
`BillkeeperError` keeps its traceback, because that one really is for us.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from typer.core import TyperGroup

from billkeeper import __version__, config
from billkeeper.config import RepoConfig
from billkeeper.errors import BillkeeperError
from billkeeper.storage import Repo


class BillkeeperGroup(TyperGroup):
    """Typer's command group, with billkeeper's own errors reported as one line.

    Wrapping dispatch catches every command with one piece of code, including
    the ones written later, where a decorator on each command would be a thing
    to remember. `ctx` is typed loosely because Typer bundles its own copy of
    Click and the context class the overridden method names is private to it.
    """

    def invoke(self, ctx: Any) -> Any:
        try:
            return super().invoke(ctx)
        except BillkeeperError as error:
            _report(error)
            raise typer.Exit(code=1) from None


app = typer.Typer(
    cls=BillkeeperGroup,
    no_args_is_help=True,
    add_completion=False,
    help="Create, render, and track invoices.",
)


@dataclass
class AppContext:
    """What the global options said, carried to the command about to run."""

    #: The data repo named with `--repo`, or `None` to go looking for one.
    repo: Path | None = None


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def callback(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
    repo: Annotated[
        Path | None,
        typer.Option(
            "--repo",
            metavar="PATH",
            help="Use the data repo at PATH instead of the configured one.",
        ),
    ] = None,
) -> None:
    """Create, render, and track invoices."""
    ctx.obj = AppContext(repo=repo)


def get_repo(ctx: typer.Context) -> tuple[Repo, RepoConfig]:
    """Return the data repo to work in, and what it says about itself.

    Every command but `init` starts here. When there is no repo to be found the
    `RepoNotFoundError` this raises is what tells the user to run `init`, so no
    command has to check for one itself.
    """
    root = config.resolve_repo(ctx.ensure_object(AppContext).repo)
    return Repo(root), config.load_repo_config(root)


def _report(error: BillkeeperError) -> None:
    """Print `error` as the single line it was written to be."""
    # No `Error:` prefix: these messages are whole sentences addressed to the
    # user, and reading one is not helped by being told it is an error twice.
    typer.secho(error.message, err=True, fg=typer.colors.RED)


def main() -> None:
    """Console script entry point."""
    try:
        app()
    except BillkeeperError as error:
        # Dispatch handles the usual case. This catches the errors raised
        # before a command runs, while the arguments are still being read.
        _report(error)
        raise SystemExit(1) from None


# Imported last, and inside the module rather than at the top of it: the
# commands import `AppContext` and `get_repo` from here, so they can only be
# registered once those exist.
from billkeeper.cli.client import client_app  # noqa: E402
from billkeeper.cli.init_cmd import init  # noqa: E402

app.command()(init)
app.add_typer(client_app, name="client")
