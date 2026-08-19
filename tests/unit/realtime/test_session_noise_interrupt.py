"""A provider ``interrupted`` edge is not proof that the user spoke (RT-09).

Gemini's server VAD has no separate speech-start signal: a cough, a door and a
real barge-in all arrive as the same ``interrupted`` flag. Cancelling the
answer on every edge means room noise ends a reply mid-sentence and the
withhold flag then discards the rest of that response — the user hears half a
statement, which is worse than hearing none.

These tests pin both halves: an unconfirmed edge leaves the answer alone, and
an edge the user's own words confirm still cuts in.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession
from tests.unit.realtime.test_session import FakeProvider, FakeSession, _cfg

HEAD = b"\x01\x02" * 8
TAIL = b"\x05\x06" * 8
# Deliberately plain smalltalk: a request that needs the user's world or a
# public fact dispatches a delegate, and a delegated turn withholds provider
# output for reasons that have nothing to do with this bug.
OPENING = "Thanks, you are great."


class _IsolatingSession(FakeSession):
    """A Gemini-shaped session: automatic responses, isolated generations."""

    creates_responses_automatically = True
    isolates_response_generations = True


def _scripted(script):
    class _ScriptedSession(_IsolatingSession):
        async def receive(self):
            async for event in script():
                yield event
                await asyncio.sleep(0)

    class _ScriptedProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _ScriptedSession([])
            return self.session

    return _ScriptedProvider([])


def _assistant_text(jsons):
    return "".join(
        str(message.get("text") or "")
        for message in jsons
        if message.get("type") == "transcript" and message.get("role") == "assistant"
    )


async def _run(provider, *, binaries, jsons, send_json=None):
    sess = RealtimeVoiceSession(
        session_id="noise-interrupt",
        send_binary=lambda data: binaries.append(data) or asyncio.sleep(0),
        send_json=send_json or (lambda message: jsons.append(message) or asyncio.sleep(0)),
        provider=provider,
        config=_cfg(),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await sess.wait_finished()
    await sess.end(reason="test")
    return sess


@pytest.mark.asyncio
async def test_noise_interruption_does_not_truncate_the_answer():
    """An ``interrupted`` edge with no user words leaves the reply running."""

    async def script():
        yield RealtimeEvent(type="input_transcript", text=OPENING, is_final=True)
        yield RealtimeEvent(type="output_transcript_delta", text="It is sunny")
        yield RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(pcm=HEAD, sample_rate=24_000, timestamp_ns=0),
        )
        # The cough.
        yield RealtimeEvent(type="interrupted")
        yield RealtimeEvent(type="output_transcript_delta", text=" and warm.")
        yield RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(pcm=TAIL, sample_rate=24_000, timestamp_ns=0),
        )
        yield RealtimeEvent(type="turn_complete")

    binaries, jsons = [], []
    provider = _scripted(script)
    sess = await _run(provider, binaries=binaries, jsons=jsons)

    assert binaries == [HEAD, TAIL]
    assert "and warm." in _assistant_text(jsons)
    assert not any(item.get("type") == "tts_cancel" for item in jsons)
    assert sess._unconfirmed_interruptions == 1


@pytest.mark.asyncio
async def test_confirmed_barge_in_still_cuts_the_answer():
    """The user's own final transcript confirms the edge and cuts the reply."""

    async def script():
        yield RealtimeEvent(type="input_transcript", text=OPENING, is_final=True)
        yield RealtimeEvent(type="output_transcript_delta", text="It is sunny")
        yield RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(pcm=HEAD, sample_rate=24_000, timestamp_ns=0),
        )
        yield RealtimeEvent(type="interrupted")
        # Real words, moments later: this is a barge-in.
        yield RealtimeEvent(type="input_transcript", text="Hey, listen to me.", is_final=True)
        yield RealtimeEvent(type="turn_complete")

    binaries, jsons = [], []
    provider = _scripted(script)
    await _run(provider, binaries=binaries, jsons=jsons)

    assert any(item.get("type") == "tts_cancel" for item in jsons)


@pytest.mark.asyncio
async def test_unconfirmed_interruption_is_committed_once_the_provider_stops():
    """A generation that really did end at the edge still closes the turn."""

    from jarvis.realtime import session as session_mod

    async def script():
        yield RealtimeEvent(type="input_transcript", text=OPENING, is_final=True)
        yield RealtimeEvent(type="output_transcript_delta", text="It is sunny")
        yield RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(pcm=HEAD, sample_rate=24_000, timestamp_ns=0),
        )
        yield RealtimeEvent(type="interrupted")
        # Gemini stops generating on its own interruption: no boundary, no
        # further output, and nothing but this backstop closes the turn.
        await asyncio.sleep(0.5)

    binaries, jsons = [], []
    provider = _scripted(script)
    original = session_mod._INTERRUPTION_CONFIRM_WINDOW_S
    session_mod._INTERRUPTION_CONFIRM_WINDOW_S = 0.05
    try:
        await _run(provider, binaries=binaries, jsons=jsons)
    finally:
        session_mod._INTERRUPTION_CONFIRM_WINDOW_S = original

    assert any(item.get("type") == "tts_cancel" for item in jsons)


@pytest.mark.asyncio
async def test_empty_interrupt_before_a_reply_is_ignored():
    """Gemini Live fires ``interrupted`` for our own text with nothing playing.

    Live 2026-08-19 15:59: steering and the empty-turn re-ask each produced
    an empty ``interrupted``+``turn_complete`` before the real greeting.
    Deferring that armed a silence backstop that later cut the spoken reply.
    """

    async def script():
        yield RealtimeEvent(type="input_transcript", text=OPENING, is_final=True)
        yield RealtimeEvent(type="interrupted")
        yield RealtimeEvent(type="output_transcript_delta", text="It is sunny and warm.")
        yield RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(pcm=HEAD + TAIL, sample_rate=24_000, timestamp_ns=0),
        )
        yield RealtimeEvent(type="turn_complete")

    binaries, jsons = [], []
    provider = _scripted(script)
    sess = await _run(provider, binaries=binaries, jsons=jsons)

    assert binaries == [HEAD + TAIL]
    assert "It is sunny and warm." in _assistant_text(jsons)
    assert not any(item.get("type") == "tts_cancel" for item in jsons)
    assert sess._unconfirmed_interruptions == 0


@pytest.mark.asyncio
async def test_completed_reply_survives_the_interrupt_silence_window():
    """A cough mid-reply must not tts_cancel after the generation's own boundary."""

    from jarvis.realtime import session as session_mod

    async def script():
        yield RealtimeEvent(type="input_transcript", text=OPENING, is_final=True)
        yield RealtimeEvent(type="output_transcript_delta", text="It is sunny")
        yield RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(pcm=HEAD, sample_rate=24_000, timestamp_ns=0),
        )
        yield RealtimeEvent(type="interrupted")
        yield RealtimeEvent(type="output_transcript_delta", text=" and warm.")
        yield RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(pcm=TAIL, sample_rate=24_000, timestamp_ns=0),
        )
        yield RealtimeEvent(type="turn_complete")
        await asyncio.sleep(0.4)

    binaries, jsons = [], []
    provider = _scripted(script)
    original = session_mod._INTERRUPTION_CONFIRM_WINDOW_S
    session_mod._INTERRUPTION_CONFIRM_WINDOW_S = 0.05
    try:
        sess = await _run(provider, binaries=binaries, jsons=jsons)
    finally:
        session_mod._INTERRUPTION_CONFIRM_WINDOW_S = original

    assert binaries == [HEAD, TAIL]
    assert "and warm." in _assistant_text(jsons)
    assert not any(item.get("type") == "tts_cancel" for item in jsons)
    assert sess._unconfirmed_interruptions == 1


@pytest.mark.asyncio
async def test_stale_interrupt_cannot_cancel_speaker_drain():
    """Desktop ``finish_turn`` blocks on ``turn_complete``; that silence is expected.

    Live 2026-08-19 15:59: the 3.6 s greeting was still draining when the
    settle task treated 1 s of post-boundary provider silence as a confirmed
    barge-in and sent ``tts_cancel``. The user heard half a sentence.
    """

    from jarvis.realtime import session as session_mod

    async def script():
        yield RealtimeEvent(type="input_transcript", text=OPENING, is_final=True)
        yield RealtimeEvent(type="output_transcript_delta", text="It is sunny")
        yield RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(pcm=HEAD, sample_rate=24_000, timestamp_ns=0),
        )
        yield RealtimeEvent(type="interrupted")
        yield RealtimeEvent(type="output_transcript_delta", text=" and warm.")
        yield RealtimeEvent(
            type="audio_delta",
            audio=AudioChunk(pcm=TAIL, sample_rate=24_000, timestamp_ns=0),
        )
        yield RealtimeEvent(type="turn_complete")

    binaries, jsons = [], []

    async def slow_send_json(message):
        jsons.append(message)
        if message.get("type") == "turn_complete":
            await asyncio.sleep(0.3)

    provider = _scripted(script)
    original = session_mod._INTERRUPTION_CONFIRM_WINDOW_S
    session_mod._INTERRUPTION_CONFIRM_WINDOW_S = 0.05
    try:
        sess = await _run(provider, binaries=binaries, jsons=jsons, send_json=slow_send_json)
    finally:
        session_mod._INTERRUPTION_CONFIRM_WINDOW_S = original

    assert binaries == [HEAD, TAIL]
    assert "and warm." in _assistant_text(jsons)
    assert not any(item.get("type") == "tts_cancel" for item in jsons)
    assert sess._unconfirmed_interruptions == 1
