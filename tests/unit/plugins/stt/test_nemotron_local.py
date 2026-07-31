"""Guards for the on-device Nemotron recognizer.

Three of these pin properties that were found by MEASURING the model rather than
by reading its docs, and that a refactor would silently undo:

* the leading silence that stops the streaming encoder from eating the first
  word (verified against the model's own German sample: without it, the
  opening word was missing from the transcript entirely);
* the privacy declaration the dictation polish floor keys on;
* honest, actionable errors when the engine or the weights are absent, instead
  of a native stack trace from three layers down.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from jarvis.plugins.stt import nemotron_local
from jarvis.plugins.stt.nemotron_local import NemotronLocalSTT


class _FakeStream:
    """Records what the provider feeds the recognizer."""

    def __init__(self) -> None:
        self.chunks: list[np.ndarray] = []
        self.options: dict[str, str] = {}
        self.finished = False

    def set_option(self, key: str, value: str) -> None:
        self.options[key] = value

    def accept_waveform(self, sample_rate: int, samples: np.ndarray) -> None:
        self.chunks.append(np.asarray(samples))

    def input_finished(self) -> None:
        self.finished = True


class _FakeRecognizer:
    def __init__(self, text: str = "hello there") -> None:
        self.text = text
        self.stream = _FakeStream()
        self._decodes = 0

    def create_stream(self) -> _FakeStream:
        return self.stream

    def is_ready(self, stream: _FakeStream) -> bool:
        self._decodes += 1
        return self._decodes <= 1

    def decode_stream(self, stream: _FakeStream) -> None:
        pass

    def get_result(self, stream: _FakeStream) -> str:
        return self.text


def _provider_with(recognizer: Any, **kwargs: Any) -> NemotronLocalSTT:
    provider = NemotronLocalSTT(**kwargs)
    provider._recognizer = recognizer  # skip the real 690 MB model load
    return provider


def _pcm(seconds: float, sample_rate: int = 16_000) -> bytes:
    """A quiet but non-empty int16 buffer of the requested length."""
    return (np.ones(int(sample_rate * seconds), dtype=np.int16) * 100).tobytes()


def test_leading_silence_is_prepended_so_the_first_word_survives() -> None:
    """The regression: a cache-aware encoder with no context drops word one.

    Measured on the model's own German sample: 0.0 s of lead-in lost the
    opening word, 0.6 s brought it back. In a voice assistant the first word is
    usually the command, so this padding is load-bearing, not a nicety.
    """
    recognizer = _FakeRecognizer()
    provider = _provider_with(recognizer)

    asyncio.run(provider.transcribe_pcm(_pcm(1.0), 16_000))

    chunks = recognizer.stream.chunks
    assert len(chunks) == 3, "expected lead silence, audio, tail silence"
    lead, audio, tail = chunks
    assert lead.size >= int(16_000 * 0.6), (
        "The leading silence must be long enough to prime the encoder; below "
        "~0.6 s the opening word comes back mangled or missing."
    )
    assert np.all(lead == 0.0)
    assert np.all(tail == 0.0)
    assert audio.size == 16_000


def test_int16_input_is_scaled_into_the_range_the_features_expect() -> None:
    """Feeding int16-scaled values straight in would mis-normalise the features."""
    recognizer = _FakeRecognizer()
    provider = _provider_with(recognizer)

    asyncio.run(provider.transcribe_pcm(_pcm(0.5), 16_000))

    audio = recognizer.stream.chunks[1]
    assert np.max(np.abs(audio)) <= 1.0


def test_configured_language_is_pinned_per_stream() -> None:
    recognizer = _FakeRecognizer()
    provider = _provider_with(recognizer, language="de")

    asyncio.run(provider.transcribe_pcm(_pcm(0.3), 16_000))

    assert recognizer.stream.options.get("language") == "de"


def test_auto_clears_the_configured_pin_for_that_call() -> None:
    """"auto" is a request to DETECT, not "no opinion given".

    Treating it as the latter is what once let a German pin leak into an
    auto-detect path and write German speech as English.
    """
    recognizer = _FakeRecognizer()
    provider = _provider_with(recognizer, language="de")

    result = asyncio.run(provider.transcribe_pcm(_pcm(0.3), 16_000, language="auto"))

    assert "language" not in recognizer.stream.options
    assert result.language == "auto"


def test_a_language_pin_the_engine_rejects_does_not_kill_the_turn() -> None:
    """An older engine build must degrade to auto-detect, not lose the utterance."""

    class _RefusingStream(_FakeStream):
        def set_option(self, key: str, value: str) -> None:
            raise RuntimeError("option not supported by this build")

    recognizer = _FakeRecognizer()
    recognizer.stream = _RefusingStream()
    provider = _provider_with(recognizer, language="de")

    result = asyncio.run(provider.transcribe_pcm(_pcm(0.3), 16_000))

    assert result.text == "hello there"


def test_empty_audio_returns_an_empty_transcript_not_an_error() -> None:
    provider = _provider_with(_FakeRecognizer())

    result = asyncio.run(provider.transcribe_pcm(b"", 16_000))

    assert result.text == ""
    assert result.confidence == 0.0


def test_missing_model_files_raise_one_actionable_error(tmp_path: Path) -> None:
    """Not a native crash: the message must say what to do about it."""
    pytest.importorskip("sherpa_onnx")
    provider = NemotronLocalSTT(model_dir=tmp_path)

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(provider.transcribe_pcm(_pcm(0.3), 16_000))

    message = str(excinfo.value)
    assert "not on this machine" in message
    assert "API-Keys view" in message


def test_missing_engine_raises_one_actionable_error(monkeypatch) -> None:
    """A base install has no sherpa-onnx; the failure must name the fix."""
    import builtins

    real_import = builtins.__import__

    def _no_sherpa(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "sherpa_onnx":
            raise ImportError("No module named 'sherpa_onnx'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_sherpa)
    provider = NemotronLocalSTT()

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(provider.transcribe_pcm(_pcm(0.3), 16_000))

    assert "not installed" in str(excinfo.value)


def test_declares_that_it_runs_on_device() -> None:
    """The privacy floor reads this; a wrong answer uploads a local user's text."""
    from jarvis.plugins.stt import provider_runs_on_device

    assert NemotronLocalSTT.runs_on_device is True
    assert provider_runs_on_device("nemotron-local") is True


def test_construction_loads_no_model(monkeypatch) -> None:
    """Building the provider must stay cheap — the 690 MB load waits (AP-26).

    The STT factory constructs a provider during startup, where a model load
    has no business being; the weights arrive on first use instead.
    """
    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the model must not be loaded during construction")

    monkeypatch.setattr(nemotron_local.NemotronLocalSTT, "_ensure_model", _explode)

    provider = NemotronLocalSTT(language="de")

    assert provider._recognizer is None
