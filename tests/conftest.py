import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from server import ROOT, DashboardServer, GhFailed, TriageStore


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
    }


@pytest.fixture
def store(tmp_path: Path) -> Iterator[TriageStore]:
    store = TriageStore(tmp_path / "prs.db")
    yield store
    store.close()


@pytest.fixture
def gh_calls() -> list[tuple[list[str], str]]:
    return []


@pytest.fixture
def gh_failure() -> list[str]:
    return []


@pytest.fixture
def port(
    store: TriageStore, gh_calls: list[tuple[list[str], str]], gh_failure: list[str]
) -> Iterator[int]:
    def fake_gh(arguments: list[str], stdin: str) -> None:
        gh_calls.append((arguments, stdin))
        if gh_failure:
            raise GhFailed(gh_failure[0])

    server = DashboardServer(0, store, ROOT / "static" / "index.html", fake_gh)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield server.server_port
    server.shutdown()
    thread.join()
    server.server_close()
