"""Agent-visible remote tools, always invoked through the existing ToolExecutor."""

from __future__ import annotations

import asyncio
from typing import Any

from jarvis.core.protocols import MachineExecution, RiskTier, ToolResult

from .service import machine_service


class MachineTool:
    name = "remote-machine"
    risk_tier: RiskTier = "monitor"
    is_action_tool = True
    description = (
        "Work on an explicitly permitted remote computer. First use operation=devices "
        "to discover granted targets. Specify machine_id for shell, read, write, list or desktop. "
        "For desktop, observe first, then use the returned observation_id and screen coordinates. "
        "Remote paths belong to the target computer. Never silently fall back to local execution."
    )
    schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["devices", "shell", "read", "write", "list", "desktop"],
            },
            "machine_id": {"type": "string"},
            "command": {"type": "string"},
            "path": {"type": "string"},
            "text": {"type": "string"},
            "verb": {
                "type": "string",
                "enum": [
                    "observe",
                    "release",
                    "click",
                    "type_text",
                    "hotkey",
                    "scroll",
                    "drag",
                    "open_app",
                    "switch_window",
                ],
            },
            "params": {
                "type": "object",
                "description": "Desktop action arguments in original screen coordinates "
                "(x/y, text, keys, app_name).",
            },
            "observation_id": {"type": "string"},
            "timeout_s": {"type": "number", "minimum": 1, "maximum": 900},
        },
        "required": ["operation"],
        "additionalProperties": False,
    }

    def __init__(self, runtime: Any, agent_id: str, *, default_machine: str = "") -> None:
        from .context import target_machine

        self.runtime = runtime
        self.agent_id = agent_id
        self.target_machine = target_machine.get()
        self.default_machine = default_machine

    def risk_tier_for_args(self, args: dict[str, Any]) -> str:
        if args.get("operation") in {"devices", "read", "list"}:
            return "safe"
        if args.get("operation") == "shell":
            from jarvis.safety.command_impact import DESTRUCTIVE, classify_command

            if classify_command(str(args.get("command", ""))).level == DESTRUCTIVE:
                return "ask"
        return "monitor"

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        from jarvis.society.approvals import Verdict, decide
        from jarvis.society.events import AgentState

        caller = await self.runtime.roster.get(self.agent_id)
        if (
            caller is None
            or caller.state != AgentState.ACTIVE
            or await self.runtime.store.kill_switch()
        ):
            return ToolResult(False, None, "Agent is inactive or the society is halted")
        operation = str(args.get("operation", ""))
        requested_machine = str(
            args.get("machine_id") or self.target_machine or self.default_machine
        )
        if (
            self.target_machine
            and requested_machine != self.target_machine
            and operation != "devices"
        ):
            return ToolResult(False, None, "This task is bound to a different target computer")
        if "core:remote-machine" in caller.denies or (
            str(caller.grant_mode) == "allowlist" and "core:remote-machine" not in caller.grants
        ):
            return ToolResult(False, None, "Remote computer access is disabled for this agent")
        verdict = decide(
            caller, "core:remote-machine", self.risk_tier_for_args(args), verb=operation
        )
        if verdict == Verdict.BLOCK or (
            verdict == Verdict.QUEUE
            and getattr(ctx, "approved_by", None) not in {"user", "whitelist", "explicit-intent"}
        ):
            return ToolResult(
                False, None, "Remote action is not permitted by the agent's approval rules"
            )
        hub = machine_service(self.runtime.data_dir)
        await hub.start()
        try:
            if operation == "devices":
                rows = await hub.store.rows(
                    "SELECT machine_id FROM grants WHERE agent_id=?", (self.agent_id,)
                )
                permitted = {row["machine_id"] for row in rows}
                return ToolResult(
                    True, [m for m in await hub.list_machines() if m["id"] in permitted]
                )
            executor: MachineExecution = hub
            result = await executor.execute_on_machine(
                agent_id=self.agent_id,
                machine_id=requested_machine,
                operation=operation,
                args={
                    k: v
                    for k, v in args.items()
                    if k in {"command", "path", "text", "verb", "params", "observation_id"}
                },
                trace_id=str(ctx.trace_id),
                timeout_s=float(args.get("timeout_s", 120)),
            )
            output = result.get("output")
            artifacts: Any = ()
            if isinstance(output, dict) and output.get("image_png"):
                from jarvis.agent_chat.media import MediaNormalizer
                from jarvis.core.paths import repo_root
                from jarvis.missions.isolation.worktree import resolve_outputs_root

                output = dict(output)
                encoded = output.pop("image_png")
                artifacts = ({"type": "image", "mime": "image/png", "data": encoded},)
                normalizer = MediaNormalizer(
                    self.runtime.data_dir,
                    resolve_outputs_root(repo_root()),
                    f"society:{self.agent_id}",
                    allow_local_files=False,
                )
                url = await asyncio.to_thread(
                    normalizer.reference, "data:image/png;base64," + encoded
                )
                if normalizer.errors:
                    raise ValueError("Remote screenshot could not be archived in the hub")
                output = {"image_url": url, "machine_id": requested_machine, **output}
            return ToolResult(result["success"], output, result.get("error"), artifacts=artifacts)
        except (PermissionError, ConnectionError, ValueError, TimeoutError) as exc:
            return ToolResult(False, None, str(exc))
