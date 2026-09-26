"""Question choice, timeout, continuation and restart contract on every OS."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.ui.web.agent_chat_routes import router


def _session(store: AgentChatStore, session_id: str = "society:scout") -> None:
    store.create_session(
        provider="openai",
        model="",
        effort="",
        cwd=".",
        session_id=session_id,
        surface="society",
    )


async def _sent(sends: list[dict[str, Any]]) -> dict[str, Any]:
    for _ in range(100):
        if sends:
            return sends[0]
        await asyncio.sleep(0.01)
    raise AssertionError("the agent did not continue")


@pytest.fixture
def store(tmp_path: Path):
    result = AgentChatStore(tmp_path / "questions.db")
    yield result
    result.close()


@pytest.mark.asyncio
async def test_timeout_uses_recommendation_and_starts_followup(
    store: AgentChatStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("jarvis.agent_chat.questions.QUESTION_TIMEOUT_S", 0.03)
    _session(store)
    service = AgentChatService(store)
    if service.questions._recovering is not None:
        await service.questions._recovering
    sends: list[dict[str, Any]] = []

    async def send(session_id: str, text: str, *args: Any, **kwargs: Any) -> str:
        sends.append({"session_id": session_id, "text": text, **kwargs})
        return "turn2"

    monkeypatch.setattr(service, "send", send)
    question_id = await service.questions.create(
        "society:scout",
        agent_name="Scout",
        question="Which format?",
        options=[
            {"label": "PDF", "description": "Easy to share"},
            {"label": "HTML", "description": "Easy to update"},
        ],
        recommended_index=1,
        recommendation_reason="The user will edit it again.",
    )
    sent = await _sent(sends)
    assert "HTML" in sent["text"]
    assert sent["question_owned"] is True
    assert sent["question_id"] == question_id
    events = store.list_events("society:scout")
    notices = [e["payload"] for e in events]
    assert 0 <= notices[0]["deadline_ms"] - events[0]["ts_ms"] <= 40
    assert notices[1]["kind"] == "question_resolved"
    assert notices[1]["source"] == "timeout"
    service.questions.close()


@pytest.mark.asyncio
async def test_user_answer_wins_and_wrong_session_cannot_answer(
    store: AgentChatStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session(store)
    _session(store, "society:other")
    service = AgentChatService(store)
    if service.questions._recovering is not None:
        await service.questions._recovering
    sends: list[dict[str, Any]] = []

    async def send(session_id: str, text: str, *args: Any, **kwargs: Any) -> str:
        sends.append({"session_id": session_id, "text": text, **kwargs})
        return "turn2"

    monkeypatch.setattr(service, "send", send)
    question_id = await service.questions.create(
        "society:scout",
        agent_name="Scout",
        question="Which format?",
        options=[
            {"label": "PDF", "description": "Easy to share"},
            {"label": "HTML", "description": "Easy to update"},
        ],
        recommended_index=1,
        recommendation_reason="Editable.",
    )
    assert await service.questions.resolve("society:other", question_id, selected_index=0) is None
    with pytest.raises(ValueError, match="Choose one"):
        await service.questions.resolve("society:scout", question_id)
    await service.questions.resolve("society:scout", question_id, custom_text="Markdown")
    sent = await _sent(sends)
    assert "Markdown" in sent["text"]
    assert "the user's answer" in sent["text"]
    assert await service.questions.resolve("society:scout", question_id, selected_index=1) is None
    with pytest.raises(ValueError, match="direct chat"):
        await service.questions.create(
            "society:scout:routine:r1:run1",
            agent_name="Scout",
            question="Which format?",
            options=[],
            recommended_index=0,
            recommendation_reason="Reason",
        )
    service.questions.close()


@pytest.mark.asyncio
async def test_restart_recovers_unanswered_question(
    store: AgentChatStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session(store)
    first = AgentChatService(store)
    if first.questions._recovering is not None:
        await first.questions._recovering
    question_id = await first.questions.create(
        "society:scout",
        agent_name="Scout",
        question="Which format?",
        options=[
            {"label": "PDF", "description": "Easy to share"},
            {"label": "HTML", "description": "Easy to update"},
        ],
        recommended_index=1,
        recommendation_reason="Editable.",
    )
    first.questions.close()
    second = AgentChatService(store)
    sends: list[dict[str, Any]] = []

    async def send(session_id: str, text: str, *args: Any, **kwargs: Any) -> str:
        sends.append({"session_id": session_id, "text": text, **kwargs})
        return "turn2"

    monkeypatch.setattr(second, "send", send)
    assert await second.questions.resolve("society:scout", question_id, selected_index=0)
    assert "PDF" in (await _sent(sends))["text"]
    second.questions.close()


@pytest.mark.asyncio
async def test_answer_route_accepts_one_choice_and_rejects_late_duplicate(
    store: AgentChatStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session(store)
    service = AgentChatService(store)
    if service.questions._recovering is not None:
        await service.questions._recovering
    sends: list[dict[str, Any]] = []

    async def send(session_id: str, text: str, *args: Any, **kwargs: Any) -> str:
        sends.append({"session_id": session_id, "text": text, **kwargs})
        return "turn2"

    monkeypatch.setattr(service, "send", send)
    question_id = await service.questions.create(
        "society:scout",
        agent_name="Scout",
        question="Which format?",
        options=[
            {"label": "PDF", "description": "Easy to share"},
            {"label": "HTML", "description": "Easy to update"},
        ],
        recommended_index=1,
        recommendation_reason="Editable.",
    )
    app = FastAPI()
    app.state.agent_chat = service
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        url = f"/api/agent-chat/sessions/society:scout/questions/{question_id}"
        assert (await client.post(url, json={})).status_code == 400
        answer = await client.post(url, json={"selected_index": 0})
        assert answer.status_code == 200
        assert answer.json()["answer"] == "PDF"
        assert (await client.post(url, json={"selected_index": 1})).status_code == 404
    assert "PDF" in (await _sent(sends))["text"]
    service.questions.close()


@pytest.mark.asyncio
async def test_resolved_question_continues_after_restart(
    store: AgentChatStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session(store)
    first = AgentChatService(store)
    if first.questions._recovering is not None:
        await first.questions._recovering
    question_id = await first.questions.create(
        "society:scout",
        agent_name="Scout",
        question="Which format?",
        options=[
            {"label": "PDF", "description": "Easy to share"},
            {"label": "HTML", "description": "Easy to update"},
        ],
        recommended_index=1,
        recommendation_reason="Editable.",
    )
    blocked = asyncio.Event()

    async def wait_turn(_session_id: str) -> None:
        await blocked.wait()

    monkeypatch.setattr(first, "wait_turn", wait_turn)
    await first.questions.resolve("society:scout", question_id, selected_index=0)
    first.questions.close()
    second = AgentChatService(store)
    sends: list[dict[str, Any]] = []

    async def send(session_id: str, text: str, *args: Any, **kwargs: Any) -> str:
        sends.append({"session_id": session_id, "text": text, **kwargs})
        return "turn2"

    monkeypatch.setattr(second, "send", send)
    assert "PDF" in (await _sent(sends))["text"]
    second.questions.close()
