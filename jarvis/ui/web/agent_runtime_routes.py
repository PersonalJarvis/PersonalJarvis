"""``/api/agent-runtimes`` — Hermes / OpenClaw status, install and update.

The create dialog reads these to show whether Hermes or OpenClaw is ready
or being set up. Setup and updates run the runtimes' own official tools and
start on their own (``agent_runtimes.manager``): picking a runtime sends
``ensure``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request

from jarvis.agent_runtimes import RUNTIME_NAMES, manager
from jarvis.agent_runtimes.model_map import (
    access_blocked,
    access_choices,
    cli_subscriptions,
    login_providers,
    subscription_providers,
    supported_providers,
    usable_providers,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-runtimes", tags=["agent-runtimes"])

#: ``[checked_at, providers, login_providers, access_choices, access_blocked]``: the
#: keyring is read at most every few seconds, since the UI polls this route while an
#: install runs.
_USABLE_CACHE: list[Any] = [float("-inf"), [], [], {}, {}]
_USABLE_TTL_S = 10.0


@router.get("")
async def list_agent_runtimes(request: Request, refresh: bool = False) -> dict[str, Any]:
    """Every external runtime: installed, version, ready, problem, setup job,
    plus the Jarvis providers an agent on such a runtime can use right now
    (``supported_providers``: saved key or local server; ``all_providers``:
    every provider the runtimes can drive once connected; ``login_providers``:
    usable ones that answer on a Claude login, billed as extra usage;
    ``access``: for a provider that can pay two ways, which ways work now —
    ``"api"`` and/or ``"subscription"``; ``access_blocked``: per provider and
    way, the reason code a way is refused right now, e.g.
    ``{"claude-api": {"subscription": "extra_usage_off"}}`` — the dialog shows
    that way disabled with its reason)."""
    now = time.monotonic()
    if refresh or now - _USABLE_CACHE[0] > _USABLE_TTL_S:
        config = getattr(request.app.state, "config", None)
        if config is None:
            from jarvis.core.config import load_config

            config = await asyncio.to_thread(load_config)
        usable = await asyncio.to_thread(usable_providers, config)
        _USABLE_CACHE[:] = [
            now,
            usable,
            await asyncio.to_thread(login_providers),
            await asyncio.to_thread(access_choices),
            await asyncio.to_thread(access_blocked),
        ]
    return {
        "runtimes": await manager.statuses(refresh=refresh),
        "supported_providers": list(_USABLE_CACHE[1]),
        "all_providers": sorted(supported_providers()),
        "subscription_providers": sorted(subscription_providers()),
        "login_providers": list(_USABLE_CACHE[2]),
        "access": dict(_USABLE_CACHE[3]),
        # Per runtime, providers whose subscription runs through the vendor's
        # own CLI (Claude on OpenClaw via Claude Code): offered at own risk.
        "cli_subscriptions": await asyncio.to_thread(cli_subscriptions),
        "access_blocked": dict(_USABLE_CACHE[4]),
    }


#: The device login that waits for approval in the browser, and its poller.
_XAI_LOGIN: dict[str, Any] = {"login": None, "task": None, "error": ""}


@router.get("/xai-login", summary="Grok subscription login for Hermes / OpenClaw agents")
async def xai_login_status() -> dict[str, Any]:
    """Whether the SuperGrok / X Premium+ login for agents is connected, and the
    pending browser approval (code and link) while one runs."""
    from jarvis.agent_runtimes import xai_login

    state = await asyncio.to_thread(xai_login.status)
    login = _XAI_LOGIN["login"]
    task = _XAI_LOGIN["task"]
    pending = login is not None and task is not None and not task.done()
    return {
        **state,
        "pending": login.to_public() if pending else None,
        "error": _XAI_LOGIN["error"],
    }


@router.post(
    "/xai-login",
    summary="Start the Grok subscription login for Hermes / OpenClaw agents",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def xai_login_start() -> dict[str, Any]:
    """Ask xAI for a device code; the person approves it in the browser and the
    app polls until the login is stored. Returns the code and the link."""
    from jarvis.agent_runtimes import xai_login

    task = _XAI_LOGIN["task"]
    if task is not None and not task.done():
        task.cancel()
    try:
        login = await asyncio.to_thread(xai_login.start_device_login)
    except xai_login.XaiLoginError as exc:
        raise HTTPException(502, {"reason": exc.code, "message": str(exc)}) from exc
    _XAI_LOGIN.update(login=login, error="")
    _XAI_LOGIN["task"] = asyncio.get_running_loop().create_task(
        _poll_xai_login(login), name="xai-agents-login"
    )
    return {"pending": login.to_public()}


async def _poll_xai_login(login: Any) -> None:
    from jarvis.agent_runtimes import xai_login

    try:
        while True:
            await asyncio.sleep(login.interval_s)
            if await asyncio.to_thread(xai_login.poll_device_login, login):
                _USABLE_CACHE[0] = float("-inf")  # the dialog shows the new seat at once
                return
    except xai_login.XaiLoginError as exc:
        log.info("xai login for agents ended: %s", exc.code)
        _XAI_LOGIN["error"] = exc.code


@router.delete(
    "/xai-login",
    summary="Disconnect the Grok subscription login for Hermes / OpenClaw agents",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def xai_login_disconnect() -> dict[str, Any]:
    from jarvis.agent_runtimes import xai_login

    await asyncio.to_thread(xai_login.disconnect)
    _USABLE_CACHE[0] = float("-inf")
    return {"connected": False}


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
