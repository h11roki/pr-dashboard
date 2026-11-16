import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from pathlib import Path

import pytest

from server import (
    HOST,
    MAX_REASON_LENGTH,
    Action,
    DecisionError,
    DecisionRequest,
    ResumeFailed,
    ResumeSession,
    Session,
    SessionBusy,
    Triage,
    TriageStore,
    decide,
    parse_report,
)

GhCalls = list[tuple[list[str], str]]
Resumed = list[tuple[Session, str]]
SESSION = Session(id="0f8fad5b-d9cb-469f-a165-70867728950e", workdir="/work/web")


def from_a_session(report: dict[str, object]) -> dict[str, object]:
    return report | {"session": {"id": SESSION.id, "workdir": SESSION.workdir}}


def save_pending(store: TriageStore, report: dict[str, object]) -> Triage:
    store.save(parse_report(report), datetime.now(UTC))
    [triage] = store.list_triages()
    return triage


def post_decision(
    port: int,
    triage: Triage,
    action: str,
    reason: str = "",
    headers: dict[str, str] | None = None,
    seen: str | None = None,
) -> tuple[int, dict[str, str]]:
    body = json.dumps({"seen": triage.received_at if seen is None else seen, "reason": reason})
    connection = HTTPConnection(HOST, port, timeout=5)
    connection.request(
        "POST",
        f"/api/prs/{triage.id}/{action}",
        body.encode(),
        {"Content-Type": "application/json", **(headers or {})},
    )
    response = connection.getresponse()
    result = response.status, json.loads(response.read())
    connection.close()
    return result


def status_of(store: TriageStore, triage: Triage) -> str | None:
    found = store.find(triage.id)
    return None if found is None else found.status


def test_requesting_a_review_marks_the_pr_ready(
    port: int, store: TriageStore, report: dict[str, object], gh_calls: GhCalls
) -> None:
    triage = save_pending(store, report)

    status, body = post_decision(port, triage, "request-review")

    assert (status, body) == (200, {"status": "requested", "warning": ""})
    assert gh_calls == [(["pr", "ready", "128", "-R", "acme/web"], "")]
    assert status_of(store, triage) == "requested"


def test_requesting_a_review_adds_the_suggested_reviewers(
    port: int, store: TriageStore, report: dict[str, object], gh_calls: GhCalls
) -> None:
    triage = save_pending(store, report | {"suggested_reviewers": ["alice", "acme/core"]})

    post_decision(port, triage, "request-review")

    assert gh_calls[1] == (
        ["pr", "edit", "128", "-R", "acme/web", "--add-reviewer", "alice,acme/core"],
        "",
    )


def test_a_reviewer_that_cannot_be_added_only_warns(
    port: int, store: TriageStore, report: dict[str, object], gh_failures: dict[str, str]
) -> None:
    triage = save_pending(store, report | {"suggested_reviewers": ["h11roki"]})
    gh_failures["edit"] = "Review cannot be requested from pull request author."

    status, body = post_decision(port, triage, "request-review")

    assert status == 200
    assert "author" in body["warning"]
    assert status_of(store, triage) == "requested"


def test_a_stored_reviewer_that_no_longer_passes_is_left_out(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object], gh_calls: GhCalls
) -> None:
    save_pending(store, report)
    with closing(sqlite3.connect(tmp_path / "prs.db")) as old, old:
        old.execute(
            "UPDATE prs SET payload = json_set(payload, '$.suggested_reviewers', json(?))",
            (json.dumps(["@alice", "bob"]),),
        )
    [triage] = store.list_triages()

    status, _ = post_decision(port, triage, "request-review")

    assert triage.reviewers == ("bob",)
    assert status == 200
    assert gh_calls[1][0][-1] == "bob"


def test_returning_a_pr_hands_the_reason_to_the_session_that_made_it(
    port: int, store: TriageStore, report: dict[str, object], gh_calls: GhCalls, resumed: Resumed
) -> None:
    triage = save_pending(store, from_a_session(report))

    status, _ = post_decision(port, triage, "return", "テストが足りない")

    assert status == 200
    assert gh_calls == []
    [(session, prompt)] = resumed
    assert session == SESSION
    assert "https://github.com/acme/web/pull/128" in prompt
    assert "テストが足りない" in prompt
    assert "toriage:report" in prompt
    returned = store.find(triage.id)
    assert returned is not None
    assert (returned.status, returned.note) == ("returned", "テストが足りない")


@pytest.mark.parametrize("reason", ["", "   ", "あ" * (MAX_REASON_LENGTH + 1)])
def test_returning_needs_a_reason(
    port: int, store: TriageStore, report: dict[str, object], resumed: Resumed, reason: str
) -> None:
    triage = save_pending(store, from_a_session(report))

    status, _ = post_decision(port, triage, "return", reason)

    assert status == 400
    assert resumed == []
    assert status_of(store, triage) == "pending"


def test_a_report_without_its_session_cannot_be_returned(
    port: int, store: TriageStore, report: dict[str, object], resumed: Resumed
) -> None:
    triage = save_pending(store, report)

    status, body = post_decision(port, triage, "return", "テストが足りない")

    assert Action.RETURN not in triage.actions
    assert status == 409
    assert "セッション" in body["error"]
    assert resumed == []
    assert status_of(store, triage) == "pending"


def test_a_stored_session_that_no_longer_passes_is_left_out(
    tmp_path: Path, store: TriageStore, report: dict[str, object]
) -> None:
    save_pending(store, from_a_session(report))
    with closing(sqlite3.connect(tmp_path / "prs.db")) as old, old:
        old.execute("UPDATE prs SET payload = json_set(payload, '$.session.id', '-rf')")

    [triage] = store.list_triages()

    assert triage.session is None
    assert Action.RETURN not in triage.actions


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (ResumeFailed("PR を作った場所 /work/web がもうない"), 502),
        (SessionBusy("この会話は前の差し戻しをまだ直している"), 409),
    ],
)
def test_a_session_that_cannot_be_resumed_leaves_the_status_alone(
    port: int,
    store: TriageStore,
    report: dict[str, object],
    resume_failures: list[DecisionError],
    failure: DecisionError,
    expected: int,
) -> None:
    triage = save_pending(store, from_a_session(report))
    resume_failures.append(failure)

    status, body = post_decision(port, triage, "return", "テストが足りない")

    assert status == expected
    assert body["error"] == str(failure)
    assert status_of(store, triage) == "pending"


def test_a_returned_pr_can_be_returned_again_when_the_session_stopped(
    port: int, store: TriageStore, report: dict[str, object], resumed: Resumed
) -> None:
    triage = save_pending(store, from_a_session(report))
    post_decision(port, triage, "return", "テストが足りない")
    [returned] = store.list_triages()

    status, _ = post_decision(port, returned, "return", "もう一度直して")

    assert returned.actions == (Action.RETURN,)
    assert status == 200
    assert [prompt for _, prompt in resumed][-1].count("もう一度直して") == 1
    found = store.find(triage.id)
    assert found is not None
    assert (found.status, found.note) == ("returned", "もう一度直して")


def test_holding_changes_only_the_status_and_can_be_decided_later(
    port: int, store: TriageStore, report: dict[str, object], gh_calls: GhCalls
) -> None:
    triage = save_pending(store, report)

    held, _ = post_decision(port, triage, "hold")
    assert (held, gh_calls, status_of(store, triage)) == (200, [], "on_hold")

    requested, _ = post_decision(port, triage, "request-review")
    assert (requested, status_of(store, triage)) == (200, "requested")


def test_a_held_pr_cannot_be_held_again(
    port: int, store: TriageStore, report: dict[str, object]
) -> None:
    triage = save_pending(store, report)
    post_decision(port, triage, "hold")

    status, _ = post_decision(port, triage, "hold")

    assert status == 409


def test_lists_the_decisions_each_pr_can_take(
    port: int, store: TriageStore, report: dict[str, object]
) -> None:
    triage = save_pending(store, from_a_session(report))
    post_decision(port, triage, "hold")

    [held] = store.list_triages()

    assert held.actions == (Action.REQUEST_REVIEW, Action.RETURN)


def test_a_decision_on_a_report_the_screen_has_not_seen_is_refused(
    port: int, store: TriageStore, report: dict[str, object], gh_calls: GhCalls
) -> None:
    triage = save_pending(store, report)

    status, body = post_decision(
        port, triage, "request-review", seen="2026-01-01T00:00:00.000+00:00"
    )

    assert status == 409
    assert "読み直して" in body["error"]
    assert gh_calls == []


def test_a_failing_gh_leaves_the_status_alone(
    port: int, store: TriageStore, report: dict[str, object], gh_failures: dict[str, str]
) -> None:
    triage = save_pending(store, report)
    gh_failures["ready"] = "HTTP 404: Not Found"

    status, body = post_decision(port, triage, "request-review")

    assert status == 502
    assert "404" in body["error"]
    assert status_of(store, triage) == "pending"


def test_a_decided_pr_cannot_be_decided_again(
    port: int, store: TriageStore, report: dict[str, object]
) -> None:
    triage = save_pending(store, report)
    post_decision(port, triage, "request-review")

    status, _ = post_decision(port, triage, "hold")

    assert status == 409


def test_deciding_an_unknown_pr_is_not_found(
    port: int, store: TriageStore, report: dict[str, object]
) -> None:
    missing = replace(save_pending(store, report), id=999)

    status, _ = post_decision(port, missing, "hold")

    assert status == 404


def test_a_decision_needs_a_json_body(
    port: int, store: TriageStore, report: dict[str, object], gh_calls: GhCalls
) -> None:
    triage = save_pending(store, report)

    status, _ = post_decision(
        port, triage, "request-review", headers={"Content-Type": "text/plain"}
    )

    assert status == 415
    assert gh_calls == []


def test_a_decision_from_another_site_is_refused(
    port: int, store: TriageStore, report: dict[str, object], gh_calls: GhCalls
) -> None:
    triage = save_pending(store, report)

    status, _ = post_decision(
        port, triage, "request-review", headers={"Origin": "https://attacker.example"}
    )

    assert status == 403
    assert gh_calls == []


def test_a_report_that_arrives_while_returning_is_recorded_with_a_warning(
    store: TriageStore,
    report: dict[str, object],
    recording_gh: Callable[[list[str], str], None],
) -> None:
    triage = save_pending(store, from_a_session(report))

    def resume_while_a_new_report_arrives(session: Session, prompt: str) -> None:
        store.save(
            parse_report(from_a_session(report) | {"title": "直した題"}),
            datetime.now(UTC) + timedelta(seconds=1),
        )

    resume: ResumeSession = resume_while_a_new_report_arrives
    decision = decide(
        store,
        recording_gh,
        resume,
        triage.id,
        Action.RETURN,
        DecisionRequest(seen=triage.received_at, reason="直して"),
    )

    assert decision.status == "returned"
    assert "新しい報告" in decision.warning
    assert status_of(store, triage) == "returned"
