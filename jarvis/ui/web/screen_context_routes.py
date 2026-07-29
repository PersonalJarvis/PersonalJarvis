"""REST API for Screen Context — one-shot, on-request screen look.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET    /api/screen-context/status          → capability + settings + held count.
    POST   /api/screen-context/classify        → what a given utterance would do.
    POST   /api/screen-context/capture         → take ONE capture now.
    GET    /api/screen-context/{id}            → metadata for a held capture.
    POST   /api/screen-context/{id}/consume    → take the image out (single use).
    DELETE /api/screen-context                 → discard every held capture.
    GET    /api/screen-context/settings        → the [screen_context] block.
    PUT    /api/screen-context/settings        → change one or more keys.

Why REST and not only an internal service: under the CLI-first contract
(CLAUDE.md §5) a capability that exists only inside the voice path is not
finished. Mounting this router makes every action a
``jarvis api screen-context <op>`` command, which is also how the feature is
verified on a machine with no display — ``status`` answers honestly instead of
pretending, and ``capture`` returns a named refusal rather than a stack trace.

Two deliberate shapes:

* ``POST /capture`` returns **metadata only**. The image comes from
  ``/{id}/consume``, and that call removes it. Returning pixels from the
  capture call would make the single-use contract a suggestion — anyone could
  re-POST for a fresh copy of the same screen without a fresh trigger.
* ``POST /classify`` captures nothing. It exists so the settings UI (and a
  curious user) can see exactly how an utterance would be read, which is the
  only honest way to explain a three-valued gate.
"""
from __future__ import annotations

import base64
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.screen_context.models import VisualIntent
from jarvis.screen_context.service import (
    ScreenContextService,
    settings_from_config,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screen-context", tags=["screen-context"])

#: Process-wide service. Built on first use, never at import (AP-26), and
#: rebuilt whenever settings change so a config edit takes effect without a
#: restart.
_service: ScreenContextService | None = None


def _get_service(request: Request) -> ScreenContextService:
    global _service
    if _service is None:
        from jarvis.core.config import load_config  # noqa: PLC0415

        _service = ScreenContextService(
            settings=settings_from_config(load_config()),
            bus=getattr(request.app.state, "bus", None),
        )
    return _service


def _reset_service() -> None:
    """Drop the cached service so the next call picks up new settings.

    Also discards anything it still holds: a settings change that tightens the
    denylist must not leave a capture taken under the old rules in memory.
    """
    global _service
    if _service is not None:
        _service.discard_all()
    _service = None


class ClassifyRequest(BaseModel):
    text: str = Field(default="", description="The utterance to classify.")
    locale: str = Field(
        default="en",
        description="Already-resolved output language (BCP-47 base, e.g. 'de').",
    )


class CaptureRequest(BaseModel):
    text: str = Field(
        default="",
        description="User utterance. Empty with force=true for an explicit capture.",
    )
    locale: str = Field(default="en", description="Resolved output language.")
    force: bool = Field(
        default=True,
        description=(
            "Skip intent classification (an explicit API call IS the intent). "
            "Never skips permissions, the privacy denylist, or redaction."
        ),
    )


class SettingsPatch(BaseModel):
    enabled: bool | None = None
    denylist: list[str] | None = None
    sensitive_patterns: list[str] | None = None
    include_default_patterns: bool | None = None
    max_text_chars: int | None = None
    ttl_s: float | None = None
    ocr_enabled: bool | None = None


def _context_metadata(context: Any, handle_id: str | None) -> dict[str, Any]:
    """Everything about a capture EXCEPT its content.

    Used for the capture response and the receipt endpoint. Image bytes and
    captured text are deliberately absent: this payload ends up in logs and
    the flight recorder, and screen content must not.
    """
    return {
        "id": handle_id,
        "receipt": context.describe(),
        "target": {
            "kind": str(context.target.kind),
            "reason": str(context.target.reason),
            "monitor": context.target.monitor_name,
            "bbox": list(context.target.bbox),
            "app": context.target.window.app_name,
            "window_title": context.target.window.title,
        },
        "image": {
            "mime": context.mime,
            "width": context.size[0],
            "height": context.size[1],
            "bytes": context.byte_size,
        },
        "ui_text": {
            "source": context.ui_text_source,
            "chars": len(context.ui_text),
        },
        "redactions": {
            "summary": context.redactions.summary(),
            "image_regions": context.redactions.region_count,
            "text_matches": context.redactions.text_count,
            "labels": sorted({h.label for h in context.redactions.hits if h.label}),
        },
        "degradations": [
            {"code": str(d.code), "message": d.message} for d in context.degradations
        ],
        "captured_at_ns": context.captured_at_ns,
    }


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    """Capability and live state — answers honestly on a machine with no screen."""
    service = _get_service(request)
    from jarvis.screen_context.ports import capture_permission_error  # noqa: PLC0415

    monitors = service.displays.monitors()
    permission_error = capture_permission_error()
    return {
        "enabled": service.settings.enabled,
        "available": bool(monitors) and permission_error is None,
        "blocked_reason": permission_error,
        "monitor_count": max(0, len(monitors) - 1) if monitors else 0,
        "cursor_readable": service.cursor.position() is not None,
        "held_captures": service.held_count,
        "ttl_s": service.settings.ttl_s,
    }


@router.post("/classify")
async def classify(request: Request, body: ClassifyRequest) -> dict[str, Any]:
    """What would happen for this utterance — without capturing anything."""
    service = _get_service(request)
    verdict = service.classify(body.text, locale=body.locale)
    action = {
        VisualIntent.NONE: "nothing",
        VisualIntent.AMBIGUOUS: "ask",
        VisualIntent.SCREEN: "capture_screen",
        VisualIntent.WINDOW: "capture_window",
    }[verdict.intent]
    payload: dict[str, Any] = {
        "intent": str(verdict.intent),
        "action": action,
        "evidence": list(verdict.evidence),
    }
    if verdict.needs_clarification:
        from jarvis.screen_context.intent import clarifying_question  # noqa: PLC0415

        payload["question"] = clarifying_question(body.locale)
    return payload


@router.post("/capture")
async def capture(request: Request, body: CaptureRequest) -> dict[str, Any]:
    """Take exactly one capture. Returns metadata; the image is fetched once."""
    service = _get_service(request)
    outcome = await service.capture_for_turn(
        body.text, locale=body.locale, force=body.force
    )

    if outcome.status == "captured" and outcome.context is not None:
        return {"status": "captured", **_context_metadata(outcome.context, outcome.handle_id)}
    if outcome.status == "clarify":
        return {
            "status": "clarify",
            "question": outcome.question,
            "evidence": list(outcome.verdict.evidence),
        }
    if outcome.status == "not_requested":
        return {"status": "not_requested", "intent": str(outcome.verdict.intent)}
    return {"status": "refused", "reason": outcome.message}


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    from jarvis.core.config import load_config  # noqa: PLC0415

    block = load_config().screen_context
    return {
        "enabled": block.enabled,
        "denylist": list(block.denylist),
        "sensitive_patterns": list(block.sensitive_patterns),
        "include_default_patterns": block.include_default_patterns,
        "max_text_chars": block.max_text_chars,
        "ttl_s": block.ttl_s,
        "ocr_enabled": block.ocr_enabled,
    }


@router.put("/settings")
async def put_settings(request: Request, patch: SettingsPatch) -> dict[str, Any]:
    """Change one or more keys, then rebuild the service so it takes effect."""
    import asyncio  # noqa: PLC0415

    from jarvis.core.config import ScreenContextConfig  # noqa: PLC0415
    from jarvis.core.config_writer import set_screen_context_setting  # noqa: PLC0415

    changes = patch.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No settings were provided.")

    # Validate against the model BEFORE touching the file, so a rejected value
    # cannot leave half the patch written.
    try:
        ScreenContextConfig(**changes)
    except Exception as exc:  # noqa: BLE001 — pydantic raises its own type
        raise HTTPException(
            status_code=400, detail=f"Invalid setting: {exc}"
        ) from exc

    def _write() -> None:
        for key, value in changes.items():
            set_screen_context_setting(key, value)

    try:
        await asyncio.to_thread(_write)
    except Exception as exc:  # noqa: BLE001 — surface the write failure honestly
        log.error("screen_context: settings write failed", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Could not save the settings: {exc}"
        ) from exc

    _reset_service()
    return {"ok": True, "changed": sorted(changes)}


@router.get("/{capture_id}")
async def get_capture(request: Request, capture_id: str) -> dict[str, Any]:
    """Metadata for a held capture WITHOUT consuming it (the receipt)."""
    service = _get_service(request)
    context = service.peek(capture_id)
    if context is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "That capture is gone — it was either already used or it "
                "expired. Captures are single-use and short-lived by design."
            ),
        )
    return _context_metadata(context, capture_id)


@router.post("/{capture_id}/consume")
async def consume_capture(request: Request, capture_id: str) -> dict[str, Any]:
    """Take the image and text out. The capture is removed by this call."""
    service = _get_service(request)
    context = service.consume(capture_id)
    if context is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "That capture is gone — it was either already used or it "
                "expired. Captures are single-use and short-lived by design."
            ),
        )
    payload = _context_metadata(context, capture_id)
    payload["image_base64"] = base64.b64encode(context.image).decode("ascii")
    payload["text"] = context.ui_text
    return payload


@router.delete("", openapi_extra={"x-jarvis-dangerous": True})
async def discard_all(request: Request) -> dict[str, Any]:
    """Drop every held capture right now — the panic button."""
    service = _get_service(request)
    return {"ok": True, "discarded": service.discard_all()}
