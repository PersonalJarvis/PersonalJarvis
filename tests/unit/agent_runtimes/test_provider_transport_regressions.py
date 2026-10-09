"""Provider boundaries exercised with SDKs and local, scripted transports."""

import json

import httpx
import pytest
from anthropic import AsyncAnthropic

from jarvis.agent_runtimes import gateway
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest
from jarvis.plugins.brain._anthropic_base import stream_complete
from tests.fakes.fake_anthropic_wire import AnthropicWire


@pytest.fixture
def isolated_gateway():
    gateway.reset()
    yield
    gateway.reset()


async def test_claude_tool_history_uses_the_declared_names_without_mutating_history():
    wire = AnthropicWire()
    blocks = [
        {"type": "tool_use", "id": "one", "name": "github/search", "input": {}},
        {"type": "tool_use", "id": "two", "name": "github_search", "input": {}},
    ]
    request = BrainRequest(messages=(
        BrainMessage("user", "Search"), BrainMessage("assistant", blocks),
        BrainMessage("tool", "first", tool_call_id="one"),
        BrainMessage("tool", "second", tool_call_id="two"),
    ), tools=({"name": "github/search"}, {"name": "github_search"}))
    async with AsyncAnthropic(api_key="test-only", http_client=wire.client()) as client:
        assert [d async for d in stream_complete(client, "claude-sonnet-4-6", request)]
    payload = json.loads(wire.requests[0].content)
    declared = [tool["name"] for tool in payload["tools"]]
    history = [block["name"] for block in payload["messages"][1]["content"]]
    assert history == declared
    assert blocks[0]["name"] == "github/search"


async def test_claude_agent_timeout_reaches_the_sdks_own_http_transport():
    from jarvis.plugins.brain import _agent_profile

    wire = AnthropicWire()
    token = _agent_profile.PROFILE.set(_agent_profile.AgentRequestProfile(read_timeout_s=301.0))
    try:
        async with AsyncAnthropic(api_key="test-only", http_client=wire.client()) as client:
            result = [delta async for delta in stream_complete(
                client, "claude-sonnet-4-6", BrainRequest(messages=(BrainMessage("user", "Hi"),)),
            )]
        assert any(delta.content == "OK" for delta in result)
        assert wire.requests[0].extensions["timeout"] == {
            "connect": 5.0, "read": 301.0, "write": 60.0, "pool": 30.0,
        }
    finally:
        _agent_profile.PROFILE.reset(token)


@pytest.mark.parametrize("intermediate_usage", [False, True])
async def test_claude_cache_preserves_oauth_header_and_stream_usage(
    monkeypatch, intermediate_usage,
):
    monkeypatch.setenv("JARVIS_ANTHROPIC_PROMPT_CACHE", "1")
    wire = AnthropicWire()
    wire.intermediate_usage = intermediate_usage
    async with AsyncAnthropic(api_key="", auth_token="test-only",  # noqa: S106 - local fake
        default_headers={"anthropic-beta": "oauth-2025-04-20"},
        http_client=wire.client(),
    ) as client:
        deltas = [d async for d in stream_complete(client, "claude-sonnet-4-6",
            BrainRequest(messages=(BrainMessage("user", "Hi"),), system="Be brief."))]
    assert set(wire.requests[0].headers["anthropic-beta"].split(",")) == {
        "oauth-2025-04-20", "extended-cache-ttl-2025-04-11",
    }
    usage = {key: sum((d.usage or {}).get(key, 0) for d in deltas) for key in (
        "input_tokens", "output_tokens", "cache_hit_tokens", "cache_write_tokens",
    )}
    assert usage == {"input_tokens": 100, "output_tokens": 2,
                     "cache_hit_tokens": 20, "cache_write_tokens": 5}


@pytest.mark.parametrize("provider", ["ollama", "claude-api", "openai", "gemini",
    "grok", "openrouter", "nvidia", "local-openai"])
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("partial", [False, True])
async def test_gateway_never_marks_unfinished_provider_streams_complete(
    monkeypatch, isolated_gateway, provider, streamed, partial,
):
    async def deltas(*args):
        if partial:
            yield BrainDelta(content="unfinished")
    monkeypatch.setattr(gateway, "_deltas", deltas)
    token = gateway.grant_token("agent", provider)
    signal = gateway.watch_failure(token)
    grant = gateway.verify(token)
    request = BrainRequest(messages=(BrainMessage("user", "Hello"),))
    if streamed and partial:
        stream = await gateway.open_chat_stream(grant, "model", request)
        chunks = b"".join([chunk async for chunk in stream])
        assert b'"error"' in chunks
        assert b"[DONE]" not in chunks
    else:
        with pytest.raises(gateway.GatewayError) as failed:
            if streamed:
                await gateway.open_chat_stream(grant, "model", request)
            else:
                await gateway.complete_chat(grant, "model", request)
        assert failed.value.code == "incomplete"
    assert signal.done()


async def test_subscription_pin_cannot_fall_back_to_api_key_after_login_expires(
    monkeypatch, isolated_gateway,
):
    from jarvis.agent_chat import runner_api
    from jarvis.agent_chat.catalog import SUBSCRIPTION_ACCOUNT
    from jarvis.agent_runtimes import model_map
    from jarvis.core import config

    monkeypatch.setattr(model_map, "claude_login_token", lambda: None)
    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda provider: "test-api-key")
    called = []
    monkeypatch.setattr(runner_api, "build_brain", lambda *args: called.append(args))
    grant = gateway.Grant("agent", "claude-api", SUBSCRIPTION_ACCOUNT)
    with pytest.raises(Exception, match="Claude Code login"):
        await anext(gateway._deltas(grant, "claude-sonnet-4-6", BrainRequest(messages=())))
    assert called == []


@pytest.mark.parametrize("arguments", ['{"path":', '[]', 'null'])
async def test_claude_never_emits_malformed_executable_tools(arguments):
    wire = AnthropicWire()
    wire.tool_arguments = arguments
    received = []
    async with AsyncAnthropic(api_key="test-only", http_client=wire.client()) as client:
        with pytest.raises(ValueError, match="tool arguments"):
            async for delta in stream_complete(client, "claude-sonnet-4-6",
                BrainRequest(messages=(BrainMessage("user", "Read"),))):
                received.append(delta)
    assert not any(d.tool_call for d in received)


def test_standard_tool_reply_restores_name_required_by_gemini():
    _, request = gateway.chat_request({"model": "gemini-test", "messages": [
        {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function",
            "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "contents"},
    ]})
    assert request.messages[1].name == "read_file"


@pytest.mark.parametrize("state", ["idle", "released", "failed", "cancelled"])
async def test_persistent_runtime_token_cannot_infer_outside_live_turn(
    monkeypatch, isolated_gateway, state,
):
    from jarvis.agent_chat import runner_api

    token = gateway.grant_token("agent", "openai", require_active_turn=True)
    if state != "idle":
        signal = gateway.watch_failure(token)
        if state == "released":
            gateway.unwatch_failure(token, signal)
        elif state == "failed":
            signal.set_result("provider failure")
        else:
            signal.cancel()
    calls = []
    monkeypatch.setattr(runner_api, "build_brain", lambda *args: calls.append(args))
    with pytest.raises(gateway.GatewayError, match="no active turn"):
        await anext(gateway._deltas(gateway.verify(token), "model", BrainRequest(messages=())))
    assert not calls


async def test_selected_effort_reaches_api_brain(monkeypatch, isolated_gateway):
    from jarvis.agent_chat import runner_api
    from jarvis.agent_runtimes import model_map
    from jarvis.core import config

    requests = []

    class Brain:
        async def complete(self, request):
            requests.append(request)
            yield BrainDelta(content="OK", finish_reason="stop")

    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda provider: None)
    monkeypatch.setattr(model_map, "login_route", lambda *args: (False, None))
    monkeypatch.setattr(runner_api, "build_brain", lambda *args: Brain())
    token = gateway.grant_token("agent", "ollama", require_active_turn=True)
    gateway.watch_failure(token, effort="high")
    assert [d async for d in gateway._deltas(gateway.verify(token), "model",
        BrainRequest(messages=(), reasoning_effort="low"))]
    assert requests[0].reasoning_effort == "high"


def test_openclaw_workshop_cannot_start_background_model_turns(tmp_path):
    from jarvis.agent_runtimes.openclaw import OpenClawRuntime
    from tests.unit.agent_runtimes.test_runtime_configs import _turn

    config = OpenClawRuntime().config_for(
        _turn(tmp_path), port=12345, token="test-only",  # noqa: S106 - local fake
    )
    assert config["skills"]["workshop"]["autonomous"]["mode"] == "off"


@pytest.mark.parametrize("arguments,finish", [('{"path":', "tool_calls"),
    ('[]', "tool_calls"), ('null', "tool_calls"), ('{"path":"a"}', "length")])
async def test_openai_compatible_streams_never_emit_broken_tool_calls(arguments, finish):
    from openai import AsyncOpenAI

    from jarvis.plugins.brain._openai_base import stream_complete as stream_openai
    from tests.fakes.fake_openai_tool_wire import OpenAIToolWire

    wire = OpenAIToolWire(arguments, finish)
    received = []
    async with AsyncOpenAI(api_key="test-only", http_client=httpx.AsyncClient(
        transport=httpx.MockTransport(wire.handle),
    )) as client:
        if finish == "length":
            received = [delta async for delta in stream_openai(client, "test", BrainRequest(
                messages=(BrainMessage("user", "Read"),),
            ))]
            assert any(delta.finish_reason == "length" for delta in received)
        else:
            with pytest.raises(ValueError, match="tool"):
                async for delta in stream_openai(client, "test", BrainRequest(messages=(
                    BrainMessage("user", "Read"),))):
                    received.append(delta)
    assert not any(delta.tool_call for delta in received)


async def test_ollama_native_calls_forward_resolved_endpoint_auth(monkeypatch, tmp_path):
    from jarvis.brain import ollama_profiles
    from jarvis.brain.model_catalog import ModelCatalog
    from jarvis.core import config
    from jarvis.plugins.brain.ollama import OllamaBrain

    seen = []
    models = [{"name": "test:latest", "size": 100}]
    root = "http://ollama.test/p/ollama"

    def respond(request):
        seen.append(request)
        assert request.headers.get("Authorization") == "Bearer test-proxy-token"
        if request.url.path.endswith("/api/tags"):
            return httpx.Response(200, json={"models": models})
        if request.url.path.endswith("/api/show"):
            return httpx.Response(200, json={"capabilities": ["completion", "tools"],
                "model_info": {"test.context_length": 8192}})
        return httpx.Response(200, json={"status": "success", "done": True})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(
        **{**kwargs, "transport": httpx.MockTransport(respond)}))
    monkeypatch.setattr(config, "resolve_provider_endpoint", lambda *args, **kwargs:
        config.ResolvedEndpoint(base_url=root, credential="test-proxy-token", via_proxy=True))
    monkeypatch.setattr(config, "get_provider_secret", lambda provider: None)
    brain = OllamaBrain()
    assert await brain._resolve_model(need_tools=True) == "test:latest"
    monkeypatch.setattr(brain, "_model_options", lambda model:
        config.OllamaModelOptions(num_ctx=4096, keep_alive="1m"))
    ollama_profiles.reset_process_memo()
    await brain._apply_model_options("test:latest", BrainRequest(messages=()))
    catalog = ModelCatalog(cache_path=tmp_path / "catalog.json")
    rows = await catalog._fetch_raw("ollama")
    assert rows and rows[0].context_length == 8192
    assert {request.url.path.rsplit("/", 1)[-1] for request in seen} >= {
        "tags", "show", "create", "generate",
    }


async def test_claude_signed_tool_continuation_survives_openai_runtime(
    monkeypatch, isolated_gateway,
):
    from jarvis.agent_chat import runner_api
    from jarvis.agent_runtimes import anthropic_history, model_map
    from jarvis.core import config
    from jarvis.plugins.brain.claude_api import ClaudeAPIBrain

    wire = AnthropicWire()
    wire.tool_arguments = '{"path":"a"}'
    wire.thinking = True
    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda provider: None)
    monkeypatch.setattr(model_map, "login_route", lambda *args: (False, None))
    grant = gateway.Grant("agent", "claude-api")
    body = {"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "Read a"}]}
    async with AsyncAnthropic(api_key="test-only", http_client=wire.client()) as client:
        brain = ClaudeAPIBrain(model=body["model"])
        brain._client = client
        monkeypatch.setattr(runner_api, "build_brain", lambda *args: brain)
        model, request = gateway.chat_request(body)
        first = await gateway.complete_chat(grant, model, request)
        message = first["choices"][0]["message"]
        assert "signature" not in json.dumps(message)
        assert "private test reasoning" not in json.dumps(message)
        body["messages"].extend([message, {
            "role": "tool", "tool_call_id": message["tool_calls"][0]["id"], "content": "contents",
        }])
        _, continuation = gateway.chat_request(body)
        await gateway.complete_chat(grant, model, continuation)
    assistant = json.loads(wire.requests[1].content)["messages"][1]["content"]
    assert assistant[0] == {"type": "thinking", "thinking": "private test reasoning",
                            "signature": "opaque-test-signature"}
    assert assistant[1]["type"] == "tool_use"
    # Opaque signatures never move to another agent/account or changed prefix.
    other = anthropic_history.restore(gateway.Grant("other", "claude-api"), model, continuation)
    assert other == continuation
    from dataclasses import replace

    changed = replace(continuation, system="different instructions")
    assert anthropic_history.restore(grant, model, changed) == changed
    changed_tools = replace(continuation, tools=({"name": "changed", "input_schema": {}},))
    assert anthropic_history.restore(grant, model, changed_tools) == changed_tools


@pytest.mark.parametrize("provider_reason,wire_reason", [
    ("FinishReason.MAX_TOKENS", "length"), ("SAFETY", "content_filter"), ("end_turn", "stop"),
])
def test_provider_finish_reasons_keep_their_meaning(provider_reason, wire_reason):
    assert gateway._finish(provider_reason) == wire_reason


@pytest.mark.parametrize("partial", [False, True])
async def test_responses_stream_requires_terminal_event(monkeypatch, isolated_gateway, partial):
    from types import SimpleNamespace

    async def events(**kwargs):
        if partial:
            yield {"type": "response.created", "response": {"id": "test"}}

    monkeypatch.setattr(gateway, "_client", lambda account: SimpleNamespace(stream=events))
    token = gateway.grant_token("agent", "openai-codex", require_active_turn=True)
    signal = gateway.watch_failure(token)
    if partial:
        stream = await gateway.open_response_stream(gateway.verify(token), {"model": "test"})
        assert b"response.failed" in b"".join([part async for part in stream])
    else:
        with pytest.raises(gateway.GatewayError, match="before completion"):
            await gateway.open_response_stream(gateway.verify(token), {"model": "test"})
    assert signal.done()


async def test_selected_effort_reaches_subscription(monkeypatch, isolated_gateway):
    from tests.unit.agent_runtimes.test_runtime_gateway import FakeSubscription

    client = FakeSubscription()
    monkeypatch.setattr(gateway, "_client", lambda account: client)
    token = gateway.grant_token("agent", "openai-codex", require_active_turn=True)
    gateway.watch_failure(token, effort="low")
    await gateway.complete_response(
        gateway.verify(token), {"model": "test", "reasoning_effort": "high"},
    )
    assert client.calls[0]["reasoning_effort"] == "low"


async def test_poisoned_openclaw_gateway_is_reaped_before_reuse(monkeypatch, tmp_path):
    import hashlib
    from types import SimpleNamespace

    from jarvis.agent_runtimes import openclaw
    from tests.unit.agent_runtimes.test_runtime_configs import _turn

    runtime = openclaw.OpenClawRuntime()
    old = openclaw._Gateway("agent", tmp_path, 12345,
        hashlib.sha256(b"[]").hexdigest(), SimpleNamespace(returncode=None), None, None,
        None, None,
        poisoned=True)
    runtime._gateways["agent"] = old
    actions = []

    async def stop(current):
        actions.append("stop")
        assert current is old
        current.proc.returncode = 0

    async def start(turn, key, home, port, token, env_hash, launcher, env):
        actions.append("start")
        assert old.proc.returncode == 0
        return openclaw._Gateway(key, home, port, env_hash,
            SimpleNamespace(returncode=None), None, None, None, None)

    monkeypatch.setattr(runtime, "_stop_gateway", stop)
    monkeypatch.setattr(runtime, "_start_gateway", start)
    monkeypatch.setattr(runtime, "_ensure_reaper", lambda: None)
    monkeypatch.setattr(openclaw, "write_json_if_changed", lambda *args: False)
    replacement = await runtime._ensure_gateway(_turn(tmp_path), "agent", tmp_path,
        "test-only", ["fake"], {})
    assert actions == ["stop", "start"] and replacement is not old


@pytest.mark.parametrize("streamed", [False, True])
async def test_waiting_request_cannot_acquire_replacement_turn(
    monkeypatch, isolated_gateway, streamed,
):
    import asyncio
    import threading

    from jarvis.agent_runtimes.model_limits import ModelLimits

    entered, resume = threading.Event(), threading.Event()
    calls = []

    def limits(*args):
        entered.set()
        assert resume.wait(5)
        return ModelLimits(context_window=8192)

    async def deltas(*args):
        calls.append(args)
        yield BrainDelta(content="must not run", finish_reason="stop")

    monkeypatch.setattr(gateway, "model_limits", limits)
    monkeypatch.setattr(gateway, "_deltas", deltas)
    token = gateway.grant_token("agent", "ollama", require_active_turn=True)
    old = gateway.watch_failure(token)
    complete = gateway.open_chat_stream if streamed else gateway.complete_chat
    task = asyncio.create_task(complete(gateway.verify(token), "model", BrainRequest(messages=())))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        gateway.unwatch_failure(token, old)
        current = gateway.watch_failure(token)
    finally:
        resume.set()
    with pytest.raises(gateway.GatewayError, match="no active turn"):
        await task
    assert not calls and not current.done()


def test_claude_evicted_ancestor_cannot_restore_dependent_signature(isolated_gateway):
    from dataclasses import replace

    from jarvis.agent_runtimes import anthropic_history as history

    grant = gateway.Grant("agent", "claude-api")
    base = BrainRequest(messages=(BrainMessage("user", "Read"),))
    first = {"type": "tool_use", "id": "first", "name": "read", "input": {}}
    thought = {"type": "thinking", "thinking": "private", "signature": "signed"}
    history.remember(grant, "model", base,
        {"id": "first", "_anthropic_blocks": [thought, first]}, provider_request=base)
    next_request = replace(base, messages=(*base.messages, BrainMessage("assistant", [first]),
                                          BrainMessage("tool", "ok", tool_call_id="first")))
    restored = history.restore(grant, "model", next_request)
    second = {**first, "id": "second"}
    history.remember(grant, "model", next_request,
        {"id": "second", "_anthropic_blocks": [thought, second]}, provider_request=restored)
    history._HISTORY.pop((grant, "model", "first"))
    final = replace(next_request, messages=(*next_request.messages,
                                            BrainMessage("assistant", [second])))
    assert history.restore(grant, "model", final) == final
