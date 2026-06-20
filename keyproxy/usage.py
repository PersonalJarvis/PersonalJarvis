"""Best-effort usage recording + a per-token/period report query.

Recording is fire-and-forget from the passthrough's perspective: a parse miss
records the call with null counts and an unknown model records a null cost. The
report aggregates calls + token totals + estimated cost per token over an
optional period.

The price table is intentionally small and static (USD per 1M tokens, input /
output). Unknown model -> cost ``None``. It is a best-effort estimate, not
billing-grade.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from .store import Store
from .vendors import ParsedUsage

# model (lower-cased, substring match) -> (input_per_1m, output_per_1m) USD.
# Substring match keeps it resilient to dated suffixes (e.g.
# "gpt-4o-mini-2024-07-18" matches "gpt-4o-mini").
_PRICE_TABLE: list[tuple[str, float, float]] = [
    # OpenAI
    ("gpt-4o-mini", 0.15, 0.60),
    ("gpt-4o", 2.50, 10.00),
    ("gpt-4.1-mini", 0.40, 1.60),
    ("gpt-4.1", 2.00, 8.00),
    ("o3-mini", 1.10, 4.40),
    # Anthropic
    ("claude-3-5-haiku", 0.80, 4.00),
    ("claude-3-5-sonnet", 3.00, 15.00),
    ("claude-3-7-sonnet", 3.00, 15.00),
    ("claude-3-opus", 15.00, 75.00),
    # Gemini
    ("gemini-2.0-flash", 0.10, 0.40),
    ("gemini-1.5-flash", 0.075, 0.30),
    ("gemini-1.5-pro", 1.25, 5.00),
    # xAI Grok
    ("grok-2", 2.00, 10.00),
    ("grok-beta", 5.00, 15.00),
]


def estimate_cost(
    model: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> float | None:
    """USD estimate, or ``None`` for an unknown model / missing counts."""
    if not model or prompt_tokens is None or completion_tokens is None:
        return None
    name = model.lower()
    for needle, in_price, out_price in _PRICE_TABLE:
        if needle in name:
            return round(
                (prompt_tokens / 1_000_000) * in_price
                + (completion_tokens / 1_000_000) * out_price,
                6,
            )
    return None


class UsageStore:
    def __init__(self, store: Store) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Recording (best-effort, never raises out of the request path)
    # ------------------------------------------------------------------

    def record(
        self,
        *,
        token_id: str | None,
        provider_id: str,
        parsed: ParsedUsage | None,
        ts: int | None = None,
    ) -> None:
        model = parsed.model if parsed else None
        pt = parsed.prompt_tokens if parsed else None
        ct = parsed.completion_tokens if parsed else None
        tt = parsed.total_tokens if parsed else None
        est = estimate_cost(model, pt, ct)
        try:
            self._store.execute(
                "INSERT INTO usage (id, token_id, provider_id, model, "
                "prompt_tokens, completion_tokens, total_tokens, est_cost, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid4()),
                    token_id,
                    provider_id,
                    model,
                    pt,
                    ct,
                    tt,
                    est,
                    ts if ts is not None else int(time.time()),
                ),
            )
        except Exception:  # noqa: BLE001 — metering must never fail the request
            pass

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._store.query_all(
            "SELECT * FROM usage ORDER BY ts DESC, id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def report(
        self,
        *,
        token_id: str | None = None,
        since: int | None = None,
        until: int | None = None,
    ) -> list[dict[str, Any]]:
        """Per-token aggregate (calls, token totals, est cost) over a period."""
        where: list[str] = []
        params: list[Any] = []
        if token_id is not None:
            where.append("token_id = ?")
            params.append(token_id)
        if since is not None:
            where.append("ts >= ?")
            params.append(since)
        if until is not None:
            where.append("ts <= ?")
            params.append(until)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        # ``clause`` is built only from static literal fragments; all values are
        # bound parameters.
        sql = (
            "SELECT token_id, COUNT(*) AS calls, "
            "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
            "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
            "COALESCE(SUM(est_cost), 0.0) AS est_cost "
            f"FROM usage{clause} "
            "GROUP BY token_id ORDER BY total_tokens DESC"  # noqa: S608
        )
        rows = self._store.query_all(sql, tuple(params))
        return [dict(r) for r in rows]
