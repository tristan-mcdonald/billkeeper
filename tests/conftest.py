"""Fixtures the CLI tests share: a home of their own, and a data repo in it.

Every test that runs a billkeeper command needs both. The tool writes to the
user's config directory and commits to a git repository, so a test that got the
isolation wrong would repoint the developer's own billkeeper at a temporary
directory and then delete it. `sandbox` is that isolation. `repo` is one data
repo standing inside it, made the way a user makes one — by running `init` and
answering its questions — so the tests exercise a repo the tool built rather
than a hand-assembled imitation of one that could drift away from the real
thing without any test noticing.

The helpers below are here for the same reason. Reading the commit log and
standing in a fake `$EDITOR` are what nearly every command test does, and a
fake editor is the only way to exercise a command that opens one without a
person at a terminal — with the pleasant side effect of testing exactly what
those commands promise, that whatever the editor leaves on disk is what gets
read back and committed.

`library` is a step further on: a repo that already holds a set of invoices
across both clients, both currencies, two years, and all four statuses, for the
commands that read invoices back or change their status rather than create them.
It is shared because `list`, `show`, `mark-paid`, and `void` all want the same
repo to work from, and a set of invoices described once is a set every test can
be read against.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Final

import pytest
from typer.testing import CliRunner

from billkeeper import clock, gitrepo
from billkeeper.cli import app
from billkeeper.config import REPO_ENV_VAR, XDG_CONFIG_HOME_ENV
from billkeeper.gitrepo import run_git
from billkeeper.models import ClientSnapshot, Invoice, InvoiceStatus, LineItem
from billkeeper.storage import Repo

#: What the `repo` fixture answers `init` with, in the order it asks.
ISSUER_NAME: Final = "Example Consulting"
ISSUER_ADDRESS: Final = "1 Example Street\nExampleton"
ISSUER_EMAIL: Final = "billing@example.invalid"
PAYMENT_DETAILS: Final = "Bank: Example Bank\nIBAN: XX00 EXAM 0000"
REPO_CURRENCY: Final = "EUR"
REPO_LOCALE: Final = "en_GB"

INIT_ANSWERS: Final = (
    ISSUER_NAME,
    ISSUER_ADDRESS.replace("\n", "\\n"),
    ISSUER_EMAIL,
    PAYMENT_DETAILS.replace("\n", "\\n"),
    REPO_CURRENCY,
    REPO_LOCALE,
    "",
)


def answered(*answers: str) -> str:
    """Return `answers` as the keystrokes a user would give the prompts."""
    return "".join(f"{answer}\n" for answer in answers)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give the test a home of its own, and cut git off from every config file."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv(XDG_CONFIG_HOME_ENV, str(home / ".config"))
    monkeypatch.delenv(REPO_ENV_VAR, raising=False)
    monkeypatch.setenv("LANG", "en_GB.UTF-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-such-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GNUPGHOME", str(tmp_path / "no-such-gnupg"))
    # A test that opens the editor without saying which one gets one that does
    # nothing, rather than a real `vi` waiting for a keystroke that never comes.
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "true")
    return home.resolve()


@pytest.fixture
def repo(tmp_path: Path, sandbox: Path) -> Repo:
    """Return a data repo freshly made by `billkeeper init`, and pointed at."""
    root = tmp_path / "data"
    result = CliRunner().invoke(app, ["init", str(root)], input=answered(*INIT_ANSWERS))
    assert result.exit_code == 0, result.output
    return Repo(root.resolve())


def commit_count(repo: Repo) -> int:
    """Return how many commits the data repo has."""
    return int(run_git(repo.root, "rev-list", "--count", "HEAD"))


def head_message(repo: Repo) -> str:
    """Return the subject line of the data repo's latest commit."""
    return run_git(repo.root, "log", "-1", "--pretty=%s")


def uncommitted(repo: Repo) -> str:
    """Return what `git status` has to say, which after a command should be nothing."""
    return run_git(repo.root, "status", "--porcelain")


def committed_paths(repo: Repo) -> set[str]:
    """Return every path the latest commit touched, added or removed.

    Renames are turned off on purpose: issuing an invoice moves a draft, and
    what has to be checked is that both the path it left and the path it
    arrived at are in the one commit.
    """
    listing = run_git(
        repo.root, "diff-tree", "--no-commit-id", "--no-renames", "--name-only", "-r", "HEAD"
    )
    return {line for line in listing.splitlines() if line}


def relative(repo: Repo, path: Path) -> str:
    """Return `path` as git names it: relative to the data repo's root."""
    return str(path.relative_to(repo.root))


def editor_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> None:
    """Stand in an `$EDITOR` that runs `body`, with the file to edit as `$1`."""
    script = tmp_path / "fake-editor.sh"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(script))


def editor_writing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str) -> None:
    """Stand in an `$EDITOR` that replaces the file with `content`."""
    heredoc = f"cat > \"$1\" <<'BILLKEEPER_EOF'\n{content}BILLKEEPER_EOF"
    editor_running(monkeypatch, tmp_path, heredoc)


# ------------------------------------------- a repo with invoices already in it

#: The day the tests that use `library` are run on.
TODAY: Final = dt.date(2026, 3, 1)

#: The two clients the shared invoice set is raised against, and the second
#: currency beside the repo's own EUR. One of them has an address, because an
#: address is the one client field that is several lines long.
ACME: Final = ("acme", "Acme Ltd")
GLOBEX: Final = ("globex", "Globex SA")
ACME_EMAIL: Final = "ap@acme.invalid"
ACME_ADDRESS: Final = "2 Client Road\nClientville"
OTHER_CURRENCY: Final = "GBP"

#: What each invoice in the shared set is known by.
OLD_DRAFT: Final = "globex-20250601"
NEW_DRAFT: Final = "acme-20260301"
LAST_YEAR: Final = "INV-2025-0001"
OUTSTANDING: Final = "INV-2026-0001"
SETTLED: Final = "INV-2026-0002"
WITHDRAWN: Final = "INV-2026-0003"

#: What the set's totals come to in the repo's own locale.
OLD_DRAFT_TOTAL: Final = "£40.00"
NEW_DRAFT_TOTAL: Final = "€350.00"
LAST_YEAR_TOTAL: Final = "€100.00"
OUTSTANDING_TOTAL: Final = "€200.00"
SETTLED_TOTAL: Final = "£500.00"
WITHDRAWN_TOTAL: Final = "£75.00"

#: Why the voided one was voided, as the invoice records it.
VOID_REASON: Final = "Raised against the wrong client"

#: The two lines the newest draft carries: 2.5 at 80.00 and 1 at 150.00, which
#: is where its 350.00 comes from. Everything else bills one line.
TWO_ITEMS: Final = (
    ("Design work", "2.5", "80.00", "hour"),
    ("Site visit", "1", "150.00", "day"),
)

#: What stands in for a rendered invoice where only its presence is in question.
PRETEND_PDF: Final = b"%PDF-1.7 pretend\n"


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> dt.date:
    """Hold the date still at `TODAY`, and return it.

    Every command that dates something takes the date from `clock.today`, so
    this one patch is what keeps a draft id, an invoice's year, and the day a
    payment is recorded from moving at midnight or at new year.
    """
    monkeypatch.setattr(clock, "today", lambda: TODAY)
    return TODAY


@pytest.fixture
def library(repo: Repo, frozen_clock: dt.date) -> Repo:
    """Return a data repo holding two clients and six invoices, all committed.

    | id              | client | currency | status | date       |  total |
    | --------------- | ------ | -------- | ------ | ---------- | ------ |
    | globex-20250601 | globex | GBP      | draft  | 2025-06-01 |  40.00 |
    | INV-2025-0001   | acme   | EUR      | issued | 2025-11-10 | 100.00 |
    | INV-2026-0002   | globex | GBP      | paid   | 2026-01-15 | 500.00 |
    | INV-2026-0001   | acme   | EUR      | issued | 2026-02-01 | 200.00 |
    | INV-2026-0003   | globex | GBP      | void   | 2026-02-10 |  75.00 |
    | acme-20260301   | acme   | EUR      | draft  | 2026-03-01 | 350.00 |

    The date is the day the invoice went out, or the day it was begun for the
    two drafts, which is what a listing sorts and filters by. In this set that
    order deliberately runs against the order of the ids, so a test asserting on
    it is asserting on something. `INV-2026-0001` was begun in 2025 and issued
    in 2026, so a `--year` filter reading the wrong one of those dates shows it
    in the wrong year; `globex-20250601` is the only invoice of any kind in 2025
    besides `INV-2025-0001`, and the only draft there.

    The invoices are written through the storage layer rather than by running
    `issue`, and the PDF beside each numbered one is a stand-in, so every test
    that uses this repo runs on a machine with no Pandoc. What these tests ask
    of a PDF is only whether it is still on disk.
    """
    add_client(*ACME, REPO_CURRENCY, "--email", ACME_EMAIL, "--address", ACME_ADDRESS)
    add_client(*GLOBEX, OTHER_CURRENCY)

    write_draft(repo, OLD_DRAFT, GLOBEX[0], OTHER_CURRENCY, dt.date(2025, 6, 1), "40.00")
    write_draft(repo, NEW_DRAFT, ACME[0], REPO_CURRENCY, TODAY, "350.00", items=TWO_ITEMS)

    take_number(
        repo,
        write_draft(repo, "acme-20251110", ACME[0], REPO_CURRENCY, dt.date(2025, 11, 10), "100.00"),
        LAST_YEAR,
        dt.date(2025, 11, 10),
    )
    # Begun in one year and issued in the next, so `--year` has something to be
    # wrong about.
    take_number(
        repo,
        write_draft(repo, "acme-20251220", ACME[0], REPO_CURRENCY, dt.date(2025, 12, 20), "200.00"),
        OUTSTANDING,
        dt.date(2026, 2, 1),
    )
    settled = take_number(
        repo,
        write_draft(
            repo, "globex-20260115", GLOBEX[0], OTHER_CURRENCY, dt.date(2026, 1, 15), "500.00"
        ),
        SETTLED,
        dt.date(2026, 1, 15),
    )
    record(repo, settled, InvoiceStatus.PAID, dt.date(2026, 2, 20))
    withdrawn = take_number(
        repo,
        write_draft(
            repo, "globex-20260210", GLOBEX[0], OTHER_CURRENCY, dt.date(2026, 2, 10), "75.00"
        ),
        WITHDRAWN,
        dt.date(2026, 2, 10),
    )
    record(repo, withdrawn, InvoiceStatus.VOID, dt.date(2026, 2, 11), reason=VOID_REASON)

    gitrepo.commit(repo.root, [repo.invoices_dir], "Add the invoices the tests work from")
    # A test that asserts nothing is left uncommitted after the command it runs
    # has to start from a clean tree, so this is where that is checked.
    assert run_git(repo.root, "status", "--porcelain") == ""
    return repo


def add_client(slug: str, name: str, currency: str, *details: str) -> None:
    """Add one client by running the command a user would, and check it took."""
    result = CliRunner().invoke(
        app,
        ["client", "add", "--slug", slug, "--name", name, "--currency", currency, *details],
    )
    assert result.exit_code == 0, result.output


def write_draft(
    repo: Repo,
    draft_id: str,
    client: str,
    currency: str,
    created: dt.date,
    amount: str,
    items: tuple[tuple[str, str, str, str], ...] = (),
) -> Invoice:
    """Write a draft for `client`, billing `amount` on one line unless given more."""
    invoice = Invoice(
        draft_id=draft_id,
        client=ClientSnapshot.of(repo.read_client(client)),
        currency=currency,
        created=created,
        items=[
            LineItem(
                description=description,
                quantity=Decimal(quantity),
                unit_price=Decimal(price),
                unit=unit,
            )
            for description, quantity, price, unit in items
            or (("Consulting", "1", amount, "item"),)
        ],
    )
    repo.write_invoice(invoice)
    return invoice


def take_number(repo: Repo, invoice: Invoice, number: str, on: dt.date) -> Invoice:
    """Issue `invoice` by hand: number it, date it, file it, and leave a PDF.

    Storage's own route rather than the `issue` command, because `issue` renders
    the PDF with Pandoc and a test about listing an invoice or marking it paid
    should not need Pandoc installed to set itself up. The sequence counter is
    left alone, since nothing that reads these invoices back consults it.
    """
    invoice.number = number
    invoice.issue_date = on
    invoice.due_date = on + dt.timedelta(days=invoice.payment_terms_days)
    invoice.transition(InvoiceStatus.ISSUED, on=on)
    repo.move_draft_to_issued(invoice)
    fake_pdf(repo, invoice)
    return invoice


def record(
    repo: Repo, invoice: Invoice, status: InvoiceStatus, on: dt.date, reason: str | None = None
) -> Invoice:
    """Move `invoice` to `status` by hand, and write it back where it lives."""
    invoice.transition(status, on=on, reason=reason)
    repo.write_invoice(invoice)
    return invoice


def fake_pdf(repo: Repo, invoice: Invoice) -> Path:
    """Put something PDF-shaped where `invoice`'s PDF belongs, and return the path."""
    path = repo.pdf_path(invoice)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PRETEND_PDF)
    return path
