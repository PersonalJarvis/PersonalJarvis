"""Public, versioned gateway to the Brain Manager's live tool catalog.

Higher layers receive only secret-free descriptors and execute by name. The
concrete ``Tool`` objects and ``ToolExecutor`` remain inside the brain layer,
and every call resolves against the current catalog immediately before use.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import UUID

from jarvis.core.protocols import (
    RiskTier,
    SupervisorToolDescriptor,
    SupervisorToolRequest,
    Tool,
    ToolResult,
)

_VALID_RISK_TIERS = frozenset({"safe", "monitor", "ask", "block"})


class BrainSupervisorToolGateway:
    """Adapter that keeps Brain Manager implementation details private."""

    def __init__(
        self,
        manager: Any,
        *,
        session_tool: Callable[[str], Awaitable[Tool | None]] | None = None,
        browser_tool: Callable[[str], Awaitable[Tool | None]] | None = None,
        session_tools: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any] | None]]
        | None = None,
    ) -> None:
        self._manager = manager
        self._session_tool = session_tool
        self._browser_tool = browser_tool
        self._session_tools = session_tools
        self._lock = threading.Lock()
        self._fingerprint: tuple[tuple[str, int], ...] = ()
        self._catalog_version = 0

    def _live_tools(self) -> dict[str, Any]:
        tools = getattr(self._manager, "_tools", None)
        if not isinstance(tools, dict):
            return {}
        for _attempt in range(2):
            try:
                return dict(tools)
            except RuntimeError:
                continue
        return {}

    def catalog(self) -> tuple[SupervisorToolDescriptor, ...]:
        tools = self._live_tools()
        fingerprint = tuple(sorted((str(name), id(tool)) for name, tool in tools.items()))
        with self._lock:
            if fingerprint != self._fingerprint:
                self._fingerprint = fingerprint
                self._catalog_version += 1

        return self._describe(tools)

    @staticmethod
    def _describe(tools: dict[str, Any]) -> tuple[SupervisorToolDescriptor, ...]:
        descriptors: list[SupervisorToolDescriptor] = []
        for name, tool in sorted(tools.items()):
            if not callable(getattr(tool, "execute", None)):
                continue
            schema = getattr(tool, "schema", None)
            if not isinstance(schema, dict):
                schema = {"type": "object", "properties": {}}
            raw_risk_tier = str(getattr(tool, "risk_tier", "monitor"))
            risk_tier = cast(
                RiskTier,
                raw_risk_tier if raw_risk_tier in _VALID_RISK_TIERS else "monitor",
            )
            hook = getattr(tool, "risk_tier_for_args", None)
            impact = getattr(tool, "describe_args", None)
            descriptors.append(
                SupervisorToolDescriptor(
                    name=str(name),
                    description=str(getattr(tool, "description", "")),
                    input_schema=copy.deepcopy(schema),
                    risk_tier=risk_tier,
                    is_action_tool=bool(getattr(tool, "is_action_tool", False)),
                    yields_instructions_only=bool(getattr(tool, "yields_instructions_only", False)),
                    risk_tier_for_args=hook if callable(hook) else None,
                    describe_args=impact if callable(impact) else None,
                )
            )
        return tuple(descriptors)

    @property
    def catalog_version(self) -> int:
        self.catalog()
        with self._lock:
            return self._catalog_version

    def _voice_tools(self) -> dict[str, Any]:
        from jarvis.harness.computer_use_context import peek_computer_use_context
        from jarvis.plugins.tool.live_screen import LiveScreenTool

        tools = self._live_tools()
        context = peek_computer_use_context()
        if context is not None:
            tools.update(context.tools or {})
        tools["screen_snapshot"] = LiveScreenTool()
        return tools

    def voice_catalog(self) -> tuple[SupervisorToolDescriptor, ...]:
        """Live models can see desktop primitives without a second tool model."""
        return self._describe(self._voice_tools())

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        request: SupervisorToolRequest,
    ) -> ToolResult:
        if request.cancel_token is not None and request.cancel_token.is_cancelled():
            return ToolResult(
                success=False,
                output=None,
                error=f"cancelled ({request.cancel_token.reason or 'requested'})",
            )

        tools = self._voice_tools() if request.origin == "realtime" else self._live_tools()
        ref = str(request.config_snapshot.get("approval_ref") or "")
        scoped = None
        if (
            self._session_tools is not None
            and request.origin == "agent-chat"
            and ref.startswith("agent-chat:")
        ):
            scoped = await self._session_tools(ref.removeprefix("agent-chat:"), tools)
        tool = (scoped if scoped is not None else tools).get(name)
        if scoped is None and name in {"coding-session", "society_browser"}:
            # Never install this controller in the global worker tool set.
            # The authenticated chat transport supplies its canonical session reference.
            ref = str(request.config_snapshot.get("approval_ref") or "")
            resolver = self._browser_tool if name == "society_browser" else self._session_tool
            tool = (
                await resolver(ref.removeprefix("agent-chat:"))
                if resolver is not None
                and request.origin == "agent-chat"
                and ref.startswith("agent-chat:")
                else None
            )
        executor = getattr(self._manager, "_tool_executor", None)
        if tool is None or not callable(getattr(tool, "execute", None)):
            return ToolResult(
                success=False,
                output=None,
                error="Supervisor tool is no longer available.",
            )
        if executor is None or not callable(getattr(executor, "execute", None)):
            return ToolResult(
                success=False,
                output=None,
                error="Supervisor tool gateway is not ready.",
            )

        config_snapshot = dict(request.config_snapshot)
        ref = str(config_snapshot.get("approval_ref") or "")
        if ref.startswith("agent-chat:"):
            from jarvis.core.tool_read_only import chat_is_read_only

            if chat_is_read_only(ref.removeprefix("agent-chat:")):
                config_snapshot["chat_read_only"] = True
        config_snapshot.update(
            {
                "tool_origin": request.origin,
                "mission_id": request.mission_id,
                "worker_id": request.worker_id,
            }
        )
        return await executor.execute(
            tool,
            dict(arguments),
            user_utterance=request.user_utterance,
            config_snapshot=config_snapshot,
            trace_id=request.trace_id,
            rationale=request.rationale,
            cancel_token=request.cancel_token,
        )

    async def session_catalog(self, session_id: str) -> tuple[SupervisorToolDescriptor, ...]:
        """Resolve the permitted session-owned tools without exposing them globally."""
        if self._session_tools is not None:
            tools = await self._session_tools(session_id, self._live_tools())
            if tools is not None:
                return self._describe(tools)
        catalog = self.catalog()
        scoped = []
        for resolver in (self._session_tool, self._browser_tool):
            tool = await resolver(session_id) if resolver is not None else None
            if tool is None:
                continue
            scoped.append(
                SupervisorToolDescriptor(
                    name=tool.name,
                    description=tool.description,
                    input_schema=copy.deepcopy(tool.schema),
                    risk_tier=cast(RiskTier, tool.risk_tier),
                    is_action_tool=bool(getattr(tool, "is_action_tool", True)),
                    risk_tier_for_args=getattr(tool, "risk_tier_for_args", None),
                )
            )
        return (*catalog, *scoped)

    async def execute_confirmed(
        self,
        trace_id: UUID,
        request: SupervisorToolRequest,
    ) -> ToolResult:
        executor = getattr(self._manager, "_tool_executor", None)
        resume = getattr(executor, "execute_confirmed", None)
        if not callable(resume):
            return ToolResult(
                success=False,
                output=None,
                error="Supervisor confirmation gateway is not ready.",
            )
        config_snapshot = dict(request.config_snapshot)
        config_snapshot.update(
            {
                "tool_origin": request.origin,
                "mission_id": request.mission_id,
                "worker_id": request.worker_id,
            }
        )
        return await resume(
            trace_id,
            user_utterance=request.user_utterance,
            config_snapshot=config_snapshot,
        )

    async def cancel_pending(self, trace_id: UUID) -> bool:
        executor = getattr(self._manager, "_tool_executor", None)
        cancel = getattr(executor, "cancel_pending", None)
        if not callable(cancel):
            return False
        return bool(await cancel(trace_id))

    async def publish_guard_denied(
        self,
        name: str,
        reason: str,
        *,
        trace_id: UUID,
    ) -> None:
        executor = getattr(self._manager, "_tool_executor", None)
        publish = getattr(executor, "publish_guard_denied", None)
        if callable(publish):
            await publish(name, reason, trace_id=trace_id)


__all__ = ["BrainSupervisorToolGateway"]
