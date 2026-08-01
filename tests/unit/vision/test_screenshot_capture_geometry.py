from __future__ import annotations

import asyncio
import hashlib
import threading

from jarvis.vision.screenshot import ScreenshotSource, _CapturedImage


async def test_concurrent_observations_keep_their_own_capture_geometry(
    monkeypatch,
) -> None:
    source = ScreenshotSource(save_blob=False)
    barrier = threading.Barrier(2)
    capture_count = 0
    count_lock = threading.Lock()
    captures = (
        _CapturedImage(b"first", (-1920, 0, 1920, 1080)),
        _CapturedImage(b"second", (0, 0, 2560, 1440)),
    )

    def capture() -> _CapturedImage:
        nonlocal capture_count
        with count_lock:
            index = capture_count
            capture_count += 1
        barrier.wait(timeout=2.0)
        return captures[index]

    monkeypatch.setattr(source, "_capture_image", capture)
    observations = await asyncio.gather(source.observe(), source.observe())

    actual = {(item.screenshot_hash, item.monitor_geom) for item in observations}
    expected = {
        (hashlib.sha256(item.image_bytes).hexdigest(), item.monitor_geom)
        for item in captures
    }
    assert actual == expected
