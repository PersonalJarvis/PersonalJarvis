"""An agent's multiple-choice question: the card, the pick, the 5-minute fallback."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.agent_chat.questions import answer_from, parse_question
from jarvis.agent_chat.service import AgentChatService, _Running
from jarvis.agent_chat.store import AgentChatStore
from jarvis.society.ask_tool import ASK_USER_TOOL_NAME, AskUserTool

_ARGS: dict[str, Any] = {
    "question": "Which database should the project use?",
    "header": "Database",
    "options": [
        {"label": "SQLite", "description": "Zero setup"},
        {"label": "Postgres", "description": "Scales further"},
    ],
    "recommendation_reason": "No server to run.",
}


def test_parse_moves_the_recommendation_first_and_rejects_bad_shapes() -> None:
    spec = parse_question({**_ARGS, "recommended": 1})
    assert [o.label for o in spec.options] == ["Postgres", "SQLite"]
    assert spec.recommended.label == "Postgres"
    with pytest.raises(ValueError):
        parse_question({**_ARGS, "options": [{"label": "only one"}]})
    with pytest.raises(ValueError):
        parse_question({**_ARGS, "options": [{"label": str(i)} for i in range(5)]})
    with pytest.raises(ValueError):
        parse_question({**_ARGS, "question": " "})
    with pytest.raises(ValueError):
        parse_question({**_ARGS, "recommended": 7})


def test_answer_needs_exactly_one_of_option_or_text() -> None:
    spec = parse_question(_ARGS)
    assert answer_from(spec, option_index=1, text=None).answer == "Postgres"
    assert answer_from(spec, option_index=None, text=" MySQL ").answer == "MySQL"
    for kwargs in (
        {"option_index": None, "text": ""},
        {"option_index": 0, "text": "x"},
        {"option_index": 9, "text": None},
    ):
        with pytest.raises(ValueError):
            answer_from(spec, **kwargs)


async def _service_with_turn(tmp_path: Path) -> tuple[AgentChatService, str, asyncio.Task[None]]:
    svc = AgentChatService(AgentChatStore(":memory:"), assistant_name=lambda: "Testo")
    session = svc.store.create_session(
        provider="fakeprov", model="m", effort="high", cwd=str(tmp_path)
    )
    run = _Running("turn-1", asyncio.Event())
    run.task = asyncio.create_task(asyncio.sleep(3600))
    svc._running[session.session_id] = run
    return svc, session.session_id, run.task


async def _next(q: asyncio.Queue, kind: str) -> dict[str, Any]:
    while True:
        ev = await asyncio.wait_for(q.get(), timeout=5)
        if ev["kind"] == kind:
            return ev["payload"]


def test_the_person_picks_an_option(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc, sid, task = await _service_with_turn(tmp_path)
        q = svc.subscribe(sid)
        asking = asyncio.create_task(svc.ask_question(sid, parse_question(_ARGS)))
        card = await _next(q, "question_required")
        assert card["turn_id"] == "turn-1" and card["recommended"] == 0
        assert [o["label"] for o in card["options"]] == ["SQLite", "Postgres"]
        assert svc.pending_questions(sid) == [card["question_id"]]
        assert not svc.resolve_question("other-session", card["question_id"], option_index=1)
        assert svc.resolve_question(sid, card["question_id"], option_index=1)
        answer = await asking
        assert (answer.answer, answer.source, answer.auto) == ("Postgres", "person", False)
        resolved = await _next(q, "question_resolved")
        assert resolved["answer"] == "Postgres" and resolved["auto"] is False
        assert svc.pending_questions(sid) == []
        assert not svc.resolve_question(sid, card["question_id"], option_index=0)
        task.cancel()

    asyncio.run(scenario())


def test_no_answer_in_time_picks_the_recommendation(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc, sid, task = await _service_with_turn(tmp_path)
        q = svc.subscribe(sid)
        answer = await svc.ask_question(sid, parse_question(_ARGS), timeout_s=0.05)
        assert (answer.answer, answer.option_index, answer.auto) == ("SQLite", 0, True)
        resolved = await _next(q, "question_resolved")
        assert resolved["source"] == "timeout" and resolved["auto"] is True
        task.cancel()

    asyncio.run(scenario())


def test_cancelling_the_turn_closes_the_question(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc, sid, task = await _service_with_turn(tmp_path)
        q = svc.subscribe(sid)
        asking = asyncio.create_task(svc.ask_question(sid, parse_question(_ARGS)))
        await _next(q, "question_required")
        assert svc.signal_cancel(sid)
        answer = await asking
        assert answer.source == "cancelled"
        task.cancel()

    asyncio.run(scenario())


def test_asking_without_a_running_turn_raises(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc = AgentChatService(AgentChatStore(":memory:"))
        session = svc.store.create_session(
            provider="fakeprov", model="m", effort="high", cwd=str(tmp_path)
        )
        with pytest.raises(RuntimeError):
            await svc.ask_question(session.session_id, parse_question(_ARGS))

    asyncio.run(scenario())


class _FakeService:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def ask_question(self, session_id: str, spec: Any) -> Any:
        self.asked.append(session_id)
        return answer_from(spec, option_index=1, text=None)


def _tool(session_id: str, service: _FakeService) -> AskUserTool:
    runtime = SimpleNamespace(chat_service=lambda: service)
    return AskUserTool(runtime, "ada", session_id=session_id)


def test_tool_returns_the_persons_answer() -> None:
    service = _FakeService()
    result = asyncio.run(_tool("society:ada", service).execute(dict(_ARGS), None))
    assert result.success and result.output["answer"] == "Postgres"
    assert result.output["recommended"] is False
    assert service.asked == ["society:ada"]


def test_a_routine_never_waits_for_the_user() -> None:
    service = _FakeService()
    tool = _tool("society:ada:routine:t1:run1", service)
    result = asyncio.run(tool.execute(dict(_ARGS), None))
    assert result.success and result.output["answer"] == "SQLite"
    assert result.output["source"] == "unattended" and "Routines" in result.output["note"]
    assert service.asked == []


def test_tool_rejects_an_invalid_question() -> None:
    result = asyncio.run(
        _tool("society:ada", _FakeService()).execute({"question": "?", "options": []}, None)
    )
    assert not result.success and "invalid question" in (result.error or "")


def test_routine_chats_are_not_offered_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.society import surface

    browser = SimpleNamespace(is_installed=lambda: False, live=SimpleNamespace(model_resolver=None))
    rt = SimpleNamespace(browser=browser, chat_service=lambda: None)
    monkeypatch.setattr(surface, "current_runtime", lambda: rt)
    canonical = SimpleNamespace(session_id="society:ada", cwd="", permission_mode="")
    routine = SimpleNamespace(
        session_id="society:ada:routine:t1:run1", cwd="", permission_mode="bypass"
    )
    assert ASK_USER_TOOL_NAME in surface.society_tools(None, None, canonical)
    assert ASK_USER_TOOL_NAME not in surface.society_tools(None, None, routine)
