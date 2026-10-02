"""Application-owned workspace tool, independent of any voice provider."""

from __future__ import annotations

from jarvis.core.agent_brief import AGENT_BRIEF_RULE
from jarvis.core.protocols import ExecutionContext, ToolResult, WorkspaceOrchestrationGateway


def _point_at_jarvis_agent(args: dict, result: dict) -> dict:
    """Say so when a name no coding pane carries is one of the user's Jarvis agents.

    Live 2026-10-02: "send Jarvis Scout a quick message" went to this tool,
    found no pane called that, and the user heard "I couldn't find agents
    with those names" - while Jarvis-Scout sat idle on the team.
    """
    agent_ref = str(args.get("agent") or "").strip()
    if not agent_ref or result.get("status") not in {"needs_clarification", "unavailable"}:
        return result
    if args.get("action") == "create":
        return result
    try:
        from jarvis.society.agent_names import jarvis_agent_hint

        hint = jarvis_agent_hint(agent_ref, context=str(args.get("prompt") or ""))
    except Exception:  # noqa: BLE001 - the hint is advisory; the pane result stands
        import logging

        logging.getLogger(__name__).warning("workspace tool: agent hint failed", exc_info=True)
        return result
    if hint is None:
        return result
    names = " or ".join(f"'{name}'" for name in hint["agents"])
    reason = (
        f"'{agent_ref}' is not a coding pane in the Agentic IDE; it names the user's Jarvis "
        f"agent {names}. Use delegate_to_agent (to assign work) or message_agent (to send a "
        "message) with that agent name instead."
    )
    if not hint["certain"]:
        reason += f" The name is not certain: ask the user whether they mean {names} first."
    earlier = str(result.get("reason") or "").strip()
    return {**result, "jarvis_agent": hint, "reason": f"{reason} {earlier}".strip()}


class WorkspaceOrchestrationTool:
    name = "workspace-orchestrate"
    # Handing a task to the user's own coding agent is Jarvis's job, not an
    # outward act: it runs without a spoken "shall I send it?" and is logged
    # (live 2026-10-01: the user asked three times and was still asked back).
    risk_tier = "monitor"
    is_action_tool = True
    description = (
        "Route coding tasks to Projects > Workspaces > coding agents (the coding panes of "
        "the Agentic IDE). The user's named Jarvis agents (their team, e.g. a Scout or a "
        "Gmail agent) are NOT panes: reach them with delegate_to_agent or message_agent. "
        "A resolve result carrying jarvis_agent means the name is such an agent. "
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
        "The result returns asynchronously to this conversation; keep talking to the user "
        "instead of waiting or polling in a loop. "
        "After a proven pre-write refusal, resolve again for a fresh request_id "
        "before a new attempt. "
        "Use context with the same IDs to inspect recorded results. No prompt rewriting is needed. "
        "A prompt for create, open_workspace or send is a work order the agent carries "
        "out, never a read-only request unless the user asked for one (see prompt)."
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
                )
            },
            "prompt": {
                "type": "string",
                "description": (
                    "create/open_workspace/send: the full, self-contained task brief; "
                    "respond: the answer to type. " + AGENT_BRIEF_RULE
                ),
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
        from jarvis.core.delegation import current_delegation_origin, origin_metadata

        token = current_delegation_origin.set(origin_metadata(
            language=str(ctx.config.get("output_language") or ""),
        ))
        try:
            result = await self.gateway.run(args, trace_id=str(ctx.trace_id))
        except ValueError as exc:
            # Invalid arguments go back to the model as the tool error.
            return ToolResult(success=False, output=None, error=str(exc))
        finally:
            current_delegation_origin.reset(token)
        status = result.get("status")
        result = _point_at_jarvis_agent(args, result)
        return ToolResult(
            success=result.get("success") is not False
            and status not in {"uncertain", "unavailable", "stale_target", "not_accepted"},
            output=result,
        )
