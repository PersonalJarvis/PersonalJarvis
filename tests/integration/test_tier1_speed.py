"""Tier-1 latency test: live brain with new settings.

Measures:
- Brain latency with max_retries=0 (should fall back immediately on 429)
- Multi-tool-call latency
- RateLimitTracker behavior under a simulated 429
"""
from __future__ import annotations

import asyncio
import time

import pytest

def _api_key_available() -> bool:
    import os
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY"))


def _oauth_available() -> bool:
    # Claude OAuth Max plan via the claude-api provider; currently uses the
    # same env marker as _api_key_available, until a more specific OAuth
    # token check is needed.
    return _api_key_available()


@pytest.mark.voice_latency
@pytest.mark.skipif(not _api_key_available(), reason="no Brain API key")
@pytest.mark.asyncio
async def test_latency_simple_answer():
    from jarvis.brain.factory import build_default_brain
    brain = build_default_brain()
    t0 = time.perf_counter()
    r = await asyncio.wait_for(brain.generate("Sag 'Hi'."), timeout=15)
    dt = time.perf_counter() - t0
    print(f"\nTier-1 Haiku-Latenz: {dt:.2f}s")
    print(f"Response: {r[:80]}")
    assert dt < 8.0, f"Zu langsam: {dt}s"


@pytest.mark.voice_latency
@pytest.mark.skipif(not _api_key_available(), reason="no Brain API key")
@pytest.mark.asyncio
async def test_rate_limit_tracker_skips_bad_provider():
    from jarvis.brain.factory import build_default_brain
    brain = build_default_brain()

    # Markiere Haiku als rate-limited
    brain._rate_tracker.mark_rate_limited("claude-api", "claude-haiku-4-5-20251001")

    t0 = time.perf_counter()
    r = await asyncio.wait_for(brain.generate("Sag 'Hi'."), timeout=30)
    dt = time.perf_counter() - t0
    print(f"\nMit haiku rate-limited: {dt:.2f}s → {r[:80]}")
    # Should fall back to Opus (deep_model), still sub-15s
    assert dt < 25.0


@pytest.mark.voice_latency
@pytest.mark.skipif(not _oauth_available(), reason="no Claude OAuth")
@pytest.mark.asyncio
async def test_multitool_latency():
    """Latency test: a multi-part request must be answered within the deadline.

    (open_app was used as the tool-assertion anchor in older builds; that
    tool was removed — this test now measures pure response latency.)
    """
    from jarvis.brain.factory import build_default_brain
    brain = build_default_brain()

    t0 = time.perf_counter()
    r = await asyncio.wait_for(
        brain.generate("Öffne bitte 3 Windows Terminal-Fenster (wt)."),  # i18n-allow (simulated German user utterance)
        timeout=30,
    )
    dt = time.perf_counter() - t0
    print(f"\nMulti-Tool-Latenz Tier-1: {dt:.2f}s")
    print(f"Response: {r[:150]}")
    assert r, "Brain must produce a response"
    assert dt < 25.0, f"Brain zu langsam: {dt}s"
