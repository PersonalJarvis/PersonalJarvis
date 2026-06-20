"""REST routes for the Antigravity (Google-subscription) provider connect flow.

OAuth-only: the user signs in with Google once (via the official ``agy``/``gemini``
CLI), and Jarvis drives that CLI as a subprocess to bill the Brain/Subagents
against the Google subscription — no API key. This is the Google sibling of the
``/api/codex/*`` routes in :mod:`jarvis.ui.web.provider_routes`.

Kept in its own router module (not folded into ``provider_routes``) so the
feature ships as a self-contained unit. Mount in the web server alongside the
other routers::

    from .antigravity_routes import router as antigravity_router
    app.include_router(antigravity_router)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from jarvis.google_cli.auth_service import GoogleCliAuthService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["antigravity"])

# Shown to the user when no official Google CLI is installed. The Gemini CLI
# (``npm i -g @google/gemini-cli``) is the cross-platform fallback; ``agy`` is
# the official successor (macOS/Linux installer below).
_INSTALL_HINT = "curl -fsSL https://antigravity.google/cli/install.sh | bash"


@router.get("/antigravity/status")
async def antigravity_status() -> dict[str, Any]:
    """Honest snapshot of the Google CLI login (installed / connected / account)."""
    return GoogleCliAuthService().status().to_dict()


@router.post("/antigravity/login")
async def antigravity_login() -> dict[str, Any]:
    """Start the interactive Google login in a terminal. 409 if no CLI is found."""
    service = GoogleCliAuthService()
    status = service.status()
    if not status.installed:
        raise HTTPException(
            status_code=409,
            detail={"message": "No Google CLI found", "install_command": _INSTALL_HINT},
        )
    try:
        proc = service.start_login()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Google login could not be started: {type(exc).__name__}: {exc}",
        ) from exc
    return {"ok": True, "pid": proc.pid, "message": "Google login was started in the terminal"}


@router.post("/antigravity/logout")
async def antigravity_logout() -> dict[str, Any]:
    """Disconnect the Google login (removes the on-disk creds / agy logout)."""
    service = GoogleCliAuthService()
    status = service.status()
    if not status.installed:
        raise HTTPException(status_code=409, detail="No Google CLI found")
    ok, error = service.logout_blocking()
    if not ok:
        raise HTTPException(status_code=500, detail=error or "Google logout failed")
    return {"ok": True, "message": "Antigravity (Google) was disconnected"}
