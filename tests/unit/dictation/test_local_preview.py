"""The live preview runs locally so the transcript keeps the whole quota.

Root cause these lock (2026-07-29): preview and transcript shared one cloud
provider, so the throwaway half spent the 20 RPM ceiling — which Groq applies to
its PAID plan too — and the half carrying the user's words got the 429s.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.dictation.local_preview import (
    LocalPreviewTranscriber,
    local_preview,
    reset_local_preview_for_tests,
)


@pytest.fixture(autouse=True)
def _isolated():
    reset_local_preview_for_tests()
    yield
    reset_local_preview_for_tests()


class _Engine(LocalPreviewTranscriber):
    """Preview engine with the native model replaced by a scripted stub.

    ``_model`` is pre-set because ``transcribe`` gates on it: an engine that has
    not finished loading answers "nothing yet" without transcribing anything.
    """

    def __init__(self, text="hallo welt", delay=0.0, error=None):  # i18n-allow: German test fixture
        super().__init__()
        self.text, self.delay, self.error = text, delay, error
        self.calls = 0
        self._model = object()  # pretend the load already completed

    def _transcribe_sync(self, pcm, language):
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.delay:
            import time

            time.sleep(self.delay)
        return self.text


async def test_preview_text_comes_back():
    engine = _Engine(text="ich moechte ein Feature")  # i18n-allow: German test fixture
    assert await engine.transcribe(b"\x00" * 32000) == "ich moechte ein Feature"


async def test_empty_audio_needs_no_engine():
    engine = _Engine()
    assert await engine.transcribe(b"") is None
    assert engine.calls == 0


async def test_a_second_caller_is_turned_away_not_queued():
    """AP-24: a queued call into a native engine is how it wedges permanently.

    Skipping costs one stale preview line; queueing costs the whole dictation.
    """
    engine = _Engine(delay=0.25)
    first = asyncio.create_task(engine.transcribe(b"\x00" * 32000))
    await asyncio.sleep(0.05)
    assert await engine.transcribe(b"\x00" * 32000) is None
    assert await first is not None
    assert engine.calls == 1


async def test_a_slow_preview_is_abandoned_not_awaited():
    """Stale preview text is worthless; the loop must not wait for it."""
    import jarvis.dictation.local_preview as mod

    engine = _Engine(delay=0.5)
    original = mod.PREVIEW_TIMEOUT_S
    mod.PREVIEW_TIMEOUT_S = 0.05
    try:
        assert await engine.transcribe(b"\x00" * 32000) is None
    finally:
        mod.PREVIEW_TIMEOUT_S = original


async def test_a_failing_preview_never_breaks_the_dictation():
    engine = _Engine(error=RuntimeError("engine exploded"))
    assert await engine.transcribe(b"\x00" * 32000) is None


async def test_a_failing_engine_is_dropped_so_a_fresh_one_can_be_built():
    """AP-24: re-asking a wedged native session never recovers it.

    Dropping the model is the repair — the next tick finds none and rebuilds.
    Crucially the PATH stays available: one bad segment must not cost this host
    its fast preview forever. Only a failed BUILD proves the host cannot run one.
    """
    engine = _Engine(error=RuntimeError("nope"))
    for _ in range(3):
        await engine.transcribe(b"\x00" * 32000)

    assert engine.ready is False, "the wedged engine should have been dropped"
    assert engine.available is True, "a bad segment is not a dead host"


async def test_a_failed_build_is_what_disables_the_local_path():
    """The honest signal: this host cannot construct an engine at all."""
    engine = LocalPreviewTranscriber()
    engine._model_name = "a-model-that-does-not-exist-anywhere"
    engine._load_model()

    assert engine.available is False
    assert await engine.transcribe(b"\x00" * 32000) is None


def test_no_local_engine_means_no_local_preview(monkeypatch):
    """A base/headless install has no faster-whisper — the caller must branch.

    Returning None rather than an inert object keeps that fallback an explicit
    decision instead of a preview that silently never appears.
    """
    import jarvis.dictation.local_preview as mod

    monkeypatch.setattr(mod, "faster_whisper_available", lambda: False)
    assert local_preview() is None


def test_the_engine_is_shared_across_dictations(monkeypatch):
    """Consecutive dictations must not each pay the model load."""
    import jarvis.dictation.local_preview as mod

    monkeypatch.setattr(mod, "faster_whisper_available", lambda: True)
    assert local_preview() is local_preview()


async def test_loading_the_engine_never_counts_as_a_failure():
    """REGRESSION: the local preview used to disable itself before ever working.

    Building the engine takes seconds, far longer than a preview may be held
    for. Loading it inside the timed call made every early tick "time out", and
    each timeout counted as an engine failure — so the path reliably switched
    itself off during the first dictation. Loading now happens off the
    transcribe path and an unready engine simply answers "nothing yet".
    """
    engine = LocalPreviewTranscriber()
    started: list[bool] = []
    engine._start_loading = lambda: started.append(True)  # type: ignore[method-assign]

    for _ in range(10):
        assert await engine.transcribe(b"\x00" * 32000) is None

    assert started, "an unready engine must kick off its background load"
    assert engine.available is True, "waiting to load is not a failure"


def test_the_device_probe_falls_back_to_cpu_when_cuda_is_not_usable(monkeypatch):
    """CUDA present and CUDA usable are different questions (AP-21/AP-25)."""
    import builtins

    real_import = builtins.__import__

    def _no_torch(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("no torch here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_torch)
    assert LocalPreviewTranscriber._pick_device() == ("cpu", "int8")
