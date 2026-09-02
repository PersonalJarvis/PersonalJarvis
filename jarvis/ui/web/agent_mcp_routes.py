"""Connect-flow for the Agent MCP surface (``/api/agent-mcp``).

The surface itself is mounted raw at ``/api/control/mcp/agents`` because the
MCP transport needs an untouched ASGI triple. These routes are the *about* and
*connect* layer around it: what the surface offers, which clients this machine
has, the config snippet for each, and — behind an explicit call — writing that
entry into the client's own config.

``connect`` carries ``x-jarvis-dangerous`` because it writes a file that
belongs to another application. Reading a snippet does not: a person copying
text into their own editor is the safe path and should never need a
confirmation.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-mcp", tags=["agent-mcp"])

# Every handler here is a plain ``def``: none of them awaits, and they touch
# the filesystem. FastAPI runs a sync handler in the anyio threadpool, off the
# one event loop the whole app shares — an ``async def`` that never awaits
# would block it for the length of each config read (check_async_routes).


@router.get("/status", openapi_extra={"x-jarvis-readonly": True})
def agent_mcp_status() -> dict[str, Any]:
    """What the Agent MCP surface is and what it currently offers."""
    from jarvis.mcp.agents import SERVER_NAME, tool_specs
    from jarvis.mcp.agents.bridge import SURFACE_PATH, api_base_url
    from jarvis.mcp.agents.server import PROTOCOL_VERSION

    specs = tool_specs()
    return {
        "server_name": SERVER_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "url": f"{api_base_url()}{SURFACE_PATH}",
        "auth": "bearer-control-key",
        "tools": [
            {
                "name": s["name"],
                "description": s["description"],
                "dangerous": s["dangerous"],
                "tags": s["tags"],
            }
            for s in specs
        ],
        "tool_count": len(specs),
        "bridge_command": ["python", "-m", "jarvis.mcp.agents.bridge"],
    }


@router.get("/clients", openapi_extra={"x-jarvis-readonly": True})
def agent_mcp_clients() -> dict[str, Any]:
    """Every MCP client this machine could connect, and whether it is installed."""
    from jarvis.mcp.agents import export

    rows = [
        {
            "key": t.key,
            "label": t.label,
            "config_path": str(t.config_path) if t.config_path else None,
            "shape": t.shape,
            "installed": t.installed,
            "writable": t.shape != "toml",
        }
        for t in export.targets()
    ]
    return {"clients": rows, "total": len(rows)}


@router.get("/snippet", openapi_extra={"x-jarvis-readonly": True})
def agent_mcp_snippet(client: str = "claude-desktop", transport: str = "stdio") -> dict[str, Any]:
    """The config block to paste into one client."""
    from jarvis.mcp.agents import export

    if transport not in ("stdio", "http"):
        raise HTTPException(422, "transport must be 'stdio' or 'http'")
    known = {t.key for t in export.targets()}
    if client not in known:
        raise HTTPException(404, f"unknown client {client!r}. Known: {sorted(known)}")
    return {
        "client": client,
        "transport": transport,
        "snippet": export.snippet(client, transport=transport),
    }


class ConnectBody(BaseModel):
    client: str = Field(description="claude-desktop, cursor, claude-code or codex")
    transport: str = Field(default="stdio", description="stdio (preferred) or http")
    include_key: bool = Field(
        default=False,
        description=(
            "Write the control key into the client's config. Off by default — the "
            "bridge reads it from the credential store instead."
        ),
    )


@router.post("/connect", openapi_extra={"x-jarvis-dangerous": True})
def agent_mcp_connect(body: ConnectBody) -> dict[str, Any]:
    """Write the Jarvis entry into a client's own MCP config."""
    from jarvis.mcp.agents import export

    if body.transport not in ("stdio", "http"):
        raise HTTPException(422, "transport must be 'stdio' or 'http'")
    result = export.install(body.client, transport=body.transport, include_key=body.include_key)
    if not result.get("ok"):
        # A refusal with a snippet is guidance, not a failure — hand it back at
        # 200 so the caller can show the text instead of an error toast.
        if result.get("snippet"):
            return result
        raise HTTPException(400, result.get("error") or "could not write the config")
    return result


__all__ = ["router"]
