"""Unit tests for the OpenAI GA realtime adapter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.model_catalog import REALTIME_MODELS
from jarvis.plugins.realtime.openai_realtime import OpenAIRealtimeProvider
from jarvis.realtime.protocol import RealtimeSessionConfig


class _FakeConn:
    def __init__(self) -> None:
        self.session_updates: list[dict[str, Any]] = []
        self.created_items: list[dict[str, Any]] = []
        self.response_creates = 0
        self.conversation = SimpleNamespace(
            item=SimpleNamespace(create=self._create_item)
        )
        self.response = SimpleNamespace(create=self._create_response)
        self._events = iter(
            [
                SimpleNamespace(type="session.created"),
                SimpleNamespace(type="session.updated"),
            ]
        )

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._events)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    @property
    def session(self) -> _FakeConn:
        return self

    async def update(self, session: dict[str, Any]) -> None:
        self.session_updates.append(session)

    async def _create_item(self, *, item: dict[str, Any]) -> None:
        self.created_items.append(item)

    async def _create_response(self) -> None:
        self.response_creates += 1


class _FakeConnectCM:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn
        self.exited = False

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_args: object) -> None:
        self.exited = True


class _FakeRealtimeAPI:
    def __init__(self) -> None:
        self.connect_calls: list[str] = []
        self.last_conn = _FakeConn()

    def connect(self, *, model: str) -> _FakeConnectCM:
        self.connect_calls.append(model)
        return _FakeConnectCM(self.last_conn)


class _FakeAsyncOpenAI:
    def __init__(self, *, api_key: str | None = None) -> None:
        self.api_key = api_key
        self.realtime = _FakeRealtimeAPI()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _patch_openai_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, _FakeAsyncOpenAI]:
    holder: dict[str, _FakeAsyncOpenAI] = {}

    def _make_client(*, api_key: str | None = None) -> _FakeAsyncOpenAI:
        client = _FakeAsyncOpenAI(api_key=api_key)
        holder["client"] = client
        return client

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _make_client)
    return holder


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    [model.id for model in REALTIME_MODELS["openai-realtime"]],
)
async def test_every_selectable_model_uses_the_valid_ga_session_schema(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    holder = _patch_openai_client(monkeypatch)
    provider = OpenAIRealtimeProvider(api_key="test-key")

    session = await provider.open_session(
        RealtimeSessionConfig(model=model, voice="echo", language="en")
    )
    client = holder["client"]
    payload = client.realtime.last_conn.session_updates[0]

    assert client.realtime.connect_calls == [model]
    assert payload["type"] == "realtime"
    assert payload["output_modalities"] == ["audio"]
    assert payload["audio"]["input"]["format"] == {
        "type": "audio/pcm",
        "rate": 24_000,
    }
    assert payload["audio"]["output"]["format"] == {
        "type": "audio/pcm",
        "rate": 24_000,
    }
    assert payload["audio"]["input"]["transcription"]["model"] == (
        "gpt-4o-mini-transcribe"
    )
    assert "language" not in payload["audio"]["input"]["transcription"]
    assert payload["audio"]["input"]["turn_detection"]["create_response"] is False
    assert payload["audio"]["output"]["voice"] == "echo"
    await session.request_response()
    assert client.realtime.last_conn.response_creates == 1
    await session.close()


@pytest.mark.asyncio
async def test_open_session_falls_back_to_adapter_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_openai_client(monkeypatch)
    provider = OpenAIRealtimeProvider(api_key="test-key")

    session = await provider.open_session(RealtimeSessionConfig(model=""))

    assert holder["client"].realtime.connect_calls == ["gpt-realtime"]
    await session.close()


@pytest.mark.asyncio
async def test_handshake_error_rejects_session(monkeypatch: pytest.MonkeyPatch) -> None:
    holder = _patch_openai_client(monkeypatch)
    error = SimpleNamespace(code="bad_schema", message="Invalid session schema")
    holder_factory = holder

    import openai

    original = openai.AsyncOpenAI

    def _make_error_client(*, api_key=None):
        client = original(api_key=api_key)
        client.realtime.last_conn._events = iter(
            [SimpleNamespace(type="session.created"), SimpleNamespace(type="error", error=error)]
        )
        holder_factory["client"] = client
        return client

    monkeypatch.setattr(openai, "AsyncOpenAI", _make_error_client)

    with pytest.raises(RuntimeError, match="bad_schema"):
        await OpenAIRealtimeProvider(api_key="test-key").open_session(
            RealtimeSessionConfig()
        )
    assert holder_factory["client"].closed is True


@pytest.mark.asyncio
async def test_keyless_provider_is_unavailable() -> None:
    assert await OpenAIRealtimeProvider().can_open_duplex_session() is False


@pytest.mark.asyncio
async def test_tools_are_declared_mapped_and_answered(monkeypatch: pytest.MonkeyPatch):
    holder = _patch_openai_client(monkeypatch)
    declaration = {
        "name": "open_app",
        "description": "Open an application.",
        "parameters": {
            "type": "object",
            "properties": {"app_name": {"type": "string"}},
        },
    }
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(tools=(declaration,))
    )
    conn = holder["client"].realtime.last_conn
    payload = conn.session_updates[0]

    assert payload["tools"] == [{"type": "function", **declaration}]
    assert payload["tool_choice"] == "auto"

    conn._events = iter(
        [
            SimpleNamespace(
                type="response.function_call_arguments.done",
                call_id="call-1",
                name="open_app",
                arguments='{"app_name":"Calculator"}',
            ),
            SimpleNamespace(type="response.done"),
            SimpleNamespace(type="response.done"),
        ]
    )
    session._events = conn.__aiter__()
    events = session.receive()
    tool_event = await anext(events)

    assert tool_event.type == "tool_call"
    assert tool_event.tool_args == {"app_name": "Calculator"}

    await session.send_tool_result(
        "call-1",
        "open_app",
        {"success": True, "output": "opened", "error": None},
    )
    assert conn.created_items[0]["type"] == "function_call_output"
    assert conn.created_items[0]["call_id"] == "call-1"
    assert conn.response_creates == 0
    final_event = await anext(events)
    assert final_event.type == "turn_complete"
    assert conn.response_creates == 1
    await session.close()
