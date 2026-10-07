"""Startup overlap, offline preparation and cleanup without provider requests."""

import asyncio
import logging
import threading
from types import SimpleNamespace

import httpx
import pytest

from jarvis.core.protocols import ContinuousVoiceStart
from jarvis.live.timing import StartupTimings
from jarvis.plugins.realtime import openai_live as wire


class FakeHttpClient:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        self.calls.append(url)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"session": {"id": "session/test"}, "transport": {"sdp": "answer"}},
        )


@pytest.fixture
def transport(monkeypatch):
    from websockets.asyncio import client

    calls = []
    attaching = asyncio.Event()
    release = asyncio.Event()

    async def connect(*args, **kwargs):
        attaching.set()
        await release.wait()
        return SimpleNamespace()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: FakeHttpClient(calls))
    monkeypatch.setattr(client, "connect", connect)
    return calls, attaching, release


@pytest.mark.asyncio
async def test_answer_is_delivered_while_sideband_is_still_connecting(transport):
    calls, attaching, release = transport
    answers = []

    async def ready(sdp):
        answers.append(sdp)

    task = asyncio.create_task(wire.OpenAILiveProvider(api_key="fake").open_session(
        ContinuousVoiceStart(session={}, offer_sdp="offer", on_transport_ready=ready)
    ))
    await asyncio.wait_for(attaching.wait(), 2)
    assert answers == ["answer"]
    assert not task.done()
    assert len(calls) == 1
    release.set()
    connection = await task
    assert connection.answer_sdp == "answer"


@pytest.mark.asyncio
async def test_cancelled_attachment_hangs_up_created_session_once(transport):
    calls, attaching, _ = transport
    task = asyncio.create_task(wire.OpenAILiveProvider(api_key="fake").open_session(
        ContinuousVoiceStart(session={}, offer_sdp="offer")
    ))
    await asyncio.wait_for(attaching.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == [
        "https://api.openai.com/v1/live/sessions",
        "https://api.openai.com/v1/live/sessions/session%2Ftest/hangup",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [True, False])
async def test_failed_early_answer_closes_session_without_attaching(transport, cancel):
    calls, attaching, _ = transport

    async def ready(sdp):
        if cancel:
            raise asyncio.CancelledError()
        raise RuntimeError("Surface disconnected")

    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await wire.OpenAILiveProvider(api_key="fake").open_session(
            ContinuousVoiceStart(session={}, offer_sdp="offer", on_transport_ready=ready)
        )
    assert len(calls) == 2 and calls[-1].endswith("/hangup")
    assert not attaching.is_set()


@pytest.mark.asyncio
async def test_legacy_caller_without_callback_still_gets_the_answer(transport):
    _, _, release = transport
    release.set()
    result = await wire.OpenAILiveProvider(api_key="fake").open_session(
        SimpleNamespace(session={}, offer_sdp="offer")
    )
    assert result.answer_sdp == "answer"


@pytest.mark.asyncio
async def test_warm_prepares_tls_off_loop_without_opening_a_session(monkeypatch):
    owner = threading.get_ident()
    prepared = []
    monkeypatch.setattr("socket.getaddrinfo", lambda *args: [])

    class PrepareClient:
        def __init__(self, **kwargs):
            prepared.append(threading.get_ident())

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def forbidden(*args, **kwargs):
        raise AssertionError("Warmup must not open a provider session")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(httpx, "Client", PrepareClient)
    await wire.OpenAILiveProvider.warm_transport()
    assert prepared and prepared[0] != owner


@pytest.mark.asyncio
async def test_cold_http_client_construction_stays_off_audio_loop(transport, monkeypatch):
    calls, _, release = transport
    owner = threading.get_ident()
    prepared = []

    def build(**kwargs):
        prepared.append(threading.get_ident())
        return FakeHttpClient(calls)

    release.set()
    monkeypatch.setattr(httpx, "AsyncClient", build)
    await wire.OpenAILiveProvider(api_key="fake").open_session(
        ContinuousVoiceStart(session={}, offer_sdp="offer")
    )
    assert prepared and prepared[0] != owner


def test_timings_are_once_only_and_reject_untrusted_content(caplog):
    timing = StartupTimings("test-session")
    with caplog.at_level(logging.INFO, logger="jarvis.live.timing"):
        timing.mark("audio_ready")
        timing.mark("audio_ready")
        timing.browser({"capture_ready": 5, "secret-text": 1, "offer_ready": float("nan")})
        timing.browser({"capture_ready": 10, "input_released": True})
    assert len(timing.marks) == 1
    assert timing.browser_marks == {"capture_ready": 5}
    assert "secret-text" not in caplog.text
    assert caplog.text.count("phase=audio_ready") == 1


@pytest.mark.asyncio
async def test_native_handoff_finishes_before_a_slow_provider_open(monkeypatch, tmp_path):
    from jarvis.live import session as module
    from jarvis.live.config import LiveConfig

    sent = []
    handoff = asyncio.Event()
    opening = asyncio.Event()
    release = asyncio.Event()
    closed = asyncio.Event()

    async def send(message):
        sent.append(message)

    class Connection:
        session_id = "fake"
        answer_sdp = "answer"

        async def send(self, message):
            if message["type"] == "session.close":
                closed.set()

        async def receive(self):
            await closed.wait()
            return {"type": "session.closed", "usage": {"seconds": 0}}

        async def close(self):
            closed.set()

    class Provider:
        name = "test"

        async def open_session(self, config):
            assert handoff.is_set()
            await config.on_transport_ready("answer")
            opening.set()
            await release.wait()
            return Connection()

    async def take_input(self, message):
        handoff.set()

    monkeypatch.setattr(module, "get_supervisor_tool_gateway", lambda: SimpleNamespace())
    monkeypatch.setattr(module, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "_identity", lambda config: "")
    monkeypatch.setattr(module.LiveTools, "declarations", lambda self, **kw: [])
    monkeypatch.setattr(module.LiveVoiceSession, "_take_startup_input", take_input)
    config = SimpleNamespace(brain=SimpleNamespace(reply_language="en"),
                             live=LiveConfig(configured=True, backend_model="test"))
    session = module.LiveVoiceSession(session_id="handoff", send_binary=send,
                                     send_json=send, providers=[Provider()], config=config)
    task = asyncio.create_task(session.handle_control({
        "type": "audio_start", "sample_rate": 48000, "webrtc_offer_sdp": "offer",
    }))
    try:
        await asyncio.wait_for(opening.wait(), 2)
        assert any(message["type"] == "audio_transport" for message in sent)
        assert not any(message["type"] == "audio_ready" for message in sent)
        release.set()
        await task
        assert any(message["type"] == "audio_ready" for message in sent)
    finally:
        release.set()
        await task
        await session.end()


@pytest.mark.asyncio
async def test_invalid_answer_after_allocation_still_hangs_up(transport, monkeypatch):
    calls, attaching, _ = transport
    original = FakeHttpClient.post

    async def post(self, url, **kwargs):
        result = await original(self, url, **kwargs)
        if not url.endswith("hangup"):
            result.json = lambda: {"session": {"id": "session/test"}, "transport": {}}
        return result

    monkeypatch.setattr(FakeHttpClient, "post", post)
    with pytest.raises(KeyError):
        await wire.OpenAILiveProvider(api_key="fake").open_session(
            ContinuousVoiceStart(session={}, offer_sdp="offer")
        )
    assert len(calls) == 2 and calls[-1].endswith("/hangup")
    assert not attaching.is_set()


@pytest.mark.asyncio
async def test_late_connection_closes_even_if_hangup_send_fails(monkeypatch, tmp_path):
    from jarvis.live import session as module
    from jarvis.live.config import LiveConfig

    opening, release = asyncio.Event(), asyncio.Event()
    closes = []

    async def send(message):
        return None

    async def failed_send(message):
        raise RuntimeError("control disconnected")

    async def close():
        closes.append(True)

    class Provider:
        name = "test"

        async def open_session(self, config):
            opening.set()
            await release.wait()
            return SimpleNamespace(answer_sdp="answer", send=failed_send, close=close)

    monkeypatch.setattr(module, "get_supervisor_tool_gateway", lambda: SimpleNamespace())
    monkeypatch.setattr(module, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "_identity", lambda config: "")
    monkeypatch.setattr(module.LiveTools, "declarations", lambda self, **kw: [])
    config = SimpleNamespace(brain=SimpleNamespace(reply_language="en"),
                             live=LiveConfig(configured=True, backend_model="test"))
    session = module.LiveVoiceSession(session_id="cancel", send_binary=send,
                                     send_json=send, providers=[Provider()], config=config)
    task = asyncio.create_task(session.handle_control({
        "type": "audio_start", "sample_rate": 48000, "webrtc_offer_sdp": "offer",
    }))
    await asyncio.wait_for(opening.wait(), 2)
    await session.end()
    release.set()
    with pytest.raises(RuntimeError, match="control disconnected"):
        await task
    assert closes == [True]


@pytest.mark.asyncio
async def test_timing_observes_subscription_snapshots_before_adapter_dispatch():
    from jarvis.live.session import LiveVoiceSession

    async def send(message):
        return None

    async def receive():
        return {"type": "session.input_transcript.done", "snapshot": True}

    class SnapshotSession(LiveVoiceSession):
        async def _event(self, event):
            self._closed.set()  # Adapter consumes the snapshot without super().

    session = SnapshotSession(
        session_id="timing", send_json=send, send_binary=send,
        config=SimpleNamespace(brain=SimpleNamespace(reply_language="en")),
        providers=[SimpleNamespace(name="test")],
    )
    session._startup_timing = StartupTimings("timing")
    session._connection = SimpleNamespace(receive=receive)
    await session._pump()
    assert "first_input_transcript" in session._startup_timing.marks
