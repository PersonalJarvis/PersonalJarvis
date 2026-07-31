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

    def __init__(
        self,
        text="hallo welt",  # i18n-allow: German test fixture
        delay=0.0,
        error=None,
        detected="",
        probability=0.0,
    ):
        super().__init__()
        self.text, self.delay, self.error = text, delay, error
        self.detected, self.probability = detected, probability
        self.calls = 0
        self._model = object()  # pretend the load already completed

    def _transcribe_sync(self, pcm, language):
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.delay:
            import time

            time.sleep(self.delay)
        return self.text, self.detected, self.probability


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
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(
            get_supported_compute_types=lambda _device: (_ for _ in ()).throw(
                RuntimeError("no CUDA here")
            )
        ),
    )
    assert LocalPreviewTranscriber._pick_device() == ("cpu", "int8")


def test_the_device_probe_asks_ctranslate2_not_torch(monkeypatch):
    """The executing runtime decides; another loader may temporarily hide torch."""
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(
            get_supported_compute_types=lambda device: (
                {"int8", "int8_float16"} if device == "cuda" else {"int8"}
            )
        ),
    )

    assert LocalPreviewTranscriber._pick_device() == ("cuda", "int8_float16")


def test_a_cuda_model_that_cannot_decode_falls_back_to_cpu(monkeypatch):
    """A successful constructor is not proof that CUDA inference is usable."""
    import jarvis.plugins.stt.fwhisper as fwhisper

    calls: list[tuple[str, str]] = []
    cpu_model = object()

    class _Model:
        def __init__(self, device: str) -> None:
            self.device = device

        def transcribe(self, _audio, **_kwargs):
            if self.device == "cuda":
                raise RuntimeError("CUDA decode failed")
            return [], object()

    def _build(_name: str, device: str, compute: str):
        calls.append((device, compute))
        if device == "cpu":
            model = _Model(device)
            model.marker = cpu_model
            return model
        return _Model(device)

    monkeypatch.setattr(fwhisper, "_new_whisper_model", _build)
    engine = LocalPreviewTranscriber()
    engine._pick_device = lambda: ("cuda", "int8_float16")  # type: ignore[method-assign]

    engine._load_model()

    assert calls == [("cuda", "int8_float16"), ("cpu", "int8")]
    assert getattr(engine._model, "marker", None) is cpu_model


async def test_a_timed_out_native_call_keeps_the_engine_busy_until_it_really_ends():
    """AP-24: the timeout bounds the wait; it does not stop the native thread."""
    import jarvis.dictation.local_preview as mod

    engine = _Engine(delay=0.15)
    original = mod.PREVIEW_TIMEOUT_S
    mod.PREVIEW_TIMEOUT_S = 0.02
    try:
        assert await engine.transcribe(b"\x00" * 32000) is None
        assert await engine.transcribe(b"\x00" * 32000) is None
        assert engine.calls == 1, "the still-running native call must own the engine"
        await asyncio.sleep(0.2)
        assert await engine.transcribe(b"\x00" * 32000) is None
        assert engine.calls == 2
    finally:
        mod.PREVIEW_TIMEOUT_S = original


async def test_a_wedged_native_preview_rotates_to_a_fresh_model_and_guard():
    """BUG-036: busy ticks must eventually orphan a never-returning engine."""
    import threading

    import jarvis.dictation.local_preview as mod

    release = threading.Event()
    engine = _Engine()

    def _wedge(_pcm, _language):
        engine.calls += 1
        release.wait(timeout=5.0)
        return "", "", 0.0

    engine._transcribe_sync = _wedge  # type: ignore[method-assign]
    old_guard = engine._busy
    original = mod.PREVIEW_TIMEOUT_S
    mod.PREVIEW_TIMEOUT_S = 0.02
    try:
        assert await engine.transcribe(b"\x00" * 32000) is None
        assert await engine.transcribe(b"\x00" * 32000) is None
        assert await engine.transcribe(b"\x00" * 32000) is None
        assert engine.ready is False
        assert engine._busy is not old_guard
        assert engine.available is True
    finally:
        release.set()
        await asyncio.sleep(0.05)
        mod.PREVIEW_TIMEOUT_S = original


async def test_the_audio_language_reading_is_kept_not_discarded():
    """The decoder names the spoken language on every call; the preview keeps it.

    This reading is the only one taken from the AUDIO. A cloud provider handed
    a few seconds of speech may TRANSLATE it, and translated words look like
    the target language to any text-based detector — so a reading taken after
    the fact cannot replace this one.
    """
    # i18n-allow: German test fixture — the speech under test
    engine = _Engine(text="hallo welt", detected="de", probability=0.99)

    assert await engine.transcribe(b"\x00" * 32000) == "hallo welt"
    assert engine.last_language == "de"
    assert engine.last_language_probability == 0.99


class _Segment:
    def __init__(self, text):
        self.text = text


class _Info:
    def __init__(self, language, probability):
        self.language = language
        self.language_probability = probability


class _NativeModel:
    """Stands in for the real decoder, which always names a language."""

    def __init__(self, text, language, probability):
        self._answer = (text, language, probability)
        self.asked_for: list[str | None] = []
        self.options: list[dict[str, object]] = []

    def transcribe(self, samples, language=None, **kwargs):
        self.asked_for.append(language)
        self.options.append(kwargs)
        text, detected, probability = self._answer
        return [_Segment(text)], _Info(detected, probability)


def test_a_pinned_language_is_not_reported_back_as_a_detection():
    """Otherwise a pin would confirm itself forever and never be re-examined.

    Runs the REAL decode path — the point is what the engine does with the
    decoder's answer, so a stub that skips that step would prove nothing.
    """
    engine = LocalPreviewTranscriber()
    # The decoder is handed "de" and, as it always does, echoes a language back.
    engine._model = _NativeModel("Hallo Welt", "de", 1.0)  # i18n-allow: German test fixture

    text, detected, probability = engine._transcribe_sync(b"\x00" * 32000, "de")

    assert text == "Hallo Welt"  # i18n-allow: German test fixture
    assert detected == "", (
        "a language the caller supplied is an instruction, not a reading"
    )
    assert probability == 1.0


def test_an_unpinned_decode_reports_the_language_it_found():
    engine = LocalPreviewTranscriber()
    engine._model = _NativeModel("Hallo Welt", "de", 0.98)  # i18n-allow: German test fixture

    text, detected, probability = engine._transcribe_sync(b"\x00" * 32000, None)

    assert (detected, probability) == ("de", 0.98)
    assert text == "Hallo Welt"  # i18n-allow: German test fixture
    assert engine._model.options[-1]["temperature"] == 0.0
