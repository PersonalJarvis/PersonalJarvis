"""Jarvis' model gateway: Hermes / OpenClaw agents on the ChatGPT subscription.

The gateway is reached the way a runtime reaches it — through the real
``SurfaceSecurity`` guard with a Bearer and no Origin — because a credential
the guard does not know would be refused before any route ran.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent_runtimes import gateway
from jarvis.live.subscription_reasoning import SubscriptionReasoningError
from jarvis.ui.web.runtime_gateway_routes import router
from jarvis.ui.web.surface_security import SurfaceSecurity

_EVENTS = [
    {"type": "response.created", "response": {"id": "r1"}},
    {"type": "response.output_text.delta", "delta": "Hello"},
    {
        "type": "response.completed",
        "response": {"id": "r1", "status": "completed", "output": [{"type": "message"}]},
    },
]


class FakeSubscription:
    """Stands in for ``SubscriptionReasoning``: records the call, replays events."""

    def __init__(self, *, fail_after: int | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_after = fail_after

    async def stream(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        self.calls.append(kwargs)
        for index, event in enumerate(_EVENTS):
            if self.fail_after is not None and index == self.fail_after:
                raise SubscriptionReasoningError("The ChatGPT login expired.", code="login")
            yield event

    async def list_models(self) -> list[dict[str, Any]]:
        return [{"id": "gpt-5.6-sol", "label": "GPT-5.6 Sol"}]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSubscription:
    gateway.reset()
    client = FakeSubscription()
    monkeypatch.setattr(gateway, "_client", lambda account_id: client)
    yield client
    gateway.reset()


@pytest.fixture
def guarded() -> TestClient:
    app = FastAPI()
    app.include_router(router)

    @app.post("/api/settings/danger")
    def _settings() -> dict[str, str]:
        return {"changed": "yes"}

    return TestClient(SurfaceSecurity(app, control_key_validator=lambda token: False))


def _post(client: TestClient, token: str, body: dict[str, Any], path: str = "/responses") -> Any:
    return client.post(
        f"{gateway.BASE_PATH}{path}", headers={"Authorization": f"Bearer {token}"}, json=body
    )


def test_tokens_are_per_agent_and_stable() -> None:
    gateway.reset()
    first = gateway.grant_token("agent-1", "openai-codex", "acct")
    assert gateway.grant_token("agent-1", "openai-codex", "acct") == first
    assert gateway.grant_token("agent-2", "openai-codex", "acct") != first
    assert gateway.grant_token("agent-1", "openai") != first
    assert gateway.verify(first) == gateway.Grant("agent-1", "openai-codex", "acct")
    assert gateway.verify("jrg_made-up") is None
    assert gateway.verify("sk-anything") is None


def test_a_request_is_rebuilt_field_by_field() -> None:
    args = gateway.request_args(
        {
            "model": "gpt-5.6-sol",
            "input": "Hi",
            "instructions": "Be brief.",
            "tools": [
                {"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}},
                {"type": "function", "name": "b", "description": "B"},
                {"type": "web_search"},
            ],
            "reasoning": {"effort": "high"},
            "store": True,
            "temperature": 0.2,
            "previous_response_id": "r0",
        }
    )
    assert args["model"] == "gpt-5.6-sol"
    assert args["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "Hi"}]}]
    assert [tool["name"] for tool in args["tools"]] == ["a", "b"]
    assert all(tool["type"] == "function" for tool in args["tools"])
    assert args["reasoning_effort"] == "high"
    assert set(args) == {"model", "input", "instructions", "tools", "reasoning_effort"}
    with pytest.raises(gateway.GatewayError):
        gateway.request_args({"input": "no model"})


def test_a_runtime_streams_through_the_guard_without_an_origin(fake, guarded) -> None:
    token = gateway.grant_token("agent-1", "openai-codex")
    answer = _post(guarded, token, {"model": "gpt-5.6-sol", "input": "Hi", "stream": True})
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"].startswith("text/event-stream")
    frames = [json.loads(line[5:]) for line in answer.text.splitlines() if line.startswith("data:")]
    assert [frame["type"] for frame in frames] == [event["type"] for event in _EVENTS]
    assert fake.calls[0]["model"] == "gpt-5.6-sol"


def test_a_runtime_that_does_not_stream_gets_the_finished_response(fake, guarded) -> None:
    token = gateway.grant_token("agent-1", "openai-codex")
    answer = _post(guarded, token, {"model": "gpt-5.6-sol", "input": "Hi"})
    assert answer.status_code == 200
    assert answer.json()["id"] == "r1"


def test_a_failure_mid_stream_arrives_as_response_failed(fake, guarded) -> None:
    fake.fail_after = 2
    token = gateway.grant_token("agent-1", "openai-codex")
    answer = _post(guarded, token, {"model": "gpt-5.6-sol", "input": "Hi", "stream": True})
    frames = [json.loads(line[5:]) for line in answer.text.splitlines() if line.startswith("data:")]
    assert frames[-1]["type"] == "response.failed"
    assert "login expired" in frames[-1]["response"]["error"]["message"]


def test_models_are_listed_in_the_openai_shape(fake, guarded) -> None:
    token = gateway.grant_token("agent-1", "openai-codex")
    answer = guarded.get(
        f"{gateway.BASE_PATH}/models", headers={"Authorization": f"Bearer {token}"}
    )
    assert answer.json() == {
        "object": "list",
        "data": [{"id": "gpt-5.6-sol", "object": "model", "owned_by": "openai"}],
    }


def test_an_unknown_token_is_refused(fake, guarded) -> None:
    answer = _post(guarded, "jrg_not-minted", {"model": "m", "input": "Hi"})
    assert answer.status_code == 401
    assert fake.calls == []


def test_a_gateway_token_opens_nothing_but_the_gateway(fake, guarded) -> None:
    token = gateway.grant_token("agent-1", "openai-codex")
    answer = guarded.post(
        "/api/settings/danger", headers={"Authorization": f"Bearer {token}"}, json={}
    )
    assert answer.status_code == 401


# ------------------------------------------------- chat completions (API keys)


class _ProviderError(Exception):
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        super().__init__("provider body that must never reach the runtime")
        self.status_code = status
        self.headers = headers or {}


@pytest.fixture
def brain(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stands in for the provider plugin behind ``gateway._deltas``."""
    from jarvis.core.protocols import BrainDelta

    state: dict[str, Any] = {"requests": [], "fail": None, "fail_late": None}

    async def deltas(grant: gateway.Grant, model: str, request: Any) -> AsyncIterator[Any]:
        state["requests"].append((grant, model, request))
        if state["fail"] is not None:
            raise _ProviderError(state["fail"], state.get("headers"))
        yield BrainDelta(content="Looking")
        if state["fail_late"] is not None:
            raise _ProviderError(state["fail_late"], state.get("headers"))
        yield BrainDelta(
            tool_call={
                "id": "call_1",
                "name": "read_file",
                "input": {"path": "a.txt"},
                "thought_signature": "sig-1",
            }
        )
        yield BrainDelta(usage={"input_tokens": 12, "output_tokens": 3}, finish_reason="stop")

    monkeypatch.setattr(gateway, "_deltas", deltas)
    gateway.reset()
    yield state
    gateway.reset()


_CHAT = {
    "model": "gpt-5.2",
    "messages": [
        {"role": "system", "content": "You are Probe."},
        {"role": "user", "content": [{"type": "text", "text": "Read a.txt"}]},
    ],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }
    ],
    "max_tokens": 1000,
    "reasoning_effort": "low",
}


def _chat(client: TestClient, token: str, body: dict[str, Any]) -> Any:
    return client.post(
        f"{gateway.BASE_PATH}/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )


def test_a_chat_request_becomes_a_jarvis_brain_request() -> None:
    model, request = gateway.chat_request(
        {
            **_CHAT,
            "messages": [
                *_CHAT["messages"],
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_0",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"path": "b"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_0", "content": "file body"},
            ],
        }
    )
    assert model == "gpt-5.2"
    assert request.system == "You are Probe."
    assert [m.role for m in request.messages] == ["user", "assistant", "tool"]
    assert request.messages[1].content == [
        {"type": "tool_use", "id": "call_0", "name": "read_file", "input": {"path": "b"}}
    ]
    assert request.messages[2].tool_call_id == "call_0"
    assert request.tools[0]["name"] == "read_file" and "input_schema" in request.tools[0]
    assert request.max_tokens == 1000 and request.reasoning_effort == "low"


def test_chat_streams_text_and_tool_calls_in_the_openai_shape(brain, guarded) -> None:
    token = gateway.grant_token("agent-1", "openai")
    answer = _chat(guarded, token, {**_CHAT, "stream": True})
    assert answer.status_code == 200, answer.text
    lines = [line[5:].strip() for line in answer.text.splitlines() if line.startswith("data:")]
    assert lines[-1] == "[DONE]"
    chunks = [json.loads(line) for line in lines[:-1]]
    deltas = [chunk["choices"][0]["delta"] for chunk in chunks]
    assert deltas[0]["role"] == "assistant"
    assert "".join(d.get("content") or "" for d in deltas) == "Looking"
    calls = [call for d in deltas for call in d.get("tool_calls") or []]
    assert calls[0]["function"] == {"name": "read_file", "arguments": '{"path": "a.txt"}'}
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert chunks[-1]["usage"] == {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
    grant, model, _request = brain["requests"][0]
    assert grant.provider == "openai" and model == "gpt-5.2"


def test_a_gemini_signature_comes_back_with_its_tool_call(brain, guarded) -> None:
    token = gateway.grant_token("agent-1", "gemini")
    _chat(guarded, token, {**_CHAT, "stream": True})
    _model, request = gateway.chat_request(
        {
            "model": "gemini-3",
            "messages": [
                {"role": "user", "content": "Read a.txt"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_1", "function": {"name": "read_file", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "x"},
            ],
        }
    )
    assert request.messages[1].content[0]["thought_signature"] == "sig-1"


def test_chat_without_streaming_returns_a_completion(brain, guarded) -> None:
    token = gateway.grant_token("agent-1", "openai")
    body = _chat(guarded, token, _CHAT).json()
    message = body["choices"][0]["message"]
    assert message["content"] == "Looking"
    assert message["tool_calls"][0]["id"] == "call_1"
    assert body["choices"][0]["finish_reason"] == "tool_calls"


def test_a_rate_limit_up_front_is_a_429_without_the_provider_body(brain, guarded) -> None:
    brain["fail"] = 429
    token = gateway.grant_token("agent-1", "openai")
    answer = _chat(guarded, token, {**_CHAT, "stream": True})
    assert answer.status_code == 429
    assert "never reach" not in answer.text


@pytest.mark.parametrize("stream", [False, True])
def test_retry_after_blocks_repeated_calls_and_expires(brain, guarded, monkeypatch, stream):
    now = [100.0]
    monkeypatch.setattr(gateway.time, "monotonic", lambda: now[0])
    brain.update(fail=429, headers={"Retry-After": "90"})
    token = gateway.grant_token("agent-1", "openai", scope="chat")
    signal = gateway.watch_failure(token)
    answer = _chat(guarded, token, {**_CHAT, "stream": stream})
    assert answer.status_code == 429 and answer.headers["Retry-After"] == "90"
    assert "/continue" in signal.result(timeout=1)
    assert "fallback provider was called" in answer.text
    assert "never reach" not in signal.result()
    gateway.unwatch_failure(token, signal)

    # A second agent on the same model/key must also respect the cooldown.
    other = gateway.grant_token("agent-2", "openai")
    now[0] += 40
    assert _chat(guarded, other, _CHAT).headers["Retry-After"] == "50"
    assert len(brain["requests"]) == 1
    brain["fail"] = None
    now[0] += 50
    assert _chat(guarded, token, _CHAT).status_code == 200
    assert len(brain["requests"]) == 2


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("12.3", 12.3),
        ("0", 0),
        ("-1", 0),
        ("NaN", None),
        ("Infinity", None),
        ("invalid", None),
        ("Wed, 21 Oct 2015 07:28:00 GMT", 60),
    ],
)
def test_retry_after_parses_numeric_and_http_date_without_provider_body(monkeypatch, raw, expected):
    monkeypatch.setattr(gateway.time, "time", lambda: 1445412420.0)
    failure = gateway._failure("openai", _ProviderError(429, {"rEtRy-AfTeR": raw}))
    assert failure.retry_after == expected
    assert "never reach" not in str(failure)


def test_a_long_limit_returns_immediately_without_waiting_or_fallback(brain, guarded):
    brain.update(fail=429, headers={"Retry-After": "86400"})
    token = gateway.grant_token("agent-1", "claude-api")
    answer = _chat(guarded, token, _CHAT)
    assert answer.headers["Retry-After"] == "86400"
    assert len(brain["requests"]) == 1
    assert brain["requests"][0][0].provider == "claude-api"


def test_missing_retry_after_uses_short_conservative_cooldown(brain, guarded):
    brain["fail"] = 429
    token = gateway.grant_token("agent-1", "openai")
    assert _chat(guarded, token, _CHAT).headers["Retry-After"] == "30"
    assert _chat(guarded, token, _CHAT).status_code == 429
    assert len(brain["requests"]) == 1


def test_routine_failure_cannot_end_the_direct_chat(brain, guarded):
    chat = gateway.grant_token("agent-1", "openai", scope="agent-1")
    routine = gateway.grant_token("agent-1", "openai", scope="agent-1~runs")
    assert chat != routine
    chat_signal, routine_signal = gateway.watch_failure(chat), gateway.watch_failure(routine)
    brain["fail"] = 429
    _chat(guarded, routine, _CHAT)
    assert routine_signal.done() and not chat_signal.done()
    gateway.unwatch_failure(chat, chat_signal)
    gateway.unwatch_failure(routine, routine_signal)


def test_late_failure_keeps_the_old_turn_identity(brain):
    token = gateway.grant_token("agent-1", "openai", scope="chat")
    old = gateway.watch_failure(token)
    gateway.unwatch_failure(token, old)
    current = gateway.watch_failure(token)
    gateway._report_failure(
        gateway.verify(token), "m", gateway._failure("openai", _ProviderError(429)), old
    )
    assert old.done() and not current.done()
    gateway.unwatch_failure(token, current)


def test_midstream_rate_limit_stops_the_runner_without_done(brain, guarded):
    token = gateway.grant_token("agent-1", "openai")
    signal = gateway.watch_failure(token)
    brain.update(fail_late=429, headers={"Retry-After": "3"})
    response = _chat(guarded, token, {**_CHAT, "stream": True})
    assert "Looking" in response.text and "[DONE]" not in response.text
    assert "HTTP 429" in signal.result(timeout=1)
    gateway.unwatch_failure(token, signal)


def test_subscription_limit_never_uses_the_api_path(fake, guarded, monkeypatch):
    async def limited(**kwargs):
        fake.calls.append(kwargs)
        raise SubscriptionReasoningError("usage limit", status=429, retry_after="120")
        yield  # pragma: no cover - keep this a stream

    monkeypatch.setattr(fake, "stream", limited)
    token = gateway.grant_token("agent-1", "openai-codex", "account-a")
    signal = gateway.watch_failure(token)
    answer = _post(guarded, token, {"model": "m", "input": "hello"})
    assert answer.status_code == 429 and answer.headers["Retry-After"] == "120"
    assert "openai-codex" in signal.result(timeout=1)
    _post(guarded, token, {"model": "m", "input": "hello"})
    assert len(fake.calls) == 1
    gateway.check_cooldown("openai-codex", "m", "account-b")
    gateway.unwatch_failure(token, signal)


def test_a_failure_mid_stream_is_an_error_chunk(brain, guarded) -> None:
    brain["fail_late"] = 500
    token = gateway.grant_token("agent-1", "openai")
    answer = _chat(guarded, token, {**_CHAT, "stream": True})
    assert '"error"' in answer.text and "[DONE]" not in answer.text
    assert "never reach" not in answer.text


async def test_a_closed_stream_releases_the_provider_generator(monkeypatch):
    from jarvis.core.protocols import BrainDelta

    closed = []

    async def deltas(*_args):
        try:
            yield BrainDelta(content="partial")
            yield BrainDelta(content="more")
        finally:
            closed.append(True)

    gateway.reset()
    monkeypatch.setattr(gateway, "_deltas", deltas)
    model, request = gateway.chat_request(_CHAT)
    stream = await gateway.open_chat_stream(gateway.Grant("a", "openai"), model, request)
    await anext(stream)
    await stream.aclose()
    assert closed == [True]


def test_each_shape_answers_only_its_own_providers(brain, guarded) -> None:
    subscription = gateway.grant_token("agent-1", "openai-codex")
    assert _chat(guarded, subscription, _CHAT).status_code == 400
    api_key = gateway.grant_token("agent-1", "openai")
    assert _post(guarded, api_key, {"model": "m", "input": "Hi"}).status_code == 400


def test_api_key_models_come_from_jarvis_catalog(guarded) -> None:
    gateway.reset()
    from jarvis.agent_chat.catalog import provider_row

    with_catalog = next(
        name
        for name in ("claude-api", "openai", "gemini", "grok")
        if (row := provider_row(name)) is not None and row.curated_models
    )
    token = gateway.grant_token("agent-1", with_catalog)
    data = guarded.get(
        f"{gateway.BASE_PATH}/models", headers={"Authorization": f"Bearer {token}"}
    ).json()["data"]
    assert [row["id"] for row in data] == [m.id for m in provider_row(with_catalog).curated_models]
    gateway.reset()


def test_a_streamed_answer_ends_cleanly_with_the_real_key_and_cost_context(
    monkeypatch: pytest.MonkeyPatch, guarded
) -> None:
    """The plugin runs under context variables (key override, cost caller).
    The streamed response is read by another task than the one that opened
    it; the plugin's own task keeps them from failing at the end
    (live 2026-10-06: every Ollama answer ended in an error chunk)."""
    import jarvis.agent_chat.runner_api as runner_api
    import jarvis.core.config as config
    from jarvis.core.protocols import BrainDelta

    class Brain:
        async def complete(self, request: Any) -> AsyncIterator[BrainDelta]:
            yield BrainDelta(content="OK")
            yield BrainDelta(finish_reason="stop", usage={"input_tokens": 2, "output_tokens": 1})

    monkeypatch.setattr(runner_api, "build_brain", lambda provider, model: Brain())
    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda provider: "sk-agents-tier")
    gateway.reset()
    token = gateway.grant_token("agent-1", "ollama")
    answer = _chat(guarded, token, {**_CHAT, "stream": True})
    gateway.reset()
    assert answer.status_code == 200
    assert '"error"' not in answer.text
    assert answer.text.rstrip().endswith("data: [DONE]")
