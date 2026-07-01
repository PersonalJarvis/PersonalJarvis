"""MCP server registry: specs and runtime management of active clients.

``MCPServerSpec`` describes an installable MCP server (install command,
auth keys, mandatory/optional). The global ``BOOTSTRAP_SERVERS`` list is
the authoritative catalogue of all servers offered during the first-run
wizard. ``MCPRegistry`` holds the active clients at runtime and can start
or stop them as a group.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .client import MCPClient

log = logging.getLogger(__name__)

# Per-server start ceiling (seconds). A server that hangs on initialize /
# list_tools must not hang its start task forever — mirrors the 60 s guard in
# ``bootstrap.verify_server``. On expiry we log per-server and move on; the
# parallel start of the other servers is never aborted.
_START_TIMEOUT_S = 60.0


# ----------------------------------------------------------------------
# Spec
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MCPServerSpec:
    """Describes an installable MCP server (static, immutable)."""

    name: str                               # internal ID, e.g. "filesystem-mcp"
    display: str                            # user-facing display name
    description: str                        # one-line description
    install_command: list[str]              # argv for subprocess (uvx/npx/...)
    required_auth: list[str] = field(default_factory=list)  # keyring-Keys
    transport: Literal["stdio", "sse", "http"] = "stdio"
    mandatory: bool = False
    platform_notes: str = ""
    # Remote-transport endpoint (http/sse). Unused for stdio.
    url: str | None = None
    # HTTP headers for the http transport. Values may contain ``$SECRET``
    # placeholders resolved against the Credential Manager at connect time.
    headers: dict[str, str] = field(default_factory=dict)


# ----------------------------------------------------------------------
# BOOTSTRAP_SERVERS
# ----------------------------------------------------------------------
# Intentionally empty: user decision 2026-04-22. The previously pre-curated
# 8 servers (filesystem-mcp, git-mcp, memory-mcp, windows-mcp, gmail-mcp, ...)
# were a promise that was not kept — some uvx/npx packages do not exist under
# those names and OAuth flows were missing. All servers now come exclusively
# from mcp.json, which the user edits directly or via Claude Desktop import.
BOOTSTRAP_SERVERS: list[MCPServerSpec] = []


# ----------------------------------------------------------------------
# Runtime-Registry
# ----------------------------------------------------------------------

class MCPRegistry:
    """Holds specs and active clients; starts and stops them as a group."""

    def __init__(self) -> None:
        # Specs are stored as a dict keyed by name — third-party specs can
        # be registered at runtime (for tests and plugin extensions).
        self._specs: dict[str, MCPServerSpec] = {s.name: s for s in BOOTSTRAP_SERVERS}
        self._clients: dict[str, MCPClient] = {}
        # Last start error per server name so the UI can show a concrete
        # reason instead of just "not running".
        self._errors: dict[str, str] = {}

    # ---- Specs --------------------------------------------------------

    def register_spec(self, spec: MCPServerSpec) -> None:
        """Register an additional spec (overwrites an existing one with the same name)."""
        self._specs[spec.name] = spec

    def all_specs(self) -> list[MCPServerSpec]:
        """List of all known specs (mandatory + optional, including registered ones)."""
        return list(self._specs.values())

    def get_spec(self, name: str) -> MCPServerSpec | None:
        return self._specs.get(name)

    # ---- mcp.json-Integration ----------------------------------------

    def load_from_mcp_json(self) -> None:
        """Load user overrides and custom servers from ``mcp.json``.

        - Entries with ``command`` override/extend bootstrap specs.
        - Entries WITHOUT ``command`` (pure enable overrides) are left
          at the existing bootstrap spec and are not touched here.
        """
        from .state import load_config  # lazy, prevents circular import

        cfg = load_config()
        for name, entry in cfg.get("mcpServers", {}).items():
            if not isinstance(entry, dict):
                continue
            transport = entry.get("transport", "stdio")
            command = entry.get("command")
            url = entry.get("url")
            if transport == "http":
                # Remote HTTP servers carry a ``url`` instead of a subprocess
                # ``command``; without one there is nothing to connect to.
                if not url:
                    continue
                install_command: list[str] = []
            else:
                if not command:
                    continue
                install_command = [command, *list(entry.get("args", []))]
            try:
                spec = MCPServerSpec(
                    name=name,
                    display=entry.get("display") or name.replace("-", " ").title(),
                    description=entry.get("description", ""),
                    install_command=install_command,
                    required_auth=list(entry.get("required_auth", [])),
                    transport=transport,
                    mandatory=False,
                    platform_notes=entry.get("platform_notes", ""),
                    url=url,
                    headers=dict(entry.get("headers", {})),
                )
            except (TypeError, ValueError) as exc:
                log.warning("mcp.json[%s] invalid: %s", name, exc)
                continue
            self._specs[name] = spec

    # ---- Error-Tracking ----------------------------------------------

    def last_error(self, name: str) -> str | None:
        """Last stored start error for a server, or None."""
        return self._errors.get(name)

    def clear_error(self, name: str) -> None:
        self._errors.pop(name, None)

    # ---- Clients ------------------------------------------------------

    def active_clients(self) -> dict[str, MCPClient]:
        """Mapping of name → MCPClient for all currently running clients."""
        return dict(self._clients)

    async def start_enabled(self, enabled_names: list[str]) -> None:
        """Start all specified servers in parallel. Errors are logged and stored
        per server in ``self._errors``, but the overall start is not aborted —
        a single broken server must not block the entire pipeline.
        """
        from .client import MCPClient  # local import to avoid circular dependency
        from .notification_filter import install_notification_log_filter

        # A server may emit a non-standard notification method (observed live:
        # ``method='log'``) that the SDK's strict ``ServerNotification`` union
        # rejects, spamming the log with a 19-error Pydantic dump per frame.
        # Installing the tolerant filter before any client starts keeps that
        # quiet (idempotent — safe to call on every start).
        install_notification_log_filter()

        async def _start_one(spec: MCPServerSpec) -> None:
            # Pull env overrides from mcp.json — allows setting OAuth tokens
            # or API keys per server without code changes.
            env_overrides = _env_from_mcp_json(spec.name)
            client = MCPClient(spec, env_overrides=env_overrides)
            try:
                # Per-server start timeout: a server hanging on initialize /
                # list_tools must not block this task forever. asyncio.wait_for
                # cancels the hung start on expiry and raises TimeoutError, which
                # the ``except`` below records per server — the gather over the
                # other servers keeps running, so one hung server never stalls
                # the parallel boot.
                await asyncio.wait_for(client.start(), timeout=_START_TIMEOUT_S)
                self._clients[spec.name] = client
                self._errors.pop(spec.name, None)
                log.info("MCP server %s started", spec.name)
            except TimeoutError:
                error_msg = f"start timeout ({_START_TIMEOUT_S:.0f}s)"
                self._errors[spec.name] = error_msg
                log.error("MCP-Server %s Startfehler: %s", spec.name, error_msg)
            except Exception as e:  # noqa: BLE001
                error_msg = f"{type(e).__name__}: {e}"
                self._errors[spec.name] = error_msg
                log.error("MCP-Server %s Startfehler: %s", spec.name, e)

        tasks = [
            _start_one(self._specs[n])
            for n in enabled_names
            if n in self._specs
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=False)

    async def stop_all(self) -> None:
        """Stop all active clients in parallel (best effort)."""
        clients = list(self._clients.values())
        self._clients.clear()
        if not clients:
            return
        await asyncio.gather(
            *(c.stop() for c in clients),
            return_exceptions=True,
        )


def _env_from_mcp_json(name: str) -> dict[str, str] | None:
    """Read the ``env`` block from mcp.json for a server, or None.

    Values that start with ``$`` are resolved via ``get_secret()``
    (e.g. ``"$GMAIL_OAUTH_TOKEN"`` → lookup in the Credential Manager).
    """
    from jarvis.core.config import get_secret

    from .state import get_server_entry

    entry = get_server_entry(name)
    if not entry:
        return None
    raw_env = entry.get("env") or {}
    if not isinstance(raw_env, dict) or not raw_env:
        return None

    resolved: dict[str, str] = {}
    for key, value in raw_env.items():
        if not isinstance(value, str):
            continue
        if value.startswith("$"):
            secret_key = value[1:]
            looked_up = get_secret(secret_key, env_fallback=secret_key)
            if looked_up:
                resolved[key] = looked_up
                continue
            log.warning("env[%s]=%s could not be resolved for MCP %s",
                        key, value, name)
            continue
        resolved[key] = value
    return resolved or None
