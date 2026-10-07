"""REST API for Jarvis X — the built-in screenshot and screen-recording tool.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET    /api/jarvisx/settings                → switches, shortcuts, readiness.
    PUT    /api/jarvisx/settings                → change any subset; shortcuts re-arm live.
    GET    /api/jarvisx/items?limit=100         → the library, newest first.
    GET    /api/jarvisx/items/{id}              → one item.
    GET    /api/jarvisx/items/{id}/file         → the original PNG / video (Range-aware).
    GET    /api/jarvisx/items/{id}/thumb        → a small JPEG preview.
    GET    /api/jarvisx/items/{id}/edited       → the annotated PNG (404 when none).
    PUT    /api/jarvisx/items/{id}/edited       → save an annotated PNG (raw image/png body).
    POST   /api/jarvisx/items/{id}/copy         → copy the picture to the OS clipboard.
    POST   /api/jarvisx/items/{id}/reveal       → show the file in the OS file manager.
    POST   /api/jarvisx/items/{id}/open-editor  → open the annotation editor window.
    DELETE /api/jarvisx/items/{id}              → delete the files and the entry.
    POST   /api/jarvisx/capture                 → take a screenshot (region/window/fullscreen).
    POST   /api/jarvisx/record/start            → start recording (region/fullscreen).
    POST   /api/jarvisx/record/stop             → stop and save the recording.
    GET    /api/jarvisx/record/status           → is a recording running, and for how long.

Under the CLI-first contract every route here is also a ``jarvis api jarvisx
<op>`` command (``operation_id`` = the command name). Clipboard, reveal and the
editor act on the machine the backend runs on, so they answer ``ok: false``
on a headless server rather than acting on a desktop nobody sees.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jarvisx", tags=["jarvisx"])

_ITEM_ID = r"^[0-9a-f]{32}$"
ItemId = Annotated[str, PathParam(pattern=_ITEM_ID, description="Library item id.")]
_NO_STORE = {"Cache-Control": "no-store"}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class HotkeysPatch(BaseModel):
    region: str | None = Field(default=None, max_length=64)
    window: str | None = Field(default=None, max_length=64)
    fullscreen: str | None = Field(default=None, max_length=64)
    record_region: str | None = Field(default=None, max_length=64)
    record_fullscreen: str | None = Field(default=None, max_length=64)
    stop_recording: str | None = Field(default=None, max_length=64)


class SettingsPatch(BaseModel):
    """Every field optional; only the ones sent are written."""

    enabled: bool | None = None
    hotkeys: HotkeysPatch | None = None
    thumbnail_persist: bool | None = None
    thumbnail_dismiss_s: int | None = Field(default=None, ge=1, le=3600)
    save_dir: str | None = Field(default=None, max_length=1024)
    copy_to_clipboard: bool | None = None
    sound: bool | None = None
    effect: bool | None = None
    #: Keep only the newest N screenshots and N videos; ``0`` keeps all.
    keep_newest: int | None = Field(default=None, ge=0, le=1000)


class CaptureRequest(BaseModel):
    mode: Literal["region", "window", "fullscreen"]
    #: Seconds to wait first, so the user can bring the right window forward
    #: after clicking the button inside this app.
    delay_s: float = Field(default=0.0, ge=0.0, le=10.0)


class RecordRequest(BaseModel):
    mode: Literal["region", "fullscreen"]


class CopyRequest(BaseModel):
    edited: bool = False


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _service(request: Request) -> Any:
    from jarvis.jarvisx.service import get_service  # noqa: PLC0415

    service = get_service()
    service.bind(bus=getattr(request.app.state, "bus", None), app_state=request.app.state)
    return service


def _native_actions(request: Request) -> bool:
    return bool(getattr(request.app.state, "native_file_actions", False))


def _item_or_404(item_id: str) -> Any:
    from jarvis.jarvisx.store import get_store  # noqa: PLC0415

    item = get_store().get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="That capture no longer exists.")
    return item


def _is_full_path(text: str) -> bool:
    """An absolute path on this OS (``~`` allowed); a bare ``shots`` is not."""
    return Path(text).expanduser().is_absolute()


def _media_type(path: Path) -> str:
    return {
        ".png": "image/png",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(path.suffix.lower(), "application/octet-stream")


def _settings_payload() -> dict[str, Any]:
    from jarvis.core.config import load_config  # noqa: PLC0415
    from jarvis.jarvisx import capture, geometry, paths  # noqa: PLC0415
    from jarvis.jarvisx.hotkeys import (  # noqa: PLC0415
        SHORTCUT_KEYS,
        configured_hotkeys,
        get_shortcuts,
    )
    from jarvis.jarvisx.overlay.controller import overlay_capability  # noqa: PLC0415
    from jarvis.jarvisx.recorder import probe_encoder  # noqa: PLC0415

    config = load_config()
    block = config.jarvisx
    hotkeys = configured_hotkeys(block)
    shortcuts = get_shortcuts()
    if shortcuts is not None:
        statuses = {name: status.to_json() for name, status in shortcuts.statuses.items()}
    else:
        statuses = {
            name: {"hotkey": hotkeys[name], "armed": False, "detail": "Not started yet."}
            for name in SHORTCUT_KEYS
        }
    encoder = probe_encoder()
    capture_ok, capture_detail = capture.capability()
    overlay_ok, overlay_detail = overlay_capability()
    return {
        "enabled": bool(block.enabled),
        "hotkeys": hotkeys,
        "thumbnail_persist": bool(block.thumbnail_persist),
        "thumbnail_dismiss_s": geometry.clamp_dismiss_s(block.thumbnail_dismiss_s),
        "save_dir": str(block.save_dir or ""),
        "save_dir_effective": str(paths.resolve_save_dir(block.save_dir)),
        "save_dir_default": str(paths.default_save_dir()),
        "copy_to_clipboard": bool(block.copy_to_clipboard),
        "sound": bool(block.sound),
        "effect": bool(block.effect),
        "keep_newest": max(0, min(int(getattr(block, "keep_newest", 0) or 0), 1000)),
        "sound_effects_master": bool(getattr(config.ui, "sound_effects", True)),
        "recording_available": bool(encoder.available),
        "recording_detail": encoder.detail,
        "capture_available": capture_ok,
        "capture_detail": capture_detail,
        "overlay_available": overlay_ok,
        "overlay_detail": overlay_detail,
        "shortcuts": statuses,
    }


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


@router.get("/settings", operation_id="settings_get")
async def get_settings() -> dict[str, Any]:
    return await asyncio.to_thread(_settings_payload)


@router.put("/settings", operation_id="settings_set")
async def put_settings(patch: SettingsPatch) -> dict[str, Any]:
    """Write the changed settings, then re-arm the shortcuts in place."""
    from jarvis.core.config import load_config  # noqa: PLC0415
    from jarvis.core.config_writer import set_jarvisx_settings  # noqa: PLC0415
    from jarvis.jarvisx.hotkeys import (  # noqa: PLC0415
        SHORTCUT_KEYS,
        check_hotkeys,
        configured_hotkeys,
        get_shortcuts,
        normalize_hotkey,
    )

    body = patch.model_dump(exclude_none=True)
    hotkey_patch = body.pop("hotkeys", None) or {}
    if not body and not hotkey_patch:
        raise HTTPException(status_code=400, detail="No settings were provided.")
    changes: dict[str, Any] = dict(body)
    if "save_dir" in changes:
        changes["save_dir"] = str(changes["save_dir"]).strip()
        if changes["save_dir"] and not _is_full_path(changes["save_dir"]):
            raise HTTPException(status_code=422, detail="The save folder must be a full path.")
    if hotkey_patch:
        current = configured_hotkeys((await asyncio.to_thread(load_config)).jarvisx)
        merged = {**current, **{k: normalize_hotkey(v) for k, v in hotkey_patch.items()}}
        problems = check_hotkeys(merged)
        bad = [
            f"{name.replace('_', ' ')}: {problems[name]}" for name in hotkey_patch if problems[name]
        ]
        if bad:
            raise HTTPException(status_code=422, detail=" ".join(bad))
        for name in hotkey_patch:
            changes[SHORTCUT_KEYS[name]] = merged[name]
    try:
        await asyncio.to_thread(set_jarvisx_settings, changes)
    except Exception as exc:  # noqa: BLE001 - surface the write failure honestly
        log.error("jarvisx: settings write failed", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Could not save the settings: {exc}") from exc
    shortcuts = get_shortcuts()
    if shortcuts is not None and (hotkey_patch or "enabled" in changes):
        await shortcuts.reload()
    if changes.get("keep_newest"):
        from jarvis.jarvisx.store import get_store  # noqa: PLC0415

        await asyncio.to_thread(get_store().prune, int(changes["keep_newest"]))
    return await asyncio.to_thread(_settings_payload)


# --------------------------------------------------------------------------
# library
# --------------------------------------------------------------------------


@router.get("/items", operation_id="items_list")
async def list_items(limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
    from jarvis.jarvisx.store import get_store  # noqa: PLC0415

    items = await asyncio.to_thread(get_store().recent, limit)
    return {"items": [item.to_json() for item in items]}


@router.get("/items/{item_id}", operation_id="item_get")
async def get_item(item_id: ItemId) -> dict[str, Any]:
    item = await asyncio.to_thread(_item_or_404, item_id)
    return item.to_json()


@router.get("/items/{item_id}/file", operation_id="item_file")
async def item_file(item_id: ItemId) -> FileResponse:
    item = await asyncio.to_thread(_item_or_404, item_id)
    path = Path(item.path)
    if not await asyncio.to_thread(Path.is_file, path):
        raise HTTPException(status_code=404, detail="The file was moved or deleted.")
    return FileResponse(path, media_type=_media_type(path), filename=path.name)


@router.get("/items/{item_id}/thumb", operation_id="item_thumb")
async def item_thumb(item_id: ItemId) -> Response:
    item = await asyncio.to_thread(_item_or_404, item_id)
    data = await asyncio.to_thread(_thumb_bytes, item)
    if not data:
        raise HTTPException(status_code=404, detail="No preview is available.")
    return Response(content=data, media_type="image/jpeg", headers=_NO_STORE)


def _thumb_bytes(item: Any) -> bytes:
    """The stored preview, rebuilt from the picture when it went missing."""
    thumb = Path(item.thumb_path) if item.thumb_path else None
    if thumb is not None and thumb.is_file():
        return thumb.read_bytes()
    if item.kind != "image":
        return b""
    try:
        from PIL import Image  # noqa: PLC0415

        from jarvis.jarvisx.capture import thumbnail_jpeg  # noqa: PLC0415

        with Image.open(item.path) as image:
            rgb = image.convert("RGB")
            data = thumbnail_jpeg(rgb.width, rgb.height, rgb.tobytes())
        if thumb is not None:
            thumb.parent.mkdir(parents=True, exist_ok=True)
            thumb.write_bytes(data)
        return data
    except Exception:  # noqa: BLE001 - a broken picture has no preview; the 404 says so
        log.warning("jarvisx: preview rebuild failed for %s", item.id, exc_info=True)
        return b""


@router.get("/items/{item_id}/edited", operation_id="item_edited_get")
async def get_edited(item_id: ItemId) -> FileResponse:
    item = await asyncio.to_thread(_item_or_404, item_id)
    if not item.has_edited():
        raise HTTPException(status_code=404, detail="This capture has no annotated copy.")
    path = Path(str(item.edited_path))
    return FileResponse(path, media_type="image/png", filename=path.name, headers=_NO_STORE)


@router.put("/items/{item_id}/edited", operation_id="item_edited_save")
async def put_edited(request: Request, item_id: ItemId) -> dict[str, Any]:
    """Save the editor's annotated PNG as ``<name>-edited.png`` next to the original."""
    from jarvis.jarvisx.service import MAX_EDITED_BYTES  # noqa: PLC0415

    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "image/png":
        raise HTTPException(status_code=415, detail="Send the picture as image/png.")
    data = await request.body()
    if len(data) > MAX_EDITED_BYTES:
        raise HTTPException(status_code=413, detail="The picture is too large.")
    if not data.startswith(_PNG_SIGNATURE) or not await asyncio.to_thread(_is_png, data):
        raise HTTPException(status_code=422, detail="That is not a readable PNG picture.")
    result = await _service(request).save_edited(item_id, data)
    if not result.ok:
        code = 404 if result.item is None and "no longer" in result.message else 409
        raise HTTPException(status_code=code, detail=result.message)
    return result.item.to_json()


def _is_png(data: bytes) -> bool:
    import io  # noqa: PLC0415

    try:
        from PIL import Image  # noqa: PLC0415

        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        return True
    except Exception:  # noqa: BLE001 - any decoder complaint means "not a PNG we keep"
        log.info("jarvisx: rejected an unreadable annotated picture")
        return False


@router.post("/items/{item_id}/copy", operation_id="item_copy")
async def copy_item(
    request: Request, item_id: ItemId, body: CopyRequest | None = None
) -> dict[str, Any]:
    if not _native_actions(request):
        return {"ok": False, "message": "The clipboard belongs to the computer Jarvis runs on."}
    result = await _service(request).copy_item(item_id, edited=(body or CopyRequest()).edited)
    return {"ok": result.ok, "message": result.message}


@router.post("/items/{item_id}/reveal", operation_id="item_reveal")
async def reveal_item(request: Request, item_id: ItemId) -> dict[str, Any]:
    if not _native_actions(request):
        return {"ok": False, "message": "Showing files needs the desktop app."}
    result = await _service(request).reveal(item_id)
    return {"ok": result.ok, "message": result.message}


@router.post("/items/{item_id}/open-editor", operation_id="item_open_editor")
async def open_editor(request: Request, item_id: ItemId) -> dict[str, Any]:
    result = await _service(request).open_editor(item_id)
    return {
        "ok": result.ok,
        "message": result.message,
        "url": f"/?view=jarvisx-editor&solo=1&item={item_id}",
    }


@router.delete("/items/{item_id}", operation_id="item_delete")
async def delete_item(request: Request, item_id: ItemId) -> dict[str, Any]:
    result = await _service(request).delete(item_id)
    if not result.ok:
        raise HTTPException(status_code=404, detail=result.message)
    return {"ok": True, "message": result.message}


# --------------------------------------------------------------------------
# capture + recording
# --------------------------------------------------------------------------


@router.post("/capture", operation_id="capture")
async def take_capture(request: Request, body: CaptureRequest) -> dict[str, Any]:
    result = await _service(request).capture(body.mode, delay_s=body.delay_s)
    return {
        "ok": result.ok,
        "item": result.item.to_json() if result.item is not None else None,
        "message": result.message,
    }


@router.post("/record/start", operation_id="record_start")
async def record_start(request: Request, body: RecordRequest) -> dict[str, Any]:
    service = _service(request)
    result = await service.start_recording(body.mode)
    return {"ok": result.ok, "message": result.message, **service.recording_status()}


@router.post("/record/stop", operation_id="record_stop")
async def record_stop(request: Request) -> dict[str, Any]:
    service = _service(request)
    result = await service.stop_recording()
    return {
        "ok": result.ok,
        "item": result.item.to_json() if result.item is not None else None,
        "message": result.message,
        **service.recording_status(),
    }


@router.get("/record/status", operation_id="record_status")
def record_status(request: Request) -> dict[str, Any]:
    return _service(request).recording_status()
