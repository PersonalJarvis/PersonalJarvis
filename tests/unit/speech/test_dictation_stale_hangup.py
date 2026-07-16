"""Regression test: chat mic-dictation must not inherit a stale hangup.

User-reported symptom (2026-06-28): clicking the chat mic button starts
dictation and it *immediately* stops again — the recording never stays open.

Root cause: ``_hangup_event`` ("auflegen" — the hard voice kill-switch) is set
on every voice hangup and only ever cleared when the NEXT voice session is
accepted (``_run_session`` loop). The transcribe-only dictation lane shares that
event in its ``asyncio.wait({stop, hangup, drain})`` gate, so a leftover hangup
from an earlier voice call makes ``hangup_task`` complete instantly →
``FIRST_COMPLETED`` returns → the session finalizes → a final
``DictationTranscript`` flips the UI ``dictating`` flag back to false the moment
it went true.

A fresh dictation session must start clean, mirroring the voice path's
``self._hangup_event.clear()`` at session accept. This pins that contract.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.speech.pipeline import PipelineState, SpeechPipeline


class _StubSTT:
    async def transcribe_pcm(self, pcm: bytes):  # pragma: no cover - never called
        raise AssertionError("dictation session must not run in this unit test")


def _make_idle_pipeline() -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._utterance_stt = _StubSTT()
    pipe._dictation_task = None
    pipe._dictation_stop_event = asyncio.Event()
    pipe._ptt_mode = False
    pipe._state = PipelineState.IDLE
    pipe._input_device = "default"
    # Stale hangup left over from a previous voice call that was never followed
    # by a fresh voice session (so the voice-path clear never ran).
    pipe._hangup_event = asyncio.Event()
    pipe._hangup_event.set()
    return pipe


@pytest.mark.asyncio
async def test_start_dictation_clears_stale_hangup() -> None:
    pipe = _make_idle_pipeline()
    assert pipe._hangup_event.is_set()  # precondition: stale hangup present

    started = pipe.start_dictation()

    # Cancel the spawned session immediately — we only assert on the synchronous
    # state set BEFORE the mic-touching task body runs.
    task = pipe._dictation_task
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    assert started is True
    assert not pipe._hangup_event.is_set(), (
        "start_dictation must clear a stale hangup so the session is not "
        "finalized on its first wait tick"
    )


@pytest.mark.asyncio
async def test_start_dictation_does_not_spawn_when_capture_gate_is_closed() -> None:
    pipe = _make_idle_pipeline()
    pipe._activation_gate = lambda: False

    assert pipe.start_dictation() is False
    assert pipe._dictation_task is None
    assert pipe._hangup_event.is_set()
