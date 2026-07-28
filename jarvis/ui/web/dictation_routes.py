"""REST API for dictation mode — hold a key, speak, text lands where you type.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET    /api/dictation/status    → capability + live state + the shortcut.
    POST   /api/dictation/start     → begin a dictation ({"target": ...}).
    POST   /api/dictation/stop      → finish the running one.
    GET    /api/dictation/history   → recent dictations (raw + cleaned).
    DELETE /api/dictation/history   → purge everything (destructive).
    DELETE /api/dictation/history/{id} → drop one entry.
    GET    /api/dictation/settings  → the [dictation] block.
    PUT    /api/dictation/settings  → change one or more keys.

Why REST and not only the WebSocket command the chat mic button uses: under the
CLI-first contract (CLAUDE.md §5) a capability that exists only in the UI is not
finished. Mounting this router also makes every action a
``jarvis api dictation <op>`` command for free — and *that* is the documented
fallback on Wayland, where the compositor owns global shortcuts and the app
cannot bind one itself.

No Brain dependency, so it works headless and with a MockBrain; on a host with
no microphone the status endpoint answers honestly instead of 500-ing.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dictation", tags=["dictation"])


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _config(request: Request) -> Any:
    return getattr(request.app.state, "config", None)


def _pipeline() -> Any:
    """The live SpeechPipeline, or ``None`` (headless / voice disabled)."""
    try:
        from jarvis.core.runtime_refs import get_speech_pipeline

        return get_speech_pipeline()
    except Exception:  # noqa: BLE001 — a missing runtime ref is "no pipeline"
        return None


def _dictation_cfg(request: Request) -> Any:
    cfg = _config(request)
    dictation = getattr(cfg, "dictation", None) if cfg is not None else None
    if dictation is not None:
        return dictation
    from jarvis.core.config import DictationConfig

    return DictationConfig()


# ----------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------


class StartBody(BaseModel):
    target: str = Field(
        default="auto",
        description=(
            "auto = follow [dictation].target (insert, unless Jarvis itself is "
            "the window in front); insert = always paste into the app in front; "
            "chat = only publish the transcript (fills the chat composer)"
        ),
    )


class SettingsBody(BaseModel):
    """Partial update — only the keys present are changed."""

    mode: str | None = None
    target: str | None = None
    insert_method: str | None = None
    paste_chord: str | None = None
    paste_delay_ms: int | None = None
    paste_delay_after_ms: int | None = None
    restore_clipboard: bool | None = None
    remove_fillers: bool | None = None
    filler_max_removed_fraction: float | None = None
    max_seconds: float | None = None
    partial_interval_s: float | None = None
    segment_seconds: float | None = None
    history_enabled: bool | None = None
    history_max_entries: int | None = None
    history_retention_days: int | None = None
    persist: bool = Field(
        default=True, description="Also write the change to jarvis.toml"
    )


# ----------------------------------------------------------------------
# Status + control
# ----------------------------------------------------------------------


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    """Can dictation run here, is it running, and where would text go?

    Answers honestly on a host that cannot do it rather than hiding the
    feature: ``available`` false plus a ``reason`` the UI can show.
    """
    pipeline = _pipeline()
    cfg = _config(request)
    trigger = getattr(cfg, "trigger", None) if cfg is not None else None
    dictation = _dictation_cfg(request)

    available = False
    active = False
    reason = ""
    if pipeline is None:
        reason = "No speech pipeline is running (headless, or voice is disabled)."
    else:
        try:
            available = bool(pipeline.dictation_available())
            active = bool(pipeline.dictation_active())
            if not available:
                reason = "No microphone or no speech-to-text provider is configured."
        except Exception as exc:  # noqa: BLE001 — never 500 a status probe
            log.debug("dictation availability probe failed: %s", exc)
            reason = "Dictation status could not be read."

    # Whether the transcript could actually be pasted into another app. This is
    # the honest part: on Wayland, on a headless host, or in front of an
    # elevated window it cannot, and saying so up front beats a silent no-op.
    insertion: dict[str, Any] = {"can_insert": False, "reason": "", "detail": ""}
    try:
        from jarvis.dictation.insert import describe_target

        report = describe_target()
        insertion = {
            "can_insert": report.can_insert,
            "reason": report.reason,
            "detail": report.detail,
        }
    except Exception as exc:  # noqa: BLE001
        log.debug("insertion probe failed: %s", exc)

    return {
        "available": available,
        "active": active,
        "reason": reason,
        "hotkey": str(getattr(trigger, "hotkey_dictate", "") or ""),
        "mode": str(getattr(dictation, "mode", "hold")),
        "target": str(getattr(dictation, "target", "auto")),
        "insertion": insertion,
    }


@router.post("/start")
async def start(body: StartBody, request: Request) -> dict[str, Any]:
    """Begin a dictation. 409 when the mic is busy, 503 when there is none."""
    pipeline = _pipeline()
    if pipeline is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Dictation needs a running speech pipeline with a microphone; "
                "this host has none."
            ),
        )
    # "auto" defers to the configured target, which the pipeline resolves
    # against the live foreground window at the moment recording starts.
    target = body.target if body.target in ("chat", "insert") else str(
        getattr(_dictation_cfg(request), "target", "auto") or "auto"
    )
    try:
        started = bool(pipeline.start_dictation(target=target))
    except Exception as exc:  # noqa: BLE001
        log.warning("dictation start failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Dictation could not start.") from exc
    if not started:
        raise HTTPException(
            status_code=409,
            detail=(
                "Dictation could not start — a voice session is running, "
                "dictation is already active, or the microphone is not ready."
            ),
        )
    return {"ok": True, "active": True, "target": target}


@router.post("/stop")
async def stop() -> dict[str, Any]:
    """Finish the running dictation. Idempotent: stopping nothing is not an error."""
    pipeline = _pipeline()
    if pipeline is None:
        return {"ok": True, "stopped": False, "active": False}
    try:
        stopped = bool(pipeline.stop_dictation())
    except Exception as exc:  # noqa: BLE001
        log.warning("dictation stop failed: %s", exc, exc_info=True)
        stopped = False
    return {"ok": True, "stopped": stopped, "active": False}


# ----------------------------------------------------------------------
# History
# ----------------------------------------------------------------------


@router.get("/history")
async def get_history(limit: int = 50) -> dict[str, Any]:
    """Recent dictations, newest first — raw text alongside the cleaned text.

    Local-only data. It exists so a filler-cleanup can be audited after the
    fact ("did it drop a word I actually said?") and so a transcript survives
    an insertion that had to fall back to the clipboard.
    """
    from jarvis.dictation.history import DictationHistory

    capped = max(1, min(int(limit or 50), 500))
    entries = DictationHistory().list_all()[:capped]
    return {"entries": [e.to_dict() for e in entries], "count": len(entries)}


@router.delete(
    "/history",
    openapi_extra={"x-jarvis-dangerous": True},
)
async def clear_history() -> dict[str, Any]:
    """Purge the whole dictation history. Irreversible."""
    from jarvis.dictation.history import DictationHistory

    return {"ok": bool(DictationHistory().clear())}


@router.delete("/history/{entry_id}")
async def delete_history_entry(entry_id: str) -> dict[str, Any]:
    """Drop one entry (idempotent — removing an absent id is not an error)."""
    from jarvis.dictation.history import DictationHistory

    return {"removed": bool(DictationHistory().delete(entry_id))}


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    """The live ``[dictation]`` block plus the accepted values per key."""
    from jarvis.core.config_writer import DICTATION_SETTING_KEYS

    dictation = _dictation_cfg(request)
    values = {key: getattr(dictation, key, None) for key in DICTATION_SETTING_KEYS}
    return {
        "settings": values,
        "choices": {
            "mode": ["hold", "toggle"],
            "target": ["auto", "insert", "chat"],
            "insert_method": ["clipboard", "type"],
            "paste_chord": ["auto", "ctrl_v", "ctrl_shift_v", "shift_insert"],
        },
    }


@router.put("/settings")
async def put_settings(body: SettingsBody, request: Request) -> dict[str, Any]:
    """Change one or more ``[dictation]`` keys.

    Validated against ``DictationConfig`` BEFORE anything is written, so an
    out-of-range delay or an unknown mode is a 400 rather than a config file
    the app then refuses to boot from. Applies live to the running pipeline;
    ``max_seconds`` and the shortcut itself take effect immediately, and the
    rest are read per dictation anyway.
    """
    from jarvis.core.config import DictationConfig

    updates = {
        key: value
        for key, value in body.model_dump(exclude={"persist"}).items()
        if value is not None
    }
    if not updates:
        raise HTTPException(status_code=400, detail="No settings were provided.")

    dictation = _dictation_cfg(request)
    current = {
        key: getattr(dictation, key)
        for key in DictationConfig.model_fields
        if hasattr(dictation, key)
    }
    try:
        validated = DictationConfig(**{**current, **updates})
    except Exception as exc:  # noqa: BLE001 — pydantic ValidationError and friends
        raise HTTPException(status_code=400, detail=f"Invalid setting: {exc}") from exc

    # In-memory first so a running pipeline sees the change even if the write
    # fails (a read-only config file must not silently drop the setting).
    for key in updates:
        try:
            setattr(dictation, key, getattr(validated, key))
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory dictation.%s update skipped: %s", key, exc)

    persisted = False
    if body.persist:
        from jarvis.core import config_writer

        try:
            for key in updates:
                config_writer.set_dictation_setting(key, getattr(validated, key))
            persisted = True
        except Exception as exc:  # noqa: BLE001
            log.warning("dictation settings persist failed: %s", exc)

    # Live-apply what the running pipeline caches at construction time.
    applied_live = False
    pipeline = _pipeline()
    if pipeline is not None:
        try:
            pipeline._dictation_cfg = dictation
            pipeline._dictation_max_s = float(validated.max_seconds)
            if "mode" in updates:
                pipeline._dictate_mode = validated.mode
                # The mode decides whether the binding wants both key edges,
                # so the trigger has to re-arm for a hold<->toggle switch.
                if hasattr(pipeline, "set_keybinds"):
                    pipeline.set_keybinds()
            applied_live = True
        except Exception as exc:  # noqa: BLE001 — never fail a save on live-apply
            log.warning("dictation settings live-apply failed: %s", exc)

    return {
        "ok": True,
        "settings": {
            key: getattr(validated, key)
            for key in DictationConfig.model_fields
        },
        "persisted": persisted,
        "applied_live": applied_live,
    }
