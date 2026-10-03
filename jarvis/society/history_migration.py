"""One-time projection of the retired agent board into the Society Ledger.

The old DepartureBoard kept its durable half in missions.db. Society's Ledger
reads only society_events, so pre-Society missions would otherwise vanish from
the new surface. Import at most the same 50 rows the old board showed, once, as
non-live DIGEST records.

The imported event carries historical cost only in its payload. cost_usd stays
zero so migration can never consume a present-day Society budget.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Final

from jarvis.missions.stream_evidence import clean_request_body

from .events import MsgType, SocietyEnvelope
from .roster import LEAD_AGENT_ID
from .store import SocietyStore

log = logging.getLogger(__name__)

__all__ = ["migrate_legacy_missions"]

_MIGRATION_KEY: Final[str] = "migration:legacy_mission_history:v1"
_HISTORY_LIMIT: Final[int] = 50
_PROMPT_CHARS: Final[int] = 1_000


def _prompt_preview(value: Any) -> str:
    text = clean_request_body(str(value or "")).strip()
    if len(text) <= _PROMPT_CHARS:
        return text
    return text[: _PROMPT_CHARS - 2].rstrip() + " …"


def _historic_cost(value: Any) -> float:
    try:
        cost = float(value or 0.0)
    except (TypeError, ValueError) as exc:
        log.debug("society history migration: invalid historic cost %r (%s)", value, exc)
        return 0.0
    return cost if cost > 0 and math.isfinite(cost) else 0.0


def _timestamp(row: dict[str, Any]) -> int:
    for key in ("updated_ms", "created_ms"):
        try:
            value = int(row.get(key) or 0)
        except (TypeError, ValueError) as exc:
            log.debug(
                "society history migration: invalid %s timestamp %r (%s)",
                key,
                row.get(key),
                exc,
            )
            value = 0
        if value > 0:
            return value
    return 0


async def migrate_legacy_missions(
    store: SocietyStore,
    manager: Any,
    *,
    limit: int = _HISTORY_LIMIT,
) -> int:
    """Import legacy mission headers once, without emitting live bus activity."""
    if await store.get_meta(_MIGRATION_KEY, "0") == "1":
        return 0
    source = getattr(manager, "store", None)
    lister = getattr(source, "list_missions", None)
    if not callable(lister):
        return 0

    rows = await lister(limit=max(1, min(int(limit), _HISTORY_LIMIT)))
    imported = 0
    # Mission storage returns newest first; insert oldest first so Ledger
    # sequence order stays intuitive.
    for raw in reversed(rows):
        if not isinstance(raw, dict):
            continue
        mission_id = str(raw.get("id") or "").strip()
        if not mission_id:
            continue
        trace_id = f"mission:{mission_id}"
        if await store.events_for_trace(trace_id):
            continue

        state = str(raw.get("state") or "").strip().upper() or "UNKNOWN"
        prompt = _prompt_preview(raw.get("prompt"))
        text = prompt or f"Mission {mission_id}"
        payload: dict[str, Any] = {
            "kind": "legacy_mission",
            "run_id": mission_id,
            "state": state,
            "text": f"{text} · {state}",
            "migrated": True,
        }
        cost = _historic_cost(raw.get("cost_usd"))
        if cost:
            payload["historic_cost_usd"] = cost

        await store.import_event(
            SocietyEnvelope(
                event_id=f"legacy-mission:{mission_id}:history",
                msg_type=MsgType.DIGEST,
                from_agent=LEAD_AGENT_ID,
                trace_id=trace_id,
                ts_ms=_timestamp(raw),
                cost_usd=0.0,
                payload=payload,
            )
        )
        imported += 1

    await store.set_meta(_MIGRATION_KEY, "1")
    return imported
