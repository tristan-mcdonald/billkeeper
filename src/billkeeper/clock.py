"""The current date, asked for in one place so a test can hold it still.

Every command that dates something — the day a draft was raised, the day an
invoice was issued, the day the money arrived — asks here rather than calling
`date.today()` where it stands. That buys two things. A test can freeze time by
monkeypatching this one function, which is the difference between asserting on
a filename and hoping the clock does not move; and a command that reads the
date twice cannot be caught by midnight falling between the two readings.

Reading a date the user typed lives here too, because the only default a
`--date` option ever has is today, and one parser means a mistyped date is
refused in the same words whichever command was asked for one.
"""

from __future__ import annotations

import datetime as dt

from billkeeper.errors import ValidationError


def today() -> dt.date:
    """Return today's date, in the machine's own time zone."""
    return dt.date.today()


def parse_date(value: str) -> dt.date:
    """Return the date `value` names, or today when it names none.

    An empty string is how a `--date` option says it was not given, so it
    answers with today rather than failing: the commands that take one all mean
    "now" by default.
    """
    trimmed = value.strip()
    if not trimmed:
        return today()
    try:
        return dt.date.fromisoformat(trimmed)
    except ValueError:
        raise ValidationError(
            f"{value!r} is not a date billkeeper can read. Write it as YYYY-MM-DD, "
            f"like {today():%Y-%m-%d}."
        ) from None
