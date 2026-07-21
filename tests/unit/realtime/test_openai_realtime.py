"""Unit tests for the OpenAI GA realtime adapter."""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.model_catalog import REALTIME_MODELS
from jarvis.plugins.realtime import openai_realtime
from jarvis.plugins.realtime.openai_realtime import OpenAIRealtimeProvider
from jarvis.realtime.protocol import RealtimeSessionConfig


class _FakeConn:
    def __init__(self) -> None:
        self.session_updates: list[dict[str, Any]] = []
        self.created_items: list[dict[str, Any]] = []
        self.response_creates = 0
        self.response_create_payloads: list[dict[str, Any]] = []
        self.response_cancels: list[str] = []
        self.conversation = SimpleNamespace(item=SimpleNamespace(create=self._create_item))
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
        # Transports handed out to reconnects (BUG-064 rebuild tests).
        self.extra_conns: list[_FakeConn] = []
        self.connect_error: Exception | None = None

    def connect(self, *, model: str) -> _FakeConnectCM:
        self.connect_calls.append(model)
        if len(self.connect_calls) > 1:
            if self.connect_error is not None:
                raise self.connect_error
            self.last_conn = self.extra_conns.pop(0) if self.extra_conns else _FakeConn()
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
    assert payload["audio"]["input"]["transcription"]["model"] == ("gpt-4o-mini-transcribe")
    assert "language" not in payload["audio"]["input"]["transcription"]
    assert payload["audio"]["input"]["turn_detection"]["create_response"] is False
    assert payload["audio"]["input"]["turn_detection"]["interrupt_response"] is False
    assert payload["audio"]["input"]["turn_detection"]["silence_duration_ms"] == 2_700
    assert payload["audio"]["output"]["voice"] == "echo"
    await session.request_response()
    assert client.realtime.last_conn.response_creates == 1
    await session.close()


@pytest.mark.asyncio
async def test_default_config_keeps_native_server_vad_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_openai_client(monkeypatch)
    provider = OpenAIRealtimeProvider(api_key="test-key")

    session = await provider.open_session(
        RealtimeSessionConfig(voice="echo", language="en")
    )
    payload = holder["client"].realtime.last_conn.session_updates[0]
    turn_detection = payload["audio"]["input"]["turn_detection"]
    # No forced window: OpenAI's native server-VAD default decides the turn
    # end (the Settings "Thinking pause" endpoints the pipeline only).
    assert "silence_duration_ms" not in turn_detection
    await session.close()


@pytest.mark.asyncio
async def test_text_update_creates_tool_free_audio_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
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
async def test_open_session_seeds_prior_call_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-088: a mid-call open (cross-family fallback after another
    provider's transport died) carries the call transcript; the adapter must
    recreate it as conversation items so the model keeps the context."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(
            history=(
                {"role": "user", "text": "let's talk programming languages"},
                {"role": "assistant", "text": "Sure — which one?"},
            )
        )
    )
    conn = holder["client"].realtime.last_conn

    assert conn.created_items == [
        {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "let's talk programming languages",
                }
            ],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Sure — which one?"}],
        },
    ]
    await session.close()


@pytest.mark.asyncio
async def test_transport_rebuild_replays_the_current_history_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-088 x BUG-064: the in-place transport rebuild replaces the
    connection that held the conversation server-side. The rebuilt transport
    must receive the orchestrator's latest history snapshot — not the (empty)
    open-time seed — so the call continues with context."""
    holder = _patch_openai_client(monkeypatch)
    monkeypatch.setattr(openai_realtime, "_TRANSCRIPT_OVERDUE_S", 0.0)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(model="gpt-realtime")
    )
    session.set_history_snapshot(
        (
            {"role": "user", "text": "let's talk programming languages"},
            {"role": "assistant", "text": "Sure — which one?"},
            {"role": "ignored-role", "text": "dropped"},
            {"role": "user", "text": "   "},
        )
    )
    api = holder["client"].realtime
    conn1 = api.last_conn
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

    _events = [event async for event in session.receive()]

    assert session._conn is conn2
    assert conn2.created_items == [
        {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "let's talk programming languages",
                }
            ],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Sure — which one?"}],
        },
    ]
    await session.close()


@pytest.mark.asyncio
async def test_unsolicited_second_response_is_cancelled_without_replaying_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One manual request may emit exactly one audible response lifecycle."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn

    await session.request_response()
    marker = conn.response_create_payloads[0]["response"]["metadata"]["jarvis_request_id"]
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
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn

    await session.request_response()
    marker = conn.response_create_payloads[0]["response"]["metadata"]["jarvis_request_id"]
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
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn

    await session.request_response()
    old_marker = conn.response_create_payloads[0]["response"]["metadata"]["jarvis_request_id"]
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
    new_marker = conn.response_create_payloads[1]["response"]["metadata"]["jarvis_request_id"]
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
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn

    await session.request_response()
    old_marker = conn.response_create_payloads[0]["response"]["metadata"]["jarvis_request_id"]
    await session.interrupt()
    await session.request_response()
    new_marker = conn.response_create_payloads[1]["response"]["metadata"]["jarvis_request_id"]
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
        await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
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
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
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
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn

    await session.request_response()
    marker = conn.response_create_payloads[0]["response"]["metadata"]["jarvis_request_id"]
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
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn

    await session.send_text("First trusted update")
    first_marker = conn.response_create_payloads[0]["response"]["metadata"]["jarvis_request_id"]
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
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
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
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
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
    marker = conn.response_create_payloads[0]["response"]["metadata"]["jarvis_request_id"]
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
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
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


@pytest.mark.asyncio
async def test_response_cancel_not_active_error_is_recoverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-053/BUG-056: a cancel that loses the response-boundary race is a
    benign no-op — the response it wanted dead is already gone. The provider's
    ``response_cancel_not_active`` error must surface as RECOVERABLE so the
    session pump warns and continues instead of ending the call (live
    incidents 09:04 barge-in and 15:13 scrub-cancel, both 2026-07-14)."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn
    conn._events = iter(
        [
            SimpleNamespace(
                type="error",
                error=SimpleNamespace(
                    code="response_cancel_not_active",
                    message="Cancellation failed: no active response found",
                ),
            ),
        ]
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == ["error"]
    assert events[0].recoverable is True
    await session.close()


@pytest.mark.asyncio
async def test_unsolicited_response_after_heard_user_turn_is_adopted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the server dropped the manual-response contract and auto-answers a
    user turn it audibly heard (speech_started since our last response), that
    response is the ONLY answer the turn will ever get. Suppressing it left
    Jarvis silent until manual hang-up (live grok-realtime 2026-07-16 09:23).
    It must be adopted — audible, uncancelled — while the contract is still
    re-armed for the following turns."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn
    conn._events = iter(
        [
            SimpleNamespace(type="input_audio_buffer.speech_started"),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-auto", metadata=None),
            ),
            SimpleNamespace(
                type="response.output_audio.delta",
                response_id="resp-auto",
                item_id="item-auto",
                delta=base64.b64encode(b"\x03\x00").decode("ascii"),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-auto"),
            ),
        ]
    )
    session._events = conn.__aiter__()

    events = [event async for event in session.receive()]

    assert "audio_delta" in [event.type for event in events]
    assert conn.response_cancels == []
    assert len(conn.session_updates) == 2
    rearmed = conn.session_updates[-1]
    assert rearmed["audio"]["input"]["turn_detection"]["create_response"] is False
    await session.close()


@pytest.mark.asyncio
async def test_delayed_transcript_after_adoption_does_not_double_speak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the adopted auto-response's input transcript arrives DELAYED (not
    lost), the orchestrator requests its own response for it. Honoring that
    request would speak a second, independent answer to the same utterance —
    it must be skipped exactly once."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn
    conn._events = iter(
        [
            SimpleNamespace(type="input_audio_buffer.speech_started"),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-auto", metadata=None),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-auto"),
            ),
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.completed",
                item_id="item-late",
                transcript="The real question",
            ),
        ]
    )
    session._events = conn.__aiter__()

    [event async for event in session.receive()]

    await session.request_response()
    assert conn.response_creates == 0, (
        "the delayed transcript's response request must be skipped — the "
        "adopted auto-response already answered this turn"
    )
    await session.request_response()
    assert conn.response_creates == 1, "the skip must be one-shot"
    await session.close()


@pytest.mark.asyncio
async def test_second_unsolicited_response_without_new_speech_is_still_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adoption is a one-shot salvage per heard user turn: a further
    unsolicited response WITHOUT new speech evidence is a true stray and keeps
    the BUG-064 suppression (no double-speak)."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn
    conn._events = iter(
        [
            SimpleNamespace(type="input_audio_buffer.speech_started"),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-auto-1", metadata=None),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-auto-1"),
            ),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-auto-2", metadata=None),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-auto-2"),
            ),
        ]
    )
    session._events = conn.__aiter__()

    [event async for event in session.receive()]

    assert conn.response_cancels == ["resp-auto-2"]
    await session.close()


@pytest.mark.asyncio
async def test_unsolicited_response_rearms_the_full_session_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-064: an unsolicited response means the server dropped the
    manual-response contract (live grok-realtime 2026-07-16 08:07: after a
    barge-in cancel the session went permanently deaf — no input transcription
    events, server VAD auto-created responses). Suppression must re-send the
    FULL session payload so transcription and ``create_response: false`` are
    restored."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn
    conn._events = iter(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-auto", metadata=None),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-auto"),
            ),
        ]
    )
    session._events = conn.__aiter__()

    events = [event async for event in session.receive()]

    assert events == []
    assert conn.response_cancels == ["resp-auto"]
    assert len(conn.session_updates) == 2
    rearmed = conn.session_updates[-1]
    assert rearmed == conn.session_updates[0]
    assert rearmed["audio"]["input"]["transcription"]["model"]
    assert rearmed["audio"]["input"]["turn_detection"]["create_response"] is False
    await session.close()


@pytest.mark.asyncio
async def test_contract_rearm_is_throttled_within_a_burst(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-064: a burst of unsolicited responses re-arms the contract once per
    cooldown window; every response in the burst is still cancelled."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn
    conn._events = iter(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-auto-1", metadata=None),
            ),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-auto-2", metadata=None),
            ),
        ]
    )
    session._events = conn.__aiter__()

    events = [event async for event in session.receive()]

    assert events == []
    assert conn.response_cancels == ["resp-auto-1", "resp-auto-2"]
    assert len(conn.session_updates) == 2
    await session.close()


@pytest.mark.asyncio
async def test_contract_rearm_carries_live_instruction_and_tool_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-064: the re-armed payload must reflect the LATEST live session
    state — re-asserting the contract must never revert instructions or tool
    declarations to their session-start values."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(instructions="Session-start instructions.")
    )
    conn = holder["client"].realtime.last_conn
    declaration = {
        "name": "late_tool",
        "description": "A tool connected mid-session.",
        "parameters": {"type": "object", "properties": {}},
    }
    await session.update_session(instructions="Turn-updated instructions.", tools=(declaration,))
    conn._events = iter(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-auto", metadata=None),
            ),
        ]
    )
    session._events = conn.__aiter__()

    _ = [event async for event in session.receive()]

    rearmed = conn.session_updates[-1]
    assert rearmed["instructions"] == "Turn-updated instructions."
    assert rearmed["tools"] == [{"type": "function", **declaration}]
    assert rearmed["audio"]["input"]["turn_detection"]["create_response"] is False
    await session.close()


@pytest.mark.asyncio
async def test_interrupt_skips_wire_cancel_when_no_response_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-053 correction 2: when the adapter already knows no response
    lifecycle is active, ``interrupt()`` must not put ``response.cancel`` on
    the wire at all — that request can only ever produce the benign error."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn

    await session.interrupt()

    assert conn.response_cancels == []
    await session.close()


@pytest.mark.asyncio
async def test_interrupt_still_cancels_while_response_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The skip above must never eat a REAL cancellation: with a response
    lifecycle in flight, interrupt() still sends response.cancel."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn

    await session.request_response()
    await session.interrupt()

    assert conn.response_cancels == ["<active>"]
    await session.close()


@pytest.mark.asyncio
async def test_deaf_session_rebuilds_the_transport_and_receive_hops_onto_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-064 escalation (grok-realtime 2026-07-16 09:23): the contract
    re-arm demonstrably ran and the server STILL never delivered another
    input transcript — the call sat in LISTENING until manual hang-up. Once
    the transcript deadline for a heard-but-untranscribed user turn expires,
    the adapter must open a fresh transport carrying the same session
    contract, and the receive pump must hop onto it instead of treating the
    old transport's end as the end of the voice session."""
    holder = _patch_openai_client(monkeypatch)
    monkeypatch.setattr(openai_realtime, "_TRANSCRIPT_OVERDUE_S", 0.0)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(model="gpt-realtime")
    )
    api = holder["client"].realtime
    conn1 = api.last_conn
    conn2 = _FakeConn()
    conn2._events = iter(
        [
            SimpleNamespace(type="session.updated"),
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.completed",
                item_id="post-rebuild-1",
                transcript="Repeated question",
            ),
        ]
    )
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

    assert [event.type for event in events] == [
        "speech_started",
        "error",
        "input_transcript",
    ]
    assert events[1].recoverable is True
    assert "rebuilt" in str(events[1].error)
    assert events[2].text == "Repeated question"
    assert api.connect_calls == ["gpt-realtime", "gpt-realtime"]
    contract = conn2.session_updates[0]
    assert contract["audio"]["input"]["turn_detection"]["create_response"] is False
    assert contract["audio"]["input"]["transcription"]["model"]
    assert session._conn is conn2
    await session.close()


@pytest.mark.asyncio
async def test_committed_turn_arms_and_transcript_clears_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An input_audio_buffer commit means the contract owes a transcript; the
    arriving transcript proves the server hears and must disarm the rebuild
    deadline so healthy sessions never reconnect."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn
    conn._events = iter([SimpleNamespace(type="input_audio_buffer.committed", item_id="item-1")])
    session._events = conn.__aiter__()

    assert [event async for event in session.receive()] == []
    assert session._transcript_deadline is not None

    conn._events = iter(
        [
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.completed",
                item_id="item-1",
                transcript="One request",
            )
        ]
    )
    session._events = conn.__aiter__()
    events = [event async for event in session.receive()]

    assert [event.type for event in events] == ["input_transcript"]
    assert session._transcript_deadline is None
    assert holder["client"].realtime.connect_calls == ["gpt-realtime"]
    await session.close()


@pytest.mark.asyncio
async def test_suppressed_duplicate_right_after_a_transcript_does_not_arm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The benign duplicate race (openai-realtime 2026-07-15): our
    response.create crossed the server's auto response moments after the
    input transcript arrived. That suppression must NOT arm the transcript
    deadline — the session demonstrably hears."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn
    conn._events = iter(
        [
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.completed",
                item_id="item-1",
                transcript="Heard fine",
            ),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-duplicate", metadata=None),
            ),
        ]
    )
    session._events = conn.__aiter__()

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == ["input_transcript"]
    assert conn.response_cancels == ["resp-duplicate"]
    assert session._transcript_deadline is None
    await session.close()


@pytest.mark.asyncio
async def test_failed_transport_rebuild_closes_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rebuild that cannot reconnect must close the session so the
    orchestrator reports an honest provider error — never keep a silently
    deaf call open."""
    holder = _patch_openai_client(monkeypatch)
    monkeypatch.setattr(openai_realtime, "_TRANSCRIPT_OVERDUE_S", 0.0)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    api = holder["client"].realtime
    api.connect_error = RuntimeError("reconnect refused")
    conn1 = api.last_conn
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

    assert [event.type for event in events] == ["speech_started"]
    assert session._closed is True
    assert holder["client"].closed is True


@pytest.mark.asyncio
async def test_grok_generic_cancellation_failed_error_is_recoverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-064 recurrence #2 (grok-realtime 2026-07-16 10:23): xAI wraps the
    benign cancel-after-done race in the generic ``invalid_request_error``
    code instead of ``response_cancel_not_active``, so the code set alone
    cannot recognize it. The message shape must be matched — labeling this
    error terminal ended a healthy live call with hangup_reason=error."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn
    conn._events = iter(
        [
            SimpleNamespace(
                type="error",
                error=SimpleNamespace(
                    code="invalid_request_error",
                    message="Cancellation failed: no active response found",
                ),
            ),
        ]
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == ["error"]
    assert events[0].recoverable is True
    await session.close()


@pytest.mark.asyncio
async def test_second_stray_after_unheeded_rearm_rebuilds_the_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-064 recurrence #2 (grok-realtime 2026-07-16 10:23, session
    204b108a): the first stray auto-response arrived 1.9 s after the turn's
    transcript — inside the benign-race quiet window, so no transcript
    deadline was armed — and the deaf server then emitted nothing for 16 s,
    so the deadline path never got a second chance. A FURTHER unsolicited
    response after a contract re-arm that produced no transcript is proof the
    re-arm failed to restore hearing: the adapter must rebuild the transport
    immediately instead of re-arming forever."""
    holder = _patch_openai_client(monkeypatch)
    monkeypatch.setattr(openai_realtime, "_CONTRACT_REARM_COOLDOWN_S", 0.0)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(model="gpt-realtime")
    )
    api = holder["client"].realtime
    conn1 = api.last_conn
    conn2 = _FakeConn()
    conn2._events = iter(
        [
            SimpleNamespace(type="session.updated"),
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.completed",
                item_id="post-rebuild-1",
                transcript="Heard again",
            ),
        ]
    )
    api.extra_conns.append(conn2)
    conn1._events = iter(
        [
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.completed",
                item_id="item-1",
                transcript="Was?",
            ),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-stray-1", metadata=None),
            ),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-stray-2", metadata=None),
            ),
        ]
    )
    session._events = conn1.__aiter__()

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "input_transcript",
        "error",
        "input_transcript",
    ]
    assert events[1].recoverable is True
    assert "rebuilt" in str(events[1].error)
    assert events[2].text == "Heard again"
    assert conn1.response_cancels == ["resp-stray-1", "resp-stray-2"]
    assert api.connect_calls == ["gpt-realtime", "gpt-realtime"]
    assert session._conn is conn2
    await session.close()


@pytest.mark.asyncio
async def test_accepted_response_without_done_stalls_and_rebuilds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-064 recurrence #3 (grok-realtime 2026-07-16 10:51, session
    1fd3fa38): the server never sent ``response.done`` for an accepted
    response whose output a local barge-in had dropped, so ``_response_idle``
    stayed clear forever — and every idle-gated defense (adoption, transcript
    deadline, transport rebuild) was disarmed at once while the session sat
    silent until manual hang-up. A response lifecycle that emits no event at
    all for ``_RESPONSE_STALL_S`` must be declared dead and the transport
    rebuilt; the microphone pump is the guaranteed trigger because a fully
    silent server delivers no events to react to."""
    holder = _patch_openai_client(monkeypatch)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(model="gpt-realtime")
    )
    api = holder["client"].realtime
    conn1 = api.last_conn
    conn2 = _FakeConn()
    conn2._events = iter(
        [
            SimpleNamespace(type="session.updated"),
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.completed",
                item_id="post-rebuild-1",
                transcript="Heard again",
            ),
        ]
    )
    api.extra_conns.append(conn2)

    await session.request_response()
    conn1._events = iter(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-hung", metadata=None),
            ),
        ]
    )
    session._events = conn1.__aiter__()

    assert [event async for event in session.receive()] == []
    assert not session._response_idle.is_set()

    # The healthy 8 s threshold protected the accept flow above; from here
    # the stalled clock has run out.
    monkeypatch.setattr(openai_realtime, "_RESPONSE_STALL_S", 0.0)
    await session.send_audio(SimpleNamespace(sample_rate=24000, pcm=b"\x00\x01"))
    assert session._rebuild_task is not None
    await session._rebuild_task

    assert session._conn is conn2
    assert session._response_idle.is_set()
    assert api.connect_calls == ["gpt-realtime", "gpt-realtime"]

    events = [event async for event in session.receive()]
    assert [event.type for event in events] == ["input_transcript"]
    assert events[0].text == "Heard again"
    await session.close()


@pytest.mark.asyncio
async def test_failed_transcription_does_not_mark_rearm_as_heeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-064 recurrence #4 (grok-realtime 2026-07-16 11:23, session
    a69d2318): the deaf server emitted transcription FAILED events, which
    counted as "the re-arm restored hearing" — so the
    stray-after-unheeded-re-arm escalation never fired and the session
    re-armed forever until manual hang-up. A failed transcript settles the
    per-turn contract debt but proves nothing about the transcription side:
    only a COMPLETED transcript may mark a re-arm as heeded."""
    holder = _patch_openai_client(monkeypatch)
    monkeypatch.setattr(openai_realtime, "_CONTRACT_REARM_COOLDOWN_S", 0.0)
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(model="gpt-realtime")
    )
    api = holder["client"].realtime
    conn1 = api.last_conn
    conn2 = _FakeConn()
    conn2._events = iter(
        [
            SimpleNamespace(type="session.updated"),
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.completed",
                item_id="post-rebuild-1",
                transcript="Heard again",
            ),
        ]
    )
    api.extra_conns.append(conn2)
    conn1._events = iter(
        [
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.completed",
                item_id="item-1",
                transcript="How much money does",
            ),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-stray-1", metadata=None),
            ),
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.failed",
                item_id="item-2",
                error=SimpleNamespace(code="", message="transcription failed"),
            ),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-stray-2", metadata=None),
            ),
        ]
    )
    session._events = conn1.__aiter__()

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "input_transcript",
        "input_transcript",
        "error",
        "input_transcript",
    ]
    assert "rebuilt" in str(events[2].error)
    assert events[3].text == "Heard again"
    assert conn1.response_cancels == ["resp-stray-1", "resp-stray-2"]
    assert api.connect_calls == ["gpt-realtime", "gpt-realtime"]
    assert session._conn is conn2
    await session.close()


@pytest.mark.asyncio
async def test_unsolicited_stray_does_not_feed_the_stall_watchdog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-064 recurrence #4 (grok-realtime 2026-07-16 11:23): the wedged
    server auto-created stray responses every ~7.8 s — just under the 8 s
    stall threshold — and every stray stamped the response-liveness clock,
    keeping a dead in-flight lifecycle looking alive forever. Only events of
    an ACCEPTED response may feed the stall watchdog."""
    holder = _patch_openai_client(monkeypatch)
    monkeypatch.setattr(openai_realtime, "_RESPONSE_STALL_S", float("inf"))
    session = await OpenAIRealtimeProvider(api_key="test-key").open_session(RealtimeSessionConfig())
    conn = holder["client"].realtime.last_conn

    await session.request_response()
    conn._events = iter(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-live", metadata=None),
            ),
        ]
    )
    session._events = conn.__aiter__()
    assert [event async for event in session.receive()] == []
    assert not session._response_idle.is_set()

    sentinel = 123.0
    session._last_response_activity = sentinel
    conn._events = iter(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-stray", metadata=None),
            ),
            SimpleNamespace(
                type="response.output_audio.delta",
                response_id="resp-stray",
                item_id="item-stray",
                delta="AAA=",
            ),
        ]
    )
    session._events = conn.__aiter__()
    assert [event async for event in session.receive()] == []

    assert session._last_response_activity == sentinel
    assert session._rebuild_task is None
    assert conn.response_cancels == ["resp-stray"]
    await session.close()
