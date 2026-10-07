"""Which action state a tool call shows on the pet (``docs/pets.md``).

A tool step is ``searching`` when it LOOKS something up — a web search, a page
or file read, a wiki or memory query, a screen read — and ``working`` for
everything else (it changes something, sends something, runs something).

The decision reads only the tool's own name, so it works the same for built-in
tools, plugins and MCP tools (``mcp__server__tool``) and never needs a list of
providers or tool ids that drifts out of date.
"""

from __future__ import annotations

import re

#: Name words that mark a lookup. Matched as whole words of the tool name.
SEARCH_WORDS: frozenset[str] = frozenset(
    {
        "browse",
        "fetch",
        "find",
        "get",
        "grep",
        "list",
        "lookup",
        "navigate",
        "query",
        "read",
        "recall",
        "retrieve",
        "scrape",
        "search",
        "snapshot",
        "screenshot",
        "wiki",
    }
)

_SPLIT = re.compile(r"[^a-z0-9]+")


def action_for_tool(tool_name: str) -> str:
    """``searching`` for a lookup tool, else ``working``."""
    name = str(tool_name or "").strip().lower().split("__")[-1]
    words = {word for word in _SPLIT.split(name) if word}
    return "searching" if words & SEARCH_WORDS else "working"
