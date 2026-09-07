"""Tests for the invoice-number counter.

The thing worth being sure about is that a number is never handed out twice and
never quietly skipped, including across the two places numbering can restart:
a new year, and a repo that has changed its mind about resetting at all.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

import pytest

from billkeeper.config import Defaults, Issuer, Numbering, RepoConfig
from billkeeper.errors import BillkeeperError, ConfigError
from billkeeper.numbering import DEFAULT_FORMAT
from billkeeper.sequence import ALL_SCOPE, NEXT_TABLE, allocate, peek, rollback, scope_key
from billkeeper.storage import Repo


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    """Return a data repo directory with no `sequence.toml` in it yet."""
    root = tmp_path / "data"
    root.mkdir()
    return Repo(root)


def make_config(
    fmt: str = DEFAULT_FORMAT,
    reset: Literal["yearly", "never"] = "yearly",
) -> RepoConfig:
    return RepoConfig(
        issuer=Issuer(name="Example Consulting"),
        defaults=Defaults(currency="EUR"),
        numbering=Numbering(format=fmt, reset=reset),
    )


def write_sequence(repo: Repo, body: str) -> Path:
    repo.sequence_path.write_text(body, encoding="utf-8")
    return repo.sequence_path


def read_counters(repo: Repo) -> dict[str, int]:
    with repo.sequence_path.open("rb") as handle:
        table: dict[str, int] = tomllib.load(handle)[NEXT_TABLE]
    return table


class TestScope:
    def test_a_yearly_reset_counts_within_the_year(self) -> None:
        assert scope_key(make_config(), 2026) == "2026"

    def test_never_resetting_puts_every_invoice_in_one_scope(self) -> None:
        assert scope_key(make_config(reset="never"), 2026) == ALL_SCOPE


class TestPeek:
    def test_the_first_invoice_of_a_scope_is_number_one(self, repo: Repo) -> None:
        assert peek(repo, make_config(), 2026) == 1

    def test_a_file_without_the_year_starts_that_year_at_one(self, repo: Repo) -> None:
        write_sequence(repo, f"[{NEXT_TABLE}]\n2025 = 12\n")

        assert peek(repo, make_config(), 2026) == 1

    def test_peeking_does_not_use_the_number_up(self, repo: Repo) -> None:
        cfg = make_config()
        assert peek(repo, cfg, 2026) == 1
        assert peek(repo, cfg, 2026) == 1
        assert allocate(repo, cfg, 2026)[0] == 1


class TestAllocate:
    def test_ten_allocations_leave_no_gap(self, repo: Repo) -> None:
        cfg = make_config()

        taken = [allocate(repo, cfg, 2026) for _ in range(10)]

        assert [seq for seq, _ in taken] == list(range(1, 11))
        assert [number for _, number in taken] == [f"INV-2026-{n:04d}" for n in range(1, 11)]
        assert peek(repo, cfg, 2026) == 11

    def test_a_new_year_starts_again_at_one(self, repo: Repo) -> None:
        cfg = make_config()
        allocate(repo, cfg, 2026)
        allocate(repo, cfg, 2026)

        assert allocate(repo, cfg, 2027) == (1, "INV-2027-0001")
        assert peek(repo, cfg, 2026) == 3

    def test_never_resetting_carries_the_count_across_the_year(self, repo: Repo) -> None:
        cfg = make_config(reset="never")
        allocate(repo, cfg, 2026)
        allocate(repo, cfg, 2026)

        assert allocate(repo, cfg, 2027) == (3, "INV-2027-0003")

    def test_switching_to_never_leaves_the_yearly_counters_where_they_were(
        self, repo: Repo
    ) -> None:
        allocate(repo, make_config(), 2026)
        allocate(repo, make_config(reset="never"), 2026)

        assert read_counters(repo) == {"2026": 2, ALL_SCOPE: 2}

    def test_a_custom_format_shapes_the_number_but_not_the_counter(self, repo: Repo) -> None:
        cfg = make_config(fmt="ACME-{seq:05d}")

        assert allocate(repo, cfg, 2026) == (1, "ACME-00001")
        assert allocate(repo, cfg, 2026) == (2, "ACME-00002")

    def test_the_counter_file_is_the_documented_shape(self, repo: Repo) -> None:
        allocate(repo, make_config(), 2026)

        assert read_counters(repo) == {"2026": 2}

    def test_the_counters_are_written_in_key_order_so_the_file_settles(self, repo: Repo) -> None:
        cfg = make_config()
        for year in (2027, 2025, 2026):
            allocate(repo, cfg, year)
        written = repo.sequence_path.read_text(encoding="utf-8")

        assert list(read_counters(repo)) == ["2025", "2026", "2027"]

        allocate(repo, cfg, 2026)
        rollback(repo, cfg, 2026, 2)
        assert repo.sequence_path.read_text(encoding="utf-8") == written


class TestRollback:
    def test_a_number_that_was_never_used_goes_back(self, repo: Repo) -> None:
        cfg = make_config()
        seq, _ = allocate(repo, cfg, 2026)

        rollback(repo, cfg, 2026, seq)

        assert peek(repo, cfg, 2026) == 1
        assert allocate(repo, cfg, 2026) == (1, "INV-2026-0001")

    def test_returning_a_number_once_the_next_is_out_is_refused(self, repo: Repo) -> None:
        cfg = make_config()
        seq, _ = allocate(repo, cfg, 2026)
        allocate(repo, cfg, 2026)

        with pytest.raises(BillkeeperError, match="stands at 3"):
            rollback(repo, cfg, 2026, seq)

        assert peek(repo, cfg, 2026) == 3

    def test_returning_a_number_that_was_never_taken_is_refused(self, repo: Repo) -> None:
        cfg = make_config()

        with pytest.raises(BillkeeperError, match="stands at 1"):
            rollback(repo, cfg, 2026, 1)

    def test_the_scope_is_the_one_the_number_came_from(self, repo: Repo) -> None:
        cfg = make_config(reset="never")
        seq, _ = allocate(repo, cfg, 2026)

        rollback(repo, cfg, 2027, seq)

        assert read_counters(repo) == {ALL_SCOPE: 1}


class TestCorruptFile:
    def test_a_file_that_is_not_toml_is_refused(self, repo: Repo) -> None:
        path = write_sequence(repo, "next = {2026 = \n")

        with pytest.raises(ConfigError, match="not valid TOML") as caught:
            peek(repo, make_config(), 2026)
        assert str(path) in caught.value.message

    def test_a_counter_that_is_not_a_number_is_refused(self, repo: Repo) -> None:
        write_sequence(repo, f'[{NEXT_TABLE}]\n2026 = "7"\n')

        with pytest.raises(ConfigError, match="whole number"):
            peek(repo, make_config(), 2026)

    def test_a_boolean_counter_is_refused(self, repo: Repo) -> None:
        write_sequence(repo, f"[{NEXT_TABLE}]\n2026 = true\n")

        with pytest.raises(ConfigError, match="whole number"):
            peek(repo, make_config(), 2026)

    def test_a_counter_below_one_is_refused(self, repo: Repo) -> None:
        write_sequence(repo, f"[{NEXT_TABLE}]\n2026 = 0\n")

        with pytest.raises(ConfigError, match="cannot be below 1"):
            peek(repo, make_config(), 2026)

    def test_a_next_that_is_not_a_table_is_refused(self, repo: Repo) -> None:
        write_sequence(repo, f"{NEXT_TABLE} = 7\n")

        with pytest.raises(ConfigError, match="must be a table"):
            peek(repo, make_config(), 2026)

    def test_a_mistyped_table_name_is_refused_rather_than_ignored(self, repo: Repo) -> None:
        write_sequence(repo, "[nxet]\n2026 = 7\n")

        with pytest.raises(ConfigError, match="'nxet'"):
            peek(repo, make_config(), 2026)

    def test_allocating_over_a_corrupt_file_changes_nothing(self, repo: Repo) -> None:
        body = f'[{NEXT_TABLE}]\n2026 = "7"\n'
        write_sequence(repo, body)

        with pytest.raises(ConfigError):
            allocate(repo, make_config(), 2026)

        assert repo.sequence_path.read_text(encoding="utf-8") == body
