"""What is owed and what has come in, added up one currency at a time.

Both reports answer a question about money, and billkeeper never converts
between currencies, so neither has a single answer: each has one answer per
currency. The functions here return exactly that — a mapping from currency code
to the totals in it — and nothing in this module ever adds two currencies
together. `Money` would refuse to if it were asked.

They are pure. They take the invoices and the date to measure lateness against,
and return numbers; reading the repo, asking the clock, and printing are the
command's business. That is what lets every rule below be tested on invoices
built in memory.

The definitions, which the command's help text repeats:

- **Outstanding** is every invoice with status `issued`: sent and not yet paid.
  Drafts have not been sent and voids are not owed, so neither counts. `--year`
  is the year it was issued. Of those, an invoice is **overdue** when its due
  date is before today. An issued credit note counts towards outstanding at its
  own negative total, so the figure is what is owed net of credit owed back,
  but it is never overdue: a client cannot be late paying one.
- **Revenue** is every invoice with status `paid`. `--year` is the year it was
  paid, read from the `paid` entry in its status history, because that is the
  year the money arrived — an invoice issued in December and paid in January
  is the next year's revenue. A paid credit note counts at its negative total,
  since it is money that went back out.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from billkeeper.models import Invoice, InvoiceStatus
from billkeeper.money import Money


@dataclass(frozen=True)
class CurrencyTotals:
    """How many invoices one currency's line of a report counts, and their sum.

    The overdue figures are a part of the count and total, not in addition to
    them, and stay at zero in a revenue report, where nothing can be late.
    """

    count: int
    total: Money
    overdue_count: int
    overdue_total: Money

    @classmethod
    def empty(cls, currency: str) -> CurrencyTotals:
        """Return the totals of no invoices at all in `currency`."""
        zero = Money.zero(currency)
        return cls(count=0, total=zero, overdue_count=0, overdue_total=zero)

    def counting(self, invoice: Invoice, overdue: bool = False) -> CurrencyTotals:
        """Return these totals with `invoice` added, and to the overdue part if `overdue`."""
        more = replace(self, count=self.count + 1, total=self.total + invoice.total)
        if not overdue:
            return more
        return replace(
            more,
            overdue_count=self.overdue_count + 1,
            overdue_total=self.overdue_total + invoice.total,
        )


def outstanding(
    invoices: Iterable[Invoice],
    today: dt.date,
    year: int | None = None,
    currency: str | None = None,
) -> dict[str, CurrencyTotals]:
    """Return what is issued and unpaid, per currency, with the overdue part of it.

    `year` keeps only invoices issued in that year, and `currency` only those
    in that currency, which is expected already normalised.
    """
    owed = (
        invoice
        for invoice in invoices
        if invoice.status == InvoiceStatus.ISSUED
        and (year is None or (invoice.issue_date is not None and invoice.issue_date.year == year))
    )
    return _totalled(owed, currency, lambda invoice: is_overdue(invoice, today))


def revenue(
    invoices: Iterable[Invoice],
    year: int | None = None,
    currency: str | None = None,
) -> dict[str, CurrencyTotals]:
    """Return what has been paid, per currency.

    `year` keeps only invoices paid in that year. A paid invoice whose history
    records no payment date belongs to no year, so a `year` leaves it out; with
    no `year` it is counted like any other.
    """
    paid = (
        invoice
        for invoice in invoices
        if invoice.status == InvoiceStatus.PAID
        and (year is None or ((on := payment_date(invoice)) is not None and on.year == year))
    )
    return _totalled(paid, currency, lambda invoice: False)


def is_overdue(invoice: Invoice, today: dt.date) -> bool:
    """Return whether `invoice` is owed and its due date has gone by.

    Due today is not late yet. A credit note is never overdue, because the
    money on it is owed to the client rather than by them.
    """
    return (
        invoice.status == InvoiceStatus.ISSUED
        and invoice.kind == "invoice"
        and invoice.due_date is not None
        and invoice.due_date < today
    )


def payment_date(invoice: Invoice) -> dt.date | None:
    """Return the day `invoice` was recorded as paid, or `None` if it never was.

    Read from the end of the history, where the latest answer is: an invoice
    can be paid only once, but a file edited by hand may say so twice.
    """
    for event in reversed(invoice.status_history):
        if event.status == InvoiceStatus.PAID:
            return event.date
    return None


def _totalled(
    invoices: Iterable[Invoice],
    currency: str | None,
    overdue: Callable[[Invoice], bool],
) -> dict[str, CurrencyTotals]:
    """Return `invoices` added up per currency, keyed and ordered by currency code."""
    totals: dict[str, CurrencyTotals] = {}
    for invoice in invoices:
        if currency is not None and invoice.currency != currency:
            continue
        running = totals.get(invoice.currency) or CurrencyTotals.empty(invoice.currency)
        totals[invoice.currency] = running.counting(invoice, overdue=overdue(invoice))
    return dict(sorted(totals.items()))
