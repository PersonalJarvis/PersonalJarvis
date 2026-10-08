"""A restored runtime must acknowledge the current model before it can bill a turn."""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.agent_runtimes.acp import AcpTurn


class FakeIO:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    async def write(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    async def ask(self, *args: Any) -> str:
        raise AssertionError("Model selection does not need a tool approval")


async def _opened(*, resume: str | None = None, model: str = "claude-native:sonnet"):
    turn = AcpTurn("turn", "/workspace", "next user message", resume=resume, model_id=model)
    io = FakeIO()
    await turn.on_message({"id": 1, "result": {
        "protocolVersion": 1, "agentCapabilities": {"loadSession": True},
    }}, io)
    await turn.on_message({"id": 2, "result": {
        "sessionId": resume or "new-session",
        "models": {"currentModelId": "custom:jarvis:old-model"},
    }}, io)
    return turn, io


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [None, "existing-session"])
async def test_model_is_selected_after_open_and_before_prompt(resume):
    turn, io = await _opened(resume=resume)
    assert [frame["method"] for frame in io.frames] == [
        "session/load" if resume else "session/new", "session/set_model",
    ]
    switch = io.frames[-1]
    assert switch["params"] == {
        "sessionId": resume or "new-session", "modelId": "claude-native:sonnet",
    }
    await turn.on_message({"id": switch["id"], "result": {}}, io)
    assert io.frames[-1]["method"] == "session/prompt"
    assert io.frames[-1]["params"]["prompt"] == [{"type": "text", "text": "next user message"}]
    assert turn.vendor_session == resume or turn.vendor_session == "new-session"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    {"code": -32601, "message": "Method not found"},
    {"code": -32602, "message": "Model unavailable"},
])
async def test_model_rejection_is_terminal_without_prompt_or_old_route(error):
    turn, io = await _opened(resume="existing-session")
    await turn.on_message({"id": io.frames[-1]["id"], "error": error}, io)
    assert turn.status == "error" and turn.saw_result
    assert "could not select" in (turn.error or "")
    assert "session/prompt" not in [frame["method"] for frame in io.frames]


@pytest.mark.asyncio
async def test_missing_model_selection_result_is_not_success():
    turn, io = await _opened()
    await turn.on_message({"id": io.frames[-1]["id"], "result": None}, io)
    assert turn.status == "error"
    assert io.frames[-1]["method"] == "session/set_model"


@pytest.mark.asyncio
async def test_model_restore_notifications_do_not_duplicate_conversation():
    turn, io = await _opened(resume="existing-session")
    await turn.on_message({
        "method": "session/update", "params": {
            "sessionId": "existing-session", "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "old assistant response"},
            },
        },
    }, io)
    assert io.events == []
    await turn.on_message({"id": io.frames[-1]["id"], "result": {}}, io)
    assert turn.vendor_session == "existing-session"


@pytest.mark.asyncio
async def test_runtime_without_model_selection_keeps_existing_handshake():
    _, io = await _opened(model="")
    assert [frame["method"] for frame in io.frames] == ["session/new", "session/prompt"]
