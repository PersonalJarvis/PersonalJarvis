"""No-I/O ports for testing the browser tool's early desktop handoff."""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.society.events import AgentState


class FakeBrowserHandoffRuntime:
    def __init__(self, *, active: bool = True, halted: bool = False) -> None:
        self.roster = self
        self.store = self
        self.halted = halted
        self.caller = SimpleNamespace(agent_id="scout", state=AgentState.ACTIVE) if active else None

    async def get(self, agent_id: str):
        assert agent_id == "scout"
        return self.caller

    async def kill_switch(self) -> bool:
        return self.halted


class ForbiddenBrowserJobs:
    @property
    def _python(self):
        # Both the live bridge and the subprocess runner are selected through
        # this port. A handoff must return before selecting either executor.
        raise AssertionError("a handed-off desktop task reached the browser executor")
