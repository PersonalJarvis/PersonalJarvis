"""``/api/agent-runtimes`` — Hermes / OpenClaw status, install and update.

The society UI reads these to offer the runtime switch on an agent's Brain
settings: installed or not, version, whether that version is new enough, and
a running setup job with its log tail. Install and update run the runtimes'
own official tools and only when the person presses the button.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from jarvis.agent_runtimes import RUNTIME_NAMES, manager

router = APIRouter(prefix="/api/agent-runtimes", tags=["agent-runtimes"])


@router.get("")
async def list_agent_runtimes(refresh: bool = False) -> dict[str, Any]:
    """Every external runtime: installed, version, ready, problem, setup job."""
    return {"runtimes": await manager.statuses(refresh=refresh)}


@router.post("/{runtime}/{action}")
async def run_agent_runtime_setup(
    runtime: str, action: Literal["install", "update"]
) -> dict[str, Any]:
    """Start the runtime's official installer or updater (person-started only)."""
    if runtime not in RUNTIME_NAMES:
        raise HTTPException(404, f"unknown runtime {runtime!r}")
    return {"job": manager.start(runtime, action).to_dict()}


@router.get("/{runtime}/job")
async def agent_runtime_job(runtime: str) -> dict[str, Any]:
    """The running or last setup job of one runtime (``null`` when none)."""
    if runtime not in RUNTIME_NAMES:
        raise HTTPException(404, f"unknown runtime {runtime!r}")
    current = manager.job(runtime)
    return {"job": current.to_dict() if current is not None else None}
