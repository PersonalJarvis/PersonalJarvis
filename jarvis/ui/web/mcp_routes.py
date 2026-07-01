"""REST API for MCP server management.

Mounted by the WebServer. Reads bootstrap specs from
``jarvis.mcp.registry`` and user state from ``jarvis.mcp.state``.

Endpoints:
    GET  /api/mcps                       -> list servers with status + tools
    POST /api/mcps/{name}/enable         -> enable + immediately start
    POST /api/mcps/{name}/disable        -> disable + stop
    POST /api/mcps/{name}/start          -> manual start (without enable toggle)
    POST /api/mcps/{name}/stop           -> manual stop
    POST /api/mcps/import-claude-desktop -> import mcpServers from Claude Desktop config
    DELETE /api/mcps/{name}              -> delete custom spec (custom only)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from jarvis.core.config import get_secret, set_secret
from jarvis.core.events import BrainToolsChanged
from jarvis.mcp import state as mcp_state
from jarvis.mcp.registry import BOOTSTRAP_SERVERS, MCPRegistry, MCPServerSpec

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcps", tags=["mcps"])


# ----------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------

def _spec_to_dict(
    spec: MCPServerSpec,
    *,
    state: dict[str, Any],
    registry: MCPRegistry,
    bootstrap_names: set[str],
) -> dict[str, Any]:
    active = registry.active_clients()
    is_running = spec.name in active
    tools: list[dict[str, Any]] = []
    error: str | None = registry.last_error(spec.name)

    if is_running:
        client = active[spec.name]
        tools = [
            {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
            }
            for t in client._tools_cache  # noqa: SLF001 — internal cache is public-ish
        ]
        if not client.is_healthy and not error:
            error = "circuit-breaker open"

    # Credential-Status: welche required_auth-Keys sind im keyring gesetzt?
    credentials_status: dict[str, bool] = {}
    for auth_key in spec.required_auth:
        credentials_status[auth_key] = bool(get_secret(auth_key, env_fallback=auth_key))

    return {
        "name": spec.name,
        "display": spec.display,
        "description": spec.description,
        "transport": spec.transport,
        "mandatory": spec.mandatory,
        "required_auth": list(spec.required_auth),
        "credentials_status": credentials_status,
        "credentials_complete": all(credentials_status.values()) if credentials_status else True,
        "platform_notes": spec.platform_notes,
        "install_command": list(spec.install_command),
        "is_bootstrap": spec.name in bootstrap_names,
        "enabled": spec.name in state.get("enabled", []),
        "status": "running" if is_running else "stopped",
        "tools": tools,
        "error": error,
    }


def _get_registry(request: Request) -> MCPRegistry | None:
    return getattr(request.app.state, "mcp_registry", None)


def _get_tool_registry(request: Request) -> dict[str, Any] | None:
    return getattr(request.app.state, "tool_registry", None)


async def _sync_tools_for_server(
    request: Request,
    registry: MCPRegistry,
    server_name: str,
    *,
    adding: bool,
) -> None:
    """Keep the tool_registry in sync with MCP server state.

    ``adding=True`` after start: registers all tools of the server as adapters.
    ``adding=False`` after stop: removes all tools that originated from that server.
    """
    tool_registry = _get_tool_registry(request)
    if tool_registry is None:
        return

    prefix = f"{server_name}/"

    if not adding:
        # Remove all entries beginning with "<server>/"
        for key in list(tool_registry.keys()):
            if key.startswith(prefix):
                tool_registry.pop(key, None)
    else:
        try:
            from jarvis.mcp.adapter import MCPToolAdapter

            client = registry.active_clients().get(server_name)
            if client is None:
                return
            risk_tier = "monitor"
            # Pick up risk tier from config when available
            cfg = getattr(request.app.state, "cfg", None)
            if cfg is not None and hasattr(cfg, "harness"):
                risk_tier = getattr(cfg.harness, "default_risk_tier", "monitor")

            for mcp_tool in await client.list_tools():
                adapter = MCPToolAdapter(client, mcp_tool, risk_tier=risk_tier)
                tool_registry[adapter.name] = adapter
        except Exception as exc:  # noqa: BLE001
            log.warning("Tool registry sync for %s failed: %s", server_name, exc)


async def _publish_brain_tools_changed(request: Request, reason: str) -> None:
    """Publish a BrainToolsChanged event so the live brain reloads its tool set.

    Mirrors the async/sync convention from ``plugin_registry.py`` exactly.
    No-ops silently when ``app.state.bus`` is not yet set.
    """
    bus = getattr(request.app.state, "bus", None)
    if bus is None:
        return
    event = BrainToolsChanged(
        source_layer="mcp_routes",
        reason=reason,
    )
    try:
        if asyncio.iscoroutinefunction(bus.publish):
            await bus.publish(event)
        else:
            bus.publish(event)
    except Exception as exc:  # noqa: BLE001
        log.debug("BrainToolsChanged publish failed: %s", exc)


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------

@router.get("")
async def list_mcps(request: Request) -> dict[str, Any]:
    registry = _get_registry(request)
    state = mcp_state.load_state()
    bootstrap_names = {s.name for s in BOOTSTRAP_SERVERS}

    if registry is None:
        return {
            "servers": [
                {
                    "name": s.name,
                    "display": s.display,
                    "description": s.description,
                    "transport": s.transport,
                    "mandatory": s.mandatory,
                    "required_auth": list(s.required_auth),
                    "platform_notes": s.platform_notes,
                    "install_command": list(s.install_command),
                    "is_bootstrap": True,
                    "enabled": s.name in state.get("enabled", []),
                    "status": "not-initialized",
                    "tools": [],
                    "error": None,
                }
                for s in BOOTSTRAP_SERVERS
            ],
            "total": len(BOOTSTRAP_SERVERS),
            "running": 0,
            "registry_ready": False,
        }

    specs = registry.all_specs()
    servers = [
        _spec_to_dict(s, state=state, registry=registry, bootstrap_names=bootstrap_names)
        for s in specs
    ]
    running = sum(1 for s in servers if s["status"] == "running")

    return {
        "servers": servers,
        "total": len(servers),
        "running": running,
        "registry_ready": True,
    }


@router.post("/{name}/enable")
async def enable_mcp(name: str, request: Request) -> dict[str, Any]:
    """Enables an MCP server: status check FIRST, only persists
    ``enabled=true`` in mcp.json on success.

    Rationale: the user expects "toggle on = connected". If we wrote
    ``enabled=true`` upfront and the start then failed, we'd be left with an
    inconsistent state (enabled in config, but offline at runtime). The
    connection probe upfront avoids that.
    """
    registry = _get_registry(request)
    if registry is None:
        raise HTTPException(503, "MCP registry not initialized.")

    spec = registry.get_spec(name)
    if spec is None:
        raise HTTPException(404, f"MCP server '{name}' unknown.")

    # Already active? Just persist enabled=true, no restart needed.
    if name in registry.active_clients():
        mcp_state.enable(name)
        await _sync_tools_for_server(request, registry, name, adding=True)
        await _publish_brain_tools_changed(request, f"mcp_enabled:{name}")
        return {"ok": True, "name": name, "enabled": True, "started": True}

    # Probe start: stays started if it succeeds. On error, nothing is
    # persisted — the user sees the reason directly.
    try:
        await registry.start_enabled([name])
    except Exception as exc:  # noqa: BLE001
        log.warning("Enable-start of %s failed: %s", name, exc)

    # Check success: is the client in active_clients + no error?
    if name not in registry.active_clients():
        error = registry.last_error(name) or "Connection failed"
        return {
            "ok": False,
            "name": name,
            "enabled": False,
            "started": False,
            "error": error,
        }

    # Success -> persist enabled=true + register tools + notify brain
    mcp_state.enable(name)
    await _sync_tools_for_server(request, registry, name, adding=True)
    await _publish_brain_tools_changed(request, f"mcp_enabled:{name}")

    return {"ok": True, "name": name, "enabled": True, "started": True}


@router.post("/{name}/disable")
async def disable_mcp(name: str, request: Request) -> dict[str, Any]:
    registry = _get_registry(request)
    if registry is None:
        raise HTTPException(503, "MCP registry not initialized.")

    mcp_state.disable(name)

    active = registry.active_clients()
    if name in active:
        try:
            await active[name].stop()
        except Exception as exc:  # noqa: BLE001
            log.warning("Stop of %s failed: %s", name, exc)
        # Clean up the registry slot
        registry._clients.pop(name, None)  # noqa: SLF001

    # Remove the stopped server's tools from the tool registry + notify brain
    await _sync_tools_for_server(request, registry, name, adding=False)
    await _publish_brain_tools_changed(request, f"mcp_disabled:{name}")

    return {"ok": True, "name": name, "enabled": False, "stopped": True}


@router.post("/{name}/start")
async def start_mcp(name: str, request: Request) -> dict[str, Any]:
    registry = _get_registry(request)
    if registry is None:
        raise HTTPException(503, "MCP registry not initialized.")

    if registry.get_spec(name) is None:
        raise HTTPException(404, f"MCP server '{name}' unknown.")

    if name in registry.active_clients():
        return {"ok": True, "name": name, "status": "already-running"}

    try:
        await registry.start_enabled([name])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Start failed: {exc}") from exc

    await _sync_tools_for_server(request, registry, name, adding=True)
    await _publish_brain_tools_changed(request, f"mcp_started:{name}")

    return {
        "ok": True,
        "name": name,
        "status": "running" if name in registry.active_clients() else "failed",
    }


@router.post("/{name}/stop")
async def stop_mcp(name: str, request: Request) -> dict[str, Any]:
    registry = _get_registry(request)
    if registry is None:
        raise HTTPException(503, "MCP registry not initialized.")

    active = registry.active_clients()
    if name not in active:
        return {"ok": True, "name": name, "status": "not-running"}

    try:
        await active[name].stop()
    except Exception as exc:  # noqa: BLE001
        log.warning("Stop of %s failed: %s", name, exc)
    registry._clients.pop(name, None)  # noqa: SLF001

    await _sync_tools_for_server(request, registry, name, adding=False)
    await _publish_brain_tools_changed(request, f"mcp_stopped:{name}")

    return {"ok": True, "name": name, "status": "stopped"}


@router.post("/import-claude-desktop")
async def import_claude_desktop(request: Request) -> dict[str, Any]:
    count, names, note = mcp_state.import_claude_desktop()

    registry = _get_registry(request)
    if registry is not None and count > 0:
        state = mcp_state.load_state()
        for name in names:
            spec_dict = state["custom"].get(name)
            if not spec_dict:
                continue
            try:
                registry.register_spec(MCPServerSpec(**spec_dict))
            except Exception as exc:  # noqa: BLE001
                log.warning("Custom spec %s could not be registered: %s", name, exc)

    return {"ok": True, "count": count, "added": names, "note": note}


class CredentialsPayload(BaseModel):
    credentials: dict[str, str]


@router.post("/{name}/check")
async def check_mcp(name: str, request: Request) -> dict[str, Any]:
    """Probe start: starts the server, verifies the handshake + tool listing,
    then stops it again. Does not change `enabled` — connection test only.

    Response: ``{"ok": bool, "tools_count": int, "error": str | None}``.
    """
    registry = _get_registry(request)
    if registry is None:
        raise HTTPException(503, "MCP registry not initialized.")

    spec = registry.get_spec(name)
    if spec is None:
        raise HTTPException(404, f"MCP server '{name}' unknown.")

    # If already active → just check the tool listing (cheap, no restart cost)
    active = registry.active_clients()
    if name in active:
        client = active[name]
        try:
            tools = await client.list_tools()
            registry.clear_error(name)
            return {
                "ok": True,
                "tools_count": len(tools),
                "error": None,
                "note": "already connected",
            }
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            registry._errors[name] = msg  # noqa: SLF001
            return {"ok": False, "tools_count": 0, "error": msg}

    # Probe: fresh client, start, list tools, stop. No persistent
    # state — ideal as a pre-enable check.
    from jarvis.mcp.client import MCPClient
    from jarvis.mcp.registry import _env_from_mcp_json

    env = _env_from_mcp_json(name)
    client = MCPClient(spec, env_overrides=env)
    try:
        await client.start()
        tools = await client.list_tools()
        registry.clear_error(name)
        return {"ok": True, "tools_count": len(tools), "error": None}
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        registry._errors[name] = msg  # noqa: SLF001
        return {"ok": False, "tools_count": 0, "error": msg}
    finally:
        try:
            await client.stop()
        except Exception:  # noqa: BLE001
            pass


@router.post("/{name}/credentials")
async def set_credentials(
    name: str, payload: CredentialsPayload, request: Request
) -> dict[str, Any]:
    """Writes one or more secrets to the Windows Credential Manager.

    Body: ``{"credentials": {"gmail_oauth_token": "..."}}``.
    Empty strings are ignored (allows partial updates).
    """
    registry = _get_registry(request)
    spec = registry.get_spec(name) if registry else None
    if spec is None:
        raise HTTPException(404, f"MCP server '{name}' unknown.")

    # Only accept keys the server actually needs — protects
    # against accidentally writing arbitrary secrets.
    allowed = set(spec.required_auth)
    written: list[str] = []
    rejected: list[str] = []
    failed: list[str] = []

    for key, value in payload.credentials.items():
        if not value:
            continue
        if key not in allowed:
            rejected.append(key)
            continue
        ok = set_secret(key, value)
        if ok:
            written.append(key)
        else:
            failed.append(key)

    return {
        "ok": not failed,
        "written": written,
        "rejected": rejected,
        "failed": failed,
    }


@router.get("/config/info")
async def config_info() -> dict[str, Any]:
    """Path + existence + raw content of mcp.json (for the UI editor)."""
    from jarvis.mcp.state import MCP_JSON_PATH

    exists = MCP_JSON_PATH.exists()
    content: str | None = None
    if exists:
        try:
            content = MCP_JSON_PATH.read_text(encoding="utf-8")
        except OSError:
            content = None
    return {
        "path": str(MCP_JSON_PATH),
        "exists": exists,
        "content": content,
    }


@router.put("/config/raw")
async def update_raw_config(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """Writes raw mcp.json — allows direct editing from the UI.

    The body is the complete root dict (``{"mcpServers": {...}}``). Only
    validates the basics — a syntax error would block the next boot.
    """
    if not isinstance(payload, dict) or "mcpServers" not in payload:
        raise HTTPException(400, "Payload needs the 'mcpServers' key.")
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        raise HTTPException(400, "'mcpServers' must be an object.")

    mcp_state.save_config(payload)

    # Reload registry from the freshly written file
    registry = _get_registry(request)
    if registry is not None:
        registry.load_from_mcp_json()

    await _publish_brain_tools_changed(request, "mcp_config_raw")

    return {"ok": True, "servers": len(servers)}


@router.delete("/{name}")
async def delete_mcp(name: str, request: Request) -> dict[str, Any]:
    """Removes a server's mcp.json entry.

    Bootstrap specs stay in the code — if the server exists there, the
    registry falls back to the code default spec after the delete (without
    overrides or a custom env). So the user still sees it as available, just
    without the enable flag.
    """
    registry = _get_registry(request)

    # Check the spec — block a mandatory server (bootstrap-only, no mcp.json entry)
    entry = mcp_state.get_server_entry(name)
    spec = registry.get_spec(name) if registry else None
    if entry is None and spec is not None and spec.mandatory:
        raise HTTPException(
            400, f"'{name}' is an essential bootstrap server — can only be disabled."
        )

    # Stop the active client
    if registry is not None:
        active = registry.active_clients()
        if name in active:
            try:
                await active[name].stop()
            except Exception:  # noqa: BLE001
                pass
            registry._clients.pop(name, None)  # noqa: SLF001

    # Delete the mcp.json entry
    removed = mcp_state.remove_server(name)

    # If no bootstrap exists, remove it entirely from the registry
    if registry is not None and spec is not None and not any(
        s.name == name for s in BOOTSTRAP_SERVERS
    ):
        registry._specs.pop(name, None)  # noqa: SLF001

    # After removing, reload the registry from mcp.json — this restores
    # bootstrap specs in case the mcp.json entry was an override.
    if registry is not None:
        registry.load_from_mcp_json()

    return {"ok": True, "name": name, "deleted": removed}
