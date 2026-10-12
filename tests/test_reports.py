"""Tests for `billkeeper.reports`, on invoices built in memory.

The reports are pure functions, so these need no repo, no git, and no clock:
each test builds the handful of invoices its rule is about and reads back the
totals. What most of them check is which invoices a report counts — a draft, a
void, an invoice due today, one issued in one year and paid in the next — and
that the answer always comes back one currency at a time.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

from billkeeper.models import ClientSnapshot, Invoice, InvoiceStatus, LineItem, StatusEvent
from billkeeper.money import Money
from billkeeper.reports import CurrencyTotals, is_overdue, outstanding, payment_date, revenue

TODAY = dt.date(2026, 3, 1)
YESTERDAY = TODAY - dt.timedelta(days=1)
TOMORROW = TODAY + dt.timedelta(days=1)


def invoice(
    currency: str,
    amount: str,
    status: InvoiceStatus = InvoiceStatus.ISSUED,
    issued: dt.date = dt.date(2026, 1, 15),
    due: dt.date | None = None,
    paid: dt.date | None = None,
    kind: Literal["invoice", "credit_note"] = "invoice",
) -> Invoice:
    """Return an invoice billing `amount` on one line, moved on to `status`.

    Due a month after it was issued unless `due` says otherwise, and paid on
    `paid` when its status is `paid`. A draft has no issue or due date, as a
    real one would not.
    """
    built = Invoice(
        kind=kind,
        draft_id="client-20260101",
        client=ClientSnapshot(slug="client", name="Client Ltd"),
        currency=currency,
        created=dt.date(2025, 12, 1),
        items=[LineItem(description="Work", quantity=Decimal(1), unit_price=Decimal(amount))],
    )
    if status == InvoiceStatus.DRAFT:
        return built
    if status == InvoiceStatus.VOID:
        built.transition(InvoiceStatus.VOID, on=issued, reason="Withdrawn")
        return built
    built.draft_id = None
    built.number = f"INV-{issued.year}-0001"
    built.issue_date = issued
    built.due_date = due or issued + dt.timedelta(days=30)
    built.transition(InvoiceStatus.ISSUED, on=issued)
    if status == InvoiceStatus.PAID:
        built.transition(InvoiceStatus.PAID, on=paid or issued + dt.timedelta(days=10))
    return built


def totals(
    currency: str, count: int, total: str, overdue_count: int = 0, overdue_total: str = "0"
) -> CurrencyTotals:
    """Return the `CurrencyTotals` a test expects, written out in strings."""
    return CurrencyTotals(
        count=count,
        total=Money(total, currency),
        overdue_count=overdue_count,
        overdue_total=Money(overdue_total, currency),
    )


#: A set across three currencies and every status, with no invoice overdue.
MIXED = (
    invoice("GBP", "100.00"),
    invoice("GBP", "250.50"),
    invoice("GBP", "999.00", InvoiceStatus.DRAFT),
    invoice("GBP", "40.00", InvoiceStatus.PAID),
    invoice("USD", "1200.00", InvoiceStatus.PAID),
    invoice("USD", "300.00", InvoiceStatus.PAID),
    invoice("USD", "75.00"),
    invoice("USD", "500.00", InvoiceStatus.VOID),
    invoice("JPY", "120000"),
    invoice("JPY", "8000.4"),
    invoice("JPY", "50000", InvoiceStatus.PAID),
)


class TestOutstanding:
    def test_reports_each_currency_separately(self) -> None:
        assert outstanding(MIXED, today=dt.date(2026, 1, 20)) == {
            "GBP": totals("GBP", 2, "350.50"),
            "JPY": totals("JPY", 2, "128000"),
            "USD": totals("USD", 1, "75.00"),
        }

    def test_keys_come_back_in_currency_code_order(self) -> None:
        assert list(outstanding(MIXED, today=TODAY)) == ["GBP", "JPY", "USD"]

    def test_every_total_is_in_the_currency_it_is_filed_under(self) -> None:
        for code, found in outstanding(MIXED, today=TODAY).items():
            assert found.total.currency == code
            assert found.overdue_total.currency == code

    def test_yen_are_totalled_in_whole_units(self) -> None:
        total = outstanding(MIXED, today=TODAY)["JPY"].total.amount

        assert total == Decimal(128000)
        assert total.as_tuple().exponent == 0

    def test_drafts_voids_and_paid_invoices_are_not_outstanding(self) -> None:
        excluded = [
            invoice("GBP", "1.00", InvoiceStatus.DRAFT),
            invoice("GBP", "2.00", InvoiceStatus.VOID),
            invoice("GBP", "3.00", InvoiceStatus.PAID),
        ]

        assert outstanding(excluded, today=TODAY) == {}

    def test_an_invoice_due_yesterday_is_overdue(self) -> None:
        late = invoice("GBP", "10.00", due=YESTERDAY)

        assert is_overdue(late, TODAY)
        assert outstanding([late], today=TODAY) == {"GBP": totals("GBP", 1, "10.00", 1, "10.00")}

    def test_an_invoice_due_today_or_tomorrow_is_not_overdue_yet(self) -> None:
        on_time = [invoice("GBP", "10.00", due=TODAY), invoice("GBP", "20.00", due=TOMORROW)]

        assert not any(is_overdue(each, TODAY) for each in on_time)
        assert outstanding(on_time, today=TODAY) == {"GBP": totals("GBP", 2, "30.00")}

    def test_overdue_is_a_part_of_each_currency_not_a_total_of_its_own(self) -> None:
        found = outstanding(
            [
                invoice("GBP", "10.00", due=YESTERDAY),
                invoice("GBP", "20.00", due=TOMORROW),
                invoice("USD", "5.00", due=YESTERDAY),
            ],
            today=TODAY,
        )

        assert found == {
            "GBP": totals("GBP", 2, "30.00", 1, "10.00"),
            "USD": totals("USD", 1, "5.00", 1, "5.00"),
        }

    def test_a_paid_invoice_past_its_due_date_is_not_overdue(self) -> None:
        assert not is_overdue(invoice("GBP", "10.00", InvoiceStatus.PAID, due=YESTERDAY), TODAY)

    def test_year_is_the_issue_year(self) -> None:
        """Both were begun in 2025; only the issue date tells them apart."""
        december = invoice("GBP", "10.00", issued=dt.date(2025, 12, 30))
        january = invoice("GBP", "20.00", issued=dt.date(2026, 1, 2))

        assert outstanding([december, january], today=TODAY, year=2025) == {
            "GBP": totals("GBP", 1, "10.00", 1, "10.00")
        }
        assert outstanding([december, january], today=TODAY, year=2026) == {
            "GBP": totals("GBP", 1, "20.00", 1, "20.00")
        }

    def test_currency_keeps_only_that_currency(self) -> None:
        assert outstanding(MIXED, today=TODAY, currency="USD") == {
            "USD": totals("USD", 1, "75.00", 1, "75.00")
        }

    def test_a_currency_with_nothing_in_it_is_an_empty_report(self) -> None:
        assert outstanding(MIXED, today=TODAY, currency="EUR") == {}

    def test_nothing_at_all_is_an_empty_report(self) -> None:
        assert outstanding([], today=TODAY) == {}

    def test_a_credit_note_nets_off_but_is_never_overdue(self) -> None:
        found = outstanding(
            [
                invoice("GBP", "100.00", due=YESTERDAY),
                invoice("GBP", "-30.00", kind="credit_note", due=YESTERDAY),
            ],
            today=TODAY,
        )

        assert found == {"GBP": totals("GBP", 2, "70.00", 1, "100.00")}


class TestRevenue:
    def test_reports_each_currency_separately(self) -> None:
        assert revenue(MIXED) == {
            "GBP": totals("GBP", 1, "40.00"),
            "JPY": totals("JPY", 1, "50000"),
            "USD": totals("USD", 2, "1500.00"),
        }

    def test_drafts_voids_and_unpaid_invoices_are_not_revenue(self) -> None:
        excluded = [
            invoice("GBP", "1.00", InvoiceStatus.DRAFT),
            invoice("GBP", "2.00", InvoiceStatus.VOID),
            invoice("GBP", "3.00"),
        ]

        assert revenue(excluded) == {}

    def test_year_is_the_payment_year_not_the_issue_year(self) -> None:
        crossing = invoice(
            "GBP",
            "500.00",
            InvoiceStatus.PAID,
            issued=dt.date(2025, 12, 15),
            paid=dt.date(2026, 1, 10),
        )

        assert revenue([crossing], year=2025) == {}
        assert revenue([crossing], year=2026) == {"GBP": totals("GBP", 1, "500.00")}

    def test_nothing_is_ever_overdue(self) -> None:
        late = invoice("GBP", "10.00", InvoiceStatus.PAID, due=YESTERDAY)

        assert revenue([late]) == {"GBP": totals("GBP", 1, "10.00")}

    def test_currency_keeps_only_that_currency(self) -> None:
        assert revenue(MIXED, currency="JPY") == {"JPY": totals("JPY", 1, "50000")}

    def test_a_paid_credit_note_reduces_revenue(self) -> None:
        found = revenue(
            [
                invoice("GBP", "100.00", InvoiceStatus.PAID),
                invoice("GBP", "-30.00", InvoiceStatus.PAID, kind="credit_note"),
            ]
        )

        assert found == {"GBP": totals("GBP", 2, "70.00")}

    def test_a_payment_with_no_recorded_date_belongs_to_no_year(self) -> None:
        """A hand-edited file can say `paid` without saying when."""
        undated = invoice("GBP", "10.00")
        undated.status = InvoiceStatus.PAID

        assert revenue([undated]) == {"GBP": totals("GBP", 1, "10.00")}
        assert revenue([undated], year=2026) == {}


class TestPaymentDate:
    def test_is_the_date_of_the_paid_entry(self) -> None:
        paid = invoice("GBP", "1.00", InvoiceStatus.PAID, paid=dt.date(2026, 2, 3))

        assert payment_date(paid) == dt.date(2026, 2, 3)

    def test_is_none_for_an_invoice_never_paid(self) -> None:
        assert payment_date(invoice("GBP", "1.00")) is None

    def test_takes_the_latest_when_a_file_records_two(self) -> None:
        paid = invoice("GBP", "1.00", InvoiceStatus.PAID, paid=dt.date(2026, 2, 3))
        paid.status_history.append(StatusEvent(status=InvoiceStatus.PAID, date=dt.date(2026, 2, 9)))

        assert payment_date(paid) == dt.date(2026, 2, 9)
