"""Unit tests for the OpenAI GA realtime adapter."""

from __future__ import annotations

import asyncio
import base64
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
        self.response_create_payloads: list[dict[str, Any]] = []
        self.response_cancels: list[str] = []
        self.conversation = SimpleNamespace(
            item=SimpleNamespace(create=self._create_item)
        )
        self.response = SimpleNamespace(
            create=self._create_response,
            cancel=self._cancel_response,
        )
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

    async def _create_response(self, **kwargs: Any) -> None:
        self.response_creates += 1
        self.response_create_payloads.append(kwargs)

    async def _cancel_response(self, *, response_id: str | None = None) -> None:
        self.response_cancels.append(response_id or "<active>")


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
        RealtimeSessionConfig(
            model=model,
            voice="echo",
            language="en",
            silence_duration_ms=2_700,
        )
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
    assert payload["audio"]["input"]["turn_detection"]["interrupt_response"] is False
    assert payload["audio"]["input"]["turn_detection"]["silence_duration_ms"] == 2_700
    assert payload["audio"]["output"]["voice"] == "echo"
    await session.request_response()
    assert client.realtime.last_conn.response_creates == 1
    await session.close()


@pytest.mark.asyncio
async def test_text_update_creates_tool_free_audio_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig()
    )
    conn = holder["client"].realtime.last_conn

    await session.send_text("Deliver the completed mission update.")

    assert conn.created_items == [
        {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Deliver the completed mission update.",
                }
            ],
        }
    ]
    response = conn.response_create_payloads[0]["response"]
    assert response["tool_choice"] == "none"
    assert response["metadata"]["jarvis_request_id"]
    await session.close()


@pytest.mark.asyncio
async def test_unsolicited_second_response_is_cancelled_without_replaying_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One manual request may emit exactly one audible response lifecycle."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig()
    )
    conn = holder["client"].realtime.last_conn

    await session.request_response()
    marker = conn.response_create_payloads[0]["response"]["metadata"][
        "jarvis_request_id"
    ]
    conn._events = iter(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(
                    id="resp-requested",
                    metadata={"jarvis_request_id": marker},
                ),
            ),
            SimpleNamespace(
                type="response.output_audio.delta",
                response_id="resp-requested",
                item_id="item-requested",
                delta=base64.b64encode(b"\x01\x00").decode("ascii"),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-requested"),
            ),
            # Live incident 2026-07-11: after the completed response returned
            # the desktop to LISTENING, another response started without a new
            # final input transcript or client request. Its PCM must never be
            # forwarded to the speaker.
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-unsolicited", metadata=None),
            ),
            SimpleNamespace(
                type="response.output_audio.delta",
                response_id="resp-unsolicited",
                item_id="item-unsolicited",
                delta=base64.b64encode(b"\x02\x00").decode("ascii"),
            ),
            SimpleNamespace(
                type="response.output_audio_transcript.delta",
                response_id="resp-unsolicited",
                delta="Repeated answer.",
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-unsolicited"),
            ),
        ]
    )
    session._events = conn.__aiter__()

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == ["audio_delta", "turn_complete"]
    assert events[0].audio.pcm == b"\x01\x00"
    assert conn.response_cancels == ["resp-unsolicited"]
    await session.close()


@pytest.mark.asyncio
async def test_automatic_response_race_consumes_only_one_manual_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unmarked VAD response racing response.create must not yield two replies."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig()
    )
    conn = holder["client"].realtime.last_conn

    await session.request_response()
    marker = conn.response_create_payloads[0]["response"]["metadata"][
        "jarvis_request_id"
    ]
    conn._events = iter(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-vad-race", metadata=None),
            ),
            SimpleNamespace(
                type="response.output_audio.delta",
                response_id="resp-vad-race",
                item_id="item-vad-race",
                delta=base64.b64encode(b"\x03\x00").decode("ascii"),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-vad-race"),
            ),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(
                    id="resp-client-late",
                    metadata={"jarvis_request_id": marker},
                ),
            ),
            SimpleNamespace(
                type="response.output_audio.delta",
                response_id="resp-client-late",
                item_id="item-client-late",
                delta=base64.b64encode(b"\x04\x00").decode("ascii"),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-client-late"),
            ),
        ]
    )
    session._events = conn.__aiter__()

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == ["audio_delta", "turn_complete"]
    assert events[0].audio.pcm == b"\x03\x00"
    assert conn.response_cancels == ["resp-client-late"]
    await session.close()


@pytest.mark.asyncio
async def test_interrupt_invalidates_late_events_from_cancelled_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig()
    )
    conn = holder["client"].realtime.last_conn

    await session.request_response()
    old_marker = conn.response_create_payloads[0]["response"]["metadata"][
        "jarvis_request_id"
    ]
    await session._handle_response_created(
        SimpleNamespace(
            type="response.created",
            response=SimpleNamespace(
                id="resp-old",
                metadata={"jarvis_request_id": old_marker},
            ),
        )
    )
    await session.interrupt()
    await session.request_response()
    new_marker = conn.response_create_payloads[1]["response"]["metadata"][
        "jarvis_request_id"
    ]
    conn._events = iter(
        [
            SimpleNamespace(
                type="response.output_audio.delta",
                response_id="resp-old",
                item_id="item-old",
                delta=base64.b64encode(b"\x01\x00").decode("ascii"),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-old"),
            ),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(
                    id="resp-new",
                    metadata={"jarvis_request_id": new_marker},
                ),
            ),
            SimpleNamespace(
                type="response.output_audio.delta",
                response_id="resp-new",
                item_id="item-new",
                delta=base64.b64encode(b"\x02\x00").decode("ascii"),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-new"),
            ),
        ]
    )
    session._events = conn.__aiter__()

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == ["audio_delta", "turn_complete"]
    assert events[0].audio.pcm == b"\x02\x00"
    assert conn.response_cancels == ["<active>"]
    await session.close()


@pytest.mark.asyncio
async def test_interrupt_invalidates_pending_response_before_created_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig()
    )
    conn = holder["client"].realtime.last_conn

    await session.request_response()
    old_marker = conn.response_create_payloads[0]["response"]["metadata"][
        "jarvis_request_id"
    ]
    await session.interrupt()
    await session.request_response()
    new_marker = conn.response_create_payloads[1]["response"]["metadata"][
        "jarvis_request_id"
    ]
    conn._events = iter(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(
                    id="resp-old",
                    metadata={"jarvis_request_id": old_marker},
                ),
            ),
            SimpleNamespace(
                type="response.output_audio.delta",
                response_id="resp-old",
                item_id="item-old",
                delta=base64.b64encode(b"OLD").decode("ascii"),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-old"),
            ),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(
                    id="resp-new",
                    metadata={"jarvis_request_id": new_marker},
                ),
            ),
            SimpleNamespace(
                type="response.output_audio.delta",
                response_id="resp-new",
                item_id="item-new",
                delta=base64.b64encode(b"NEW").decode("ascii"),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-new"),
            ),
        ]
    )
    session._events = conn.__aiter__()

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == ["audio_delta", "turn_complete"]
    assert events[0].audio.pcm == b"NEW"
    assert conn.response_cancels == ["<active>", "resp-old"]
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
@pytest.mark.parametrize(
    ("code", "recoverable"),
    [
        ("conversation_already_has_active_response", True),
        ("rate_limit_exceeded", False),
    ],
)
async def test_runtime_errors_preserve_provider_recoverability(
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    recoverable: bool,
) -> None:
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig()
    )
    conn = holder["client"].realtime.last_conn
    conn._events = iter(
        [
            SimpleNamespace(
                type="error",
                error=SimpleNamespace(code=code, message="Provider rejected operation"),
            )
        ]
    )
    session._events = conn.__aiter__()

    event = await anext(session.receive())

    assert event.type == "error"
    assert event.recoverable is recoverable
    assert code in event.error
    await session.close()


@pytest.mark.asyncio
async def test_failed_response_done_is_reported_before_turn_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig()
    )
    conn = holder["client"].realtime.last_conn

    await session.request_response()
    marker = conn.response_create_payloads[0]["response"]["metadata"][
        "jarvis_request_id"
    ]
    conn._events = iter(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(
                    id="resp-failed",
                    metadata={"jarvis_request_id": marker},
                ),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(
                    id="resp-failed",
                    status="failed",
                    status_details=SimpleNamespace(
                        error=SimpleNamespace(
                            code="server_error",
                            message="The response could not be generated.",
                        )
                    ),
                ),
            ),
        ]
    )
    session._events = conn.__aiter__()

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == ["error", "turn_complete"]
    assert events[0].recoverable is True
    assert "failed" in str(events[0].error)
    assert "server_error" in str(events[0].error)
    await session.close()


@pytest.mark.asyncio
async def test_response_requests_wait_for_the_active_response_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig()
    )
    conn = holder["client"].realtime.last_conn

    await session.send_text("First trusted update")
    first_marker = conn.response_create_payloads[0]["response"]["metadata"][
        "jarvis_request_id"
    ]
    second = asyncio.create_task(session.send_text("Second trusted update"))
    await asyncio.sleep(0)

    assert second.done() is False
    assert conn.response_creates == 1

    conn._events = iter(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(
                    id="resp-first",
                    metadata={"jarvis_request_id": first_marker},
                ),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-first"),
            ),
        ]
    )
    session._events = conn.__aiter__()

    event = await anext(session.receive())
    await second

    assert event.type == "turn_complete"
    assert conn.response_creates == 2
    await session.close()


@pytest.mark.asyncio
async def test_keyless_provider_is_unavailable() -> None:
    assert await OpenAIRealtimeProvider().can_open_duplex_session() is False


@pytest.mark.asyncio
async def test_transcription_failure_is_a_final_input_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig()
    )
    conn = holder["client"].realtime.last_conn
    conn._events = iter(
        [
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.failed",
                item_id="failed-input-1",
                error=SimpleNamespace(
                    code="transcription_failed",
                    message="Input transcription was unavailable",
                ),
            )
        ]
    )
    session._events = conn.__aiter__()

    event = await anext(session.receive())

    assert event.type == "input_transcript"
    assert event.text == ""
    assert event.is_final is True
    assert event.item_id == "failed-input-1"
    assert "transcription_failed" in event.error
    await session.close()


@pytest.mark.asyncio
async def test_completed_transcription_preserves_input_item_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig()
    )
    conn = holder["client"].realtime.last_conn
    conn._events = iter(
        [
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.completed",
                item_id="input-item-1",
                transcript="One request",
            )
        ]
    )
    session._events = conn.__aiter__()

    event = await anext(session.receive())

    assert event.type == "input_transcript"
    assert event.item_id == "input-item-1"
    assert event.text == "One request"
    await session.close()


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

    await session.request_response(required_tool="open_app")
    assert conn.response_create_payloads[0]["response"]["tool_choice"] == {
        "type": "function",
        "name": "open_app",
    }
    marker = conn.response_create_payloads[0]["response"]["metadata"][
        "jarvis_request_id"
    ]
    conn._events = iter(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(
                    id="resp-tool",
                    metadata={"jarvis_request_id": marker},
                ),
            ),
            SimpleNamespace(
                type="response.function_call_arguments.done",
                response_id="resp-tool",
                call_id="call-1",
                name="open_app",
                arguments='{"app_name":"Calculator"}',
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-tool"),
            ),
            # The follow-up marker is created while handling the preceding
            # response.done. No metadata here exercises the compatibility
            # path for SDK/server versions that do not echo response metadata.
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-final", metadata=None),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-final"),
            ),
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
    assert conn.response_creates == 1
    final_event = await anext(events)
    assert final_event.type == "turn_complete"
    assert conn.response_creates == 2
    assert conn.response_cancels == []
    await session.close()


@pytest.mark.asyncio
async def test_live_session_update_replaces_tool_declarations(
    monkeypatch: pytest.MonkeyPatch,
):
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig()
    )
    declaration = {
        "name": "new_tool",
        "description": "A newly connected tool.",
        "parameters": {"type": "object", "properties": {}},
    }

    await session.update_session(tools=(declaration,))

    update = holder["client"].realtime.last_conn.session_updates[-1]
    assert update["tools"] == [{"type": "function", **declaration}]
    assert update["tool_choice"] == "auto"
    await session.close()
