"""REST API for appshots — the front window as conversation context.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET    /api/appshot/settings         → switches, shortcut state, readiness.
    PUT    /api/appshot/settings         → change one or more switches.
    POST   /api/appshot/take             → take one appshot now (optional delay).
    GET    /api/appshot/latest           → metadata of the last appshot.
    GET    /api/appshot/latest/image     → its picture (never cached).
    GET    /api/appshot/pending          → the appshot waiting for the next message.
    POST   /api/appshot/pending/claim    → hand that one to the chat composer.
    DELETE /api/appshot                  → forget every held appshot now.

Under the CLI-first contract every action here is also a
``jarvis api appshot <op>`` command. Pixels stay in memory
(``jarvis.appshot.store``); nothing here writes an image to disk.
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
    target: Literal["auto", "message", "voice"] | None = None
    sound: bool | None = None
    effect: bool | None = None


class TakeRequest(BaseModel):
    #: Seconds to wait first, so the user can bring the window they mean to
    #: the front after pressing the button inside this app.
    delay_s: float = Field(default=0.0, ge=0.0, le=10.0)


class ClaimRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64)


def _bus(request: Request) -> Any | None:
    return getattr(request.app.state, "bus", None)


def _capability() -> dict[str, Any]:
    import importlib.util  # noqa: PLC0415

    from jarvis.cu.indicator.controller import screen_indicator_capability  # noqa: PLC0415

    capture_ok = importlib.util.find_spec("mss") is not None
    effect_ok, effect_reason = screen_indicator_capability()
    return {
        "capture": capture_ok,
        "capture_detail": "" if capture_ok else "The screen-capture package is not installed.",
        "effect": effect_ok,
        "effect_detail": effect_reason,
    }


def _settings_payload() -> dict[str, Any]:
    from jarvis.appshot.hotkey import get_shortcut, normalize_hotkey  # noqa: PLC0415
    from jarvis.core.config import load_config  # noqa: PLC0415

    config = load_config()
    block = config.appshot
    shortcut = get_shortcut()
    status = (
        shortcut.status.to_json()
        if shortcut is not None
        else {"hotkey": normalize_hotkey(block.hotkey), "armed": False, "detail": ""}
    )
    return {
        "enabled": bool(config.screen_context.enabled),
        "hotkey": normalize_hotkey(block.hotkey),
        "target": block.target,
        "sound": bool(block.sound),
        "effect": bool(block.effect),
        "sound_effects_master": bool(getattr(config.ui, "sound_effects", True)),
        "shortcut": status,
        "readiness": _capability(),
    }


@router.get("/settings")
async def get_settings() -> dict[str, Any]:
    return await asyncio.to_thread(_settings_payload)


@router.put("/settings")
async def put_settings(request: Request, patch: SettingsPatch) -> dict[str, Any]:
    """Write the changed switches, then re-arm the shortcut in place."""
    from jarvis.appshot.hotkey import get_shortcut, normalize_hotkey  # noqa: PLC0415
    from jarvis.core.config_writer import (  # noqa: PLC0415
        set_appshot_settings,
        set_screen_context_settings,
    )

    changes = patch.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No settings were provided.")
    enabled = changes.pop("enabled", None)
    if "hotkey" in changes:
        changes["hotkey"] = normalize_hotkey(changes["hotkey"])
        if changes["hotkey"] and changes["hotkey"] != "alt+alt":
            from jarvis.trigger.hotkey import validate_hotkey  # noqa: PLC0415

            verdict = validate_hotkey(changes["hotkey"])
            if not verdict.ok:
                raise HTTPException(status_code=400, detail=verdict.reason or "Invalid shortcut.")

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
    if shortcut is not None and "hotkey" in changes:
        await shortcut.reload()
    return await asyncio.to_thread(_settings_payload)


@router.post("/take")
async def take(request: Request, body: TakeRequest | None = None) -> dict[str, Any]:
    """Take one appshot of the front window and deliver it like the shortcut."""
    from jarvis.appshot.service import take_appshot  # noqa: PLC0415

    delay = (body or TakeRequest()).delay_s
    if delay:
        await asyncio.sleep(delay)
    result = await take_appshot(trigger="button", bus=_bus(request))
    if not result.ok or result.shot is None:
        return {"ok": False, "reason": result.reason_code, "message": result.message}
    return {"ok": True, "appshot": result.shot.meta()}


@router.get("/latest")
async def latest() -> dict[str, Any]:
    from jarvis.appshot.store import get_store  # noqa: PLC0415

    shot = await asyncio.to_thread(get_store().latest)
    return {"appshot": shot.meta() if shot is not None else None}


@router.get("/latest/image")
async def latest_image() -> Response:
    from jarvis.appshot.store import get_store  # noqa: PLC0415

    shot = await asyncio.to_thread(get_store().latest)
    if shot is None:
        raise HTTPException(status_code=404, detail="No appshot is being kept right now.")
    return Response(content=shot.image, media_type=shot.mime, headers=_NO_STORE)


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
