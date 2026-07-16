"""Unit tests for the xAI Grok Voice Agent realtime adapter."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.plugins.realtime.grok_realtime import GrokRealtimeProvider
from jarvis.realtime.protocol import RealtimeSessionConfig


class _FakeConn:
    def __init__(self) -> None:
        self.session_updates: list[dict[str, Any]] = []
        self.created_items: list[dict[str, Any]] = []
        self.response_creates: list[dict[str, Any]] = []
        self.response_cancels: list[str] = []
        self.appended_audio: list[str] = []
        self._events = iter(
            [
                SimpleNamespace(type="session.created"),
                SimpleNamespace(type="session.updated"),
            ]
        )
        self.session = SimpleNamespace(update=self._update_session)
        self.input_audio_buffer = SimpleNamespace(append=self._append_audio)
        self.conversation = SimpleNamespace(
            item=SimpleNamespace(
                create=self._create_item,
                truncate=self._truncate_item,
            )
        )
        self.response = SimpleNamespace(
            create=self._create_response,
            cancel=self._cancel_response,
        )

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._events)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def _update_session(self, *, session: dict[str, Any]) -> None:
        self.session_updates.append(session)

    async def _append_audio(self, *, audio: str) -> None:
        self.appended_audio.append(audio)

    async def _create_item(self, *, item: dict[str, Any]) -> None:
        self.created_items.append(item)

    async def _truncate_item(self, **_kwargs: Any) -> None:
        return None

    async def _create_response(self, **kwargs: Any) -> None:
        self.response_creates.append(kwargs)

    async def _cancel_response(self, *, response_id: str | None = None) -> None:
        self.response_cancels.append(response_id or "<active>")


class _FakeConnectCM:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn
        self.exited = False

    async def __aenter__(self) -> _FakeConn:
        return self.conn

    async def __aexit__(self, *_args: object) -> None:
        self.exited = True


class _FakeRealtimeAPI:
    def __init__(self) -> None:
        self.models: list[str] = []
        self.conn = _FakeConn()
        # Transports handed out to reconnects (BUG-064 rebuild tests).
        self.extra_conns: list[_FakeConn] = []

    def connect(self, *, model: str) -> _FakeConnectCM:
        self.models.append(model)
        if len(self.models) > 1 and self.extra_conns:
            self.conn = self.extra_conns.pop(0)
        return _FakeConnectCM(self.conn)


class _FakeAsyncOpenAI:
    last: _FakeAsyncOpenAI | None = None

    def __init__(self, *, api_key: str, base_url: str) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.realtime = _FakeRealtimeAPI()
        self.closed = False
        type(self).last = self

    async def close(self) -> None:
        self.closed = True


def _patch_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import openai

    _FakeAsyncOpenAI.last = None
    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)


@pytest.mark.asyncio
async def test_open_session_uses_xai_endpoint_model_and_transcription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch)
    provider = GrokRealtimeProvider(api_key="xai-test")

    session = await provider.open_session(
        RealtimeSessionConfig(
            model="grok-voice-think-fast-1.0",
            voice="leo",
            instructions="Be concise.",
            silence_duration_ms=2_100,
        )
    )

    client = _FakeAsyncOpenAI.last
    assert client is not None
    assert client.api_key == "xai-test"
    assert client.base_url == "https://api.x.ai/v1"
    assert client.realtime.models == ["grok-voice-think-fast-1.0"]
    payload = client.realtime.conn.session_updates[0]
    assert payload["type"] == "realtime"
    assert payload["audio"]["input"]["transcription"] == {
        "model": "grok-transcribe"
    }
    assert payload["audio"]["input"]["turn_detection"][
        "silence_duration_ms"
    ] == 2_100
    assert payload["audio"]["output"]["voice"] == "leo"
    assert payload["instructions"] == "Be concise."
    await session.close()


@pytest.mark.asyncio
async def test_default_model_audio_and_xai_events_are_mapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch)
    session = await GrokRealtimeProvider(api_key="xai-test").open_session(
        RealtimeSessionConfig()
    )
    client = _FakeAsyncOpenAI.last
    assert client is not None
    conn = client.realtime.conn
    assert client.realtime.models == ["grok-voice-latest"]

    await session.send_audio(
        SimpleNamespace(pcm=b"\x01\x00", sample_rate=24_000)
    )
    assert base64.b64decode(conn.appended_audio[0]) == b"\x01\x00"

    await session.request_response()
    marker = conn.response_creates[0]["response"]["metadata"]["jarvis_request_id"]
    conn._events = iter(
        [
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.completed",
                item_id="user-1",
                transcript="Hello Grok",
            ),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(
                    id="response-1",
                    metadata={"jarvis_request_id": marker},
                ),
            ),
            SimpleNamespace(
                type="response.output_audio.delta",
                response_id="response-1",
                item_id="assistant-1",
                delta=base64.b64encode(b"\x02\x00").decode("ascii"),
            ),
            SimpleNamespace(
                type="response.output_audio_transcript.delta",
                response_id="response-1",
                delta="Hi",
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="response-1"),
            ),
        ]
    )
    session._events = conn.__aiter__()

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "input_transcript",
        "audio_delta",
        "output_transcript_delta",
        "turn_complete",
    ]
    assert events[0].text == "Hello Grok"
    assert events[1].audio.pcm == b"\x02\x00"
    assert events[2].text == "Hi"
    await session.close()


@pytest.mark.asyncio
async def test_unsolicited_response_rearms_grok_transcription_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-064 (live incident 2026-07-16 08:07): after a barge-in cancel the
    Grok server stopped emitting input transcription events and auto-created
    responses — the session stayed connected but permanently deaf. Suppressing
    such an unsolicited response must re-send the full Grok session payload,
    including the grok-transcribe input transcription."""
    _patch_client(monkeypatch)
    session = await GrokRealtimeProvider(api_key="xai-test").open_session(
        RealtimeSessionConfig()
    )
    client = _FakeAsyncOpenAI.last
    assert client is not None
    conn = client.realtime.conn
    conn._events = iter(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-auto", metadata=None),
            ),
        ]
    )
    session._events = conn.__aiter__()

    events = [event async for event in session.receive()]

    assert events == []
    assert conn.response_cancels == ["resp-auto"]
    assert len(conn.session_updates) == 2
    rearmed = conn.session_updates[-1]
    assert rearmed["audio"]["input"]["transcription"] == {
        "model": "grok-transcribe"
    }
    assert rearmed["audio"]["input"]["turn_detection"]["create_response"] is False
    await session.close()


@pytest.mark.asyncio
async def test_deaf_grok_session_rebuild_carries_the_grok_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-064 escalation (live 2026-07-16 09:23): the re-arm ran and the Grok
    server still delivered no further input transcript. The transport rebuild
    must reconnect with the Grok model and re-send the full Grok session
    payload — including grok-transcribe input transcription — on the fresh
    connection."""
    _patch_client(monkeypatch)
    from jarvis.plugins.realtime import openai_realtime as shared_adapter

    monkeypatch.setattr(shared_adapter, "_TRANSCRIPT_OVERDUE_S", 0.0)
    session = await GrokRealtimeProvider(api_key="xai-test").open_session(
        RealtimeSessionConfig()
    )
    client = _FakeAsyncOpenAI.last
    assert client is not None
    api = client.realtime
    conn1 = api.conn
    conn2 = _FakeConn()
    conn2._events = iter([SimpleNamespace(type="session.updated")])
    api.extra_conns.append(conn2)
    conn1._events = iter(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-auto", metadata=None),
            ),
            SimpleNamespace(type="input_audio_buffer.speech_started"),
        ]
    )
    session._events = conn1.__aiter__()

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == ["speech_started", "error"]
    assert events[1].recoverable is True
    assert api.models == ["grok-voice-latest", "grok-voice-latest"]
    rebuilt_contract = conn2.session_updates[0]
    assert rebuilt_contract["audio"]["input"]["transcription"] == {
        "model": "grok-transcribe"
    }
    assert (
        rebuilt_contract["audio"]["input"]["turn_detection"]["create_response"]
        is False
    )
    await session.close()


@pytest.mark.asyncio
async def test_keyless_provider_is_unavailable() -> None:
    assert await GrokRealtimeProvider().can_open_duplex_session() is False

