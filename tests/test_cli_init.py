"""Tests for `billkeeper init`, the one command that runs without a data repo.

Every test runs against a fake home directory and a git sandbox. `init` writes
to the user's config directory and commits to a repository, so a test that got
the isolation wrong would repoint the developer's own billkeeper at a temporary
directory and then delete it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from billkeeper.cli import app
from billkeeper.cli.init_cmd import DEFAULT_CURRENCY, INITIAL_COMMIT_MESSAGE
from billkeeper.config import (
    REPO_ENV_VAR,
    UserConfig,
    load_repo_config,
    load_user_config,
    save_user_config,
    user_config_path,
)
from billkeeper.gitrepo import git_available, run_git
from billkeeper.numbering import DEFAULT_FORMAT
from billkeeper.sequence import NEXT_TABLE

pytestmark = [
    pytest.mark.skipif(not git_available(), reason="git is not installed"),
    # The sandbox lives in `conftest.py`, where the other CLI tests take it from.
    pytest.mark.usefixtures("sandbox"),
]

runner = CliRunner()

NAME = "Example Consulting"
ADDRESS = "1 Example Street\nExampleton"
EMAIL = "billing@example.invalid"
PAYMENT_DETAILS = "Bank: Example Bank\nIBAN: XX00 EXAM 0000"

#: The answers to `init`'s questions, in the order it asks them. The two
#: multi-line ones are typed the way a user types them: with a literal `\n`.
ANSWERS = (
    NAME,
    ADDRESS.replace("\n", "\\n"),
    EMAIL,
    PAYMENT_DETAILS.replace("\n", "\\n"),
    "GBP",
    "en_GB",
    "",
)

#: What a data repo holds the moment it has been initialised.
EXPECTED_FILES = (
    "config.toml",
    "sequence.toml",
    "clients/.gitkeep",
    "invoices/drafts/.gitkeep",
    "templates/invoice.md",
    "templates/invoice.typ",
)


def answered(*answers: str) -> str:
    """Return `answers` as the keystrokes a user would give the prompts."""
    return "".join(f"{answer}\n" for answer in answers)


def init(*args: str, answers: tuple[str, ...] = ANSWERS) -> Result:
    """Run `billkeeper init args…`, answering every question it asks."""
    return runner.invoke(app, ["init", *args], input=answered(*answers))


def commit_count(repo: Path) -> int:
    return int(run_git(repo, "rev-list", "--count", "HEAD"))


def snapshot(repo: Path) -> dict[str, bytes]:
    """Return every file in the data repo itself, ignoring git's own store."""
    return {
        str(path.relative_to(repo)): path.read_bytes()
        for path in sorted(repo.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(repo).parts
    }


class TestFreshRepo:
    def test_creates_every_file_a_data_repo_starts_with(self, tmp_path: Path) -> None:
        target = tmp_path / "data"

        result = init(str(target))

        assert result.exit_code == 0, result.output
        for name in EXPECTED_FILES:
            assert (target / name).is_file(), f"{name} was not created"

    def test_makes_one_commit_holding_the_whole_repo(self, tmp_path: Path) -> None:
        target = tmp_path / "data"

        init(str(target))

        assert commit_count(target) == 1
        assert run_git(target, "log", "-1", "--pretty=%s") == INITIAL_COMMIT_MESSAGE
        committed = set(run_git(target, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
        assert committed == set(EXPECTED_FILES)

    def test_writes_a_config_that_reloads_with_the_answers_given(self, tmp_path: Path) -> None:
        target = tmp_path / "data"

        init(str(target))

        cfg = load_repo_config(target)
        assert cfg.issuer.name == NAME
        assert cfg.issuer.address == ADDRESS
        assert cfg.issuer.email == EMAIL
        assert cfg.issuer.payment_details == PAYMENT_DETAILS
        assert cfg.defaults.currency == "GBP"
        assert cfg.defaults.locale == "en_GB"
        assert cfg.numbering.format == DEFAULT_FORMAT
        assert cfg.numbering.reset == "yearly"

    def test_starts_the_counter_empty(self, tmp_path: Path) -> None:
        target = tmp_path / "data"

        init(str(target))

        with (target / "sequence.toml").open("rb") as handle:
            assert tomllib.load(handle) == {NEXT_TABLE: {}}

    def test_copies_the_packaged_templates_out_whole(self, tmp_path: Path) -> None:
        target = tmp_path / "data"

        init(str(target))

        # The templates are the user's to edit from here on, so what matters is
        # that the real ones arrived: Pandoc's placeholder and a Jinja tag.
        assert "$body$" in (target / "templates" / "invoice.typ").read_text(encoding="utf-8")
        assert "{{" in (target / "templates" / "invoice.md").read_text(encoding="utf-8")

    def test_points_the_user_config_at_the_new_repo(self, tmp_path: Path, sandbox: Path) -> None:
        target = tmp_path / "data"

        init(str(target))

        # Under the fake home, and nowhere near the developer's own config.
        assert user_config_path() == sandbox / ".config" / "billkeeper" / "config.toml"
        assert user_config_path().is_file()
        assert load_user_config().repo == target.resolve()

    def test_says_what_to_do_next(self, tmp_path: Path) -> None:
        target = tmp_path / "data"

        result = init(str(target))

        assert str(target) in result.stdout
        assert "billkeeper client add" in result.stdout


class TestPath:
    def test_prompts_for_the_path_when_none_is_given(self, tmp_path: Path) -> None:
        target = tmp_path / "elsewhere"

        result = init(answers=(str(target), *ANSWERS))

        assert result.exit_code == 0, result.output
        assert (target / "config.toml").is_file()

    def test_offers_the_home_directory_by_default(self, sandbox: Path) -> None:
        result = init(answers=("", *ANSWERS))

        assert result.exit_code == 0, result.output
        assert (sandbox / "billkeeper" / "config.toml").is_file()

    def test_offers_the_environment_variable_when_it_is_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "from-the-environment"
        monkeypatch.setenv(REPO_ENV_VAR, str(target))

        result = init(answers=("", *ANSWERS))

        assert result.exit_code == 0, result.output
        assert (target / "config.toml").is_file()

    def test_takes_the_path_from_the_repo_option(self, tmp_path: Path) -> None:
        target = tmp_path / "data"

        result = runner.invoke(app, ["--repo", str(target), "init"], input=answered(*ANSWERS))

        assert result.exit_code == 0, result.output
        assert (target / "config.toml").is_file()

    def test_refuses_two_different_paths(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["--repo", str(tmp_path / "one"), "init", str(tmp_path / "two")]
        )

        assert result.exit_code == 1
        assert "Name the data repo once." in result.stderr
        assert not (tmp_path / "one").exists()
        assert not (tmp_path / "two").exists()

    def test_refuses_a_path_that_is_a_file(self, tmp_path: Path) -> None:
        target = tmp_path / "not-a-directory"
        target.write_text("", encoding="utf-8")

        result = init(str(target))

        assert result.exit_code == 1
        assert str(target) in result.stderr


class TestExistingRepo:
    def test_says_so_and_changes_nothing(self, tmp_path: Path) -> None:
        target = tmp_path / "data"
        init(str(target))
        before = snapshot(target)

        result = init(str(target))

        assert result.exit_code == 0, result.output
        assert f"Already initialised: {target / 'config.toml'}" in result.stdout
        assert f"billkeeper will now use {target}." in result.stdout
        assert snapshot(target) == before
        assert commit_count(target) == 1
        assert run_git(target, "status", "--porcelain") == ""

    def test_records_the_path_when_the_user_config_is_absent(self, tmp_path: Path) -> None:
        target = tmp_path / "data"
        init(str(target))
        user_config_path().unlink()

        init(str(target))

        assert load_user_config().repo == target.resolve()

    def test_records_the_path_when_the_user_config_points_elsewhere(self, tmp_path: Path) -> None:
        target = tmp_path / "data"
        init(str(target))
        save_user_config(UserConfig(repo=tmp_path / "somewhere-else"))

        init(str(target))

        assert load_user_config().repo == target.resolve()


class TestAnswers:
    def test_asks_again_for_an_invoice_number_format_that_will_not_do(self, tmp_path: Path) -> None:
        target = tmp_path / "data"
        answers = (*ANSWERS[:-1], "INV-{year}", "INV-{seq}")

        result = init(str(target), answers=answers)

        assert result.exit_code == 0, result.output
        assert "{seq}" in result.stderr
        assert load_repo_config(target).numbering.format == "INV-{seq}"

    def test_asks_again_for_a_currency_that_is_not_one(self, tmp_path: Path) -> None:
        target = tmp_path / "data"
        answers = (*ANSWERS[:4], "pounds", "GBP", *ANSWERS[5:])

        result = init(str(target), answers=answers)

        assert result.exit_code == 0, result.output
        assert "not a valid currency code" in result.stderr
        assert load_repo_config(target).defaults.currency == "GBP"

    def test_asks_again_for_a_locale_babel_does_not_know(self, tmp_path: Path) -> None:
        target = tmp_path / "data"
        answers = (*ANSWERS[:5], "not-a-locale", "en_GB", ANSWERS[6])

        result = init(str(target), answers=answers)

        assert result.exit_code == 0, result.output
        assert "not a locale" in result.stderr
        assert load_repo_config(target).defaults.locale == "en_GB"

    def test_asks_again_for_a_name_left_empty(self, tmp_path: Path) -> None:
        target = tmp_path / "data"
        answers = ("   ", *ANSWERS)

        result = init(str(target), answers=answers)

        assert result.exit_code == 0, result.output
        assert load_repo_config(target).issuer.name == NAME

    def test_takes_the_defaults_offered(self, tmp_path: Path) -> None:
        target = tmp_path / "data"
        answers = (NAME, "", "", "", "", "", "")

        result = init(str(target), answers=answers)

        assert result.exit_code == 0, result.output
        cfg = load_repo_config(target)
        assert cfg.issuer.address == ""
        assert cfg.defaults.currency == DEFAULT_CURRENCY
        assert cfg.defaults.locale == "en_GB"  # from $LANG
        assert cfg.numbering.format == DEFAULT_FORMAT

    def test_falls_back_to_english_when_the_environment_says_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LANG", "C")
        target = tmp_path / "data"

        init(str(target), answers=(NAME, "", "", "", "", "", ""))

        assert load_repo_config(target).defaults.locale == "en_US"
