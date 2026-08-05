"""Shared staging for files waiting on a spoken Agentic-IDE prompt.

The orb, the web dock, and the native Jarvis Bar are three surfaces for one
gesture: attach these files to the next instruction for the selected coding
pane.  This module owns the common queue operation so every surface gets the
same limits, workspace-local copies, analysis, and consumption path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from jarvis.agentic_ide import drop_analysis, drops
from jarvis.agentic_ide.session import PendingPromptAttachmentBatch, get_registry
from jarvis.brain.drop_context import DroppedItem

MAX_PENDING_BATCHES = 8
MAX_PENDING_ATTACHMENTS = 16
MAX_PENDING_CHARS = 20_000


class PromptAttachmentQueueFull(RuntimeError):
    """The selected pane already carries the maximum pending context."""


@dataclass(frozen=True, slots=True)
class StagedPromptAttachments:
    """A completed staging operation and the pane that owns it."""

    terminal: str
    batch_id: str
    files: tuple[str, ...]


def _analysis_chars(items: Sequence[Any]) -> int:
    return sum(
        len(str(getattr(item, field, "") or ""))
        for item in items
        for field in ("name", "reference", "detail", "note")
    )


async def enqueue(
    term: Any,
    attachments: Sequence[Any],
    files: Sequence[str],
) -> PendingPromptAttachmentBatch:
    """Append one bounded batch to ``term`` under its attachment lock."""
    batch = PendingPromptAttachmentBatch(
        batch_id=uuid4().hex,
        attachments=tuple(attachments),
        files=tuple(files),
    )
    async with term.pending_prompt_attachment_lock:
        pending = term.pending_prompt_attachment_batches
        attachment_count = sum(len(item.attachments) for item in pending)
        char_count = sum(_analysis_chars(item.attachments) for item in pending)
        if (
            len(pending) >= MAX_PENDING_BATCHES
            or attachment_count + len(batch.attachments) > MAX_PENDING_ATTACHMENTS
            or char_count + _analysis_chars(batch.attachments) > MAX_PENDING_CHARS
        ):
            raise PromptAttachmentQueueFull(
                "This pane already has the maximum amount of voice-prompt "
                "context waiting. Use or remove a pending drop first."
            )
        pending.append(batch)
    return batch


async def stage_items_for_prompt_target(
    items: Sequence[DroppedItem],
    *,
    registry: Any = None,
) -> StagedPromptAttachments | None:
    """Copy and analyse ``items`` for the prompt target currently on screen.

    ``None`` means no Agentic-IDE pane is an honest target right now. The
    caller can then retain the drop as ordinary assistant context instead.
    Once a target is chosen, the batch remains bound to that pane even if the
    user selects another one while image analysis is running.
    """
    usable = [item for item in items if item.name and item.data]
    if not usable:
        return None

    active_registry = registry if registry is not None else get_registry()
    session = active_registry.session
    if session is None:
        return None
    term = session.prompt_target_terminal()
    if term is None:
        return None

    stored = await asyncio.to_thread(
        drops.store,
        session.folder,
        [(item.name, item.data) for item in usable],
    )
    pairs: list[tuple[DroppedItem, str]] = []
    for item, saved in zip(usable, stored, strict=True):
        pairs.append((item, drops.reference(saved.relative_path, agent=term.agent)))
    analyses = await drop_analysis.analyze(pairs)
    if not analyses:
        return None

    # The pane may have been closed while a vision provider described an image.
    # Never park context on a detached Terminal object the UI cannot list.
    found = active_registry.find_terminal(term.name)
    if found is None or found[0] is not session or found[1] is not term:
        return None

    batch = await enqueue(term, analyses, [saved.name for saved in stored])
    return StagedPromptAttachments(
        terminal=term.name,
        batch_id=batch.batch_id,
        files=batch.files,
    )


__all__ = [
    "MAX_PENDING_ATTACHMENTS",
    "MAX_PENDING_BATCHES",
    "MAX_PENDING_CHARS",
    "PromptAttachmentQueueFull",
    "StagedPromptAttachments",
    "enqueue",
    "stage_items_for_prompt_target",
]
