"""Jarvis' own tools, offered outwards as an MCP server.

Every other MCP module in this package points INWARDS: Jarvis is the client
that mounts somebody else's server (``client.py``, ``loader.py``,
``registry.py``), or projects a connected plugin into a worker's config
(``claude_export.py``, ``marketplace/mcp_bridge.py``). This module is the one
that points OUT — it lets an agent session that Jarvis spawned reach back in
and drive Jarvis itself.

That is what turns the typed chat from "a coding CLI that happens to run in
Jarvis' window" into Jarvis with a keyboard instead of a microphone: the model
paying with the person's subscription runs its own agent loop, but its hands
are the hands the voice has — ``open-app``, ``google-calendar``,
``wiki-recall``, ``switch-provider``, every registered tool plugin.

**The safety seam is structural, not polite.** Tools are listed from
:class:`~jarvis.core.protocols.SupervisorToolGateway` and executed through it,
which is the only authorized execution path in the codebase (AP-3). A caller
here cannot reach a tool's ``execute`` directly, so risk tiers, the approval
workflow and the audit log apply exactly as they do on the voice path — there
is no second door to keep in sync.

Transport is Streamable HTTP, mounted on the app's own web server
(``jarvis.ui.web.mcp_server_routes``) rather than a stdio child process. The
gateway lives in the running app's memory; a child process would only be able
to talk back over the same HTTP anyway, one hop later.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final
from uuid import uuid4

log = logging.getLogger(__name__)

#: What ``SupervisorToolRequest.origin`` is stamped with. The risk evaluator and
#: the audit log use it to tell a chat-driven action from a spoken one.
CHAT_ORIGIN: Final[str] = "agent-chat"

#: Tools never offered to a chat session, by name. A spawn vehicle would let the
#: session start a background worker that starts another one — the recursion the
#: router tiers exist to prevent (AP-5/AP-14). The session IS the heavy worker
#: here; it has no reason to hire one.
_WITHHELD: Final[frozenset[str]] = frozenset(
    {
        "spawn-worker",
        "spawn_worker",
        "multi-spawn",
        "multi_spawn",
        "spawn-subagents",
        "spawn_subagents",
        "dispatch-to-harness",
        "dispatch_to_harness",
        "dispatch-with-review",
        "dispatch_with_review",
        "dispatch-to-admin",
        "dispatch_to_admin",
    }
)

#: MCP requires tool names to match ``[a-zA-Z0-9_-]{1,128}``. Jarvis names
#: already do; anything else is dropped rather than silently renamed, because a
#: renamed tool is a tool the model cannot be told about in a prompt.
_NAME_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _usable_name(name: str) -> bool:
    return bool(name) and len(name) <= 128 and set(name) <= _NAME_OK


def _gateway() -> Any | None:
    from jarvis.core import runtime_refs

    return runtime_refs.get_supervisor_tool_gateway()


def offered_tools() -> list[Any]:
    """The catalog entries a chat session may call, gateway order preserved."""
    gateway = _gateway()
    if gateway is None:
        return []
    try:
        catalog = gateway.catalog()
    except Exception:  # noqa: BLE001 — a broken catalog offers nothing, it never crashes the server
        log.warning("jarvis MCP: tool catalog unavailable", exc_info=True)
        return []
    return [
        entry
        for entry in catalog
        if _usable_name(str(entry.name)) and str(entry.name) not in _WITHHELD
    ]


def _render(result: Any) -> str:
    """A ToolResult as the text the model reads back."""
    if result is None:
        return "The tool returned nothing."
    output = getattr(result, "output", None)
    error = getattr(result, "error", None)
    if not getattr(result, "success", False):
        return f"Tool failed: {error or 'no reason given'}"
    if output is None:
        return "Done."
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(output)


def build_server() -> Any:
    """A low-level MCP server bound to the live tool gateway.

    Built per process, not per session: the handlers read the gateway on every
    call, so a tool that connects later (an MCP source, a marketplace plugin, a
    CLI that finished bootstrapping) shows up without a rebuild.
    """
    import mcp.types as types
    from mcp.server.lowlevel import Server

    server: Any = Server("jarvis")

    @server.list_tools()  # type: ignore[misc, no-untyped-call]
    async def _list_tools() -> list[Any]:
        tools: list[Any] = []
        for entry in offered_tools():
            schema = entry.input_schema
            if not isinstance(schema, dict) or not schema:
                schema = {"type": "object", "properties": {}}
            tools.append(
                types.Tool(
                    name=str(entry.name),
                    description=str(entry.description or entry.name),
                    inputSchema=schema,
                )
            )
        return tools

    @server.call_tool()  # type: ignore[misc, no-untyped-call]
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        from jarvis.core.protocols import SupervisorToolRequest

        gateway = _gateway()
        if gateway is None:
            return [types.TextContent(type="text", text="Jarvis is still starting up.")]
        if name in _WITHHELD:
            return [
                types.TextContent(
                    type="text",
                    text=(
                        f"{name} is not available from the chat — you ARE the worker. "
                        "Do the work yourself."
                    ),
                )
            ]
        request = SupervisorToolRequest(
            trace_id=uuid4(),
            origin=CHAT_ORIGIN,
            user_utterance="",
            rationale="agent chat tool call",
        )
        try:
            result = await gateway.execute(name, dict(arguments or {}), request)
        except Exception as exc:  # noqa: BLE001 — a tool failure is data for the model, not a crash
            log.warning("jarvis MCP: %s raised", name, exc_info=True)
            return [
                types.TextContent(type="text", text=f"Tool failed: {type(exc).__name__}: {exc}")
            ]
        return [types.TextContent(type="text", text=_render(result))]

    return server


__all__ = ["CHAT_ORIGIN", "build_server", "offered_tools"]
