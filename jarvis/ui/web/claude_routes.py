"""REST routes for the Claude (Anthropic) subscription connect flow.

Dual billing, mirror of Codex / Antigravity: the heavy-task subagent runs over
the **Claude Max subscription** (the ``claude`` CLI's OAuth login in
``~/.claude/.credentials.json`` — no per-token bill) OR over an **Anthropic API
key** (billed per token). This module surfaces an honest snapshot of which one is
live, plus the connected account email + subscription tier, so the Subagent card
can render "Connected as <email>" exactly like the Codex / Antigravity cards.

This is the Anthropic sibling of ``/api/codex/*``
(:mod:`jarvis.ui.web.provider_routes`) and ``/api/antigravity/*``
(:mod:`jarvis.ui.web.antigravity_routes`). Kept in its own router module so the
feature ships as a self-contained unit. Mount alongside the other routers::

    from .claude_routes import router as claude_router
    app.include_router(claude_router)

The endpoint returns NO secret — only the display-safe account email + tier and
connection booleans.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from jarvis.claude_auth import ClaudeAuthService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["claude"])

_INSTALL_HINT = "npm i -g @anthropic-ai/claude-code"


def _real_api_key_present() -> bool:
    """True when a CLASSIC Anthropic API key (sk-ant-api…) is stored.

    A stored ``anthropic_api_key`` that is actually an OAuth bearer (sk-ant-oat,
    auto-populated from a Claude Max login) is NOT an API-key billing path — that
    is the subscription, already detected from the credentials file. Only a real
    API key flips the service into ``api_key`` mode.
    """
    try:
        from jarvis.core.config import get_jarvis_agent_secret

        key = get_jarvis_agent_secret("claude-api")
    except Exception:  # noqa: BLE001
        return False
    return bool(key) and not str(key).startswith("sk-ant-oat")


def _service() -> ClaudeAuthService:
    return ClaudeAuthService(api_key_present=_real_api_key_present())


@router.get("/claude/status")
async def claude_status() -> dict[str, Any]:
    """Honest snapshot of the Claude login (installed / connected / account)."""
    return _service().status().to_dict()


@router.post("/claude/login")
async def claude_login() -> dict[str, Any]:
    """Start the interactive Claude sign-in in a terminal. 409 if no CLI is found."""
    service = _service()
    status = service.status()
    if not status.installed:
        raise HTTPException(
            status_code=409,
            detail={"message": "Claude CLI is not installed", "install_command": _INSTALL_HINT},
        )
    try:
        proc = service.start_login()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Claude login could not be started: {type(exc).__name__}: {exc}",
        ) from exc
    return {"ok": True, "pid": proc.pid, "message": "Claude login was started in the terminal"}


@router.post("/claude/logout")
async def claude_logout() -> dict[str, Any]:
    """Disconnect the Claude subscription login (removes the on-disk bearer)."""
    service = _service()
    ok, error = service.logout_blocking()
    if not ok:
        raise HTTPException(status_code=500, detail=error or "Claude logout failed")
    return {"ok": True, "message": "Claude was disconnected"}
