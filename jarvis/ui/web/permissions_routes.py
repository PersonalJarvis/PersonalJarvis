"""Local-user API for macOS system-permission status and request flows."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from jarvis.platform.permissions import (
    PermissionId,
    SystemPermissionPort,
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


@router.get("/status", summary="Inspect system permission readiness")
def get_permissions_status(request: Request) -> dict:
    """Return a fresh native permission and feature-readiness snapshot."""
    return _port(request).snapshot()


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
    payload = _port(request).request(permission_id, dry_run=dry_run).to_dict()
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
    payload = _port(request).open_settings(permission_id, dry_run=dry_run).to_dict()
    return _operation_response(payload)


__all__ = ["router"]
