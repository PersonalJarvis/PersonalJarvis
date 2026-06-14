"""search_web-Tool: DuckDuckGo Instant-Answer-API (kein Key nötig).

Risk-Tier: safe — reines Readonly.

Weather path (live forensic 2026-06-10 23:12, data/jarvis_desktop.log):
"What's weather like tomorrow?" fired three DDG instant-answer calls that ALL
came back 202/empty — DDG has no weather data, the brain had nothing to say
and the turn died in the leak-recovery fallback. Weather-intent queries now
resolve via Open-Meteo (geocoding + forecast, key-free, no account) and return
a normal result snippet the brain phrases in the user's language. Any failure
(no location in the query, network error, unexpected payload) falls back to
the DDG path unchanged.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Final

from jarvis.core.protocols import ExecutionContext, ToolResult

# Hard ceiling for the DuckDuckGo round-trip. This tool is router-tier since
# 2026-06-10 (ADR-0011 amendment "Inline web search"), so the call sits on the
# voice turn — the p95 intent->ACK SLO is 3.0 s and a wedged search must fail
# fast (the brain then answers from context) instead of holding the turn for
# the old 15 s. Pinned by tests/unit/plugins/tool/test_search_web_router_tier.py.
_TIMEOUT_S: Final[float] = 5.0

# The weather path makes TWO sequential calls (geocode -> forecast). The
# per-call socket timeout is only a fairness split so one slow call cannot eat
# the whole budget; it does NOT bound the total (an httpx float timeout applies
# per-phase, so two calls could otherwise run ~2x). The HARD total ceiling is
# the ``asyncio.timeout(_TIMEOUT_S)`` wrapping the whole lookup in ``execute`` —
# do not remove it thinking the per-call timeout is enough. On any wedge/timeout
# the turn falls through to the DDG path. Pinned by test_search_web_weather.py.
_WEATHER_CALL_TIMEOUT_S: Final[float] = _TIMEOUT_S / 2

_GEOCODE_URL: Final[str] = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL: Final[str] = "https://api.open-meteo.com/v1/forecast"

_WEATHER_INTENT_RE = re.compile(
    r"\b(weather|forecast|wetter\w*|wettervorhersage|vorhersage|"
    r"temperatur\w*|temperature|tiempo|clima)\b",
    re.IGNORECASE,
)

# Tokens stripped from a weather query before treating the remainder as a
# location for geocoding ("weather Berlin tomorrow 11 June 2026" -> "Berlin").
_WEATHER_NOISE: Final[frozenset[str]] = frozenset({
    # intent / question scaffolding (en)
    "weather", "forecast", "temperature", "like", "what", "what's", "whats",
    "is", "the", "it", "it's", "will", "be", "and", "how", "hot", "cold",
    "rain", "raining", "rainy", "snow", "snowing", "sunny", "please", "tell",
    "me", "give", "a", "an", "honest", "review", "in", "at", "for", "on",
    "today", "tomorrow", "tonight", "this", "next", "week", "weekend",
    "day", "days", "morning", "evening", "going", "to", "out", "outside",
    # intent / question scaffolding (de)
    "wetter", "wettervorhersage", "vorhersage", "temperatur", "temperaturen",  # i18n-allow
    "wie", "ist", "das", "wird", "es", "heute", "morgen", "uebermorgen",  # i18n-allow
    "übermorgen", "jetzt", "diese", "woche", "am", "wochenende", "und",  # i18n-allow
    "bitte", "sag", "mir", "gib", "regnet", "regen", "schnee", "schneit",  # i18n-allow
    "kalt", "warm", "heiss", "heiß", "sonnig", "draussen", "draußen",  # i18n-allow
    "fuer", "für",  # i18n-allow
    # intent / question scaffolding (es)
    "tiempo", "clima", "qué", "que", "hace", "hacer", "hay", "hoy",
    "mañana", "manana", "en", "el", "la", "de", "por", "favor", "dime",
    "va", "llover", "lluvia", "nieve", "frío", "frio", "calor", "cómo",
    "como", "será", "sera",
    # month names (en/de/es) — date fragments are never a location
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "januar", "februar", "märz", "maerz", "mai", "juni", "juli",  # i18n-allow
    "oktober", "dezember",
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
})

_LOCATION_TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)

# WMO weather interpretation codes -> short English condition text (the brain
# phrases the spoken answer in the user's language).
_WMO_CONDITIONS: Final[dict[int, str]] = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    56: "freezing drizzle", 57: "dense freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light rain showers", 81: "rain showers", 82: "violent rain showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


def _extract_weather_location(query: str) -> str:
    """Strip weather/date scaffolding from *query*; the remainder is the place.

    Returns ``""`` when nothing location-shaped survives — the caller then
    falls back to the DDG path instead of geocoding garbage.
    """
    tokens = _LOCATION_TOKEN_RE.findall(query or "")
    kept = [
        tok for tok in tokens
        if not re.search(r"\d", tok) and tok.lower() not in _WEATHER_NOISE
    ]
    return " ".join(kept).strip()


def _condition_text(code: object) -> str:
    try:
        return _WMO_CONDITIONS.get(int(code), f"weather code {code}")
    except (TypeError, ValueError):
        return "unknown conditions"


async def _weather_results(query: str, client: Any) -> list[dict[str, Any]] | None:
    """Open-Meteo lookup: geocode the location in *query*, fetch a 3-day
    forecast, return it as one search-result snippet. ``None`` = not
    resolvable (caller falls back to DDG)."""
    location = _extract_weather_location(query)
    if not location:
        return None

    geo_resp = await client.get(
        _GEOCODE_URL,
        params={"name": location, "count": 1, "language": "en", "format": "json"},
    )
    geo_results = (geo_resp.json() or {}).get("results") or []
    if not geo_results:
        return None
    place = geo_results[0]

    fc_resp = await client.get(
        _FORECAST_URL,
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,weather_code",
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max"
            ),
            "forecast_days": 3,
            "timezone": "auto",
        },
    )
    data = fc_resp.json() or {}
    daily = data.get("daily") or {}
    days = daily.get("time") or []
    if not days:
        return None

    lines: list[str] = []
    current = data.get("current") or {}
    if current.get("temperature_2m") is not None:
        lines.append(
            f"now: {current['temperature_2m']}°C, "
            f"{_condition_text(current.get('weather_code'))}"
        )
    labels = ["today", "tomorrow", "day after tomorrow"]
    for i, day in enumerate(days[:3]):
        label = labels[i] if i < len(labels) else day
        try:
            lines.append(
                f"{label} ({day}): {_condition_text(daily['weather_code'][i])}, "
                f"{daily['temperature_2m_min'][i]}–{daily['temperature_2m_max'][i]}°C, "
                f"precipitation chance {daily['precipitation_probability_max'][i]}%"
            )
        except (KeyError, IndexError, TypeError):
            continue
    if not lines:
        return None

    name = str(place.get("name") or location)
    country = str(place.get("country") or "").strip()
    title = f"Weather forecast {name}" + (f", {country}" if country else "")
    return [{
        "title": title,
        "snippet": "; ".join(lines),
        "url": "https://open-meteo.com/",
    }]


class SearchWebTool:
    name: str = "search_web"
    risk_tier: str = "safe"
    description: str = (
        "[RESEARCH-PRIMARY] Web-Suche via DuckDuckGo mit Kurz-Zusammenfassung. "
        "NUTZE DIESES TOOL wenn der User über ein Thema recherchieren, analysieren, "
        "erklären, vergleichen oder zusammenfassen möchte — egal ob Firma, Produkt, "
        "Technologie, Konzept oder News. Beispiele: 'recherchiere zu X', 'was ist X', "
        "'vergleiche X mit Y', 'erklär mir X'. "
        "ALSO for weather questions ('weather tomorrow') — include the location "
        "in the query (e.g. 'weather Berlin tomorrow'). "
        "NICHT NUTZEN für Aktionen auf verbundenen Systemen (dafür cli_* oder MCP-Tools) — "
        "'meine X' oder 'starte X' sind NIE Research-Intent."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Suchanfrage"},
            "max_results": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        query = (args.get("query") or "").strip()
        max_results = int(args.get("max_results", 5))
        if not query:
            return ToolResult(success=False, output=None, error="query fehlt")

        try:
            import httpx
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=f"httpx nicht verfügbar: {exc}")

        # Weather fast-path: DDG instant answers have no weather data (every
        # call returns empty), so weather intents go to Open-Meteo. The whole
        # two-call lookup is bounded by a single ``_TIMEOUT_S`` deadline so it
        # never exceeds the single-call voice budget; any failure / timeout
        # falls through to DDG so e.g. "Open-Meteo weather API docs" research
        # queries still work.
        if _WEATHER_INTENT_RE.search(query):
            try:
                async with asyncio.timeout(_TIMEOUT_S):
                    async with httpx.AsyncClient(timeout=_WEATHER_CALL_TIMEOUT_S) as client:
                        weather = await _weather_results(query, client)
            except Exception:  # noqa: BLE001 — weather is best-effort (incl. TimeoutError)
                weather = None
            if weather:
                return ToolResult(
                    success=True,
                    output={"query": query, "results": weather},
                )

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.get(
                    "https://api.duckduckgo.com/",
                    params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                    follow_redirects=True,
                )
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=f"Request fehlgeschlagen: {exc}")

        results = []
        abstract = data.get("AbstractText") or data.get("Abstract") or ""
        if abstract:
            results.append({"title": data.get("Heading", ""), "snippet": abstract,
                           "url": data.get("AbstractURL", "")})

        for topic in (data.get("RelatedTopics") or [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": topic.get("Text", "")[:80],
                    "snippet": topic.get("Text", ""),
                    "url": topic.get("FirstURL", ""),
                })
            if len(results) >= max_results:
                break

        return ToolResult(
            success=True,
            output={"query": query, "results": results[:max_results]},
        )
