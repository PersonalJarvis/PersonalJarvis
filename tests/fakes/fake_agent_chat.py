"""A chat service that answers a scripted turn — no model, no provider, no network.

The society dispatches work by seating an agent's canonical chat session and
watching its event stream. Anything that exercises that path (the Agent MCP
surface, `chat_binding`, the scheduler) needs a service that behaves like the
real one at that seam: `is_running`, `subscribe`/`unsubscribe`, and a `send`
that publishes turn events to the subscribers.

The script is deliberately synchronous — every event is queued before `send`
returns — so a test never sleeps waiting for a turn. Pass `script=[]` to model
a turn that never answers.
"""

from __future__ import annotations

import asyncio
from typing import Any


def default_script(text: str) -> list[dict[str, Any]]:
    """One tool call, one answer, a clean finish."""
    return [
        {"kind": "tool_call", "payload": {"name": "wiki-recall"}},
        {"kind": "assistant_text", "payload": {"text": f"Heard you: {text}"}},
        {"kind": "turn_finished", "payload": {"status": "ok"}},
    ]


class FakeChatService:
    """Stands in for `AgentChatService` at the seam the society uses."""

    def __init__(self, store: Any, script: list[dict[str, Any]] | None = None) -> None:
        self.store = store
        self.sent: list[tuple[str, str]] = []
        self.busy: set[str] = set()
        self._subs: dict[str, set[asyncio.Queue[Any]]] = {}
        self._script = script

    def is_running(self, session_id: str) -> bool:
        return session_id in self.busy

    def subscribe(self, session_id: str) -> asyncio.Queue[Any]:
        q: asyncio.Queue[Any] = asyncio.Queue()
        self._subs.setdefault(session_id, set()).add(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue[Any]) -> None:
        self._subs.get(session_id, set()).discard(q)

    async def send(self, session_id: str, text: str, attachments: Any = None) -> str:
        self.sent.append((session_id, text))
        turn_id = f"turn-{len(self.sent)}"
        events = self._script if self._script is not None else default_script(text)
        for event in events:
            payload = dict(event.get("payload") or {})
            payload.setdefault("turn_id", turn_id)
            for q in self._subs.get(session_id, set()):
                q.put_nowait({"kind": event["kind"], "payload": payload})
        return turn_id


__all__ = ["FakeChatService", "default_script"]
