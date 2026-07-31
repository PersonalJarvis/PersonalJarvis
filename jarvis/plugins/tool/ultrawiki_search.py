"""``ultrawiki-search`` tool — hybrid semantic search over the UltraWiki store.

Router-tier pull tool (ADR-0011 amendment "UltraWiki Search"); mission workers
reach it only through the ADR-0025 broker grant once the ADR-0030 gate is live.

Why this tool exists
--------------------
UltraWiki is the semantic memory mode of the Wiki section (``UltraWiki/*.md``,
roadmap P4): fused keyword + vector retrieval with RRF, optional rerank, and
citation permalinks. The engine and REST surface exist; this tool is the
brain-facing primitive so voice/chat can PULL from the store when the user
asks a knowledge question while UltraWiki mode is active.

Pull-only by design: this tool never injects unsolicited context. The
relevance floor (``rerank_min_score``) governs unsolicited surfaces; an
explicit search on the user's behalf shows what matched, best first
(``enforce_floor`` stays off — the Ask-view semantics).

Resolution
----------
``service_resolver`` returns the live :class:`~jarvis.ultrawiki.service.
UltraWikiService` or ``None``. Resolver-not-instance because the service is
constructed by the WebServer AFTER the brain build (the wiki-ingest lazy
curator precedent). The tool loads even when UltraWiki mode is disabled and
answers with an honest steer-to-wiki error — a stable tool surface across
mode toggles (the awareness-recall precedent).

Honesty ladder (anti-confabulation, 2026-06-18 lesson)
------------------------------------------------------
* no service        → ``success=False`` "not running" (bootstrap in flight)
* mode disabled     → ``success=False`` steer to the classic ``wiki-*`` tools
* search raised     → ``success=False`` with the exception text
* zero hits         → ``success=True`` with a sentence that AFFIRMS the store
  was searched — an honest "empty" must never escalate into a false
  "unavailable" claim.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from jarvis.core.protocols import ToolResult

log = logging.getLogger(__name__)

_MAX_SNIPPET_CHARS = 220
_MAX_OUTPUT_CHARS = 4000


def build_ultrawiki_service_resolver() -> Callable[[], Any]:
    """Default resolver: the live service the WebServer parked on app state."""

    def _resolve() -> Any:
        from jarvis.core import runtime_refs  # noqa: PLC0415 — lazy, boot-safe

        app = runtime_refs.get_web_app()
        return getattr(getattr(app, "state", None), "ultrawiki", None)

    return _resolve


def _render_hit(hit: Any) -> str:
    """One compact markdown line per fused hit, citation included."""
    title = str(getattr(hit, "title", "") or "(untitled)").strip()
    snippet = " ".join(str(getattr(hit, "snippet", "") or "").split())
    if len(snippet) > _MAX_SNIPPET_CHARS:
        snippet = snippet[:_MAX_SNIPPET_CHARS].rstrip() + "…"
    source = str(getattr(hit, "source_id", "") or "?")
    stamp = str(getattr(hit, "timestamp_utc", "") or "")
    permalink = str(getattr(hit, "permalink", "") or "")
    matched = getattr(hit, "matched_by", ()) or ()
    matched_note = f" [{', '.join(str(m) for m in matched)}]" if matched else ""
    cite = f" ({permalink})" if permalink else ""
    when = f", {stamp}" if stamp else ""
    return f"- [{source}{when}] **{title}** — {snippet}{cite}{matched_note}"


class UltraWikiSearchTool:
    """Router-tier hybrid search over the UltraWiki semantic store."""

    name: str = "ultrawiki-search"
    description: str = (
        "Search the UltraWiki semantic knowledge base (fused keyword + vector "
        "retrieval with citations). Call this for knowledge questions when "
        "UltraWiki mode is active — e.g. facts about people, projects, past "
        "decisions, or anything the user stored. Returns up to k ranked hits, "
        "each with a citation permalink. Read-only. When UltraWiki mode is "
        "disabled, use the classic wiki tools (wiki-recall, wiki-page-read) "
        "instead."
    )
    risk_tier: str = "safe"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query — a short phrase or keywords.",
            },
            "k": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 10,
                "description": "Maximum number of ranked hits to return.",
            },
            "area": {
                "type": "string",
                "description": "Optional area id to restrict the search to.",
            },
        },
        "required": ["query"],
    }

    def __init__(self, service_resolver: Callable[[], Any] | None = None) -> None:
        self._resolve_service = service_resolver or build_ultrawiki_service_resolver()

    @staticmethod
    def _mode_enabled() -> bool:
        try:
            from jarvis.core.config import load_config  # noqa: PLC0415 — lazy

            return bool(load_config().ultrawiki.enabled)
        except Exception:  # noqa: BLE001 — config drift must not crash the turn
            return False

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult(
                success=True,
                output="(empty query — nothing searched; provide a phrase or keywords)",
            )
        k = max(1, min(int(args.get("k", 5) or 5), 10))
        area = str(args.get("area", "") or "").strip() or None

        if not self._mode_enabled():
            return ToolResult(
                success=False,
                output="",
                error=(
                    "UltraWiki mode is disabled — use the classic wiki tools "
                    "(wiki-recall, wiki-page-read, wiki-list) instead."
                ),
            )
        service = self._resolve_service()
        if service is None:
            return ToolResult(
                success=False,
                output="",
                error="UltraWiki service is not running (bootstrap may still be in flight).",
            )

        try:
            # rerank=False by design (doc 03 "primitive tools"): this tool's
            # consumer IS a model and judges relevance itself — paying a
            # second model call to pre-grade for it is pure added latency.
            hits = await service.search(query, k=k, area_id=area, rerank=False)
        except Exception as exc:  # noqa: BLE001 — surface honestly, never crash the turn
            log.warning("ultrawiki-search failed: %s", exc)
            return ToolResult(
                success=False,
                output="",
                error=f"UltraWiki search failed: {exc}",
            )

        hits = list(hits or ())
        if not hits:
            # Affirm reachability so an honest "empty" can never be narrated
            # as the store being down.
            return ToolResult(
                success=True,
                output=(
                    f"UltraWiki was searched successfully for '{query}' — "
                    "no stored item matched."
                ),
            )

        lines = [f"UltraWiki hits for '{query}' (best first):"]
        lines.extend(_render_hit(hit) for hit in hits[:k])
        output = "\n".join(lines)
        if len(output) > _MAX_OUTPUT_CHARS:
            output = output[:_MAX_OUTPUT_CHARS].rstrip() + "…"
        # Hit count only — what the user asks their own memory stays out of logs.
        log.info("ultrawiki-search: %d hit(s)", len(hits))
        return ToolResult(success=True, output=output)


__all__ = ["UltraWikiSearchTool", "build_ultrawiki_service_resolver"]
