"""MCPClient — wraps an MCP server (stdio/sse/http) as an asyncio client.

Handles lifecycle (start/stop), tool listing with a cache, and a
circuit breaker: after 3 consecutive failures the client is marked
"unhealthy" for 60 s and call_tool rejects further calls.

MCP-SDK quirk: the official client transports (``stdio_client``,
``sse_client``) are async context managers. We must set them up via
``AsyncExitStack`` manually so that ``start()`` and ``stop()`` work as
separate methods — ``async with`` does not work because ownership must
be held for the entire lifetime of the client.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from jarvis.core.config import DATA_DIR, PROJECT_ROOT, get_secret

from .registry import MCPServerSpec

log = logging.getLogger(__name__)

# Circuit-breaker defaults
_CB_THRESHOLD = 3
_CB_COOLDOWN_NS = 60_000_000_000  # 60 seconds

# Launcher binaries covered by a concrete "install Node.js" hint — the vast
# majority of stdio MCP servers are npm packages launched via ``npx``/``node``.
_NODE_LAUNCHERS = frozenset({"node", "npx", "npm"})


def _stdio_launcher_missing_message(spec_name: str, command: str) -> str:
    """Actionable message for a stdio MCP server whose launcher binary is
    absent from PATH (AP-23 wave-2 finding 10).

    Without this, a missing ``npx``/``node`` surfaces as a raw
    ``FileNotFoundError: [Errno 2] No such file or directory: 'npx'`` on the
    plugin badge (caught generically at ``mcp/registry.py``'s
    ``except Exception as e: error_msg = f"{type(e).__name__}: {e}"``). Mirrors
    the honest launcher check already used at connect-time in
    ``marketplace_routes._mcp_live`` (``shutil.which`` on the stdio
    ``install`` command) so the badge and the live client agree on what
    "missing" means and both name the actual missing binary.
    """
    if command in _NODE_LAUNCHERS:
        return (
            f"{spec_name}: install Node.js 18+ to use this plugin "
            f"('{command}' not found on PATH)"
        )
    return (
        f"{spec_name}: '{command}' not found on PATH — install it to use "
        "this plugin"
    )


def _read_call_timeout_s() -> float:
    """Per-call timeout (seconds), ENV-overridable with a safe fallback.

    Default 20 s. Rationale: a hung MCP server must NOT block the whole voice
    turn (~35 s hangs were observed), but the ceiling must not guillotine
    legitimately slower tools — a remote http MCP doing real network I/O
    (search, a DB query, a web fetch) can take well over 5-10 s. 20 s sits
    comfortably above normal tool latency yet still trips long before a voice
    turn feels dead, and a repeatedly-slow server tips into the circuit
    breaker (3 strikes) and drops off the surface on its own. Tunable per host
    via ``JARVIS_MCP_CALL_TIMEOUT_S`` (a headless VPS with a slow uvx cold
    start can raise it) without code or config edits.
    """
    raw = os.environ.get("JARVIS_MCP_CALL_TIMEOUT_S")
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            log.warning("JARVIS_MCP_CALL_TIMEOUT_S=%r is not a number; using 20s", raw)
    return 20.0


# Module-level so a test can monkeypatch it and call_tool re-reads it each call.
_CALL_TIMEOUT_S = _read_call_timeout_s()

# Matches ``$NAME`` secret placeholders inside http header values, e.g.
# ``"Bearer $ZAPIER_TOKEN"`` → the ``ZAPIER_TOKEN`` group.
_SECRET_TOKEN_RE = re.compile(r"\$([A-Z_][A-Z0-9_]*)")


class MCPClient:
    """An active connection handle to an MCP server."""

    def __init__(
        self,
        spec: MCPServerSpec,
        env_overrides: dict[str, str] | None = None,
    ) -> None:
        self.spec = spec
        self._env_overrides = env_overrides or {}
        self._exit_stack: AsyncExitStack | None = None
        self._session: Any | None = None  # mcp.ClientSession (typed locally)
        self._tools_cache: list[dict[str, Any]] = []
        self._circuit_breaker_failures = 0
        self._disabled_until_ns: int = 0

    # ---- Lifecycle ----------------------------------------------------

    async def start(self) -> None:
        """Set up the transport, initialise the session, and cache the tool list."""
        if self._session is not None:
            return  # idempotent

        # Lazy imports — MCP lib is optional-but-expected (requirements.txt).
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        stack = AsyncExitStack()
        try:
            if self.spec.transport == "stdio":
                command, args, env = self._resolve_install_command()
                if shutil.which(command) is None:
                    # Fail actionable, not raw: without this check
                    # ``stdio_client`` spawns the launcher itself and a
                    # missing binary surfaces as a bare FileNotFoundError.
                    raise FileNotFoundError(
                        _stdio_launcher_missing_message(self.spec.name, command)
                    )
                params = StdioServerParameters(
                    command=command,
                    args=args,
                    env=env,
                )
                read, write = await stack.enter_async_context(stdio_client(params))
            elif self.spec.transport == "sse":
                from mcp.client.sse import sse_client

                command, args, _env = self._resolve_install_command()
                # install_command for SSE transport = [url] (by convention)
                url = args[0] if args else command
                read, write = await stack.enter_async_context(sse_client(url))
            elif self.spec.transport == "http":
                from mcp.client.streamable_http import streamablehttp_client

                if not self.spec.url:
                    raise ValueError(
                        f"{self.spec.name}: http transport requires a 'url'"
                    )
                headers = self._resolve_headers()
                # streamablehttp_client yields a 3-tuple
                # (read, write, get_session_id) — unlike the 2-tuple stdio/sse
                # transports. The session-id callback is unused here.
                read, write, _ = await stack.enter_async_context(
                    streamablehttp_client(self.spec.url, headers=headers or None)
                )
            else:
                raise NotImplementedError(
                    f"Transport {self.spec.transport!r} not (yet) supported"
                )

            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            # Cache tools — saves round trips on every list_tools() call.
            tools_result = await session.list_tools()
            self._tools_cache = [_tool_to_dict(t) for t in tools_result.tools]

            self._exit_stack = stack
            self._session = session
            log.info(
                "MCPClient[%s] ready — %d tools",
                self.spec.name,
                len(self._tools_cache),
            )
        except Exception:
            await stack.aclose()
            raise

    async def stop(self) -> None:
        """Close the session and transport cleanly."""
        if self._exit_stack is None:
            return
        try:
            await self._exit_stack.aclose()
        except Exception as e:  # noqa: BLE001
            log.warning("MCPClient[%s] stop error: %s", self.spec.name, e)
        finally:
            self._exit_stack = None
            self._session = None

    # ---- Interaction ----------------------------------------------------

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the cached tool list (list of dicts)."""
        return list(self._tools_cache)

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Invoke a tool on the server. Respects the circuit breaker."""
        now = time.time_ns()
        if self._disabled_until_ns > now:
            raise RuntimeError(
                f"{self.spec.name} circuit-breaker open "
                f"(cooldown {(self._disabled_until_ns - now) // 1_000_000_000}s)"
            )
        if self._session is None:
            raise RuntimeError(f"{self.spec.name}: MCPClient not started")

        try:
            # Per-call timeout: the MCP SDK has no built-in ceiling, so a server
            # that hangs on a tool call would block the whole turn forever. We
            # bound it here and RAISE on expiry; the raise falls into the
            # ``except Exception`` below, which counts the failure — so a
            # repeatedly-hung server trips the circuit breaker and drops from the
            # surface instead of hanging every future turn. We re-read the module
            # global each call so the ENV override / a test monkeypatch applies.
            timeout_s = _CALL_TIMEOUT_S
            try:
                result = await asyncio.wait_for(
                    self._session.call_tool(name, args),
                    timeout=timeout_s,
                )
            except TimeoutError as exc:
                # Clean, honest message so the brain's tool loop surfaces it and
                # the voice layer can speak its timeout phrase — not a bare crash.
                raise RuntimeError(
                    f"{self.spec.name} tool {name!r} timed out after "
                    f"{timeout_s:.0f}s (server not responding)"
                ) from exc
            # MCP-SDK quirk: server-side tool exceptions do NOT propagate as
            # Python exceptions; instead they arrive as a CallToolResult with
            # isError=True. Raise into the one shared failure counter below;
            # counting here as well would make one server error count twice.
            if getattr(result, "isError", False):
                raise RuntimeError(
                    _extract_error_text(result)
                    or f"MCP tool {name} returned isError=True"
                )
            self._circuit_breaker_failures = 0
            return result
        except Exception:
            self._circuit_breaker_failures += 1
            if self._circuit_breaker_failures >= _CB_THRESHOLD:
                self._disabled_until_ns = time.time_ns() + _CB_COOLDOWN_NS
                log.warning(
                    "MCPClient[%s] circuit breaker OPEN (60s) after %d failures",
                    self.spec.name,
                    self._circuit_breaker_failures,
                )
            raise

    # ---- Health -------------------------------------------------------

    @property
    def is_healthy(self) -> bool:
        """True if the session is alive and the circuit breaker is not open."""
        return (
            self._session is not None
            and self._disabled_until_ns <= time.time_ns()
        )

    # ---- Helpers ------------------------------------------------------

    def _resolve_headers(self) -> dict[str, str]:
        """Resolve ``$SECRET`` placeholders inside http header values.

        ``"Bearer $ZAPIER_TOKEN"`` → ``"Bearer <secret>"`` via ``get_secret``.
        The literal placeholder is kept when the secret cannot be resolved, so
        a misconfiguration surfaces (a 401 from the server) instead of silently
        sending ``"Bearer "``.
        """

        def _sub(match: re.Match[str]) -> str:
            key = match.group(1)
            secret = get_secret(key, env_fallback=key)
            return secret if secret else match.group(0)

        resolved: dict[str, str] = {}
        for key, value in (self.spec.headers or {}).items():
            if not isinstance(value, str):
                continue
            resolved[key] = _SECRET_TOKEN_RE.sub(_sub, value)
        return resolved

    def _resolve_install_command(self) -> tuple[str, list[str], dict[str, str]]:
        """Expand placeholders such as {PROJECT_ROOT} / {DATA_DIR} and
        merge env_overrides with the system environment.
        """
        import os

        raw = list(self.spec.install_command)
        if not raw:
            raise ValueError(f"{self.spec.name}: install_command is empty")

        def _expand(s: str) -> str:
            return (
                s.replace("{PROJECT_ROOT}", str(PROJECT_ROOT))
                 .replace("{DATA_DIR}", str(Path(DATA_DIR)))
            )

        expanded = [_expand(p) for p in raw]
        command, *args = expanded

        # env: system env as the base, overrides on top. String values only.
        env = dict(os.environ)
        env.update({k: str(v) for k, v in self._env_overrides.items()})
        return command, args, env


# ----------------------------------------------------------------------
# Utilities
# ----------------------------------------------------------------------

def _extract_error_text(result: Any) -> str:
    """Extract the error text from a CallToolResult(isError=True)."""
    content = getattr(result, "content", None) or []
    parts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    return " | ".join(parts)


def _tool_to_dict(tool: Any) -> dict[str, Any]:
    """Normalise an MCP ``Tool`` object to a plain dict.

    The MCP lib returns pydantic models (with ``.model_dump()``); we
    deliberately map only the fields we use — this decouples us from
    SDK changes.
    """
    if isinstance(tool, dict):
        return dict(tool)
    return {
        "name": getattr(tool, "name", ""),
        "description": getattr(tool, "description", "") or "",
        "inputSchema": getattr(tool, "inputSchema", None) or {},
    }
