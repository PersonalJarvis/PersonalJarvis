"""An interrupted Live wire must not impersonate a user's hang-up."""

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.live import recovery, runtime
from jarvis.live.session import LiveVoiceSession
from jarvis.live.state import LiveLedger
from jarvis.live.tools import LiveTools
from tests.contract.test_gpt_live import Gateway


class Wire:
    answer_sdp = "existing-answer"
    session_id = "same-provider-session"

    def __init__(self):
        self.events = asyncio.Queue()
        self.closed = False
        self.sent = []

    async def receive(self):
        event = await self.events.get()
        if isinstance(event, Exception):
            raise event
        return event

    async def send(self, event):
        self.sent.append(event)
        if event["type"] == "session.close":
            await self.events.put({"type": "session.closed", "reason": "close_requested"})

    async def close(self):
        self.closed = True


@pytest.fixture
def quick_reconnect(monkeypatch):
    import jarvis.live.session as module

    permits = []

    async def permit():
        permits.append(True)

    monkeypatch.setattr(recovery, "connection_permit", permit)
    monkeypatch.setattr(module.random, "uniform", lambda *_args: 0)
    return permits


async def test_lost_sideband_with_pending_work_stays_registered_and_reattaches(
    tmp_path, quick_reconnect,
):
    old, restored = Wire(), Wire()
    await old.events.put(OSError("network lost: WinError 121"))
    ready = asyncio.Event()
    messages = []

    async def send(message):
        messages.append(message)
        if message.get("type") == "audio_ready":
            ready.set()

    class Provider:
        name = "fake-live"
        attaches = 0

        async def reattach_session(self, previous):
            assert previous is old
            self.attaches += 1
            if self.attaches < 4:
                raise OSError("DNS temporarily unavailable")
            return restored

        async def open_session(self, _cfg):
            raise AssertionError("A lost sideband must not create a second billed call")

    provider = Provider()
    ledger = LiveLedger(tmp_path / "live.db")
    session = LiveVoiceSession(
        session_id="network-drop", send_json=send, send_binary=send,
        config=SimpleNamespace(brain=SimpleNamespace(reply_language="en")),
        providers=[provider],
    )
    session._connection = old
    session._ledger = ledger
    session._tools = LiveTools(Gateway(), ledger, session.session_id,
                               language="en", backend_model="fake")
    ledger.claim(session.session_id, "uncertain", "write-file", {}, 0)
    session._responses["thinking"] = [{"name": "write-file"}]
    pending = asyncio.create_task(asyncio.Event().wait())
    session._jobs.add(pending)
    runtime.register(session)
    session._pump_task = asyncio.create_task(session._pump())
    try:
        await asyncio.wait_for(ready.wait(), 2)
        assert session in runtime.active()
        assert not session._closed.is_set()
        assert not session.failed and not session.hangup_reason
        assert len(quick_reconnect) == provider.attaches == 4
        assert old.closed and not pending.done()
        assert session._wire_epoch == 0
        assert not session._tools.gateway.calls
        assert not session._tools.accepting
        assert not any(m.get("type") in {"provider_error", "audio_closed"} for m in messages)
        assert messages[-1]["reuse_webrtc"] is True
        assert "thinking" in session._responses
    finally:
        session._jobs.discard(pending)
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await asyncio.wait_for(session.end(reason="client_stop"), 2)


async def test_button_hangup_cancels_an_offline_reconnect_immediately(tmp_path, quick_reconnect):
    entered = asyncio.Event()

    class Provider:
        name = "fake-live"

        async def reattach_session(self, _previous):
            entered.set()
            await asyncio.Event().wait()

    async def send(_message):
        return None

    session = LiveVoiceSession(
        session_id="stop-offline", send_json=send, send_binary=send,
        config=SimpleNamespace(brain=SimpleNamespace(reply_language="en")),
        providers=[Provider()],
    )
    session._connection = Wire()
    session._ledger = LiveLedger(tmp_path / "offline.db")
    session._tools = LiveTools(Gateway(), session._ledger, session.session_id,
                               language="en", backend_model="fake")
    await session._connection.events.put(OSError("offline"))
    runtime.register(session)
    session._pump_task = asyncio.create_task(session._pump())
    await asyncio.wait_for(entered.wait(), 2)
    assert session.phase == "connecting"
    await asyncio.wait_for(session.end(reason="client_stop"), 1)
    assert session._ended and session._pump_task.done()
    assert session.hangup_reason == "client_stop"
    assert session not in runtime.active()


@pytest.mark.parametrize("next_text", ["No", "Explain the screen", "Yes, but keep talking"])
async def test_live_hangup_question_cannot_authorize_a_later_unrelated_yes(tmp_path, next_text):
    ledger = LiveLedger(tmp_path / "hangup.db")
    tools = LiveTools(Gateway(), ledger, "s", language="en", backend_model="fake")
    questions = []

    async def ask(text):
        questions.append(text)

    tools.ask_hangup = ask
    try:
        for revision, text in enumerate(["Hang up", next_text, "Yes"]):
            tools.user_text, tools.revision = text, revision
            result = await tools.execute(str(revision), "end_call", {}, revision)
            assert not result["success"] and not tools.end_requested
        assert len(questions) == 1
    finally:
        ledger.close()


async def test_transport_reattach_reuses_the_existing_session_without_rest_creation(monkeypatch):
    import httpx
    import websockets.asyncio.client

    from jarvis.plugins.realtime.openai_live import OpenAILiveConnection, OpenAILiveProvider

    connected = []
    wire = object()

    async def connect(url, **kwargs):
        connected.append(url)
        return wire

    def no_rest(*_args, **_kwargs):
        raise AssertionError("Reattaching must not create or hang up a billed session")

    monkeypatch.setattr(websockets.asyncio.client, "connect", connect)
    monkeypatch.setattr(httpx, "AsyncClient", no_rest)
    provider = OpenAILiveProvider(api_key="fake-test-credential")
    old = OpenAILiveConnection(object(), session_id="existing-session", answer_sdp="existing-sdp")
    result = await provider.reattach_session(old)
    assert result.socket is wire
    assert result.session_id == old.session_id
    assert result.answer_sdp == old.answer_sdp
    assert connected == ["wss://api.openai.com/v1/live/sessions/existing-session/attach"]


async def test_expired_sideband_allows_guarded_replacement(monkeypatch):
    import websockets.asyncio.client
    from websockets.exceptions import InvalidStatus

    from jarvis.plugins.realtime.openai_live import OpenAILiveConnection, OpenAILiveProvider

    async def expired(*_args, **_kwargs):
        raise InvalidStatus(SimpleNamespace(status_code=404))

    monkeypatch.setattr(websockets.asyncio.client, "connect", expired)
    provider = OpenAILiveProvider(api_key="fake-test-credential")
    old = OpenAILiveConnection(object(), session_id="expired", answer_sdp="old-sdp")
    assert await provider.reattach_session(old) is None
