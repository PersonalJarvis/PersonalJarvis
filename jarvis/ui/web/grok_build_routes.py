"""REST routes for the Grok Build (xAI subscription) provider connect flow.

OAuth-only: the user signs in with SuperGrok / X Premium+ once (via the official
``grok`` CLI), and Jarvis drives that CLI as a subprocess to bill Jarvis-Agent
work against the subscription — no xAI API key. This is the xAI sibling of the
``/api/codex/*`` and ``/api/antigravity/*`` routes.

Kept in its own router module so the feature ships as a self-contained unit.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from jarvis.core.interactive_terminal import InteractiveTerminalUnavailable
from jarvis.grok_build_auth import (
    GrokBuildAuthService,
    grok_build_install_command,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["grok-build"])


@router.get("/grok-build/status")
async def grok_build_status() -> dict[str, Any]:
    """Honest snapshot of the Grok Build login (installed / connected / account).

    Off the event loop: the probe may spawn the CLI binary.
    """
    status = await asyncio.to_thread(GrokBuildAuthService().status)
    return status.to_dict()


@router.post("/grok-build/test")
async def grok_build_test() -> dict[str, Any]:
    """Live CLI test: binary, version, login.

    Re-augments PATH first, so a CLI installed after app start is found without
    a restart. Runs off the event loop — the probe spawns the real binary.
    """
    from jarvis.agent_cli_probe import test_grok_build

    return (await asyncio.to_thread(test_grok_build)).to_dict()


@router.post("/grok-build/login")
async def grok_build_login() -> dict[str, Any]:
    """Start the interactive SuperGrok login in a terminal. 409 if no CLI is found."""
    service = GrokBuildAuthService()
    status = await asyncio.to_thread(service.status)
    if not status.installed:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Grok Build CLI is not installed",
                "install_command": grok_build_install_command(),
            },
        )
    try:
        launch = await asyncio.to_thread(service.start_login)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InteractiveTerminalUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Grok Build login could not be started: {type(exc).__name__}: {exc}",
        ) from exc
    return {
        "ok": True,
        "pid": launch.pid,
        "message": "Grok Build login was started in the terminal",
    }


@router.post("/grok-build/logout")
async def grok_build_logout() -> dict[str, Any]:
    """Disconnect the SuperGrok login (``grok logout`` / remove auth.json)."""
    service = GrokBuildAuthService()
    status = await asyncio.to_thread(service.status)
    if not status.installed:
        raise HTTPException(status_code=409, detail="Grok Build CLI is not installed")
    ok, error = await asyncio.to_thread(service.logout_blocking)
    if not ok:
        raise HTTPException(status_code=500, detail=error or "Grok Build logout failed")
    return {"ok": True, "message": "Grok Build was disconnected"}
