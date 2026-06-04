"""CostMeter + BudgetConfig + CooldownState (Phase 5, ADR-0006).

Responsibilities:
- Accumulate costs per `trace_id`.
- Accumulate total costs per day (daily budget).
- On overrun, invoke `CancelToken.cancel(reason=...)` and publish a
  `BudgetExceeded` event.
- Persist cooldown state (`data/cost_cooldown.json`) so that an app restart
  does not grant a budget reset.

The protocol (`jarvis.core.protocols.CostMeter`) is synchronous — therefore
the meter keeps the daily total in memory and flushes periodically into
`data/jarvis.db` (table `cost_ledger`). This avoids async hooks in
brain-stream consumers.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from jarvis.core.events import (
    BudgetExceeded,
    BudgetWarning,
    CooldownEnded,
    CooldownStarted,
)
from jarvis.core.protocols import CostRecord

# ---------------------------------------------------------------------------
# Vision cost sub-bucket
# ---------------------------------------------------------------------------
# Vision tokens / screenshots are tracked on a SEPARATE per-trace bucket from
# the main token ledger so cost dashboards can break out spend-by-modality.
# The hard cap (40 screenshots per mission): the computer-use worker
# must call ``check_vision_cap(trace_id)`` BEFORE each OmniParser invocation
# and treat ``CostCapExceeded`` as a clean mission-failure signal (it must
# NOT propagate out as an unhandled exception).
VISION_SCREENSHOTS_HARD_CAP: int = 40


class CostCapExceeded(RuntimeError):
    """Raised by :meth:`CostMeter.check_vision_cap` when a mission has
    already consumed :data:`VISION_SCREENSHOTS_HARD_CAP` screenshots.

    Carries the offending ``trace_id`` plus the current count so callers
    (the computer-use worker) can emit a structured mission-failure
    (reason ``"vision_cap_exceeded"``) instead of a raw stack trace.
    """

    def __init__(self, trace_id: UUID, screenshots: int) -> None:
        super().__init__(
            f"Vision-screenshot cap exceeded for trace {trace_id.hex}: "
            f"{screenshots}/{VISION_SCREENSHOTS_HARD_CAP}"
        )
        self.trace_id = trace_id
        self.screenshots = screenshots

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus

    from .cancel import KillSwitch


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD per 1 M tokens for input / output / cache-hit of a model."""
    usd_per_1m_input: float
    usd_per_1m_output: float
    usd_per_1m_cache_hit: float = 0.0


@dataclass(frozen=True, slots=True)
class BudgetConfig:
    """Populated from `jarvis.toml:[cost]`. All values are in Euro (user-visible);
    conversion from USD happens at the boundary to CostMeter.
    """
    enabled: bool = False
    per_task_eur: float = 2.0
    per_day_eur: float = 30.0
    cooldown_minutes: int = 60
    warn_at_fraction: float = 0.8            # 80% → BudgetWarning
    eur_per_usd: float = 0.92
    prices: dict[str, ModelPrice] = field(default_factory=dict)

    @staticmethod
    def estimate_usd(
        prices: dict[str, ModelPrice],
        model: str,
        tokens_in: int,
        tokens_out: int,
        tokens_cache_hit: int = 0,
    ) -> float:
        """Estimate USD cost using the price table.

        Returns 0.0 when the model is not found in the table. This is a
        deliberate safety net: no budget is preferable to a wrong budget.
        The caller (BrainManager hook) should log missing prices.
        """
        price = prices.get(model)
        if price is None:
            return 0.0
        return (
            tokens_in / 1_000_000 * price.usd_per_1m_input
            + tokens_out / 1_000_000 * price.usd_per_1m_output
            + tokens_cache_hit / 1_000_000 * price.usd_per_1m_cache_hit
        )


# ----------------------------------------------------------------------
# Cooldown persistence
# ----------------------------------------------------------------------

@dataclass
class CooldownState:
    """Persistent cooldown — survives app restarts."""
    until_ns: int = 0
    reason: str = ""

    @classmethod
    def load(cls, path: Path) -> CooldownState:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                until_ns=int(data.get("until_ns", 0)),
                reason=str(data.get("reason", "")),
            )
        except (OSError, json.JSONDecodeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"until_ns": self.until_ns, "reason": self.reason}),
            encoding="utf-8",
        )
        tmp.replace(path)

    def is_active(self, now_ns: int | None = None) -> bool:
        return self.until_ns > (now_ns if now_ns is not None else time.time_ns())


# ----------------------------------------------------------------------
# CostMeter
# ----------------------------------------------------------------------

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS cost_ledger (
    day              TEXT NOT NULL,
    provider         TEXT NOT NULL,
    model            TEXT NOT NULL,
    tokens_in        INTEGER NOT NULL DEFAULT 0,
    tokens_out       INTEGER NOT NULL DEFAULT 0,
    tokens_cache_hit INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL    NOT NULL DEFAULT 0.0,
    PRIMARY KEY (day, provider, model)
);
"""


class CostMeter:
    """Concrete implementation of the `CostMeter` protocol with SQLite
    persistence and a cooldown file.

    Lifecycle:
        meter = CostMeter(config, db_path, cooldown_path, bus=bus, kill_switch=ks)
        meter.start(trace_id, provider, model)
        meter.add(CostRecord(...))           # per BrainDelta with usage
        if meter.over_task_budget(trace_id):
            ...                               # caller cancels stream
        meter.close(trace_id)                # after stream end

    Thread safety: an internal `threading.Lock` protects the in-memory
    totals. Protocol methods are intentionally synchronous so they can be
    called safely from brain-delta callbacks (which often run outside the
    asyncio event loop).
    """

    name: str = "cost-meter"

    def __init__(
        self,
        config: BudgetConfig,
        db_path: Path,
        cooldown_path: Path,
        *,
        bus: EventBus | None = None,
        kill_switch: KillSwitch | None = None,
        now_ns: Callable[[], int] = time.time_ns,   # fuer Tests
        today_date: Callable[[], str] = lambda: datetime.now().strftime("%Y-%m-%d"),
    ) -> None:
        self._config = config
        self._db_path = db_path
        self._cooldown_path = cooldown_path
        self._bus = bus
        self._kill_switch = kill_switch
        self._now_ns = now_ns
        self._today = today_date

        self._lock = threading.Lock()
        self._task_totals_usd: dict[UUID, float] = defaultdict(float)
        self._task_provider_model: dict[UUID, tuple[str, str]] = {}
        self._task_warned: set[UUID] = set()
        self._daily_total_usd: float = 0.0
        self._daily_warned: bool = False
        self._cooldown = CooldownState.load(cooldown_path)

        # AD-UF34: per-trace vision sub-bucket (tokens + screenshots).
        # Lives alongside the main token ledger so vision cost can be
        # broken out separately in dashboards. Reset on close().
        self._task_vision_tokens: dict[UUID, int] = defaultdict(int)
        self._task_vision_screenshots: dict[UUID, int] = defaultdict(int)

        # Pending writes — flushed periodically and on close().
        # Key = (day, provider, model); Value = aggregated totals.
        self._ledger_buffer: dict[tuple[str, str, str], dict[str, int | float]] = defaultdict(
            lambda: {"tokens_in": 0, "tokens_out": 0, "tokens_cache_hit": 0, "cost_usd": 0.0},
        )

        self._init_db()
        self._load_today_total()

    # ---- DB ----

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(_LEDGER_DDL)

    def _load_today_total(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) FROM cost_ledger WHERE day = ?",
                (self._today(),),
            ).fetchone()
        self._daily_total_usd = float(row[0]) if row else 0.0

    def _flush_ledger(self) -> None:
        """Persist the in-memory buffer to the database. Idempotent."""
        if not self._ledger_buffer:
            return
        snapshot = dict(self._ledger_buffer)
        self._ledger_buffer.clear()
        with sqlite3.connect(self._db_path) as conn:
            for (day, provider, model), agg in snapshot.items():
                conn.execute(
                    """
                    INSERT INTO cost_ledger(day, provider, model,
                                            tokens_in, tokens_out, tokens_cache_hit, cost_usd)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(day, provider, model) DO UPDATE SET
                        tokens_in = tokens_in + excluded.tokens_in,
                        tokens_out = tokens_out + excluded.tokens_out,
                        tokens_cache_hit = tokens_cache_hit + excluded.tokens_cache_hit,
                        cost_usd = cost_usd + excluded.cost_usd
                    """,
                    (day, provider, model,
                     agg["tokens_in"], agg["tokens_out"], agg["tokens_cache_hit"],
                     agg["cost_usd"]),
                )
            conn.commit()

    # ---- Protocol API ----

    def start(self, trace_id: UUID, provider: str, model: str) -> None:
        with self._lock:
            self._task_totals_usd.setdefault(trace_id, 0.0)
            self._task_provider_model[trace_id] = (provider, model)

    def add(self, record: CostRecord) -> None:
        with self._lock:
            self._task_totals_usd[record.trace_id] += record.usd
            self._daily_total_usd += record.usd
            key = (self._today(), record.provider, record.model)
            agg = self._ledger_buffer[key]
            agg["tokens_in"] += record.tokens_in
            agg["tokens_out"] += record.tokens_out
            agg["tokens_cache_hit"] += record.tokens_cache_hit
            agg["cost_usd"] += record.usd

            self._maybe_warn(record.trace_id)
            self._maybe_trip(record.trace_id)

    def total_for(self, trace_id: UUID) -> float:
        return self._usd_to_eur(self._task_totals_usd.get(trace_id, 0.0))

    def total_today(self) -> float:
        return self._usd_to_eur(self._daily_total_usd)

    def over_task_budget(self, trace_id: UUID) -> bool:
        if not self._config.enabled:
            return False
        return self.total_for(trace_id) > self._config.per_task_eur

    def over_daily_budget(self) -> bool:
        if not self._config.enabled:
            return False
        return self.total_today() > self._config.per_day_eur

    def close(self, trace_id: UUID) -> None:
        with self._lock:
            self._task_totals_usd.pop(trace_id, None)
            self._task_provider_model.pop(trace_id, None)
            self._task_warned.discard(trace_id)
            self._task_vision_tokens.pop(trace_id, None)
            self._task_vision_screenshots.pop(trace_id, None)
            self._flush_ledger()

    # ------------------------------------------------------------------
    # Vision sub-bucket (Wave 4 sub-task 4.1, AD-UF34)
    # ------------------------------------------------------------------

    def record_vision(
        self,
        trace_id: UUID,
        *,
        tokens: int = 0,
        screenshots: int = 0,
    ) -> None:
        """Record vision-modality consumption for ``trace_id``.

        Called by the computer-use worker on each OmniParser invocation. Both
        counters monotonically increase per trace; the trace is reset on
        :meth:`close`. The cap is **not** enforced here — call
        :meth:`check_vision_cap` BEFORE each OmniParser invocation
        instead. Splitting record from check keeps the worker's
        control flow simple (record-after-success vs check-before-call).
        """
        if tokens < 0 or screenshots < 0:
            raise ValueError(
                "record_vision: tokens and screenshots must be non-negative"
            )
        with self._lock:
            self._task_vision_tokens[trace_id] += tokens
            self._task_vision_screenshots[trace_id] += screenshots

    def vision_screenshots_for(self, trace_id: UUID) -> int:
        """Return the current screenshot count for ``trace_id`` (read-only)."""
        return self._task_vision_screenshots.get(trace_id, 0)

    def vision_tokens_for(self, trace_id: UUID) -> int:
        """Return the current vision-token count for ``trace_id`` (read-only)."""
        return self._task_vision_tokens.get(trace_id, 0)

    def check_vision_cap(self, trace_id: UUID) -> None:
        """Raise :class:`CostCapExceeded` if ``trace_id`` has consumed
        :data:`VISION_SCREENSHOTS_HARD_CAP` or more screenshots.

        Must be called by the computer-use worker BEFORE each
        OmniParser invocation so the cap is enforced as a clean
        mission-failure (reason = ``"vision_cap_exceeded"``) rather
        than letting the worker burn an extra frame past the cap.
        """
        current = self.vision_screenshots_for(trace_id)
        if current >= VISION_SCREENSHOTS_HARD_CAP:
            raise CostCapExceeded(trace_id, current)

    # ---- Cooldown ----

    def is_in_cooldown(self) -> bool:
        now_ns = self._now_ns()
        if not self._cooldown.is_active(now_ns):
            if self._cooldown.until_ns > 0:
                # Cooldown was active and has now expired → emit event + reset.
                self._cooldown = CooldownState()
                self._cooldown.save(self._cooldown_path)
                self._publish(CooldownEnded())
            return False
        return True

    def start_cooldown(self, reason: str) -> None:
        until_ns = self._now_ns() + self._config.cooldown_minutes * 60 * 1_000_000_000
        self._cooldown = CooldownState(until_ns=until_ns, reason=reason)
        self._cooldown.save(self._cooldown_path)
        self._publish(CooldownStarted(until_ns=until_ns, reason=reason))

    @property
    def cooldown_until_ns(self) -> int:
        return self._cooldown.until_ns

    # ---- Internal helpers ----

    def _usd_to_eur(self, usd: float) -> float:
        return usd * self._config.eur_per_usd

    def _maybe_warn(self, trace_id: UUID) -> None:
        if not self._config.enabled:
            return
        task_eur = self.total_for(trace_id)
        if (
            trace_id not in self._task_warned
            and task_eur >= self._config.per_task_eur * self._config.warn_at_fraction
            and task_eur <= self._config.per_task_eur
        ):
            self._task_warned.add(trace_id)
            self._publish(BudgetWarning(
                scope="task", spent_eur=task_eur, limit_eur=self._config.per_task_eur,
            ))
        day_eur = self.total_today()
        if (
            not self._daily_warned
            and day_eur >= self._config.per_day_eur * self._config.warn_at_fraction
            and day_eur <= self._config.per_day_eur
        ):
            self._daily_warned = True
            self._publish(BudgetWarning(
                scope="daily", spent_eur=day_eur, limit_eur=self._config.per_day_eur,
            ))

    def _maybe_trip(self, trace_id: UUID) -> None:
        """Check both budgets; cancel the token via KillSwitch.trip() if either is exceeded."""
        if not self._config.enabled:
            return
        if self.over_task_budget(trace_id):
            self._publish(BudgetExceeded(
                scope="task",
                spent_eur=self.total_for(trace_id),
                limit_eur=self._config.per_task_eur,
            ))
            self._trigger_kill("budget_task_exceeded")
            return
        if self.over_daily_budget():
            self._publish(BudgetExceeded(
                scope="daily",
                spent_eur=self.total_today(),
                limit_eur=self._config.per_day_eur,
            ))
            self.start_cooldown("budget_daily_exceeded")
            self._trigger_kill("budget_daily_exceeded")

    def _trigger_kill(self, reason: str) -> None:
        """Cancel all active tokens. Preferred path: KillSwitch.trip() on the
        running loop; fallback: synchronous Token.cancel().

        H11 fix: uses `asyncio.get_running_loop()` (stable API) instead of
        `get_event_loop()` (deprecated and returns a fresh loop on some code
        paths). No `asyncio.run()` fallback — that crashes when called from
        another thread inside a running loop.
        """
        if self._kill_switch is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(self._kill_switch.trip(reason=reason))
            return
        # No loop is running in this thread — cancel tokens synchronously.
        # The KillSwitch.trip() side-path with ack_bus etc. is skipped,
        # but the primary goal (budget stop) is achieved.
        for token, _holder in self._kill_switch.active_tokens():
            token.cancel(reason)

    def _publish(self, event) -> None:  # type: ignore[no-untyped-def]
        """H11 fix: only publish the event when a loop is running in the current
        thread. Silently discard in the fallback — events are telemetry;
        the budget gate itself already blocks via CancelToken.
        """
        if self._bus is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._bus.publish(event))
