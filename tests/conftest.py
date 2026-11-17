from __future__ import annotations

import os
import signal
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

import pytest

from server import (
    HOST,
    PORT,
    ROOT,
    DashboardServer,
    DecisionError,
    FetchStates,
    GhFailed,
    ResumeSession,
    RunGh,
    Session,
    TriageStore,
)


def dashboard_is_listening() -> bool:
    with socket.socket() as probe:
        return probe.connect_ex((HOST, PORT)) == 0


@pytest.fixture
def dashboard_home(tmp_path: Path) -> Iterator[Path]:
    if dashboard_is_listening():
        pytest.skip(f"{HOST}:{PORT} でダッシュボードが動いているので、起動は試さない")
    home = tmp_path / "home"
    yield home
    with suppress(FileNotFoundError, ProcessLookupError):
        os.kill(int((home / "server.pid").read_text(encoding="utf-8")), signal.SIGTERM)
    deadline = time.monotonic() + 5
    while dashboard_is_listening() and time.monotonic() < deadline:
        time.sleep(0.1)


@pytest.fixture
def report() -> dict[str, object]:
    return {
        "repo": "acme/web",
        "number": 128,
        "url": "https://github.com/acme/web/pull/128",
        "title": "検索APIにページングを追加",
        "branch": "feat/search-paging",
        "changes": {"files": 8, "additions": 120, "deletions": 30},
        "summary": [
            "検索APIにcursor形式のページングを追加",
            "既存のoffset指定は非推奨にして互換を維持",
            "フロントの一覧画面を無限スクロールに変更",
        ],
        "why": "件数が多いと検索がタイムアウトする問題の対応",
        "risk": "medium",
        "risk_reason": "公開APIのレスポンス形式が変わる",
        "concerns": ["並び順が同値のときのcursorの扱い"],
        "tests": {"status": "passed", "detail": "unit 42件、e2e 3件"},
        "suggested_reviewers": [],
        "artifact_url": "https://claude.ai/artifact/example",
        "session": None,
        "account": None,
    }


@pytest.fixture
def store(tmp_path: Path) -> Iterator[TriageStore]:
    store = TriageStore(tmp_path / "prs.db")
    yield store
    store.close()


@pytest.fixture
def gh_calls() -> list[tuple[list[str], str, str | None]]:
    return []


@pytest.fixture
def gh_failures() -> dict[str, str]:
    return {}


@pytest.fixture
def recording_gh(
    gh_calls: list[tuple[list[str], str, str | None]], gh_failures: dict[str, str]
) -> RunGh:
    def run(arguments: list[str], stdin: str, account: str | None) -> None:
        gh_calls.append((arguments, stdin, account))
        if arguments[1] in gh_failures:
            raise GhFailed(gh_failures[arguments[1]])

    return run


@pytest.fixture
def resumed() -> list[tuple[Session, str, str | None]]:
    return []


@pytest.fixture
def resume_failures() -> list[DecisionError]:
    return []


@pytest.fixture
def recording_resume(
    resumed: list[tuple[Session, str, str | None]], resume_failures: list[DecisionError]
) -> ResumeSession:
    def resume(session: Session, prompt: str, account: str | None) -> None:
        if resume_failures:
            raise resume_failures[0]
        resumed.append((session, prompt, account))

    return resume


@pytest.fixture
def github_states() -> dict[tuple[str, int], str]:
    return {}


@pytest.fixture
def state_requests() -> list[list[tuple[str, int, str | None]]]:
    return []


@pytest.fixture
def state_failures() -> list[GhFailed]:
    return []


@pytest.fixture
def recording_states(
    github_states: dict[tuple[str, int], str],
    state_requests: list[list[tuple[str, int, str | None]]],
    state_failures: list[GhFailed],
) -> FetchStates:
    def fetch(pull_requests: list[tuple[str, int, str | None]]) -> dict[tuple[str, int], str]:
        state_requests.append(pull_requests)
        if state_failures:
            raise state_failures[0]
        return {
            (repo, number): github_states[repo, number]
            for repo, number, _ in pull_requests
            if (repo, number) in github_states
        }

    return fetch


@pytest.fixture
def port(
    store: TriageStore,
    recording_gh: RunGh,
    recording_resume: ResumeSession,
    recording_states: FetchStates,
) -> Iterator[int]:
    server = DashboardServer(
        0, store, ROOT / "static" / "index.html", recording_gh, recording_resume, recording_states
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield server.server_port
    server.shutdown()
    thread.join()
    server.server_close()
