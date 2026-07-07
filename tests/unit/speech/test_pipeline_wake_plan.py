"""SpeechPipeline honours a resolved WakeWordPlan.

When a wake_plan is threaded in, the OpenWakeWord detector is built from the
plan (model path, canonical keyword, sensitivity-derived threshold) and the
prefix verifier + rolling-whisper use the plan's phrase matcher. When no plan
is given, the legacy default path builds a provider with no model.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

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


def _custom_plan(tmp_path: Path, phrase: str = "Hey Fable"):
    """A user-trained custom_onnx plan — the one OWW path that still exists."""
    onnx = tmp_path / "hey_fable.onnx"
    onnx.write_bytes(b"stub")
    return resolve_wake_plan(
        _wake_cfg(phrase=phrase, engine="custom_onnx", custom_model_path=str(onnx)),
        local_whisper_available=False,
    )


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


def test_plan_builds_oww_from_custom_model(tmp_path: Path) -> None:
    plan = _custom_plan(tmp_path)
    assert plan.engine == "custom_onnx"
    pipe = _pipe(plan)
    assert pipe._wake_matcher is plan.matcher
    assert pipe._wake._keywords == (plan.oww_keyword,)
    assert pipe._wake._model_path == plan.oww_model_path
    assert pipe._wake._threshold == plan.threshold


def test_plan_matcher_drives_prefix_verifier_phrase(tmp_path: Path) -> None:
    plan = _custom_plan(tmp_path)
    pipe = _pipe(plan)
    # The verifier matcher confirms the configured phrase, nothing else.
    assert pipe._wake_matcher.search("hey fable") is not None
    assert pipe._wake_matcher.search("hey jarvis") is None
