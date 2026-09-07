"""`billkeeper init`: make a data repo, or adopt one that is already there.

This is the one command that runs without a data repo, so it is also the one
that has to be careful with the directory it is pointed at. When there is
already a `config.toml` the answer is not an error and not a no-op either: a
user who has cloned their data repo onto a second machine is running `init`
precisely to say "this one is mine", so the path is written to the user config
and nothing inside the repo is touched.

Everything else is questions. Each answer is checked as it is given and asked
again if it will not do, because the alternative — failing at the end on the
currency typed three questions ago — throws away answers the user has already
taken the trouble to type.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Annotated, Final

import tomli_w
import typer
from babel.core import Locale, UnknownLocaleError

from billkeeper import config, gitrepo, sequence
from billkeeper.cli import AppContext
from billkeeper.config import DEFAULT_LOCALE, Defaults, Issuer, Numbering, RepoConfig, UserConfig
from billkeeper.errors import BillkeeperError, ConfigError, ValidationError
from billkeeper.money import normalize_currency
from billkeeper.numbering import DEFAULT_FORMAT, validate_format
from billkeeper.storage import Repo, atomic_write

#: The currency a data repo starts out in, when the user says nothing else.
DEFAULT_CURRENCY: Final = "USD"

#: The environment variable a desktop tells its programs the locale in.
LANG_ENV_VAR: Final = "LANG"

#: The directory of packaged templates, and the files copied out of it.
TEMPLATE_RESOURCE_DIR: Final = "templates"
TEMPLATE_NAMES: Final = ("invoice.md", "invoice.typ")

#: What a fresh data repo's first commit is recorded as.
INITIAL_COMMIT_MESSAGE: Final = "Initialise billkeeper data repo"

#: The file that keeps git interested in a directory with nothing in it yet.
GITKEEP: Final = ".gitkeep"

_NEXT_STEPS: Final = """
Next steps:
  billkeeper client add             add the first client
  billkeeper new --client <slug>    start a draft invoice
  billkeeper issue <id>             number it, render the PDF, and commit
"""


def init(
    ctx: typer.Context,
    path: Annotated[
        Path | None,
        typer.Argument(
            metavar="PATH",
            help="Where the data repo goes. Asked for if left out.",
        ),
    ] = None,
) -> None:
    """Set up a data repo, or start using one that already exists."""
    target = _target(ctx, path)

    if config.is_repo(target):
        _adopt(target)
        return

    repo = Repo(target)
    _write_repo(repo, _ask())
    gitrepo.init_repo(repo.root)
    gitrepo.commit(
        repo.root,
        [
            repo.config_path,
            repo.sequence_path,
            repo.clients_dir,
            repo.invoices_dir,
            repo.templates_dir,
        ],
        INITIAL_COMMIT_MESSAGE,
    )
    _remember(repo.root)

    typer.echo(f"Initialised billkeeper in {repo.root}.")
    typer.echo(_NEXT_STEPS)


def _adopt(target: Path) -> None:
    """Take up the data repo already at `target` without disturbing it."""
    _remember(target)
    typer.echo(f"Already initialised: {config.repo_config_path(target)}")
    typer.echo(f"billkeeper will now use {target}.")


def _remember(target: Path) -> None:
    """Write `target` to the user config as the repo this machine works in.

    The file is written fresh rather than read and amended: where the data repo
    lives is the only thing it holds, and `init` is the command a user reaches
    for when their setup is in a mess, so it should mend that file rather than
    refuse to run because of it.
    """
    config.save_user_config(UserConfig(repo=target))


def _target(ctx: typer.Context, path: Path | None) -> Path:
    """Return the directory to initialise, asking for one only if nothing said."""
    named = ctx.ensure_object(AppContext).repo
    if path is not None and named is not None and not _same(path, named):
        raise ConfigError(
            f"Both --repo {named} and the path {path} were given, and they are not the "
            "same directory. Name the data repo once."
        )

    chosen = path or named
    if chosen is None:
        chosen = Path(
            _ask_for("Data repo path", _path, default=str(_suggested_path())),
        )
    return chosen.expanduser().resolve()


def _suggested_path() -> Path:
    """Return the path to offer at the prompt.

    `$BILLKEEPER_REPO` is offered rather than ignored: a user who has set it
    and then accepts a different default here would end up with a data repo
    that every later command looks straight past.
    """
    from_env = os.environ.get(config.REPO_ENV_VAR, "")
    if from_env.strip():
        return Path(from_env.strip())
    return Path("~") / config.DEFAULT_REPO_NAME


def _ask() -> RepoConfig:
    """Put the questions a fresh data repo needs answers to."""
    typer.echo("These go on every invoice, and can be changed later in config.toml.")
    issuer = Issuer(
        name=_ask_for("Your name or business name", _required),
        address=_ask_for("Address (use \\n for a line break)", _multiline, default=""),
        email=_ask_for("Email", _trimmed, default=""),
        payment_details=_ask_for(
            "Payment details (use \\n for a line break)", _multiline, default=""
        ),
    )
    defaults = Defaults(
        currency=_ask_for("Default currency", _currency, default=DEFAULT_CURRENCY),
        locale=_ask_for("Locale for writing amounts", _locale, default=_default_locale()),
    )
    numbering = Numbering(
        format=_ask_for("Invoice number format", _number_format, default=DEFAULT_FORMAT),
    )
    return RepoConfig(issuer=issuer, defaults=defaults, numbering=numbering)


def _ask_for[T](text: str, parse: Callable[[str], T], default: str | None = None) -> T:
    """Prompt until the answer is one billkeeper can use, and return it parsed.

    Every error billkeeper raises on purpose already says what was wrong with
    the value it was given, which is exactly what someone standing at a prompt
    needs, so all of them are shown and the question asked again.
    """
    while True:
        raw: str = typer.prompt(text, default=default, show_default=bool(default))
        try:
            return parse(raw)
        except BillkeeperError as error:
            typer.secho(error.message, err=True, fg=typer.colors.RED)


def _trimmed(value: str) -> str:
    return value.strip()


def _required(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValidationError("This one cannot be left empty.")
    return trimmed


def _multiline(value: str) -> str:
    r"""Return `value` with the `\n` the user typed turned into line breaks.

    A terminal prompt reads one line, and an address is several. Rather than
    open an editor for two fields, `init` takes the escape a user typing at a
    shell already knows and unfolds it here.
    """
    return value.strip().replace("\\n", "\n")


def _currency(value: str) -> str:
    return normalize_currency(value)


def _locale(value: str) -> str:
    """Return `value` as a locale Babel will write money in.

    Both spellings are accepted — `en_GB` and `en-GB` — and the answer is
    stored the way Babel writes it, so the config file settles on one form.
    """
    wanted = value.strip().replace("-", "_")
    try:
        return str(Locale.parse(wanted))
    except (ValueError, TypeError, UnknownLocaleError):
        raise ValidationError(
            f"{value!r} is not a locale Babel knows, so amounts could not be "
            f"written with it. Try one like {DEFAULT_LOCALE!r} or 'de_DE'."
        ) from None


def _number_format(value: str) -> str:
    validate_format(value)
    return value


def _path(value: str) -> Path:
    trimmed = value.strip()
    if not trimmed:
        raise ValidationError("Give a path for the data repo.")
    candidate = Path(trimmed).expanduser()
    if candidate.exists() and not candidate.is_dir():
        raise ValidationError(f"{candidate} is a file, so a data repo cannot go there.")
    return candidate


def _default_locale() -> str:
    """Return the locale to offer, taken from the desktop's `$LANG` if it parses."""
    # `en_GB.UTF-8` and `en_GB@euro` are both ordinary values of LANG, and
    # neither the encoding nor the modifier means anything to Babel.
    candidate = os.environ.get(LANG_ENV_VAR, "").split(".")[0].split("@")[0]
    try:
        return _locale(candidate)
    except ValidationError:
        return DEFAULT_LOCALE


def _write_repo(repo: Repo, cfg: RepoConfig) -> None:
    """Lay out an empty data repo at `repo.root` and fill in what it starts with."""
    if repo.root.exists() and not repo.root.is_dir():
        raise ConfigError(f"{repo.root} is a file, so a data repo cannot go there.")

    for directory in (repo.clients_dir, repo.drafts_dir, repo.templates_dir):
        directory.mkdir(parents=True, exist_ok=True)
    # git records files, not directories, so the two that start out empty need
    # something in them to survive being cloned.
    for directory in (repo.clients_dir, repo.drafts_dir):
        (directory / GITKEEP).touch()

    config.save_repo_config(repo.root, cfg)
    atomic_write(repo.sequence_path, tomli_w.dumps({sequence.NEXT_TABLE: {}}))
    _copy_templates(repo.templates_dir)


def _copy_templates(into: Path) -> None:
    """Copy the packaged default templates into the data repo.

    Copied rather than read from the package at render time: the templates are
    the user's to edit, and a template that changed under them when they
    upgraded billkeeper would rewrite invoices they had already sent.
    """
    packaged = resources.files("billkeeper") / TEMPLATE_RESOURCE_DIR
    for name in TEMPLATE_NAMES:
        atomic_write(into / name, (packaged / name).read_text(encoding="utf-8"))


def _same(one: Path, other: Path) -> bool:
    return one.expanduser().resolve() == other.expanduser().resolve()
