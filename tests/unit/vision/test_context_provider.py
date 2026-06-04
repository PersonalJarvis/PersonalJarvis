"""Unit-Tests fuer VisionContextProvider.

Verifiziert Background-Refresh, Force-Refresh, Pause/Resume, Loop-
Resilience gegen Exceptions und Clean-Shutdown-Timing.
"""
from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import pytest

from jarvis.core.protocols import Observation
from jarvis.vision.context_provider import VisionContextProvider, VisionPaused


def _make_obs(hash_: str = "abc", *, timestamp_ns: int | None = None) -> Observation:
    return Observation(
        trace_id=uuid4(),
        timestamp_ns=timestamp_ns if timestamp_ns is not None else time.time_ns(),
        screenshot_path=None,
        screenshot_hash=hash_,
        nodes=(),
        window_title="test",
        active_pid=0,
        source="screenshot_only",
        pruning_stats={},
    )


class FakeEngine:
    """Minimaler VisionEngine-Ersatz fuer Provider-Tests."""

    def __init__(self, *, raise_once: bool = False) -> None:
        self.calls = 0
        self.raise_once = raise_once
        self.last_mode: str | None = None

    async def observe(self, *, mode: str = "auto", **kwargs):
        self.calls += 1
        self.last_mode = mode
        if self.raise_once and self.calls == 1:
            raise RuntimeError("boom")
        return _make_obs(f"hash-{self.calls}")


@pytest.mark.asyncio
async def test_provider_holds_fresh_observation():
    """Background-Loop fuellt den Cache, current() liefert ohne Engine-Call."""
    engine = FakeEngine()
    prov = VisionContextProvider(engine, refresh_interval_s=0.05, max_staleness_s=2.0)
    await prov.start()
    try:
        await asyncio.sleep(0.15)  # >= 2 loop-iterations
        calls_after_loop = engine.calls
        assert calls_after_loop >= 1
        obs = await prov.current()
        assert obs is not None
        assert obs.screenshot_hash.startswith("hash-")
        # current() sollte NICHT zusaetzlich observen - Cache ist frisch.
        assert engine.calls == calls_after_loop
    finally:
        await prov.stop()


@pytest.mark.asyncio
async def test_provider_force_refresh_always_captures():
    """force_refresh=True erzwingt einen Engine-Call, auch bei frischem Cache."""
    engine = FakeEngine()
    prov = VisionContextProvider(
        engine, refresh_interval_s=10.0, max_staleness_s=10.0
    )
    await prov.start()
    try:
        # Warten bis mindestens ein Loop-Observe lief.
        for _ in range(20):
            if engine.calls >= 1:
                break
            await asyncio.sleep(0.01)
        before = engine.calls
        await prov.current(force_refresh=True)
        assert engine.calls == before + 1
    finally:
        await prov.stop()


@pytest.mark.asyncio
async def test_provider_pause_resume():
    """pause() blockt current(), resume() laesst es wieder durch."""
    engine = FakeEngine()
    prov = VisionContextProvider(engine, refresh_interval_s=0.05)
    await prov.start()
    try:
        prov.pause()
        assert prov.is_paused is True
        with pytest.raises(VisionPaused):
            await prov.current()
        prov.resume()
        assert prov.is_paused is False
        obs = await prov.current(force_refresh=True)
        assert obs is not None
    finally:
        await prov.stop()


@pytest.mark.asyncio
async def test_provider_loop_survives_exception():
    """Exception im observe() killt den Loop nicht - retry auf naechster Tick."""
    engine = FakeEngine(raise_once=True)
    prov = VisionContextProvider(engine, refresh_interval_s=0.05)
    await prov.start()
    try:
        # Genug Zeit fuer mindestens 2 Tick-Versuche (erster raised, zweiter ok).
        for _ in range(40):
            if engine.calls >= 2:
                break
            await asyncio.sleep(0.025)
        assert engine.calls >= 2
        assert prov.is_running is True  # Loop lebt noch
    finally:
        await prov.stop()


@pytest.mark.asyncio
async def test_provider_clean_shutdown_under_500ms():
    """stop() cancelt den Task und kehrt in <500ms zurueck."""
    engine = FakeEngine()
    prov = VisionContextProvider(engine, refresh_interval_s=10.0)
    await prov.start()
    await asyncio.sleep(0.05)
    t0 = time.perf_counter()
    await prov.stop()
    dt_ms = (time.perf_counter() - t0) * 1000
    assert dt_ms < 500, f"stop() dauerte {dt_ms:.0f}ms"
    assert not prov.is_running
