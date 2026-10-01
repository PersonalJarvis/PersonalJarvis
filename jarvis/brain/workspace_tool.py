"""Application-owned workspace tool, independent of any voice provider."""

from __future__ import annotations

from jarvis.core.protocols import ExecutionContext, ToolResult, WorkspaceOrchestrationGateway


class WorkspaceOrchestrationTool:
    name = "workspace-orchestrate"
    # Handing a task to the user's own coding agent is Jarvis's job, not an
    # outward act: it runs without a spoken "shall I send it?" and is logged
    # (live 2026-10-01: the user asked three times and was still asked back).
    risk_tier = "monitor"
    is_action_tool = True
    description = (
        "Route coding tasks to Projects > Workspaces > coding agents. "
        "When the user asks for a NEW agent, terminal or session (for example 'spawn two "
        "Claude Code agents in the VMs workspace'), call create: it opens count new panes of "
        "the named cli in the named or visible workspace and, with prompt, hands each the "
        "task. Never reuse an existing agent and never use spawn_worker for that. "
        "Mixed CLIs in one request ('five Claude Code and three Codex') go in agents: "
        "[{cli, count}, ...]. open_workspace opens a NEW workspace for folder (absolute "
        "path) or a known project, with agents and an optional prompt; restore reopens a "
        "closed workspace; show brings one on screen. For one pane (by agent call-sign or "
        "terminal_id): observe reads its screen, question and newest events; respond types "
        "the answer to the question or permission prompt it shows (prompt = the answer); keys "
        "presses keys such as enter, escape, up/down, digits, shift+tab (permission mode); "
        "interrupt stops its current turn; close removes it. These are the user's own app "
        "actions: run them when asked, without asking back. "
        "Inspect the current graph; "
        "resolve explicit project/workspace/agent references (names or IDs) before sending. "
        "With no named workspace resolve uses the visible workspace, and selects an idle agent "
        "without requiring a focused terminal. On needs_clarification pick from the returned "
        "candidates when one clearly fits, else ask; never invent a target. "
        "Send right away with the resolved IDs and the request_id from resolve (the app "
        "remembers its resolves, so a slightly mistyped id still works); reuse it for "
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
            "action": {
                "type": "string",
                "enum": [
                    "inspect",
                    "resolve",
                    "send",
                    "context",
                    "create",
                    "open_workspace",
                    "restore",
                    "show",
                    "observe",
                    "respond",
                    "keys",
                    "interrupt",
                    "close",
                ],
            },
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
                )
            },
            # No pattern: a schema rejection would discard the whole call over
            # one mistyped character. The orchestrator repairs the id instead.
            "request_id": {
                "type": "string",
                "description": "request_id from resolve; keep it unchanged on retries.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "cli": {
                "type": "string",
                "description": (
                    "create: the coding CLI as the user said it, e.g. 'Claude Code' or "
                    "'Codex'; omitted, the workspace's usual CLI."
                ),
            },
            "count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 16,
                "description": "create: how many new agents to open (default 1).",
            },
            "name": {
                "type": "string",
                "description": (
                    "create: optional name for a single new agent; open_workspace: the new "
                    "workspace's tab name."
                ),
            },
            "agents": {
                "type": "array",
                "maxItems": 8,
                "description": "create/open_workspace: several CLIs at once, one entry per CLI.",
                "items": {
                    "type": "object",
                    "properties": {
                        "cli": {"type": "string"},
                        "count": {"type": "integer", "minimum": 1, "maximum": 16},
                    },
                },
            },
            "folder": {
                "type": "string",
                "description": "open_workspace: absolute path of the folder to open.",
            },
            "keys": {
                "type": "array",
                "maxItems": 10,
                "description": "keys: e.g. ['down', 'enter'] or ['shift+tab'].",
                "items": {"type": "string"},
            },
        },
        "required": ["action"],
    }

    def __init__(self, gateway: WorkspaceOrchestrationGateway) -> None:
        self.gateway = gateway

    def risk_tier_for_args(self, args: dict) -> str:
        reads = {"inspect", "resolve", "context", "observe"}
        return "safe" if args.get("action") in reads else "monitor"

    def describe_args(self, args: dict) -> dict:
        return {
            "level": "read" if self.risk_tier_for_args(args) == "safe" else "modify",
            "project": str(args.get("project_id") or args.get("project") or ""),
            "workspace": str(args.get("workspace_id") or args.get("workspace") or ""),
            "agent": str(args.get("terminal_id") or args.get("agent") or "")
            or (
                f"{args.get('count') or 1} new {args.get('cli') or 'agent'}"
                if args.get("action") == "create"
                else ""
            ),
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
