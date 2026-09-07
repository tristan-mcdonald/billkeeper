"""The `git` binary, wrapped just enough to keep the data repo committed.

billkeeper's store is a git repository and every mutating command ends with a
commit, so the tool has to run git — but it needs very little of it. There is
no git library here on purpose: shelling out to the binary the user already has
means their data repo behaves in billkeeper's hands exactly as it does in their
own terminal, with their config, their hooks, and a history they can read with
the commands they already know.

Three habits keep that safe. Every call is an argv list and never a shell
string, so a client called `; rm -rf ~` is a filename and nothing else.
Commit signing is switched off for the calls billkeeper makes, because a
signing setup that prompts or fails would strand an invoice that is already
written to disk. And the identity is the user's own wherever they have one:
billkeeper supplies a stand-in only for the half that is missing, so a fresh
container can commit while a configured machine is left alone.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from billkeeper.errors import GitError

#: The name and address commits are made under when the user has neither.
FALLBACK_NAME: Final = "billkeeper"
FALLBACK_EMAIL: Final = "billkeeper@localhost"

#: The branch a fresh data repo starts on.
DEFAULT_BRANCH: Final = "main"

#: The git settings billkeeper imposes on every call it makes.
_FORCED_CONFIG: Final = ("commit.gpgsign=false",)

_INSTALL_HINT: Final = (
    "billkeeper keeps your invoices in a git repository, so it needs the git "
    "command. Install it from https://git-scm.com/downloads and try again."
)


def git_available() -> bool:
    """Return whether there is a `git` to run."""
    return shutil.which("git") is not None


def require_git() -> str:
    """Return the path to the `git` binary, or say how to get one."""
    found = shutil.which("git")
    if found is None:
        raise GitError(f"Cannot find git on PATH. {_INSTALL_HINT}")
    return found


def is_git_repo(path: Path) -> bool:
    """Return whether `path` is the root of a git repository.

    Asked of the directory itself, not of the tree above it: a data repo that
    is really a subdirectory of some larger repository is a mistake worth
    noticing, not a repo billkeeper should quietly commit into. The check reads
    the filesystem rather than running git, so it also answers when git is
    missing. `.git` is a directory in a normal clone and a file in a worktree,
    hence `exists` rather than `is_dir`.
    """
    return (path / ".git").exists()


def init_repo(path: Path) -> None:
    """Make `path` a git repository, creating it if it is not there yet.

    The repository is left empty rather than given a first commit: what goes
    into it is the caller's business, and `commit` is safe to call on a
    repository with no history. Re-running against a repository that already
    exists does nothing, so adopting a data repo someone has cloned is not a
    destructive act.
    """
    path.mkdir(parents=True, exist_ok=True)
    if is_git_repo(path):
        return
    # `init.defaultBranch` rather than `--initial-branch`: a git too old to
    # know the setting ignores it and falls back to its own default, where the
    # unknown flag would have been fatal.
    run_git(path, "-c", f"init.defaultBranch={DEFAULT_BRANCH}", "init")


def commit(repo: Path, paths: Sequence[Path], message: str) -> str | None:
    """Stage exactly `paths`, commit them under `message`, and return the short SHA.

    Returns `None` when there was nothing to commit — no paths, or paths that
    match nothing git can act on, or paths whose content is already what is
    recorded. A command that changed nothing should leave no empty commit
    behind, and should not fail either.
    """
    specs = [_pathspec(repo, path) for path in paths]
    if not specs:
        return None

    stageable = _stageable(repo, specs)
    if not stageable:
        return None

    # `-A` so that a file the command deleted — a draft that has just been
    # issued — is staged as the deletion it is, not left behind in the index.
    run_git(repo, "add", "-A", "--", *stageable)
    if not run_git(repo, "diff", "--cached", "--name-only", "--", *stageable):
        return None

    run_git(repo, "commit", "-m", message)
    return run_git(repo, "rev-parse", "--short", "HEAD")


def head_sha(repo: Path) -> str | None:
    """Return the short SHA of the current commit, or `None` before the first."""
    if not is_git_repo(repo):
        return None
    # `--verify --quiet` is how git says "no commits yet" without an error:
    # it exits non-zero and prints nothing, which is exactly the answer wanted.
    return _run(repo, "rev-parse", "--verify", "--quiet", "--short", "HEAD").stdout.strip() or None


def run_git(repo: Path, *args: str) -> str:
    """Run `git args…` inside `repo` and return what it printed."""
    done = _run(repo, *args)
    if done.returncode != 0:
        stderr = done.stderr.strip()
        raise GitError(
            f"git {' '.join(args)} failed in {repo} (exit {done.returncode})"
            + (f": {stderr}" if stderr else "."),
            stderr=stderr,
        )
    return done.stdout.strip()


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one git command, and hand the result back however it went."""
    git = require_git()
    if not repo.is_dir():
        raise GitError(f"There is no directory at {repo} for git to work in.")
    argv = [git]
    for setting in (*_FORCED_CONFIG, *_identity_config(repo)):
        argv += ["-c", setting]
    argv += args
    return subprocess.run(argv, cwd=repo, capture_output=True, text=True, check=False)


def _identity_config(repo: Path) -> list[str]:
    """Return `user.…` settings for whichever half of the identity git lacks.

    Filled in one key at a time rather than as a pair: a machine that has a
    name configured but no address should commit under that name, and only
    borrow billkeeper's address.
    """
    configured = _configured_identity(repo)
    settings = []
    if "user.name" not in configured:
        settings.append(f"user.name={FALLBACK_NAME}")
    if "user.email" not in configured:
        settings.append(f"user.email={FALLBACK_EMAIL}")
    return settings


def _configured_identity(repo: Path) -> set[str]:
    """Return which of `user.name` and `user.email` git already answers with here.

    Read with a bare `subprocess.run` rather than through `run_git`, which
    would ask this same question again to build its own argv.
    """
    git = require_git()
    done = subprocess.run(
        [git, "config", "--get-regexp", r"^user\.(name|email)$"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    found = set()
    for line in done.stdout.splitlines():
        key, _, value = line.partition(" ")
        if value.strip():
            found.add(key)
    return found


def _pathspec(repo: Path, path: Path) -> str:
    """Return `path` as git should be told about it: relative to the repo.

    Absolute paths work too, but only when they spell the repo the same way
    git does; on macOS `/tmp` and `/private/tmp` are the same directory and
    not the same string.
    """
    try:
        return str(path.resolve().relative_to(repo.resolve()))
    except ValueError:
        return str(path)


def _stageable(repo: Path, specs: Sequence[str]) -> list[str]:
    """Return the pathspecs git can act on: they are there, or it already tracks them.

    A pathspec matching nothing at all is fatal to `git add`, and a caller
    naming a file that was never written — a PDF whose render failed — is
    asking for a commit of everything else, not for an error.
    """
    tracked = set(run_git(repo, "ls-files", "--", *specs).splitlines())
    return [
        spec
        for spec in specs
        if (repo / spec).exists() or any(_covers(spec, path) for path in tracked)
    ]


def _covers(spec: str, tracked: str) -> bool:
    return tracked == spec or tracked.startswith(f"{spec}/")
