import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from server import ROOT, DashboardServer, TriageStore


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
def port(store: TriageStore) -> Iterator[int]:
    server = DashboardServer(0, store, ROOT / "static" / "index.html")
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield server.server_port
    server.shutdown()
    thread.join()
    server.server_close()
