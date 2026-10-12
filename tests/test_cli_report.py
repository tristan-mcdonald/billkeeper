"""Tests for `billkeeper report`, on a real repo and git.

The arithmetic is tested in `test_reports.py`; these check what reaches the
terminal. They run against `conftest.library` with four more invoices added in
two more currencies, so that the repo holds EUR, GBP, USD, and JPY:

| id            | currency | status | issued     | due        | paid       |    total |
| ------------- | -------- | ------ | ---------- | ---------- | ---------- | -------- |
| INV-2025-0001 | EUR      | issued | 2025-11-10 | 2025-12-10 |            |   100.00 |
| INV-2026-0001 | EUR      | issued | 2026-02-01 | 2026-03-03 |            |   200.00 |
| INV-2026-0002 | GBP      | paid   | 2026-01-15 | 2026-02-14 | 2026-02-20 |   500.00 |
| INV-2025-0002 | JPY      | paid   | 2025-12-15 | 2026-01-14 | 2026-01-10 |  120,000 |
| INV-2026-0004 | USD      | issued | 2026-01-29 | 2026-02-28 |            |   300.00 |
| INV-2026-0005 | USD      | issued | 2026-01-31 | 2026-03-02 |            |    45.50 |
| INV-2026-0006 | JPY      | issued | 2026-02-01 | 2026-03-03 |            |    8,000 |

plus the library's two drafts and one void, which neither report may count. Today
is 2026-03-01, so the two USD invoices are due one day either side of it, and
the JPY payment was issued in 2025 and paid in 2026.

Each table is read back into a mapping of currency to row to (count, total),
which is what most assertions are about. The rest are about the one rule the
command exists to keep: no line of output puts two currencies' money together.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from typer.testing import CliRunner, Result

from billkeeper import gitrepo
from billkeeper.cli import app
from billkeeper.cli.report import (
    NO_REVENUE,
    NOTHING_OUTSTANDING,
    OUTSTANDING_ROW,
    OVERDUE_ROW,
    PAID_ROW,
    PER_CURRENCY_FOOTER,
)
from billkeeper.gitrepo import git_available
from billkeeper.models import Invoice, InvoiceStatus
from billkeeper.storage import Repo

from conftest import add_client, commit_count, record, take_number, uncommitted, write_draft

pytestmark = pytest.mark.skipif(not git_available(), reason="git is not installed")

runner = CliRunner()

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

#: One body row of a table: its label, the count, and the formatted total.
ROW_RE = re.compile(r"^│\s*(\w+)\s*│\s*(\d+)\s*│\s*(.+?)\s*│$")

#: Every currency the ledger holds, and every mark a formatted amount in one of
#: them can carry. JP¥ is listed before ¥ and US$ before $ so that each amount
#: is matched by its whole mark rather than by the tail of it.
CODES = ("EUR", "GBP", "JPY", "USD")
MARKS_RE = re.compile(r"US\$|JP¥|€|£|¥|\$")

#: What a table holds: per currency, per row label, the count and the total.
Report = dict[str, dict[str, tuple[int, str]]]


@pytest.fixture
def ledger(library: Repo) -> Repo:
    """Return the shared library with a USD client and a JPY client added."""
    add_client("initech", "Initech Inc", "USD")
    add_client("yoyodyne", "Yoyodyne KK", "JPY")

    issue(library, "initech-20260129", "initech", "USD", "300.00", "INV-2026-0004", (2026, 1, 29))
    issue(library, "initech-20260131", "initech", "USD", "45.50", "INV-2026-0005", (2026, 1, 31))
    issue(library, "yoyodyne-20260201", "yoyodyne", "JPY", "8000", "INV-2026-0006", (2026, 2, 1))
    crossing = issue(
        library, "yoyodyne-20251215", "yoyodyne", "JPY", "120000", "INV-2025-0002", (2025, 12, 15)
    )
    record(library, crossing, InvoiceStatus.PAID, dt.date(2026, 1, 10))

    gitrepo.commit(library.root, [library.invoices_dir], "Add invoices in USD and JPY")
    assert uncommitted(library) == ""
    return library


def issue(
    repo: Repo,
    draft_id: str,
    client: str,
    currency: str,
    amount: str,
    number: str,
    on: tuple[int, int, int],
) -> Invoice:
    """Write a draft and issue it by hand on `on`, due thirty days later."""
    when = dt.date(*on)
    return take_number(
        repo, write_draft(repo, draft_id, client, currency, when, amount), number, when
    )


def billkeeper(*args: str) -> Result:
    """Run `billkeeper args…`, and check it succeeded."""
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result


def plain(text: str) -> str:
    """Return `text` with any colour Rich put in it removed."""
    return ANSI_RE.sub("", text)


def report(*args: str) -> Report:
    """Return what `billkeeper report args…` printed, read back table by table."""
    found: Report = {}
    current: dict[str, tuple[int, str]] = {}
    for line in plain(billkeeper("report", *args).stdout).splitlines():
        if line.strip() in CODES:
            current = found.setdefault(line.strip(), {})
        elif match := ROW_RE.match(line.strip()):
            label, count, total = match.groups()
            current[label] = (int(count), total)
    return found


def out(*args: str) -> str:
    """Return everything `billkeeper report args…` printed."""
    return plain(billkeeper("report", *args).stdout)


# ------------------------------------------------------------------ outstanding


class TestOutstanding:
    def test_reports_each_currency_in_its_own_table(self, ledger: Repo) -> None:
        assert report("outstanding") == {
            "EUR": {OUTSTANDING_ROW: (2, "€300.00"), OVERDUE_ROW: (1, "€100.00")},
            "JPY": {OUTSTANDING_ROW: (1, "JP¥8,000"), OVERDUE_ROW: (0, "JP¥0")},
            "USD": {OUTSTANDING_ROW: (2, "US$345.50"), OVERDUE_ROW: (1, "US$300.00")},
        }

    def test_tables_come_in_currency_code_order(self, ledger: Repo) -> None:
        assert list(report("outstanding")) == ["EUR", "JPY", "USD"]

    def test_leaves_out_drafts_voids_and_paid_invoices(self, ledger: Repo) -> None:
        """GBP has a draft, a void, and a payment, and nothing owed."""
        assert "GBP" not in report("outstanding")

    def test_overdue_is_measured_against_today(self, ledger: Repo) -> None:
        """Due yesterday is overdue; due tomorrow is not."""
        usd = report("outstanding", "--currency", "USD")["USD"]

        assert usd[OVERDUE_ROW] == (1, "US$300.00")

    def test_year_is_the_issue_year(self, ledger: Repo) -> None:
        """INV-2026-0001 was begun in 2025, but it was issued in 2026."""
        assert report("outstanding", "--year", "2025") == {
            "EUR": {OUTSTANDING_ROW: (1, "€100.00"), OVERDUE_ROW: (1, "€100.00")},
        }
        assert report("outstanding", "--year", "2026")["EUR"][OUTSTANDING_ROW] == (1, "€200.00")

    def test_currency_restricts_the_output(self, ledger: Repo) -> None:
        assert list(report("outstanding", "--currency", "usd")) == ["USD"]

    def test_says_so_when_nothing_is_outstanding(self, ledger: Repo) -> None:
        assert out("outstanding", "--currency", "GBP").strip() == NOTHING_OUTSTANDING

    def test_an_empty_repo_is_not_an_error(self, repo: Repo) -> None:
        assert out("outstanding").strip() == NOTHING_OUTSTANDING


# ---------------------------------------------------------------------- revenue


class TestRevenue:
    def test_reports_each_currency_in_its_own_table(self, ledger: Repo) -> None:
        assert report("revenue") == {
            "GBP": {PAID_ROW: (1, "£500.00")},
            "JPY": {PAID_ROW: (1, "JP¥120,000")},
        }

    def test_leaves_out_drafts_voids_and_unpaid_invoices(self, ledger: Repo) -> None:
        """EUR and USD have only invoices still owed; nothing in them was paid."""
        assert set(report("revenue")).isdisjoint({"EUR", "USD"})

    def test_year_is_the_payment_year_not_the_issue_year(self, ledger: Repo) -> None:
        """INV-2025-0002 was issued in 2025 and paid in 2026."""
        assert "JPY" in report("revenue", "--year", "2026")
        assert out("revenue", "--year", "2025").strip() == NO_REVENUE

    def test_currency_restricts_the_output(self, ledger: Repo) -> None:
        assert report("revenue", "--currency", "GBP") == {"GBP": {PAID_ROW: (1, "£500.00")}}

    def test_says_so_when_nothing_has_been_paid(self, repo: Repo) -> None:
        assert out("revenue").strip() == NO_REVENUE


# ------------------------------------------------------------- the one rule


class TestNeverCombined:
    @pytest.mark.parametrize("kind", ["outstanding", "revenue"])
    def test_no_line_holds_two_currencies(self, ledger: Repo, kind: str) -> None:
        for line in out(kind).splitlines():
            assert len(set(MARKS_RE.findall(line))) <= 1, line

    def test_no_cross_currency_sum_is_printed(self, ledger: Repo) -> None:
        """What EUR and USD outstanding would come to if anything added them."""
        assert "645.50" not in out("outstanding")
        assert "120,500" not in out("revenue")

    @pytest.mark.parametrize("kind", ["outstanding", "revenue"])
    def test_footer_appears_when_there_are_several_currencies(
        self, ledger: Repo, kind: str
    ) -> None:
        assert PER_CURRENCY_FOOTER in out(kind)

    def test_footer_is_left_off_a_report_in_one_currency(self, ledger: Repo) -> None:
        assert PER_CURRENCY_FOOTER not in out("outstanding", "--currency", "EUR")

    @pytest.mark.parametrize("kind", ["outstanding", "revenue"])
    def test_help_states_the_definitions(self, kind: str) -> None:
        help_text = plain(runner.invoke(app, ["report", kind, "--help"]).stdout)

        assert "never combined" in help_text
        assert ("'issued'" if kind == "outstanding" else "'paid'") in help_text


# ----------------------------------------------------------------- refusals


class TestRefusals:
    @pytest.mark.parametrize("code", ["XXQ", "pounds"])
    def test_an_unknown_currency_exits_1(self, ledger: Repo, code: str) -> None:
        result = runner.invoke(app, ["report", "outstanding", "--currency", code])

        assert result.exit_code == 1
        assert code.casefold() in result.output.casefold()

    @pytest.mark.parametrize("kind", ["outstanding", "revenue"])
    def test_reports_write_nothing(self, ledger: Repo, kind: str) -> None:
        before = commit_count(ledger)

        billkeeper("report", kind)

        assert commit_count(ledger) == before
        assert uncommitted(ledger) == ""
