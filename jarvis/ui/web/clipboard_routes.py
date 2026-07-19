"""Desktop-only REST fallback for writing text to the system clipboard."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.platform.clipboard import write_text

router = APIRouter(prefix="/api/clipboard", tags=["clipboard"])

_MAX_TEXT_CHARS = 5 * 1024 * 1024


class ClipboardTextRequest(BaseModel):
    """Text to place on the local desktop clipboard."""

    text: str = Field(max_length=_MAX_TEXT_CHARS)


class ClipboardTextResponse(BaseModel):
    """Native clipboard write result."""

    copied: bool


@router.post(
    "/text",
    response_model=ClipboardTextResponse,
    summary="Copy text to the local desktop clipboard",
    openapi_extra={"x-jarvis-dangerous": True},
)
def copy_text_to_system_clipboard(
    request: Request,
    body: ClipboardTextRequest,
) -> ClipboardTextResponse:
    """Write text only when this backend owns a local desktop shell."""
    if not bool(getattr(request.app.state, "native_file_actions", False)):
        raise HTTPException(status_code=404, detail="native-clipboard-disabled")
    if not write_text(body.text):
        raise HTTPException(status_code=503, detail="native-clipboard-unavailable")
    return ClipboardTextResponse(copied=True)

__all__ = ["router"]
