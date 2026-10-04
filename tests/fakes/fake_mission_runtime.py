"""Fakes for driving ``Kontrollierer`` end-to-end without subprocesses.

Workers yield one synthetic ``result`` event, the critic approves, worktrees
are plain directories that are really created and removed. Per AGENTS.md:
real fakes, never ``unittest.mock``.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.missions.budget import BudgetTracker
from jarvis.missions.critic.verdict import REQUIRED_AXES, CriticAxis, CriticVerdict
from jarvis.missions.kontrollierer.decomposer import MissionPlan, Step
from jarvis.missions.kontrollierer.orchestrator import Kontrollierer
from jarvis.missions.manager import MissionManager

SESSION_LIMIT_ERROR = "You've hit your session limit · resets 11:10pm"


@dataclass
class ResultEvent:
    """Stand-in for a worker's terminal stream-json ``result`` record."""

    type: str = "result"
    cost_usd: float = 0.0
    total_tokens: int = 0
    session_id: str | None = "s"
    is_error: bool = False
    result: str = ""
    subtype: str = "success"


def _write_log(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "stream.jsonl").write_text("{}\n", encoding="utf-8")


class FakeMissionWorker:
    """One spawn = one terminal event. Records every spawn.

    ``quota`` makes each spawn end on a spent subscription window;
    ``writes`` maps relative paths to content the spawn writes into its
    workspace before finishing.
    """

    cli = "claude"

    def __init__(
        self,
        *,
        family: str = "claude",
        quota: bool = False,
        writes: dict[str, str] | None = None,
        on_spawn: Callable[[str, Path], None] | None = None,
    ) -> None:
        self.family = family
        self.quota = quota
        self.writes = dict(writes or {})
        self.on_spawn = on_spawn
        self.last_pid = 4242
        self.prompts: list[str] = []

    async def spawn(
        self, prompt: str, *, worktree: Path, log_dir: Path, **_k: Any
    ) -> AsyncIterator[Any]:
        self.prompts.append(prompt)
        if self.on_spawn is not None:
            self.on_spawn(prompt, worktree)
        for rel, text in self.writes.items():
            target = worktree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        _write_log(log_dir)
        if self.quota:
            yield ResultEvent(
                is_error=True, subtype="error_during_execution", result=SESSION_LIMIT_ERROR
            )
        else:
            yield ResultEvent()


class ApprovingCritic:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, **_k: Any) -> CriticVerdict:
        self.calls += 1
        return CriticVerdict(
            verdict="approve",
            axes={ax: CriticAxis(status="pass", evidence=["x:1"]) for ax in REQUIRED_AXES},
            issues=[],
            correction_instruction="",
            summary="ok",
            summary_de="ok",
            confidence=0.9,
            suggested_next_action="accept",
        )


class FixedPlanDecomposer:
    def __init__(self, plan: MissionPlan) -> None:
        self.plan = plan
        self.calls = 0

    async def decompose(self, _prompt: str) -> MissionPlan:
        self.calls += 1
        return self.plan


class NoopJob:
    async def __aenter__(self) -> NoopJob:
        return self

    async def __aexit__(self, *_a: object) -> None:
        return None

    def assign(self, _pid: int) -> None:
        return None


class DirWorktrees:
    """A fresh directory per ``create`` and a real delete on ``remove`` — so a
    resumed step can only see earlier work that was explicitly restored."""

    def __init__(self, base: Path) -> None:
        self._base = base
        self._n = 0

    def create(self, *, task_id: str, **_k: Any) -> Path:
        self._n += 1
        wt = self._base / f"{task_id[:13]}-{self._n}"
        wt.mkdir(parents=True, exist_ok=True)
        return wt

    def remove(self, path: Path, **_k: Any) -> None:
        shutil.rmtree(path, ignore_errors=True)


def make_kontrollierer(
    manager: MissionManager,
    tmp_path: Path,
    plan: MissionPlan,
    factory: Callable[[Step], Any],
    critic: Any | None = None,
) -> Kontrollierer:
    return Kontrollierer(
        manager=manager,
        decomposer=FixedPlanDecomposer(plan),  # type: ignore[arg-type]
        critic_runner=critic or ApprovingCritic(),  # type: ignore[arg-type]
        worktree_mgr=DirWorktrees(tmp_path / "worktrees"),  # type: ignore[arg-type]
        env_builder=lambda _p: {},
        budget=BudgetTracker(per_mission_usd=10.0, daily_usd=100.0),
        worker_factory=factory,
        job_factory=NoopJob,
        isolation_root=tmp_path / "missions",
    )
