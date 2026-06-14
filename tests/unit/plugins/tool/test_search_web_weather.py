"""Weather path for search_web (live forensic 2026-06-10 23:12).

"What's weather like tomorrow?" produced three DuckDuckGo Instant-Answer calls
that ALL returned 202/empty — the DDG instant-answer API has no weather data,
so the brain had nothing to say and the turn died in the leak-recovery
fallback. Weather-intent queries now resolve via Open-Meteo (geocoding +
forecast, key-free) and return a normal result snippet the brain can phrase
in the user's language. Any failure falls back to the DDG path unchanged.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest

from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool.search_web import SearchWebTool, _extract_weather_location


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="test",
        config={},
        memory_read=None,
        approved_by="auto",
    )


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


_GEO_PAYLOAD = {
    "results": [
        {
            "name": "Berlin",
            "country": "Germany",
            "latitude": 52.52,
            "longitude": 13.41,
        }
    ]
}

_FORECAST_PAYLOAD = {
    "current": {"temperature_2m": 21.3, "weather_code": 2},
    "daily": {
        "time": ["2026-06-10", "2026-06-11", "2026-06-12"],
        "weather_code": [2, 61, 3],
        "temperature_2m_max": [24.1, 19.5, 22.0],
        "temperature_2m_min": [14.2, 12.8, 13.1],
        "precipitation_probability_max": [10, 80, 30],
    },
}

_DDG_PAYLOAD = {
    "AbstractText": "DuckDuckGo abstract",
    "Heading": "Some Topic",
    "AbstractURL": "https://example.org",
    "RelatedTopics": [],
}


class _FakeClient:
    """Routes Open-Meteo / DDG URLs to canned payloads and records calls."""

    calls: list[str] = []

    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> _FakeResponse:
        _FakeClient.calls.append(url)
        if "geocoding-api.open-meteo.com" in url:
            return _FakeResponse(_GEO_PAYLOAD)
        if "api.open-meteo.com" in url:
            return _FakeResponse(_FORECAST_PAYLOAD)
        return _FakeResponse(_DDG_PAYLOAD)


@pytest.fixture(autouse=True)
def _fake_httpx(monkeypatch: pytest.MonkeyPatch):
    _FakeClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    yield


# ---------------------------------------------------------------------------
# Location extraction (pure)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("weather Berlin tomorrow 11 June 2026", "Berlin"),
        ("Wetter Berlin morgen", "Berlin"),  # i18n-allow: German voice fixture
        ("what's the weather like in London next week", "London"),
        ("weather tomorrow", ""),
        ("¿Qué tiempo hace mañana en Madrid?", "Madrid"),
    ],
)
def test_extract_weather_location(query: str, expected: str) -> None:
    assert _extract_weather_location(query) == expected


# ---------------------------------------------------------------------------
# Weather queries route to Open-Meteo and return a forecast snippet
# ---------------------------------------------------------------------------

async def test_weather_query_returns_forecast() -> None:
    tool = SearchWebTool()
    result = await tool.execute({"query": "weather Berlin tomorrow"}, _ctx())
    assert result.success is True
    results = result.output["results"]
    assert len(results) == 1
    snippet = results[0]["snippet"]
    assert "Berlin" in results[0]["title"]
    # Tomorrow's row must carry temps + precipitation chance from the payload.
    assert "19.5" in snippet and "12.8" in snippet and "80" in snippet
    # Open-Meteo was used, never DDG.
    assert any("open-meteo" in u for u in _FakeClient.calls)
    assert not any("duckduckgo" in u for u in _FakeClient.calls)


async def test_weather_query_without_location_falls_back_to_ddg() -> None:
    tool = SearchWebTool()
    result = await tool.execute({"query": "weather tomorrow"}, _ctx())
    assert result.success is True
    assert any("duckduckgo" in u for u in _FakeClient.calls)


async def test_non_weather_query_uses_ddg_only() -> None:
    tool = SearchWebTool()
    result = await tool.execute({"query": "Supabase pricing"}, _ctx())
    assert result.success is True
    assert result.output["results"][0]["snippet"] == "DuckDuckGo abstract"
    assert not any("open-meteo" in u for u in _FakeClient.calls)


class _RaisingGeocodeClient(_FakeClient):
    """Open-Meteo geocode wedges/errors; DDG still answers."""

    async def get(self, url: str, params: dict[str, Any] | None = None, **kw: Any) -> _FakeResponse:
        _FakeClient.calls.append(url)
        if "open-meteo.com" in url:
            raise httpx.ConnectError("boom")
        return _FakeResponse(_DDG_PAYLOAD)


async def test_weather_lookup_failure_falls_back_to_ddg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wedged/erroring Open-Meteo call must never sink the turn — the tool
    falls through to DDG instead of raising (AD-OE6 zero-silent-drop)."""
    monkeypatch.setattr(httpx, "AsyncClient", _RaisingGeocodeClient)
    tool = SearchWebTool()
    result = await tool.execute({"query": "weather Berlin tomorrow"}, _ctx())
    assert result.success is True
    assert any("duckduckgo" in u for u in _FakeClient.calls)


def test_weather_total_budget_within_single_call_ceiling() -> None:
    """The two weather sub-calls together must not exceed the single-call voice
    budget (no 2x latency regression on the router-tier voice path)."""
    from jarvis.plugins.tool.search_web import _TIMEOUT_S, _WEATHER_CALL_TIMEOUT_S

    assert _WEATHER_CALL_TIMEOUT_S * 2 <= _TIMEOUT_S
