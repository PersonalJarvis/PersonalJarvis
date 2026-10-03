"""Desktop-only controls for an explicitly selected ordinary Chrome window."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from jarvis.ui.system_browser import SystemBrowserSession, Viewport

router = APIRouter(prefix="/api/system-browser", tags=["system-browser"])
log = logging.getLogger(__name__)
_lock = threading.Lock()


class Bounds(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(ge=0, le=32768)
    y: float = Field(ge=0, le=32768)
    width: float = Field(gt=0, le=32768)
    height: float = Field(gt=0, le=32768)
    viewport_width: float = Field(gt=0, le=32768)
    viewport_height: float = Field(gt=0, le=32768)

    @model_validator(mode="after")
    def within_viewport(self) -> Bounds:
        self.viewport()
        return self

    def viewport(self) -> Viewport:
        return Viewport(**self.model_dump())


class AttachBody(BaseModel):
    window_id: str = Field(min_length=1, max_length=64)
    bounds: Bounds


class LeaseBody(BaseModel):
    lease: str = Field(min_length=1, max_length=64)


class PresentBody(LeaseBody):
    bounds: Bounds


def _session(request: Request) -> SystemBrowserSession | None:
    desktop = getattr(request.app.state, "desktop_app", None)
    host = getattr(desktop, "_window", None)
    if os.name != "nt" or host is None:
        return None
    with _lock:
        session = getattr(desktop, "_system_browser_session", None)
        if session is None or session.host is not host:
            if session is not None:
                session.close()
            session = SystemBrowserSession(host)
            desktop._system_browser_session = session
            host.events.closing += session.close
            host.events.loaded += session.close
        return session


async def _call(request: Request, method: str, *args: Any) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        session = _session(request)
        if session is None:
            return {"ok": False, "available": False, "can_dock": False,
                    "docked": False, "windows": [], "reason": "windows_desktop_required"}
        return getattr(session, method)(*args)
    try:
        return await asyncio.to_thread(run)
    except Exception:
        log.exception("System browser operation failed: %s", method)
        return {"ok": False, "reason": "browser_operation_failed"}


@router.get("/status", operation_id="system_browser_status")
async def browser_status(request: Request) -> dict[str, Any]:
    """Read native system-browser capabilities without launching a browser."""
    return await _call(request, "status")


@router.post("/open", operation_id="system_browser_open")
async def browser_open(request: Request) -> dict[str, Any]:
    """Open the default browser with its normal profile selection and startup."""
    return await _call(request, "open")


@router.get("/windows", operation_id="system_browser_windows")
async def browser_windows(request: Request) -> dict[str, Any]:
    """List selectable windows of the default Chrome executable."""
    return await _call(request, "windows")


@router.post("/attach", operation_id="system_browser_attach")
async def browser_attach(body: AttachBody, request: Request) -> dict[str, Any]:
    """Dock an explicitly selected window inside the desktop's browser area."""
    return await _call(request, "attach", body.window_id, body.bounds.viewport())


@router.post("/present", operation_id="system_browser_present")
async def browser_present(body: PresentBody, request: Request) -> dict[str, Any]:
    """Update the active browser area's bounds and renew its presence lease."""
    return await _call(request, "present", body.lease, body.bounds.viewport())


@router.post("/detach", operation_id="system_browser_detach")
async def browser_detach(body: LeaseBody, request: Request) -> dict[str, Any]:
    """Restore the docked browser's original position without closing it."""
    return await _call(request, "detach", body.lease)
