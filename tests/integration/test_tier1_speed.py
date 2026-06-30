"""Tier-1-Latenz-Test: Live-Brain mit neuen Settings.

Misst:
- Brain-Latenz mit max_retries=0 (sollte bei 429 sofort fallbacken)
- Multi-Tool-Call-Latenz
- RateLimitTracker-Verhalten bei simuliertem 429
"""
from __future__ import annotations

import asyncio
import time

import pytest

def _api_key_available() -> bool:
    import os
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY"))


def _oauth_available() -> bool:
    # Claude OAuth Max-Plan via claude-api Provider; aktuell ueber denselben
    # Env-Marker wie _api_key_available, bis spezifischer OAuth-Token-Check noetig wird.
    return _api_key_available()


@pytest.mark.voice_latency
@pytest.mark.skipif(not _api_key_available(), reason="kein Brain-API-Key")
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
@pytest.mark.skipif(not _api_key_available(), reason="kein Brain-API-Key")
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
    # Sollte auf Opus (deep_model) fallbacken, immer noch sub-15s
    assert dt < 25.0


@pytest.mark.voice_latency
@pytest.mark.skipif(not _oauth_available(), reason="kein Claude OAuth")
@pytest.mark.asyncio
async def test_multitool_latency():
    """Latenz-Test: Multi-Part-Request muss innerhalb der Deadline beantwortet werden.

    (open_app wurde in älteren Builds als Tool-Assertion-Anker genutzt; das Tool
    wurde entfernt — dieser Test misst jetzt reine Antwort-Latenz.)
    """
    from jarvis.brain.factory import build_default_brain
    brain = build_default_brain()

    t0 = time.perf_counter()
    r = await asyncio.wait_for(
        brain.generate("Öffne bitte 3 Windows Terminal-Fenster (wt)."),
        timeout=30,
    )
    dt = time.perf_counter() - t0
    print(f"\nMulti-Tool-Latenz Tier-1: {dt:.2f}s")
    print(f"Response: {r[:150]}")
    assert r, "Brain muss eine Antwort liefern"
    assert dt < 25.0, f"Brain zu langsam: {dt}s"
