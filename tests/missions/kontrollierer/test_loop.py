"""Integration tests for the Kontrollierer.run_mission loop.

Uses FakeWorker + FakeCriticRunner — NO real subprocess spawns,
no real claude-CLI calls. Verifies state machine, iteration cap,
reflections, budget integration.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Iterable
from unittest.mock import MagicMock

import pytest

from jarvis.missions.budget import BudgetTracker
from jarvis.missions.critic.reflections import ReflectionMemory
from jarvis.missions.critic.verdict import (
    CriticAxis,
    CriticVerdict,
    REQUIRED_AXES,
)
from jarvis.missions.kontrollierer.decomposer import MissionDecomposer, MissionPlan, Step
from jarvis.missions.kontrollierer.orchestrator import (
    MAX_WORKERS_PER_MISSION,
    Kontrollierer,
    TaskOutcome,
)
from jarvis.missions.kontrollierer.worker_prompt import ARTIFACT_LANGUAGE_DIRECTIVE
from jarvis.missions.manager import MissionManager
from jarvis.missions.state_machine import MissionState
from jarvis.missions.workers.base import SpawnedWorker


# --- Fakes ---


@dataclass
class _FakeWorkerEvent:
    """Simulates a Claude stream result event with cost_usd."""

    type: str = "result"
    cost_usd: float = 0.05
    total_tokens: int = 1000
    session_id: str | None = "fake-session-1"


class FakeWorker:
    """Worker stub: yields a single result event with cost + session."""

    cli = "claude"

    def __init__(self, *, cost: float = 0.05, tokens: int = 1000, session: str = "s1") -> None:
        self._cost = cost
        self._tokens = tokens
        self._session = session
        self.last_pid: int = 12345  # plausible
        self.spawn_calls: list[dict[str, Any]] = []

    async def spawn(
        self,
        prompt: str,
        *,
        worktree: Path,
        env: dict[str, str],
        job: Any,
        worker_id: str,
        log_dir: Path,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        self.spawn_calls.append({
            "prompt": prompt,
            "worker_id": worker_id,
            "worktree": str(worktree),
            **kwargs,
        })
        # Write a few log lines for the critic
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "stream.jsonl").write_text(
            '{"type":"result","subtype":"success"}\n', encoding="utf-8"
        )
        # Write a small diff file so `git diff` would show something — that
        # would require worktree to be a git repo, which it is not in the test.
        # We rely on an empty diff (capture_diff returned "") — the
        # critic stub doesn't care.
        yield _FakeWorkerEvent(cost_usd=self._cost, total_tokens=self._tokens, session_id=self._session)


class FakeCriticRunner:
    """Critic stub: returns hardcoded verdicts in order."""

    def __init__(self, *verdicts: CriticVerdict) -> None:
        self._verdicts = list(verdicts)
        self._idx = 0
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> CriticVerdict:
        self.calls.append(kwargs)
        if self._idx >= len(self._verdicts):
            # Default: revise with empty-evidence avoidance
            return _make_revise_verdict()
        v = self._verdicts[self._idx]
        self._idx += 1
        return v


class FakeJobObject:
    """No-op async context manager for the job object."""

    async def __aenter__(self) -> "FakeJobObject":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def assign(self, pid: int) -> None:
        pass


class FakeWorktreeManager:
    """Returns tmp_path-basierte Worktrees ohne git."""

    def __init__(self, base: Path) -> None:
        self._base = base
        self._counter = 0

    def create(self, *, mission_slug: str, task_id: str, **kwargs: Any) -> Path:
        self._counter += 1
        wt = self._base / f"wt_{self._counter}_{task_id[:8]}"
        wt.mkdir(parents=True, exist_ok=True)
        return wt

    def remove(self, path: Path, **kwargs: Any) -> None:
        pass


# --- Verdict-Factories ---


def _make_approve_verdict() -> CriticVerdict:
    return CriticVerdict(
        verdict="approve",
        axes={
            ax: CriticAxis(status="pass", evidence=["src/x.py:1"])
            for ax in REQUIRED_AXES
        },
        issues=[],
        correction_instruction="",
        summary="ok",
        summary_de="ok",
        confidence=0.9,
        suggested_next_action="accept",
    )


def _make_revise_verdict(summary: str = "needs fix") -> CriticVerdict:
    return CriticVerdict(
        verdict="revise",
        axes={
            "correctness": CriticAxis(status="fail", evidence=["src/x.py:1"]),
            "completeness": CriticAxis(status="pass", evidence=["src/y.py:2"]),
            "side_effects": CriticAxis(status="pass", evidence=["src/z.py:3"]),
            "security": CriticAxis(status="pass", evidence=["src/a.py:4"]),
        },
        issues=[],
        correction_instruction="fix the bug",
        summary=summary,
        summary_de=summary,
        confidence=0.8,
        suggested_next_action="retry",
    )


def _make_reject_verdict() -> CriticVerdict:
    v = _make_revise_verdict()
    return v.model_copy(update={"verdict": "reject", "suggested_next_action": "abort"})


# --- Fixtures ---


@pytest.fixture
async def manager(tmp_missions_db: Path):
    m = MissionManager(tmp_missions_db)
    await m.start()
    yield m
    await m.stop()


def _make_kontrollierer(
    *,
    manager: MissionManager,
    tmp_path: Path,
    critic: FakeCriticRunner,
    worker_factory_fn=None,
    decomposer_plan: MissionPlan | None = None,
    budget: BudgetTracker | None = None,
) -> Kontrollierer:
    """Helper function to build a Kontrollierer with fakes."""

    decomposer = MagicMock(spec=MissionDecomposer)
    if decomposer_plan is None:
        decomposer_plan = MissionPlan(
            steps=[Step(slug="task", prompt="do task")],
            n_workers=1,
            expected_output="x",
        )

    async def _decompose(prompt: str) -> MissionPlan:
        return decomposer_plan  # type: ignore[return-value]

    decomposer.decompose = _decompose  # type: ignore[method-assign]

    if worker_factory_fn is None:
        shared_worker = FakeWorker()

        def worker_factory_fn(step):  # type: ignore[no-redef]
            return shared_worker

    return Kontrollierer(
        manager=manager,
        decomposer=decomposer,
        critic_runner=critic,  # type: ignore[arg-type]
        worktree_mgr=FakeWorktreeManager(tmp_path / "worktrees"),  # type: ignore[arg-type]
        env_builder=lambda p: {},
        budget=budget or BudgetTracker(per_mission_usd=10.0, daily_usd=100.0),
        worker_factory=worker_factory_fn,
        job_factory=FakeJobObject,
        isolation_root=tmp_path / "missions",
    )


# --- Scenarios ---


@pytest.mark.asyncio
async def test_happy_path_iteration_0_approves(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Worker spawnt -> Critic approves on iter 0 -> MissionApproved."""
    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(manager=manager, tmp_path=tmp_path, critic=critic)
    mid = await manager.dispatch(prompt="build palindrome function")

    end_state = await k.run_mission(mid)
    assert end_state == MissionState.APPROVED
    view = await manager.mission(mid)
    assert view is not None
    assert view.state == MissionState.APPROVED
    assert len(critic.calls) == 1


@pytest.mark.asyncio
async def test_two_iterations_approve_on_second(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Iter 0 revise -> iter 1 approve -> MissionApproved + 1 reflection."""
    critic = FakeCriticRunner(_make_revise_verdict("missing edge case"), _make_approve_verdict())
    k = _make_kontrollierer(manager=manager, tmp_path=tmp_path, critic=critic)
    mid = await manager.dispatch(prompt="build X")

    end_state = await k.run_mission(mid)
    assert end_state == MissionState.APPROVED
    assert len(critic.calls) == 2

    # Reflection file contains one entry
    mission_dir = tmp_path / "missions" / f"mission_{mid[:13]}"
    refl = ReflectionMemory(mission_dir)
    last = refl.last_n(5)
    assert len(last) == 1
    assert last[0].iteration == 0
    assert "missing edge case" in last[0].summary


@pytest.mark.asyncio
async def test_iter1_does_not_resume_jarvis_agent_session(
    manager: MissionManager, tmp_path: Path
) -> None:
    """BUG-LIVE-03 regression — `resume_session_id` must be None on every
    iteration. The external openclaw worker (v2026.5.7) prefers the failover
    chain persisted in the session-state file over the explicit `--model`
    flag on resume, so a reused session-id silently downgrades the provider
    routing. The critic correction lives in the worker prompt
    (`prior_block`) and not in the external worker's session state, so a
    fresh session loses nothing.
    """
    critic = FakeCriticRunner(_make_revise_verdict("needs fix"), _make_approve_verdict())
    shared_worker = FakeWorker(session="s-iter0")
    k = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,
        worker_factory_fn=lambda step: shared_worker,
    )
    mid = await manager.dispatch(prompt="build with mistake first")
    await k.run_mission(mid)

    assert len(shared_worker.spawn_calls) == 2, "expected two worker spawns"
    iter0_resume = shared_worker.spawn_calls[0].get("resume_session_id")
    iter1_resume = shared_worker.spawn_calls[1].get("resume_session_id")
    assert iter0_resume is None, f"iter0 must start a fresh session, got {iter0_resume!r}"
    assert iter1_resume is None, (
        f"iter1 must NOT resume iter0's session-id, got {iter1_resume!r}"
    )


@pytest.mark.asyncio
async def test_critic_unavailable_short_circuits_when_iter0_has_real_diff(
    manager: MissionManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live forensic 2026-05-16 (mission_019e3288): the Critic subprocess
    crashed on iter0 (EPERM symlink on `plugin-skills/browser-automation`,
    then `Unknown agent id "critic"`), but the worker had already produced
    a real 1237-byte diff. The old behavior swallowed the crash via
    `continue`, let iter1+iter2 land no-op edits that reverted the diff
    back to empty, then exited with `critic_loop_exhausted` — the user
    heard a generic failure phrase and the iter0 work was lost.

    With the new short-circuit, iter0 crash + non-empty real diff yields
    a `MissionFailed` carrying ``reason="critic_unavailable"`` after
    exactly one critic call. The on-disk `diff.iter0.patch` records the
    work so the user can replay it.
    """
    from jarvis.missions.critic.verdict import CriticSchemaInvalid

    class _CrashingCritic:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def run(self, **kwargs: Any) -> CriticVerdict:
            self.calls.append(kwargs)
            raise CriticSchemaInvalid("simulated subprocess crash")

    critic = _CrashingCritic()
    shared_worker = FakeWorker()
    k = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,  # type: ignore[arg-type]
        worker_factory_fn=lambda step: shared_worker,
    )
    # Fake a non-empty real diff so the short-circuit condition fires.
    monkeypatch.setattr(
        Kontrollierer,
        "_capture_diff",
        lambda self, worktree: (
            "diff --git a/docs/BUGS.md b/docs/BUGS.md\n"
            "@@ -1 +1,2 @@\n"
            " baseline\n"
            "+real BUG-021 content\n"
        ),
    )

    mid = await manager.dispatch(prompt="add BUG-021 entry to BUGS.md")
    end_state = await k.run_mission(mid)

    assert end_state == MissionState.FAILED
    # Exactly one critic call: short-circuit fires immediately.
    assert len(critic.calls) == 1
    # Mission-failed event carries the new reason.
    events = await manager.store.events_for_mission(mid)
    failed = [
        e.payload for e in events if e.payload.event_type == "MissionFailed"
    ]
    assert len(failed) == 1
    assert failed[0].reason == "critic_unavailable"  # type: ignore[attr-defined]
    # Partial-artifacts records the iter0 diff file we kept.
    artifacts = failed[0].partial_artifacts  # type: ignore[attr-defined]
    assert any("diff.iter0.patch" in str(p) for p in artifacts), (
        f"expected diff.iter0.patch in partial_artifacts, got {artifacts!r}"
    )


@pytest.mark.asyncio
async def test_three_iter_exhaustion_fails_mission(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Alle 3 Iterationen revise -> MissionFailed("critic_loop_exhausted") nach exakt 3 Spawns."""
    critic = FakeCriticRunner(*[_make_revise_verdict() for _ in range(3)])
    shared_worker = FakeWorker()
    k = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,
        worker_factory_fn=lambda step: shared_worker,
    )
    mid = await manager.dispatch(prompt="build X")

    end_state = await k.run_mission(mid)
    assert end_state == MissionState.FAILED
    assert len(critic.calls) == 3
    assert len(shared_worker.spawn_calls) == 3


@pytest.mark.asyncio
async def test_reject_early_stops_loop(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Iter 0 reject -> MissionFailed immediately, no retry."""
    critic = FakeCriticRunner(_make_reject_verdict())
    shared_worker = FakeWorker()
    k = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,
        worker_factory_fn=lambda step: shared_worker,
    )
    mid = await manager.dispatch(prompt="build X")
    end_state = await k.run_mission(mid)
    assert end_state == MissionState.FAILED
    assert len(critic.calls) == 1
    assert len(shared_worker.spawn_calls) == 1


@pytest.mark.asyncio
async def test_budget_exceeded_aborts_loop(
    manager: MissionManager, tmp_path: Path
) -> None:
    """BudgetTracker raises -> MissionFailed("budget_exceeded")."""
    # FakeWorker meldet $0.50 pro Iteration; per_mission limit = $0.40 -> raise after iter 0
    critic = FakeCriticRunner(_make_revise_verdict(), _make_revise_verdict())
    shared_worker = FakeWorker(cost=0.50)
    budget = BudgetTracker(per_mission_usd=0.40, daily_usd=10.0)
    k = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,
        worker_factory_fn=lambda step: shared_worker,
        budget=budget,
    )
    mid = await manager.dispatch(prompt="build X")
    end_state = await k.run_mission(mid)
    assert end_state == MissionState.FAILED


@pytest.mark.asyncio
async def test_reflections_persist_across_iterations(
    manager: MissionManager, tmp_path: Path
) -> None:
    """The worker prompt of the second iteration should contain the first reflection."""
    critic = FakeCriticRunner(
        _make_revise_verdict("first iteration issue"),
        _make_approve_verdict(),
    )
    shared_worker = FakeWorker()
    k = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,
        worker_factory_fn=lambda step: shared_worker,
    )
    mid = await manager.dispatch(prompt="build X")
    await k.run_mission(mid)

    # Iter 1 worker spawn-prompt enthaelt "Prior Critic Feedback"
    iter1_call = shared_worker.spawn_calls[1]
    assert "Prior Critic Feedback" in iter1_call["prompt"]
    assert "first iteration issue" in iter1_call["prompt"]


@pytest.mark.asyncio
async def test_state_machine_transitions_fire(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Mission durchlaeuft PENDING -> RUNNING -> CRITIQUING -> APPROVED."""
    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(manager=manager, tmp_path=tmp_path, critic=critic)
    mid = await manager.dispatch(prompt="build X")
    await k.run_mission(mid)

    events = await manager.store.events_for_mission(mid)
    state_changes = [
        e.payload for e in events
        if e.payload.event_type == "MissionStateChanged"
    ]
    transitions = [(s.from_state, s.to_state) for s in state_changes]  # type: ignore[attr-defined]
    assert ("PENDING", "RUNNING") in transitions
    assert ("RUNNING", "CRITIQUING") in transitions
    assert ("CRITIQUING", "APPROVED") in transitions


@pytest.mark.asyncio
async def test_publishes_critic_verdict_ready_event(
    manager: MissionManager, tmp_path: Path
) -> None:
    critic = FakeCriticRunner(_make_revise_verdict("issue X"), _make_approve_verdict())
    k = _make_kontrollierer(manager=manager, tmp_path=tmp_path, critic=critic)
    mid = await manager.dispatch(prompt="build X")
    await k.run_mission(mid)

    events = await manager.store.events_for_mission(mid)
    verdict_events = [
        e.payload for e in events
        if e.payload.event_type == "CriticVerdictReady"
    ]
    assert len(verdict_events) == 2
    iterations = sorted(e.iteration for e in verdict_events)  # type: ignore[attr-defined]
    assert iterations == [0, 1]


@pytest.mark.asyncio
async def test_publishes_mission_plan_ready(
    manager: MissionManager, tmp_path: Path
) -> None:
    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(manager=manager, tmp_path=tmp_path, critic=critic)
    mid = await manager.dispatch(prompt="build X")
    await k.run_mission(mid)

    events = await manager.store.events_for_mission(mid)
    plan_events = [e for e in events if e.payload.event_type == "MissionPlanReady"]
    assert len(plan_events) == 1


@pytest.mark.asyncio
async def test_max_workers_constant_is_5() -> None:
    assert MAX_WORKERS_PER_MISSION == 5


@pytest.mark.asyncio
async def test_unknown_mission_id_raises(
    manager: MissionManager, tmp_path: Path
) -> None:
    critic = FakeCriticRunner()
    k = _make_kontrollierer(manager=manager, tmp_path=tmp_path, critic=critic)
    with pytest.raises(KeyError, match="Mission not found"):
        await k.run_mission("00000000-0000-0000-0000-000000000000")


# ---------------------------------------------------------------------------
# Audit-2 (2026-05-18): CRITIC_UNAVAILABLE vs LOOP_EXHAUSTED distinction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_three_iters_critic_exception_fails_critic_unavailable(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Audit-2: when all 3 Critic calls raise (timeout/schema-invalid/etc.)
    AND iter0's diff is empty (so the early short-circuit doesn't fire),
    the loop must exit with CRITIC_UNAVAILABLE — not LOOP_EXHAUSTED.

    Rationale: `loop_exhausted` semantically means "the worker was given
    three rounds of valid feedback and still didn't fix the problem".
    When every Critic call crashed, the worker was never actually
    judged — surfacing `critic_loop_exhausted` to the voice layer
    lies to the user about how the mission was reviewed.
    """
    from jarvis.missions.critic.verdict import CriticTimeout

    class _AlwaysTimingOutCritic:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def run(self, **kwargs: Any) -> CriticVerdict:
            self.calls.append(kwargs)
            raise CriticTimeout("simulated timeout")

    critic = _AlwaysTimingOutCritic()
    shared_worker = FakeWorker()
    k = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,  # type: ignore[arg-type]
        worker_factory_fn=lambda step: shared_worker,
    )
    # Empty diff on every iter — that's the FakeWorktreeManager default
    # since there's no real git repo. We assert via `_capture_diff` here
    # explicitly so the test stays correct if the fake ever changes.

    mid = await manager.dispatch(prompt="research X read-only")
    end_state = await k.run_mission(mid)

    assert end_state == MissionState.FAILED
    # All three Critic spawns were attempted (no early short-circuit
    # because diff is empty, so the iter0-non-empty branch is bypassed).
    assert len(critic.calls) == 3
    # MissionFailed event carries critic_unavailable, NOT loop_exhausted.
    events = await manager.store.events_for_mission(mid)
    failed = [
        e.payload for e in events if e.payload.event_type == "MissionFailed"
    ]
    assert len(failed) == 1
    assert failed[0].reason == "critic_unavailable"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_mixed_critic_outcomes_one_valid_revise_yields_exhausted(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Audit-2: if at least one iteration produced a parsed revise
    verdict, the loop's exit semantically reflects "Worker got real
    feedback and didn't fix it" — that's LOOP_EXHAUSTED, not
    CRITIC_UNAVAILABLE.

    Setup: iter0 raises CriticTimeout, iter1 raises CriticTimeout,
    iter2 returns a valid revise verdict — loop ends naturally at
    MAX_CRITIC_LOOPS with one successful verdict on the books, so
    EXHAUSTED is correct.

    Inverse setup: iter0 valid revise, iter1+iter2 timeout — at the
    iter2 timeout the `iteration == MAX_CRITIC_LOOPS - 1` branch fires
    with `critic_ok_count >= 1`, so EXHAUSTED is also correct there.
    """
    from jarvis.missions.critic.verdict import CriticTimeout

    class _MixedCritic:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self._iter = 0

        async def run(self, **kwargs: Any) -> CriticVerdict:
            self.calls.append(kwargs)
            i = self._iter
            self._iter += 1
            # iter0+iter1 raise, iter2 returns valid revise
            if i < 2:
                raise CriticTimeout(f"simulated timeout iter{i}")
            return _make_revise_verdict("real feedback on iter2")

    critic = _MixedCritic()
    shared_worker = FakeWorker()
    k = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,  # type: ignore[arg-type]
        worker_factory_fn=lambda step: shared_worker,
    )

    mid = await manager.dispatch(prompt="build with one good critic round")
    end_state = await k.run_mission(mid)

    assert end_state == MissionState.FAILED
    assert len(critic.calls) == 3
    # Even though 2 of 3 critic calls crashed, the third produced a
    # valid verdict — the loop ended at natural for-loop exhaustion
    # (`return TaskOutcome.EXHAUSTED` at bottom of for-loop), so the
    # mission-fail reason is `critic_loop_exhausted`.
    events = await manager.store.events_for_mission(mid)
    failed = [
        e.payload for e in events if e.payload.event_type == "MissionFailed"
    ]
    assert len(failed) == 1
    assert failed[0].reason == "critic_loop_exhausted", (  # type: ignore[attr-defined]
        f"expected critic_loop_exhausted, got {failed[0].reason!r}"  # type: ignore[attr-defined]
    )


@pytest.mark.asyncio
async def test_first_two_valid_then_iter2_crashes_yields_exhausted(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Audit-2: iter0 + iter1 yield valid revise verdicts (Critic
    worked, worker didn't reach approve); iter2 then raises an
    exception. Since `critic_ok_count >= 1`, this is LOOP_EXHAUSTED.
    """
    from jarvis.missions.critic.verdict import CriticSchemaInvalid

    class _LateCrashCritic:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self._iter = 0

        async def run(self, **kwargs: Any) -> CriticVerdict:
            self.calls.append(kwargs)
            i = self._iter
            self._iter += 1
            if i < 2:
                return _make_revise_verdict(f"valid feedback iter{i}")
            raise CriticSchemaInvalid("crash on iter2")

    critic = _LateCrashCritic()
    shared_worker = FakeWorker()
    k = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,  # type: ignore[arg-type]
        worker_factory_fn=lambda step: shared_worker,
    )

    mid = await manager.dispatch(prompt="iterate twice then crash")
    end_state = await k.run_mission(mid)

    assert end_state == MissionState.FAILED
    assert len(critic.calls) == 3
    events = await manager.store.events_for_mission(mid)
    failed = [
        e.payload for e in events if e.payload.event_type == "MissionFailed"
    ]
    assert len(failed) == 1
    assert failed[0].reason == "critic_loop_exhausted"  # type: ignore[attr-defined]


# --- Read-only / informational missions surface the worker's answer ---------


class _ReadonlyAnswerWorker:
    """Worker stub: empty diff + (optionally) real tool evidence + an answer.

    Mirrors the 2026-05-24 GitHub mission: the worker queries github (read-only,
    no diff) and produces a final answer. With `with_tools=False` it simulates a
    bare "I did it" claim with no tool evidence (the hallucination case).
    """

    cli = "claude"

    def __init__(self, answer: str, *, with_tools: bool = True) -> None:
        self.last_pid = 222
        self._answer = answer
        self._with_tools = with_tools

    async def spawn(
        self, prompt: str, *, worktree: Path, env: dict[str, str], job: Any,
        worker_id: str, log_dir: Path, **kwargs: Any,
    ) -> AsyncIterator[Any]:
        import json as _json

        log_dir.mkdir(parents=True, exist_ok=True)
        lines: list[dict[str, Any]] = []
        if self._with_tools:
            lines.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "mcp__github__search_repositories", "input": {}}]}})
            lines.append({"type": "user", "message": {"content": [
                {"type": "tool_result", "content": [
                    {"type": "text", "text": '{"total_count":32}'}]}]}})
        lines.append({"type": "result", "result": self._answer, "subtype": "success"})
        (log_dir / "stream.jsonl").write_text(
            "\n".join(_json.dumps(line) for line in lines), encoding="utf-8"
        )
        yield _FakeWorkerEvent()


@pytest.mark.asyncio
async def test_readonly_mission_speaks_worker_answer(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Empty diff + tool evidence + answer -> MissionApproved.summary_de IS the answer."""
    critic = FakeCriticRunner(_make_approve_verdict())
    worker = _ReadonlyAnswerWorker(
        "Du hast 32 aktive Repositories (1 öffentlich, 31 privat)."  # i18n-allow: simulated German voice-readback answer (summary_de)
    )
    k = _make_kontrollierer(
        manager=manager, tmp_path=tmp_path, critic=critic,
        worker_factory_fn=lambda step: worker,
    )
    mid = await manager.dispatch(prompt="liste meine aktiven GitHub Repositories")

    end = await k.run_mission(mid)
    assert end == MissionState.APPROVED

    events = await manager.store.events_for_mission(mid)
    approved = [e.payload for e in events if e.payload.event_type == "MissionApproved"]
    assert approved, "expected a MissionApproved event"
    assert "32 aktive Repositories" in approved[0].summary_de  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_empty_diff_without_tool_evidence_keeps_generic_summary(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Anti-hallucination: empty diff + NO tool evidence must NOT surface a
    fabricated answer — the generic completion phrase stays."""
    critic = FakeCriticRunner(_make_approve_verdict())
    worker = _ReadonlyAnswerWorker("Habe alles erledigt.", with_tools=False)
    k = _make_kontrollierer(
        manager=manager, tmp_path=tmp_path, critic=critic,
        worker_factory_fn=lambda step: worker,
    )
    mid = await manager.dispatch(prompt="behaupte erfolg ohne tools")

    end = await k.run_mission(mid)
    assert end == MissionState.APPROVED

    events = await manager.store.events_for_mission(mid)
    approved = [e.payload for e in events if e.payload.event_type == "MissionApproved"]
    assert approved
    assert approved[0].summary_de == "Mission abgeschlossen."  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Task 2.2 — mission-level wall-clock deadline (2026-06-07)
# ---------------------------------------------------------------------------


class _HangingWorker:
    """Worker stub that never yields a terminal event — simulates a runaway
    subprocess that blocks indefinitely. Used to verify the mission-level
    wall-clock deadline cuts the execution.
    """

    cli = "claude"
    last_pid: int = 99999

    async def spawn(
        self,
        prompt: str,
        *,
        worktree: Path,
        env: dict[str, str],
        job: Any,
        worker_id: str,
        log_dir: Path,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        log_dir.mkdir(parents=True, exist_ok=True)
        # Hang for 100 seconds — the deadline (0.3s in the test) must cut this.
        await asyncio.sleep(100)
        # This yield is unreachable under the deadline; it is here only so the
        # type checker is satisfied that the function is an async generator.
        yield _FakeWorkerEvent()  # pragma: no cover


@pytest.mark.asyncio
async def test_mission_deadline_fails_as_timed_out(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Task 2.2: a mission that exceeds the wall-clock deadline is failed
    honestly as attempts_timed_out rather than the generic task_error.

    Uses a 0.3s deadline and a worker that sleeps 100s — the deadline must
    fire well before the sleep completes.
    """
    import time

    critic = FakeCriticRunner(_make_approve_verdict())
    hanging_worker = _HangingWorker()

    k = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,
        worker_factory_fn=lambda step: hanging_worker,
    )
    # Inject the tiny deadline directly onto the Kontrollierer instance so we
    # do not need to thread it through _make_kontrollierer's kwargs.
    k._mission_deadline_s = 0.3

    mid = await manager.dispatch(prompt="build something that hangs")

    t0 = time.monotonic()
    end_state = await k.run_mission(mid)
    elapsed = time.monotonic() - t0

    # The deadline must have fired (not the 100s hang).
    assert elapsed < 10.0, f"deadline did not fire; elapsed={elapsed:.1f}s"

    # State must be FAILED.
    assert end_state == MissionState.FAILED

    # The MissionFailed event must carry reason="attempts_timed_out".
    events = await manager.store.events_for_mission(mid)
    failed_payloads = [
        e.payload for e in events if e.payload.event_type == "MissionFailed"
    ]
    assert len(failed_payloads) == 1, (
        f"expected exactly one MissionFailed event, got {len(failed_payloads)}"
    )
    assert failed_payloads[0].reason == "attempts_timed_out", (  # type: ignore[attr-defined]
        f"expected reason='attempts_timed_out', got {failed_payloads[0].reason!r}"  # type: ignore[attr-defined]
    )


@pytest.mark.asyncio
async def test_fast_mission_not_affected_by_deadline(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Task 2.2: the deadline wrap must not disturb a mission that completes
    normally well within the deadline. A large deadline (default 2400s) and a
    fast FakeWorker must still yield MissionState.APPROVED.
    """
    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(manager=manager, tmp_path=tmp_path, critic=critic)
    # Leave _mission_deadline_s at the default (2400s) — no injection needed.

    mid = await manager.dispatch(prompt="build palindrome function fast")
    end_state = await k.run_mission(mid)

    assert end_state == MissionState.APPROVED

    events = await manager.store.events_for_mission(mid)
    approved = [e.payload for e in events if e.payload.event_type == "MissionApproved"]
    assert len(approved) == 1, "expected one MissionApproved event on the happy path"


# --- 2026-05-28 sub-agent mass-failure resilience (OAuth-contention) ---


@dataclass
class _FakeTimeoutEvent:
    """A worker result event flagged is_error with a timeout message — what
    ClaudeDirectWorker now yields when ``claude`` produces zero output within
    the first-output gate (Claude Max OAuth contention)."""

    result: str = (
        "ClaudeDirectWorker: subprocess produced no output within 120s startup "
        "timeout (claude emitted zero bytes — likely Claude Max OAuth "
        "contention); killed for retry"
    )
    type: str = "result"
    is_error: bool = True
    session_id: str | None = "to-session"


class _TimeoutThenOkWorker:
    """Yields a timeout-error event on the first spawn, a normal result on the
    second — models a transient hang that clears once the contention is gone."""

    cli = "claude"

    def __init__(self) -> None:
        self.last_pid = 222
        self.spawn_calls: list[dict[str, Any]] = []

    async def spawn(
        self, prompt: str, *, worktree: Path, env: dict[str, str], job: Any,
        worker_id: str, log_dir: Path, **kwargs: Any,
    ) -> AsyncIterator[Any]:
        self.spawn_calls.append(dict(kwargs))
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "stream.jsonl").write_text(
            '{"type":"result","subtype":"success"}\n', encoding="utf-8"
        )
        if len(self.spawn_calls) == 1:
            yield _FakeTimeoutEvent()
        else:
            yield _FakeWorkerEvent()


@pytest.mark.asyncio
async def test_worker_timeout_retries_then_approves(
    manager: MissionManager, tmp_path: Path
) -> None:
    """A transient worker timeout (zero output) on iter0 must RETRY on a fresh
    spawn instead of failing the mission. Regression for the 2026-05-28
    OAuth-contention mass-failure (95 task_error / 630s hangs)."""
    worker = _TimeoutThenOkWorker()
    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(
        manager=manager, tmp_path=tmp_path, critic=critic,
        worker_factory_fn=lambda step: worker,
    )
    mid = await manager.dispatch(prompt="build X after a transient timeout")

    end = await k.run_mission(mid)
    assert end == MissionState.APPROVED
    # iter0 timed out -> retried; iter1 succeeded.
    assert len(worker.spawn_calls) == 2
    # The critic only ran on the iteration that actually produced output.
    assert len(critic.calls) == 1


@pytest.mark.asyncio
async def test_worker_timeout_every_iteration_fails_with_timeout_reason(
    manager: MissionManager, tmp_path: Path
) -> None:
    """If the worker times out on EVERY iteration, the mission fails with
    reason='timeout' (not the old mislabel 'user') after exhausting retries."""

    class _AlwaysTimeoutWorker:
        cli = "claude"

        def __init__(self) -> None:
            self.last_pid = 333
            self.spawn_calls: list[dict[str, Any]] = []

        async def spawn(self, prompt, *, worktree, env, job, worker_id, log_dir, **kw):  # type: ignore[no-untyped-def]
            self.spawn_calls.append(dict(kw))
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "stream.jsonl").write_text("", encoding="utf-8")
            yield _FakeTimeoutEvent()

    worker = _AlwaysTimeoutWorker()
    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(
        manager=manager, tmp_path=tmp_path, critic=critic,
        worker_factory_fn=lambda step: worker,
    )
    mid = await manager.dispatch(prompt="task that always hangs")

    end = await k.run_mission(mid)
    assert end == MissionState.FAILED
    # Retried up to MAX_CRITIC_LOOPS times.
    assert len(worker.spawn_calls) == 3
    events = await manager.store.events_for_mission(mid)
    killed = [e.payload for e in events if e.payload.event_type == "WorkerKilled"]
    assert killed
    assert killed[-1].reason == "timeout"  # type: ignore[attr-defined]
    # The MISSION-level failure reason must be honest about the timeout too —
    # NOT the generic 'task_error' (the "worker aborted" voice phrase) that a
    # real worker crash produces. Live deep-dive 2026-06-07 (mission 019ea1da):
    # a Computer-Use mission whose final iteration hit the 630s wall-clock cap
    # was mislabeled task_error, so the user heard a worker-abort phrase for a
    # mission they never consciously spawned. A worker that ran out of time on
    # every attempt is a timeout; the voice layer must say so.
    failed = [e.payload for e in events if e.payload.event_type == "MissionFailed"]
    assert len(failed) == 1
    assert failed[0].reason == "attempts_timed_out", (  # type: ignore[attr-defined]
        f"expected attempts_timed_out, got {failed[0].reason!r}"  # type: ignore[attr-defined]
    )


# --- 2026-07-06 sub-agent auth-failure resilience (expired OAuth token) ---


@dataclass
class _FakeAuthErrorEvent:
    """The verbatim terminal event of the 2026-07-06 incident: the claude CLI
    ran on an expired OAuth token (injected without an expiresAt check) and
    died before any work."""

    result: str = (
        "Failed to authenticate. API Error: 401 Invalid authentication "
        "credentials"
    )
    type: str = "result"
    is_error: bool = True
    session_id: str | None = "auth-session"


class _AuthErrorWorker:
    """Models a worker whose provider auth is dead — every spawn 401s."""

    cli = "claude"

    def __init__(self) -> None:
        self.last_pid = 444
        self.spawn_calls: list[dict[str, Any]] = []

    async def spawn(self, prompt, *, worktree, env, job, worker_id, log_dir, **kw):  # type: ignore[no-untyped-def]
        self.spawn_calls.append(dict(kw))
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "stream.jsonl").write_text("", encoding="utf-8")
        yield _FakeAuthErrorEvent()


@pytest.mark.asyncio
async def test_worker_auth_error_retries_with_fresh_factory_pick(
    manager: MissionManager, tmp_path: Path
) -> None:
    """An auth-failure (401) on iter0 must RETRY — the worker factory is
    consulted again and (with the provider flagged auth-dead) picks a
    DIFFERENT family, so the mission completes instead of failing terminally.
    Regression for 2026-07-06: missions 019f36e5 + 019f38b1 died task_error
    while a healthy codex login and OpenRouter key were available (AP-22)."""
    dead_worker = _AuthErrorWorker()
    healthy_worker = FakeWorker()
    factory_calls: list[int] = []

    def _factory(step):  # type: ignore[no-untyped-def]
        factory_calls.append(1)
        return dead_worker if len(factory_calls) == 1 else healthy_worker

    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(
        manager=manager, tmp_path=tmp_path, critic=critic,
        worker_factory_fn=_factory,
    )
    mid = await manager.dispatch(prompt="build X across a dead provider")

    end = await k.run_mission(mid)
    assert end == MissionState.APPROVED
    # iter0 401'd -> retried on a fresh factory pick; iter1 succeeded.
    assert len(factory_calls) == 2
    assert len(dead_worker.spawn_calls) == 1
    assert len(healthy_worker.spawn_calls) == 1
    # The critic only graded the iteration that actually produced output.
    assert len(critic.calls) == 1


@pytest.mark.asyncio
async def test_worker_auth_error_every_iteration_fails_honestly(
    manager: MissionManager, tmp_path: Path
) -> None:
    """If EVERY family 401s, the mission fails honestly after the retry cap —
    never an infinite loop, and the kill reason stays worker_error."""
    worker = _AuthErrorWorker()
    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(
        manager=manager, tmp_path=tmp_path, critic=critic,
        worker_factory_fn=lambda step: worker,
    )
    mid = await manager.dispatch(prompt="task on an all-dead credential set")

    end = await k.run_mission(mid)
    assert end == MissionState.FAILED
    assert len(worker.spawn_calls) == 3  # retried up to MAX_CRITIC_LOOPS
    events = await manager.store.events_for_mission(mid)
    killed = [e.payload for e in events if e.payload.event_type == "WorkerKilled"]
    assert killed
    assert killed[-1].reason == "worker_error"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_mission_failed_carries_error_classification(
    manager: MissionManager, tmp_path: Path
) -> None:
    """The 2026-07-06 gap: MissionFailed.error_class was always None, so the
    UI/voice could not name the cause. An all-401 mission must now carry
    error_class="provider_auth", the truncated upstream text, and the
    provider slug of the worker that failed."""
    worker = _AuthErrorWorker()
    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(
        manager=manager, tmp_path=tmp_path, critic=critic,
        worker_factory_fn=lambda step: worker,
    )
    mid = await manager.dispatch(prompt="task on a dead credential")

    end = await k.run_mission(mid)
    assert end == MissionState.FAILED

    events = await manager.store.events_for_mission(mid)
    failed = [e.payload for e in events if e.payload.event_type == "MissionFailed"]
    assert len(failed) == 1
    assert failed[0].error_class == "provider_auth"
    assert "401" in (failed[0].error_detail or "")
    assert failed[0].failed_provider == "claude"

    killed = [e.payload for e in events if e.payload.event_type == "WorkerKilled"]
    assert killed
    assert killed[-1].error_class == "provider_auth"
    assert "401" in (killed[-1].error_detail or "")


@pytest.mark.asyncio
async def test_critic_rejection_does_not_inherit_stale_worker_error_context(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Review finding 2026-07-07: a worker error that left a real diff falls
    through to critic grading; if the critic then REJECTS, the mission's
    failure cause is the critic verdict — it must NOT be stamped with the
    survived worker error's error_class (stale-context misattribution)."""

    class _AuthErrorWithDiffWorker:
        cli = "claude"

        def __init__(self) -> None:
            self.last_pid = 555
            self.spawn_calls: list[dict[str, Any]] = []

        async def spawn(self, prompt, *, worktree, env, job, worker_id, log_dir, **kw):  # type: ignore[no-untyped-def]
            self.spawn_calls.append(dict(kw))
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "stream.jsonl").write_text("", encoding="utf-8")
            # Leave a real file so the external-write augmentation produces a
            # non-empty diff — mirroring "worker did real work, then 401'd".
            yield _FakeAuthErrorEvent()

    worker = _AuthErrorWithDiffWorker()
    critic = FakeCriticRunner(_make_reject_verdict())
    k = _make_kontrollierer(
        manager=manager, tmp_path=tmp_path, critic=critic,
        worker_factory_fn=lambda step: worker,
    )
    # Force the graded path: patch the diff capture so the worker error falls
    # through to the critic instead of retrying (non-empty diff).
    k._capture_diff = lambda wt: "diff --git a/x b/x\n+real work\n"  # type: ignore[method-assign]
    mid = await manager.dispatch(prompt="work then die on auth")

    end = await k.run_mission(mid)
    assert end == MissionState.FAILED
    events = await manager.store.events_for_mission(mid)
    failed = [e.payload for e in events if e.payload.event_type == "MissionFailed"]
    assert len(failed) == 1
    assert failed[0].reason == "critic_rejected"
    # The honest cause is the critic verdict — no stale provider_auth stamp.
    assert failed[0].error_class is None
    assert failed[0].failed_provider is None


@pytest.mark.asyncio
async def test_critic_timeout_retries_then_approves(
    manager: MissionManager, tmp_path: Path
) -> None:
    """A single transient CriticTimeout must NOT be immediately fatal — the
    critic is retried on a fresh iteration. Regression for the 22
    critic_unavailable failures (critic shells out to the same Max OAuth)."""
    from jarvis.missions.critic.verdict import CriticTimeout

    class _TimeoutThenApproveCritic:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def run(self, **kwargs: Any) -> CriticVerdict:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise CriticTimeout("transient overload")
            return _make_approve_verdict()

    critic = _TimeoutThenApproveCritic()
    k = _make_kontrollierer(
        manager=manager, tmp_path=tmp_path, critic=critic,  # type: ignore[arg-type]
    )
    mid = await manager.dispatch(prompt="build X, critic blips once")

    end = await k.run_mission(mid)
    assert end == MissionState.APPROVED
    # iter0 critic timed out (retry), iter1 critic approved.
    assert len(critic.calls) == 2


class _AlwaysTimeoutWorkerWithDiff:
    """Yields a timeout-error event on every spawn, simulating a long Git/build
    task that completed its file writes and THEN hit the wall-clock cap. The
    on-disk work is modelled via a monkeypatched ``_capture_diff`` in the test
    (the worker itself can't write a real git diff in the fake worktree)."""

    cli = "claude"

    def __init__(self) -> None:
        self.last_pid = 555
        self.spawn_calls: list[dict[str, Any]] = []

    async def spawn(self, prompt, *, worktree, env, job, worker_id, log_dir, **kw):  # type: ignore[no-untyped-def]
        self.spawn_calls.append(dict(kw))
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "stream.jsonl").write_text(
            '{"type":"result"}\n', encoding="utf-8"
        )
        yield _FakeTimeoutEvent()


@pytest.mark.asyncio
async def test_timeout_with_real_diff_is_graded_not_discarded(
    manager: MissionManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker killed by the wall-clock cap that still left real files on disk
    (non-empty diff) must be GRADED by the critic, not discarded as task_error.

    Live false-negative E1: a long Git/build task ("open PRs", "commit and
    push") completes its writes, then ``communicate()`` hits the 630s cap and
    the worker is killed. The old loop returned TaskOutcome.ERROR immediately,
    throwing the real on-disk work away and reporting task_error. The critic is
    the ground-truth judge — let it grade the partial deliverable instead."""
    worker = _AlwaysTimeoutWorkerWithDiff()
    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(
        manager=manager, tmp_path=tmp_path, critic=critic,
        worker_factory_fn=lambda step: worker,
    )
    # Simulate the worker having written a real file before the cap fired.
    monkeypatch.setattr(
        Kontrollierer,
        "_capture_diff",
        lambda self, wt: (
            "diff --git a/out.html b/out.html\n"
            "@@ -0,0 +1 @@\n"
            "+<html>built before the timeout</html>\n"
        ),
    )

    mid = await manager.dispatch(prompt="long build that times out after writing")
    end = await k.run_mission(mid)

    assert end == MissionState.APPROVED, (
        "a timeout-killed worker that produced a real diff must be graded "
        "by the critic, not failed as task_error"
    )
    # Graded on the very first iteration — no wasteful retry of a task that
    # already produced output.
    assert len(critic.calls) >= 1
    assert len(worker.spawn_calls) == 1


class _GitPushWorker:
    """Worker stub for a 'commit and push' task: empty worktree diff (the work
    is a remote ref update, not a file change) but a real, non-errored git-push
    tool_use in the stream — the dominant Git/GitHub critic_loop_exhausted
    false-negative shape."""

    cli = "claude"

    def __init__(self) -> None:
        self.last_pid = 666
        self.spawn_calls: list[dict[str, Any]] = []

    async def spawn(self, prompt, *, worktree, env, job, worker_id, log_dir, **kw):  # type: ignore[no-untyped-def]
        import json as _json

        self.spawn_calls.append(dict(kw))
        log_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "g1", "name": "Bash",
                 "input": {"command": "git push origin main"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "g1",
                 "content": "To github.com:me/repo.git\n   abc..def  main -> main"}]}},
            {"type": "result", "subtype": "success", "result": "Pushed."},
        ]
        (log_dir / "stream.jsonl").write_text(
            "\n".join(_json.dumps(line) for line in lines), encoding="utf-8"
        )
        yield _FakeWorkerEvent()


@pytest.mark.asyncio
async def test_git_push_evidence_reaches_critic_as_nonempty_diff(
    manager: MissionManager, tmp_path: Path
) -> None:
    """A 'commit and push' task leaves an empty worktree diff but a verified
    git-push tool call. The critic must SEE a non-empty diff carrying the
    command evidence (so its empty-diff GROUND-TRUTH veto no longer fires) and
    the mission can succeed instead of failing critic_loop_exhausted."""
    from jarvis.missions.kontrollierer.orchestrator import _real_diff_is_empty

    worker = _GitPushWorker()
    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(
        manager=manager, tmp_path=tmp_path, critic=critic,
        worker_factory_fn=lambda step: worker,
    )
    mid = await manager.dispatch(prompt="commit and push to main")
    end = await k.run_mission(mid)

    assert end == MissionState.APPROVED
    # The critic was called with a diff that carries the command evidence.
    assert len(critic.calls) == 1
    reviewed_diff = critic.calls[0]["worker_diff"]
    assert "verified-command-execution" in reviewed_diff
    assert "main -> main" in reviewed_diff
    # And that augmented diff is NOT considered empty (no blind veto).
    assert not _real_diff_is_empty(reviewed_diff)


@pytest.mark.asyncio
async def test_empty_task_outcomes_is_not_approved(
    manager: MissionManager, tmp_path: Path
) -> None:
    """MAJOR-1 guard: when task_outcomes is empty after the TaskGroup exits,
    the guard must fail the mission with reason='task_error' instead of letting
    all(...) over an empty list return vacuously True and silently APPROVE.

    MissionPlan enforces min_length=1 on steps, so we bypass Pydantic validation
    via model_construct to inject a genuine zero-step plan — the exact degenerate
    input the guard is designed to catch.
    """
    # Bypass MissionPlan's min_length=1 validator to produce a zero-step plan.
    zero_step_plan = MissionPlan.model_construct(
        steps=[],
        n_workers=1,
        expected_output="nothing — degenerate zero-step plan",
    )

    k = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=FakeCriticRunner(),  # never called — plan has no steps
        decomposer_plan=zero_step_plan,
    )

    mid = await manager.dispatch(prompt="degenerate zero-step plan")
    end_state = await k.run_mission(mid)

    # Must be FAILED, not APPROVED (the vacuous-truth false-APPROVE bug).
    assert end_state == MissionState.FAILED

    events = await manager.store.events_for_mission(mid)
    failed_payloads = [
        e.payload for e in events if e.payload.event_type == "MissionFailed"
    ]
    assert len(failed_payloads) == 1, (
        f"expected exactly one MissionFailed event, got {len(failed_payloads)}"
    )
    assert failed_payloads[0].reason == "task_error", (  # type: ignore[attr-defined]
        f"expected reason='task_error', got {failed_payloads[0].reason!r}"  # type: ignore[attr-defined]
    )


@pytest.mark.asyncio
async def test_default_max_concurrent_missions_is_one(
    manager: MissionManager, tmp_path: Path
) -> None:
    """The heavy claude phase is serialised by default (OAuth-contention guard,
    2026-05-28). Two concurrent claude-direct missions saturate Max OAuth."""
    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(manager=manager, tmp_path=tmp_path, critic=critic)
    # Semaphore initial value == configured concurrency cap.
    assert k._mission_sem._value == 1  # noqa: SLF001 — asserting the default


# ---------------------------------------------------------------------------
# Task 3.1 — desktop-launch evidence channel
# ---------------------------------------------------------------------------


class _DesktopLaunchWorker:
    """Worker stub for an "open Explorer" task: empty worktree diff (the
    deliverable is a running process) but a real, non-errored 'start
    explorer.exe' Bash tool_use in the stream — mirrors _GitPushWorker for the
    desktop-launch false-negative shape."""

    cli = "claude"

    def __init__(self) -> None:
        self.last_pid = 777
        self.spawn_calls: list[dict[str, Any]] = []

    async def spawn(self, prompt, *, worktree, env, job, worker_id, log_dir, **kw):  # type: ignore[no-untyped-def]
        import json as _json

        self.spawn_calls.append(dict(kw))
        log_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "de1", "name": "Bash",
                 "input": {"command": "start explorer.exe"}}]}},
            # Silent detached spawn — empty stdout is success, not failure.
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "de1",
                 "content": ""}]}},
            {"type": "result", "subtype": "success", "result": "Opened Explorer."},
        ]
        (log_dir / "stream.jsonl").write_text(
            "\n".join(_json.dumps(line) for line in lines), encoding="utf-8"
        )
        yield _FakeWorkerEvent()


# ---------------------------------------------------------------------------
# Live WorkerProgress emission (transparency: long-but-healthy missions must
# show what they are doing so the user doesn't restart them mid-run, 2026-06-15)
# ---------------------------------------------------------------------------


class _ToolUseProgressWorker:
    """Yields one assistant tool_use event and one assistant text event WHILE
    running (before the terminal result), modelling an incrementally-streaming
    worker. The orchestrator must translate that activity into WorkerProgress
    events on the bus/store so the UI ReasoningPanel can render live progress."""

    cli = "claude"

    def __init__(self) -> None:
        self.last_pid = 4242

    async def spawn(self, prompt, *, worktree, env, job, worker_id, log_dir, **kw):  # type: ignore[no-untyped-def]
        from jarvis.missions.workers.stream_consumer import (
            ClaudeAssistantMessage,
            ClaudeResult,
        )

        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "stream.jsonl").write_text(
            '{"type":"result","subtype":"success"}\n', encoding="utf-8"
        )
        yield ClaudeAssistantMessage(
            message={
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Bash",
                     "input": {"command": "git push origin main"}}
                ],
            },
            session_id="s1",
        )
        yield ClaudeAssistantMessage(
            message={
                "role": "assistant",
                "content": [{"type": "text", "text": "Analysing the repository layout"}],
            },
            session_id="s1",
        )
        yield ClaudeResult(
            subtype="success", is_error=False, result="done", session_id="s1"
        )


@pytest.mark.asyncio
async def test_worker_progress_events_emitted_during_run(
    manager: MissionManager, tmp_path: Path
) -> None:
    """The orchestrator must emit WorkerProgress events as the worker streams
    activity, so the (already-built, dormant) UI ReasoningPanel lights up. The
    note must carry a human-readable description of the latest activity."""
    critic = FakeCriticRunner(_make_approve_verdict())
    worker = _ToolUseProgressWorker()
    k = _make_kontrollierer(
        manager=manager, tmp_path=tmp_path, critic=critic,
        worker_factory_fn=lambda step: worker,
    )
    mid = await manager.dispatch(prompt="push to main and analyse")
    end = await k.run_mission(mid)
    assert end == MissionState.APPROVED

    events = await manager.store.events_for_mission(mid)
    progress = [
        e.payload for e in events if e.payload.event_type == "WorkerProgress"
    ]
    assert progress, "expected at least one WorkerProgress event during the run"
    # The note describes the latest worker activity (tool name / text snippet).
    notes = " | ".join((p.note or "") for p in progress)  # type: ignore[attr-defined]
    assert "Bash" in notes or "git push" in notes, (
        f"expected a tool-use note in WorkerProgress, got: {notes!r}"
    )
    # Progress is attributed to the worker that produced it.
    assert all(p.worker_id for p in progress)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_swarm_phase_output_is_logged_for_mission_run(
    manager: MissionManager, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The mission log should show the human-readable team flow from the Bridge
    runbook: coordinator -> scouts -> builders."""
    critic = FakeCriticRunner(_make_approve_verdict(), _make_approve_verdict())
    plan = MissionPlan(
        steps=[
            Step(slug="research", prompt="research current AI news", needs_repo=False),
            Step(slug="write-report", prompt="write the report", needs_repo=False),
        ],
        n_workers=2,
        expected_output="research report",
    )
    k = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,
        decomposer_plan=plan,
        worker_factory_fn=lambda step: FakeWorker(),
    )
    mid = await manager.dispatch(prompt="deep research")

    with caplog.at_level(logging.INFO, logger="jarvis.missions.kontrollierer.orchestrator"):
        end = await k.run_mission(mid)

    assert end == MissionState.APPROVED
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "coordinator -> scouts -> builders" in log_text
    assert mid in log_text
    assert "research" in log_text
    assert "write-report" in log_text


@pytest.mark.asyncio
async def test_desktop_launch_evidence_reaches_critic_as_nonempty_diff(
    manager: MissionManager, tmp_path: Path
) -> None:
    """An 'open Explorer' task leaves an empty worktree diff but a verified
    desktop-launch tool call. The critic must SEE a non-empty diff carrying
    the desktop-action evidence (so its empty-diff GROUND-TRUTH veto no longer
    fires) and the mission can succeed instead of failing critic_loop_exhausted.
    Mirrors test_git_push_evidence_reaches_critic_as_nonempty_diff for the
    desktop-launch shape (Task 3.1)."""
    from jarvis.missions.kontrollierer.orchestrator import _real_diff_is_empty

    worker = _DesktopLaunchWorker()
    critic = FakeCriticRunner(_make_approve_verdict())
    k = _make_kontrollierer(
        manager=manager, tmp_path=tmp_path, critic=critic,
        worker_factory_fn=lambda step: worker,
    )
    mid = await manager.dispatch(prompt="open Windows Explorer")
    end = await k.run_mission(mid)

    assert end == MissionState.APPROVED
    # The critic was called with a diff that carries the desktop-launch evidence.
    assert len(critic.calls) == 1
    reviewed_diff = critic.calls[0]["worker_diff"]
    assert "verified-desktop-launch" in reviewed_diff
    assert "start explorer.exe" in reviewed_diff
    # The sentinel string for a silent detached spawn must be present.
    assert "(command succeeded; no output captured)" in reviewed_diff
    # And that augmented diff is NOT considered empty (no blind veto).
    assert not _real_diff_is_empty(reviewed_diff)


@pytest.mark.asyncio
async def test_worker_prompt_carries_artifact_language_directive(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Every dispatched worker prompt leads with the English-artifact directive.

    Root-cause guard (2026-06-22): a German mission used to hand the worker a
    purely German prompt, so the worker wrote German code. The orchestrator must
    prepend ARTIFACT_LANGUAGE_DIRECTIVE to the worker prompt for EVERY step,
    while keeping the step instruction intact (the directive is additive)."""
    worker = FakeWorker()
    critic = FakeCriticRunner(_make_approve_verdict())
    plan = MissionPlan(
        steps=[Step(slug="html", prompt="Erstelle eine HTML-Seite namens test.html")],
        n_workers=1,
        expected_output="x",
    )
    k = _make_kontrollierer(
        manager=manager,
        tmp_path=tmp_path,
        critic=critic,
        worker_factory_fn=lambda step: worker,
        decomposer_plan=plan,
    )
    mid = await manager.dispatch(prompt="Erstelle eine HTML-Seite namens test.html")
    await k.run_mission(mid)

    assert worker.spawn_calls, "worker was never spawned"
    prompt = worker.spawn_calls[0]["prompt"]
    assert ARTIFACT_LANGUAGE_DIRECTIVE in prompt
    # The step instruction survives — the directive is prepended, not a replacement.
    assert "test.html" in prompt
