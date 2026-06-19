"""Bug-C-Tests (2026-04-29): BrainTurnCompleted enthaelt provider/model.

Hintergrund: Vorher schrieb der SessionRecorder provider/model aus
BrainTurnStarted in voice_turns. Bei Fallback-Chain (5 Provider-Versuche
hintereinander) gewann der LETZTE Started-Event — auch wenn der Call
crashte. Resultat: voice_turns hatte Halluzinations-Tags wie
"openai/gpt-4o" obwohl kein OpenAI-Key existierte.

Fix: BrainTurnCompleted hat jetzt provider/model. Recorder zieht von dort.
Manager.generate publisht beide Events nur wenn der Stream wirklich Daten
lieferte.
"""
from __future__ import annotations

from jarvis.core.events import BrainTurnCompleted, BrainTurnStarted


def test_brain_turn_completed_has_provider_field() -> None:
    e = BrainTurnCompleted(
        provider="grok",
        model="grok-4.1-fast",
        tokens_in=100,
        tokens_out=20,
        cost_usd=0.04,
        text_len=50,
        finish_reason="ok",
    )
    assert e.provider == "grok"
    assert e.model == "grok-4.1-fast"


def test_brain_turn_completed_provider_optional() -> None:
    """Backwards-Compat: alte Caller ohne provider/model funktionieren weiter."""
    e = BrainTurnCompleted(tokens_in=100, tokens_out=20)
    assert e.provider == ""
    assert e.model == ""


def test_brain_turn_started_unchanged() -> None:
    """Schema von BrainTurnStarted bleibt rueckwaerts-kompatibel."""
    e = BrainTurnStarted(
        provider="gemini",
        model="gemini-3-flash",
        intent_level="fast",
    )
    assert e.provider == "gemini"
    assert e.model == "gemini-3-flash"
    assert e.intent_level == "fast"
