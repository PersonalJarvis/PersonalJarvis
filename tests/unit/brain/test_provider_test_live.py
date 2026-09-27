"""The saved-key Test button must use the continuous voice protocol for Live."""

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.brain.provider_test import _default_realtime_probe
from jarvis.core import config, registry
from jarvis.core.protocols import ContinuousVoiceStart
from jarvis.live import recovery
from jarvis.live.config import LiveConfig
from jarvis.realtime.protocol import RealtimeSessionConfig


@pytest.fixture
def probe(monkeypatch):
    opened, sent = [], []
    receiving = asyncio.Event()

    class Connection:
        terminal = "session.started"
        closed = False

        async def receive(self):
            receiving.set()
            if self.terminal == "wait":
                await asyncio.Event().wait()
            return {"type": self.terminal, "error": {"message": "private provider body"}}

        async def send(self, event):
            sent.append(event)

        async def close(self):
            self.closed = True

    connection = Connection()

    class Provider:
        continuous_conversation = True
        credential_candidates = (("synthetic_slot", "SYNTHETIC_ENV"),)

        def __init__(self, *, api_key):
            assert api_key == "synthetic"

        async def open_session(self, start):
            opened.append(start)
            return connection

    async def permit():
        return None

    monkeypatch.setattr(registry, "load", lambda *args, **kwargs: Provider)
    monkeypatch.setattr(config, "get_secret_any", lambda *args: "synthetic")
    monkeypatch.setattr(recovery, "connection_permit", permit)
    cfg = SimpleNamespace(
        live=LiveConfig(configured=True, backend_model="chosen-model", voice="gleam"),
        brain=SimpleNamespace(providers={}),
    )
    return SimpleNamespace(
        cfg=cfg, provider=Provider, connection=connection, opened=opened,
        sent=sent, receiving=receiving, spec=SimpleNamespace(id="continuous-test"),
    )


@pytest.mark.asyncio
async def test_live_key_probe_uses_saved_selection_and_closes(probe):
    before = probe.cfg.live.model_dump()
    elapsed = await _default_realtime_probe(probe.spec, probe.cfg, timeout_s=1)
    assert elapsed >= 0
    assert len(probe.opened) == 1
    start = probe.opened[0]
    assert isinstance(start, ContinuousVoiceStart)
    assert start.session["model"] == "gpt-live-1"
    assert start.session["audio"]["output"]["voice"] == "gleam"
    assert start.session["delegation"]["responses"]["model"] == "chosen-model"
    assert start.session["store"] is False
    assert probe.sent == [{"type": "session.close"}]
    assert probe.connection.closed
    assert probe.cfg.live.model_dump() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["error", "session.closed"])
async def test_rejected_live_probe_closes_without_exposing_provider_body(probe, terminal):
    probe.connection.terminal = terminal
    with pytest.raises(RuntimeError, match="selected session") as caught:
        await _default_realtime_probe(probe.spec, probe.cfg, timeout_s=1)
    assert "private provider body" not in str(caught.value)
    assert probe.connection.closed


@pytest.mark.asyncio
async def test_live_probe_timeout_releases_connection(probe):
    probe.connection.terminal = "wait"
    with pytest.raises(TimeoutError):
        await _default_realtime_probe(probe.spec, probe.cfg, timeout_s=0.01)
    assert probe.connection.closed


@pytest.mark.asyncio
async def test_live_probe_cancellation_releases_connection(probe):
    probe.connection.terminal = "wait"
    task = asyncio.create_task(_default_realtime_probe(probe.spec, probe.cfg, timeout_s=1))
    await probe.receiving.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert probe.connection.closed


@pytest.mark.asyncio
async def test_probe_budget_includes_waiting_for_a_connection_permit(probe, monkeypatch):
    async def busy_budget():
        await asyncio.Event().wait()

    monkeypatch.setattr(recovery, "connection_permit", busy_budget)
    with pytest.raises(TimeoutError):
        await _default_realtime_probe(probe.spec, probe.cfg, timeout_s=0.01)
    assert not probe.opened


@pytest.mark.asyncio
async def test_missing_selection_does_not_silently_choose_a_model(probe):
    probe.cfg.live = LiveConfig()
    with pytest.raises(ValueError, match="Choose"):
        await _default_realtime_probe(probe.spec, probe.cfg, timeout_s=1)
    assert not probe.opened


@pytest.mark.asyncio
async def test_existing_realtime_adapters_keep_their_contract(probe):
    probe.provider.continuous_conversation = False
    await _default_realtime_probe(probe.spec, probe.cfg, timeout_s=1)
    assert isinstance(probe.opened[0], RealtimeSessionConfig)
    assert probe.connection.closed
    assert not probe.receiving.is_set()
