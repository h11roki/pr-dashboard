import json
import subprocess
import sys

import pytest

from server import MAX_BODY_BYTES, ROOT, InvalidReport, parse_body, parse_report


def test_accepts_the_example_from_the_design(report: dict[str, object]) -> None:
    parsed = parse_report(report)

    assert parsed.changes.additions == 120
    assert parsed.tests.status == "passed"
    assert parsed.artifact_url == "https://claude.ai/artifact/example"


def test_artifact_url_may_be_left_out(report: dict[str, object]) -> None:
    del report["artifact_url"]

    assert parse_report(report).artifact_url is None


def test_rejects_a_body_that_is_not_an_object() -> None:
    with pytest.raises(InvalidReport, match="オブジェクト"):
        parse_report([])


def test_rejects_text_that_cannot_be_stored_as_utf8(report: dict[str, object]) -> None:
    report["title"] = "\ud800"

    with pytest.raises(InvalidReport, match="UTF-8"):
        parse_report(report)


def test_rejects_a_missing_field(report: dict[str, object]) -> None:
    del report["why"]

    with pytest.raises(InvalidReport, match="why"):
        parse_report(report)


@pytest.mark.parametrize(
    ("key", "value", "named"),
    [
        ("repo", "acme", "repo"),
        ("repo", "../..", "repo"),
        ("repo", "-R/web", "repo"),
        ("repo", "アクメ/ウェブ", "repo"),
        ("number", 0, "number"),
        ("number", 2**31, "number"),
        ("number", True, "number"),
        ("url", "javascript:alert(1)", "url"),
        ("url", "https://github.com/acme/api/pull/7", "url"),
        ("title", "  ", "title"),
        ("summary", [""], "summary"),
        ("artifact_url", "http://claude.ai/artifact/example", "artifact_url"),
        ("risk", "critical", "risk"),
        ("summary", [], "summary"),
        ("summary", ["1", "2", "3", "4"], "summary"),
        ("concerns", "ひとつだけ", "concerns"),
        ("suggested_reviewers", ["--repo=other/web"], "suggested_reviewers"),
        ("changes", {"files": 1, "additions": -1, "deletions": 0}, "additions"),
        ("changes", {"files": 1, "additions": 2**53 + 1, "deletions": 0}, "additions"),
        ("tests", {"status": "skipped", "detail": ""}, "status"),
    ],
)
def test_rejects_an_invalid_field(
    report: dict[str, object], key: str, value: object, named: str
) -> None:
    report[key] = value

    with pytest.raises(InvalidReport, match=named):
        parse_report(report)


@pytest.mark.parametrize(
    ("body", "named"),
    [
        (b" " * (MAX_BODY_BYTES + 1), "バイト"),
        (b"{", "JSON"),
        (b"\xff", "JSON"),
        (b"[" * 50_000, "JSON"),
    ],
)
def test_rejects_a_body_that_cannot_be_a_report(body: bytes, named: str) -> None:
    with pytest.raises(InvalidReport, match=named):
        parse_body(body)


def run_check(report: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(ROOT / "server.py"), "--check"],
        input=json.dumps(report),
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_check_command_accepts_a_valid_report(report: dict[str, object]) -> None:
    result = run_check(report)

    assert result.returncode == 0, result.stderr


def test_the_check_command_explains_an_invalid_report(report: dict[str, object]) -> None:
    result = run_check(report | {"risk": "critical"})

    assert result.returncode == 1
    assert "risk" in result.stderr
