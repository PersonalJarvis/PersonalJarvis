"""``awareness-recall`` tool — BM25 full-text search across recent episodes.

Plan §7 (Awareness Phase A3, L3 Session Search). Originally specified as a
``SUB_TOOL`` for "Sub-Jarvis"; after Welle 4 deleted the sub tier entirely
this tool lives in ``ROUTER_TOOLS``. The router brain calls it directly
when the user asks about earlier work ("vorhin", "heute morgen", "an
welcher Datei war ich"), and may bake the result into an
``spawn_worker`` ``context_hints`` field if it then decides to delegate
heavy work to an OpenClaw worker.

The tool is read-only (``risk_tier="safe"``): a single FTS5 ``MATCH``
query against the ``awareness_episodes_fts`` virtual table, no brain
call, no network, no mutation. Output is a compact markdown block that
the router brain can consume directly inside its system prompt — same
contract as ``awareness-snapshot`` so the brain does not need to learn a
new shape.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# We import RecallStore only for type hints to keep the plugin importable
# in environments where the recall store happens to be unavailable
# (configured off, schema migration in progress, etc.). The actual
# instance is injected via the constructor.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.memory.recall import RecallStore


@dataclass
class ToolResult:
    """Mirror of ``awareness_snapshot.ToolResult`` for a uniform shape.

    If the canonical Tool-Result protocol in ``jarvis.core.protocols`` is
    extended later, this dataclass must stay structurally compatible.
    """

    success: bool
    output: str
    error: str | None = None


_MINUTES_IN_NS: int = 60 * 1_000_000_000


def _format_episode(row: dict[str, Any]) -> str:
    """Render a single episode row as a one-line markdown snippet.

    Format: ``- [HH:MM, primary_app] summary-first-120-chars…``

    ``started_at_ns`` is interpreted as a nanosecond Unix timestamp; the
    local-time HH:MM is shown so a router brain can humanise it without
    re-parsing the value. The summary is truncated to 120 characters and
    suffixed with an ellipsis when it had to be cut — the goal is to fit
    several snippets into a system-prompt without blowing the budget.
    """
    started_ns = int(row.get("started_at_ns", 0))
    started_s = started_ns / 1_000_000_000 if started_ns > 0 else 0
    hhmm = time.strftime("%H:%M", time.localtime(started_s)) if started_s else "??:??"
    app = row.get("primary_app", "unknown") or "unknown"
    summary = (row.get("summary") or "").strip()
    if len(summary) > 120:
        summary = summary[:120].rstrip() + "…"
    return f"- [{hhmm}, {app}] {summary}"


class AwarenessRecallTool:
    """Router-tier full-text search over the last N hours of episodes."""

    name: str = "awareness-recall"
    description: str = (
        "Searches the awareness episode log for the last N hours by full-text. "
        "Call this when the user asks about earlier work — phrases like "
        "'vorhin', 'heute morgen', 'der Befehl von eben', 'an welcher Datei "
        "war ich'. Returns up to 5 ranked snippets with timestamp and the "
        "primary app of each episode. Read-only, no brain call."
    )
    risk_tier: str = "safe"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "One to four FTS5 keywords. Avoid full sentences.",
            },
            "k": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 10,
                "description": "Maximum number of ranked snippets to return.",
            },
            "since_minutes": {
                "type": "integer",
                "default": 1440,
                "minimum": 1,
                "maximum": 10080,
                "description": (
                    "Search horizon in minutes. Default 1440 (24h), maximum "
                    "10080 (7 days). Older episodes are filtered out."
                ),
            },
        },
        "required": ["query"],
    }

    def __init__(self, recall_store: "RecallStore | None") -> None:
        self._recall = recall_store

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        """Execute the search. Args are validated at call time, not in schema.

        Defensive behaviour:
        - ``recall_store`` is None → ``success=False`` with a clear error,
          no exception. This happens when awareness is configured off or
          the bootstrap is still in flight; the router brain should treat
          it as "tool unavailable" rather than a hard failure.
        - empty query → ``success=True`` with a placeholder line so the
          brain still sees structured output, not a crash.
        - keyword miss but episodes exist in the window → ``success=True``
          with the most recent activity timeline (recency fallback), so a
          recency question ("what did I have open today") is answered from
          real data instead of a bare "nothing found" that a brain can
          mis-narrate as the store being down (2026-06-18 confabulation).
        - genuinely empty window → ``success=True`` with a sentence that
          explicitly affirms the store is reachable, so the honest "empty"
          cannot be escalated into a false "unavailable"/"error" claim.
        """
        if self._recall is None:
            return ToolResult(
                success=False,
                output="",
                error="awareness recall store unavailable",
            )

        query = str(args.get("query", "")).strip()
        k = int(args.get("k", 5))
        since_minutes = int(args.get("since_minutes", 1440))

        # Clamp to schema bounds defensively — JSON-schema validation may
        # not have run upstream when called from a test harness.
        k = max(1, min(k, 10))
        since_minutes = max(1, min(since_minutes, 10080))

        since_ns = time.time_ns() - since_minutes * _MINUTES_IN_NS

        hours = since_minutes / 60

        # Query the store. A genuine DB failure must surface HONESTLY (so the
        # brain can say "search is down") and be logged — it must never be
        # invisible. Previously this path had no logging at all, so when the
        # live deep/fast brain claimed "der lokale Verlaufsspeicher ist nicht
        # verfügbar" there was no way to tell whether the store had actually
        # failed or the brain had confabulated an outage over a healthy result
        # (2026-06-18). The log line below records the real branch + output so
        # that question is answerable from the log alone.
        try:
            rows = await self._recall.search_episodes(
                query=query,
                limit=k,
                since_ns=since_ns,
            )
            recent = rows or await self._recall.recent_episodes(
                limit=k, since_ns=since_ns,
            )
        except Exception as exc:  # noqa: BLE001 — surface, never confabulate
            log.warning(
                "awareness-recall query failed for %r (since=%dmin): %s",
                query, since_minutes, exc, exc_info=True,
            )
            return ToolResult(
                success=False,
                output="",
                error=f"awareness recall query failed: {exc}",
            )

        if rows:
            lines = [_format_episode(r) for r in rows]
            out = (
                f"Found {len(rows)} episode(s) matching '{query}' in the last "
                f"{hours:.1f}h:\n" + "\n".join(lines)
            )
            log.info("awareness-recall %r: keyword hit, %d row(s)", query, len(rows))
            return ToolResult(success=True, output=out)

        # No keyword match. A question like "what did I have open today?" is a
        # *recency* query, not a keyword query, and episode summaries are often
        # sparse (empty when the Verdichter could not characterise a window).
        # Hand the brain the user's actual recent activity timeline — it
        # directly answers a recency question. Lead with the DATA, positively:
        # an earlier version led with "No episode summary matched '<query>' …"
        # and the live deep/fast brain latched onto that negative opener and
        # reported the (healthy, fully-populated) store as "nicht verfügbar"
        # (2026-06-18). The keyword caveat is now a trailing parenthetical so a
        # skimming model reads "recent activity from the store" first, never a
        # leading negation.
        if recent:
            lines = [_format_episode(r) for r in recent]
            out = (
                f"Recent activity from the awareness store (last {hours:.1f}h, "
                f"newest first) — this IS your window/app history:\n"
                + "\n".join(lines)
                + f"\n(No episode summary text contained '{query}'; the list "
                f"above is the recency timeline.)"
            )
            log.info(
                "awareness-recall %r: recency fallback, %d row(s)",
                query, len(recent),
            )
            return ToolResult(success=True, output=out)

        # Genuinely nothing logged in the window. State explicitly that the
        # store *is* reachable and was searched, so this honest "empty" cannot
        # be escalated into a false "unavailable"/"error" claim downstream.
        log.info("awareness-recall %r: empty window (store reachable)", query)
        return ToolResult(
            success=True,
            output=(
                f"The awareness store was searched successfully and is "
                f"reachable, but it has no activity recorded in the last "
                f"{hours:.1f}h."
            ),
        )
