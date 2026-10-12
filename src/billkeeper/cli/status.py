"""`billkeeper mark-paid` and `billkeeper void`: the last two status changes.

These are the only commands that write to an invoice after it has been issued,
and they are allowed to because they change nothing about what it says. An
issued invoice is a record of what was sent; that the money arrived, or that the
document was withdrawn, is news about it rather than a revision of it. The
storage layer enforces exactly that — a write to an issued invoice is refused
unless `status` and `status_history` are the only things different — so neither
command has to be trusted to leave the rest alone.

A voided invoice keeps its number and its PDF. Giving the number back would put
a gap in the series, and the first thing anyone auditing a set of invoices asks
about is a gap; the document that was sent stays on disk beside the TOML saying
it was withdrawn and why. Which is why `--reason` is required rather than
helpful: "void" on its own, read back in two years, is not an answer.

`models.Invoice.transition` holds the rule about which change is legal, so what
is left here is to refuse the illegal one in words that name where the invoice
has got to, record the date, and commit.
"""

from __future__ import annotations

from typing import Annotated, Final

import typer

from billkeeper import clock, gitrepo
from billkeeper.cli import get_repo
from billkeeper.cli.query import one_line
from billkeeper.errors import ImmutableInvoiceError, ValidationError
from billkeeper.models import Invoice, InvoiceStatus
from billkeeper.money import format_money
from billkeeper.storage import invoice_id

#: What the commits made here are recorded as.
PAID_COMMIT_MESSAGE: Final = "Mark {ident} paid"
VOID_COMMIT_MESSAGE: Final = "Void {ident}: {reason}"

#: What either command says once it has committed.
PAID_MESSAGE: Final = "Marked {ident} paid on {date}. {total} received."
VOIDED_MESSAGE: Final = "Voided {ident} on {date}: {reason}"

#: What an invoice that is not waiting to be paid gets told, and why.
NOT_ISSUED_MESSAGE: Final = "{ident} is {status}, so it cannot be marked paid. {hint}"
PAID_HINTS: Final[dict[InvoiceStatus, str]] = {
    InvoiceStatus.DRAFT: "Issue it first with 'billkeeper issue {ident}'.",
    InvoiceStatus.PAID: "It was already marked paid on {date}.",
    InvoiceStatus.VOID: "A voided invoice is not owed, so there is nothing to pay.",
}

#: What an invoice that is past withdrawing gets told, and why.
NOT_VOIDABLE_MESSAGE: Final = "{ident} is {status}, so it cannot be voided. {hint}"
VOID_HINTS: Final[dict[InvoiceStatus, str]] = {
    InvoiceStatus.PAID: (
        "An invoice that has been paid is a record of money received; raise a credit "
        "note for the amount instead."
    ),
    InvoiceStatus.VOID: "It was already voided on {date}.",
}

#: What `--reason ''` is told. Required and empty are the same mistake.
EMPTY_REASON_MESSAGE: Final = (
    "Voiding an invoice needs a reason: give '--reason' something to record, such as "
    "--reason 'Raised against the wrong client'. It is kept in the invoice's history, "
    "which is the only place the answer will be in two years."
)


def mark_paid(
    ctx: typer.Context,
    ident: Annotated[
        str,
        typer.Argument(metavar="ID", help="The invoice that has been paid."),
    ],
    date: Annotated[
        str,
        typer.Option(
            "--date",
            metavar="YYYY-MM-DD",
            help="The day the money arrived. Defaults to today.",
        ),
    ] = "",
) -> None:
    """Record that an invoice has been paid."""
    repo, cfg = get_repo(ctx)
    invoice = repo.resolve(ident)
    _refuse_unless_issued(invoice)
    on = clock.parse_date(date)

    invoice.transition(InvoiceStatus.PAID, on=on)
    name = invoice_id(invoice) or ident
    path = repo.write_invoice(invoice)
    gitrepo.commit(repo.root, [path], PAID_COMMIT_MESSAGE.format(ident=name))

    typer.echo(
        PAID_MESSAGE.format(
            ident=name,
            date=on.isoformat(),
            total=format_money(invoice.total, cfg.defaults.locale),
        )
    )


def void(
    ctx: typer.Context,
    ident: Annotated[
        str,
        typer.Argument(metavar="ID", help="The invoice or draft to withdraw."),
    ],
    reason: Annotated[
        str,
        typer.Option(
            "--reason", metavar="TEXT", help="Why it is being voided. Kept in its history."
        ),
    ],
) -> None:
    """Withdraw an invoice or draft, recording why.

    An issued invoice keeps its number and its PDF: the number is used up, and
    the document was sent.
    """
    repo, _ = get_repo(ctx)
    invoice = repo.resolve(ident)
    _refuse_unless_voidable(invoice)
    explanation = reason.strip()
    if not explanation:
        raise ValidationError(EMPTY_REASON_MESSAGE)
    on = clock.today()

    invoice.transition(InvoiceStatus.VOID, on=on, reason=explanation)
    name = invoice_id(invoice) or ident
    # The invoice keeps the reason as it was typed; a commit subject and a line
    # of confirmation have no room for a paragraph, so both get it collapsed.
    summary = one_line(explanation)
    path = repo.write_invoice(invoice)
    gitrepo.commit(repo.root, [path], VOID_COMMIT_MESSAGE.format(ident=name, reason=summary))

    typer.echo(VOIDED_MESSAGE.format(ident=name, date=on.isoformat(), reason=summary))


def _refuse_unless_issued(invoice: Invoice) -> None:
    """Raise unless `invoice` is issued and waiting to be paid."""
    if invoice.status == InvoiceStatus.ISSUED:
        return
    raise ImmutableInvoiceError(
        NOT_ISSUED_MESSAGE.format(
            ident=_named(invoice),
            status=invoice.status.value,
            hint=_hint(PAID_HINTS, invoice),
        )
    )


def _refuse_unless_voidable(invoice: Invoice) -> None:
    """Raise unless `invoice` is a draft or an issued invoice."""
    if invoice.status in (InvoiceStatus.DRAFT, InvoiceStatus.ISSUED):
        return
    raise ImmutableInvoiceError(
        NOT_VOIDABLE_MESSAGE.format(
            ident=_named(invoice),
            status=invoice.status.value,
            hint=_hint(VOID_HINTS, invoice),
        )
    )


def _hint(hints: dict[InvoiceStatus, str], invoice: Invoice) -> str:
    """Return what to say next about an invoice that has got where it has.

    The status alone answers why the command was refused; the hint answers what
    to do instead, which is a different question for each of the three ways of
    being refused.
    """
    return hints.get(invoice.status, "").format(
        ident=_named(invoice), date=_reached(invoice, invoice.status)
    )


def _named(invoice: Invoice) -> str:
    """Return what to call `invoice` in a sentence addressed to the user."""
    return invoice_id(invoice) or "This invoice"


def _reached(invoice: Invoice, status: InvoiceStatus) -> str:
    """Return the day `invoice` reached `status`, or say that it is not recorded.

    Read from the end of the history, which is where the latest answer is: an
    invoice can only reach `paid` or `void` once, but a file someone has edited
    by hand may say so twice.
    """
    for event in reversed(invoice.status_history):
        if event.status == status:
            return f"{event.date:%Y-%m-%d}"
    return "a day it does not record"
