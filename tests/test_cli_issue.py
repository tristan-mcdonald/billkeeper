"""Tests for `billkeeper issue` and `billkeeper render`, on a real repo and git.

Issuing is the command with the most to lose. It draws from a counter that must
never show a gap, moves a file, shells out to two programs, and commits the lot,
so most of what is checked here is what the repo looks like when one of those
steps does not happen: the draft back where it was, the counter where it was,
and no commit. Those tests stand in a render that fails rather than arranging
for a real one to, because a failure that can be asked for on demand is the only
kind that can be tested on every machine.

The rest make real PDFs, and are skipped with a reason where Pandoc and Typst
are not installed. CI installs both. The clock is frozen throughout because a
draft id carries the day it was raised and an invoice number carries the year it
was issued in, so without that the filenames every assertion looks for would
change at midnight and again at new year.
"""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner, Result

from billkeeper import clock, render, sequence
from billkeeper.cli import app
from billkeeper.cli.issue import UP_TO_DATE_MESSAGE
from billkeeper.config import RepoConfig, load_repo_config
from billkeeper.errors import BillkeeperError
from billkeeper.gitrepo import git_available
from billkeeper.models import Invoice, InvoiceStatus
from billkeeper.render import REQUIRED_TOOLS, missing_tools
from billkeeper.storage import Repo

from conftest import (
    REPO_CURRENCY,
    commit_count,
    committed_paths,
    editor_writing,
    head_message,
    relative,
    uncommitted,
)

pytestmark = pytest.mark.skipif(not git_available(), reason="git is not installed")

#: Making a real PDF needs the real pipeline installed.
_MISSING = missing_tools()
needs_tools = pytest.mark.skipif(bool(_MISSING), reason=f"not installed: {', '.join(_MISSING)}")

runner = CliRunner()

#: Kept before any test replaces `shutil.which`, to answer for everything else.
_REAL_WHICH = shutil.which

#: The day every test is run on, and what 30-day terms make of it.
TODAY = dt.date(2026, 3, 1)
DUE = dt.date(2026, 3, 31)
STAMP = "20260301"

ACME_DRAFT = f"acme-{STAMP}"
SECOND_DRAFT = f"acme-{STAMP}-2"

FIRST = "INV-2026-0001"
SECOND = "INV-2026-0002"

#: A draft as a user leaves it: 2.5 at 80.00 and 1 at 150.00 come to 350.00.
TWO_ITEMS = """
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
"""

TOTAL = "€350.00"

#: A draft nobody has put a line on yet, which is not an invoice to send.
NO_ITEMS = ""


def draft_body(draft_id: str = ACME_DRAFT, items: str = TWO_ITEMS, created: dt.date = TODAY) -> str:
    """Return a whole draft file, as an editor would leave it behind."""
    return f"""\
kind = "invoice"
draft_id = "{draft_id}"
currency = "{REPO_CURRENCY}"
created = {created:%Y-%m-%d}
payment_terms_days = 30
status = "draft"

[client]
slug = "acme"
name = "Acme Ltd"
{items}"""


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold the date still, so an id and a number are the same ones every run."""
    monkeypatch.setattr(clock, "today", lambda: TODAY)


@pytest.fixture
def drafted(repo: Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Repo:
    """Return a data repo holding one client and one draft with real lines on it."""
    add_client()
    make_draft(monkeypatch, tmp_path, draft_body())
    return repo


def billkeeper(*args: str) -> Result:
    """Run `billkeeper args…`."""
    return runner.invoke(app, list(args))


def add_client(slug: str = "acme", name: str = "Acme Ltd") -> None:
    """Add the client every draft here is raised against."""
    result = billkeeper("client", "add", "--name", name, "--slug", slug)
    assert result.exit_code == 0, result.output


def make_draft(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> None:
    """Create a draft and have the fake editor leave `body` in it."""
    editor_writing(monkeypatch, tmp_path, body)
    result = billkeeper("new", "--client", "acme")
    assert result.exit_code == 0, result.output


def issued_paths(repo: Repo, number: str = FIRST, year: int = 2026) -> tuple[Path, Path]:
    """Return where an issued invoice's TOML and PDF belong."""
    toml = repo.year_dir(year) / f"{number}.toml"
    return toml, toml.with_suffix(".pdf")


def draft_paths(repo: Repo, draft_id: str = ACME_DRAFT) -> tuple[Path, Path]:
    """Return where a draft's TOML and PDF belong."""
    toml = repo.drafts_dir / f"{draft_id}.toml"
    return toml, toml.with_suffix(".pdf")


def cfg_of(repo: Repo) -> RepoConfig:
    return load_repo_config(repo.root)


def next_number(repo: Repo, year: int = 2026) -> int:
    """Return the sequence number the next invoice of `year` would take."""
    return sequence.peek(repo, cfg_of(repo), year)


def stored(repo: Repo, path: Path) -> Invoice:
    return repo.read_invoice_at(path)


def render_failing(monkeypatch: pytest.MonkeyPatch, message: str = "Typst fell over.") -> None:
    """Stand in a render that fails the way a template with a mistake in it does."""

    def fail(*args: object, **kwargs: object) -> Path:
        raise BillkeeperError(message)

    # The tools check is stood down with it: this test is about what happens
    # after a number has been drawn, and it has to run on a machine with no
    # Pandoc as much as on one with it.
    monkeypatch.setattr(render, "check_tools", lambda: None)
    monkeypatch.setattr(render, "render_pdf", fail)


def without_the_render_tools(name: str, *args: object, **kwargs: object) -> str | None:
    """Stand in for `shutil.which` on a machine with no Pandoc and no Typst.

    Everything else is still found, git included: these tests read the commit
    log after the command they are testing has refused to run.
    """
    return None if name in REQUIRED_TOOLS else _REAL_WHICH(name)


def issue_by_hand(repo: Repo, draft_id: str = ACME_DRAFT, number: str = FIRST) -> Invoice:
    """Take a draft through storage's own route to an issued file, without Pandoc.

    `issue` is what this file is testing, so the tests about refusing to issue
    something twice cannot use it to set themselves up — and they would be
    skipped on a machine with no Pandoc if they did.
    """
    invoice = repo.resolve(draft_id)
    invoice.number = number
    invoice.issue_date = TODAY
    invoice.due_date = DUE
    invoice.transition(InvoiceStatus.ISSUED, on=TODAY)
    repo.move_draft_to_issued(invoice)
    return invoice


# ------------------------------------------------------------------- issuing


class TestIssue:
    @needs_tools
    @pytest.mark.requires_tools
    def test_numbers_the_draft_files_it_under_its_year_and_renders_it(self, drafted: Repo) -> None:
        toml, pdf = issued_paths(drafted)

        result = billkeeper("issue", ACME_DRAFT)

        assert result.exit_code == 0, result.output
        assert toml.is_file()
        assert pdf.read_bytes().startswith(b"%PDF-")
        assert pdf.stat().st_size > 1024
        assert FIRST in result.output
        assert TOTAL in result.output
        assert str(pdf) in result.output

    @needs_tools
    @pytest.mark.requires_tools
    def test_records_the_number_the_dates_and_the_status(self, drafted: Repo) -> None:
        toml, _ = issued_paths(drafted)

        billkeeper("issue", ACME_DRAFT)

        invoice = stored(drafted, toml)
        assert invoice.number == FIRST
        assert invoice.draft_id is None
        assert invoice.status == InvoiceStatus.ISSUED
        assert invoice.issue_date == TODAY
        # 30 days' terms, counted from the day it went out.
        assert invoice.due_date == DUE
        assert [event.status for event in invoice.status_history] == [InvoiceStatus.ISSUED]
        assert invoice.status_history[-1].date == TODAY

    @needs_tools
    @pytest.mark.requires_tools
    def test_leaves_nothing_behind_under_drafts(self, drafted: Repo) -> None:
        billkeeper("issue", ACME_DRAFT)

        assert list(drafted.drafts_dir.glob("*.toml")) == []
        assert list(drafted.drafts_dir.glob("*.pdf")) == []

    @needs_tools
    @pytest.mark.requires_tools
    def test_commits_the_invoice_the_pdf_and_the_counter_together(self, drafted: Repo) -> None:
        before = commit_count(drafted)
        toml, pdf = issued_paths(drafted)
        draft_toml, _ = draft_paths(drafted)

        billkeeper("issue", ACME_DRAFT)

        assert commit_count(drafted) == before + 1
        assert head_message(drafted) == f"Issue invoice {FIRST}"
        assert committed_paths(drafted) == {
            relative(drafted, toml),
            relative(drafted, pdf),
            "sequence.toml",
            # The draft leaving is part of the same change, not the next one.
            relative(drafted, draft_toml),
        }
        assert uncommitted(drafted) == ""

    @needs_tools
    @pytest.mark.requires_tools
    def test_the_draft_pdf_goes_with_the_draft(self, drafted: Repo) -> None:
        """A PDF of a draft is a file git is already tracking, so it has to be removed."""
        assert billkeeper("render", ACME_DRAFT).exit_code == 0
        draft_pdf = draft_paths(drafted)[1]
        assert draft_pdf.is_file()

        billkeeper("issue", ACME_DRAFT)

        assert not draft_pdf.exists()
        assert relative(drafted, draft_pdf) in committed_paths(drafted)
        assert uncommitted(drafted) == ""

    @needs_tools
    @pytest.mark.requires_tools
    def test_the_second_invoice_takes_the_next_number(
        self, drafted: Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        make_draft(monkeypatch, tmp_path, draft_body(SECOND_DRAFT))

        first = billkeeper("issue", ACME_DRAFT)
        second = billkeeper("issue", SECOND_DRAFT)

        assert first.exit_code == 0, first.output
        assert second.exit_code == 0, second.output
        assert FIRST in first.output
        assert SECOND in second.output
        assert issued_paths(drafted, FIRST)[0].is_file()
        assert issued_paths(drafted, SECOND)[0].is_file()

    @needs_tools
    @pytest.mark.requires_tools
    def test_the_counter_advances_by_exactly_one_each_time(
        self, drafted: Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        make_draft(monkeypatch, tmp_path, draft_body(SECOND_DRAFT))
        assert next_number(drafted) == 1

        billkeeper("issue", ACME_DRAFT)
        assert next_number(drafted) == 2

        billkeeper("issue", SECOND_DRAFT)
        assert next_number(drafted) == 3

    @needs_tools
    @pytest.mark.requires_tools
    def test_an_unambiguous_prefix_names_the_draft(self, drafted: Repo) -> None:
        result = billkeeper("issue", "acme")

        assert result.exit_code == 0, result.output
        assert issued_paths(drafted)[0].is_file()

    @needs_tools
    @pytest.mark.requires_tools
    def test_a_draft_begun_last_year_is_dated_and_filed_by_the_day_it_goes_out(
        self, repo: Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """What dates an invoice is the day it is issued, not the day it was begun."""
        add_client()
        make_draft(monkeypatch, tmp_path, draft_body(created=dt.date(2025, 12, 20)))

        result = billkeeper("issue", ACME_DRAFT)

        assert result.exit_code == 0, result.output
        toml, pdf = issued_paths(repo, FIRST, year=2026)
        assert toml.is_file()
        assert pdf.is_file()
        invoice = stored(repo, toml)
        assert invoice.created == dt.date(2025, 12, 20)
        assert invoice.issue_date == TODAY
        assert invoice.due_date == DUE


# --------------------------------------------------- refusing to issue at all


class TestIssueRefused:
    def test_a_draft_with_no_line_items_is_refused_and_draws_no_number(
        self, repo: Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        add_client()
        make_draft(monkeypatch, tmp_path, draft_body(items=NO_ITEMS))
        before = commit_count(repo)

        result = billkeeper("issue", ACME_DRAFT)

        assert result.exit_code == 1
        assert "line item" in result.stderr
        assert next_number(repo) == 1
        assert draft_paths(repo)[0].is_file()
        assert commit_count(repo) == before
        assert uncommitted(repo) == ""

    def test_an_already_issued_invoice_is_refused_by_name_and_status(self, drafted: Repo) -> None:
        issue_by_hand(drafted)

        result = billkeeper("issue", FIRST)

        assert result.exit_code == 1
        assert FIRST in result.stderr
        assert "issued" in result.stderr
        assert next_number(drafted) == 1

    def test_a_missing_pandoc_is_reported_before_a_number_is_drawn(
        self, drafted: Repo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing to give back is better than giving a number back."""
        before = commit_count(drafted)
        monkeypatch.setattr(shutil, "which", without_the_render_tools)

        result = billkeeper("issue", ACME_DRAFT)

        assert result.exit_code == 1
        assert "pandoc" in result.stderr
        assert next_number(drafted) == 1
        assert draft_paths(drafted)[0].is_file()
        assert commit_count(drafted) == before
        assert uncommitted(drafted) == ""

    def test_an_unknown_id_says_so(self, drafted: Repo) -> None:
        result = billkeeper("issue", "nothing-like-it")

        assert result.exit_code == 1
        assert "nothing-like-it" in result.stderr
        assert next_number(drafted) == 1


# ------------------------------------------------------------- undoing a half


class TestIssueUndone:
    def test_a_failed_render_puts_the_draft_back_and_gives_the_number_back(
        self, drafted: Repo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = commit_count(drafted)
        draft_toml, _ = draft_paths(drafted)
        was = draft_toml.read_bytes()
        render_failing(monkeypatch)

        result = billkeeper("issue", ACME_DRAFT)

        assert result.exit_code == 1
        assert "Typst fell over." in result.stderr
        # The draft, byte for byte, where it was.
        assert draft_toml.read_bytes() == was
        assert stored(drafted, draft_toml).number is None
        assert stored(drafted, draft_toml).status == InvoiceStatus.DRAFT
        # No half-issued invoice, and no gap in the numbering.
        toml, pdf = issued_paths(drafted)
        assert not toml.exists()
        assert not pdf.exists()
        assert next_number(drafted) == 1
        assert commit_count(drafted) == before
        assert uncommitted(drafted) == ""

    @needs_tools
    @pytest.mark.requires_tools
    def test_the_number_the_failed_issue_gave_back_is_the_one_the_next_takes(
        self, drafted: Repo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point of the rollback: no gap for anyone to ask about."""
        with monkeypatch.context() as broken:
            render_failing(broken)
            assert billkeeper("issue", ACME_DRAFT).exit_code == 1

        result = billkeeper("issue", ACME_DRAFT)

        assert result.exit_code == 0, result.output
        assert FIRST in result.output
        assert issued_paths(drafted, FIRST)[0].is_file()

    def test_a_failed_render_leaves_a_draft_pdf_alone(
        self, drafted: Repo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The draft's own PDF goes when it is issued, so it comes back when it is not."""
        draft_pdf = draft_paths(drafted)[1]
        draft_pdf.write_bytes(b"%PDF-1.7 pretend\n")
        was = draft_pdf.read_bytes()
        render_failing(monkeypatch)

        result = billkeeper("issue", ACME_DRAFT)

        assert result.exit_code == 1
        assert draft_pdf.read_bytes() == was

    def test_a_counter_that_moved_on_is_reported_with_both_failures(
        self, drafted: Repo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An undo that cannot finish is the one thing worth interrupting to say."""
        before = commit_count(drafted)
        render_failing(monkeypatch)

        def moved_on(repo: Repo, cfg: RepoConfig, year: int, seq: int) -> None:
            # What `sequence.rollback` itself does when something else has been
            # numbered since the number was drawn: the one case it refuses.
            raise BillkeeperError("the counter stands at 99")

        monkeypatch.setattr(sequence, "rollback", moved_on)

        result = billkeeper("issue", ACME_DRAFT)

        assert result.exit_code == 1
        # Both halves reach the user: what failed, and what is left over.
        assert "Typst fell over." in result.stderr
        assert "the counter stands at 99" in result.stderr
        assert str(drafted.root) in result.stderr
        assert commit_count(drafted) == before

    def test_an_invoice_already_at_that_number_is_not_overwritten(
        self, drafted: Repo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The counter and the files have gone out of step; the files win."""
        toml, _ = issued_paths(drafted)
        toml.parent.mkdir(parents=True, exist_ok=True)
        toml.write_bytes(b"# somebody else's invoice\n")
        monkeypatch.setattr(render, "check_tools", lambda: None)

        result = billkeeper("issue", ACME_DRAFT)

        assert result.exit_code == 1
        assert toml.read_bytes() == b"# somebody else's invoice\n"
        assert draft_paths(drafted)[0].is_file()
        assert next_number(drafted) == 1


# ------------------------------------------------------------------ rendering


class TestRender:
    @needs_tools
    @pytest.mark.requires_tools
    def test_an_issued_invoice_rendered_again_is_up_to_date_and_commits_nothing(
        self, drafted: Repo
    ) -> None:
        assert billkeeper("issue", ACME_DRAFT).exit_code == 0
        _, pdf = issued_paths(drafted)
        was = pdf.read_bytes()
        before = commit_count(drafted)

        again = billkeeper("render", FIRST)
        third = billkeeper("render", FIRST)

        assert again.exit_code == 0, again.output
        assert third.exit_code == 0, third.output
        assert UP_TO_DATE_MESSAGE in again.output
        assert UP_TO_DATE_MESSAGE in third.output
        assert pdf.read_bytes() == was
        assert commit_count(drafted) == before
        assert uncommitted(drafted) == ""

    @needs_tools
    @pytest.mark.requires_tools
    def test_a_pdf_that_was_damaged_or_deleted_comes_back_as_the_bytes_git_has(
        self, drafted: Repo
    ) -> None:
        """Which leaves nothing new to record: the repo was never out of date."""
        assert billkeeper("issue", ACME_DRAFT).exit_code == 0
        _, pdf = issued_paths(drafted)
        was = pdf.read_bytes()
        pdf.write_bytes(b"not a PDF at all\n")
        before = commit_count(drafted)

        damaged = billkeeper("render", FIRST)
        pdf.unlink()
        deleted = billkeeper("render", FIRST)

        assert damaged.exit_code == 0, damaged.output
        assert deleted.exit_code == 0, deleted.output
        assert pdf.read_bytes() == was
        assert commit_count(drafted) == before
        assert uncommitted(drafted) == ""

    @needs_tools
    @pytest.mark.requires_tools
    def test_an_edited_template_is_rendered_again_and_only_the_pdf_is_committed(
        self, drafted: Repo
    ) -> None:
        """The one thing that really does change an issued invoice's PDF."""
        assert billkeeper("issue", ACME_DRAFT).exit_code == 0
        _, pdf = issued_paths(drafted)
        was = pdf.read_bytes()
        template = drafted.templates_dir / "invoice.typ"
        template.write_text(
            template.read_text(encoding="utf-8") + "\n#text(size: 8pt)[Thank you.]\n",
            encoding="utf-8",
        )
        before = commit_count(drafted)

        result = billkeeper("render", FIRST)

        assert result.exit_code == 0, result.output
        assert pdf.read_bytes() != was
        assert pdf.read_bytes().startswith(b"%PDF-")
        assert commit_count(drafted) == before + 1
        assert head_message(drafted) == f"Render {FIRST}"
        # The user's own edit to their template is theirs to commit, not the
        # command's to sweep up along the way.
        assert committed_paths(drafted) == {relative(drafted, pdf)}
        assert uncommitted(drafted).split() == ["M", "templates/invoice.typ"]

    @needs_tools
    @pytest.mark.requires_tools
    def test_a_draft_renders_beside_its_own_file(self, drafted: Repo) -> None:
        draft_toml, draft_pdf = draft_paths(drafted)
        before = commit_count(drafted)

        result = billkeeper("render", ACME_DRAFT)

        assert result.exit_code == 0, result.output
        assert draft_pdf.read_bytes().startswith(b"%PDF-")
        assert draft_toml.is_file()
        assert commit_count(drafted) == before + 1
        assert head_message(drafted) == f"Render {ACME_DRAFT}"
        assert uncommitted(drafted) == ""

    @needs_tools
    @pytest.mark.requires_tools
    def test_open_hands_the_pdf_to_the_desktop(
        self, drafted: Repo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert billkeeper("issue", ACME_DRAFT).exit_code == 0
        _, pdf = issued_paths(drafted)
        launched: list[str] = []
        monkeypatch.setattr(typer, "launch", lambda url: launched.append(url))

        result = billkeeper("render", FIRST, "--open")

        assert result.exit_code == 0, result.output
        assert launched == [str(pdf)]

    def test_a_missing_pandoc_is_reported_rather_than_traced(
        self, drafted: Repo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", without_the_render_tools)

        result = billkeeper("render", ACME_DRAFT)

        assert result.exit_code == 1
        assert "pandoc" in result.stderr
        assert "typst" in result.stderr
