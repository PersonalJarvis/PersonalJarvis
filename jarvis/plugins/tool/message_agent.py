"""The lead's internal messaging hand, sharing the society delivery path."""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.protocols import ToolResult
from jarvis.plugins.tool.delegate_to_agent import RuntimeResolver
from jarvis.society.agent_tools import MessageAgentTool

log = logging.getLogger(__name__)


class LeadMessageAgentTool:
    name = "message_agent"
    risk_tier = "safe"
    is_action_tool = True
    schema = MessageAgentTool.schema
    description = (
        "Send an INTERNAL chat message to one of the user's Jarvis agents by name or id. "
        "Use for 'send the Gmail agent a test message': this does NOT send email. "
        "No extra confirmation is needed for internal messages. For assigning work use "
        "delegate_to_agent. Report queued, delivered or failed exactly as returned."
    )

    def __init__(self, *, runtime_resolver: RuntimeResolver) -> None:
        self._resolve = runtime_resolver

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        try:
            runtime = self._resolve()
            if runtime is None:
                return ToolResult(success=False, output=None, error="society unavailable")
            await runtime.ensure_started()
            return await MessageAgentTool(runtime, runtime.lead_id).execute(args, ctx)
        except Exception as exc:  # noqa: BLE001 — return an honest, inspectable tool failure
            log.warning("message_agent: delivery failed", exc_info=True)
            return ToolResult(success=False, output=None, error=str(exc))
