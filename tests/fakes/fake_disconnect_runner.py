"""A runner that waits on the real server approval before recording one effect."""

from __future__ import annotations

from jarvis.agent_chat.events import make_event


class ApprovalRunner:
    def __init__(self):
        self.starts = []
        self.effects = []

    async def __call__(self, handle, prompt, runner="api", **kwargs):
        self.starts.append(runner)
        await handle.emit(make_event("tool_call", {
            "turn_id": handle.turn_id, "call_id": "effect-1", "name": "Write", "input": {},
        }))
        answer = await handle.request_approval("effect-1", "Write", {}, "Write one result")
        if answer == "allow":
            self.effects.append(handle.turn_id)
        await handle.emit(make_event("tool_result", {
            "turn_id": handle.turn_id, "call_id": "effect-1",
            "output": "Recorded" if answer == "allow" else "Denied", "is_error": answer != "allow",
        }))
        await handle.emit(make_event("assistant_text", {
            "turn_id": handle.turn_id, "message_id": "result", "text": "The result is saved.",
        }))
        await handle.emit(make_event("turn_finished", {
            "turn_id": handle.turn_id, "status": "done", "duration_ms": 1, "usage": {},
        }))
        return None
