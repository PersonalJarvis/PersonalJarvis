"""``POST /api/chat/drop`` — drag-and-drop intake for the Jarvis dock / overlay.

A dropped file (image, document, code, …) or dragged text is posted here as
multipart; the route normalises it to :class:`DroppedItem`s and hands it to
``ingest_drop``, which composes one proactive ``MessageSent`` brain turn (spoken
on the voice build, shown in chat, landed in ``_history``). Images ride the
multimodal path so the brain can see a dropped picture.

Mirrors the avatar-upload pattern (multipart + size cap). The reply flows back
over the normal WS event stream — this route only kicks off the turn.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from jarvis.brain.drop_context import DroppedItem, ingest_drop

router = APIRouter(tags=["chat"])

#: Hard total cap across all dropped files — protects RAM, the token budget and
#: the WS broadcast circuit-breaker. A single drop is interactive, not a bulk
#: upload, so 25 MB is generous.
_MAX_DROP_BYTES = 25 * 1024 * 1024


@router.post("/api/chat/drop")
async def drop_into_context(
    request: Request,
    files: list[UploadFile] | None = File(default=None),  # noqa: B008
    thread_id: str = Form(default="default"),  # noqa: B008
    text: str | None = Form(default=None),  # noqa: B008
    surface: str | None = Form(default=None),  # noqa: B008
) -> dict[str, Any]:
    """Ingest dropped content as a proactive brain turn.

    Returns ``{"dispatched": bool}`` — ``False`` when the drop carried nothing
    usable (no turn dispatched). 413 when the total payload exceeds the cap.
    """
    brain = getattr(request.app.state, "brain", None)
    if brain is None:
        raise HTTPException(
            status_code=503, detail="Brain unavailable to hold dropped context."
        )

    items: list[DroppedItem] = []
    total = 0
    for upload in files or []:
        data = await upload.read()
        total += len(data)
        if total > _MAX_DROP_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Dropped content too large (max {_MAX_DROP_BYTES // (1024 * 1024)} MB).",
            )
        if not data:
            continue
        items.append(
            DroppedItem(
                name=upload.filename or "file",
                mime=upload.content_type or "application/octet-stream",
                data=data,
            )
        )

    captured = await ingest_drop(
        brain=brain,
        thread_id=thread_id or "default",
        items=items,
        dragged_text=text,
    )
    # ``dispatched`` kept as the response key for frontend compatibility; it now
    # means "captured into context" (a drop never dispatches a turn on its own).
    return {"dispatched": captured}
