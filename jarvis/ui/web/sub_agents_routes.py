"""REST API for the sub-agent dashboard (desktop UI).

Endpoints:
- ``GET /api/sub-agents/tree``          → snapshot of all active agents.
- ``GET /api/sub-agents/{trace_id}``    → a single node (for the detail panel).

The router expects a ``SubAgentRegistry`` on
``app.state.sub_agent_registry`` (set by ``WebServer._build_app``).
"""
from __future__ import annotations

import dataclasses
import logging

from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sub-agents", tags=["sub-agents"])


@router.get("/tree")
async def get_tree(request: Request) -> dict:
    """Current agent tree (all running + TTL-buffered nodes)."""
    registry = getattr(request.app.state, "sub_agent_registry", None)
    if registry is None:
        return {"roots": [], "all": {}, "count": 0, "server_ts_ns": 0}
    return registry.to_json()


@router.get("/{trace_id}")
async def get_agent(trace_id: str, request: Request) -> dict:
    """A single agent node in detail (for the detail panel)."""
    registry = getattr(request.app.state, "sub_agent_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="sub-agent registry not ready")
    stripped = trace_id.replace("-", "")
    node = registry.snapshot().get(stripped)
    if node is None:
        raise HTTPException(status_code=404, detail=f"agent {trace_id} not found")
    return dataclasses.asdict(node)
