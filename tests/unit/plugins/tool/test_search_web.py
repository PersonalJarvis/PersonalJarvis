"""Tool-level tests for SearchWebTool.execute after the backend-chain rewire.

The tool delegates the actual lookup to search_backends.run_search; these tests
pin run_search and the settings loader so they exercise the tool's contract
(output shape, status passthrough, honest 'unavailable' detail) without any
network or config/keyring access.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool import search_web
from jarvis.plugins.tool.search_backends import SearchOutcome
from jarvis.plugins.tool.search_web import SearchWebTool


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="test",
        config={},
        memory_read=None,
        approved_by="auto",
    )


async def test_execute_returns_real_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_search(query, max_results, *, client, searcher=None):
        return SearchOutcome(
            results=[{"title": "Billboard", "snippet": "chart", "url": "u"}],
            backend="ddg_serp", status="ok")
    monkeypatch.setattr(search_web, "run_search", fake_run_search)
    result = await SearchWebTool().execute({"query": "top ten songs"}, _ctx())
    assert result.success is True
    assert result.output["results"][0]["title"] == "Billboard"
    assert result.output["status"] == "ok"
    assert result.output["backend"] == "ddg_serp"
    assert result.output["query"] == "top ten songs"


async def test_execute_unavailable_surfaces_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_search(query, max_results, *, client, searcher=None):
        return SearchOutcome(results=[], backend="ddg", status="unavailable")
    monkeypatch.setattr(search_web, "run_search", fake_run_search)
    result = await SearchWebTool().execute({"query": "top ten songs"}, _ctx())
    assert result.success is True
    assert result.output["status"] == "unavailable"
    assert "unavailable" in result.output["detail"].lower()


async def test_execute_empty_has_no_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_search(query, max_results, *, client, searcher=None):
        return SearchOutcome(results=[], backend="ddg", status="empty")
    monkeypatch.setattr(search_web, "run_search", fake_run_search)
    result = await SearchWebTool().execute({"query": "asdfqwer"}, _ctx())
    assert result.success is True
    assert result.output["status"] == "empty"
    assert "detail" not in result.output


async def test_execute_missing_query_still_fails() -> None:
    result = await SearchWebTool().execute({"query": "  "}, _ctx())
    assert result.success is False


async def test_execute_ok_carries_synthesis_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A result set with hits MUST tell the brain to synthesize, not read the
    raw hits aloud. Live forensic 2026-06-28 (voice Turn 4): Gemini read a whole
    DuckDuckGo result list verbatim — titles, dates, 'Weitere Ergebnisse von
    www.gutefrage.net' — instead of answering the question."""
    async def fake_run_search(query, max_results, *, client, searcher=None):
        return SearchOutcome(
            results=[{"title": "Notenschluessel", "snippet": "43 bis 34,5 = Note 2",
                      "url": "https://www.gutefrage.net/x"}],
            backend="ddg_serp", status="ok")
    monkeypatch.setattr(search_web, "run_search", fake_run_search)
    result = await SearchWebTool().execute({"query": "wie viele punkte note 1"}, _ctx())
    instr = (result.output.get("answer_instruction") or "").lower()
    assert instr, "ok result must carry an answer_instruction"
    # It must steer the brain away from reading titles / URLs / source names.
    assert "url" in instr
    assert "never" in instr


async def test_execute_empty_has_no_synthesis_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_search(query, max_results, *, client, searcher=None):
        return SearchOutcome(results=[], backend="ddg", status="empty")
    monkeypatch.setattr(search_web, "run_search", fake_run_search)
    result = await SearchWebTool().execute({"query": "asdfqwer"}, _ctx())
    assert "answer_instruction" not in result.output
