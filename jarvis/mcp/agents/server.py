"""The Agent MCP server: tools, resources and prompts over one connection.

Three primitives, because a standard that only ships tools makes every client
re-discover the same state by hand:

* **Tools** (``tools.py``) — the verbs: talk to an agent, assign work, settle a
  room, resolve an approval.
* **Resources** — the nouns, read-only and addressable: ``jarvis://agents`` is
  the roster, ``jarvis://agent/<id>`` one teammate, ``jarvis://ecosystem`` the
  house at a glance. A client can attach these as context without spending a
  tool call, which is what makes "what is my team doing?" answerable in one
  turn instead of three.
* **Prompts** — the openings worth having ready: a standup, a briefing for one
  agent, a room on a question.

Built per process, never per session: every handler reads the live runtime when
it is called, so an agent created a minute ago, a plugin that just connected or
a CLI that finished bootstrapping shows up without a rebuild.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final

from . import tools as agent_tools
from .context import EcosystemUnavailable, society

log = logging.getLogger(__name__)

#: The name clients show in their connector list. Kept distinct from the tools
#: surface's plain "jarvis" so both can be connected at once.
SERVER_NAME: Final[str] = "jarvis-agents"

#: Bumped when the tool contract changes in a way a client must notice.
PROTOCOL_VERSION: Final[str] = "1.0"

_ROSTER_URI: Final[str] = "jarvis://agents"
_ECOSYSTEM_URI: Final[str] = "jarvis://ecosystem"
_CAPABILITIES_URI: Final[str] = "jarvis://capabilities"
_AGENT_PREFIX: Final[str] = "jarvis://agent/"


async def _read_resource(uri: str) -> str:
    """One resource as JSON text, or a sentence saying why it is not there."""
    try:
        if uri == _ECOSYSTEM_URI:
            body = await agent_tools._ecosystem_status({})  # noqa: SLF001 — same package
        elif uri == _ROSTER_URI:
            body = await agent_tools._agents_list({})  # noqa: SLF001
        elif uri == _CAPABILITIES_URI:
            body = await agent_tools._capabilities_list({})  # noqa: SLF001
        elif uri.startswith(_AGENT_PREFIX):
            body = await agent_tools._agent_get(  # noqa: SLF001
                {"agent": uri[len(_AGENT_PREFIX) :]}
            )
        else:
            return f"Unknown resource: {uri}"
    except EcosystemUnavailable as exc:
        return f"{exc.what} is not available: {exc.hint}"
    except Exception as exc:  # noqa: BLE001 — a resource read never breaks the connection
        log.warning("agent MCP: resource %s failed", uri, exc_info=True)
        return f"Could not read {uri}: {type(exc).__name__}: {exc}"
    return json.dumps(body, ensure_ascii=False, default=str, indent=2)


async def _agent_resources() -> list[tuple[str, str, str]]:
    """``(uri, name, description)`` per agent — empty when the society is down."""
    try:
        rt = await society()
        agents = await rt.roster.list()
    except Exception:  # noqa: BLE001 — a listing that cannot be built is simply empty
        log.debug("agent MCP: roster unavailable for resource listing", exc_info=True)
        return []
    return [
        (
            f"{_AGENT_PREFIX}{a.agent_id}",
            a.name,
            f"{a.title} — {a.description}"[:300],
        )
        for a in agents
    ]


#: ``(name, description, arguments)`` for each prompt this server offers.
_PROMPTS: Final[tuple[tuple[str, str, tuple[tuple[str, str, bool], ...]], ...]] = (
    (
        "standup",
        "Ask every active agent what it is working on and summarise the team's state.",
        (),
    ),
    (
        "brief_agent",
        "Get one agent fully briefed on a task, then hand the task over.",
        (("agent", "The agent's id or name.", True), ("task", "What it should take on.", True)),
    ),
    (
        "settle_question",
        "Put a question to several agents in a room and report what they concluded.",
        (
            ("question", "The question to settle.", True),
            ("members", "Comma-separated agent names. Default: pick from the roster.", False),
        ),
    ),
)


def _prompt_text(name: str, args: dict[str, str]) -> str:
    if name == "standup":
        return (
            "Call ecosystem_status, then agents_list. For every agent whose run_state "
            "is 'working', read its recent events with agent_get. Report, in plain "
            "language: who is busy with what, who is idle, what is waiting on my "
            "decision (approvals_list), and anything that looks stuck. Do not start "
            "any work."
        )
    if name == "brief_agent":
        agent = args.get("agent", "")
        task = args.get("task", "")
        return (
            f"Read {agent}'s roster row and recent events with agent_get so you know "
            f"its focus and what it has been doing. Then use agent_chat to ask it "
            f"whether it has what it needs for this task: {task}. If it says yes, "
            f"hand the task over with agent_assign and report the scheduler's verdict. "
            f"If it says no, tell me what is missing instead of assigning anyway."
        )
    if name == "settle_question":
        question = args.get("question", "")
        members = args.get("members", "")
        who = f"these agents: {members}" if members else "2-4 agents whose focus fits the question"
        return (
            f"Open a room with {who} on this question: {question}. Use agents_list "
            f"first if you need to choose. Let the room run, then settle it with "
            f"room_settle and report what they concluded and where they disagreed."
        )
    return f"Unknown prompt: {name}"


def build_server() -> Any:
    """A low-level MCP server bound to the live agent ecosystem."""
    import mcp.types as types
    from mcp.server.lowlevel import Server

    server: Any = Server(SERVER_NAME)

    @server.list_tools()  # type: ignore[misc, no-untyped-call]
    async def _list_tools() -> list[Any]:
        return [
            types.Tool(
                name=spec["name"],
                description=spec["description"],
                inputSchema=spec["inputSchema"],
            )
            for spec in agent_tools.tool_specs()
        ]

    @server.call_tool()  # type: ignore[misc, no-untyped-call]
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        text = await agent_tools.call(name, arguments)
        return [types.TextContent(type="text", text=text)]

    @server.list_resources()  # type: ignore[misc, no-untyped-call]
    async def _list_resources() -> list[Any]:
        fixed = [
            types.Resource(
                uri=_ECOSYSTEM_URI,
                name="Ecosystem status",
                description="The whole agent house at a glance: agents, runs, rooms, approvals.",
                mimeType="application/json",
            ),
            types.Resource(
                uri=_ROSTER_URI,
                name="Agent roster",
                description="Every agent with its role, model, focus and current run state.",
                mimeType="application/json",
            ),
            types.Resource(
                uri=_CAPABILITIES_URI,
                name="Connected capabilities",
                description="Plugins, CLIs, MCP servers and skills the agents can actually use.",
                mimeType="application/json",
            ),
        ]
        for uri, name, description in await _agent_resources():
            fixed.append(
                types.Resource(
                    uri=uri, name=name, description=description, mimeType="application/json"
                )
            )
        return fixed

    @server.read_resource()  # type: ignore[misc, no-untyped-call]
    async def _read(uri: Any) -> str:
        return await _read_resource(str(uri))

    @server.list_prompts()  # type: ignore[misc, no-untyped-call]
    async def _list_prompts() -> list[Any]:
        return [
            types.Prompt(
                name=name,
                description=description,
                arguments=[
                    types.PromptArgument(name=arg, description=desc, required=required)
                    for arg, desc, required in arguments
                ],
            )
            for name, description, arguments in _PROMPTS
        ]

    @server.get_prompt()  # type: ignore[misc, no-untyped-call]
    async def _get_prompt(name: str, arguments: dict[str, str] | None) -> Any:
        return types.GetPromptResult(
            description=next((d for n, d, _ in _PROMPTS if n == name), name),
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text", text=_prompt_text(name, dict(arguments or {}))
                    ),
                )
            ],
        )

    return server


__all__ = ["PROTOCOL_VERSION", "SERVER_NAME", "build_server"]
