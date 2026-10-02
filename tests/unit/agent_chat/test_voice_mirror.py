"""Voice turns appear in the Jarvis agent chat without being re-answered."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.agent_chat.voice_mirror import VoiceChatMirror
from jarvis.core.bus import EventBus
from jarvis.core.events import VoiceTurnCompleted


def _service(cwd: Path) -> AgentChatService:
    store = AgentChatStore(":memory:")
    return AgentChatService(store, assistant_name=lambda: "Jarvis", default_cwd=lambda: str(cwd))


def _open_session(svc: AgentChatService):
    return svc.create_session(provider="openai", model="m", effort="low", surface="jarvis")


def test_import_voice_turn_writes_user_and_finished_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc = _service(tmp_path)
        session = _open_session(svc)
        q = svc.subscribe(session.session_id)
        turn_id = await svc.import_voice_turn(
            session.session_id,
            "What did I say out loud?",
            "You said this out loud.",
            voice_turn_id="v1",
        )
        assert turn_id
        kinds = []
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=5.0)
            kinds.append(ev["kind"])
            if ev["kind"] == "turn_finished":
                break
        assert kinds[0] == "user_message"
        assert "turn_started" in kinds
        assert "assistant_text" in kinds
        stored = svc.store.list_events(session.session_id)
        by_kind = [e["kind"] for e in stored]
        assert by_kind == ["user_message", "turn_started", "assistant_text", "turn_finished"]
        assert stored[0]["payload"]["text"] == "What did I say out loud?"
        assert stored[2]["payload"]["text"] == "You said this out loud."
        assert stored[1]["payload"]["runner"] == "voice"
        assert stored[-1]["payload"]["status"] == "done"

    asyncio.run(scenario())


def test_import_voice_turn_dedupes_and_skips_empties(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc = _service(tmp_path)
        session = _open_session(svc)
        first = await svc.import_voice_turn(session.session_id, "hi", "hello", voice_turn_id="dup")
        second = await svc.import_voice_turn(session.session_id, "hi", "hello", voice_turn_id="dup")
        assert first
        assert second is None
        assert await svc.import_voice_turn(session.session_id, "", "  ") is None
        assert len(svc.store.list_events(session.session_id)) == 4

    asyncio.run(scenario())


def test_import_voice_turn_keeps_user_line_without_reply(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc = _service(tmp_path)
        session = _open_session(svc)
        assert await svc.import_voice_turn(session.session_id, "only heard", "") is None
        stored = svc.store.list_events(session.session_id)
        assert [e["kind"] for e in stored] == ["user_message"]

    asyncio.run(scenario())


def test_mirror_files_completed_turn_into_newest_jarvis_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc = _service(tmp_path)
        session = _open_session(svc)
        bus = EventBus()
        VoiceChatMirror(lambda: svc).attach(bus)
        await bus.publish(
            VoiceTurnCompleted(
                session_id="voice-1",
                turn_id="vt-1",
                user_text="spoken question",
                jarvis_text="spoken answer",
                provider="openai",
                model="m",
            )
        )
        stored = svc.store.list_events(session.session_id)
        assert [e["kind"] for e in stored] == [
            "user_message",
            "turn_started",
            "assistant_text",
            "turn_finished",
        ]
        # A redelivery of the same voice turn must not duplicate the chat.
        await bus.publish(
            VoiceTurnCompleted(
                session_id="voice-1",
                turn_id="vt-1",
                user_text="spoken question",
                jarvis_text="spoken answer",
            )
        )
        assert len(svc.store.list_events(session.session_id)) == 4

    asyncio.run(scenario())


def test_mirror_creates_first_session_when_none_open(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc = _service(tmp_path)
        bus = EventBus()
        VoiceChatMirror(lambda: svc).attach(bus)
        await bus.publish(
            VoiceTurnCompleted(
                session_id="voice-1",
                turn_id="vt-9",
                user_text="first words ever",
                jarvis_text="first answer ever",
                provider="openai",
            )
        )
        sessions: list[Any] = svc.store.list_sessions(limit=10, surface="jarvis")
        assert len(sessions) == 1
        stored = svc.store.list_events(sessions[0].session_id)
        assert stored[0]["payload"]["text"] == "first words ever"

    asyncio.run(scenario())


def _turn(call: str, turn: str, user: str, reply: str) -> VoiceTurnCompleted:
    return VoiceTurnCompleted(
        session_id=call, turn_id=turn, user_text=user, jarvis_text=reply, provider="openai"
    )


def test_mirror_files_into_the_bound_chat_not_the_newest(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc = _service(tmp_path)
        a = _open_session(svc)
        b = _open_session(svc)
        newest = svc.store.list_sessions(limit=1, surface="jarvis")[0]
        older = a if newest.session_id == b.session_id else b
        svc.bind_voice_chat(older.session_id)
        bus = EventBus()
        VoiceChatMirror(lambda: svc).attach(bus)
        await bus.publish(_turn("call-1", "t1", "continue here", "continuing"))
        assert len(svc.store.list_events(older.session_id)) == 4
        assert svc.store.list_events(newest.session_id) == []

    asyncio.run(scenario())


def test_one_call_stays_in_one_chat_when_another_is_opened(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc = _service(tmp_path)
        first = _open_session(svc)
        second = _open_session(svc)
        svc.bind_voice_chat(first.session_id)
        bus = EventBus()
        VoiceChatMirror(lambda: svc).attach(bus)
        await bus.publish(_turn("call-1", "t1", "one", "uno"))
        svc.bind_voice_chat(second.session_id)
        await bus.publish(_turn("call-1", "t2", "two", "dos"))
        assert len(svc.store.list_events(first.session_id)) == 8
        assert svc.store.list_events(second.session_id) == []
        # The NEXT call follows the new binding.
        await bus.publish(_turn("call-2", "t3", "three", "tres"))
        assert len(svc.store.list_events(second.session_id)) == 4

    asyncio.run(scenario())


def test_blank_page_call_opens_a_new_chat_and_keeps_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc = _service(tmp_path)
        old = _open_session(svc)
        svc.bind_voice_chat(None)
        bus = EventBus()
        VoiceChatMirror(lambda: svc).attach(bus)
        await bus.publish(_turn("call-1", "t1", "fresh start", "fresh answer"))
        assert svc.store.list_events(old.session_id) == []
        created = svc.voice_chat_id
        assert created and created != old.session_id
        assert not svc.voice_chat_fresh
        # A second call from the same page continues the chat the first opened.
        await bus.publish(_turn("call-2", "t2", "again", "again answer"))
        assert len(svc.store.list_events(created)) == 8
        assert len(svc.store.list_sessions(limit=10, surface="jarvis")) == 2

    asyncio.run(scenario())


def test_voice_chat_history_is_the_bound_chats_prose(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc = _service(tmp_path)
        session = _open_session(svc)
        assert svc.voice_chat_history() == []
        await svc.import_voice_turn(session.session_id, "my question", "my answer")
        svc.bind_voice_chat(session.session_id)
        history = svc.voice_chat_history()
        assert [(m.role, m.content) for m in history] == [
            ("user", "my question"),
            ("assistant", "my answer"),
        ]

    asyncio.run(scenario())


def test_binding_rejects_unknown_and_foreign_surface_chats(tmp_path: Path) -> None:
    import pytest

    from jarvis.agent_chat.service import NoSuchSession

    svc = _service(tmp_path)
    coding = svc.create_session(provider="openai", model="m", effort="low", surface="agent")
    with pytest.raises(NoSuchSession):
        svc.bind_voice_chat("missing")
    with pytest.raises(NoSuchSession):
        svc.bind_voice_chat(coding.session_id)


def test_a_chat_opened_by_a_call_keeps_the_persons_chat_model(tmp_path: Path) -> None:
    async def scenario() -> None:
        svc = _service(tmp_path)
        typed = _open_session(svc)
        svc.bind_voice_chat(None)
        bus = EventBus()
        VoiceChatMirror(lambda: svc).attach(bus)
        await bus.publish(
            VoiceTurnCompleted(
                session_id="call-1",
                turn_id="t1",
                user_text="hello",
                jarvis_text="hi",
                provider="openai",
                model="gpt-realtime",
            )
        )
        created = svc.store.get_session(svc.voice_chat_id or "")
        assert created is not None and created.session_id != typed.session_id
        # Back on the keyboard, the chat runs on the seat picked for typing,
        # never on the realtime voice model.
        assert (created.provider, created.model) == (typed.provider, typed.model)

    asyncio.run(scenario())


def test_a_continued_voice_chat_is_not_copied_into_a_typed_chat(tmp_path: Path) -> None:
    from jarvis.sessions.continuation import continue_voice_session, continued_voice_session

    async def scenario() -> None:
        svc = _service(tmp_path)
        typed = _open_session(svc)
        svc.bind_voice_chat(
            None, voice_session="archived", voice_history=lambda: [("user", "old words")]
        )
        assert continued_voice_session() == "archived"
        assert svc.voice_chat_history() == [("user", "old words")]
        bus = EventBus()
        VoiceChatMirror(lambda: svc).attach(bus)
        await bus.publish(_turn("call-1", "t1", "go on", "going on"))
        assert svc.store.list_events(typed.session_id) == []
        assert len(svc.store.list_sessions(limit=10, surface="jarvis")) == 1
        # Putting a typed chat on stage ends the continuation.
        svc.bind_voice_chat(typed.session_id)
        assert continued_voice_session() is None

    try:
        asyncio.run(scenario())
    finally:
        continue_voice_session(None)
