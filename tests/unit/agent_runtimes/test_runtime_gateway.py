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
    first = gateway.grant_token("agent-1", "acct")
    assert gateway.grant_token("agent-1", "acct") == first
    assert gateway.grant_token("agent-2", "acct") != first
    assert gateway.verify(first) == gateway.Grant("agent-1", "acct")
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
    token = gateway.grant_token("agent-1")
    answer = _post(guarded, token, {"model": "gpt-5.6-sol", "input": "Hi", "stream": True})
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"].startswith("text/event-stream")
    frames = [json.loads(line[5:]) for line in answer.text.splitlines() if line.startswith("data:")]
    assert [frame["type"] for frame in frames] == [event["type"] for event in _EVENTS]
    assert fake.calls[0]["model"] == "gpt-5.6-sol"


def test_a_runtime_that_does_not_stream_gets_the_finished_response(fake, guarded) -> None:
    token = gateway.grant_token("agent-1")
    answer = _post(guarded, token, {"model": "gpt-5.6-sol", "input": "Hi"})
    assert answer.status_code == 200
    assert answer.json()["id"] == "r1"


def test_a_failure_mid_stream_arrives_as_response_failed(fake, guarded) -> None:
    fake.fail_after = 2
    token = gateway.grant_token("agent-1")
    answer = _post(guarded, token, {"model": "gpt-5.6-sol", "input": "Hi", "stream": True})
    frames = [json.loads(line[5:]) for line in answer.text.splitlines() if line.startswith("data:")]
    assert frames[-1]["type"] == "response.failed"
    assert "login expired" in frames[-1]["response"]["error"]["message"]


def test_models_are_listed_in_the_openai_shape(fake, guarded) -> None:
    token = gateway.grant_token("agent-1")
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
    token = gateway.grant_token("agent-1")
    answer = guarded.post(
        "/api/settings/danger", headers={"Authorization": f"Bearer {token}"}, json={}
    )
    assert answer.status_code == 401
