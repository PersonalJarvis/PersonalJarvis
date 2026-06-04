"""Unit tests for the vision cost sub-bucket on :class:`CostMeter`.

Covers: record_vision monotonic accumulation, check_vision_cap raising at
the hard cap, screenshot/token isolation per trace, and reset-on-close.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from jarvis.control.cost import (
    VISION_SCREENSHOTS_HARD_CAP,
    BudgetConfig,
    CostCapExceeded,
    CostMeter,
)


@pytest.fixture
def meter(tmp_path: Path) -> CostMeter:
    return CostMeter(
        config=BudgetConfig(enabled=False),
        db_path=tmp_path / "cost.db",
        cooldown_path=tmp_path / "cooldown.json",
    )


def test_record_vision_accumulates(meter: CostMeter) -> None:
    trace = uuid4()
    meter.record_vision(trace, tokens=128, screenshots=1)
    meter.record_vision(trace, tokens=64, screenshots=2)
    assert meter.vision_tokens_for(trace) == 192
    assert meter.vision_screenshots_for(trace) == 3


def test_record_vision_traces_isolated(meter: CostMeter) -> None:
    a = uuid4()
    b = uuid4()
    meter.record_vision(a, tokens=10, screenshots=5)
    meter.record_vision(b, tokens=20, screenshots=2)
    assert meter.vision_tokens_for(a) == 10
    assert meter.vision_screenshots_for(a) == 5
    assert meter.vision_tokens_for(b) == 20
    assert meter.vision_screenshots_for(b) == 2


def test_record_vision_rejects_negative(meter: CostMeter) -> None:
    trace = uuid4()
    with pytest.raises(ValueError):
        meter.record_vision(trace, tokens=-1, screenshots=0)
    with pytest.raises(ValueError):
        meter.record_vision(trace, tokens=0, screenshots=-1)


def test_check_vision_cap_does_not_raise_below_cap(meter: CostMeter) -> None:
    trace = uuid4()
    meter.record_vision(trace, screenshots=VISION_SCREENSHOTS_HARD_CAP - 1)
    # 39 screenshots — the NEXT call would be the 40th, still allowed
    # (cap is the bound — check raises only when current count >= cap).
    meter.check_vision_cap(trace)  # must not raise


def test_check_vision_cap_raises_at_cap(meter: CostMeter) -> None:
    trace = uuid4()
    meter.record_vision(trace, screenshots=VISION_SCREENSHOTS_HARD_CAP)
    with pytest.raises(CostCapExceeded) as excinfo:
        meter.check_vision_cap(trace)
    assert excinfo.value.trace_id == trace
    assert excinfo.value.screenshots == VISION_SCREENSHOTS_HARD_CAP


def test_check_vision_cap_carries_trace_and_count(meter: CostMeter) -> None:
    trace = uuid4()
    meter.record_vision(trace, screenshots=VISION_SCREENSHOTS_HARD_CAP + 5)
    try:
        meter.check_vision_cap(trace)
    except CostCapExceeded as exc:
        assert exc.trace_id == trace
        assert exc.screenshots == VISION_SCREENSHOTS_HARD_CAP + 5
        # The repr must include both pieces so the worker's log line is
        # self-describing without extra interpolation.
        rendered = str(exc)
        assert trace.hex in rendered
        assert str(VISION_SCREENSHOTS_HARD_CAP) in rendered
    else:
        pytest.fail("CostCapExceeded not raised")


def test_close_resets_vision_buckets(meter: CostMeter) -> None:
    trace = uuid4()
    meter.record_vision(trace, tokens=100, screenshots=10)
    assert meter.vision_screenshots_for(trace) == 10
    meter.close(trace)
    # After close the trace is forgotten — a fresh check returns 0.
    assert meter.vision_screenshots_for(trace) == 0
    assert meter.vision_tokens_for(trace) == 0
    # And the cap-check passes again for that trace id.
    meter.check_vision_cap(trace)


def test_vision_bucket_does_not_leak_into_main_ledger(meter: CostMeter) -> None:
    """The vision sub-bucket is SEPARATE from the main token ledger —
    record_vision must not bump task_total_usd or daily_total."""
    trace = uuid4()
    meter.start(trace, provider="computer_use", model="gemini-3.1-pro-preview")
    meter.record_vision(trace, tokens=1000, screenshots=10)
    assert meter.total_for(trace) == 0.0
    assert meter.total_today() == 0.0
