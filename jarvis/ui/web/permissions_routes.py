"""Local-user API for macOS system-permission status and request flows."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from jarvis.platform.permissions import (
    PermissionId,
    SystemPermissionPort,
    active_features,
    get_system_permission_port,
)

from .control_auth import require_control_key_or_session

router = APIRouter(
    prefix="/api/permissions",
    tags=["permissions"],
    dependencies=[Depends(require_control_key_or_session)],
)

def _port(request: Request) -> SystemPermissionPort:
    injected = getattr(request.app.state, "system_permission_port", None)
    return injected if injected is not None else get_system_permission_port()


def _active_features(request: Request) -> frozenset[str]:
    """The features the live configuration has turned on.

    The port reports what macOS says; which of those answers are worth asking
    about is a policy of the running configuration (a ducking switch that is
    off needs no Music/Spotify consent), so the route owns it and every
    response — status AND each operation's before/after snapshot — carries the
    same ``wanted`` flags.
    """
    state = request.app.state
    return active_features(getattr(state, "config", None) or getattr(state, "cfg", None))


@router.get("/status", summary="Inspect system permission readiness")
def get_permissions_status(request: Request) -> dict:
    """Return a fresh native permission and feature-readiness snapshot."""
    return _port(request).snapshot(active_features=_active_features(request))


def _operation_response(payload: dict) -> Any:
    if payload["ok"]:
        return payload
    return JSONResponse(status_code=409, content=payload)


@router.post(
    "/{permission_id}/request",
    summary="Request a system permission",
    openapi_extra={"x-jarvis-dangerous": True},
    response_model=None,
)
def request_permission(
    permission_id: PermissionId,
    request: Request,
    dry_run: bool = Query(default=False),
) -> Any:
    """Trigger an Apple prompt only from the foreground installed app."""
    payload = (
        _port(request)
        .request(permission_id, dry_run=dry_run, active_features=_active_features(request))
        .to_dict()
    )
    return _operation_response(payload)


@router.post(
    "/{permission_id}/open-settings",
    summary="Open a system permission settings pane",
    openapi_extra={"x-jarvis-dangerous": True},
    response_model=None,
)
def open_permission_settings(
    permission_id: PermissionId,
    request: Request,
    dry_run: bool = Query(default=False),
) -> Any:
    """Open the matching pane only for a local foreground app interaction."""
    payload = (
        _port(request)
        .open_settings(permission_id, dry_run=dry_run, active_features=_active_features(request))
        .to_dict()
    )
    return _operation_response(payload)


@router.post(
    "/{permission_id}/reset",
    summary="Reset this app's own system permission record",
    openapi_extra={"x-jarvis-dangerous": True},
    response_model=None,
)
def reset_permission(
    permission_id: PermissionId,
    request: Request,
    dry_run: bool = Query(default=False),
) -> Any:
    """Drop the app's own macOS TCC row so the native prompt can reappear.

    Recovery for the permanently-"Denied" trap: macOS auto-denies an app
    that ever listened before being asked and then never prompts again.
    Scoped strictly to this app's bundle id; other apps stay untouched.
    """
    payload = (
        _port(request)
        .reset(permission_id, dry_run=dry_run, active_features=_active_features(request))
        .to_dict()
    )
    return _operation_response(payload)


__all__ = ["router"]
