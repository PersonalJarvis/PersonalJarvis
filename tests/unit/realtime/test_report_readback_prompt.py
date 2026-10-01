"""A live voice model thinks about an agent's report instead of relaying a line.

Background results (a coding pane finishing a job Jarvis handed it, a Jarvis
agent reporting back) carry a short deterministic line AND the agent's full
report. Every live engine hands the report to its model with the request to
work out what the user needs to hear; classic TTS keeps speaking the line.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.events import AnnouncementRequested
from jarvis.core.protocols import AudioChunk
from jarvis.live.native import NativeLiveVoiceSession
from jarvis.live.session import LiveVoiceSession
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.report_prompt import REPORT_PROMPT_CHARS, report_update_prompt
from jarvis.realtime.session import RealtimeVoiceSession
from tests.unit.realtime.test_session import FakeBus, TextResultGatedProvider, _cfg
from tests.unit.speech.test_realtime_announcement_bridge import _pipeline

REPORT = (
    "Terminal: T1\n\nWhat the user asked for:\nfix the login bug\n\n"
    "The coding agent's final message:\nFixed the token refresh in auth.py; "
    "12 tests pass. The logout flow is still untested."
)


def test_the_prompt_asks_the_model_to_think_and_carries_the_whole_report() -> None:
    prompt = report_update_prompt("T1 is done.", REPORT, language="de", kind="completion")

    assert "think it through" in prompt
    assert "German" in prompt
    assert "<trusted_update>\nT1 is done.\n</trusted_update>" in prompt
    assert "The logout flow is still untested." in prompt
    assert "do not call a function or tool" in prompt


def test_a_runaway_report_keeps_both_ends() -> None:
    report = "ASK " + "x" * (REPORT_PROMPT_CHARS * 2) + " VERDICT"
    prompt = report_update_prompt("done", report, language="en", kind="completion")

    assert "ASK" in prompt and "VERDICT" in prompt
    assert len(prompt) < REPORT_PROMPT_CHARS + 3000


@pytest.mark.asyncio
async def test_the_pipeline_hands_the_report_to_the_live_model() -> None:
    pipeline, tts, player, realtime = _pipeline(accepted=True)

    await pipeline._on_announcement(
        AnnouncementRequested(
            text="T1 is done.", language="en", kind="completion", report=REPORT
        )
    )

    assert realtime.calls == [
        {
            "text": "T1 is done.",
            "language": "en",
            "spoken_kind": "completion",
            "detail": None,
            "report": REPORT,
        }
    ]
    assert realtime.remembered[0]["report"] == REPORT
    assert tts.calls == [] and player.plays == 0


@pytest.mark.asyncio
async def test_the_realtime_session_sends_the_report_prompt_and_remembers_it() -> None:
    provider = TextResultGatedProvider(
        [
            RealtimeEvent(type="output_transcript_delta", text="T1 fixed the login."),
            RealtimeEvent(
                type="audio_delta",
                audio=AudioChunk(pcm=b"\x01\x02" * 8, sample_rate=24_000, timestamp_ns=0),
            ),
            RealtimeEvent(type="turn_complete"),
        ]
    )
    sess = RealtimeVoiceSession(
        session_id="report-readback",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(),
        bus=FakeBus(),
        surface="desktop",
    )

    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    accepted = await sess.deliver_announcement(
        text="T1 is done.", language="en", spoken_kind="completion", report=REPORT
    )
    await sess.wait_finished()
    await sess.end(reason="test")

    assert accepted is True
    sent = provider.session.text_inputs[0]
    assert "think it through" in sent and "<agent_report>" in sent
    assert "The logout flow is still untested." in sent
    history = "\n".join(str(message.content) for message in sess._delegate_history)
    assert "Full report:" in history


class _Connection:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
        self.texts: list[str] = []

    async def send(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)

    async def send_text(self, text: str) -> None:
        self.texts.append(text)


def _live(cls: type) -> tuple[Any, _Connection]:
    async def _send(_frame: Any) -> None:
        return None

    session = cls(
        session_id="voice",
        bus=None,
        send_json=_send,
        send_binary=_send,
        providers=[SimpleNamespace(name="test")],
        config=SimpleNamespace(brain=SimpleNamespace(reply_language="en")),
    )
    connection = _Connection()
    session._connection = connection
    return session, connection


@pytest.mark.asyncio
async def test_gpt_live_lets_its_reasoning_backend_think_about_the_report() -> None:
    session, connection = _live(LiveVoiceSession)

    accepted = await session.deliver_announcement(
        "T1 is done.", language="en", spoken_kind="completion", report=REPORT
    )

    assert accepted is True
    assert [frame["type"] for frame in connection.frames] == [
        "response.item.create",
        "response.create",
    ]
    text = connection.frames[0]["item"]["content"][0]["text"]
    assert "think it through" in text and "The logout flow is still untested." in text


@pytest.mark.asyncio
async def test_gpt_live_refuses_a_report_mid_turn_so_it_is_retried_later() -> None:
    session, connection = _live(LiveVoiceSession)
    session._thinking = True

    accepted = await session.deliver_announcement("T1 is done.", report=REPORT)

    assert accepted is False
    assert connection.frames == []


@pytest.mark.asyncio
async def test_gpt_live_still_relays_a_plain_announcement_as_commentary() -> None:
    session, connection = _live(LiveVoiceSession)

    assert await session.deliver_announcement("The mission is ready.") is True
    assert connection.frames[0]["type"] == "session.commentary.append"


@pytest.mark.asyncio
async def test_native_live_sends_the_report_prompt() -> None:
    session, connection = _live(NativeLiveVoiceSession)

    accepted = await session.deliver_announcement(
        "T1 is done.", language="en", spoken_kind="completion", report=REPORT
    )

    assert accepted is True
    assert "think it through" in connection.texts[0]
    assert "The logout flow is still untested." in connection.texts[0]
