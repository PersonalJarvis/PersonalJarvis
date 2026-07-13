"""Public, versioned gateway to the Brain Manager's live tool catalog.

Higher layers receive only secret-free descriptors and execute by name. The
concrete ``Tool`` objects and ``ToolExecutor`` remain inside the brain layer,
and every call resolves against the current catalog immediately before use.
"""
from __future__ import annotations

import copy
import threading
from typing import Any, cast
from uuid import UUID

from jarvis.core.protocols import (
    RiskTier,
    SupervisorToolDescriptor,
    SupervisorToolRequest,
    ToolResult,
)

_VALID_RISK_TIERS = frozenset({"safe", "monitor", "ask", "block"})


class BrainSupervisorToolGateway:
    """Adapter that keeps Brain Manager implementation details private."""

    def __init__(self, manager: Any) -> None:
        self._manager = manager
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
            descriptors.append(
                SupervisorToolDescriptor(
                    name=str(name),
                    description=str(getattr(tool, "description", "")),
                    input_schema=copy.deepcopy(schema),
                    risk_tier=risk_tier,
                    is_action_tool=bool(getattr(tool, "is_action_tool", False)),
                )
            )
        return tuple(descriptors)

    @property
    def catalog_version(self) -> int:
        self.catalog()
        with self._lock:
            return self._catalog_version

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

        tool = self._live_tools().get(name)
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
