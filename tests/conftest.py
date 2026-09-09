"""Fixtures the CLI tests share: a home of their own, and a data repo in it.

Every test that runs a billkeeper command needs both. The tool writes to the
user's config directory and commits to a git repository, so a test that got the
isolation wrong would repoint the developer's own billkeeper at a temporary
directory and then delete it. `sandbox` is that isolation. `repo` is one data
repo standing inside it, made the way a user makes one — by running `init` and
answering its questions — so the tests exercise a repo the tool built rather
than a hand-assembled imitation of one that could drift away from the real
thing without any test noticing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from typer.testing import CliRunner

from billkeeper.cli import app
from billkeeper.config import REPO_ENV_VAR, XDG_CONFIG_HOME_ENV
from billkeeper.storage import Repo

#: What the `repo` fixture answers `init` with, in the order it asks.
ISSUER_NAME: Final = "Example Consulting"
ISSUER_ADDRESS: Final = "1 Example Street\nExampleton"
ISSUER_EMAIL: Final = "billing@example.invalid"
PAYMENT_DETAILS: Final = "Bank: Example Bank\nIBAN: XX00 EXAM 0000"
REPO_CURRENCY: Final = "EUR"
REPO_LOCALE: Final = "en_GB"

INIT_ANSWERS: Final = (
    ISSUER_NAME,
    ISSUER_ADDRESS.replace("\n", "\\n"),
    ISSUER_EMAIL,
    PAYMENT_DETAILS.replace("\n", "\\n"),
    REPO_CURRENCY,
    REPO_LOCALE,
    "",
)


def answered(*answers: str) -> str:
    """Return `answers` as the keystrokes a user would give the prompts."""
    return "".join(f"{answer}\n" for answer in answers)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give the test a home of its own, and cut git off from every config file."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv(XDG_CONFIG_HOME_ENV, str(home / ".config"))
    monkeypatch.delenv(REPO_ENV_VAR, raising=False)
    monkeypatch.setenv("LANG", "en_GB.UTF-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-such-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GNUPGHOME", str(tmp_path / "no-such-gnupg"))
    # A test that opens the editor without saying which one gets one that does
    # nothing, rather than a real `vi` waiting for a keystroke that never comes.
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "true")
    return home.resolve()


@pytest.fixture
def repo(tmp_path: Path, sandbox: Path) -> Repo:
    """Return a data repo freshly made by `billkeeper init`, and pointed at."""
    root = tmp_path / "data"
    result = CliRunner().invoke(app, ["init", str(root)], input=answered(*INIT_ANSWERS))
    assert result.exit_code == 0, result.output
    return Repo(root.resolve())
