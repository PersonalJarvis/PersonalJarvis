"""Preview routes: serves registered dev servers for the sidebar Previews view."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/previews", tags=["previews"])


@router.get("")
async def list_previews(request: Request) -> list[dict[str, Any]]:
    registry = getattr(request.app.state, "preview_registry", None)
    if registry is None:
        return []
    return [
        {
            "port": e.port,
            "title": e.title,
            "kind": e.kind,
            "url": e.url,
            "started_ns": e.started_ns,
            "agent_trace_id": e.agent_trace_id,
        }
        for e in registry.list()
    ]
