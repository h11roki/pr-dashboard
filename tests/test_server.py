import json
import socket
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from http.client import HTTPConnection
from pathlib import Path

import pytest

from server import (
    HOST,
    MAX_BODY_BYTES,
    MAX_REASON_LENGTH,
    Action,
    DashboardHandler,
    ReportChanged,
    TriageStore,
    decide,
    parse_report,
)

CLIENT_TIMEOUT_SECONDS = 5


def send(
    port: int,
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    connection = HTTPConnection(HOST, port, timeout=CLIENT_TIMEOUT_SECONDS)
    connection.request(method, path, body, headers or {})
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def post_report(
    port: int, report: dict[str, object], headers: dict[str, str] | None = None
) -> tuple[int, bytes]:
    body = json.dumps(report).encode()
    return send(
        port, "POST", "/api/prs", body, {"Content-Type": "application/json", **(headers or {})}
    )


def test_a_posted_report_appears_in_the_pending_list(port: int, report: dict[str, object]) -> None:
    status, body = post_report(port, report)
    assert status == 200
    assert json.loads(body) == {"saved": True}

    status, body = send(port, "GET", "/api/prs")

    assert status == 200
    [triage] = json.loads(body)
    assert triage["status"] == "pending"
    assert triage["report"] == report


def test_a_request_from_the_page_itself_is_accepted(port: int, report: dict[str, object]) -> None:
    status, _ = post_report(port, report, {"Origin": f"http://localhost:{port}"})

    assert status == 200


def test_refuses_a_foreign_host(port: int) -> None:
    status, _ = send(port, "GET", "/api/prs", headers={"Host": f"attacker.example:{port}"})

    assert status == 403


def test_refuses_a_foreign_origin(port: int, store: TriageStore, report: dict[str, object]) -> None:
    status, _ = post_report(port, report, {"Origin": "https://attacker.example"})

    assert status == 403
    assert store.list_triages() == []


def test_refuses_a_body_that_is_not_json(port: int, report: dict[str, object]) -> None:
    status, _ = send(
        port, "POST", "/api/prs", json.dumps(report).encode(), {"Content-Type": "text/plain"}
    )

    assert status == 415


def test_refuses_a_body_over_the_limit(port: int) -> None:
    headers = {"Content-Type": "application/json", "Content-Length": str(MAX_BODY_BYTES + 1)}

    status, _ = send(port, "POST", "/api/prs", b"{}", headers)

    assert status == 413


def test_explains_why_a_report_is_invalid(port: int, report: dict[str, object]) -> None:
    report["risk"] = "critical"

    status, body = post_report(port, report)

    assert status == 400
    assert "risk" in json.loads(body)["error"]


def start_stalled_upload(port: int) -> socket.socket:
    stalled = socket.create_connection((HOST, port), timeout=CLIENT_TIMEOUT_SECONDS)
    stalled.sendall(
        (
            "POST /api/prs HTTP/1.1\r\n"
            f"Host: {HOST}:{port}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 100\r\n\r\n{"
        ).encode()
    )
    return stalled


def test_a_stalled_upload_does_not_block_the_list(port: int) -> None:
    with start_stalled_upload(port):
        status, _ = send(port, "GET", "/api/prs")

    assert status == 200


def test_a_stalled_upload_times_out(port: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DashboardHandler, "timeout", 0.2)

    with start_stalled_upload(port) as stalled:
        response = stalled.recv(1024)

    assert response.startswith(b"HTTP/1.0 408")


def test_answers_deeply_nested_json_with_an_error(port: int) -> None:
    status, _ = send(port, "POST", "/api/prs", b"[" * 50_000, {"Content-Type": "application/json"})

    assert status == 400


def test_refuses_an_absurdly_long_content_length(port: int) -> None:
    headers = {"Content-Type": "application/json", "Content-Length": "9" * 5000}

    status, _ = send(port, "POST", "/api/prs", b"{}", headers)

    assert status == 413


def test_accepts_a_content_length_with_leading_zeros(port: int, report: dict[str, object]) -> None:
    body = json.dumps(report).encode()
    headers = {"Content-Type": "application/json", "Content-Length": f"000{len(body)}"}

    status, _ = send(port, "POST", "/api/prs", body, headers)

    assert status == 200


def test_unknown_paths_are_not_found(port: int) -> None:
    status, _ = send(port, "GET", "/admin")

    assert status == 404


def test_serves_the_page_and_keeps_other_sites_from_framing_it(port: int) -> None:
    connection = HTTPConnection(HOST, port)
    connection.request("GET", "/")
    response = connection.getresponse()
    body = response.read()
    connection.close()

    assert response.status == 200
    assert response.getheader("Content-Type") == "text/html; charset=utf-8"
    assert "frame-ancestors 'none'" in response.getheader("Content-Security-Policy", "")
    assert "<title>PRトリアージ</title>" in body.decode()


def save_pending(store: TriageStore, report: dict[str, object]) -> int:
    store.save(parse_report(report), datetime.now(UTC))
    [triage] = store.list_triages()
    return triage.id


def post_decision(
    port: int, triage_id: int, action: str, body: object, headers: dict[str, str] | None = None
) -> tuple[int, bytes]:
    return send(
        port,
        "POST",
        f"/api/prs/{triage_id}/{action}",
        json.dumps(body).encode(),
        {"Content-Type": "application/json", **(headers or {})},
    )


def status_of(store: TriageStore, triage_id: int) -> str | None:
    triage = store.find(triage_id)
    return None if triage is None else triage.status


def test_requesting_a_review_marks_the_pr_ready(
    port: int, store: TriageStore, report: dict[str, object], gh_calls: list[tuple[list[str], str]]
) -> None:
    triage_id = save_pending(store, report)

    status, body = post_decision(port, triage_id, "request-review", {})

    assert status == 200
    assert json.loads(body) == {"status": "requested", "warning": ""}
    assert gh_calls == [(["pr", "ready", "128", "-R", "acme/web"], "")]
    assert status_of(store, triage_id) == "requested"


def test_requesting_a_review_adds_the_suggested_reviewers(
    port: int, store: TriageStore, report: dict[str, object], gh_calls: list[tuple[list[str], str]]
) -> None:
    triage_id = save_pending(store, report | {"suggested_reviewers": ["alice", "acme/core"]})

    post_decision(port, triage_id, "request-review", {})

    assert gh_calls[1] == (
        ["pr", "edit", "128", "-R", "acme/web", "--add-reviewer", "alice,acme/core"],
        "",
    )


def test_returning_a_pr_comments_the_reason(
    port: int, store: TriageStore, report: dict[str, object], gh_calls: list[tuple[list[str], str]]
) -> None:
    triage_id = save_pending(store, report)

    status, _ = post_decision(port, triage_id, "return", {"reason": "テストが足りない"})

    assert status == 200
    assert gh_calls == [
        (["pr", "comment", "128", "-R", "acme/web", "--body-file", "-"], "テストが足りない")
    ]
    triage = store.find(triage_id)
    assert triage is not None
    assert (triage.status, triage.note) == ("returned", "テストが足りない")


@pytest.mark.parametrize("reason", ["", "   ", "あ" * (MAX_REASON_LENGTH + 1)])
def test_returning_needs_a_reason(
    port: int,
    store: TriageStore,
    report: dict[str, object],
    gh_calls: list[tuple[list[str], str]],
    reason: str,
) -> None:
    triage_id = save_pending(store, report)

    status, _ = post_decision(port, triage_id, "return", {"reason": reason})

    assert status == 400
    assert gh_calls == []
    assert status_of(store, triage_id) == "pending"


def test_holding_changes_only_the_status_and_can_be_decided_later(
    port: int, store: TriageStore, report: dict[str, object], gh_calls: list[tuple[list[str], str]]
) -> None:
    triage_id = save_pending(store, report)

    held, _ = post_decision(port, triage_id, "hold", {})
    assert (held, gh_calls, status_of(store, triage_id)) == (200, [], "on_hold")

    requested, _ = post_decision(port, triage_id, "request-review", {})
    assert (requested, status_of(store, triage_id)) == (200, "requested")


def test_a_failing_gh_leaves_the_status_alone(
    port: int, store: TriageStore, report: dict[str, object], gh_failures: dict[str, str]
) -> None:
    triage_id = save_pending(store, report)
    gh_failures["ready"] = "HTTP 404: Not Found"

    status, body = post_decision(port, triage_id, "request-review", {})

    assert status == 502
    assert "404" in json.loads(body)["error"]
    assert status_of(store, triage_id) == "pending"


def test_a_reviewer_that_cannot_be_added_only_warns(
    port: int, store: TriageStore, report: dict[str, object], gh_failures: dict[str, str]
) -> None:
    triage_id = save_pending(store, report | {"suggested_reviewers": ["h11roki"]})
    gh_failures["edit"] = "Review cannot be requested from pull request author."

    status, body = post_decision(port, triage_id, "request-review", {})

    assert status == 200
    assert "author" in json.loads(body)["warning"]
    assert status_of(store, triage_id) == "requested"


def test_a_stored_reviewer_that_no_longer_passes_is_skipped(
    tmp_path: Path,
    port: int,
    store: TriageStore,
    report: dict[str, object],
    gh_calls: list[tuple[list[str], str]],
) -> None:
    triage_id = save_pending(store, report)
    with closing(sqlite3.connect(tmp_path / "prs.db")) as old, old:
        old.execute(
            "UPDATE prs SET payload = json_set(payload, '$.suggested_reviewers', json(?))",
            (json.dumps(["@alice", "bob"]),),
        )

    status, _ = post_decision(port, triage_id, "request-review", {})

    assert status == 200
    assert gh_calls[1][0][-1] == "bob"


def test_a_report_that_arrives_during_a_decision_is_not_overwritten(
    store: TriageStore, report: dict[str, object]
) -> None:
    triage_id = save_pending(store, report)

    def gh_while_a_new_report_arrives(arguments: list[str], stdin: str) -> None:
        store.save(parse_report(report | {"title": "直した題"}), datetime.now(UTC))

    with pytest.raises(ReportChanged):
        decide(store, gh_while_a_new_report_arrives, triage_id, Action.RETURN, "直して")

    assert status_of(store, triage_id) == "pending"


def test_a_decided_pr_cannot_be_decided_again(
    port: int, store: TriageStore, report: dict[str, object]
) -> None:
    triage_id = save_pending(store, report)
    post_decision(port, triage_id, "request-review", {})

    status, _ = post_decision(port, triage_id, "hold", {})

    assert status == 409


def test_deciding_an_unknown_pr_is_not_found(port: int) -> None:
    status, _ = post_decision(port, 999, "hold", {})

    assert status == 404


def test_a_decision_needs_a_json_body(
    port: int, store: TriageStore, report: dict[str, object], gh_calls: list[tuple[list[str], str]]
) -> None:
    triage_id = save_pending(store, report)

    status, _ = send(
        port, "POST", f"/api/prs/{triage_id}/request-review", b"{}", {"Content-Type": "text/plain"}
    )

    assert status == 415
    assert gh_calls == []


def test_a_decision_from_another_site_is_refused(
    port: int, store: TriageStore, report: dict[str, object], gh_calls: list[tuple[list[str], str]]
) -> None:
    triage_id = save_pending(store, report)

    status, _ = post_decision(
        port, triage_id, "request-review", {}, {"Origin": "https://attacker.example"}
    )

    assert status == 403
    assert gh_calls == []
