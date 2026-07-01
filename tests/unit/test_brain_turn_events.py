"""Bug-C tests (2026-04-29): BrainTurnCompleted now carries provider/model.

Background: previously the SessionRecorder wrote provider/model from
BrainTurnStarted into voice_turns. In a fallback chain (5 consecutive
provider attempts), the LAST Started event won — even if that call
crashed. Result: voice_turns ended up with hallucinated tags like
"openai/gpt-4o" even though no OpenAI key existed.

Fix: BrainTurnCompleted now carries provider/model. The recorder reads
from there. Manager.generate only publishes both events when the stream
actually delivered data.
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
    """Backwards-compat: old callers without provider/model keep working."""
    e = BrainTurnCompleted(tokens_in=100, tokens_out=20)
    assert e.provider == ""
    assert e.model == ""


def test_brain_turn_started_unchanged() -> None:
    """Schema of BrainTurnStarted stays backwards-compatible."""
    e = BrainTurnStarted(
        provider="gemini",
        model="gemini-3-flash",
        intent_level="fast",
    )
    assert e.provider == "gemini"
    assert e.model == "gemini-3-flash"
    assert e.intent_level == "fast"
