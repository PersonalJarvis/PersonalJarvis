"""Unit-Tests für CacheHeartbeat."""
from __future__ import annotations

import asyncio

import pytest

from jarvis.brain import CacheHeartbeat


@pytest.mark.asyncio
async def test_heartbeat_fires_periodically():
    counter = {"n": 0}

    async def probe():
        counter["n"] += 1

    hb = CacheHeartbeat(interval_s=0.05, probe=probe)
    hb.start()
    await asyncio.sleep(0.18)  # erwartet ~3 ticks
    await hb.stop()
    assert counter["n"] >= 2


@pytest.mark.asyncio
async def test_heartbeat_stops_cleanly():
    counter = {"n": 0}

    async def probe():
        counter["n"] += 1

    hb = CacheHeartbeat(interval_s=0.05, probe=probe)
    hb.start()
    await asyncio.sleep(0.07)
    await hb.stop()
    mid = counter["n"]
    await asyncio.sleep(0.12)
    # Nach stop keine neuen Ticks mehr
    assert counter["n"] == mid


@pytest.mark.asyncio
async def test_heartbeat_probe_failures_are_swallowed():
    counter = {"n": 0}

    async def probe():
        counter["n"] += 1
        if counter["n"] == 1:
            raise RuntimeError("simulated failure")

    hb = CacheHeartbeat(interval_s=0.04, probe=probe)
    hb.start()
    await asyncio.sleep(0.15)
    await hb.stop()
    # Loop darf nach fehlerhaftem probe weiterlaufen
    assert counter["n"] >= 2
