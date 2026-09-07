"""The invoice-number counter: `sequence.toml`, and the numbers it hands out.

A gap in a numbered series is the first thing anyone auditing a set of invoices
asks about, so the counter is kept on disk rather than derived from the files
that happen to be there: an invoice that was numbered and then deleted must
still have used up its number. The file is deliberately dull.

    [next]
    2026 = 7

The key is the scope the counter runs in — the four-digit year while numbering
resets yearly, the literal `all` when it never does — so a repo that changes
its mind about resetting keeps both counters and cannot reissue a number it has
already used.

A number is drawn only on issue, never when a draft is created, and `rollback`
puts one back if the issue it was drawn for did not survive (a PDF that failed
to render, say). That is the only way a number is ever returned, and it is
refused once anything else has been numbered on top of it, because by then
giving it back would mean two invoices sharing a number.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import tomli_w

from billkeeper.config import RepoConfig
from billkeeper.errors import BillkeeperError, ConfigError
from billkeeper.numbering import format_number
from billkeeper.storage import Repo, atomic_write

#: The table in `sequence.toml` holding every counter.
NEXT_TABLE: Final = "next"

#: The scope every invoice shares when numbering never resets.
ALL_SCOPE: Final = "all"

#: The number the first invoice of a scope is given.
FIRST: Final = 1


def scope_key(cfg: RepoConfig, year: int) -> str:
    """Return the name of the counter `year`'s invoices are drawn from."""
    if cfg.numbering.reset == "never":
        return ALL_SCOPE
    return f"{year:04d}"


def peek(repo: Repo, cfg: RepoConfig, year: int) -> int:
    """Return the number the next invoice of `year` would take, without taking it."""
    return _counters(repo).get(scope_key(cfg, year), FIRST)


def allocate(repo: Repo, cfg: RepoConfig, year: int) -> tuple[int, str]:
    """Take the next number for `year`, and return it raw and formatted.

    The counter is written back before the caller gets the number, so an
    interruption costs at worst one unused number rather than handing the same
    one out twice.
    """
    key = scope_key(cfg, year)
    counters = _counters(repo)
    seq = counters.get(key, FIRST)
    number = format_number(cfg.numbering.format, year, seq)
    counters[key] = seq + 1
    _write_counters(repo, counters)
    return seq, number


def rollback(repo: Repo, cfg: RepoConfig, year: int, seq: int) -> None:
    """Return `seq` to the counter, if nothing has been numbered since."""
    key = scope_key(cfg, year)
    counters = _counters(repo)
    current = counters.get(key, FIRST)
    if current != seq + 1:
        raise BillkeeperError(
            f"Cannot give invoice number {seq} of {key} back: the counter stands at "
            f"{current}, not {seq + 1}, so something has been numbered since. Leaving "
            "it alone — an unused number is a smaller problem than two invoices "
            "sharing one."
        )
    counters[key] = seq
    _write_counters(repo, counters)


def _counters(repo: Repo) -> dict[str, int]:
    """Return every counter in `sequence.toml`, checked before it is believed."""
    path = repo.sequence_path
    if not path.is_file():
        return {}

    data = _read_toml(path)
    # An unknown top-level key is nearly always `[nxet]` or a stray edit, and
    # letting it pass would silently start the numbering again from one.
    unknown = sorted(key for key in data if key != NEXT_TABLE)
    if unknown:
        raise ConfigError(
            f"{path} has {', '.join(repr(key) for key in unknown)} at the top level, "
            f"but the only table it may hold is [{NEXT_TABLE}]."
        )

    table = data.get(NEXT_TABLE, {})
    if not isinstance(table, dict):
        raise ConfigError(f"{path}: [{NEXT_TABLE}] must be a table of counters, not {table!r}.")

    counters: dict[str, int] = {}
    for key, value in table.items():
        # `bool` is an `int` in Python but `true` is not a counter.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(
                f"{path}: the counter for {key!r} is {value!r}, but it must be a whole number."
            )
        if value < FIRST:
            raise ConfigError(
                f"{path}: the counter for {key!r} is {value}, but the next invoice "
                f"number cannot be below {FIRST}."
            )
        counters[key] = value
    return counters


def _write_counters(repo: Repo, counters: Mapping[str, int]) -> None:
    """Write every counter back, in key order so the file settles."""
    body = {NEXT_TABLE: {key: counters[key] for key in sorted(counters)}}
    atomic_write(repo.sequence_path, tomli_w.dumps(body))


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc.strerror}.") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from None
