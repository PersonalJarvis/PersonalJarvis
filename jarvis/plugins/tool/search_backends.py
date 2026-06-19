"""Pluggable web-search backends for the search_web tool.

The historical backend was the DuckDuckGo *Instant Answer* API
(api.duckduckgo.com), which returns only DuckDuckGo's curated knowledge box
(AbstractText / RelatedTopics). It has NO real-time index, so freshness
queries — music charts, news, prices, sports, "what's trending" — come back
202/empty and the brain (correctly) reports it found nothing. See the
2026-06-15 "top ten songs" forensic (session 95a404b4) and the 2026-06-10
weather forensic.

This module replaces that single backend with a priority chain that returns
real web results for any query, while keeping a key-free default so the base
install still searches on a fresh python:3.11-slim VPS:

    keyed API (Brave, if a key is configured)
        -> real DuckDuckGo SERP (key-free, default)
            -> DuckDuckGo Instant Answer (last-resort encyclopedic abstract)

Each backend returns a SearchOutcome with an explicit status so the brain can
tell "searched, genuinely empty" from "backend temporarily unavailable" and
phrase the spoken answer honestly instead of always saying "no results".

This module performs no config or secret access — the caller (search_web.py)
loads the backend preference and the optional Brave key and passes them in.
That keeps every function here pure and unit-testable with an injected httpx
client (httpx.MockTransport) or an injected synchronous searcher.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Literal

SearchResult = dict[str, str]
SearchStatus = Literal["ok", "empty", "unavailable"]

_INSTANT_URL: Final[str] = "https://api.duckduckgo.com/"


@dataclass(frozen=True)
class SearchOutcome:
    """The result of one search attempt.

    ``status`` is the honesty signal:
      * ``ok``          — reached the index and has results.
      * ``empty``       — reached the index, genuinely no results.
      * ``unavailable`` — could not reach the search service (timeout / error /
                          library missing). The brain must NOT claim "no
                          results" for this case — it should say search is down.
    """
    results: list[SearchResult]
    backend: str
    status: SearchStatus


# ---------------------------------------------------------------------------
# DuckDuckGo Instant Answer (encyclopedic fallback)
# ---------------------------------------------------------------------------

async def ddg_instant_search(query: str, max_results: int, client: Any) -> SearchOutcome:
    """DuckDuckGo Instant Answer API — encyclopedic abstracts only, no
    real-time data. Kept as the final encyclopedic fallback in the chain."""
    try:
        resp = await client.get(
            _INSTANT_URL,
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            follow_redirects=True,
        )
    except Exception:  # noqa: BLE001 — network / transport error -> unavailable
        return SearchOutcome(results=[], backend="ddg_instant", status="unavailable")
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001 — a 202/empty/non-JSON body is DDG's "I
        # have no instant answer" signal: a genuine EMPTY, not a failure.
        data = {}

    results: list[SearchResult] = []
    abstract = data.get("AbstractText") or data.get("Abstract") or ""
    if abstract:
        results.append({"title": data.get("Heading", ""), "snippet": abstract,
                        "url": data.get("AbstractURL", "")})
    for topic in (data.get("RelatedTopics") or [])[:max_results]:
        if isinstance(topic, dict) and topic.get("Text"):
            results.append({"title": topic.get("Text", "")[:80],
                            "snippet": topic.get("Text", ""),
                            "url": topic.get("FirstURL", "")})
        if len(results) >= max_results:
            break
    return SearchOutcome(results=results[:max_results], backend="ddg_instant",
                         status="ok" if results else "empty")


# ---------------------------------------------------------------------------
# Real DuckDuckGo SERP (key-free default)
# ---------------------------------------------------------------------------

# A searcher returns RAW ddgs rows (``{"title", "body", "href"}``); the
# mapping to the SearchResult shape happens in ddg_serp_search so the injected
# test searcher and the real library searcher share one code path.
DdgsSearcher = Callable[[str, int], list[dict[str, Any]]]

# Curated, fast, key-free text engines. The ddgs "auto" default is a trap on the
# voice path: for text queries it deliberately front-loads the two SLOWEST
# engines — ``wikipedia`` (DNS-fails on the wt-wt region: it derives the
# nonexistent host ``wt.wikipedia.org``) and ``grokipedia`` (regularly times
# out) — and with max_results=5 the first concurrent batch is exactly those two
# (ddgs sets max_workers = ceil(5/10)+1 = 2). Their aggregate latency blew the
# 5 s budget enforced by search_web.execute → status="unavailable" → the spoken
# "search backend timed out", even though Google had already returned. Live
# forensic 2026-06-16, voice session 15:05 ("best city … no taxes").
#
# The engine names MUST be valid ddgs ``text`` backends — an unknown name is
# silently dropped with a per-call "backends do not exist" warning. As of ddgs
# 9.14.x the valid text engines are: brave, duckduckgo, google, grokipedia,
# mojeek, startpage, wikipedia, yahoo, yandex. NOTE: ``bing`` is NOT a text
# backend (only images/news) — do not add it. ``brave`` is excluded because it
# rate-limits aggressively (HTTP 429) from a server IP. Any single engine is
# flaky in isolation (transient "No results"), so BREADTH is the reliability
# lever and the bounded timeout is the latency lever: this five-engine set
# returned results in 0.66–0.75 s across 3/3 live runs (validated against the
# running app, voice session 15:40). Re-validate this list when bumping ddgs.
_DDGS_BACKENDS: Final[str] = "duckduckgo, mojeek, google, yahoo, startpage"

# Per-engine ceiling inside ddgs. ddgs waits each concurrent batch for up to its
# DDGS(timeout=…) window; with no timeout a single hung engine blocks until the
# OUTER asyncio.timeout (5 s) cancels the whole call. A 4 s cap stays below that
# 5 s budget so a slow engine degrades to "use whatever the fast engines already
# returned" instead of sinking the turn.
_DDGS_TIMEOUT_S: Final[float] = 4.0


def _default_ddgs_searcher(query: str, max_results: int) -> list[dict[str, Any]]:
    """Real DuckDuckGo SERP via the ``ddgs`` package (renamed from
    ``duckduckgo_search`` in 2025). Pure-Python, cross-platform, key-free, so
    it is safe for the headless VPS base install. Imported lazily so a minimal
    install without it degrades to the Instant-Answer backend. Returns the raw
    library rows; ddg_serp_search maps them to the SearchResult shape.

    Pins an explicit ``backend`` and a bounded ``timeout`` so the slow/flaky
    engines in ddgs "auto" mode cannot blow the voice budget (see
    ``_DDGS_BACKENDS`` / ``_DDGS_TIMEOUT_S``)."""
    text_kwargs: dict[str, Any] = {
        "region": "wt-wt", "safesearch": "moderate", "max_results": max_results,
    }
    try:
        from ddgs import DDGS  # type: ignore
        # ``backend`` is the modern multi-engine ddgs surface; the legacy
        # ``duckduckgo_search.text`` below does not accept it.
        text_kwargs["backend"] = _DDGS_BACKENDS
    except Exception:  # noqa: BLE001 — older package name
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("ddgs not installed") from exc
    with DDGS(timeout=_DDGS_TIMEOUT_S) as ddgs:
        return list(ddgs.text(query, **text_kwargs) or [])


async def ddg_serp_search(
    query: str,
    max_results: int,
    *,
    searcher: DdgsSearcher | None = None,
) -> SearchOutcome:
    """Real DuckDuckGo SERP (full web results), key-free. The ``ddgs`` call is
    synchronous, so it runs in a worker thread to keep the voice event loop
    free; the caller's asyncio.timeout bounds the total wait."""
    fn = searcher or _default_ddgs_searcher
    try:
        rows = await asyncio.to_thread(fn, query, max_results)
    except Exception:  # noqa: BLE001 — library missing / rate-limit / parse
        return SearchOutcome(results=[], backend="ddg_serp", status="unavailable")
    results: list[SearchResult] = [{
        "title": str(row.get("title", "")),
        "snippet": str(row.get("body", "")),
        "url": str(row.get("href", "")),
    } for row in rows[:max_results]]
    return SearchOutcome(results=results, backend="ddg_serp",
                         status="ok" if results else "empty")


# ---------------------------------------------------------------------------
# Resolver: real SERP first, Instant Answer as the encyclopedic fallback
# ---------------------------------------------------------------------------

async def run_search(
    query: str,
    max_results: int,
    *,
    client: Any,
    searcher: DdgsSearcher | None = None,
) -> SearchOutcome:
    """Real DuckDuckGo web search first; the DuckDuckGo Instant-Answer box as a
    cheap encyclopedic fallback. Honest status: ``ok`` with results, otherwise
    ``empty`` if a backend actually reached its index, else ``unavailable`` so
    the brain says search is down rather than claiming there is nothing."""
    saw_empty = False
    serp = await ddg_serp_search(query, max_results, searcher=searcher)
    if serp.status == "ok" and serp.results:
        return serp
    saw_empty = serp.status == "empty"

    instant = await ddg_instant_search(query, max_results, client)
    if instant.status == "ok" and instant.results:
        return instant
    saw_empty = saw_empty or instant.status == "empty"

    return SearchOutcome(results=[], backend="ddg",
                         status="empty" if saw_empty else "unavailable")
