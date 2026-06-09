"""Wave-3 latency fix: bound the per-turn vision capture.

``_collect_vision_images`` awaited ``vision.current()`` with NO timeout. A
stalled capture (mss BitBlt hang, paused-state miss, slow disk) would block the
whole brain turn on the hot path. Cap it so the turn proceeds text-only instead
of hanging.
"""
from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import pytest

from jarvis.brain.manager import BrainManager


class _SlowVision:
    """Vision provider whose capture never returns in time."""

    is_paused = False

    async def current(self):  # noqa: ANN201
        await asyncio.sleep(30)  # far longer than the collect timeout


@pytest.mark.asyncio
async def test_vision_collect_times_out_and_returns_empty() -> None:
    mgr = BrainManager.__new__(BrainManager)
    mgr._vision_provider = _SlowVision()
    mgr._active_name = "test"
    mgr._config = object()   # no `.performance` → conditional-vision gate skipped
    mgr._bus = None

    start = time.monotonic()
    result = await mgr._collect_vision_images(trace_id=uuid4(), user_text="hallo")
    elapsed = time.monotonic() - start

    assert result == ()
    assert elapsed < 5.0, f"vision collect must bail out fast, took {elapsed:.1f}s"
