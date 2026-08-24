"""Building the native Whisper engine must never block the event loop, and two
callers arriving during a cold load must share ONE build.

Live forensic 2026-08-24 (dictation on local models, right after a restart): the
loop watchdog logged a 75 s stall whose stack was
``transcribe_pcm`` -> ``_ensure_model`` -> ``WhisperModel(...)``. ``transcribe_pcm``
is a coroutine, so awaiting the blocking construction inline froze the whole
backend: every WebSocket frame, HTTP route and brain turn queued behind the loop
thread, and the caller's own ``asyncio.wait_for`` could not fire either — a
timeout needs a running loop to fire ON. The abandoned build kept the engine
busy, so the retries came back ``TranscribeBusy`` and the dictation ended with
zero characters.

Three contracts pinned here:
  1. The construction runs OFF the loop, so the loop keeps ticking during it.
  2. A concurrent second caller waits for the first engine instead of building a
     rival one (doubling both the wait and the VRAM).
  3. ``is_loading`` reports the build honestly, so a warm-up supervisor can tell
     "cold start" apart from "wedged" instead of restarting the load from zero.
"""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from jarvis.plugins.stt import fwhisper
from jarvis.plugins.stt.fwhisper import FasterWhisperProvider

PCM_1S = b"\x00\x00" * 16_000

# Long enough that an on-loop build is unmistakable, short enough to stay a unit
# test: the loop ticks every 10 ms below, so a free loop racks up dozens of them.
_BUILD_SECONDS = 0.5


class _StubModel:
    """Stands in for a built WhisperModel — returns an empty transcript."""

    def transcribe(self, audio, **kwargs):  # noqa: ANN001, ANN003
        return iter(()), SimpleNamespace(language="de")


async def test_model_build_does_not_block_the_event_loop(monkeypatch) -> None:
    def _slow_build(*_a, **_kw):
        time.sleep(_BUILD_SECONDS)  # a cold ctranslate2 load, in miniature
        return _StubModel()

    monkeypatch.setattr(fwhisper, "_new_whisper_model", _slow_build)
    prov = FasterWhisperProvider(device="cpu", compute_type="int8")

    ticks = 0

    async def _heartbeat() -> None:
        # Stands in for every WebSocket frame, HTTP route and brain turn: work
        # the loop owes other callers while the engine loads.
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.create_task(_heartbeat())
    try:
        await prov.transcribe_pcm(PCM_1S)
    finally:
        beat.cancel()

    assert ticks >= 10, (
        f"the event loop only ticked {ticks} times during a "
        f"{_BUILD_SECONDS}s model build — the native load is blocking the loop "
        "again (the 75 s backend freeze of 2026-08-24)"
    )


async def test_a_concurrent_caller_shares_one_build(monkeypatch) -> None:
    builds = 0
    build_lock = threading.Lock()

    def _counting_build(*_a, **_kw):
        nonlocal builds
        with build_lock:
            builds += 1
        time.sleep(_BUILD_SECONDS)
        return _StubModel()

    monkeypatch.setattr(fwhisper, "_new_whisper_model", _counting_build)
    prov = FasterWhisperProvider(device="cpu", compute_type="int8")

    # Both arrive while the engine is still cold — the wake poll loop and the
    # dictation final pass in production.
    await asyncio.gather(
        prov.transcribe_pcm(PCM_1S),
        prov.transcribe_pcm(PCM_1S),
        return_exceptions=True,  # one may lose the inference lock; that is fine
    )

    assert builds == 1, (
        f"{builds} engines were built for one provider — a concurrent caller "
        "started a rival load instead of waiting for the first"
    )


async def test_is_loading_is_true_only_while_building(monkeypatch) -> None:
    building = threading.Event()
    release = threading.Event()

    def _held_build(*_a, **_kw):
        building.set()
        release.wait(timeout=5.0)
        return _StubModel()

    monkeypatch.setattr(fwhisper, "_new_whisper_model", _held_build)
    prov = FasterWhisperProvider(device="cpu", compute_type="int8")

    assert prov.is_loading is False, "a provider that never loaded is not loading"
    call = asyncio.create_task(prov.transcribe_pcm(PCM_1S))
    try:
        await asyncio.to_thread(building.wait, 5.0)
        assert prov.is_loading is True, (
            "is_loading stayed False during the build — a warm-up supervisor "
            "cannot tell a cold start from a wedge and will restart the load"
        )
    finally:
        release.set()
        await call

    assert prov.is_loading is False, "is_loading must clear once the build ends"


def test_recover_frees_the_load_lock_too() -> None:
    # A thread wedged INSIDE the native build still holds the old load lock;
    # the fresh path must not queue up behind it (AP-24).
    prov = FasterWhisperProvider(device="cpu", compute_type="int8")
    old_lock = prov._load_lock  # noqa: SLF001
    old_lock.acquire()  # a wedged build "holds" it

    prov.recover()

    assert prov._load_lock is not old_lock, "recover() must swap in a fresh load lock"  # noqa: SLF001
    assert prov._load_lock.acquire(blocking=False) is True  # noqa: SLF001
    prov._load_lock.release()  # noqa: SLF001
    old_lock.release()
