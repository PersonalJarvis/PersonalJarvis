"""Bounded second-layer sub-agent fan-out for mission workers.

A top-level mission worker (the sub-agent the user spawned) may fan out into
N parallel child sub-agents, each with its own prompt, and gets every child's
final answer back as text. This module is the runner that actually spawns and
drains those children; the model-facing entry is
``jarvis.plugins.tool.spawn_subagents.SpawnSubagentsTool`` (granted through
the ADR-0025 worker tool broker).

Exactly TWO layers, enforced structurally, never by prompt:

1. Only top-level mission workers receive the ``spawn_subagents`` grant in
   their broker binding (``jarvis/missions/init.py``).
2. Children spawned here get a knowledge-only capability inventory WITHOUT
   the fan-out tool — the broker refuses the name as "tool not granted".
3. Child worker ids carry the ``::sub`` marker and the tool refuses any call
   whose calling worker_id contains it (defense in depth).

Children are WORKERS, not missions: they run through the same worker factory
and provider routing as the parent (a Claude parent fans out Claude clones, a
Codex parent Codex clones, …), each in its own plain subdirectory under the
mission dir — deliberately NOT the parent's git worktree, so ten parallel
children can never trample one shared checkout. The child's deliverable is its
final answer text (plus whatever files it wrote in its own directory, whose
path the parent receives); the parent integrates results in its worktree.

Board + budget visibility ride the existing event vocabulary: each child
publishes ``WorkerSpawned`` / ``WorkerDraftReady`` / ``WorkerKilled`` on the
mission event store, so the {name}-Agents board shows the children under the
mission node and the ``BudgetTracker`` (subscribed to ``WorkerDraftReady``)
counts their cost against the mission budget with zero extra wiring.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .events import (
    EventEnvelope,
    WorkerDraftReady,
    WorkerKilled,
    WorkerSpawned,
    now_ms,
)
from .stream_evidence import extract_stream_evidence

logger = logging.getLogger(__name__)

#: Marker embedded in every child worker id. The fan-out tool refuses callers
#: whose worker_id contains it — a child can never fan out again (layer wall).
SUBAGENT_WORKER_MARKER = "::sub"

#: How many children may RUN at the same time. The total per mission is
#: budgeted by the tool; this only bounds simultaneous subprocesses so a
#: user-authorized larger fan-out cannot fork-bomb the host.
_MAX_CONCURRENT_CHILDREN = 10

#: Per-child wall-clock defaults/bounds (seconds). The child timeout is a
#: worker-internal budget (same contract as the orchestrator's iteration
#: timeouts); the outer ``asyncio.timeout`` below is the hard backstop.
DEFAULT_CHILD_TIMEOUT_S = 300
MIN_CHILD_TIMEOUT_S = 60
MAX_CHILD_TIMEOUT_S = 900
_CHILD_TIMEOUT_GRACE_S = 120.0

#: Cap for a single child's answer text inside the aggregated tool result.
_MAX_CHILD_ANSWER_CHARS = 40_000


@dataclass(frozen=True, slots=True)
class SubagentResult:
    """One child's terminal outcome, safe to hand back to the parent worker."""

    index: int
    ok: bool
    answer: str
    workspace: str
    error: str = ""
    duration_s: float = 0.0
    cost_usd: float = 0.0
    tokens_used: int = 0


class SubagentFanoutRunner:
    """Spawns bounded, parallel child workers for one mission.

    Wired once at mission bootstrap (``bootstrap_missions``) with the SAME
    worker factory / env builder / job factory the Kontrollierer uses, so a
    child runs on exactly the provider routing the parent did.

    ``worker_factory(step, inventory)`` must accept the capability-inventory
    override — the runner passes a knowledge-only inventory WITHOUT the
    fan-out tool, which is what makes the two-layer wall structural.
    """

    def __init__(
        self,
        *,
        worker_factory: Callable[..., Any],
        env_builder: Callable[[Path], dict[str, str]],
        job_factory: Callable[[], Any],
        isolation_root: Path,
        manager: Any,
        child_inventory_builder: Callable[[str], Any],
    ) -> None:
        self._worker_factory = worker_factory
        self._env_builder = env_builder
        self._job_factory = job_factory
        self._isolation_root = Path(isolation_root)
        self._manager = manager
        self._child_inventory_builder = child_inventory_builder

    async def run(
        self,
        *,
        mission_id: str,
        prompts: list[str],
        timeout_s: int = DEFAULT_CHILD_TIMEOUT_S,
    ) -> list[SubagentResult]:
        """Run one bounded fan-out round; never raises.

        Every child failure is contained in its own :class:`SubagentResult`
        so one crashed clone can never take down the siblings or the parent's
        tool call.
        """
        timeout = max(MIN_CHILD_TIMEOUT_S, min(int(timeout_s), MAX_CHILD_TIMEOUT_S))
        mission_dir = self._isolation_root / f"mission_{mission_id[:13]}"
        fanout_root = mission_dir / "subagents"
        sem = asyncio.Semaphore(_MAX_CONCURRENT_CHILDREN)
        existing = self._next_child_ordinal(fanout_root)

        async def _guarded(index: int, prompt: str) -> SubagentResult:
            async with sem:
                try:
                    return await self._run_child(
                        mission_id=mission_id,
                        mission_dir=mission_dir,
                        ordinal=existing + index,
                        prompt=prompt,
                        timeout_s=timeout,
                    )
                except Exception as exc:  # noqa: BLE001 - one child stays one child
                    logger.exception(
                        "Mission %s: sub-agent %d crashed outside the worker",
                        mission_id,
                        index,
                    )
                    return SubagentResult(
                        index=index,
                        ok=False,
                        answer="",
                        workspace="",
                        error=f"{type(exc).__name__}: {exc}"[:300],
                    )

        results = await asyncio.gather(
            *(_guarded(i, p) for i, p in enumerate(prompts))
        )
        return list(results)

    @staticmethod
    def _next_child_ordinal(fanout_root: Path) -> int:
        """First free ``sub-NN`` ordinal, so repeated rounds never collide."""
        try:
            taken = [
                int(p.name[4:])
                for p in fanout_root.iterdir()
                if p.is_dir() and p.name.startswith("sub-") and p.name[4:].isdigit()
            ]
        except OSError:
            return 0
        return (max(taken) + 1) if taken else 0

    async def _run_child(
        self,
        *,
        mission_id: str,
        mission_dir: Path,
        ordinal: int,
        prompt: str,
        timeout_s: int,
    ) -> SubagentResult:
        from .ids import uuid7_str
        from .kontrollierer.decomposer import Step
        from .kontrollierer.worker_prompt import compose_worker_prompt
        from .workers.worker_tool_broker import EmptyWorkerToolBrokerBinding

        started = asyncio.get_running_loop().time()
        workspace = mission_dir / "subagents" / f"sub-{ordinal:02d}"
        log_dir = workspace / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        worker_id = f"{mission_id[:13]}{SUBAGENT_WORKER_MARKER}-{ordinal:02d}"

        step = Step(
            task_id=uuid7_str(),
            slug=f"subagent-{ordinal:02d}",
            prompt=prompt,
        )
        # Knowledge-only inventory WITHOUT spawn_subagents: the structural
        # layer wall. The broker will answer "tool not granted" to any spawn
        # attempt from this child.
        inventory = self._child_inventory_builder(prompt)
        worker = self._worker_factory(step, inventory)

        broker_binding: Any = EmptyWorkerToolBrokerBinding()
        bind_broker = getattr(inventory, "bind_broker", None)
        if callable(bind_broker):
            try:
                issued = bind_broker(
                    ttl_s=timeout_s + 60.0,
                    mission_id=mission_id,
                    worker_id=worker_id,
                )
                if issued is not None:
                    broker_binding = issued
            except Exception:  # noqa: BLE001 - children degrade to tool-less
                logger.debug(
                    "sub-agent %s: knowledge-tool grant unavailable",
                    worker_id,
                    exc_info=True,
                )

        child_prompt = compose_worker_prompt(
            "",
            (
                "You are ONE of several parallel sub-agents working on parts "
                "of a larger task; a coordinating agent will integrate the "
                "results. Work ONLY inside your assigned working directory. "
                "Your final message IS your deliverable back to the "
                "coordinator: end with a complete, self-contained answer "
                "(plus the relative paths of any files you created).\n\n"
                f"Your sub-task: {prompt}"
            ),
        )

        cost = 0.0
        tokens = 0
        error: str | None = None
        timed_out = False
        spawned_emitted = False
        job = self._job_factory()
        try:
            async with job:
                async with asyncio.timeout(timeout_s + _CHILD_TIMEOUT_GRACE_S):
                    async for ev in worker.spawn(
                        child_prompt,
                        worktree=workspace,
                        env=self._env_builder(mission_dir),
                        job=job,
                        worker_id=worker_id,
                        log_dir=log_dir,
                        model=step.model,
                        allowed_tools=step.allowed_tools,
                        mission_id=mission_id,
                        _broker_binding=broker_binding,
                        timeout_s=timeout_s,
                    ):
                        if not spawned_emitted:
                            spawned_emitted = True
                            await self._publish(
                                mission_id,
                                WorkerSpawned(
                                    worker_id=worker_id,
                                    pid=int(getattr(worker, "last_pid", 0) or 0),
                                    cli=worker.cli,
                                    model=step.model,
                                    worktree=str(workspace),
                                ),
                            )
                        ev_cost = getattr(ev, "cost_usd", None)
                        if ev_cost is not None:
                            cost = float(ev_cost)
                        ev_tokens = (
                            getattr(ev, "tokens_used", None)
                            or getattr(ev, "total_tokens", None)
                            or getattr(ev, "num_turns", None)
                        )
                        if ev_tokens is not None:
                            tokens = int(ev_tokens)
                        if getattr(ev, "is_error", False):
                            upstream = (
                                getattr(ev, "result", None)
                                or getattr(ev, "subtype", None)
                                or "worker reported is_error=True"
                            )
                            error = str(upstream)[:300]
                        if getattr(ev, "timed_out", False):
                            timed_out = True
        except TimeoutError:
            timed_out = True
            error = error or f"sub-agent exceeded {timeout_s}s"
        finally:
            await broker_binding.aclose()

        duration = asyncio.get_running_loop().time() - started
        answer = self._read_final_answer(log_dir)
        ok = error is None and not timed_out and bool(answer.strip())
        if not ok and error is None:
            error = (
                f"sub-agent exceeded {timeout_s}s"
                if timed_out
                else "sub-agent produced no final answer"
            )

        if ok:
            await self._publish(
                mission_id,
                WorkerDraftReady(
                    worker_id=worker_id,
                    artifact_uri=workspace.resolve().as_uri(),
                    diff="",
                    tokens_used=tokens,
                    cost_usd=cost,
                    session_id="",
                ),
            )
        else:
            await self._publish(
                mission_id,
                WorkerKilled(
                    worker_id=worker_id,
                    reason="timeout" if timed_out else "worker_error",
                    error_detail=(error or "")[:300] or None,
                ),
            )

        return SubagentResult(
            index=ordinal,
            ok=ok,
            answer=answer[:_MAX_CHILD_ANSWER_CHARS],
            workspace=str(workspace),
            error=error or "",
            duration_s=round(duration, 1),
            cost_usd=cost,
            tokens_used=tokens,
        )

    @staticmethod
    def _read_final_answer(log_dir: Path) -> str:
        stream_path = log_dir / "stream.jsonl"
        try:
            stream_text = stream_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return extract_stream_evidence(stream_text).final_answer

    async def _publish(self, mission_id: str, payload: Any) -> None:
        """Best-effort board/budget event — a publish fault never fails a child."""
        try:
            await self._manager.store.append_and_publish(
                EventEnvelope(
                    mission_id=mission_id,
                    source_actor="worker",
                    ts_ms=now_ms(),
                    payload=payload,
                )
            )
        except Exception:  # noqa: BLE001 - visibility is best-effort
            logger.debug(
                "sub-agent event publish failed for %s", mission_id, exc_info=True
            )


__all__ = [
    "DEFAULT_CHILD_TIMEOUT_S",
    "MAX_CHILD_TIMEOUT_S",
    "MIN_CHILD_TIMEOUT_S",
    "SUBAGENT_WORKER_MARKER",
    "SubagentFanoutRunner",
    "SubagentResult",
]
