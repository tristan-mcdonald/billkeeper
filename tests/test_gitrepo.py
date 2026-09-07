"""Tests for the git wrapper, run against the real `git` binary.

Nothing here is mocked: the point of the module is that it drives the same git
the user has, so a test that stood in for it would prove nothing. Every test
runs in a sandbox with no global or system git config, which is both the
isolation the developer's own identity needs and the fresh-container case the
wrapper has to work in.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from billkeeper.errors import GitError
from billkeeper.gitrepo import (
    FALLBACK_EMAIL,
    FALLBACK_NAME,
    commit,
    git_available,
    head_sha,
    init_repo,
    is_git_repo,
    require_git,
    run_git,
)

pytestmark = pytest.mark.skipif(not git_available(), reason="git is not installed")


@pytest.fixture(autouse=True)
def sandboxed_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cut git off from every config file outside `tmp_path`."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-such-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GNUPGHOME", str(tmp_path / "no-such-gnupg"))
    for who in ("AUTHOR", "COMMITTER"):
        for part in ("NAME", "EMAIL"):
            monkeypatch.delenv(f"GIT_{who}_{part}", raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Return an initialised, empty data repo."""
    root = tmp_path / "data"
    init_repo(root)
    return root


def write(repo: Path, name: str, body: str = "hello\n") -> Path:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def log(repo: Path, *args: str) -> str:
    return run_git(repo, "log", *args)


class TestAvailability:
    def test_git_is_found(self) -> None:
        assert git_available()
        assert Path(require_git()).name.startswith("git")

    def test_a_missing_git_says_how_to_get_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("billkeeper.gitrepo.shutil.which", lambda _: None)

        assert not git_available()
        with pytest.raises(GitError, match=r"git-scm\.com"):
            require_git()


class TestInit:
    def test_it_creates_a_repository(self, tmp_path: Path) -> None:
        root = tmp_path / "data"

        init_repo(root)

        assert is_git_repo(root)
        assert run_git(root, "rev-parse", "--is-inside-work-tree") == "true"

    def test_it_creates_the_directory_it_is_given(self, tmp_path: Path) -> None:
        init_repo(tmp_path / "nested" / "data")

        assert (tmp_path / "nested" / "data" / ".git").exists()

    def test_a_fresh_repository_has_no_commit(self, repo: Path) -> None:
        assert head_sha(repo) is None

    def test_it_leaves_an_existing_repository_alone(self, repo: Path) -> None:
        write(repo, "one.toml")
        first = commit(repo, [repo / "one.toml"], "Add one")

        init_repo(repo)

        assert head_sha(repo) == first

    def test_a_plain_directory_is_not_a_repository(self, tmp_path: Path) -> None:
        assert not is_git_repo(tmp_path)
        assert head_sha(tmp_path) is None


class TestCommit:
    def test_a_new_file_is_committed_and_the_sha_returned(self, repo: Path) -> None:
        path = write(repo, "clients/acme.toml")

        sha = commit(repo, [path], "Add client acme")

        assert sha is not None
        assert sha == head_sha(repo)
        assert log(repo, "-1", "--pretty=%s") == "Add client acme"
        assert run_git(repo, "ls-files") == "clients/acme.toml"

    def test_a_second_commit_with_nothing_changed_does_nothing(self, repo: Path) -> None:
        path = write(repo, "clients/acme.toml")
        first = commit(repo, [path], "Add client acme")

        assert commit(repo, [path], "Add client acme again") is None
        assert head_sha(repo) == first
        assert log(repo, "--oneline").count("\n") == 0

    def test_committing_no_paths_at_all_does_nothing(self, repo: Path) -> None:
        write(repo, "clients/acme.toml")

        assert commit(repo, [], "Nothing in particular") is None
        assert head_sha(repo) is None

    def test_a_path_that_was_never_written_is_passed_over(self, repo: Path) -> None:
        path = write(repo, "invoices/2026/INV-2026-0001.toml")
        missing = repo / "invoices" / "2026" / "INV-2026-0001.pdf"

        sha = commit(repo, [path, missing], "Issue INV-2026-0001")

        assert sha is not None
        assert run_git(repo, "ls-files") == "invoices/2026/INV-2026-0001.toml"

    def test_a_deletion_is_committed_too(self, repo: Path) -> None:
        draft = write(repo, "invoices/drafts/acme-20260301.toml")
        commit(repo, [draft], "Add draft")
        draft.unlink()
        issued = write(repo, "invoices/2026/INV-2026-0001.toml")

        sha = commit(repo, [draft, issued], "Issue INV-2026-0001")

        assert sha is not None
        assert run_git(repo, "ls-files") == "invoices/2026/INV-2026-0001.toml"

    def test_only_the_named_paths_are_committed(self, repo: Path) -> None:
        wanted = write(repo, "clients/acme.toml")
        write(repo, "clients/other.toml")

        commit(repo, [wanted], "Add client acme")

        assert run_git(repo, "ls-files") == "clients/acme.toml"

    def test_a_path_outside_the_repo_is_refused(self, repo: Path, tmp_path: Path) -> None:
        outside = tmp_path / "elsewhere.toml"
        outside.write_text("nope\n", encoding="utf-8")

        with pytest.raises(GitError):
            commit(repo, [outside], "Steal a file")

    def test_a_shell_metacharacter_in_a_name_is_just_a_name(self, repo: Path) -> None:
        name = "clients/acme; rm -rf ~.toml"
        path = write(repo, name)

        assert commit(repo, [path], "Add an awkwardly named client") is not None

        # Raises unless git tracks that exact path, punctuation and all.
        run_git(repo, "ls-files", "--error-unmatch", "--", name)
        assert path.is_file()


class TestIdentity:
    def test_it_commits_without_any_configured_identity(self, repo: Path) -> None:
        commit(repo, [write(repo, "one.toml")], "Add one")

        assert log(repo, "-1", "--pretty=%an <%ae>") == f"{FALLBACK_NAME} <{FALLBACK_EMAIL}>"

    def test_a_configured_identity_is_used_as_it_stands(self, repo: Path) -> None:
        run_git(repo, "config", "user.name", "Real Person")
        run_git(repo, "config", "user.email", "real@example.invalid")

        commit(repo, [write(repo, "one.toml")], "Add one")

        assert log(repo, "-1", "--pretty=%an <%ae>") == "Real Person <real@example.invalid>"

    def test_only_the_missing_half_is_filled_in(self, repo: Path) -> None:
        run_git(repo, "config", "user.name", "Real Person")

        commit(repo, [write(repo, "one.toml")], "Add one")

        assert log(repo, "-1", "--pretty=%an <%ae>") == f"Real Person <{FALLBACK_EMAIL}>"

    def test_a_signing_setup_does_not_block_the_commit(self, repo: Path) -> None:
        run_git(repo, "config", "commit.gpgsign", "true")
        run_git(repo, "config", "user.signingkey", "0000000000000000")

        assert commit(repo, [write(repo, "one.toml")], "Add one") is not None


class TestFailures:
    def test_a_failing_command_carries_the_stderr(self, repo: Path) -> None:
        with pytest.raises(GitError) as caught:
            run_git(repo, "rev-parse", "--verify", "refs/heads/no-such-branch")

        assert caught.value.stderr
        assert caught.value.stderr in caught.value.message
        assert "rev-parse" in caught.value.message

    def test_an_unknown_subcommand_fails_loudly(self, repo: Path) -> None:
        with pytest.raises(GitError, match="not a git command"):
            run_git(repo, "invent")

    def test_a_missing_directory_is_named(self, tmp_path: Path) -> None:
        with pytest.raises(GitError, match="no directory"):
            run_git(tmp_path / "nowhere", "status")

    def test_git_is_never_run_through_a_shell(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[object, dict[str, Any]]] = []
        real = subprocess.run

        def spy(argv: list[str], **kwargs: Any) -> Any:
            calls.append((argv, kwargs))
            return real(argv, **kwargs)

        monkeypatch.setattr(subprocess, "run", spy)
        run_git(repo, "status", "--porcelain")

        assert calls
        for argv, kwargs in calls:
            assert isinstance(argv, list)
            assert not kwargs.get("shell", False)
