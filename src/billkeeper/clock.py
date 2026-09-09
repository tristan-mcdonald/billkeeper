"""The current date, asked for in one place so a test can hold it still.

Every command that dates something — the day a draft was raised, the day an
invoice was issued, the day the money arrived — asks here rather than calling
`date.today()` where it stands. That buys two things. A test can freeze time by
monkeypatching this one function, which is the difference between asserting on
a filename and hoping the clock does not move; and a command that reads the
date twice cannot be caught by midnight falling between the two readings.
"""

from __future__ import annotations

import datetime as dt


def today() -> dt.date:
    """Return today's date, in the machine's own time zone."""
    return dt.date.today()
