"""``awareness-snapshot`` tool — synchronous state read for the router.

Binding per Plan §5: this tool is router-tier-only (NOT in SUB_TOOLS). It
makes NO brain call and NO IO — just a synchronous read on
``AwarenessState.snapshot_for_prompt()``.

When the main Jarvis uses it: for utterances like "what am I doing right
now?" or "which file am I in?" — the answer is already in the awareness
state and doesn't need an LLM roundtrip or an OpenClaw spawn.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jarvis.awareness.manager import AwarenessManager


@dataclass
class ToolResult:
    """Minimal wrapper for tool output. The full contract lives in
    jarvis.core.protocols; here just what we actually send back.

    If the real tool-result protocol is extended, this class must stay
    structurally compatible (or be replaced directly by the protocol).
    """
    success: bool
    output: str
    error: str | None = None


class AwarenessSnapshotTool:
    """Synchronous state read on ``manager.state.snapshot_for_prompt()``."""

    name: str = "awareness-snapshot"
    description: str = (
        "Returns the current awareness state (active window, idle status, "
        "last episode summary if any). USE this BEFORE asking the user for "
        "context — the answer is often already in here."
    )
    risk_tier: str = "safe"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(self, manager: AwarenessManager) -> None:
        self._manager = manager

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        """Synchronous state read — NO brain call, NO IO.

        ``args`` and ``ctx`` are ignored (schema has no required fields).
        """
        snap = self._manager.state.snapshot_for_prompt()
        return ToolResult(success=True, output=snap)
