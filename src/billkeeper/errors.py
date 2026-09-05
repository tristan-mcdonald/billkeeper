"""The exception hierarchy every billkeeper module raises from.

Everything the tool raises on purpose derives from `BillkeeperError`, so the CLI
has a single place to catch it and print one clear line for the user.
"""


class BillkeeperError(Exception):
    """Base class for every billkeeper error, carrying a human-readable message."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationError(BillkeeperError):
    """Data that does not satisfy billkeeper's rules."""


class CurrencyError(BillkeeperError):
    """A currency code or monetary amount that billkeeper cannot work with."""


class CurrencyMismatchError(CurrencyError):
    """An attempt to combine amounts denominated in different currencies."""
