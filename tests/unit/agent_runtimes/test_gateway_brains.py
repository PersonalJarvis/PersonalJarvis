"""How the model gateway runs a provider plugin for a Hermes / OpenClaw agent.

One brain (and HTTP client) per session, model and credential instead of one
per call; an agent request profile the voice path never sees: agent-scale
read timeouts, the runtime's own sampling choice, Anthropic prompt caching.
No provider is contacted: the plugins run against stand-in SDK clients.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.agent_runtimes import gateway
from jarvis.agent_runtimes.model_limits import ModelLimits
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest
from jarvis.plugins.brain import _agent_profile

_BODY = {"model": "m", "messages": [{"role": "user", "content": "Hi"}]}


class _Client:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _Brain:
    """A plugin stand-in that records the profile its call ran under."""

    built: list[_Brain] = []

    def __init__(self, provider: str, model: str) -> None:
        self._model = model
        self._client = _Client()
        self.profiles: list[Any] = []
        _Brain.built.append(self)

    async def complete(self, request: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.profiles.append(_agent_profile.current())
        yield BrainDelta(content="ok", finish_reason="stop")


@pytest.fixture
def plugins(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    import jarvis.agent_chat.runner_api as runner_api
    import jarvis.core.config as config

    credential = {"key": "sk-first"}
    _Brain.built = []
    monkeypatch.setattr(runner_api, "build_brain", _Brain)
    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda provider: None)
    monkeypatch.setattr(
        config,
        "resolve_provider_endpoint",
        lambda provider, **kwargs: SimpleNamespace(base_url=None, credential=credential["key"]),
    )
    gateway.reset()
    yield credential
    gateway.reset()


async def _call(grant: gateway.Grant, body: dict[str, Any] = _BODY) -> None:
    gateway._MODEL_LIMITS.setdefault(grant, {})["m"] = ModelLimits(200_000, 8_000)
    model, request = gateway.chat_request(body)
    stream = await gateway.open_chat_stream(
        grant, model, request, temperature_given=gateway.temperature_given(body)
    )
    async for _ in stream:
        pass


async def test_a_tool_loop_reuses_one_brain_and_its_client(plugins) -> None:
    grant = gateway.Grant("agent-1", "openai", scope="s1")
    for _ in range(3):
        await _call(grant)
    assert len(_Brain.built) == 1


async def test_a_changed_key_or_another_session_gets_its_own_brain(plugins) -> None:
    await _call(gateway.Grant("agent-1", "openai", scope="s1"))
    await _call(gateway.Grant("agent-1", "openai", scope="s2"))
    plugins["key"] = "sk-rotated"
    await _call(gateway.Grant("agent-1", "openai", scope="s1"))
    assert len(_Brain.built) == 3


async def test_reset_closes_the_idle_clients(plugins) -> None:
    await _call(gateway.Grant("agent-1", "openai"))
    client = _Brain.built[0]._client
    gateway.reset()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert client.closed


async def test_an_evicted_brain_in_use_is_closed_only_after_its_call(plugins) -> None:
    slot = gateway._acquire_brain(("in-use",), lambda: _Brain("openai", "m"))
    for index in range(gateway._BRAINS_MAX):
        gateway._release_brain(gateway._acquire_brain((str(index),),
                                                      lambda: _Brain("openai", "m")))
    await asyncio.sleep(0)
    assert slot.retired and not slot.brain._client.closed
    gateway._release_brain(slot)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert slot.brain._client.closed


@pytest.mark.parametrize(("provider", "read_timeout"), [("openai", 300.0), ("ollama", 900.0)])
async def test_an_agent_call_runs_under_the_agent_profile(plugins, provider, read_timeout) -> None:
    await _call(gateway.Grant("agent-1", provider))
    await _call(gateway.Grant("agent-1", provider), {**_BODY, "temperature": 0.2})
    first, second = _Brain.built[0].profiles
    assert first.read_timeout_s == read_timeout and first.prompt_cache
    assert first.omit_temperature and not second.omit_temperature
    # The profile never leaks out of the call.
    assert _agent_profile.current() is None


# ------------------------------------------------- the plugins under a profile


class _Recorder:
    """Captures the SDK call's keyword arguments and returns an empty stream."""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}


def _openai_client(recorder: _Recorder) -> Any:
    async def create(**kwargs: Any) -> Any:
        recorder.kwargs = kwargs

        async def chunks() -> AsyncIterator[Any]:
            delta = SimpleNamespace(content="ok", tool_calls=None)
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=delta, finish_reason="stop")], usage=None
            )

        return chunks()

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), base_url="stand-in"
    )


async def _openai_kwargs(profile: Any) -> dict[str, Any]:
    from jarvis.plugins.brain._openai_base import stream_complete

    recorder = _Recorder()
    token = _agent_profile.PROFILE.set(profile)
    try:
        request = BrainRequest(messages=(BrainMessage("user", "Hi"),), max_tokens=64)
        async for _ in stream_complete(_openai_client(recorder), "m", request):
            pass
    finally:
        _agent_profile.PROFILE.reset(token)
    return recorder.kwargs


async def test_the_voice_path_sends_what_it_always_sent() -> None:
    kwargs = await _openai_kwargs(None)
    assert kwargs["temperature"] == 0.7 and "timeout" not in kwargs


async def test_an_agent_call_gets_a_long_read_timeout_and_no_invented_temperature() -> None:
    profile = _agent_profile.AgentRequestProfile(read_timeout_s=300.0, omit_temperature=True)
    kwargs = await _openai_kwargs(profile)
    assert "temperature" not in kwargs
    assert kwargs["timeout"].read == 300.0 and kwargs["timeout"].connect == 5.0


class _AnthropicStream:
    def __init__(self, recorder: _Recorder, kwargs: dict[str, Any]) -> None:
        recorder.kwargs = kwargs

    async def __aenter__(self) -> _AnthropicStream:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[Any]:
        async def events() -> AsyncIterator[Any]:
            yield SimpleNamespace(
                type="message_delta",
                delta=SimpleNamespace(stop_reason="end_turn"),
                usage=SimpleNamespace(input_tokens=5, output_tokens=1),
            )

        return events()


async def _anthropic_kwargs(profile: Any, messages: tuple[BrainMessage, ...]) -> dict[str, Any]:
    from jarvis.plugins.brain._anthropic_base import stream_complete

    recorder = _Recorder()
    client = SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **kwargs: _AnthropicStream(recorder, kwargs))
    )
    request = BrainRequest(
        messages=messages,
        system="You are Probe.",
        tools=({"name": "read_file", "input_schema": {"type": "object"}},),
        max_tokens=64,
    )
    token = _agent_profile.PROFILE.set(profile)
    try:
        async for _ in stream_complete(client, "claude-3-5-haiku-latest", request):
            pass
    finally:
        _agent_profile.PROFILE.reset(token)
    return recorder.kwargs


async def test_an_agent_tool_loop_on_claude_is_prompt_cached(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_ANTHROPIC_PROMPT_CACHE", raising=False)  # the voice switch
    profile = _agent_profile.AgentRequestProfile(
        read_timeout_s=300.0, prompt_cache=True, omit_temperature=True
    )
    history = (
        BrainMessage("user", "Read a.txt"),
        BrainMessage("assistant", [{"type": "tool_use", "id": "t1", "name": "read_file",
                                    "input": {"path": "a.txt"}}]),
        BrainMessage("tool", "file body", tool_call_id="t1"),
    )
    kwargs = await _anthropic_kwargs(profile, history)
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    last = kwargs["messages"][-1]["content"][-1]
    assert last["type"] == "tool_result" and last["cache_control"] == {"type": "ephemeral"}
    # Earlier turns are untouched: one rolling breakpoint at the end.
    assert "cache_control" not in str(kwargs["messages"][:-1])
    assert "temperature" not in kwargs and kwargs["timeout"].read == 300.0


async def test_the_voice_path_to_claude_is_unchanged(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_ANTHROPIC_PROMPT_CACHE", raising=False)
    kwargs = await _anthropic_kwargs(None, (BrainMessage("user", "Hi"),))
    assert kwargs["system"] == "You are Probe."
    assert kwargs["messages"] == [{"role": "user", "content": "Hi"}]
    assert kwargs["temperature"] == 0.7 and "timeout" not in kwargs


def test_the_subscription_client_waits_for_long_thinking() -> None:
    gateway.reset()
    try:
        client = gateway._client("account-a")
        assert client._pool._timeout_s == gateway._HOSTED_READ_TIMEOUT_S
    finally:
        gateway.reset()
