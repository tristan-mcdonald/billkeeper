"""Turning a sequence number into the invoice number a client sees.

The format is the user's choice, so it has to be checked before it is trusted:
an invoice number becomes a filename, and a format that renders the same string
for every invoice would silently overwrite last month's work.
"""

from __future__ import annotations

import re
import string
from typing import Final

from billkeeper.errors import ValidationError

#: The numbering format a fresh data repo starts with.
DEFAULT_FORMAT: Final = "INV-{year}-{seq:04d}"

#: The only names a format may interpolate.
PLACEHOLDERS: Final = frozenset({"year", "seq"})

#: A year to render with when checking a format, never used for a real number.
_SAMPLE_YEAR: Final = 2000

# Path separators (`/` on POSIX, either slash on Windows) and whitespace: an
# invoice number goes straight into a filename, so neither may survive.
_UNSAFE = re.compile(r"[/\\\s]")


def format_number(fmt: str, year: int, seq: int) -> str:
    """Render sequence number `seq` of `year` using `fmt`."""
    try:
        return fmt.format(year=year, seq=seq)
    except (IndexError, KeyError, ValueError) as exc:
        raise ValidationError(f"Invoice number format {fmt!r} cannot be rendered: {exc}") from None


def validate_format(fmt: str) -> None:
    """Raise `ValidationError` unless `fmt` can number invoices safely."""
    if "{seq" not in fmt:
        raise ValidationError(
            f"Invoice number format {fmt!r} has no {{seq}} placeholder, "
            "so every invoice would be given the same number."
        )

    for _literal, field, _spec, _conversion in string.Formatter().parse(fmt):
        if field is None:
            continue
        name = field.split(".")[0].split("[")[0]
        if name not in PLACEHOLDERS:
            placeholder = f"{{{field}}}" if field else "{}"
            raise ValidationError(
                f"Invoice number format {fmt!r} uses {placeholder}, "
                "but only {year} and {seq} are available."
            )

    first = format_number(fmt, _SAMPLE_YEAR, 1)
    second = format_number(fmt, _SAMPLE_YEAR, 2)

    for rendered in (first, second):
        unsafe = _UNSAFE.search(rendered)
        if unsafe is not None:
            raise ValidationError(
                f"Invoice number format {fmt!r} renders as {rendered!r}, which contains "
                f"{unsafe.group()!r}. An invoice number is used as a filename, so it may "
                "not contain a path separator or whitespace."
            )

    if first == second:
        raise ValidationError(
            f"Invoice number format {fmt!r} renders as {first!r} for both the first and "
            "the second invoice, so numbers would not be unique."
        )
