import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import assert_never
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8787
MAX_BODY_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 10
MAX_SUMMARY_LINES = 3
MAX_INTEGER = 2**31 - 1
REPO_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/(?!\.{1,2}$)[A-Za-z0-9._][A-Za-z0-9._-]*")
REVIEWER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?")
SESSION_ID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
RISKS = ("low", "medium", "high")
TEST_STATUSES = ("passed", "failed", "not_run")
ACTION_PATH = re.compile(r"/api/prs/([0-9]{1,10})/(request-review|return|hold)")
MAX_REASON_LENGTH = 2000
GH_TIMEOUT_SECONDS = 30
REPORT_COMMAND = "toriage-report"
PLUGIN_MANIFEST = Path(".claude-plugin") / "plugin.json"
INHERITED_SESSION_VARIABLES = frozenset(
    {
        "CLAUDECODE",
        "CLAUDE_AGENT_SDK_VERSION",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_EXECPATH",
        "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_CODE_MESSAGING_TOKEN",
        "CLAUDE_CODE_SESSION_ATTENDED",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_EFFORT",
        "CLAUDE_ENV_FILE",
        "CLAUDE_PID",
        "CLAUDE_PLUGIN_DATA",
        "CLAUDE_PLUGIN_ROOT",
        "CLAUDE_PROJECT_DIR",
    }
)
RETURN_PROMPT = """toriage で {url} が差し戻されました。

理由:
{reason}

この PR を直して push し、/toriage:report で判断材料を送り直してください。"""
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

UPSERT_REPORT = """
INSERT INTO prs (repo, number, payload, received_at) VALUES (?, ?, ?, ?)
ON CONFLICT (repo, number) DO UPDATE SET
    payload = excluded.payload,
    received_at = excluded.received_at,
    status = CASE status WHEN 'returned' THEN 'pending' ELSE status END,
    note = CASE status WHEN 'returned' THEN NULL ELSE note END,
    decided_at = CASE status WHEN 'returned' THEN NULL ELSE decided_at END
WHERE excluded.received_at > prs.received_at
"""

SELECT_TRIAGES = """
SELECT id, repo, number, status, note, received_at, decided_at, payload FROM prs
ORDER BY received_at DESC, id DESC
"""

SELECT_TRIAGE = """
SELECT id, repo, number, status, note, received_at, decided_at, payload FROM prs WHERE id = ?
"""

SELECT_DECISION_STATE = """
SELECT status, received_at FROM prs WHERE id = ?
"""

UPDATE_DECISION = """
UPDATE prs SET status = ?, note = ?, decided_at = ? WHERE id = ?
"""

RunGh = Callable[[list[str], str], None]


class Action(StrEnum):
    REQUEST_REVIEW = "request-review"
    RETURN = "return"
    HOLD = "hold"


ALLOWED_ACTIONS: dict[str, tuple[Action, ...]] = {
    "pending": (Action.REQUEST_REVIEW, Action.RETURN, Action.HOLD),
    "on_hold": (Action.REQUEST_REVIEW, Action.RETURN),
    "returned": (Action.RETURN,),
}


class InvalidReport(ValueError):
    pass


class DecisionError(Exception):
    status = HTTPStatus.BAD_REQUEST


class TriageNotFound(DecisionError):
    status = HTTPStatus.NOT_FOUND


class AlreadyDecided(DecisionError):
    status = HTTPStatus.CONFLICT


class ReportChanged(DecisionError):
    status = HTTPStatus.CONFLICT


class InvalidReason(DecisionError):
    status = HTTPStatus.BAD_REQUEST


class GhFailed(DecisionError):
    status = HTTPStatus.BAD_GATEWAY


class ResumeFailed(DecisionError):
    status = HTTPStatus.BAD_GATEWAY


class SessionBusy(DecisionError):
    status = HTTPStatus.CONFLICT


@dataclass(frozen=True)
class Decision:
    status: str
    warning: str


@dataclass(frozen=True)
class DecisionRequest:
    seen: str
    reason: str


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
class Session:
    id: str
    workdir: str


ResumeSession = Callable[[Session, str], None]


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
    session: Session | None


@dataclass(frozen=True)
class Triage:
    id: int
    repo: str
    number: int
    status: str
    note: str | None
    received_at: str
    decided_at: str | None
    report: object
    reviewers: tuple[str, ...]
    session: Session | None
    actions: tuple[Action, ...]


def _field[T](data: dict[str, object], key: str, kind: type[T]) -> T:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, kind):
        raise InvalidReport(f"{key} は {kind.__name__} で指定する")
    return value


def _count(data: dict[str, object], key: str) -> int:
    value = _field(data, key, int)
    if not 0 <= value <= MAX_INTEGER:
        raise InvalidReport(f"{key} は 0〜{MAX_INTEGER} で指定する")
    return value


def _choice(data: dict[str, object], key: str, choices: tuple[str, ...]) -> str:
    value = _field(data, key, str)
    if value not in choices:
        raise InvalidReport(f"{key} は {' / '.join(choices)} のどれかで指定する")
    return value


def _text(data: dict[str, object], key: str) -> str:
    value = _field(data, key, str)
    if not value.strip():
        raise InvalidReport(f"{key} を空にしない")
    return value


def _texts(data: dict[str, object], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise InvalidReport(f"{key} は空でない文字列の配列で指定する")
    return tuple(value)


def _https_url(data: dict[str, object], key: str) -> str:
    value = _field(data, key, str)
    if not value.startswith("https://"):
        raise InvalidReport(f"{key} は https:// で始まる URL で指定する")
    return value


def _session_or_none(value: object) -> Session | None:
    if not isinstance(value, dict):
        return None
    session_id, workdir = value.get("id"), value.get("workdir")
    if not isinstance(session_id, str) or not SESSION_ID_PATTERN.fullmatch(session_id):
        return None
    if not isinstance(workdir, str) or not Path(workdir).is_absolute() or "\0" in workdir:
        return None
    return Session(id=session_id, workdir=workdir)


def pull_request_url(repo: str, number: int) -> str:
    return f"https://github.com/{repo}/pull/{number}"


def parse_report(raw: object) -> Report:
    if not isinstance(raw, dict):
        raise InvalidReport("報告は JSON のオブジェクトで送る")
    try:
        json.dumps(raw, ensure_ascii=False).encode()
    except UnicodeEncodeError as error:
        raise InvalidReport("UTF-8 で表せない文字を含めない") from error
    repo = _field(raw, "repo", str)
    if not REPO_PATTERN.fullmatch(repo):
        raise InvalidReport("repo は owner/name の形で指定する")
    number = _field(raw, "number", int)
    if not 1 <= number <= MAX_INTEGER:
        raise InvalidReport(f"number は 1〜{MAX_INTEGER} で指定する")
    url = _field(raw, "url", str)
    if url != pull_request_url(repo, number):
        raise InvalidReport(f"url は {pull_request_url(repo, number)} にする")
    summary = _texts(raw, "summary")
    if not 1 <= len(summary) <= MAX_SUMMARY_LINES:
        raise InvalidReport(f"summary は 1〜{MAX_SUMMARY_LINES} 行で書く")
    reviewers = _texts(raw, "suggested_reviewers")
    if not all(REVIEWER_PATTERN.fullmatch(reviewer) for reviewer in reviewers):
        raise InvalidReport("suggested_reviewers は GitHub のユーザー名か org/team で指定する")
    changes = _field(raw, "changes", dict)
    tests = _field(raw, "tests", dict)
    return Report(
        repo=repo,
        number=number,
        url=url,
        title=_text(raw, "title"),
        branch=_text(raw, "branch"),
        changes=Changes(
            files=_count(changes, "files"),
            additions=_count(changes, "additions"),
            deletions=_count(changes, "deletions"),
        ),
        summary=summary,
        why=_text(raw, "why"),
        risk=_choice(raw, "risk", RISKS),
        risk_reason=_text(raw, "risk_reason"),
        concerns=_texts(raw, "concerns"),
        tests=TestResult(
            status=_choice(tests, "status", TEST_STATUSES),
            detail=_text(tests, "detail"),
        ),
        suggested_reviewers=reviewers,
        artifact_url=None if raw.get("artifact_url") is None else _https_url(raw, "artifact_url"),
        session=_session_or_none(raw.get("session")),
    )


class TriageStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(SCHEMA)
        self._lock = threading.Lock()

    def save(self, report: Report, received_at: datetime) -> bool:
        payload = json.dumps(asdict(report), ensure_ascii=False)
        received = received_at.astimezone(UTC).isoformat(timespec="milliseconds")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                UPSERT_REPORT, (report.repo, report.number, payload, received)
            )
        return cursor.rowcount == 1

    def list_triages(self) -> list[Triage]:
        with self._lock:
            rows = self._connection.execute(SELECT_TRIAGES).fetchall()
        return [self._triage(row) for row in rows]

    def find(self, triage_id: int) -> Triage | None:
        with self._lock:
            row = self._connection.execute(SELECT_TRIAGE, (triage_id,)).fetchone()
        return None if row is None else self._triage(row)

    def decide(
        self, triage: Triage, status: str, note: str | None, decided_at: datetime
    ) -> tuple[bool, bool]:
        decided = decided_at.astimezone(UTC).isoformat(timespec="milliseconds")
        with self._lock, self._connection:
            row = self._connection.execute(SELECT_DECISION_STATE, (triage.id,)).fetchone()
            if row is None or row["status"] != triage.status:
                return False, False
            self._connection.execute(UPDATE_DECISION, (status, note, decided, triage.id))
        return True, row["received_at"] != triage.received_at

    @staticmethod
    def _triage(row: sqlite3.Row) -> Triage:
        report = json.loads(row["payload"])
        stored = report.get("suggested_reviewers") if isinstance(report, dict) else None
        session = _session_or_none(report.get("session") if isinstance(report, dict) else None)
        return Triage(
            id=row["id"],
            repo=row["repo"],
            number=row["number"],
            status=row["status"],
            note=row["note"],
            received_at=row["received_at"],
            decided_at=row["decided_at"],
            report=report,
            reviewers=tuple(
                name
                for name in (stored if isinstance(stored, list) else [])
                if isinstance(name, str) and REVIEWER_PATTERN.fullmatch(name)
            ),
            session=session,
            actions=tuple(
                action
                for action in ALLOWED_ACTIONS.get(row["status"], ())
                if action is not Action.RETURN or session is not None
            ),
        )

    def close(self) -> None:
        self._connection.close()


def load_json[E: Exception](body: bytes, error: type[E]) -> object:
    try:
        return json.loads(body)
    except (ValueError, RecursionError) as problem:
        raise error(f"JSON として読めない: {problem}") from problem


def parse_body(body: bytes) -> Report:
    if len(body) > MAX_BODY_BYTES:
        raise InvalidReport(f"本文は {MAX_BODY_BYTES} バイトまで")
    return parse_report(load_json(body, InvalidReport))


def run_gh(arguments: list[str], stdin: str) -> None:
    try:
        completed = subprocess.run(
            ["gh", *arguments],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise GhFailed(
            f"gh が {GH_TIMEOUT_SECONDS} 秒で終わらなかった。どうなったかは GitHub で確かめる"
        ) from error
    except OSError as error:
        raise GhFailed(f"gh を実行できなかった: {error}") from error
    if completed.returncode != 0:
        raise GhFailed(completed.stderr.strip() or f"gh が {completed.returncode} で終わった")


def resume_in_background(logs: Path) -> ResumeSession:
    lock = threading.Lock()

    def wait_and_record(process: subprocess.Popen[bytes], log: Path, marker: Path) -> None:
        code = process.wait()
        with log.open("a", encoding="utf-8") as output:
            output.write(f"{_now()} に終わった(終了コード {code})\n")
        with lock, suppress(OSError):
            if marker.read_text(encoding="utf-8").strip() == str(process.pid):
                marker.unlink()

    def resume(session: Session, prompt: str) -> None:
        if not Path(session.workdir).is_dir():
            raise ResumeFailed(f"PR を作った場所 {session.workdir} がもうない")
        environment = _resume_environment(session.workdir)
        log = logs / f"{session.id}.log"
        marker = logs / f"{session.id}.pid"
        with lock:
            if _still_resuming(marker, session):
                raise SessionBusy("この会話は前の差し戻しをまだ直している。終わってから差し戻す")
            try:
                logs.mkdir(parents=True, exist_ok=True)
                with log.open("a", encoding="utf-8") as output:
                    output.write(f"{_now()} に差し戻した\n")
                    output.flush()
                    process = subprocess.Popen(
                        [
                            "claude",
                            "-p",
                            "--resume",
                            session.id,
                            "--permission-mode",
                            "auto",
                            "--permission-prompts",
                            "none",
                            prompt,
                        ],
                        cwd=session.workdir,
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
            except OSError as error:
                raise ResumeFailed(f"Claude Code を起動できなかった: {error}") from error
            try:
                marker.write_text(f"{process.pid}\n", encoding="utf-8")
            except OSError as error:
                process.kill()
                process.wait()
                raise ResumeFailed(f"再開したことを書き残せなかったので止めた: {error}") from error
        threading.Thread(target=wait_and_record, args=(process, log, marker), daemon=True).start()

    return resume


def _resume_environment(workdir: str) -> dict[str, str]:
    environment = {
        name: value for name, value in os.environ.items() if name not in INHERITED_SESSION_VARIABLES
    }
    if "PATH" in environment:
        environment["PATH"] = os.pathsep.join(
            directory
            for directory in environment["PATH"].split(os.pathsep)
            if not _is_plugin_bin(Path(workdir, directory))
        )
    return environment


def _is_plugin_bin(directory: Path) -> bool:
    resolved = directory.resolve()
    return (resolved / REPORT_COMMAND).is_file() and (resolved.parent / PLUGIN_MANIFEST).is_file()


def _still_resuming(marker: Path, session: Session) -> bool:
    try:
        pid = int(marker.read_text(encoding="utf-8"))
        command = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except (OSError, ValueError):
        return False
    return f"--resume {session.id}" in command


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def parse_decision_request(body: bytes) -> DecisionRequest:
    raw = load_json(body, InvalidReason)
    if not isinstance(raw, dict):
        raise InvalidReason("判断の本文は JSON のオブジェクトで送る")
    seen = raw.get("seen")
    reason = raw.get("reason", "")
    if not isinstance(seen, str) or not isinstance(reason, str):
        raise InvalidReason("seen(見た報告の受信日時)と reason は文字列で送る")
    return DecisionRequest(seen=seen, reason=reason)


def decide(
    store: TriageStore,
    gh: RunGh,
    resume: ResumeSession,
    triage_id: int,
    action: Action,
    request: DecisionRequest,
) -> Decision:
    triage = store.find(triage_id)
    if triage is None:
        raise TriageNotFound("その PR は見つからない")
    if action is Action.RETURN and triage.session is None:
        raise AlreadyDecided("この報告には PR を作ったセッションが付いていないので差し戻せない")
    if action not in triage.actions:
        raise AlreadyDecided("この PR にはもうその判断をできない")
    if triage.received_at != request.seen:
        raise ReportChanged("新しい報告が届いている。読み直してから判断する")
    target = [str(triage.number), "-R", triage.repo]
    warnings = []
    match action:
        case Action.REQUEST_REVIEW:
            gh(["pr", "ready", *target], "")
            if triage.reviewers:
                try:
                    gh(["pr", "edit", *target, "--add-reviewer", ",".join(triage.reviewers)], "")
                except GhFailed as error:
                    warnings.append(
                        "Ready にしたが、レビュアーを付けられなかった。"
                        f"GitHub の PR で付ける({error})"
                    )
            status, note = "requested", None
        case Action.RETURN:
            if not request.reason.strip() or len(request.reason) > MAX_REASON_LENGTH:
                raise InvalidReason(f"差し戻す理由は 1〜{MAX_REASON_LENGTH} 字で書く")
            assert triage.session is not None
            url = pull_request_url(triage.repo, triage.number)
            resume(triage.session, RETURN_PROMPT.format(url=url, reason=request.reason))
            status, note = "returned", request.reason
        case Action.HOLD:
            status, note = "on_hold", None
        case _:
            assert_never(action)
    recorded, report_changed = store.decide(triage, status, note, datetime.now(UTC))
    if not recorded:
        raise AlreadyDecided("反映はしたが、そのあいだに状態が変わったので記録していない")
    if report_changed:
        warnings.append(
            "判断のあいだに新しい報告が届いた。判断はそのまま記録したので、新しい報告を確かめる"
        )
    return Decision(status, " ".join(warnings))


def import_inbox(inbox: Path, store: TriageStore) -> int:
    imported = 0
    for path in sorted(inbox.glob("*.json")):
        try:
            written = path.stat()
            if written.st_size > MAX_BODY_BYTES:
                raise InvalidReport(f"本文は {MAX_BODY_BYTES} バイトまで")
            written_at = datetime.fromtimestamp(written.st_mtime, UTC)
            report = parse_body(path.read_bytes())
        except (OSError, ValueError, OverflowError) as error:
            print(f"{path} を取り込めませんでした: {error}", file=sys.stderr)
            continue
        if store.save(report, written_at):
            imported += 1
        path.unlink()
    return imported


class DashboardServer(ThreadingHTTPServer):
    def __init__(
        self, port: int, store: TriageStore, page: Path, gh: RunGh, resume: ResumeSession
    ) -> None:
        super().__init__((HOST, port), DashboardHandler)
        self.store = store
        self.page = page.read_bytes()
        self.gh = gh
        self.resume = resume
        self.decision_lock = threading.Lock()
        self.allowed_hosts = frozenset(
            {f"{HOST}:{self.server_port}", f"localhost:{self.server_port}"}
        )
        self.allowed_origins = frozenset(f"http://{host}" for host in self.allowed_hosts)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer
    timeout = REQUEST_TIMEOUT_SECONDS

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if not self._is_local_request():
            self._reject(HTTPStatus.FORBIDDEN, "ほかのサイトからの要求は受け付けない")
        elif url.path == "/":
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", self.server.page)
        elif url.path == "/api/prs":
            triages = self.server.store.list_triages()
            self._send_json(HTTPStatus.OK, [asdict(triage) for triage in triages])
        else:
            self._reject(HTTPStatus.NOT_FOUND, "見つからない")

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        action = ACTION_PATH.fullmatch(path)
        if not self._is_local_request():
            self._reject(HTTPStatus.FORBIDDEN, "ほかのサイトからの要求は受け付けない")
        elif path == "/api/prs":
            self._receive_report()
        elif path == "/api/shutdown":
            self._send_json(HTTPStatus.OK, {})
            self.server.shutdown()
        elif action:
            self._decide(int(action[1]), Action(action[2]))
        else:
            self._reject(HTTPStatus.NOT_FOUND, "見つからない")

    def _is_local_request(self) -> bool:
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in self.server.allowed_hosts and (
            origin is None or origin in self.server.allowed_origins
        )

    def _read_json_body(self) -> bytes | None:
        length = self.headers.get("Content-Length", "")
        if not (length.isascii() and length.isdigit()):
            self._reject(HTTPStatus.LENGTH_REQUIRED, "Content-Length を付ける")
            return None
        digits = length.lstrip("0") or "0"
        if len(digits) > len(str(MAX_BODY_BYTES)) or int(digits) > MAX_BODY_BYTES:
            self._reject(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"本文は {MAX_BODY_BYTES} バイトまで")
            return None
        try:
            body = self.rfile.read(int(digits))
        except TimeoutError:
            self._reject(HTTPStatus.REQUEST_TIMEOUT, "本文が届かない")
            return None
        if self.headers.get_content_type() != "application/json":
            self._reject(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "本文は application/json で送る")
            return None
        return body

    def _receive_report(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            report = parse_body(body)
        except InvalidReport as error:
            self._reject(HTTPStatus.BAD_REQUEST, str(error))
            return
        saved = self.server.store.save(report, datetime.now(UTC))
        self._send_json(HTTPStatus.OK, {"saved": saved})

    def _decide(self, triage_id: int, action: Action) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            request = parse_decision_request(body)
            with self.server.decision_lock:
                decision = decide(
                    self.server.store,
                    self.server.gh,
                    self.server.resume,
                    triage_id,
                    action,
                    request,
                )
        except DecisionError as error:
            self._reject(error.status, str(error))
            return
        except sqlite3.Error as error:
            self._reject(HTTPStatus.INTERNAL_SERVER_ERROR, f"判断を記録できなかった: {error}")
            return
        self._send_json(HTTPStatus.OK, asdict(decision))

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


def data_home() -> Path:
    home = Path(os.environ.get("TORIAGE_HOME", ""))
    if home.is_absolute():
        return home
    shared = Path(os.environ.get("XDG_DATA_HOME", ""))
    if not shared.is_absolute():
        shared = Path.home() / ".local" / "share"
    return shared / "toriage"


def main() -> None:
    home = data_home()
    store = TriageStore(home / "prs.db")
    server = DashboardServer(
        PORT, store, ROOT / "static" / "index.html", run_gh, resume_in_background(home / "returns")
    )
    pid_file = home / "server.pid"
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    imported = import_inbox(home / "inbox", store)
    if imported:
        print(f"inbox から {imported} 件取り込んだ")
    print(f"http://{HOST}:{PORT} で待ち受けている")
    with suppress(KeyboardInterrupt):
        server.serve_forever()
    pid_file.unlink(missing_ok=True)
    server.server_close()
    store.close()


def check_stdin() -> None:
    try:
        parse_body(sys.stdin.buffer.read())
    except InvalidReport as error:
        sys.exit(str(error))


if __name__ == "__main__":
    match sys.argv[1:]:
        case []:
            main()
        case ["--check"]:
            check_stdin()
        case _:
            sys.exit("使い方: python3 server.py [--check]")
