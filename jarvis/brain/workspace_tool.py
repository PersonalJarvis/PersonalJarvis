"""Application-owned workspace tool, independent of any voice provider."""

from __future__ import annotations

from jarvis.core.protocols import ExecutionContext, ToolResult, WorkspaceOrchestrationGateway


class WorkspaceOrchestrationTool:
    name = "workspace-orchestrate"
    risk_tier = "ask"
    is_action_tool = True
    description = (
        "Route coding tasks to Projects > Workspaces > coding agents. Inspect the current graph; "
        "resolve explicit project/workspace/agent references (names or IDs) before sending. "
        "With no named workspace resolve uses the visible workspace, and selects an idle agent "
        "without requiring a focused terminal. Ask on needs_clarification; never guess. "
        "Send with all three resolved IDs and request_id returned by resolve; reuse it for "
        "retries. Explicit background targets never switch the visible workspace. "
        "Accepted means delivered, not completed; uncertain delivery must not be retried. "
        "After a proven pre-write refusal, resolve again for a fresh request_id "
        "before a new attempt. "
        "Use context with the same IDs to inspect recorded results. No prompt rewriting is needed."
    )
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["inspect", "resolve", "send", "context"]},
            **{
                key: {"type": "string"}
                for key in (
                    "project",
                    "workspace",
                    "agent",
                    "project_id",
                    "workspace_id",
                    "terminal_id",
                    "prompt",
                    "request_id",
                )
            },
            "request_id": {
                "type": "string",
                "pattern": "^[a-fA-F0-9]{32}$",
                "description": "Copy request_id from resolve; keep it unchanged on retries.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["action"],
    }

    def __init__(self, gateway: WorkspaceOrchestrationGateway) -> None:
        self.gateway = gateway

    def risk_tier_for_args(self, args: dict) -> str:
        return "safe" if args.get("action") in {"inspect", "resolve", "context"} else "ask"

    def describe_args(self, args: dict) -> dict:
        return {
            "level": "read" if self.risk_tier_for_args(args) == "safe" else "modify",
            "project": str(args.get("project_id") or args.get("project") or ""),
            "workspace": str(args.get("workspace_id") or args.get("workspace") or ""),
            "agent": str(args.get("terminal_id") or args.get("agent") or ""),
            "task": str(args.get("prompt") or "")[:240],
        }

    async def execute(self, args: dict, ctx: ExecutionContext) -> ToolResult:
        try:
            result = await self.gateway.run(args, trace_id=str(ctx.trace_id))
        except ValueError as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        status = result.get("status")
        return ToolResult(
            success=result.get("success") is not False
            and status not in {"uncertain", "unavailable", "stale_target", "not_accepted"},
            output=result,
        )
