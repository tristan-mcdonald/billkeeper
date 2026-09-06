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


class ConfigError(BillkeeperError):
    """Configuration that billkeeper cannot read or make sense of."""


class RepoNotFoundError(BillkeeperError):
    """No data repo where billkeeper was told to look."""

    #: What to say when no particular path is to blame for the miss.
    DEFAULT_MESSAGE = "No billkeeper data repo found. Run 'billkeeper init' to create one."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.DEFAULT_MESSAGE)
