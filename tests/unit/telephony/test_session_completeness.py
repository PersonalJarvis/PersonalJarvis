"""TDD tests for completeness-gating in TelephonyCallSession._run_turn.

Tests are written BEFORE the implementation (red → green TDD discipline).
All behavior mirrors spec §5 for the telephony surface:
  - COMPLETE  → dispatch text to the brain (combining any pending buffer).
  - INCOMPLETE → no brain call; speak "Mhm?"; append fragment to pending.
  - ABRUPT_ABORT → no brain call; speak "Okay."; clear pending.
  - Discard timer expires → clear pending, NO brain dispatch.
  - Classifier raises → fail-open (dispatch to brain).
  - enabled=False → bypass gating entirely (existing behavior).

The helper ``_FakeConfig`` supplies the defensive attribute-chain the
implementation reads via ``getattr(getattr(getattr(...), ...), ..., default)``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest

from jarvis.telephony.session import TelephonyCallSession
from tests.fakes.fake_telephony_stack import FakeBrain, FakeSTT, FakeTTS

# ---------------------------------------------------------------------------
# Minimal config fake
# ---------------------------------------------------------------------------


@dataclass
class _CompletenessConfig:
    enabled: bool = True
    pending_discard_s: float = 8.0
    max_pending_fragments: int = 2


@dataclass
class _SpeechConfig:
    completeness: _CompletenessConfig = field(default_factory=_CompletenessConfig)


@dataclass
class _FakeConfig:
    speech: _SpeechConfig = field(default_factory=_SpeechConfig)


# ---------------------------------------------------------------------------
# TTS recording wrapper: captures spoken text without executing real synthesis
# ---------------------------------------------------------------------------


class _RecordingTTS:
    """Wraps FakeTTS and records every synthesize() call text in order."""

    def __init__(self) -> None:
        self._inner = FakeTTS(ms_per_char=1)
        self.spoken: list[str] = []

    async def synthesize(
        self, text: str, language_code: str = "de-DE", voice: str | None = None
    ) -> Any:
        self.spoken.append(text)
        async for chunk in self._inner.synthesize(text, language_code):
            yield chunk


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------


class _Sink:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, msg: dict) -> None:
        self.messages.append(msg)


def _make_session(
    *,
    stt_texts: list[str] | None = None,
    brain_response: str = "Antwort.",
    language_code: str = "de-DE",
    config: Any = None,
    tts: Any = None,
) -> tuple[TelephonyCallSession, FakeBrain, _RecordingTTS | FakeTTS, _Sink]:
    sink = _Sink()
    brain = FakeBrain(brain_response)
    tts_instance = tts if tts is not None else _RecordingTTS()
    session = TelephonyCallSession(
        call_sid="CA-test",
        stream_sid="MZ-test",
        send=sink.send,
        stt=FakeSTT(stt_texts or ["Wie spät ist es?"]),  # i18n-allow
        brain=brain,
        tts=tts_instance,
        language_code=language_code,
    )
    if config is not None:
        session._config = config
    return session, brain, tts_instance, sink


# ---------------------------------------------------------------------------
# Helper: directly invoke _run_turn (bypasses audio path for clarity)
# ---------------------------------------------------------------------------


async def _run_turn_direct(session: TelephonyCallSession, text: str) -> None:
    """Invoke _run_turn with a synthetic PCM utterance whose transcript is ``text``.

    We monkey-patch _transcribe so no real audio is needed.
    """
    original_transcribe = session._transcribe

    async def _fake_transcribe(_pcm: bytes) -> str:
        return text

    session._transcribe = _fake_transcribe
    dummy_pcm = b"\x00" * 320  # 10 ms of silence at 16 kHz
    await session._run_turn(dummy_pcm)
    session._transcribe = original_transcribe


# ===========================================================================
# Test cases
# ===========================================================================


# ---------------------------------------------------------------------------
# 1. COMPLETE — passes through; brain called exactly once with the text
# ---------------------------------------------------------------------------


async def test_complete_utterance_dispatches_to_brain():
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg)

    await _run_turn_direct(session, "Öffne Chrome")  # i18n-allow

    assert brain.prompts == ["Öffne Chrome"]  # i18n-allow


async def test_complete_utterance_with_nonempty_pending_dispatches_combined():
    """If pending buffer has a fragment, COMPLETE combines and dispatches the pair."""
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg)

    # Manually plant a pending fragment
    session._pending_completeness_fragments = ["Send a mail to"]

    await _run_turn_direct(session, "john@example.com please")

    # Brain should receive the combined text
    assert len(brain.prompts) == 1
    combined = brain.prompts[0]
    assert "Send a mail to" in combined
    assert "john@example.com please" in combined

    # Pending cleared after dispatch
    assert session._pending_completeness_fragments == []


async def test_complete_combined_still_complete_clears_pending_and_dispatches():
    """Two-turn completion: buffer + completing utterance → combined to brain once."""
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg)

    # Turn 1: INCOMPLETE (trailing "to")
    await _run_turn_direct(session, "Send a mail to")
    assert brain.prompts == []  # no brain call on INCOMPLETE
    assert len(session._pending_completeness_fragments) == 1

    # Turn 2: completion text that makes the full sentence COMPLETE
    await _run_turn_direct(session, "alice@example.com please")
    assert len(brain.prompts) == 1
    combined = brain.prompts[0]
    assert "Send a mail to" in combined
    assert "alice@example.com please" in combined
    assert session._pending_completeness_fragments == []


# ---------------------------------------------------------------------------
# 2. INCOMPLETE — no brain call; "Mhm?" spoken; fragment appended to pending
# ---------------------------------------------------------------------------


async def test_incomplete_utterance_no_brain_call():
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg)

    await _run_turn_direct(session, "Schreib eine Mail an")  # trailing "an" — but "an" not in dangling
    # "an" is excluded from _DANGLING; use a definite INCOMPLETE trigger
    # Re-test with "und" which IS in the dangling set
    await _run_turn_direct(session, "Kauf Milch und")

    # Brain should not have been called for the incomplete
    # (First utterance is complete by default, second is INCOMPLETE)
    assert "Kauf Milch und" not in brain.prompts


async def test_incomplete_utterance_speaks_mhm():
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg)

    await _run_turn_direct(session, "Kauf Milch und")

    assert isinstance(tts, _RecordingTTS)
    # "Mhm?" should have been spoken (as a cue)
    assert any("Mhm" in s for s in tts.spoken), f"Expected 'Mhm?' in spoken, got: {tts.spoken}"


async def test_incomplete_fragment_appended_to_pending():
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg)

    await _run_turn_direct(session, "Ich brauche einen")

    assert len(session._pending_completeness_fragments) == 1
    assert session._pending_completeness_fragments[0] == "Ich brauche einen"


async def test_incomplete_does_not_dispatch_to_brain():
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg)

    await _run_turn_direct(session, "Ich glaube dass")

    assert brain.prompts == []


# ---------------------------------------------------------------------------
# 3. INCOMPLETE followed by completion — brain gets combined text exactly once
# ---------------------------------------------------------------------------


async def test_two_turn_completion_brain_receives_combined_text():
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg)

    # Turn 1: trailing subordinator → INCOMPLETE
    await _run_turn_direct(session, "Öffne mal eine")  # i18n-allow
    assert brain.prompts == []

    # Turn 2: completing content (no dangling token, no abort phrase)
    await _run_turn_direct(session, "Datei im Explorer")
    assert len(brain.prompts) == 1
    combined = brain.prompts[0]
    assert "Öffne mal eine" in combined  # i18n-allow
    assert "Datei im Explorer" in combined


async def test_two_turn_completion_pending_cleared_after_dispatch():
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg)

    await _run_turn_direct(session, "Ich brauche einen")
    await _run_turn_direct(session, "Hammer bitte")

    assert session._pending_completeness_fragments == []


# ---------------------------------------------------------------------------
# 4. ABRUPT_ABORT — no brain call; "Okay." spoken; pending cleared
# ---------------------------------------------------------------------------


async def test_abort_does_not_dispatch_to_brain():
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg)

    await _run_turn_direct(session, "never mind")

    assert brain.prompts == []


async def test_abort_speaks_okay():
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg)

    await _run_turn_direct(session, "never mind")

    assert isinstance(tts, _RecordingTTS)
    assert any("Okay" in s for s in tts.spoken), f"Expected 'Okay' in spoken, got: {tts.spoken}"


async def test_abort_clears_pending():
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg)

    # Plant pending fragment
    session._pending_completeness_fragments = ["Send it to"]

    await _run_turn_direct(session, "forget it")

    assert session._pending_completeness_fragments == []


async def test_abort_german_clears_pending():
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg)
    session._pending_completeness_fragments = ["Schreib eine Mail an"]

    await _run_turn_direct(session, "vergiss es")

    assert session._pending_completeness_fragments == []
    assert brain.prompts == []


# ---------------------------------------------------------------------------
# 5. Discard timer expires → pending cleared, NO brain dispatch
# ---------------------------------------------------------------------------


async def test_discard_timer_clears_pending_without_dispatching_to_brain():
    """Regression: the old auto-flush timer sent a half-command to the brain.
    The new timer is DISCARD-ONLY — expiry must never dispatch to the brain."""
    cfg = _FakeConfig()
    cfg.speech.completeness.pending_discard_s = 0.5  # timer fires after assertions

    session, brain, tts, _sink = _make_session(config=cfg)

    # Plant the pending fragment directly (avoids race with the cue TTS timing)
    session._pending_completeness_fragments = ["Ich brauche einen"]
    # Arm the real discard timer
    session._rearm_discard_timer(0.05)

    assert len(session._pending_completeness_fragments) == 1
    assert brain.prompts == []  # no dispatch yet

    # Wait for the discard timer to fire
    await asyncio.sleep(0.15)

    # Pending buffer cleared
    assert session._pending_completeness_fragments == []
    # Brain still not called — discard-only, never flush
    assert brain.prompts == []


async def test_discard_timer_does_not_dispatch_even_if_long_wait():
    """Even a very long wait after INCOMPLETE must never auto-dispatch."""
    cfg = _FakeConfig()
    cfg.speech.completeness.pending_discard_s = 0.05

    session, brain, tts, _sink = _make_session(config=cfg)

    await _run_turn_direct(session, "Kauf Milch und")
    await asyncio.sleep(0.2)

    assert brain.prompts == []


# ---------------------------------------------------------------------------
# 6. Classifier raises → fail-open (dispatch to brain)
# ---------------------------------------------------------------------------


async def test_classifier_exception_fails_open_to_brain():
    """If classify_completeness raises, the turn falls through to brain dispatch."""
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg)

    with patch(
        "jarvis.telephony.session.classify_completeness",
        side_effect=RuntimeError("boom"),
    ):
        await _run_turn_direct(session, "Öffne Chrome")  # i18n-allow

    # Fail-open: brain must have been called
    assert brain.prompts == ["Öffne Chrome"]  # i18n-allow


# ---------------------------------------------------------------------------
# 7. enabled=False → bypass gating, existing behavior
# ---------------------------------------------------------------------------


async def test_kill_switch_disabled_bypasses_gating():
    """With enabled=False, incomplete utterances still dispatch to the brain."""
    cfg = _FakeConfig()
    cfg.speech.completeness.enabled = False

    session, brain, tts, _sink = _make_session(config=cfg)

    # "Kauf Milch und" would be INCOMPLETE when the gate is on
    await _run_turn_direct(session, "Kauf Milch und")

    # Kill-switch off → brain receives the text unchanged
    assert brain.prompts == ["Kauf Milch und"]


async def test_kill_switch_disabled_abort_phrase_still_dispatches():
    """With enabled=False, abort phrases also dispatch normally."""
    cfg = _FakeConfig()
    cfg.speech.completeness.enabled = False

    session, brain, tts, _sink = _make_session(config=cfg)

    await _run_turn_direct(session, "never mind")

    assert brain.prompts == ["never mind"]


# ---------------------------------------------------------------------------
# 8. max_pending_fragments bound — oldest entry discarded on overflow
# ---------------------------------------------------------------------------


async def test_pending_buffer_bounded_by_max_fragments():
    cfg = _FakeConfig()
    cfg.speech.completeness.max_pending_fragments = 2

    session, brain, tts, _sink = _make_session(config=cfg)

    # Three consecutive INCOMPLETE utterances
    await _run_turn_direct(session, "Kauf Milch und")
    await _run_turn_direct(session, "Ich brauche einen")
    await _run_turn_direct(session, "Jarvis wenn")

    # Buffer capped at max_pending_fragments=2, oldest entry discarded
    assert len(session._pending_completeness_fragments) <= 2


# ---------------------------------------------------------------------------
# 9. Language-specific cues
# ---------------------------------------------------------------------------


async def test_incomplete_cue_german():
    """German session speaks 'Mhm?' on INCOMPLETE."""
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg, language_code="de-DE")

    await _run_turn_direct(session, "Kauf Milch und")

    assert isinstance(tts, _RecordingTTS)
    assert any("Mhm" in s for s in tts.spoken)


async def test_incomplete_cue_english():
    """English session speaks 'Mhm?' on INCOMPLETE."""
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg, language_code="en-US")

    await _run_turn_direct(session, "Open the")  # trailing "the" → INCOMPLETE

    assert isinstance(tts, _RecordingTTS)
    assert any("Mhm" in s for s in tts.spoken)


async def test_abort_cue_english():
    """English session speaks 'Okay.' on ABRUPT_ABORT."""
    cfg = _FakeConfig()
    session, brain, tts, _sink = _make_session(config=cfg, language_code="en-US")

    await _run_turn_direct(session, "forget it")

    assert isinstance(tts, _RecordingTTS)
    assert any("Okay" in s for s in tts.spoken)


# ---------------------------------------------------------------------------
# 10. No-config defensive path (config attribute missing)
# ---------------------------------------------------------------------------


async def test_no_config_attribute_uses_defaults():
    """Session with no _config attribute at all falls back to defaults (enabled=True)."""
    session, brain, tts, _sink = _make_session()
    # Ensure no _config at all
    if hasattr(session, "_config"):
        del session._config

    # COMPLETE utterance should still dispatch
    await _run_turn_direct(session, "Öffne Chrome")  # i18n-allow
    assert brain.prompts == ["Öffne Chrome"]  # i18n-allow


async def test_no_config_attribute_incomplete_still_gates():
    """Without _config, defaults apply (enabled=True); INCOMPLETE still gates."""
    session, brain, tts, _sink = _make_session()
    if hasattr(session, "_config"):
        del session._config

    await _run_turn_direct(session, "Kauf Milch und")
    assert brain.prompts == []
