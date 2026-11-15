import json
import threading
from collections.abc import Iterator
from http.client import HTTPConnection

import pytest

from server import HOST, MAX_BODY_BYTES, ROOT, DashboardServer, TriageStore


@pytest.fixture
def port(store: TriageStore) -> Iterator[int]:
    server = DashboardServer(0, store, ROOT / "static" / "index.html")
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield server.server_port
    server.shutdown()
    thread.join()
    server.server_close()


def send(
    port: int,
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    connection = HTTPConnection(HOST, port)
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
    assert json.loads(body) == {"id": 1}

    status, body = send(port, "GET", "/api/prs?status=pending")

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
    assert store.list_triages(None) == []


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


def test_answers_deeply_nested_json_with_an_error(port: int) -> None:
    status, _ = send(port, "POST", "/api/prs", b"[" * 50_000, {"Content-Type": "application/json"})

    assert status == 400


def test_refuses_an_unknown_status(port: int) -> None:
    status, _ = send(port, "GET", "/api/prs?status=merged")

    assert status == 400


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
