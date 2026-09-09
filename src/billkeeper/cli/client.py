"""`billkeeper client`: the people and companies you invoice.

One client is one TOML file under `clients/`, named by a slug derived from the
name the first time it is seen. The slug is the client's identity everywhere
else — `new --client` takes it, every invoice records it — so it is fixed at
the moment the client is added and only the `name` changes afterwards when a
company renames itself.

`add` and `edit` reach the same file two ways on purpose. Adding a client is
often the first line of a script, so it takes options and prints the slug it
chose and nothing else. Changing one is nearly always a matter of retyping an
address, which is a job for a text editor rather than for six more options, so
`edit` opens the file and reads back whatever the user leaves there. Both end
in a commit, and `edit` refuses to commit anything the models will not read
back, so the data repo never records a client the tool cannot load.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Final

import typer
from rich.console import Console
from rich.table import Table

from billkeeper import gitrepo
from billkeeper.cli import get_repo
from billkeeper.editor import open_in_editor
from billkeeper.errors import AlreadyExistsError, NotFoundError, ValidationError
from billkeeper.models import Client
from billkeeper.storage import Repo

#: What the commits made here are recorded as.
ADD_COMMIT_MESSAGE: Final = "Add client {slug}"
EDIT_COMMIT_MESSAGE: Final = "Edit client {slug}"

#: What `list` says when there is nothing to list yet.
EMPTY_MESSAGE: Final = "No clients yet. Add one with 'billkeeper client add'."

#: What `edit` says when the editor was closed without touching the file.
NO_CHANGES_MESSAGE: Final = "No changes."

#: The fields `show` prints, in the order it prints them.
SHOWN_FIELDS: Final = ("slug", "name", "currency", "email", "address", "contact", "notes")

client_app = typer.Typer(
    no_args_is_help=True,
    help="Add and inspect the clients you invoice.",
)


@client_app.command()
def add(
    ctx: typer.Context,
    name: Annotated[
        str,
        typer.Option("--name", prompt="Client name", help="The name as it goes on invoices."),
    ],
    email: Annotated[str, typer.Option("--email", help="Where invoices are sent.")] = "",
    address: Annotated[str, typer.Option("--address", help="The billing address.")] = "",
    contact: Annotated[str, typer.Option("--contact", help="Who to address, if anyone.")] = "",
    currency: Annotated[
        str,
        typer.Option("--currency", help="ISO 4217 code. Defaults to the repo's own."),
    ] = "",
    notes: Annotated[str, typer.Option("--notes", help="Anything for your eyes only.")] = "",
    slug: Annotated[
        str,
        typer.Option("--slug", help="File the client under this slug instead of a derived one."),
    ] = "",
) -> None:
    """Add a client, and print the slug it was filed under."""
    # Multi-line values are passed through as they arrive: unlike `init`, which
    # has to read an address from a one-line prompt, a shell can hand an option
    # a real line break, and `client edit` is there for anything longer.
    repo, cfg = get_repo(ctx)

    wanted = slug.strip()
    if wanted and repo.client_exists(wanted):
        raise AlreadyExistsError(
            f"There is already a client filed under {wanted!r}. Choose another slug, "
            f"or change that one with 'billkeeper client edit {wanted}'."
        )

    client = Client(
        slug=wanted or repo.allocate_slug(name),
        name=name,
        email=_or_none(email),
        address=_or_none(address),
        contact=_or_none(contact),
        currency=_or_none(currency) or cfg.defaults.currency,
        notes=_or_none(notes),
    )
    path = repo.write_client(client)
    gitrepo.commit(repo.root, [path], ADD_COMMIT_MESSAGE.format(slug=client.slug))

    # The slug alone, so that `slug=$(billkeeper client add --name …)` works.
    typer.echo(client.slug)


@client_app.command("list")
def list_clients(ctx: typer.Context) -> None:
    """List every client."""
    repo, _ = get_repo(ctx)
    clients = sorted(repo.list_clients(), key=lambda client: client.slug)
    if not clients:
        typer.echo(EMPTY_MESSAGE)
        return

    table = Table()
    table.add_column("Slug")
    table.add_column("Name")
    table.add_column("Currency")
    table.add_column("Email")
    for client in clients:
        table.add_row(client.slug, client.name, client.currency, client.email or "")
    # Built here rather than at import: `CliRunner` and a redirected shell both
    # replace stdout, and a Console made earlier would still hold the old one.
    Console().print(table)


@client_app.command()
def show(
    ctx: typer.Context,
    slug: Annotated[str, typer.Argument(help="The client to show.")],
) -> None:
    """Show everything recorded about one client."""
    repo, _ = get_repo(ctx)
    for line in _described(load_client(repo, slug)):
        typer.echo(line)


@client_app.command()
def edit(
    ctx: typer.Context,
    slug: Annotated[str, typer.Argument(help="The client to edit.")],
) -> None:
    """Open a client's file in $EDITOR, then check and commit what comes back."""
    repo, _ = get_repo(ctx)
    load_client(repo, slug)
    path = repo.client_path(slug)

    before = path.read_bytes()
    open_in_editor(path)
    if path.read_bytes() == before:
        # Checked before the file is validated, so quitting the editor on a
        # file that was already broken says so rather than failing.
        typer.echo(NO_CHANGES_MESSAGE)
        return

    # Anything wrong with the file leaves this command here, with the user's
    # text still on disk and nothing committed: `Repo.read_client` raises a
    # `ValidationError` naming the file, which the CLI prints as one line.
    edited = repo.read_client(slug)
    if edited.slug != slug:
        raise ValidationError(
            f"{path}: the slug now reads {edited.slug!r}, but the file is named "
            f"{path.name}. A slug is what invoices are raised against, so it cannot "
            "be changed by editing; add a second client if you need another one."
        )

    gitrepo.commit(repo.root, [path], EDIT_COMMIT_MESSAGE.format(slug=slug))
    typer.echo(f"Updated client {slug}.")


def load_client(repo: Repo, slug: str) -> Client:
    """Return the client called `slug`, or say how to find out what there is.

    Public because `billkeeper new` looks a client up the same way and should
    fail the same way, down to the sentence telling the user where to look.
    """
    if not repo.client_exists(slug):
        raise NotFoundError(
            f"No client named {slug!r}. Run 'billkeeper client list' to see them all."
        )
    return repo.read_client(slug)


def _described(client: Client) -> Iterator[str]:
    """Yield `client` as one labelled line per field, longest label setting the width."""
    labels = {field: f"{field.capitalize()}:" for field in SHOWN_FIELDS}
    width = max(len(label) for label in labels.values())
    for field in SHOWN_FIELDS:
        value = getattr(client, field)
        # An address is several lines; they line up under the label rather than
        # turning one field into several that look like fields of their own.
        head, *rest = str(value or "").split("\n")
        yield f"{labels[field]:<{width}} {head}".rstrip()
        for line in rest:
            yield f"{'':<{width}} {line}".rstrip()


def _or_none(value: str) -> str | None:
    """Return `value`, or `None` for one that was left empty.

    An option not given and an option given as `''` mean the same thing, and
    absence is what the TOML file records: an empty `email = ""` in a file
    someone opens later reads as a fact about the client rather than a gap.
    """
    return value.strip() or None
