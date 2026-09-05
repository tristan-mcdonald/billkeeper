"""Command-line interface for billkeeper."""

from typing import Annotated

import typer

from billkeeper import __version__

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Create, render, and track invoices.",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def callback(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Create, render, and track invoices."""


@app.command()
def hello() -> None:
    """Print the installed billkeeper version."""
    typer.echo(f"billkeeper {__version__}")


def main() -> None:
    """Console script entry point."""
    app()
