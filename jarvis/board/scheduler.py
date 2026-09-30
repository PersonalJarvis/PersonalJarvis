"""BioScheduler — rewrites the Board bio when something meaningful happens.

There is no timer. The bio used to be regenerated every Sunday at 18:00, on
every boot of an install without a bio, and on ``*_master`` achievements —
on the primary provider's deep model, usually a paid key, whether or not
anything had changed. Maintainer decision (2026-09-30): update the bio on real
board events and bill it to the subscription (see ``bio_brain.py``).

Hooks:

1. **Achievement unlocked** — every ``AchievementUnlocked`` on the bus. The
   evaluator publishes each achievement exactly once (``INSERT OR IGNORE``),
   so these are the board's milestones.
2. **Explicit refresh** — ``POST /api/board/bio/regenerate`` calls the
   generator directly and is never held back by the spacing below.

A burst of hooks (one tool call can unlock two achievements) is debounced into
ONE generation, and a hook-driven generation keeps at least
:attr:`BioScheduler.MIN_SPACING_S` after the newest bio. A hook inside that
window is not dropped: the pending generation waits for the window to close.
A bio written after the first pending hook — the user refreshed by hand —
already covers it, so the pending run then ends without generating again.

A failed or deferred generation (no subscription usable right now) is not
retried on a timer; the next board event tries again. Pending work lives in
memory only, and nothing is generated at boot: an install without a bio gets
its first one at its first achievement or refresh click.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from jarvis.core.bus import EventBus
from jarvis.core.events import AchievementUnlocked

from .profile import BioGenerator, BioStore

log = logging.getLogger(__name__)


class BioScheduler:
    """Turns board events into (at most) one bio generation at a time."""

    #: Quiet period that folds a burst of hooks into one generation.
    DEBOUNCE_S = 30.0
    #: Minimum distance between the newest bio and a hook-driven generation.
    MIN_SPACING_S = 24 * 3600.0

    def __init__(
        self,
        *,
        generator: BioGenerator,
        bio_store: BioStore,
        bus: EventBus | None = None,
        debounce_s: float | None = None,
        min_spacing_s: float | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._gen = generator
        self._bio_store = bio_store
        self._bus = bus
        self._debounce_s = self.DEBOUNCE_S if debounce_s is None else max(0.0, debounce_s)
        self._min_spacing_s = (
            self.MIN_SPACING_S if min_spacing_s is None else max(0.0, min_spacing_s)
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._subscribed = False
        self._closed = False
        self._pending: asyncio.Task[None] | None = None
        self._reasons: list[str] = []
        self._first_hook_at: datetime | None = None

    # --------------- Lifecycle ---------------

    def start(self) -> None:
        """Subscribes to the hooks. Starts no task and generates nothing."""
        self._closed = False
        if self._bus is not None and not self._subscribed:
            self._bus.subscribe(AchievementUnlocked, self._on_achievement)
            self._subscribed = True

    async def stop(self) -> None:
        self._closed = True
        if self._bus is not None and self._subscribed:
            self._bus.unsubscribe(AchievementUnlocked, self._on_achievement)
            self._subscribed = False
        task, self._pending = self._pending, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # the cancellation requested just above
        self._reasons.clear()
        self._first_hook_at = None

    # --------------- Hooks ---------------

    async def _on_achievement(self, event: AchievementUnlocked) -> None:
        """Bus callback: only records the hook, never waits on a brain."""
        achievement = (event.achievement_id or "").strip()
        self.notify(f"milestone:{achievement}" if achievement else "milestone")

    def notify(self, reason: str) -> None:
        """Records one meaningful board event. Call on the event loop."""
        if self._closed:
            return
        if not self._reasons:
            self._first_hook_at = self._clock()
        self._reasons.append(reason)
        if self._pending is None or self._pending.done():
            self._pending = asyncio.create_task(
                self._run_pending(), name="board-bio-refresh",
            )

    # --------------- Pending generation ---------------

    async def _run_pending(self) -> None:
        try:
            await self._wait_then_generate()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one failed run must not kill later hooks
            log.exception("BioScheduler: hook-driven bio generation failed")
        # Hooks that arrived while this run was generating get their own run;
        # it finds the fresh bio and ends unless they came after it.
        if self._reasons and not self._closed:
            self._pending = asyncio.create_task(
                self._run_pending(), name="board-bio-refresh",
            )
        else:
            self._pending = None

    async def _wait_then_generate(self) -> None:
        if self._debounce_s > 0:
            await asyncio.sleep(self._debounce_s)
        while True:
            wait_s = await asyncio.to_thread(self._seconds_until_due)
            if wait_s is None:
                log.info(
                    "BioScheduler: a newer bio already covers %s", ", ".join(self._reasons),
                )
                self._drain()
                return
            if wait_s <= 0:
                break
            log.info(
                "BioScheduler: %s waits %.0f s for the %.0f h spacing",
                ", ".join(self._reasons), wait_s, self._min_spacing_s / 3600,
            )
            await asyncio.sleep(wait_s)

        reasons = self._drain()
        result = await self._gen.generate_bio(triggered_by=reasons[0])
        if result is None:
            log.info(
                "BioScheduler: bio not rewritten (trigger=%s); the next board event retries",
                ", ".join(reasons),
            )
        else:
            log.info("BioScheduler: bio rewritten (trigger=%s)", ", ".join(reasons))

    def _seconds_until_due(self) -> float | None:
        """Seconds until a generation may run; ``None`` if a newer bio covers it."""
        latest = self._bio_store.latest()
        generated_at = _parse_iso((latest or {}).get("generated_at"))
        if generated_at is None:
            return 0.0
        first_hook_at = self._first_hook_at
        if first_hook_at is not None and generated_at >= first_hook_at:
            return None
        due = generated_at + timedelta(seconds=self._min_spacing_s)
        return max(0.0, (due - self._clock()).total_seconds())

    def _drain(self) -> list[str]:
        reasons = list(self._reasons) or ["milestone"]
        self._reasons.clear()
        self._first_hook_at = None
        return reasons


def _parse_iso(value: object) -> datetime | None:
    """An aware datetime from a stored ISO timestamp, or None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        log.warning("BioScheduler: unreadable bio timestamp %r, treating as no bio", value)
        return None
    # BioStore writes local time with its offset; a naive value is local too.
    return parsed if parsed.tzinfo is not None else parsed.astimezone()
