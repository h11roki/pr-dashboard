import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import server
from server import (
    ROOT,
    SYNC_INTERVAL_SECONDS,
    DashboardServer,
    FetchStates,
    GhFailed,
    ResumeSession,
    TriageStore,
    parse_report,
    pull_request_states,
    pull_request_url,
)

MORNING = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
OLD_SCHEMA = """
CREATE TABLE prs (
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


@pytest.fixture
def dashboard(
    store: TriageStore,
    recording_gh: Callable[[list[str], str], None],
    recording_resume: ResumeSession,
    recording_states: FetchStates,
) -> Iterator[DashboardServer]:
    server = DashboardServer(
        0, store, ROOT / "static" / "index.html", recording_gh, recording_resume, recording_states
    )
    yield server
    server.server_close()


def save(
    store: TriageStore, report: dict[str, object], number: int, repo: str = "acme/web"
) -> None:
    store.save(
        parse_report(
            report | {"repo": repo, "number": number, "url": pull_request_url(repo, number)}
        ),
        MORNING,
    )


def by_number(store: TriageStore) -> dict[int, tuple[str, str | None, int]]:
    return {
        triage.number: (triage.status, triage.closed, len(triage.actions))
        for triage in store.list_triages()
    }


def test_moves_merged_and_closed_prs_and_keeps_open_ones(
    dashboard: DashboardServer,
    store: TriageStore,
    report: dict[str, object],
    github_states: dict[tuple[str, int], str],
) -> None:
    save(store, report, 128)
    save(store, report, 129)
    save(store, report, 5, repo="acme/api")
    [requested] = [triage for triage in store.list_triages() if triage.number == 129]
    store.decide(requested, "requested", None, MORNING)
    github_states.update(
        {("acme/web", 128): "MERGED", ("acme/web", 129): "CLOSED", ("acme/api", 5): "OPEN"}
    )

    closed = dashboard.sync_closed_if_due()

    assert closed == 2
    assert by_number(store) == {
        128: ("pending", "merged", 0),
        129: ("requested", "closed", 0),
        5: ("pending", None, 2),
    }


def test_waits_five_minutes_before_asking_github_again(
    dashboard: DashboardServer,
    store: TriageStore,
    report: dict[str, object],
    state_requests: list[list[tuple[str, int]]],
) -> None:
    save(store, report, 128)

    dashboard.sync_closed_if_due()
    dashboard.sync_closed_if_due()
    assert len(state_requests) == 1

    dashboard.synced_at = time.monotonic() - SYNC_INTERVAL_SECONDS - 1
    dashboard.sync_closed_if_due()
    assert len(state_requests) == 2


def test_asks_github_only_about_prs_that_are_still_open(
    dashboard: DashboardServer,
    store: TriageStore,
    report: dict[str, object],
    github_states: dict[tuple[str, int], str],
    state_requests: list[list[tuple[str, int]]],
) -> None:
    save(store, report, 128)
    save(store, report, 129)
    github_states[("acme/web", 128)] = "MERGED"
    dashboard.sync_closed_if_due()
    dashboard.synced_at = None

    dashboard.sync_closed_if_due()

    assert state_requests[-1] == [("acme/web", 129)]


def test_does_not_ask_github_when_nothing_is_open(
    dashboard: DashboardServer, state_requests: list[list[tuple[str, int]]]
) -> None:
    assert dashboard.sync_closed_if_due() == 0
    assert state_requests == []


def test_a_new_report_brings_a_closed_pr_back(
    store: TriageStore, report: dict[str, object]
) -> None:
    save(store, report, 128)
    [open_pr] = store.open_pull_requests()
    store.mark_closed({open_pr: "closed"})

    store.save(parse_report(report), MORNING + timedelta(hours=1))

    assert by_number(store) == {128: ("pending", None, 2)}


def test_adds_the_closed_column_to_a_database_made_before_it(
    tmp_path: Path, report: dict[str, object]
) -> None:
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(OLD_SCHEMA)
        connection.execute(
            "INSERT INTO prs (repo, number, payload, received_at) VALUES (?, ?, ?, ?)",
            ("acme/web", 128, json.dumps(report), MORNING.isoformat()),
        )

    with closing(TriageStore(path)) as store:
        [open_pr] = store.open_pull_requests()
        store.mark_closed({open_pr: "merged"})
        assert by_number(store) == {128: ("pending", "merged", 0)}


def write_gh(tmp_path: Path, output: str, code: int) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" >> "{tmp_path / "arguments"}"\n'
        f"cat <<'JSON'\n{output}\nJSON\n"
        'echo "Could not resolve to a Repository" >&2\n'
        f"exit {code}\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return bin_dir


def test_reads_the_states_of_many_prs_in_one_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answer = {
        "data": {
            "p0": {"pullRequest": {"state": "MERGED"}},
            "p1": None,
            "p2": {"pullRequest": {"state": "OPEN"}},
        }
    }
    monkeypatch.setenv(
        "PATH", f"{write_gh(tmp_path, json.dumps(answer), 1)}{os.pathsep}/usr/bin:/bin"
    )

    states = pull_request_states([("acme/web", 128), ("acme/gone", 1), ("acme/api", 5)])

    assert states == {("acme/web", 128): "MERGED", ("acme/api", 5): "OPEN"}
    assert "Could not resolve" in capsys.readouterr().err
    arguments = (tmp_path / "arguments").read_text(encoding="utf-8")
    assert arguments.startswith("api\ngraphql\n-f\n")
    assert (
        'p0: repository(owner: "acme", name: "web") { pullRequest(number: 128) { state } }'
        in arguments
    )


def test_fails_with_what_gh_said_when_the_answer_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", f"{write_gh(tmp_path, 'not json', 1)}{os.pathsep}/usr/bin:/bin")

    with pytest.raises(GhFailed, match="Could not resolve"):
        pull_request_states([("acme/web", 128)])


def test_does_not_close_a_pr_reported_again_while_github_was_asked(
    store: TriageStore, report: dict[str, object]
) -> None:
    save(store, report, 128)
    [asked] = store.open_pull_requests()
    store.save(parse_report(report), MORNING + timedelta(hours=1))

    assert store.mark_closed({asked: "closed"}) == 0
    assert by_number(store) == {128: ("pending", None, 2)}


def test_does_not_record_a_decision_on_a_pr_that_closed_meanwhile(
    store: TriageStore, report: dict[str, object]
) -> None:
    save(store, report, 128)
    [seen] = store.list_triages()
    [open_pr] = store.open_pull_requests()
    store.mark_closed({open_pr: "merged"})

    assert store.decide(seen, "on_hold", None, MORNING) == (False, False)
    assert by_number(store) == {128: ("pending", "merged", 0)}


def test_fails_when_github_returns_no_states_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = json.dumps({"data": None, "errors": [{"message": "too complex"}]})
    monkeypatch.setenv("PATH", f"{write_gh(tmp_path, answer, 1)}{os.pathsep}/usr/bin:/bin")

    with pytest.raises(GhFailed, match="Could not resolve"):
        pull_request_states([("acme/web", 128)])


def test_splits_many_prs_into_several_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = {"data": {"p0": {"pullRequest": {"state": "MERGED"}}}}
    monkeypatch.setenv(
        "PATH", f"{write_gh(tmp_path, json.dumps(answer), 0)}{os.pathsep}/usr/bin:/bin"
    )
    monkeypatch.setattr(server, "STATES_PER_QUERY", 2)

    states = pull_request_states([("acme/web", 1), ("acme/web", 2), ("acme/web", 3)])

    assert states == {("acme/web", 1): "MERGED", ("acme/web", 3): "MERGED"}
    calls = (tmp_path / "arguments").read_text(encoding="utf-8").count("graphql")
    assert calls == 2
