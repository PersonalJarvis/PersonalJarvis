"""``/api/agent-runtimes`` — Hermes / OpenClaw status, install and update.

The create dialog reads these to show whether Hermes or OpenClaw is ready
or being set up. Setup and updates run the runtimes' own official tools and
start on their own (``agent_runtimes.manager``): picking a runtime sends
``ensure``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request

from jarvis.agent_runtimes import RUNTIME_NAMES, manager
from jarvis.agent_runtimes.model_map import (
    login_providers,
    subscription_providers,
    supported_providers,
    usable_providers,
)

router = APIRouter(prefix="/api/agent-runtimes", tags=["agent-runtimes"])

#: ``[checked_at, providers, login_providers]``: the keyring is read at most every few seconds,
#: since the UI polls this route while an install runs.
_USABLE_CACHE: list[Any] = [float("-inf"), [], []]
_USABLE_TTL_S = 10.0


@router.get("")
async def list_agent_runtimes(request: Request, refresh: bool = False) -> dict[str, Any]:
    """Every external runtime: installed, version, ready, problem, setup job,
    plus the Jarvis providers an agent on such a runtime can use right now
    (``supported_providers``: saved key or local server; ``all_providers``:
    every provider the runtimes can drive once connected; ``login_providers``:
    usable ones that answer on a Claude login, billed as extra usage)."""
    now = time.monotonic()
    if refresh or now - _USABLE_CACHE[0] > _USABLE_TTL_S:
        config = getattr(request.app.state, "config", None)
        if config is None:
            from jarvis.core.config import load_config

            config = await asyncio.to_thread(load_config)
        usable = await asyncio.to_thread(usable_providers, config)
        _USABLE_CACHE[:] = [now, usable, await asyncio.to_thread(login_providers)]
    return {
        "runtimes": await manager.statuses(refresh=refresh),
        "supported_providers": list(_USABLE_CACHE[1]),
        "all_providers": sorted(supported_providers()),
        "subscription_providers": sorted(subscription_providers()),
        "login_providers": list(_USABLE_CACHE[2]),
    }


@router.post(
    "/{runtime}/{action}",
    # Downloads and runs the project's installer / updater and stops the
    # runtime's running processes: the CLI asks before sending it.
    openapi_extra={"x-jarvis-dangerous": True},
)
async def run_agent_runtime_setup(
    runtime: str, action: Literal["install", "update", "ensure"]
) -> dict[str, Any]:
    """Start the runtime's official installer or updater. ``ensure`` starts
    whichever one it needs (``job`` is ``null`` when it is ready already);
    the create dialog sends it as soon as Hermes or OpenClaw is picked."""
    if runtime not in RUNTIME_NAMES:
        raise HTTPException(404, f"unknown runtime {runtime!r}")
    if action == "ensure":
        found = await manager.ensure(runtime)
        return {"job": found.to_dict() if found is not None else None}
    return {"job": (await manager.start(runtime, action)).to_dict()}


@router.get("/{runtime}/job")
def agent_runtime_job(runtime: str) -> dict[str, Any]:
    """The running or last setup job of one runtime (``null`` when none)."""
    if runtime not in RUNTIME_NAMES:
        raise HTTPException(404, f"unknown runtime {runtime!r}")
    current = manager.job(runtime)
    return {"job": current.to_dict() if current is not None else None}
