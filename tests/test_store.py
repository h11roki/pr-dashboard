import json
import os
import sqlite3
import subprocess
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server import MAX_BODY_BYTES, ROOT, TriageStore, data_home, import_inbox, parse_report

MORNING = datetime(2026, 11, 15, 9, 0, 0, 123456, tzinfo=UTC)
NOON = MORNING + timedelta(hours=3)
EVENING = MORNING + timedelta(hours=9)


def test_a_saved_report_waits_as_pending(store: TriageStore, report: dict[str, object]) -> None:
    assert store.save(parse_report(report), MORNING)

    [triage] = store.list_triages()
    assert triage.status == "pending"
    assert triage.report == report
    assert triage.received_at == "2026-11-15T09:00:00.123+00:00"


def test_resending_a_pr_overwrites_its_report(
    store: TriageStore, report: dict[str, object]
) -> None:
    store.save(parse_report(report), MORNING)
    report["title"] = "検索APIにcursor形式のページングを追加"

    assert store.save(parse_report(report), NOON)

    [triage] = store.list_triages()
    assert triage.report == report


def test_an_older_report_does_not_overwrite_a_newer_one(
    store: TriageStore, report: dict[str, object]
) -> None:
    store.save(parse_report(report), NOON)
    stale = report | {"title": "古い題"}

    assert not store.save(parse_report(stale), MORNING)

    [triage] = store.list_triages()
    assert triage.report == report


@pytest.mark.parametrize(
    ("decided", "after_resend"),
    [("returned", "pending"), ("on_hold", "on_hold"), ("requested", "requested")],
)
def test_resending_brings_only_a_returned_pr_back_to_pending(
    tmp_path: Path, store: TriageStore, report: dict[str, object], decided: str, after_resend: str
) -> None:
    store.save(parse_report(report), MORNING)
    with closing(sqlite3.connect(tmp_path / "prs.db")) as decider, decider:
        decider.execute(
            "UPDATE prs SET status = ?, note = 'テストが足りない', "
            "decided_at = '2026-11-15T10:00:00.000+00:00'",
            (decided,),
        )

    store.save(parse_report(report), NOON)

    [triage] = store.list_triages()
    assert triage.status == after_resend
    assert (triage.note is None) == (after_resend == "pending")
    assert (triage.decided_at is None) == (after_resend == "pending")


def test_the_latest_report_comes_first(store: TriageStore, report: dict[str, object]) -> None:
    other = report | {"number": 129, "url": "https://github.com/acme/web/pull/129"}
    store.save(parse_report(report), MORNING)
    store.save(parse_report(other), NOON)
    store.save(parse_report(report), EVENING)

    assert [triage.report for triage in store.list_triages()] == [report, other]


def test_imports_the_inbox_and_keeps_what_it_cannot_read(
    tmp_path: Path, store: TriageStore, report: dict[str, object]
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    readable = inbox / "acme_web-128.json"
    readable.write_text(json.dumps(report), encoding="utf-8")
    broken = inbox / "acme_web-129.json"
    broken.write_text("{", encoding="utf-8")
    nested = inbox / "acme_web-130.json"
    nested.write_text("[" * 50_000, encoding="utf-8")
    oversized = inbox / "acme_web-131.json"
    oversized.write_text(" " * (MAX_BODY_BYTES + 1), encoding="utf-8")

    imported = import_inbox(inbox, store)

    assert imported == 1
    assert not readable.exists()
    assert broken.exists()
    assert nested.exists()
    assert oversized.exists()
    assert len(store.list_triages()) == 1


def test_a_report_left_in_the_inbox_does_not_undo_a_newer_one(
    tmp_path: Path, store: TriageStore, report: dict[str, object]
) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    stale = inbox / "acme_web-128.json"
    stale.write_text(json.dumps(report | {"title": "古い題"}), encoding="utf-8")
    os.utime(stale, (MORNING.timestamp(), MORNING.timestamp()))
    store.save(parse_report(report), NOON)

    imported = import_inbox(inbox, store)

    assert imported == 0
    assert not stale.exists()
    [triage] = store.list_triages()
    assert triage.report == report


def test_a_missing_inbox_imports_nothing(tmp_path: Path, store: TriageStore) -> None:
    assert import_inbox(tmp_path / "inbox", store) == 0


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"PR_DASHBOARD_HOME": "/srv/dashboard", "XDG_DATA_HOME": "/xdg"}, "/srv/dashboard"),
        ({"XDG_DATA_HOME": "/xdg"}, "/xdg/pr-dashboard"),
        ({"PR_DASHBOARD_HOME": "dashboard", "XDG_DATA_HOME": "/xdg"}, "/xdg/pr-dashboard"),
        ({"XDG_DATA_HOME": "relative/share"}, "/home/someone/.local/share/pr-dashboard"),
        ({}, "/home/someone/.local/share/pr-dashboard"),
    ],
)
def test_the_server_and_the_scripts_agree_on_where_the_data_lives(
    monkeypatch: pytest.MonkeyPatch, environment: dict[str, str], expected: str
) -> None:
    monkeypatch.setenv("HOME", "/home/someone")
    monkeypatch.delenv("PR_DASHBOARD_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    from_scripts = subprocess.run(
        [ROOT / "libexec" / "pr-dashboard-home"], capture_output=True, text=True, check=True
    ).stdout

    assert data_home() == Path(expected)
    assert from_scripts == f"{expected}\n"
