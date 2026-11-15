import json
import os
import socket
import subprocess
from pathlib import Path

import pytest

from server import HOST, ROOT, TriageStore

SCRIPT = ROOT / "scripts" / "report-pr.sh"
FACTS_FROM_GH = ("repo", "number", "url", "title", "branch", "changes")
GH_PR_VIEW = {
    "number": 128,
    "url": "https://github.com/acme/web/pull/128",
    "title": "検索APIにページングを追加",
    "headRefName": "feat/search-paging",
    "additions": 120,
    "deletions": 30,
    "changedFiles": 8,
}


@pytest.fixture
def fake_gh(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "pr" ]; then\n'
        f"  echo '{json.dumps(GH_PR_VIEW, ensure_ascii=False)}'\n"
        "else\n"
        "  echo acme/web\n"
        "fi\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return bin_dir


def judgment_of(report: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in report.items() if key not in FACTS_FROM_GH}


def run_script(
    bin_dir: Path, judgment: dict[str, object], dashboard: str, inbox: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        input=json.dumps(judgment),
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "PR_DASHBOARD_URL": dashboard,
            "PR_DASHBOARD_INBOX": str(inbox),
        },
        check=False,
        timeout=30,
    )


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        port: int = probe.getsockname()[1]
    return port


def test_sends_the_judgment_with_the_facts_from_gh(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object], fake_gh: Path
) -> None:
    result = run_script(
        fake_gh, judgment_of(report), f"http://{HOST}:{port}", tmp_path / "inbox", "128"
    )

    assert result.returncode == 0, result.stderr
    [triage] = store.list_triages()
    assert triage.report == report


def test_keeps_the_report_in_the_inbox_when_the_dashboard_is_down(
    tmp_path: Path, report: dict[str, object], fake_gh: Path
) -> None:
    inbox = tmp_path / "inbox"

    result = run_script(
        fake_gh, judgment_of(report), f"http://{HOST}:{closed_port()}", inbox, "128"
    )

    assert result.returncode == 0
    assert "inbox" in result.stderr
    saved = json.loads((inbox / "acme_web-128.json").read_text(encoding="utf-8"))
    assert saved == report


def test_fails_with_the_reason_when_the_dashboard_refuses(
    tmp_path: Path, port: int, report: dict[str, object], fake_gh: Path
) -> None:
    inbox = tmp_path / "inbox"

    result = run_script(
        fake_gh,
        judgment_of(report) | {"risk": "critical"},
        f"http://{HOST}:{port}",
        inbox,
        "128",
    )

    assert result.returncode == 1
    assert "risk" in result.stderr
    assert not inbox.exists()


def test_needs_a_pr_number(tmp_path: Path, report: dict[str, object], fake_gh: Path) -> None:
    result = run_script(fake_gh, judgment_of(report), "http://unused", tmp_path / "inbox")

    assert result.returncode == 2
