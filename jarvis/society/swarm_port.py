"""Explicit Swarm proposals and bounded public-profile projection for specialists."""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.protocols import SwarmRequests, ToolResult

log = logging.getLogger(__name__)


class SocietySwarmProfiles:
    def __init__(self, runtime: Any):
        self._runtime = runtime

    async def profile(self, source_agent_id: str) -> dict[str, Any]:
        runtime = self._runtime()
        source = await runtime.roster.get(source_agent_id)
        if source is None or str(source.state) != "active":
            raise PermissionError("The selected specialist is not active")
        return {
            "id": source.agent_id,
            "name": source.name,
            "title": source.title,
            "focus": list(source.focus)[:20],
            "state": "active",
        }


class RequestSwarmTool:
    name = "society_request_swarm"
    risk_tier = "monitor"
    is_action_tool = True
    description = (
        "Propose a separate Ultra Agent Swarm for user review. This never starts work, "
        "grants membership, or copies your private history. Supply only explicitly relevant "
        "input. Use request_id to inspect your own existing proposal."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "maxLength": 120},
            "goal": {"type": "string", "maxLength": 8000},
            "acceptance": {"type": "string", "maxLength": 4000},
            "authorized_input": {"type": "string", "maxLength": 16000},
            "request_key": {"type": "string", "maxLength": 100},
            "request_id": {"type": "string", "maxLength": 100},
        },
        "additionalProperties": False,
    }

    def __init__(self, runtime: Any, source_agent_id: str):
        self._runtime, self._source = runtime, source_agent_id

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        try:
            runtime = self._runtime
            caller = await runtime.roster.get(self._source)
            if (
                await runtime.store.kill_switch()
                or caller is None
                or str(caller.state) != "active"
                or {self.name, "core:" + self.name} & set(caller.denies)
            ):
                raise PermissionError("Swarm proposals are unavailable for this specialist")
            getter = getattr(runtime, "swarm_requests", None)
            if not callable(getter):
                raise PermissionError("The Swarm request service is unavailable")
            port: SwarmRequests = getter()
            if set(args) - set(self.schema["properties"]):
                raise ValueError("Unexpected proposal fields")
            if args.get("request_id"):
                if set(args) != {"request_id"}:
                    raise ValueError("Status inspection accepts only request_id")
                return ToolResult(True, await port.request_status(self._source, args["request_id"]))
            return ToolResult(True, await port.request_swarm(self._source, args))
        except (ValueError, PermissionError, RuntimeError) as exc:
            log.info("Specialist Swarm proposal refused: %s", type(exc).__name__)
            return ToolResult(False, None, str(exc)[:500])
