import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from server import ResumeFailed, Session, SessionBusy, resume_in_background

SESSION_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys, time
with open({calls!r}, "w") as calls:
    json.dump(
        {{
            "args": sys.argv[1:],
            "cwd": os.getcwd(),
            "session_variable": os.environ.get("CLAUDE_CODE_SESSION_ID"),
            "path": os.environ.get("PATH"),
        }},
        calls,
    )
while not os.path.exists({release!r}):
    time.sleep(0.05)
print("直して送り直した")
"""


def wait_until(probe: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not probe():
        if time.monotonic() > deadline:
            raise AssertionError("待っても起きなかった")
        time.sleep(0.05)


class FakeClaude:
    def __init__(self, root: Path) -> None:
        self.calls = root / "calls.json"
        self.release = root / "release"

    def call(self) -> dict[str, object]:
        wait_until(lambda: self.calls.exists() and bool(self.calls.read_text(encoding="utf-8")))
        loaded: dict[str, object] = json.loads(self.calls.read_text(encoding="utf-8"))
        return loaded

    def finish(self) -> None:
        self.release.touch()


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeClaude:
    fake = FakeClaude(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(
        FAKE_CLAUDE.format(calls=str(fake.calls), release=str(fake.release)), encoding="utf-8"
    )
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return fake


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    path = tmp_path / "worktree"
    path.mkdir()
    return path


def log_of(logs: Path) -> str:
    log = logs / f"{SESSION_ID}.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_resumes_the_session_in_its_workdir_with_auto_mode(
    tmp_path: Path, fake_claude: FakeClaude, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "the-session-that-started-the-server")
    logs = tmp_path / "returns"

    resume_in_background(logs)(Session(id=SESSION_ID, workdir=str(workdir)), "直してください")

    call = fake_claude.call()
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
    fake_claude.finish()
    wait_until(lambda: "終了コード 0" in log_of(logs))
    assert "に差し戻した" in log_of(logs)
    assert "直して送り直した" in log_of(logs)


def test_leaves_out_the_plugin_bin_the_server_was_started_with(
    tmp_path: Path, fake_claude: FakeClaude, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    older_plugin = tmp_path / "plugins" / "toriage" / "0.1.0"
    (older_plugin / ".claude-plugin").mkdir(parents=True)
    (older_plugin / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    (older_plugin / "bin").mkdir()
    (older_plugin / "bin" / "toriage-report").write_text("#!/bin/sh\n", encoding="utf-8")
    path_without_plugin = os.environ["PATH"]
    monkeypatch.setenv("PATH", f"{older_plugin / 'bin'}{os.pathsep}{path_without_plugin}")

    resume_in_background(tmp_path / "returns")(
        Session(id=SESSION_ID, workdir=str(workdir)), "直してください"
    )

    assert fake_claude.call()["path"] == path_without_plugin
    fake_claude.finish()


def test_keeps_a_directory_that_holds_the_report_command_outside_a_plugin(
    tmp_path: Path, fake_claude: FakeClaude, workdir: Path
) -> None:
    shared_with_claude = Path(os.environ["PATH"].split(os.pathsep)[0])
    (shared_with_claude / "toriage-report").write_text("#!/bin/sh\n", encoding="utf-8")

    resume_in_background(tmp_path / "returns")(
        Session(id=SESSION_ID, workdir=str(workdir)), "直してください"
    )

    assert fake_claude.call()["path"] == os.environ["PATH"]
    fake_claude.finish()


def test_does_not_resume_a_session_that_is_still_running(
    tmp_path: Path, fake_claude: FakeClaude, workdir: Path
) -> None:
    logs = tmp_path / "returns"
    resume = resume_in_background(logs)
    session = Session(id=SESSION_ID, workdir=str(workdir))
    resume(session, "直してください")
    fake_claude.call()

    with pytest.raises(SessionBusy, match="まだ直している"):
        resume(session, "もう一度")

    fake_claude.finish()
    wait_until(lambda: "終了コード" in log_of(logs))
    resume(session, "直ったか確かめて")
    wait_until(lambda: log_of(logs).count("終了コード") == 2)


def test_a_pid_left_behind_by_another_process_does_not_block(
    tmp_path: Path, fake_claude: FakeClaude, workdir: Path
) -> None:
    logs = tmp_path / "returns"
    logs.mkdir()
    with subprocess.Popen(["sleep", "30"]) as stranger:
        (logs / f"{SESSION_ID}.pid").write_text(f"{stranger.pid}\n", encoding="utf-8")
        try:
            resume_in_background(logs)(Session(id=SESSION_ID, workdir=str(workdir)), "直して")

            assert fake_claude.call()["args"]
        finally:
            stranger.kill()
            fake_claude.finish()


def test_stops_claude_when_it_cannot_record_the_resume(
    tmp_path: Path, fake_claude: FakeClaude, workdir: Path
) -> None:
    logs = tmp_path / "returns"
    (logs / f"{SESSION_ID}.pid").mkdir(parents=True)

    with pytest.raises(ResumeFailed, match="書き残せなかった"):
        resume_in_background(logs)(Session(id=SESSION_ID, workdir=str(workdir)), "直して")


def test_fails_when_the_workdir_is_gone(tmp_path: Path, fake_claude: FakeClaude) -> None:
    session = Session(id=SESSION_ID, workdir=str(tmp_path / "removed-worktree"))

    with pytest.raises(ResumeFailed, match="removed-worktree"):
        resume_in_background(tmp_path / "returns")(session, "直してください")

    assert not fake_claude.calls.exists()


def test_fails_when_the_log_cannot_be_written(
    tmp_path: Path, fake_claude: FakeClaude, workdir: Path
) -> None:
    logs = tmp_path / "returns"
    logs.write_text("ディレクトリではない", encoding="utf-8")

    with pytest.raises(ResumeFailed, match="起動できなかった"):
        resume_in_background(logs)(Session(id=SESSION_ID, workdir=str(workdir)), "直して")

    assert not fake_claude.calls.exists()


def test_fails_when_claude_code_is_not_installed(
    tmp_path: Path, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    with pytest.raises(ResumeFailed, match="Claude Code"):
        resume_in_background(tmp_path / "returns")(
            Session(id=SESSION_ID, workdir=str(workdir)), "直してください"
        )
