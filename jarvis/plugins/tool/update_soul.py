"""update_soul tool: the live conversation model keeps SOUL.md up to date itself.

When the user tells the assistant how it should be or present itself ("be
less formal", "you are George, not Personal Jarvis"), the GPT-Live thinking
model records that as one short note in SOUL.md's learned section while the
call runs. The next call's identity block (``jarvis.brain.identity``) carries
it on every surface. This replaces a separate review model call for the
assistant's character: the model that already heard the correction writes it.

Voice-only (``BrainSupervisorToolGateway._voice_tools``): the router stays a
pure dispatcher (ADR-0011). Writes go through the learning notebook when it
runs (lock, budget, ledger), else straight to SOUL.md under its lock. Every
note passes the learning guard (no orders, injections, secrets or hidden
characters). Risk tier: safe — one bounded, ledgered, user-editable local file.
"""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)

_OPERATIONS = ("add", "replace", "remove")


def _notes(path: Any) -> list[dict[str, str]]:
    from jarvis.memory.soul import Soul

    return [{"id": e.id, "text": e.text} for e in Soul.load(path).learned()]


class UpdateSoulTool:
    name: str = "update_soul"
    risk_tier: str = "safe"
    description: str = (
        "Update your own character file (SOUL.md) when the user tells you how YOU should "
        "be or present yourself, or corrects it: personality, humour, tone, manner, how "
        "you introduce yourself. Not for facts about the user. Your name comes from the "
        "wake word and is never stored here. note = one short third-person sentence, e.g. "
        "'The assistant keeps its jokes dry.' Use replace/remove with an id from notes."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": list(_OPERATIONS)},
            "note": {"type": "string", "description": "One short third-person sentence."},
            "id": {"type": "string", "description": "Note id, for replace and remove."},
        },
        "required": ["operation"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        import asyncio

        try:
            return await asyncio.to_thread(self._apply, args)
        except Exception as exc:  # noqa: BLE001 — a failed write is reported, never raised
            log.warning("update_soul failed", exc_info=True)
            return ToolResult(success=False, output=None, error=str(exc))

    def _apply(self, args: dict[str, Any]) -> ToolResult:
        from jarvis.brain.identity import soul_path
        from jarvis.memory.learning import notebook as notebook_module
        from jarvis.memory.learning.guard import refusal

        operation = str(args.get("operation") or "add").strip().lower()
        note = " ".join(str(args.get("note") or "").split())
        entry_id = str(args.get("id") or "").strip()
        if operation not in _OPERATIONS:
            return ToolResult(
                success=False, output=None, error="operation must be add, replace or remove"
            )
        if operation != "remove":
            reason = refusal(note)
            if reason:
                return ToolResult(success=False, output=None, error=f"note refused: {reason}")
        if operation != "add" and not entry_id:
            return ToolResult(success=False, output=None, error="id is required")
        path = soul_path()
        if not path.is_file():
            return ToolResult(success=False, output=None, error="SOUL.md does not exist yet")

        book = notebook_module.active()
        if book is not None and book.soul_path == path:
            change = book.apply(
                target=notebook_module.SOUL,
                operation=operation,
                text=note,
                entry_id=entry_id,
                importance=8,
                origin="conversation",
                source="update_soul tool",
            )
            changed = change is not None
        else:
            changed = self._write_directly(path, operation, note, entry_id)
        return ToolResult(
            success=True,
            output={"changed": changed, "notes": _notes(path)},
        )

    @staticmethod
    def _write_directly(path: Any, operation: str, note: str, entry_id: str) -> bool:
        """Without the learning loop: the same change, under SOUL.md's lock."""
        from jarvis.memory.soul import edit_soul
        from jarvis.society.notebook import change

        def mutate(soul: Any) -> bool:
            current = soul.learned()
            updated = change(
                current, note, operation=operation, entry_id=entry_id,
                importance=8, origin="conversation",
            )
            if updated == current:
                return False
            soul.set_learned(updated)
            return True

        return edit_soul(path, mutate)
