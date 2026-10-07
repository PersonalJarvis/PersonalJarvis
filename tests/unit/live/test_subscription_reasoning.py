"""Subscription SSE, completion, billing isolation, and image Brain contracts."""

from __future__ import annotations

import json

import httpx
import pytest

from jarvis.core.http_pool import HttpClientPool
from jarvis.core.protocols import Brain, BrainMessage, BrainRequest, ImageBlock
from jarvis.live.subscription_auth import SubscriptionCredentials
from jarvis.live.subscription_reasoning import (
    SubscriptionReasoning,
    SubscriptionReasoningBrain,
    SubscriptionReasoningError,
)


class Credentials:
    def __init__(self):
        self.calls = []

    async def __call__(self, *, force_refresh=False):
        self.calls.append(force_refresh)
        return SubscriptionCredentials(
            "fixture-fresh" if force_refresh else "fixture-access", "fixture-account"
        )


def _sse(*events):
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


def _completed(**extra):
    return {"type": "response.completed", "response": {"status": "completed", **extra}}


def _reasoning(handler):
    credentials = Credentials()
    pool = HttpClientPool(timeout_s=1, transport=httpx.MockTransport(handler))
    return SubscriptionReasoning(credentials, http_pool=pool), credentials


async def _collect(reasoning, **kwargs):
    return [
        event
        async for event in reasoning.stream(
            model="chosen-model", input=[], instructions="Answer", tools=[], **kwargs
        )
    ]


@pytest.mark.asyncio
async def test_stream_uses_oauth_codex_route_and_explicit_completed_event(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-forbidden-api-key")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, text=_sse({"type": "response.output_text.delta", "delta": "Hello"}, _completed())
        )

    reasoning, _ = _reasoning(handler)
    events = await _collect(reasoning)
    assert events[-1]["type"] == "response.completed"
    assert str(requests[0].url) == "https://chatgpt.com/backend-api/codex/responses"
    assert requests[0].headers["Authorization"] == "Bearer fixture-access"
    assert requests[0].headers["ChatGPT-Account-Id"] == "fixture-account"
    body = json.loads(requests[0].content)
    assert body["store"] is False and body["stream"] is True
    assert body["model"] == "chosen-model"
    assert body["include"] == ["reasoning.encrypted_content"]
    await reasoning.aclose()


@pytest.mark.asyncio
async def test_auth_rejection_gets_one_refresh_without_api_fallback():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(401 if len(requests) == 1 else 200, text=_sse(_completed()))

    reasoning, credentials = _reasoning(handler)
    await _collect(reasoning)
    assert credentials.calls == [False, True]
    assert requests[1].headers["Authorization"] == "Bearer fixture-fresh"
    await reasoning.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", ["data: [DONE]\n\n", _sse({"type": "response.output_text.delta", "delta": "partial"})]
)
async def test_disconnect_never_counts_as_completion_or_retries(body):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=body)

    reasoning, _ = _reasoning(handler)
    with pytest.raises(SubscriptionReasoningError, match="before completion"):
        await _collect(reasoning)
    assert len(requests) == 1
    await reasoning.aclose()


@pytest.mark.asyncio
async def test_usage_limit_midstream_is_safe_and_terminal():
    event = {
        "type": "response.failed",
        "response": {
            "error": {
                "code": "usage_limit_reached",
                "message": "sensitive-provider-body",
            }
        },
    }
    reasoning, credentials = _reasoning(lambda _request: httpx.Response(200, text=_sse(event)))
    with pytest.raises(SubscriptionReasoningError, match="usage limit") as caught:
        await _collect(reasoning)
    assert caught.value.code == "usage_limit_reached"
    assert caught.value.status == 429
    assert "sensitive-provider-body" not in str(caught.value)
    assert credentials.calls == [False]
    await reasoning.aclose()


async def test_http_rate_limit_keeps_retry_after_without_retrying_or_refreshing():
    requests = []

    def limited(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "3600"}, text="sensitive-body")

    reasoning, credentials = _reasoning(limited)
    with pytest.raises(SubscriptionReasoningError) as caught:
        await _collect(reasoning)
    assert caught.value.status == 429 and caught.value.retry_after == "3600"
    assert "sensitive-body" not in str(caught.value)
    assert credentials.calls == [False] and len(requests) == 1
    await reasoning.aclose()


@pytest.mark.asyncio
async def test_hosted_tool_is_rejected_before_auth_or_network():
    def forbidden(_request):
        pytest.fail("Hosted tools must fail before opening a request")

    reasoning, credentials = _reasoning(forbidden)
    with pytest.raises(SubscriptionReasoningError, match="Jarvis function"):
        async for _ in reasoning.stream(
            model="chosen", input=[], instructions="", tools=[{"type": "computer"}]
        ):
            pass
    assert not credentials.calls
    await reasoning.aclose()


@pytest.mark.asyncio
async def test_catalog_is_subscription_specific_and_filters_hidden_models():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "models": [
                    {"slug": "selected", "display_name": "Selected", "visibility": "list"},
                    {"slug": "hidden", "visibility": "hide"},
                ]
            },
        )

    reasoning, _ = _reasoning(handler)
    assert await reasoning.list_models() == [{"id": "selected", "label": "Selected"}]
    assert requests[0].url.path == "/backend-api/codex/models"
    assert "client_version" in requests[0].url.params
    await reasoning.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["", "max"])
async def test_stream_preserves_model_default_and_max(effort):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=_sse(_completed()))

    reasoning, _ = _reasoning(handler)
    await _collect(reasoning, reasoning_effort=effort)
    actual = json.loads(requests[0].content)["reasoning"]
    assert actual == ({"summary": "auto", "effort": "max"} if effort else {"summary": "auto"})
    await reasoning.aclose()


@pytest.mark.asyncio
async def test_catalog_efforts_and_default_are_preserved_without_provider_metadata():
    reasoning, _ = _reasoning(
        lambda _request: httpx.Response(
            200,
            json={
                "models": [
                    {
                        "slug": "selected",
                        "display_name": "Selected",
                        "visibility": "list",
                        "supported_reasoning_levels": [
                            {"effort": "low", "description": "private-provider-text"},
                            {"effort": "max"},
                            {"effort": "ultra"},
                            {"effort": "max"},
                            {"effort": "bad\nvalue"},
                        ],
                        "default_reasoning_level": "low",
                    }
                ]
            },
        )
    )
    assert await reasoning.list_models() == [
        {
            "id": "selected",
            "label": "Selected",
            "efforts": ["low", "max", "ultra"],
            "default_effort": "low",
        }
    ]
    await reasoning.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("advertised", [True, False])
async def test_future_effort_requires_model_catalog_capability(advertised):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "slug": "chosen-model",
                            "supported_reasoning_levels": [
                                {"effort": "ultra" if advertised else "max"},
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(200, text=_sse(_completed()))

    reasoning, _ = _reasoning(handler)
    if advertised:
        await _collect(reasoning, reasoning_effort="ultra")
        assert json.loads(requests[1].content)["reasoning"]["effort"] == "ultra"
    else:
        with pytest.raises(SubscriptionReasoningError, match="does not advertise"):
            await _collect(reasoning, reasoning_effort="ultra")
        assert len(requests) == 1
    await reasoning.aclose()


@pytest.mark.asyncio
async def test_loaded_catalog_rejects_unsupported_known_effort():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "slug": "chosen-model",
                        "supported_reasoning_levels": [{"effort": "low"}],
                    }
                ]
            },
        )

    reasoning, _ = _reasoning(handler)
    await reasoning.list_models()
    with pytest.raises(SubscriptionReasoningError, match="does not support"):
        await _collect(reasoning, reasoning_effort="max")
    assert len(requests) == 1
    await reasoning.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", [None, "max"])
async def test_brain_preserves_default_and_max(effort):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=_sse(_completed()))

    reasoning, _ = _reasoning(handler)
    brain = SubscriptionReasoningBrain(reasoning, "selected")
    request = BrainRequest(messages=(), reasoning_effort=effort)
    _ = [delta async for delta in brain.complete(request)]
    actual = json.loads(requests[0].content)["reasoning"]
    assert actual == ({"summary": "auto", "effort": "max"} if effort else {"summary": "auto"})
    await reasoning.aclose()


@pytest.mark.asyncio
async def test_brain_preserves_images_function_calls_and_cache_usage():
    requests = []
    call = {
        "type": "response.output_item.done",
        "item": {
            "type": "function_call",
            "call_id": "call-1",
            "name": "inspect",
            "arguments": '{"target":"page"}',
        },
    }

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            text=_sse(
                {"type": "response.output_text.delta", "delta": "Ready"},
                call,
                call,
                _completed(
                    usage={
                        "input_tokens": 20,
                        "output_tokens": 3,
                        "input_tokens_details": {"cached_tokens": 7},
                    }
                ),
            ),
        )

    reasoning, _ = _reasoning(handler)
    brain = SubscriptionReasoningBrain(reasoning, "selected-brain")
    assert isinstance(brain, Brain)
    assert brain.estimate_cost(BrainRequest(messages=())) == 0.0
    request = BrainRequest(
        system="Analyze the image",
        messages=(BrainMessage("user", "Where?", images=(ImageBlock("image/png", "fixture"),)),),
        tools=({"name": "inspect", "input_schema": {"type": "object"}},),
        reasoning_effort="low",
    )
    deltas = [delta async for delta in brain.complete(request)]
    assert deltas[0].content == "Ready"
    assert len([delta for delta in deltas if delta.tool_call]) == 1
    assert deltas[-2].finish_reason == "tool_calls"
    assert deltas[-1].usage == {"input_tokens": 13, "output_tokens": 3, "cache_hit_tokens": 7}
    body = json.loads(requests[0].content)
    assert body["model"] == "selected-brain"
    assert body["input"][0]["content"][1] == {
        "type": "input_image",
        "image_url": "data:image/png;base64,fixture",
    }
    assert body["reasoning"]["effort"] == "low"
    await reasoning.aclose()


@pytest.mark.asyncio
async def test_brain_does_not_emit_tool_calls_from_failed_stream():
    call = {
        "type": "response.output_item.done",
        "item": {
            "type": "function_call",
            "call_id": "call-1",
            "name": "inspect",
            "arguments": "{}",
        },
    }
    reasoning, _ = _reasoning(lambda _request: httpx.Response(200, text=_sse(call)))
    brain = SubscriptionReasoningBrain(reasoning, "chosen")
    deltas = []
    with pytest.raises(SubscriptionReasoningError):
        async for delta in brain.complete(BrainRequest(messages=())):
            deltas.append(delta)
    assert not deltas
    await reasoning.aclose()


@pytest.mark.asyncio
async def test_brain_accepts_protocol_req_keyword():
    reasoning, _ = _reasoning(lambda _request: httpx.Response(200, text=_sse(_completed())))
    brain = SubscriptionReasoningBrain(reasoning, "chosen-model")
    deltas = [delta async for delta in brain.complete(req=BrainRequest(messages=()))]
    assert deltas[-1].finish_reason == "stop"
    await reasoning.aclose()
