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
async def test_explicit_reply_language_configures_gemini_speech(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_genai_client(monkeypatch)
    session = await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(language="es", language_is_pinned=True)
    )
    _model, config = holder["client"].aio.live.connect_calls[0]

    assert config.speech_config.language_code == "es"
    await session.request_response()  # Gemini auto-responds; the method is a no-op.
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


@pytest.mark.asyncio
async def test_tool_call_suppresses_intermediate_turn_complete() -> None:
    messages = [
        _fake_message(
            tool_call=SimpleNamespace(
                function_calls=[
                    SimpleNamespace(id="call-1", name="open_app", args={})
                ]
            ),
            server_content=SimpleNamespace(
                output_transcription=None,
                input_transcription=None,
                interrupted=False,
                turn_complete=True,
            ),
        ),
        _fake_message(
            server_content=SimpleNamespace(
                output_transcription=None,
                input_transcription=None,
                interrupted=False,
                turn_complete=True,
            )
        ),
    ]

    async def fake_receive():
        for message in messages:
            yield message

    session = _GeminiLiveSession(
        session=SimpleNamespace(receive=fake_receive),
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="tool-turn",
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == ["tool_call", "turn_complete"]


# --- function_declarations schema sanitizing --------------------------------


def _sanitize(schema):
    from jarvis.plugins.realtime.gemini_live import _sanitize_schema_for_gemini

    return _sanitize_schema_for_gemini(schema)


def test_sanitizer_strips_additional_properties_recursively() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "nested": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"leaf": {"type": "string"}},
            },
            "listed": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": False},
            },
        },
    }

    result = _sanitize(schema)

    assert "additionalProperties" not in result
    assert "additionalProperties" not in result["properties"]["nested"]
    assert "additionalProperties" not in result["properties"]["listed"]["items"]


def test_sanitizer_preserves_supported_keys() -> None:
    schema = {
        "type": "object",
        "description": "A tool input.",
        "properties": {
            "mode": {"type": "string", "enum": ["fast", "slow"], "default": "fast"},
            "count": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["mode"],
    }

    assert _sanitize(schema) == schema


def test_sanitizer_drops_ref_and_combinators_keeping_siblings() -> None:
    schema = {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {"x": {"type": "string"}},
        "oneOf": [{"type": "string"}],
        "properties": {
            "value": {"$ref": "#/$defs/x", "description": "kept sibling"}
        },
    }

    result = _sanitize(schema)

    assert set(result) == {"type", "properties"}
    assert result["properties"]["value"] == {"description": "kept sibling"}


def test_sanitizer_is_idempotent() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"name": {"type": "string", "format": "uri"}},
    }

    once = _sanitize(schema)

    assert _sanitize(once) == once


@pytest.mark.parametrize(
    "module_name, class_name",
    [
        ("jarvis.plugins.tool.describe_app_settings", "DescribeAppSettingsTool"),
        ("jarvis.plugins.tool.dispatch_with_review", "DispatchWithReviewTool"),
        ("jarvis.plugins.tool.manage_mcp_server", "ManageMcpServerTool"),
        ("jarvis.plugins.tool.reveal_key_preview", "RevealKeyPreviewTool"),
        ("jarvis.plugins.tool.switch_provider", "SwitchProviderTool"),
    ],
)
def test_real_router_tool_schemas_survive_sanitizing(
    module_name: str, class_name: str
) -> None:
    """The known additionalProperties carriers must come out Gemini-safe."""
    import importlib

    module = importlib.import_module(module_name)
    tool_cls = getattr(module, class_name)
    schema = getattr(tool_cls, "schema", None)
    if not isinstance(schema, dict):
        instance = tool_cls.__new__(tool_cls)
        schema = getattr(instance, "schema", None)
    assert isinstance(schema, dict), f"{class_name} exposes no dict schema"

    forbidden = {
        "additionalProperties",
        "$schema",
        "$defs",
        "definitions",
        "$ref",
        "oneOf",
        "anyOf",
        "allOf",
        "format",
        "pattern",
        "minLength",
        "maxLength",
    }

    def _assert_clean(node) -> None:
        if isinstance(node, dict):
            assert not (set(node) & forbidden), f"forbidden keys survive: {node}"
            for value in node.values():
                _assert_clean(value)
        elif isinstance(node, list):
            for value in node:
                _assert_clean(value)

    result = _sanitize(schema)

    _assert_clean(result)
    assert result.get("type") == schema.get("type")
    if "properties" in schema:
        assert set(result["properties"]) == set(schema["properties"])
