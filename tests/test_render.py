"""Tests for the PDF pipeline, run against real Pandoc and real Typst.

Nothing here is mocked. A rendering pipeline is three programs and two template
languages agreeing about quoting, indentation, and escaping, and a fake Pandoc
would agree with whatever the test author believed — which is exactly the thing
most likely to be wrong. So the tests that make a PDF shell out for real, and
are skipped with a reason when the binaries are not installed. CI installs both,
so they run there on every leg.

The rest need neither binary. Everything up to the Markdown is ordinary Python,
and that is where the money formatting, the draft mark, and the template's own
error handling live, so those are checked without leaving the process. The one
test that must never be skipped is the one about the binaries being absent: it
is the only thing standing between a user without Pandoc and a traceback.
"""

from __future__ import annotations

import datetime as dt
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from billkeeper import render
from billkeeper.config import Defaults, Issuer, RepoConfig, load_repo_config
from billkeeper.errors import BillkeeperError, MissingToolError
from billkeeper.gitrepo import git_available
from billkeeper.models import ClientSnapshot, Invoice, InvoiceStatus, LineItem, StatusEvent
from billkeeper.render import MARKDOWN_TEMPLATE, TYPST_TEMPLATE, missing_tools
from billkeeper.storage import Repo

from conftest import ISSUER_NAME, REPO_CURRENCY, REPO_LOCALE

#: The day every invoice here is raised and issued on.
TODAY = dt.date(2026, 3, 1)
DUE = dt.date(2026, 3, 31)

NUMBER = "INV-2026-0001"
DRAFT_ID = "acme-20260301"

#: A data repo is made by running `init`, which commits.
needs_git = pytest.mark.skipif(not git_available(), reason="git is not installed")

#: Shelling out to the real pipeline needs the real pipeline installed.
_MISSING = missing_tools()
needs_tools = pytest.mark.skipif(bool(_MISSING), reason=f"not installed: {', '.join(_MISSING)}")


@pytest.fixture
def cfg(repo: Repo) -> RepoConfig:
    """Return what the data repo says about itself: EUR amounts written en_GB."""
    return load_repo_config(repo.root)


def an_invoice(**overrides: Any) -> Invoice:
    """Return a two-line draft, with `overrides` applied over the top."""
    fields: dict[str, Any] = {
        "draft_id": DRAFT_ID,
        "client": ClientSnapshot(
            slug="acme",
            name="Acme Ltd",
            address="2 Acme Road\nCoyote Flats",
            contact="Wile E. Coyote",
            email="accounts@acme.invalid",
        ),
        "currency": REPO_CURRENCY,
        "created": TODAY,
        "items": [
            LineItem(
                description="Design work",
                quantity=Decimal("2.5"),
                unit_price=Decimal("80.00"),
                unit="hour",
            ),
            LineItem(
                description="Site visit",
                quantity=Decimal("1"),
                unit_price=Decimal("150.00"),
                unit="day",
            ),
        ],
    }
    return Invoice(**(fields | overrides))


def an_issued_invoice(**overrides: Any) -> Invoice:
    """Return the same invoice, numbered and issued."""
    return an_invoice(
        draft_id=None,
        number=NUMBER,
        issue_date=TODAY,
        due_date=DUE,
        status=InvoiceStatus.ISSUED,
        status_history=[StatusEvent(status=InvoiceStatus.ISSUED, date=TODAY)],
        **overrides,
    )


def a_bare_invoice() -> Invoice:
    """Return the draft `billkeeper new` writes: one example line and nothing else."""
    return an_invoice(
        client=ClientSnapshot(slug="acme", name="Acme Ltd"),
        items=[
            LineItem(
                description="Describe the work",
                quantity=Decimal("1"),
                unit_price=Decimal("0.00"),
                unit="hour",
            )
        ],
    )


def a_bare_config() -> RepoConfig:
    """Return the least a data repo can say about itself: a name and a currency."""
    return RepoConfig(
        issuer=Issuer(name=ISSUER_NAME),
        defaults=Defaults(currency=REPO_CURRENCY, locale=REPO_LOCALE),
    )


def nothing_installed(name: str) -> str | None:
    """Stand in for `shutil.which` on a machine with neither binary."""
    return None


def everything_installed(name: str) -> str | None:
    """Stand in for `shutil.which` on a machine with both."""
    return f"/usr/bin/{name}"


def only_pandoc_installed(name: str) -> str | None:
    """Stand in for `shutil.which` where the second half of the pipeline is missing."""
    return None if name == "typst" else f"/usr/bin/{name}"


def one_line(unit_price: str, quantity: str = "2") -> list[LineItem]:
    """Return the single line an invoice in an awkward currency is made of."""
    return [
        LineItem(
            description="Design work",
            quantity=Decimal(quantity),
            unit_price=Decimal(unit_price),
            unit="hour",
        )
    ]


# ------------------------------------------------------- the tools themselves


def test_check_tools_names_every_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one test here that must run on a machine with nothing installed."""
    monkeypatch.setattr(shutil, "which", nothing_installed)

    with pytest.raises(MissingToolError) as raised:
        render.check_tools()

    message = raised.value.message
    assert "pandoc" in message
    assert "typst" in message
    assert "brew install pandoc typst" in message
    assert "apt install pandoc" in message
    assert "github.com/typst/typst/releases" in message


def test_check_tools_names_only_the_binary_that_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", only_pandoc_installed)

    with pytest.raises(MissingToolError) as raised:
        render.check_tools()

    assert "typst is not on your PATH" in raised.value.message


def test_check_tools_is_quiet_when_both_are_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", everything_installed)

    render.check_tools()


# ------------------------------------------------------------- the Markdown


@needs_git
def test_markdown_carries_the_number_the_client_and_the_amounts(
    repo: Repo, cfg: RepoConfig
) -> None:
    markdown = render.render_markdown(repo, an_issued_invoice(), cfg)

    assert f"number: |-\n  {NUMBER}" in markdown
    assert "draft: false" in markdown
    assert "Acme Ltd" in markdown
    assert ISSUER_NAME in markdown
    assert "issue-date: |-\n  2026-03-01" in markdown
    assert "due-date: |-\n  2026-03-31" in markdown
    # 2.5 at 80.00 and 1 at 150.00 come to 350.00, written the en_GB way.
    assert "€200.00" in markdown
    assert "€150.00" in markdown
    assert "total: |-\n  €350.00" in markdown


@needs_git
def test_a_draft_is_marked_draft_and_carries_no_number(repo: Repo, cfg: RepoConfig) -> None:
    markdown = render.render_markdown(repo, an_invoice(), cfg)

    assert "number: |-\n  DRAFT" in markdown
    assert "draft: true" in markdown
    assert "INV-" not in markdown
    assert "issue-date:" not in markdown


@needs_git
def test_a_multi_line_address_becomes_one_yaml_entry_per_line(repo: Repo, cfg: RepoConfig) -> None:
    """The lines have to survive, and YAML has no way to hold them as one scalar."""
    markdown = render.render_markdown(repo, an_issued_invoice(), cfg)

    assert "  address:\n    - |-\n      2 Acme Road\n    - |-\n      Coyote Flats" in markdown


@needs_git
def test_a_jpy_invoice_is_written_in_whole_units(repo: Repo, cfg: RepoConfig) -> None:
    invoice = an_issued_invoice(currency="JPY", items=one_line("9000"))

    markdown = render.render_markdown(repo, invoice, cfg)

    assert "¥" in markdown
    assert "total: |-\n  JP¥18,000" in markdown
    # Yen has no minor units, so nothing on the invoice may carry any.
    assert "18,000.00" not in markdown


@needs_git
def test_a_gbp_invoice_under_en_gb_is_written_with_a_pound(repo: Repo, cfg: RepoConfig) -> None:
    invoice = an_issued_invoice(currency="GBP", items=one_line("150.00"))

    markdown = render.render_markdown(repo, invoice, cfg)

    assert cfg.defaults.locale == "en_GB"
    assert "total: |-\n  £300.00" in markdown


@needs_git
def test_an_undefined_variable_stops_the_render(repo: Repo, cfg: RepoConfig) -> None:
    """A gap on an invoice is worse than a command that refuses to finish."""
    template = repo.templates_dir / MARKDOWN_TEMPLATE
    template.write_text("---\nnumber: |-\n  {{ nope }}\n---\n", encoding="utf-8")

    with pytest.raises(BillkeeperError) as raised:
        render.render_markdown(repo, an_issued_invoice(), cfg)

    assert str(template) in raised.value.message
    assert "nope" in raised.value.message


@needs_git
def test_a_missing_markdown_template_says_which_file(repo: Repo, cfg: RepoConfig) -> None:
    (repo.templates_dir / MARKDOWN_TEMPLATE).unlink()

    with pytest.raises(BillkeeperError) as raised:
        render.render_markdown(repo, an_issued_invoice(), cfg)

    assert MARKDOWN_TEMPLATE in raised.value.message


@needs_git
def test_an_empty_optional_field_leaves_nothing_behind_in_the_yaml(repo: Repo) -> None:
    """A repo set up by answering nothing but the first question still renders."""
    markdown = render.render_markdown(repo, a_bare_invoice(), a_bare_config())

    for absent in ("address:", "email:", "contact:", "payment-details:", "references:"):
        assert absent not in markdown
    assert "## Notes" not in markdown
    assert "number: |-\n  DRAFT" in markdown


# ------------------------------------------------------------------- the PDF


@needs_git
@pytest.mark.requires_tools
@needs_tools
def test_rendering_an_issued_invoice_writes_a_pdf(repo: Repo, cfg: RepoConfig) -> None:
    invoice = an_issued_invoice()
    out = repo.year_dir(TODAY.year) / f"{NUMBER}.pdf"

    written = render.render_pdf(repo, invoice, cfg, out)

    assert written == out
    assert out.read_bytes().startswith(b"%PDF-")
    assert out.stat().st_size > 1024


@needs_git
@pytest.mark.requires_tools
@needs_tools
def test_rendering_the_same_invoice_twice_produces_the_same_bytes(
    repo: Repo, cfg: RepoConfig, tmp_path: Path
) -> None:
    """Otherwise `render` could never tell a stale PDF from a re-run one."""
    invoice = an_issued_invoice()

    first = render.render_pdf(repo, invoice, cfg, tmp_path / "first.pdf").read_bytes()
    second = render.render_pdf(repo, invoice, cfg, tmp_path / "second.pdf").read_bytes()

    assert first == second


@needs_git
@pytest.mark.requires_tools
@needs_tools
def test_a_draft_renders_with_no_number(repo: Repo, cfg: RepoConfig, tmp_path: Path) -> None:
    out = render.render_pdf(repo, an_invoice(), cfg, tmp_path / "draft.pdf")

    assert out.read_bytes().startswith(b"%PDF-")


@needs_git
@pytest.mark.requires_tools
@needs_tools
@pytest.mark.parametrize(
    ("currency", "unit_price"), [("JPY", "9000"), ("GBP", "150.00"), ("USD", "150.00")]
)
def test_every_currency_renders(
    repo: Repo, cfg: RepoConfig, tmp_path: Path, currency: str, unit_price: str
) -> None:
    """USD is here because its symbol is what Pandoc reads as a variable."""
    invoice = an_issued_invoice(currency=currency, items=one_line(unit_price))

    out = render.render_pdf(repo, invoice, cfg, tmp_path / f"{currency}.pdf")

    assert out.read_bytes().startswith(b"%PDF-")


@needs_git
@pytest.mark.requires_tools
@needs_tools
def test_awkward_text_does_not_break_the_document(
    repo: Repo, cfg: RepoConfig, tmp_path: Path
) -> None:
    """Every character here is special to YAML, to Markdown, or to Typst."""
    invoice = an_issued_invoice(
        items=[
            LineItem(
                description='Stickers: "rush job", 100% #1 [set] *and* $cash$',
                quantity=Decimal("3"),
                unit_price=Decimal("19.99"),
            )
        ],
        notes="Payable on receipt.\n\n- Half up front\n- Half on delivery",
        references="Supersedes INV-2025-0042",
    )

    out = render.render_pdf(repo, invoice, cfg, tmp_path / "awkward.pdf")

    assert out.read_bytes().startswith(b"%PDF-")


@needs_git
@pytest.mark.requires_tools
@needs_tools
def test_the_pdf_directory_is_made_if_it_is_not_there(
    repo: Repo, cfg: RepoConfig, tmp_path: Path
) -> None:
    out = tmp_path / "not" / "yet" / "there" / "invoice.pdf"

    render.render_pdf(repo, an_issued_invoice(), cfg, out)

    assert out.is_file()


@needs_git
@pytest.mark.requires_tools
@needs_tools
def test_the_draft_billkeeper_new_writes_renders_as_it_stands(repo: Repo, tmp_path: Path) -> None:
    """The first PDF a new user makes is this one, before they have filled in a thing."""
    out = render.render_pdf(repo, a_bare_invoice(), a_bare_config(), tmp_path / "bare.pdf")

    assert out.read_bytes().startswith(b"%PDF-")
    assert out.stat().st_size > 1024


@needs_git
@pytest.mark.requires_tools
@needs_tools
def test_a_broken_typst_template_is_reported_with_what_pandoc_said(
    repo: Repo, cfg: RepoConfig, tmp_path: Path
) -> None:
    (repo.templates_dir / TYPST_TEMPLATE).write_text("#let = =\n$body$\n", encoding="utf-8")

    with pytest.raises(BillkeeperError) as raised:
        render.render_pdf(repo, an_issued_invoice(), cfg, tmp_path / "broken.pdf")

    assert "pandoc said" in raised.value.message
    # Whatever Typst objected to has to reach the user; a bare "it failed"
    # leaves them with a template they cannot fix.
    assert "(nothing)" not in raised.value.message


@needs_git
def test_a_missing_typst_template_says_which_file(
    repo: Repo, cfg: RepoConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(render, "check_tools", lambda: None)
    (repo.templates_dir / TYPST_TEMPLATE).unlink()

    with pytest.raises(BillkeeperError) as raised:
        render.render_pdf(repo, an_issued_invoice(), cfg, tmp_path / "gone.pdf")

    assert TYPST_TEMPLATE in raised.value.message


# ----------------------------------------------------- the templates we ship


def test_the_shipped_typst_template_sets_both_fonts_with_fallbacks() -> None:
    """The fonts are the one thing the roadmap promises is obvious to change."""
    packaged = Path(render.__file__).parent / "templates" / TYPST_TEMPLATE
    text = packaged.read_text(encoding="utf-8")

    assert '#let body-font = ("Inter",' in text
    assert '#let mono-font = ("JetBrains Mono",' in text
    assert "#set text(font: body-font)" in text
    assert "#show raw: set text(font: mono-font)" in text
    assert "SIL Open Font License 1.1" in text
    assert "fallback" in text


@needs_git
def test_init_copies_the_packaged_templates_verbatim(repo: Repo, cfg: RepoConfig) -> None:
    """The repo's copies are the ones used, so they are the ones that must work."""
    packaged = Path(render.__file__).parent / "templates"

    for name in (MARKDOWN_TEMPLATE, TYPST_TEMPLATE):
        assert (repo.templates_dir / name).read_text(encoding="utf-8") == (
            packaged / name
        ).read_text(encoding="utf-8")
