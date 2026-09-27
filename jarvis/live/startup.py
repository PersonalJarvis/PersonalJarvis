"""One-use, memory-only handoff from the wake microphone to browser capture."""

from __future__ import annotations

import asyncio
import base64
import math
import threading
import time
from typing import Any

from jarvis.live.media import InputPrefix

_lock = threading.Lock()
_pending: dict[str, tuple[asyncio.AbstractEventLoop, Any]] = {}


def offer(session_id: str, source: Any) -> None:
    """Keep the existing wake capture alive until the browser actually captures."""
    with _lock:
        _pending[session_id] = (asyncio.get_running_loop(), source)


def discard(session_id: str) -> None:
    with _lock:
        _pending.pop(session_id, None)


async def take(session_id: str, capture_started_at_ms: Any) -> dict | None:
    """Trim at the browser's first sample, close native capture, and consume once.

    Both clocks are wall clocks on the same desktop. Only the owning desktop
    session can have a source here; a headless/browser-only call has none.
    AudioChunk timestamps mark the END of their frame. Trimming overlapping
    samples prevents the user's opening words from being sent twice.
    """
    with _lock:
        entry = _pending.pop(session_id, None)
    if entry is None:
        return None
    loop, source = entry

    async def collect() -> dict | None:
        await source.close()
        # Old clients cannot consume a prefix. Release their native lease too.
        if capture_started_at_ms is None:
            return None
        if (
            isinstance(capture_started_at_ms, bool)
            or not isinstance(capture_started_at_ms, (int, float))
            or not math.isfinite(capture_started_at_ms)
            or abs(time.time() * 1000 - capture_started_at_ms) > 60_000
        ):
            raise ValueError("Invalid browser capture timestamp")
        cutoff_ns = round(capture_started_at_ms * 1_000_000)
        audio = bytearray()
        rate = 0
        async for chunk in source.stream():
            if chunk.channels != 1 or chunk.sample_rate <= 0 or len(chunk.pcm) % 2:
                raise ValueError("Invalid wake microphone format")
            if rate and rate != chunk.sample_rate:
                raise ValueError("Wake microphone format changed during startup")
            rate = chunk.sample_rate
            samples = len(chunk.pcm) // 2
            end_ns = chunk.timestamp_ns
            start_ns = end_ns - samples * 1_000_000_000 // rate
            keep = max(0, min(samples, (cutoff_ns - start_ns) * rate // 1_000_000_000))
            audio.extend(chunk.pcm[: keep * 2])
            if len(audio) > rate * 2 * 30:
                raise ValueError("Wake microphone exceeded the startup buffer")
        if not audio:
            return None
        return InputPrefix(
            sample_rate=rate, audio=base64.b64encode(audio).decode("ascii")
        ).model_dump()

    if asyncio.get_running_loop() is loop:
        return await collect()
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(collect(), loop))
