import json
from pathlib import Path

from server import TriageStore, import_inbox, parse_report


def test_a_saved_report_waits_as_pending(store: TriageStore, report: dict[str, object]) -> None:
    store.save(parse_report(report))

    [triage] = store.list_triages(None)
    assert triage.status == "pending"
    assert triage.report == report


def test_resending_a_pr_overwrites_its_report(
    store: TriageStore, report: dict[str, object]
) -> None:
    first = store.save(parse_report(report))
    report["title"] = "検索APIにcursor形式のページングを追加"

    second = store.save(parse_report(report))

    assert second == first
    [triage] = store.list_triages(None)
    assert triage.report == report


def test_the_latest_report_comes_first(store: TriageStore, report: dict[str, object]) -> None:
    older = store.save(parse_report(report))
    newer = store.save(
        parse_report(report | {"number": 129, "url": "https://github.com/acme/web/pull/129"})
    )
    store.save(parse_report(report))

    assert [triage.id for triage in store.list_triages(None)] == [older, newer]


def test_lists_only_the_requested_status(store: TriageStore, report: dict[str, object]) -> None:
    store.save(parse_report(report))

    assert store.list_triages("on_hold") == []
    assert len(store.list_triages("pending")) == 1


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

    imported = import_inbox(inbox, store)

    assert imported == 1
    assert not readable.exists()
    assert broken.exists()
    assert nested.exists()
    assert len(store.list_triages(None)) == 1


def test_a_missing_inbox_imports_nothing(tmp_path: Path, store: TriageStore) -> None:
    assert import_inbox(tmp_path / "inbox", store) == 0
