"""REST API for the mission deck — the pictures it shows next to its numbers.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET  /api/deck/frame            → the last Screen-Context picture (bytes).
    GET  /api/deck/frame/meta       → whether one is held, and its shape.
    GET  /api/deck/cu-frame/{sha}   → one Computer-Use frame from the flight
                                      recorder, addressed by content hash.

Two producers, two shapes, one reason: the deck shows the user what Jarvis
just looked at.

* The Screen-Context picture comes from
  :mod:`jarvis.screen_context.last_frame` — at most one frame, in memory,
  gone after ``[screen_context].deck_preview_s`` seconds. The route never
  captures; it only serves what the service already produced for a capture
  the user asked for.
* Computer-Use frames are ALREADY on disk: the harness stores every
  observation under ``data/flight_recorder/blobs/<sha256>.<ext>`` for replay,
  and ``ObservationCaptured`` carries that hash. Serving by hash — validated
  as 64 hex characters, joined onto the fixed blob directory — is how the
  route stays traversal-proof without ever accepting a path.

Every response is ``Cache-Control: no-store``: a picture of the screen must
not outlive its budget in a browser cache either.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response

from jarvis.screen_context.last_frame import get_last_frame_mirror

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/deck", tags=["deck"])

#: The Computer-Use harness writes here (``jarvis/cu/engine.py``) — the same
#: constant, not a lookup, so a moved recorder shows up as a test failure
#: rather than a route that silently serves nothing.
CU_BLOB_DIR = Path("data") / "flight_recorder" / "blobs"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NO_STORE = {"Cache-Control": "no-store"}
_CU_EXTENSIONS = (".jpg", ".png")
_MIME_BY_SUFFIX = {".jpg": "image/jpeg", ".png": "image/png"}


@router.get("/frame/meta", summary="Whether a Screen-Context picture is held")
def frame_meta() -> dict[str, Any]:
    """Shape of the held picture, or ``available: false``.

    Cheap on purpose — the deck polls this after a ``ScreenCaptureCompleted``
    event and only fetches bytes when ``seq`` moved.
    """
    frame = get_last_frame_mirror().get()
    if frame is None:
        return {"available": False, "seq": 0}
    return {
        "available": True,
        "seq": frame.seq,
        "width": frame.width,
        "height": frame.height,
        "mime": frame.mime,
        "source": frame.source,
        "target_label": frame.target_label,
        "captured_at_ns": frame.captured_at_ns,
        "expires_at_ns": frame.expires_at_ns,
    }


@router.get("/frame", summary="The last Screen-Context picture")
def frame() -> Response:
    """The held picture as bytes, or 404 when none is held (or it expired)."""
    held = get_last_frame_mirror().get()
    if held is None:
        raise HTTPException(status_code=404, detail="no_frame")
    return Response(
        content=held.image,
        media_type=held.mime,
        headers={
            **_NO_STORE,
            "X-Frame-Seq": str(held.seq),
            "X-Frame-Width": str(held.width),
            "X-Frame-Height": str(held.height),
            "X-Frame-Source": held.source,
        },
    )


@router.get("/cu-frame/{sha}", summary="One Computer-Use frame, by content hash")
def cu_frame(sha: str) -> Response:
    """A recorded Computer-Use observation from the flight recorder.

    404 covers both "never recorded" and "already pruned by the retention
    sweep" — the deck treats them the same (show nothing, keep going).
    """
    if not _SHA256_RE.match(sha):
        # A hash is the ONLY accepted key. Anything else — a filename, a
        # path, a shorter digest — is refused before it touches the filesystem.
        raise HTTPException(status_code=400, detail="invalid_hash")
    for ext in _CU_EXTENSIONS:
        candidate = CU_BLOB_DIR / f"{sha}{ext}"
        if candidate.is_file():
            try:
                data = candidate.read_bytes()
            except OSError:
                log.debug("deck: cu frame %s unreadable", sha, exc_info=True)
                break
            return Response(
                content=data,
                media_type=_MIME_BY_SUFFIX[ext],
                headers=_NO_STORE,
            )
    raise HTTPException(status_code=404, detail="no_frame")


__all__ = ["router", "CU_BLOB_DIR"]
