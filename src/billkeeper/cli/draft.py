"""`billkeeper new` and `billkeeper edit`: drafts, and only drafts.

A draft is where an invoice gets written. `new` lays down a complete, valid
file — the client's details already copied in, one example line to overtype —
and hands it to `$EDITOR`; `edit` opens that same file again later. Neither
command asks for line items at a prompt, because a set of them is a table, and
a table is something to edit rather than to answer six questions about.

Two rules hold both commands together. Whatever the editor leaves on disk is
read back and validated before anything is committed, and a file that will not
load is left exactly as the user left it with nothing committed — so a mistyped
line is something to go back and fix, never work thrown away. And an invoice
that has been issued is not reopened here at all: its number has already gone
to someone, so a correction is a credit note or a new invoice referencing it,
never a quiet rewrite of what was sent.

The client's details are copied into the draft rather than pointed at. An
invoice is a record of what was said at the time, so moving office next year
must not restate the address on an invoice that was settled last year.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Final

import typer

from billkeeper import clock, gitrepo
from billkeeper.cli import get_repo
from billkeeper.cli.client import load_client
from billkeeper.editor import open_in_editor
from billkeeper.errors import ImmutableInvoiceError, ValidationError
from billkeeper.models import ClientSnapshot, Invoice, InvoiceStatus, LineItem
from billkeeper.money import format_money
from billkeeper.storage import Repo, invoice_id

#: What the commits made here are recorded as.
CREATE_COMMIT_MESSAGE: Final = "Create draft {draft_id}"
EDIT_COMMIT_MESSAGE: Final = "Edit draft {draft_id}"

#: What either command says once it has committed, and what it comes to.
CREATED_MESSAGE: Final = "Created draft {draft_id}."
UPDATED_MESSAGE: Final = "Updated draft {draft_id}."
TOTAL_MESSAGE: Final = "Total: {total}"

#: What `edit` says when the editor was closed without touching the file.
NO_CHANGES_MESSAGE: Final = "No changes."

#: What `edit` says about an invoice that has left the drafting stage.
IMMUTABLE_MESSAGE: Final = (
    "{ident} is {status} and cannot be edited. "
    "Create a credit note or a new invoice referencing it."
)

#: The one line a new draft starts with, written to be overtyped.
EXAMPLE_DESCRIPTION: Final = "Describe the work"
EXAMPLE_UNIT: Final = "hour"


def new(
    ctx: typer.Context,
    client: Annotated[
        str,
        typer.Option("--client", metavar="SLUG", help="The client to invoice."),
    ],
    currency: Annotated[
        str,
        typer.Option(
            "--currency", metavar="CODE", help="ISO 4217 code. Defaults to the client's own."
        ),
    ] = "",
    date: Annotated[
        str,
        typer.Option("--date", metavar="YYYY-MM-DD", help="The day the draft is raised."),
    ] = "",
    notes: Annotated[str, typer.Option("--notes", help="A note to print on the invoice.")] = "",
    no_edit: Annotated[
        bool,
        typer.Option("--no-edit", help="Write the draft without opening an editor."),
    ] = False,
) -> None:
    """Start a draft invoice for a client, and open it in $EDITOR."""
    repo, cfg = get_repo(ctx)
    billed = load_client(repo, client)
    created = _date(date)
    draft_id = repo.allocate_draft_id(billed.slug, created)

    draft = Invoice(
        draft_id=draft_id,
        client=ClientSnapshot.of(billed),
        # The client's currency is inherited rather than looked up again at
        # issue time: the snapshot beside it is already fixed, and an invoice
        # that changed currency between drafting and sending would be a
        # different invoice.
        currency=currency.strip() or billed.currency,
        created=created,
        payment_terms_days=cfg.defaults.payment_terms_days,
        items=[_example_item()],
        notes=notes.strip() or None,
        status=InvoiceStatus.DRAFT,
    )
    path = repo.write_invoice(draft)

    if not no_edit:
        open_in_editor(path)
    # Read back even when the editor was skipped: it costs one file read and it
    # proves the draft on disk is one the tool can load again.
    edited = _reread(repo, path, draft_id)

    gitrepo.commit(repo.root, [path], CREATE_COMMIT_MESSAGE.format(draft_id=draft_id))
    typer.echo(CREATED_MESSAGE.format(draft_id=draft_id))
    typer.echo(TOTAL_MESSAGE.format(total=format_money(edited.total, cfg.defaults.locale)))


def edit(
    ctx: typer.Context,
    ident: Annotated[
        str,
        typer.Argument(metavar="ID", help="The draft to edit, or enough of its id to name it."),
    ],
) -> None:
    """Open a draft in $EDITOR, then check and commit what comes back."""
    repo, cfg = get_repo(ctx)
    invoice = repo.resolve(ident)
    draft_id = _editable_draft_id(invoice)
    path = repo.invoice_path(invoice)

    before = path.read_bytes()
    open_in_editor(path)
    if path.read_bytes() == before:
        # Checked before the file is validated, so quitting the editor on a
        # draft that was already broken says so rather than failing.
        typer.echo(NO_CHANGES_MESSAGE)
        return

    edited = _reread(repo, path, draft_id)
    gitrepo.commit(repo.root, [path], EDIT_COMMIT_MESSAGE.format(draft_id=draft_id))
    typer.echo(UPDATED_MESSAGE.format(draft_id=draft_id))
    typer.echo(TOTAL_MESSAGE.format(total=format_money(edited.total, cfg.defaults.locale)))


def _editable_draft_id(invoice: Invoice) -> str:
    """Return the draft id of `invoice`, refusing anything past drafting."""
    if invoice.status != InvoiceStatus.DRAFT:
        raise ImmutableInvoiceError(
            IMMUTABLE_MESSAGE.format(
                ident=invoice_id(invoice) or "This invoice", status=invoice.status.value
            )
        )
    if invoice.draft_id is None:
        # Only reachable from a hand-written file: a numbered invoice still
        # marked draft is not filed under `drafts/`, so there is nothing here
        # for the editor to open.
        raise ValidationError(
            f"{invoice_id(invoice) or 'This invoice'} says it is a draft but carries a "
            "number, so it is not filed as one and there is no draft file to edit."
        )
    return invoice.draft_id


def _reread(repo: Repo, path: Path, draft_id: str) -> Invoice:
    """Return the draft now at `path`, refusing one that is no longer that draft.

    Anything the models will not read back ends the command here, with the
    user's text still on disk and nothing committed: `read_invoice_at` raises a
    `ValidationError` naming the file, which the CLI prints as its one line.
    """
    invoice = repo.read_invoice_at(path)
    if invoice.draft_id != draft_id:
        raise ValidationError(
            f"{path}: the draft id now reads {invoice.draft_id!r}, but the file is named "
            f"{path.name}. It is what the draft is looked up by, so it cannot be changed "
            "by editing; start another draft if you wanted a different one."
        )
    if invoice.status != InvoiceStatus.DRAFT:
        raise ValidationError(
            f"{path}: the status now reads {invoice.status.value!r}, but everything filed "
            "under drafts/ is a draft. Run 'billkeeper issue' to issue it, which numbers "
            "it and files it under its year."
        )
    return invoice


def _example_item() -> LineItem:
    """Return the line a new draft is written with, there to be overtyped.

    A draft is given a line rather than an empty `items = []` because a TOML
    array of tables is not something to write from memory, and because the file
    has to stay valid if the user saves it and comes back to it tomorrow.
    """
    return LineItem(
        description=EXAMPLE_DESCRIPTION,
        quantity=Decimal("1"),
        unit_price=Decimal("0.00"),
        unit=EXAMPLE_UNIT,
    )


def _date(value: str) -> dt.date:
    """Return the date `value` names, or today when it names none."""
    trimmed = value.strip()
    if not trimmed:
        return clock.today()
    try:
        return dt.date.fromisoformat(trimmed)
    except ValueError:
        raise ValidationError(
            f"{value!r} is not a date billkeeper can read. Write it as YYYY-MM-DD, "
            f"like {clock.today():%Y-%m-%d}."
        ) from None
