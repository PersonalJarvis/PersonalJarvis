"""Stale delegate-readback repeat guard (live forensic 2026-07-21 11:32).

A delegate reply whose provider rendering never became audible is spoken by
the surface TTS — but the injected rendering order, carrying the verbatim
reply text, stays live in the provider's conversation context. Three turns
later a one-word user fragment made Gemini execute that stale order and
repeat the whole earlier answer word for word. The session must recognize a
plain turn that re-renders an already-delivered delegate reply and suppress
it, while a sanctioned delegate readback of the same text stays audible.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import jarvis.realtime.session as session_mod
from jarvis.core.protocols import AudioChunk
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession
from jarvis.voice.action_phrases import action_phrase

DELIVERED_REPLY = (
    "School districts in the United States are organized locally and "
    "regionally and play a central role in the education system. They are "
    "largely independent and fund themselves mostly from local property "
    "taxes."
)

AUDIO = AudioChunk(pcm=b"\x01\x02" * 7_200, sample_rate=24_000, timestamp_ns=0)


class FakeSession:
    session_id = "fake"
    supports_tool_updates = True
    creates_responses_automatically = False
    isolates_response_generations = False

    def __init__(self, events):
        self._events = events
        self.sent_audio = []
        self.text_inputs = []
        self.tool_results = []
        self.session_updates = []
        self.response_requests = 0
        self.interrupts = 0
        self.closed = False

    async def send_audio(self, chunk):
        self.sent_audio.append(chunk)

    async def receive(self):
        for event in self._events:
            yield event
            await asyncio.sleep(0)

    async def update_session(self, *, instructions=None, language=None, tools=None):
        self.session_updates.append(
            {"instructions": instructions, "language": language, "tools": tools}
        )

    async def request_response(self, *, required_tool=None):
        del required_tool
        self.response_requests += 1

    async def send_text(self, text):
        self.text_inputs.append(text)

    async def truncate(self, audio_end_ms):
        del audio_end_ms

    async def interrupt(self):
        self.interrupts += 1

    async def send_tool_result(self, call_id, name, result):
        self.tool_results.append((call_id, name, result))

    async def close(self):
        self.closed = True


class FakeProvider:
    name = "fake"
    supports_realtime = True
    input_sample_rate = 16_000
    output_sample_rate = 24_000

    def __init__(self, events):
        self._events = events
        self.opened_with = None
        self.session = None

    async def can_open_duplex_session(self):
        return True

    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = FakeSession(self._events)
        return self.session


class ReadbackGatedSession(FakeSession):
    """Yield the user turn, then hold the readback until the result arrives."""

    def __init__(self, events):
        super().__init__(events)
        self._text_sent = asyncio.Event()

    async def receive(self):
        yield RealtimeEvent(
            type="input_transcript",
            text="What is in my Gmail inbox?",
            is_final=True,
        )
        await self._text_sent.wait()
        for event in self._events:
            yield event
            await asyncio.sleep(0)

    async def send_text(self, text):
        await super().send_text(text)
        self._text_sent.set()


class ReadbackGatedProvider(FakeProvider):
    async def open_session(self, cfg):
        self.opened_with = cfg
        self.session = ReadbackGatedSession(self._events)
        return self.session


class FakeBrain:
    def __init__(self, replies=("done",)):
        self.calls = []
        self._replies = list(replies)

    async def generate(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return self._replies.pop(0) if self._replies else "done"

    async def __call__(self, text):
        return await self.generate(text)


def _cfg():
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="delegate"),
        latency=SimpleNamespace(enabled=False),
    )


def _session(provider, *, brain=None, jsons=None, binaries=None):
    return RealtimeVoiceSession(
        session_id="stale-readback-test",
        send_binary=(
            (lambda data: binaries.append(data) or asyncio.sleep(0))
            if binaries is not None
            else (lambda _data: asyncio.sleep(0))
        ),
        send_json=(
            (lambda m: jsons.append(m) or asyncio.sleep(0))
            if jsons is not None
            else (lambda _m: asyncio.sleep(0))
        ),
        provider=provider,
        config=_cfg(),
        brain=brain,
    )


# ---------------------------------------------------------------------------
# Matching primitives
# ---------------------------------------------------------------------------


def test_repeat_match_is_word_agnostic_about_punctuation_and_case():
    sess = _session(FakeProvider([]))
    sess._arm_stale_readback_guard(DELIVERED_REPLY)
    # The provider's re-render transcription drops commas and changes casing;
    # the words are what identify the stale repeat.
    rerender = DELIVERED_REPLY[:80].upper().replace(",", " ")
    assert sess._match_stale_readback(rerender) is not None


def test_short_or_fresh_text_never_arms_or_matches():
    sess = _session(FakeProvider([]))
    sess._arm_stale_readback_guard("Done.")
    assert sess._stale_readback_refs == []
    sess._arm_stale_readback_guard(DELIVERED_REPLY)
    assert (
        sess._match_stale_readback(
            "Here is a completely fresh answer about tomorrow's weather in Berlin."
        )
        is None
    )
    # Too little accumulated text is never conclusive, even when it matches.
    assert sess._match_stale_readback(DELIVERED_REPLY[:20]) is None


def test_full_one_delta_rerender_with_trailing_extra_still_matches():
    sess = _session(FakeProvider([]))
    sess._arm_stale_readback_guard(DELIVERED_REPLY)
    assert sess._match_stale_readback(DELIVERED_REPLY + " Would you like more?") is not None


# ---------------------------------------------------------------------------
# Pump behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plain_turn_rerendering_a_delivered_reply_is_cancelled():
    """The live failure: three turns after the surface TTS delivered the
    delegate reply, a plain turn started re-rendering it verbatim. The guard
    must cancel the repeat, speak the clarify line, and disarm (one-shot)."""
    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text="hello", is_final=True),
            RealtimeEvent(type="output_transcript_delta", text=DELIVERED_REPLY[:80]),
            RealtimeEvent(type="output_transcript_delta", text=DELIVERED_REPLY[80:]),
            RealtimeEvent(type="audio_delta", audio=AUDIO),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    jsons: list[dict] = []
    binaries: list[bytes] = []
    sess = _session(provider, brain=FakeBrain(), jsons=jsons, binaries=binaries)
    sess._arm_stale_readback_guard(DELIVERED_REPLY)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(sess.wait_finished(), timeout=5)
    await sess.end(reason="test")

    assert any(m.get("type") == "tts_cancel" for m in jsons)
    spoken = [m for m in jsons if m.get("type") == "error_spoken"]
    assert [m["text"] for m in spoken] == [action_phrase("stale_repeat_clarify", "en")]
    # The stale rendering's audio never reaches the surface.
    assert binaries == []
    # One-shot: a genuine repeat request right after works on its next try.
    assert sess._stale_readback_refs == []


@pytest.mark.asyncio
async def test_plain_turn_with_fresh_text_plays_normally():
    fresh = (
        "Tomorrow looks calm and sunny with a light breeze from the west "
        "and no rain expected until the evening hours."
    )
    provider = FakeProvider(
        [
            RealtimeEvent(type="input_transcript", text="hello", is_final=True),
            RealtimeEvent(type="output_transcript_delta", text=fresh),
            RealtimeEvent(type="audio_delta", audio=AUDIO),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    jsons: list[dict] = []
    binaries: list[bytes] = []
    sess = _session(provider, brain=FakeBrain(), jsons=jsons, binaries=binaries)
    sess._arm_stale_readback_guard(DELIVERED_REPLY)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(sess.wait_finished(), timeout=5)
    await sess.end(reason="test")

    assert not any(m.get("type") == "tts_cancel" for m in jsons)
    assert binaries, "fresh provider audio must keep flowing"
    # The guard stays armed for the reply that was never re-rendered.
    assert sess._stale_readback_refs != []


@pytest.mark.asyncio
async def test_sanctioned_delegate_readback_of_same_text_stays_audible():
    """A delegate turn rendering ITS OWN trusted reply is the sanctioned
    readback — even when an earlier fallback delivery armed the guard with
    identical text (the user asked the same question again)."""
    provider = ReadbackGatedProvider(
        [
            RealtimeEvent(type="output_transcript_delta", text=DELIVERED_REPLY),
            RealtimeEvent(type="audio_delta", audio=AUDIO),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    jsons: list[dict] = []
    binaries: list[bytes] = []
    sess = _session(
        provider,
        brain=FakeBrain(replies=(DELIVERED_REPLY,)),
        jsons=jsons,
        binaries=binaries,
    )
    sess._arm_stale_readback_guard(DELIVERED_REPLY)

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await asyncio.wait_for(sess.wait_finished(), timeout=10)
    await sess.end(reason="test")

    assert provider.session.text_inputs, "trusted result was never injected"
    assert not any(m.get("type") == "tts_cancel" for m in jsons)
    assert binaries, "the sanctioned readback's audio must stay audible"


# ---------------------------------------------------------------------------
# Prompt-side expiry
# ---------------------------------------------------------------------------


def test_result_prompt_orders_a_single_expiring_rendering():
    prompt = session_mod._delegate_result_prompt("The answer.", language="en", success=True)
    assert "immediate next reply" in prompt
    assert "never speak, repeat, or paraphrase" in prompt


def test_session_instructions_forbid_replaying_earlier_action_results():
    text = session_mod._session_instructions("en")
    assert "one-time rendering order" in text
    assert "unless the user explicitly asks for a repeat" in text
