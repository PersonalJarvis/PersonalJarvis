"""Tests for the shared tool registry (routing data contract).

The registry is plain data owned by the orchestrator so the routing logic
(sub-agent 1) and the tool behaviour (sub-agent 2) agree on the same catalog
without depending on each other's code.
"""
from __future__ import annotations

from optimistic.events import RouteKind
from optimistic.registry import (
    ALL_TOOLS,
    DUMB_TOOLS,
    SMART_TOOLS,
    ToolDef,
    match_tool,
)


def test_catalogs_nonempty() -> None:
    assert DUMB_TOOLS and SMART_TOOLS
    assert set(ALL_TOOLS) == set(DUMB_TOOLS) | set(SMART_TOOLS)


def test_dumb_tools_have_dumb_kind() -> None:
    assert all(t.kind is RouteKind.DUMB_TOOL for t in DUMB_TOOLS)


def test_smart_tools_have_smart_kind() -> None:
    assert all(t.kind is RouteKind.SMART_TOOL for t in SMART_TOOLS)


def test_match_music_is_dumb() -> None:
    t = match_tool("spiel mal etwas Musik ab")
    assert t is not None and t.kind is RouteKind.DUMB_TOOL


def test_match_gmail_is_smart() -> None:
    t = match_tool("schreib Max eine E-Mail")
    assert t is not None
    assert t.name == "gmail" and t.kind is RouteKind.SMART_TOOL


def test_no_match_returns_none() -> None:
    assert match_tool("erzaehl mir einen Witz ueber Katzen") is None  # i18n-allow


def test_dumb_is_checked_before_smart() -> None:
    # Overlapping command: 'spiel' (dumb) and 'mail' (smart) both present.
    # A local action must NEVER escalate to the worker (AD-OE3 / M5).
    t = match_tool("spiel die mail-melodie ab")
    assert t is not None and t.kind is RouteKind.DUMB_TOOL


def test_tooldef_is_frozen_hashable() -> None:
    t = ToolDef(name="x", kind=RouteKind.DUMB_TOOL, triggers=("x",))
    assert hash(t) is not None  # frozen + hashable -> usable in sets
