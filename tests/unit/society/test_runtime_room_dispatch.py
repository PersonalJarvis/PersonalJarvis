"""Room turns use the restricted chat contract."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.society import chat_binding
from jarvis.society.runtime import SocietyRuntime


class _FakeStore:
    def incoming_message(self, session_id: str, message_id: str):
        return None


class _FakeService:
    supports_turn_completion = True

    def __init__(self) -> None:
        self.store = _FakeStore()
        self.sent: dict[str, object] = {}
        self.queue = object()

    def is_running(self, session_id: str) -> bool:
        return False

    def subscribe(self, session_id: str):
        return self.queue

    def unsubscribe(self, session_id: str, queue: object) -> None:
        return None

    async def send(self, session_id: str, text: str, *, incoming, direct_user, read_only):
        self.sent = {
            "session_id": session_id,
            "text": text,
            "incoming": incoming,
            "direct_user": direct_user,
            "read_only": read_only,
        }
        return "turn-1"


class _FakeRooms:
    def __init__(self) -> None:
        self.bound: tuple[str, str, str] | None = None

    async def bind_turn(self, room_id: str, claim_id: str, turn_id: str):
        self.bound = (room_id, claim_id, turn_id)


@pytest.mark.asyncio
async def test_room_dispatch_forces_read_only_chat_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = SocietyRuntime.__new__(SocietyRuntime)
    service = _FakeService()
    rooms = _FakeRooms()
    runtime._get_chat = lambda: service
    runtime._get_cfg = lambda: None
    runtime._watchers = set()
    runtime.rooms = rooms

    def fake_ensure_session(svc, cfg, target):
        return SimpleNamespace(session_id="society:scout")

    async def fake_room_prompt(room, target):
        return "bounded prompt"

    async def fake_watch(*_args):
        return None

    monkeypatch.setattr(chat_binding, "ensure_session", fake_ensure_session)
    monkeypatch.setattr(runtime, "_room_prompt", fake_room_prompt)
    monkeypatch.setattr(runtime, "_watch_room_turn", fake_watch)

    target = SimpleNamespace(agent_id="scout", name="Scout", session_id="society:scout")
    room = SimpleNamespace(
        room_id="room-1",
        opened_by="jarvis",
        topic="Discuss the plan.",
        trace_id="room:room-1",
    )

    result = await runtime._dispatch_room_turn(target, room, "claim-1")
    await asyncio.sleep(0)

    assert result == "room:room-1:turn-1"
    assert service.sent["read_only"] is True
    assert service.sent["direct_user"] is False
    incoming = service.sent["incoming"]
    assert incoming.message_id == "claim-1"
    assert incoming.trace_id == "room:room-1"
    assert rooms.bound == ("room-1", "claim-1", "turn-1")
