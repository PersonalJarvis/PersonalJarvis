"""Behaviour of the CI orchestrator's scripts (lanes, gate, sharding, ratchet,
impact selection, conflict resolution, release cut)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.ci import (
    agent_integrate,
    classify_changes,
    cut_release,
    ratchet_tests,
    required_results,
    run_tests_parallel,
    select_tests,
)

# --------------------------------------------------------------------------- lanes


def test_empty_change_set_fails_open_to_every_lane():
    result = classify_changes.classify([])
    assert result["full"] is True
    assert all(result[lane] for lane in classify_changes.LANES)


def test_pipeline_change_runs_everything():
    result = classify_changes.classify(["scripts/ci/run_gates.py"])
    assert result["full"] and result["frontend"] and result["macos_desktop"]


def test_frontend_source_change_keeps_python_on_because_tests_read_it():
    result = classify_changes.classify(["jarvis/ui/web/frontend/src/views/tasks/taskSpec.ts"])
    assert result["frontend"] is True
    assert result["python"] is True
    assert result["realtime"] is False
    assert result["full"] is False


def test_unrelated_subproject_skips_python():
    result = classify_changes.classify(["wiki-video/src/scene.tsx", "homebrew-tap/Formula/x.rb"])
    assert result["python"] is False
    assert not any(result[lane] for lane in classify_changes.LANES)


def test_route_change_turns_on_cli_lane_and_windows_path_is_normalized():
    result = classify_changes.classify(["jarvis\\ui\\web\\update_routes.py"])
    assert result["cli"] is True
    assert result["python"] is True


def test_lockfile_change_reaches_deps_realtime_and_installer():
    result = classify_changes.classify(["requirements.txt"])
    assert result["deps"] and result["realtime"] and result["installer"]


# --------------------------------------------------------------------------- gate


def test_gate_passes_skips_but_fails_failures_and_cancellations():
    needs = {"a": {"result": "success"}, "b": {"result": "skipped"}}
    assert required_results.evaluate(needs)["ok"] is True
    needs["c"] = {"result": "cancelled"}
    verdict = required_results.evaluate(needs)
    assert verdict["ok"] is False and verdict["failed"] == ["c"]


def test_strict_gate_rejects_a_skip_outside_the_allow_list():
    needs = {"frontend": {"result": "skipped"}, "soak": {"result": "skipped"}}
    verdict = required_results.evaluate(needs, strict=True)
    assert verdict["failed"] == ["frontend"]


def test_gate_with_no_jobs_fails():
    assert required_results.evaluate({})["ok"] is False


# --------------------------------------------------------------------------- sharding


def test_shards_partition_the_suite_exactly_once():
    files = [f"tests/unit/m{i}/test_{i}.py" for i in range(40)]
    durations = {f: float(i % 7 + 1) for i, f in enumerate(files)}
    parts = [run_tests_parallel.shard(files, i, 4, durations) for i in range(1, 5)]
    flat = [f for part in parts for f in part]
    assert sorted(flat) == sorted(files)
    loads = [sum(run_tests_parallel.estimate(f, durations) for f in part) for part in parts]
    assert max(loads) - min(loads) <= 7  # LPT keeps shards within one file


def test_batches_stay_in_one_directory_and_isolate_slow_files():
    durations = {"tests/a/test_slow.py": 200.0}
    files = ["tests/a/test_1.py", "tests/a/test_slow.py", "tests/a/test_2.py", "tests/b/test_3.py"]
    batches = run_tests_parallel.make_batches(files, durations)
    assert ["tests/a/test_slow.py"] in batches
    assert all(len({f.rsplit("/", 1)[0] for f in batch}) == 1 for batch in batches)
    assert sorted(f for batch in batches for f in batch) == sorted(files)


def test_file_timeout_scales_with_history_but_is_bounded():
    assert run_tests_parallel.file_timeout("x", {}) == run_tests_parallel.MIN_FILE_TIMEOUT
    assert (
        run_tests_parallel.file_timeout("x", {"x": 10_000}) == run_tests_parallel.MAX_FILE_TIMEOUT
    )


def test_runner_reports_failures_and_passes(tmp_path):
    repo = run_tests_parallel.REPO_ROOT
    rel = Path(f"tests/unit/ci/_runner_probe_{tmp_path.name}")
    probe = repo / rel
    probe.mkdir()
    try:
        (probe / "test_probe_ok.py").write_text("def test_ok():\n    assert True\n")
        (probe / "test_probe_bad.py").write_text("def test_bad():\n    assert False\n")
        code = run_tests_parallel.main(
            [
                str(rel),
                "--workers",
                "2",
                "--markers",
                "",
                "--durations",
                str(tmp_path / "none.json"),
                "--report",
                str(tmp_path / "r.json"),
                "--junit-out",
                str(tmp_path / "r.xml"),
            ]
        )
    finally:
        shutil.rmtree(probe, ignore_errors=True)
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert code == 1
    assert report["counts"]["passed"] == 1
    assert any(fid.endswith("::test_bad") for fid in report["failed_ids"])


# --------------------------------------------------------------------------- ratchet


def _report(path: Path, failed: list[str], passed: int = 10) -> Path:
    path.write_text(
        json.dumps({"failed_ids": failed, "counts": {"passed": passed}, "elapsed_seconds": 1}),
        encoding="utf-8",
    )
    return path


def test_ratchet_blocks_only_new_failures(tmp_path):
    baseline = tmp_path / "b.json"
    baseline.write_text(json.dumps({"known_failures": ["t.a::x"]}), encoding="utf-8")
    known = _report(tmp_path / "r1.json", ["t.a::x"])
    new = _report(tmp_path / "r2.json", ["t.a::x", "t.b::y"])
    assert ratchet_tests.main(["check", "--baseline", str(baseline), str(known)]) == 0
    assert ratchet_tests.main(["check", "--baseline", str(baseline), str(new)]) == 1


def test_ratchet_matches_timeouts_regardless_of_budget(tmp_path):
    baseline = tmp_path / "b.json"
    baseline.write_text(
        json.dumps({"known_failures": ["tests/x.py::<timeout 300s>"]}), encoding="utf-8"
    )
    report = _report(tmp_path / "r.json", ["tests/x.py::<timeout 900s>"])
    assert ratchet_tests.main(["check", "--baseline", str(baseline), str(report)]) == 0


def test_missing_baseline_is_report_only(tmp_path):
    report = _report(tmp_path / "r.json", ["t::new"])
    assert ratchet_tests.main(["check", "--baseline", str(tmp_path / "no.json"), str(report)]) == 0


def test_update_writes_a_baseline_the_check_accepts(tmp_path):
    report = _report(tmp_path / "test-report-1.json", ["t::a", "t::b"])
    out = tmp_path / "base.json"
    assert ratchet_tests.main(["update", "--out", str(out), str(report)]) == 0
    assert ratchet_tests.main(["check", "--baseline", str(out), str(report)]) == 0


# --------------------------------------------------------------------------- selection


def test_selection_maps_a_module_to_the_tests_that_import_it():
    others = [f"tests/unit/b/test_other{i}.py" for i in range(9)]
    tests = ["tests/unit/a/test_widget.py", *others]
    texts = dict.fromkeys(others, "import json")
    texts["tests/unit/a/test_widget.py"] = "from jarvis.core.widget import Widget"
    mode, chosen = select_tests.select(["jarvis/core/widget.py"], tests, texts)
    assert mode == "selected"
    assert chosen == ["tests/unit/a/test_widget.py"]


def test_selection_falls_back_to_everything_for_shared_fixtures():
    tests = ["tests/unit/a/test_x.py"]
    mode, chosen = select_tests.select(["tests/conftest.py"], tests, {tests[0]: ""})
    assert mode == "all" and chosen == tests


# --------------------------------------------------------------------------- integration


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "core.autocrlf", "false")
    (root / "CHANGELOG.md").write_text("# Changelog\n\n- base\n", encoding="utf-8")
    (root / "test_durations.json").write_text('{"a": 1}\n', encoding="utf-8")
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    return root


def _diverge(repo: Path, main_edit: dict[str, str], branch_edit: dict[str, str]) -> None:
    _git(repo, "checkout", "-q", "-b", "feature")
    for name, text in branch_edit.items():
        (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "feature work")
    _git(repo, "checkout", "-q", "main")
    for name, text in main_edit.items():
        (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "main work")
    _git(repo, "checkout", "-q", "feature")


@pytest.mark.parametrize("mode", ["merge", "rebase"])
def test_generated_and_append_only_conflicts_resolve_themselves(repo, mode):
    _diverge(
        repo,
        {
            "CHANGELOG.md": "# Changelog\n\n- main entry\n- base\n",
            "test_durations.json": '{"a": 2}\n',
        },
        {
            "CHANGELOG.md": "# Changelog\n\n- branch entry\n- base\n",
            "test_durations.json": '{"a": 3}\n',
        },
    )
    report = agent_integrate.update("main", mode, "", repo)
    assert report["status"] == "updated", report
    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "main entry" in changelog and "branch entry" in changelog
    assert json.loads((repo / "test_durations.json").read_text(encoding="utf-8")) == {"a": 2}
    assert _git(repo, "status", "--porcelain").strip() == ""


@pytest.mark.parametrize("mode", ["merge", "rebase"])
def test_semantic_conflict_aborts_cleanly_without_a_resolver(repo, mode):
    _diverge(repo, {"app.py": "VALUE = 2\n"}, {"app.py": "VALUE = 3\n"})
    head = _git(repo, "rev-parse", "HEAD")
    report = agent_integrate.update("main", mode, "", repo)
    assert report["status"] == "conflict"
    assert report["unresolved"] == ["app.py"]
    assert _git(repo, "rev-parse", "HEAD") == head
    assert (repo / "app.py").read_text(encoding="utf-8") == "VALUE = 3\n"


def test_up_to_date_branch_is_left_alone(repo):
    assert agent_integrate.update("main", "merge", "", repo) == {"status": "up-to-date"}


def test_train_eligibility():
    base = {"baseRefName": "main", "isDraft": False, "labels": [], "isCrossRepository": False}
    assert agent_integrate.eligible({**base, "headRefName": "codex/fix"})
    assert not agent_integrate.eligible({**base, "headRefName": "feature/x"})
    assert agent_integrate.eligible(
        {**base, "headRefName": "feature/x", "labels": [{"name": "auto-merge"}]}
    )
    assert not agent_integrate.eligible(
        {**base, "headRefName": "codex/x", "labels": [{"name": "needs-human"}]}
    )
    assert not agent_integrate.eligible(
        {**base, "headRefName": "codex/x", "isCrossRepository": True}
    )
    assert not agent_integrate.eligible({**base, "headRefName": "dependabot/pip/x"})


def _run(event, status, conclusion, created, run_id=1):
    return {
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "created_at": created,
        "id": run_id,
    }


def test_train_reads_a_running_ci_as_pending():
    runs = [_run("pull_request", "in_progress", None, "2")]
    assert agent_integrate.run_state(runs) == ("pending", None)


def test_train_ignores_dispatch_runs_because_the_pr_never_sees_them():
    runs = [_run("workflow_dispatch", "completed", "success", "3")]
    assert agent_integrate.run_state(runs) == ("missing", None)


def test_train_approves_a_parked_bot_run_and_reruns_a_cancelled_one():
    parked = _run("pull_request", "completed", "action_required", "4", 7)
    assert agent_integrate.run_state([parked]) == ("approve", 7)
    cancelled = _run("pull_request", "completed", "cancelled", "5", 8)
    assert agent_integrate.run_state([parked, cancelled]) == ("rerun", 8)


def test_train_uses_the_newest_pull_request_verdict():
    old = _run("pull_request", "completed", "failure", "1", 1)
    new = _run("pull_request", "completed", "success", "2", 2)
    assert agent_integrate.run_state([new, old]) == ("success", 2)
    assert agent_integrate.run_state([old]) == ("failure", 1)


def test_train_updates_only_conflicting_or_stale_red_branches():
    decide = agent_integrate.decide
    assert decide("CONFLICTING", "success", True) == "update"
    assert decide("MERGEABLE", "success", True) == "merge"  # behind main is fine
    assert decide("MERGEABLE", "pending", True) == "wait"
    assert decide("MERGEABLE", "approve", False) == "approve"
    assert decide("MERGEABLE", "rerun", True) == "rerun"
    assert decide("MERGEABLE", "missing", False) == "wait"
    assert decide("MERGEABLE", "failure", True) == "update"
    assert decide("MERGEABLE", "failure", False) == "wait"
    assert decide("UNKNOWN", "success", False) == "wait"
    assert decide("UNKNOWN", "approve", False) == "approve"


# --------------------------------------------------------------------------- release


def test_bump_and_commit_notes():
    assert cut_release.bump("2.3.2", "patch") == "2.3.3"
    assert cut_release.bump("2.3.2", "minor") == "2.4.0"
    assert cut_release.bump("2.3.2", "major") == "3.0.0"
    notes = cut_release.notes_from_commits(
        ["feat(ide): drag folders", "fix: stale backend", "chore: tidy", "feat!: new config"]
    )
    assert "### Added\n\n- Drag folders" in notes
    assert "### Fixed\n\n- Stale backend" in notes
    assert "### Breaking" in notes and "Tidy" not in notes


def test_apply_moves_notes_under_a_dated_section(tmp_path):
    (tmp_path / "jarvis").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n', encoding="utf-8")
    (tmp_path / "jarvis" / "__init__.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n---\n\n## [1.0.0] — 2026-01-01\n\n- old\n",
        encoding="utf-8",
    )
    cut_release.apply("1.1.0", "### Added\n\n- thing", "2026-09-28", tmp_path)
    text = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.index("## [Unreleased]") < text.index("## [1.1.0] — 2026-09-28")
    assert text.index("## [1.1.0]") < text.index("## [1.0.0]")
    assert cut_release.section_notes(text, "1.1.0") == "### Added\n\n- thing"
    from scripts.ci import release_admit

    assert release_admit.versions(tmp_path) == ("1.1.0", "1.1.0")
    assert release_admit.check_identity("v1.1.0", tmp_path) == []
    assert release_admit.check_identity("v1.2.0", tmp_path)


def test_any_failure_inside_a_flaky_file_is_known(tmp_path):
    baseline = tmp_path / "b.json"
    baseline.write_text(
        json.dumps({"flaky_files": ["tests/unit/x/test_timing.py"], "known_failures": []}),
        encoding="utf-8",
    )
    flip = _report(tmp_path / "r1.json", ["tests.unit.x.test_timing::test_ramp"])
    crash = _report(tmp_path / "r2.json", ["tests/unit/x/test_timing.py::<timeout 300s>"])
    other = _report(tmp_path / "r3.json", ["tests.unit.x.test_timing_other::test_a"])
    assert ratchet_tests.main(["check", "--baseline", str(baseline), str(flip)]) == 0
    assert ratchet_tests.main(["check", "--baseline", str(baseline), str(crash)]) == 0
    assert ratchet_tests.main(["check", "--baseline", str(baseline), str(other)]) == 1
    out = tmp_path / "b.json"
    assert ratchet_tests.main(["update", "--out", str(out), str(other)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["flaky_files"] == [
        "tests/unit/x/test_timing.py"
    ]
