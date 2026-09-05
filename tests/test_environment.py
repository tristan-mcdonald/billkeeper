"""Checks on the environment the test suite runs in."""

import shutil
import sys

import pytest

RENDERING_TOOLS = ("pandoc", "typst")


def test_python_is_supported() -> None:
    assert sys.version_info >= (3, 12)


@pytest.mark.requires_tools
@pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in RENDERING_TOOLS),
    reason="pandoc and typst are not both installed",
)
def test_rendering_tools_are_on_path() -> None:
    for tool in RENDERING_TOOLS:
        assert shutil.which(tool) is not None, f"{tool} is not on PATH"
