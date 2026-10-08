"""Move existing agents to a selected independent VPS host."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .society_routes import _runtime

router = APIRouter(prefix="/api/society-cloud", tags=["society-cloud"])


class CloudHandoffBody(BaseModel):
    computer_id: str = Field(min_length=1, max_length=128)


@router.post(
    "/agents/{agent_id}/cancel",
    operation_id="cancel_agent_cloud_preparation",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def cancel_agent_cloud_preparation(agent_id: str, request: Request) -> dict[str, Any]:
    """Cancel a transfer only after proving the remote agent is not active."""
    from jarvis.society.cloud_host import CloudHost, CloudHostError

    runtime = await _runtime(request)
    try:
        result = await CloudHost(runtime).cancel_preparation(agent_id)
    except CloudHostError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return {**result, "placement": CloudHost(runtime).placement(agent_id)}


@router.get("/agents/{agent_id}", operation_id="get_agent_cloud_placement")
async def get_agent_cloud_placement(agent_id: str, request: Request) -> dict[str, Any]:
    """Read the durable cloud owner without starting a model or connecting to the VPS."""
    from jarvis.society.cloud_host import CloudHost

    runtime = await _runtime(request)
    if await runtime.roster.get(agent_id) is None:
        raise HTTPException(404, "Agent not found")
    return {"placement": CloudHost(runtime).placement(agent_id)}


@router.post(
    "/agents/{agent_id}/status",
    operation_id="check_agent_cloud_status",
    openapi_extra={"x-jarvis-readonly": True},
)
async def check_agent_cloud_status(agent_id: str, request: Request) -> dict[str, Any]:
    """Reconnect to the VPS and reconcile a lost activation acknowledgement."""
    from jarvis.society.cloud_host import CloudHost, CloudHostError

    runtime = await _runtime(request)
    if await runtime.roster.get(agent_id) is None:
        raise HTTPException(404, "Agent not found")
    try:
        placement = await CloudHost(runtime).status(agent_id)
    except CloudHostError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(502, "Cloud host unavailable; local execution stays disabled.") from exc
    return {"placement": placement}


@router.post(
    "/agents/{agent_id}/handoff",
    operation_id="handoff_agent_to_cloud",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def handoff_agent_to_cloud(
    agent_id: str,
    body: CloudHandoffBody,
    request: Request,
) -> dict[str, Any]:
    """Transfer an idle agent, context and routines to a connected VPS."""
    from jarvis.society.cloud_host import CloudHost, CloudHostError

    runtime = await _runtime(request)
    try:
        placement = await CloudHost(runtime).handoff(agent_id, body.computer_id)
    except CloudHostError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except (ValueError, PermissionError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"placement": placement}
