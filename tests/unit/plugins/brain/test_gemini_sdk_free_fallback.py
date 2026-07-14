"""Gemini remains usable when the native Google SDK stack is unavailable."""
from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import openai
import pytest

import jarvis.core.config as cfg
from jarvis.core.config import ResolvedEndpoint
from jarvis.core.protocols import BrainMessage, BrainRequest, ImageBlock
from jarvis.plugins.brain import gemini as gemini_mod
from jarvis.plugins.brain.gemini import GeminiBrain

_MISSING_NATIVE_MODULES = (
    "google.genai",
    "google.auth",
    "cryptography",
    "grpc",
)
_SECRET = "test-gemini-secret-never-log"  # noqa: S105 - inert test credential


def _chunk(
    *,
    content: str | None = None,
    tool_calls: list[Any] | None = None,
    finish_reason: str | None = None,
    usage: Any = None,
) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=content,
                    tool_calls=tool_calls or [],
                ),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
    )


def _tool_fragment(
    *,
    name: str | None = None,
    arguments: str = "",
    call_id: str | None = None,
) -> Any:
    return SimpleNamespace(
        index=0,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


ResponseFactory = Callable[[dict[str, Any]], list[Any]]


class _CompatHarness:
    def __init__(self, responses: list[ResponseFactory]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.client_kwargs: dict[str, Any] = {}


def _install_sdk_free_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    missing_module: str,
    responses: list[ResponseFactory],
    base_url: str | None = None,
) -> tuple[GeminiBrain, _CompatHarness]:
    harness = _CompatHarness(responses)

    def _native_import_failure(_endpoint: Any) -> Any:
        raise ModuleNotFoundError(
            f"No module named {missing_module!r}",
            name=missing_module,
        )

    class _FakeCompletions:
        async def create(self, **kwargs: Any) -> Any:
            harness.calls.append(kwargs)
            factory = harness.responses.pop(0)
            chunks = factory(kwargs)

            async def _stream() -> Any:
                for chunk in chunks:
                    yield chunk

            return _stream()

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            harness.client_kwargs = kwargs
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    monkeypatch.setattr(gemini_mod, "_create_native_client", _native_import_failure)
    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda _provider: ResolvedEndpoint(
            base_url=base_url,
            credential=_SECRET,
            via_proxy=bool(base_url),
        ),
    )

    return GeminiBrain(model="gemini-3-flash"), harness


def _stop_response(_kwargs: dict[str, Any]) -> list[Any]:
    return [_chunk(finish_reason="stop")]


@pytest.mark.parametrize("missing_module", _MISSING_NATIVE_MODULES)
@pytest.mark.asyncio
async def test_text_uses_official_compatibility_endpoint_without_native_stack(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    missing_module: str,
) -> None:
    brain, harness = _install_sdk_free_client(
        monkeypatch,
        missing_module=missing_module,
        responses=[lambda _kwargs: [_chunk(content="Portable"), _chunk(finish_reason="stop")]],
    )
    request = BrainRequest(
        messages=(BrainMessage(role="user", content="Reply briefly."),),
        system="Be accurate.",
    )

    deltas = [delta async for delta in brain.complete(request)]

    assert [delta.content for delta in deltas if delta.content] == ["Portable"]
    assert harness.client_kwargs["base_url"] == gemini_mod.OPENAI_COMPAT_BASE_URL
    assert harness.client_kwargs["api_key"] == _SECRET
    assert harness.calls[0]["messages"] == [
        {"role": "system", "content": "Be accurate."},
        {"role": "user", "content": "Reply briefly."},
    ]
    assert _SECRET not in caplog.text


@pytest.mark.parametrize("missing_module", _MISSING_NATIVE_MODULES)
@pytest.mark.asyncio
async def test_vision_payload_survives_without_native_stack(
    monkeypatch: pytest.MonkeyPatch,
    missing_module: str,
) -> None:
    brain, harness = _install_sdk_free_client(
        monkeypatch,
        missing_module=missing_module,
        responses=[_stop_response],
    )
    image = ImageBlock(mime="image/png", data_b64="AAAA", source_hash="hash")
    request = BrainRequest(
        messages=(
            BrainMessage(
                role="user",
                content="Describe this image.",
                images=(image,),
            ),
        ),
    )

    _ = [delta async for delta in brain.complete(request)]

    user_message = harness.calls[0]["messages"][0]
    assert user_message["role"] == "user"
    assert user_message["content"] == [
        {"type": "text", "text": "Describe this image."},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,AAAA"},
        },
    ]


@pytest.mark.parametrize("missing_module", _MISSING_NATIVE_MODULES)
@pytest.mark.asyncio
async def test_streaming_is_incremental_without_native_stack(
    monkeypatch: pytest.MonkeyPatch,
    missing_module: str,
) -> None:
    brain, _harness = _install_sdk_free_client(
        monkeypatch,
        missing_module=missing_module,
        responses=[
            lambda _kwargs: [
                _chunk(content="first "),
                _chunk(content="second"),
                _chunk(
                    finish_reason="stop",
                    usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
                ),
            ],
        ],
    )

    deltas = [
        delta
        async for delta in brain.complete(
            BrainRequest(messages=(BrainMessage(role="user", content="Stream."),))
        )
    ]

    assert [delta.content for delta in deltas if delta.content] == ["first ", "second"]
    usage = next(delta.usage for delta in deltas if delta.usage)
    assert usage == {"input_tokens": 3, "output_tokens": 2}


@pytest.mark.asyncio
async def test_lazy_native_import_failure_retries_before_first_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brain, _harness = _install_sdk_free_client(
        monkeypatch,
        missing_module="google.genai",
        responses=[lambda _kwargs: [_chunk(content="Recovered"), _chunk(finish_reason="stop")]],
    )

    class _LazyNativeClient:
        def __init__(self) -> None:
            self.aio = SimpleNamespace(models=self)

        async def generate_content_stream(self, **_kwargs: Any) -> Any:
            raise ModuleNotFoundError(
                "No module named 'google.auth'",
                name="google.auth",
            )

    brain._client = _LazyNativeClient()
    brain._transport = gemini_mod._TRANSPORT_NATIVE

    deltas = [
        delta
        async for delta in brain.complete(
            BrainRequest(messages=(BrainMessage(role="user", content="Recover."),))
        )
    ]

    assert [delta.content for delta in deltas if delta.content] == ["Recovered"]
    assert brain._transport == gemini_mod._TRANSPORT_OPENAI_COMPAT


@pytest.mark.parametrize("missing_module", _MISSING_NATIVE_MODULES)
@pytest.mark.asyncio
async def test_tool_loop_round_trips_names_and_gemini_signature_without_native_stack(
    monkeypatch: pytest.MonkeyPatch,
    missing_module: str,
) -> None:
    def _tool_response(kwargs: dict[str, Any]) -> list[Any]:
        safe_name = kwargs["tools"][0]["function"]["name"]
        return [
            _chunk(
                tool_calls=[
                    _tool_fragment(
                        name=safe_name,
                        arguments='{"query":',
                        call_id="call-1",
                    )
                ]
            ),
            _chunk(tool_calls=[_tool_fragment(arguments='"portable"}')]),
            _chunk(finish_reason="tool_calls"),
        ]

    brain, harness = _install_sdk_free_client(
        monkeypatch,
        missing_module=missing_module,
        responses=[
            _tool_response,
            lambda _kwargs: [
                _chunk(content="Done"),
                _chunk(finish_reason="stop"),
            ],
        ],
    )
    tools = (
        {
            "name": "github/search",
            "description": "Search a connected repository.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
                "input_examples": [{"query": "example"}],
            },
        },
    )
    user = BrainMessage(role="user", content="Search the repository.")

    first = [
        delta
        async for delta in brain.complete(BrainRequest(messages=(user,), tools=tools))
    ]
    tool_call = next(delta.tool_call for delta in first if delta.tool_call)

    assert tool_call == {
        "id": "call-1",
        "name": "github/search",
        "input": {"query": "portable"},
    }
    declaration = harness.calls[0]["tools"][0]["function"]
    assert declaration["name"] == "github_search"
    assert "additionalProperties" not in declaration["parameters"]
    assert "input_examples" not in declaration["parameters"]

    second_request = BrainRequest(
        messages=(
            user,
            BrainMessage(
                role="assistant",
                content=[
                    {
                        "type": "tool_use",
                        "id": tool_call["id"],
                        "name": tool_call["name"],
                        "input": tool_call["input"],
                    }
                ],
            ),
            BrainMessage(
                role="tool",
                content='{"items": []}',
                tool_call_id=tool_call["id"],
                name=tool_call["name"],
            ),
        ),
        tools=tools,
    )
    second = [delta async for delta in brain.complete(second_request)]

    assert [delta.content for delta in second if delta.content] == ["Done"]
    assistant_history = harness.calls[1]["messages"][1]
    assert assistant_history["tool_calls"][0]["function"]["name"] == "github_search"
    assert assistant_history["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "skip_thought_signature_validator"},
    }
    tool_history = harness.calls[1]["messages"][2]
    assert tool_history["tool_call_id"] == "call-1"


def test_team_proxy_fallback_uses_openai_subpath() -> None:
    assert gemini_mod._openai_compat_base_url("https://keys.example/p/gemini") == (
        "https://keys.example/p/gemini/openai/"
    )
