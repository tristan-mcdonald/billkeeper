"""Tests for user config, repo config, and data-repo discovery.

Every test runs against a fake home directory. Nothing here may read or write
the real `$HOME`: finding files in the user's home directory is the module's
whole job, so a test that got the isolation wrong would quietly start reading
the developer's own data repo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from billkeeper.config import (
    CONFIG_FILENAME,
    DEFAULT_LOCALE,
    REPO_ENV_VAR,
    XDG_CONFIG_HOME_ENV,
    Defaults,
    Issuer,
    Numbering,
    RepoConfig,
    UserConfig,
    default_repo,
    is_repo,
    load_repo_config,
    load_user_config,
    resolve_repo,
    save_repo_config,
    save_user_config,
    user_config_dir,
    user_config_path,
)
from billkeeper.errors import ConfigError, RepoNotFoundError, ValidationError
from billkeeper.models import DEFAULT_PAYMENT_TERMS_DAYS
from billkeeper.numbering import DEFAULT_FORMAT

MINIMAL_CONFIG = """\
[issuer]
name = "Example Consulting"

[defaults]
currency = "EUR"
"""


@pytest.fixture(autouse=True)
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the home directory at `tmp_path` and clear our environment."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv(XDG_CONFIG_HOME_ENV, raising=False)
    monkeypatch.delenv(REPO_ENV_VAR, raising=False)
    return home.resolve()


def make_repo(path: Path, body: str = MINIMAL_CONFIG) -> Path:
    """Create a directory that `resolve_repo` will accept as a data repo."""
    path.mkdir(parents=True, exist_ok=True)
    (path / CONFIG_FILENAME).write_text(body, encoding="utf-8")
    return path.resolve()


def write_user_config(body: str) -> Path:
    """Write `body` to the user config file, byte for byte."""
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def make_repo_config(**overrides: Any) -> RepoConfig:
    fields: dict[str, Any] = {
        "issuer": Issuer(name="Example Consulting"),
        "defaults": Defaults(currency="EUR"),
    }
    return RepoConfig(**(fields | overrides))


class TestUserConfigLocation:
    def test_xdg_config_home_is_honoured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(XDG_CONFIG_HOME_ENV, str(tmp_path / "xdg"))
        assert user_config_dir() == (tmp_path / "xdg" / "billkeeper").resolve()

    def test_falls_back_to_dot_config_when_xdg_is_unset(self, fake_home: Path) -> None:
        assert user_config_dir() == fake_home / ".config" / "billkeeper"

    def test_falls_back_to_dot_config_when_xdg_is_empty(
        self, fake_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(XDG_CONFIG_HOME_ENV, "")
        assert user_config_dir() == fake_home / ".config" / "billkeeper"

    def test_the_config_file_sits_in_that_directory(self) -> None:
        assert user_config_path() == user_config_dir() / CONFIG_FILENAME

    def test_the_default_repo_is_under_the_home_directory(self, fake_home: Path) -> None:
        assert default_repo() == fake_home / "billkeeper"


class TestUserConfig:
    def test_an_absent_file_reads_as_empty(self) -> None:
        assert not user_config_path().exists()
        assert load_user_config().repo is None

    def test_round_trips(self, tmp_path: Path) -> None:
        save_user_config(UserConfig(repo=tmp_path / "data"))
        assert load_user_config().repo == tmp_path / "data"

    def test_saving_creates_the_config_directory(self) -> None:
        assert not user_config_dir().exists()
        save_user_config(UserConfig())
        assert user_config_path().is_file()

    def test_malformed_toml_raises_naming_the_file(self) -> None:
        path = write_user_config("repo = [unclosed\n")
        with pytest.raises(ConfigError) as caught:
            load_user_config()
        assert str(path) in caught.value.message

    def test_an_unknown_key_is_rejected(self) -> None:
        write_user_config('repo = "/tmp/data"\nrpeo = "/tmp/typo"\n')
        with pytest.raises(ValidationError, match="rpeo"):
            load_user_config()


class TestRepoConfig:
    def test_round_trips_through_toml(self, tmp_path: Path) -> None:
        cfg = make_repo_config(
            issuer=Issuer(
                name="Example Consulting",
                address="1 Example Street\nExampleton",
                email="billing@example.invalid",
                payment_details="Bank: Example Bank\nIBAN: XX00 0000 0000",
            ),
            defaults=Defaults(currency="JPY", locale="ja_JP", payment_terms_days=14),
            numbering=Numbering(format="{year}-{seq:03d}", reset="never"),
        )
        save_repo_config(tmp_path, cfg)
        assert load_repo_config(tmp_path) == cfg

    def test_a_multi_line_address_survives_the_round_trip(self, tmp_path: Path) -> None:
        cfg = make_repo_config(issuer=Issuer(name="Åsa Björk", address="Line one\nLine two"))
        save_repo_config(tmp_path, cfg)
        loaded = load_repo_config(tmp_path).issuer
        assert (loaded.name, loaded.address) == ("Åsa Björk", "Line one\nLine two")

    def test_the_defaults_are_the_documented_ones(self) -> None:
        cfg = make_repo_config()
        assert cfg.defaults.locale == DEFAULT_LOCALE
        assert cfg.defaults.payment_terms_days == DEFAULT_PAYMENT_TERMS_DAYS
        assert cfg.numbering.format == DEFAULT_FORMAT
        assert cfg.numbering.reset == "yearly"

    def test_the_optional_issuer_fields_default_to_nothing(self) -> None:
        """No default may put words, least of all a person, into an invoice."""
        issuer = make_repo_config().issuer
        assert (issuer.address, issuer.email, issuer.payment_details) == ("", "", "")

    def test_the_currency_is_normalised(self) -> None:
        assert make_repo_config(defaults=Defaults(currency=" eur ")).defaults.currency == "EUR"

    def test_an_invalid_numbering_format_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"\{seq\}"):
            Numbering.model_validate({"format": "INV-{year}"})

    def test_an_invalid_numbering_format_is_rejected_on_load(self, tmp_path: Path) -> None:
        make_repo(tmp_path, MINIMAL_CONFIG + '\n[numbering]\nformat = "INV-{year}"\n')
        with pytest.raises(ValidationError, match=r"\{seq\}"):
            load_repo_config(tmp_path)

    def test_an_unknown_reset_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="reset"):
            Numbering.model_validate({"reset": "monthly"})

    def test_an_unknown_key_is_rejected(self, tmp_path: Path) -> None:
        make_repo(tmp_path, MINIMAL_CONFIG + '\n[numbering]\nfromat = "INV-{seq}"\n')
        with pytest.raises(ValidationError, match="fromat"):
            load_repo_config(tmp_path)

    def test_a_missing_issuer_is_rejected(self, tmp_path: Path) -> None:
        make_repo(tmp_path, '[defaults]\ncurrency = "EUR"\n')
        with pytest.raises(ValidationError, match="issuer"):
            load_repo_config(tmp_path)

    def test_malformed_toml_raises_naming_the_file(self, tmp_path: Path) -> None:
        make_repo(tmp_path, "[issuer\n")
        with pytest.raises(ConfigError) as caught:
            load_repo_config(tmp_path)
        assert str(tmp_path / CONFIG_FILENAME) in caught.value.message

    def test_saving_leaves_a_file_the_user_can_read(self, tmp_path: Path) -> None:
        save_repo_config(tmp_path, make_repo_config())
        assert "[issuer]" in (tmp_path / CONFIG_FILENAME).read_text(encoding="utf-8")


class TestIsRepo:
    def test_a_directory_with_a_config_is_a_repo(self, tmp_path: Path) -> None:
        assert is_repo(make_repo(tmp_path / "data"))

    def test_a_directory_without_one_is_not(self, tmp_path: Path) -> None:
        (tmp_path / "empty").mkdir()
        assert not is_repo(tmp_path / "empty")

    def test_a_missing_directory_is_not(self, tmp_path: Path) -> None:
        assert not is_repo(tmp_path / "nowhere")


class TestResolveRepo:
    def test_the_flag_wins_over_everything(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_home: Path
    ) -> None:
        flagged = make_repo(tmp_path / "flagged")
        monkeypatch.setenv(REPO_ENV_VAR, str(make_repo(tmp_path / "env")))
        save_user_config(UserConfig(repo=make_repo(tmp_path / "configured")))
        make_repo(fake_home / "billkeeper")
        assert resolve_repo(flagged) == flagged

    def test_the_environment_wins_over_the_user_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_home: Path
    ) -> None:
        from_env = make_repo(tmp_path / "env")
        monkeypatch.setenv(REPO_ENV_VAR, str(from_env))
        save_user_config(UserConfig(repo=make_repo(tmp_path / "configured")))
        make_repo(fake_home / "billkeeper")
        assert resolve_repo() == from_env

    def test_the_user_config_wins_over_the_default(self, tmp_path: Path, fake_home: Path) -> None:
        configured = make_repo(tmp_path / "configured")
        save_user_config(UserConfig(repo=configured))
        make_repo(fake_home / "billkeeper")
        assert resolve_repo() == configured

    def test_the_default_is_the_last_resort(self, fake_home: Path) -> None:
        expected = make_repo(fake_home / "billkeeper")
        assert resolve_repo() == expected

    def test_a_home_relative_path_is_expanded(self, fake_home: Path) -> None:
        make_repo(fake_home / "elsewhere")
        assert resolve_repo(Path("~/elsewhere")) == fake_home / "elsewhere"

    def test_a_home_relative_user_config_is_expanded(self, fake_home: Path) -> None:
        expected = make_repo(fake_home / "elsewhere")
        save_user_config(UserConfig(repo=Path("~/elsewhere")))
        assert resolve_repo() == expected

    def test_a_relative_path_is_made_absolute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        expected = make_repo(tmp_path / "data")
        monkeypatch.chdir(tmp_path)
        assert resolve_repo(Path("data")) == expected

    def test_an_empty_environment_variable_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch, fake_home: Path
    ) -> None:
        monkeypatch.setenv(REPO_ENV_VAR, "")
        expected = make_repo(fake_home / "billkeeper")
        assert resolve_repo() == expected

    def test_a_stale_user_config_falls_through_to_the_default(
        self, tmp_path: Path, fake_home: Path
    ) -> None:
        save_user_config(UserConfig(repo=tmp_path / "moved-away"))
        expected = make_repo(fake_home / "billkeeper")
        assert resolve_repo() == expected

    def test_a_flagged_path_without_a_config_raises_naming_it(self, tmp_path: Path) -> None:
        (tmp_path / "empty").mkdir()
        with pytest.raises(RepoNotFoundError) as caught:
            resolve_repo(tmp_path / "empty")
        assert str((tmp_path / "empty").resolve()) in caught.value.message
        assert "billkeeper init" in caught.value.message

    def test_an_environment_path_without_a_config_raises_naming_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(REPO_ENV_VAR, str(tmp_path / "empty"))
        with pytest.raises(RepoNotFoundError) as caught:
            resolve_repo()
        assert str((tmp_path / "empty").resolve()) in caught.value.message
        assert REPO_ENV_VAR in caught.value.message

    def test_no_repo_anywhere_raises_the_default_message(self) -> None:
        with pytest.raises(RepoNotFoundError) as caught:
            resolve_repo()
        assert caught.value.message == (
            "No billkeeper data repo found. Run 'billkeeper init' to create one."
        )
        assert "\n" not in caught.value.message
