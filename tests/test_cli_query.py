"""Tests for `billkeeper list` and `billkeeper show`, on a real repo and git.

Both commands only read, so what is asserted is the output and nothing else:
which invoices came back, in what order, and in whose currency. The set they
run against is the one `conftest.library` describes, chosen so that the order a
listing sorts into is not the order the ids sort into and so that every filter
has something to leave out.

Rich draws both tables and colours them whenever it thinks it is talking to a
terminal, so the output is read back with the escapes taken out. The two
currencies are the point of several of these: a listing of a GBP invoice and a
EUR invoice has no total to print at the bottom, and the tests say so.
"""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner, Result

from billkeeper.cli import app
from billkeeper.cli.query import DRAFT_LABEL, EMPTY_MESSAGE
from billkeeper.gitrepo import git_available
from billkeeper.storage import Repo

from conftest import (
    ACME,
    ACME_ADDRESS,
    ACME_EMAIL,
    ISSUER_ADDRESS,
    ISSUER_NAME,
    LAST_YEAR,
    LAST_YEAR_TOTAL,
    NEW_DRAFT,
    NEW_DRAFT_TOTAL,
    OLD_DRAFT,
    OLD_DRAFT_TOTAL,
    OUTSTANDING,
    OUTSTANDING_TOTAL,
    SETTLED,
    SETTLED_TOTAL,
    VOID_REASON,
    WITHDRAWN,
    WITHDRAWN_TOTAL,
    commit_count,
    head_message,
)

pytestmark = pytest.mark.skipif(not git_available(), reason="git is not installed")

runner = CliRunner()

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

#: Every invoice in the shared set, in the order a listing should put them.
EVERYTHING = (OLD_DRAFT, LAST_YEAR, SETTLED, OUTSTANDING, WITHDRAWN, NEW_DRAFT)

#: The two currency symbols the set is written in.
EURO = "€"
POUND = "£"

#: What the whole set would come to if anything were foolish enough to add it
#: up: 40 + 100 + 500 + 200 + 75 + 350. No command may ever print this.
COMBINED_TOTAL = "1,265.00"


def plain(text: str) -> str:
    """Return `text` with any colour Rich put in it removed."""
    return ANSI_RE.sub("", text)


def billkeeper(*args: str) -> Result:
    """Run `billkeeper args…`."""
    return runner.invoke(app, list(args))


def listed(*args: str) -> list[str]:
    """Return the ids `billkeeper list args…` came back with, in output order."""
    result = billkeeper("list", *args)
    assert result.exit_code == 0, result.output
    out = plain(result.stdout)
    return sorted(
        (ident for ident in EVERYTHING if ident in out),
        key=out.index,
    )


def shown(ident: str) -> str:
    """Return what `billkeeper show <ident>` printed."""
    result = billkeeper("show", ident)
    assert result.exit_code == 0, result.output
    return plain(result.stdout)


# ----------------------------------------------------------------- the listing


class TestList:
    def test_with_no_filters_shows_every_invoice(self, library: Repo) -> None:
        assert set(listed()) == set(EVERYTHING)

    def test_sorts_by_date_and_not_by_id(self, library: Repo) -> None:
        """The set is built so that the two orders disagree."""
        assert listed() == list(EVERYTHING)
        assert listed() != sorted(EVERYTHING)

    def test_shows_the_status_and_the_client_slug_of_each(self, library: Repo) -> None:
        out = plain(billkeeper("list").stdout)

        for expected in ("draft", "issued", "paid", "void", ACME[0], "globex"):
            assert expected in out

    def test_says_so_when_nothing_matches(self, library: Repo) -> None:
        result = billkeeper("list", "--client", "nobody")

        assert result.exit_code == 0, result.output
        assert plain(result.stdout).strip() == EMPTY_MESSAGE

    def test_a_repo_with_no_invoices_at_all_is_not_an_error(self, repo: Repo) -> None:
        """A freshly `init`ed repo has nothing to list, which is not a failure."""
        result = billkeeper("list")

        assert result.exit_code == 0, result.output
        assert plain(result.stdout).strip() == EMPTY_MESSAGE


class TestListFilters:
    def test_status_narrows_to_that_status(self, library: Repo) -> None:
        assert listed("--status", "draft") == [OLD_DRAFT, NEW_DRAFT]
        assert listed("--status", "paid") == [SETTLED]
        assert listed("--status", "void") == [WITHDRAWN]

    def test_status_may_be_given_more_than_once(self, library: Repo) -> None:
        assert listed("--status", "paid", "--status", "void") == [SETTLED, WITHDRAWN]

    def test_client_narrows_to_that_slug(self, library: Repo) -> None:
        assert listed("--client", ACME[0]) == [LAST_YEAR, OUTSTANDING, NEW_DRAFT]
        assert listed("--client", "globex") == [OLD_DRAFT, SETTLED, WITHDRAWN]

    def test_year_reads_the_issue_date_of_an_issued_invoice(self, library: Repo) -> None:
        """`INV-2026-0001` was begun in 2025 and issued in 2026."""
        assert OUTSTANDING in listed("--year", "2026")
        assert OUTSTANDING not in listed("--year", "2025")

    def test_year_reads_the_creation_date_of_a_draft(self, library: Repo) -> None:
        assert listed("--year", "2025") == [OLD_DRAFT, LAST_YEAR]
        assert listed("--year", "2026") == [SETTLED, OUTSTANDING, WITHDRAWN, NEW_DRAFT]

    def test_the_filters_combine(self, library: Repo) -> None:
        assert listed("--client", "globex", "--year", "2026", "--status", "paid") == [SETTLED]
        assert listed("--client", ACME[0], "--status", "draft") == [NEW_DRAFT]
        assert listed("--client", ACME[0], "--status", "void") == []

    def test_a_year_with_nothing_in_it_says_so(self, library: Repo) -> None:
        result = billkeeper("list", "--year", "2019")

        assert result.exit_code == 0, result.output
        assert plain(result.stdout).strip() == EMPTY_MESSAGE

    def test_an_unknown_status_is_refused_with_the_four_valid_ones(self, library: Repo) -> None:
        result = billkeeper("list", "--status", "posted")

        assert result.exit_code == 1
        assert "posted" in result.stderr
        for valid in ("draft", "issued", "paid", "void"):
            assert valid in result.stderr


class TestListCurrencies:
    def test_each_total_is_written_in_its_own_invoices_currency(self, library: Repo) -> None:
        out = plain(billkeeper("list").stdout)

        for total in (
            OLD_DRAFT_TOTAL,
            LAST_YEAR_TOTAL,
            SETTLED_TOTAL,
            OUTSTANDING_TOTAL,
            WITHDRAWN_TOTAL,
            NEW_DRAFT_TOTAL,
        ):
            assert total in out

    def test_no_line_puts_two_currencies_in_one_number(self, library: Repo) -> None:
        for line in plain(billkeeper("list").stdout).splitlines():
            assert not (EURO in line and POUND in line), line

    def test_nothing_adds_the_two_currencies_together(self, library: Repo) -> None:
        out = plain(billkeeper("list").stdout)

        assert COMBINED_TOTAL not in out
        # Not a "Total" row of any kind: the column heading is the only place
        # the word belongs in a listing of mixed currencies.
        assert out.count("Total") == 1


# -------------------------------------------------------------------- one of them


class TestShow:
    def test_a_draft_has_no_number_and_no_dates_it_has_not_reached(self, library: Repo) -> None:
        out = shown(NEW_DRAFT)

        assert f"Number:   {DRAFT_LABEL}" in out
        assert f"Draft id: {NEW_DRAFT}" in out
        assert "Status:   draft" in out
        assert "Created:  2026-03-01" in out
        assert "Issued:" not in out
        assert "Due:" not in out

    def test_a_draft_shows_its_line_items_and_what_they_come_to(self, library: Repo) -> None:
        out = shown(NEW_DRAFT)

        for expected in ("Design work", "2.5", "hour", "€80.00", "€200.00", "Site visit", "day"):
            assert expected in out
        assert f"Subtotal: {NEW_DRAFT_TOTAL}" in out
        assert f"Total:    {NEW_DRAFT_TOTAL}" in out

    def test_an_issued_invoice_shows_its_number_and_all_three_dates(self, library: Repo) -> None:
        out = shown(OUTSTANDING)

        assert f"Number:   {OUTSTANDING}" in out
        assert "Status:   issued" in out
        assert "Currency: EUR" in out
        assert "Created:  2025-12-20" in out
        assert "Issued:   2026-02-01" in out
        assert "Due:      2026-03-03" in out
        assert "Terms:    30 days" in out

    def test_both_the_issuer_and_the_client_are_named(self, library: Repo) -> None:
        out = shown(OUTSTANDING)

        assert ISSUER_NAME in out
        assert ACME[1] in out
        assert ACME_EMAIL in out
        # A multi-line address keeps its lines, indented under the block.
        for line in (*ISSUER_ADDRESS.split("\n"), *ACME_ADDRESS.split("\n")):
            assert f"  {line}" in out

    def test_the_pdf_is_named_when_it_is_there_and_left_out_when_it_is_not(
        self, library: Repo
    ) -> None:
        assert str(library.year_dir(2026) / f"{OUTSTANDING}.pdf") in shown(OUTSTANDING)
        assert "PDF:" not in shown(NEW_DRAFT)

    def test_the_status_history_reads_as_one_line_per_change(self, library: Repo) -> None:
        assert "issued on 2026-01-15" in shown(SETTLED)
        assert "paid on 2026-02-20" in shown(SETTLED)

    def test_a_voided_invoice_gives_the_reason_beside_the_date(self, library: Repo) -> None:
        out = shown(WITHDRAWN)

        assert "Status:   void" in out
        assert f"void on 2026-02-11 — {VOID_REASON}" in out
        # Voiding it did not take its number away.
        assert f"Number:   {WITHDRAWN}" in out

    def test_an_unambiguous_prefix_names_the_invoice(self, library: Repo) -> None:
        assert f"Number:   {LAST_YEAR}" in shown("INV-2025")

    def test_a_prefix_that_fits_two_lists_them_rather_than_guessing(self, library: Repo) -> None:
        result = billkeeper("show", "INV-2026")

        assert result.exit_code == 1
        for ident in (OUTSTANDING, SETTLED, WITHDRAWN):
            assert ident in result.stderr

    def test_an_unknown_id_says_so(self, library: Repo) -> None:
        result = billkeeper("show", "nothing-like-it")

        assert result.exit_code == 1
        assert "nothing-like-it" in result.stderr


def test_neither_command_writes_anything(library: Repo) -> None:
    """Both are read-only, which is a promise about the git tree as well."""
    before = commit_count(library)
    was = head_message(library)

    assert billkeeper("list").exit_code == 0
    assert billkeeper("show", NEW_DRAFT).exit_code == 0

    assert commit_count(library) == before
    assert head_message(library) == was
