"""Deterministic subscription orchestration without credentials or network."""

import asyncio
import copy
from types import SimpleNamespace

from jarvis.core.model_selection import operation_model
from jarvis.core.protocols import SupervisorToolDescriptor, ToolResult


class SubscriptionGateway:
    def __init__(self):
        self.calls = []
        self.image = None

    def catalog(self):
        return (
            SupervisorToolDescriptor(
                "inspect-state",
                "Inspect current application state",
                {"type": "object", "properties": {}},
                "safe",
            ),
        )

    async def execute(self, name, args, request):
        self.calls.append((name, args, operation_model.get()))
        output = {"verified": True}
        if self.image is not None:
            output["_image"] = self.image
        return ToolResult(True, output)

    async def cancel_pending(self, *_args, **_kwargs):
        return True


class SubscriptionConnection:
    session_id = "rtc_fake"
    answer_sdp = "answer"

    def __init__(self):
        self.sent = []
        self.closed = False

    async def send(self, event):
        self.sent.append(event)

    async def close(self):
        self.closed = True


class SubscriptionAccount:
    async def credentials(self, **_kwargs):
        raise AssertionError("An orchestration unit test must never load credentials")

    async def aclose(self):
        pass


class SubscriptionEvents:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


class ScriptedSubscriptionReasoning:
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.requests = []
        self.closed = False
        self.started = asyncio.Event()
        self.block = None

    async def stream(self, **request):
        self.requests.append(copy.deepcopy(request))
        self.started.set()
        if self.block is not None:
            await self.block.wait()
        for event in self.rounds.pop(0):
            yield event

    async def aclose(self):
        self.closed = True


def subscription_provider():
    return SimpleNamespace(
        name="openai-live-subscription",
        usage_billed=False,
        requires_close_ack=False,
        subscription_auth=SubscriptionAccount(),
    )


def completed_response(identifier, output):
    return [
        {"type": "response.created", "response": {"id": identifier}},
        *[{"type": "response.output_item.done", "item": item} for item in output],
        {
            "type": "response.completed",
            "response": {
                "id": identifier,
                "output": output,
                "usage": {"input_tokens": 11, "output_tokens": 7},
            },
        },
    ]


def spoken_result(text="The state is verified."):
    return {
        "id": "message-final",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }
