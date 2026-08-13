"""Unit tests for the xAI Grok Voice Agent realtime adapter.

The adapter exists a second time. Its first incarnation was removed on
2026-07-16 because it was written as a manual-response OpenAI clone while the
xAI server answers spoken turns by itself (BUG-064). These tests pin the
distinction that removal cost: an automatic response is this provider's NORMAL
turn shape and must be adopted silently, while text turns and tool
continuations still belong to Jarvis.
"""

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


async def _open(monkeypatch: pytest.MonkeyPatch, **cfg: Any):
    _patch_client(monkeypatch)
    session = await GrokRealtimeProvider(api_key="xai-test").open_session(
        RealtimeSessionConfig(**cfg)
    )
    client = _FakeAsyncOpenAI.last
    assert client is not None
    return session, client.realtime.conn, client


async def _drain(session: Any) -> list[Any]:
    return [event async for event in session.receive()]


@pytest.mark.asyncio
async def test_open_session_uses_xai_endpoint_model_and_transcription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, conn, client = await _open(
        monkeypatch,
        model="grok-voice-think-fast-2.0",
        voice="leo",
        instructions="Be concise.",
        silence_duration_ms=2_100,
    )

    assert client.api_key == "xai-test"
    assert client.base_url == "https://api.x.ai/v1"
    assert client.realtime.models == ["grok-voice-think-fast-2.0"]
    payload = conn.session_updates[0]
    assert payload["audio"]["input"]["transcription"]["model"] == "grok-transcribe"
    assert payload["audio"]["input"]["turn_detection"]["silence_duration_ms"] == 2_100
    assert payload["audio"]["output"]["voice"] == "leo"
    assert payload["instructions"] == "Be concise."
    await session.close()


@pytest.mark.asyncio
async def test_session_payload_omits_the_manual_response_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """xAI echoes ``create_response: false`` and then answers anyway. Sending
    it would record a contract the server never keeps — the exact reading that
    made the 2026-07 adapter treat every normal turn as a fault."""
    session, conn, _client = await _open(monkeypatch)

    turn_detection = conn.session_updates[0]["audio"]["input"]["turn_detection"]
    assert "create_response" not in turn_detection
    assert "interrupt_response" not in turn_detection
    assert turn_detection["type"] == "server_vad"
    await session.close()


@pytest.mark.asyncio
async def test_session_declares_the_automatic_response_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _conn, _client = await _open(monkeypatch)

    assert session.creates_responses_automatically is True
    assert session.server_answers_speech_turns is True
    await session.close()


@pytest.mark.asyncio
async def test_default_model_and_audio_are_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, conn, client = await _open(monkeypatch)

    assert client.realtime.models == ["grok-voice-latest"]
    await session.send_audio(SimpleNamespace(pcm=b"\x01\x00", sample_rate=24_000))
    assert base64.b64decode(conn.appended_audio[0]) == b"\x01\x00"
    await session.close()


@pytest.mark.asyncio
async def test_server_created_response_is_adopted_without_cancel_or_rearm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The heart of the restoration. On a spoken turn the server creates the
    response itself; Jarvis must let it speak. Cancelling it would discard the
    only answer the turn ever gets (the 2026-07-16 silence), and re-arming the
    session contract would spend a wire call per turn while arming the
    unheeded-re-arm rebuild against a perfectly healthy session."""
    session, conn, _client = await _open(monkeypatch)
    # The ORDER is the measured one (2026-08-13): xAI transcribes first, then
    # creates its response, and only THEN seals the input buffer. An order
    # with the commit before the response would pass even while the adapter
    # is broken, because the commit re-supplies the heard-user evidence the
    # final transcript had just retracted.
    conn._events = iter(
        [
            SimpleNamespace(type="input_audio_buffer.speech_started"),
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.completed",
                item_id="user-1",
                transcript="Wie spaet ist es?",  # noqa: RUF001
            ),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-auto", metadata=None),
            ),
            SimpleNamespace(type="input_audio_buffer.committed"),
            SimpleNamespace(
                type="response.output_audio.delta",
                response_id="resp-auto",
                delta=base64.b64encode(b"\x02\x00").decode(),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-auto", status="completed"),
            ),
        ]
    )

    events = await _drain(session)

    assert conn.response_cancels == []
    # Only the ONE payload from open(); no contract re-arm was sent.
    assert len(conn.session_updates) == 1
    kinds = [event.type for event in events]
    assert "audio_delta" in kinds
    assert "turn_complete" in kinds
    audio = next(event for event in events if event.type == "audio_delta")
    assert audio.audio.pcm == b"\x02\x00"


@pytest.mark.asyncio
async def test_request_response_after_an_adopted_answer_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The orchestrator still calls request_response() at the turn boundary.
    Creating a second response there would speak two answers to one sentence."""
    session, conn, _client = await _open(monkeypatch)
    conn._events = iter(
        [
            SimpleNamespace(type="input_audio_buffer.speech_started"),
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.completed",
                item_id="user-1",
                transcript="Wie spaet ist es?",  # noqa: RUF001
            ),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-auto", metadata=None),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-auto", status="completed"),
            ),
        ]
    )
    await _drain(session)

    await session.request_response()

    assert conn.response_creates == []


@pytest.mark.asyncio
async def test_text_turns_still_request_their_own_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measured 2026-08-13: after a text item xAI waits for us. A text turn
    that never asks is a turn the user never hears."""
    session, conn, _client = await _open(monkeypatch)

    await session.send_text("Der Kalender ist geoeffnet.")  # noqa: RUF001 i18n-allow

    assert conn.created_items[0]["content"][0]["type"] == "input_text"
    assert len(conn.response_creates) == 1
    await session.close()


@pytest.mark.asyncio
async def test_tool_result_continues_the_turn_with_an_explicit_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same measurement for the tool path: xAI does not answer a
    function_call_output on its own."""
    session, conn, _client = await _open(monkeypatch)
    conn._events = iter(
        [
            SimpleNamespace(type="input_audio_buffer.speech_started"),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-auto", metadata=None),
            ),
            SimpleNamespace(
                type="response.function_call_arguments.done",
                response_id="resp-auto",
                call_id="call-1",
                name="get_weather",
                arguments='{"city": "Berlin"}',
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-auto", status="completed"),
            ),
        ]
    )
    events = await _drain(session)

    tool_call = next(event for event in events if event.type == "tool_call")
    assert tool_call.tool_name == "get_weather"
    assert tool_call.tool_args == {"city": "Berlin"}
    # A tool call is not a finished turn.
    assert "turn_complete" not in [event.type for event in events]

    await session.send_tool_result("call-1", "get_weather", {"temp_c": 21})

    assert conn.created_items[-1]["type"] == "function_call_output"
    assert len(conn.response_creates) == 1


@pytest.mark.asyncio
async def test_missing_key_is_refused_before_any_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch)
    provider = GrokRealtimeProvider(api_key="   ")

    assert await provider.can_open_duplex_session() is False
    with pytest.raises(RuntimeError, match="not configured"):
        await provider.open_session(RealtimeSessionConfig())
    assert _FakeAsyncOpenAI.last is None


@pytest.mark.asyncio
async def test_server_answer_is_adopted_even_while_a_lifecycle_hangs_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live 2026-08-13 18:53 silence, pinned.

    A text/tool response was created and then produced nothing. With that
    lifecycle still open, ``_response_idle`` stays clear — and the adoption
    gate used to require it, so every server answer that followed was
    cancelled as a stray: three of them (18:53:40, :41, :44) before the 8 s
    stall rebuilt the transport and the rest of the call went text-only.

    For a provider that answers spoken turns itself there is no contract to
    break, so its answer must win over the stuck lifecycle, not be cancelled
    by it.
    """
    session, conn, _client = await _open(monkeypatch)

    # Jarvis opens a lifecycle of its own (the tool/text path xAI does wait
    # for) — the server acknowledges it and then never produces output.
    await session.send_text("Ich schaue kurz nach.")
    assert len(conn.response_creates) == 1
    conn._events = iter(
        [
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-ours", metadata=None),
            ),
            # The user speaks into that silence; xAI answers on its own.
            SimpleNamespace(type="input_audio_buffer.speech_started"),
            SimpleNamespace(
                type="conversation.item.input_audio_transcription.completed",
                item_id="user-1",
                transcript="Und was ist mit dem Rest?",  # i18n-allow: spoken
            ),
            SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(id="resp-auto", metadata=None),
            ),
            SimpleNamespace(
                type="response.output_audio.delta",
                response_id="resp-auto",
                delta=base64.b64encode(b"\x07\x00").decode(),
            ),
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(id="resp-auto", status="completed"),
            ),
        ]
    )

    events = await _drain(session)

    assert conn.response_cancels == [], (
        "the server's own answer was cancelled as a stray — this is the "
        "2026-08-13 silence"
    )
    # No contract re-arm either: only the payload from open().
    assert len(conn.session_updates) == 1
    audio = [event for event in events if event.type == "audio_delta"]
    assert audio, "the adopted answer never reached the user"
    assert audio[0].audio.pcm == b"\x07\x00"
