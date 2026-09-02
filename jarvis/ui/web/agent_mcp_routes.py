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


# --------------------------------------------------------------- pairing


class PairBody(BaseModel):
    name: str = Field(
        description="What to call this client later, e.g. 'Claude Desktop - MacBook'.",
        min_length=1,
        max_length=120,
    )
    scope: str = Field(
        default="work",
        description=(
            "read = look but do not spend; work = talk to agents and post quests; "
            "full = also governance (approvals, kill switch)."
        ),
    )
    client: str = Field(
        default="",
        description="Which client this is for. Shapes the snippet; blank gives the generic one.",
    )
    transport: str = Field(default="stdio", description="stdio (preferred) or http")
    base_url: str = Field(
        default="",
        description=(
            "The URL this client will reach Jarvis at. Set it when pairing a client "
            "on ANOTHER machine; blank means this box's own address."
        ),
    )
    install: bool = Field(
        default=False,
        description="Also write the entry into the client's config file on this machine.",
    )


@router.post("/pair", openapi_extra={"x-jarvis-dangerous": True})
def agent_mcp_pair(body: PairBody) -> dict[str, Any]:
    """Issue a client its OWN credential and hand back the finished config.

    This is the one-click path: a named, scoped, individually revocable token
    plus the exact JSON to paste. The secret appears in this response and never
    again — it is stored only as a hash — so whatever reads this must show it
    to the person or write it straight into the client's config.
    """
    from jarvis.mcp.agents import export, tokens

    if body.transport not in ("stdio", "http"):
        raise HTTPException(422, "transport must be 'stdio' or 'http'")
    base_url = body.base_url.strip().rstrip("/") or None
    try:
        token, secret = tokens.store().issue(name=body.name, scope=body.scope, client=body.client)
    except tokens.TokenError as exc:
        raise HTTPException(422, str(exc)) from exc

    client_key = body.client.strip() or "claude-desktop"
    known = {t.key for t in export.targets()}
    if client_key not in known:
        client_key = "claude-desktop"

    entry = export.entry_for(transport=body.transport, token=secret, base_url=base_url)
    result: dict[str, Any] = {
        "token": token.to_public(),
        "secret": secret,
        "secret_shown_once": True,
        "transport": body.transport,
        "client": client_key,
        "config": {"mcpServers": {export.ENTRY_NAME: entry}},
        "config_text": export.snippet(
            client_key, transport=body.transport, token=secret, base_url=base_url
        ),
        "installed": None,
    }
    if body.install:
        result["installed"] = export.install(
            client_key, transport=body.transport, token=secret, base_url=base_url
        )
    return result


@router.get("/tokens", openapi_extra={"x-jarvis-readonly": True})
def agent_mcp_tokens(include_revoked: bool = False) -> dict[str, Any]:
    """Every client that has been paired — never the secrets, only who and when."""
    from jarvis.mcp.agents import tokens

    rows = [t.to_public() for t in tokens.store().list(include_revoked=include_revoked)]
    return {"tokens": rows, "total": len(rows), "scopes": list(tokens.SCOPES)}


@router.delete("/tokens/{token_id}", openapi_extra={"x-jarvis-dangerous": True})
def agent_mcp_revoke(token_id: str) -> dict[str, Any]:
    """Cut off one client. Every other paired client keeps working."""
    from jarvis.mcp.agents import tokens

    try:
        token = tokens.store().revoke(token_id)
    except tokens.TokenError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"token": token.to_public(), "revoked": True}


__all__ = ["router"]
