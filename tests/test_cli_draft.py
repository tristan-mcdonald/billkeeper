"""Tests for `billkeeper new` and `billkeeper edit`, on a real repo and git.

A draft is a file the user is expected to open and rewrite, so most of what is
worth checking here only happens once an editor has been and gone. The fake
`$EDITOR` from `conftest.py` stands in for the person, and the clock is frozen
because a draft id carries the day it was raised: without that, the filename
every assertion looks for would change at midnight.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from billkeeper import clock
from billkeeper.cli import app
from billkeeper.cli.draft import EXAMPLE_DESCRIPTION, NO_CHANGES_MESSAGE
from billkeeper.gitrepo import git_available, run_git
from billkeeper.models import Invoice, InvoiceStatus
from billkeeper.storage import Repo

from conftest import REPO_CURRENCY, commit_count, editor_running, editor_writing, head_message

pytestmark = pytest.mark.skipif(not git_available(), reason="git is not installed")

runner = CliRunner()

#: The day every test is run on, and the stamp it puts in a draft id.
TODAY = dt.date(2026, 3, 1)
STAMP = "20260301"

ACME_DRAFT = f"acme-{STAMP}"

#: A draft as a user would leave it: three real lines, one of them fractional.
#: 2.5 at 80.00, 1 at 150.00, and 3 at 19.99 come to 409.97.
THREE_ITEMS = f"""\
kind = "invoice"
draft_id = "{ACME_DRAFT}"
currency = "{REPO_CURRENCY}"
created = {TODAY:%Y-%m-%d}
payment_terms_days = 30
status = "draft"

[client]
slug = "acme"
name = "Acme Ltd"

[[items]]
description = "Design work"
quantity = "2.5"
unit_price = "80.00"
unit = "hour"

[[items]]
description = "Site visit"
quantity = "1"
unit_price = "150.00"
unit = "day"

[[items]]
description = "Licence"
quantity = "3"
unit_price = "19.99"
unit = "item"
"""

THREE_ITEMS_TOTAL = "409.97"

BROKEN = 'description = "unclosed\n'

#: An editor that puts a price on the line a new draft starts with, in place
#: rather than by rewriting the file, so the rest of the draft is left alone.
PRICE_THE_EXAMPLE_LINE = (
    'sed \'s/unit_price = "0.00"/unit_price = "25.00"/\' "$1" > "$1.new" && mv "$1.new" "$1"'
)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold the date still, so a draft id is the same one every run."""
    monkeypatch.setattr(clock, "today", lambda: TODAY)


def billkeeper(*args: str) -> Result:
    """Run `billkeeper args…`."""
    return runner.invoke(app, list(args))


def add_client(slug: str = "acme", name: str = "Acme Ltd", *options: str) -> None:
    """Add a client to raise drafts against, and check that it took."""
    result = billkeeper("client", "add", "--name", name, "--slug", slug, *options)
    assert result.exit_code == 0, result.output


def new_draft(*options: str) -> Result:
    """Create a draft for `acme` without opening an editor, and check that it took."""
    result = billkeeper("new", "--client", "acme", "--no-edit", *options)
    assert result.exit_code == 0, result.output
    return result


def draft_path(repo: Repo, draft_id: str = ACME_DRAFT) -> Path:
    return repo.drafts_dir / f"{draft_id}.toml"


def stored(repo: Repo, draft_id: str = ACME_DRAFT) -> Invoice:
    return repo.read_invoice_at(draft_path(repo, draft_id))


def issue(repo: Repo, draft_id: str = ACME_DRAFT, number: str = "INV-2026-0001") -> Invoice:
    """Take a draft through storage's one legitimate route to an issued file."""
    invoice = repo.resolve(draft_id)
    invoice.number = number
    invoice.issue_date = TODAY
    invoice.transition(InvoiceStatus.ISSUED, on=TODAY)
    repo.move_draft_to_issued(invoice)
    return invoice


class TestNew:
    def test_writes_a_draft_named_for_the_client_and_the_day(self, repo: Repo) -> None:
        add_client()

        new_draft()

        assert draft_path(repo).is_file()
        assert stored(repo).draft_id == ACME_DRAFT

    def test_copies_the_clients_details_into_the_draft(self, repo: Repo) -> None:
        add_client("acme", "Acme Ltd", "--email", "ap@acme.invalid", "--contact", "A. Payer")

        new_draft()

        snapshot = stored(repo).client
        assert snapshot.slug == "acme"
        assert snapshot.name == "Acme Ltd"
        assert snapshot.email == "ap@acme.invalid"
        assert snapshot.contact == "A. Payer"

    def test_a_later_client_edit_does_not_reach_back_into_the_draft(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_client()
        new_draft()
        editor_writing(
            monkeypatch, tmp_path, 'slug = "acme"\nname = "Renamed Ltd"\ncurrency = "EUR"\n'
        )

        assert billkeeper("client", "edit", "acme").exit_code == 0
        assert stored(repo).client.name == "Acme Ltd"

    def test_starts_a_draft_with_no_number_and_a_line_to_overtype(self, repo: Repo) -> None:
        add_client()

        new_draft()

        draft = stored(repo)
        assert draft.number is None
        assert draft.status is InvoiceStatus.DRAFT
        assert [item.description for item in draft.items] == [EXAMPLE_DESCRIPTION]
        assert draft.items[0].unit == "hour"
        assert draft.total.amount == Decimal("0.00")

    def test_reports_the_draft_id_and_what_it_comes_to(self, repo: Repo) -> None:
        add_client()

        result = new_draft()

        assert ACME_DRAFT in result.stdout
        assert "0.00" in result.stdout

    def test_commits_once_saying_what_it_did(self, repo: Repo) -> None:
        add_client()
        before = commit_count(repo)

        new_draft()

        assert commit_count(repo) == before + 1
        assert head_message(repo) == f"Create draft {ACME_DRAFT}"
        assert run_git(repo.root, "status", "--porcelain") == ""

    def test_inherits_the_clients_currency_rather_than_the_repos(self, repo: Repo) -> None:
        # Given a currency the repo does not default to, so that the draft
        # taking it cannot be the repo's own EUR arriving by another route.
        add_client("acme", "Acme Ltd", "--currency", "GBP")

        new_draft()

        assert stored(repo).currency == "GBP"

    def test_a_currency_given_outright_wins(self, repo: Repo) -> None:
        add_client("acme", "Acme Ltd", "--currency", "GBP")

        new_draft("--currency", "usd")

        assert stored(repo).currency == "USD"

    def test_takes_the_payment_terms_from_the_repos_config(self, repo: Repo) -> None:
        config = repo.config_path.read_text(encoding="utf-8")
        assert "payment_terms_days = 30" in config
        repo.config_path.write_text(
            config.replace("payment_terms_days = 30", "payment_terms_days = 14"),
            encoding="utf-8",
        )
        add_client()

        new_draft()

        assert stored(repo).payment_terms_days == 14

    def test_records_a_note_that_was_given(self, repo: Repo) -> None:
        add_client()

        new_draft("--notes", "Thank you for your business.")

        assert stored(repo).notes == "Thank you for your business."

    def test_a_second_draft_the_same_day_is_filed_beside_the_first(self, repo: Repo) -> None:
        add_client()

        new_draft()
        new_draft()

        assert draft_path(repo).is_file()
        assert draft_path(repo, f"{ACME_DRAFT}-2").is_file()

    def test_a_date_given_outright_names_the_draft(self, repo: Repo) -> None:
        add_client()

        new_draft("--date", "2026-01-15")

        assert stored(repo, "acme-20260115").created == dt.date(2026, 1, 15)

    def test_a_date_it_cannot_read_is_refused_before_anything_is_written(self, repo: Repo) -> None:
        add_client()
        before = commit_count(repo)

        result = billkeeper("new", "--client", "acme", "--no-edit", "--date", "yesterday")

        assert result.exit_code == 1
        assert "YYYY-MM-DD" in result.stderr
        assert commit_count(repo) == before
        assert not draft_path(repo).exists()

    def test_an_unknown_client_names_it_and_says_how_to_look(self, repo: Repo) -> None:
        before = commit_count(repo)

        result = billkeeper("new", "--client", "nobody", "--no-edit")

        assert result.exit_code == 1
        assert "nobody" in result.stderr
        assert "billkeeper client list" in result.stderr
        assert commit_count(repo) == before

    def test_no_edit_never_reaches_the_editor(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_client()
        editor_running(monkeypatch, tmp_path, "exit 3")

        new_draft()

        assert draft_path(repo).is_file()

    def test_reports_the_total_of_what_the_editor_left(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_client()
        editor_writing(monkeypatch, tmp_path, THREE_ITEMS)

        result = billkeeper("new", "--client", "acme")

        assert result.exit_code == 0, result.output
        assert THREE_ITEMS_TOTAL in result.stdout
        draft = stored(repo)
        assert [item.quantity for item in draft.items] == [
            Decimal("2.5"),
            Decimal("1"),
            Decimal("3"),
        ]
        assert head_message(repo) == f"Create draft {ACME_DRAFT}"

    def test_invalid_toml_keeps_the_text_and_commits_nothing(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_client()
        editor_writing(monkeypatch, tmp_path, BROKEN)
        before = commit_count(repo)

        result = billkeeper("new", "--client", "acme")

        assert result.exit_code == 1
        assert str(draft_path(repo)) in result.stderr
        assert "unclosed" in draft_path(repo).read_text(encoding="utf-8")
        assert commit_count(repo) == before

    def test_an_editor_that_fails_commits_nothing(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_client()
        editor_running(monkeypatch, tmp_path, "exit 3")
        before = commit_count(repo)

        result = billkeeper("new", "--client", "acme")

        assert result.exit_code == 1
        assert "3" in result.stderr
        assert commit_count(repo) == before


class TestEdit:
    def test_commits_what_the_editor_left_and_reports_the_new_total(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_client()
        new_draft()
        editor_writing(monkeypatch, tmp_path, THREE_ITEMS)
        before = commit_count(repo)

        result = billkeeper("edit", ACME_DRAFT)

        assert result.exit_code == 0, result.output
        assert THREE_ITEMS_TOTAL in result.stdout
        assert len(stored(repo).items) == 3
        assert commit_count(repo) == before + 1
        assert head_message(repo) == f"Edit draft {ACME_DRAFT}"
        assert run_git(repo.root, "status", "--porcelain") == ""

    def test_says_nothing_changed_when_the_editor_touched_nothing(self, repo: Repo) -> None:
        add_client()
        new_draft()
        before = commit_count(repo)

        result = billkeeper("edit", ACME_DRAFT)

        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == NO_CHANGES_MESSAGE
        assert commit_count(repo) == before

    def test_invalid_toml_keeps_the_text_and_commits_nothing(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_client()
        new_draft()
        editor_writing(monkeypatch, tmp_path, BROKEN)
        before = commit_count(repo)

        result = billkeeper("edit", ACME_DRAFT)

        assert result.exit_code == 1
        assert str(draft_path(repo)) in result.stderr
        assert "unclosed" in draft_path(repo).read_text(encoding="utf-8")
        assert commit_count(repo) == before

    def test_a_field_the_model_refuses_stops_short_of_a_commit(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_client()
        new_draft()
        editor_writing(monkeypatch, tmp_path, THREE_ITEMS.replace('"2.5"', "2.5"))
        before = commit_count(repo)

        result = billkeeper("edit", ACME_DRAFT)

        assert result.exit_code == 1
        assert commit_count(repo) == before

    def test_refuses_to_rename_the_draft_id(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_client()
        new_draft()
        editor_writing(monkeypatch, tmp_path, THREE_ITEMS.replace(ACME_DRAFT, "something-else"))
        before = commit_count(repo)

        result = billkeeper("edit", ACME_DRAFT)

        assert result.exit_code == 1
        assert "something-else" in result.stderr
        assert commit_count(repo) == before

    def test_refuses_an_issued_invoice_and_says_what_to_do_instead(self, repo: Repo) -> None:
        add_client()
        new_draft()
        issue(repo)
        before = commit_count(repo)

        result = billkeeper("edit", "INV-2026-0001")

        assert result.exit_code == 1
        assert "INV-2026-0001 is issued and cannot be edited." in result.stderr
        assert "credit note" in result.stderr
        assert commit_count(repo) == before

    def test_refuses_a_paid_invoice(self, repo: Repo) -> None:
        add_client()
        new_draft()
        invoice = issue(repo)
        invoice.transition(InvoiceStatus.PAID, on=TODAY)
        repo.write_invoice(invoice)

        result = billkeeper("edit", "INV-2026-0001")

        assert result.exit_code == 1
        assert "is paid and cannot be edited." in result.stderr

    def test_refuses_a_void_invoice(self, repo: Repo) -> None:
        add_client()
        new_draft()
        invoice = issue(repo)
        invoice.transition(InvoiceStatus.VOID, on=TODAY, reason="Raised by mistake")
        repo.write_invoice(invoice)

        result = billkeeper("edit", "INV-2026-0001")

        assert result.exit_code == 1
        assert "is void and cannot be edited." in result.stderr

    def test_a_unique_prefix_names_the_draft(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_client()
        add_client("zebra", "Zebra Ltd")
        new_draft()
        assert billkeeper("new", "--client", "zebra", "--no-edit").exit_code == 0
        editor_running(monkeypatch, tmp_path, PRICE_THE_EXAMPLE_LINE)

        result = billkeeper("edit", "zeb")

        assert result.exit_code == 0, result.output
        assert stored(repo, f"zebra-{STAMP}").total.amount == Decimal("25.00")
        assert head_message(repo) == f"Edit draft zebra-{STAMP}"

    def test_an_ambiguous_prefix_lists_the_candidates(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_client()
        new_draft()
        new_draft()
        editor_running(monkeypatch, tmp_path, "exit 3")
        before = commit_count(repo)

        result = billkeeper("edit", "acme-2026")

        assert result.exit_code == 1
        assert ACME_DRAFT in result.stderr
        assert f"{ACME_DRAFT}-2" in result.stderr
        assert commit_count(repo) == before

    def test_an_unknown_id_says_so(self, repo: Repo) -> None:
        result = billkeeper("edit", "nothing-like-it")

        assert result.exit_code == 1
        assert "nothing-like-it" in result.stderr
