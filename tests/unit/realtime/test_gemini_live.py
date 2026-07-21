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

    receive_calls = 0

    async def fake_receive():
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
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
        "interrupted",
        "input_transcript",
        "turn_complete",
    ]
    assert events[0].audio.pcm == b"\x01\x02\x03\x04"
    assert events[0].audio.sample_rate == 24_000
    assert events[1].text == "hello there"
    assert events[3].text == "what the user said"
    assert receive_calls == 2


@pytest.mark.asyncio
async def test_go_away_is_recoverable_and_keeps_the_stream_flowing() -> None:
    """GoAway is Gemini's courteous pre-disconnect notice, not a wire error.
    Surfacing it as terminal used to end the session with reason=error while
    the current reply was still being spoken (live incident 2026-07-15 17:40),
    dropping the buffered audio tail."""
    messages = [
        _fake_message(go_away=SimpleNamespace(time_left=3_000)),
        _fake_message(
            server_content=SimpleNamespace(
                output_transcription=SimpleNamespace(text="still speaking"),
                input_transcription=None,
                interrupted=False,
                turn_complete=True,
            )
        ),
    ]

    receive_calls = 0

    async def fake_receive():
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            for message in messages:
                yield message

    fake_session = SimpleNamespace(receive=fake_receive)
    session = _GeminiLiveSession(
        session=fake_session,
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="s-go-away",
    )

    events = [event async for event in session.receive()]

    errors = [event for event in events if event.type == "error"]
    assert len(errors) == 1
    assert errors[0].recoverable is True
    assert "reconnect" in (errors[0].error or "")
    # The notice must not terminate the stream: the reply that follows it is
    # still delivered.
    assert [event.type for event in events][-2:] == [
        "output_transcript_delta",
        "turn_complete",
    ]


@pytest.mark.asyncio
async def test_abnormal_turn_complete_reason_is_logged(caplog) -> None:
    """Every named TurnCompleteReason except UNSPECIFIED is an abnormal stop
    (safety filter, rejection, regeneration limit). Discarding it made a
    server-truncated spoken reply indistinguishable from a complete one."""
    messages = [
        _fake_message(
            server_content=SimpleNamespace(
                output_transcription=SimpleNamespace(text="partial answer"),
                input_transcription=None,
                interrupted=False,
                turn_complete=True,
                turn_complete_reason=SimpleNamespace(
                    name="MAX_REGENERATION_REACHED"
                ),
            )
        ),
    ]

    receive_calls = 0

    async def fake_receive():
        nonlocal receive_calls
        receive_calls += 1
        if receive_calls == 1:
            for message in messages:
                yield message

    fake_session = SimpleNamespace(receive=fake_receive)
    session = _GeminiLiveSession(
        session=fake_session,
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="s-reason",
    )

    with caplog.at_level("WARNING"):
        events = [event async for event in session.receive()]

    assert [event.type for event in events][-1] == "turn_complete"
    assert any(
        "MAX_REGENERATION_REACHED" in record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def test_receive_reenters_sdk_iterator_for_a_second_user_turn() -> None:
    sdk_turns = [
        [
            _fake_message(
                server_content=SimpleNamespace(
                    output_transcription=SimpleNamespace(text="first answer"),
                    input_transcription=None,
                    interrupted=False,
                    turn_complete=True,
                )
            )
        ],
        [
            _fake_message(
                server_content=SimpleNamespace(
                    output_transcription=SimpleNamespace(text="second answer"),
                    input_transcription=None,
                    interrupted=False,
                    turn_complete=True,
                )
            )
        ],
    ]
    receive_calls = 0

    async def fake_receive():
        nonlocal receive_calls
        receive_calls += 1
        turn_index = receive_calls - 1
        if turn_index < len(sdk_turns):
            for message in sdk_turns[turn_index]:
                yield message

    session = _GeminiLiveSession(
        session=SimpleNamespace(receive=fake_receive),
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="two-turns",
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == [
        "output_transcript_delta",
        "turn_complete",
        "output_transcript_delta",
        "turn_complete",
    ]
    assert [event.text for event in events if event.text] == [
        "first answer",
        "second answer",
    ]
    assert receive_calls == 3


@pytest.mark.asyncio
async def test_text_update_uses_realtime_input_for_gemini_31() -> None:
    calls: list[dict[str, str]] = []

    async def send_realtime_input(**kwargs):
        calls.append(kwargs)

    session = _GeminiLiveSession(
        session=SimpleNamespace(send_realtime_input=send_realtime_input),
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="s-text",
    )

    await session.send_text("Deliver the completed mission update.")

    assert calls == [{"text": "Deliver the completed mission update."}]


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
        RealtimeSessionConfig(
            model=model,
            voice="Puck",
            silence_duration_ms=2_700,
        )
    )

    selected, config = holder["client"].aio.live.connect_calls[0]
    assert selected == model
    assert config.input_audio_transcription is not None
    assert config.output_audio_transcription is not None
    assert (
        config.realtime_input_config.automatic_activity_detection.silence_duration_ms
        == 2_700
    )
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Puck"
    await session.close()
    assert holder["client"].closed is True


@pytest.mark.asyncio
async def test_default_config_keeps_native_activity_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_genai_client(monkeypatch)
    provider = GeminiLiveProvider(api_key="test-key")

    session = await provider.open_session(RealtimeSessionConfig(voice="Puck"))
    _selected, config = holder["client"].aio.live.connect_calls[0]
    # No forced silence window: Gemini's native automatic activity detection
    # decides the turn end (the Settings "Thinking pause" is pipeline-only).
    assert config.realtime_input_config is None
    await session.close()


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
async def test_explicit_reply_language_uses_the_session_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_genai_client(monkeypatch)
    session = await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(
            language="es",
            language_is_pinned=True,
            instructions="Reply only in Spanish for this turn.",
        )
    )
    _model, config = holder["client"].aio.live.connect_calls[0]

    assert config.system_instruction == "Reply only in Spanish for this turn."
    assert config.speech_config is None
    # Gemini auto-responds; the required-tool hint remains a compatible no-op.
    await session.request_response(required_tool="jarvis_action")
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
    # Gemini fixes tool declarations at connection time; accepting a live
    # update remains a safe no-op until the next session reconnects.
    await provider_session.update_session(tools=(declaration,))

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
    sdk_turns = [
        [
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
            )
        ],
        [
            _fake_message(
                server_content=SimpleNamespace(
                    output_transcription=None,
                    input_transcription=None,
                    interrupted=False,
                    turn_complete=True,
                )
            )
        ],
    ]
    receive_calls = 0

    async def fake_receive():
        nonlocal receive_calls
        receive_calls += 1
        turn_index = receive_calls - 1
        if turn_index < len(sdk_turns):
            for message in sdk_turns[turn_index]:
                yield message

    session = _GeminiLiveSession(
        session=SimpleNamespace(receive=fake_receive),
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="tool-turn",
    )

    events = [event async for event in session.receive()]

    assert [event.type for event in events] == ["tool_call", "turn_complete"]
    assert receive_calls == 3


# --- BUG-088: conversation-history seeding into a fresh session -------------


class _SeedableConnectCM:
    """Connect CM whose session records send_client_content calls."""

    def __init__(self) -> None:
        self.exited = False
        self.client_content_calls: list[dict] = []

        async def _send_client_content(*, turns=None, turn_complete=True):
            self.client_content_calls.append(
                {"turns": turns, "turn_complete": turn_complete}
            )

        self.session = SimpleNamespace(send_client_content=_send_client_content)

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        self.exited = True


def _patch_seedable_genai_client(monkeypatch: pytest.MonkeyPatch) -> dict:
    holder: dict = {}

    class _SeedableLiveAPI:
        def __init__(self) -> None:
            self.last_cm: _SeedableConnectCM | None = None

        def connect(self, *, model, config):
            del model, config
            self.last_cm = _SeedableConnectCM()
            return self.last_cm

    def _make_client(*, api_key=None):
        client = SimpleNamespace(
            api_key=api_key,
            aio=SimpleNamespace(live=_SeedableLiveAPI()),
            closed=False,
        )
        holder["client"] = client
        return client

    from google import genai

    monkeypatch.setattr(genai, "Client", _make_client)
    return holder


@pytest.mark.asyncio
async def test_open_session_seeds_prior_call_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-088: a mid-call transport rebuild reopens Gemini with a fresh,
    empty conversation. The open must replay the bounded call transcript via
    send_client_content(turn_complete=False) — Gemini's initial-history
    channel — so follow-up questions keep their earlier-turn grounding."""
    holder = _patch_seedable_genai_client(monkeypatch)
    await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(
            history=(
                {"role": "user", "text": "let's talk programming languages"},
                {"role": "assistant", "text": "Sure — which one interests you?"},
                {"role": "user", "text": "what is the hardest language"},
            )
        )
    )

    live = holder["client"].aio.live
    calls = live.last_cm.client_content_calls
    assert len(calls) == 1
    assert calls[0]["turn_complete"] is False
    turns = calls[0]["turns"]
    assert [turn.role for turn in turns] == ["user", "model", "user"]
    assert [turn.parts[0].text for turn in turns] == [
        "let's talk programming languages",
        "Sure — which one interests you?",
        "what is the hardest language",
    ]


@pytest.mark.asyncio
async def test_open_session_without_history_sends_no_client_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder = _patch_seedable_genai_client(monkeypatch)
    await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig()
    )

    assert holder["client"].aio.live.last_cm.client_content_calls == []


@pytest.mark.asyncio
async def test_history_seeding_failure_keeps_the_session_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seeding fails open: an amnesiac session is exactly the pre-BUG-088
    behavior and strictly better than no session at all."""
    async def _broken_send_client_content(*, turns=None, turn_complete=True):
        del turns, turn_complete
        raise RuntimeError("seed rejected")

    class _BrokenSeedCM:
        async def __aenter__(self):
            return SimpleNamespace(
                send_client_content=_broken_send_client_content
            )

        async def __aexit__(self, *_args):
            return None

    def _make_client(*, api_key=None):
        return SimpleNamespace(
            api_key=api_key,
            aio=SimpleNamespace(
                live=SimpleNamespace(
                    connect=lambda *, model, config: _BrokenSeedCM()
                )
            ),
        )

    from google import genai

    monkeypatch.setattr(genai, "Client", _make_client)
    session = await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(history=({"role": "user", "text": "hello"},))
    )

    assert session is not None


@pytest.mark.asyncio
async def test_history_seed_construction_failure_keeps_the_session_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SDK type construction sits inside the fail-open boundary too: a
    Content/Part validation error must degrade to an amnesiac session,
    never fail the provider handshake."""
    holder = _patch_seedable_genai_client(monkeypatch)

    from google.genai import types

    def _explode(*_args, **_kwargs):
        raise ValueError("SDK validation tightened")

    monkeypatch.setattr(types, "Content", _explode)
    session = await GeminiLiveProvider(api_key="test-key").open_session(
        RealtimeSessionConfig(history=({"role": "user", "text": "hello"},))
    )

    assert session is not None
    assert holder["client"].aio.live.last_cm.client_content_calls == []


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
