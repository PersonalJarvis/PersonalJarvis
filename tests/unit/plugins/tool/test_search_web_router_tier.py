"""Latency and identity pins for search_web as a router-tier tool.

search-web joined ROUTER_TOOLS on 2026-06-10 (ADR-0011 amendment "Inline web
search"), which puts its HTTP call on the voice turn. The voice SLO budget
(p95 intent->ACK < 3.0 s) cannot absorb the old 15-second httpx timeout — a
single slow DuckDuckGo response would wedge the spoken turn. The tool must
fail fast and let the brain answer from context instead.
"""
from __future__ import annotations

from jarvis.plugins.tool.search_web import _TIMEOUT_S, SearchWebTool


def test_timeout_is_voice_path_safe() -> None:
    """A wedged search must release the turn quickly (BUG-032 lesson: a
    too-long ceiling on the voice path reads as 'Jarvis never answers')."""
    assert _TIMEOUT_S <= 5.0


def test_tool_identity_and_risk_tier() -> None:
    assert SearchWebTool.name == "search_web"
    assert SearchWebTool.risk_tier == "safe"
