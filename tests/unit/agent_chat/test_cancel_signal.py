"""Nested browser denials stop the owner before the rejected tool returns."""

import asyncio
from types import SimpleNamespace

from jarvis.agent_chat.service import AgentChatService


async def test_cancel_signal_is_immediate_and_does_not_touch_another_chat():
    loop = asyncio.get_running_loop()
    own = SimpleNamespace(task=loop.create_future(), cancel=asyncio.Event())
    other = SimpleNamespace(task=loop.create_future(), cancel=asyncio.Event())
    service = object.__new__(AgentChatService)
    service._running = {"own": own, "other": other}
    service._approvals = {"a": loop.create_future(), "b": loop.create_future()}
    service._approval_session = {"a": "own", "b": "other"}
    try:
        assert service.signal_cancel("own")
        assert own.cancel.is_set()
        assert service._approvals["a"].result() == "cancel"
        assert not other.cancel.is_set()
        assert not service._approvals["b"].done()
        assert not service.signal_cancel("missing")
    finally:
        own.task.cancel()
        other.task.cancel()
        service._approvals["b"].cancel()
