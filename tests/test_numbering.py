"""Tests for invoice-number formatting."""

from __future__ import annotations

import pytest

from billkeeper.errors import ValidationError
from billkeeper.numbering import DEFAULT_FORMAT, format_number, validate_format


class TestFormatNumber:
    def test_the_default_format_at_the_first_number(self) -> None:
        assert format_number(DEFAULT_FORMAT, 2026, 1) == "INV-2026-0001"

    def test_the_default_format_at_the_last_padded_number(self) -> None:
        assert format_number(DEFAULT_FORMAT, 2026, 9999) == "INV-2026-9999"

    def test_the_default_format_keeps_counting_past_the_padding(self) -> None:
        assert format_number(DEFAULT_FORMAT, 2026, 10000) == "INV-2026-10000"

    def test_a_custom_format(self) -> None:
        assert format_number("{year}/{seq:03d}", 2026, 7) == "2026/007"

    def test_a_format_needs_neither_placeholder_to_render(self) -> None:
        assert format_number("{seq}", 2026, 42) == "42"

    def test_an_unrenderable_format_raises(self) -> None:
        with pytest.raises(ValidationError, match="cannot be rendered"):
            format_number("INV-{seq:04q}", 2026, 1)


class TestValidateFormat:
    def test_the_default_format_is_valid(self) -> None:
        validate_format(DEFAULT_FORMAT)

    @pytest.mark.parametrize(
        "fmt",
        [
            "{year}-{seq:03d}",
            "{seq}",
            "INV{seq:06d}",
            "{year}.{seq:04d}.credit",
        ],
    )
    def test_accepts_usable_custom_formats(self, fmt: str) -> None:
        validate_format(fmt)

    def test_rejects_a_format_with_no_sequence(self) -> None:
        with pytest.raises(ValidationError, match=r"no \{seq\} placeholder"):
            validate_format("INV-{year}")

    def test_rejects_a_format_with_a_path_separator(self) -> None:
        # This one renders perfectly well; it is only unusable as a filename.
        with pytest.raises(ValidationError, match="path separator or whitespace"):
            validate_format("{year}/{seq:03d}")

    @pytest.mark.parametrize("fmt", ["INV {year} {seq:04d}", "INV-{year}-{seq:04d}\t"])
    def test_rejects_a_format_with_whitespace(self, fmt: str) -> None:
        with pytest.raises(ValidationError, match="path separator or whitespace"):
            validate_format(fmt)

    @pytest.mark.parametrize("fmt", ["{month}-{seq:04d}", "{client}{seq}", "{}-{seq}"])
    def test_rejects_an_unknown_placeholder(self, fmt: str) -> None:
        with pytest.raises(ValidationError, match=r"only \{year\} and \{seq\} are available"):
            validate_format(fmt)

    def test_rejects_a_format_whose_sequence_is_only_literal_braces(self) -> None:
        # "{{seq}}" contains the text "{seq" but interpolates nothing, so every
        # invoice would be given the same number.
        with pytest.raises(ValidationError, match="would not be unique"):
            validate_format("INV-{{seq}}-{year}")

    def test_the_message_shows_the_offending_format(self) -> None:
        with pytest.raises(ValidationError) as caught:
            validate_format("INV-{year}")
        assert "'INV-{year}'" in caught.value.message
