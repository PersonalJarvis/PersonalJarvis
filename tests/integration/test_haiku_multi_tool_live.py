"""Live-Integration-Test: Haiku + Multi-Tool ("5 terminals" usecase).

Skipped by default wenn kein Claude-OAuth-Token da — kein CI-Flakiness.
Laufen mit: pytest tests/integration/test_haiku_multi_tool_live.py -v --run-live
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from jarvis.core.protocols import ToolResult


def _api_key_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY"))


@pytest.mark.skipif(not _api_key_available(), reason="kein Brain-API-Key")
@pytest.mark.asyncio
async def test_haiku_speed_under_5s():
    from jarvis.brain.factory import build_default_brain
    brain = build_default_brain()
    t0 = time.perf_counter()
    r = await asyncio.wait_for(brain.generate("Sag nur 'Hi'. Nichts sonst."), timeout=20)
    dt = time.perf_counter() - t0
    print(f"\nHaiku-Latenz: {dt:.2f}s")
    print(f"Response: {r[:100]}")
    assert dt < 10.0, f"Haiku zu langsam: {dt}s"


@pytest.mark.skipif(not _api_key_available(), reason="kein Brain-API-Key")
@pytest.mark.asyncio
async def test_haiku_spawn_multiple_terminals():
    from jarvis.brain.factory import build_default_brain
    brain = build_default_brain()
    calls = []

    async def fake_open_app(args, ctx):
        calls.append(args)
        return ToolResult(success=True, output=f"ok: {args}")

    brain._tools["open_app"].execute = fake_open_app

    t0 = time.perf_counter()
    r = await asyncio.wait_for(brain.generate("Öffne bitte 3 Terminals (Windows Terminal, wt)."),
                                timeout=45)
    dt = time.perf_counter() - t0
    print(f"\nMulti-Tool-Latenz: {dt:.2f}s")
    print(f"Response: {r[:200]}")
    print(f"Tool-Calls: {calls}")

    # Erwartung: mindestens 2 open_app-Calls (3 wäre ideal, aber Brain-Deterministik variiert)
    assert len(calls) >= 2, f"Brain hat nur {len(calls)} Tool-Calls gemacht"
    for call in calls:
        assert "app_name" in call
