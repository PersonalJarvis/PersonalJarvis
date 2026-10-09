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


class AppshotSubscriptionGateway(SubscriptionGateway):
    """A newly requested capture returns a different image, or an explicit refusal."""

    def __init__(self, *, refused=False, handoff=None):
        super().__init__()
        self.refused = refused
        self.handoff = handoff

    def catalog(self):
        from jarvis.plugins.tool.appshot import AppshotTool

        return (*super().catalog(), SupervisorToolDescriptor(
            AppshotTool.name, AppshotTool.description, AppshotTool.schema, "monitor",
        ))

    async def execute(self, name, args, request):
        self.calls.append((name, args, operation_model.get()))
        if self.refused:
            return ToolResult(False, None, "Blocked by the privacy filter.")
        output = {
            "description": "A newly captured window",
            "_image": {"mime": "image/png", "data": "TkVX"},
        }
        if self.handoff:
            output["handoff"] = self.handoff
        return ToolResult(True, output)


class PausingAgentGateway(SubscriptionGateway):
    """An agent start remains in flight while another instruction arrives."""

    def __init__(self, *, uncertain=False):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.requests = []
        self.uncertain = uncertain

    def catalog(self):
        return tuple(
            SupervisorToolDescriptor(name, name, {"type": "object"}, "monitor")
            for name in ("start-agent", "message-agent")
        )

    async def execute(self, name, args, request):
        self.calls.append((name, args, operation_model.get()))
        self.requests.append(request)
        if name == "start-agent":
            self.started.set()
            await self.release.wait()
            if self.uncertain:
                raise OSError("Receipt lost after starting the agent")
        return ToolResult(True, {"agent_id": "agent-alpha", "verified": True})


class ApprovalSubscriptionGateway(SubscriptionGateway):
    def __init__(self):
        super().__init__()
        self.confirmed = []

    def catalog(self):
        return (SupervisorToolDescriptor(
            "inspect-state", "Approved action fixture", {"type": "object"}, "ask",
            is_action_tool=True,
        ),)

    async def execute(self, name, args, request):
        from jarvis.safety.tool_executor import VOICE_CONFIRM_SENTINEL

        self.calls.append((name, args, request))
        return ToolResult(False, {}, VOICE_CONFIRM_SENTINEL)

    async def execute_confirmed(self, trace, request):
        self.confirmed.append((trace, request))
        return ToolResult(True, {"verified": True})


class SafeMessageGateway(SubscriptionGateway):
    def catalog(self):
        return (
            SupervisorToolDescriptor(
                "message_agent", "Message a teammate", {"type": "object"}, "safe",
                is_action_tool=True,
            ),
            SupervisorToolDescriptor(
                "workspace-orchestrate", "Inspect or change a workspace", {"type": "object"},
                "monitor", is_action_tool=True,
                describe_args=lambda args: {
                    "level": "read" if args.get("action") == "inspect" else "modify",
                },
            ),
        )


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


class PausingSubscriptionReasoning(ScriptedSubscriptionReasoning):
    """Pause selected rounds before completion, including cancellation races."""

    def __init__(self, rounds, *, paused=(0,)):
        super().__init__(rounds)
        self.entered = [asyncio.Event() for _ in rounds]
        self.release = {index: asyncio.Event() for index in paused}
        self.cancelled = []

    async def stream(self, **request):
        index = len(self.requests)
        self.requests.append(copy.deepcopy(request))
        self.entered[index].set()
        try:
            if index in self.release:
                await self.release[index].wait()
            for event in self.rounds[index]:
                yield event
        except asyncio.CancelledError:
            self.cancelled.append(index)
            raise


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
