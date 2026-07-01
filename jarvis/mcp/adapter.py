"""Adapter: expose MCP tools as jarvis.core.protocols.Tool instances.

Each MCP tool is made structurally compatible with the `Tool` protocol via
`MCPToolAdapter`, so it automatically runs through the risk-tier flow
(ActionProposed → ActionApproved/Denied → execute → ActionExecuted).

Side-effect on construction: each MCPToolAdapter also registers a
``Capability`` with the global CapabilityRegistry so that the voice path
knows this MCP tool exists.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from jarvis.core.capabilities import Capability, get_registry
from jarvis.core.protocols import ExecutionContext, RiskTier, ToolResult

from .client import MCPClient
from .registry import MCPRegistry

log = logging.getLogger(__name__)


def _verbs_from_description(description: str) -> tuple[str, ...]:
    """Best-effort extraction of action verbs from an MCP tool description.

    Scans the description for known English imperative verbs that indicate
    the tool performs an action.  Returns a small tuple; never empty (falls
    back to a generic "use" verb so resolve_intent can still match).
    """
    _KNOWN_VERBS = (
        "send", "create", "delete", "update", "list", "get", "fetch",
        "read", "write", "search", "query", "insert", "execute", "run",
        "upload", "download", "publish", "schedule", "post", "set",
        "add", "remove", "edit", "modify", "retrieve", "find",
    )
    desc_lower = description.lower()
    found = tuple(v for v in _KNOWN_VERBS if re.search(r"\b" + v + r"\b", desc_lower))
    return found if found else ("use",)


def _objects_from_tool_name(namespaced_name: str) -> tuple[str, ...]:
    """Derive object/domain nouns from a namespaced MCP tool name.

    Example: ``"gmail/send_mail"`` → ``("gmail", "send_mail", "mail", "email")``.
    """
    parts = re.split(r"[/_\-]", namespaced_name.lower())
    return tuple(dict.fromkeys(p for p in parts if len(p) > 1))


class MCPToolAdapter:
    """Wraps a single MCP tool as a Jarvis tool.

    The tool name has the form `"<server-name>/<mcp-tool-name>"` to prevent
    namespace collisions between servers.
    """

    def __init__(
        self,
        client: MCPClient,
        mcp_tool_def: dict[str, Any],
        risk_tier: RiskTier = "monitor",
    ) -> None:
        self._client = client
        mcp_name = mcp_tool_def.get("name", "")
        self._mcp_tool_name = mcp_name
        self.name: str = f"{client.spec.name}/{mcp_name}"
        self.schema: dict[str, Any] = dict(mcp_tool_def.get("inputSchema") or {})
        # MCP servers supply their own descriptions. We prefix with
        # [ACTION-ONLY] + a research warning so the LLM does not pick this tool
        # for general research about the topic the server covers. Concrete
        # example: the Supabase MCP would suggest an SQL call against the prod DB
        # for "research Supabase" if the description only says
        # "Execute SQL against Supabase".
        raw_desc = mcp_tool_def.get("description", "") or ""
        self.description: str = (
            f"[ACTION-ONLY · MCP: {client.spec.name}] {raw_desc} "
            f"Performs operations on the connected {client.spec.name} system. "
            f"USE ONLY for targeted actions, NOT for general research "
            f"about {client.spec.name} as a topic — use search_web for that."
        )
        # Flag for the tool-use-loop guard: MCP tools are always action-centric.
        self.is_action_tool: bool = True
        self.risk_tier: RiskTier = risk_tier

        # Register with the global CapabilityRegistry so the voice path
        # knows this MCP tool exists and resolve_intent can match it.
        _cap_id = f"mcp.{self.name}"
        _raw_desc = mcp_tool_def.get("description", "") or ""
        _cap = Capability(
            id=_cap_id,
            source="mcp",
            verbs=_verbs_from_description(_raw_desc),
            objects=_objects_from_tool_name(self.name),
            description=f"[MCP:{client.spec.name}] {_raw_desc[:200].strip()}",
            risk_tier=risk_tier,  # type: ignore[arg-type]
            requires_evidence=True,
        )
        try:
            get_registry().register(_cap)
        except Exception:  # noqa: BLE001
            log.debug("MCPToolAdapter: capability registration failed for %s", _cap_id)

    async def execute(
        self, args: dict[str, Any], ctx: ExecutionContext
    ) -> ToolResult:
        """Call the MCP tool and map the result to a `ToolResult`.

        The supervisor/orchestrator is responsible for publishing
        `ActionProposed` beforehand and obtaining approval according to the
        tier — this adapter does not publish events itself, so the call
        semantics remain uniform with native tools.
        """
        start_ns = time.time_ns()
        try:
            raw = await self._client.call_tool(self._mcp_tool_name, args)
            return ToolResult(
                success=True,
                output=_normalize_mcp_result(raw),
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "MCPTool %s error after %d ms: %s",
                self.name,
                (time.time_ns() - start_ns) // 1_000_000,
                e,
            )
            return ToolResult(success=False, output=None, error=str(e))


def _normalize_mcp_result(raw: Any) -> Any:
    """Normalize an MCP CallToolResult into a JSON-serializable value."""
    if raw is None or isinstance(raw, (str, int, float, bool, list, dict)):
        return raw
    # mcp.types.CallToolResult: .content (list[TextContent|...]) + .isError
    content = getattr(raw, "content", None)
    if content is not None:
        out: list[Any] = []
        for item in content:
            text = getattr(item, "text", None)
            if text is not None:
                out.append(text)
            else:
                # ImageContent/EmbeddedResource etc. — serialize flat
                dump = getattr(item, "model_dump", None)
                out.append(dump() if callable(dump) else repr(item))
        return out[0] if len(out) == 1 else out
    dump = getattr(raw, "model_dump", None)
    if callable(dump):
        return dump()
    return repr(raw)


# ----------------------------------------------------------------------
# Registry-Helper
# ----------------------------------------------------------------------

async def register_mcp_tools_in_registry(
    mcp_registry: MCPRegistry,
    tool_registry: Any,
    *,
    default_risk_tier: RiskTier = "monitor",
) -> list[MCPToolAdapter]:
    """Create adapters for all tools of every active MCP client and register
    them in the main tool registry.

    `tool_registry` is structurally flexible — we expect a `register(tool)`
    method, or alternatively `add(tool)`, or a mapping-style
    `__setitem__(name, tool)`. This keeps the helper decoupled from the final
    tool-registry API (which is implemented in a separate phase).
    """
    adapters: list[MCPToolAdapter] = []
    for client in mcp_registry.active_clients().values():
        for mcp_tool in await client.list_tools():
            adapter = MCPToolAdapter(client, mcp_tool, risk_tier=default_risk_tier)
            _insert_into_registry(tool_registry, adapter)
            adapters.append(adapter)
    return adapters


def _insert_into_registry(registry: Any, tool: MCPToolAdapter) -> None:
    """Structural insert strategy (register/add/__setitem__)."""
    for method_name in ("register", "add"):
        fn = getattr(registry, method_name, None)
        if callable(fn):
            fn(tool)
            return
    setitem = getattr(registry, "__setitem__", None)
    if callable(setitem):
        registry[tool.name] = tool  # type: ignore[index]
        return
    raise TypeError(
        f"tool_registry vom Typ {type(registry).__name__} bietet weder "
        "register()/add() noch __setitem__"
    )
