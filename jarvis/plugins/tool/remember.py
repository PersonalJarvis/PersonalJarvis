"""remember tool: the user says "remember X" → X is saved to Jarvis' MEMORY.md.

Every later conversation (voice, chat) shows it in its prompt.

The notebook lives in the Society lead's folder of the vault
(``society/jarvis/MEMORY.md``, see :mod:`jarvis.memory.learning.notebook`),
so the person can read and edit it in the app or in Obsidian. Without a
running notebook (a headless test, a boot that disabled learning) the fact
goes to the legacy core-memory file instead, so the request is never lost.

Risk tier: safe — only writes local files.
"""
from __future__ import annotations

import asyncio
from typing import Any

from jarvis.core.config import DATA_DIR
from jarvis.core.protocols import ExecutionContext, ToolResult


class RememberTool:
    name: str = "remember"
    risk_tier: str = "safe"
    description: str = (
        "Save something the user explicitly asked you to remember for future "
        "conversations into Jarvis' long-term MEMORY.md. Pass one self-contained "
        "sentence in the user's language that still makes sense later (resolve "
        "'this' or 'that'). Every later conversation sees it."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": "What to remember, as one self-contained sentence.",
            },
            "category": {
                "type": "string",
                "description": "Optional label (e.g. 'preference', 'project'); "
                "used only by the legacy fallback store.",
                "default": "general",
            },
        },
        "required": ["fact"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        from jarvis.memory.learning.notebook import remember_explicitly

        fact = " ".join(str(args.get("fact") or "").split())
        if not fact:
            return ToolResult(success=False, output=None, error="fact is missing")
        said = str(getattr(ctx, "user_utterance", "") or "")
        try:
            change = await asyncio.to_thread(remember_explicitly, fact, evidence=said)
        except LookupError:  # no MEMORY.md notebook: use the legacy store
            return await self._legacy(fact, str(args.get("category") or "general").strip())
        except (ValueError, OSError) as exc:  # reported to the model as a failed save
            return ToolResult(success=False, output=None, error=f"not saved: {exc}")
        if change is None:
            return ToolResult(success=True, output=f"Already in MEMORY.md: {fact}")
        return ToolResult(success=True, output=f"Saved to MEMORY.md: {fact}")

    @staticmethod
    async def _legacy(fact: str, category: str) -> ToolResult:
        from jarvis.memory import CORE_MEMORY_FILENAME, CoreMemory
        from jarvis.memory.learning.notebook import OWNER_ID
        from jarvis.memory.write_feedback import announce_write

        def _write() -> None:
            with announce_write(OWNER_ID, CORE_MEMORY_FILENAME, operation="add"):
                mem = CoreMemory.load(DATA_DIR / CORE_MEMORY_FILENAME)
                mem.add_fact(fact, category=category or "general")

        try:
            await asyncio.to_thread(_write)
        except Exception as exc:  # noqa: BLE001 — reported to the caller as a failed save
            return ToolResult(success=False, output=None, error=str(exc))
        return ToolResult(success=True, output=f"Remembered: [{category}] {fact}")
