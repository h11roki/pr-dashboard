import json
import os
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from server import HOST, ROOT, TriageStore

SCRIPT = ROOT / "bin" / "toriage-report"
REFUSED = f"http://{HOST}:1"
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


def write_gh(tmp_path: Path, body: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    gh.chmod(0o755)
    return bin_dir


@pytest.fixture
def fake_gh(tmp_path: Path) -> Path:
    return write_gh(tmp_path, f"echo '{json.dumps(GH_PR_VIEW, ensure_ascii=False)}'")


def judgment_of(report: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in report.items() if key not in FACTS_FROM_GH}


def run_script(
    bin_dir: Path, judgment: str, dashboard: str | None, home: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    environment = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TORIAGE_HOME": str(home),
    }
    environment.pop("TORIAGE_URL", None)
    if dashboard is not None:
        environment["TORIAGE_URL"] = dashboard
    return subprocess.run(
        [str(SCRIPT), *args],
        input=judgment,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
        timeout=30,
    )


def test_sends_the_judgment_with_the_facts_from_gh(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object], fake_gh: Path
) -> None:
    judgment = json.dumps(judgment_of(report))

    result = run_script(fake_gh, judgment, f"http://{HOST}:{port}", tmp_path / "home", "128")

    assert result.returncode == 0, result.stderr
    assert "acme/web#128" in result.stdout
    [triage] = store.list_triages()
    assert triage.report == report


def test_keeps_the_report_in_the_inbox_when_the_dashboard_is_down(
    tmp_path: Path, report: dict[str, object], fake_gh: Path
) -> None:
    inbox = tmp_path / "home" / "inbox"
    judgment = json.dumps(judgment_of(report))

    result = run_script(fake_gh, judgment, REFUSED, tmp_path / "home", "128")

    assert result.returncode == 0, result.stderr
    saved = json.loads((inbox / "acme_web-128.json").read_text(encoding="utf-8"))
    assert saved == report


def test_starts_a_stopped_dashboard_before_sending(
    dashboard_home: Path, report: dict[str, object], fake_gh: Path
) -> None:
    judgment = json.dumps(judgment_of(report))

    result = run_script(fake_gh, judgment, None, dashboard_home, "128")

    assert result.returncode == 0, result.stderr
    assert "acme/web#128" in result.stdout
    with closing(TriageStore(dashboard_home / "prs.db")) as store:
        [triage] = store.list_triages()
    assert triage.report == report


def test_does_not_keep_an_invalid_report_even_when_the_dashboard_is_down(
    tmp_path: Path, report: dict[str, object], fake_gh: Path
) -> None:
    inbox = tmp_path / "home" / "inbox"
    judgment = json.dumps(judgment_of(report) | {"risk": "critical"})

    result = run_script(fake_gh, judgment, REFUSED, tmp_path / "home", "128")

    assert result.returncode == 1
    assert "risk" in result.stderr
    assert not inbox.exists()


def test_fails_with_the_reason_when_the_dashboard_refuses(
    tmp_path: Path, port: int, report: dict[str, object], fake_gh: Path
) -> None:
    judgment = json.dumps(judgment_of(report))

    result = run_script(
        fake_gh, judgment, f"http://{HOST}:{port}/missing", tmp_path / "home", "128"
    )

    assert result.returncode == 1
    assert "404" in result.stderr


def test_accepts_a_dashboard_url_with_a_trailing_slash(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object], fake_gh: Path
) -> None:
    judgment = json.dumps(judgment_of(report))

    result = run_script(fake_gh, judgment, f"http://{HOST}:{port}/", tmp_path / "home", "128")

    assert result.returncode == 0, result.stderr
    assert len(store.list_triages()) == 1


@pytest.mark.parametrize("judgment", ["summary: なし", '{"risk": "low"}\n{"risk": "high"}'])
def test_fails_when_the_judgment_is_not_one_json_object(
    tmp_path: Path, fake_gh: Path, judgment: str
) -> None:
    result = run_script(fake_gh, judgment, "http://unused", tmp_path / "home", "1")

    assert result.returncode == 1
    assert "JSON" in result.stderr


def test_fails_when_gh_cannot_read_the_pr(tmp_path: Path, report: dict[str, object]) -> None:
    failing_gh = write_gh(tmp_path, "exit 1")
    judgment = json.dumps(judgment_of(report))

    result = run_script(failing_gh, judgment, "http://unused", tmp_path / "home", "9")

    assert result.returncode == 1
    assert "PR 9" in result.stderr


def test_needs_a_pr_number(tmp_path: Path, fake_gh: Path) -> None:
    result = run_script(fake_gh, "{}", "http://unused", tmp_path / "home")

    assert result.returncode == 2
