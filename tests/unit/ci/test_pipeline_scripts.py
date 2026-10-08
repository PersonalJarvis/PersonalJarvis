"""Behaviour of the CI orchestrator's scripts (lanes, gate, sharding, ratchet,
impact selection, conflict resolution, release cut)."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import threading
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
    result = classify_changes.classify(["jarvis/ui/web/frontend/src/lib/tasksApi.ts"])
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


def test_updater_change_turns_on_the_cross_os_updater_lane():
    for path in (
        "jarvis/core/installer_update.py",
        "jarvis\\ui\\relauncher.py",
        "packaging/windows/PersonalJarvis.iss",
        "tests/unit/ui/web/test_update_routes_frozen.py",
    ):
        assert classify_changes.classify([path])["updater"] is True, path


def test_unrelated_python_change_leaves_the_updater_lane_off():
    result = classify_changes.classify(["jarvis/society/roster.py"])
    assert result["python"] is True
    assert result["updater"] is False


@pytest.mark.parametrize(
    "path",
    [
        # Packaging: the spec, the build script, the entitlements and the single
        # usage-string table both macOS bundles load.
        "jarvis.spec",
        "packaging/macos/build.sh",
        "packaging/macos/entitlements.plist",
        "packaging/macos/README.md",
        "jarvis/core/macos_privacy_strings.py",
        # The just-in-time permission service, its port and its HTTP surface.
        "jarvis/platform/permission_service.py",
        "jarvis/platform/permissions.py",
        "jarvis/ui/web/permissions_routes.py",
        # Every directory whose code asks for, or acts on, a macOS permission.
        "jarvis/audio/capture.py",
        "jarvis/cu/engine.py",
        "jarvis/vision/screenshot.py",
        "jarvis/screen_context/capture.py",
        "jarvis/dictation/insert.py",
        "jarvis/trigger/backends/quartz.py",
        "jarvis/platform/window_state.py",
        # The voice gates, the wake/mic routes, the shared events and protocols, and
        # the fakes and contract tests the macOS lane runs.
        "jarvis/speech/pipeline.py",
        "jarvis/speech/diagnose.py",
        "jarvis/ui/web/settings_routes.py",
        "jarvis/core/events.py",
        # Consumers that capture, type or relay: tools, appshot, routes, CLI, bundle ids.
        "jarvis/plugins/tool/screen_snapshot.py",
        "jarvis/plugins/tool/type_text.py",
        "jarvis/plugins/tool/verify_localhost.py",
        "jarvis/plugins/harness/computer_use.py",
        "jarvis/appshot/gesture.py",
        "jarvis/ui/web/screen_context_routes.py",
        "jarvis/cli_ctl/commands/permissions.py",
        "jarvis/core/branding.py",
        "jarvis/tasks/event_catalog.py",
        "jarvis/core/protocols.py",
        "tests/fakes/fake_tcc.py",
        "tests/fakes/fake_permission_service.py",
        "tests/contract/test_permission_service_contract.py",
        "tests/unit/core/test_permission_events.py",
        "tests/unit/ci/test_macos_desktop_permission_step.py",
        "tests/unit/ui/web/test_permissions_routes.py",
        "tests/unit/trigger/test_quartz_backend.py",
        # The windows spelling of a path must classify the same way.
        "jarvis\\core\\macos_privacy_strings.py",
    ],
)
def test_macos_permission_and_packaging_paths_turn_on_the_macos_lane(path):
    result = classify_changes.classify([path])

    assert result["macos_desktop"] is True, path
    assert result["full"] is False, path


@pytest.mark.parametrize(
    "path",
    [
        "jarvis/society/roster.py",
        "jarvis/ui/web/society_browser_routes.py",
        "packaging/windows/PersonalJarvis.iss",
        "packaging/linux/build.sh",
        "docs/macos-permissions.md",
    ],
)
def test_unrelated_paths_leave_the_macos_lane_off(path):
    assert classify_changes.classify([path])["macos_desktop"] is False, path


def test_the_macos_lane_gate_names_the_new_paths_it_adds_to_the_prefix_tables():
    """Guard the data, not just the outcome: a refactor must not drop an entry."""
    assert {"jarvis.spec", "jarvis/core/macos_privacy_strings.py"} <= classify_changes._MACOS_FILES
    assert "jarvis/ui/web/permissions_routes.py" in classify_changes._MACOS_FILES
    assert {
        "jarvis/plugins/tool/screen_snapshot.py",
        "jarvis/plugins/tool/type_text.py",
        "jarvis/plugins/tool/verify_localhost.py",
        "jarvis/speech/diagnose.py",
        "jarvis/ui/web/screen_context_routes.py",
        "jarvis/cli_ctl/commands/permissions.py",
        "jarvis/core/branding.py",
        "jarvis/tasks/event_catalog.py",
    } <= classify_changes._MACOS_FILES
    assert {
        "packaging/macos/",
        "jarvis/screen_context/",
        "jarvis/dictation/",
        "jarvis/appshot/",
    } <= set(
        classify_changes._MACOS_PREFIXES
    )


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


def test_memory_probe_reads_this_machine():
    free = run_tests_parallel.available_memory()
    assert free is None or free > 0


def test_memory_gate_never_holds_back_the_only_batch():
    gate = run_tests_parallel.MemoryGate(reserve=10, probe=lambda: 0, poll_seconds=0.01)
    gate.acquire()
    assert gate.running == 1
    assert gate.waits == 0


def test_memory_gate_holds_a_second_batch_until_memory_frees():
    free = {"bytes": 0}
    gate = run_tests_parallel.MemoryGate(reserve=10, probe=lambda: free["bytes"], poll_seconds=0.01)
    gate.acquire()
    entered = threading.Event()

    def second() -> None:
        gate.acquire()
        entered.set()

    worker = threading.Thread(target=second, daemon=True)
    worker.start()
    assert not entered.wait(0.2)
    assert gate.waits == 1
    free["bytes"] = 100
    assert entered.wait(2)
    worker.join(2)
    assert gate.running == 2


def test_memory_gate_lets_a_waiting_batch_in_when_the_others_finish():
    gate = run_tests_parallel.MemoryGate(reserve=10, probe=lambda: 0, poll_seconds=0.01)
    gate.acquire()
    entered = threading.Event()
    worker = threading.Thread(target=lambda: (gate.acquire(), entered.set()), daemon=True)
    worker.start()
    assert not entered.wait(0.2)
    gate.release()
    assert entered.wait(2)
    worker.join(2)


def test_memory_gate_is_open_where_memory_cannot_be_read():
    gate = run_tests_parallel.MemoryGate(reserve=10, probe=lambda: None)
    gate.acquire()
    gate.acquire()
    assert gate.running == 2
    assert gate.waits == 0


# --------------------------------------------------------------------------- ratchet


def _report(path: Path, failed: list[str], passed: int = 10) -> Path:
    path.write_text(
        json.dumps(
            {
                "failed_ids": failed,
                "files": 1,
                "counts": {"passed": passed, "tests": passed + len(failed)},
                "elapsed_seconds": 1,
            }
        ),
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


def test_missing_baseline_rejects_unapproved_failures(tmp_path):
    report = _report(tmp_path / "r.json", ["t::new"])
    assert ratchet_tests.main(["check", "--baseline", str(tmp_path / "no.json"), str(report)]) == 1


def test_update_writes_a_baseline_the_check_accepts(tmp_path):
    report = _report(tmp_path / "test-report-1.json", ["t::a", "t::b"])
    out = tmp_path / "base.json"
    assert ratchet_tests.main(["update", "--out", str(out), str(report)]) == 0
    assert ratchet_tests.main(["check", "--baseline", str(out), str(report)]) == 0


def _junit(run_dir: Path, cases: dict[str, str]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    body = {"passed": "", "failed": "<failure/>", "skipped": "<skipped/>"}
    xml = ""
    for fid, outcome in cases.items():
        module, name = fid.split("::")
        xml += f'<testcase classname="{module}" name="{name}">{body[outcome]}</testcase>'
    (run_dir / "junit-linux-1.xml").write_text(f"<testsuite>{xml}</testsuite>", encoding="utf-8")
    return run_dir


def test_prune_drops_only_entries_that_passed_in_every_run(tmp_path):
    baseline = tmp_path / "b.json"
    known = ["t.a::stable", "t.a::flips", "t.a::skipped", "t.a::still_fails", "t.a::gone"]
    baseline.write_text(json.dumps({"known_failures": known}), encoding="utf-8")
    first = {"stable": "passed", "flips": "passed", "skipped": "skipped", "still_fails": "failed"}
    second = {"stable": "passed", "flips": "failed", "skipped": "passed", "still_fails": "failed"}
    run1 = _junit(tmp_path / "r1", {f"t.a::{k}": v for k, v in first.items()})
    run2 = _junit(tmp_path / "r2", {f"t.a::{k}": v for k, v in second.items()})
    args = ["prune", "--baseline", str(baseline), "--os", "linux", str(run1), str(run2)]
    assert ratchet_tests.main(args) == 0
    remaining = json.loads(baseline.read_text(encoding="utf-8"))["known_failures"]
    assert remaining == ["t.a::flips", "t.a::skipped", "t.a::still_fails", "t.a::gone"]


def test_prune_refuses_a_run_without_evidence(tmp_path):
    baseline = tmp_path / "b.json"
    baseline.write_text(json.dumps({"known_failures": ["t.a::x"]}), encoding="utf-8")
    (tmp_path / "empty").mkdir()
    args = ["prune", "--baseline", str(baseline), "--os", "linux", str(tmp_path / "empty")]
    assert ratchet_tests.main(args) == 1
    assert json.loads(baseline.read_text(encoding="utf-8"))["known_failures"] == ["t.a::x"]


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


def test_train_leaves_a_queued_pull_request_to_the_queue():
    decide = agent_integrate.decide
    assert decide("MERGEABLE", "success", True, queued=True) == "queued"
    assert decide("CONFLICTING", "failure", True, queued=True) == "queued"


def test_train_never_re_enqueues_a_head_the_queue_already_failed():
    decide = agent_integrate.decide
    # Behind main: a newer main may be the fix, so update and re-test.
    assert decide("MERGEABLE", "success", True, queue_failed=True) == "update"
    # Already contains main: re-adding it unchanged would fail the same way.
    assert decide("MERGEABLE", "success", False, queue_failed=True) == "held"
    assert decide("MERGEABLE", "success", False, queue_failed=False) == "merge"


def _queue_run(number, conclusion, created, status="completed"):
    return {
        "event": "merge_group",
        "head_branch": f"gh-readonly-queue/main/pr-{number}-{'b' * 40}",
        "status": status,
        "conclusion": conclusion,
        "created_at": created,
    }


def test_queue_runs_are_mapped_to_their_pull_request():
    runs = [
        _queue_run(7, "failure", "2026-10-07T10:00:00Z"),
        _queue_run(7, "success", "2026-10-07T11:00:00Z"),
        _queue_run(9, "failure", "2026-10-07T09:00:00Z"),
        {"event": "push", "head_branch": "main", "created_at": "2026-10-07T12:00:00Z"},
        {"event": "merge_group", "head_branch": "gh-readonly-queue/main/odd"},
    ]
    newest = agent_integrate.queue_runs_by_pr(runs)
    assert set(newest) == {7, 9}
    assert newest[7]["conclusion"] == "success"


def test_queue_failure_holds_only_until_a_newer_green_pull_request_run():
    green = _run("pull_request", "completed", "success", "2026-10-07T10:00:00Z")
    failed = _queue_run(5, "failure", "2026-10-07T10:30:00Z")
    assert agent_integrate.queue_rejected(failed, [green])
    newer = _run("pull_request", "completed", "success", "2026-10-07T11:00:00Z")
    assert not agent_integrate.queue_rejected(failed, [green, newer])
    # A cancelled or still-running queue run is not a verdict.
    cancelled = _queue_run(5, "cancelled", "2026-10-07T10:30:00Z")
    assert not agent_integrate.queue_rejected(cancelled, [green])
    running = _queue_run(5, None, "2026-10-07T10:30:00Z", status="in_progress")
    assert not agent_integrate.queue_rejected(running, [green])
    assert not agent_integrate.queue_rejected(None, [green])


class _FakeGh:
    """Records gh calls instead of reaching GitHub."""

    def __init__(self):
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> int:
        self.calls.append(args)
        return 0


def _origin_with_feature(tmp_path, repo, main_edit, branch_edit):
    _diverge(repo, main_edit, branch_edit)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(repo), str(origin))
    machines = []
    for name in ("integrate", "apply"):
        clone = tmp_path / name
        _git(tmp_path, "clone", "-q", str(origin), str(clone))
        _git(clone, "config", "user.email", "t@example.invalid")
        _git(clone, "config", "user.name", "t")
        _git(clone, "config", "core.autocrlf", "false")
        machines.append(clone)
    plan = {
        "repo": "example/project",
        "main": _git(origin, "rev-parse", "main").strip(),
        "queue": True,
        "entries": [
            {
                "number": 3,
                "branch": "feature",
                "head": _git(origin, "rev-parse", "feature").strip(),
                "id": "PR_node",
                "action": "update",
                "run_id": None,
                "note": "",
            }
        ],
    }
    return origin, machines[0], machines[1], plan


def test_train_phases_update_a_branch_across_separate_machines(tmp_path, repo, monkeypatch):
    origin, integrate, applier, plan = _origin_with_feature(
        tmp_path,
        repo,
        {"CHANGELOG.md": "# Changelog\n\n- main entry\n- base\n"},
        {"CHANGELOG.md": "# Changelog\n\n- branch entry\n- base\n"},
    )
    work = tmp_path / "train"
    assert agent_integrate.integrate_merge(work, plan, integrate) == 0
    agent_integrate.integrate_finish(work, plan)
    agent_integrate.cleanup_worktrees(work, integrate)
    state = json.loads((work / "state" / "pr-3.json").read_text(encoding="utf-8"))
    assert state["status"] == "ready", state
    assert state["resolved"] == ["CHANGELOG.md"]

    fake = _FakeGh()
    monkeypatch.setattr(agent_integrate, "gh", fake)
    outcome = agent_integrate.apply_update(work, plan, plan["entries"][0], False, applier)
    assert outcome.startswith("updated with main"), outcome
    pushed = _git(origin, "rev-parse", "feature").strip()
    assert pushed == state["sha"]
    changelog = _git(origin, "show", "feature:CHANGELOG.md")
    assert "main entry" in changelog and "branch entry" in changelog
    unlabel = ("pr", "edit", "3", "--repo", "example/project", "--remove-label", "needs-rebase")
    assert unlabel in fake.calls


def test_train_resolver_runs_between_merge_and_finish(tmp_path, repo, monkeypatch):
    origin, integrate, applier, plan = _origin_with_feature(
        tmp_path, repo, {"app.py": "VALUE = 2\n"}, {"app.py": "VALUE = 3\n"}
    )
    work = tmp_path / "train"
    assert agent_integrate.integrate_merge(work, plan, integrate) == 1
    resolver = tmp_path / "resolver.py"
    resolver.write_text(
        "from pathlib import Path\nPath('app.py').write_text('VALUE = 5\\n')\n", encoding="utf-8"
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(resolver))}"
    agent_integrate.integrate_resolve(work, plan, command)
    agent_integrate.integrate_finish(work, plan)
    agent_integrate.cleanup_worktrees(work, integrate)
    state = json.loads((work / "state" / "pr-3.json").read_text(encoding="utf-8"))
    assert state["status"] == "ready" and state["ai_resolved"] == ["app.py"], state

    fake = _FakeGh()
    monkeypatch.setattr(agent_integrate, "gh", fake)
    agent_integrate.apply_update(work, plan, plan["entries"][0], False, applier)
    assert _git(origin, "show", "feature:app.py") == "VALUE = 5\n"
    assert any(call[:2] == ("pr", "comment") for call in fake.calls)


def test_push_rejections_are_told_apart():
    github = (
        "! [remote rejected] abc -> claude/x (refusing to allow a GitHub App to create or "
        "update workflow `.github/workflows/ci.yml` without `workflows` permission)"
    )
    assert agent_integrate.push_rejection(github) == "workflows"
    assert agent_integrate.push_rejection("! [rejected] abc -> x (non-fast-forward)") == "moved"


def test_a_workflow_permission_refusal_is_reported_once(tmp_path, repo, monkeypatch):
    origin, integrate, applier, plan = _origin_with_feature(
        tmp_path,
        repo,
        {"CHANGELOG.md": "# Changelog\n\n- main entry\n- base\n"},
        {"CHANGELOG.md": "# Changelog\n\n- branch entry\n- base\n"},
    )
    hook = origin / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\necho 'refusing to allow a GitHub App to create or update workflow "
        "`.github/workflows/ci.yml` without `workflows` permission' >&2\nexit 1\n",
        encoding="utf-8",
        newline="\n",
    )
    hook.chmod(0o755)
    work = tmp_path / "train"
    agent_integrate.integrate_merge(work, plan, integrate)
    agent_integrate.integrate_finish(work, plan)
    agent_integrate.cleanup_worktrees(work, integrate)
    fake = _FakeGh()
    monkeypatch.setattr(agent_integrate, "gh", fake)
    outcome = agent_integrate.apply_update(work, plan, plan["entries"][0], False, applier)
    assert "workflows" in outcome, outcome
    assert any(c[:2] == ("pr", "comment") for c in fake.calls)
    told = {**plan["entries"][0], "labels": ["needs-rebase"]}
    quiet = _FakeGh()
    monkeypatch.setattr(agent_integrate, "gh", quiet)
    agent_integrate.apply_update(work, plan, told, False, applier)
    assert not quiet.calls  # already labelled: no comment on every tick


def test_train_reports_a_conflict_without_a_resolver(tmp_path, repo, monkeypatch):
    origin, integrate, applier, plan = _origin_with_feature(
        tmp_path, repo, {"app.py": "VALUE = 2\n"}, {"app.py": "VALUE = 3\n"}
    )
    head = plan["entries"][0]["head"]
    work = tmp_path / "train"
    assert agent_integrate.integrate_merge(work, plan, integrate) == 1
    agent_integrate.integrate_finish(work, plan)
    agent_integrate.cleanup_worktrees(work, integrate)
    fake = _FakeGh()
    monkeypatch.setattr(agent_integrate, "gh", fake)
    outcome = agent_integrate.apply_update(work, plan, plan["entries"][0], False, applier)
    assert outcome == "conflict - app.py"
    assert _git(origin, "rev-parse", "feature").strip() == head
    label = ("pr", "edit", "3", "--repo", "example/project", "--add-label", "needs-rebase")
    assert label in fake.calls


def test_apply_rejects_a_bundle_not_built_on_the_planned_head(tmp_path, repo, monkeypatch):
    origin, integrate, applier, plan = _origin_with_feature(
        tmp_path,
        repo,
        {"CHANGELOG.md": "# Changelog\n\n- main entry\n- base\n"},
        {"CHANGELOG.md": "# Changelog\n\n- branch entry\n- base\n"},
    )
    work = tmp_path / "train"
    agent_integrate.integrate_merge(work, plan, integrate)
    agent_integrate.integrate_finish(work, plan)
    agent_integrate.cleanup_worktrees(work, integrate)
    # A head the bundle does not build on (an unrelated commit in the applier).
    _git(applier, "checkout", "-q", "--orphan", "elsewhere")
    _git(applier, "commit", "-q", "--allow-empty", "-m", "unrelated")
    forged = {**plan["entries"][0], "head": _git(applier, "rev-parse", "HEAD").strip()}
    monkeypatch.setattr(agent_integrate, "gh", _FakeGh())
    outcome = agent_integrate.apply_update(work, plan, forged, False, applier)
    assert outcome.startswith("update bundle rejected"), outcome
    assert _git(origin, "rev-parse", "feature").strip() == plan["entries"][0]["head"]


def test_apply_enqueues_green_pull_requests_and_reports_the_rest(tmp_path, monkeypatch):
    fake = _FakeGh()
    monkeypatch.setattr(agent_integrate, "gh", fake)
    plan = {
        "repo": "example/project",
        "main": "m" * 40,
        "queue": True,
        "entries": [
            {"number": 1, "branch": "claude/a", "head": "a" * 40, "id": "PR_a", "action": "merge"},
            {"number": 2, "branch": "claude/b", "head": "b" * 40, "id": "PR_b", "action": "merge"},
            {"number": 3, "branch": "claude/c", "head": "c" * 40, "id": "PR_c", "action": "queued"},
            {"number": 4, "branch": "claude/d", "head": "d" * 40, "id": "PR_d", "action": "held"},
        ],
    }
    assert agent_integrate.apply(tmp_path, plan, False, True) == 0
    enqueued = [c for c in fake.calls if c[:2] == ("api", "graphql")]
    assert len(enqueued) == 2  # a queue takes every green PR, not one per tick
    assert "head=" + "a" * 40 in enqueued[0] and "id=PR_a" in enqueued[0]
    assert not any(c[:2] == ("pr", "merge") or c[:2] == ("workflow", "run") for c in fake.calls)


def test_apply_reruns_only_the_failed_jobs_of_a_cancelled_run(tmp_path, monkeypatch):
    fake = _FakeGh()
    monkeypatch.setattr(agent_integrate, "gh", fake)
    plan = {
        "repo": "example/project",
        "main": "m" * 40,
        "queue": True,
        "entries": [{"number": 1, "branch": "claude/a", "head": "a" * 40, "id": "PR_a",
                     "action": "rerun", "run_id": 77}],
    }
    agent_integrate.apply(tmp_path, plan, False, True)
    assert ("run", "rerun", "77", "--failed", "--repo", "example/project") in fake.calls


def test_apply_without_a_queue_merges_directly_and_pins_the_head(tmp_path, monkeypatch):
    fake = _FakeGh()
    monkeypatch.setattr(agent_integrate, "gh", fake)
    plan = {
        "repo": "example/project",
        "main": "m" * 40,
        "queue": False,
        "entries": [
            {"number": 1, "branch": "claude/a", "head": "a" * 40, "id": "PR_a", "action": "merge"}
        ],
    }
    agent_integrate.apply(tmp_path, plan, False, True)
    merge = next(c for c in fake.calls if c[:2] == ("pr", "merge"))
    assert merge[-2:] == ("--match-head-commit", "a" * 40) and "--squash" in merge
    dispatch = ("workflow", "run", "ci.yml", "--ref", "main", "-f", "full=false")
    assert dispatch + ("-f", "include_macos=false") in fake.calls  # no macOS lanes per merge


# --------------------------------------------------------------------------- release


@pytest.mark.parametrize("status,conclusion,expected", [
    ("completed", "failure", "failure"),
    ("completed", "cancelled", "failure"),
    ("completed", "success", "success"),
    ("in_progress", None, "pending"),
    ("queued", None, "pending"),
])
def test_release_admission_uses_newest_run(status, conclusion, expected):
    from scripts.ci.release_admit import latest_gate_state

    old = {"id": 10, "status": "completed", "conclusion": "success"}
    new = {"id": 11, "status": status, "conclusion": conclusion}
    assert latest_gate_state([old, new]) == expected
    assert latest_gate_state([new, old]) == expected
    assert latest_gate_state([]) == "missing"


@pytest.mark.parametrize("filename,job", [
    ("release.yml", "publish"),
    ("sign-installer.yml", "provenance"),
    ("sign-installer.yml", "release"),
])
def test_publication_requires_a_tag(filename, job):
    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[3] / ".github/workflows" / filename).read_text(
            encoding="utf-8"
        )
    )
    condition = workflow["jobs"][job]["if"]
    assert "github.ref_type == 'tag'" in condition.split(" && ")
    assert "||" not in condition


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
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "personal-jarvis"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    (tmp_path / "jarvis" / "__init__.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "dependency"\nversion = "1.0.0"\n\n'
        '[[package]]\nname = "personal-jarvis"\nversion = "1.0.0"\n'
        'source = { editable = "." }\n',
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n---\n\n## [1.0.0] — 2026-01-01\n\n- old\n",
        encoding="utf-8",
    )
    cut_release.apply("1.1.0", "### Added\n\n- thing", "2026-09-28", tmp_path)
    text = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.index("## [Unreleased]") < text.index("## [1.1.0] — 2026-09-28")
    assert text.index("## [1.1.0]") < text.index("## [1.0.0]")
    assert cut_release.section_notes(text, "1.1.0") == "### Added\n\n- thing"
    lock = (tmp_path / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "personal-jarvis"\nversion = "1.1.0"' in lock
    assert 'name = "dependency"\nversion = "1.0.0"' in lock
    from scripts.ci import release_admit

    assert release_admit.versions(tmp_path) == ("1.1.0", "1.1.0")
    assert release_admit.check_identity("v1.1.0", tmp_path) == []
    assert release_admit.check_identity("v1.2.0", tmp_path)


def test_flaky_file_metadata_cannot_waive_new_failures(tmp_path):
    baseline = tmp_path / "b.json"
    baseline.write_text(
        json.dumps({"flaky_files": ["tests/unit/x/test_timing.py"], "known_failures": []}),
        encoding="utf-8",
    )
    flip = _report(tmp_path / "r1.json", ["tests.unit.x.test_timing::test_ramp"])
    crash = _report(tmp_path / "r2.json", ["tests/unit/x/test_timing.py::<timeout 300s>"])
    other = _report(tmp_path / "r3.json", ["tests.unit.x.test_timing_other::test_a"])
    assert ratchet_tests.main(["check", "--baseline", str(baseline), str(flip)]) == 1
    assert ratchet_tests.main(["check", "--baseline", str(baseline), str(crash)]) == 1
    assert ratchet_tests.main(["check", "--baseline", str(baseline), str(other)]) == 1
    out = tmp_path / "b.json"
    assert ratchet_tests.main(["update", "--out", str(out), str(other)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["flaky_files"] == [
        "tests/unit/x/test_timing.py"
    ]
