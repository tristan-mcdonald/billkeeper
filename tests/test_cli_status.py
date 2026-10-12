"""Tests for `billkeeper mark-paid` and `billkeeper void`, on a real repo and git.

These are the only two commands that write to an invoice after it has been
issued, so the question behind most of these tests is what they leave alone. The
line items, the client, the dates, the number, the PDF: all of it has to come
back from disk exactly as it went in, with only the status and its history
different. `frozen_part` is how that is asked — it compares everything the
storage layer refuses to let an issued invoice change, so a test does not have
to list the fields and then go stale when one is added.

The other half is the transitions themselves. `draft → issued → paid` and
`void` off either of the first two are the only legal moves, and each refusal
has to name where the invoice actually is. The invoice set comes from
`conftest.library`, which has one invoice in each of the four states already.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from billkeeper.cli import app
from billkeeper.gitrepo import git_available
from billkeeper.models import Invoice, InvoiceStatus
from billkeeper.storage import MUTABLE_AFTER_ISSUE, PDF_SUFFIX, Repo, to_toml_dict

from conftest import (
    NEW_DRAFT,
    OLD_DRAFT,
    OUTSTANDING,
    OUTSTANDING_TOTAL,
    SETTLED,
    TODAY,
    WITHDRAWN,
    commit_count,
    committed_paths,
    head_message,
    relative,
    uncommitted,
)

pytestmark = pytest.mark.skipif(not git_available(), reason="git is not installed")

runner = CliRunner()

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

#: A reason of the kind a user actually types, and one with line breaks in it.
REASON = "Client cancelled the project"
RAMBLING_REASON = "Cancelled by the client\nafter the second meeting"


def billkeeper(*args: str) -> Result:
    """Run `billkeeper args…`."""
    return runner.invoke(app, list(args))


def stored(repo: Repo, ident: str) -> Invoice:
    """Return the invoice `ident` names, read back from disk."""
    return repo.resolve(ident)


def frozen_part(invoice: Invoice) -> dict[str, Any]:
    """Return everything about `invoice` that a status change must not touch.

    Taken from the storage layer's own list of what stays mutable after issue,
    so this is the same comparison `Repo.write_invoice` makes — a field added to
    the invoice later is covered here without anybody remembering to add it.
    """
    return {
        key: value for key, value in to_toml_dict(invoice).items() if key not in MUTABLE_AFTER_ISSUE
    }


def reached(invoice: Invoice, status: InvoiceStatus) -> dt.date | None:
    """Return the date `invoice`'s history records for `status`."""
    for event in invoice.status_history:
        if event.status == status:
            return event.date
    return None


# ------------------------------------------------------------------ mark-paid


class TestMarkPaid:
    def test_records_the_payment_against_the_invoice(self, library: Repo) -> None:
        was = frozen_part(stored(library, OUTSTANDING))

        result = billkeeper("mark-paid", OUTSTANDING)

        assert result.exit_code == 0, result.output
        invoice = stored(library, OUTSTANDING)
        assert invoice.status == InvoiceStatus.PAID
        assert reached(invoice, InvoiceStatus.PAID) == TODAY
        # The history is added to, not replaced: how it got here is the record.
        assert [event.status for event in invoice.status_history] == [
            InvoiceStatus.ISSUED,
            InvoiceStatus.PAID,
        ]
        assert frozen_part(invoice) == was

    def test_says_what_was_paid_and_when(self, library: Repo) -> None:
        result = billkeeper("mark-paid", OUTSTANDING)

        assert OUTSTANDING in result.stdout
        assert OUTSTANDING_TOTAL in result.stdout
        assert TODAY.isoformat() in result.stdout

    def test_commits_the_invoice_file_and_nothing_else(self, library: Repo) -> None:
        before = commit_count(library)
        path = library.year_dir(2026) / f"{OUTSTANDING}.toml"

        billkeeper("mark-paid", OUTSTANDING)

        assert commit_count(library) == before + 1
        assert head_message(library) == f"Mark {OUTSTANDING} paid"
        assert committed_paths(library) == {relative(library, path)}
        assert uncommitted(library) == ""

    def test_leaves_the_rendered_pdf_exactly_as_it_was(self, library: Repo) -> None:
        pdf = (library.year_dir(2026) / f"{OUTSTANDING}.toml").with_suffix(PDF_SUFFIX)
        was = pdf.read_bytes()

        billkeeper("mark-paid", OUTSTANDING)

        assert pdf.read_bytes() == was

    def test_a_date_that_is_given_is_the_date_recorded(self, library: Repo) -> None:
        result = billkeeper("mark-paid", OUTSTANDING, "--date", "2026-02-01")

        assert result.exit_code == 0, result.output
        assert reached(stored(library, OUTSTANDING), InvoiceStatus.PAID) == dt.date(2026, 2, 1)
        assert "2026-02-01" in result.stdout

    def test_a_date_that_is_not_a_date_is_refused_and_nothing_is_recorded(
        self, library: Repo
    ) -> None:
        before = commit_count(library)

        result = billkeeper("mark-paid", OUTSTANDING, "--date", "last Tuesday")

        assert result.exit_code == 1
        assert "YYYY-MM-DD" in result.stderr
        assert stored(library, OUTSTANDING).status == InvoiceStatus.ISSUED
        assert commit_count(library) == before
        assert uncommitted(library) == ""

    def test_an_unambiguous_prefix_names_the_invoice(self, library: Repo) -> None:
        result = billkeeper("mark-paid", "INV-2026-0001")

        assert result.exit_code == 0, result.output
        assert stored(library, OUTSTANDING).status == InvoiceStatus.PAID


class TestMarkPaidRefused:
    @pytest.mark.parametrize(
        ("ident", "status"),
        [(NEW_DRAFT, "draft"), (SETTLED, "paid"), (WITHDRAWN, "void")],
    )
    def test_only_an_issued_invoice_can_be_paid(
        self, library: Repo, ident: str, status: str
    ) -> None:
        before = commit_count(library)
        was = frozen_part(stored(library, ident))

        result = billkeeper("mark-paid", ident)

        assert result.exit_code == 1
        # The status it is actually in is the whole answer to why not.
        assert status in result.stderr
        assert ident in result.stderr
        assert frozen_part(stored(library, ident)) == was
        assert commit_count(library) == before
        assert uncommitted(library) == ""

    def test_an_invoice_already_paid_is_told_when_it_was(self, library: Repo) -> None:
        result = billkeeper("mark-paid", SETTLED)

        assert result.exit_code == 1
        assert "2026-02-20" in result.stderr

    def test_a_draft_is_told_to_be_issued_first(self, library: Repo) -> None:
        result = billkeeper("mark-paid", NEW_DRAFT)

        assert result.exit_code == 1
        assert "billkeeper issue" in result.stderr

    def test_an_unknown_id_says_so(self, library: Repo) -> None:
        result = billkeeper("mark-paid", "nothing-like-it")

        assert result.exit_code == 1
        assert "nothing-like-it" in result.stderr


# ----------------------------------------------------------------------- void


class TestVoid:
    def test_a_draft_is_voided_in_place_with_its_reason(self, library: Repo) -> None:
        path = library.drafts_dir / f"{OLD_DRAFT}.toml"
        was = frozen_part(stored(library, OLD_DRAFT))
        before = commit_count(library)

        result = billkeeper("void", OLD_DRAFT, "--reason", REASON)

        assert result.exit_code == 0, result.output
        invoice = stored(library, OLD_DRAFT)
        assert invoice.status == InvoiceStatus.VOID
        assert invoice.status_history[-1].reason == REASON
        assert invoice.status_history[-1].date == TODAY
        # A draft has no number to be filed under, so it stays where it was.
        assert path.is_file()
        assert frozen_part(invoice) == was
        assert commit_count(library) == before + 1
        assert head_message(library) == f"Void {OLD_DRAFT}: {REASON}"
        assert uncommitted(library) == ""

    def test_an_issued_invoice_keeps_its_number_and_its_pdf(self, library: Repo) -> None:
        toml = library.year_dir(2026) / f"{OUTSTANDING}.toml"
        pdf = toml.with_suffix(PDF_SUFFIX)
        was_pdf = pdf.read_bytes()
        before = commit_count(library)

        result = billkeeper("void", OUTSTANDING, "--reason", REASON)

        assert result.exit_code == 0, result.output
        invoice = stored(library, OUTSTANDING)
        assert invoice.status == InvoiceStatus.VOID
        # The number is used up, so giving it back would leave a gap.
        assert invoice.number == OUTSTANDING
        assert toml.is_file()
        assert pdf.read_bytes() == was_pdf
        assert commit_count(library) == before + 1
        assert committed_paths(library) == {relative(library, toml)}
        assert uncommitted(library) == ""

    def test_it_says_what_it_voided_and_why(self, library: Repo) -> None:
        result = billkeeper("void", OUTSTANDING, "--reason", REASON)

        assert OUTSTANDING in result.stdout
        assert REASON in result.stdout
        assert TODAY.isoformat() in result.stdout

    def test_the_reason_is_kept_whole_but_the_commit_subject_is_one_line(
        self, library: Repo
    ) -> None:
        result = billkeeper("void", OUTSTANDING, "--reason", RAMBLING_REASON)

        assert result.exit_code == 0, result.output
        assert stored(library, OUTSTANDING).status_history[-1].reason == RAMBLING_REASON
        assert "\n" not in head_message(library)
        assert head_message(library) == (
            f"Void {OUTSTANDING}: Cancelled by the client after the second meeting"
        )

    def test_the_line_items_are_untouched(self, library: Repo) -> None:
        was = frozen_part(stored(library, OUTSTANDING))

        billkeeper("void", OUTSTANDING, "--reason", REASON)

        assert frozen_part(stored(library, OUTSTANDING)) == was

    def test_an_unambiguous_prefix_names_the_invoice(self, library: Repo) -> None:
        result = billkeeper("void", "globex-2025", "--reason", REASON)

        assert result.exit_code == 0, result.output
        assert stored(library, OLD_DRAFT).status == InvoiceStatus.VOID


class TestVoidRefused:
    @pytest.mark.parametrize(("ident", "status"), [(SETTLED, "paid"), (WITHDRAWN, "void")])
    def test_a_paid_or_already_voided_invoice_is_refused_by_status(
        self, library: Repo, ident: str, status: str
    ) -> None:
        before = commit_count(library)
        was = frozen_part(stored(library, ident))

        result = billkeeper("void", ident, "--reason", REASON)

        assert result.exit_code == 1
        assert status in result.stderr
        assert ident in result.stderr
        assert frozen_part(stored(library, ident)) == was
        assert commit_count(library) == before
        assert uncommitted(library) == ""

    def test_an_already_voided_invoice_is_told_when_it_was(self, library: Repo) -> None:
        result = billkeeper("void", WITHDRAWN, "--reason", REASON)

        assert result.exit_code == 1
        assert "2026-02-11" in result.stderr

    def test_a_paid_invoice_is_pointed_at_a_credit_note(self, library: Repo) -> None:
        result = billkeeper("void", SETTLED, "--reason", REASON)

        assert result.exit_code == 1
        assert "credit note" in result.stderr

    def test_no_reason_at_all_is_refused_by_the_command_line(self, library: Repo) -> None:
        before = commit_count(library)

        result = billkeeper("void", OUTSTANDING)

        assert result.exit_code != 0
        # Click's usage error is drawn by Rich, which colours it on CI and
        # styles the two dashes apart from the name, so the escapes come out.
        assert "--reason" in ANSI_RE.sub("", result.output)
        assert stored(library, OUTSTANDING).status == InvoiceStatus.ISSUED
        assert commit_count(library) == before

    @pytest.mark.parametrize("reason", ["", "   "])
    def test_an_empty_reason_is_refused_and_says_what_a_reason_is_for(
        self, library: Repo, reason: str
    ) -> None:
        before = commit_count(library)

        result = billkeeper("void", OUTSTANDING, "--reason", reason)

        assert result.exit_code == 1
        assert "--reason" in result.stderr
        assert stored(library, OUTSTANDING).status == InvoiceStatus.ISSUED
        assert commit_count(library) == before
        assert uncommitted(library) == ""


# ---------------------------------------------------------------- the two together


def test_a_voided_invoice_cannot_then_be_paid(library: Repo) -> None:
    assert billkeeper("void", OUTSTANDING, "--reason", REASON).exit_code == 0

    result = billkeeper("mark-paid", OUTSTANDING)

    assert result.exit_code == 1
    assert stored(library, OUTSTANDING).status == InvoiceStatus.VOID


def test_a_paid_invoice_cannot_then_be_voided(library: Repo) -> None:
    assert billkeeper("mark-paid", OUTSTANDING).exit_code == 0

    result = billkeeper("void", OUTSTANDING, "--reason", REASON)

    assert result.exit_code == 1
    assert stored(library, OUTSTANDING).status == InvoiceStatus.PAID
