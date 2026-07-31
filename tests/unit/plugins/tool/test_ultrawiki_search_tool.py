"""``ultrawiki-search`` — router-tier hybrid search with an honesty ladder.

Pins the ADR-0011 amendment "UltraWiki Search": ROUTER_TOOLS membership, the
safe read-only contract, and the anti-confabulation ladder (no service /
disabled mode / search failure / honest empty vs. rendered hits).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

import jarvis.plugins.tool.ultrawiki_search as mod
from jarvis.plugins.tool.ultrawiki_search import UltraWikiSearchTool


@dataclass
class _Hit:
    item_id: int = 1
    source_id: str = "obsidian_vault"
    title: str = "Project Phoenix"
    snippet: str = "Phoenix is the rewrite of the ingestion layer started in June."
    permalink: str = "uw://item/1"
    timestamp_utc: str = "2026-07-20T10:00:00Z"
    score: float = 0.03
    matched_by: tuple[str, ...] = field(default_factory=lambda: ("keyword", "vector"))


class _FakeService:
    def __init__(self, hits: list[Any] | None = None, error: Exception | None = None) -> None:
        self._hits = hits or []
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def search(
        self, query: str, *, k: int, area_id: str | None, rerank: bool = True
    ) -> list[Any]:
        self.calls.append({"query": query, "k": k, "area_id": area_id, "rerank": rerank})
        if self._error is not None:
            raise self._error
        return self._hits


def _tool(service: Any, *, enabled: bool = True) -> UltraWikiSearchTool:
    tool = UltraWikiSearchTool(service_resolver=lambda: service)
    tool._mode_enabled = staticmethod(lambda: enabled)  # type: ignore[method-assign]
    return tool


def test_registered_in_router_tools() -> None:
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "ultrawiki-search" in ROUTER_TOOLS


def test_contract_is_safe_read_only() -> None:
    tool = UltraWikiSearchTool(service_resolver=lambda: None)
    assert tool.name == "ultrawiki-search"
    assert tool.risk_tier == "safe"
    assert tool.schema["required"] == ["query"]


async def test_no_service_is_an_honest_error() -> None:
    tool = _tool(None, enabled=True)
    result = await tool.execute({"query": "phoenix"}, ctx=None)
    assert result.success is False
    assert "not running" in (result.error or "")


async def test_disabled_mode_steers_to_classic_wiki() -> None:
    tool = _tool(_FakeService(), enabled=False)
    result = await tool.execute({"query": "phoenix"}, ctx=None)
    assert result.success is False
    assert "wiki-recall" in (result.error or "")


async def test_search_failure_surfaces_honestly() -> None:
    tool = _tool(_FakeService(error=RuntimeError("store gone")), enabled=True)
    result = await tool.execute({"query": "phoenix"}, ctx=None)
    assert result.success is False
    assert "store gone" in (result.error or "")


async def test_empty_result_affirms_reachability() -> None:
    """An honest 'empty' must never be narratable as 'the store is down'."""
    tool = _tool(_FakeService(hits=[]), enabled=True)
    result = await tool.execute({"query": "phoenix"}, ctx=None)
    assert result.success is True
    assert "searched successfully" in result.output


async def test_hits_render_with_citation_and_legs() -> None:
    service = _FakeService(hits=[_Hit()])
    tool = _tool(service, enabled=True)
    result = await tool.execute({"query": "phoenix", "k": 3, "area": "work"}, ctx=None)
    assert result.success is True
    assert "Project Phoenix" in result.output
    assert "uw://item/1" in result.output  # citation permalink
    assert "keyword" in result.output  # matched_by legs stay visible
    assert service.calls == [
        {"query": "phoenix", "k": 3, "area_id": "work", "rerank": False}
    ]


async def test_tool_searches_llm_free() -> None:
    """doc 03 'primitive tools': the consumer is a model and judges relevance
    itself — the tool must never pay a second model call to pre-grade."""
    service = _FakeService(hits=[_Hit()])
    tool = _tool(service, enabled=True)
    await tool.execute({"query": "phoenix"}, ctx=None)
    assert service.calls[0]["rerank"] is False


async def test_k_is_clamped_to_schema_bounds() -> None:
    service = _FakeService(hits=[_Hit()])
    tool = _tool(service, enabled=True)
    await tool.execute({"query": "phoenix", "k": 99}, ctx=None)
    assert service.calls[0]["k"] == 10


async def test_empty_query_is_a_clean_no_op() -> None:
    service = _FakeService()
    tool = _tool(service, enabled=True)
    result = await tool.execute({"query": "  "}, ctx=None)
    assert result.success is True
    assert service.calls == []


def test_default_resolver_reads_web_app_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from jarvis.core import runtime_refs

    sentinel = object()
    monkeypatch.setattr(
        runtime_refs,
        "get_web_app",
        lambda: SimpleNamespace(state=SimpleNamespace(ultrawiki=sentinel)),
    )
    assert mod.build_ultrawiki_service_resolver()() is sentinel
