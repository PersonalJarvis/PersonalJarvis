"""Wake/browser handoff is portable, bounded, ephemeral and consumed once."""

import asyncio
import base64
import re
import time
from array import array
from pathlib import Path

import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.live import startup
from jarvis.live.media import InputPrefix
from jarvis.speech.pipeline import _SessionInputBuffer


def test_input_prefix_python_typescript_field_parity():
    source = (
        Path(__file__).resolve().parents[2]
        / "jarvis/ui/web/frontend/src/lib/startupAudio.ts"
    ).read_text(encoding="utf-8")
    interface = source.split("export interface InputPrefix {", 1)[1].split("}", 1)[0]
    assert set(re.findall(r"^\s+(\w+):", interface, re.MULTILINE)) == set(InputPrefix.model_fields)


@pytest.mark.asyncio
async def test_prefix_trims_browser_overlap_and_releases_capture_once():
    now = time.time_ns()
    source = _SessionInputBuffer(
        initial=(
            AudioChunk(
                pcm=array("h", range(160)).tobytes(),
                sample_rate=16000,
                timestamp_ns=now,
            ),
        )
    )
    startup.offer("one", source)
    result = await startup.take("one", (now - 5_000_000) / 1_000_000)
    assert result["type"] == "input_prefix"
    assert result["sample_rate"] == 16000
    # Epoch floats have sub-microsecond precision; at most one boundary sample.
    assert 79 <= len(base64.b64decode(result["audio"])) // 2 <= 80
    assert source.released.is_set()
    assert await startup.take("one", now / 1_000_000) is None


@pytest.mark.asyncio
async def test_cancelled_start_cannot_supply_audio_to_a_later_call():
    source = _SessionInputBuffer()
    startup.offer("cancelled", source)
    startup.discard("cancelled")
    assert await startup.take("cancelled", time.time() * 1000) is None
    assert await startup.take("other", time.time() * 1000) is None
    await source.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp", [None, float("nan"), 1, True])
async def test_old_or_invalid_capture_releases_the_native_lease(timestamp):
    source = _SessionInputBuffer()
    startup.offer("invalid", source)
    if timestamp is None:
        assert await startup.take("invalid", timestamp) is None
    else:
        with pytest.raises(ValueError):
            await startup.take("invalid", timestamp)
    assert source.released.is_set()


@pytest.mark.asyncio
async def test_cross_thread_handoff_uses_the_capture_owners_event_loop():
    now = time.time_ns()
    source = _SessionInputBuffer(
        initial=(
            AudioChunk(
                pcm=b"\x01\x00" * 160,
                sample_rate=16000,
                timestamp_ns=now,
            ),
        )
    )
    startup.offer("thread", source)
    result = await asyncio.to_thread(
        lambda: asyncio.run(startup.take("thread", now / 1_000_000 + 10))
    )
    assert len(base64.b64decode(result["audio"])) == 320
    assert source.released.is_set()
