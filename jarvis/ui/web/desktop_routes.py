"""REST API for the desktop shell's window management (detachable views).

A view can be split off into its own native desktop window ("detached solo
window") — initially the Agentic IDE and the Voice/Chats surface. The window
itself is created by the desktop shell (pywebview) because ``window.open`` is
silently dropped inside WebView2; the frontend calls these routes only when it
detects the embedded shell and falls back to a plain browser tab elsewhere.

Endpoints:
    POST /api/window/detach   {view}  → open (or focus) the detached window
    GET  /api/window/detached         → registry snapshot for mount-time resync
    POST /api/window/reattach {view}  → close the detached window

Dispatch follows the ``app.state.desktop_app`` pattern (see
``settings_routes.py::open_external``): when no desktop shell is attached —
headless server, Docker, degraded Linux desktop — the answer is an honest
``{"ok": false, "reason": "no_desktop_shell", "fallback_url": ...}`` so the
caller can open the solo URL in a real browser tab instead. Every pywebview
call is thread-hopped off the event loop: window methods block the calling
thread while the GUI thread services them (the 2026-08-06 realtime stall), and
pywebview only materializes runtime windows from a non-MainThread thread.

Wired into the WebServer in ``server.py::_build_app`` via
    from .desktop_routes import router as desktop_router
    app.include_router(desktop_router)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/window", tags=["window"])


class DetachBody(BaseModel):
    view: str = Field(min_length=1, max_length=64)


def _solo_url_path(view: str) -> str:
    """Relative solo URL for a view — the SPA catch-all serves it anywhere."""
    return f"/?view={view}&solo=1"


# operation_ids are the CLI command names: the dynamic layer builds one Click
# group per tag and one command per operation, so these read
# `jarvis api window detach|detached|reattach`. The paths match no dangerous
# marker — opening/listing/closing local UI windows mutates no data — so the
# normal mutating-confirmation tier applies (see jarvis/cli_ctl/safety.py).
@router.post("/detach", operation_id="detach")
async def window_detach(body: DetachBody, request: Request) -> dict[str, Any]:
    """Open ``view`` in its own detached desktop window (or focus it).

    Idempotent: a second detach of the same view focuses the existing window
    and reports ``already_open``. An unknown/undetachable view, a host without
    a desktop shell, and a webview backend that cannot create runtime windows
    (the macOS/GTK risk) all answer with ``ok: false`` and a machine-readable
    reason — the frontend then falls back to opening ``fallback_url`` in a
    real browser tab, which is the honest degrade on those hosts.
    """
    desktop = getattr(request.app.state, "desktop_app", None)
    fn = getattr(desktop, "open_detached_window", None)
    if not callable(fn):
        return {
            "ok": False,
            "reason": "no_desktop_shell",
            "fallback_url": _solo_url_path(body.view),
        }
    try:
        return await asyncio.to_thread(fn, body.view)
    except Exception as exc:  # noqa: BLE001
        # Not swallowed: the reason travels back to the caller, mirroring the
        # /api/window/focus contract.
        log.warning("window detach failed: %s: %s", type(exc).__name__, exc)
        return {
            "ok": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "fallback_url": _solo_url_path(body.view),
        }


@router.get("/detached", operation_id="detached")
async def window_detached(request: Request) -> dict[str, Any]:
    """Snapshot of the currently detached views.

    The WS events (``DetachedViewOpened/Closed``) keep every window live; this
    GET exists for the mount-time resync after a reload (preload recovery
    reloads every window after a frontend rebuild). Headless hosts honestly
    report an empty registry — nothing can be detached there.
    """
    desktop = getattr(request.app.state, "desktop_app", None)
    fn = getattr(desktop, "detached_views_snapshot", None)
    if not callable(fn):
        return {"ok": True, "views": []}
    return {"ok": True, "views": await asyncio.to_thread(fn)}


@router.post("/reattach", operation_id="reattach")
async def window_reattach(body: DetachBody, request: Request) -> dict[str, Any]:
    """Close the detached window for ``view`` (its section returns to the app).

    The pywebview ``closed`` hook — not this route — publishes
    ``DetachedViewClosed`` and clears the registry, so closing the window with
    its own X and clicking "Bring it back" take exactly the same path.
    """
    desktop = getattr(request.app.state, "desktop_app", None)
    fn = getattr(desktop, "close_detached_window", None)
    if not callable(fn):
        return {"ok": False, "reason": "no_desktop_shell"}
    try:
        return await asyncio.to_thread(fn, body.view)
    except Exception as exc:  # noqa: BLE001
        log.warning("window reattach failed: %s: %s", type(exc).__name__, exc)
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
