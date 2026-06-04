"""Wiring tests for the incomplete-prompt completion feature.

Covers the two structural additions outside the new modules:

* a top-level ``[voice]`` config section with the three completion knobs
  (``completion_detection_enabled`` / ``completion_wait_ms`` /
  ``completion_max_chain``), and
* a new ``TurnTakingState.WAITING_FOR_COMPLETION`` enum value.

Both must be additive — defaults stay backwards-compatible and ``extra="allow"``
keeps an arbitrary future write from breaking boot (AP-16).
"""

from __future__ import annotations

from jarvis.core.config import JarvisConfig, VoiceConfig
from jarvis.speech.pipeline import TurnTakingState


def test_voice_config_has_completion_defaults() -> None:
    # Default raised from 8000 to 15000 (user-mandated 2026-05-26): the
    # earlier 8 s window felt like Jarvis interrupting the user mid-thought.
    # Pair this with the silent-discard policy in _completion_timeout_fire.
    cfg = VoiceConfig()
    assert cfg.completion_detection_enabled is True
    assert cfg.completion_wait_ms == 15000
    assert cfg.completion_max_chain == 3


def test_voice_config_allows_unknown_keys_to_avoid_boot_block() -> None:
    # ConfigDict(extra="allow") — a self-mod / drift-guard write of a key we
    # don't yet model must not crash JarvisConfig validation (AP-16).
    cfg = VoiceConfig.model_validate({"some_future_key": "value"})
    assert cfg.completion_wait_ms == 15000  # known defaults intact


def test_jarvis_config_exposes_voice_subsection() -> None:
    cfg = JarvisConfig()
    assert isinstance(cfg.voice, VoiceConfig)
    assert cfg.voice.completion_detection_enabled is True


def test_turn_taking_state_has_waiting_for_completion() -> None:
    assert TurnTakingState.WAITING_FOR_COMPLETION.value == "WAITING_FOR_COMPLETION"


def test_turn_taking_state_existing_values_unchanged() -> None:
    # Defensive: ensure we did NOT renumber/rename existing states.
    assert TurnTakingState.IDLE.value == "IDLE"
    assert TurnTakingState.LISTENING.value == "LISTENING"
    assert TurnTakingState.USER_SPEAKING.value == "USER_SPEAKING"
    assert TurnTakingState.WAITING_FOR_FINAL_TRANSCRIPT.value == "WAITING_FOR_FINAL_TRANSCRIPT"
    assert TurnTakingState.PROCESSING.value == "PROCESSING"
    assert TurnTakingState.JARVIS_SPEAKING.value == "JARVIS_SPEAKING"
