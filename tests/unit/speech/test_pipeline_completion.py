"""Integration tests for the incomplete-prompt completion buffer in the pipeline.

Covers ``SpeechPipeline._complete_or_buffer_context`` and its timeout-flush
helpers per
``docs/superpowers/specs/2026-05-25-incomplete-prompt-completion-design.md``.

Top directives under test:

* **Precision over recall** — a complete prompt must return unchanged (zero
  added latency, zero held-back prompts).
* **AD-OE6 / zero silent drops** — the per-gap timeout MUST result in Jarvis
  saying *something* (a short follow-up cue), never silent discard.
* **Hangup takes precedence** (delegated to the existing ``HANGUP_RE`` path,
  not exercised here).
* **Max-chain bound** — no infinite chained waits.

Uses the same ``SpeechPipeline.__new__`` stubbing pattern as ``test_turn_taking``
and the sibling completeness tests, with our own attribute names
(``_completion_buffer`` / ``_completion_timeout_task``) — distinct from the
parallel-session attributes (``_pending_user_context``).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from jarvis.speech.pending_buffer import PendingPromptBuffer
from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState


def _make_pipe(
    *,
    enabled: bool = True,
    wait_ms: int = 8000,
    max_chain: int = 3,
) -> SpeechPipeline:
    """Minimal ``SpeechPipeline`` stub for the completion buffer methods."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._completion_buffer = PendingPromptBuffer()
    pipe._completion_timeout_task = None
    pipe._turn_state = TurnTakingState.LISTENING

    voice_cfg = MagicMock()
    voice_cfg.completion_detection_enabled = enabled
    voice_cfg.completion_wait_ms = wait_ms
    voice_cfg.completion_max_chain = max_chain
    cfg = MagicMock()
    cfg.voice = voice_cfg
    pipe._config = cfg

    pipe._spoken: list[tuple[str, str | None]] = []
    pipe._state_history: list[TurnTakingState] = []

    async def _fake_speak(text: str, language: str | None = None) -> bool:
        pipe._spoken.append((text, language))
        return True

    async def _fake_set_turn_state(state: TurnTakingState) -> None:
        pipe._state_history.append(state)
        pipe._turn_state = state

    pipe._speak = _fake_speak  # type: ignore[method-assign]
    pipe._set_turn_state = _fake_set_turn_state  # type: ignore[method-assign]
    return pipe


# --------------------------------------------------------------------------- #
# Precision (top directive) — complete and disabled paths                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_feature_disabled_passes_text_through_unchanged() -> None:
    pipe = _make_pipe(enabled=False)
    result = await pipe._complete_or_buffer_context("Erinnere mich daran, dass", lang="de")
    # Disabled feature → zero behaviour change vs. baseline. The dangling text
    # must be returned as-is (the brain decides what to do).
    assert result == "Erinnere mich daran, dass"
    assert pipe._completion_buffer.is_pending is False


@pytest.mark.asyncio
async def test_complete_text_returns_unchanged() -> None:
    pipe = _make_pipe()
    result = await pipe._complete_or_buffer_context("Mach das Licht an", lang="de")
    # Precision-over-recall: a complete utterance MUST go straight to the brain
    # — no buffering, no waiting, no added latency.
    assert result == "Mach das Licht an"
    assert pipe._completion_buffer.is_pending is False
    assert pipe._spoken == []


# --------------------------------------------------------------------------- #
# Incomplete path — buffer + silent re-listen                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_incomplete_text_buffers_and_returns_none() -> None:
    pipe = _make_pipe()
    result = await pipe._complete_or_buffer_context("Erinnere mich daran, dass", lang="de")
    assert result is None
    assert pipe._completion_buffer.is_pending is True
    assert pipe._completion_buffer.fragment == "Erinnere mich daran, dass"
    assert pipe._completion_buffer.chain_count == 1
    # Stay silent during the wait — no TTS while we wait for the continuation.
    assert pipe._spoken == []
    # Timeout task armed.
    assert pipe._completion_timeout_task is not None


@pytest.mark.asyncio
async def test_continuation_completes_and_returns_joined_text() -> None:
    pipe = _make_pipe()
    first = await pipe._complete_or_buffer_context("Erinnere mich daran, dass", lang="de")
    assert first is None  # buffered
    second = await pipe._complete_or_buffer_context("ich morgen Brötchen kaufe", lang="de")
    assert second == "Erinnere mich daran, dass ich morgen Brötchen kaufe"
    # Buffer drained, timer cancelled.
    assert pipe._completion_buffer.is_pending is False
    # No fallback was spoken — the user completed before timeout.
    assert pipe._spoken == []


@pytest.mark.asyncio
async def test_continuation_still_incomplete_keeps_waiting() -> None:
    pipe = _make_pipe()
    await pipe._complete_or_buffer_context("Erinnere mich daran, dass", lang="de")
    second = await pipe._complete_or_buffer_context("ich morgen, falls", lang="de")
    # joined now ends on "falls" — still a dangling conjunction → keep waiting
    assert second is None
    assert pipe._completion_buffer.is_pending is True
    assert pipe._completion_buffer.chain_count == 2
    assert "falls" in pipe._completion_buffer.fragment


@pytest.mark.asyncio
async def test_max_chain_forces_flush_to_brain() -> None:
    # max_chain = 2 → first store + one continuation = chain 2, forced flush
    pipe = _make_pipe(max_chain=2)
    await pipe._complete_or_buffer_context("Erinnere mich daran, dass", lang="de")
    second = await pipe._complete_or_buffer_context("ich morgen, weil", lang="de")
    # Joined would still end on "weil" (incomplete), BUT chain_count is now 2
    # which equals max_chain → forced flush, not held forever.
    assert second is not None
    assert "weil" in second
    assert pipe._completion_buffer.is_pending is False


# --------------------------------------------------------------------------- #
# Cancel path                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cancel_phrase_during_pending_clears_buffer_silently() -> None:
    pipe = _make_pipe()
    await pipe._complete_or_buffer_context("Erinnere mich daran, dass", lang="de")
    result = await pipe._complete_or_buffer_context("vergiss das", lang="de")
    assert result is None
    assert pipe._completion_buffer.is_pending is False
    assert pipe._spoken == []  # cancel is silent


# --------------------------------------------------------------------------- #
# Timeout — the critical safety test (AD-OE6 / zero silent drops)              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_timeout_fires_and_speaks_fallback_in_german() -> None:
    pipe = _make_pipe(wait_ms=80)  # very short for test speed
    await pipe._complete_or_buffer_context("Erinnere mich daran, dass", lang="de")
    # Let the timer fire.
    await asyncio.sleep(0.3)
    # AD-OE6: SOMETHING was spoken — no silent drop.
    assert len(pipe._spoken) >= 1
    spoken_text, spoken_lang = pipe._spoken[0]
    assert isinstance(spoken_text, str) and spoken_text
    assert spoken_lang == "de"
    # Buffer cleared.
    assert pipe._completion_buffer.is_pending is False
    # State machine surfaced JARVIS_SPEAKING during the fallback and ended at LISTENING.
    assert TurnTakingState.JARVIS_SPEAKING in pipe._state_history
    assert pipe._state_history[-1] == TurnTakingState.LISTENING


@pytest.mark.asyncio
async def test_timeout_speaks_fallback_in_english_when_lang_en() -> None:
    pipe = _make_pipe(wait_ms=80)
    await pipe._complete_or_buffer_context("Remind me tomorrow that", lang="en")
    await asyncio.sleep(0.3)
    assert len(pipe._spoken) >= 1
    spoken_text, spoken_lang = pipe._spoken[0]
    assert spoken_lang == "en"
    # The fallback wording differs from German.
    assert spoken_text.isascii(), f"EN fallback should be ASCII English, got {spoken_text!r}"


@pytest.mark.asyncio
async def test_continuation_cancels_pending_timeout_task() -> None:
    pipe = _make_pipe(wait_ms=10_000)  # long timer
    await pipe._complete_or_buffer_context("Erinnere mich daran, dass", lang="de")
    task = pipe._completion_timeout_task
    assert task is not None and not task.done()
    await pipe._complete_or_buffer_context("ich morgen anrufe", lang="de")
    # The continuation completed the prompt — the timer must have been cancelled.
    assert pipe._completion_timeout_task is None
    # Let the loop process the cancellation (asyncio Task.cancel() is async).
    await asyncio.sleep(0)
    assert task.done()


@pytest.mark.asyncio
async def test_cancel_phrase_also_cancels_pending_timeout_task() -> None:
    pipe = _make_pipe(wait_ms=10_000)
    await pipe._complete_or_buffer_context("Erinnere mich daran, dass", lang="de")
    task = pipe._completion_timeout_task
    assert task is not None
    await pipe._complete_or_buffer_context("vergiss das", lang="de")
    assert pipe._completion_timeout_task is None
    await asyncio.sleep(0)
    assert task.done()
