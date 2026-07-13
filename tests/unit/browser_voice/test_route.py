"""B2 slice 2 — the /ws/audio route receive loop (fake WS, no real server).

Drives ``browser_voice_ws()`` directly with a fake WebSocket + an injected
session factory: binary frames dispatch to handle_audio_frame, JSON control
frames to handle_control, the AP-20 RuntimeError-break is terminal, and end()
runs in the finally. The real socket + AudioWorklet are browser-only (slice 3).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import WebSocketDisconnect

import jarvis.browser_voice.route as route_mod
from jarvis.browser_voice.route import browser_voice_ws
from jarvis.core.bus import EventBus
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession
from jarvis.sessions.recorder import SessionRecorder
from jarvis.sessions.store import SessionStore
from tests.fakes.fake_realtime import (
    FakeRealtimeProvider,
    FakeRealtimeToolBridge,
)


class _RecSession:
    def __init__(self) -> None:
        self.audio: list[bytes] = []
        self.controls: list[dict] = []
        self.ended = False

    async def handle_audio_frame(self, data: bytes) -> None:
        self.audio.append(bytes(data))

    async def handle_control(self, msg: dict) -> None:
        self.controls.append(msg)

    async def end(self, reason: str = "") -> None:
        self.ended = True


class _FakeWS:
    def __init__(self, incoming, *, state) -> None:
        self._incoming = list(incoming)
        self.scope = {"app": SimpleNamespace(state=state)}
        self.accepted = False
        self.sent_bytes: list[bytes] = []
        self.sent_json: list[dict] = []
        self.closed = None

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict:
        if self._incoming:
            return self._incoming.pop(0)
        raise WebSocketDisconnect()

    async def send_bytes(self, b) -> None:
        self.sent_bytes.append(bytes(b))

    async def send_json(self, m) -> None:
        self.sent_json.append(m)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


def _state(session, cfg=None):
    # Default cfg explicitly opts into the classic bridge (Task 7 inverted the
    # gate to default-OFF; these tests exercise the classic dispatch/session
    # behavior, not the gate itself — see test_route_closes_when_disabled for
    # that).
    default_cfg = SimpleNamespace(browser_voice=SimpleNamespace(enabled=True))
    return SimpleNamespace(
        config=cfg if cfg is not None else default_cfg,
        bus=None,
        browser_voice_session_factory=lambda **kw: session,
    )


def test_classic_fallback_language_uses_canonical_default_and_pin() -> None:
    auto_cfg = SimpleNamespace(brain=SimpleNamespace(reply_language="auto"))
    spanish_cfg = SimpleNamespace(brain=SimpleNamespace(reply_language="es"))

    assert route_mod._resolve_language(auto_cfg) == "en-US"
    assert route_mod._resolve_language(spanish_cfg) == "es-ES"


async def test_route_dispatches_binary_and_control_then_ends():
    rec = _RecSession()
    ws = _FakeWS(
        [
            {"type": "websocket.receive", "bytes": b"\x01\x00\x02\x00"},
            {"type": "websocket.receive", "text": '{"type":"barge_in"}'},
            {"type": "websocket.disconnect", "code": 1000},
        ],
        state=_state(rec),
    )
    await browser_voice_ws(ws)
    assert ws.accepted
    assert rec.audio == [b"\x01\x00\x02\x00"]
    assert rec.controls == [{"type": "barge_in"}]
    assert rec.ended  # end() ran in the finally


async def test_route_closes_when_disabled():
    rec = _RecSession()
    cfg = SimpleNamespace(browser_voice=SimpleNamespace(enabled=False))
    ws = _FakeWS([], state=_state(rec, cfg=cfg))
    await browser_voice_ws(ws)
    assert ws.closed is not None and ws.closed[0] == 1008
    assert rec.audio == []  # never reached the loop


async def test_route_closes_when_speech_stack_unavailable():
    state = SimpleNamespace(
        # Explicit opt-in (Task 7 default-OFF gate) so the socket reaches the
        # session build instead of closing early on the disabled gate.
        config=SimpleNamespace(browser_voice=SimpleNamespace(enabled=True)),
        bus=None,
        browser_voice_session_factory=lambda **kw: None,  # build failed (no key)
    )
    ws = _FakeWS([], state=state)
    await browser_voice_ws(ws)
    assert ws.closed is not None and ws.closed[0] == 1011


async def test_route_breaks_on_runtimeerror():
    # AP-20: an unclean disconnect raises RuntimeError (not WebSocketDisconnect)
    # -> terminal break, end() still runs.
    rec = _RecSession()

    class _RaisingWS(_FakeWS):
        async def receive(self) -> dict:
            raise RuntimeError("WebSocket is not connected")

    ws = _RaisingWS([], state=_state(rec))
    await browser_voice_ws(ws)
    assert rec.ended


async def test_realtime_handshake_failure_crosses_to_classic_browser_pipeline(
    monkeypatch,
):
    classic = _RecSession()

    class _FailedRealtime(_RecSession):
        is_realtime = True

        async def handle_control(self, msg: dict) -> None:
            raise RuntimeError("simulated duplex handshake failure")

    failed = _FailedRealtime()
    state = _state(classic)
    state.config.voice = SimpleNamespace(mode="realtime")
    monkeypatch.setattr(route_mod, "_build_browser_session", lambda **_kwargs: failed)
    ws = _FakeWS(
        [
            {
                "type": "websocket.receive",
                "text": '{"type":"audio_start","sample_rate":48000}',
            },
            {"type": "websocket.disconnect", "code": 1000},
        ],
        state=state,
    )

    await browser_voice_ws(ws)

    assert failed.ended
    assert classic.controls == [{"type": "audio_start", "sample_rate": 48_000}]
    assert classic.ended
    assert {"type": "mode_fallback", "mode": "pipeline"} in ws.sent_json


async def test_dead_realtime_stream_crosses_to_classic_on_next_audio_frame(monkeypatch):
    classic = _RecSession()

    class _DeadRealtime(_RecSession):
        is_realtime = True
        failed = True
        failure_detail = "provider stream ended"

    dead = _DeadRealtime()
    state = _state(classic)
    state.config.voice = SimpleNamespace(mode="realtime")
    monkeypatch.setattr(route_mod, "_build_browser_session", lambda **_kwargs: dead)
    ws = _FakeWS(
        [
            {
                "type": "websocket.receive",
                "text": '{"type":"audio_start","sample_rate":48000}',
            },
            {"type": "websocket.receive", "bytes": b"\x01\x00\x02\x00"},
            {"type": "websocket.disconnect", "code": 1000},
        ],
        state=state,
    )

    await browser_voice_ws(ws)

    assert dead.ended
    assert classic.controls == [{"type": "audio_start", "sample_rate": 48_000}]
    assert classic.audio == [b"\x01\x00\x02\x00"]
    assert {"type": "mode_fallback", "mode": "pipeline"} in ws.sent_json


async def test_realtime_socket_drop_flushes_pending_turn_to_session_store(
    monkeypatch,
    tmp_path,
):
    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        bus = EventBus()
        SessionRecorder(store).attach(bus)
        provider = FakeRealtimeProvider(
            "openai-realtime",
            [
                RealtimeEvent(
                    type="input_transcript",
                    text="Keep this browser turn",
                    is_final=True,
                ),
                RealtimeEvent(
                    type="output_transcript_delta",
                    text="This answer arrived before disconnect.",
                ),
            ],
            hold_after_events=True,
        )
        cfg = SimpleNamespace(
            browser_voice=SimpleNamespace(enabled=True),
            voice=SimpleNamespace(mode="realtime", realtime_tool_mode="direct"),
            brain=SimpleNamespace(
                reply_language="en",
                providers={
                    "openai-realtime": SimpleNamespace(
                        model="live-model",
                        voice="voice",
                    )
                },
            ),
            stt=SimpleNamespace(language="auto"),
        )
        state = SimpleNamespace(config=cfg, bus=bus, brain=None)

        def _build_realtime(**kwargs):
            return RealtimeVoiceSession(
                session_id=kwargs["session_id"],
                send_binary=kwargs["send_binary"],
                send_json=kwargs["send_json"],
                config=cfg,
                provider=provider,
                bus=bus,
                surface="browser",
                tool_bridge=FakeRealtimeToolBridge(),
            )

        monkeypatch.setattr(route_mod, "_build_browser_session", _build_realtime)

        class _DropAfterProviderEvents(_FakeWS):
            async def receive(self) -> dict:
                if self._incoming:
                    return self._incoming.pop(0)
                assert provider.session is not None
                await asyncio.wait_for(provider.session.events_drained.wait(), timeout=1.0)
                raise WebSocketDisconnect()

        ws = _DropAfterProviderEvents(
            [
                {
                    "type": "websocket.receive",
                    "text": '{"type":"audio_start","sample_rate":48000}',
                }
            ],
            state=state,
        )

        await browser_voice_ws(ws)

        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].hangup_reason == "ws_closed"
        turns = store.get_turns(sessions[0].id)
        assert len(turns) == 1
        assert turns[0].user_text == "Keep this browser turn"
        assert turns[0].jarvis_text == "This answer arrived before disconnect."
        assert turns[0].tier == "realtime"
        assert turns[0].ended_ms is not None
        assert provider.session is not None and provider.session.closed is True
    finally:
        store.close()
