"""Tests for the TOML storage layer.

Everything runs against a throwaway data repo under `tmp_path`. The cases that
matter most are the ones a user would only notice long after the fact: an
amount that came back a hundredth out, an issued invoice that quietly changed,
or a rewrite that churned the file for no reason.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from billkeeper.errors import (
    AlreadyExistsError,
    AmbiguousIdError,
    ImmutableInvoiceError,
    NotFoundError,
    ValidationError,
)
from billkeeper.models import Client, ClientSnapshot, Invoice, InvoiceStatus, LineItem
from billkeeper.money import Money
from billkeeper.storage import (
    Repo,
    client_to_toml_dict,
    from_toml_dict,
    invoice_id,
    to_toml_dict,
)

CREATED = dt.date(2026, 3, 1)
ISSUED_ON = dt.date(2026, 3, 2)
ADDRESS = "1 Example Street\nExampleton\nEX1 2AB"

MINIMAL_CONFIG = """\
[issuer]
name = "Example Consulting"

[defaults]
currency = "EUR"
"""


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    """Return a bare data repo: the directory tree and a `config.toml`."""
    root = tmp_path / "data"
    repo = Repo(root)
    for directory in (repo.clients_dir, repo.drafts_dir, repo.templates_dir):
        directory.mkdir(parents=True)
    repo.config_path.write_text(MINIMAL_CONFIG, encoding="utf-8")
    return repo


def make_client(**overrides: Any) -> Client:
    fields: dict[str, Any] = {
        "slug": "asa-bjork-ltd",
        "name": "Åsa Björk Ltd",
        "email": "billing@example.invalid",
        "address": ADDRESS,
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


def make_draft(**overrides: Any) -> Invoice:
    fields: dict[str, Any] = {
        "draft_id": "asa-bjork-ltd-20260301",
        "client": ClientSnapshot.of(make_client()),
        "currency": "GBP",
        "created": CREATED,
        "items": [make_item()],
    }
    return Invoice(**(fields | overrides))


def issue(repo: Repo, draft: Invoice, number: str, on: dt.date = ISSUED_ON) -> Invoice:
    """Take `draft` through the one legitimate route to an issued file."""
    repo.write_invoice(draft)
    draft.issue_date = on
    draft.due_date = on + dt.timedelta(days=draft.payment_terms_days)
    draft.number = number
    draft.transition(InvoiceStatus.ISSUED, on=on)
    repo.move_draft_to_issued(draft)
    return draft


class TestLayout:
    def test_paths_follow_the_documented_tree(self, repo: Repo) -> None:
        assert repo.clients_dir == repo.root / "clients"
        assert repo.invoices_dir == repo.root / "invoices"
        assert repo.drafts_dir == repo.root / "invoices" / "drafts"
        assert repo.templates_dir == repo.root / "templates"
        assert repo.year_dir(2026) == repo.root / "invoices" / "2026"
        assert repo.config_path == repo.root / "config.toml"
        assert repo.sequence_path == repo.root / "sequence.toml"
        assert repo.client_path("acme") == repo.root / "clients" / "acme.toml"

    def test_a_draft_is_filed_under_its_draft_id(self, repo: Repo) -> None:
        draft = make_draft()
        assert repo.invoice_path(draft) == repo.drafts_dir / "asa-bjork-ltd-20260301.toml"
        assert repo.pdf_path(draft) == repo.drafts_dir / "asa-bjork-ltd-20260301.pdf"

    def test_an_issued_invoice_is_filed_under_the_year_it_was_issued(self, repo: Repo) -> None:
        issued = issue(repo, make_draft(), "INV-2026-0001")
        assert repo.invoice_path(issued) == repo.year_dir(2026) / "INV-2026-0001.toml"
        assert repo.pdf_path(issued) == repo.year_dir(2026) / "INV-2026-0001.pdf"

    def test_a_draft_without_an_id_has_nowhere_to_go(self, repo: Repo) -> None:
        with pytest.raises(ValidationError, match="allocate_draft_id"):
            repo.invoice_path(make_draft(draft_id=None))

    def test_the_home_directory_is_expanded(self) -> None:
        assert Repo(Path("~/billkeeper")).root == Path.home() / "billkeeper"


class TestClients:
    def test_round_trip_keeps_accents_and_line_breaks(self, repo: Repo) -> None:
        client = make_client(contact="Åsa", notes="Pays\nlate")
        repo.write_client(client)

        assert repo.read_client("asa-bjork-ltd") == client
        assert repo.read_client("asa-bjork-ltd").address == ADDRESS

    def test_a_multi_line_address_is_written_as_readable_toml(self, repo: Repo) -> None:
        repo.write_client(make_client())
        written = repo.client_path("asa-bjork-ltd").read_text(encoding="utf-8")

        assert "1 Example Street\nExampleton\nEX1 2AB" in written

    def test_carriage_returns_are_normalised_so_the_file_settles(self, repo: Repo) -> None:
        repo.write_client(make_client(address="1 Example Street\r\nExampleton"))
        stored = repo.read_client("asa-bjork-ltd")

        assert stored.address == "1 Example Street\nExampleton"
        first = repo.client_path("asa-bjork-ltd").read_bytes()
        repo.write_client(stored)
        assert repo.client_path("asa-bjork-ltd").read_bytes() == first

    def test_an_unknown_client_names_the_slug(self, repo: Repo) -> None:
        with pytest.raises(NotFoundError, match="acme"):
            repo.read_client("acme")

    def test_clients_are_listed_in_slug_order(self, repo: Repo) -> None:
        for slug in ("zebra-co", "acme", "middle-ltd"):
            repo.write_client(make_client(slug=slug, name=slug.title()))

        assert [c.slug for c in repo.list_clients()] == ["acme", "middle-ltd", "zebra-co"]

    def test_listing_an_empty_repo_is_empty(self, repo: Repo) -> None:
        assert repo.list_clients() == []

    def test_allocate_slug_steps_around_collisions(self, repo: Repo) -> None:
        assert repo.allocate_slug("Acme Ltd") == "acme-ltd"

        repo.write_client(make_client(slug="acme-ltd", name="Acme Ltd"))
        assert repo.allocate_slug("Acme Ltd") == "acme-ltd-2"

        repo.write_client(make_client(slug="acme-ltd-2", name="Acme Ltd"))
        assert repo.allocate_slug("ACME, Ltd.") == "acme-ltd-3"

    def test_an_unknown_key_is_refused_with_the_file_named(self, repo: Repo) -> None:
        path = repo.client_path("acme")
        path.write_text('slug = "acme"\nname = "Acme"\ncurrency = "GBP"\nclint = "typo"\n')

        with pytest.raises(ValidationError) as caught:
            repo.read_client("acme")
        assert str(path) in caught.value.message
        assert "clint" in caught.value.message

    def test_the_toml_dict_omits_what_is_unset(self) -> None:
        data = client_to_toml_dict(make_client(email=None, contact=None))

        assert set(data) == {"slug", "name", "currency", "address"}


class TestInvoiceRoundTrip:
    def test_exact_amounts_survive_the_file(self, repo: Repo) -> None:
        draft = make_draft(items=[make_item(quantity="0.125", unit_price="1234.005")])
        repo.write_invoice(draft)

        item = repo.read_invoice_at(repo.invoice_path(draft)).items[0]
        assert item.unit_price == Decimal("1234.005")
        assert item.quantity == Decimal("0.125")
        assert str(item.unit_price) == "1234.005"

    def test_amounts_are_written_as_strings_not_toml_floats(self, repo: Repo) -> None:
        draft = make_draft(items=[make_item(quantity="0.125", unit_price="1234.005")])
        repo.write_invoice(draft)

        written = repo.invoice_path(draft).read_text(encoding="utf-8")
        assert 'quantity = "0.125"' in written
        assert 'unit_price = "1234.005"' in written

    def test_a_toml_float_amount_is_refused(self, repo: Repo) -> None:
        path = repo.drafts_dir / "acme-20260301.toml"
        path.write_text(
            'draft_id = "acme-20260301"\n'
            'currency = "GBP"\n'
            "created = 2026-03-01\n"
            'items = [{ description = "Work", quantity = 2.5, unit_price = "10.00" }]\n'
            '[client]\nslug = "acme"\nname = "Acme"\n',
            encoding="utf-8",
        )

        with pytest.raises(ValidationError, match="float"):
            repo.read_invoice_at(path)

    def test_a_zero_decimal_currency_round_trips(self, repo: Repo) -> None:
        draft = make_draft(
            currency="JPY",
            items=[make_item(quantity="2", unit_price="1500")],
        )
        repo.write_invoice(draft)

        stored = repo.read_invoice_at(repo.invoice_path(draft))
        assert stored == draft
        assert stored.total == Money("3000", "JPY")

    def test_every_field_survives_the_file(self, repo: Repo) -> None:
        issued = issue(repo, make_draft(notes="Thank you.\nPay soon."), "INV-2026-0001")
        issued.transition(InvoiceStatus.VOID, on=dt.date(2026, 4, 1), reason="Wrong client")
        repo.write_invoice(issued)

        assert repo.read_invoice_at(repo.invoice_path(issued)) == issued

    def test_rewriting_an_unchanged_invoice_changes_no_bytes(self, repo: Repo) -> None:
        draft = make_draft(notes="Thank you.\nPay soon.", references="INV-2025-0009")
        path = repo.write_invoice(draft)
        first = path.read_bytes()

        repo.write_invoice(repo.read_invoice_at(path))
        assert path.read_bytes() == first

    def test_an_unknown_key_is_refused_with_the_file_named(self, repo: Repo) -> None:
        path = repo.write_invoice(make_draft())
        path.write_text(path.read_text(encoding="utf-8") + 'clint = "typo"\n', encoding="utf-8")

        with pytest.raises(ValidationError) as caught:
            repo.read_invoice_at(path)
        assert str(path) in caught.value.message
        assert "clint" in caught.value.message

    def test_broken_toml_names_the_file(self, repo: Repo) -> None:
        path = repo.drafts_dir / "broken.toml"
        path.write_text("this is not toml", encoding="utf-8")

        with pytest.raises(ValidationError, match="not valid TOML"):
            repo.read_invoice_at(path)

    def test_reading_nothing_says_so(self, repo: Repo) -> None:
        with pytest.raises(NotFoundError, match=r"missing\.toml"):
            repo.read_invoice_at(repo.drafts_dir / "missing.toml")

    def test_the_serialised_dict_has_no_tax_key(self) -> None:
        data = to_toml_dict(make_draft())

        assert "tax" not in data["items"][0]
        assert from_toml_dict(data) == make_draft()


class TestAtomicWrites:
    def test_no_temporary_file_is_left_behind(self, repo: Repo) -> None:
        repo.write_invoice(make_draft())
        repo.write_client(make_client())

        assert [p.name for p in repo.drafts_dir.iterdir()] == ["asa-bjork-ltd-20260301.toml"]
        assert [p.name for p in repo.clients_dir.iterdir()] == ["asa-bjork-ltd.toml"]

    def test_writing_creates_the_directories_it_needs(self, tmp_path: Path) -> None:
        repo = Repo(tmp_path / "fresh")
        path = repo.write_invoice(make_draft())

        assert path.is_file()


class TestDraftIds:
    def test_a_draft_id_is_the_slug_and_the_day(self, repo: Repo) -> None:
        assert repo.allocate_draft_id("acme", CREATED) == "acme-20260301"

    def test_draft_ids_step_around_collisions(self, repo: Repo) -> None:
        for expected in ("acme-20260301", "acme-20260301-2", "acme-20260301-3"):
            allocated = repo.allocate_draft_id("acme", CREATED)
            assert allocated == expected
            repo.write_invoice(make_draft(draft_id=allocated))

    def test_a_different_day_starts_again(self, repo: Repo) -> None:
        repo.write_invoice(make_draft(draft_id="acme-20260301"))

        assert repo.allocate_draft_id("acme", dt.date(2026, 3, 2)) == "acme-20260302"


class TestListing:
    def test_numbered_invoices_come_first_in_number_order(self, repo: Repo) -> None:
        issue(repo, make_draft(draft_id="acme-20260301"), "INV-2026-0002")
        issue(repo, make_draft(draft_id="acme-20260302"), "INV-2026-0001")
        repo.write_invoice(make_draft(draft_id="zebra-20260301"))
        repo.write_invoice(make_draft(draft_id="acme-20260303"))

        assert [invoice_id(i) for i in repo.list_invoices()] == [
            "INV-2026-0001",
            "INV-2026-0002",
            "acme-20260303",
            "zebra-20260301",
        ]

    def test_every_year_is_walked(self, repo: Repo) -> None:
        issue(repo, make_draft(), "INV-2025-0001", on=dt.date(2025, 12, 31))
        issue(repo, make_draft(draft_id="acme-20260301"), "INV-2026-0001")

        assert repo.year_dir(2025).is_dir()
        assert [invoice_id(i) for i in repo.list_invoices()] == ["INV-2025-0001", "INV-2026-0001"]

    def test_stray_files_are_ignored(self, repo: Repo) -> None:
        repo.write_invoice(make_draft())
        (repo.drafts_dir / "asa-bjork-ltd-20260301.pdf").write_bytes(b"%PDF-")
        (repo.invoices_dir / "notes.txt").write_text("scratch", encoding="utf-8")

        assert len(repo.list_invoices()) == 1

    def test_an_empty_repo_lists_nothing(self, repo: Repo) -> None:
        assert repo.list_invoices() == []


class TestResolve:
    @pytest.fixture
    def populated(self, repo: Repo) -> Repo:
        issue(repo, make_draft(draft_id="acme-20260301"), "INV-2026-0001")
        issue(repo, make_draft(draft_id="acme-20260302"), "INV-2026-0011")
        repo.write_invoice(make_draft(draft_id="acme-20260303"))
        repo.write_invoice(make_draft(draft_id="acme-20260304"))
        repo.write_invoice(make_draft(draft_id="zebra-20260303"))
        return repo

    def test_an_exact_number_wins(self, populated: Repo) -> None:
        assert populated.resolve("INV-2026-0001").number == "INV-2026-0001"

    def test_an_exact_draft_id_wins(self, populated: Repo) -> None:
        assert populated.resolve("acme-20260303").draft_id == "acme-20260303"

    def test_a_unique_prefix_is_enough(self, populated: Repo) -> None:
        assert populated.resolve("INV-2026-001").number == "INV-2026-0011"
        assert populated.resolve("zeb").draft_id == "zebra-20260303"

    def test_a_prefix_ignores_case(self, populated: Repo) -> None:
        assert populated.resolve("inv-2026-0001").number == "INV-2026-0001"

    def test_a_shared_prefix_of_two_drafts_lists_the_candidates(self, populated: Repo) -> None:
        with pytest.raises(AmbiguousIdError) as caught:
            populated.resolve("acme-2026030")

        message = caught.value.message
        assert "acme-20260303" in message
        assert "acme-20260304" in message
        assert "zebra-20260303" not in message

    def test_a_shared_prefix_of_two_numbers_lists_the_candidates(self, populated: Repo) -> None:
        with pytest.raises(AmbiguousIdError) as caught:
            populated.resolve("INV-2026-00")

        assert "INV-2026-0001" in caught.value.message
        assert "INV-2026-0011" in caught.value.message

    def test_nothing_matching_says_so(self, populated: Repo) -> None:
        with pytest.raises(NotFoundError, match="nope"):
            populated.resolve("nope")

    def test_an_empty_id_is_not_a_wildcard(self, populated: Repo) -> None:
        with pytest.raises(NotFoundError):
            populated.resolve("   ")


class TestImmutability:
    def test_a_draft_may_be_changed_freely(self, repo: Repo) -> None:
        draft = make_draft()
        repo.write_invoice(draft)

        draft.items.append(make_item(description="Extra", unit_price="50.00"))
        path = repo.write_invoice(draft)

        assert len(repo.read_invoice_at(path).items) == 2

    def test_a_changed_line_item_is_refused_once_issued(self, repo: Repo) -> None:
        issued = issue(repo, make_draft(), "INV-2026-0001")
        issued.items[0].unit_price = Decimal("999.00")

        with pytest.raises(ImmutableInvoiceError) as caught:
            repo.write_invoice(issued)
        assert "items" in caught.value.message
        assert "INV-2026-0001" in caught.value.message

    def test_a_changed_client_is_refused_once_issued(self, repo: Repo) -> None:
        issued = issue(repo, make_draft(), "INV-2026-0001")
        issued.client.name = "Someone Else Ltd"

        with pytest.raises(ImmutableInvoiceError, match="client"):
            repo.write_invoice(issued)

    def test_the_stored_invoice_is_untouched_after_a_refusal(self, repo: Repo) -> None:
        issued = issue(repo, make_draft(), "INV-2026-0001")
        path = repo.invoice_path(issued)
        before = path.read_bytes()
        issued.items[0].unit_price = Decimal("999.00")

        with pytest.raises(ImmutableInvoiceError):
            repo.write_invoice(issued)
        assert path.read_bytes() == before

    def test_a_status_change_is_allowed(self, repo: Repo) -> None:
        issued = issue(repo, make_draft(), "INV-2026-0001")
        issued.transition(InvoiceStatus.PAID, on=dt.date(2026, 4, 1))
        path = repo.write_invoice(issued)

        stored = repo.read_invoice_at(path)
        assert stored.status is InvoiceStatus.PAID
        assert [e.status for e in stored.status_history] == [
            InvoiceStatus.ISSUED,
            InvoiceStatus.PAID,
        ]

    def test_a_voided_draft_stays_in_drafts_and_may_be_written(self, repo: Repo) -> None:
        draft = make_draft()
        repo.write_invoice(draft)
        draft.transition(InvoiceStatus.VOID, on=CREATED, reason="Raised by mistake")
        path = repo.write_invoice(draft)

        assert path == repo.drafts_dir / "asa-bjork-ltd-20260301.toml"
        assert repo.read_invoice_at(path).status is InvoiceStatus.VOID

    def test_an_issued_invoice_cannot_be_conjured_by_a_plain_write(self, repo: Repo) -> None:
        issued = issue(repo, make_draft(), "INV-2026-0001")
        issued.number = "INV-2026-0009"

        with pytest.raises(ImmutableInvoiceError, match="move_draft_to_issued"):
            repo.write_invoice(issued)


class TestMoveDraftToIssued:
    def test_the_draft_file_gives_way_to_the_numbered_one(self, repo: Repo) -> None:
        draft = make_draft()
        draft_path = repo.write_invoice(draft)
        (repo.drafts_dir / "asa-bjork-ltd-20260301.pdf").write_bytes(b"%PDF-")

        draft.number = "INV-2026-0001"
        draft.issue_date = ISSUED_ON
        draft.transition(InvoiceStatus.ISSUED, on=ISSUED_ON)
        path = repo.move_draft_to_issued(draft)

        assert path == repo.year_dir(2026) / "INV-2026-0001.toml"
        assert not draft_path.exists()
        assert list(repo.drafts_dir.iterdir()) == []

    def test_the_draft_id_is_dropped_once_a_number_names_it(self, repo: Repo) -> None:
        issued = issue(repo, make_draft(), "INV-2026-0001")

        assert issued.draft_id is None
        assert "draft_id" not in repo.invoice_path(issued).read_text(encoding="utf-8")

    def test_an_unnumbered_invoice_cannot_leave_drafts(self, repo: Repo) -> None:
        draft = make_draft()
        repo.write_invoice(draft)

        with pytest.raises(ValidationError, match="needs a number"):
            repo.move_draft_to_issued(draft)

    def test_an_existing_number_is_never_overwritten(self, repo: Repo) -> None:
        issue(repo, make_draft(draft_id="acme-20260301"), "INV-2026-0001")

        clash = make_draft(draft_id="acme-20260302")
        repo.write_invoice(clash)
        clash.number = "INV-2026-0001"
        clash.issue_date = ISSUED_ON
        clash.transition(InvoiceStatus.ISSUED, on=ISSUED_ON)

        with pytest.raises(AlreadyExistsError, match="INV-2026-0001"):
            repo.move_draft_to_issued(clash)
        assert clash.draft_id == "acme-20260302"


class TestDeleteDraft:
    def test_the_toml_and_its_pdf_both_go(self, repo: Repo) -> None:
        draft = make_draft()
        path = repo.write_invoice(draft)
        pdf = repo.pdf_path(draft)
        pdf.write_bytes(b"%PDF-")

        repo.delete_draft(draft)

        assert not path.exists()
        assert not pdf.exists()

    def test_deleting_what_is_not_there_says_so(self, repo: Repo) -> None:
        with pytest.raises(NotFoundError, match="asa-bjork-ltd-20260301"):
            repo.delete_draft(make_draft())

    def test_an_issued_invoice_has_no_draft_to_delete(self, repo: Repo) -> None:
        issued = issue(repo, make_draft(), "INV-2026-0001")

        with pytest.raises(ValidationError, match="not a draft"):
            repo.delete_draft(issued)
