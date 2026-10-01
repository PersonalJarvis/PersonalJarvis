"""The Society kill switch stops only Society-owned chat turns."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from jarvis.agent_chat.store import AgentChatStore
from jarvis.society.runtime import SocietyRuntime
from tests.fakes.fake_agent_chat import FakeChatService


class CancellableChat(FakeChatService):
    def __init__(self, store: AgentChatStore) -> None:
        super().__init__(store)
        self._running: dict[str, SimpleNamespace] = {}
        self.signaled: list[str] = []
        self.cancelled: list[str] = []

    def signal_cancel(self, session_id: str) -> bool:
        self.signaled.append(session_id)
        return True

    async def cancel(self, session_id: str) -> bool:
        self.cancelled.append(session_id)
        return True


async def test_kill_switch_cancels_active_society_chats_only(tmp_path: Path):
    chat_store = AgentChatStore(tmp_path / "chat.db")
    chat = CancellableChat(chat_store)
    ids = (
        ("society:scout", "society"),
        ("society:scout:routine:one", "society"),
        ("agent:ordinary", "agent"),
    )
    for session_id, surface in ids:
        chat_store.create_session(
            session_id=session_id,
            surface=surface,
            provider="openai",
            model="test",
            effort="",
            cwd=str(tmp_path),
            permission_mode="ask",
        )
        chat._running[session_id] = SimpleNamespace(task=object())
    runtime = SocietyRuntime(
        tmp_path, seed_starter_team=False, chat_service=lambda: chat
    )
    await runtime.ensure_started()
    try:
        result = await runtime.engage_kill_switch()
        assert result["engaged"]
        assert await runtime.store.kill_switch()
        assert set(chat.signaled) == {"society:scout", "society:scout:routine:one"}
        assert set(chat.cancelled) == set(chat.signaled)
    finally:
        await runtime.close()
        chat_store.close()
