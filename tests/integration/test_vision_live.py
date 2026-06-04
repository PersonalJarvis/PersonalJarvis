"""Live-Tests fuer die Vision-Engine (Phase 5 Capability 1).

Diese Tests brauchen einen echten Windows-Desktop (mss, pywinauto,
Foreground-Window). Sie sind standardmaessig **skipped**, weil sie in
CI-/Sandbox-Umgebungen ohne GUI fehlschlagen. Lokal manuell via:

    pytest tests/integration/test_vision_live.py -m phase5 --runlive

(Die `--runlive`-Konvention ist eine Placeholder-Marker-Kombination, die
der Haupt-Dev spaeter verdrahtet. Solange gilt: die Tests skippen.)
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
    """Notepad -> UIA-Tree muss unter 2000ms bleiben (Performance-Budget).

    Voraussetzung: Notepad ist geoeffnet und im Vordergrund. Wird vom
    Haupt-Dev vor dem Smoke-Run manuell arrangiert.
    """
    from jarvis.vision.uia_tree import UIATreeSource

    src = UIATreeSource()
    start = time.perf_counter()
    obs = await src.observe()
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 2000, f"UIA-Tree took {elapsed_ms:.1f}ms, budget 2000ms"
    # Sanity-Check: mindestens Root ist drin oder overflow.
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
