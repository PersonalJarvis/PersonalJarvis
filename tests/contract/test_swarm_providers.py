"""Transport containment and client lifecycle across supported provider families."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import format_datetime
from importlib import import_module
from types import SimpleNamespace

import pytest

from jarvis.brain.swarm_factory import SwarmBrainFactory, SwarmProvider, _retry_after
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest
from jarvis.plugins.brain._anthropic_base import stream_complete
from jarvis.plugins.brain._openai_base import _stream_via_responses, _to_openai_messages
from jarvis.plugins.brain._openai_base import stream_complete as openai_complete

API_PROVIDERS = [
    ("claude_api", "ClaudeAPIBrain"),
    ("openai", "OpenAIBrain"),
    ("gemini", "GeminiBrain"),
    ("vertex", "VertexBrain"),
    ("grok", "GrokBrain"),
    ("openrouter", "OpenRouterBrain"),
    ("nvidia", "NvidiaBrain"),
    ("ollama", "OllamaBrain"),
    ("local_openai", "LocalOpenAIBrain"),
]
FAMILIES = ["claude-api", "openai", "gemini", "vertex", "grok", "openrouter", "nvidia"]


@pytest.mark.parametrize("module,name", API_PROVIDERS)
def test_api_adapters_explicitly_guarantee_no_ambient_execution(module, name):
    cls = getattr(import_module(f"jarvis.plugins.brain.{module}"), name)
    assert cls.scoped_execution_only is True


@pytest.mark.parametrize(
    "module,name",
    [
        ("codex", "CodexBrain"),
        ("antigravity", "AntigravityBrain"),
        ("claude_cli", "ClaudeCliBrain"),
    ],
)
def test_subscription_agent_transports_are_not_containment_capabilities(module, name):
    cls = getattr(import_module(f"jarvis.plugins.brain.{module}"), name)
    assert getattr(cls, "scoped_execution_only", False) is not True


class AsyncClient:
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


class GeminiClient:
    def __init__(self):
        self.closed = 0
        self.aio = AsyncClient()

    def close(self):
        self.closed += 1


class Registry:
    def __init__(self, providers):
        self.providers = providers

    def available(self):
        return list(self.providers)

    def get_class(self, name):
        return self.providers[name]


def fake_provider(*, ready=True, clients=None):
    class APIBrain:
        scoped_execution_only = True
        supports_tools = True

        def __init__(self, model=None):
            self._model = model or "test-model"
            self._client = None

        def _ensure_client(self):
            self._client = AsyncClient()
            if clients is not None:
                clients.append(self._client)
            if not ready:
                raise RuntimeError("credential unavailable")
            return self._client

        async def complete(self, request):
            yield BrainDelta(content="Provider works", finish_reason="stop")

    return APIBrain


@pytest.mark.asyncio
async def test_unsafe_adapter_rejected_before_constructor_even_when_it_supports_tools():
    class AmbientBrain:
        supports_tools = True

        def __init__(self, model=None):
            pytest.fail("An unsafe transport must never be instantiated")

    factory = SwarmBrainFactory(JarvisConfig())
    factory.registry = Registry({"ambient": AmbientBrain})
    with pytest.raises(RuntimeError, match="guarantee scoped execution"):
        await factory._candidate("ambient", None, prepare=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("only_key", FAMILIES)
async def test_any_single_api_family_survives_other_missing_credentials(only_key):
    factory = SwarmBrainFactory(JarvisConfig())
    clients = {name: [] for name in FAMILIES}
    factory.registry = Registry(
        {name: fake_provider(ready=name == only_key, clients=clients[name]) for name in FAMILIES}
    )
    provider = await factory.create()
    assert provider.provider == only_key
    deltas = [delta async for delta in provider.brain.complete(BrainRequest(messages=()))]
    assert deltas[-1].content == "Provider works"
    await provider.aclose()
    assert all(client.closed == 1 for values in clients.values() for client in values)


@pytest.mark.asyncio
async def test_failed_provider_falls_back_to_another_family_and_does_not_reuse_failed_client():
    factory = SwarmBrainFactory(JarvisConfig())
    clients = []
    factory.registry = Registry(
        {
            "claude-api": fake_provider(clients=clients),
            "gemini": fake_provider(clients=clients),
        }
    )
    first = await factory.create()
    factory.failed(first.provider)
    second = await factory.create()
    assert {first.provider, second.provider} == {"claude-api", "gemini"}
    assert first.brain is not second.brain
    await first.aclose()
    await second.aclose()
    assert [client.closed for client in clients] == [1, 1]


@pytest.mark.asyncio
async def test_gemini_native_and_replacement_client_are_closed_exactly_once():
    native, replacement = GeminiClient(), AsyncClient()
    brain = SimpleNamespace(_client=native)
    provider = SwarmProvider(brain, "gemini", "test-model", Decimal("1"))
    provider.track_clients()
    brain._client = replacement
    await asyncio.gather(provider.aclose(), provider.aclose())
    await provider.aclose()
    assert native.closed == native.aio.closed == replacement.closed == 1


@pytest.mark.asyncio
async def test_one_cleanup_error_does_not_skip_the_other_gemini_pool():
    class FailingAsyncClient(AsyncClient):
        async def close(self):
            self.closed += 1
            raise RuntimeError("pool already disconnected")

    client = GeminiClient()
    client.aio = FailingAsyncClient()
    provider = SwarmProvider(SimpleNamespace(_client=client), "gemini", "test-model", None)
    await provider.aclose()
    assert client.closed == client.aio.closed == 1


@pytest.mark.asyncio
async def test_cancel_during_setup_waits_for_and_closes_both_client_pools():
    started, release = threading.Event(), threading.Event()
    clients = []

    class SlowBrain:
        scoped_execution_only = True
        supports_tools = True

        def __init__(self, model=None):
            self._model = "test-model"
            self._client = None

        def _ensure_client(self):
            started.set()
            assert release.wait(5), "Test setup was never released"
            self._client = GeminiClient()
            clients.append(self._client)

    factory = SwarmBrainFactory(JarvisConfig())
    factory.registry = Registry({"slow": SlowBrain})
    task = asyncio.create_task(factory._candidate("slow", None, prepare=True))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(clients) == 1
    assert clients[0].closed == clients[0].aio.closed == 1


@pytest.mark.asyncio
async def test_openai_compatible_thinking_metadata_survives_stream_and_replay():
    metadata = {"google": {"thought_signature": "original-signature"}}

    class ChatClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=self)

        async def create(self, **kwargs):
            async def chunks():
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                tool_calls=[
                                    SimpleNamespace(
                                        index=0,
                                        id="call-1",
                                        extra_content=metadata,
                                        function=SimpleNamespace(
                                            name="read", arguments='{"id":"a"}'
                                        ),
                                    )
                                ]
                            ),
                            finish_reason="tool_calls",
                        )
                    ]
                )

            return chunks()

    deltas = [
        delta
        async for delta in openai_complete(
            ChatClient(),
            "test-model",
            BrainRequest(messages=()),
        )
    ]
    call = next(delta.tool_call for delta in deltas if delta.tool_call)
    assert call["extra_content"] == metadata
    replay = _to_openai_messages(
        (
            BrainMessage(
                role="assistant",
                content=[
                    {"type": "tool_use", **call},
                ],
            ),
        ),
        system_extra=None,
        assistant_tool_call_extra_content={"google": {"thought_signature": "fallback"}},
    )
    assert replay[0]["tool_calls"][0]["extra_content"] == metadata


class AnthropicStream:
    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def __aiter__(self):
        for event in self.events:
            yield event


def anthropic_client(*events):
    return SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **kwargs: AnthropicStream(events))
    )


@pytest.mark.asyncio
async def test_anthropic_start_and_delta_merge_input_cache_creation_and_output():
    client = anthropic_client(
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=30,
                    output_tokens=1,
                    cache_read_input_tokens=40,
                    cache_creation_input_tokens=20,
                )
            ),
        ),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=12),
        ),
    )
    deltas = [
        delta async for delta in stream_complete(client, "test-model", BrainRequest(messages=()))
    ]
    assert deltas[-1].usage == {"input_tokens": 50, "output_tokens": 12, "cache_hit_tokens": 40}
    assert deltas[-1].finish_reason == "end_turn"


@pytest.mark.asyncio
async def test_anthropic_missing_start_does_not_invent_zero_input_usage():
    client = anthropic_client(
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=12),
        )
    )
    deltas = [
        delta async for delta in stream_complete(client, "test-model", BrainRequest(messages=()))
    ]
    assert deltas[-1].usage == {"output_tokens": 12}


class Clock:
    def __init__(self):
        self.now = 100.0
        self.waits = []

    def monotonic(self):
        return self.now

    async def sleep(self, delay):
        self.waits.append(delay)
        self.now += delay
        await asyncio.sleep(0)


class HTTPFailure(Exception):
    def __init__(self, status=429, headers=None, response_json=None):
        self.code = status
        self.response = SimpleNamespace(headers=headers or {})
        self.response_json = response_json


@pytest.mark.parametrize(
    "failure,expected",
    [
        (HTTPFailure(headers={"retry-after": "17.25"}), 17.25),
        (
            HTTPFailure(
                headers={
                    "retry-after": format_datetime(datetime.fromtimestamp(120, UTC), usegmt=True)
                }
            ),
            20.0,
        ),
        (HTTPFailure(response_json={"error": {"details": [{"retryDelay": "17s"}]}}), 17.0),
        (
            HTTPFailure(
                response_json={
                    "message": '{"error":{"message":"Please retry in 17.94s.",'
                    '"details":[{"retryDelay":"17s"}]}}'
                }
            ),
            17.94,
        ),
        (HTTPFailure(headers={"retry-after": "NaN"}), None),
        (HTTPFailure(headers={"retry-after": "-1"}), None),
        (HTTPFailure(response_json={"error": {"details": [{"retryDelay": "Infinity"}]}}), None),
    ],
)
def test_retry_after_parses_supported_protocol_hints_and_rejects_invalid_values(failure, expected):
    assert _retry_after(failure, 100.0) == expected


def gated_factory(clock, providers):
    cfg = JarvisConfig()
    cfg.brain.primary = "gemini"
    factory = SwarmBrainFactory(
        cfg,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        jitter=lambda low, high: low,
    )
    factory.registry = Registry(providers)
    return factory


@pytest.mark.asyncio
async def test_sole_key_provider_waits_instead_of_falling_back_to_unconfigured_local_runtime():
    clock = Clock()

    class UnconfiguredLocal:
        scoped_execution_only = True

        def __init__(self, **kwargs):
            pytest.fail("A cloud-key installation did not configure a local runtime")

    factory = gated_factory(clock, {"gemini": fake_provider(), "ollama": UnconfiguredLocal})
    first = await factory.create()
    recovered = await factory.recover(first, HTTPFailure(headers={"retry-after": "17"}))
    assert recovered is first
    # A second team can select the same family while it cools; neither team
    # submits another request before the shared gate releases it.
    second = await factory.create()
    admissions = []

    async def admit(provider):
        await factory.await_ready(provider.provider)
        admissions.append(clock.now)

    await asyncio.gather(admit(first), admit(second))
    assert admissions == pytest.approx([117.1, 117.6])
    await first.aclose()
    await second.aclose()


@pytest.mark.asyncio
async def test_configured_keyless_provider_remains_eligible():
    clock = Clock()
    factory = gated_factory(clock, {"ollama": fake_provider()})
    factory.cfg.brain.providers["ollama"] = BrainProviderConfig(model="configured-model")
    provider = await factory.create()
    assert provider.provider == "ollama"
    assert provider.model == "configured-model"
    await provider.aclose()


@pytest.mark.asyncio
async def test_backpressure_uses_ready_cross_family_fallback_without_waiting():
    clock = Clock()
    factory = gated_factory(clock, {"gemini": fake_provider(), "openai": fake_provider()})
    first = await factory.create()
    second = await factory.recover(first, HTTPFailure(headers={"retry-after": "50"}))
    assert second.provider == "openai"
    assert clock.waits == []
    assert factory._cooldowns["gemini"] >= 150
    await first.aclose()
    await second.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403, 404])
async def test_sole_provider_auth_failure_requires_input_without_timed_retry(status):
    clock = Clock()
    factory = gated_factory(clock, {"gemini": fake_provider()})
    provider = await factory.create()
    assert await factory.recover(provider, HTTPFailure(status=status)) is None
    assert clock.waits == []
    await provider.aclose()


@pytest.mark.asyncio
async def test_new_backpressure_extends_existing_shared_wait():
    clock = Clock()
    factory = gated_factory(clock, {"gemini": fake_provider()})
    factory._cooldowns["gemini"] = 110
    waits = []

    async def sleep(delay):
        waits.append(delay)
        clock.now += delay
        if len(waits) == 1:
            factory._cooldowns["gemini"] = 120

    factory._sleep = sleep
    await factory.await_ready("gemini")
    assert waits == [10, 10]
    assert clock.now == 120


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["chat", "responses"])
@pytest.mark.parametrize(
    "supplied,expected",
    [
        ({"output": 12}, {"output_tokens": 12}),
        ({"input": 40}, {"input_tokens": 40}),
        ({"input": None, "output": 0}, {"output_tokens": 0}),
        ({"input": True, "output": 2}, {"output_tokens": 2}),
        ({"input": -1, "output": 2}, {"output_tokens": 2}),
        ({"input": 40, "output": 0}, {"input_tokens": 40, "output_tokens": 0}),
    ],
)
async def test_openai_surfaces_preserve_unknown_usage_fields(surface, supplied, expected):
    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=self)
            self.responses = self

        async def create(self, **kwargs):
            async def events():
                if surface == "chat":
                    names = {"input": "prompt_tokens", "output": "completion_tokens"}
                    yield SimpleNamespace(
                        choices=[],
                        usage=SimpleNamespace(
                            **{names[name]: value for name, value in supplied.items()}
                        ),
                    )
                else:
                    names = {"input": "input_tokens", "output": "output_tokens"}
                    yield SimpleNamespace(
                        type="response.completed",
                        response=SimpleNamespace(
                            usage=SimpleNamespace(
                                **{names[name]: value for name, value in supplied.items()}
                            ),
                        ),
                    )

            return events()

    stream = (
        openai_complete(Client(), "test-model", BrainRequest(messages=()))
        if surface == "chat"
        else _stream_via_responses(Client(), {"model": "test-model", "messages": []}, {})
    )
    deltas = [delta async for delta in stream]
    assert next(delta.usage for delta in deltas if delta.usage is not None) == expected


def test_isolated_clients_disable_hidden_http_retries_before_requests():
    class RetryController:
        stop = None

        def __call__(self):
            return None

    sync_retry, async_retry = RetryController(), RetryController()
    api = SimpleNamespace(_retry=sync_retry, _async_retry=async_retry)
    client = SimpleNamespace(max_retries=2, _api_client=api)
    provider = SwarmProvider(SimpleNamespace(_client=client), "gemini", "model", None)
    provider.own_request_retries()
    assert client.max_retries == 0
    assert sync_retry.stop(SimpleNamespace(attempt_number=1)) is True
    assert async_retry.stop(SimpleNamespace(attempt_number=1)) is True


@pytest.mark.asyncio
async def test_cleanup_failure_logs_no_sdk_body_and_continues_other_surfaces(caplog):
    caplog.set_level(logging.DEBUG)

    class BrokenClose:
        def __init__(self):
            self.aio = AsyncClient()

        def close(self):
            raise RuntimeError("private-provider-cleanup-body")

    client = BrokenClose()
    provider = SwarmProvider(SimpleNamespace(_client=client), "test-api", "model", None)
    await provider.aclose()
    assert client.aio.closed == 1
    assert "private-provider-cleanup-body" not in caplog.text
    assert "client cleanup failed" in caplog.text


@pytest.mark.asyncio
async def test_gemini_cache_fallback_logs_no_provider_response_body(caplog):
    from jarvis.plugins.brain.gemini import GeminiBrain

    caplog.set_level(logging.DEBUG)

    class RefusingCaches:
        async def create(self, **kwargs):
            raise RuntimeError("private-provider-cache-error-body")

    provider = GeminiBrain(model="gemini-3.5-flash")
    provider._client = SimpleNamespace(aio=SimpleNamespace(caches=RefusingCaches()))
    assert await provider._ensure_cache("x" * 20_000, None) is None
    assert "private-provider-cache-error-body" not in caplog.text
    assert "Gemini cache create failed" in caplog.text


@pytest.mark.asyncio
async def test_openai_sdk_adaptation_logs_no_error_body(caplog):
    caplog.set_level(logging.DEBUG)

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=self)
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TypeError("Unsupported stream_options: private-provider-sdk-error-body")

            async def events():
                return
                yield  # pragma: no cover - model an empty successful stream

            return events()

    client = Client()
    assert [
        delta async for delta in openai_complete(client, "test-model", BrainRequest(messages=()))
    ] == []
    assert client.calls == 2
    assert "private-provider-sdk-error-body" not in caplog.text
    assert "openai SDK rejected" in caplog.text
