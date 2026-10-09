"""Read independent server readiness and explicitly stop its owner."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/agent-server", tags=["agent-server"])


@router.get("/status", operation_id="agent_server_status")
def status(request: Request) -> dict[str, Any]:
    """Read agent server ownership and startup state without calling a model."""
    state = request.app.state
    return {
        "independent": bool(getattr(state, "agent_server_mode", False)),
        "ready": bool(getattr(state, "agent_server_ready", False)),
        "error": getattr(state, "agent_server_error", None),
    }


@router.post(
    "/stop", operation_id="agent_server_stop",
    openapi_extra={"x-jarvis-dangerous": True},
)
def stop(request: Request) -> dict[str, bool]:
    """Explicitly stop the independent server and its agents."""
    state = request.app.state
    callback = getattr(state, "agent_server_stop", None)
    if not getattr(state, "agent_server_mode", False) or not callable(callback):
        raise HTTPException(409, "This backend is not an independent agent server.")
    callback()
    return {"stopping": True}
