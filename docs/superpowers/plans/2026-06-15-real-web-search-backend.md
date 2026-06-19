# Real Web-Search Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `search_web` tool's DuckDuckGo *Instant Answer* backend — which has no real-time index and returns `202`/empty for every freshness query (music charts, news, prices, sports) — with a pluggable backend chain that returns real web results for **every** brain provider, keeps a key-free default so the headless VPS base install still searches, auto-upgrades to a keyed API when one is configured, and (opt-in) lets Gemini use its native Google Search grounding.

**Architecture:** A new `jarvis/plugins/tool/search_backends.py` module holds independent async backend functions (`brave_search`, `ddg_serp_search`, `ddg_instant_search`) that each return a `SearchOutcome` carrying an explicit `status` (`ok` / `empty` / `unavailable`). A resolver tries them in priority order — `keyed API → real DDG SERP → DDG Instant Answer` — and the first backend with real results wins. `SearchWebTool.execute` keeps its public output shape (`{"query","results"}`), its weather fast-path, and its 5 s router-tier deadline, but delegates the actual lookup to the resolver and adds the honesty `status`. Gemini native grounding is a separate, config-gated change in `jarvis/plugins/brain/gemini.py`.

**Tech Stack:** Python 3.11, `httpx` (already used), `ddgs` (maintained successor to `duckduckgo_search`, pure-Python/cross-platform, key-free), `google-genai` (already used for Gemini), `pytest` with `httpx.MockTransport` and dependency-injected fakes (no `unittest.mock`, per repo convention).

**Root cause (proven 2026-06-15):** Flight-recorder forensic of session `95a404b4…` shows the "top ten songs" turn fired `search_web` twice against `https://api.duckduckgo.com/`; both returned `HTTP 202`/empty (the documented "DDG has no data for this query" signal — see the weather note in `search_web.py:5-12`), so Gemini honestly reported it found nothing. The bug is the backend's capability, not the brain provider — it fails identically on Claude/Grok/OpenAI.

**Non-negotiable constraints (from CLAUDE.md / CLOUD.md):**
- Base install must still boot and search on a fresh `python:3.11-slim` Linux container with no API key → the default backend must be key-free; `ddgs` import must be lazy with graceful fallback.
- Router-tier tool on the voice path: keep the single `asyncio.timeout(_TIMEOUT_S=5.0)` hard ceiling (`search_web.py:27`). The synchronous `ddgs` call MUST run in `asyncio.to_thread` so it never blocks the event loop.
- All artifacts (code, comments, docstrings, tests, config descriptions, the spoken `detail` note) are **English** (Output Language Policy).
- API keys come only from `get_secret(...)` (Credential Manager → ENV → `.env`). **Never** write a key into `jarvis.toml` or a commit (AP-12).
- Config schema changes go through the existing Pydantic model and must tolerate `extra="allow"` (AP-16); any runtime config write goes through `config_writer` (AP-7) — this plan only adds *read* keys, no runtime writes.
- TDD throughout: failing test → run it red → minimal implementation → run it green → commit.

**Preflight (do once before Task 1):**
```
pwsh scripts/preflight.ps1
python -c "import jarvis; print(jarvis.__file__)"
```
Confirm the editable install resolves to THIS worktree (BUG-006/014 guard). Fix before coding if it exits non-zero.

---

## File Structure

| File | Responsibility |
|---|---|
| `jarvis/plugins/tool/search_backends.py` | **New.** `SearchOutcome` dataclass, `SearchSettings`, the three backend functions, `build_chain`, `run_search`. Pure logic, fully unit-testable with injected client/searcher. |
| `jarvis/plugins/tool/search_web.py` | **Modify.** `execute` delegates to `run_search`; keep weather fast-path, 5 s ceiling, `{"query","results"}` shape; add `status`/`detail`. |
| `jarvis/core/config.py` | **Modify.** Add a `SearchConfig` sub-model + `search` field on the root `JarvisConfig` (backend preference only — no keys). |
| `jarvis/plugins/brain/gemini.py` | **Modify (Wave 3).** Add the native `google_search` built-in tool to the request when `[brain.gemini].native_search_grounding` is set. |
| `requirements.txt` | **Modify.** Add `ddgs`. |
| `tests/unit/plugins/tool/test_search_backends.py` | **New.** Unit tests for every backend + chain ordering + status logic. |
| `tests/unit/plugins/tool/test_search_web.py` | **New/Extend.** Tool-level tests: weather path untouched, real-SERP result returned, unavailable status surfaces `detail`. |
| `tests/unit/plugins/brain/test_gemini_grounding.py` | **New (Wave 3).** Verifies the google_search tool is added iff the flag is set. |

Existing guard tests to keep green: `tests/unit/plugins/tool/test_search_web_router_tier.py` (5 s timeout pin), `tests/unit/plugins/tool/test_search_web_weather.py` (Open-Meteo fast-path).

---

# Wave 1 — Pluggable backend + real DDG SERP default (the core cross-provider fix)

> Wave 1 alone fixes the reported bug for **all** providers, key-free. Waves 2 and 3 are independent enhancements.

### Task 1: `SearchOutcome` + Instant-Answer backend (extracted, status-aware)

**Files:**
- Create: `jarvis/plugins/tool/search_backends.py`
- Test: `tests/unit/plugins/tool/test_search_backends.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/plugins/tool/test_search_backends.py
import httpx
import pytest

from jarvis.plugins.tool.search_backends import ddg_instant_search


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_ddg_instant_parses_abstract():
    def handler(request):
        return httpx.Response(200, json={
            "Heading": "Python", "AbstractText": "A programming language.",
            "AbstractURL": "https://en.wikipedia.org/wiki/Python",
            "RelatedTopics": [],
        })
    async with _client(handler) as client:
        outcome = await ddg_instant_search("python", 5, client)
    assert outcome.status == "ok"
    assert outcome.backend == "ddg_instant"
    assert outcome.results[0]["snippet"] == "A programming language."


@pytest.mark.asyncio
async def test_ddg_instant_202_empty_body_is_empty_not_unavailable():
    # The 202/empty body is DDG's "no instant answer" signal — a genuine
    # EMPTY, not a transport failure (forensic 2026-06-15 top-ten-songs turn).
    def handler(request):
        return httpx.Response(202, content=b"")
    async with _client(handler) as client:
        outcome = await ddg_instant_search("top ten songs", 5, client)
    assert outcome.status == "empty"
    assert outcome.results == []


@pytest.mark.asyncio
async def test_ddg_instant_transport_error_is_unavailable():
    def handler(request):
        raise httpx.ConnectError("boom")
    async with _client(handler) as client:
        outcome = await ddg_instant_search("python", 5, client)
    assert outcome.status == "unavailable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/plugins/tool/test_search_backends.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.plugins.tool.search_backends'`

- [ ] **Step 3: Write minimal implementation**

```python
# jarvis/plugins/tool/search_backends.py
"""Pluggable web-search backends for the search_web tool.

The historical backend was the DuckDuckGo *Instant Answer* API
(api.duckduckgo.com), which returns only DuckDuckGo's curated knowledge box
(AbstractText / RelatedTopics). It has NO real-time index, so freshness
queries — music charts, news, prices, sports, "what's trending" — come back
202/empty and the brain (correctly) reports it found nothing. See the
2026-06-15 "top ten songs" forensic and the 2026-06-10 weather forensic.

This module replaces that single backend with a priority chain that returns
real web results for any query, while keeping a key-free default so the base
install still searches on a fresh python:3.11-slim VPS:

    keyed API (Brave, if a key is configured)
        -> real DuckDuckGo SERP (key-free, default)
            -> DuckDuckGo Instant Answer (last-resort encyclopedic abstract)

Each backend returns a SearchOutcome with an explicit status so the brain can
tell "searched, genuinely empty" from "backend temporarily unavailable" and
phrase the spoken answer honestly instead of always saying "no results".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal

SearchResult = dict[str, str]
SearchStatus = Literal["ok", "empty", "unavailable"]

_INSTANT_URL: Final[str] = "https://api.duckduckgo.com/"


@dataclass(frozen=True)
class SearchOutcome:
    results: list[SearchResult]
    backend: str
    status: SearchStatus


async def ddg_instant_search(query: str, max_results: int, client: Any) -> SearchOutcome:
    """DuckDuckGo Instant Answer API — encyclopedic abstracts only, no
    real-time data. Kept as the final encyclopedic fallback in the chain."""
    try:
        resp = await client.get(
            _INSTANT_URL,
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            follow_redirects=True,
        )
        # A 202/empty body is DDG's "I have no instant answer" — that is an
        # EMPTY result, not a failure, so resp.json() must not be called on an
        # empty body (it would raise and mask the real status).
        data = resp.json() if resp.content else {}
    except Exception:  # noqa: BLE001 — network / decode error
        return SearchOutcome(results=[], backend="ddg_instant", status="unavailable")

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/plugins/tool/test_search_backends.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add jarvis/plugins/tool/search_backends.py tests/unit/plugins/tool/test_search_backends.py
git commit -m "feat(search): add status-aware DDG Instant-Answer backend"
```

---

### Task 2: Real DuckDuckGo SERP backend (key-free default)

**Files:**
- Modify: `jarvis/plugins/tool/search_backends.py`
- Test: `tests/unit/plugins/tool/test_search_backends.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/plugins/tool/test_search_backends.py
from jarvis.plugins.tool.search_backends import ddg_serp_search


@pytest.mark.asyncio
async def test_ddg_serp_maps_library_rows():
    def fake_searcher(query, max_results):
        return [
            {"title": "Billboard Hot 100", "body": "This week's chart...", "href": "https://billboard.com/"},
            {"title": "Top 10 songs", "body": "Current top ten...", "href": "https://example.com/"},
        ]
    outcome = await ddg_serp_search("top ten songs", 5, searcher=fake_searcher)
    assert outcome.status == "ok"
    assert outcome.backend == "ddg_serp"
    assert outcome.results[0]["url"] == "https://billboard.com/"
    assert outcome.results[0]["snippet"] == "This week's chart..."


@pytest.mark.asyncio
async def test_ddg_serp_empty_is_empty():
    outcome = await ddg_serp_search("zxqw", 5, searcher=lambda q, n: [])
    assert outcome.status == "empty"


@pytest.mark.asyncio
async def test_ddg_serp_library_missing_is_unavailable():
    def boom(query, max_results):
        raise RuntimeError("ddgs not installed")
    outcome = await ddg_serp_search("python", 5, searcher=boom)
    assert outcome.status == "unavailable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/plugins/tool/test_search_backends.py -k ddg_serp -v`
Expected: FAIL with `ImportError: cannot import name 'ddg_serp_search'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to jarvis/plugins/tool/search_backends.py
import asyncio
from typing import Callable

DdgsSearcher = Callable[[str, int], list[SearchResult]]


def _default_ddgs_searcher(query: str, max_results: int) -> list[SearchResult]:
    """Real DuckDuckGo SERP via the `ddgs` package (renamed from
    `duckduckgo_search` in 2025). Pure-Python, cross-platform, key-free, so it
    is safe for the headless VPS base install. Imported lazily so a minimal
    install without it degrades to the Instant-Answer backend."""
    try:
        from ddgs import DDGS  # type: ignore
    except Exception:  # noqa: BLE001 — older package name
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("ddgs not installed") from exc
    out: list[SearchResult] = []
    with DDGS() as ddgs:
        for row in ddgs.text(query, region="wt-wt", safesearch="moderate",
                             max_results=max_results) or []:
            out.append({
                "title": str(row.get("title", "")),
                "snippet": str(row.get("body", "")),
                "url": str(row.get("href", "")),
            })
    return out


async def ddg_serp_search(
    query: str,
    max_results: int,
    *,
    searcher: DdgsSearcher | None = None,
) -> SearchOutcome:
    """Real DuckDuckGo SERP (full web results), key-free. The `ddgs` call is
    synchronous, so it runs in a worker thread to keep the voice event loop
    free; the caller's asyncio.timeout bounds the total wait."""
    fn = searcher or _default_ddgs_searcher
    try:
        results = await asyncio.to_thread(fn, query, max_results)
    except Exception:  # noqa: BLE001 — library missing / rate-limit / parse
        return SearchOutcome(results=[], backend="ddg_serp", status="unavailable")
    return SearchOutcome(results=results[:max_results], backend="ddg_serp",
                         status="ok" if results else "empty")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/plugins/tool/test_search_backends.py -k ddg_serp -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add jarvis/plugins/tool/search_backends.py tests/unit/plugins/tool/test_search_backends.py
git commit -m "feat(search): add real DuckDuckGo SERP backend (key-free default)"
```

---

### Task 3: Backend chain + resolver

**Files:**
- Modify: `jarvis/plugins/tool/search_backends.py`
- Test: `tests/unit/plugins/tool/test_search_backends.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/plugins/tool/test_search_backends.py
from jarvis.plugins.tool.search_backends import (
    SearchOutcome, SearchSettings, build_chain, run_search,
)


def _outcome(status, n=0, backend="x"):
    res = [{"title": "t", "snippet": "s", "url": "u"} for _ in range(n)]
    return SearchOutcome(results=res, backend=backend, status=status)


@pytest.mark.asyncio
async def test_run_search_returns_first_backend_with_results():
    calls = []
    async def b_empty():
        calls.append("empty"); return _outcome("empty")
    async def b_ok():
        calls.append("ok"); return _outcome("ok", n=2, backend="ddg_serp")
    async def b_never():
        calls.append("never"); return _outcome("ok", n=9)
    outcome = await run_search([("a", b_empty), ("b", b_ok), ("c", b_never)])
    assert outcome.backend == "ddg_serp"
    assert "never" not in calls  # short-circuits after first real hit


@pytest.mark.asyncio
async def test_run_search_reports_empty_when_a_backend_reached_index():
    async def b_empty():
        return _outcome("empty")
    async def b_unavail():
        return _outcome("unavailable")
    outcome = await run_search([("a", b_empty), ("b", b_unavail)])
    assert outcome.status == "empty"  # at least one backend truly searched


@pytest.mark.asyncio
async def test_run_search_reports_unavailable_when_all_failed():
    async def b_unavail():
        return _outcome("unavailable")
    outcome = await run_search([("a", b_unavail)])
    assert outcome.status == "unavailable"


def test_build_chain_auto_without_key_skips_brave():
    chain = build_chain("q", 5, settings=SearchSettings(), client=object())
    names = [n for n, _ in chain]
    assert names == ["ddg_serp", "ddg_instant"]


def test_build_chain_auto_with_key_prepends_brave():
    chain = build_chain("q", 5, settings=SearchSettings(brave_key="k"), client=object())
    names = [n for n, _ in chain]
    assert names == ["brave", "ddg_serp", "ddg_instant"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/plugins/tool/test_search_backends.py -k "run_search or build_chain" -v`
Expected: FAIL with `ImportError: cannot import name 'SearchSettings'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to jarvis/plugins/tool/search_backends.py
from typing import Awaitable

Backend = Callable[[], Awaitable[SearchOutcome]]


@dataclass(frozen=True)
class SearchSettings:
    """Backend preference + optional keys. Keys are loaded from get_secret by
    the caller — they NEVER live in jarvis.toml (AP-12)."""
    backend: str = "auto"  # auto | brave | ddg_serp | ddg_instant
    brave_key: str = ""


def build_chain(
    query: str,
    max_results: int,
    *,
    settings: SearchSettings,
    client: Any,
    searcher: DdgsSearcher | None = None,
) -> list[tuple[str, Backend]]:
    """Build the ordered backend chain from settings. Instant Answer is always
    appended as the final encyclopedic fallback."""
    chain: list[tuple[str, Backend]] = []

    def brave() -> tuple[str, Backend]:
        return ("brave", lambda: brave_search(query, max_results, client, settings.brave_key))

    def serp() -> tuple[str, Backend]:
        return ("ddg_serp", lambda: ddg_serp_search(query, max_results, searcher=searcher))

    def instant() -> tuple[str, Backend]:
        return ("ddg_instant", lambda: ddg_instant_search(query, max_results, client))

    pref = settings.backend
    if pref == "brave" and settings.brave_key:
        chain.append(brave())
    elif pref == "ddg_serp":
        chain.append(serp())
    elif pref == "ddg_instant":
        chain.append(instant())
    else:  # auto
        if settings.brave_key:
            chain.append(brave())
        chain.append(serp())

    if not any(name == "ddg_instant" for name, _ in chain):
        chain.append(instant())
    return chain


async def run_search(chain: list[tuple[str, Backend]]) -> SearchOutcome:
    """Try backends in order; first with real results wins. If none has
    results, report 'empty' when at least one backend reached its index,
    else 'unavailable'."""
    saw_empty = False
    for _name, backend in chain:
        outcome = await backend()
        if outcome.status == "ok" and outcome.results:
            return outcome
        if outcome.status == "empty":
            saw_empty = True
    return SearchOutcome(results=[], backend="chain",
                         status="empty" if saw_empty else "unavailable")
```

> Note: `brave_search` is referenced here but only fully exercised in Wave 2. Add a minimal stub now so the module imports; Wave 2 Task 7 replaces the body and adds its tests.
```python
# minimal stub (Wave 2 Task 7 replaces this)
async def brave_search(query: str, max_results: int, client: Any, api_key: str) -> SearchOutcome:
    return SearchOutcome(results=[], backend="brave", status="unavailable")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/plugins/tool/test_search_backends.py -v`
Expected: PASS (all backend + chain tests)

- [ ] **Step 5: Commit**

```bash
git add jarvis/plugins/tool/search_backends.py tests/unit/plugins/tool/test_search_backends.py
git commit -m "feat(search): add backend chain resolver with honest status"
```

---

### Task 4: Rewire `SearchWebTool.execute` to the resolver

**Files:**
- Modify: `jarvis/plugins/tool/search_web.py:239-269` (the DDG block) and the `execute` body
- Test: `tests/unit/plugins/tool/test_search_web.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/plugins/tool/test_search_web.py
import pytest

from jarvis.plugins.tool.search_backends import SearchOutcome
from jarvis.plugins.tool.search_web import SearchWebTool


class _Ctx:  # minimal ExecutionContext stand-in
    pass


@pytest.mark.asyncio
async def test_execute_returns_real_results(monkeypatch):
    async def fake_run_search(chain):
        return SearchOutcome(
            results=[{"title": "Billboard", "snippet": "chart", "url": "u"}],
            backend="ddg_serp", status="ok")
    monkeypatch.setattr("jarvis.plugins.tool.search_web.run_search", fake_run_search)
    result = await SearchWebTool().execute({"query": "top ten songs"}, _Ctx())
    assert result.success is True
    assert result.output["results"][0]["title"] == "Billboard"
    assert result.output["status"] == "ok"
    assert result.output["backend"] == "ddg_serp"


@pytest.mark.asyncio
async def test_execute_unavailable_surfaces_detail(monkeypatch):
    async def fake_run_search(chain):
        return SearchOutcome(results=[], backend="chain", status="unavailable")
    monkeypatch.setattr("jarvis.plugins.tool.search_web.run_search", fake_run_search)
    result = await SearchWebTool().execute({"query": "top ten songs"}, _Ctx())
    assert result.success is True
    assert result.output["status"] == "unavailable"
    assert "unavailable" in result.output["detail"].lower()


@pytest.mark.asyncio
async def test_execute_missing_query_still_fails():
    result = await SearchWebTool().execute({"query": "  "}, _Ctx())
    assert result.success is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/plugins/tool/test_search_web.py -v`
Expected: FAIL — `run_search` not importable from `search_web`, and `output` has no `status` key.

- [ ] **Step 3: Write minimal implementation**

Replace the DDG block (`search_web.py:239-269`) with a delegation to the resolver, and add imports + a settings loader at module level:

```python
# new imports near the top of search_web.py
from jarvis.plugins.tool.search_backends import (
    SearchSettings, build_chain, run_search,
)


def _load_search_settings() -> SearchSettings:
    """Read backend preference from config and the optional Brave key from the
    secret store. Defensive: any failure yields the key-free 'auto' default so
    a misconfigured install still searches via DDG SERP."""
    backend = "auto"
    brave_key = ""
    try:
        from jarvis.core.config import get_secret, load_config
        cfg = load_config()
        backend = getattr(getattr(cfg, "search", None), "backend", "auto") or "auto"
        brave_key = get_secret("BRAVE_SEARCH_API_KEY", env_fallback="BRAVE_SEARCH_API_KEY") or ""
    except Exception:  # noqa: BLE001 — never let config break the voice path
        pass
    return SearchSettings(backend=backend, brave_key=brave_key)
```

> The engineer must confirm the real config accessor in `jarvis/core/config.py`. If the loader is named differently (e.g. `get_config()` or a cached singleton), use that — the pattern is "read `cfg.search.backend`, default to `auto`". Keep the `try/except` so a missing `search` section degrades gracefully.

Then replace the DDG block at the end of `execute` (keep the weather fast-path above it exactly as-is):

```python
        settings = _load_search_settings()
        try:
            async with asyncio.timeout(_TIMEOUT_S):
                async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                    chain = build_chain(query, max_results, settings=settings, client=client)
                    outcome = await run_search(chain)
        except Exception as exc:  # noqa: BLE001 — incl. TimeoutError
            return ToolResult(
                success=True,
                output={
                    "query": query, "results": [], "backend": "none",
                    "status": "unavailable",
                    "detail": (
                        "Web search timed out. Tell the user the search backend "
                        "could not be reached right now and offer to try again — "
                        "do not claim there are no results."
                    ),
                },
            )

        output: dict[str, Any] = {
            "query": query,
            "results": outcome.results,
            "backend": outcome.backend,
            "status": outcome.status,
        }
        if outcome.status == "unavailable":
            output["detail"] = (
                "Web search is temporarily unavailable. Tell the user the search "
                "backend could not be reached right now and offer to try again — "
                "do not claim there are no results."
            )
        return ToolResult(success=True, output=output)
```

> The `{"query","results"}` keys are preserved, so `_render_recovered_tool_output` (manager.py:522-574) and the tool-use loop's JSON re-injection keep working unchanged. The new `backend`/`status`/`detail` keys are additive.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/plugins/tool/test_search_web.py tests/unit/plugins/tool/test_search_web_router_tier.py tests/unit/plugins/tool/test_search_web_weather.py -v`
Expected: PASS — new tool tests green AND the timeout-pin + weather guards still green.

- [ ] **Step 5: Commit**

```bash
git add jarvis/plugins/tool/search_web.py tests/unit/plugins/tool/test_search_web.py
git commit -m "fix(search): route search_web through real-backend chain + honest status"
```

---

### Task 5: Add `ddgs` dependency (lazy, VPS-safe)

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the dependency**

Add to `requirements.txt`:
```
ddgs>=9.0
```

- [ ] **Step 2: Verify the base import still works without GPU/Windows deps**

Run:
```bash
pip install -e . --no-deps
pip install ddgs
python -c "from jarvis.plugins.tool.search_backends import _default_ddgs_searcher; print('ok')"
```
Expected: prints `ok`. (Lazy import means even without `ddgs` installed the module imports; the searcher only fails at call time and the chain falls through to Instant Answer.)

- [ ] **Step 3: Verify graceful degradation when the lib is absent**

Run: `pytest tests/unit/plugins/tool/test_search_backends.py -k library_missing -v`
Expected: PASS (`test_ddg_serp_library_missing_is_unavailable`).

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "build(search): add ddgs for real DuckDuckGo SERP (lazy, key-free)"
```

---

### Task 6: Wave 1 verification (live smoke)

- [ ] **Step 1: Run the full search + brain unit suites**

Run: `pytest tests/unit/plugins/tool/ tests/unit/brain/test_routing.py tests/unit/brain/test_output_filter.py -q`
Expected: PASS (no regressions in router discipline / scrubber).

- [ ] **Step 2: Restart the live app and re-drive the bug**

The editable install means the new code only loads after a restart. Restart via the API (NOT `Stop-Process` — that returns Access Denied per prior sessions):
```
POST http://127.0.0.1:<ui-port>/api/settings/restart-app
```
Then drive the live app over WebSocket (`ws://127.0.0.1:<port>/ws`, `{"type":"message","text":"What are the current top ten songs?"}`) and confirm Jarvis now returns real chart results instead of "my web search returns no results."

- [ ] **Step 3: Commit any smoke-fix, then proceed to Wave 2 (optional).**

---

# Wave 2 — Optional keyed backend (Brave Search), auto-selected when a key is present

> Wave 2 makes results reliable (Brave has a real index and a 2,000-query/month free tier) without breaking the key-free default. Skip if you do not want to manage a key.

### Task 7: Brave Search backend

**Files:**
- Modify: `jarvis/plugins/tool/search_backends.py` (replace the Task 3 stub)
- Test: `tests/unit/plugins/tool/test_search_backends.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/plugins/tool/test_search_backends.py
from jarvis.plugins.tool.search_backends import brave_search


@pytest.mark.asyncio
async def test_brave_maps_web_results():
    def handler(request):
        assert request.headers["X-Subscription-Token"] == "k"
        return httpx.Response(200, json={"web": {"results": [
            {"title": "Hot 100", "description": "chart", "url": "https://b.com"},
        ]}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = await brave_search("top ten songs", 5, client, "k")
    assert outcome.status == "ok"
    assert outcome.results[0]["url"] == "https://b.com"


@pytest.mark.asyncio
async def test_brave_http_error_is_unavailable():
    def handler(request):
        return httpx.Response(429, json={})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        outcome = await brave_search("x", 5, client, "k")
    assert outcome.status == "unavailable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/plugins/tool/test_search_backends.py -k brave -v`
Expected: FAIL (`test_brave_maps_web_results` — stub returns `unavailable`).

- [ ] **Step 3: Replace the stub with the real implementation**

```python
# replace the Task 3 brave_search stub in search_backends.py
_BRAVE_URL: Final[str] = "https://api.search.brave.com/res/v1/web/search"


async def brave_search(query: str, max_results: int, client: Any, api_key: str) -> SearchOutcome:
    """Brave Search API — real index, JSON, key required (free tier 2k/mo)."""
    if not api_key:
        return SearchOutcome(results=[], backend="brave", status="unavailable")
    try:
        resp = await client.get(
            _BRAVE_URL,
            params={"q": query, "count": max_results},
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        )
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception:  # noqa: BLE001
        return SearchOutcome(results=[], backend="brave", status="unavailable")
    web = (data.get("web") or {}).get("results") or []
    results = [{
        "title": str(r.get("title", "")),
        "snippet": str(r.get("description", "")),
        "url": str(r.get("url", "")),
    } for r in web[:max_results]]
    return SearchOutcome(results=results, backend="brave",
                         status="ok" if results else "empty")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/plugins/tool/test_search_backends.py -k brave -v`
Expected: PASS (2 tests). Also rerun the chain tests — `build_chain_auto_with_key_prepends_brave` still green.

- [ ] **Step 5: Commit**

```bash
git add jarvis/plugins/tool/search_backends.py tests/unit/plugins/tool/test_search_backends.py
git commit -m "feat(search): add Brave Search backend (keyed, auto-selected)"
```

---

### Task 8: `[search]` config section

**Files:**
- Modify: `jarvis/core/config.py`
- Test: `tests/unit/test_config_search.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_config_search.py
from jarvis.core.config import JarvisConfig


def test_search_defaults_to_auto():
    cfg = JarvisConfig.model_validate({})
    assert cfg.search.backend == "auto"


def test_search_backend_override():
    cfg = JarvisConfig.model_validate({"search": {"backend": "ddg_serp"}})
    assert cfg.search.backend == "ddg_serp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config_search.py -v`
Expected: FAIL — `JarvisConfig` has no `search` attribute.

- [ ] **Step 3: Add the sub-model**

In `jarvis/core/config.py`, following the existing sibling-section pattern (look at how `[stt]` / `[brain]` are modelled), add:

```python
class SearchConfig(BaseModel):
    model_config = ConfigDict(extra="allow")  # AP-16: tolerate forward keys
    # auto = keyed API if a key is set, else real DDG SERP, then Instant Answer.
    backend: Literal["auto", "brave", "ddg_serp", "ddg_instant"] = "auto"
```

and on the root `JarvisConfig` model add the field:

```python
    search: SearchConfig = Field(default_factory=SearchConfig)
```

> Use the `BaseModel`/`ConfigDict`/`Field`/`Literal` imports already present in the file. Do not add a key field here — the Brave key is read via `get_secret` (Task 4 loader), never from TOML (AP-12).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_config_search.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/core/config.py tests/unit/test_config_search.py
git commit -m "feat(config): add [search].backend preference"
```

---

### Task 9: Document the Brave key + backend toggle

**Files:**
- Modify: the setup/onboarding docs (e.g. `docs/obsidian-setup.md` sibling or the install README section that lists optional keys) and the wizard secret list if applicable.

- [ ] **Step 1:** Document that `BRAVE_SEARCH_API_KEY` is an **optional** secret (Credential Manager service `personal-jarvis`, or ENV) that upgrades web search reliability, and that without it Jarvis uses the key-free DuckDuckGo SERP. Document `[search].backend` values.
- [ ] **Step 2:** Verify the doc passes the `language-policy` CI gate (English only).
- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs(search): document optional Brave key and [search].backend toggle"
```

---

# Wave 3 — Gemini native Google Search grounding (premium freshness, opt-in)

> Wave 3 gives best-in-class freshness on the provider you actually run (Gemini), matching gemini.google.com. It is config-gated and provider-specific, so it does **not** change behavior for other providers. Skip if Wave 1+2 quality is sufficient.

### Task 10: `[brain.gemini].native_search_grounding` flag

**Files:**
- Modify: `jarvis/core/config.py` (the Gemini provider sub-model)
- Test: `tests/unit/test_config_search.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_config_search.py
def test_gemini_grounding_defaults_off():
    cfg = JarvisConfig.model_validate({})
    assert cfg.brain.gemini.native_search_grounding is False
```

> Adjust the attribute path (`cfg.brain.gemini...`) to match the real Gemini provider config model in `config.py`.

- [ ] **Step 2: Run → FAIL.** `pytest tests/unit/test_config_search.py -k grounding -v`

- [ ] **Step 3: Add the flag** to the Gemini provider config sub-model:

```python
    # Opt-in: enable Gemini's native Google Search grounding (server-side,
    # provider-specific). When true, the provider adds the google_search
    # built-in tool to its request so freshness queries are answered from
    # Google's live index instead of (or alongside) the search_web tool.
    native_search_grounding: bool = False
```

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit**

```bash
git add jarvis/core/config.py tests/unit/test_config_search.py
git commit -m "feat(config): add [brain.gemini].native_search_grounding flag"
```

---

### Task 11: Add the `google_search` built-in tool to the Gemini request

**Files:**
- Modify: `jarvis/plugins/brain/gemini.py` (request construction, ~line 398-434)
- Test: `tests/unit/plugins/brain/test_gemini_grounding.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/plugins/brain/test_gemini_grounding.py
from jarvis.plugins.brain.gemini import _build_gemini_tools  # exact name TBD per file


def test_grounding_tool_added_when_enabled():
    tools = _build_gemini_tools(declared_tools=[], native_search_grounding=True)
    assert any(getattr(t, "google_search", None) is not None for t in tools)


def test_grounding_tool_absent_when_disabled():
    tools = _build_gemini_tools(declared_tools=[], native_search_grounding=False)
    assert all(getattr(t, "google_search", None) is None for t in tools)
```

> The engineer factors the tool-list construction in `gemini.py` into a small pure helper (`_build_gemini_tools`) so it is unit-testable without an API call. If a helper already exists, test that one.

- [ ] **Step 2: Run → FAIL** (helper not yet present / does not add google_search).

- [ ] **Step 3: Implement**

In `gemini.py`, where the request's `tools` are built from `functionDeclarations`, append the native search tool when the flag is on:

```python
from google.genai import types  # already imported in this provider

def _build_gemini_tools(declared_tools, native_search_grounding):
    tools = list(declared_tools)
    if native_search_grounding:
        tools.append(types.Tool(google_search=types.GoogleSearch()))
    return tools
```

Wire the flag from config into the call site (read `cfg.brain.gemini.native_search_grounding`).

> **Verification caveat to record in the commit:** not every Gemini model permits combining `functionDeclarations` with `google_search` in one request. If the active model rejects the combination, gate so grounding is only added when the turn would otherwise call `search_web` (i.e. a research/freshness turn), or send grounding-only on a retry. Confirm against the live `gemini-3.1-pro-preview` model and the google-genai docs (use the `gemini-api` skill / context7) before claiming it works.

- [ ] **Step 4: Run → PASS.** `pytest tests/unit/plugins/brain/test_gemini_grounding.py -v`

- [ ] **Step 5: Commit**

```bash
git add jarvis/plugins/brain/gemini.py tests/unit/plugins/brain/test_gemini_grounding.py
git commit -m "feat(brain): opt-in Gemini native Google Search grounding"
```

---

### Task 12: Reconcile router discipline with native grounding + live verify

**Files:**
- Modify: `jarvis/brain/router.py` (system-prompt note, ~line 88-117) — only if grounding is enabled.
- Verify: live app.

- [ ] **Step 1:** When `native_search_grounding` is on, the router prompt's "use search_web for news/knowledge" instruction should permit Gemini to answer a freshness question **directly** from grounding instead of being forced to call `search_web`. Add a conditional clause (English) to the prompt only when grounding is active. Keep the default prompt unchanged for non-grounding providers.
- [ ] **Step 2:** Run `pytest tests/unit/brain/test_routing.py -v` — router discipline (ROUTER_TOOLS frozenset) must stay green; do not add/remove tools.
- [ ] **Step 3:** Restart the app (`POST /api/settings/restart-app`), enable the flag, re-drive "What are the current top ten songs?" over WS, and confirm a grounded, current answer. Record the result in the commit message and update memory.

---

## Self-Review

**Spec coverage:**
- "Works for all providers" → Wave 1 (backend chain) is provider-agnostic; the tool is in `ROUTER_TOOLS` used by every provider. ✓
- "Key-free VPS default" → `ddg_serp` default, lazy `ddgs` import, graceful fallback to Instant Answer. ✓ (Task 2, 5)
- "Optional keyed upgrade" → Brave backend + `auto` chain ordering + `get_secret` key. ✓ (Task 7, 8)
- "Gemini native grounding" → Wave 3, config-gated, provider-specific. ✓ (Task 10-12)
- "Honest failure phrasing" → `status` + `detail`, distinguishes empty vs unavailable. ✓ (Task 1, 4)
- Voice-path safety → 5 s ceiling preserved, `ddgs` in `to_thread`, weather fast-path untouched. ✓ (Task 2, 4)

**Placeholder scan:** Config accessor name in Task 4/8/10 is flagged as "confirm the real symbol in config.py" rather than invented — this is a deliberate verification instruction, not a code placeholder; the surrounding code is complete. Gemini helper name `_build_gemini_tools` is defined in Task 11 and used consistently in its test.

**Type consistency:** `SearchOutcome(results, backend, status)`, `SearchSettings(backend, brave_key)`, `build_chain(query, max_results, *, settings, client, searcher=None)`, `run_search(chain)`, and backend signatures are identical across Tasks 1-9. The tool reads `outcome.results` / `outcome.status` / `outcome.backend` exactly as defined.

---

## Risks & notes

- **DDG SERP rate-limiting:** the free SERP can be throttled under load (the original `202` may already be DDG bot-detection). This is exactly why Wave 2 (Brave) exists — for reliability, configure a key. The chain degrades gracefully either way.
- **`ddgs` API stability:** the library occasionally changes its `.text()` signature across majors. Pin a major in `requirements.txt` and keep the `searcher` injection point so a future swap (SearXNG, Tavily) is a one-function change.
- **Restart required:** every change here loads only after `POST /api/settings/restart-app` (editable install). Unit tests pass without restart; live verification needs it.
