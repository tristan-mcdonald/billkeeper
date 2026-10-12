"""`billkeeper list` and `billkeeper show`: reading the invoices back.

Neither command writes anything, which is most of what there is to say about
them. What is left is making a directory of TOML files legible: `list` as one
line per invoice, `show` as everything one invoice holds.

Two rules shape the output. Every amount is printed in its own invoice's
currency, and nothing here adds two invoices together — billkeeper never
converts between currencies, so a column of mixed-currency totals has no sum to
put at the bottom of it and `list` prints none. And an invoice is named by its
number once it has one and by its draft id until then, which is what the id
column holds and what `show` prints where the number goes.

The date a listing sorts and filters by is the day the invoice went out, or the
day it was begun for one that has not gone out. A voided draft never got an
issue date either, so it is filed by the day it was begun too.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Iterator, Sequence
from typing import Annotated, Final

import typer
from rich.console import Console
from rich.table import Table

from billkeeper.cli import get_repo
from billkeeper.config import RepoConfig
from billkeeper.errors import ValidationError
from billkeeper.models import Invoice, InvoiceStatus, StatusEvent
from billkeeper.money import Money, format_money
from billkeeper.render import KIND_LABELS
from billkeeper.storage import Repo, invoice_id

#: What `list` says when nothing matched. Not an error: a filter that finds
#: nothing has answered the question it was asked.
EMPTY_MESSAGE: Final = "No invoices match."

#: What `show` prints where the number goes on an invoice that has none yet.
DRAFT_LABEL: Final = "DRAFT"

#: What `list`'s columns are headed, in the order they appear.
LIST_COLUMNS: Final = ("Id", "Client", "Date", "Status", "Total")

#: What `show`'s line-item table is headed.
ITEM_COLUMNS: Final = ("Description", "Quantity", "Unit", "Unit price", "Total")

#: One more than the longest label `show` prints — "Currency:", "Draft id:",
#: and "Subtotal:" are all nine. Fixed rather than measured per invoice so the
#: values line up whichever of the labels an invoice has something to say for.
LABEL_WIDTH: Final = 10

#: How far the lines of a block are set in under the label above them.
INDENT: Final = "  "


def list_invoices(
    ctx: typer.Context,
    status: Annotated[
        list[str] | None,
        typer.Option(
            "--status",
            metavar="STATUS",
            help="Only invoices with this status. May be given more than once.",
        ),
    ] = None,
    client: Annotated[
        str,
        typer.Option("--client", metavar="SLUG", help="Only invoices raised for this client."),
    ] = "",
    year: Annotated[
        int | None,
        typer.Option(
            "--year",
            metavar="YYYY",
            help="Only invoices issued in this year, or drafts begun in it.",
        ),
    ] = None,
) -> None:
    """List invoices, each total in its own currency.

    Totals are never added up across the listing, because an invoice in one
    currency and an invoice in another have no sum.
    """
    repo, cfg = get_repo(ctx)
    wanted = parse_statuses(status)
    found = matching(repo.list_invoices(), statuses=wanted, client=client.strip(), year=year)
    if not found:
        typer.echo(EMPTY_MESSAGE)
        return
    # Built here rather than at import: `CliRunner` and a redirected shell both
    # replace stdout, and a Console made earlier would still hold the old one.
    Console().print(_listing(found, cfg.defaults.locale))


def show(
    ctx: typer.Context,
    ident: Annotated[
        str,
        typer.Argument(metavar="ID", help="The invoice or draft to show."),
    ],
) -> None:
    """Show everything recorded about one invoice."""
    repo, cfg = get_repo(ctx)
    invoice = repo.resolve(ident)
    locale = cfg.defaults.locale

    for line in _heading(invoice, cfg):
        typer.echo(line)
    Console().print(_items(invoice, locale))
    for line in _summary(repo, invoice, locale):
        typer.echo(line)


def listing_date(invoice: Invoice) -> dt.date:
    """Return the date `invoice` is listed, sorted, and filtered by.

    The day it went out, for anything that has been issued; the day it was
    begun, for anything that has not. One date rather than two columns, because
    what a listing answers is "when was this", and a draft's answer to that is
    the day it was started.
    """
    return invoice.issue_date or invoice.created


def parse_statuses(values: Sequence[str] | None) -> frozenset[InvoiceStatus]:
    """Return the statuses `values` names, or every status for none at all.

    A status that is not one of the four is a typo worth stopping for, and the
    answer names all four: there are few enough to list and guessing at `sent`
    or `unpaid` would show an invoice set the user did not ask about.
    """
    if not values:
        return frozenset(InvoiceStatus)
    chosen = set()
    for value in values:
        try:
            chosen.add(InvoiceStatus(value.strip().casefold()))
        except ValueError:
            raise ValidationError(
                f"{value!r} is not an invoice status. Choose from {_statuses_listed()}."
            ) from None
    return frozenset(chosen)


def matching(
    invoices: Iterable[Invoice],
    statuses: frozenset[InvoiceStatus],
    client: str = "",
    year: int | None = None,
) -> list[Invoice]:
    """Return the invoices every given filter accepts, in date then id order.

    The client is matched on the slug recorded on the invoice rather than
    against `clients/`, so invoices raised for a client whose file has since
    been deleted are still found by the slug they carry.
    """
    return sorted(
        (
            invoice
            for invoice in invoices
            if invoice.status in statuses
            and (not client or invoice.client.slug == client)
            and (year is None or listing_date(invoice).year == year)
        ),
        key=lambda invoice: (listing_date(invoice), invoice_id(invoice)),
    )


def one_line(text: str) -> str:
    """Return `text` as the single line it is printed on, however it was typed.

    Public because `void` collapses a reason the same way for its commit
    subject: a reason is free text and may arrive with line breaks in it, and
    neither a one-line summary nor a commit subject has room for them.
    """
    return " ".join(text.split())


def _listing(invoices: Sequence[Invoice], locale: str) -> Table:
    """Return `invoices` as the table `list` prints."""
    table = Table()
    for column in LIST_COLUMNS:
        # The total is the one column read by its last digit rather than its
        # first, so it is the one column set to the right.
        table.add_column(column, justify="right" if column == "Total" else "left")
    for invoice in invoices:
        table.add_row(
            invoice_id(invoice),
            # The slug, not the name: it is what `--client` takes and what the
            # user types everywhere else.
            invoice.client.slug,
            listing_date(invoice).isoformat(),
            invoice.status.value,
            format_money(invoice.total, locale),
        )
    return table


def _heading(invoice: Invoice, cfg: RepoConfig) -> Iterator[str]:
    """Yield who the invoice is from, who it is to, and what it is."""
    yield from _block("From:", [cfg.issuer.name, cfg.issuer.address, cfg.issuer.email])
    yield from _block(
        "To:",
        [
            invoice.client.name,
            invoice.client.contact,
            invoice.client.address,
            invoice.client.email,
        ],
    )
    yield _field("Number:", invoice.number or DRAFT_LABEL)
    if invoice.draft_id is not None:
        yield _field("Draft id:", invoice.draft_id)
    yield _field("Client:", invoice.client.slug)
    yield _field("Kind:", KIND_LABELS.get(invoice.kind, invoice.kind))
    yield _field("Status:", invoice.status.value)
    yield _field("Currency:", invoice.currency)
    yield _field("Created:", invoice.created.isoformat())
    # Absent on a draft, and a labelled blank where a date goes reads as a date
    # that has gone missing rather than one that has not happened yet.
    if invoice.issue_date is not None:
        yield _field("Issued:", invoice.issue_date.isoformat())
    if invoice.due_date is not None:
        yield _field("Due:", invoice.due_date.isoformat())
    yield _field("Terms:", f"{invoice.payment_terms_days} days")
    yield ""


def _items(invoice: Invoice, locale: str) -> Table:
    """Return `invoice`'s line items as the table `show` prints."""
    table = Table()
    for column in ITEM_COLUMNS:
        table.add_column(column, justify="left" if column in ("Description", "Unit") else "right")
    for item in invoice.items:
        table.add_row(
            item.description,
            str(item.quantity),
            item.unit or "",
            # Rounded to the currency's minor units, exactly as the PDF prints
            # it: the figures on screen and the figures on the document someone
            # is asked to pay are the same figures.
            format_money(Money(item.unit_price, invoice.currency), locale),
            format_money(invoice.line_total(item), locale),
        )
    return table


def _summary(repo: Repo, invoice: Invoice, locale: str) -> Iterator[str]:
    """Yield what the invoice comes to, and everything recorded under it."""
    yield ""
    yield _field("Subtotal:", format_money(invoice.subtotal, locale))
    yield _field("Total:", format_money(invoice.total, locale))
    yield ""
    yield from _block("Notes:", [invoice.notes])
    yield from _block("References:", [invoice.references])
    pdf = repo.pdf_path(invoice)
    # Shown only when it is really there: a path to a PDF that has not been
    # rendered is something to go looking for and not find.
    if pdf.is_file():
        yield _field("PDF:", str(pdf))
        yield ""
    yield from _block("History:", [_event(event) for event in invoice.status_history])


def _event(event: StatusEvent) -> str:
    """Return one status change as the line `show` prints for it."""
    line = f"{event.status.value} on {event.date:%Y-%m-%d}"
    return f"{line} — {one_line(event.reason)}" if event.reason else line


def _field(label: str, value: str) -> str:
    """Return one labelled line, its value set where every other value starts."""
    return f"{label:<{LABEL_WIDTH}}{value}"


def _block(label: str, values: Iterable[str | None]) -> Iterator[str]:
    """Yield `label` with `values` indented under it, or nothing if there are none.

    A value may be several lines — an address usually is — so they are split
    and indented one by one, and a label with nothing to say is left out
    altogether rather than printed over a gap.
    """
    body = [line for value in values if value for line in value.split("\n") if line.strip()]
    if not body:
        return
    yield label
    for line in body:
        yield f"{INDENT}{line}"
    yield ""


def _statuses_listed() -> str:
    return ", ".join(status.value for status in InvoiceStatus)
