"""REST API for appshots — the front window as conversation context.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET    /api/appshot/settings         → switches, shortcut state, readiness.
    PUT    /api/appshot/settings         → change one or more switches.
    POST   /api/appshot/take             → take one appshot now (window or area).
    GET    /api/appshot/latest           → metadata of the last appshot.
    GET    /api/appshot/latest/image     → its picture (never cached).
    PUT    /api/appshot/latest/image     → replace it with the editor's version and
                                           hand that to the assistant (deliver_edit).
    POST   /api/appshot/latest/card      → bring it back into the corner card stack.
    POST   /api/appshot/open-editor      → open the editor in its own window.
    POST   /api/appshot/clipboard        → copy the editor's PNG natively (desktop only).
    POST   /api/appshot/drag-file        → write it for a native drag out (desktop only).
    GET    /api/appshot/pending          → the appshot waiting for the next message.
    POST   /api/appshot/pending/claim    → hand that one to the chat composer.
    DELETE /api/appshot                  → forget every held appshot now.
    GET    /api/appshot/library          → every kept appshot and edit (the gallery).
    GET    /api/appshot/library/{id}/image → one kept picture (``thumb=1``: small).
    POST   /api/appshot/library/{id}/open  → open a kept picture in the editor.
    DELETE /api/appshot/library/{id}     → delete an edit, or the whole appshot.
    DELETE /api/appshot/library          → delete the whole gallery.

Under the CLI-first contract every action here is also a
``jarvis api appshot <op>`` command. Held pictures stay in memory
(``jarvis.appshot.store``); the gallery's history lives in
``jarvis.appshot.library`` while ``[appshot].library`` is on, and the editor's
drag-out writes one temporary file (``/drag-file``, ``jarvis.appshot.dragfile``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/appshot", tags=["appshot"])

_NO_STORE = {"Cache-Control": "no-store"}


class SettingsPatch(BaseModel):
    """Every field optional; only the ones sent are written."""

    enabled: bool | None = None
    hotkey: str | None = Field(default=None, max_length=64)
    region_hotkey: str | None = Field(default=None, max_length=64)
    recording_hotkey: str | None = Field(default=None, max_length=64)
    target: Literal["auto", "message", "voice"] | None = None
    sound: bool | None = None
    effect: bool | None = None
    card_seconds: int | None = Field(default=None, ge=0, le=600)
    library: bool | None = None
    copy_to_clipboard: bool | None = None


class TakeRequest(BaseModel):
    #: Seconds to wait first, so the user can bring the window they mean to
    #: the front after pressing the button inside this app.
    delay_s: float = Field(default=0.0, ge=0.0, le=10.0)
    #: ``window``: the front window. ``region``: the user drags out an area
    #: first (the screens dim until a rectangle is chosen or Esc is pressed).
    scope: Literal["window", "region"] = "window"


class ClaimRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64)


def _bus(request: Request) -> Any | None:
    return getattr(request.app.state, "bus", None)


def _capability() -> dict[str, Any]:
    import importlib.util  # noqa: PLC0415

    from jarvis.appshot.region import picker_capability  # noqa: PLC0415
    from jarvis.cu.indicator.controller import screen_indicator_capability  # noqa: PLC0415

    capture_ok = importlib.util.find_spec("mss") is not None
    effect_ok, effect_reason = screen_indicator_capability()
    region_ok, region_reason = picker_capability()
    return {
        "capture": capture_ok,
        "capture_detail": "" if capture_ok else "The screen-capture package is not installed.",
        "effect": effect_ok,
        "effect_detail": effect_reason,
        "region": region_ok,
        "region_detail": region_reason,
    }


def _settings_payload() -> dict[str, Any]:
    from jarvis.appshot.hotkey import configured_hotkeys, get_shortcut  # noqa: PLC0415
    from jarvis.core.config import load_config  # noqa: PLC0415

    config = load_config()
    block = config.appshot
    hotkeys = configured_hotkeys(block)
    shortcut = get_shortcut()

    def _status(scope: str) -> dict[str, Any]:
        if shortcut is not None:
            return shortcut.status_for(scope).to_json()
        return {"hotkey": hotkeys[scope], "armed": False, "detail": ""}

    return {
        "enabled": bool(config.screen_context.enabled),
        "hotkey": hotkeys["window"],
        "region_hotkey": hotkeys["region"],
        "recording_hotkey": hotkeys["recording"],
        "target": block.target,
        "sound": bool(block.sound),
        "effect": bool(block.effect),
        "card_seconds": int(getattr(block, "card_seconds", 6)),
        "library": bool(getattr(block, "library", True)),
        "copy_to_clipboard": bool(getattr(block, "copy_to_clipboard", True)),
        "sound_effects_master": bool(getattr(config.ui, "sound_effects", True)),
        "shortcut": _status("window"),
        "region_shortcut": _status("region"),
        "recording_shortcut": _status("recording"),
        "readiness": _capability(),
    }


@router.get("/settings")
async def get_settings() -> dict[str, Any]:
    return await asyncio.to_thread(_settings_payload)


@router.put("/settings")
async def put_settings(request: Request, patch: SettingsPatch) -> dict[str, Any]:
    """Write the changed switches, then re-arm the shortcut in place."""
    from jarvis.appshot.hotkey import (  # noqa: PLC0415
        configured_hotkeys,
        get_shortcut,
        is_gesture,
        normalize_hotkey,
        shortcuts_conflict,
    )
    from jarvis.core.config import load_config  # noqa: PLC0415
    from jarvis.core.config_writer import (  # noqa: PLC0415
        set_appshot_settings,
        set_screen_context_settings,
    )

    changes = patch.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No settings were provided.")
    enabled = changes.pop("enabled", None)
    for key in ("hotkey", "region_hotkey", "recording_hotkey"):
        if key not in changes:
            continue
        changes[key] = normalize_hotkey(changes[key])
        if changes[key] and not is_gesture(changes[key]):
            from jarvis.trigger.hotkey import validate_hotkey  # noqa: PLC0415

            verdict = validate_hotkey(changes[key])
            if not verdict.ok:
                raise HTTPException(status_code=400, detail=verdict.reason or "Invalid shortcut.")
    if any(key in changes for key in ("hotkey", "region_hotkey", "recording_hotkey")):
        current = configured_hotkeys((await asyncio.to_thread(load_config)).appshot)
        window = changes.get("hotkey", current["window"])
        region = changes.get("region_hotkey", current["region"])
        recording = changes.get("recording_hotkey", current["recording"])
        chosen = [key for key in (window, region, recording) if key]
        overlaps = any(
            shortcuts_conflict(key, earlier)
            for index, key in enumerate(chosen)
            for earlier in chosen[:index]
        )
        if overlaps:
            raise HTTPException(
                status_code=400,
                detail="Each AppShot action needs a different shortcut.",
            )

    def _write() -> None:
        if changes:
            set_appshot_settings(changes)
        if enabled is not None:
            set_screen_context_settings({"enabled": bool(enabled)})

    try:
        await asyncio.to_thread(_write)
    except Exception as exc:  # noqa: BLE001 - surface the write failure honestly
        log.error("appshot: settings write failed", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Could not save the settings: {exc}") from exc

    if enabled is not None:
        from jarvis.screen_context.turn import reset_service  # noqa: PLC0415

        reset_service()
    shortcut = get_shortcut()
    if shortcut is not None and any(
        key in changes for key in ("hotkey", "region_hotkey", "recording_hotkey")
    ):
        await shortcut.reload()
    return await asyncio.to_thread(_settings_payload)


@router.get("/recording")
async def get_recording() -> dict[str, Any]:
    """Read local screen recording state and capture availability."""
    from jarvis.appshot.recording import capability, get_recording_service, recent_recordings

    ready, recent = await asyncio.gather(
        asyncio.to_thread(capability), asyncio.to_thread(recent_recordings)
    )
    return {**get_recording_service().status(), "capability": ready, "recent": recent}


@router.post("/recording/start")
async def start_recording() -> dict[str, Any]:
    """Select an area or an entire screen and record a local video."""
    from jarvis.appshot.recording import get_recording_service

    try:
        return await get_recording_service().start()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/recording/stop")
async def stop_recording() -> dict[str, Any]:
    """Stop the current recording and finish writing its local video."""
    from jarvis.appshot.recording import get_recording_service

    return await get_recording_service().stop()


@router.get("/recording/{recording_id}/video")
async def recording_video(recording_id: str):
    """Download a finalized screen recording by its opaque identifier."""
    from fastapi.responses import FileResponse

    from jarvis.appshot.recording import recording_file

    path = await asyncio.to_thread(recording_file, recording_id)
    if path is None:
        raise HTTPException(status_code=404, detail="The recording is not available.")
    return FileResponse(path, media_type="video/mp4", filename=f"appshot-{recording_id}.mp4",
                        headers=_NO_STORE)


@router.post("/take")
async def take(request: Request, body: TakeRequest | None = None) -> dict[str, Any]:
    """Take one appshot (front window or a selected area), like the shortcuts."""
    from jarvis.appshot.service import take_appshot  # noqa: PLC0415

    body = body or TakeRequest()
    if body.delay_s:
        await asyncio.sleep(body.delay_s)
    result = await take_appshot(trigger="button", bus=_bus(request), scope=body.scope)
    if not result.ok or result.shot is None:
        return {"ok": False, "reason": result.reason_code, "message": result.message}
    return {"ok": True, "appshot": result.shot.meta()}


@router.get("/latest")
async def latest() -> dict[str, Any]:
    from jarvis.appshot.store import get_store  # noqa: PLC0415

    shot = await asyncio.to_thread(get_store().latest)
    return {"appshot": shot.meta() if shot is not None else None}


@router.get("/latest/image")
async def latest_image(id: str = "") -> Response:  # noqa: A002
    """The last appshot's picture — or, with ``id``, that kept appshot's.

    An older card in the corner stack opens the editor on its own picture;
    an unknown id falls back to the last appshot, as before ids were sent.
    """
    from jarvis.appshot.store import get_store  # noqa: PLC0415

    store = get_store()
    shot = await asyncio.to_thread(store.get, id) if id else None
    if shot is None and id and await asyncio.to_thread(_library_has, id):
        # A gallery picture whose hold expired while its editor stayed open.
        shot = await asyncio.to_thread(_hold_from_library, id, "edited") or await asyncio.to_thread(
            _hold_from_library, id, "original"
        )
    if shot is None:
        shot = await asyncio.to_thread(store.latest)
    if shot is None:
        raise HTTPException(status_code=404, detail="No appshot is being kept right now.")
    return Response(content=shot.image, media_type=shot.mime, headers=_NO_STORE)


#: Upper bound for an edited picture (a 4K PNG with annotations stays far below).
_MAX_EDIT_BYTES = 20 * 1024 * 1024


@router.put("/latest/image")
async def replace_latest_image(request: Request, id: str) -> dict[str, Any]:  # noqa: A002
    """Store the appshot editor's result in place of the last appshot.

    Body: the PNG the editor rendered. In memory only, like every appshot; the
    next message that takes the appshot gets the edited picture.
    """
    from jarvis.appshot.store import get_store  # noqa: PLC0415

    body = await request.body()
    if not body or len(body) > _MAX_EDIT_BYTES:
        raise HTTPException(status_code=413, detail="The edited picture is empty or too large.")
    size = await asyncio.to_thread(_png_size, body)
    if size is None:
        raise HTTPException(status_code=400, detail="The edited picture is not a PNG.")
    shot = await asyncio.to_thread(get_store().replace_image, id, body, "image/png", *size)
    if shot is None and await asyncio.to_thread(_hold_from_library, id, "original"):
        # Edited from the gallery after its hold expired: hold it again, then edit.
        shot = await asyncio.to_thread(get_store().replace_image, id, body, "image/png", *size)
    if shot is None:
        raise HTTPException(status_code=404, detail="That appshot is no longer kept.")
    from jarvis.appshot.service import deliver_edit, keep_edit_in_library  # noqa: PLC0415

    await keep_edit_in_library(shot)
    delivered_to = await deliver_edit(shot)
    return {"ok": True, "appshot": {**shot.meta(), "delivered_to": delivered_to}}


class OpenEditorRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64)


@router.post("/open-editor")
async def open_editor(body: OpenEditorRequest) -> dict[str, Any]:
    """Open the editor in its own window. ``window: false`` = edit in the page.

    The desktop shell opens a detached window (``jarvis.appshot.editor_window``);
    a headless or browser-only run cannot, and the page shows its own editor.
    """
    from jarvis.appshot.editor_window import open_editor_window  # noqa: PLC0415

    return {"window": await open_editor_window(body.id)}


class ReturnCardRequest(BaseModel):
    #: Where the editor showed the picture, ``[x, y, w, h]`` in global
    #: logical pixels: the saved picture flies from there into the corner.
    fly_from: list[float] | None = Field(default=None, min_length=4, max_length=4)
    #: The appshot the editor had open; empty = the last one.
    id: str = Field(default="", max_length=64)


@router.post("/latest/card")
async def return_card(body: ReturnCardRequest | None = None) -> dict[str, Any]:
    """Bring the held (edited) appshot back into the corner card.

    The editor calls this when it closes, so the picture stays at hand
    like after the shutter; after a save it flies there from the editor and
    lands at the bottom of the corner stack. ``shown: false`` where no
    overlay can run.
    """
    from jarvis.appshot.card_actions import return_to_corner  # noqa: PLC0415

    if body is None:
        return {"shown": await return_to_corner(None)}
    return {"shown": await return_to_corner(body.fly_from, shot_id=body.id)}


def _png_size(data: bytes) -> tuple[int, int] | None:
    import io  # noqa: PLC0415

    from PIL import Image, UnidentifiedImageError  # noqa: PLC0415

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG":
                return None
            return int(image.width), int(image.height)
    except (UnidentifiedImageError, OSError):
        return None


@router.post("/clipboard", openapi_extra={"x-jarvis-dangerous": True})
async def copy_to_clipboard(request: Request) -> dict[str, Any]:
    """Put the editor's PNG on the desktop clipboard natively.

    The embedded WebView cannot be trusted to copy images itself (see
    :mod:`jarvis.platform.clipboard_image`). Desktop only: on a browser or
    headless server the clipboard would be the server's, so this 404s there
    and the page keeps the browser's own clipboard.
    """
    if not bool(getattr(request.app.state, "native_file_actions", False)):
        raise HTTPException(status_code=404, detail="native-clipboard-disabled")
    body = await request.body()
    if not body or len(body) > _MAX_EDIT_BYTES:
        raise HTTPException(status_code=413, detail="The picture is empty or too large.")
    from jarvis.platform.clipboard_image import is_png, write_png  # noqa: PLC0415

    if not is_png(body):
        raise HTTPException(status_code=400, detail="The picture is not a PNG.")
    if not await asyncio.to_thread(write_png, body):
        raise HTTPException(status_code=503, detail="native-clipboard-unavailable")
    return {"copied": True}


@router.post("/drag-file")
async def drag_file(request: Request) -> dict[str, Any]:
    """Write the editor's PNG for a native drag and return its path.

    Called the moment the user presses the editor's "Drag me" handle — the
    only time an edited appshot touches disk (``jarvis.appshot.dragfile``).
    Desktop only: the path must be on the machine the drag starts from.
    """
    if not bool(getattr(request.app.state, "native_file_actions", False)):
        raise HTTPException(status_code=404, detail="native-drag-disabled")
    body = await request.body()
    if not body or len(body) > _MAX_EDIT_BYTES:
        raise HTTPException(status_code=413, detail="The picture is empty or too large.")
    from jarvis.appshot.dragfile import write_drag_file  # noqa: PLC0415
    from jarvis.platform.clipboard_image import is_png  # noqa: PLC0415

    if not is_png(body):
        raise HTTPException(status_code=400, detail="The picture is not a PNG.")
    path = await asyncio.to_thread(write_drag_file, body)
    if path is None:
        raise HTTPException(status_code=503, detail="The drag file could not be written.")
    return {"path": str(path)}


@router.get("/pending")
async def pending() -> dict[str, Any]:
    from jarvis.appshot.store import get_store  # noqa: PLC0415

    shot = await asyncio.to_thread(get_store().peek_pending)
    return {"appshot": shot.meta() if shot is not None else None}


@router.post("/pending/claim")
async def claim_pending(body: ClaimRequest) -> Response:
    """Move the waiting appshot into the chat composer. Single use."""
    from jarvis.appshot.store import get_store  # noqa: PLC0415

    shot = await asyncio.to_thread(get_store().take_pending, body.id)
    if shot is None:
        raise HTTPException(
            status_code=404,
            detail="That appshot was already used or has expired.",
        )
    return Response(content=shot.image, media_type=shot.mime, headers=_NO_STORE)


@router.delete("", openapi_extra={"x-jarvis-dangerous": True})
async def forget_all() -> dict[str, Any]:
    """Drop the waiting and the last appshot right now."""
    from jarvis.appshot.store import get_store  # noqa: PLC0415

    await asyncio.to_thread(get_store().clear)
    return {"ok": True}


# -- library (the gallery on the Appshots page) -------------------------------

LibraryVariant = Literal["original", "edited"]

_LIBRARY_CACHE = {"Cache-Control": "private, max-age=86400"}

#: How long a gallery picture opened in the editor stays held, at least.
_LIBRARY_HOLD_S = 900.0


def _hold_from_library(shot_id: str, variant: str) -> Any | None:
    """Put a kept picture back into the store so the editor can work on it."""
    from jarvis.appshot import library  # noqa: PLC0415
    from jarvis.appshot.store import get_store  # noqa: PLC0415
    from jarvis.core.config import load_config  # noqa: PLC0415

    shot = library.load_shot(shot_id, "edited" if variant == "edited" else "original")
    if shot is None:
        return None
    keep_s = max(float(load_config().screen_context.deck_preview_s), _LIBRARY_HOLD_S)
    get_store().remember(shot, keep_s=keep_s)
    return shot


def _library_has(shot_id: str) -> bool:
    from jarvis.appshot import library  # noqa: PLC0415

    return library.valid_id(shot_id) and (library.library_root() / shot_id).is_dir()


def _library_id(shot_id: str) -> str:
    from jarvis.appshot.library import valid_id  # noqa: PLC0415

    if not valid_id(shot_id):
        raise HTTPException(status_code=404, detail="No such appshot.")
    return shot_id


@router.get("/library")
async def library_list() -> dict[str, Any]:
    """Every kept appshot and edit, newest first — no pixels."""
    from jarvis.appshot import library  # noqa: PLC0415

    items = await asyncio.to_thread(library.list_items)
    return {
        "items": [item.to_json() for item in items],
        "max_entries": library.MAX_ENTRIES,
    }


@router.get("/library/{shot_id}/image")
async def library_image(
    shot_id: str, variant: LibraryVariant = "original", thumb: bool = False
) -> Response:
    """One kept picture; ``thumb=1`` sends the gallery's small JPEG."""
    from jarvis.appshot import library  # noqa: PLC0415

    item = await asyncio.to_thread(library.get_item, _library_id(shot_id), variant)
    if item is None:
        raise HTTPException(status_code=404, detail="That appshot is no longer kept.")
    if thumb:
        data, mime = await asyncio.to_thread(library.thumbnail, item)
    else:
        data, mime = await asyncio.to_thread(item.path.read_bytes), item.mime
    # The page versions the URL by its edit time, so a cached copy is never stale.
    return Response(content=data, media_type=mime, headers=_LIBRARY_CACHE)


class LibraryOpenRequest(BaseModel):
    variant: LibraryVariant = "original"


@router.post("/library/{shot_id}/open")
async def library_open(shot_id: str, body: LibraryOpenRequest | None = None) -> dict[str, Any]:
    """Hold a kept picture again and open the editor on it.

    ``window: false`` = the page opens its own editor on ``id``.
    """
    variant = (body or LibraryOpenRequest()).variant
    shot = await asyncio.to_thread(_hold_from_library, _library_id(shot_id), variant)
    if shot is None:
        raise HTTPException(status_code=404, detail="That appshot is no longer kept.")
    from jarvis.appshot.editor_window import open_editor_window  # noqa: PLC0415

    return {"id": shot.id, "window": await open_editor_window(shot.id)}


@router.delete("/library/{shot_id}", openapi_extra={"x-jarvis-dangerous": True})
async def library_delete(shot_id: str, variant: LibraryVariant = "original") -> dict[str, Any]:
    """Delete an edit only, or — for the original — the whole appshot."""
    from jarvis.appshot import library  # noqa: PLC0415

    removed = await asyncio.to_thread(library.delete, _library_id(shot_id), variant)
    if not removed:
        raise HTTPException(status_code=404, detail="That appshot is no longer kept.")
    return {"ok": True}


@router.delete("/library", openapi_extra={"x-jarvis-dangerous": True})
async def library_clear() -> dict[str, Any]:
    """Delete every kept appshot and edit."""
    from jarvis.appshot import library  # noqa: PLC0415

    return {"ok": True, "removed": await asyncio.to_thread(library.clear)}
