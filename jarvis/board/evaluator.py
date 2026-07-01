"""AchievementEvaluator — live bus subscriber that unlocks achievements.

Design:

1. Subscribes to ``EventBus.subscribe_all`` and filters on the classes listed
   in ``achievements.TRIGGERING_EVENT_NAMES``. Everything else is discarded
   in O(1).

2. Builds an ``AchievementContext`` from:
   - an LRU map ``trace_id → set(tool_name)`` (in-memory, cap=200)
   - persisted counters in ``aggregator_meta``
   - live running sums that are flushed to the DB after each event.

3. Iterates through the ``ACHIEVEMENTS`` catalog; when an evaluator returns
   an ``UnlockDecision``, the writer attempts an ``INSERT OR IGNORE`` — if
   ``rowcount > 0`` it emits an ``AchievementUnlocked`` event on the same bus.

## Error isolation

Each ``_on_event`` is wrapped in a ``try/except Exception: log`` — an
evaluator that crashes due to a malformed event payload must not block
voice-loop telemetry (Plan §5-B bullet "Achievement-Evaluator must not wait
on a Brain call").
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from jarvis.core.bus import EventBus
from jarvis.core.events import AchievementUnlocked, Event

from .achievements import (
    ACHIEVEMENTS,
    ACHIEVEMENTS_BY_ID,
    TRIGGERING_EVENT_NAMES,
    AchievementContext,
    AchievementSpec,
)

log = logging.getLogger(__name__)

_TRACE_LRU_CAP = 200          # number of trace_ids kept in memory
_7D_SECONDS = 7 * 24 * 3600


class _LiveContext(AchievementContext):
    """Concrete ``AchievementContext`` implementation.

    Holds a small set of running counters plus an LRU map per trace.
    On evaluator start (``attach()``) it populates itself once from the DB
    so that counters survive restarts.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._tools_ever: set[str] = set()
        self._successful_tasks = 0
        self._openclaw_success = 0
        self._mcp_success = 0
        self._trace_tools: OrderedDict[str, set[str]] = OrderedDict()
        self._first_event_iso: str | None = None
        self._hours_saved_7d_cache: tuple[int, float] = (0, 0.0)
        self._lock = threading.Lock()
        self._hydrate()

    # --------------- Hydration ---------------

    def _hydrate(self) -> None:
        # Wave-4 migration: ``sub_jarvis_success_total`` is kept as a carry-over
        # for backwards compatibility — if an old DB still has the key, its
        # value is merged into ``openclaw_success_total``. New writes go
        # exclusively to ``openclaw_success_total``.
        rows = self._conn.execute(
            "SELECT key, value FROM aggregator_meta "
            "WHERE key IN ('tools_ever','successful_tasks_total',"
            "'openclaw_success_total','sub_jarvis_success_total',"
            "'mcp_success_total','first_event_iso')"
        ).fetchall()
        data = {r["key"]: r["value"] for r in rows}
        try:
            loaded = json.loads(data.get("tools_ever", "[]"))
            if isinstance(loaded, list):
                self._tools_ever = {str(t) for t in loaded}
        except json.JSONDecodeError:
            self._tools_ever = set()
        self._successful_tasks = int(data.get("successful_tasks_total", "0") or 0)
        legacy_sj = int(data.get("sub_jarvis_success_total", "0") or 0)
        self._openclaw_success = int(data.get("openclaw_success_total", "0") or 0) + legacy_sj
        self._mcp_success = int(data.get("mcp_success_total", "0") or 0)
        self._first_event_iso = data.get("first_event_iso") or None

    def _persist(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO aggregator_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # --------------- Context-Protocol ---------------

    def ever_seen_tools(self) -> set[str]:
        return set(self._tools_ever)

    def tools_for_trace(self, trace_id: str) -> set[str]:
        return set(self._trace_tools.get(trace_id, set()))

    def successful_tasks_total(self) -> int:
        return self._successful_tasks

    def openclaw_success_total(self) -> int:
        return self._openclaw_success

    def mcp_success_total(self) -> int:
        return self._mcp_success

    def hours_saved_last_7d(self) -> float:
        """Computed from ``daily_stats`` — with a 60 s cache so that OpenClaw
        bursts do not saturate the query.
        """
        now = int(time.time())
        cached_at, cached = self._hours_saved_7d_cache
        if now - cached_at < 60:
            return cached
        cutoff = date.fromtimestamp(now - _7D_SECONDS).isoformat() if hasattr(date, "fromtimestamp") else (
            datetime.fromtimestamp(now - _7D_SECONDS).date().isoformat()
        )
        row = self._conn.execute(
            "SELECT COALESCE(SUM(hours_saved_estimate), 0) AS s "
            "FROM daily_stats WHERE date >= ?",
            (cutoff,),
        ).fetchone()
        value = float(row["s"] if row is not None else 0.0)
        self._hours_saved_7d_cache = (now, value)
        return value

    def first_event_date_iso(self) -> str | None:
        return self._first_event_iso

    # --------------- Mutators (called by the evaluator) ---------------

    def record_event(self, event: Event) -> None:
        """Updates counters/sets based on a relevant event.

        Must be called *before* ``iter_unlocks`` so that evaluator callbacks
        see the updated state.
        """
        with self._lock:
            # First-event tracking: only set on the very first event (never overwritten).
            if self._first_event_iso is None:
                ts_ns = int(getattr(event, "timestamp_ns", 0) or 0)
                if ts_ns > 0:
                    iso = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)\
                        .astimezone().date().isoformat()
                    self._first_event_iso = iso
                    self._persist("first_event_iso", iso)

            name = type(event).__name__

            if name == "ActionExecuted" and bool(getattr(event, "success", False)):
                tool = str(getattr(event, "tool_name", "") or "").strip()
                if tool:
                    newly = tool not in self._tools_ever
                    self._tools_ever.add(tool)
                    if newly:
                        self._persist("tools_ever", json.dumps(sorted(self._tools_ever)))
                    # LRU per trace
                    trace_id = getattr(event, "trace_id", None)
                    if trace_id is not None:
                        key = trace_id.hex if hasattr(trace_id, "hex") else str(trace_id)
                        bucket = self._trace_tools.pop(key, set())
                        bucket.add(tool)
                        self._trace_tools[key] = bucket
                        while len(self._trace_tools) > _TRACE_LRU_CAP:
                            self._trace_tools.popitem(last=False)

            elif name == "TaskCompleted":
                self._successful_tasks += 1
                self._persist("successful_tasks_total", str(self._successful_tasks))

            elif name == "JarvisAgentTaskCompleted":
                if bool(getattr(event, "success", False)):
                    self._successful_tasks += 1
                    self._openclaw_success += 1
                    self._persist("successful_tasks_total", str(self._successful_tasks))
                    self._persist("openclaw_success_total", str(self._openclaw_success))

            elif name == "HarnessCompleted":
                harness = getattr(event, "harness", "")
                result = getattr(event, "result", None)
                exit_code = int(getattr(result, "exit_code", -1)) if result is not None else -1
                if harness == "mcp-remote" and exit_code == 0:
                    self._mcp_success += 1
                    self._persist("mcp_success_total", str(self._mcp_success))


# ----------------------------------------------------------------------
# Evaluator
# ----------------------------------------------------------------------

class AchievementEvaluator:
    """Bus subscriber that persists achievements and publishes AchievementUnlocked.

    Usage::

        ev = AchievementEvaluator(db_path, bus=bus)
        ev.attach()        # subscribes to the EventBus
        # ...
        ev.detach()
    """

    def __init__(self, db_path: Path, *, bus: EventBus | None = None) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._bus = bus
        self._conn: sqlite3.Connection | None = None
        self._ctx: _LiveContext | None = None
        self._subscribed = False

    # --------------- Lifecycle ---------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
            self._conn.executescript(schema)
        return self._conn

    def attach(self) -> None:
        """Initialises the context and subscribes to the bus. Idempotent."""
        self._connect()
        if self._ctx is None:
            self._ctx = _LiveContext(self._conn)  # type: ignore[arg-type]
        if self._bus is not None and not self._subscribed:
            self._bus.subscribe_all(self._on_event)
            self._subscribed = True

    def detach(self) -> None:
        """Stops the bus subscription (the connection stays open for queries)."""
        if self._bus is not None and self._subscribed:
            try:
                self._bus._wildcard_subscribers.remove(self._on_event)  # type: ignore[attr-defined]
            except (AttributeError, ValueError):
                pass
        self._subscribed = False

    def close(self) -> None:
        self.detach()
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    # --------------- Event-Dispatch ---------------

    async def _on_event(self, event: Event) -> None:
        """Bus callback. Must NEVER raise — Plan §5-B."""
        try:
            name = type(event).__name__
            if name not in TRIGGERING_EVENT_NAMES:
                return
            assert self._ctx is not None
            self._ctx.record_event(event)
            unlocks = list(self._evaluate(event, self._ctx))
            for spec, decision in unlocks:
                await self._fire_unlock(spec, decision)
        except Exception:  # noqa: BLE001
            log.exception("AchievementEvaluator._on_event failed")

    def evaluate_sync(self, event: Event) -> list[tuple[AchievementSpec, Any]]:
        """Synchronous entry point for tests.

        Returns the list of specs that were actually *newly* unlocked (i.e.
        after ``INSERT OR IGNORE`` with ``rowcount > 0``). Publish events are
        NOT fired here — those are tested separately.
        """
        self.attach()
        assert self._ctx is not None
        if type(event).__name__ not in TRIGGERING_EVENT_NAMES:
            return []
        self._ctx.record_event(event)
        unlocks = []
        for spec, decision in self._evaluate(event, self._ctx):
            if self._write_unlock(spec, decision):
                unlocks.append((spec, decision))
        return unlocks

    def _evaluate(self, event: Event, ctx: _LiveContext):
        for spec in ACHIEVEMENTS:
            try:
                result = spec.evaluator(event, ctx)
            except Exception:  # noqa: BLE001
                log.exception("Evaluator for %s failed", spec.id)
                continue
            if result is not None:
                yield spec, result

    # --------------- Persist + Publish ---------------

    def _write_unlock(self, spec: AchievementSpec, decision: Any) -> bool:
        """Executes INSERT OR IGNORE. Returns True if the row was newly inserted."""
        conn = self._connect()
        evidence_json = json.dumps(decision.evidence) if decision else "{}"
        now_iso = datetime.now(timezone.utc).astimezone().isoformat()
        cur = conn.execute(
            "INSERT OR IGNORE INTO achievements (id, unlocked_at, evidence) "
            "VALUES (?, ?, ?)",
            (spec.id, now_iso, evidence_json),
        )
        return cur.rowcount > 0

    async def _fire_unlock(self, spec: AchievementSpec, decision: Any) -> None:
        if not self._write_unlock(spec, decision):
            return
        if self._bus is None:
            return
        evt = AchievementUnlocked(
            achievement_id=spec.id,
            title=spec.title,
            description=spec.description,
            tier=spec.tier,
            evidence=dict(decision.evidence or {}),
            source_layer="board.evaluator",
        )
        try:
            await self._bus.publish(evt)
        except Exception:  # noqa: BLE001
            log.exception("Publish AchievementUnlocked failed")

    # --------------- Read-only queries (for the API) ---------------

    def list_all(self) -> list[dict[str, Any]]:
        """All specs (locked and unlocked) with metadata for the API.

        Opens a short-lived connection so that the method is thread-safe
        (the API route runs it in ``asyncio.to_thread``, not on the main
        event-loop thread — SQLite connections are by default bound to the
        creating thread).
        """
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
            conn.executescript(schema)
            rows = {
                r["id"]: r for r in conn.execute(
                    "SELECT id, unlocked_at, evidence FROM achievements"
                ).fetchall()
            }
        finally:
            conn.close()
        out: list[dict[str, Any]] = []
        for spec in ACHIEVEMENTS:
            row = rows.get(spec.id)
            unlocked_at = row["unlocked_at"] if row is not None else None
            evidence = {}
            if row is not None and row["evidence"]:
                try:
                    evidence = json.loads(row["evidence"])
                except json.JSONDecodeError:
                    evidence = {}
            out.append({
                "id": spec.id,
                "title": spec.title,
                "description": spec.description,
                "tier": spec.tier,
                "unlocked_at": unlocked_at,
                "evidence": evidence,
            })
        return out
