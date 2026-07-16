"""Run-control REST surface for Computer-Use goals (deep-dive 2026-07-15, H-09).

Start / list / inspect / cancel desktop-automation missions over HTTP — which
makes each operation a ``jarvis api computer-use <op>`` CLI command
automatically (CLI-first contract). Before this module the feature was
reachable only through voice and the LLM tool; there was no uniform
automation/control API and no per-goal cancel.

The routes are a thin shell: run inventory and per-id cancel live in
``jarvis.harness.cu_run_registry`` (populated by the harness itself for EVERY
launch route), and a started goal runs the same ``ComputerUseHarness`` path as
voice/tool launches — same guards, same desktop lock, same registry lifecycle.
Platform-neutral: plain Python/FastAPI, no OS-specific code.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from jarvis.harness import cu_run_registry

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/computer-use", tags=["computer-use"])

#: Matches the LLM tool's default mission timeout.
_DEFAULT_TIMEOUT_S = 120.0
_MIN_TIMEOUT_S = 10.0
_MAX_TIMEOUT_S = 3600.0

#: Strong refs so background missions are never garbage-collected mid-flight
#: (same pattern as ComputerUseTool._background_tasks).
_BACKGROUND_MISSIONS: set[asyncio.Task[None]] = set()


class StartGoalBody(BaseModel):
    """Request body for starting a Computer-Use goal."""

    goal: str = Field(min_length=1, max_length=2000)
    timeout_s: float = Field(
        default=_DEFAULT_TIMEOUT_S, ge=_MIN_TIMEOUT_S, le=_MAX_TIMEOUT_S,
    )


def _require_cu_wired() -> None:
    """503 with an honest, actionable message when Computer Use is not live."""
    from jarvis.harness.computer_use_context import (  # noqa: PLC0415
        peek_computer_use_context,
    )

    if peek_computer_use_context() is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "computer-use is not active on this machine: "
                "[computer_use].enabled is false (the shipped default) or no "
                "vision engine could be built. Enable it with "
                "`jarvis config set computer_use.enabled true` and restart."
            ),
        )


def _launch_mission(mission_id: str, goal: str, timeout_s: float) -> None:
    """Start the mission as a detached background task.

    The harness registers the run (queued -> running -> terminal) under the
    pre-assigned ``mission_id``; this function only owns the task lifetime.
    Split out so route tests can monkeypatch the actual desktop dispatch.
    """
    from jarvis.core.protocols import HarnessTask  # noqa: PLC0415
    from jarvis.plugins.harness.computer_use import (  # noqa: PLC0415
        CU_MISSION_ID_ENV_KEY,
        CU_SOURCE_ENV_KEY,
        ComputerUseHarness,
    )

    task = HarnessTask(
        prompt=goal,
        timeout_s=int(timeout_s),
        env={CU_MISSION_ID_ENV_KEY: mission_id, CU_SOURCE_ENV_KEY: "api"},
    )
    harness = ComputerUseHarness()

    async def _drain() -> None:
        try:
            async for _chunk in harness.invoke(task):
                pass
        except Exception:  # noqa: BLE001 — a crash must not kill the event loop
            log.exception("[cu] API-started mission %s crashed", mission_id)
            cu_run_registry.finish_run(mission_id, "error")

    bg = asyncio.create_task(_drain(), name=f"cu-api-mission-{mission_id}")
    _BACKGROUND_MISSIONS.add(bg)
    bg.add_done_callback(_BACKGROUND_MISSIONS.discard)


@router.post(
    "/goals",
    status_code=201,
    summary="Start a Computer-Use goal",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def start_cu_goal(body: StartGoalBody) -> dict[str, Any]:
    """Dispatch a desktop-automation goal in the background.

    Returns the mission id for status polling and cancel. Refuses honestly
    (503) when Computer Use is not wired, and absorbs a duplicate of an
    ALREADY-RUNNING identical goal (409) instead of racing the desktop.
    """
    _require_cu_wired()
    goal = body.goal.strip()
    if not goal:
        raise HTTPException(status_code=400, detail="goal must not be blank")
    goal_key = " ".join(goal.split()).casefold()
    for run in cu_run_registry.list_runs(limit=100):
        if (
            run["status"] in cu_run_registry.ACTIVE_STATUSES
            and " ".join(run["goal"].split()).casefold() == goal_key
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "an identical goal is already running",
                    "mission_id": run["mission_id"],
                },
            )
    mission_id = uuid.uuid4().hex[:12]
    # Visible to GET immediately; the harness replaces this entry with the
    # live one (real cancel token) the moment the background task starts.
    cu_run_registry.register_run(mission_id, goal, token=None, source="api")
    _launch_mission(mission_id, goal, body.timeout_s)
    return {"mission_id": mission_id, "status": "queued", "goal": goal}


@router.get("/goals", summary="List Computer-Use runs")
async def list_cu_goals(
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Newest-first run snapshots (active and recent terminal runs)."""
    runs = cu_run_registry.list_runs(limit=limit)
    return {
        "runs": runs,
        "active": cu_run_registry.active_run_count(),
        "total": len(runs),
    }


@router.post(
    "/goals/cancel-all",
    summary="Cancel every active Computer-Use run",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def cancel_all_cu_goals() -> dict[str, Any]:
    """Fire the cancel token of every active mission (queued or running)."""
    return {"cancelled": cu_run_registry.cancel_all_runs(reason="api_cancel")}


@router.get("/goals/{mission_id}", summary="Inspect one Computer-Use run")
async def get_cu_goal(mission_id: str) -> dict[str, Any]:
    run = cu_run_registry.get_run(mission_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown mission id")
    return run


@router.post(
    "/goals/{mission_id}/cancel",
    summary="Cancel one Computer-Use run",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def cancel_cu_goal(mission_id: str) -> dict[str, Any]:
    """Cancel one active mission by id (404 unknown, 409 already finished)."""
    run = cu_run_registry.get_run(mission_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown mission id")
    if run["status"] in cu_run_registry.TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"run is already terminal ({run['status']})",
        )
    if not cu_run_registry.cancel_run(mission_id, reason="api_cancel"):
        raise HTTPException(
            status_code=409,
            detail="run has no live cancel token (it is about to start or end)",
        )
    return {"mission_id": mission_id, "cancel_requested": True}


__all__ = ["router"]
