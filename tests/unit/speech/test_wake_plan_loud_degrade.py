"""Task 5 (B3): a plain custom phrase served ONLY by stt_match (local-Whisper
transcript match) must be a LOUD degrade, not a silent success.

AP-27: the base Whisper model structurally cannot recognize a hard proper
noun reliably via transcription — it garbles it on speech and can hallucinate
it on silence. Landing there for an ordinary custom word must surface a clear
`degraded=True` + remedy message (install the Vosk any-word model), not report
success as if the wake word just works.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.speech.wake_phrase import resolve_wake_plan


def _cfg(phrase: str, engine: str = "auto") -> SimpleNamespace:
    return SimpleNamespace(
        phrase=phrase,
        engine=engine,
        custom_model_path="",
        sensitivity=0.5,
        fuzzy_match_ratio=0.8,
    )


def test_stt_match_custom_word_is_loudly_degraded() -> None:
    # No vosk model, whisper present -> lands on stt_match. For a custom word
    # this must be a LOUD degrade, not silent success.
    plan = resolve_wake_plan(
        _cfg("Athena"), local_whisper_available=True, vosk_available=False
    )
    assert plan.engine == "stt_match"
    assert plan.degraded is True
    assert "vosk" in plan.message.lower() or "reliable" in plan.message.lower()


def test_vosk_preferred_over_stt_match_when_available(monkeypatch) -> None:
    import jarvis.speech.wake_phrase as wp

    monkeypatch.setattr(wp, "resolve_vosk_model_path", lambda lang: "/fake/de")
    plan = resolve_wake_plan(
        _cfg("Athena"),
        local_whisper_available=True,
        vosk_available=True,
        language="de",
    )
    assert plan.engine == "vosk_kws"
    assert plan.degraded is False
