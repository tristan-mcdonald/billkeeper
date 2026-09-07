"""Tests for the domain models."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from billkeeper.errors import CurrencyError, ValidationError
from billkeeper.models import (
    DEFAULT_PAYMENT_TERMS_DAYS,
    SLUG_MAX_LENGTH,
    Client,
    ClientSnapshot,
    Invoice,
    InvoiceStatus,
    LineItem,
    slugify,
)
from billkeeper.money import Money

CREATED = dt.date(2026, 3, 1)
ISSUED_ON = dt.date(2026, 3, 2)
PAID_ON = dt.date(2026, 4, 1)


def make_client(**overrides: Any) -> Client:
    fields: dict[str, Any] = {
        "slug": "asa-bjork-ltd",
        "name": "Åsa Björk Ltd",
        "email": "billing@example.invalid",
        "address": "1 Example Street\nExampleton",
        "currency": "GBP",
    }
    return Client(**(fields | overrides))


def make_item(quantity: str = "1", unit_price: str = "100.00", **overrides: Any) -> LineItem:
    fields: dict[str, Any] = {
        "description": "Consulting",
        "quantity": Decimal(quantity),
        "unit_price": Decimal(unit_price),
    }
    return LineItem(**(fields | overrides))


def make_invoice(**overrides: Any) -> Invoice:
    fields: dict[str, Any] = {
        "client": ClientSnapshot.of(make_client()),
        "currency": "USD",
        "created": CREATED,
        "items": [make_item()],
    }
    return Invoice(**(fields | overrides))


class TestSlugify:
    def test_strips_accents_to_ascii(self) -> None:
        assert slugify("Åsa Björk Ltd") == "asa-bjork-ltd"

    def test_collapses_runs_of_punctuation_to_one_hyphen(self) -> None:
        assert slugify("Foo & Bar, Inc. (UK)!") == "foo-bar-inc-uk"

    def test_trims_leading_and_trailing_separators(self) -> None:
        assert slugify("  --Hello, World--  ") == "hello-world"

    def test_truncates_to_the_maximum_length(self) -> None:
        slug = slugify("Wonderfully Overlong Consultancy Partners International")
        assert len(slug) == SLUG_MAX_LENGTH
        assert slug == "wonderfully-overlong-consultancy-partner"

    def test_truncation_never_leaves_a_trailing_hyphen(self) -> None:
        # The cut falls exactly on the separator between the two words.
        slug = slugify("a" * 39 + " b")
        assert slug == "a" * 39

    @pytest.mark.parametrize("value", ["", "   ", "!!!", "---", "日本語"])
    def test_a_name_with_nothing_to_slug_raises(self, value: str) -> None:
        with pytest.raises(ValidationError, match="no letters or digits"):
            slugify(value)


class TestClient:
    def test_normalizes_the_currency(self) -> None:
        assert make_client(currency=" gbp ").currency == "GBP"

    def test_rejects_an_invalid_currency(self) -> None:
        with pytest.raises(CurrencyError, match="not a valid currency code"):
            make_client(currency="pounds")

    def test_slug_must_already_be_a_slug(self) -> None:
        with pytest.raises(ValidationError, match="is not a slug; use 'not-a-slug'"):
            make_client(slug="Not A Slug")

    def test_name_must_say_something(self) -> None:
        with pytest.raises(ValidationError, match="name"):
            make_client(name="   ")

    def test_optional_fields_default_to_none(self) -> None:
        client = make_client()
        assert client.contact is None
        assert client.notes is None


class TestClientSnapshot:
    def test_copies_the_identifying_fields(self) -> None:
        client = make_client(contact="A. Person")
        snapshot = ClientSnapshot.of(client)
        assert snapshot.slug == client.slug
        assert snapshot.name == client.name
        assert snapshot.email == client.email
        assert snapshot.address == client.address
        assert snapshot.contact == client.contact

    def test_later_edits_to_the_client_do_not_reach_the_snapshot(self) -> None:
        client = make_client()
        snapshot = ClientSnapshot.of(client)
        client.name = "Renamed Ltd"
        assert snapshot.name == "Åsa Björk Ltd"

    def test_does_not_carry_the_client_currency_or_notes(self) -> None:
        assert "currency" not in ClientSnapshot.model_fields
        assert "notes" not in ClientSnapshot.model_fields


class TestLineItem:
    def test_tax_defaults_to_none(self) -> None:
        assert make_item().tax is None

    def test_tax_may_not_be_given_a_value(self) -> None:
        with pytest.raises(ValidationError, match="tax"):
            make_item(tax=Decimal("0.20"))

    @pytest.mark.parametrize("field", ["quantity", "unit_price"])
    def test_rejects_a_float(self, field: str) -> None:
        fields: dict[str, Any] = {
            "description": "Consulting",
            "quantity": Decimal(1),
            "unit_price": Decimal(1),
            field: 1.5,
        }
        with pytest.raises(ValidationError, match="must not be the float"):
            LineItem(**fields)

    def test_description_must_say_something(self) -> None:
        with pytest.raises(ValidationError, match="description"):
            make_item(description="  ")

    def test_unit_is_optional(self) -> None:
        assert make_item().unit is None
        assert make_item(unit="hour").unit == "hour"


class TestTotals:
    def test_line_total_is_quantity_times_unit_price(self) -> None:
        invoice = make_invoice(items=[make_item(quantity="7.5", unit_price="80.00")])
        assert invoice.line_total(invoice.items[0]) == Money("600.00", "USD")

    def test_each_line_is_rounded_before_it_is_summed(self) -> None:
        # Three lines that each come to exactly 0.005: rounded per line they are
        # a cent each, so the invoice says 0.03. Rounding the raw sum says 0.02.
        items = [make_item(quantity="0.5", unit_price="0.01") for _ in range(3)]
        invoice = make_invoice(items=items)
        raw = sum((item.quantity * item.unit_price for item in items), Decimal(0))
        assert Money(raw, "USD") == Money("0.02", "USD")
        assert invoice.subtotal == Money("0.03", "USD")

    def test_total_matches_subtotal_while_there_is_no_tax(self) -> None:
        invoice = make_invoice(items=[make_item(quantity="3", unit_price="19.99")])
        assert invoice.total == invoice.subtotal == Money("59.97", "USD")

    def test_a_jpy_invoice_totals_in_whole_yen(self) -> None:
        invoice = make_invoice(currency="JPY", items=[make_item(quantity="3", unit_price="1234.5")])
        assert invoice.total == Money("3704", "JPY")
        assert invoice.total.amount == Decimal("3704")

    def test_an_invoice_with_no_lines_totals_zero(self) -> None:
        assert make_invoice(items=[]).total == Money.zero("USD")

    def test_totals_use_the_invoice_currency_not_the_client_one(self) -> None:
        invoice = make_invoice(currency="eur")
        assert invoice.currency == "EUR"
        assert invoice.total.currency == "EUR"


LEGAL_TRANSITIONS = [
    (InvoiceStatus.DRAFT, InvoiceStatus.ISSUED, None),
    (InvoiceStatus.DRAFT, InvoiceStatus.VOID, "raised by mistake"),
    (InvoiceStatus.ISSUED, InvoiceStatus.PAID, None),
    (InvoiceStatus.ISSUED, InvoiceStatus.VOID, "client cancelled"),
]

ILLEGAL_TRANSITIONS = [
    (InvoiceStatus.DRAFT, InvoiceStatus.PAID),
    (InvoiceStatus.DRAFT, InvoiceStatus.DRAFT),
    (InvoiceStatus.ISSUED, InvoiceStatus.DRAFT),
    (InvoiceStatus.ISSUED, InvoiceStatus.ISSUED),
    (InvoiceStatus.PAID, InvoiceStatus.ISSUED),
    (InvoiceStatus.PAID, InvoiceStatus.VOID),
    (InvoiceStatus.VOID, InvoiceStatus.DRAFT),
    (InvoiceStatus.VOID, InvoiceStatus.ISSUED),
    (InvoiceStatus.VOID, InvoiceStatus.PAID),
]


class TestTransitions:
    @pytest.mark.parametrize(("start", "target", "reason"), LEGAL_TRANSITIONS)
    def test_legal_transitions(
        self, start: InvoiceStatus, target: InvoiceStatus, reason: str | None
    ) -> None:
        invoice = make_invoice(status=start)
        invoice.transition(target, ISSUED_ON, reason=reason)
        assert invoice.status is target
        assert invoice.status_history[-1].status is target
        assert invoice.status_history[-1].date == ISSUED_ON
        assert invoice.status_history[-1].reason == reason

    @pytest.mark.parametrize(("start", "target"), ILLEGAL_TRANSITIONS)
    def test_illegal_transitions_name_both_statuses(
        self, start: InvoiceStatus, target: InvoiceStatus
    ) -> None:
        invoice = make_invoice(status=start)
        with pytest.raises(ValidationError) as caught:
            invoice.transition(target, ISSUED_ON, reason="because")
        assert start.value in caught.value.message
        assert target.value in caught.value.message
        assert invoice.status is start
        assert invoice.status_history == []

    @pytest.mark.parametrize("reason", [None, "", "   "])
    def test_voiding_without_a_reason_raises(self, reason: str | None) -> None:
        invoice = make_invoice(status=InvoiceStatus.ISSUED)
        with pytest.raises(ValidationError, match="needs a reason"):
            invoice.transition(InvoiceStatus.VOID, ISSUED_ON, reason=reason)
        assert invoice.status is InvoiceStatus.ISSUED

    def test_the_whole_lifecycle_is_recorded_in_order(self) -> None:
        invoice = make_invoice()
        invoice.transition(InvoiceStatus.ISSUED, ISSUED_ON)
        invoice.transition(InvoiceStatus.PAID, PAID_ON)
        assert invoice.status is InvoiceStatus.PAID
        assert [event.status for event in invoice.status_history] == [
            InvoiceStatus.ISSUED,
            InvoiceStatus.PAID,
        ]
        assert [event.date for event in invoice.status_history] == [ISSUED_ON, PAID_ON]

    @pytest.mark.parametrize(
        ("status", "editable"),
        [
            (InvoiceStatus.DRAFT, True),
            (InvoiceStatus.ISSUED, False),
            (InvoiceStatus.PAID, False),
            (InvoiceStatus.VOID, False),
        ],
    )
    def test_only_drafts_are_editable(self, status: InvoiceStatus, editable: bool) -> None:
        assert make_invoice(status=status).is_editable is editable


class TestValidateIssuable:
    def test_a_complete_draft_is_issuable(self) -> None:
        make_invoice().validate_issuable()

    def test_an_invoice_with_no_lines_is_not(self) -> None:
        with pytest.raises(ValidationError, match="at least one line item"):
            make_invoice(items=[]).validate_issuable()

    def test_an_already_numbered_invoice_is_not(self) -> None:
        with pytest.raises(ValidationError, match="already numbered INV-2026-0001"):
            make_invoice(number="INV-2026-0001").validate_issuable()

    def test_a_negative_total_invoice_is_not(self) -> None:
        invoice = make_invoice(items=[make_item(quantity="-1", unit_price="10.00")])
        with pytest.raises(ValidationError, match="credit note"):
            invoice.validate_issuable()

    def test_a_negative_total_credit_note_is(self) -> None:
        invoice = make_invoice(
            kind="credit_note",
            references="INV-2026-0001",
            items=[make_item(quantity="-1", unit_price="10.00")],
        )
        invoice.validate_issuable()
        assert invoice.total == Money("-10.00", "USD")


class TestInvoiceDefaults:
    def test_a_new_invoice_is_an_unnumbered_draft(self) -> None:
        invoice = make_invoice()
        assert invoice.kind == "invoice"
        assert invoice.number is None
        assert invoice.draft_id is None
        assert invoice.status is InvoiceStatus.DRAFT
        assert invoice.status_history == []
        assert invoice.issue_date is None
        assert invoice.due_date is None
        assert invoice.payment_terms_days == DEFAULT_PAYMENT_TERMS_DAYS

    def test_status_history_is_not_shared_between_invoices(self) -> None:
        first, second = make_invoice(), make_invoice()
        first.transition(InvoiceStatus.ISSUED, ISSUED_ON)
        assert second.status_history == []

    def test_kind_is_limited_to_invoice_and_credit_note(self) -> None:
        with pytest.raises(ValidationError, match="kind"):
            make_invoice(kind="receipt")


class TestDraftId:
    def test_a_draft_may_be_named_by_a_draft_id(self) -> None:
        assert make_invoice(draft_id="acme-ltd-20260301-2").draft_id == "acme-ltd-20260301-2"

    @pytest.mark.parametrize(
        "value",
        ["acme/20260301", "acme 20260301", "Acme-20260301", "-acme", "acme--20260301", ""],
    )
    def test_a_draft_id_that_could_not_be_a_filename_is_refused(self, value: str) -> None:
        with pytest.raises(ValidationError, match="draft_id"):
            make_invoice(draft_id=value)

    def test_an_invoice_is_never_both_a_draft_and_a_number(self) -> None:
        with pytest.raises(ValidationError, match="cannot be both"):
            make_invoice(number="INV-2026-0001", draft_id="acme-20260301")


class TestUnknownKeys:
    def test_client_rejects_an_unknown_field(self) -> None:
        with pytest.raises(ValidationError, match="vat_number: Extra inputs are not permitted"):
            make_client(vat_number="GB123")

    def test_line_item_rejects_an_unknown_field(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            make_item(discount="10%")

    def test_invoice_rejects_an_unknown_field(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            make_invoice(paid_on=PAID_ON)

    def test_a_typo_nested_in_parsed_data_is_reported(self) -> None:
        # What a hand-edited TOML file looks like once it has been read.
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            Invoice.model_validate(
                {
                    "client": {"slug": "acme", "name": "Acme", "e-mail": "a@example.invalid"},
                    "currency": "USD",
                    "created": "2026-03-01",
                }
            )
