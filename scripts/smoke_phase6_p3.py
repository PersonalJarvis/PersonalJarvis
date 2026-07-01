"""Smoke test Phase 3: Critic loop + Kontrollierer (mocked, no API).

Runs end-to-end without pytest, checking the acceptance criteria from
docs/phase6-prompt-chain.md for Phase 3 with fakes instead of real subprocesses.
Exit 0 on success, exit 1 on failure.

What is verified:
1. Decomposer (heuristic path) returns a 1-step plan for a short prompt.
2. Kontrollierer runs the worker+critic loop:
   - Iter 0: Critic returns revise -> reflection persisted -> iter 1 starts.
   - Iter 1: Critic returns approve -> MissionApproved.
3. Reflections.md has exactly 1 entry (from iter 0).
4. State machine: PENDING -> RUNNING -> CRITIQUING -> APPROVED.
5. CriticVerdictReady events: 2 (iter 0 revise + iter 1 approve).
6. MissionApproved event on the bus.
7. Cost accumulated in BudgetTracker (mocked $0.05 + $0.05 = $0.10).
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import MagicMock

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

# Repo root in sys.path so `from jarvis.missions...` works
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.missions.budget import BudgetTracker  # noqa: E402
from jarvis.missions.critic.reflections import ReflectionMemory  # noqa: E402
from jarvis.missions.critic.verdict import (  # noqa: E402
    REQUIRED_AXES,
    CriticAxis,
    CriticVerdict,
)
from jarvis.missions.kontrollierer.decomposer import (  # noqa: E402
    MissionDecomposer,
    MissionPlan,
    Step,
)
from jarvis.missions.kontrollierer.orchestrator import Kontrollierer  # noqa: E402
from jarvis.missions.manager import MissionManager  # noqa: E402
from jarvis.missions.state_machine import MissionState  # noqa: E402

OK = "[OK]"
FAIL = "[FAIL]"


# --- Fakes ---


@dataclass
class _FakeWorkerEvent:
    type: str = "result"
    cost_usd: float = 0.05
    total_tokens: int = 1000
    session_id: str | None = "fake-session"


class FakeWorker:
    cli = "claude"

    def __init__(self) -> None:
        self.last_pid = 12345
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
        self.spawn_calls.append({"prompt": prompt, "worker_id": worker_id})
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "stream.jsonl").write_text(
            '{"type":"result","subtype":"success"}\n', encoding="utf-8"
        )
        yield _FakeWorkerEvent()


class FakeCriticRunner:
    def __init__(self, *verdicts: CriticVerdict) -> None:
        self._verdicts = list(verdicts)
        self._idx = 0
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> CriticVerdict:
        self.calls.append(kwargs)
        v = self._verdicts[self._idx]
        self._idx += 1
        return v


class FakeJobObject:
    async def __aenter__(self) -> "FakeJobObject":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def assign(self, pid: int) -> None:
        pass


class FakeWorktreeManager:
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


def _approve_verdict() -> CriticVerdict:
    return CriticVerdict(
        verdict="approve",
        axes={ax: CriticAxis(status="pass", evidence=["src/x.py:1"]) for ax in REQUIRED_AXES},
        issues=[],
        correction_instruction="",
        summary="ok",
        summary_de="ok",
        confidence=0.9,
        suggested_next_action="accept",
    )


def _revise_verdict(summary: str = "needs an edge case") -> CriticVerdict:
    return CriticVerdict(
        verdict="revise",
        axes={
            "correctness": CriticAxis(status="fail", evidence=["src/x.py:7"]),
            "completeness": CriticAxis(status="pass", evidence=["src/x.py:1"]),
            "side_effects": CriticAxis(status="pass", evidence=["src/x.py:1"]),
            "security": CriticAxis(status="pass", evidence=["src/x.py:1"]),
        },
        issues=[],
        correction_instruction="add empty-string handling",
        summary=summary,
        summary_de=summary,
        confidence=0.8,
        suggested_next_action="retry",
    )


# --- Smoke ---


async def smoke() -> int:
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "smoke_p3.db"

        # MissionManager + Decomposer
        mgr = MissionManager(db_path)
        await mgr.start()

        decomposer = MagicMock(spec=MissionDecomposer)

        async def _decompose(prompt: str) -> MissionPlan:
            return MissionPlan(
                steps=[Step(slug="palindrome", prompt=prompt)],
                n_workers=1,
                expected_output="palindrome function",
            )

        decomposer.decompose = _decompose  # type: ignore[method-assign]

        # Critic: iter 0 revise, iter 1 approve
        critic = FakeCriticRunner(_revise_verdict(), _approve_verdict())
        worker = FakeWorker()
        budget = BudgetTracker(per_mission_usd=10.0, daily_usd=100.0)
        # Production records cost via the event bus, not a direct .record() call
        # (the orchestrator deliberately does NOT call _budget.record() — see
        # orchestrator._run_iterations + init.py:256). Bind here so the
        # WorkerDraftReady cost_usd is accumulated, mirroring bootstrap_missions.
        budget.bind_to_event_bus(mgr.bus)

        kontrollierer = Kontrollierer(
            manager=mgr,
            decomposer=decomposer,
            critic_runner=critic,  # type: ignore[arg-type]
            worktree_mgr=FakeWorktreeManager(tmp_path / "worktrees"),  # type: ignore[arg-type]
            env_builder=lambda p: {},
            budget=budget,
            worker_factory=lambda step: worker,
            job_factory=FakeJobObject,
            isolation_root=tmp_path / "missions",
        )

        # Dispatch + run
        mid = await mgr.dispatch(prompt="Write is_palindrome(s: str) -> bool")
        end_state = await kontrollierer.run_mission(mid)

        # Check 1: end state
        if end_state != MissionState.APPROVED:
            failures.append(f"end_state {end_state} != APPROVED")
        else:
            print(f"{OK} mission ended APPROVED")

        # Check 2: mission header state
        view = await mgr.mission(mid)
        if view is None or view.state != MissionState.APPROVED:
            failures.append(f"mission state {view.state if view else None} != APPROVED")
        else:
            print(f"{OK} mission header is APPROVED")

        # Check 3: number of iterations
        if len(critic.calls) != 2:
            failures.append(f"critic.calls = {len(critic.calls)}, expected 2")
        else:
            print(f"{OK} critic was called exactly 2 times")

        if len(worker.spawn_calls) != 2:
            failures.append(f"worker.spawn_calls = {len(worker.spawn_calls)}, expected 2")
        else:
            print(f"{OK} worker spawned exactly 2 times")

        # Check 4: Reflections.md has 1 entry (from iter 0 revise).
        # mission_dir uses mission_id[:13] (BUG-LIVE-10 bumped the prefix from 8
        # to 13 chars — see orchestrator._run_mission + outputs_routes.py).
        mission_dir = tmp_path / "missions" / f"mission_{mid[:13]}"
        refl = ReflectionMemory(mission_dir)
        last = refl.last_n(5)
        if len(last) != 1:
            failures.append(f"reflections={len(last)}, expected 1")
        else:
            print(f"{OK} reflections.md has 1 entry (iter 0)")
            if "edge case" not in last[0].summary:
                failures.append(f"unexpected reflection summary: {last[0].summary!r}")
            else:
                print(f"{OK} reflection summary contains 'edge case'")

        # Check 5: state-machine transitions
        events = await mgr.store.events_for_mission(mid)
        sc_events = [e for e in events if e.payload.event_type == "MissionStateChanged"]
        transitions = [(e.payload.from_state, e.payload.to_state) for e in sc_events]  # type: ignore[attr-defined]
        for expected in [
            ("PENDING", "RUNNING"),
            ("RUNNING", "CRITIQUING"),
            ("CRITIQUING", "APPROVED"),
        ]:
            if expected not in transitions:
                failures.append(f"transition {expected} missing; got {transitions}")
        if all((exp in transitions) for exp in [
            ("PENDING", "RUNNING"),
            ("RUNNING", "CRITIQUING"),
            ("CRITIQUING", "APPROVED"),
        ]):
            print(f"{OK} state-machine: PENDING -> RUNNING -> CRITIQUING -> APPROVED")

        # Check 6: CriticVerdictReady events
        verdict_events = [e for e in events if e.payload.event_type == "CriticVerdictReady"]
        if len(verdict_events) != 2:
            failures.append(f"CriticVerdictReady events = {len(verdict_events)}, expected 2")
        else:
            print(f"{OK} 2x CriticVerdictReady on event store")

        # Check 7: MissionApproved event
        approved_events = [e for e in events if e.payload.event_type == "MissionApproved"]
        if len(approved_events) != 1:
            failures.append(f"MissionApproved events = {len(approved_events)}, expected 1")
        else:
            print(f"{OK} MissionApproved emitted")

        # Check 8: cost accumulated
        cost = budget.mission_cost(mid)
        if cost != 0.10:
            failures.append(f"budget.mission_cost = {cost}, expected 0.10")
        else:
            print(f"{OK} budget.mission_cost = $0.10 (2 iterations × $0.05)")

        # Check 9: MissionPlanReady
        plan_events = [e for e in events if e.payload.event_type == "MissionPlanReady"]
        if len(plan_events) != 1:
            failures.append(f"MissionPlanReady events = {len(plan_events)}, expected 1")
        else:
            print(f"{OK} MissionPlanReady emitted")

        # Check 10: worker prompt iter 1 contained the reflection block
        if "Prior Critic Feedback" not in worker.spawn_calls[1]["prompt"]:
            failures.append("worker prompt iter 1 does NOT contain 'Prior Critic Feedback'")
        else:
            print(f"{OK} worker iter 1 prompt has 'Prior Critic Feedback' block")

        await mgr.stop()

    print()
    if failures:
        print(f"{FAIL} {len(failures)} smoke-failures:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"{OK} ALL SMOKE CHECKS GREEN -- Phase 3 Critic-Loop ready.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke()))
