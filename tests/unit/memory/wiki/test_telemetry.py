"""Unit tests for ``jarvis.memory.wiki.telemetry`` (B8.7).

The telemetry singleton must be:

* thread-safe under concurrent ``inc()`` from multiple workers,
* stable in its counter set (defaults must survive reset),
* cheaply queryable via ``snapshot()``,
* cancellable when the hourly-summary loop is stopped.

These tests exercise an isolated :class:`MemoryTelemetry` instance per
case to avoid leaking state into the module-level singleton.
"""
from __future__ import annotations

import asyncio
import logging
import threading

import pytest

from jarvis.memory.wiki.telemetry import (
    DEFAULT_COUNTERS,
    MemoryTelemetry,
    format_summary,
    get_telemetry,
    run_hourly_summary_loop,
)


# ---------------------------------------------------------------------------
# Default counter set
# ---------------------------------------------------------------------------


def test_default_counters_registered_at_zero() -> None:
    """All eight named counters must appear in the first snapshot, value 0."""

    t = MemoryTelemetry()
    snap = t.snapshot()
    for name in DEFAULT_COUNTERS:
        assert name in snap, f"missing default counter: {name}"
        assert snap[name] == 0, f"default counter {name} should start at 0"


def test_snapshot_is_a_copy_not_a_view() -> None:
    """Mutating the returned dict must NOT affect the store."""

    t = MemoryTelemetry()
    snap = t.snapshot()
    snap["voice_turns_seen"] = 999
    assert t.get("voice_turns_seen") == 0


def test_snapshot_keys_are_alphabetically_sorted() -> None:
    """JSON-stable ordering: the snapshot keys come out sorted."""

    t = MemoryTelemetry()
    t.inc("zzz")
    t.inc("aaa")
    snap = t.snapshot()
    keys = list(snap.keys())
    assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# Increment semantics
# ---------------------------------------------------------------------------


def test_inc_increments_by_one_by_default() -> None:
    t = MemoryTelemetry()
    t.inc("voice_turns_seen")
    t.inc("voice_turns_seen")
    t.inc("voice_turns_seen")
    assert t.get("voice_turns_seen") == 3


def test_inc_with_amount() -> None:
    """``inc(name, amount=N)`` adds N; ``amount=0`` is a no-op."""

    t = MemoryTelemetry()
    t.inc("wiki_pages_created", amount=5)
    t.inc("wiki_pages_created", amount=0)  # no-op
    assert t.get("wiki_pages_created") == 5


def test_inc_auto_registers_unknown_counter() -> None:
    """An unfamiliar name must auto-register at 0 + amount, no error."""

    t = MemoryTelemetry()
    t.inc("custom.counter.example")
    assert t.get("custom.counter.example") == 1
    assert "custom.counter.example" in t.snapshot()


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_inc_is_thread_safe_under_contention() -> None:
    """Ten worker threads doing 1 000 inc()s each end at exactly 10 000."""

    t = MemoryTelemetry()
    workers = 10
    per_worker = 1_000

    def _worker() -> None:
        for _ in range(per_worker):
            t.inc("voice_turns_seen")

    threads = [threading.Thread(target=_worker) for _ in range(workers)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert t.get("voice_turns_seen") == workers * per_worker


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_reset_zeros_known_counters_and_keeps_keys() -> None:
    t = MemoryTelemetry()
    t.inc("voice_turns_seen", amount=7)
    t.inc("custom.thing")
    t.reset()
    snap = t.snapshot()
    # All counters back to zero...
    assert all(v == 0 for v in snap.values())
    # ...but the keys still appear (registered counters survive).
    assert "voice_turns_seen" in snap
    assert "custom.thing" in snap


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


def test_get_telemetry_returns_module_singleton() -> None:
    """``get_telemetry()`` returns the same instance on every call."""

    a = get_telemetry()
    b = get_telemetry()
    assert a is b


# ---------------------------------------------------------------------------
# format_summary
# ---------------------------------------------------------------------------


def test_format_summary_renders_key_value_pairs() -> None:
    rendered = format_summary({"voice_turns_seen": 3, "wiki_pages_created": 1})
    assert "voice_turns_seen=3" in rendered
    assert "wiki_pages_created=1" in rendered


# ---------------------------------------------------------------------------
# Hourly summary loop (cancellation contract)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hourly_summary_loop_emits_one_log_line_per_tick(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The loop logs a single ``wiki_telemetry hourly: ...`` line per tick.

    We drive it with a 50 ms interval and stop after two ticks.
    """

    instance = MemoryTelemetry()
    instance.inc("voice_turns_seen", amount=4)

    with caplog.at_level(logging.INFO, logger="jarvis.memory.wiki.telemetry"):
        task = asyncio.create_task(
            run_hourly_summary_loop(interval_seconds=0.05, instance=instance),
        )
        # Two ticks should comfortably land inside 200 ms.
        await asyncio.sleep(0.18)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    summary_lines = [
        rec for rec in caplog.records
        if "wiki_telemetry hourly" in rec.getMessage()
    ]
    assert len(summary_lines) >= 2, (
        f"expected at least 2 hourly summary lines, got {len(summary_lines)}"
    )
    # And the snapshot we incremented must appear in the rendered text.
    assert any("voice_turns_seen=4" in r.getMessage() for r in summary_lines)


@pytest.mark.asyncio
async def test_hourly_summary_loop_propagates_cancel() -> None:
    """Cancelling the task surfaces a clean ``CancelledError`` and stops."""

    task = asyncio.create_task(run_hourly_summary_loop(interval_seconds=10))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
