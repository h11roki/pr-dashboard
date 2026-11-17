from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server import HOST, ROOT, Agent, Session, TriageStore, parse_report

SCRIPT = ROOT / "bin" / "toriage-report"
REFUSED = f"http://{HOST}:1"
SESSION_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
ACCOUNT = "acme-bot"
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


NOTIFIERS = ("osascript", "notify-send")
END_OF_CALL = "-- end of call --"
SUBTITLE = "acme/web#128 · リスク 中"
TITLE_TO_KEEP = '-v [WIP] say "done" & <b>quit</b> \\ 終わり'


def write_command(bin_dir: Path, name: str, body: str) -> None:
    command = bin_dir / name
    command.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    command.chmod(0o755)


def recorder(tmp_path: Path) -> str:
    return (
        f'printf "%s\\n" "$(basename "$0")" "$@" "$(cat)" "{END_OF_CALL}"'
        f' >> "{tmp_path / "notified"}"'
    )


def write_commands(
    tmp_path: Path, gh_body: str, login: str | None = ACCOUNT, keychain: str = ACCOUNT
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    who = "exit 1" if login is None else f"echo {login}; exit 0"
    write_command(
        bin_dir,
        "gh",
        f'if [ "$1 $2" = "api user" ]; then {who}; fi\n'
        f'if [ "$1 $2" = "auth token" ]; then [ "$4" = {keychain} ] && echo token; exit; fi\n'
        f"{gh_body}",
    )
    write_command(bin_dir, "uname", "echo Linux")
    for name in NOTIFIERS:
        write_command(bin_dir, name, recorder(tmp_path))
    jq = shutil.which("jq")
    assert jq is not None
    (bin_dir / "jq").symlink_to(jq)
    (bin_dir / "python3").symlink_to(sys.executable)
    return bin_dir


def notified(tmp_path: Path, seconds: float) -> str:
    record = tmp_path / "notified"
    deadline = time.monotonic() + seconds
    while True:
        text = record.read_text(encoding="utf-8") if record.exists() else ""
        if text.endswith(f"{END_OF_CALL}\n") or time.monotonic() >= deadline:
            return text
        time.sleep(0.05)


@pytest.fixture
def fake_gh(tmp_path: Path) -> Path:
    return write_commands(tmp_path, f"echo '{json.dumps(GH_PR_VIEW, ensure_ascii=False)}'")


def judgment_of(report: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in report.items() if key not in FACTS_FROM_GH}


def run_script(
    bin_dir: Path,
    judgment: str,
    dashboard: str | None,
    home: Path,
    *args: str,
    session_id: str | None = None,
    codex_thread_id: str | None = None,
    cwd: Path | None = None,
    notify_setting: str | None = "0",
    display: str | None = ":0",
) -> subprocess.CompletedProcess[str]:
    environment = os.environ | {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "TORIAGE_HOME": str(home),
    }
    for name, value in (
        ("TORIAGE_URL", dashboard),
        ("CLAUDE_CODE_SESSION_ID", session_id),
        ("CODEX_THREAD_ID", codex_thread_id),
        ("TORIAGE_NOTIFY", notify_setting),
        ("DISPLAY", display),
        ("WAYLAND_DISPLAY", None),
    ):
        environment.pop(name, None)
        if value is not None:
            environment[name] = value
    return subprocess.run(
        [str(SCRIPT), *args],
        input=judgment,
        capture_output=True,
        text=True,
        env=environment,
        cwd=cwd,
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
    assert triage.report == report | {"account": ACCOUNT}
    assert triage.account == ACCOUNT


def test_does_not_take_the_account_from_the_judgment(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object], fake_gh: Path
) -> None:
    judgment = judgment_of(report) | {"account": "someone-else"}

    result = run_script(
        fake_gh, json.dumps(judgment), f"http://{HOST}:{port}", tmp_path / "home", "128"
    )

    assert result.returncode == 0, result.stderr
    [triage] = store.list_triages()
    assert triage.account == ACCOUNT


def test_sends_without_an_account_that_gh_has_no_login_for(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object]
) -> None:
    bin_dir = write_commands(
        tmp_path, f"echo '{json.dumps(GH_PR_VIEW, ensure_ascii=False)}'", keychain="acme-dev"
    )

    result = run_script(
        bin_dir, json.dumps(judgment_of(report)), f"http://{HOST}:{port}", tmp_path / "home", "128"
    )

    assert result.returncode == 0, result.stderr
    [triage] = store.list_triages()
    assert triage.account is None


def test_sends_without_an_account_when_gh_cannot_tell_who_it_is(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object]
) -> None:
    bin_dir = write_commands(
        tmp_path, f"echo '{json.dumps(GH_PR_VIEW, ensure_ascii=False)}'", login=None
    )

    result = run_script(
        bin_dir, json.dumps(judgment_of(report)), f"http://{HOST}:{port}", tmp_path / "home", "128"
    )

    assert result.returncode == 0, result.stderr
    [triage] = store.list_triages()
    assert triage.account is None


def checkout_of(tmp_path: Path, branch: str) -> Path:
    worktree = tmp_path / "worktree"
    (worktree / "docs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(worktree)], check=True)
    return worktree


def test_attaches_the_claude_code_session_and_the_checkout_of_the_pr(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object], fake_gh: Path
) -> None:
    worktree = checkout_of(tmp_path, "feat/search-paging")

    result = run_script(
        fake_gh,
        json.dumps(judgment_of(report)),
        f"http://{HOST}:{port}",
        tmp_path / "home",
        "128",
        session_id=SESSION_ID,
        cwd=worktree / "docs",
    )

    assert result.returncode == 0, result.stderr
    [triage] = store.list_triages()
    assert triage.session == Session(
        id=SESSION_ID, workdir=str(worktree.resolve()), agent=Agent.CLAUDE
    )


def test_attaches_the_codex_thread_and_the_checkout_of_the_pr(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object], fake_gh: Path
) -> None:
    worktree = checkout_of(tmp_path, "feat/search-paging")

    result = run_script(
        fake_gh,
        json.dumps(judgment_of(report)),
        f"http://{HOST}:{port}",
        tmp_path / "home",
        "128",
        codex_thread_id=SESSION_ID,
        cwd=worktree,
    )

    assert result.returncode == 0, result.stderr
    [triage] = store.list_triages()
    assert triage.session == Session(
        id=SESSION_ID, workdir=str(worktree.resolve()), agent=Agent.CODEX
    )


def test_does_not_attach_a_session_when_both_claude_code_and_codex_name_one(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object], fake_gh: Path
) -> None:
    result = run_script(
        fake_gh,
        json.dumps(judgment_of(report)),
        f"http://{HOST}:{port}",
        tmp_path / "home",
        "128",
        session_id=SESSION_ID,
        codex_thread_id="0b5e7c3a-1d2f-7a40-9e1b-2c3d4e5f6a7b",
        cwd=checkout_of(tmp_path, "feat/search-paging"),
    )

    assert result.returncode == 0, result.stderr
    assert "差し戻しはできない" in result.stderr
    [triage] = store.list_triages()
    assert triage.session is None


def test_does_not_attach_the_session_outside_the_branch_of_the_pr(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object], fake_gh: Path
) -> None:
    main_checkout = checkout_of(tmp_path, "main")

    result = run_script(
        fake_gh,
        json.dumps(judgment_of(report)),
        f"http://{HOST}:{port}",
        tmp_path / "home",
        "128",
        session_id=SESSION_ID,
        cwd=main_checkout,
    )

    assert result.returncode == 0, result.stderr
    [triage] = store.list_triages()
    assert triage.session is None


def test_sends_without_a_session_when_the_session_id_is_not_a_uuid(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object], fake_gh: Path
) -> None:
    result = run_script(
        fake_gh,
        json.dumps(judgment_of(report)),
        f"http://{HOST}:{port}",
        tmp_path / "home",
        "128",
        session_id="--dangerously-skip-permissions",
        cwd=checkout_of(tmp_path, "feat/search-paging"),
    )

    assert result.returncode == 0, result.stderr
    [triage] = store.list_triages()
    assert triage.session is None


def test_does_not_take_the_session_from_the_judgment(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object], fake_gh: Path
) -> None:
    judgment = judgment_of(report) | {"session": {"id": SESSION_ID, "workdir": "/elsewhere"}}

    result = run_script(
        fake_gh, json.dumps(judgment), f"http://{HOST}:{port}", tmp_path / "home", "128"
    )

    assert result.returncode == 0, result.stderr
    [triage] = store.list_triages()
    assert triage.session is None


@pytest.mark.parametrize(
    ("system", "notifier", "shown"),
    [
        ("Darwin", "terminal-notifier", TITLE_TO_KEEP),
        ("Darwin", "osascript", TITLE_TO_KEEP),
        ("Linux", "notify-send", '-v [WIP] say "done" &amp; &lt;b&gt;quit&lt;/b&gt; \\\\ 終わり'),
    ],
)
def test_notifies_with_the_pr_and_its_title_as_it_is(
    tmp_path: Path,
    port: int,
    store: TriageStore,
    report: dict[str, object],
    system: str,
    notifier: str,
    shown: str,
) -> None:
    facts = GH_PR_VIEW | {"title": TITLE_TO_KEEP}
    bin_dir = write_commands(
        tmp_path, f"cat <<'JSON'\n{json.dumps(facts, ensure_ascii=False)}\nJSON"
    )
    write_command(bin_dir, "uname", f"echo {system}")
    write_command(bin_dir, notifier, recorder(tmp_path))

    result = run_script(
        bin_dir,
        json.dumps(judgment_of(report)),
        f"http://{HOST}:{port}",
        tmp_path / "home",
        "128",
        notify_setting=None,
    )

    assert result.returncode == 0, result.stderr
    record = notified(tmp_path, 5)
    assert record.count(END_OF_CALL) == 1
    calls = record.splitlines()
    assert calls[0] == notifier
    assert SUBTITLE in calls
    assert shown in calls
    assert "-message" not in calls


def test_lets_terminal_notifier_open_the_dashboard_and_replace_the_last_notice_of_the_pr(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object], fake_gh: Path
) -> None:
    write_command(fake_gh, "uname", "echo Darwin")
    write_command(fake_gh, "terminal-notifier", recorder(tmp_path))

    result = run_script(
        fake_gh,
        json.dumps(judgment_of(report)),
        f"http://{HOST}:{port}/",
        tmp_path / "home",
        "128",
        notify_setting=None,
    )

    assert result.returncode == 0, result.stderr
    calls = notified(tmp_path, 5).splitlines()
    assert calls[0] == "terminal-notifier"
    assert calls[calls.index("-open") + 1] == f"http://{HOST}:{port}"
    assert calls[calls.index("-group") + 1] == "acme/web#128"


def test_does_not_notify_when_notifying_is_turned_off(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object], fake_gh: Path
) -> None:
    judgment = json.dumps(judgment_of(report))

    result = run_script(
        fake_gh, judgment, f"http://{HOST}:{port}", tmp_path / "home", "128", notify_setting="0"
    )

    assert result.returncode == 0, result.stderr
    assert len(store.list_triages()) == 1
    assert notified(tmp_path, 1) == ""


def test_does_not_notify_when_the_report_waits_in_the_inbox(
    tmp_path: Path, report: dict[str, object], fake_gh: Path
) -> None:
    judgment = json.dumps(judgment_of(report))

    result = run_script(fake_gh, judgment, REFUSED, tmp_path / "home", "128", notify_setting=None)

    assert result.returncode == 0, result.stderr
    assert notified(tmp_path, 1) == ""


def test_does_not_notify_when_a_newer_report_is_kept(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object], fake_gh: Path
) -> None:
    store.save(parse_report(report), datetime.now(timezone.utc) + timedelta(days=1))
    judgment = json.dumps(judgment_of(report))

    result = run_script(
        fake_gh, judgment, f"http://{HOST}:{port}", tmp_path / "home", "128", notify_setting=None
    )

    assert result.returncode == 0, result.stderr
    assert "置き換えなかった" in result.stderr
    assert notified(tmp_path, 1) == ""


def test_does_not_notify_without_a_display(
    tmp_path: Path, port: int, store: TriageStore, report: dict[str, object], fake_gh: Path
) -> None:
    judgment = json.dumps(judgment_of(report))

    result = run_script(
        fake_gh,
        judgment,
        f"http://{HOST}:{port}",
        tmp_path / "home",
        "128",
        notify_setting=None,
        display=None,
    )

    assert result.returncode == 0, result.stderr
    assert len(store.list_triages()) == 1
    assert notified(tmp_path, 1) == ""


def test_keeps_the_report_in_the_inbox_when_the_dashboard_is_down(
    tmp_path: Path, report: dict[str, object], fake_gh: Path
) -> None:
    inbox = tmp_path / "home" / "inbox"
    judgment = json.dumps(judgment_of(report))

    result = run_script(fake_gh, judgment, REFUSED, tmp_path / "home", "128")

    assert result.returncode == 0, result.stderr
    saved = json.loads((inbox / "acme_web-128.json").read_text(encoding="utf-8"))
    assert saved == report | {"account": ACCOUNT}


def test_starts_a_stopped_dashboard_before_sending(
    dashboard_home: Path, report: dict[str, object], fake_gh: Path
) -> None:
    judgment = json.dumps(judgment_of(report))

    result = run_script(fake_gh, judgment, None, dashboard_home, "128")

    assert result.returncode == 0, result.stderr
    assert "acme/web#128" in result.stdout
    with closing(TriageStore(dashboard_home / "prs.db")) as store:
        [triage] = store.list_triages()
    assert triage.report == report | {"account": ACCOUNT}


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
    failing_gh = write_commands(tmp_path, "exit 1")
    judgment = json.dumps(judgment_of(report))

    result = run_script(failing_gh, judgment, "http://unused", tmp_path / "home", "9")

    assert result.returncode == 1
    assert "PR 9" in result.stderr


def test_needs_a_pr_number(tmp_path: Path, fake_gh: Path) -> None:
    result = run_script(fake_gh, "{}", "http://unused", tmp_path / "home")

    assert result.returncode == 2
