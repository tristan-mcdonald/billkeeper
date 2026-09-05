"""Tests for the money and currency layer."""

from decimal import Decimal

import pytest

from billkeeper.errors import CurrencyError, CurrencyMismatchError
from billkeeper.money import (
    Money,
    format_money,
    minor_units,
    normalize_currency,
    symbol,
)


class TestNormalizeCurrency:
    def test_uppercases_and_strips(self) -> None:
        assert normalize_currency("  gbp ") == "GBP"

    @pytest.mark.parametrize("code", ["", "US", "USDD", "US1", "euro", "€"])
    def test_rejects_codes_that_are_not_three_letters(self, code: str) -> None:
        with pytest.raises(CurrencyError, match="not a valid currency code"):
            normalize_currency(code)


class TestMinorUnits:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [("USD", 2), ("GBP", 2), ("EUR", 2), ("JPY", 0), ("ISK", 0), ("KWD", 3), ("BHD", 3)],
    )
    def test_known_currencies(self, code: str, expected: int) -> None:
        assert minor_units(code) == expected

    def test_unknown_but_valid_code_defaults_to_two(self) -> None:
        assert minor_units("XTS") == 2

    def test_normalizes_first(self) -> None:
        assert minor_units("jpy") == 0


class TestQuantization:
    def test_two_minor_units(self) -> None:
        assert Money("10.005", "USD").amount == Decimal("10.01")
        assert Money("1.5", "GBP").amount == Decimal("1.50")

    def test_zero_minor_units(self) -> None:
        assert Money("1234.6", "JPY").amount == Decimal("1235")
        assert str(Money("1234", "JPY").amount) == "1234"

    def test_three_minor_units(self) -> None:
        assert str(Money("1.2", "KWD").amount) == "1.200"
        assert Money("1.2345", "KWD").amount == Decimal("1.235")

    def test_unknown_but_valid_code_uses_two(self) -> None:
        assert str(Money("1.239", "XTS").amount) == "1.24"

    def test_rounds_half_up_not_half_even(self) -> None:
        assert Money("0.005", "USD").amount == Decimal("0.01")
        assert Money("0.015", "USD").amount == Decimal("0.02")

    def test_accepts_int_str_and_decimal(self) -> None:
        assert Money(7, "USD") == Money("7", "USD") == Money(Decimal("7.00"), "USD")

    def test_normalizes_the_currency(self) -> None:
        assert Money("1", " usd ").currency == "USD"


class TestFloatRejection:
    def test_construction_from_a_float_raises(self) -> None:
        with pytest.raises(CurrencyError, match="float"):
            Money(0.1, "USD")  # type: ignore[arg-type]

    def test_multiplication_by_a_float_raises(self) -> None:
        with pytest.raises(CurrencyError, match="float"):
            Money("10.00", "USD") * 0.5  # type: ignore[operator]

    def test_non_numeric_string_raises(self) -> None:
        with pytest.raises(CurrencyError, match="not a monetary amount"):
            Money("ten pounds", "GBP")

    def test_non_finite_amount_raises(self) -> None:
        with pytest.raises(CurrencyError, match="finite"):
            Money("NaN", "USD")


class TestArithmetic:
    def test_addition_is_exact(self) -> None:
        assert Money("0.10", "USD") + Money("0.20", "USD") == Money("0.30", "USD")
        assert (Money("0.10", "USD") + Money("0.20", "USD")).amount == Decimal("0.30")

    def test_subtraction(self) -> None:
        assert Money("0.30", "USD") - Money("0.10", "USD") == Money("0.20", "USD")

    def test_negation(self) -> None:
        assert -Money("1.25", "USD") == Money("-1.25", "USD")

    def test_addition_across_currencies_raises(self) -> None:
        with pytest.raises(CurrencyMismatchError, match="never converts"):
            Money("1.00", "USD") + Money("1.00", "EUR")

    def test_subtraction_across_currencies_raises(self) -> None:
        with pytest.raises(CurrencyMismatchError):
            Money("1.00", "USD") - Money("1.00", "EUR")

    def test_multiplication_by_a_fraction_quantizes(self) -> None:
        assert Money("10.00", "USD") * Decimal("0.125") == Money("1.25", "USD")

    def test_multiplication_rounds_half_up(self) -> None:
        # 2.01 x 0.5 is exactly 1.005; half-even would give 1.00.
        assert Money("2.01", "USD") * Decimal("0.5") == Money("1.01", "USD")

    def test_multiplication_by_an_int(self) -> None:
        assert Money("1.15", "USD") * 3 == Money("3.45", "USD")

    def test_multiplication_in_a_zero_unit_currency(self) -> None:
        assert Money("1000", "JPY") * Decimal("1.5") == Money("1500", "JPY")

    def test_zero_and_is_zero(self) -> None:
        assert Money.zero("KWD") == Money("0", "KWD")
        assert Money.zero("KWD").is_zero
        assert not Money("0.001", "KWD").is_zero
        assert Money("0.0004", "KWD").is_zero


class TestComparison:
    def test_ordering(self) -> None:
        assert Money("1.00", "USD") < Money("2.00", "USD")
        assert Money("2.00", "USD") > Money("1.00", "USD")
        assert Money("1.00", "USD") <= Money("1.00", "USD")
        assert Money("1.00", "USD") >= Money("1.00", "USD")

    def test_ordering_across_currencies_raises(self) -> None:
        with pytest.raises(CurrencyMismatchError):
            _ = Money("1.00", "USD") < Money("2.00", "EUR")

    def test_equality_across_currencies_is_false_not_an_error(self) -> None:
        assert Money("1.00", "USD") != Money("1.00", "EUR")


class TestValueSemantics:
    def test_is_frozen(self) -> None:
        with pytest.raises(AttributeError):
            Money("1.00", "USD").amount = Decimal("2.00")  # type: ignore[misc]

    def test_is_hashable_and_usable_as_a_dict_key(self) -> None:
        totals = {Money("1.00", "USD"): "one dollar", Money("1.00", "EUR"): "one euro"}
        assert totals[Money("1.00", "USD")] == "one dollar"
        assert totals[Money("1", "EUR")] == "one euro"
        assert len({Money("1.00", "USD"), Money("1.000", "USD")}) == 1


class TestSymbols:
    def test_known_symbol(self) -> None:
        assert symbol("gbp") == "£"

    def test_unknown_currency_uses_its_code(self) -> None:
        assert symbol("ZMW") == "ZMW"


class TestFormatMoney:
    def test_gbp_in_en_gb_leads_with_a_pound_sign(self) -> None:
        assert format_money(Money("1234.56", "GBP"), locale="en_GB").startswith("£")

    def test_jpy_uses_a_yen_sign_and_no_decimals(self) -> None:
        formatted = format_money(Money("1234", "JPY"))
        assert formatted.startswith("¥")
        assert "." not in formatted

    def test_currency_without_a_symbol_is_written_with_its_code(self) -> None:
        assert format_money(Money("1234.56", "ZMW")).startswith("ZMW")

    def test_locale_changes_the_layout(self) -> None:
        assert format_money(Money("1234.56", "EUR"), locale="de_DE").endswith("€")

    @pytest.mark.parametrize("locale", ["xx_YY", "not a locale"])
    def test_unknown_locale_falls_back_to_code_and_amount(self, locale: str) -> None:
        assert format_money(Money("1234.56", "USD"), locale=locale) == "USD 1234.56"
