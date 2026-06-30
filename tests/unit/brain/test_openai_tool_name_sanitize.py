"""OpenAI/Anthropic tool-name sanitization.

Live forensic 2026-06-29: a real voice question failed with HTTP 400
``tools.19.custom.name: String should match pattern '^[a-zA-Z0-9_-]{1,128}$'``
on the OpenRouter→Anthropic primary, then cascaded through the dead Gemini + a
bad Claude key and surfaced as "I can't reach my language model." Root cause: MCP
tools are namespaced ``"<server>/<tool>"`` (jarvis/mcp/adapter.py:70), and the
slash is rejected by Anthropic AND OpenAI. Gemini has its own sanitizer; the
OpenAI-base path (used by OpenRouter + OpenAI) sent the raw name. A tool turn that
includes any MCP tool therefore bricked every Anthropic/OpenAI-family model.

Anthropic allows ``[a-zA-Z0-9_-]{1,128}``; OpenAI allows ``[a-zA-Z0-9_-]{1,64}``.
We sanitize to the stricter (<=64) so both accept it, and keep a forward+reverse
map so the model's tool_call resolves back to the original tool.
"""
from __future__ import annotations

import re

from jarvis.plugins.brain._openai_base import _tools_openai_format

# The strictest of the two families (OpenAI's 64-char cap; Anthropic's is 128).
_VALID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def test_mcp_slash_tool_name_is_sanitized():
    out = _tools_openai_format(({"name": "github/get_pull_request"},))
    name = out[0]["function"]["name"]
    assert _VALID.match(name), f"tool name {name!r} violates ^[A-Za-z0-9_-]{{1,64}}$"


def test_dot_and_colon_names_are_sanitized():
    out = _tools_openai_format(({"name": "mcp.notion:search.page"},))
    name = out[0]["function"]["name"]
    assert _VALID.match(name), f"tool name {name!r} not sanitized"


def test_valid_name_is_left_unchanged():
    out = _tools_openai_format(({"name": "search_web"},))
    assert out[0]["function"]["name"] == "search_web"


def test_collisions_stay_unique():
    # "a/b" and "a_b" both sanitize to "a_b" — the second must get a suffix so the
    # tool-call→original map stays bijective.
    out = _tools_openai_format(({"name": "a/b"}, {"name": "a_b"}))
    names = [t["function"]["name"] for t in out]
    assert len(set(names)) == 2, f"names collided: {names}"
    assert all(_VALID.match(n) for n in names)
