"""Unit tests for the web-search backends.

Covers the real-SERP backend, the Instant-Answer fallback, and run_search's
SERP-first / Instant-fallback ordering with honest status. No real network —
SERP uses an injected synchronous searcher, Instant uses httpx.MockTransport.
Backed by the 2026-06-15 "top ten songs" forensic: the old Instant-Answer-only
backend returned 202/empty for every freshness query.
"""
from __future__ import annotations

import httpx

from jarvis.plugins.tool.search_backends import (
    _default_ddgs_searcher,
    ddg_instant_search,
    ddg_serp_search,
    run_search,
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


_ABSTRACT = {
    "Heading": "Python", "AbstractText": "A programming language.",
    "AbstractURL": "https://en.wikipedia.org/wiki/Python", "RelatedTopics": [],
}


def _serp_rows(query: str, max_results: int) -> list[dict[str, str]]:
    return [
        {"title": "Billboard Hot 100", "body": "This week's chart...", "href": "https://billboard.com/"},
        {"title": "Top 10 songs", "body": "Current top ten...", "href": "https://example.com/"},
    ]


# ---------------------------------------------------------------------------
# Instant Answer (encyclopedic fallback)
# ---------------------------------------------------------------------------

async def test_ddg_instant_parses_abstract() -> None:
    async with _client(lambda r: httpx.Response(200, json=_ABSTRACT)) as client:
        outcome = await ddg_instant_search("python", 5, client)
    assert outcome.status == "ok"
    assert outcome.results[0]["snippet"] == "A programming language."


async def test_ddg_instant_202_empty_body_is_empty_not_unavailable() -> None:
    # 202/empty is DDG's "no instant answer" signal — a genuine EMPTY.
    async with _client(lambda r: httpx.Response(202, content=b"")) as client:
        outcome = await ddg_instant_search("top ten songs", 5, client)
    assert outcome.status == "empty"
    assert outcome.results == []


async def test_ddg_instant_transport_error_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")
    async with _client(handler) as client:
        outcome = await ddg_instant_search("python", 5, client)
    assert outcome.status == "unavailable"


# ---------------------------------------------------------------------------
# Real DuckDuckGo SERP
# ---------------------------------------------------------------------------

async def test_ddg_serp_maps_library_rows() -> None:
    outcome = await ddg_serp_search("top ten songs", 5, searcher=_serp_rows)
    assert outcome.status == "ok"
    assert outcome.results[0]["url"] == "https://billboard.com/"
    assert outcome.results[0]["snippet"] == "This week's chart..."


async def test_ddg_serp_empty_is_empty() -> None:
    outcome = await ddg_serp_search("zxqw", 5, searcher=lambda q, n: [])
    assert outcome.status == "empty"


async def test_ddg_serp_library_missing_is_unavailable() -> None:
    def boom(query: str, max_results: int) -> list[dict[str, str]]:
        raise RuntimeError("ddgs not installed")
    outcome = await ddg_serp_search("python", 5, searcher=boom)
    assert outcome.status == "unavailable"


# ---------------------------------------------------------------------------
# Real-library searcher: must NOT inherit ddgs "auto" mode
# ---------------------------------------------------------------------------

def test_default_searcher_pins_fast_backends_and_bounded_timeout(monkeypatch) -> None:
    """The real-library searcher must pin an explicit, fast backend list and a
    bounded DDGS timeout — never inherit ddgs "auto" mode.

    Live forensic 2026-06-16 (voice session 15:05, "best city … no taxes"):
    ddgs "auto" text mode front-loads the two SLOWEST engines — `wikipedia`
    (DNS-fails on the wt-wt region; it derives the nonexistent host
    `wt.wikipedia.org`) and `grokipedia` (times out). With max_results=5 the
    first concurrent batch is exactly those two (max_workers = ceil(5/10)+1 = 2),
    and with no DDGS(timeout=…) a single hung engine blocks the per-batch wait
    for the full default window. The aggregate blew the 5 s voice budget in
    search_web.execute → status="unavailable" → "search backend timed out",
    even though Google had already returned results.
    """
    import sys
    import types

    captured: dict[str, object] = {}

    class _FakeDDGS:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            captured["init_kwargs"] = kwargs

        def __enter__(self) -> _FakeDDGS:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def text(self, query, **kwargs):  # noqa: ANN001, ANN003, ANN201
            captured["text_kwargs"] = kwargs
            return [{"title": "t", "body": "b", "href": "https://x"}]

    fake_mod = types.ModuleType("ddgs")
    fake_mod.DDGS = _FakeDDGS  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ddgs", fake_mod)

    rows = _default_ddgs_searcher("best cities with no income tax", 5)
    assert rows, "searcher must return the library rows"

    init_kwargs = captured["init_kwargs"]
    assert isinstance(init_kwargs, dict)
    timeout = init_kwargs.get("timeout")
    assert timeout is not None, "DDGS must be constructed with a bounded timeout"
    assert isinstance(timeout, (int, float)) and 0 < timeout < 5.0

    text_kwargs = captured["text_kwargs"]
    assert isinstance(text_kwargs, dict)
    backend = text_kwargs.get("backend")
    assert backend, "must pin an explicit backend, not inherit ddgs 'auto'"
    parts = {b.strip().lower() for b in str(backend).split(",")}
    assert "auto" not in parts and "all" not in parts
    # The two slow engines that sank the live turn must be excluded.
    assert "wikipedia" not in parts and "grokipedia" not in parts
    # Breadth is the reliability lever: any single ddgs engine is flaky
    # (transient "No results"), but several racing always return.
    assert len(parts) >= 3, "keep enough fast engines for reliability"
    # Every pinned name must be a VALID ddgs `text` backend — an unknown name
    # (e.g. `bing`, which is images/news only) is silently dropped with a
    # per-call warning, quietly shrinking the pool. Valid set per ddgs 9.14.x;
    # re-validate when bumping the dependency.
    _VALID_DDGS_TEXT_BACKENDS = {
        "brave", "duckduckgo", "google", "grokipedia",
        "mojeek", "startpage", "wikipedia", "yahoo", "yandex",
    }
    unknown = parts - _VALID_DDGS_TEXT_BACKENDS
    assert not unknown, f"unknown ddgs text backend(s) would be dropped: {unknown}"


# ---------------------------------------------------------------------------
# run_search: SERP first, Instant fallback, honest status
# ---------------------------------------------------------------------------

async def test_run_search_prefers_real_serp() -> None:
    # SERP has results → returned directly; Instant (httpx) never consulted.
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Instant must not be called when SERP succeeds")
    async with _client(handler) as client:
        outcome = await run_search("top ten songs", 5, client=client, searcher=_serp_rows)
    assert outcome.status == "ok"
    assert outcome.backend == "ddg_serp"


async def test_run_search_falls_back_to_instant_when_serp_empty() -> None:
    async with _client(lambda r: httpx.Response(200, json=_ABSTRACT)) as client:
        outcome = await run_search("python", 5, client=client, searcher=lambda q, n: [])
    assert outcome.status == "ok"
    assert outcome.backend == "ddg_instant"


async def test_run_search_empty_when_searched_but_nothing_found() -> None:
    # SERP empty + Instant empty (202) → honest 'empty', not 'unavailable'.
    async with _client(lambda r: httpx.Response(202, content=b"")) as client:
        outcome = await run_search("asdfqwer", 5, client=client, searcher=lambda q, n: [])
    assert outcome.status == "empty"


async def test_run_search_unavailable_when_both_fail() -> None:
    def boom_searcher(query: str, max_results: int) -> list[dict[str, str]]:
        raise RuntimeError("ddgs not installed")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")
    async with _client(handler) as client:
        outcome = await run_search("python", 5, client=client, searcher=boom_searcher)
    assert outcome.status == "unavailable"
