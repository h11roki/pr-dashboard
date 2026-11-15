import json
import re
import sqlite3
import sys
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8787
MAX_BODY_BYTES = 64 * 1024
MAX_SUMMARY_LINES = 3
REPO_PATTERN = re.compile(r"[\w.-]+/[\w.-]+")
RISKS = ("low", "medium", "high")
TEST_STATUSES = ("passed", "failed", "not_run")
STATUSES = ("pending", "requested", "returned", "on_hold")
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'none'",
        "script-src 'unsafe-inline'",
        "style-src 'unsafe-inline'",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'none'",
    ]
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS prs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    number INTEGER NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'requested', 'returned', 'on_hold')),
    note TEXT,
    received_at TEXT NOT NULL,
    decided_at TEXT,
    UNIQUE (repo, number)
)
"""

UPSERT = """
INSERT INTO prs (repo, number, payload, received_at) VALUES (?, ?, ?, ?)
ON CONFLICT (repo, number) DO UPDATE SET
    payload = excluded.payload,
    received_at = excluded.received_at
RETURNING id
"""

SELECT = """
SELECT id, status, note, received_at, decided_at, payload FROM prs
WHERE ? IS NULL OR status = ?
ORDER BY status = 'pending' DESC, received_at DESC, id DESC
"""


class InvalidReport(ValueError):
    pass


@dataclass(frozen=True)
class Changes:
    files: int
    additions: int
    deletions: int


@dataclass(frozen=True)
class TestResult:
    status: str
    detail: str


@dataclass(frozen=True)
class Report:
    repo: str
    number: int
    url: str
    title: str
    branch: str
    changes: Changes
    summary: tuple[str, ...]
    why: str
    risk: str
    risk_reason: str
    concerns: tuple[str, ...]
    tests: TestResult
    suggested_reviewers: tuple[str, ...]
    artifact_url: str | None


@dataclass(frozen=True)
class Triage:
    id: int
    status: str
    note: str | None
    received_at: str
    decided_at: str | None
    report: object


def _field[T](data: dict[str, object], key: str, kind: type[T]) -> T:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, kind):
        raise InvalidReport(f"{key} は {kind.__name__} で指定する")
    return value


def _count(data: dict[str, object], key: str) -> int:
    value = _field(data, key, int)
    if value < 0:
        raise InvalidReport(f"{key} は 0 以上で指定する")
    return value


def _choice(data: dict[str, object], key: str, choices: tuple[str, ...]) -> str:
    value = _field(data, key, str)
    if value not in choices:
        raise InvalidReport(f"{key} は {' / '.join(choices)} のどれかで指定する")
    return value


def _texts(data: dict[str, object], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidReport(f"{key} は文字列の配列で指定する")
    return tuple(value)


def _https_url(data: dict[str, object], key: str) -> str:
    value = _field(data, key, str)
    if not value.startswith("https://"):
        raise InvalidReport(f"{key} は https:// で始まる URL で指定する")
    return value


def parse_report(raw: object) -> Report:
    if not isinstance(raw, dict):
        raise InvalidReport("報告は JSON のオブジェクトで送る")
    repo = _field(raw, "repo", str)
    if not REPO_PATTERN.fullmatch(repo):
        raise InvalidReport("repo は owner/name の形で指定する")
    number = _field(raw, "number", int)
    if number < 1:
        raise InvalidReport("number は 1 以上で指定する")
    summary = _texts(raw, "summary")
    if not 1 <= len(summary) <= MAX_SUMMARY_LINES:
        raise InvalidReport(f"summary は 1〜{MAX_SUMMARY_LINES} 行で書く")
    changes = _field(raw, "changes", dict)
    tests = _field(raw, "tests", dict)
    return Report(
        repo=repo,
        number=number,
        url=_https_url(raw, "url"),
        title=_field(raw, "title", str),
        branch=_field(raw, "branch", str),
        changes=Changes(
            files=_count(changes, "files"),
            additions=_count(changes, "additions"),
            deletions=_count(changes, "deletions"),
        ),
        summary=summary,
        why=_field(raw, "why", str),
        risk=_choice(raw, "risk", RISKS),
        risk_reason=_field(raw, "risk_reason", str),
        concerns=_texts(raw, "concerns"),
        tests=TestResult(
            status=_choice(tests, "status", TEST_STATUSES),
            detail=_field(tests, "detail", str),
        ),
        suggested_reviewers=_texts(raw, "suggested_reviewers"),
        artifact_url=None if raw.get("artifact_url") is None else _https_url(raw, "artifact_url"),
    )


class TriageStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(SCHEMA)

    def save(self, report: Report) -> int:
        payload = json.dumps(asdict(report), ensure_ascii=False)
        received_at = datetime.now(UTC).isoformat()
        with self._connection:
            row = self._connection.execute(
                UPSERT, (report.repo, report.number, payload, received_at)
            ).fetchone()
        return int(row["id"])

    def list_triages(self, status: str | None) -> list[Triage]:
        rows = self._connection.execute(SELECT, (status, status)).fetchall()
        return [
            Triage(
                id=row["id"],
                status=row["status"],
                note=row["note"],
                received_at=row["received_at"],
                decided_at=row["decided_at"],
                report=json.loads(row["payload"]),
            )
            for row in rows
        ]

    def close(self) -> None:
        self._connection.close()


def import_inbox(inbox: Path, store: TriageStore) -> int:
    imported = 0
    for path in sorted(inbox.glob("*.json")):
        try:
            report = parse_report(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as error:
            print(f"{path} を取り込めませんでした: {error}", file=sys.stderr)
            continue
        store.save(report)
        path.unlink()
        imported += 1
    return imported


class DashboardServer(HTTPServer):
    def __init__(self, port: int, store: TriageStore, page: Path) -> None:
        super().__init__((HOST, port), DashboardHandler)
        self.store = store
        self.page = page
        self.allowed_hosts = frozenset(
            {f"{HOST}:{self.server_port}", f"localhost:{self.server_port}"}
        )
        self.allowed_origins = frozenset(f"http://{host}" for host in self.allowed_hosts)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if not self._is_local_request():
            self._reject(HTTPStatus.FORBIDDEN, "ほかのサイトからの要求は受け付けない")
        elif url.path == "/":
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", self.server.page.read_bytes())
        elif url.path == "/api/prs":
            self._send_triages(url.query)
        else:
            self._reject(HTTPStatus.NOT_FOUND, "見つからない")

    def do_POST(self) -> None:
        if not self._is_local_request():
            self._reject(HTTPStatus.FORBIDDEN, "ほかのサイトからの要求は受け付けない")
        elif urlsplit(self.path).path == "/api/prs":
            self._receive_report()
        else:
            self._reject(HTTPStatus.NOT_FOUND, "見つからない")

    def _is_local_request(self) -> bool:
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in self.server.allowed_hosts and (
            origin is None or origin in self.server.allowed_origins
        )

    def _receive_report(self) -> None:
        length = self.headers.get("Content-Length", "")
        if self.headers.get_content_type() != "application/json":
            self._reject(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "本文は application/json で送る")
        elif not (length.isascii() and length.isdigit()):
            self._reject(HTTPStatus.LENGTH_REQUIRED, "Content-Length を付ける")
        elif int(length) > MAX_BODY_BYTES:
            self._reject(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"本文は {MAX_BODY_BYTES} バイトまで")
        else:
            try:
                report = parse_report(json.loads(self.rfile.read(int(length))))
            except ValueError as error:
                self._reject(HTTPStatus.BAD_REQUEST, str(error))
                return
            self._send_json(HTTPStatus.OK, {"id": self.server.store.save(report)})

    def _send_triages(self, query: str) -> None:
        statuses = parse_qs(query).get("status")
        status = statuses[0] if statuses else None
        if status is not None and status not in STATUSES:
            self._reject(HTTPStatus.BAD_REQUEST, f"status は {' / '.join(STATUSES)} のどれか")
            return
        triages = self.server.store.list_triages(status)
        self._send_json(HTTPStatus.OK, [asdict(triage) for triage in triages])

    def _reject(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"error": message})

    def _send_json(self, status: HTTPStatus, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self._send(status, "application/json; charset=utf-8", body)

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    store = TriageStore(ROOT / "data" / "prs.db")
    imported = import_inbox(ROOT / "inbox", store)
    if imported:
        print(f"inbox から {imported} 件取り込んだ")
    server = DashboardServer(PORT, store, ROOT / "static" / "index.html")
    print(f"http://{HOST}:{PORT} で待ち受けている")
    with suppress(KeyboardInterrupt):
        server.serve_forever()
    server.server_close()
    store.close()


if __name__ == "__main__":
    main()
