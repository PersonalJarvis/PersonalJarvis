"""Serve captured screenshot blobs by content hash (read-only).

The Computer-Use engine and the vision layer write every captured frame to
``data/flight_recorder/blobs/<sha256>.jpg`` (ADR-0007 spill convention);
events reference frames by hash only, never by pixels. Until now nothing
served those files over HTTP, so no UI could ever show them. This router is
the one read path: content-addressed, hash-validated, immutable-cacheable.

Endpoints:
- ``GET /api/screenshots/{sha256}`` → the frame as image bytes.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/screenshots", tags=["screenshots"])

#: Same location the CU engine + vision layer write to (relative to the app
#: working directory, like every other ``data/`` path).
_BLOB_DIR = Path("data") / "flight_recorder" / "blobs"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: Blob suffixes in write order of likelihood: the CU engine writes JPEG,
#: the vision screenshot helper PNG.
_SUFFIXES: tuple[tuple[str, str], ...] = (
    (".jpg", "image/jpeg"),
    (".png", "image/png"),
)


@router.get("/{sha256}")
async def get_screenshot(sha256: str) -> FileResponse:
    """One captured frame by its content hash.

    The hash IS the full authorization to a specific image: it only ever
    appears in event payloads the caller could already read, and the strict
    hex check makes path traversal structurally impossible. Content-addressed
    files never change, hence the immutable cache header.
    """
    digest = sha256.lower()
    if not _SHA256_RE.fullmatch(digest):
        raise HTTPException(status_code=400, detail="not a sha256 hash")
    for suffix, media_type in _SUFFIXES:
        path = _BLOB_DIR / f"{digest}{suffix}"
        if path.is_file():
            return FileResponse(
                path,
                media_type=media_type,
                headers={"Cache-Control": "private, max-age=31536000, immutable"},
            )
    raise HTTPException(status_code=404, detail="screenshot not found")
