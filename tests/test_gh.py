from pathlib import Path

import pytest

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

    run_gh(["pr", "comment", "128", "-R", "acme/web", "--body-file", "-"], "テストが足りない")

    assert received.read_text(encoding="utf-8") == (
        "pr comment 128 -R acme/web --body-file -\nテストが足りない"
    )


def test_reports_what_gh_said_when_it_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put_gh_on_path(tmp_path, monkeypatch, "echo 'HTTP 404: Not Found' >&2\nexit 1")

    with pytest.raises(GhFailed, match="404"):
        run_gh(["pr", "ready", "128", "-R", "acme/web"], "")


def test_reports_that_gh_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(GhFailed, match="実行できなかった"):
        run_gh(["pr", "ready", "128", "-R", "acme/web"], "")
