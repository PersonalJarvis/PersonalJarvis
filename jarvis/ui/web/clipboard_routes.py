"""Desktop-only REST fallback for reading and writing the system clipboard."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.platform.clipboard import read_text, write_text

router = APIRouter(prefix="/api/clipboard", tags=["clipboard"])

_MAX_TEXT_CHARS = 5 * 1024 * 1024


class ClipboardTextRequest(BaseModel):
    """Text to place on the local desktop clipboard."""

    text: str = Field(max_length=_MAX_TEXT_CHARS)


class ClipboardTextResponse(BaseModel):
    """Native clipboard write result."""

    copied: bool


class ClipboardReadResponse(BaseModel):
    """Current text on the local desktop clipboard."""

    text: str


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


@router.get(
    "/text",
    response_model=ClipboardReadResponse,
    summary="Read text from the local desktop clipboard",
)
def read_text_from_system_clipboard(request: Request) -> ClipboardReadResponse:
    """Return the clipboard text so the desktop shell can offer a real Paste.

    The embedded WebView withholds the browser ``clipboard-read`` permission,
    so a right-click "Paste" has no in-page path and reads through here. Gated
    on the same local-desktop capability as the write route: on a browser or a
    remote/headless server this would expose the *server's* clipboard to a
    remote page, so it stays absent there. The text is never logged.
    """
    if not bool(getattr(request.app.state, "native_file_actions", False)):
        raise HTTPException(status_code=404, detail="native-clipboard-disabled")
    text = read_text()
    if text is None:
        raise HTTPException(status_code=503, detail="native-clipboard-unavailable")
    return ClipboardReadResponse(text=text)

__all__ = ["router"]
