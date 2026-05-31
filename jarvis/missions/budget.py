"""Token-bucket cost guard for Phase-6 missions.

Per-mission $5 + daily $50 (jarvis.toml [phase6.budget]). Voice warnings at
50% / 80% via `MissionBudgetWarning` event. Hard abort at 100% via
`BudgetExceeded` exception (orchestrator catches this and transitions
to MissionFailed("budget_exceeded")).

Wiring:
- `BudgetTracker.record(mission_id, cost_usd)` called from the orchestrator after
  each `WorkerDraftReady` OR `CriticVerdictReady` (each with cost_usd).
- Optional `bind_to_event_bus(bus)` registers a subscribe_all handler
  that automatically tracks `WorkerDraftReady` events via record().

State:
- `_per_mission_costs: dict[mission_id, accumulated_usd]` — RAM only, does NOT
  survive a crash (intentional — cost is also persisted in missions.cost_usd;
  recovery could reconstruct from there if needed).
- `_daily_total_usd: float` + `_daily_reset_ts_ms: int` — reset at midnight UTC.
- `_warned_thresholds_per_mission: dict[mission_id, set[int]]` — prevents
  duplicate warnings on every increment.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Final

from .events import EventEnvelope, MissionBudgetWarning, WorkerDraftReady, now_ms

logger = logging.getLogger(__name__)


DEFAULT_PER_MISSION_USD: Final[float] = 5.0
DEFAULT_DAILY_USD: Final[float] = 50.0
DEFAULT_WARN_PCT: Final[tuple[int, ...]] = (50, 80)
ONE_DAY_MS: Final[int] = 24 * 60 * 60 * 1000


class BudgetExceeded(RuntimeError):
    """Per-mission or daily budget exceeded — mission abort."""


# Type for optional event emitter (e.g. event_store.append_and_publish).
EventEmitterFn = Callable[[EventEnvelope], Awaitable[int]]


class BudgetTracker:
    """Cost accumulator + warning emitter + hard-abort guard."""

    def __init__(
        self,
        *,
        per_mission_usd: float = DEFAULT_PER_MISSION_USD,
        daily_usd: float = DEFAULT_DAILY_USD,
        warn_pct: tuple[int, ...] = DEFAULT_WARN_PCT,
        emitter: EventEmitterFn | None = None,
    ) -> None:
        if per_mission_usd <= 0 or daily_usd <= 0:
            raise ValueError("Budget-Caps muessen > 0 sein")
        for p in warn_pct:
            if not (0 < p < 100):
                raise ValueError(f"warn_pct {p} muss in (0, 100) liegen")

        self._per_mission_usd = per_mission_usd
        self._daily_usd = daily_usd
        self._warn_pct = tuple(sorted(set(warn_pct)))  # dedup + sorted
        self._emitter = emitter

        self._per_mission_costs: dict[str, float] = {}
        self._daily_total_usd: float = 0.0
        self._daily_reset_ts_ms: int = self._next_midnight_utc_ms(now_ms())
        self._warned_per_mission: dict[str, set[int]] = {}
        self._lock = asyncio.Lock()  # serializes record() for parallel workers

    @property
    def per_mission_limit(self) -> float:
        return self._per_mission_usd

    @property
    def daily_limit(self) -> float:
        return self._daily_usd

    def mission_cost(self, mission_id: str) -> float:
        """Current accumulated cost for a mission."""
        return self._per_mission_costs.get(mission_id, 0.0)

    def daily_total(self) -> float:
        """Current accumulated daily cost (all missions)."""
        self._maybe_reset_daily()
        return self._daily_total_usd

    async def record(self, mission_id: str, cost_usd: float) -> None:
        """Increments counters; emits warnings; raises BudgetExceeded.

        Args:
            mission_id: Mission ID (UUIDv7).
            cost_usd: Increment (positive). 0 or negative is logged as a
                no-op but not raised.
        """
        if cost_usd <= 0:
            logger.debug("BudgetTracker.record: ignoring non-positive cost %s", cost_usd)
            return

        async with self._lock:
            self._maybe_reset_daily()

            # Increment first, THEN check — otherwise the exceeding entry
            # would not appear in the stats.
            new_mission = self._per_mission_costs.get(mission_id, 0.0) + cost_usd
            new_daily = self._daily_total_usd + cost_usd

            self._per_mission_costs[mission_id] = new_mission
            self._daily_total_usd = new_daily

            await self._maybe_emit_warning(mission_id, new_mission)

            # Hard-cap check AFTER warning emit (user should still hear the warning).
            if new_mission >= self._per_mission_usd:
                raise BudgetExceeded(
                    f"Mission {mission_id} hat ${new_mission:.2f} verbraucht, "
                    f"Limit ${self._per_mission_usd:.2f}."
                )
            if new_daily >= self._daily_usd:
                raise BudgetExceeded(
                    f"Daily Budget ueberschritten: ${new_daily:.2f} / "
                    f"${self._daily_usd:.2f}. Mission {mission_id} aborted."
                )

    def assert_under_limit(self, mission_id: str) -> None:
        """Synchronous pre-check: raises BudgetExceeded without incrementing.

        Call from the orchestrator before every worker spawn — prevents a
        worker from starting when the budget is already exhausted.
        """
        self._maybe_reset_daily()
        cur = self._per_mission_costs.get(mission_id, 0.0)
        if cur >= self._per_mission_usd:
            raise BudgetExceeded(
                f"Mission {mission_id}: Pre-Spawn-Check failed (${cur:.2f} >= "
                f"${self._per_mission_usd:.2f})"
            )
        if self._daily_total_usd >= self._daily_usd:
            raise BudgetExceeded(
                f"Daily-Limit ueberschritten ({self._daily_total_usd:.2f} >= "
                f"{self._daily_usd:.2f}); kein neuer Worker-Spawn."
            )

    def bind_to_event_bus(self, bus) -> None:  # type: ignore[no-untyped-def]
        """Registers a subscribe_all handler that automatically counts
        WorkerDraftReady events via record().

        Args:
            bus: MissionBus with `.subscribe_all(handler)`.
        """

        async def _handler(env: EventEnvelope) -> None:
            payload = env.payload
            if isinstance(payload, WorkerDraftReady):
                try:
                    await self.record(env.mission_id, payload.cost_usd)
                except BudgetExceeded:
                    # The orchestrator should raise this, not the bus handler.
                    # Logging here is sufficient — the record call has already
                    # stored the cost and emitted the warning.
                    logger.warning(
                        "BudgetTracker: Budget exceeded fuer mission %s "
                        "(via WorkerDraftReady auto-track) — orchestrator must abort",
                        env.mission_id,
                    )

        bus.subscribe_all(_handler)

    # --- Internals ---

    async def _maybe_emit_warning(
        self, mission_id: str, new_cost: float
    ) -> None:
        """Emits MissionBudgetWarning when crossing the warn_pct thresholds."""
        if self._emitter is None:
            return
        warned = self._warned_per_mission.setdefault(mission_id, set())
        for pct in self._warn_pct:
            if pct in warned:
                continue
            threshold_usd = self._per_mission_usd * pct / 100.0
            if new_cost >= threshold_usd:
                warned.add(pct)
                env = EventEnvelope(
                    mission_id=mission_id,
                    source_actor="system",
                    ts_ms=now_ms(),
                    payload=MissionBudgetWarning(
                        mission_id=mission_id,
                        pct_used=new_cost / self._per_mission_usd * 100.0,
                        limit_usd=self._per_mission_usd,
                    ),
                )
                try:
                    await self._emitter(env)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "BudgetTracker: emitter raised on warning emit", exc_info=True
                    )

    def _maybe_reset_daily(self) -> None:
        """Resets _daily_total when midnight UTC has been passed."""
        cur = now_ms()
        if cur >= self._daily_reset_ts_ms:
            logger.info(
                "BudgetTracker: daily-reset (was $%.2f)", self._daily_total_usd
            )
            self._daily_total_usd = 0.0
            self._daily_reset_ts_ms = self._next_midnight_utc_ms(cur)

    @staticmethod
    def _next_midnight_utc_ms(from_ms: int) -> int:
        """Next midnight UTC in ms-since-epoch."""
        # Current second -> mod 86400 -> seconds until next 00:00 UTC.
        sec = from_ms // 1000
        sec_in_day = sec % 86_400
        sec_until_midnight = 86_400 - sec_in_day
        return (sec + sec_until_midnight) * 1000


__all__ = [
    "DEFAULT_DAILY_USD",
    "DEFAULT_PER_MISSION_USD",
    "DEFAULT_WARN_PCT",
    "ONE_DAY_MS",
    "BudgetExceeded",
    "BudgetTracker",
    "EventEmitterFn",
]
