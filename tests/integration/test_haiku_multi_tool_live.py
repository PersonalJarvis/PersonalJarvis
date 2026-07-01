"""Live integration test: Haiku + multi-tool ("5 terminals" use case).

Skipped by default when there's no Claude OAuth token — no CI flakiness.
Run with: pytest tests/integration/test_haiku_multi_tool_live.py -v --run-live
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

def _api_key_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY"))


@pytest.mark.voice_latency
@pytest.mark.skipif(not _api_key_available(), reason="no brain API key")
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


@pytest.mark.voice_latency
@pytest.mark.skipif(not _api_key_available(), reason="no brain API key")
@pytest.mark.asyncio
async def test_haiku_spawn_multiple_terminals():
    """Latency test: how long does the brain need for a complex multi-task request?

    The result can be a tool call or an explanatory answer — the point is
    that the brain answers within the deadline. (open_app used to be the
    assertion anchor in older builds; the tool was removed — now pure latency.)
    """
    from jarvis.brain.factory import build_default_brain
    brain = build_default_brain()

    t0 = time.perf_counter()
    r = await asyncio.wait_for(
        brain.generate("Öffne bitte 3 Terminals (Windows Terminal, wt)."),  # i18n-allow
        timeout=45,
    )
    dt = time.perf_counter() - t0
    print(f"\nMulti-Request-Latenz: {dt:.2f}s")
    print(f"Response: {r[:200]}")
    assert r, "brain must return a response"
    assert dt < 30.0, f"Brain zu langsam: {dt}s"
