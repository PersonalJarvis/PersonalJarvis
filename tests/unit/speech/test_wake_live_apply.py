"""Live-apply of a wake-word change + non-jarvis prefix-verifier skip.

Root cause of "only Hey Jarvis works": the wake model is loaded ONCE at
SpeechPipeline construction, so a UI/toml change never reached the running
detector. ``set_wake_plan`` reconfigures the live wake detection (no app
restart), mirroring the ``set_tts`` live-switch. And the jarvis-specific
prefix verifier must NOT suppress non-jarvis wakes (a German-pinned STT
mis-transcribing "Mycroft"/"Alexa" would otherwise reject valid hits).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from jarvis.speech import wake_constants as wc
from jarvis.speech import wake_phrase as wp
from jarvis.speech.pipeline import SpeechPipeline
from jarvis.speech.wake_phrase import resolve_wake_plan


@pytest.fixture(autouse=True)
def _no_vosk_model(monkeypatch):
    """Isolate from any per-install Vosk model: this module pins the
    stt_match/openwakeword live-apply contracts. vosk_kws has its own suite
    in test_wake_plan_vosk.py."""
    monkeypatch.setattr(wp, "resolve_vosk_model_path", lambda *_: None)


@dataclass
class _FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True

    async def synthesize(
        self, text: str, voice: str | None = None, language_code: str | None = None
    ) -> AsyncIterator:
        if False:  # pragma: no cover
            yield


class _FakeSTT:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def transcribe_pcm(
        self, pcm_bytes: bytes, sample_rate: int = 16_000, language: str | None = None
    ) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(text=self.text)


def _cfg(**kw: object) -> SimpleNamespace:
    base = dict(
        phrase="Hey Jarvis", engine="auto", custom_model_path="",
        sensitivity=0.5, fuzzy_match_ratio=0.8,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _pretend_oww_models_exist(
    monkeypatch: pytest.MonkeyPatch, *model_names: str
) -> None:
    models = set(model_names)
    original_resolve = wc.resolve_oww_model_path

    def fake_resolve(model_name: str) -> str | None:
        if model_name in models:
            return f"C:/fake-openwakeword/{model_name}_v0.1.onnx"
        return original_resolve(model_name)

    monkeypatch.setattr(wc, "resolve_oww_model_path", fake_resolve)
    monkeypatch.setattr(wp, "resolve_oww_model_path", fake_resolve)


def _pipe() -> SpeechPipeline:
    return SpeechPipeline(
        tts=_FakeTTS(), bus=None,
        enable_openwakeword=False, enable_whisper_wake=False,
        enable_local_whisper=False, config=None,
    )


# --------------------------------------------------------------------------
# WakeWordPlan.verify_prefix — only the jarvis family needs STT re-verification
# --------------------------------------------------------------------------

def test_verify_prefix_true_for_jarvis_default() -> None:
    plan = resolve_wake_plan(_cfg(phrase="Hey Jarvis"), local_whisper_available=False)
    assert plan.verify_prefix is True


def test_verify_prefix_false_for_alexa_and_mycroft() -> None:
    for phrase in ("Alexa", "Hey Mycroft", "Rhasspy"):
        plan = resolve_wake_plan(_cfg(phrase=phrase), local_whisper_available=False)
        assert plan.verify_prefix is False, phrase


def test_verify_prefix_false_for_stt_match_custom_name() -> None:
    plan = resolve_wake_plan(_cfg(phrase="Athena"), local_whisper_available=True)
    assert plan.engine == "stt_match"
    assert plan.verify_prefix is False


def test_unknown_phrase_without_model_is_hotkey_only() -> None:
    # Product rule (2026-07-04): an unknown phrase without a local model does NOT
    # degrade to a branded 'Hey Rhasspy' model — the wake word is OFF and the
    # user activates via hotkey / push-to-talk. No detector is armed.
    plan = resolve_wake_plan(_cfg(phrase="Computer"), local_whisper_available=False)
    assert plan.engine == "none"
    assert plan.wake_available is False


# --------------------------------------------------------------------------
# _verify_oww_hit skips the STT re-verification for non-jarvis plans
# --------------------------------------------------------------------------

async def test_verify_oww_hit_trusts_non_jarvis_model_without_stt() -> None:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._require_hey_prefix = True
    stt = _FakeSTT("totally unrelated transcript")
    pipe._utterance_stt = stt
    pipe._wake_plan = SimpleNamespace(verify_prefix=False)
    pipe._wake_matcher = None

    assert await pipe._verify_oww_hit(b"\x00\x00" * 100) is True
    assert stt.calls == 0  # trusted the specific OWW model, no STT re-verify


async def test_verify_oww_hit_still_verifies_jarvis() -> None:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._require_hey_prefix = True
    stt = _FakeSTT("Jarvis")  # bare jarvis -> must be rejected
    pipe._utterance_stt = stt
    pipe._wake_plan = SimpleNamespace(verify_prefix=True)
    pipe._wake_matcher = None  # None -> default jarvis pattern in verifier

    assert await pipe._verify_oww_hit(b"\x00\x00" * 100) is False
    assert stt.calls == 1


# --------------------------------------------------------------------------
# set_wake_plan — live reconfiguration (no restart)
# --------------------------------------------------------------------------

def test_set_wake_plan_live_swaps_to_pretrained_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe()
    _pretend_oww_models_exist(monkeypatch, "alexa")
    plan = resolve_wake_plan(_cfg(phrase="Alexa"), local_whisper_available=False)
    pipe.set_wake_plan(plan)

    assert pipe._wake._keywords == ("alexa",)
    assert pipe._wake._model_path == plan.oww_model_path
    assert pipe._wake_matcher is plan.matcher
    assert pipe._openwakeword_enabled is True
    assert pipe._wake_phrase_label == "Alexa"
    assert pipe._wake_reload_event.is_set()  # running wake loop will re-arm


def test_set_wake_plan_stt_match_enables_whisper_disables_oww() -> None:
    pipe = _pipe()
    plan = resolve_wake_plan(_cfg(phrase="Computer"), local_whisper_available=True)
    pipe.set_wake_plan(plan)

    assert pipe._openwakeword_enabled is False
    assert pipe._whisper_wake_enabled is True
    assert pipe._whisper_wake is not None
    assert pipe._stt is not None  # local Whisper was built for the custom phrase
    assert pipe._wake_reload_event.is_set()


def test_set_wake_plan_switch_back_to_jarvis_reenables_oww() -> None:
    pipe = _pipe()
    pipe.set_wake_plan(resolve_wake_plan(_cfg(phrase="Computer"), local_whisper_available=True))
    pipe._wake_reload_event.clear()
    pipe.set_wake_plan(resolve_wake_plan(_cfg(phrase="Hey Jarvis"), local_whisper_available=True))

    assert pipe._openwakeword_enabled is True
    assert pipe._wake._keywords == ("hey_jarvis",)
    assert pipe._whisper_wake_enabled is False
    assert pipe._wake_reload_event.is_set()
