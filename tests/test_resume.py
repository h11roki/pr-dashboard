import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

import server
from server import Agent, GhFailed, ResumeFailed, Session, SessionBusy, resume_in_background

SESSION_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
FAKE_AGENT = """#!/usr/bin/env python3
import json, os, sys, time
with open({calls!r}, "w") as calls:
    json.dump(
        {{
            "args": sys.argv[1:],
            "cwd": os.getcwd(),
            "session_variable": os.environ.get("CLAUDE_CODE_SESSION_ID"),
            "codex_thread_variable": os.environ.get("CODEX_THREAD_ID"),
            "path": os.environ.get("PATH"),
            "gh_token": os.environ.get("GH_TOKEN"),
        }},
        calls,
    )
while not os.path.exists({release!r}):
    time.sleep(0.05)
print("直して送り直した")
"""


CODEX_RESUME_ARGS = [
    "exec",
    "resume",
    "-c",
    'sandbox_mode="workspace-write"',
    "-c",
    'approval_policy="on-request"',
    "-c",
    'approvals_reviewer="auto_review"',
]


def wait_until(probe: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not probe():
        if time.monotonic() > deadline:
            raise AssertionError("待っても起きなかった")
        time.sleep(0.05)


class FakeAgent:
    def __init__(self, root: Path) -> None:
        self.bin_dir = root / "bin"
        self.calls = root / "calls.json"
        self.release = root / "release"

    def call(self) -> dict[str, object]:
        wait_until(lambda: self.calls.exists() and bool(self.calls.read_text(encoding="utf-8")))
        loaded: dict[str, object] = json.loads(self.calls.read_text(encoding="utf-8"))
        return loaded

    def finish(self) -> None:
        self.release.touch()


def install_fake_agent(command: Path, fake: FakeAgent) -> None:
    command.parent.mkdir(parents=True, exist_ok=True)
    command.write_text(
        FAKE_AGENT.format(calls=str(fake.calls), release=str(fake.release)), encoding="utf-8"
    )
    command.chmod(0o755)


@pytest.fixture
def fake_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeAgent]:
    fake = FakeAgent(tmp_path)
    for name in ("claude", "codex"):
        install_fake_agent(fake.bin_dir / name, fake)
    monkeypatch.setenv("PATH", f"{fake.bin_dir}:/usr/bin:/bin")
    monkeypatch.delenv("TORIAGE_CODEX", raising=False)
    monkeypatch.setattr(server, "CODEX_LOCATIONS", (tmp_path / "no-app" / "codex",))
    yield fake
    fake.finish()


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    path = tmp_path / "worktree"
    path.mkdir()
    return path


def log_of(logs: Path) -> str:
    log = logs / f"{SESSION_ID}.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_resumes_the_session_in_its_workdir_with_auto_mode(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "the-session-that-started-the-server")
    monkeypatch.setenv("CODEX_THREAD_ID", "the-thread-that-started-the-server")
    logs = tmp_path / "returns"

    resume_in_background(logs)(Session(id=SESSION_ID, workdir=str(workdir)), "直してください", None)

    call = fake_agent.call()
    assert call["args"] == [
        "-p",
        "--resume",
        SESSION_ID,
        "--permission-mode",
        "auto",
        "--permission-prompts",
        "none",
        "直してください",
    ]
    assert Path(str(call["cwd"])).resolve() == workdir.resolve()
    assert call["session_variable"] is None
    assert call["codex_thread_variable"] is None
    fake_agent.finish()
    wait_until(lambda: "終了コード 0" in log_of(logs))
    assert "に差し戻した" in log_of(logs)
    assert "直して送り直した" in log_of(logs)


def test_resumes_a_codex_session_with_automatic_review(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "the-session-that-started-the-server")
    monkeypatch.setenv("CODEX_THREAD_ID", "the-thread-that-started-the-server")
    logs = tmp_path / "returns"

    resume_in_background(logs)(
        Session(id=SESSION_ID, workdir=str(workdir), agent=Agent.CODEX), "直してください", None
    )

    call = fake_agent.call()
    assert call["args"] == [*CODEX_RESUME_ARGS, SESSION_ID, "直してください"]
    assert Path(str(call["cwd"])).resolve() == workdir.resolve()
    assert call["session_variable"] is None
    assert call["codex_thread_variable"] is None
    fake_agent.finish()
    wait_until(lambda: "終了コード 0" in log_of(logs))


def test_uses_the_codex_named_in_toriage_codex(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "elsewhere" / "my-codex"
    install_fake_agent(configured, fake_agent)
    (fake_agent.bin_dir / "codex").unlink()
    monkeypatch.setenv("TORIAGE_CODEX", str(configured))

    resume_in_background(tmp_path / "returns")(
        Session(id=SESSION_ID, workdir=str(workdir), agent=Agent.CODEX), "直してください", None
    )

    assert fake_agent.call()["args"] == [*CODEX_RESUME_ARGS, SESSION_ID, "直してください"]


def test_rejects_a_relative_toriage_codex(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TORIAGE_CODEX", "bin/codex")
    logs = tmp_path / "returns"

    with pytest.raises(ResumeFailed, match="絶対パス"):
        resume_in_background(logs)(
            Session(id=SESSION_ID, workdir=str(workdir), agent=Agent.CODEX), "直してください", None
        )

    assert not fake_agent.calls.exists()
    assert not logs.exists()


def test_names_toriage_codex_when_the_codex_it_names_cannot_run(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TORIAGE_CODEX", str(tmp_path / "moved" / "codex"))
    logs = tmp_path / "returns"

    with pytest.raises(ResumeFailed, match="TORIAGE_CODEX"):
        resume_in_background(logs)(
            Session(id=SESSION_ID, workdir=str(workdir), agent=Agent.CODEX), "直してください", None
        )

    assert not fake_agent.calls.exists()
    assert not logs.exists()


@pytest.mark.parametrize("agent", list(Agent))
def test_does_not_run_an_agent_found_through_a_relative_path_entry(
    tmp_path: Path,
    fake_agent: FakeAgent,
    workdir: Path,
    agent: Agent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_agent(workdir / "tools" / agent.value, fake_agent)
    (fake_agent.bin_dir / agent.value).unlink()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("PATH", f"tools{os.pathsep}{os.environ['PATH']}")

    with pytest.raises(ResumeFailed, match=f"{agent.value} が見つからない"):
        resume_in_background(tmp_path / "returns")(
            Session(id=SESSION_ID, workdir=str(workdir), agent=agent), "直してください", None
        )

    assert not fake_agent.calls.exists()


def test_finds_codex_inside_an_app_when_it_is_not_on_the_path(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_codex = tmp_path / "ChatGPT.app" / "codex"
    install_fake_agent(app_codex, fake_agent)
    (fake_agent.bin_dir / "codex").unlink()
    monkeypatch.setattr(server, "CODEX_LOCATIONS", (tmp_path / "Codex.app" / "codex", app_codex))

    resume_in_background(tmp_path / "returns")(
        Session(id=SESSION_ID, workdir=str(workdir), agent=Agent.CODEX), "直してください", None
    )

    assert fake_agent.call()["args"] == [*CODEX_RESUME_ARGS, SESSION_ID, "直してください"]


def test_fails_when_codex_cannot_be_found(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path
) -> None:
    (fake_agent.bin_dir / "codex").unlink()
    logs = tmp_path / "returns"

    with pytest.raises(ResumeFailed, match="TORIAGE_CODEX"):
        resume_in_background(logs)(
            Session(id=SESSION_ID, workdir=str(workdir), agent=Agent.CODEX), "直してください", None
        )

    assert not fake_agent.calls.exists()
    assert not logs.exists()


def install_report_command(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    command = bin_dir / "toriage-report"
    command.write_text("#!/bin/sh\n", encoding="utf-8")
    command.chmod(0o755)


def install_plugin(root: Path) -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    install_report_command(root / "bin")
    return root / "bin"


def test_leaves_out_the_plugin_bin_the_server_was_started_with(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    older_plugin_bin = install_plugin(tmp_path / "plugins" / "toriage" / "0.1.0")
    path_without_plugin = os.environ["PATH"]
    monkeypatch.setenv("PATH", f"{older_plugin_bin}{os.pathsep}{path_without_plugin}")

    resume_in_background(tmp_path / "returns")(
        Session(id=SESSION_ID, workdir=str(workdir)), "直してください", None
    )

    assert fake_agent.call()["path"] == path_without_plugin


def test_leaves_out_a_plugin_bin_reached_through_a_symlink(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked_bin = tmp_path / "linked-bin"
    linked_bin.symlink_to(install_plugin(tmp_path / "plugins" / "toriage" / "0.1.0"))
    path_without_plugin = os.environ["PATH"]
    monkeypatch.setenv("PATH", f"{linked_bin}{os.pathsep}{path_without_plugin}")

    resume_in_background(tmp_path / "returns")(
        Session(id=SESSION_ID, workdir=str(workdir)), "直してください", None
    )

    assert fake_agent.call()["path"] == path_without_plugin


def test_checks_a_relative_path_entry_from_the_workdir(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_plugin(workdir)
    data_home = tmp_path / "data-home"
    data_home.mkdir()
    monkeypatch.chdir(data_home)
    path_without_plugin = os.environ["PATH"]
    monkeypatch.setenv("PATH", f"bin{os.pathsep}{path_without_plugin}")

    resume_in_background(tmp_path / "returns")(
        Session(id=SESSION_ID, workdir=str(workdir)), "直してください", None
    )

    assert fake_agent.call()["path"] == path_without_plugin


def test_keeps_a_directory_that_holds_the_report_command_outside_a_plugin(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path
) -> None:
    install_report_command(fake_agent.bin_dir)

    resume_in_background(tmp_path / "returns")(
        Session(id=SESSION_ID, workdir=str(workdir)), "直してください", None
    )

    assert fake_agent.call()["path"] == os.environ["PATH"]


@pytest.mark.parametrize("agent", list(Agent))
def test_does_not_resume_a_session_that_is_still_running(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path, agent: Agent
) -> None:
    logs = tmp_path / "returns"
    resume = resume_in_background(logs)
    session = Session(id=SESSION_ID, workdir=str(workdir), agent=agent)
    resume(session, "直してください", None)
    fake_agent.call()

    with pytest.raises(SessionBusy, match="まだ直している"):
        resume(session, "もう一度", None)

    fake_agent.finish()
    wait_until(lambda: "終了コード" in log_of(logs))
    resume(session, "直ったか確かめて", None)
    wait_until(lambda: log_of(logs).count("終了コード") == 2)


@pytest.mark.parametrize("agent", list(Agent))
def test_sees_a_running_resume_even_when_columns_is_narrow(
    tmp_path: Path,
    fake_agent: FakeAgent,
    workdir: Path,
    agent: Agent,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "40")
    resume = resume_in_background(tmp_path / "returns")
    session = Session(id=SESSION_ID, workdir=str(workdir), agent=agent)
    resume(session, "直してください", None)
    fake_agent.call()

    with pytest.raises(SessionBusy, match="まだ直している"):
        resume(session, "もう一度", None)


SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)"]


@pytest.mark.parametrize(
    ("agent", "stranger"),
    [
        (Agent.CLAUDE, ["sleep", "30"]),
        (Agent.CLAUDE, [*SLEEPER, "claude", "--resume", SESSION_ID]),
        (Agent.CODEX, [*SLEEPER, "codex", "resume", SESSION_ID]),
    ],
    ids=["unrelated", "claude-opened-by-hand", "codex-opened-by-hand"],
)
def test_a_pid_left_behind_by_another_process_does_not_block(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path, agent: Agent, stranger: list[str]
) -> None:
    logs = tmp_path / "returns"
    logs.mkdir()
    with subprocess.Popen(stranger) as reused:
        (logs / f"{SESSION_ID}.pid").write_text(f"{reused.pid}\n", encoding="utf-8")
        try:
            resume_in_background(logs)(
                Session(id=SESSION_ID, workdir=str(workdir), agent=agent), "直して", None
            )

            assert fake_agent.call()["args"]
        finally:
            reused.kill()


def is_running(pid: int) -> bool:
    state = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, check=False
    ).stdout.strip()
    return state != "" and not state.startswith("Z")


def test_stops_the_agent_and_what_it_started_when_it_cannot_record_the_resume(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child_pid = tmp_path / "child.pid"
    wrapper = fake_agent.bin_dir / "claude"
    wrapper.write_text(
        f"#!/bin/sh\nsleep 30 &\necho $! > {child_pid}.tmp\nmv {child_pid}.tmp {child_pid}\nwait\n",
        encoding="utf-8",
    )

    class PopenAfterTheChildStarts(subprocess.Popen[bytes]):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            wait_until(child_pid.exists)

    monkeypatch.setattr(subprocess, "Popen", PopenAfterTheChildStarts)
    logs = tmp_path / "returns"
    (logs / f"{SESSION_ID}.pid").mkdir(parents=True)

    with pytest.raises(ResumeFailed, match="書き残せなかった"):
        resume_in_background(logs)(Session(id=SESSION_ID, workdir=str(workdir)), "直して", None)

    wait_until(lambda: not is_running(int(child_pid.read_text(encoding="utf-8"))))


def test_reports_that_it_could_not_record_even_when_the_agent_has_already_exited(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (fake_agent.bin_dir / "claude").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    class PopenAfterTheAgentExits(subprocess.Popen[bytes]):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOWAIT)

    monkeypatch.setattr(subprocess, "Popen", PopenAfterTheAgentExits)
    logs = tmp_path / "returns"
    (logs / f"{SESSION_ID}.pid").mkdir(parents=True)

    with pytest.raises(ResumeFailed, match="書き残せなかった"):
        resume_in_background(logs)(Session(id=SESSION_ID, workdir=str(workdir)), "直して", None)


def test_fails_when_the_workdir_is_gone(tmp_path: Path, fake_agent: FakeAgent) -> None:
    session = Session(id=SESSION_ID, workdir=str(tmp_path / "removed-worktree"))

    with pytest.raises(ResumeFailed, match="removed-worktree"):
        resume_in_background(tmp_path / "returns")(session, "直してください", None)

    assert not fake_agent.calls.exists()


def test_fails_when_the_log_cannot_be_written(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path
) -> None:
    logs = tmp_path / "returns"
    logs.write_text("ディレクトリではない", encoding="utf-8")

    with pytest.raises(ResumeFailed, match="起動できなかった"):
        resume_in_background(logs)(Session(id=SESSION_ID, workdir=str(workdir)), "直して", None)

    assert not fake_agent.calls.exists()


def test_fails_when_claude_code_is_not_installed(
    tmp_path: Path, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    logs = tmp_path / "returns"

    with pytest.raises(ResumeFailed, match="Claude Code"):
        resume_in_background(logs)(
            Session(id=SESSION_ID, workdir=str(workdir)), "直してください", None
        )

    assert not logs.exists()


def install_gh_logged_in_as(bin_dir: Path, account: str) -> None:
    gh = bin_dir / "gh"
    gh.write_text(
        f'#!/bin/sh\n[ "$*" = "auth token --user {account}" ] || exit 1\necho token-of-{account}\n',
        encoding="utf-8",
    )
    gh.chmod(0o755)


def test_lets_the_resumed_agent_use_github_as_the_account_that_made_the_pr(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_gh_logged_in_as(fake_agent.bin_dir, "acme-bot")
    monkeypatch.setenv("GH_TOKEN", "token-the-server-was-started-with")

    resume_in_background(tmp_path / "returns")(
        Session(id=SESSION_ID, workdir=str(workdir)), "直してください", "acme-bot"
    )

    assert fake_agent.call()["gh_token"] == "token-of-acme-bot"


def test_leaves_github_to_the_active_account_when_the_report_has_no_account(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)

    resume_in_background(tmp_path / "returns")(
        Session(id=SESSION_ID, workdir=str(workdir)), "直してください", None
    )

    assert fake_agent.call()["gh_token"] is None


def test_does_not_resume_when_gh_is_not_logged_in_as_the_account(
    tmp_path: Path, fake_agent: FakeAgent, workdir: Path
) -> None:
    install_gh_logged_in_as(fake_agent.bin_dir, "acme-bot")
    logs = tmp_path / "returns"

    with pytest.raises(GhFailed, match="acme-dev でログインしていない"):
        resume_in_background(logs)(
            Session(id=SESSION_ID, workdir=str(workdir)), "直してください", "acme-dev"
        )

    assert not fake_agent.calls.exists()
    assert not logs.exists()
