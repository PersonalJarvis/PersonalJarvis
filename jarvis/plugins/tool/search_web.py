"""search_web-Tool: DuckDuckGo Instant-Answer-API (kein Key nötig).

Risk-Tier: safe — reines Readonly.
"""
from __future__ import annotations

from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


class SearchWebTool:
    name: str = "search_web"
    risk_tier: str = "safe"
    description: str = (
        "[RESEARCH-PRIMARY] Web-Suche via DuckDuckGo mit Kurz-Zusammenfassung. "
        "NUTZE DIESES TOOL wenn der User über ein Thema recherchieren, analysieren, "
        "erklären, vergleichen oder zusammenfassen möchte — egal ob Firma, Produkt, "
        "Technologie, Konzept oder News. Beispiele: 'recherchiere zu X', 'was ist X', "
        "'vergleiche X mit Y', 'erklär mir X'. "
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

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
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
