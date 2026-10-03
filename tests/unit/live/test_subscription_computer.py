"""The subscription thinking model operates the screen itself (ADR-0038)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from jarvis.core.http_pool import HttpClientPool
from jarvis.core.protocols import SupervisorToolDescriptor, ToolResult
from jarvis.cu.direct import TOOL_DESCRIPTION, tool_schema
from jarvis.live.config import LiveConfig
from jarvis.live.state import LiveLedger
from jarvis.live.subscription_auth import SubscriptionCredentials
from jarvis.live.subscription_reasoning import SubscriptionReasoning
from jarvis.live.tools import LiveTools
from tests.fakes.fake_subscription_session import (
    ScriptedSubscriptionReasoning,
    SubscriptionConnection,
    SubscriptionEvents,
    completed_response,
    spoken_result,
    subscription_provider,
)

# -- effort: "no thinking" must never 400 a Codex model ----------------------


async def _credentials(*, force_refresh=False):
    return SubscriptionCredentials("fixture-access", "fixture-account")


def _catalog(efforts):
    return {"models": [{"slug": "chosen-model", "supported_reasoning_levels": efforts}]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("advertised", "sent"),
    [(["low", "medium"], "low"), (["none", "low"], "none"), ([], None)],
)
async def test_no_thinking_maps_to_the_models_lightest_level(advertised, sent):
    # Live 2026-10-03: gpt-6.1-sol answered 400 to effort "none" and every
    # computer-use step failed before acting.
    posts = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=_catalog([{"effort": e} for e in advertised]))
        posts.append(json.loads(request.content))
        done = {"type": "response.completed", "response": {"status": "completed"}}
        return httpx.Response(200, text=f"data: {json.dumps(done)}\n\n")

    pool = HttpClientPool(timeout_s=1, transport=httpx.MockTransport(handler))
    reasoning = SubscriptionReasoning(_credentials, http_pool=pool)
    _ = [
        event
        async for event in reasoning.stream(
            model="chosen-model", input=[], instructions="x", tools=[], reasoning_effort="none"
        )
    ]
    assert posts[0]["reasoning"].get("effort") == sent
    await reasoning.aclose()


# -- the delegation loop -------------------------------------------------------


class ScreenGateway:
    """A computer tool whose every call returns a new screenshot."""

    def __init__(self) -> None:
        self.calls = 0

    def catalog(self):
        return (SupervisorToolDescriptor("computer", TOOL_DESCRIPTION, tool_schema(), "monitor"),)

    async def execute(self, name, args, request):
        self.calls += 1
        image = {"mime": "image/jpeg", "data": f"frame{self.calls}"}
        return ToolResult(True, {"executed": ["pressed tab"], "_image": image})

    async def cancel_pending(self, *_args, **_kwargs):
        return True


def _computer_call(index: int) -> dict:
    return {
        "id": f"item-{index}",
        "type": "function_call",
        "call_id": f"call-{index}",
        "name": "computer",
        "arguments": json.dumps({"steps": [{"action": "key", "keys": "tab"}]}),
    }


def _session(monkeypatch, tmp_path, rounds):
    import jarvis.live.subscription as module

    reasoning = ScriptedSubscriptionReasoning(rounds)
    monkeypatch.setattr(module, "SubscriptionReasoning", lambda **kwargs: reasoning)
    config = SimpleNamespace(
        live=LiveConfig(
            configured=True,
            auth_mode="chatgpt_subscription",
            subscription_backend_model="subscription-model",
        ),
        brain=SimpleNamespace(reply_language="en"),
        computer_use=SimpleNamespace(max_steps=20, mission_timeout_s=600.0),
    )

    async def send(_value):
        return None

    session = module.SubscriptionLiveVoiceSession(
        session_id="computer-session",
        providers=[subscription_provider()],
        config=config,
        send_binary=send,
        send_json=send,
        bus=SubscriptionEvents(),
    )
    ledger = LiveLedger(tmp_path / "live.sqlite3")
    gateway = ScreenGateway()
    session._ledger = ledger
    session._connection = SubscriptionConnection()
    session._tools = LiveTools(
        gateway,
        ledger,
        session.session_id,
        language="en",
        backend_model="subscription-model",
        model_selection=session._tool_model_selection,
    )
    session._tools.user_text = "Open Chrome and the latest post."
    return session, reasoning, gateway, ledger


@pytest.mark.asyncio
async def test_a_screen_task_may_take_more_rounds_and_keeps_only_recent_screenshots(
    monkeypatch, tmp_path
):
    from jarvis.live import subscription

    steps = subscription._MAX_ROUNDS + 6  # more than an ordinary request may use
    rounds = [completed_response(f"r{i}", [_computer_call(i)]) for i in range(steps)]
    rounds.append(completed_response("done", [spoken_result("Chrome shows the post.")]))
    session, reasoning, gateway, ledger = _session(monkeypatch, tmp_path, rounds)
    try:
        await session._delegate("d1", "Open Chrome and the latest post.")
    finally:
        session._cancel_report_timeout()
        ledger.close()
    assert gateway.calls == steps
    assert session._connection.sent[-1]["content"] == "Chrome shows the post."
    last = reasoning.requests[-1]
    assert [t["name"] for t in last["tools"] if t["name"] == "computer"] == ["computer"]
    images = [
        part["image_url"]
        for item in last["input"]
        if isinstance(item.get("content"), list)
        for part in item["content"]
        if part.get("type") == "input_image"
    ]
    assert images == [f"data:image/jpeg;base64,frame{n}" for n in range(steps - 2, steps + 1)]
    omitted = json.dumps(last["input"]).count(subscription._OMITTED_SCREENSHOT)
    assert omitted == steps - subscription._KEPT_SCREENSHOTS


@pytest.mark.asyncio
async def test_ordinary_requests_keep_the_short_round_budget(monkeypatch, tmp_path):
    from jarvis.live import subscription

    read = {
        "id": "item",
        "type": "function_call",
        "call_id": "c",
        "name": "discover_tools",
        "arguments": json.dumps({"query": "x"}),
    }
    rounds = [
        completed_response(f"r{i}", [{**read, "call_id": f"c{i}", "id": f"i{i}"}])
        for i in range(subscription._MAX_ROUNDS + 2)
    ]
    session, reasoning, _, ledger = _session(monkeypatch, tmp_path, rounds)
    try:
        await session._delegate("d1", "Find something.")
    finally:
        session._cancel_report_timeout()
        ledger.close()
    assert len(reasoning.requests) == subscription._MAX_ROUNDS
