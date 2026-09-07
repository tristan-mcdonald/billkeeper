"""Where the data repo is, and what the data repo says about itself.

There are two configuration files and they answer different questions. The
user-level one, in `~/.config/billkeeper/config.toml`, only remembers *where*
the data repo is; it is the one thing that cannot live inside the repo. The
repo-level one, `config.toml` at the root of the data repo, holds everything
else — who is issuing the invoices, what currency and terms they default to,
and how invoice numbers are shaped — so that a data repo cloned onto another
machine carries its own settings with it.

Finding the repo is deliberately layered: an explicit `--repo`, then
`BILLKEEPER_REPO`, then the user config, then `~/billkeeper`. When none of them
lead anywhere, the answer is always the same single line telling the user to
run `billkeeper init`.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Annotated, Any, Final, Literal

import tomli_w
from pydantic import Field, field_validator

from billkeeper.errors import ConfigError, RepoNotFoundError, ValidationError
from billkeeper.models import DEFAULT_PAYMENT_TERMS_DAYS, BillkeeperModel, CurrencyCode, Text
from billkeeper.numbering import DEFAULT_FORMAT, validate_format

#: The file that marks a directory as a billkeeper data repo.
CONFIG_FILENAME: Final = "config.toml"

#: The environment variable that overrides where the data repo is.
REPO_ENV_VAR: Final = "BILLKEEPER_REPO"

#: The environment variable that moves the user-level config directory.
XDG_CONFIG_HOME_ENV: Final = "XDG_CONFIG_HOME"

#: The data repo's directory name under the user's home directory.
DEFAULT_REPO_NAME: Final = "billkeeper"

#: The locale used to write money when the data repo does not say otherwise.
DEFAULT_LOCALE: Final = "en_US"


def user_config_dir() -> Path:
    """Return the directory holding the user-level config."""
    xdg = os.environ.get(XDG_CONFIG_HOME_ENV)
    if xdg and xdg.strip():
        return _absolute(Path(xdg) / "billkeeper")
    return _absolute(Path.home() / ".config" / "billkeeper")


def user_config_path() -> Path:
    """Return the user-level config file, which need not exist."""
    return user_config_dir() / CONFIG_FILENAME


def default_repo() -> Path:
    """Return where a data repo lives when nothing points anywhere else."""
    return _absolute(Path.home() / DEFAULT_REPO_NAME)


def is_repo(path: Path) -> bool:
    """Return whether `path` looks like a billkeeper data repo."""
    return (path / CONFIG_FILENAME).is_file()


class UserConfig(BillkeeperModel):
    """The user-level config: where this machine keeps its data repo."""

    repo: Path | None = None


class Issuer(BillkeeperModel):
    """Who is sending the invoice, as it should appear on the document."""

    name: Text
    address: str = ""
    email: str = ""
    payment_details: str = ""
    """Free text, usually several lines, saying how to pay: bank details, an
    IBAN, a payment link. billkeeper never parses it, it only prints it."""


class Defaults(BillkeeperModel):
    """What a new client or invoice starts out with."""

    currency: CurrencyCode
    locale: str = DEFAULT_LOCALE
    payment_terms_days: Annotated[int, Field(ge=0)] = DEFAULT_PAYMENT_TERMS_DAYS


class Numbering(BillkeeperModel):
    """How invoice numbers are shaped and when the counter starts again."""

    format: str = DEFAULT_FORMAT
    reset: Literal["yearly", "never"] = "yearly"

    @field_validator("format")
    @classmethod
    def _format_is_usable(cls, value: str) -> str:
        try:
            validate_format(value)
        except ValidationError as error:
            raise ValueError(error.message) from None
        return value


class RepoConfig(BillkeeperModel):
    """The data repo's own `config.toml`."""

    issuer: Issuer
    defaults: Defaults
    numbering: Numbering = Field(default_factory=Numbering)


def load_user_config() -> UserConfig:
    """Return the user-level config, empty when there is no file yet."""
    path = user_config_path()
    if not path.is_file():
        return UserConfig()
    return UserConfig(**_read_toml(path))


def save_user_config(cfg: UserConfig) -> None:
    """Write `cfg` to the user-level config, creating the directory."""
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if cfg.repo is not None:
        data["repo"] = str(cfg.repo)
    _write_toml(path, data)


def repo_config_path(repo: Path) -> Path:
    """Return the `config.toml` of the data repo rooted at `repo`."""
    return repo / CONFIG_FILENAME


def load_repo_config(repo: Path) -> RepoConfig:
    """Return the config of the data repo rooted at `repo`."""
    return RepoConfig(**_read_toml(repo_config_path(repo)))


def save_repo_config(repo: Path, cfg: RepoConfig) -> None:
    """Write `cfg` to the data repo rooted at `repo`."""
    path = repo_config_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_toml(path, cfg.model_dump(mode="json"))


def resolve_repo(explicit: Path | None = None) -> Path:
    """Return the data repo to work in, or say how to make one.

    The first of `explicit`, `$BILLKEEPER_REPO`, the user config, and
    `~/billkeeper` that holds a `config.toml` wins. A path the user named
    outright is not silently skipped when it turns out to be empty: being
    quietly sent to a different repo than the one asked for is worse than
    being told this one is not there.
    """
    if explicit is not None:
        return _require_repo(explicit, "--repo")

    from_env = os.environ.get(REPO_ENV_VAR)
    if from_env and from_env.strip():
        return _require_repo(Path(from_env.strip()), REPO_ENV_VAR)

    configured = load_user_config().repo
    if configured is not None:
        candidate = _absolute(configured)
        if is_repo(candidate):
            return candidate

    fallback = default_repo()
    if is_repo(fallback):
        return fallback

    raise RepoNotFoundError()


def _absolute(path: Path) -> Path:
    return path.expanduser().resolve()


def _require_repo(path: Path, source: str) -> Path:
    resolved = _absolute(path)
    if not is_repo(resolved):
        raise RepoNotFoundError(
            f"No billkeeper data repo at {resolved} (from {source}): "
            f"it has no {CONFIG_FILENAME}. Run 'billkeeper init' to create one."
        )
    return resolved


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc.strerror}.") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from None


def _write_toml(path: Path, data: dict[str, Any]) -> None:
    try:
        with path.open("wb") as handle:
            # Multi-line strings written as multi-line strings: an address and
            # a set of payment details are several lines each, and a config
            # file meant to be hand-edited should not show them as one long
            # line with `\n` in it.
            tomli_w.dump(data, handle, multiline_strings=True)
    except OSError as exc:
        raise ConfigError(f"Cannot write {path}: {exc.strerror}.") from None
