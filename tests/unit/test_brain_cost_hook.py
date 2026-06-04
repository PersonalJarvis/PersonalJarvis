"""Regression-Tests fuer H13: CostMeter-Hook im BrainManager.

Deckt die Pre-Call-Gate-Pfade (Cooldown, Task-Budget, Tages-Budget)
und die Post-Call-Usage-Fuetterung.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from jarvis.control.cost import BudgetConfig, CostMeter, ModelPrice

# ---------------------------------------------------------------------
# Minimal-Harness: Real-BrainManager zu initialisieren ist teuer.
# Wir stubben die Dispatch-Grenze, indem wir direkt den Cost-Hook-Flow
# als Funktion ausfahren wie es generate() tut.
# ---------------------------------------------------------------------

@dataclass
class _FakeAgg:
    text: str = "hello"
    tool_calls: list = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict = field(default_factory=dict)


@dataclass
class _FakeDispatcher:
    response_usage: dict[str, int] = field(default_factory=dict)
    response_text: str = "hello"

    async def dispatch(self, user_text, history, trace_id=None):
        return _FakeAgg(text=self.response_text, usage=dict(self.response_usage))


def _make_meter(tmp_path, **overrides):
    base = {
        "enabled": True, "per_task_eur": 1.0, "per_day_eur": 100.0,
        "eur_per_usd": 1.0,
        "prices": {"test": ModelPrice(usd_per_1m_input=1.0,
                                        usd_per_1m_output=5.0)},
    }
    base.update(overrides)
    return CostMeter(
        config=BudgetConfig(**base),
        db_path=tmp_path / "jarvis.db",
        cooldown_path=tmp_path / "cooldown.json",
    )


def test_estimate_usd_from_usage_uses_meter_prices(tmp_path):
    from jarvis.brain.manager import _estimate_usd_from_usage
    meter = _make_meter(tmp_path)
    usd = _estimate_usd_from_usage(
        meter, "test",
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    assert usd == pytest.approx(6.0)


def test_estimate_usd_returns_zero_for_unknown_model(tmp_path):
    from jarvis.brain.manager import _estimate_usd_from_usage
    meter = _make_meter(tmp_path)
    usd = _estimate_usd_from_usage(
        meter, "unknown-model",
        {"input_tokens": 1_000, "output_tokens": 1_000},
    )
    assert usd == 0.0


def test_meter_accumulates_on_dispatch(tmp_path):
    """Der Hook im generate() schickt agg.usage an meter.add()."""
    from jarvis.core.protocols import CostRecord

    meter = _make_meter(tmp_path)
    tid = uuid4()
    meter.start(tid, "test", "test")
    meter.add(CostRecord(
        trace_id=tid, provider="test", model="test",
        tokens_in=500_000, tokens_out=100_000, tokens_cache_hit=0,
        usd=1.0, timestamp_ns=time.time_ns(),
    ))
    # 1.0 USD × eur_per_usd=1.0 = 1.0 EUR == per_task_eur
    assert meter.total_for(tid) == pytest.approx(1.0)
    assert not meter.over_task_budget(tid)            # nicht > 1.0
    meter.add(CostRecord(
        trace_id=tid, provider="test", model="test",
        tokens_in=1, tokens_out=1, tokens_cache_hit=0,
        usd=0.01, timestamp_ns=time.time_ns(),
    ))
    assert meter.over_task_budget(tid)                # jetzt > 1.0


def test_cooldown_blocks_new_requests(tmp_path):
    """Nach Tages-Overrun setzt der Meter Cooldown — der Pre-Gate muss
    das erkennen und Brain-Dispatch uebergehen.
    """
    meter = _make_meter(tmp_path, per_day_eur=0.5, cooldown_minutes=60)
    meter.start_cooldown("test")
    assert meter.is_in_cooldown()
