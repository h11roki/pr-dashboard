from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from server import ROOT

START = ROOT / "bin" / "toriage-start"


def start(
    home: Path, *args: str, path: str | None = None, script: Path = START
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), *args],
        capture_output=True,
        text=True,
        env=os.environ | {"TORIAGE_HOME": str(home), "PATH": path or os.environ["PATH"]},
        check=False,
        timeout=30,
    )


def server_pid(home: Path) -> int:
    return int((home / "server.pid").read_text(encoding="utf-8"))


def test_starts_the_dashboard_once_however_often_it_is_called(dashboard_home: Path) -> None:
    first = start(dashboard_home)
    second = start(dashboard_home)

    assert (first.returncode, second.returncode) == (0, 0), first.stderr + second.stderr
    assert first.stdout == second.stdout == ""
    os.kill(server_pid(dashboard_home), 0)
    log = (dashboard_home / "server.log").read_text(encoding="utf-8")
    assert "Address already in use" not in log


def test_restart_replaces_the_running_dashboard(dashboard_home: Path) -> None:
    start(dashboard_home)
    before = server_pid(dashboard_home)

    result = start(dashboard_home, "--restart")

    assert result.returncode == 0, result.stderr
    assert server_pid(dashboard_home) != before


def test_restart_stops_the_dashboard_that_answers_not_the_pid_in_the_file(
    dashboard_home: Path,
) -> None:
    start(dashboard_home)
    before = server_pid(dashboard_home)
    with subprocess.Popen(["sleep", "30"]) as stranger:
        (dashboard_home / "server.pid").write_text(f"{stranger.pid}\n", encoding="utf-8")
        try:
            result = start(dashboard_home, "--restart")

            assert result.returncode == 0, result.stderr
            assert server_pid(dashboard_home) not in (before, stranger.pid)
            assert stranger.poll() is None
        finally:
            stranger.kill()


def test_restart_replaces_a_dashboard_started_from_another_copy(
    dashboard_home: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other-version"
    for directory in ("bin", "libexec", "static"):
        shutil.copytree(ROOT / directory, other / directory)
    shutil.copy2(ROOT / "server.py", other / "server.py")
    start(dashboard_home, script=other / "bin" / "toriage-start")
    before = server_pid(dashboard_home)

    result = start(dashboard_home, "--restart")

    assert result.returncode == 0, result.stderr
    assert server_pid(dashboard_home) != before


def test_says_where_to_look_when_the_dashboard_cannot_start(
    dashboard_home: Path, tmp_path: Path
) -> None:
    without_python = tmp_path / "bin"
    without_python.mkdir()
    python3 = without_python / "python3"
    python3.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    python3.chmod(0o755)

    result = start(dashboard_home, path=f"{without_python}:/usr/bin:/bin")

    assert result.returncode == 1
    assert str(dashboard_home / "server.log") in result.stderr
    log = (dashboard_home / "server.log").read_text(encoding="utf-8")
    assert "3.9 以上か uv が要る" in log
