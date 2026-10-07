"""Flaky-test aggregation: step summary, run vetting and the rolling tracking issue."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci import flaky_report

REPO = "PersonalJarvis/PersonalJarvis"
RUN_URL = "https://github.com/PersonalJarvis/PersonalJarvis/actions/runs/{}"


def _report(tmp: Path, name: str, platform: str, *, flaky=(), failed=(), passed=10) -> Path:
    path = tmp / name
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "shard": "1/1",
        "platform": platform,
        "counts": {"tests": passed + len(failed), "passed": passed, "failed": len(failed)},
        "failed_ids": list(failed),
        "flaky_files": list(flaky),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _baseline(tmp: Path, os_name: str, known: list[str]) -> None:
    payload = {"known_failures": known}
    (tmp / f"test-baseline-{os_name}.json").write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------------------- collect


def test_collect_groups_shards_by_os_and_normalizes_crashes(tmp_path):
    _report(tmp_path, "tests-linux-1/test-report-linux-1.json", "linux", flaky=["tests/a.py"])
    _report(
        tmp_path,
        "tests-linux-2/test-report-linux-2.json",
        "linux",
        failed=["tests/b.py::<timeout 300s>"],
    )
    _report(tmp_path, "test-report-windows-1.json", "win32", flaky=["tests/a.py"])
    results = flaky_report.collect([tmp_path])
    assert set(results) == {"linux", "windows"}
    assert results["linux"].shards == 2
    assert results["linux"].flaky == {"tests/a.py"}
    assert results["linux"].failures == {"tests/b.py::<crash>"}
    assert results["windows"].flaky == {"tests/a.py"}


def test_os_falls_back_to_the_report_file_name():
    assert flaky_report.os_of({}, Path("test-report-macos-3.json")) == "macos"
    assert flaky_report.os_of({"platform": "darwin"}) == "macos"
    assert flaky_report.os_of({}) == "unknown"


def test_unreadable_report_is_skipped_not_fatal(tmp_path, capsys):
    (tmp_path / "test-report-linux-1.json").write_text("{not json", encoding="utf-8")
    _report(tmp_path, "test-report-linux-2.json", "linux", flaky=["tests/a.py"])
    results = flaky_report.collect([tmp_path, tmp_path / "missing"])
    assert results["linux"].shards == 1
    assert "unreadable test report" in capsys.readouterr().out


# --------------------------------------------------------------------------- summary


def test_summary_splits_new_from_known_per_os_baseline(tmp_path):
    _baseline(tmp_path, "linux", ["tests/k.py::test_known", "tests/c.py::<timeout 1s>"])
    _report(
        tmp_path,
        "r/test-report-linux-1.json",
        "linux",
        flaky=["tests/order.py"],
        failed=["tests/k.py::test_known", "tests/n.py::test_new", "tests/c.py::<timeout 9s>"],
    )
    results = flaky_report.collect([tmp_path / "r"])
    new, known = flaky_report.split_failures(
        results["linux"].failures,
        flaky_report.load_baseline(tmp_path / "test-baseline-linux.json"),
    )
    assert new == ["tests/n.py::test_new"]
    assert known == ["tests/c.py::<crash>", "tests/k.py::test_known"]
    text = "\n".join(flaky_report.summary_lines(results, tmp_path))
    assert "1 flaky file(s), 1 new failure(s), 2 known failure(s)" in text
    assert "`tests/order.py`" in text
    assert "`tests/n.py::test_new`" in text


def test_summary_without_reports_says_so(tmp_path):
    lines = flaky_report.summary_lines({}, tmp_path)
    assert "No test reports" in lines[-1]


def test_summary_command_writes_the_step_summary(tmp_path, monkeypatch):
    target = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
    _report(tmp_path, "r/test-report-linux-1.json", "linux", flaky=["tests/x.py"])
    args = ["summary", "--baseline-dir", str(tmp_path), str(tmp_path / "r")]
    assert flaky_report.main(args) == 0
    assert "`tests/x.py`" in target.read_text(encoding="utf-8")


def test_long_lists_are_capped():
    lines = flaky_report._bullets([f"t{i}" for i in range(flaky_report.MAX_LISTED + 5)])
    assert len(lines) == flaky_report.MAX_LISTED + 1
    assert lines[-1] == "- ... and 5 more"


# --------------------------------------------------------------------------- run vetting


def _run(**overrides) -> dict:
    run = {
        "id": 42,
        "html_url": RUN_URL.format(42),
        "run_started_at": "2026-10-07T02:17:00Z",
        "event": "schedule",
        "status": "completed",
        "head_branch": "main",
        "head_repository": {"full_name": REPO},
    }
    run.update(overrides)
    return run


def test_vet_run_accepts_a_finished_main_run():
    values, reason = flaky_report.vet_run(_run(), REPO)
    assert reason == ""
    assert values["id"] == "42"
    assert values["at"] == "2026-10-07T02:17:00Z"


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"event": "pull_request"}, "triggered by"),
        ({"head_repository": {"full_name": "fork/PersonalJarvis"}}, "belongs to"),
        ({"head_branch": "feature"}, "not main"),
        ({"status": "in_progress"}, "not completed"),
        ({"id": "1; rm -rf /"}, "numeric id"),
    ],
)
def test_vet_run_refuses_anything_but_trusted_main_runs(overrides, why):
    values, reason = flaky_report.vet_run(_run(**overrides), REPO)
    assert values == {}
    assert why in reason


# --------------------------------------------------------------------------- tracking issue


def _results(**flaky_by_os) -> dict[str, flaky_report.OsResult]:
    return {
        os_name: flaky_report.OsResult(flaky=set(files)) for os_name, files in flaky_by_os.items()
    }


def test_merge_counts_runs_per_file_and_os_and_is_idempotent():
    state = {"entries": {}, "runs": []}
    state = flaky_report.merge_run(
        state,
        _results(linux=["tests/a.py"], windows=["tests/a.py"]),
        run_id="1",
        run_url=RUN_URL.format(1),
        run_at="2026-10-01T02:00:00Z",
    )
    again = flaky_report.merge_run(
        state,
        _results(linux=["tests/a.py"]),
        run_id="1",
        run_url=RUN_URL.format(1),
        run_at="2026-10-01T02:00:00Z",
    )
    assert again["entries"]["linux|tests/a.py"]["count"] == 1
    later = flaky_report.merge_run(
        again,
        _results(linux=["tests/a.py"]),
        run_id="2",
        run_url=RUN_URL.format(2),
        run_at="2026-10-02T02:00:00Z",
    )
    entry = later["entries"]["linux|tests/a.py"]
    assert entry["count"] == 2
    assert entry["first_seen"] == "2026-10-01T02:00:00Z"
    assert entry["last_seen"] == "2026-10-02T02:00:00Z"
    assert entry["last_run_url"] == RUN_URL.format(2)
    assert later["entries"]["windows|tests/a.py"]["count"] == 1
    assert later["runs"] == ["1", "2"]


def test_rows_not_seen_within_the_window_age_out():
    state = flaky_report.merge_run(
        {"entries": {}, "runs": []},
        _results(linux=["tests/old.py"]),
        run_id="1",
        run_url=RUN_URL.format(1),
        run_at="2026-08-01T00:00:00Z",
    )
    state = flaky_report.merge_run(
        state,
        _results(linux=["tests/new.py"]),
        run_id="2",
        run_url=RUN_URL.format(2),
        run_at="2026-10-01T00:00:00Z",
        keep_days=30,
    )
    assert set(state["entries"]) == {"linux|tests/new.py"}


def test_remembered_runs_are_bounded():
    state = {"entries": {}, "runs": [str(i) for i in range(flaky_report.MAX_RUNS_REMEMBERED)]}
    state = flaky_report.merge_run(
        state, {}, run_id="new", run_url="", run_at="2026-10-01T00:00:00Z"
    )
    assert len(state["runs"]) == flaky_report.MAX_RUNS_REMEMBERED
    assert state["runs"][-1] == "new"


def test_rendered_body_round_trips_its_state_and_sorts_by_count():
    state = flaky_report.merge_run(
        {"entries": {}, "runs": []},
        _results(linux=["tests/once.py", "tests/twice.py"]),
        run_id="1",
        run_url=RUN_URL.format(1),
        run_at="2026-10-01T00:00:00Z",
    )
    state = flaky_report.merge_run(
        state,
        _results(linux=["tests/twice.py", "tests/a--b|c.py"]),
        run_id="2",
        run_url=RUN_URL.format(2),
        run_at="2026-10-02T00:00:00Z",
    )
    body = flaky_report.render_issue(state)
    table = [line for line in body.splitlines() if line.startswith("| `")]
    assert table[0].startswith("| `tests/twice.py` | linux | 2 |")
    assert "`tests/a--b\\|c.py`" in body
    comment = body[body.index("<!--") + 4 :]
    assert "--" not in comment[: comment.index("-->")]
    parsed = flaky_report.parse_state(body)
    assert parsed["entries"] == state["entries"]
    assert parsed["runs"] == ["1", "2"]


def test_empty_state_renders_a_quiet_body():
    body = flaky_report.render_issue({"entries": {}, "runs": []})
    assert "No flaky test files" in body
    assert "| Test file |" not in body


@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "edited by hand",
        "<!-- flaky-state: {broken -->",
        '<!-- flaky-state: {"entries": [], "runs": "x"} -->',
        '<!-- flaky-state: {"entries": {"k": {"file": "a", "os": "linux", "count": "x"}}} -->',
    ],
)
def test_missing_or_mangled_state_starts_fresh(body):
    assert flaky_report.parse_state(body) == {"entries": {}, "runs": []}


def test_pick_issue_prefers_the_open_exact_title():
    issues = [
        {"number": 5, "title": "Flaky tests on main (old)", "state": "OPEN"},
        {"number": 7, "title": flaky_report.ISSUE_TITLE, "state": "CLOSED"},
        {"number": 9, "title": flaky_report.ISSUE_TITLE, "state": "OPEN"},
    ]
    assert flaky_report.pick_issue(issues)["number"] == 9
    assert flaky_report.pick_issue(issues[:2])["number"] == 7
    assert flaky_report.pick_issue([]) is None


@pytest.mark.parametrize(
    ("issue", "has_rows", "expected"),
    [
        (None, True, "create"),
        (None, False, "none"),
        ({"number": 1, "state": "CLOSED", "body": ""}, True, "reopen"),
        ({"number": 1, "state": "CLOSED", "body": ""}, False, "none"),
        ({"number": 1, "state": "OPEN", "body": "old"}, False, "edit"),
        ({"number": 1, "state": "OPEN", "body": "same"}, True, "none"),
    ],
)
def test_plan_issue(issue, has_rows, expected):
    assert flaky_report.plan_issue(issue, "same", has_rows) == expected


def test_issue_command_end_to_end(tmp_path, monkeypatch):
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    _report(tmp_path, "r/test-report-linux-1.json", "linux", flaky=["tests/a.py"])
    previous = flaky_report.render_issue(
        flaky_report.merge_run(
            {"entries": {}, "runs": []},
            _results(linux=["tests/a.py"]),
            run_id="1",
            run_url=RUN_URL.format(1),
            run_at="2026-10-06T02:00:00Z",
        )
    )
    issues = tmp_path / "issues.json"
    issues.write_text(
        json.dumps(
            [{"number": 12, "title": flaky_report.ISSUE_TITLE, "state": "OPEN", "body": previous}]
        ),
        encoding="utf-8",
    )
    body = tmp_path / "out" / "body.md"
    args = [
        "issue",
        "--issues",
        str(issues),
        "--out",
        str(body),
        "--run-id",
        "2",
        "--run-url",
        RUN_URL.format(2),
        "--run-at",
        "2026-10-07T02:00:00Z",
        str(tmp_path / "r"),
    ]
    assert flaky_report.main(args) == 0
    assert "action=edit\nnumber=12\nrows=1\n" == output.read_text(encoding="utf-8")
    assert "| `tests/a.py` | linux | 2 |" in body.read_text(encoding="utf-8")


def test_issue_command_rejects_a_non_numeric_run_id(tmp_path):
    args = ["issue", "--issues", str(tmp_path / "none.json"), "--out", str(tmp_path / "b.md")]
    args += ["--run-id", "x", "--run-url", "u", "--run-at", "2026-10-07T00:00:00Z"]
    assert flaky_report.main(args) == 1
