"""Clients, line items, invoices, and the rules that bind them.

Pure domain. Nothing here touches the filesystem, git, or the terminal; storage
and the CLI are layered on top of these models later.

Two rules shape the whole module. Every model forbids unknown keys, because the
data repo is a directory of hand-editable TOML files and a mistyped key is a
mistake the user wants told about rather than a field silently dropped. And
every failure surfaces as `billkeeper.errors.ValidationError`, so the CLI keeps
one place to catch problems and print a single clear line.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic import ValidationError as PydanticValidationError
from pydantic.functional_validators import ModelWrapValidatorHandler

from billkeeper.errors import ValidationError
from billkeeper.money import Money, normalize_currency

#: How long a slug may be before it is cut short.
SLUG_MAX_LENGTH: Final = 40

#: Days to pay, when nothing in the data repo's config says otherwise.
DEFAULT_PAYMENT_TERMS_DAYS: Final = 30

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Return `value` as the lowercase ASCII slug that names its file."""
    ascii_only = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG.sub("-", ascii_only.lower()).strip("-")
    slug = slug[:SLUG_MAX_LENGTH].rstrip("-")
    if not slug:
        raise ValidationError(
            f"{value!r} has no letters or digits to build a slug from; "
            "give a name containing at least one."
        )
    return slug


class InvoiceStatus(StrEnum):
    """Where an invoice has got to."""

    DRAFT = "draft"
    ISSUED = "issued"
    PAID = "paid"
    VOID = "void"


#: The only status changes an invoice may make. Everything else is a mistake.
LEGAL_TRANSITIONS: Final[dict[InvoiceStatus, frozenset[InvoiceStatus]]] = {
    InvoiceStatus.DRAFT: frozenset({InvoiceStatus.ISSUED, InvoiceStatus.VOID}),
    InvoiceStatus.ISSUED: frozenset({InvoiceStatus.PAID, InvoiceStatus.VOID}),
    InvoiceStatus.PAID: frozenset(),
    InvoiceStatus.VOID: frozenset(),
}


def _reject_float(value: object) -> object:
    if isinstance(value, float):
        raise ValueError(
            f"must not be the float {value!r}; pass a string, integer, or Decimal "
            "so the amount stays exact"
        )
    return value


def _normalize_currency(value: object) -> object:
    return normalize_currency(value) if isinstance(value, str) else value


#: A `Decimal` that refuses to be built from a float.
ExactDecimal = Annotated[Decimal, BeforeValidator(_reject_float)]

#: Free text that has to say something, trimmed of surrounding whitespace.
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

#: An ISO 4217 code, upper-cased and checked on the way in.
CurrencyCode = Annotated[str, BeforeValidator(_normalize_currency)]


def _describe(model: type[BaseModel], error: PydanticValidationError) -> str:
    problems = []
    for detail in error.errors():
        where = ".".join(str(part) for part in detail["loc"])
        # Pydantic prefixes messages raised from our own validators; the user
        # only needs to read what went wrong.
        message = detail["msg"].removeprefix("Value error, ")
        problems.append(f"{where}: {message}" if where else message)
    return f"{model.__name__}: " + "; ".join(problems)


class BillkeeperModel(BaseModel):
    """A model that forbids unknown keys and reports failures as billkeeper errors."""

    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)

    @model_validator(mode="wrap")
    @classmethod
    def _as_billkeeper_error(cls, data: Any, handler: ModelWrapValidatorHandler[Self]) -> Self:
        try:
            return handler(data)
        except PydanticValidationError as error:
            raise ValidationError(_describe(cls, error)) from None


class StatusEvent(BillkeeperModel):
    """One recorded change of status, and why it happened."""

    status: InvoiceStatus
    date: dt.date
    reason: str | None = None


class Client(BillkeeperModel):
    """Someone you invoice, as stored in `clients/<slug>.toml`."""

    slug: str
    name: Text
    email: str | None = None
    address: str | None = None
    contact: str | None = None
    currency: CurrencyCode
    notes: str | None = None

    @field_validator("slug")
    @classmethod
    def _slug_is_a_slug(cls, value: str) -> str:
        expected = slugify(value)
        if value != expected:
            raise ValueError(f"{value!r} is not a slug; use {expected!r}")
        return value


class ClientSnapshot(BillkeeperModel):
    """The client's details as they stood when the draft was created.

    Copied onto the invoice so an issued invoice stays self-contained: editing a
    client later must never rewrite what an already-sent invoice said. The
    client's `currency` is not copied because the invoice carries its own, and
    `notes` are not copied because they are yours, not the client's.
    """

    slug: str
    name: Text
    email: str | None = None
    address: str | None = None
    contact: str | None = None

    @classmethod
    def of(cls, client: Client) -> ClientSnapshot:
        """Return the snapshot to record on an invoice raised for `client`."""
        return cls(
            slug=client.slug,
            name=client.name,
            email=client.email,
            address=client.address,
            contact=client.contact,
        )


class LineItem(BillkeeperModel):
    """One billable line: what it was, how much of it, and at what price."""

    description: Text
    quantity: ExactDecimal
    unit_price: ExactDecimal
    unit: str | None = None
    tax: None = None
    """The extension point for tax, which is out of scope for v1.

    It must stay `None`: nothing in the codebase calculates tax, so a value here
    would be quietly ignored on a document someone is expected to pay.
    """


class Invoice(BillkeeperModel):
    """An invoice or credit note, in exactly one currency."""

    kind: Literal["invoice", "credit_note"] = "invoice"
    number: str | None = None
    client: ClientSnapshot
    currency: CurrencyCode
    created: dt.date
    issue_date: dt.date | None = None
    due_date: dt.date | None = None
    payment_terms_days: Annotated[int, Field(ge=0)] = DEFAULT_PAYMENT_TERMS_DAYS
    items: list[LineItem] = Field(default_factory=list)
    notes: str | None = None
    references: str | None = None
    status: InvoiceStatus = InvoiceStatus.DRAFT
    status_history: list[StatusEvent] = Field(default_factory=list)

    def line_total(self, item: LineItem) -> Money:
        """Return what `item` comes to, rounded to the invoice's currency."""
        return Money(item.quantity * item.unit_price, self.currency)

    @property
    def subtotal(self) -> Money:
        """Return the sum of the line totals.

        Each line is rounded before it is added, which is what the printed
        invoice shows; rounding the raw sum instead can differ by a penny.
        """
        running = Money.zero(self.currency)
        for item in self.items:
            running = running + self.line_total(item)
        return running

    @property
    def total(self) -> Money:
        """Return what is owed. The same as `subtotal` until tax exists."""
        return self.subtotal

    @property
    def is_editable(self) -> bool:
        """Return whether the invoice may still be changed. Drafts only."""
        return self.status == InvoiceStatus.DRAFT

    def transition(self, to: InvoiceStatus, on: dt.date, reason: str | None = None) -> None:
        """Move the invoice to `to`, recording the date and any reason."""
        if to not in LEGAL_TRANSITIONS[self.status]:
            raise ValidationError(f"An invoice cannot go from {self.status.value} to {to.value}.")
        if to == InvoiceStatus.VOID and not (reason and reason.strip()):
            raise ValidationError("Voiding an invoice needs a reason.")
        self.status = to
        self.status_history.append(StatusEvent(status=to, date=on, reason=reason))

    def validate_issuable(self) -> None:
        """Raise `ValidationError` unless the invoice is ready to be issued."""
        if not self.items:
            raise ValidationError(
                "An invoice needs at least one line item before it can be issued."
            )
        if self.number is not None:
            raise ValidationError(
                f"This invoice is already numbered {self.number}; it cannot be issued twice."
            )
        if self.total < Money.zero(self.currency) and self.kind != "credit_note":
            raise ValidationError(
                f"An invoice cannot total {self.total.amount} {self.currency}. "
                "Raise a credit note instead."
            )
