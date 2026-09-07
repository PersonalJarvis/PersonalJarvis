"""SpawnSubagentsTool — bounded parallel sub-agent fan-out for mission workers.

The sanctioned exception to the "no spawn tools in worker sets" rule
(AP-5/AP-14): a TOP-LEVEL mission worker may fan its task out into parallel
child sub-agents — exactly one extra layer, never more. The legacy recursive
vehicles (``spawn_worker``, ``multi_spawn``, ``run_skill``) stay forbidden;
this tool exists precisely so that the one legitimate fan-out shape has a
guarded door instead of a hole in the fence.

Runaway protection, all deterministic (control flow, not prompts):

* **Layer wall** — children get a broker grant WITHOUT this tool
  (``SubagentFanoutRunner``), and this tool additionally refuses any caller
  whose worker_id carries the child marker. Two layers, by construction.
* **Fan-out budget** — at most :data:`DEFAULT_FANOUT_BUDGET` children per
  mission, CUMULATIVE across calls, so a looping orchestrator exhausts its
  budget instead of multiplying. The budget rises only when the USER's own
  words named a bigger number next to agent vocabulary, and never beyond
  :data:`ABSOLUTE_FANOUT_CEILING`.
* **Origin gate** — executes only for broker-brokered mission-worker calls
  (``tool_origin == "mission_worker"``); the router/realtime surfaces never
  see the tool at all (it lives on the gateway's worker-only catalog).

Distribution model: the tool blocks until every child finished and returns
each child's final answer text plus its working directory, so the calling
worker can integrate the results in its own worktree. Children run on the
same provider routing as the parent.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any, Final

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)

#: Default per-mission fan-out budget (cumulative across calls). Maintainer
#: mandate 2026-08-14: up to 10 sub-agents unless the user explicitly asked
#: for more.
DEFAULT_FANOUT_BUDGET: Final[int] = 10

#: Hard ceiling no request can exceed, user-authorized or not. This is the
#: bill-shock backstop — a bug that tries to spawn 5 000 clones dies here.
ABSOLUTE_FANOUT_CEILING: Final[int] = 50

#: Runner resolver — mirrors the spawn_worker lazy-resolver pattern (AD-OC1):
#: the fan-out runner is built by the mission bootstrap AFTER the brain.
RunnerResolver = Callable[[], Any | None]

#: A number the USER spoke next to agent vocabulary ("20 subagents",
#: "spawne 25 Agenten", "15 workers"). Both orders, bounded gap, digits only —
#: deterministic, judged on the user's words (mirrors the spawn-gate
#: philosophy), never on the model's paraphrase.
_USER_COUNT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:\b(\d{1,3})\b[^\d\n]{0,40}?"
    r"(?:sub-?agent|agent|worker|clone|klon|instanz|instance)"
    r"|(?:sub-?agent|agent|worker|clone|klon|instanz|instance)\w*"
    r"[^\d\n]{0,25}?\b(\d{1,3})\b)",
    re.IGNORECASE,
)


def user_authorized_fanout(user_text: str) -> int:
    """Largest child count the user's own words authorize, floor = default.

    Returns :data:`DEFAULT_FANOUT_BUDGET` when no explicit number is present,
    and never more than :data:`ABSOLUTE_FANOUT_CEILING`.
    """
    best = DEFAULT_FANOUT_BUDGET
    for match in _USER_COUNT_RE.finditer(user_text or ""):
        raw = match.group(1) or match.group(2)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        best = max(best, value)
    return min(best, ABSOLUTE_FANOUT_CEILING)


class SpawnSubagentsTool:
    """Fan a mission out into parallel child sub-agents (one layer only)."""

    name: str = "spawn_subagents"
    risk_tier: str = "monitor"
    # The description doubles as the orchestration hint — CLI workers read
    # tool capabilities from the broker's tool listing, so this is the one
    # text every backend is guaranteed to see (ADR-0011: models pick tools by
    # description).
    description: str = (
        "Fan your task out into parallel sub-agents (max 10 per mission "
        "unless the user explicitly asked for more). Use this DELIBERATELY, "
        "and only when the task genuinely splits into independent parts that "
        "benefit from parallel work (research angles, per-module analysis, "
        "content sections) — never for a step you can just do yourself, and "
        "never in a loop. You are the coordinator: write each prompt fully "
        "self-contained (a sub-agent shares none of your context), then "
        "integrate the returned answers yourself. The call blocks until all "
        "sub-agents finish and returns each one's final answer text plus its "
        "working directory (copy any files you need from there into your own "
        "workspace). Sub-agents cannot spawn further sub-agents."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "prompts": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": ABSOLUTE_FANOUT_CEILING,
                "description": (
                    "One complete, self-contained task instruction per "
                    "sub-agent. Include every fact the sub-agent needs — it "
                    "cannot see your conversation, your files, or the other "
                    "sub-agents. Keep it to at most 10 prompts unless the "
                    "user explicitly requested a larger number."
                ),
            },
            "timeout_s": {
                "type": "integer",
                "default": 300,
                "minimum": 60,
                "maximum": 900,
                "description": (
                    "Per-sub-agent time budget in seconds (default 300)."
                ),
            },
        },
        "required": ["prompts"],
    }

    def __init__(
        self,
        *,
        runner: Any | None = None,
        runner_resolver: RunnerResolver | None = None,
    ) -> None:
        if runner is None and runner_resolver is None:
            raise ValueError(
                "SpawnSubagentsTool requires either 'runner' or 'runner_resolver'"
            )
        self._runner = runner
        self._runner_resolver = runner_resolver
        # Cumulative children per mission_id — the loop guard. Process-local
        # is correct here: the runner lives in the same process, and a fresh
        # app start means fresh missions.
        self._spawned_per_mission: dict[str, int] = {}

    def _resolve_runner(self) -> Any | None:
        if self._runner is not None:
            return self._runner
        if self._runner_resolver is not None:
            return self._runner_resolver()
        return None

    async def execute(
        self, args: dict[str, Any], ctx: ExecutionContext
    ) -> ToolResult:
        from jarvis.missions.subagent_fanout import (
            DEFAULT_CHILD_TIMEOUT_S,
            MAX_CHILD_TIMEOUT_S,
            MIN_CHILD_TIMEOUT_S,
            SUBAGENT_WORKER_MARKER,
        )

        origin = str(ctx.config.get("tool_origin") or "")
        if origin != "mission_worker":
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "spawn_subagents is only available to mission workers "
                    "through the supervisor tool broker."
                ),
            )
        mission_id = str(ctx.config.get("mission_id") or "").strip()
        worker_id = str(ctx.config.get("worker_id") or "")
        if not mission_id:
            return ToolResult(
                success=False, output=None, error="missing mission attribution"
            )
        if SUBAGENT_WORKER_MARKER in worker_id:
            # Layer wall, second line of defense (the first is the child's
            # grant, which does not contain this tool at all).
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "Depth limit: you are already a sub-agent. Sub-agents "
                    "cannot spawn further sub-agents — do this part of the "
                    "work yourself."
                ),
            )

        prompts = [
            p.strip()
            for p in (args.get("prompts") or [])
            if isinstance(p, str) and p.strip()
        ]
        if not prompts:
            return ToolResult(
                success=False, output=None, error="prompts must not be empty"
            )

        budget = user_authorized_fanout(ctx.user_utterance)
        already = self._spawned_per_mission.get(mission_id, 0)
        remaining = budget - already
        if len(prompts) > remaining:
            return ToolResult(
                success=False,
                output=None,
                error=(
                    f"Fan-out budget exceeded: this mission may start "
                    f"{budget} sub-agents in total ({already} already used, "
                    f"{max(remaining, 0)} left), and only an explicit user "
                    f"request for a larger number raises the limit. Reduce "
                    f"the batch or consolidate prompts — do not retry in a "
                    f"loop."
                ),
            )

        runner = self._resolve_runner()
        if runner is None:
            return ToolResult(
                success=False,
                output=None,
                error="sub-agent fan-out is not available in this session",
            )

        try:
            raw_timeout = int(args.get("timeout_s") or DEFAULT_CHILD_TIMEOUT_S)
        except (TypeError, ValueError):
            raw_timeout = DEFAULT_CHILD_TIMEOUT_S
        timeout_s = max(MIN_CHILD_TIMEOUT_S, min(raw_timeout, MAX_CHILD_TIMEOUT_S))

        # Reserve the budget BEFORE the first await so two concurrent calls
        # from one worker cannot both pass the check (same synchronous-arm
        # discipline as the spawn_worker cooldown gate).
        self._spawned_per_mission[mission_id] = already + len(prompts)
        log.info(
            "Mission %s: fanning out %d sub-agent(s) (budget %d, used %d)",
            mission_id,
            len(prompts),
            budget,
            already + len(prompts),
        )
        try:
            results = await runner.run(
                mission_id=mission_id,
                prompts=prompts,
                timeout_s=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - the parent must get an answer
            log.exception("Mission %s: sub-agent fan-out crashed", mission_id)
            return ToolResult(
                success=False,
                output=None,
                error=f"sub-agent fan-out failed: {type(exc).__name__}: {exc}",
            )

        ok_count = sum(1 for r in results if r.ok)
        sections = []
        for pos, r in enumerate(results, start=1):
            head = f"--- Sub-agent {pos}/{len(results)} "
            head += "(ok" if r.ok else f"(FAILED: {r.error or 'unknown'}"
            head += f", {r.duration_s:.0f}s) — workspace: {r.workspace}"
            sections.append(f"{head}\n{r.answer or '(no answer)'}")
        return ToolResult(
            success=ok_count > 0,
            output={
                "subagents_total": len(results),
                "subagents_ok": ok_count,
                "budget_remaining": budget - self._spawned_per_mission[mission_id],
                "combined": "\n\n".join(sections),
                "results": [
                    {
                        "index": r.index,
                        "ok": r.ok,
                        "workspace": r.workspace,
                        "error": r.error,
                        "duration_s": r.duration_s,
                    }
                    for r in results
                ],
            },
            error=None if ok_count > 0 else "every sub-agent failed",
        )


__all__ = [
    "ABSOLUTE_FANOUT_CEILING",
    "DEFAULT_FANOUT_BUDGET",
    "SpawnSubagentsTool",
    "user_authorized_fanout",
]
