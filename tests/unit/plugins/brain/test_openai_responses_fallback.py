"""Responses-API transport fallback for Responses-only OpenAI models.

OpenAI serves its deep-reasoning "pro" class only via the Responses API; a
Chat-Completions call 404s with "This is not a chat model...". Live
2026-08-06 20:19: the screen-context vision turn crossed families onto
openai(gpt-5.5-pro), took exactly that 404, and the user heard the dishonest
"network or provider issue" apology while hunting for broken API keys.

The shared base must (1) switch transports on the server's EXPLICIT rejection
(never on a model-name pin, AP-21), (2) cache the verdict per endpoint+model,
(3) emit the same BrainDelta sequence as the chat path, and (4) classify the
rejection honestly if the transport switch is impossible.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.protocols import BrainMessage, BrainRequest, ImageBlock
from jarvis.plugins.brain import _openai_base
from jarvis.plugins.brain._openai_base import stream_complete

# The live rejection, verbatim shape (desktop log 2026-08-06 20:19:14).
_NOT_A_CHAT_MODEL_404 = (
    "Error code: 404 - {'error': {'message': 'This is not a chat model and "
    "thus not supported in the v1/chat/completions endpoint. Did you mean to "
    "use v1/completions?', 'type': 'invalid_request_error', 'param': 'model', "
    "'code': None}}"
)


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both process-global memories are isolated per test."""
    monkeypatch.setattr(_openai_base, "_PARAM_ADAPTATION_CACHE", {})
    monkeypatch.setattr(_openai_base, "_RESPONSES_ONLY_CACHE", set())


class _FakeResponsesStream:
    def __init__(self, events: list[SimpleNamespace]) -> None:
        self._events = list(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


class _FakeClient:
    """Chat rejects with the live 404; Responses streams a scripted answer."""

    base_url = "https://api.openai.com/v1"

    def __init__(self, events: list[SimpleNamespace]) -> None:
        self.chat_calls: list[dict] = []
        self.responses_calls: list[dict] = []
        self._events = events

        async def _chat_create(**kwargs):
            self.chat_calls.append(kwargs)
            raise RuntimeError(_NOT_A_CHAT_MODEL_404)

        async def _responses_create(**kwargs):
            self.responses_calls.append(kwargs)
            return _FakeResponsesStream(list(self._events))

        chat = SimpleNamespace(completions=SimpleNamespace(create=_chat_create))
        self.chat = chat
        self.responses = SimpleNamespace(create=_responses_create)


def _text_events() -> list[SimpleNamespace]:
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
    )
    return [
        SimpleNamespace(type="response.output_text.delta", delta="You are on "),
        SimpleNamespace(type="response.output_text.delta", delta="example.com."),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=usage),
        ),
    ]


def _vision_request() -> BrainRequest:
    return BrainRequest(
        messages=(
            BrainMessage(role="system", content="You are Jarvis."),
            BrainMessage(
                role="user",
                content="Which domain am I on?",
                images=(ImageBlock(mime="image/png", data_b64="QUJD"),),
            ),
        ),
        max_tokens=256,
    )


async def _collect(client, model: str, req: BrainRequest) -> list:
    return [d async for d in stream_complete(client, model, req)]


async def test_not_a_chat_model_404_switches_to_responses_transport() -> None:
    client = _FakeClient(_text_events())
    deltas = await _collect(client, "gpt-5.5-pro", _vision_request())

    text = "".join(d.content for d in deltas if d.content)
    assert text == "You are on example.com."
    assert [d.finish_reason for d in deltas if d.finish_reason] == ["stop"]
    usage = next(d.usage for d in deltas if d.usage)
    assert usage == {"input_tokens": 100, "output_tokens": 20}

    # The failed chat probe ran exactly once, then the Responses call.
    assert len(client.chat_calls) == 1
    assert len(client.responses_calls) == 1

    r_kwargs = client.responses_calls[0]
    assert r_kwargs["model"] == "gpt-5.5-pro"
    assert r_kwargs["instructions"] == "You are Jarvis."
    # Reasoning models spend hidden thought against max_output_tokens; the
    # tiny chat-era budget must be floored or the answer arrives empty.
    assert r_kwargs["max_output_tokens"] >= 4096
    # Sampling and reasoning knobs are deliberately not carried over.
    assert "temperature" not in r_kwargs
    assert "reasoning_effort" not in r_kwargs

    user_item = next(
        i for i in r_kwargs["input"] if i.get("role") == "user"
    )
    kinds = [b["type"] for b in user_item["content"]]
    assert kinds == ["input_text", "input_image"]
    assert user_item["content"][1]["image_url"].startswith("data:image/png;base64,")


async def test_verdict_is_cached_so_second_turn_skips_the_chat_probe() -> None:
    client = _FakeClient(_text_events())
    await _collect(client, "gpt-5.5-pro", _vision_request())

    client._events = _text_events()
    await _collect(client, "gpt-5.5-pro", _vision_request())

    assert len(client.chat_calls) == 1  # only the first turn paid the probe
    assert len(client.responses_calls) == 2


async def test_function_call_items_come_back_as_tool_call_deltas() -> None:
    events = [
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_abc",
                name="jarvis_action",
                arguments='{"utterance": "check the screen"}',
            ),
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=None),
        ),
    ]
    client = _FakeClient(events)
    req = BrainRequest(
        messages=(BrainMessage(role="user", content="do it"),),
        tools=(
            {
                "name": "jarvis_action",
                "description": "Dispatch an action.",
                "input_schema": {"type": "object", "properties": {}},
            },
        ),
    )
    deltas = await _collect(client, "gpt-5.5-pro", req)

    tool = next(d.tool_call for d in deltas if d.tool_call)
    assert tool == {
        "id": "call_abc",
        "name": "jarvis_action",
        "input": {"utterance": "check the screen"},
    }
    assert [d.finish_reason for d in deltas if d.finish_reason] == ["tool_calls"]

    # Declared tools were translated to the flat Responses shape.
    declared = client.responses_calls[0]["tools"]
    assert declared == [
        {
            "type": "function",
            "name": "jarvis_action",
            "description": "Dispatch an action.",
            "parameters": {"type": "object", "properties": {}},
        }
    ]


async def test_tool_history_round_trips_as_function_call_items() -> None:
    client = _FakeClient(_text_events())
    req = BrainRequest(
        messages=(
            BrainMessage(role="user", content="open the site"),
            BrainMessage(
                role="assistant",
                content=[
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "jarvis_action",
                        "input": {"utterance": "open"},
                    }
                ],
            ),
            BrainMessage(role="tool", content="done", tool_call_id="call_1"),
        ),
    )
    await _collect(client, "gpt-5.5-pro", req)

    items = client.responses_calls[0]["input"]
    call_item = next(i for i in items if i.get("type") == "function_call")
    assert call_item["call_id"] == "call_1"
    assert call_item["name"] == "jarvis_action"
    output_item = next(i for i in items if i.get("type") == "function_call_output")
    assert output_item == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "done",
    }


async def test_unrelated_errors_are_never_retried_on_responses() -> None:
    class _PlainFailClient:
        base_url = "https://api.openai.com/v1"

        def __init__(self) -> None:
            async def _chat_create(**kwargs):
                raise RuntimeError("Error code: 500 - upstream exploded")

            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=_chat_create)
            )
            self.responses = SimpleNamespace(create=None)

    with pytest.raises(RuntimeError, match="upstream exploded"):
        await _collect(
            _PlainFailClient(),
            "gpt-5.5",
            BrainRequest(messages=(BrainMessage(role="user", content="hi"),)),
        )


async def test_sdk_without_responses_api_reraises_the_honest_404() -> None:
    """An old SDK cannot switch transports — the 404 must surface unchanged

    so the manager's classifier reads it as invalid_model (an honest spoken
    cause), never as a silent no-op (AP-30).
    """

    class _OldSdkClient:
        base_url = "https://api.openai.com/v1"

        def __init__(self) -> None:
            async def _chat_create(**kwargs):
                raise RuntimeError(_NOT_A_CHAT_MODEL_404)

            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=_chat_create)
            )
            # No .responses attribute at all on old SDKs.

    with pytest.raises(RuntimeError, match="not a chat model"):
        await _collect(
            _OldSdkClient(),
            "gpt-5.5-pro",
            BrainRequest(messages=(BrainMessage(role="user", content="hi"),)),
        )


def test_manager_classifies_the_live_404_as_invalid_model() -> None:
    from jarvis.brain.manager import _classify_provider_error

    assert (
        _classify_provider_error(_NOT_A_CHAT_MODEL_404, default="call_fail")
        == "invalid_model"
    )
