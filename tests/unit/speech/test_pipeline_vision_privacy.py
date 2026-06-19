"""Wave-2 B7: Vision-Privacy-Hooks in SpeechPipeline.

Testet die extrahierten Helper-Methods (_vision_cfg, _match_privacy_phrase,
_maybe_toggle_vision_on_state) direkt auf einer Pipeline-Instanz via
`__new__`, ohne den vollen STT/TTS/Wake-Bootstrap zu durchlaufen.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.speech.pipeline import (
    SpeechPipeline,
    _is_non_substantive_response,
    _smalltalk_fallback_for_non_substantive,
)


class _FakeProvider:
    def __init__(self) -> None:
        self.paused = False
        self.paused_count = 0
        self.resumed_count = 0

    def pause(self) -> None:
        self.paused = True
        self.paused_count += 1

    def resume(self) -> None:
        self.paused = False
        self.resumed_count += 1

    @property
    def is_paused(self) -> bool:
        return self.paused


def _make_pipeline(*, provider=None, vision_cfg=None) -> SpeechPipeline:
    """Baut eine nackte Pipeline-Instanz ohne Bootstrap.

    Nutzt `__new__` + manuelle Attribut-Injection — reicht fuer die Helper-
    Method-Tests. Vermeidet STT/TTS/Wake-Init.
    """
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._vision_provider = provider
    if vision_cfg is None:
        pipe._config = None
    else:
        cfg = SimpleNamespace(brain=SimpleNamespace(router=SimpleNamespace(vision=vision_cfg)))
        pipe._config = cfg
    pipe._supervisor = None
    return pipe


def _default_vision_cfg(pause_on_idle: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True,
        pause_on_idle=pause_on_idle,
        voice_pause_phrase_de="privacy",
        voice_pause_phrase_en="privacy mode",
        voice_resume_phrase_de="du darfst wieder sehen",
        voice_resume_phrase_en="vision back on",
    )


# ----------------------------------------------------------------------
# _maybe_toggle_vision_on_state
# ----------------------------------------------------------------------


def test_pipeline_pauses_vision_on_idle_transition():
    prov = _FakeProvider()
    pipe = _make_pipeline(provider=prov, vision_cfg=_default_vision_cfg())
    pipe._maybe_toggle_vision_on_state("IDLE")
    assert prov.paused_count == 1
    assert prov.paused is True


def test_pipeline_resumes_vision_on_active_transition():
    prov = _FakeProvider()
    prov.pause()  # start paused
    prov.paused_count = 0  # reset counter for the test
    pipe = _make_pipeline(provider=prov, vision_cfg=_default_vision_cfg())
    pipe._maybe_toggle_vision_on_state("LISTENING")
    assert prov.resumed_count == 1
    assert prov.paused is False

    pipe._maybe_toggle_vision_on_state("THINKING")
    assert prov.resumed_count == 2


def test_pipeline_vision_hook_noop_when_pause_on_idle_false():
    prov = _FakeProvider()
    pipe = _make_pipeline(provider=prov, vision_cfg=_default_vision_cfg(pause_on_idle=False))
    pipe._maybe_toggle_vision_on_state("IDLE")
    pipe._maybe_toggle_vision_on_state("LISTENING")
    assert prov.paused_count == 0
    assert prov.resumed_count == 0


def test_pipeline_vision_hook_noop_when_no_provider():
    pipe = _make_pipeline(provider=None, vision_cfg=_default_vision_cfg())
    # Darf nicht crashen
    pipe._maybe_toggle_vision_on_state("IDLE")
    pipe._maybe_toggle_vision_on_state("LISTENING")


# ----------------------------------------------------------------------
# _match_privacy_phrase
# ----------------------------------------------------------------------


def test_voice_phrase_privacy_returns_pause():
    pipe = _make_pipeline(provider=_FakeProvider(), vision_cfg=_default_vision_cfg())
    assert pipe._match_privacy_phrase("Jarvis, privacy") == "pause"
    assert pipe._match_privacy_phrase("privacy mode please") == "pause"


def test_voice_phrase_vision_back_on_returns_resume():
    pipe = _make_pipeline(provider=_FakeProvider(), vision_cfg=_default_vision_cfg())
    assert pipe._match_privacy_phrase("du darfst wieder sehen") == "resume"
    assert pipe._match_privacy_phrase("Jarvis, vision back on") == "resume"


def test_voice_phrase_non_match_returns_none():
    pipe = _make_pipeline(provider=_FakeProvider(), vision_cfg=_default_vision_cfg())
    assert pipe._match_privacy_phrase("was ist das") is None
    assert pipe._match_privacy_phrase("hallo") is None


def test_voice_phrase_match_returns_none_when_no_config():
    pipe = _make_pipeline(provider=_FakeProvider(), vision_cfg=None)
    assert pipe._match_privacy_phrase("privacy") is None


# ----------------------------------------------------------------------
# Smoke-Test: __init__ nimmt kwargs
# ----------------------------------------------------------------------


def test_pipeline_init_accepts_vision_kwargs():
    """__init__-Signatur akzeptiert config + vision_provider ohne Fehler.

    Wir instanziieren mit minimal-valid Komponenten; wenn die Kwargs fehlen
    wuerden, waere das ein TypeError. Reiner Signatur-Smoke.
    """
    import inspect
    sig = inspect.signature(SpeechPipeline.__init__)
    assert "config" in sig.parameters
    assert "vision_provider" in sig.parameters
    assert "activation_gate" in sig.parameters


def test_activation_gate_defaults_to_allowed():
    pipe = _make_pipeline()
    pipe._activation_gate = lambda: True
    assert pipe._activation_allowed() is True


def test_activation_gate_fails_closed():
    pipe = _make_pipeline()

    def _boom() -> bool:
        raise RuntimeError("bad gate")

    pipe._activation_gate = _boom
    assert pipe._activation_allowed() is False


def test_readiness_phrase_is_non_substantive():
    assert _is_non_substantive_response("Ich bin einsatzbereit.")


def test_wellbeing_question_gets_smalltalk_fallback():
    assert (
        _smalltalk_fallback_for_non_substantive("Wie geht's dir?", "de")
        == "Mir geht's gut, Ruben. Was machen wir als Naechstes?"
    )


def test_smalltalk_fallback_only_for_wellbeing_questions():
    assert _smalltalk_fallback_for_non_substantive("Oeffne Chrome", "de") is None
