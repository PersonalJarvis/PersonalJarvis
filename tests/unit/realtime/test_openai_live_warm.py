"""OpenAI Live wake-path latency: warmable setup must not bill a call."""

from __future__ import annotations

import asyncio
import socket
import sys
import time
from types import SimpleNamespace

import pytest

from jarvis.live.config import LiveConfig


@pytest.mark.asyncio
async def test_warm_transport_preimports_without_network(monkeypatch) -> None:
    """First wake must not pay cold imports or DNS on the handshake path."""
    from jarvis.plugins.realtime.openai_live import OpenAILiveProvider

    for module in ("httpx", "websockets.asyncio.client"):
        sys.modules.pop(module, None)
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args, **kwargs: [("warm",)]
    )
    await OpenAILiveProvider.warm_transport(None)
    assert "httpx" in sys.modules
    assert "websockets.asyncio.client" in sys.modules


@pytest.mark.asyncio
async def test_warm_transport_survives_offline_dns(monkeypatch) -> None:
    """Offline DNS must degrade to an unwarned call, never a failed warm."""
    from jarvis.plugins.realtime.openai_live import OpenAILiveProvider

    def _offline(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(socket, "getaddrinfo", _offline)
    await OpenAILiveProvider.warm_transport(None)


@pytest.mark.asyncio
async def test_factory_warm_reaches_openai_live(monkeypatch) -> None:
    """The boot worker must pick up the new capability for an explicit primary."""
    from jarvis.plugins.realtime.openai_live import OpenAILiveProvider
    from jarvis.realtime import factory

    seen: list[object] = []
    real_warm = OpenAILiveProvider.warm_transport

    async def _spy(cfg: object = None) -> None:
        seen.append(cfg)
        await real_warm(cfg)

    monkeypatch.setattr(OpenAILiveProvider, "warm_transport", _spy)
    monkeypatch.setattr(
        factory, "_explicit_provider_ids", lambda _cfg: ["openai-live"]
    )
    monkeypatch.setattr(
        factory, "load", lambda _group, pid, protocol=None: OpenAILiveProvider
    )
    cfg = SimpleNamespace(
        voice=SimpleNamespace(mode="realtime", profile=""),
        brain=SimpleNamespace(realtime=SimpleNamespace(provider="openai-live")),
    )
    monkeypatch.setattr(
        factory, "_realtime_is_the_configured_voice_mode", lambda _cfg: True
    )
    await factory.realtime_warm_selected_transports(cfg)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_start_opens_store_and_permit_concurrently(monkeypatch, tmp_path) -> None:
    """Ledger open and the connection budget must overlap, not stack."""
    import jarvis.live.session as module
    from jarvis.live.recovery import connection_permit as real_permit
    from jarvis.live.session import LiveVoiceSession
    from jarvis.live.state import LiveLedger

    events: list[tuple[str, float]] = []

    async def _slow_permit() -> None:
        events.append(("permit_start", time.monotonic()))
        await asyncio.sleep(0.3)
        events.append(("permit_end", time.monotonic()))
        await real_permit()

    def _slow_ledger(path):
        events.append(("ledger_start", time.monotonic()))
        time.sleep(0.3)
        ledger = LiveLedger(path)
        events.append(("ledger_end", time.monotonic()))
        return ledger

    monkeypatch.setattr(
        module, "get_supervisor_tool_gateway", lambda: SimpleNamespace()
    )
    monkeypatch.setattr(module, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module.LiveTools, "declarations", lambda self: [])
    monkeypatch.setattr("jarvis.live.recovery.connection_permit", _slow_permit)
    monkeypatch.setattr(module, "LiveLedger", _slow_ledger)

    sent: list[dict] = []

    async def _send_json(message: dict) -> None:
        sent.append(message)

    async def _send_binary(_data: bytes) -> None:
        return None

    class Connection:
        answer_sdp = "answer"
        session_id = "wire"

        async def send(self, _event: dict) -> None:
            return None

        async def receive(self) -> dict:
            await asyncio.sleep(3600)
            return {"type": "noop"}

        async def close(self) -> None:
            return None

    class Provider:
        name = "openai-live"

        async def open_session(self, _cfg):
            return Connection()

    cfg = SimpleNamespace(
        brain=SimpleNamespace(reply_language="en"),
        live=LiveConfig(
            configured=True, backend_model="chosen-model", model="gpt-live-1"
        ),
    )
    session = LiveVoiceSession(
        session_id="warm-wake",
        send_json=_send_json,
        send_binary=_send_binary,
        providers=[Provider()],
        config=cfg,
    )
    try:
        await session.handle_control(
            {"type": "audio_start", "sample_rate": 48000, "webrtc_offer_sdp": "v=0"}
        )
    finally:
        await session.end(reason="client_stop")
    assert any(message.get("type") == "audio_ready" for message in sent)
    stamp = dict(events)
    assert stamp["permit_start"] < stamp["ledger_end"]
    assert stamp["ledger_start"] < stamp["permit_end"]
