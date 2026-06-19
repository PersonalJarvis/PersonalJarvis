"""The voice-hangup chokepoint must also cancel an in-flight CHAT turn.

Live bug 2026-06-19: the whisper-bar X (``request_hangup`` → ``_trigger_voice_hangup``)
set the voice ``_hangup_event`` and cancelled active Computer-Use, but a chat
turn dispatched through ``desktop_app._on_user_message`` runs on a separate path
that ignored the X — so the brain kept thinking through ~27 X presses. The fix
wires ``_trigger_voice_hangup`` to ``runtime_refs.cancel_active_chat_turn`` so the
single hangup chokepoint stops a chat turn too.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.core import runtime_refs
from jarvis.speech.pipeline import SpeechPipeline


class _FakePlayer:
    def stop(self) -> None:  # the hangup stops the player first
        pass


@pytest.fixture(autouse=True)
def _clean_refs():
    runtime_refs._reset_for_tests()
    yield
    runtime_refs._reset_for_tests()


def _hangup_pipeline() -> SpeechPipeline:
    """A SpeechPipeline stub carrying only what ``_trigger_voice_hangup`` reads
    before the chat-cancel call (the continuation cleanup after it is wrapped in
    a swallowing try/except, so missing attrs there are harmless)."""
    p = SpeechPipeline.__new__(SpeechPipeline)
    p._player = _FakePlayer()
    p._session_end_reason = None
    p._hangup_event = asyncio.Event()
    return p


@pytest.mark.asyncio
async def test_voice_hangup_cancels_an_active_chat_turn():
    loop = asyncio.get_running_loop()
    started = asyncio.Event()

    async def chat_turn():
        started.set()
        await asyncio.sleep(60)  # the brain is THINKING; the user hits X

    task = asyncio.create_task(chat_turn())
    await started.wait()
    runtime_refs.set_active_chat_turn(task, loop)

    _hangup_pipeline()._trigger_voice_hangup()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_voice_hangup_is_harmless_with_no_chat_turn():
    """A hangup with no chat turn armed must not raise."""
    _hangup_pipeline()._trigger_voice_hangup()  # no exception == pass
