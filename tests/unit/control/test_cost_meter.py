"""Unit-Tests fuer CostMeter (ADR-0006)."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.control import (
    BudgetConfig,
    CancelScope,
    CostMeter,
    KillSwitch,
    ModelPrice,
)
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    BudgetExceeded,
    BudgetWarning,
    CooldownEnded,
    CooldownStarted,
)
from jarvis.core.protocols import CostRecord


def _make_config(**overrides) -> BudgetConfig:
    base = {
        "enabled": True,
        "per_task_eur": 2.0,
        "per_day_eur": 30.0,
        "cooldown_minutes": 60,
        "warn_at_fraction": 0.8,
        "eur_per_usd": 1.0,                 # 1:1 fuer einfachere Arithmetik
        "prices": {
            "test-model": ModelPrice(
                usd_per_1m_input=1.0,
                usd_per_1m_output=5.0,
            ),
        },
    }
    base.update(overrides)
    return BudgetConfig(**base)


def _make_meter(tmp_path: Path, config: BudgetConfig, **kwargs) -> CostMeter:
    return CostMeter(
        config=config,
        db_path=tmp_path / "jarvis.db",
        cooldown_path=tmp_path / "cost_cooldown.json",
        **kwargs,
    )


def _record(trace_id, usd: float, provider="claude-api", model="test-model") -> CostRecord:
    return CostRecord(
        trace_id=trace_id, provider=provider, model=model,
        tokens_in=1000, tokens_out=200, tokens_cache_hit=0,
        usd=usd, timestamp_ns=time.time_ns(),
    )


# ---------------------------------------------------------------------
# Basic tracking
# ---------------------------------------------------------------------

def test_protocol_structural_match(tmp_path):
    from jarvis.core.protocols import CostMeter as CostMeterProto
    meter = _make_meter(tmp_path, _make_config())
    assert isinstance(meter, CostMeterProto)


def test_start_and_add_accumulates_per_trace(tmp_path):
    meter = _make_meter(tmp_path, _make_config())
    tid = uuid4()
    meter.start(tid, "claude-api", "test-model")
    meter.add(_record(tid, 0.5))
    meter.add(_record(tid, 0.3))
    assert meter.total_for(tid) == pytest.approx(0.8)


def test_disabled_config_never_trips(tmp_path):
    meter = _make_meter(tmp_path, _make_config(enabled=False))
    tid = uuid4()
    meter.start(tid, "claude-api", "test-model")
    meter.add(_record(tid, 100.0))                     # massiv ueber jedem Limit
    assert meter.over_task_budget(tid) is False
    assert meter.over_daily_budget() is False


def test_close_removes_trace_summary(tmp_path):
    meter = _make_meter(tmp_path, _make_config())
    tid = uuid4()
    meter.start(tid, "claude-api", "test-model")
    meter.add(_record(tid, 0.5))
    meter.close(tid)
    assert meter.total_for(tid) == 0.0


# ---------------------------------------------------------------------
# Budget-Trip + Events
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_budget_trip_fires_exceeded_and_cancels_token(tmp_path):
    bus = EventBus()
    ks = KillSwitch()
    meter = _make_meter(tmp_path, _make_config(), bus=bus, kill_switch=ks)

    events: list = []
    async def capture(ev):
        events.append(ev)
    bus.subscribe(BudgetExceeded, capture)
    bus.subscribe(BudgetWarning, capture)

    async with CancelScope(ks, holder="brain_stream") as token:
        tid = uuid4()
        meter.start(tid, "claude-api", "test-model")
        meter.add(_record(tid, 2.5))                   # direkt ueber per_task_eur=2.0
        # Events sind async dispatched
        for _ in range(5):
            await asyncio.sleep(0)

    kinds = [type(ev).__name__ for ev in events]
    assert "BudgetExceeded" in kinds
    exceeded = next(ev for ev in events if isinstance(ev, BudgetExceeded))
    assert exceeded.scope == "task"
    assert token.is_cancelled()
    assert token.reason == "budget_task_exceeded"


@pytest.mark.asyncio
async def test_warning_at_80_percent(tmp_path):
    bus = EventBus()
    meter = _make_meter(tmp_path, _make_config(), bus=bus)
    warnings: list[BudgetWarning] = []
    async def capture(ev: BudgetWarning) -> None:
        warnings.append(ev)
    bus.subscribe(BudgetWarning, capture)

    tid = uuid4()
    meter.start(tid, "claude-api", "test-model")
    meter.add(_record(tid, 1.7))                       # 85% von 2.0
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(warnings) == 1
    assert warnings[0].scope == "task"
    assert warnings[0].spent_eur == pytest.approx(1.7)


def test_warning_not_repeated_same_trace(tmp_path):
    meter = _make_meter(tmp_path, _make_config())
    tid = uuid4()
    meter.start(tid, "claude-api", "test-model")
    # Fuegen wir 1.7 EUR, dann 0.1 EUR hinzu — Warning darf NUR einmal feuern.
    meter.add(_record(tid, 1.7))
    first_warned = tid in meter._task_warned  # type: ignore[attr-defined]
    meter.add(_record(tid, 0.1))
    assert first_warned
    assert meter.total_for(tid) == pytest.approx(1.8)
    # Kein trip, weil unter 2.0
    assert not meter.over_task_budget(tid)


# ---------------------------------------------------------------------
# Daily budget + Cooldown
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_budget_trip_starts_cooldown(tmp_path):
    bus = EventBus()
    ks = KillSwitch()
    meter = _make_meter(
        tmp_path, _make_config(per_day_eur=1.0, per_task_eur=100.0),
        bus=bus, kill_switch=ks,
    )
    starts: list[CooldownStarted] = []
    async def capture(ev: CooldownStarted) -> None:
        starts.append(ev)
    bus.subscribe(CooldownStarted, capture)

    tid = uuid4()
    meter.start(tid, "claude-api", "test-model")
    meter.add(_record(tid, 1.5))                       # direkt ueber daily
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(starts) == 1
    assert starts[0].reason == "budget_daily_exceeded"
    assert meter.is_in_cooldown()


@pytest.mark.asyncio
async def test_cooldown_expires_and_fires_ended_event(tmp_path):
    bus = EventBus()
    fake_now = [time.time_ns()]
    meter = _make_meter(
        tmp_path, _make_config(cooldown_minutes=1),
        bus=bus, now_ns=lambda: fake_now[0],
    )
    ends: list[CooldownEnded] = []
    async def capture(_ev: CooldownEnded) -> None:
        ends.append(_ev)
    bus.subscribe(CooldownEnded, capture)

    meter.start_cooldown("test")
    assert meter.is_in_cooldown()

    # Zeit 2 Minuten vorspulen
    fake_now[0] += 2 * 60 * 1_000_000_000
    assert not meter.is_in_cooldown()
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(ends) == 1


def test_cooldown_persists_across_restart(tmp_path):
    cfg = _make_config()
    meter1 = _make_meter(tmp_path, cfg)
    meter1.start_cooldown("persist_test")

    meter2 = _make_meter(tmp_path, cfg)            # neuer Meter, gleicher Pfad
    assert meter2.is_in_cooldown()
    assert meter2.cooldown_until_ns == meter1.cooldown_until_ns


# ---------------------------------------------------------------------
# Persistence (cost_ledger)
# ---------------------------------------------------------------------

def test_ledger_persists_on_close(tmp_path):
    import sqlite3
    meter = _make_meter(tmp_path, _make_config())
    tid = uuid4()
    meter.start(tid, "claude-api", "test-model")
    meter.add(_record(tid, 0.42))
    meter.close(tid)

    with sqlite3.connect(tmp_path / "jarvis.db") as conn:
        row = conn.execute(
            "SELECT cost_usd FROM cost_ledger WHERE provider=? AND model=?",
            ("claude-api", "test-model"),
        ).fetchone()
    assert row is not None
    assert row[0] == pytest.approx(0.42)


def test_ledger_loads_today_total_on_init(tmp_path):
    import sqlite3
    # Erst einen Eintrag von heute in die DB setzen
    (tmp_path / "").mkdir(exist_ok=True)
    db_path = tmp_path / "jarvis.db"
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cost_ledger (
                day TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                tokens_in INTEGER NOT NULL DEFAULT 0,
                tokens_out INTEGER NOT NULL DEFAULT 0,
                tokens_cache_hit INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL NOT NULL DEFAULT 0.0,
                PRIMARY KEY (day, provider, model)
            );
            """,
        )
        conn.execute(
            "INSERT INTO cost_ledger VALUES (?, ?, ?, ?, ?, ?, ?)",
            (today, "claude-api", "test-model", 1000, 200, 0, 5.0),
        )
        conn.commit()

    meter = _make_meter(tmp_path, _make_config())
    assert meter.total_today() == pytest.approx(5.0)


# ---------------------------------------------------------------------
# Pricing helper
# ---------------------------------------------------------------------

def test_estimate_usd_computes_linearly():
    prices = {"test-model": ModelPrice(usd_per_1m_input=1.0, usd_per_1m_output=5.0)}
    usd = BudgetConfig.estimate_usd(prices, "test-model",
                                    tokens_in=1_000_000, tokens_out=1_000_000)
    assert usd == pytest.approx(6.0)


def test_estimate_usd_unknown_model_returns_zero():
    usd = BudgetConfig.estimate_usd({}, "unknown", tokens_in=1, tokens_out=1)
    assert usd == 0.0
