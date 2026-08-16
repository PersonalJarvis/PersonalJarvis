"""Turning a multipart upload into the ``(path, bytes)`` pairs staging wants.

The browser sends one part per file. A folder drop needs the relative path of
each file too, and ``webkitRelativePath`` is not part of what a multipart body
carries — so the UI sends a parallel ``paths`` field holding a JSON array in
the same order as the files.

Reading happens with a ceiling rather than whole: ``UploadFile`` spools to
disk, so an oversized upload has to be refused while it is being absorbed, not
after. The count is checked before the first read, because refusing a dropped
home directory should not cost two thousand file reads first.

This module is deliberately the only bridge between FastAPI and
``jarvis.core.uploads`` — the staging rules themselves stay framework-free and
testable without a request.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, UploadFile

from jarvis.core.uploads import (
    MAX_UPLOAD_FILE_BYTES,
    MAX_UPLOAD_FILE_COUNT,
    MAX_UPLOAD_TOTAL_BYTES,
    UploadRejected,
)


def parse_upload_paths(raw: str | None, count: int) -> list[str] | None:
    """The client-supplied relative paths, or ``None`` to fall back to filenames.

    A malformed value is refused rather than ignored: silently falling back
    would flatten a folder upload into a pile of loose files, and the owner
    would only notice once the install produced the wrong thing.
    """
    if raw is None or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"'paths' is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, list) or not all(isinstance(p, str) for p in parsed):
        raise HTTPException(
            status_code=400, detail="'paths' must be a JSON array of strings."
        )
    if len(parsed) != count:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'paths' has {len(parsed)} entries but {count} files were "
                "uploaded — they must line up."
            ),
        )
    return parsed


async def read_upload_entries(
    files: list[UploadFile],
    paths: str | None = None,
) -> list[tuple[str, bytes]]:
    """Reads the multipart parts into memory, refusing anything oversized.

    Raises ``HTTPException`` — this is the request-facing edge, so the caller
    can let the error travel untouched.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded.")
    if len(files) > MAX_UPLOAD_FILE_COUNT:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{len(files)} files were uploaded — the limit is "
                f"{MAX_UPLOAD_FILE_COUNT}."
            ),
        )

    supplied = parse_upload_paths(paths, len(files))
    entries: list[tuple[str, bytes]] = []
    total = 0
    for index, upload in enumerate(files):
        path = (supplied[index] if supplied else "") or upload.filename or ""
        # One byte past the ceiling is enough to know it is too large, and it
        # keeps a 2 GB drop from being absorbed before it is refused.
        data = await upload.read(MAX_UPLOAD_FILE_BYTES + 1)
        if len(data) > MAX_UPLOAD_FILE_BYTES:
            limit_mb = MAX_UPLOAD_FILE_BYTES // (1024 * 1024)
            raise HTTPException(
                status_code=413,
                detail=f"'{path or upload.filename}' is larger than {limit_mb} MB.",
            )
        total += len(data)
        if total > MAX_UPLOAD_TOTAL_BYTES:
            limit_mb = MAX_UPLOAD_TOTAL_BYTES // (1024 * 1024)
            raise HTTPException(
                status_code=413,
                detail=f"The upload is larger than {limit_mb} MB in total.",
            )
        entries.append((path, data))
    return entries


def upload_http_error(exc: UploadRejected) -> HTTPException:
    """The staging rejection, as the HTTP answer the owner will read."""
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def upload_limits() -> dict[str, Any]:
    """The ceilings, so the UI can state them instead of guessing."""
    return {
        "max_file_bytes": MAX_UPLOAD_FILE_BYTES,
        "max_total_bytes": MAX_UPLOAD_TOTAL_BYTES,
        "max_file_count": MAX_UPLOAD_FILE_COUNT,
    }
