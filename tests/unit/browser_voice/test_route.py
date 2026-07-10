"""B2 slice 2 — the /ws/audio route receive loop (fake WS, no real server).

Drives ``browser_voice_ws()`` directly with a fake WebSocket + an injected
session factory: binary frames dispatch to handle_audio_frame, JSON control
frames to handle_control, the AP-20 RuntimeError-break is terminal, and end()
runs in the finally. The real socket + AudioWorklet are browser-only (slice 3).
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import WebSocketDisconnect

import jarvis.browser_voice.route as route_mod
from jarvis.browser_voice.route import browser_voice_ws


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
