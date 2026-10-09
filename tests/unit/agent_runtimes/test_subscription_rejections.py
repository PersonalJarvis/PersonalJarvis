"""A rejected subscription request must not become a retryable server failure."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent_runtimes import gateway
from jarvis.core.http_pool import HttpClientPool
from jarvis.live.subscription_auth import SubscriptionCredentials
from jarvis.live.subscription_reasoning import SubscriptionReasoning
from jarvis.ui.web.runtime_gateway_routes import router


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    ("status", "code", "hint"),
    [
        (400, "invalid_function_parameters", "tool schema"),
        (400, "context_length_exceeded", "context"),
        (404, "model_not_found", "model"),
        (422, "invalid_request_error", "HTTP 422"),
        (400, "private-code-must-not-leak", "HTTP 400"),
    ],
)
def test_rejection_preserves_status_and_safe_reason(monkeypatch, stream, status, code, hint):
    requests = []

    async def credentials(**kwargs):
        return SubscriptionCredentials("fixture-token", "fixture-account")

    def upstream(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={"error": {"code": code, "message": "private-provider-body"}},
        )

    client = SubscriptionReasoning(
        credentials, http_pool=HttpClientPool(transport=httpx.MockTransport(upstream))
    )
    gateway.reset()
    monkeypatch.setattr(gateway, "_client", lambda account_id: client)
    token = gateway.grant_token("rejection-test", "openai-codex")
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as local:
        response = local.post(
            f"{gateway.BASE_PATH}/responses",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "selected", "input": "Hi", "stream": stream},
        )
    gateway.reset()

    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == ("invalid_request_error" if code.startswith("private-") else code)
    assert hint in error["message"]
    assert "private-provider-body" not in response.text
    assert "private-code-must-not-leak" not in response.text
    assert len(requests) == 1


@pytest.mark.parametrize("body", [b"not JSON", b"x" * 70_000], ids=["invalid", "oversized"])
async def test_invalid_error_bodies_still_report_rejection_without_leaking(body):
    from jarvis.live.subscription_reasoning import SubscriptionReasoningError

    async def credentials(**kwargs):
        return SubscriptionCredentials("fixture-token", "fixture-account")

    client = SubscriptionReasoning(
        credentials,
        http_pool=HttpClientPool(
            transport=httpx.MockTransport(lambda request: httpx.Response(400, content=body))
        ),
    )
    try:
        with pytest.raises(SubscriptionReasoningError, match="HTTP 400") as caught:
            async for _ in client.stream(model="selected", input=[], instructions="", tools=[]):
                pass
        assert caught.value.status == 400
        assert caught.value.code == "invalid_request_error"
        assert body.decode() not in str(caught.value)
    finally:
        await client.aclose()


async def test_rejection_after_stream_start_stays_an_sse_failure(monkeypatch):
    from jarvis.live.subscription_reasoning import SubscriptionReasoningError

    class FailingStream:
        async def stream(self, **kwargs):
            yield {"type": "response.created", "response": {"id": "fixture"}}
            raise SubscriptionReasoningError("Request rejected.", status=400)

    gateway.reset()
    monkeypatch.setattr(gateway, "_client", lambda account_id: FailingStream())
    events = [
        json.loads(frame.decode().split("data: ")[1])
        async for frame in gateway.stream_response(gateway.Grant("test", "openai-codex"), {})
    ]
    assert [event["type"] for event in events] == ["response.created", "response.failed"]
    gateway.reset()


async def test_broken_error_body_keeps_the_received_http_status():
    from jarvis.live.subscription_reasoning import SubscriptionReasoningError

    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise httpx.ReadError("private transport diagnostics")
            yield b""  # pragma: no cover - async iterator protocol

    async def credentials(**kwargs):
        return SubscriptionCredentials("fixture-token", "fixture-account")

    client = SubscriptionReasoning(
        credentials,
        http_pool=HttpClientPool(
            transport=httpx.MockTransport(lambda request: httpx.Response(400, stream=BrokenBody()))
        ),
    )
    try:
        with pytest.raises(SubscriptionReasoningError, match="HTTP 400") as caught:
            async for _ in client.stream(model="selected", input=[], instructions="", tools=[]):
                pass
        assert caught.value.status == 400
        assert "private" not in str(caught.value)
    finally:
        await client.aclose()


async def test_closing_an_accepted_response_closes_the_upstream(monkeypatch):
    closed = []

    class Stream:
        async def stream(self, **kwargs):
            try:
                yield {"type": "response.created", "response": {"id": "fixture"}}
                yield {"type": "response.output_text.delta", "delta": "text"}
            finally:
                closed.append(True)

    gateway.reset()
    monkeypatch.setattr(gateway, "_client", lambda account_id: Stream())
    events = await gateway.open_response_stream(gateway.Grant("test", "openai-codex"), {})
    await anext(events)
    await events.aclose()
    assert closed == [True]
    gateway.reset()
