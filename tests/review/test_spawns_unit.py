"""Unit tests for WorkerSpawner / ReviewerSpawner with a mocked
HarnessManager (Phase 8.3).

Plan reference: §6.3 acceptance criteria — CLI arguments, prompt content,
RunDirectory layout, verdict parsing + schema rejection.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.protocols import HarnessResult, HarnessTask
from jarvis.core.review.errors import (
    HarnessUnavailable,
    ReviewerUnavailable,
    VerdictParseError,
    WorkerSpawnError,
)
from jarvis.core.review.io import RunDirectory
from jarvis.core.review.spawns import (
    DEFAULT_REVIEWER_BUDGET_USD,
    DEFAULT_REVIEWER_TOOLS,
    DEFAULT_WORKER_BUDGET_USD,
    DEFAULT_WORKER_TOOLS,
    ReviewerSpawner,
    WorkerSpawner,
    _extract_verdict_json,
)
from jarvis.core.review.state import RunState
from jarvis.core.review.verdict import ReviewStatus

# ----------------------------------------------------------------------
# Fake HarnessManager
# ----------------------------------------------------------------------


class FakeHarnessManager:
    """Drop-in replacement for HarnessManager — no subprocess, returns a scripted result.

    Each call consumes one prepared `script` sequence entry; this lets
    a test script several consecutive dispatch() calls (worker
    + reviewer) with different outputs.

    `registered` mirrors `HarnessManager.available()` — the spawners check
    this before dispatching (AP-23 wave-2 finding 5), so tests exercising
    the real spawn path must include the harness name they expect to be
    picked. Default: `["jarvis_agent"]`, the canonical Jarvis-Agent worker
    harness name (see spawns.py `_WORKER_HARNESS_CANDIDATES`).
    """

    def __init__(
        self,
        scripts: list[dict[str, Any]] | None = None,
        *,
        registered: list[str] | None = None,
    ) -> None:
        # script: {"stdout": "...", "stderr": "...", "exit_code": 0, "duration_ms": 42}
        self._scripts: list[dict[str, Any]] = list(scripts or [])
        self.calls: list[tuple[str, HarnessTask]] = []
        self._registered = ["jarvis_agent"] if registered is None else list(registered)

    def available(self) -> list[str]:
        return list(self._registered)

    def dispatch(
        self, name: str, task: HarnessTask
    ) -> AsyncIterator[HarnessResult]:
        self.calls.append((name, task))
        script = self._scripts.pop(0) if self._scripts else {
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "duration_ms": 1,
        }
        return self._gen(script)

    @staticmethod
    async def _gen(script: dict[str, Any]) -> AsyncIterator[HarnessResult]:
        if script.get("stdout"):
            yield HarnessResult(stdout=script["stdout"])
        if script.get("stderr"):
            yield HarnessResult(stderr=script["stderr"])
        yield HarnessResult(
            exit_code=script.get("exit_code", 0),
            duration_ms=script.get("duration_ms", 1),
            is_final=True,
        )


def _make_state(*, run_id: str = "run-1", task: str = "do something useful") -> RunState:
    return RunState(run_id=run_id, task=task, rubric_id="default")


def _valid_verdict_dict(*, status: str = "pass") -> dict:
    return {
        "status": status,
        "summary": "all good",
        "issues": [],
        "rubric_results": [],
        "score": 0.95,
    }


# ======================================================================
# WorkerSpawner
# ======================================================================


def test_worker_spawner_calls_registered_jarvis_agent_harness(tmp_path: Path) -> None:
    """Auto-resolves the canonical `jarvis_agent` harness name — never a
    hardcoded literal (AP-21/AP-23 wave-2 finding 5)."""
    fake = FakeHarnessManager(
        [{"stdout": "wrote artifact.\n", "exit_code": 0, "duration_ms": 100}],
        registered=["jarvis_agent"],
    )
    spawner = WorkerSpawner(harness_manager=fake, runs_root=tmp_path / "runs")
    state = _make_state()

    result = asyncio.run(spawner.spawn(state, iteration=1))

    assert len(fake.calls) == 1
    name, task = fake.calls[0]
    assert name == "jarvis_agent"
    assert isinstance(task, HarnessTask)
    assert "Original task" in task.prompt
    assert state.task in task.prompt
    # worker prompt contains a path hint (AD-9)
    assert "iter-1" in task.prompt and "worker.out" in task.prompt

    assert isinstance(result, str) and result.strip()


def test_worker_spawner_falls_back_to_legacy_openclaw_alias_when_registered(
    tmp_path: Path,
) -> None:
    """The pre-rename ``openclaw`` name is still accepted as a back-compat
    alias IF it is actually a registered harness (never assumed)."""
    fake = FakeHarnessManager(
        [{"stdout": "wrote artifact.\n", "exit_code": 0}],
        registered=["openclaw"],
    )
    spawner = WorkerSpawner(harness_manager=fake, runs_root=tmp_path / "runs")

    asyncio.run(spawner.spawn(_make_state(), iteration=1))

    name, _task = fake.calls[0]
    assert name == "openclaw"


def test_worker_spawner_raises_honest_unavailable_when_no_harness_registered(
    tmp_path: Path,
) -> None:
    """The AP-23 wave-2 finding 5 regression test: when neither
    ``jarvis_agent`` nor ``openclaw`` is registered (true of every install
    today — Welle-4 removed the old subprocess bridge), the spawner must
    raise the honest ``HarnessUnavailable`` — never a raw ``KeyError`` and
    never a message containing the dead internal harness name."""
    fake = FakeHarnessManager(registered=[])
    spawner = WorkerSpawner(harness_manager=fake, runs_root=tmp_path / "runs")

    with pytest.raises(HarnessUnavailable) as exc:
        asyncio.run(spawner.spawn(_make_state(), iteration=1))

    assert "openclaw" not in str(exc.value)
    assert not fake.calls  # never reached dispatch() — no phantom subprocess spawn


def test_worker_spawner_writes_iter_n_worker_out(tmp_path: Path) -> None:
    """If the worker itself doesn't write, the spawner persists stdout."""
    fake = FakeHarnessManager([
        {"stdout": "summary line\n", "exit_code": 0}
    ])
    runs_root = tmp_path / "runs"
    spawner = WorkerSpawner(harness_manager=fake, runs_root=runs_root)
    state = _make_state(run_id="abc")

    asyncio.run(spawner.spawn(state, iteration=1))

    out_path = runs_root / "abc" / "iter-1" / "worker.out"
    assert out_path.exists()
    assert out_path.read_text(encoding="utf-8").strip() == "summary line"


def test_worker_spawner_keeps_self_written_artifact(tmp_path: Path) -> None:
    """If the worker itself writes to worker.out, the spawner does NOT
    overwrite it with stdout.
    """
    runs_root = tmp_path / "runs"
    state = _make_state(run_id="abc")

    # Pre-create worker.out (simulates worker tool use)
    pre_dir = RunDirectory(runs_root, "abc").ensure()
    pre_dir.write_worker_output(1, "self-written artifact body")

    fake = FakeHarnessManager([
        {"stdout": "summary only\n", "exit_code": 0}
    ])
    spawner = WorkerSpawner(harness_manager=fake, runs_root=runs_root)

    result = asyncio.run(spawner.spawn(state, iteration=1))
    assert result == "self-written artifact body"


def test_worker_spawner_raises_on_nonzero_exit(tmp_path: Path) -> None:
    fake = FakeHarnessManager([
        {"stdout": "", "stderr": "boom", "exit_code": 1}
    ])
    spawner = WorkerSpawner(harness_manager=fake, runs_root=tmp_path / "runs")

    with pytest.raises(WorkerSpawnError) as exc:
        asyncio.run(spawner.spawn(_make_state(), iteration=1))
    assert "exit_code=1" in str(exc.value)


def test_worker_spawner_includes_feedback_block_on_iter2(tmp_path: Path) -> None:
    """Iter-2 spawn must include the feedback block from iter 1."""
    from jarvis.core.review.verdict import ReviewIssue, ReviewVerdict
    fake = FakeHarnessManager([
        {"stdout": "summary\n", "exit_code": 0}
    ])
    spawner = WorkerSpawner(harness_manager=fake, runs_root=tmp_path / "runs")
    state = _make_state()
    state.record_iteration(
        iteration=1,
        worker_output="initial output",
        verdict=ReviewVerdict(
            status=ReviewStatus.NEEDS_REVISION,
            summary="needs work",
            issues=[
                ReviewIssue(
                    severity="warning",
                    description="docstring missing on add()",
                    location="src/calc.py:5",
                    fix_hint="add a one-line docstring",
                )
            ],
            score=0.5,
        ),
    )

    asyncio.run(spawner.spawn(state, iteration=2))

    _, task = fake.calls[-1]
    assert "Reviewer feedback from iteration 1" in task.prompt
    assert "docstring missing on add()" in task.prompt
    assert "src/calc.py:5" in task.prompt
    assert "add a one-line docstring" in task.prompt


# ======================================================================
# ReviewerSpawner
# ======================================================================


def test_reviewer_spawner_passes_schema_and_low_effort(tmp_path: Path) -> None:
    fake = FakeHarnessManager(
        [{"stdout": json.dumps(_valid_verdict_dict()), "exit_code": 0}],
        registered=["jarvis_agent"],
    )
    runs_root = tmp_path / "runs"
    # worker.out must exist for the reviewer prompt path — it's written by
    # WorkerSpawner; in unit tests we mock this.
    RunDirectory(runs_root, "abc").ensure().write_worker_output(1, "x")

    spawner = ReviewerSpawner(harness_manager=fake, runs_root=runs_root)
    state = _make_state(run_id="abc")

    asyncio.run(spawner.spawn(state, "ignored", iteration=1))

    name, task = fake.calls[0]
    assert name == "jarvis_agent"
    assert task.timeout_s == 120
    assert "verdict_schema.json" in task.prompt


def test_reviewer_spawner_raises_honest_unavailable_when_no_harness_registered(
    tmp_path: Path,
) -> None:
    """AP-23 wave-2 finding 5, reviewer side: no registered harness must
    degrade honestly, never a raw KeyError naming ``openclaw``."""
    fake = FakeHarnessManager(registered=[])
    runs_root = tmp_path / "runs"
    RunDirectory(runs_root, "abc").ensure().write_worker_output(1, "x")
    spawner = ReviewerSpawner(harness_manager=fake, runs_root=runs_root)

    with pytest.raises(HarnessUnavailable) as exc:
        asyncio.run(spawner.spawn(_make_state(run_id="abc"), "ignored", 1))

    assert "openclaw" not in str(exc.value)
    assert not fake.calls


def test_reviewer_spawner_returns_parsed_verdict(tmp_path: Path) -> None:
    fake = FakeHarnessManager([
        {"stdout": json.dumps(_valid_verdict_dict(status="pass")), "exit_code": 0}
    ])
    runs_root = tmp_path / "runs"
    RunDirectory(runs_root, "abc").ensure().write_worker_output(1, "x")
    spawner = ReviewerSpawner(harness_manager=fake, runs_root=runs_root)

    verdict = asyncio.run(spawner.spawn(_make_state(run_id="abc"), "ignored", 1))
    assert verdict.status is ReviewStatus.PASS
    assert verdict.score == 0.95


def test_reviewer_spawner_writes_iter_n_verdict_json(tmp_path: Path) -> None:
    payload = _valid_verdict_dict(status="needs_revision")
    payload["score"] = 0.6
    fake = FakeHarnessManager([
        {"stdout": json.dumps(payload), "exit_code": 0}
    ])
    runs_root = tmp_path / "runs"
    RunDirectory(runs_root, "abc").ensure().write_worker_output(1, "x")
    spawner = ReviewerSpawner(harness_manager=fake, runs_root=runs_root)

    asyncio.run(spawner.spawn(_make_state(run_id="abc"), "ignored", 1))

    verdict_path = runs_root / "abc" / "iter-1" / "verdict.json"
    assert verdict_path.exists()
    on_disk = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert on_disk["status"] == "needs_revision"
    assert on_disk["score"] == 0.6


def test_reviewer_spawner_raises_unavailable_on_nonzero_exit(
    tmp_path: Path,
) -> None:
    fake = FakeHarnessManager([
        {"stdout": "", "stderr": "auth failed", "exit_code": 1}
    ])
    runs_root = tmp_path / "runs"
    RunDirectory(runs_root, "abc").ensure().write_worker_output(1, "x")
    spawner = ReviewerSpawner(harness_manager=fake, runs_root=runs_root)

    with pytest.raises(ReviewerUnavailable):
        asyncio.run(spawner.spawn(_make_state(run_id="abc"), "ignored", 1))


def test_reviewer_spawner_raises_parse_error_on_invalid_json(
    tmp_path: Path,
) -> None:
    fake = FakeHarnessManager([
        {"stdout": "this is not JSON at all", "exit_code": 0}
    ])
    runs_root = tmp_path / "runs"
    RunDirectory(runs_root, "abc").ensure().write_worker_output(1, "x")
    spawner = ReviewerSpawner(harness_manager=fake, runs_root=runs_root)

    with pytest.raises(VerdictParseError):
        asyncio.run(spawner.spawn(_make_state(run_id="abc"), "ignored", 1))


def test_reviewer_spawner_raises_parse_error_on_schema_violation(
    tmp_path: Path,
) -> None:
    """JSON is valid, but score=2.0 violates the schema."""
    bad = {
        "status": "pass",
        "summary": "ok",
        "issues": [],
        "rubric_results": [],
        "score": 2.0,
    }
    fake = FakeHarnessManager([
        {"stdout": json.dumps(bad), "exit_code": 0}
    ])
    runs_root = tmp_path / "runs"
    RunDirectory(runs_root, "abc").ensure().write_worker_output(1, "x")
    spawner = ReviewerSpawner(harness_manager=fake, runs_root=runs_root)

    with pytest.raises(VerdictParseError):
        asyncio.run(spawner.spawn(_make_state(run_id="abc"), "ignored", 1))


def test_reviewer_spawner_handles_prose_prefix(tmp_path: Path) -> None:
    """Reviewer ignores prose before the JSON (robustness against --output-format drift)."""
    payload = _valid_verdict_dict()
    fake = FakeHarnessManager([
        {"stdout": "Here is my verdict:\n" + json.dumps(payload), "exit_code": 0}
    ])
    runs_root = tmp_path / "runs"
    RunDirectory(runs_root, "abc").ensure().write_worker_output(1, "x")
    spawner = ReviewerSpawner(harness_manager=fake, runs_root=runs_root)

    verdict = asyncio.run(spawner.spawn(_make_state(run_id="abc"), "ignored", 1))
    assert verdict.status is ReviewStatus.PASS


# ======================================================================
# _extract_verdict_json
# ======================================================================


def test_extract_clean_object() -> None:
    assert _extract_verdict_json('{"a": 1}') == {"a": 1}


def test_extract_with_prose_prefix() -> None:
    assert _extract_verdict_json('Here you go:\n{"a": 1}') == {"a": 1}


def test_extract_with_prose_suffix() -> None:
    assert _extract_verdict_json('{"a": 1}\nThanks.') == {"a": 1}


def test_extract_empty_returns_none() -> None:
    assert _extract_verdict_json("") is None
    assert _extract_verdict_json("   \n\t  ") is None


def test_extract_non_json_returns_none() -> None:
    assert _extract_verdict_json("nothing structured here") is None


def test_extract_array_at_top_returns_none() -> None:
    """Top level is an array, not an object — reviewer verdict must be an object."""
    assert _extract_verdict_json("[1, 2, 3]") is None
