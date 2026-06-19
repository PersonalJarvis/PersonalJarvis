"""BioScheduler — triggers the BioGenerator on a regular schedule (phase B).

Two trigger types:

1. **Weekly** (default: Sunday 18:00 local time). An ``asyncio`` loop ticks
   every 60 s and checks: is it Sunday within the 18:00–18:05 window and
   has regeneration not yet run *today*? The date guard stored in
   ``aggregator_meta[last_bio_run_date]`` prevents double-runs even when
   the app is restarted inside the window.

2. **Master achievement**: a second bus subscriber listens for
   ``AchievementUnlocked`` and triggers regeneration for every
   ``*_master`` ID — e.g. ``tool_master``.

No APScheduler dependency (project pattern, see RECON.md §4).
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from jarvis.core.bus import EventBus
from jarvis.core.events import AchievementUnlocked, Event

from .profile import BioGenerator, BioStore
from .store import BoardStore

log = logging.getLogger(__name__)


class BioScheduler:
    """Orchestrates bio regeneration.

    Uses the same DB as the aggregator and evaluator — an additional
    ``meta`` key (``last_bio_run_date``) guarantees idempotency.
    """

    # Default config — can be overridden in the future via cfg.board.ai_profile
    # (plan §5-B Ultrathink #3).
    DEFAULT_WEEKDAY = 6     # Sonntag (Mon=0)
    DEFAULT_HOUR = 18
    WINDOW_MINUTES = 5

    def __init__(
        self,
        *,
        generator: BioGenerator,
        db_path: Path,
        bus: EventBus | None = None,
        weekday: int | None = None,
        hour: int | None = None,
        tick_interval_s: float = 60.0,
        memory_text_provider: Any = None,
        soul_text_provider: Any = None,
        bio_store: BioStore | None = None,
        board_store: BoardStore | None = None,
        cold_start_min_days: int = 1,
    ) -> None:
        self._gen = generator
        self._db_path = Path(db_path)
        self._bus = bus
        self._weekday = self.DEFAULT_WEEKDAY if weekday is None else weekday
        self._hour = self.DEFAULT_HOUR if hour is None else hour
        self._tick_s = tick_interval_s
        self._memory_provider = memory_text_provider
        self._soul_provider = soul_text_provider
        self._task: asyncio.Task[None] | None = None
        self._subscribed = False
        # Cold-start: fires once when no bio exists AND the user already has
        # at least ``cold_start_min_days`` days of activity.
        self._bio_store = bio_store
        self._board_store = board_store
        self._cold_start_min_days = max(0, int(cold_start_min_days))
        self._cold_start_done = False

    # --------------- Lifecycle ---------------

    def start(self) -> None:
        if self._bus is not None and not self._subscribed:
            self._bus.subscribe_all(self._on_event)
            self._subscribed = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="bio-scheduler")

    async def stop(self) -> None:
        if self._bus is not None and self._subscribed:
            try:
                self._bus._wildcard_subscribers.remove(self._on_event)  # type: ignore[attr-defined]
            except (AttributeError, ValueError):
                pass
            self._subscribed = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    # --------------- Weekly loop ---------------

    async def _loop(self) -> None:
        while True:
            try:
                await self._maybe_run_cold_start()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("BioScheduler cold-start tick failed")
            try:
                await self._maybe_run_weekly()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("BioScheduler weekly tick failed")
            try:
                await asyncio.sleep(self._tick_s)
            except asyncio.CancelledError:
                raise

    async def _maybe_run_cold_start(self) -> None:
        """Fires once when no bio exists yet.

        The check ticks every minute (cheap), but the flag ``_cold_start_done``
        prevents multiple triggers within the same app session.
        Persistent idempotency is inherited from ``last_bio_run_date`` — once
        the cold-start bio is written, ``BioStore.latest()`` is no longer
        ``None``, so the path does not trigger again.
        """
        if self._cold_start_done:
            return
        if self._bio_store is None or self._board_store is None:
            self._cold_start_done = True       # Stack is incomplete, abort
            return
        latest = self._bio_store.latest()
        if latest is not None:
            self._cold_start_done = True       # bio already exists
            return
        try:
            days = self._board_store.days_observed()
        except Exception:  # noqa: BLE001
            return
        if days < self._cold_start_min_days:
            return
        today_iso = datetime.now().astimezone().date().isoformat()
        await self._run_and_mark(triggered_by="cold_start", today_iso=today_iso)
        self._cold_start_done = True

    async def _maybe_run_weekly(self) -> None:
        now = datetime.now().astimezone()
        if now.weekday() != self._weekday:
            return
        if now.hour != self._hour:
            return
        if now.minute >= self.WINDOW_MINUTES:
            return
        today_iso = now.date().isoformat()
        if self._read_meta("last_bio_run_date") == today_iso:
            return
        await self._run_and_mark(triggered_by="weekly", today_iso=today_iso)

    async def _run_and_mark(self, *, triggered_by: str, today_iso: str) -> None:
        memory = await _call(self._memory_provider, default="")
        soul = await _call(self._soul_provider, default="")
        result = await self._gen.generate_bio(
            memory_text=memory,
            soul_text=soul,
            triggered_by=triggered_by,
        )
        if result is not None:
            self._write_meta("last_bio_run_date", today_iso)
            log.info("BioScheduler: Bio regeneriert (trigger=%s)", triggered_by)

    # --------------- Achievement-driven trigger ---------------

    async def _on_event(self, event: Event) -> None:
        """Bus callback for ``AchievementUnlocked`` events with id *_master."""
        try:
            if not isinstance(event, AchievementUnlocked):
                return
            if not event.achievement_id.endswith("_master"):
                return
            today_iso = datetime.now().astimezone().date().isoformat()
            # Milestone regeneration is NOT blocked by the date guard —
            # if the user reaches tool_master on a Sunday, they should
            # receive the fresh bio immediately, not next week.
            await self._gen.generate_bio(
                memory_text=await _call(self._memory_provider, default=""),
                soul_text=await _call(self._soul_provider, default=""),
                triggered_by=f"milestone:{event.achievement_id}",
            )
            self._write_meta("last_bio_run_date", today_iso)
        except Exception:  # noqa: BLE001
            log.exception("BioScheduler milestone trigger failed")

    # --------------- DB helpers ---------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _read_meta(self, key: str) -> str | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT value FROM aggregator_meta WHERE key = ?", (key,),
            ).fetchone()
            return row["value"] if row is not None else None
        finally:
            conn.close()

    def _write_meta(self, key: str, value: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO aggregator_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        finally:
            conn.close()


async def _call(provider: Any, *, default: str) -> str:
    """Accepts a callable, a coroutine-callable, or a plain string as provider."""
    if provider is None:
        return default
    if callable(provider):
        result = provider()
        if asyncio.iscoroutine(result):
            result = await result
        return str(result) if result is not None else default
    return str(provider)
