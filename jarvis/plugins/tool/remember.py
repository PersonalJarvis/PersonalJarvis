"""remember-Tool: User sagt "merk dir X" → Fact wird ins Core-Memory persistiert.

Risk-Tier: safe — schreibt nur in die lokale JSON-Datei.
"""
from __future__ import annotations

from typing import Any

from jarvis.core.config import DATA_DIR
from jarvis.core.protocols import ExecutionContext, ToolResult


class RememberTool:
    name: str = "remember"
    risk_tier: str = "safe"
    description: str = (
        "Speichert einen Fact im persistenten Core-Memory. Wird beim nächsten "
        "Brain-Call automatisch in den System-Prompt injiziert."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "fact": {"type": "string", "description": "Der zu merkende Fact (kurzer Satz)"},
            "category": {
                "type": "string",
                "description": "Kategorie (z.B. 'identity', 'preference', 'project')",
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
            return ToolResult(success=False, output=None, error="fact fehlt")

        try:
            mem = CoreMemory.load(DATA_DIR / CORE_MEMORY_FILENAME)
            mem.add_fact(fact, category=category)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))

        return ToolResult(
            success=True,
            output=f"Gemerkt: [{category}] {fact}",
        )
