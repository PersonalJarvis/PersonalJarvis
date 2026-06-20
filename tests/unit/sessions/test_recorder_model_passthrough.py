"""Regression guards for the per-user model-selection feature (2026-06-20).

A sibling session is adding a UI where the user picks the concrete model per
brain provider. Gemini (Flash vs. Pro) is only the worked example — the same
must hold for EVERY brain provider (claude-api, openrouter, openai, gemini,
grok). The transcription view must show the model that REALLY ran, not a
hard-wired default: the recorder takes ``provider``/``model`` verbatim from the
completed brain turn, so the contract is inherently provider-agnostic. These
tests pin that across all providers so a refactor of the selection feature
cannot let the transcript diverge from the executed model for any of them.

Two invariants, each checked for all providers:
  1. The (provider, model) carried on ``BrainTurnCompleted`` lands verbatim in
     ``VoiceTurnRow`` (the values shown as the transcript badges).
  2. ``tier`` and ``model`` are independent axes — a fast-tier turn running on a
     deep/Pro model must record BOTH faithfully. No layer may infer the tier
     from the model name (or vice versa).
"""
from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    BrainTurnCompleted,
    BrainTurnStarted,
    ListeningStarted,
    TranscriptFinal,
    VoiceSessionEnded,
    VoiceSessionStarted,
)
from jarvis.core.protocols import Transcript
from jarvis.sessions.recorder import SessionRecorder
from jarvis.sessions.store import SessionStore

# One representative (provider, model) per configured brain provider. The model
# names are arbitrary from the recorder's perspective (it is event-driven), so
# these are realistic frontier picks per provider — Gemini is just one row.
PROVIDER_MODEL_CASES = [
    ("claude-api", "claude-opus-4-8"),
    ("openrouter", "anthropic/claude-opus-4.8"),
    ("openai", "gpt-5.5-pro"),
    ("gemini", "gemini-3.1-pro-preview"),
    ("grok", "grok-4.3"),
]


def _final(text: str, lang: str = "de") -> TranscriptFinal:
    return TranscriptFinal(
        source_layer="speech.stt",
        transcript=Transcript(
            text=text, language=lang, confidence=0.9, is_partial=False
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,model", PROVIDER_MODEL_CASES)
async def test_user_selected_model_is_recorded_verbatim(provider, model, tmp_path) -> None:
    """For EVERY provider, the model on the completed brain turn flows 1:1 into
    the row. Uses a non-default model per provider so the test would fail if the
    recorder ever substituted a configured/guessed default instead of the name
    the brain actually reported."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)

        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="s-model",
                wake_keyword="hey_jarvis",
                language="de",
            )
        )
        await bus.publish(ListeningStarted(source_layer="speech"))
        await bus.publish(_final("was geht ab"))
        await bus.publish(
            BrainTurnCompleted(
                source_layer="brain",
                provider=provider,
                model=model,
                finish_reason="stop",
            )
        )
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="s-model",
                hangup_reason="voice_pattern",
            )
        )

        turns = store.get_turns("s-model")
        assert len(turns) == 1
        assert turns[0].provider == provider
        assert turns[0].model == model
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,model", PROVIDER_MODEL_CASES)
async def test_tier_and_model_are_independent_axes(provider, model, tmp_path) -> None:
    """For EVERY provider, a fast-tier turn may run on a deep/Pro model when the
    user picks that model for the fast tier. Both must be recorded faithfully
    and separately — the transcript must never infer the tier from the model
    name."""
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)

        await bus.publish(
            VoiceSessionStarted(
                source_layer="speech.pipeline",
                session_id="s-axes",
                wake_keyword="hey_jarvis",
                language="de",
            )
        )
        await bus.publish(ListeningStarted(source_layer="speech"))
        await bus.publish(_final("mask it up"))
        # Router classified this as a fast turn ...
        await bus.publish(
            BrainTurnStarted(source_layer="brain", intent_level="fast")
        )
        # ... but the user selected the deep/Pro model for that provider.
        await bus.publish(
            BrainTurnCompleted(
                source_layer="brain",
                provider=provider,
                model=model,
                finish_reason="stop",
            )
        )
        await bus.publish(
            VoiceSessionEnded(
                source_layer="speech.pipeline",
                session_id="s-axes",
                hangup_reason="voice_pattern",
            )
        )

        turns = store.get_turns("s-axes")
        assert len(turns) == 1
        assert turns[0].tier == "fast"
        assert turns[0].provider == provider
        assert turns[0].model == model
    finally:
        store.close()
