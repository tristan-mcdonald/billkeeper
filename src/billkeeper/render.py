"""Invoices as PDFs: Jinja2 to Markdown, Pandoc to Typst.

The pipeline has three steps and each one is somebody else's job. `build_context`
turns an invoice into plain strings — every amount already written the way the
repo's locale writes money, so no decision about rounding or currency symbols is
left to a template. The repo's own `templates/invoice.md` turns that context into
Markdown with a metadata block. Pandoc reads the Markdown, renders it through the
repo's `templates/invoice.typ`, and hands the result to Typst to make the PDF.

Both templates belong to the user. `init` copies them into the data repo once and
nothing here ever writes to them, reads a packaged copy instead of theirs, or
adds styling of its own on the way past: an invoice that looked different after
upgrading billkeeper would be a document someone has already sent changing under
them. The consequence is that a broken template is the user's to fix, so Jinja2
runs with `StrictUndefined` — a variable the template asks for and this module
does not provide stops the render and says so, rather than printing a blank space
on something expected to be paid.

Rendering the same invoice twice produces the same bytes. `SOURCE_DATE_EPOCH`
pins the creation date Typst would otherwise take from the clock, which is what
lets `render` tell "this PDF is out of date" apart from "this PDF was made at a
different time", and keeps the data repo's history free of commits that change
nothing but a timestamp.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Final

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound
from jinja2 import TemplateError as JinjaTemplateError

from billkeeper.config import RepoConfig
from billkeeper.errors import BillkeeperError, MissingToolError
from billkeeper.models import Invoice, LineItem
from billkeeper.money import Money, format_money
from billkeeper.storage import Repo, invoice_id

#: The external programs the pipeline is, in the order they run.
PANDOC: Final = "pandoc"
TYPST: Final = "typst"
REQUIRED_TOOLS: Final = (PANDOC, TYPST)

#: The template files, as named in the data repo's `templates/`.
MARKDOWN_TEMPLATE: Final = "invoice.md"
TYPST_TEMPLATE: Final = "invoice.typ"

#: How each kind of document calls itself on the page.
KIND_LABELS: Final[dict[str, str]] = {"invoice": "Invoice", "credit_note": "Credit note"}

_INSTALL_HINT: Final = (
    "Install them with 'brew install pandoc typst' on macOS, or on Linux "
    "'apt install pandoc' plus a Typst build from "
    "https://github.com/typst/typst/releases."
)


def missing_tools() -> list[str]:
    """Return which of the rendering programs are not on `PATH`."""
    return [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]


def check_tools() -> None:
    """Raise `MissingToolError` unless both rendering programs are installed."""
    missing = missing_tools()
    if not missing:
        return
    raise MissingToolError(
        f"billkeeper renders PDFs with {PANDOC} and {TYPST}, and "
        f"{_listed(missing)} {'is' if len(missing) == 1 else 'are'} not on your PATH. "
        + _INSTALL_HINT
    )


def build_context(invoice: Invoice, cfg: RepoConfig) -> dict[str, object]:
    """Return what the Markdown template has to work with.

    Every amount arrives already written out, because how much money something
    is cannot be a template's decision: `format_money` rounds to the currency's
    minor units and uses the repo's locale, so the figures in the PDF are the
    same figures every other command prints.
    """
    locale = cfg.defaults.locale

    def money(amount: Money) -> str:
        return format_money(amount, locale)

    def priced(value: Decimal) -> str:
        return money(Money(value, invoice.currency))

    def line(item: LineItem) -> dict[str, object]:
        return {
            "description": item.description,
            "quantity": str(item.quantity),
            "unit": item.unit,
            "unit_price": priced(item.unit_price),
            "line_total": money(invoice.line_total(item)),
        }

    return {
        # A draft is told apart by having no number rather than by its status,
        # because what the template has to decide is whether there is a number
        # to print where the number goes.
        "draft": invoice.number is None,
        "number": invoice.number,
        "kind": invoice.kind,
        "kind_label": KIND_LABELS.get(invoice.kind, invoice.kind),
        "status": invoice.status.value,
        "currency": invoice.currency,
        "created": invoice.created,
        "issue_date": invoice.issue_date,
        "due_date": invoice.due_date,
        "payment_terms_days": invoice.payment_terms_days,
        "references": invoice.references,
        "notes": invoice.notes,
        "locale": locale,
        "issuer": {
            "name": cfg.issuer.name,
            "address": cfg.issuer.address,
            "email": cfg.issuer.email,
            "payment_details": cfg.issuer.payment_details,
        },
        "client": {
            "slug": invoice.client.slug,
            "name": invoice.client.name,
            "email": invoice.client.email,
            "address": invoice.client.address,
            "contact": invoice.client.contact,
        },
        "items": [line(item) for item in invoice.items],
        "subtotal": money(invoice.subtotal),
        "total": money(invoice.total),
    }


def render_markdown(repo: Repo, invoice: Invoice, cfg: RepoConfig) -> str:
    """Return `invoice` as the Markdown the repo's own `invoice.md` makes of it."""
    path = repo.templates_dir / MARKDOWN_TEMPLATE
    environment = Environment(
        loader=FileSystemLoader(repo.templates_dir),
        # A template that asks for something billkeeper does not pass is a
        # mistake worth stopping for. The alternative is a gap on an invoice
        # where an amount or an address should be, which nobody notices until
        # the client does.
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        # The output is Markdown on its way to Typst, so HTML escaping would
        # corrupt every ampersand and angle bracket it touched.
        autoescape=False,
    )
    try:
        template = environment.get_template(MARKDOWN_TEMPLATE)
    except TemplateNotFound:
        raise BillkeeperError(
            f"No Markdown template at {path}. Run 'billkeeper init' against this repo "
            "to put the default templates back."
        ) from None
    try:
        return template.render(**build_context(invoice, cfg))
    except JinjaTemplateError as error:
        raise BillkeeperError(f"{path}: {error.message or error}") from error


def render_pdf(repo: Repo, invoice: Invoice, cfg: RepoConfig, out_path: Path) -> Path:
    """Render `invoice` to a PDF at `out_path`, and return where it went."""
    check_tools()
    typst_template = repo.templates_dir / TYPST_TEMPLATE
    if not typst_template.is_file():
        raise BillkeeperError(
            f"No Typst template at {typst_template}. Run 'billkeeper init' against this "
            "repo to put the default templates back."
        )

    markdown = render_markdown(repo, invoice, cfg)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # The Markdown is written beside nothing at all: it is a temporary of this
    # one render, and leaving it in the data repo would put a file in the user's
    # git status that they never asked for and cannot edit to any effect.
    with tempfile.TemporaryDirectory(prefix="billkeeper-render-") as workspace:
        source = Path(workspace) / MARKDOWN_TEMPLATE
        source.write_text(markdown, encoding="utf-8")
        completed = subprocess.run(
            [
                PANDOC,
                str(source),
                "--from=markdown",
                "--pdf-engine=typst",
                f"--template={typst_template}",
                f"--output={out_path}",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=_environment(invoice),
        )

    if completed.returncode != 0:
        raise BillkeeperError(
            f"Could not render {invoice_id(invoice) or 'this invoice'} to {out_path}. "
            f"{PANDOC} said:\n{_said(completed)}"
        )
    return out_path


def _environment(invoice: Invoice) -> dict[str, str]:
    """Return the environment Pandoc and Typst are run in.

    `SOURCE_DATE_EPOCH` is the one addition, and it is what makes re-rendering
    an unchanged invoice a no-op: without it Typst stamps the PDF with the
    moment it ran, every render differs from the last, and `render` could never
    say the PDF is already up to date. Midnight UTC rather than local midnight,
    so the bytes do not depend on where the invoice was rendered.
    """
    on = invoice.issue_date or invoice.created
    midnight = dt.datetime.combine(on, dt.time.min, tzinfo=dt.UTC)
    return os.environ | {"SOURCE_DATE_EPOCH": str(int(midnight.timestamp()))}


def _said(completed: subprocess.CompletedProcess[str]) -> str:
    """Return what Pandoc complained about, or say that it was silent."""
    return (completed.stderr or completed.stdout or "").strip() or "(nothing)"


def _listed(names: list[str]) -> str:
    """Return `names` as the English list they are read as in a sentence."""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"
