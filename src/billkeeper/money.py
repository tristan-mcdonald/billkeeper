"""Exact monetary amounts in a single ISO 4217 currency.

Amounts are `decimal.Decimal` throughout and are quantized to the currency's
minor units on construction, so a `Money` never carries a fraction of a cent.
Floats are rejected outright: they cannot represent 0.10 exactly, and a single
one leaking into a total is a rounding bug that only shows up on an invoice.

billkeeper never converts between currencies. Combining two currencies is an
error, not a conversion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

from babel.core import UnknownLocaleError
from babel.numbers import format_currency

from billkeeper.errors import CurrencyError, CurrencyMismatchError

#: What `Money` accepts as an amount. Deliberately not `float`.
MoneyValue = str | int | Decimal

_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")

#: Minor-unit count for any currency not listed in `MINOR_UNITS`.
DEFAULT_MINOR_UNITS: Final = 2

_ZERO_MINOR_UNITS: Final = (
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW",
    "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
)  # fmt: skip

_THREE_MINOR_UNITS: Final = ("BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND")

#: Currencies whose minor-unit count is not the usual 2.
MINOR_UNITS: Final[dict[str, int]] = {
    **dict.fromkeys(_ZERO_MINOR_UNITS, 0),
    **dict.fromkeys(_THREE_MINOR_UNITS, 3),
}

#: Symbols for the currencies we expect to meet. Anything absent uses its code.
SYMBOLS: Final[dict[str, str]] = {
    "AUD": "A$",
    "BRL": "R$",
    "CAD": "CA$",
    "CHF": "CHF",
    "DKK": "kr",
    "EUR": "€",
    "GBP": "£",
    "INR": "₹",
    "JPY": "¥",
    "NOK": "kr",
    "NZD": "NZ$",
    "PLN": "zł",
    "SEK": "kr",
    "USD": "$",
    "ZAR": "R",
}


def normalize_currency(code: str) -> str:
    """Return `code` as a validated upper-case ISO 4217 code."""
    normalized = code.strip().upper()
    if not _CURRENCY_CODE.match(normalized):
        raise CurrencyError(
            f"{code!r} is not a valid currency code: "
            "expected three letters, such as 'EUR', 'GBP', or 'JPY'."
        )
    return normalized


def minor_units(code: str) -> int:
    """Return how many decimal places `code` is written to."""
    return MINOR_UNITS.get(normalize_currency(code), DEFAULT_MINOR_UNITS)


def symbol(code: str) -> str:
    """Return the symbol for `code`, falling back to the code itself."""
    normalized = normalize_currency(code)
    return SYMBOLS.get(normalized, normalized)


def _to_decimal(value: MoneyValue) -> Decimal:
    if isinstance(value, float):
        raise CurrencyError(
            f"Refusing to build a monetary amount from the float {value!r}: "
            "pass a str, int, or Decimal so the amount stays exact."
        )
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise CurrencyError(f"{value!r} is not a monetary amount.") from None
    if not amount.is_finite():
        raise CurrencyError(f"{value!r} is not a finite monetary amount.")
    return amount


def _quantize(amount: Decimal, code: str) -> Decimal:
    places = MINOR_UNITS.get(code, DEFAULT_MINOR_UNITS)
    try:
        return amount.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise CurrencyError(f"{amount} has too many digits to hold as {code}.") from None


@dataclass(frozen=True, init=False)
class Money:
    """An exact amount in one currency, rounded to that currency's minor units."""

    amount: Decimal
    currency: str

    def __init__(self, amount: MoneyValue, currency: str) -> None:
        code = normalize_currency(currency)
        object.__setattr__(self, "currency", code)
        object.__setattr__(self, "amount", _quantize(_to_decimal(amount), code))

    @classmethod
    def zero(cls, currency: str) -> Money:
        """Return a zero amount in `currency`."""
        return cls(0, currency)

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot combine {self.currency} with {other.currency}: "
                "billkeeper never converts between currencies."
            )

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: Decimal | int) -> Money:
        return Money(self.amount * _to_decimal(factor), self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount >= other.amount


def format_money(value: Money, locale: str = "en_US") -> str:
    """Render `value` the way `locale` writes that currency."""
    try:
        return format_currency(value.amount, value.currency, locale=locale)
    except (UnknownLocaleError, ValueError):
        return f"{value.currency} {value.amount}"
