"""Integration test: drive the media WebSocket with synthetic Twilio frames.

This is the in-repo proof of the simulated call (DoD item 2/3). It connects to
``/api/telephony/media`` through Starlette's TestClient WS support, sends the
``connected`` / ``start`` / ``media`` / ``stop`` JSON frames Twilio would send,
and asserts that the handler:

  * builds a session (via an injected fake factory using fakes for STT/Brain/TTS),
  * produces outbound ``media`` frames (Jarvis spoke),
  * records the finished call in the ring buffer.

No real socket, no model download, no API key. Signature/secret checks are
disabled with ``app.state.telephony_skip_signature``.
"""

from __future__ import annotations

import base64
import math
import struct

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.telephony.audio import TWILIO_SAMPLE_RATE, pcm16_to_ulaw
from jarvis.telephony.session import TelephonyCallSession
from jarvis.telephony.status import TelephonyManager
from jarvis.ui.web.telephony_routes import router as telephony_router
from tests.fakes.fake_telephony_stack import FakeBrain, FakeSTT, FakeTTS


def _ulaw_frame_b64(amp: int, ms: int = 20, freq: int = 300) -> str:
    n = TWILIO_SAMPLE_RATE * ms // 1000
    if amp == 0:
        pcm = b"\x00\x00" * n
    else:
        pcm = b"".join(
            struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / TWILIO_SAMPLE_RATE)))
            for i in range(n)
        )
    return base64.b64encode(pcm16_to_ulaw(pcm)).decode("ascii")


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.include_router(telephony_router)
    application.state.telephony_manager = TelephonyManager()
    application.state.bus = None
    application.state.telephony_skip_signature = True

    def factory(*, call_sid, stream_sid, from_number, to_number, language_code, send):
        session = TelephonyCallSession(
            call_sid=call_sid,
            stream_sid=stream_sid,
            send=send,
            stt=FakeSTT(["Wie spät ist es?"]),
            brain=FakeBrain("Es ist genau vierzehn Uhr dreißig."),
            # Short greeting/answers keep the synchronous TestClient WS from
            # backpressuring: ~40 ms of audio is ~2 frames, well within buffers.
            tts=FakeTTS(ms_per_char=1),
            from_number=from_number,
            to_number=to_number,
            language_code=language_code,
            max_call_seconds=600,
            greeting="Hallo.",
        )
        # Speed up the endpointer for the test's short frame stream.
        session._endpointer.silence_ms = 100
        session._endpointer.min_speech_ms = 60
        return session

    application.state.telephony_session_factory = factory
    return application


def test_simulated_call_produces_outbound_media_and_records_call(app):
    with TestClient(app) as client:
        with client.websocket_connect("/api/telephony/media") as ws:
            ws.send_json({"event": "connected", "protocol": "Call"})
            ws.send_json(
                {
                    "event": "start",
                    "streamSid": "MZ123",
                    "start": {
                        "streamSid": "MZ123",
                        "callSid": "CA123",
                        "customParameters": {
                            "secret": "x",
                            "call_sid": "CA123",
                            "language": "de-DE",
                        },
                        "mediaFormat": {
                            "encoding": "audio/x-mulaw",
                            "sampleRate": 8000,
                            "channels": 1,
                        },
                    },
                }
            )
            outbound_media = 0

            def _drain(budget: int = 80) -> None:
                """Pull any pending server frames so the synchronous WS does not
                backpressure while we keep sending caller audio."""
                nonlocal outbound_media
                for _ in range(budget):
                    msg = ws.receive_json()
                    if msg.get("event") == "media":
                        outbound_media += 1
                    # Stop draining once we've seen the greeting tail (a mark).
                    if msg.get("event") == "mark":
                        return

            # Greeting plays first — drain it.
            _drain()

            # lead silence
            for _ in range(2):
                ws.send_json({"event": "media", "media": {"payload": _ulaw_frame_b64(0)}})
            # speech
            for _ in range(8):
                ws.send_json({"event": "media", "media": {"payload": _ulaw_frame_b64(15000)}})
            # trailing silence -> endpoint -> turn
            for _ in range(12):
                ws.send_json({"event": "media", "media": {"payload": _ulaw_frame_b64(0)}})

            # Drain the answer's outbound frames.
            _drain()

            assert outbound_media > 0, "Jarvis produced no outbound mu-law audio"

            ws.send_json({"event": "stop", "stop": {"callSid": "CA123"}})

    # The call was recorded in the ring buffer after the socket closed.
    mgr: TelephonyManager = app.state.telephony_manager
    calls = mgr.recent_calls()
    assert len(calls) == 1
    assert calls[0]["call_sid"] == "CA123"
    assert calls[0]["status"] in ("completed", "in_progress")


def test_media_socket_records_no_audio_when_silent(app):
    with TestClient(app) as client:
        with client.websocket_connect("/api/telephony/media") as ws:
            ws.send_json({"event": "connected"})
            ws.send_json(
                {
                    "event": "start",
                    "streamSid": "MZ9",
                    "start": {
                        "streamSid": "MZ9",
                        "callSid": "CA9",
                        "customParameters": {"secret": "x", "call_sid": "CA9"},
                    },
                }
            )
            # Drain the greeting frames (ends with a mark), then stop without
            # ever sending caller audio.
            for _ in range(200):
                msg = ws.receive_json()
                if msg.get("event") == "mark":
                    break
            ws.send_json({"event": "stop"})

    mgr: TelephonyManager = app.state.telephony_manager
    calls = mgr.recent_calls()
    assert len(calls) == 1
    assert calls[0]["call_sid"] == "CA9"
    # No inbound caller audio ever arrived -> the call is flagged no_audio.
    assert calls[0]["status"] == "no_audio"
