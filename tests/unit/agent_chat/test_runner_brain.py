"""The Jarvis surface's brain runner — a whole typed turn through the service.

A fake BrainManager (tests/fakes/fake_brain_manager.py) stands in for the
harness; everything else is real: the store, the service, the event log, the
app bus, the step mirror and the approval bridge.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from jarvis.agent_chat import runner_brain
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.core.bus import EventBus
from tests.fakes.fake_brain_manager import FakeBrainManager


async def _drain(q: asyncio.Queue, until_kind: str, wait_s: float = 5.0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async with asyncio.timeout(wait_s):
        while True:
            ev = await q.get()
            out.append(ev)
            if ev["kind"] == until_kind:
                return out


def _service(bus: EventBus | None) -> AgentChatService:
    return AgentChatService(
        AgentChatStore(":memory:"), assistant_name=lambda: "Testo", bus=lambda: bus
    )


def _jarvis_session(svc: AgentChatService, tmp_path: Path, **over: Any):
    kwargs: dict[str, Any] = {
        "provider": "openai",
        "model": "gpt-x",
        "effort": "high",
        "cwd": str(tmp_path),
        "permission_mode": "ask",
        "surface": "jarvis",
    }
    kwargs.update(over)
    return svc.create_session(**kwargs)


@pytest.fixture
def no_cli(monkeypatch: pytest.MonkeyPatch):
    from jarvis.agent_chat import service as svc_mod

    monkeypatch.setattr(svc_mod, "_claude_cli_installed", lambda: False)


async def test_a_jarvis_turn_runs_on_the_brain_with_the_sessions_pick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_cli
):
    bus = EventBus()
    fake = FakeBrainManager(bus=bus, tool=("open-app", {"name": "Notes"}))
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: fake)
    monkeypatch.setattr(runner_brain, "_agent_secret", lambda _r, _p: "k-agent")
    svc = _service(bus)
    session = _jarvis_session(svc, tmp_path)
    q = svc.subscribe(session.session_id)

    turn_id = await svc.send(session.session_id, "Open my notes please")
    events = await _drain(q, "turn_finished")
    kinds = [e["kind"] for e in events]

    started = next(e for e in events if e["kind"] == "turn_started")["payload"]
    assert started["runner"] == "brain" and started["surface"] == "jarvis"
    assert "reasoning_started" in kinds
    assert [e["payload"]["text"] for e in events if e["kind"] == "text_delta"] == [
        "Hello ",
        "from ",
        "Jarvis",
    ], "deltas are chunks, not the text so far"
    assert "reasoning_delta" in kinds and "tool_call" in kinds and "tool_result" in kinds
    call = next(e for e in events if e["kind"] == "tool_call")["payload"]
    result = next(e for e in events if e["kind"] == "tool_result")["payload"]
    assert call["name"] == "open-app" and call["input"] == {"name": "Notes"}
    assert result["call_id"] == call["call_id"] and result["output"] == "looked"
    final = next(e for e in events if e["kind"] == "assistant_text")["payload"]
    assert final["text"] == "Hello from Jarvis"
    finished = events[-1]["payload"]
    assert finished["turn_id"] == turn_id and finished["status"] == "done"
    assert finished["usage"] == {"input_tokens": 12, "output_tokens": 4, "cost_usd": 0.002}

    # The one generate call carried the pick — and nothing moved the live brain.
    text, kw = fake.calls[0]
    assert text == "Open my notes please"
    override = kw["turn_override"]
    assert override.provider == "openai" and override.model == "gpt-x"
    assert override.reasoning_effort == "high"
    assert kw["use_history"] is False and kw["publish_response"] is False
    assert kw["allow_voice_confirm"] is False and kw["emit_tool_ack"] is False
    assert kw["source_layer"] == runner_brain.SOURCE_LAYER
    assert kw["conversation_id"] == session.session_id
    assert set(override.tools_extra) == {
        "Read",
        "Write",
        "Edit",
        "Ls",
        "Glob",
        "Grep",
        "RunCommand",
    }
    assert override.tool_context["approval_surface"] == "interactive"
    assert override.tool_context["approval_ref"] == f"agent-chat:{session.session_id}"
    assert override.tool_context["cwd"] == str(tmp_path)
    assert override.tool_context["delivery"] == "written"
    assert fake.seen_key == "k-agent", "the Agents-tab key was the turn's credential"
    assert not svc.is_running(session.session_id)


async def test_history_is_the_sessions_own_log_in_prose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_cli
):
    fake = FakeBrainManager(reply="second")
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: fake)
    svc = _service(None)
    session = _jarvis_session(svc, tmp_path)
    sid = session.session_id
    svc.store.append_event(sid, make_event("user_message", {"text": "first question"}))
    svc.store.append_event(sid, make_event("turn_started", {"turn_id": "t0"}))
    svc.store.append_event(
        sid,
        make_event(
            "tool_call",
            {"turn_id": "t0", "call_id": "c1", "name": "Read", "input": {"file_path": "a.py"}},
        ),
    )
    svc.store.append_event(
        sid,
        make_event(
            "tool_result",
            {"turn_id": "t0", "call_id": "c1", "output": "print(1)", "is_error": False},
        ),
    )
    svc.store.append_event(
        sid, make_event("assistant_text", {"turn_id": "t0", "text": "It prints 1."})
    )
    svc.store.append_event(sid, make_event("turn_finished", {"turn_id": "t0", "status": "done"}))
    q = svc.subscribe(sid)

    await svc.send(sid, "and now?")
    await _drain(q, "turn_finished")

    _, kw = fake.calls[0]
    history = kw["history_override"]
    assert [m.role for m in history] == ["user", "assistant"]
    assert history[0].content == "first question"
    assert "[used tool Read: a.py]" in history[1].content and "It prints 1." in history[1].content
    assert all(isinstance(m.content, str) for m in history), "prose only, no foreign tool ids"


async def test_plan_stance_offers_reading_hands_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_cli
):
    fake = FakeBrainManager()
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: fake)
    svc = _service(None)
    session = _jarvis_session(svc, tmp_path, permission_mode="plan")
    q = svc.subscribe(session.session_id)
    await svc.send(session.session_id, "what is in here?")
    await _drain(q, "turn_finished")
    override = fake.calls[0][1]["turn_override"]
    assert set(override.tools_extra) == {"Read", "Ls", "Glob", "Grep"}
    assert override.tool_filter is not None


async def test_cancel_ends_a_running_brain_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_cli
):
    hold = asyncio.Event()
    fake = FakeBrainManager(hold=hold)
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: fake)
    svc = _service(None)
    session = _jarvis_session(svc, tmp_path)
    q = svc.subscribe(session.session_id)
    await svc.send(session.session_id, "take your time")
    await _drain(q, "reasoning_started")
    assert await svc.cancel(session.session_id)
    events = await _drain(q, "turn_finished")
    assert events[-1]["payload"]["status"] == "cancelled"
    assert not svc.is_running(session.session_id)


async def test_a_brain_error_ends_the_turn_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_cli
):
    fake = FakeBrainManager(fail=RuntimeError("provider down"))
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: fake)
    svc = _service(None)
    session = _jarvis_session(svc, tmp_path)
    q = svc.subscribe(session.session_id)
    await svc.send(session.session_id, "hi")
    events = await _drain(q, "turn_finished")
    finished = events[-1]["payload"]
    assert finished["status"] == "error" and "provider down" in (finished["error"] or "")


async def test_no_brain_yet_is_a_clear_error_not_a_hang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_cli
):
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: None)
    svc = _service(None)
    session = _jarvis_session(svc, tmp_path)
    q = svc.subscribe(session.session_id)
    await svc.send(session.session_id, "hi")
    events = await _drain(q, "turn_finished")
    assert events[-1]["payload"]["status"] == "error"
    assert "starting up" in events[-1]["payload"]["error"]


async def test_the_agent_surface_still_runs_the_api_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_cli
):
    """A coding-agent session never reaches the brain runner."""
    fake = FakeBrainManager()
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: fake)
    svc = _service(None)
    session = svc.create_session(
        provider="openai", model="m", effort="high", cwd=str(tmp_path), surface="agent"
    )
    q = svc.subscribe(session.session_id)
    await svc.send(session.session_id, "hi")
    events = await _drain(q, "turn_finished")
    started = next(e for e in events if e["kind"] == "turn_started")["payload"]
    assert started["runner"] == "api" and started["surface"] == "agent"
    assert fake.calls == []


def test_step_mirror_ignores_another_turns_events():
    async def scenario() -> None:
        import uuid

        from jarvis.core.events import ActionExecuted, ActionProposed

        bus = EventBus()
        mine, theirs = uuid.uuid4(), uuid.uuid4()
        rows: list[tuple[str, dict[str, Any]]] = []

        async def emit(kind: str, payload: dict[str, Any]) -> None:
            rows.append((kind, payload))

        mirror = runner_brain._StepMirror(emit, "turn-1", bus, mine)
        mirror.start()
        await bus.publish(ActionProposed(trace_id=theirs, tool_name="voice-tool", args={}))
        await bus.publish(ActionProposed(trace_id=mine, tool_name="Read", args={"file_path": "x"}))
        assert mirror.open_call_id("Read") and not mirror.open_call_id("voice-tool")
        await bus.publish(ActionExecuted(trace_id=theirs, tool_name="voice-tool", success=True))
        await bus.publish(ActionExecuted(trace_id=mine, tool_name="Read", success=True))
        mirror.stop()
        assert [k for k, _ in rows] == ["tool_call", "tool_result"]
        assert rows[0][1]["name"] == "Read"

    asyncio.run(scenario())
