"""Live tests for the vision engine (Phase 5 Capability 1).

These tests need a real Windows desktop (mss, pywinauto,
foreground window). They are **skipped** by default, because they
fail in CI/sandbox environments without a GUI. Run locally manually via:

    pytest tests/integration/test_vision_live.py -m phase5 --runlive

(The `--runlive` convention is a placeholder-marker combination that
the lead dev will wire up later. Until then: the tests skip.)
"""
from __future__ import annotations

import os
import time

import pytest

pytestmark = [
    pytest.mark.phase5,
    pytest.mark.skip_ci,
    pytest.mark.skipif(
        os.environ.get("JARVIS_VISION_LIVE") != "1",
        reason="Live-Test: setze JARVIS_VISION_LIVE=1, braucht Windows-Desktop",
    ),
]


@pytest.mark.asyncio
async def test_screenshot_roundtrip_live():
    """Screenshot-Roundtrip: capture + hash + Observation-Felder."""
    from jarvis.vision.screenshot import ScreenshotSource

    src = ScreenshotSource(save_blob=False)
    obs = await src.observe()
    assert obs.source == "screenshot_only"
    assert obs.screenshot_hash
    assert len(obs.screenshot_hash) == 64  # SHA256 hex
    await src.close()


@pytest.mark.asyncio
async def test_uia_tree_budget_on_notepad_under_2000ms():
    """Notepad -> UIA tree must stay under 2000ms (performance budget).

    Prerequisite: Notepad is open and in the foreground. Arranged
    manually by the lead dev before the smoke run.
    """
    from jarvis.vision.uia_tree import UIATreeSource

    src = UIATreeSource()
    start = time.perf_counter()
    obs = await src.observe()
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 2000, f"UIA-Tree took {elapsed_ms:.1f}ms, budget 2000ms"
    # Sanity check: at least root is present, or overflow.
    assert obs.source in ("ui_tree_only", "screenshot_only")
    await src.close()


@pytest.mark.asyncio
async def test_engine_composite_roundtrip():
    """Full composite observe — beide Sources kombiniert."""
    from jarvis.vision.engine import VisionEngine

    engine = VisionEngine()
    obs = await engine.observe(mode="composite")
    assert obs.screenshot_hash
    assert obs.source in ("full", "screenshot_only")
    await engine.close()
