"""`billkeeper report`: what is owed, and what has come in.

The adding up is `billkeeper.reports`; this is the part that reads the repo,
asks the clock, and prints. What it prints is one table per currency, headed
with the code, because the totals are per currency and nothing else — there is
no line at the bottom adding a pound total to a yen total, and when more than
one currency appears the footer says why not, so its absence reads as a rule
rather than an oversight.

The definitions of the two reports are written into each command's help, since
"outstanding" and "revenue" each have more than one reasonable meaning and the
user should not have to read the source to learn which one they got.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Final

import typer
from rich.console import Console
from rich.table import Table

from billkeeper import clock, reports
from billkeeper.cli import get_repo
from billkeeper.money import Money, format_money, normalize_currency
from billkeeper.reports import CurrencyTotals

#: What either report says when there is nothing to put in it. Not an error: a
#: report of nothing has answered the question it was asked.
NOTHING_OUTSTANDING: Final = "Nothing outstanding."
NO_REVENUE: Final = "No revenue recorded."

#: Printed under a report that covers more than one currency.
PER_CURRENCY_FOOTER: Final = "Totals are reported per currency and never combined."

#: What each table's columns are headed. The first column names the row.
COLUMNS: Final = ("", "Invoices", "Total")

#: What each row of a table is called.
OUTSTANDING_ROW: Final = "Outstanding"
OVERDUE_ROW: Final = "Overdue"
PAID_ROW: Final = "Paid"

report_app = typer.Typer(
    no_args_is_help=True,
    help="Add up what is owed and what has been paid, one currency at a time.",
)

CurrencyOption = Annotated[
    str,
    typer.Option(
        "--currency",
        metavar="CODE",
        help="Only this currency, as an ISO 4217 code such as GBP.",
        show_default=False,
    ),
]


@report_app.command()
def outstanding(
    ctx: typer.Context,
    year: Annotated[
        int | None,
        typer.Option(
            "--year", metavar="YYYY", help="Only invoices issued in this year.", show_default=False
        ),
    ] = None,
    currency: CurrencyOption = "",
) -> None:
    """Show what has been issued and not yet paid, per currency.

    Counts every invoice with status 'issued'. Drafts have not been sent and
    voided invoices are not owed, so neither is counted. --year is the year an
    invoice was issued.

    Overdue is the part of that whose due date is before today. An issued
    credit note reduces the outstanding total but is never overdue.

    Totals are per currency and never combined.
    """
    repo, cfg = get_repo(ctx)
    found = reports.outstanding(
        repo.list_invoices(), today=clock.today(), year=year, currency=_currency(currency)
    )
    _print(
        found,
        cfg.defaults.locale,
        empty=NOTHING_OUTSTANDING,
        rows=lambda totals: [
            (OUTSTANDING_ROW, totals.count, totals.total),
            (OVERDUE_ROW, totals.overdue_count, totals.overdue_total),
        ],
    )


@report_app.command()
def revenue(
    ctx: typer.Context,
    year: Annotated[
        int | None,
        typer.Option(
            "--year", metavar="YYYY", help="Only invoices paid in this year.", show_default=False
        ),
    ] = None,
    currency: CurrencyOption = "",
) -> None:
    """Show what has been paid, per currency.

    Counts every invoice with status 'paid'. --year is the year it was paid,
    taken from the date it was marked paid rather than the date it was issued,
    because that is the year the money arrived. A paid credit note reduces the
    total.

    Totals are per currency and never combined.
    """
    repo, cfg = get_repo(ctx)
    found = reports.revenue(repo.list_invoices(), year=year, currency=_currency(currency))
    _print(
        found,
        cfg.defaults.locale,
        empty=NO_REVENUE,
        rows=lambda totals: [(PAID_ROW, totals.count, totals.total)],
    )


def _currency(value: str) -> str | None:
    """Return the currency `--currency` names, or `None` when it names none."""
    return normalize_currency(value) if value.strip() else None


def _print(
    found: dict[str, CurrencyTotals],
    locale: str,
    empty: str,
    rows: Callable[[CurrencyTotals], list[tuple[str, int, Money]]],
) -> None:
    """Print one table per currency in `found`, or `empty` when there are none."""
    if not found:
        typer.echo(empty)
        return
    # Built here rather than at import: `CliRunner` and a redirected shell both
    # replace stdout, and a Console made earlier would still hold the old one.
    console = Console()
    for position, (code, totals) in enumerate(found.items()):
        if position:
            console.print()
        table = Table(title=code, title_justify="left")
        for column in COLUMNS:
            table.add_column(column, justify="left" if column == "" else "right")
        for label, count, total in rows(totals):
            table.add_row(label, str(count), format_money(total, locale))
        console.print(table)
    if len(found) > 1:
        typer.echo(PER_CURRENCY_FOOTER)
