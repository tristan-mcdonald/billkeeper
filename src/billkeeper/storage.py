"""The data repo on disk: clients and invoices as TOML files.

The whole store is a directory of plain text under git, so every rule here
exists to keep that directory something a person can open, read, and hand-edit
without the tool losing track of it:

    config.toml
    sequence.toml
    clients/<slug>.toml
    invoices/drafts/<draft-id>.toml
    invoices/<YYYY>/<number>.toml
    templates/

Three things this module is careful about. Writes go through a temporary file
and `os.replace`, so a crash or a full disk can never leave half an invoice
behind. Serialisation lives here rather than on the models, because it is a
property of the file format and not of the domain: `Decimal` is written as a
quoted string since TOML has no decimal type, and keys are emitted in a fixed
order so re-writing an unchanged invoice produces byte-identical output. And an
issued invoice is frozen — a write that changes anything but its status is
refused, because an invoice someone has already been sent is a record of what
was said, not a document to revise.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
import tomllib
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import tomli_w

from billkeeper.errors import (
    AlreadyExistsError,
    AmbiguousIdError,
    ImmutableInvoiceError,
    NotFoundError,
    ValidationError,
)
from billkeeper.models import Client, ClientSnapshot, Invoice, InvoiceStatus, LineItem, slugify

#: The data repo's own config, and the invoice-number counter beside it.
CONFIG_FILENAME: Final = "config.toml"
SEQUENCE_FILENAME: Final = "sequence.toml"

CLIENTS_DIRNAME: Final = "clients"
INVOICES_DIRNAME: Final = "invoices"
DRAFTS_DIRNAME: Final = "drafts"
TEMPLATES_DIRNAME: Final = "templates"

TOML_SUFFIX: Final = ".toml"
PDF_SUFFIX: Final = ".pdf"

#: The only fields an invoice may still change once it has been issued.
MUTABLE_AFTER_ISSUE: Final = frozenset({"status", "status_history"})


# ---------------------------------------------------------------- serialisation


def to_toml_dict(invoice: Invoice) -> dict[str, Any]:
    """Return `invoice` as the dict written to its file.

    The key order is fixed rather than incidental: it is what makes an unchanged
    invoice rewrite to the same bytes, so git shows no diff for a no-op write.
    """
    return _without_none(
        {
            "kind": invoice.kind,
            "number": invoice.number,
            "draft_id": invoice.draft_id,
            "currency": invoice.currency,
            "created": invoice.created,
            "issue_date": invoice.issue_date,
            "due_date": invoice.due_date,
            "payment_terms_days": invoice.payment_terms_days,
            "status": invoice.status.value,
            "notes": _text(invoice.notes),
            "references": _text(invoice.references),
            "client": _snapshot_to_toml_dict(invoice.client),
            "items": [_item_to_toml_dict(item) for item in invoice.items],
            "status_history": [
                _without_none(
                    {
                        "status": event.status.value,
                        "date": event.date,
                        "reason": _text(event.reason),
                    }
                )
                for event in invoice.status_history
            ],
        }
    )


def from_toml_dict(data: dict[str, Any]) -> Invoice:
    """Return the invoice `data` describes, as read back from its file."""
    return Invoice(**data)


def client_to_toml_dict(client: Client) -> dict[str, Any]:
    """Return `client` as the dict written to `clients/<slug>.toml`."""
    return _without_none(
        {
            "slug": client.slug,
            "name": client.name,
            "currency": client.currency,
            "email": _text(client.email),
            "address": _text(client.address),
            "contact": _text(client.contact),
            "notes": _text(client.notes),
        }
    )


def client_from_toml_dict(data: dict[str, Any]) -> Client:
    """Return the client `data` describes, as read back from its file."""
    return Client(**data)


def invoice_id(invoice: Invoice) -> str:
    """Return what the user calls this invoice: its number, or its draft id."""
    return invoice.number or invoice.draft_id or ""


def _item_to_toml_dict(item: LineItem) -> dict[str, Any]:
    # `tax` is deliberately not written: it must stay unset in v1, and an
    # explicit `tax = ` in the file would invite someone to fill it in.
    return _without_none(
        {
            "description": item.description,
            "quantity": str(item.quantity),
            "unit_price": str(item.unit_price),
            "unit": item.unit,
        }
    )


def _snapshot_to_toml_dict(client: ClientSnapshot) -> dict[str, Any]:
    return _without_none(
        {
            "slug": client.slug,
            "name": client.name,
            "email": _text(client.email),
            "address": _text(client.address),
            "contact": _text(client.contact),
        }
    )


def _without_none(data: dict[str, Any]) -> dict[str, Any]:
    """Drop the keys with no value. TOML has no null, and absence reads better."""
    return {key: value for key, value in data.items() if value is not None}


def _text(value: str | None) -> str | None:
    """Return `value` with LF line endings, the only kind stored in the repo.

    TOML's multi-line strings normalise CRLF to LF when they are read back, so
    text pasted in from a Windows editor would otherwise change under its own
    round trip and the file would never settle.
    """
    return None if value is None else value.replace("\r\n", "\n").replace("\r", "\n")


def _dumps(data: dict[str, Any]) -> str:
    return tomli_w.dumps(data, multiline_strings=True)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except OSError as exc:
        raise ValidationError(f"Cannot read {path}: {exc.strerror}.") from None
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError(f"{path} is not valid TOML: {exc}") from None


def atomic_write(path: Path, text: str) -> None:
    """Write `text` to `path` in one indivisible step.

    The content lands in a temporary file in the same directory first, so the
    final `os.replace` either publishes the whole invoice or none of it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        # `mkstemp` creates the file private to the user; the rest of the data
        # repo comes out of ordinary writes, so match them.
        temporary.chmod(0o644)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


# ------------------------------------------------------------------------ repo


@dataclass
class Repo:
    """The data repo rooted at `root`, and every path and file inside it."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser()

    # Layout ---------------------------------------------------------------

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_FILENAME

    @property
    def sequence_path(self) -> Path:
        return self.root / SEQUENCE_FILENAME

    @property
    def clients_dir(self) -> Path:
        return self.root / CLIENTS_DIRNAME

    @property
    def invoices_dir(self) -> Path:
        return self.root / INVOICES_DIRNAME

    @property
    def drafts_dir(self) -> Path:
        return self.invoices_dir / DRAFTS_DIRNAME

    @property
    def templates_dir(self) -> Path:
        return self.root / TEMPLATES_DIRNAME

    def year_dir(self, year: int) -> Path:
        """Return the directory holding the invoices issued in `year`."""
        return self.invoices_dir / f"{year:04d}"

    def client_path(self, slug: str) -> Path:
        """Return the file holding the client called `slug`."""
        return self.clients_dir / f"{slug}{TOML_SUFFIX}"

    def invoice_path(self, invoice: Invoice) -> Path:
        """Return the file `invoice` belongs in.

        A number is what files an invoice under its year; until one is assigned
        the draft id does the filing instead.
        """
        if invoice.number is not None:
            filed_under = invoice.issue_date or invoice.created
            return self.year_dir(filed_under.year) / f"{invoice.number}{TOML_SUFFIX}"
        if invoice.draft_id is None:
            raise ValidationError(
                "This draft has no draft id, so there is nowhere to file it. "
                "Take one from Repo.allocate_draft_id first."
            )
        return self.drafts_dir / f"{invoice.draft_id}{TOML_SUFFIX}"

    def pdf_path(self, invoice: Invoice) -> Path:
        """Return where `invoice`'s rendered PDF sits, beside its TOML."""
        return self.invoice_path(invoice).with_suffix(PDF_SUFFIX)

    # Clients --------------------------------------------------------------

    def client_exists(self, slug: str) -> bool:
        """Return whether there is already a client called `slug`."""
        return self.client_path(slug).is_file()

    def write_client(self, client: Client) -> Path:
        """Write `client` to its file, and return where it went."""
        path = self.client_path(client.slug)
        atomic_write(path, _dumps(client_to_toml_dict(client)))
        return path

    def read_client(self, slug: str) -> Client:
        """Return the client called `slug`."""
        path = self.client_path(slug)
        if not path.is_file():
            raise NotFoundError(f"No client named {slug!r}.")
        with _blaming(path):
            return client_from_toml_dict(_read_toml(path))

    def list_clients(self) -> list[Client]:
        """Return every client, in slug order."""
        return [self.read_client(path.stem) for path in _toml_files(self.clients_dir)]

    def allocate_slug(self, name: str) -> str:
        """Return the free slug to file a client called `name` under."""
        return _first_free(slugify(name), self.client_exists)

    # Invoices -------------------------------------------------------------

    def allocate_draft_id(self, client_slug: str, on: dt.date) -> str:
        """Return the free draft id for a draft raised for `client_slug` `on` a day."""
        base = f"{client_slug}-{on:%Y%m%d}"
        return _first_free(base, lambda i: (self.drafts_dir / f"{i}{TOML_SUFFIX}").exists())

    def write_invoice(self, invoice: Invoice) -> Path:
        """Write `invoice` to its file, refusing to change anything already issued."""
        path = self.invoice_path(invoice)
        if invoice.status != InvoiceStatus.DRAFT:
            self._refuse_frozen_change(invoice, path)
        atomic_write(path, _dumps(to_toml_dict(invoice)))
        return path

    def read_invoice_at(self, path: Path) -> Invoice:
        """Return the invoice stored at `path`."""
        if not path.is_file():
            raise NotFoundError(f"No invoice file at {path}.")
        with _blaming(path):
            return from_toml_dict(_read_toml(path))

    def list_invoices(self) -> list[Invoice]:
        """Return every invoice — numbered ones by number, then drafts by id."""
        return sorted((self.read_invoice_at(p) for p in self._invoice_files()), key=_filing_order)

    def resolve(self, ident: str) -> Invoice:
        """Return the one invoice `ident` names.

        `ident` may be an invoice number, a draft id, or enough of the front of
        either to pick one out. A prefix that fits two invoices is never
        guessed at: the candidates are listed back instead.
        """
        wanted = ident.strip()
        if not wanted:
            raise NotFoundError("Give an invoice number or draft id to look up.")

        invoices = self.list_invoices()
        for invoice in invoices:
            if invoice.number == wanted:
                return invoice
        for invoice in invoices:
            if invoice.draft_id == wanted:
                return invoice

        folded = wanted.casefold()
        candidates = [i for i in invoices if invoice_id(i).casefold().startswith(folded)]
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            raise AmbiguousIdError(
                f"{wanted!r} matches {len(candidates)} invoices: "
                f"{_listed(invoice_id(i) for i in candidates)}. Give more of the id."
            )
        raise NotFoundError(f"No invoice or draft matching {wanted!r}.")

    def delete_draft(self, invoice: Invoice) -> None:
        """Remove a draft's file, and the PDF that was rendered from it."""
        if invoice.draft_id is None:
            raise ValidationError(
                f"{invoice_id(invoice) or 'This invoice'} is not a draft, so it has no "
                "draft file to delete."
            )
        path = self.drafts_dir / f"{invoice.draft_id}{TOML_SUFFIX}"
        if not path.is_file():
            raise NotFoundError(f"No draft file at {path}.")
        _remove_with_pdf(path)

    def move_draft_to_issued(self, invoice: Invoice) -> Path:
        """File a freshly numbered `invoice` under its year, out of `drafts/`.

        This is the one write that may change a non-draft invoice's file, and it
        clears `invoice.draft_id` because the number now names the invoice. The
        numbered file is written before the draft is removed, so an interruption
        leaves the work twice over rather than not at all.
        """
        if invoice.number is None:
            raise ValidationError("An invoice needs a number before it can leave drafts/.")

        draft_id = invoice.draft_id
        draft_path = None if draft_id is None else self.drafts_dir / f"{draft_id}{TOML_SUFFIX}"
        path = self.invoice_path(invoice)
        if path.exists():
            raise AlreadyExistsError(
                f"{path} already exists, so invoice {invoice.number} is taken. "
                "The sequence counter and the invoices on disk have gone out of step."
            )

        invoice.draft_id = None
        try:
            atomic_write(path, _dumps(to_toml_dict(invoice)))
        except BaseException:
            invoice.draft_id = draft_id
            raise

        if draft_path is not None and draft_path.is_file():
            _remove_with_pdf(draft_path)
        return path

    # Internals ------------------------------------------------------------

    def _invoice_files(self) -> Iterator[Path]:
        yield from _toml_files(self.drafts_dir)
        for year in sorted(self._year_dirs()):
            yield from _toml_files(year)

    def _year_dirs(self) -> Iterator[Path]:
        if not self.invoices_dir.is_dir():
            return
        for child in self.invoices_dir.iterdir():
            if child.is_dir() and len(child.name) == 4 and child.name.isdigit():
                yield child

    def _refuse_frozen_change(self, invoice: Invoice, path: Path) -> None:
        """Raise unless the only change to an already-issued `invoice` is its status."""
        name = invoice_id(invoice) or "This invoice"
        if not path.is_file():
            raise ImmutableInvoiceError(
                f"{name} is {invoice.status.value}, so its file cannot be created by an "
                f"ordinary write; there is nothing at {path}. Issuing goes through "
                "Repo.move_draft_to_issued."
            )
        stored = to_toml_dict(self.read_invoice_at(path))
        wanted = to_toml_dict(invoice)
        changed = sorted(
            key
            for key in stored.keys() | wanted.keys()
            if key not in MUTABLE_AFTER_ISSUE and stored.get(key) != wanted.get(key)
        )
        if changed:
            raise ImmutableInvoiceError(
                f"{name} is {invoice.status.value} and can no longer be edited, but "
                f"{_listed(changed)} changed. Raise a credit note or a new invoice "
                "referencing it instead."
            )


# --------------------------------------------------------------------- helpers


@contextmanager
def _blaming(path: Path) -> Iterator[None]:
    """Re-raise a validation failure with the file that caused it named.

    A hand-edited TOML file is the usual source of one, and "unknown key
    `clint`" is only useful once the reader knows which file to open.
    """
    try:
        yield
    except ValidationError as error:
        if error.message.startswith(str(path)):
            raise
        raise ValidationError(f"{path}: {error.message}") from None


def _filing_order(invoice: Invoice) -> tuple[bool, str, str]:
    """Sort numbered invoices by number, then the drafts by draft id."""
    return (invoice.number is None, invoice.number or "", invoice.draft_id or "")


def _toml_files(directory: Path) -> list[Path]:
    return sorted(directory.glob(f"*{TOML_SUFFIX}"))


def _remove_with_pdf(path: Path) -> None:
    path.unlink()
    path.with_suffix(PDF_SUFFIX).unlink(missing_ok=True)


def _first_free(base: str, taken: Callable[[str], bool]) -> str:
    """Return `base`, or `base-2`, `base-3`, … — the first one not `taken`."""
    candidate, suffix = base, 1
    while taken(candidate):
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def _listed(names: Iterable[str]) -> str:
    return ", ".join(sorted(names))
