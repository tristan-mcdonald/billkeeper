"""Tests for `billkeeper client`, against a real data repo and a real git.

The editor is a shell script written per test. Standing in a fake `$EDITOR` is
the only way to exercise `client edit` without a person at a terminal, and it
has the pleasant side effect of testing exactly what the command promises: that
whatever the editor leaves on disk is what gets read back and committed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from billkeeper.cli import app
from billkeeper.cli.client import EMPTY_MESSAGE, NO_CHANGES_MESSAGE
from billkeeper.gitrepo import git_available, run_git
from billkeeper.storage import Repo

from conftest import REPO_CURRENCY

pytestmark = pytest.mark.skipif(not git_available(), reason="git is not installed")

runner = CliRunner()

# Rich colours a table whenever it thinks it is talking to a terminal, and the
# escapes land in the middle of the words being looked for.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

ACME = """\
slug = "acme"
name = "Acme Holdings"
currency = "EUR"
"""


def plain(text: str) -> str:
    """Return `text` with any colour Rich put in it removed."""
    return ANSI_RE.sub("", text)


def client(*args: str, stdin: str = "") -> Result:
    """Run `billkeeper client args…`."""
    return runner.invoke(app, ["client", *args], input=stdin)


def commit_count(repo: Repo) -> int:
    return int(run_git(repo.root, "rev-list", "--count", "HEAD"))


def head_message(repo: Repo) -> str:
    return run_git(repo.root, "log", "-1", "--pretty=%s")


def editor_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> None:
    """Stand in an `$EDITOR` that runs `body`, with the file to edit as `$1`."""
    script = tmp_path / "fake-editor.sh"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(script))


def editor_writing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str) -> None:
    """Stand in an `$EDITOR` that replaces the file with `content`."""
    heredoc = f"cat > \"$1\" <<'BILLKEEPER_EOF'\n{content}BILLKEEPER_EOF"
    editor_running(monkeypatch, tmp_path, heredoc)


def add_acme(repo: Repo, *args: str) -> Result:
    """Add one client to have something to look at, and check that it took."""
    result = client("add", "--name", "Acme Ltd", *args)
    assert result.exit_code == 0, result.output
    return result


class TestAdd:
    def test_files_the_client_under_a_slug_of_its_name(self, repo: Repo) -> None:
        result = client("add", "--name", "Åsa Björk Ltd")

        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == "asa-bjork-ltd"
        assert repo.client_path("asa-bjork-ltd").is_file()

    def test_commits_once_saying_what_it_did(self, repo: Repo) -> None:
        before = commit_count(repo)

        client("add", "--name", "Acme Ltd")

        assert commit_count(repo) == before + 1
        assert head_message(repo) == "Add client acme-ltd"
        assert run_git(repo.root, "status", "--porcelain") == ""

    def test_records_every_field_it_was_given(self, repo: Repo) -> None:
        client(
            "add",
            "--name",
            "Acme Ltd",
            "--email",
            "ap@acme.invalid",
            "--address",
            "1 Example Street\nExampleton",
            "--contact",
            "A. Payer",
            "--currency",
            "gbp",
            "--notes",
            "Pays late.",
        )

        stored = repo.read_client("acme-ltd")
        assert stored.name == "Acme Ltd"
        assert stored.email == "ap@acme.invalid"
        assert stored.address == "1 Example Street\nExampleton"
        assert stored.contact == "A. Payer"
        assert stored.currency == "GBP"
        assert stored.notes == "Pays late."

    def test_inherits_the_repos_currency_when_none_is_given(self, repo: Repo) -> None:
        add_acme(repo)

        assert repo.read_client("acme-ltd").currency == REPO_CURRENCY

    def test_leaves_out_the_fields_that_were_not_given(self, repo: Repo) -> None:
        add_acme(repo)

        stored = repo.read_client("acme-ltd")
        assert stored.email is None
        assert stored.address is None
        assert "email" not in repo.client_path("acme-ltd").read_text(encoding="utf-8")

    def test_clients_of_the_same_name_are_filed_beside_each_other(self, repo: Repo) -> None:
        slugs = [add_acme(repo).stdout.strip() for _ in range(3)]

        assert slugs == ["acme-ltd", "acme-ltd-2", "acme-ltd-3"]
        assert all(repo.read_client(slug).name == "Acme Ltd" for slug in slugs)

    def test_files_under_an_explicit_slug(self, repo: Repo) -> None:
        result = add_acme(repo, "--slug", "acme")

        assert result.stdout.strip() == "acme"
        assert repo.client_path("acme").is_file()

    def test_refuses_an_explicit_slug_that_is_taken(self, repo: Repo) -> None:
        add_acme(repo, "--slug", "acme")
        before = commit_count(repo)

        result = client("add", "--name", "Acme Ltd", "--slug", "acme")

        assert result.exit_code == 1
        assert "acme" in result.stderr
        assert commit_count(repo) == before
        assert repo.read_client("acme").name == "Acme Ltd"

    def test_refuses_a_slug_that_is_not_one(self, repo: Repo) -> None:
        result = client("add", "--name", "Acme Ltd", "--slug", "Not A Slug")

        assert result.exit_code == 1
        assert "not-a-slug" in result.stderr
        assert commit_count(repo) == 1

    def test_asks_for_a_name_that_was_not_given(self, repo: Repo) -> None:
        result = client("add", stdin="Prompted Ltd\n")

        assert result.exit_code == 0, result.output
        assert repo.read_client("prompted-ltd").name == "Prompted Ltd"

    def test_refuses_a_name_with_nothing_to_make_a_slug_from(self, repo: Repo) -> None:
        result = client("add", "--name", "!!!")

        assert result.exit_code == 1
        assert commit_count(repo) == 1


class TestList:
    def test_says_so_when_there_are_none(self, repo: Repo) -> None:
        result = client("list")

        assert result.exit_code == 0, result.output
        assert plain(result.stdout).strip() == EMPTY_MESSAGE

    def test_shows_every_client_in_slug_order(self, repo: Repo) -> None:
        client("add", "--name", "Zebra Ltd", "--email", "z@example.invalid")
        client("add", "--name", "Acme Ltd", "--currency", "GBP")

        result = client("list")

        assert result.exit_code == 0, result.output
        out = plain(result.stdout)
        for expected in ("acme-ltd", "Acme Ltd", "GBP", "zebra-ltd", "z@example.invalid"):
            assert expected in out
        assert out.index("acme-ltd") < out.index("zebra-ltd")


class TestShow:
    def test_prints_every_field_one_per_line(self, repo: Repo) -> None:
        client("add", "--name", "Acme Ltd", "--email", "ap@acme.invalid", "--slug", "acme")

        result = client("show", "acme")

        assert result.exit_code == 0, result.output
        lines = result.stdout.splitlines()
        assert "Slug:     acme" in lines
        assert "Name:     Acme Ltd" in lines
        assert f"Currency: {REPO_CURRENCY}" in lines
        assert "Email:    ap@acme.invalid" in lines
        assert "Contact:" in lines

    def test_lines_a_multi_line_address_up_under_its_label(self, repo: Repo) -> None:
        client("add", "--name", "Acme Ltd", "--address", "1 Example Street\nExampleton")

        result = client("show", "acme-ltd")

        lines = result.stdout.splitlines()
        assert "Address:  1 Example Street" in lines
        assert f"{'':<10}Exampleton" in lines

    def test_an_unknown_slug_names_it_and_says_how_to_look(self, repo: Repo) -> None:
        result = client("show", "nobody")

        assert result.exit_code == 1
        assert "nobody" in result.stderr
        assert "billkeeper client list" in result.stderr


class TestEdit:
    def test_commits_what_the_editor_left(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_acme(repo, "--slug", "acme")
        editor_writing(monkeypatch, tmp_path, ACME)
        before = commit_count(repo)

        result = client("edit", "acme")

        assert result.exit_code == 0, result.output
        assert repo.read_client("acme").name == "Acme Holdings"
        assert commit_count(repo) == before + 1
        assert head_message(repo) == "Edit client acme"
        assert run_git(repo.root, "status", "--porcelain") == ""

    def test_says_nothing_changed_when_the_editor_touched_nothing(self, repo: Repo) -> None:
        add_acme(repo, "--slug", "acme")
        before = commit_count(repo)

        result = client("edit", "acme")

        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == NO_CHANGES_MESSAGE
        assert commit_count(repo) == before

    def test_invalid_toml_stops_short_of_a_commit_and_keeps_the_text(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_acme(repo, "--slug", "acme")
        editor_writing(monkeypatch, tmp_path, 'name = "unclosed\n')
        before = commit_count(repo)

        result = client("edit", "acme")

        assert result.exit_code == 1
        assert str(repo.client_path("acme")) in result.stderr
        assert commit_count(repo) == before
        assert "unclosed" in repo.client_path("acme").read_text(encoding="utf-8")

    def test_a_field_the_model_refuses_stops_short_of_a_commit(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_acme(repo, "--slug", "acme")
        editor_writing(monkeypatch, tmp_path, 'slug = "acme"\nname = "Acme"\ncurrency = "nope"\n')
        before = commit_count(repo)

        result = client("edit", "acme")

        assert result.exit_code == 1
        assert commit_count(repo) == before

    def test_refuses_to_rename_the_slug(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_acme(repo, "--slug", "acme")
        editor_writing(monkeypatch, tmp_path, ACME.replace('"acme"', '"acme-holdings"'))
        before = commit_count(repo)

        result = client("edit", "acme")

        assert result.exit_code == 1
        assert "acme-holdings" in result.stderr
        assert commit_count(repo) == before

    def test_an_editor_that_fails_commits_nothing(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_acme(repo, "--slug", "acme")
        editor_running(monkeypatch, tmp_path, "exit 3")
        before = commit_count(repo)

        result = client("edit", "acme")

        assert result.exit_code == 1
        assert "3" in result.stderr
        assert commit_count(repo) == before

    def test_an_unknown_slug_never_reaches_the_editor(
        self, repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        editor_running(monkeypatch, tmp_path, "exit 3")

        result = client("edit", "nobody")

        assert result.exit_code == 1
        assert "billkeeper client list" in result.stderr


def test_a_client_command_with_no_repo_says_to_run_init(sandbox: Path) -> None:
    """The sub-app is dispatched through the same shell, so it fails the same way."""
    result = client("list")

    assert result.exit_code == 1
    assert "billkeeper init" in result.stderr
    assert "Traceback" not in result.output
