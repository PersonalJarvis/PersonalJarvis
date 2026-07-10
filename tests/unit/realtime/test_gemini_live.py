"""Unit tests for the Gemini Live realtime adapter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.brain.model_catalog import REALTIME_MODELS
from jarvis.plugins.realtime.gemini_live import GeminiLiveProvider, _GeminiLiveSession
from jarvis.realtime.protocol import RealtimeSessionConfig


def _fake_message(*, data=None, server_content=None, tool_call=None, go_away=None):
    return SimpleNamespace(
        data=data,
        server_content=server_content,
        tool_call=tool_call,
        go_away=go_away,
    )


@pytest.mark.asyncio
async def test_key_injection_controls_availability():
    assert await GeminiLiveProvider(api_key="test-key").can_open_duplex_session() is True
    assert await GeminiLiveProvider().can_open_duplex_session() is False


@pytest.mark.asyncio
async def test_receive_maps_audio_transcripts_interrupt_and_completion():
    messages = [
        _fake_message(data=b"\x01\x02\x03\x04"),
        _fake_message(
            server_content=SimpleNamespace(
                output_transcription=SimpleNamespace(text="hello there"),
                input_transcription=SimpleNamespace(text="what the user said"),
                interrupted=True,
                turn_complete=True,
            )
        ),
    ]

    async def fake_receive():
        for message in messages:
            yield message

    fake_session = SimpleNamespace(receive=fake_receive)
    session = _GeminiLiveSession(
        session=fake_session,
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="s1",
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "audio_delta",
        "output_transcript_delta",
        "input_transcript",
        "interrupted",
        "turn_complete",
    ]
    assert events[0].audio.pcm == b"\x01\x02\x03\x04"
    assert events[0].audio.sample_rate == 24_000
    assert events[1].text == "hello there"
    assert events[2].text == "what the user said"


class _FakeConnectCM:
    def __init__(self) -> None:
        self.exited = False

    async def __aenter__(self):
        return SimpleNamespace(name="fake-live-session")

    async def __aexit__(self, *_args):
        self.exited = True


class _FakeLiveAPI:
    def __init__(self) -> None:
        self.connect_calls: list[tuple[str, object]] = []
        self.last_cm: _FakeConnectCM | None = None

    def connect(self, *, model, config):
        self.connect_calls.append((model, config))
        self.last_cm = _FakeConnectCM()
        return self.last_cm


class _FakeAio:
    def __init__(self) -> None:
        self.live = _FakeLiveAPI()


class _FakeGenaiClient:
    def __init__(self, *, api_key=None) -> None:
        self.api_key = api_key
        self.aio = _FakeAio()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _patch_genai_client(monkeypatch: pytest.MonkeyPatch) -> dict:
    holder: dict = {}

    def _make_client(*, api_key=None):
        client = _FakeGenaiClient(api_key=api_key)
        holder["client"] = client
        return client

    from google import genai

    monkeypatch.setattr(genai, "Client", _make_client)
    return holder


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    [model.id for model in REALTIME_MODELS["gemini-live"]],
)
async def test_every_selectable_model_uses_live_audio_and_transcriptions(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    holder = _patch_genai_client(monkeypatch)
    provider = GeminiLiveProvider(api_key="test-key")

    session = await provider.open_session(
        RealtimeSessionConfig(model=model, voice="Puck")
    )

    selected, config = holder["client"].aio.live.connect_calls[0]
    assert selected == model
    assert config.input_audio_transcription is not None
    assert config.output_audio_transcription is not None
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Puck"
    await session.close()
    assert holder["client"].closed is True


@pytest.mark.asyncio
async def test_open_session_uses_current_default_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_genai_client(monkeypatch)
    session = await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(model="")
    )
    assert holder["client"].aio.live.connect_calls[0][0] == (
        "gemini-3.1-flash-live-preview"
    )
    await session.close()


@pytest.mark.asyncio
async def test_tools_are_declared_mapped_and_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_genai_client(monkeypatch)
    declaration = {
        "name": "open_app",
        "description": "Open an application.",
        "parameters": {
            "type": "object",
            "properties": {"app_name": {"type": "string"}},
        },
    }
    provider_session = await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(tools=(declaration,))
    )
    _model, config = holder["client"].aio.live.connect_calls[0]
    dumped = config.model_dump(exclude_none=True)
    assert dumped["tools"][0]["function_declarations"][0]["name"] == "open_app"

    class FakeLiveSession:
        def __init__(self):
            self.responses = []

        async def receive(self):
            yield _fake_message(
                tool_call=SimpleNamespace(
                    function_calls=[
                        SimpleNamespace(
                            id="call-1",
                            name="open_app",
                            args={"app_name": "Calculator"},
                        )
                    ]
                )
            )

        async def send_tool_response(self, *, function_responses):
            self.responses.extend(function_responses)

    live = FakeLiveSession()
    mapped = _GeminiLiveSession(
        session=live,
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="tool-session",
    )
    events = [event async for event in mapped.receive()]

    assert events[0].type == "tool_call"
    assert events[0].call_id == "call-1"
    assert events[0].tool_args == {"app_name": "Calculator"}

    await mapped.send_tool_result(
        "call-1",
        "open_app",
        {"success": True, "output": "opened", "error": None},
    )
    assert live.responses[0].id == "call-1"
    assert live.responses[0].name == "open_app"
    await provider_session.close()
