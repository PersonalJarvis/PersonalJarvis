"""The Whisper model must NEVER be constructed on the event-loop thread.

``transcribe`` / ``transcribe_pcm`` are coroutines, and a coroutine's own body
runs ON the loop. A cold CUDA/large-v3 build takes seconds to minutes, so an
``_ensure_model()`` call placed before the ``asyncio.to_thread`` hop froze
every WebSocket, HTTP route and brain turn behind one model load — the
2026-08-15 stall: a provider "Test" click reached ``WhisperModel(...)`` on the
loop thread and the watchdog logged a 15 s+ event-loop stall. The load belongs
in ``_transcribe_sync``'s worker thread, where the inference lock already
serializes it (the same shape ``nemotron_local`` uses).
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from jarvis.plugins.stt import fwhisper
from jarvis.plugins.stt.fwhisper import FasterWhisperProvider


class _FakeModel:
    """Stands in for ``WhisperModel``: decodes nothing, instantly."""

    def transcribe(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return iter(()), SimpleNamespace(language="en")


def _provider_with_recording_builder(monkeypatch) -> tuple[FasterWhisperProvider, list[int]]:
    """A provider whose model build records the thread it ran on."""
    build_threads: list[int] = []

    def _recording_builder(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        build_threads.append(threading.get_ident())
        return _FakeModel()

    monkeypatch.setattr(fwhisper, "_new_whisper_model", _recording_builder)
    return FasterWhisperProvider(), build_threads


def test_transcribe_pcm_builds_the_model_off_the_loop_thread(monkeypatch) -> None:
    provider, build_threads = _provider_with_recording_builder(monkeypatch)
    pcm = b"\x01\x02" * 16_000  # one second of 16 kHz mono int16

    async def _run() -> int:
        await provider.transcribe_pcm(pcm)
        return threading.get_ident()

    loop_thread = asyncio.run(_run())
    assert build_threads, "the lazy load never ran"
    assert loop_thread not in build_threads, (
        "the Whisper model was constructed on the event-loop thread — a cold "
        "load there stalls every route behind it (live stall 2026-08-15)"
    )


def test_transcribe_builds_the_model_off_the_loop_thread(monkeypatch) -> None:
    provider, build_threads = _provider_with_recording_builder(monkeypatch)

    class _Chunk:
        pcm = b"\x01\x02" * 16_000
        sample_rate = 16_000

    async def _chunks():  # noqa: ANN202
        yield _Chunk()

    async def _run() -> int:
        await provider.transcribe(_chunks())
        return threading.get_ident()

    loop_thread = asyncio.run(_run())
    assert build_threads, "the lazy load never ran"
    assert loop_thread not in build_threads
