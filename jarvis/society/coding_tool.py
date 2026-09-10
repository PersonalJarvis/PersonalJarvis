"""A grantable IDE capability for any persistent society agent."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from jarvis.core.protocols import CodingSessionGateway, ToolResult

log = logging.getLogger(__name__)


class CodingSessionTool:
    name = "coding-session"
    is_action_tool = True
    risk_tier = "ask"
    description = (
        "Control coding CLI sessions in the existing Agentic IDE. Discover connected CLIs, "
        "accounts, launch choices and project sessions first. Open with an explicit absolute "
        "project cwd and coding agent; ask for the project when unknown. Send assignments or "
        "follow-ups using the returned workspace_id and terminal_id. Read paged context for "
        "recorded messages, tools, results and available reasoning notes. Accepted means "
        "submitted, never completed. Use a unique request_id for each open/send and reuse "
        "that SAME id for retries; never automatically retry uncertain delivery with a new id. "
        "For an implementation workflow use assign after open: include the self-contained prompt, "
        "user-grounded done_when criteria and constraints. It structures the brief and supervises "
        "the CLI to completion, waking THIS chat on input/results. Answer ordinary questions using "
        "input then respond with its input_token and the supervision "
        "update_id. Never blindly approve "
        "permission escalation or supply secrets. Use finish with evidence "
        "only once the goal is met; "
        "pause with the exact blocker for required user action; resume "
        "restarts paused supervision. "
        "Use supervision to inspect ownership/status. This controls external coding CLIs, "
        "not society teammates or mission workers."
    )
    schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "discover",
                    "open",
                    "assign",
                    "send",
                    "respond",
                    "input",
                    "context",
                    "supervision",
                    "pause",
                    "resume",
                    "finish",
                ],
            },
            **{
                key: {"type": "string"}
                for key in (
                    "cwd",
                    "agent",
                    "account",
                    "model",
                    "effort",
                    "permission_mode",
                    "workspace_id",
                    "terminal_id",
                    "prompt",
                    "request_id",
                    "update_id",
                    "input_token",
                    "summary",
                )
            },
            "done_when": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            "constraints": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            "max_turns": {"type": "integer", "minimum": 1, "maximum": 200},
            "response_mode": {"type": "string", "enum": ["text", "dialog"]},
            "cursor": {
                "type": "object",
                "properties": {
                    "source": {"type": ["string", "null"]},
                    "prefix": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0},
                },
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, runtime: Any, agent_id: str, *, session_id: str | None = None) -> None:
        self.runtime = runtime
        self.agent_id = agent_id
        self.session_id = session_id

    def risk_tier_for_args(self, args: dict[str, Any]) -> str:
        if args.get("action") in ("discover", "context", "input", "supervision"):
            return "monitor"
        if args.get("action") == "respond" and args.get("response_mode") == "dialog":
            return "ask"
        if args.get("action") in ("send", "respond", "pause", "finish"):
            from .roster import canonical_session_id

            return self.runtime.coding_supervision.continuation_tier(
                self.agent_id, self.session_id or canonical_session_id(self.agent_id), args
            )
        return "ask"

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        from .capabilities import select_tools

        if args.get("action") not in ("discover", "context", "input", "supervision"):
            if await self.runtime.store.kill_switch():
                return ToolResult(False, {}, "The society is halted.")
        agent = await self.runtime.roster.get(self.agent_id)
        if agent is None or str(agent.state) != "active":
            return ToolResult(False, {}, "The calling society agent is not active.")
        service = self.runtime.chat_service()
        if service is not None:
            session = service.store.get_session(self.session_id or agent.session_id)
            if session is None or session.permission_mode in ("plan", "read-only"):
                return ToolResult(
                    False, {}, "Coding session control is unavailable in this chat mode."
                )
        allowed = select_tools(
            {self.name: self},
            grant_mode=str(agent.grant_mode),
            grants=agent.grants,
            focus=agent.focus,
            denies=agent.denies,
        )
        if self.name not in allowed:
            return ToolResult(False, {}, "The coding-session capability is not granted.")
        gateway: CodingSessionGateway = self.runtime.coding_sessions()
        owner_session = self.session_id or agent.session_id
        if args.get("action") in ("discover", "context", "input", "supervision"):
            return await self._perform(
                gateway, args, owner_session, trace_id=str(getattr(ctx, "trace_id", ""))
            )
        request_id = str(args.get("request_id") or "").strip()
        if not request_id or len(request_id) > 128:
            return ToolResult(False, {}, "Mutations require a request_id of 1-128 characters.")
        fingerprint = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
        key = (
            "coding_request:"
            + hashlib.sha256((self.agent_id + ":" + request_id).encode()).hexdigest()
        )
        # One runtime owns the store. Serialize journal reservations before any side effect.
        async with self.runtime.coding_request_lock:
            previous = await self.runtime.store.get_meta(key)
            if previous:
                record = json.loads(previous)
                if record["fingerprint"] != fingerprint:
                    return ToolResult(
                        False, {}, "request_id was already used for different arguments."
                    )
                result = record.get("result")
                if result is not None:
                    return ToolResult(**result)
                return ToolResult(
                    True,
                    {
                        "delivery": "uncertain",
                        "completed": False,
                        "message": (
                            "Request is pending or interrupted. "
                            "Discover/read context; do not resend."
                        ),
                    },
                )
            record = {"fingerprint": fingerprint}
            await self.runtime.store.set_meta(key, json.dumps(record))
        # Cancellation leaves a durable pending record. Retrying cannot repeat the side effect.
        result = await self._perform(
            gateway, args, owner_session, trace_id=str(getattr(ctx, "trace_id", ""))
        )
        record["result"] = {
            "success": result.success,
            "output": result.output,
            "error": result.error,
        }
        await self.runtime.store.set_meta(key, json.dumps(record))
        return result

    async def _perform(
        self,
        gateway: CodingSessionGateway,
        args: dict[str, Any],
        session_id: str,
        *,
        trace_id: str = "",
    ) -> ToolResult:
        supervisor = self.runtime.coding_supervision
        try:
            action = args.get("action")
            if action == "assign":
                return ToolResult(
                    True,
                    await supervisor.assign(self.agent_id, session_id, args, trace_id=trace_id),
                )
            if action in ("supervision", "pause", "resume", "finish"):
                return ToolResult(True, await supervisor.action(self.agent_id, session_id, args))
            if action == "send":
                supervisor.check_send(self.agent_id, session_id, args)
            if action == "respond":
                await supervisor.check_reply(self.agent_id, session_id, args)
            result = await self._run(gateway, args)
            if (
                action in ("send", "respond")
                and result.success
                and result.output.get("submitted") is True
            ):
                await supervisor.acted(self.agent_id, session_id, args)
            return result
        except Exception as exc:
            log.warning("Coding supervision action failed: %s", exc)
            return ToolResult(False, {}, str(exc))

    @staticmethod
    async def _run(gateway: CodingSessionGateway, args: dict[str, Any]) -> ToolResult:
        try:
            return ToolResult(True, await gateway.run(args))
        except Exception as exc:
            log.warning("Coding session action %s failed: %s", args.get("action"), exc)
            return ToolResult(False, {"completed": False}, str(exc))
