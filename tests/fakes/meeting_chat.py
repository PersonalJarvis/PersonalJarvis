"""Deterministic chat turns for meeting tests; no provider calls."""

import asyncio

from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.store import AgentChatStore


class MeetingChatFake:
    def __init__(self, store: AgentChatStore) -> None:
        self.store = store
        self.sent = []
        self.cancelled = []
        self.busy = set()
        self.hold = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.hold_cancel = False
        self.cancel_started = asyncio.Event()
        self.cancel_release = asyncio.Event()

    def is_running(self, session_id):
        return session_id in self.busy

    def running_session_ids(self):
        return list(self.busy)

    async def send(self, session_id, text, **kwargs):
        turn_id = f"turn-{len(self.sent)}"
        self.sent.append((session_id, text, kwargs, turn_id))
        self.busy.add(session_id)
        self.started.set()
        if self.hold:
            await self.release.wait()
        self.store.append_event(
            session_id,
            make_event(
                "assistant_text",
                {
                    "turn_id": turn_id,
                    "text": f"Contribution from {session_id}",
                },
            ),
        )
        self.store.append_event(
            session_id,
            make_event(
                "turn_finished",
                {
                    "turn_id": turn_id,
                    "status": "done",
                },
            ),
        )
        self.busy.discard(session_id)
        return turn_id

    async def wait_turn(self, session_id):
        return None

    def signal_cancel(self, session_id, *, expected_turn_id=None):
        self.cancelled.append((session_id, expected_turn_id))

    async def cancel(self, session_id, *, expected_turn_id=None):
        self.cancel_started.set()
        if self.hold_cancel:
            await self.cancel_release.wait()
        self.signal_cancel(session_id, expected_turn_id=expected_turn_id)
