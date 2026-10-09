"""New Jarvis chats run autonomously while explicit permission choices persist."""

from pathlib import Path

import pytest

from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore


@pytest.mark.parametrize("provider", ["openai", "gemini", "claude-api"])
@pytest.mark.parametrize("mode", ["", "ask", "accept-edits", "plan"])
def test_jarvis_default_and_explicit_modes_survive_reopening(tmp_path: Path, provider, mode):
    path = tmp_path / "chat.db"
    store = AgentChatStore(path)
    try:
        service = AgentChatService(store)
        session = service.create_session(
            provider=provider, surface="jarvis", cwd=str(tmp_path), permission_mode=mode
        )
        assert session.permission_mode == (mode or "bypass")
    finally:
        store.close()
    reopened = AgentChatStore(path)
    try:
        assert reopened.get_session(session.session_id).permission_mode == (mode or "bypass")
    finally:
        reopened.close()
