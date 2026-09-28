"""An agent's question card: a short series, the picks, skip, and the 5-minute fallback."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.agent_chat.questions import (
    MAX_ASKS_PER_TURN,
    MAX_QUESTIONS,
    TooManyQuestions,
    answer_from,
    parse_questions,
)
from jarvis.agent_chat.service import AgentChatService, _Running
from jarvis.agent_chat.store import AgentChatStore
from jarvis.society.ask_tool import ASK_USER_TOOL_NAME, AskUserTool

_DB: dict[str, Any] = {
    "question": "Which database should the project use?",
    "options": [
        {"label": "SQLite", "description": "Zero setup"},
        {"label": "Postgres", "description": "Scales further"},
    ],
    "recommendation_reason": "No server to run.",
}
_HOST: dict[str, Any] = {
    "question": "Where should it run?",
    "options": [{"label": "Laptop"}, {"label": "VPS"}, {"label": "Both"}],
    "recommendation_reason": "Nothing to pay for.",
}
_ARGS: dict[str, Any] = {"questions": [_DB, _HOST]}


def test_parse_moves_the_recommendation_first_and_rejects_bad_shapes() -> None:
    (spec,) = parse_questions({**_DB, "recommended": 1})
    assert [o.label for o in spec.options] == ["Postgres", "SQLite"]
    assert len(parse_questions(_ARGS)) == 2
    bad = [
        {"questions": []},
        {"questions": [_DB] * 2},
        {"questions": [{**_DB, "question": f"q{i}?"} for i in range(MAX_QUESTIONS + 1)]},
        {**_DB, "options": [{"label": "only one"}]},
        {**_DB, "options": [{"label": str(i)} for i in range(5)]},
        {**_DB, "question": " "},
        {**_DB, "recommended": 7},
    ]
    for args in bad:
        with pytest.raises(ValueError):
            parse_questions(args)


def test_answer_needs_exactly_one_of_option_or_text() -> None:
    (spec,) = parse_questions(_DB)
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


def test_the_person_answers_the_series_one_by_one(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc, sid, task = await _service_with_turn(tmp_path)
        q = svc.subscribe(sid)
        asking = asyncio.create_task(svc.ask_questions(sid, parse_questions(_ARGS), asker="Ada"))
        card = await _next(q, "question_required")
        qid = card["question_id"]
        assert card["asker"] == "Ada" and len(card["questions"]) == 2
        assert svc.pending_questions(sid) == [qid]
        assert not svc.resolve_question("other", qid, index=0, option_index=1)
        assert svc.resolve_question(sid, qid, index=0, option_index=1)
        assert not svc.resolve_question(sid, qid, index=0, option_index=0)  # already answered
        progress = await _next(q, "question_progress")
        assert progress["answers"][0]["answer"] == "Postgres" and progress["answers"][1] is None
        assert svc.resolve_question(sid, qid, index=1, text="A Raspberry Pi")
        answers = await asking
        assert [(a.answer, a.source) for a in answers] == [
            ("Postgres", "person"),
            ("A Raspberry Pi", "person"),
        ]
        resolved = await _next(q, "question_resolved")
        assert [a["answer"] for a in resolved["answers"]] == ["Postgres", "A Raspberry Pi"]
        assert svc.pending_questions(sid) == []
        task.cancel()

    asyncio.run(scenario())


def test_silence_applies_the_recommendations_to_open_questions(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc, sid, task = await _service_with_turn(tmp_path)
        q = svc.subscribe(sid)
        asking = asyncio.create_task(svc.ask_questions(sid, parse_questions(_ARGS), timeout_s=0.2))
        card = await _next(q, "question_required")
        svc.resolve_question(sid, card["question_id"], index=0, option_index=1)
        answers = await asking
        assert [(a.answer, a.source) for a in answers] == [
            ("Postgres", "person"),
            ("Laptop", "timeout"),
        ]
        task.cancel()

    asyncio.run(scenario())


def test_closing_the_card_lets_the_agent_decide(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc, sid, task = await _service_with_turn(tmp_path)
        q = svc.subscribe(sid)
        asking = asyncio.create_task(svc.ask_questions(sid, parse_questions(_ARGS)))
        card = await _next(q, "question_required")
        assert svc.skip_question(sid, card["question_id"])
        answers = await asking
        assert [(a.answer, a.source) for a in answers] == [
            ("SQLite", "skipped"),
            ("Laptop", "skipped"),
        ]
        task.cancel()

    asyncio.run(scenario())


def test_cancelling_the_turn_closes_the_card(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc, sid, task = await _service_with_turn(tmp_path)
        q = svc.subscribe(sid)
        asking = asyncio.create_task(svc.ask_questions(sid, parse_questions(_DB)))
        await _next(q, "question_required")
        assert svc.signal_cancel(sid)
        (answer,) = await asking
        assert answer.source == "cancelled"
        task.cancel()

    asyncio.run(scenario())


def test_a_turn_runs_out_of_cards(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc, sid, task = await _service_with_turn(tmp_path)
        for _ in range(MAX_ASKS_PER_TURN):
            await svc.ask_questions(sid, parse_questions(_DB), timeout_s=0.01)
        with pytest.raises(TooManyQuestions):
            await svc.ask_questions(sid, parse_questions(_DB))
        task.cancel()

    asyncio.run(scenario())


def test_asking_without_a_running_turn_raises(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc = AgentChatService(AgentChatStore(":memory:"))
        session = svc.store.create_session(
            provider="fakeprov", model="m", effort="high", cwd=str(tmp_path)
        )
        with pytest.raises(RuntimeError):
            await svc.ask_questions(session.session_id, parse_questions(_DB))

    asyncio.run(scenario())


class _FakeService:
    def __init__(self, *, limit: bool = False) -> None:
        self.asked: list[tuple[str, str]] = []
        self.limit = limit

    async def ask_questions(self, session_id: str, specs: Any, *, asker: str = "") -> Any:
        if self.limit:
            raise TooManyQuestions("used up")
        self.asked.append((session_id, asker))
        return [answer_from(spec, option_index=1, text=None) for spec in specs]


def _tool(session_id: str, service: _FakeService) -> AskUserTool:
    runtime = SimpleNamespace(
        chat_service=lambda: service, cached_agent=lambda _id: SimpleNamespace(name="Ada")
    )
    return AskUserTool(runtime, "ada", session_id=session_id)


def test_tool_returns_the_persons_answers() -> None:
    service = _FakeService()
    result = asyncio.run(_tool("society:ada", service).execute(dict(_ARGS), None))
    assert result.success
    assert [r["answer"] for r in result.output["answers"]] == ["Postgres", "VPS"]
    assert result.output["answers"][0]["recommended"] is False
    assert service.asked == [("society:ada", "Ada")]


def test_a_routine_never_waits_for_the_user() -> None:
    service = _FakeService()
    tool = _tool("society:ada:routine:t1:run1", service)
    result = asyncio.run(tool.execute(dict(_ARGS), None))
    assert result.success
    assert [r["answer"] for r in result.output["answers"]] == ["SQLite", "Laptop"]
    assert {r["source"] for r in result.output["answers"]} == {"unattended"}
    assert "Routines" in result.output["note"]
    assert service.asked == []


def test_the_per_turn_limit_hands_back_the_recommendations() -> None:
    result = asyncio.run(_tool("society:ada", _FakeService(limit=True)).execute(dict(_DB), None))
    assert result.success and result.output["answers"][0]["answer"] == "SQLite"
    assert "already asked" in result.output["note"]


def test_tool_rejects_an_invalid_question() -> None:
    result = asyncio.run(
        _tool("society:ada", _FakeService()).execute({"question": "?", "options": []}, None)
    )
    assert not result.success and "invalid questions" in (result.error or "")


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
