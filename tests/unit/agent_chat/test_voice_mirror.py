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
