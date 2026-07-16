"""B2 slice 2 — the /ws/audio route receive loop (fake WS, no real server).

Drives ``browser_voice_ws()`` directly with a fake WebSocket + an injected
session factory: binary frames dispatch to handle_audio_frame, JSON control
frames to handle_control, the AP-20 RuntimeError-break is terminal, and end()
runs in the finally. The real socket + AudioWorklet are browser-only (slice 3).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

import jarvis.browser_voice.route as route_mod
from jarvis.browser_voice.route import browser_voice_ws
from jarvis.core.bus import EventBus
from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession
from jarvis.sessions.recorder import SessionRecorder
from jarvis.sessions.store import SessionStore
from jarvis.ui.web.missions_auth import register_token, revoke_token
from tests.fakes.fake_realtime import (
    FakeRealtimeProvider,
    FakeRealtimeToolBridge,
)

_VALID_TOKEN = "registered-session-token"  # noqa: S105 -- synthetic test token
_INVALID_TOKEN = "invalid-token"  # noqa: S105 -- synthetic test token


@pytest.fixture(autouse=True)
def _registered_browser_token():
    register_token(_VALID_TOKEN)
    try:
        yield
    finally:
        revoke_token(_VALID_TOKEN)


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
    def __init__(
        self,
        incoming,
        *,
        state,
        client_host: str = "127.0.0.1",
        token: str = _VALID_TOKEN,
    ) -> None:
        self._incoming = list(incoming)
        self.scope = {
            "app": SimpleNamespace(state=state),
            "client": (client_host, 50_000),
            "headers": (
                [(b"cookie", f"jarvis_session={token}".encode("ascii"))]
                if token
                else []
            ),
        }
        self.query_params = {}
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


def test_audio_queue_drops_oldest_without_blocking() -> None:
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=2)

    assert route_mod._enqueue_audio_frame(queue, b"one") is False
    assert route_mod._enqueue_audio_frame(queue, b"two") is False
    assert route_mod._enqueue_audio_frame(queue, b"three") is True
    assert queue.get_nowait() == b"two"
    assert queue.get_nowait() == b"three"


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


@pytest.mark.parametrize("client_host", ["127.0.0.1", "::1"])
async def test_loopback_route_rejects_missing_token(client_host: str) -> None:
    rec = _RecSession()
    ws = _FakeWS(
        [],
        state=_state(rec),
        client_host=client_host,
        token="",
    )

    await browser_voice_ws(ws)

    assert ws.closed == (4401, "unauthorized")
    assert rec.ended is False


@pytest.mark.parametrize("token", ["", "invalid-token"])
async def test_external_route_rejects_missing_or_invalid_token_before_session_build(
    token: str,
) -> None:
    rec = _RecSession()
    built = False
    state = _state(rec)

    def _factory(**_kwargs):
        nonlocal built
        built = True
        return rec

    state.browser_voice_session_factory = _factory
    ws = _FakeWS([], state=state, client_host="203.0.113.8", token=token)

    await browser_voice_ws(ws)

    assert ws.accepted is True
    assert ws.closed == (4401, "unauthorized")
    assert built is False
    assert rec.ended is False


async def test_external_route_accepts_registered_token() -> None:
    rec = _RecSession()
    ws = _FakeWS(
        [{"type": "websocket.disconnect", "code": 1000}],
        state=_state(rec),
        client_host="203.0.113.8",
        token=_VALID_TOKEN,
    )

    await browser_voice_ws(ws)

    assert ws.accepted is True
    assert ws.closed is None
    assert rec.ended is True


async def test_loopback_route_rejects_supplied_invalid_token() -> None:
    rec = _RecSession()
    ws = _FakeWS([], state=_state(rec), token=_INVALID_TOKEN)

    await browser_voice_ws(ws)

    assert ws.closed == (4401, "unauthorized")
    assert rec.ended is False


async def test_route_drops_stale_frames_when_provider_is_backpressured(
    monkeypatch,
) -> None:
    monkeypatch.setattr(route_mod, "_AUDIO_QUEUE_MAX_FRAMES", 2)
    release = asyncio.Event()

    class _SlowSession(_RecSession):
        async def handle_audio_frame(self, data: bytes) -> None:
            self.audio.append(bytes(data))
            if len(self.audio) == 1:
                await release.wait()

    session = _SlowSession()

    class _ReleaseOnDisconnect(_FakeWS):
        async def receive(self) -> dict:
            if self._incoming:
                return self._incoming.pop(0)
            release.set()
            raise WebSocketDisconnect()

    ws = _ReleaseOnDisconnect(
        [
            {"type": "websocket.receive", "bytes": bytes([index])}
            for index in range(10)
        ],
        state=_state(session),
    )

    await browser_voice_ws(ws)

    assert len(session.audio) <= 3
    assert session.audio[-1] == bytes([9])
    assert session.ended


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


async def test_committed_realtime_failure_closes_without_classic_replay(monkeypatch):
    classic = _RecSession()

    class _CommittedRealtime(_RecSession):
        is_realtime = True

        def __init__(self, send_json) -> None:
            super().__init__()
            self._send_json = send_json

        async def handle_control(self, msg: dict) -> None:
            await self._send_json(
                {
                    "type": "transcript",
                    "role": "user",
                    "text": "Run the action",
                    "is_final": True,
                }
            )
            raise RuntimeError("provider failed after accepting the turn")

    built: dict[str, _CommittedRealtime] = {}

    def _build(**kwargs):
        session = _CommittedRealtime(kwargs["send_json"])
        built["session"] = session
        return session

    state = _state(classic)
    state.config.voice = SimpleNamespace(mode="realtime")
    monkeypatch.setattr(route_mod, "_build_browser_session", _build)
    ws = _FakeWS(
        [
            {
                "type": "websocket.receive",
                "text": '{"type":"audio_start","sample_rate":48000}',
            }
        ],
        state=state,
    )

    await browser_voice_ws(ws)

    assert built["session"].ended is True
    assert classic.controls == []
    assert classic.audio == []
    assert {"type": "mode_fallback", "mode": "pipeline"} not in ws.sent_json
    assert any(
        message.get("type") == "provider_error"
        and "duplicate actions" in str(message.get("error", ""))
        for message in ws.sent_json
    )
    assert ws.closed == (1011, "realtime failed after committed turn")


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
