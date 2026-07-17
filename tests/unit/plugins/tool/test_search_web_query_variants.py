"""Concurrent query variants in one search_web call (BUG-072 follow-up).

A research question used to cost TWO sequential model rounds: the first
search's evidence was thin, so the model refined its query in the NEXT
round — a full provider round-trip per refinement (live: the Bugatti-Divo
question ran two searches ≈ 12.5 s). Variants passed via ``queries`` run
concurrently inside one tool call, so all evidence arrives in one round.

Contract under test:
  1. ``queries`` variants all reach the backend; results merge interleaved
     and deduplicate by URL; ``ok`` when anything was found.
  2. The variant list is bounded (3) and deduplicated case-insensitively;
     the primary ``query`` is always variant one.
  3. A single-query call behaves exactly as before (no wrapper changes).
  4. Status honesty: all-empty merges to ``empty``, all-unavailable to
     ``unavailable``.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool import search_web
from jarvis.plugins.tool.search_backends import SearchOutcome

_CTX = ExecutionContext(
    trace_id=uuid4(),
    user_utterance="test",
    config={},
    memory_read=None,
    approved_by="auto",
)


def _hit(url: str, title: str = "t") -> dict[str, str]:
    return {"title": title, "snippet": "s", "url": url}


@pytest.mark.asyncio
async def test_variants_run_and_merge_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def fake_run_search(query, max_results, *, client, searcher=None):
        seen.append(query)
        by_query = {
            "bugatti divo for sale europe": [_hit("https://a.example"), _hit("https://b.example")],
            "bugatti divo kaufen": [_hit("https://a.example"), _hit("https://c.example")],
        }
        return SearchOutcome(
            results=by_query.get(query, []), backend="ddg_serp", status="ok"
        )

    monkeypatch.setattr(search_web, "run_search", fake_run_search)
    tool = search_web.SearchWebTool()

    result = await tool.execute(
        {
            "query": "bugatti divo for sale europe",
            "queries": ["bugatti divo kaufen"],
        },
        _CTX,
    )

    assert result.success
    assert sorted(seen) == ["bugatti divo for sale europe", "bugatti divo kaufen"]
    urls = [r["url"] for r in result.output["results"]]
    # Rank-interleaved merge: rank 0 of each variant first (variant 2's rank-0
    # hit deduplicates against variant 1's), then rank 1 of each variant.
    assert urls == ["https://a.example", "https://b.example", "https://c.example"]
    assert result.output["status"] == "ok"
    assert result.output["queries"] == [
        "bugatti divo for sale europe",
        "bugatti divo kaufen",
    ]


@pytest.mark.asyncio
async def test_variant_list_is_bounded_and_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def fake_run_search(query, max_results, *, client, searcher=None):
        seen.append(query)
        return SearchOutcome(results=[_hit(f"https://{len(seen)}.example")],
                             backend="ddg_serp", status="ok")

    monkeypatch.setattr(search_web, "run_search", fake_run_search)
    tool = search_web.SearchWebTool()

    result = await tool.execute(
        {
            "query": "primary",
            "queries": ["PRIMARY", "second", "third", "fourth", "fifth"],
        },
        _CTX,
    )

    assert result.success
    # Case-insensitive dedupe of the primary + hard cap of 3 variants.
    assert seen == ["primary", "second", "third"]


@pytest.mark.asyncio
async def test_single_query_output_shape_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_search(query, max_results, *, client, searcher=None):
        return SearchOutcome(results=[_hit("https://only.example")],
                             backend="ddg_serp", status="ok")

    monkeypatch.setattr(search_web, "run_search", fake_run_search)
    tool = search_web.SearchWebTool()

    result = await tool.execute({"query": "single"}, _CTX)

    assert result.success
    assert result.output["query"] == "single"
    assert "queries" not in result.output
    assert [r["url"] for r in result.output["results"]] == ["https://only.example"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("statuses", "expected"),
    [(("empty", "empty"), "empty"), (("unavailable", "unavailable"), "unavailable")],
)
async def test_merged_status_stays_honest(
    monkeypatch: pytest.MonkeyPatch,
    statuses: tuple[str, str],
    expected: str,
) -> None:
    calls: list[str] = []

    async def fake_run_search(query, max_results, *, client, searcher=None):
        calls.append(query)
        return SearchOutcome(
            results=[], backend="ddg", status=statuses[len(calls) - 1]
        )

    monkeypatch.setattr(search_web, "run_search", fake_run_search)
    tool = search_web.SearchWebTool()

    result = await tool.execute({"query": "a", "queries": ["b"]}, _CTX)

    assert result.success
    assert result.output["status"] == expected
    assert result.output["results"] == []
