"""The on-device final pass: spawn gating, crossable failures, worker replacement.

No subprocess and no model anywhere here — the worker proxy is a fake with the
``transcribe(samples, language, beam_size)`` surface of ``_WorkerModel``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

import jarvis.dictation.local_final as local_final_mod
from jarvis.dictation.local_final import (
    FINAL_BEAM_SIZE,
    FINAL_COMPUTE,
    LocalEngineUnavailable,
    LocalFinalSTT,
)
from jarvis.speech.stt_failure import classify_stt_failure, is_crossable_failure


class _FakeWorker:
    def __init__(self, device: str = "cuda", *, fail: str = "") -> None:
        self.device = device
        self.compute = "int8_float16"
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self._fail = fail

    def transcribe(
        self, samples: Any, language: str | None = None, beam_size: int = 1, **_: Any
    ) -> tuple[list[Any], Any]:
        self.calls.append(
            {"n": int(np.asarray(samples).size), "language": language, "beam": beam_size}
        )
        if self._fail:
            raise RuntimeError(self._fail)
        segments = [
            SimpleNamespace(start=0.0, end=1.2, text=" Hallo"),
            SimpleNamespace(start=1.2, end=2.0, text=" Welt."),
        ]
        info = SimpleNamespace(language="de", language_probability=0.97)
        return segments, info

    def close(self) -> None:
        self.closed = True


def _pcm(seconds: float = 1.0) -> bytes:
    return (np.zeros(int(16_000 * seconds), dtype="<i2") + 1000).tobytes()


@pytest.fixture
def _local_ok(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """faster-whisper present, plenty of free memory, a CUDA worker on spawn."""
    import jarvis.dictation.local_preview as preview_mod
    import jarvis.hardware.detection as detection

    spawned: dict[str, Any] = {"args": []}

    def _spawn(model_name: str, *, compute: str | None = None) -> _FakeWorker:
        spawned["args"].append((model_name, compute))
        return spawned.get("next") or _FakeWorker()

    monkeypatch.setattr(preview_mod, "faster_whisper_available", lambda: True)
    monkeypatch.setattr(preview_mod, "_spawn_worker_model", _spawn)
    monkeypatch.setattr(detection, "free_accelerator_gb", lambda: (6.0, "nvml"))
    return spawned


def test_a_worker_error_is_a_crossable_failure() -> None:
    reason = classify_stt_failure(LocalEngineUnavailable("card is full"))
    assert reason == "unavailable"
    assert is_crossable_failure(reason)


def test_the_final_pass_decodes_with_a_beam_and_keeps_the_timings(
    _local_ok: dict[str, Any],
) -> None:
    stt = LocalFinalSTT("large-v3-turbo")

    transcript = asyncio.run(stt.transcribe_pcm(_pcm(2.0), language=None))

    assert _local_ok["args"] == [("large-v3-turbo", FINAL_COMPUTE)]
    assert transcript.text == "Hallo Welt."
    assert transcript.raw_text == "Hallo Welt."
    assert transcript.language == "de"
    assert transcript.segments[-1]["end"] == 2.0
    worker = stt._worker
    assert worker.calls[0]["beam"] == FINAL_BEAM_SIZE
    assert worker.calls[0]["language"] is None
    assert stt.last_used_model == "large-v3-turbo"
    assert stt.provider_label == "faster-whisper"
    assert stt.is_warm


def test_a_full_card_declines_the_spawn_and_crosses_over(
    _local_ok: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import jarvis.hardware.detection as detection

    monkeypatch.setattr(detection, "free_accelerator_gb", lambda: (0.8, "nvml"))
    stt = LocalFinalSTT(min_free_gb=1.5)

    with pytest.raises(LocalEngineUnavailable) as caught:
        asyncio.run(stt.transcribe_pcm(_pcm()))

    assert "0.8 GB" in str(caught.value)
    assert _local_ok["args"] == []  # never spawned
    assert not stt.is_warm


def test_unknown_free_memory_never_blocks_the_spawn(
    _local_ok: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apple unified memory / ROCm have no cheap reading — refusing on a number
    nobody can read would brick every such box (AP-22 shape)."""
    import jarvis.hardware.detection as detection

    monkeypatch.setattr(detection, "free_accelerator_gb", lambda: (0.0, "none"))
    stt = LocalFinalSTT(min_free_gb=1.5)

    transcript = asyncio.run(stt.transcribe_pcm(_pcm()))

    assert transcript.text == "Hallo Welt."


def test_a_cpu_only_worker_is_declined(_local_ok: dict[str, Any]) -> None:
    """A beam-search turbo decode of a 25 s window on a CPU is slower than the
    cloud it would replace; the preview keeps a CPU floor, the final pass not."""
    cpu_worker = _FakeWorker(device="cpu")
    _local_ok["next"] = cpu_worker
    stt = LocalFinalSTT()

    with pytest.raises(LocalEngineUnavailable):
        asyncio.run(stt.transcribe_pcm(_pcm()))

    assert cpu_worker.closed
    assert "CPU" in stt.unavailable_reason


def test_a_missing_runtime_is_declined_without_probing_the_card(
    _local_ok: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import jarvis.dictation.local_preview as preview_mod

    monkeypatch.setattr(preview_mod, "faster_whisper_available", lambda: False)
    stt = LocalFinalSTT()

    stt.warm_up()  # quiet — a warm-up must never raise

    assert not stt.is_warm
    assert "faster-whisper" in stt.unavailable_reason


def test_a_failing_worker_is_replaced_and_the_call_crosses_over(
    _local_ok: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = _FakeWorker(fail="preview worker exited")
    _local_ok["next"] = broken
    stt = LocalFinalSTT()

    with pytest.raises(LocalEngineUnavailable):
        asyncio.run(stt.transcribe_pcm(_pcm()))

    assert broken.closed
    assert not stt.is_warm
    # The retry window keeps a dying worker from being respawned on every press…
    with pytest.raises(LocalEngineUnavailable):
        asyncio.run(stt.transcribe_pcm(_pcm()))
    assert len(_local_ok["args"]) == 1
    # …and ``recover()`` opens it again at once (AP-24: replace, never wait).
    _local_ok["next"] = None
    stt.recover()
    transcript = asyncio.run(stt.transcribe_pcm(_pcm()))
    assert transcript.text == "Hallo Welt."
    assert len(_local_ok["args"]) == 2


def test_a_second_caller_replaces_an_abandoned_worker(
    _local_ok: dict[str, Any],
) -> None:
    """A call that outlived its ceiling left a response in the pipe; reading
    it as the answer to the NEXT press would deliver someone else's words."""
    stt = LocalFinalSTT()
    asyncio.run(stt.transcribe_pcm(_pcm()))
    first_worker = stt._worker
    assert stt._call_lock.acquire(blocking=False)  # simulate the abandoned call
    try:
        with pytest.raises(LocalEngineUnavailable):
            asyncio.run(stt.transcribe_pcm(_pcm()))
    finally:
        stt._call_lock.release()
    assert first_worker.closed
    assert not stt.is_warm


def test_warm_up_spawns_off_the_press_path(_local_ok: dict[str, Any]) -> None:
    stt = LocalFinalSTT()

    stt.warm_up()

    assert stt.is_warm
    assert stt.device == "cuda"
    assert _local_ok["args"] == [(local_final_mod.DEFAULT_FINAL_MODEL, FINAL_COMPUTE)]


def test_the_lane_puts_the_local_engine_in_front_of_the_configured_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chain order: local worker → the configured cloud provider → the other
    keyed families. The configured instance is reused, never built twice."""
    import jarvis.plugins.stt as stt_plugins
    import jarvis.speech.pipeline as pipeline_mod
    import jarvis.speech.stt_dictionary as dictionary
    from jarvis.core.config import DictationConfig, STTConfig
    from jarvis.speech.pipeline import SpeechPipeline
    from jarvis.speech.stt_fallback import FallbackSTT

    configured = SimpleNamespace(name="groq-instance")
    built: list[str] = []
    monkeypatch.setattr(stt_plugins, "build_stt_from_config", lambda cfg, **_k: configured)
    monkeypatch.setattr(
        stt_plugins,
        "build_named_stt_provider",
        lambda name, cfg, **_k: built.append(name) or SimpleNamespace(name=name),
    )
    monkeypatch.setattr(
        pipeline_mod, "_resolve_stt_fallback_chain", lambda *_a, **_k: ("openai-api",)
    )
    monkeypatch.setattr(dictionary, "wrap_stt_with_dictionary", lambda provider: provider)
    import jarvis.dictation.local_preview as preview_mod

    monkeypatch.setattr(preview_mod, "faster_whisper_available", lambda: True)

    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_stt_instance = None
    pipe._utterance_stt = object()
    pipe._dictation_cfg = DictationConfig(local_model="small")
    pipe._config = SimpleNamespace(stt=STTConfig(provider="groq-api"))

    instance = pipe._dictation_stt()

    assert isinstance(instance, FallbackSTT)
    assert isinstance(instance._primary, LocalFinalSTT)
    assert instance._primary.last_used_model == "small"
    assert instance._alternate_names == ["groq-api", "openai-api"]
    assert instance._build("groq-api") is configured
    assert built == []


def test_the_engine_status_names_what_really_answers_the_next_press(
    _local_ok: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four days of dictations ran on a recognizer the settings did not name;
    the status card now says which one is armed and why the local one is not."""
    import jarvis.plugins.stt as stt_plugins
    import jarvis.speech.pipeline as pipeline_mod
    import jarvis.speech.stt_dictionary as dictionary
    from jarvis.core.config import DictationConfig, STTConfig
    from jarvis.speech.pipeline import SpeechPipeline

    monkeypatch.setattr(
        stt_plugins, "build_stt_from_config", lambda cfg, **_k: SimpleNamespace(name="groq")
    )
    monkeypatch.setattr(
        pipeline_mod, "_resolve_stt_fallback_chain", lambda *_a, **_k: ("openai-api",)
    )
    monkeypatch.setattr(dictionary, "wrap_stt_with_dictionary", lambda provider: provider)

    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_stt_instance = None
    pipe._utterance_stt = object()
    pipe._dictation_cfg = DictationConfig()
    pipe._config = SimpleNamespace(stt=STTConfig(provider="groq-api"))

    # Before the worker is up, the configured cloud provider answers.
    before = pipe.dictation_engine_status()
    assert before["local"] is False
    assert before["provider"] == "groq-api"
    assert before["fallback"] == "openai-api"

    # After warm-up the local engine is in front and the cloud is one behind.
    pipe._dictation_stt()._primary.warm_up()
    after = pipe.dictation_engine_status()
    assert after["local"] is True
    assert after["provider"] == "faster-whisper"
    assert after["model"] == local_final_mod.DEFAULT_FINAL_MODEL
    assert after["fallback"] == "groq-api"
    assert after["detail"] == ""


def test_the_engine_status_explains_a_declined_local_engine(
    _local_ok: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import jarvis.hardware.detection as detection
    import jarvis.plugins.stt as stt_plugins
    import jarvis.speech.pipeline as pipeline_mod
    import jarvis.speech.stt_dictionary as dictionary
    from jarvis.core.config import DictationConfig, STTConfig
    from jarvis.speech.pipeline import SpeechPipeline

    monkeypatch.setattr(detection, "free_accelerator_gb", lambda: (0.4, "nvml"))
    monkeypatch.setattr(
        stt_plugins, "build_stt_from_config", lambda cfg, **_k: SimpleNamespace(name="groq")
    )
    monkeypatch.setattr(pipeline_mod, "_resolve_stt_fallback_chain", lambda *_a, **_k: ())
    monkeypatch.setattr(dictionary, "wrap_stt_with_dictionary", lambda provider: provider)

    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_stt_instance = None
    pipe._utterance_stt = object()
    pipe._dictation_cfg = DictationConfig()
    pipe._config = SimpleNamespace(stt=STTConfig(provider="groq-api"))
    pipe._dictation_stt()._primary.warm_up()  # declined: 0.4 GB free

    status = pipe.dictation_engine_status()

    assert status["local"] is False
    assert status["provider"] == "groq-api"
    assert "0.4 GB" in status["detail"]


def test_the_switch_keeps_the_configured_provider_in_front(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.plugins.stt as stt_plugins
    import jarvis.speech.pipeline as pipeline_mod
    import jarvis.speech.stt_dictionary as dictionary
    from jarvis.core.config import DictationConfig, STTConfig
    from jarvis.speech.pipeline import SpeechPipeline
    from jarvis.speech.stt_fallback import FallbackSTT

    configured = SimpleNamespace(name="groq-instance")
    monkeypatch.setattr(stt_plugins, "build_stt_from_config", lambda cfg, **_k: configured)
    monkeypatch.setattr(
        pipeline_mod, "_resolve_stt_fallback_chain", lambda *_a, **_k: ("openai-api",)
    )
    monkeypatch.setattr(dictionary, "wrap_stt_with_dictionary", lambda provider: provider)

    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_stt_instance = None
    pipe._utterance_stt = object()
    pipe._dictation_cfg = DictationConfig(local_engine=False)
    pipe._config = SimpleNamespace(stt=STTConfig(provider="groq-api"))

    instance = pipe._dictation_stt()

    assert isinstance(instance, FallbackSTT)
    assert instance._primary is configured
    assert instance._alternate_names == ["openai-api"]
