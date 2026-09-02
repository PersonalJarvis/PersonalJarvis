"""The Agent MCP surface: Jarvis' agent ecosystem, offered outwards over MCP.

``jarvis.mcp.jarvis_tools_server`` offers Jarvis' *hands* — the supervisor tool
catalog a spawned session reaches back through. This package offers the
*ecosystem*: the society roster, the board every agent speaks on, rooms,
assignments, approvals and the connected capability catalog.

The difference matters. A client on the tools surface can open an app or read
the wiki. A client here can ask who is on the team, write to one of them, watch
the answer land, give an agent a task the scheduler turns into real work, and
settle a room where several of them argued it out.

Transport and auth are the Control API's (``jarvis.ui.web.mcp_server_routes``),
so anything that can hold the control key — Claude Desktop, Cursor, VS Code,
another Jarvis — talks to the ecosystem the same way.
"""

from __future__ import annotations

from .server import SERVER_NAME, build_server
from .tools import TOOLS, AgentTool, tool_specs

__all__ = ["SERVER_NAME", "TOOLS", "AgentTool", "build_server", "tool_specs"]
