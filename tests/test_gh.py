from pathlib import Path

import pytest

import server
from server import GhFailed, run_gh


def put_gh_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")


def test_passes_the_arguments_and_stdin_to_gh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received = tmp_path / "received"
    put_gh_on_path(tmp_path, monkeypatch, f'echo "$@" > {received}\ncat >> {received}')

    run_gh(["pr", "comment", "128", "-R", "acme/web", "--body-file", "-"], "テストが足りない", None)

    assert received.read_text(encoding="utf-8") == (
        "pr comment 128 -R acme/web --body-file -\nテストが足りない"
    )


def test_reports_what_gh_said_when_it_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_gh_on_path(tmp_path, monkeypatch, "echo 'HTTP 404: Not Found' >&2\nexit 1")

    with pytest.raises(GhFailed, match="404"):
        run_gh(["pr", "ready", "128", "-R", "acme/web"], "", None)


def test_tells_to_check_github_when_gh_takes_too_long(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_gh_on_path(tmp_path, monkeypatch, "sleep 5")
    monkeypatch.setattr(server, "GH_TIMEOUT_SECONDS", 1)

    with pytest.raises(GhFailed, match="GitHub で確かめる"):
        run_gh(["pr", "comment", "128", "-R", "acme/web", "--body-file", "-"], "理由", None)


def test_runs_gh_with_the_token_of_the_account_it_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received = tmp_path / "received"
    put_gh_on_path(
        tmp_path,
        monkeypatch,
        'if [ "$*" = "auth token --user acme-bot" ]; then echo token-of-acme-bot; exit 0; fi\n'
        f'echo "$GH_TOKEN $*" > {received}',
    )
    monkeypatch.setenv("GH_TOKEN", "token-the-server-was-started-with")

    run_gh(["pr", "ready", "128", "-R", "acme/web"], "", "acme-bot")

    assert received.read_text(encoding="utf-8") == "token-of-acme-bot pr ready 128 -R acme/web\n"


def test_does_not_run_gh_when_it_is_not_logged_in_as_the_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received = tmp_path / "received"
    put_gh_on_path(
        tmp_path,
        monkeypatch,
        'if [ "$1 $2" = "auth token" ]; then echo "no oauth token found" >&2; exit 1; fi\n'
        f'echo "$*" > {received}',
    )

    with pytest.raises(GhFailed, match="acme-bot のトークンを取れなかった.no oauth token found"):
        run_gh(["pr", "ready", "128", "-R", "acme/web"], "", "acme-bot")

    assert not received.exists()


def test_reports_that_gh_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(GhFailed, match="実行できなかった"):
        run_gh(["pr", "ready", "128", "-R", "acme/web"], "", None)
