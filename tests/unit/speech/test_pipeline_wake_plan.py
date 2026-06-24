"""SpeechPipeline honours a resolved WakeWordPlan.

When a wake_plan is threaded in, the OpenWakeWord detector is built from the
plan (model path, canonical keyword, sensitivity-derived threshold) and the
prefix verifier + rolling-whisper use the plan's phrase matcher. When no plan
is given, everything is byte-identical to the legacy "Hey Jarvis" path.
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


@dataclass
class _FakeTTS:
    name: str = "fake-tts"
    supports_streaming: bool = True

    async def synthesize(
        self, text: str, voice: str | None = None, language_code: str | None = None
    ) -> AsyncIterator:
        if False:  # pragma: no cover
            yield


def _wake_cfg(**kw: object) -> SimpleNamespace:
    base = dict(
        phrase="Hey Jarvis",
        engine="auto",
        custom_model_path="",
        sensitivity=0.5,
        fuzzy_match_ratio=0.8,
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


def _pipe(wake_plan: object | None) -> SpeechPipeline:
    return SpeechPipeline(
        tts=_FakeTTS(),
        bus=None,
        enable_openwakeword=False,
        enable_whisper_wake=False,
        enable_local_whisper=False,
        config=None,
        wake_plan=wake_plan,
    )


def test_no_plan_keeps_matcher_none_legacy_behaviour() -> None:
    pipe = _pipe(None)
    assert pipe._wake_matcher is None
    # Default OWW keyword unchanged.
    assert pipe._wake._keywords == ("hey_jarvis",)


def test_plan_builds_oww_from_pretrained_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pretend_oww_models_exist(monkeypatch, "alexa")

    plan = resolve_wake_plan(_wake_cfg(phrase="Alexa"), local_whisper_available=False)
    pipe = _pipe(plan)
    assert pipe._wake_matcher is plan.matcher
    assert pipe._wake._keywords == ("alexa",)
    assert pipe._wake._model_path == plan.oww_model_path
    assert pipe._wake._threshold == plan.threshold


def test_plan_matcher_drives_prefix_verifier_phrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pretend_oww_models_exist(monkeypatch, "alexa")

    plan = resolve_wake_plan(_wake_cfg(phrase="Alexa"), local_whisper_available=False)
    pipe = _pipe(plan)
    # The verifier matcher now confirms "alexa", not "jarvis".
    assert pipe._wake_matcher.search("alexa") is not None
    assert pipe._wake_matcher.search("hey jarvis") is None
