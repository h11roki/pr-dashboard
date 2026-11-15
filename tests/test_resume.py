import json
import time
from pathlib import Path

import pytest

from server import ResumeFailed, Session, resume_in_background

SESSION_ID = "07805e44-cede-43f0-b5dd-4a9ae6541ad5"
FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
with open({calls!r}, "w") as calls:
    json.dump({{"args": sys.argv[1:], "cwd": os.getcwd()}}, calls)
print("直して送り直した")
"""


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.json"
    claude = bin_dir / "claude"
    claude.write_text(FAKE_CLAUDE.format(calls=str(calls)), encoding="utf-8")
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return calls


def wait_for(path: Path) -> str:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists() and path.read_text(encoding="utf-8"):
            return path.read_text(encoding="utf-8")
        time.sleep(0.05)
    raise AssertionError(f"{path} が書かれなかった")


def test_resumes_the_session_in_its_workdir_with_auto_mode(
    tmp_path: Path, fake_claude: Path
) -> None:
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    logs = tmp_path / "returns"

    resume_in_background(logs)(Session(id=SESSION_ID, workdir=str(workdir)), "直してください")

    call = json.loads(wait_for(fake_claude))
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
    assert Path(call["cwd"]).resolve() == workdir.resolve()
    log = logs / f"{SESSION_ID}.log"
    deadline = time.monotonic() + 10
    while "直して送り直した" not in log.read_text(encoding="utf-8"):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert "に差し戻した" in log.read_text(encoding="utf-8")


def test_fails_when_the_workdir_is_gone(tmp_path: Path, fake_claude: Path) -> None:
    session = Session(id=SESSION_ID, workdir=str(tmp_path / "removed-worktree"))

    with pytest.raises(ResumeFailed, match="removed-worktree"):
        resume_in_background(tmp_path / "returns")(session, "直してください")

    assert not fake_claude.exists()


def test_fails_when_claude_code_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    with pytest.raises(ResumeFailed, match="Claude Code"):
        resume_in_background(tmp_path / "returns")(
            Session(id=SESSION_ID, workdir=str(tmp_path)), "直してください"
        )
