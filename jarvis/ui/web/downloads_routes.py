"""REST routes that save a file to the user's local Downloads folder.

Why this exists: the desktop shell renders the UI inside pywebview (WebView2 /
Edge Chromium on Windows). pywebview ships with ``settings['ALLOW_DOWNLOADS']``
defaulting to ``False`` — its EdgeChromium handler then *silently cancels* every
browser download (blob ``<a download>``, ``Content-Disposition: attachment``).
So the client-side download that works in a plain browser produces a "success"
toast but no file on the desktop. To put a file where the user expects it, the
backend writes it directly to ``~/Downloads`` (cross-platform via
``Path.home()``) and reports the absolute path back for the toast.

This mirrors the Outputs view, which already takes the native path on the
desktop (``open_path.reveal_in_folder`` / ``open_file``) instead of a browser
download.

Endpoints:
    GET  /api/downloads/capabilities   {native_file_actions, platform}
    POST /api/downloads/save           Write base64 bytes to ~/Downloads
    POST /api/downloads/reveal         Open the OS file manager with a saved file selected
    POST /api/downloads/open           Open a saved file with its default app

``POST /save``, ``/reveal`` and ``/open`` are **desktop-only**: they are gated on
``app.state.native_file_actions`` (True on a local desktop run, False on a
headless VPS) and 404 when False — on a server the file would land on / open on
the *server's* disk, not the user's machine, so the frontend keeps the browser
download there. Loopback-only (server binds 127.0.0.1) — no auth token needed.

Why ``/reveal`` + ``/open`` exist: dragging a file OUT of the embedded WebView is
not reliably possible on ANY OS (WebView2's HTML5 drag-and-drop is broken;
WKWebView/WebKitGTK lack the Chromium ``DownloadURL`` drag format). So instead of
a broken drag, the UI offers a one-click "reveal in the file manager" — from
which the user drags the REAL file natively, anywhere, on every OS — plus a
plain "open".
"""
from __future__ import annotations

import base64
import binascii
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.platform import detect_platform

# Reuse the proven collision-avoidance helper from the sessions save path
# instead of duplicating it (the two save flows share the same ~/Downloads
# semantics). Importing the module only defines a router; no side effects.
from jarvis.ui.web.sessions_routes import _avoid_collision

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/downloads", tags=["downloads"])

# A generous ceiling for a UI-initiated save (transcripts, a share-card PNG).
# Guards against a runaway/abusive body even though the server is loopback-only.
_MAX_BYTES = 25 * 1024 * 1024  # 25 MiB

# Characters illegal in a Windows filename (the strictest of the three OSes);
# replaced with '_' so one sanitizer keeps the name portable everywhere.
_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class SaveFileRequest(BaseModel):
    """Save-to-Downloads request — filename + base64 content (text or binary)."""

    filename: str = Field(min_length=1, max_length=255)
    content_b64: str


class SaveFileResponse(BaseModel):
    """Absolute saved path for the toast, plus filename and byte count."""

    saved_path: str
    filename: str
    bytes_written: int


@router.get("/capabilities")
def downloads_capabilities(request: Request) -> dict[str, Any]:
    """Report whether the backend can save straight to the local Downloads folder.

    True only on a local desktop run (set by the launcher); False on a headless
    VPS, where writing to ``~/Downloads`` would target the *server's* disk, not
    the user's machine. The frontend uses this to decide between a backend save
    (desktop) and a normal browser download (VPS/browser).
    """
    native = bool(getattr(request.app.state, "native_file_actions", False))
    return {"native_file_actions": native, "platform": detect_platform()}


def _safe_basename(filename: str) -> str:
    """Reduce *filename* to a single portable basename (defense-in-depth).

    Strips any directory part and traversal, replaces characters illegal on
    Windows, and falls back to ``download`` when nothing usable remains. The
    frontend already builds safe names; this guarantees it regardless.
    """
    # Drop any path component — handle both separators explicitly so a Windows
    # path posted from a non-Windows client (or vice versa) is still flattened.
    base = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = _ILLEGAL_FILENAME_CHARS.sub("_", base)
    base = base.strip(" .")  # no leading/trailing dots or spaces (Windows)
    if not base or base in (".", ".."):
        return "download"
    return base[:255]


@router.post("/save", response_model=SaveFileResponse)
async def save_to_downloads(
    request: Request, body: SaveFileRequest
) -> SaveFileResponse:
    """Write base64-decoded *content* to ``~/Downloads`` and return the path.

    Desktop-only (gated on ``native_file_actions``). On a name collision a
    ``-1``, ``-2`` … suffix is appended — never overwrites. Returns the absolute
    path so the UI can show exactly where the file landed.
    """
    native = bool(getattr(request.app.state, "native_file_actions", False))
    if not native:
        # On a server this would write to the server's disk. The frontend takes
        # the browser-download path there, so this route should not be reached.
        raise HTTPException(status_code=404, detail="native-file-actions-disabled")

    try:
        data = base64.b64decode(body.content_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid-base64") from exc
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="file-too-large")

    filename = _safe_basename(body.filename)
    downloads = Path.home() / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    target = _avoid_collision(downloads / filename)

    target.write_bytes(data)
    log.info("DownloadSave: %s (%d bytes)", target, len(data))
    return SaveFileResponse(
        saved_path=str(target),
        filename=target.name,
        bytes_written=len(data),
    )


class PathRequest(BaseModel):
    """A single absolute path to a previously-saved download."""

    path: str = Field(min_length=1, max_length=4096)


def _require_native(request: Request) -> None:
    """404 unless this is a local desktop run (native file actions enabled).

    On a headless VPS the file lives on the server's disk, so revealing/opening
    it would act on the server, not the user's machine — the frontend must not
    reach these routes there.
    """
    if not bool(getattr(request.app.state, "native_file_actions", False)):
        raise HTTPException(status_code=404, detail="native-file-actions-disabled")


def _resolve_saved_file(path_str: str) -> Path:
    """Resolve *path_str* to an existing file inside the user's ~/Downloads.

    Fail-closed path safety: the path is fully resolved (so ``..`` traversal and
    symlinks cannot escape) and must sit inside ``~/Downloads`` — the only place
    ``/save`` ever writes. This keeps a compromised/renderer-supplied path from
    revealing or opening arbitrary files on disk. Raises an HTTPException on any
    miss; never returns an out-of-tree or missing path.
    """
    try:
        p = Path(path_str).resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail="invalid-path") from exc
    downloads = (Path.home() / "Downloads").resolve()
    if p != downloads and downloads not in p.parents:
        raise HTTPException(status_code=403, detail="path-outside-downloads")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="file-not-found")
    return p


@router.post("/reveal")
def reveal_download(request: Request, body: PathRequest) -> dict[str, Any]:
    """Open the OS file manager with the saved file selected/highlighted.

    Desktop-only. The user then drags the REAL file from the file manager to any
    target (folder, browser upload zone, chat) — a native OS drag the embedded
    WebView cannot do itself. Cross-platform via ``open_path.reveal_in_folder``
    (Explorer ``/select`` on Windows, ``open -R`` on macOS, folder on Linux).
    """
    _require_native(request)
    target = _resolve_saved_file(body.path)
    from jarvis.platform.open_path import reveal_in_folder

    revealed = reveal_in_folder(target)
    log.info("DownloadReveal: %s (revealed=%s)", target, revealed)
    return {"revealed": revealed}


@router.post("/open")
def open_download(request: Request, body: PathRequest) -> dict[str, Any]:
    """Open the saved file with the OS default application. Desktop-only.

    Cross-platform via ``open_path.open_file`` (``os.startfile`` / ``open`` /
    ``xdg-open``). Returns ``{opened: false}`` if no launcher could be invoked.
    """
    _require_native(request)
    target = _resolve_saved_file(body.path)
    from jarvis.platform.open_path import open_file

    opened = open_file(target)
    log.info("DownloadOpen: %s (opened=%s)", target, opened)
    return {"opened": opened}
