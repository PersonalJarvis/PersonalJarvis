"""Route one Jarvis-presence drop to the context that is actually on screen."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from loguru import logger

from jarvis.brain.drop_context import DroppedItem, ingest_drop


@dataclass(frozen=True, slots=True)
class DropCapture:
    """What accepted a drop and, when applicable, which pane owns it."""

    captured: bool
    terminal: str = ""
    batch_id: str = ""
    files: tuple[str, ...] = ()


async def capture_presence_drop(
    *,
    brain: Any,
    items: Sequence[DroppedItem],
    dragged_text: str | None = None,
    thread_id: str = "default",
) -> DropCapture:
    """Capture one native/web dock drop without splitting its meaning.

    When the Agentic IDE is on screen, its selected prompt target is explicit:
    files belong to that pane's next spoken instruction and are copied,
    analysed, displayed by the orb, and consumed through the existing guarded
    queue. Everywhere else the same files remain ordinary assistant context.
    Dragged text always stays ordinary context because it is not a file the
    coding agent can open.
    """
    staged = None
    if items:
        try:
            from jarvis.agentic_ide.prompt_attachments import (
                stage_items_for_prompt_target,
            )

            staged = await stage_items_for_prompt_target(items)
        except Exception as exc:  # noqa: BLE001 - ordinary context remains available
            logger.warning(
                "Jarvis presence drop could not stage Agentic-IDE attachments: {}",
                exc,
            )

    # A staged file must not also remain as global image context: an addressed
    # pane turn exits through the deterministic IDE path before global images
    # are consumed, which would leak the old picture into a later unrelated
    # assistant turn. Text dragged alongside files is independent and retained.
    context_items: Sequence[DroppedItem] = () if staged is not None else items
    context_captured = False
    if context_items or (dragged_text and dragged_text.strip()):
        context_captured = bool(
            await ingest_drop(
                brain=brain,
                thread_id=thread_id,
                items=context_items,
                dragged_text=dragged_text,
            )
        )

    if staged is not None:
        return DropCapture(
            captured=True,
            terminal=staged.terminal,
            batch_id=staged.batch_id,
            files=staged.files,
        )
    return DropCapture(captured=context_captured)


__all__ = ["DropCapture", "capture_presence_drop"]
