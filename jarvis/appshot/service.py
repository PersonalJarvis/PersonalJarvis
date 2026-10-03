"""Take an appshot and put it where the conversation will see it.

One entry point for every trigger — the global shortcuts, the Appshots page's
test buttons, the live model's ``take_appshot`` tool — plus
:func:`record_turn_capture` for looks a conversation turn took itself. The
capture always runs through the shared Screen Context service, so the privacy
denylist, redaction, the pre-shutter indicator and the no-disk retention rule
apply unchanged; this module only decides what happens to the picture.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Literal

from jarvis.appshot.store import Appshot, get_store

log = logging.getLogger(__name__)

Trigger = Literal["hotkey", "voice", "tool", "button"]
#: ``window``: the front window. ``region``: an area the user drags out first.
Scope = Literal["window", "region"]

#: Trusted framing in front of the untrusted screen evidence block.
_APPSHOT_PREAMBLE = (
    "APPSHOT: a captured snapshot of the user's front window, supplied as context. "
    "It shows the window at capture time, not a live view. Use it when asked about "
    "this image. A request to take another appshot requires a fresh successful "
    "take_appshot call; this earlier image is not evidence of a new capture."
)


@dataclass(frozen=True, slots=True)
class AppshotResult:
    status: Literal["captured", "refused"]
    shot: Appshot | None = None
    message: str = ""
    reason_code: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "captured" and self.shot is not None


def _load_config() -> Any:
    from jarvis.core.config import load_config  # noqa: PLC0415

    return load_config()


def shot_from_context(context: Any, *, trigger: str) -> Appshot:
    """Wrap a finished, redacted Screen Context capture as an appshot."""
    from jarvis.screen_context.turn import model_note  # noqa: PLC0415

    target = context.target
    if str(target.kind) == "window":
        label = "active window"
    elif str(target.kind) == "region":
        label = "selected area"
    else:
        label = f"monitor {target.monitor_name}" if target.monitor_name else "selected monitor"
    return Appshot(
        id=uuid.uuid4().hex,
        image=context.image,
        mime=context.mime,
        width=int(context.size[0]),
        height=int(context.size[1]),
        label=label,
        app_name=str(getattr(target.window, "app_name", "") or ""),
        note=f"{_APPSHOT_PREAMBLE}\n{model_note(context)}",
        ui_text=context.ui_text,
        trigger=trigger,
        taken_at=time.time(),
    )


async def take_appshot(
    *,
    trigger: Trigger,
    bus: Any | None = None,
    deliver: bool = True,
    trace_id: uuid.UUID | None = None,
    scope: Scope = "window",
) -> AppshotResult:
    """Capture once and deliver it per ``[appshot].target``.

    ``scope="window"`` takes the front window; ``scope="region"`` first lets
    the user drag out an area (:mod:`jarvis.appshot.region`) and takes exactly
    that — a cancelled selection is a refusal with ``reason_code="cancelled"``.
    ``deliver=False`` is for a caller that consumes the picture itself (the
    live model's tool). Never raises; a refusal carries the user-facing reason.
    """
    from jarvis.screen_context.models import IntentVerdict, VisualIntent  # noqa: PLC0415
    from jarvis.screen_context.turn import get_service  # noqa: PLC0415

    try:
        config = await asyncio.to_thread(_load_config)
        if not config.screen_context.enabled:
            return AppshotResult(
                status="refused",
                reason_code="disabled",
                message="Appshots are switched off. Turn them on under Settings > Appshots.",
            )
        service = get_service(bus=bus)
        capture_trace_id = trace_id or uuid.uuid4()
        if scope == "region":
            picked = await _pick_area(service, trace_id=capture_trace_id)
            if isinstance(picked, AppshotResult):
                return picked
            outcome = await service.capture(
                verdict=IntentVerdict(intent=VisualIntent.SCREEN, evidence=("appshot-region",)),
                trace_id=capture_trace_id,
                region=picked,
            )
        else:
            outcome = await service.capture(
                verdict=IntentVerdict(intent=VisualIntent.WINDOW, evidence=("appshot",)),
                trace_id=capture_trace_id,
            )
        if outcome.status != "captured" or outcome.context is None:
            return AppshotResult(
                status="refused",
                reason_code=outcome.reason_code or outcome.reason_kind or "refused",
                message=outcome.message or "The appshot could not be taken.",
            )
        if outcome.handle_id:
            service.consume(outcome.handle_id)
        shot = shot_from_context(outcome.context, trigger=trigger)
    except Exception:  # noqa: BLE001 - a shortcut press must never crash the app
        log.error("appshot: capture failed", exc_info=True)
        return AppshotResult(
            status="refused",
            reason_code="failure",
            message="The appshot failed. Nothing was captured.",
        )

    store = get_store()
    store.remember(shot, keep_s=float(config.screen_context.deck_preview_s))
    await _attach_to_card(shot, config)
    delivered_to = "turn"
    if deliver:
        delivered_to = await _deliver(
            shot,
            target=str(config.appshot.target),
            ttl_s=float(config.screen_context.ttl_s),
        )
    store.mark_delivered(shot.id, delivered_to)
    await _publish(bus, shot, delivered_to)
    log.info(
        "appshot: %s %dx%d via %s -> %s",
        shot.label,
        shot.width,
        shot.height,
        trigger,
        delivered_to,
    )
    return AppshotResult(status="captured", shot=replace(shot, delivered_to=delivered_to))


async def _pick_area(
    service: Any, *, trace_id: uuid.UUID | None = None,
) -> tuple[int, int, int, int] | AppshotResult:
    """Run the area picker; the chosen rectangle, or the refusal to return."""
    from jarvis.appshot.region import (  # noqa: PLC0415
        RegionUnavailable,
        pick_region,
        selection_to_bbox,
    )
    from jarvis.platform.screen_access import ScreenCaptureRefused

    try:
        selection = await pick_region(trace_id=trace_id)
    except ScreenCaptureRefused as exc:
        log.info("appshot: area selection refused (%s)", exc.reason or "capture_permission")
        return AppshotResult(
            status="refused", reason_code="capture_permission", message=exc.user_detail,
        )
    except RegionUnavailable as exc:  # reported to the caller as a refused appshot
        return AppshotResult(status="refused", reason_code="region_unavailable", message=str(exc))
    if selection is None:
        return AppshotResult(
            status="refused", reason_code="cancelled", message="No area was selected."
        )
    monitors = await asyncio.to_thread(service.displays.monitors)
    bbox = selection_to_bbox(selection, monitors)
    if bbox is None:
        return AppshotResult(
            status="refused",
            reason_code="no_display",
            message="No screen could be found for the selected area.",
        )
    return bbox


async def record_turn_capture(context: Any, *, bus: Any | None, trigger: Trigger) -> None:
    """A conversation turn looked at the screen itself — show it as an appshot."""
    try:
        config = await asyncio.to_thread(_load_config)
        shot = shot_from_context(context, trigger=trigger)
        store = get_store()
        store.remember(shot, keep_s=float(config.screen_context.deck_preview_s))
        await _attach_to_card(shot, config)
        store.mark_delivered(shot.id, "turn")
        await _publish(bus, shot, "turn")
    except Exception:  # noqa: BLE001 - the turn already has its picture
        log.warning("appshot: could not record the turn's capture", exc_info=True)


async def _attach_to_card(shot: Appshot, config: Any) -> None:
    """The corner card may hand this picture out by drag — if it may be kept.

    ``deck_preview_s = 0`` means the user wants no picture kept around, so the
    card then only opens the editor and shares nothing.
    """
    if float(config.screen_context.deck_preview_s) <= 0:
        return
    from jarvis.appshot.effect import attach_card_image  # noqa: PLC0415

    await attach_card_image(shot.image)


async def _deliver(shot: Appshot, *, target: str, ttl_s: float) -> str:
    from jarvis.appshot.delivery import deliver_to_live  # noqa: PLC0415

    if target in ("auto", "voice") and await deliver_to_live(shot.image, shot.mime, shot.note):
        return "voice"
    if target == "voice":
        return "none"
    get_store().park(shot, ttl_s=ttl_s)
    return "message"


async def _publish(bus: Any | None, shot: Appshot, delivered_to: str) -> None:
    if bus is None:
        return
    try:
        from jarvis.core.events import AppshotTaken  # noqa: PLC0415

        await bus.publish(
            AppshotTaken(
                source_layer="appshot",
                appshot_id=shot.id,
                trigger=shot.trigger,
                delivered_to=delivered_to,
                target_label=shot.label,
                width=shot.width,
                height=shot.height,
            )
        )
    except Exception:  # noqa: BLE001 - a lost receipt cannot undo the appshot
        log.warning("appshot: receipt publication failed", exc_info=True)


def take_pending_for_turn() -> Appshot | None:
    """The appshot waiting for the next message, removed on the way out."""
    return get_store().take_pending()


__all__ = [
    "AppshotResult",
    "Scope",
    "record_turn_capture",
    "shot_from_context",
    "take_appshot",
    "take_pending_for_turn",
]
