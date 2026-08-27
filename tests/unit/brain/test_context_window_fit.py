"""The tool surface fits the answering brain's context window (BUG-187).

Live 2026-08-27 10:11 (and 2026-08-26 20:32, 20:58): the front page's chat
ran on a local 32k model, the registry held 221 tools (78 built-in, 131 from
three connected MCP servers, 12 CLIs) and a plain "Hallo" went out as 67,852
tokens. The server refused it with a 400 that named the cause, the
one-provider chain was exhausted, and the user read "I can't reach my provider
— check the network". Two defects, two guards:

1. Nothing read ``Brain.context_window`` — ``_fit_tools_to_context_window``
   now sizes the surface: connected-server tools leave first, then the largest
   remaining ones, mandated tools never.
2. The 400 was classified as a generic call failure —
   ``_classify_provider_error`` now returns ``context_overflow`` and the cause
   phrase / diagnostic name the size, not the network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.brain.manager import (
    _DEAD_LIST_KINDS,
    _PROVIDER_DOWN_CAUSE_PHRASES,
    BrainManager,
    _classify_provider_error,
    _fit_tools_to_context_window,
    _format_provider_chain_error,
    _primary_provider_down_cause,
    _provider_down_phrase,
    _tool_surface_tokens,
)

# The exact server refusal from the live log (Ollama over the OpenAI route).
_LIVE_OLLAMA_400 = (
    "Error code: 400 - {'error': {'message': '{\"error\":{\"code\":400,"
    "\"message\":\"request (67852 tokens) exceeds the available context size "
    "(32768 tokens), try increasing it\",\"type\":\"exceed_context_size_error\","
    "\"n_prompt_tokens\":67852,\"n_ctx\":32768}}', 'type': 'invalid_request_error'}}"
)


@dataclass
class _FakeTool:
    name: str
    description: str = "does a thing"
    schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    is_mcp_tool: bool = False


def _tool(name: str, *, size: int = 1, mcp: bool = False) -> _FakeTool:
    """A tool whose wire size scales with ``size`` (schema padding)."""
    props = {f"arg{i}": {"type": "string", "description": "x" * 40} for i in range(size)}
    return _FakeTool(
        name=name,
        schema={"type": "object", "properties": props},
        is_mcp_tool=mcp,
    )


class _FakeBrain:
    name = "fake"
    context_window = 0
    supports_vision = False

    def __init__(self, window: int) -> None:
        self.context_window = window


# --------------------------------------------------------------- the fit


class TestFitToolsToContextWindow:
    def test_no_declared_window_leaves_the_surface_alone(self) -> None:
        tools = {"a": _tool("a", size=50), "b": _tool("b", size=50)}
        fitted, dropped = _fit_tools_to_context_window(
            tools, context_window=0, used_tokens=1_000_000
        )
        assert fitted is tools
        assert dropped == []

    def test_a_surface_that_fits_is_untouched(self) -> None:
        tools = {"a": _tool("a"), "b": _tool("b", mcp=True)}
        fitted, dropped = _fit_tools_to_context_window(
            tools, context_window=100_000, used_tokens=10_000
        )
        assert fitted is tools
        assert dropped == []

    def test_connected_server_tools_leave_before_built_in_ones(self) -> None:
        # Two small built-ins and two LARGE MCP tools; the budget holds only
        # the built-ins. Size alone would keep nothing built-in either way —
        # the group order is what protects Jarvis's own hands.
        tools = {
            "run_shell": _tool("run_shell", size=2),
            "search_web": _tool("search_web", size=2),
            "github/create_issue": _tool("github/create_issue", size=20, mcp=True),
            "linear/list_issues": _tool("linear/list_issues", size=20, mcp=True),
        }
        built_in_cost = sum(
            _tool_surface_tokens(n, t) for n, t in tools.items() if not t.is_mcp_tool
        )
        fitted, dropped = _fit_tools_to_context_window(
            tools, context_window=built_in_cost + 100, used_tokens=100
        )
        assert set(fitted) == {"run_shell", "search_web"}
        assert dropped == ["linear/list_issues", "github/create_issue"] or dropped == [
            "github/create_issue",
            "linear/list_issues",
        ]

    def test_then_the_largest_remaining_tools_go_first(self) -> None:
        tools = {
            "tiny": _tool("tiny", size=1),
            "huge": _tool("huge", size=40),
            "medium": _tool("medium", size=5),
        }
        budget = _tool_surface_tokens("tiny", tools["tiny"]) + _tool_surface_tokens(
            "medium", tools["medium"]
        )
        fitted, dropped = _fit_tools_to_context_window(
            tools, context_window=budget, used_tokens=0
        )
        assert dropped == ["huge"]
        assert list(fitted) == ["tiny", "medium"]  # registry order preserved

    def test_kept_names_are_never_dropped(self) -> None:
        tools = {
            "mandated": _tool("mandated", size=40),
            "other": _tool("other", size=1),
        }
        fitted, dropped = _fit_tools_to_context_window(
            tools, context_window=10, used_tokens=0, keep={"mandated"}
        )
        assert "mandated" in fitted
        assert dropped == ["other"]

    def test_prompt_larger_than_window_drops_every_droppable_tool(self) -> None:
        tools = {"a": _tool("a"), "b": _tool("b", mcp=True)}
        fitted, dropped = _fit_tools_to_context_window(
            tools, context_window=1_000, used_tokens=5_000
        )
        assert fitted == {}
        assert set(dropped) == {"a", "b"}


class TestFitOnTheManager:
    def _mgr(self) -> BrainManager:
        m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
        m._tools = {}
        m._evidence_required_tool = None
        m._skill_turn_match = None
        return m

    def test_cloud_sized_window_keeps_the_whole_surface(self) -> None:
        m = self._mgr()
        tools = {f"t{i}": _tool(f"t{i}", size=10, mcp=i % 2 == 0) for i in range(221)}
        fitted = m._fit_tools_to_brain(
            tools, _FakeBrain(1_048_576), system_prompt="x" * 40_000, history=None
        )
        assert fitted is tools

    def test_local_window_trims_the_live_shape(self) -> None:
        # 221 tools, a long system prompt, a 32k window: the surface shrinks
        # and the connected-server tools are the ones that went.
        m = self._mgr()
        tools = {f"t{i}": _tool(f"t{i}", size=10, mcp=i % 2 == 0) for i in range(221)}
        fitted = m._fit_tools_to_brain(
            tools, _FakeBrain(32_768), system_prompt="x" * 30_000, history=None
        )
        assert 0 < len(fitted) < 221
        assert not any(t.is_mcp_tool for t in fitted.values())

    def test_mandated_tool_survives_a_local_window(self) -> None:
        m = self._mgr()
        m._evidence_required_tool = "search_web"
        m._skill_turn_match = object()
        tools = {
            "search_web": _tool("search_web", size=60),
            "run-skill": _tool("run-skill", size=60),
            "github/x": _tool("github/x", size=60, mcp=True),
        }
        fitted = m._fit_tools_to_brain(
            tools, _FakeBrain(4_200), system_prompt="", history=None
        )
        assert {"search_web", "run-skill"} <= set(fitted)
        assert "github/x" not in fitted

    def test_undeclared_window_is_left_alone(self) -> None:
        m = self._mgr()

        class _Windowless:
            name = "w"

        tools = {"a": _tool("a", size=60)}
        assert m._fit_tools_to_brain(tools, _Windowless(), system_prompt="", history=None) is tools


# ---------------------------------------------------------- the cause


class TestContextOverflowCause:
    @pytest.mark.parametrize(
        "msg",
        [
            _LIVE_OLLAMA_400,
            "Error code: 400 - This model's maximum context length is 128000 tokens.",
            "Error code: 400 - context_length_exceeded",
            "prompt is too long: 213462 tokens > 200000 maximum",
            "400 INVALID_ARGUMENT. The input token count (2000000) exceeds the "
            "maximum number of tokens allowed (1048576).",
        ],
    )
    def test_size_refusals_classify_as_context_overflow(self, msg: str) -> None:
        assert _classify_provider_error(msg, default="call_fail") == "context_overflow"

    def test_overflow_never_dead_lists_the_provider(self) -> None:
        assert "context_overflow" not in _DEAD_LIST_KINDS

    def test_a_plain_400_still_falls_through(self) -> None:
        kind = _classify_provider_error("Error code: 400 - bad request", default="call_fail")
        assert kind == "call_fail"

    def test_spoken_cause_names_the_size_not_the_network(self) -> None:
        errors = [("ollama", "qwen3.8-16gb:latest", "context_overflow", _LIVE_OLLAMA_400)]
        cause = _primary_provider_down_cause(errors)
        assert cause == "context_overflow"
        for lang in ("de", "en", "es"):
            phrase = _provider_down_phrase(lang, 0, cause)
            assert phrase == _PROVIDER_DOWN_CAUSE_PHRASES["context_overflow"][lang]
            low = phrase.lower()
            assert "netzwerk" not in low and "network" not in low and "red" != low
            assert "ollama" not in low  # voice-safe: no provider names

    def test_missing_key_still_outranks_an_overflow(self) -> None:
        errors = [
            ("ollama", "m", "context_overflow", "…"),
            ("gemini", "g", "missing_key", "…"),
        ]
        assert _primary_provider_down_cause(errors) == "missing_key"

    def test_developer_diagnostic_stops_blaming_the_network(self) -> None:
        text = _format_provider_chain_error(
            [("ollama", "qwen3.8-16gb:latest", "context_overflow", _LIVE_OLLAMA_400)]
        )
        assert "Kontextfenster" in text  # i18n-allow: the diagnostic is German
        assert "ollama" in text
        assert "Netzwerk" not in text  # i18n-allow
