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

    def connect(self, *, model: str) -> _FakeConnectCM:
        self.models.append(model)
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
async def test_keyless_provider_is_unavailable() -> None:
    assert await GrokRealtimeProvider().can_open_duplex_session() is False

