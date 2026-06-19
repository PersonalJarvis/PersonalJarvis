"""Contract-Tests — jede CostMeter-Implementierung erfuellt das Protocol."""
from __future__ import annotations

import contextlib
import time
from uuid import uuid4

import pytest

from jarvis.core.protocols import CostMeter, CostRecord
from tests.fixtures.control.fake_cost_meter import FakeCostMeter


def _get_meters() -> list[CostMeter]:
    meters: list[CostMeter] = [FakeCostMeter(over_task_after_usd=1.0,
                                             over_daily_after_usd=10.0)]
    with contextlib.suppress(Exception):
        from jarvis.control.cost import CostMeter as ProdMeter  # type: ignore[attr-defined]
        meters.append(ProdMeter())
    return meters


@pytest.mark.parametrize("meter", _get_meters(), ids=lambda m: m.name)
def test_cost_meter_structurally_matches_protocol(meter):
    assert isinstance(meter, CostMeter)


def test_fake_cost_meter_task_budget_trip():
    meter = FakeCostMeter(over_task_after_usd=1.0)
    tid = uuid4()
    meter.start(tid, "claude-api", "haiku-4-5")
    for _ in range(2):
        meter.add(CostRecord(
            trace_id=tid, provider="claude-api", model="haiku-4-5",
            tokens_in=1000, tokens_out=500, tokens_cache_hit=0,
            usd=0.6, timestamp_ns=time.time_ns(),
        ))
    assert meter.over_task_budget(tid)          # 1.2 > 1.0
    assert meter.total_for(tid) == pytest.approx(1.2)


def test_fake_cost_meter_daily_budget_trip():
    meter = FakeCostMeter(over_daily_after_usd=2.0)
    for _ in range(3):
        tid = uuid4()
        meter.start(tid, "claude-api", "haiku-4-5")
        meter.add(CostRecord(
            trace_id=tid, provider="claude-api", model="haiku-4-5",
            tokens_in=1, tokens_out=1, tokens_cache_hit=0,
            usd=1.0, timestamp_ns=time.time_ns(),
        ))
    assert meter.over_daily_budget()            # 3.0 > 2.0
