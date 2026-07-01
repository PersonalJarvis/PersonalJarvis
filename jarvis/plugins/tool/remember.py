"""remember tool: user says "merk dir X" (English: "remember X") → the fact  # i18n-allow (quotes a DE voice-trigger example a user might say)
is persisted into core memory.

Risk tier: safe — only writes to the local JSON file.
"""
from __future__ import annotations

from typing import Any

from jarvis.core.config import DATA_DIR
from jarvis.core.protocols import ExecutionContext, ToolResult


class RememberTool:
    name: str = "remember"
    risk_tier: str = "safe"
    description: str = (
        "Stores a fact in persistent core memory. Automatically injected "
        "into the system prompt on the next brain call."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "fact": {"type": "string", "description": "The fact to remember (short sentence)"},
            "category": {
                "type": "string",
                "description": "Category (e.g. 'identity', 'preference', 'project')",
                "default": "general",
            },
        },
        "required": ["fact"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        from jarvis.memory import CORE_MEMORY_FILENAME, CoreMemory

        fact = (args.get("fact") or "").strip()
        category = (args.get("category") or "general").strip()
        if not fact:
            return ToolResult(success=False, output=None, error="fact is missing")

        try:
            mem = CoreMemory.load(DATA_DIR / CORE_MEMORY_FILENAME)
            mem.add_fact(fact, category=category)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))

        return ToolResult(
            success=True,
            output=f"Remembered: [{category}] {fact}",
        )
