"""Direct-Anthropic (claude-api) tool-name sanitization — mirror of the
OpenAI/OpenRouter fix so a user whose single key IS Anthropic also survives a tool
turn that includes a slash-named MCP tool (jarvis/mcp/adapter.py). Anthropic
rejects ``tools.N.custom.name`` for anything outside ``[a-zA-Z0-9_-]{1,128}``.
"""
from __future__ import annotations

import re

from jarvis.plugins.brain._anthropic_base import _tools_anthropic_format

_VALID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def test_mcp_slash_tool_name_sanitized_for_anthropic():
    out = _tools_anthropic_format(({"name": "github/get_pull_request"},))
    assert _VALID.match(out[0]["name"]), f"{out[0]['name']!r} violates Anthropic pattern"


def test_dot_and_colon_sanitized_for_anthropic():
    out = _tools_anthropic_format(({"name": "notion.search:page"},))
    assert _VALID.match(out[0]["name"])


def test_valid_name_unchanged_for_anthropic():
    out = _tools_anthropic_format(({"name": "search_web"},))
    assert out[0]["name"] == "search_web"


def test_collisions_stay_unique_for_anthropic():
    out = _tools_anthropic_format(({"name": "a/b"}, {"name": "a_b"}))
    names = [t["name"] for t in out]
    assert len(set(names)) == 2 and all(_VALID.match(n) for n in names)
