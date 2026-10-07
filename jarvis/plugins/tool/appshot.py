"""``take_appshot`` — the live voice model's way to honour "take an appshot".

A running voice call does not pass through the brain's turn pipeline, so a
spoken "take an appshot" reaches the live model as words. This tool gives it
the same capture the global shortcut takes — the front window, privacy
filtered, with the shutter effect and sound — and returns the picture as an
image input for the answer.

``scope="screen"`` is for an explicit request for the whole screen ("an
appshot of my full screen"): it takes the monitor the cursor is on, through
the same service, instead of narrowing to one window. The default stays the
front window, so nothing wider than asked for is captured.
"""

from __future__ import annotations

import asyncio

#: Scope → the capture it takes. ``window`` is the front app window (Jarvis's
#: own floating overlays are looked past); ``screen`` is the whole monitor.
SCOPES = ("window", "screen")

#: Trusted framing in front of the untrusted screen evidence of a full-screen
#: appshot (the window appshot carries the service's own preamble).
_SCREEN_PREAMBLE = (
    "APPSHOT: a captured snapshot of the user's whole screen, supplied as context. "
    "It shows the screen at capture time, not a live view. Use it when asked about "
    "this image. A request to take another appshot requires a fresh successful "
    "take_appshot call; this earlier image is not evidence of a new capture."
)


def _app_bus():
    from jarvis.core.runtime_refs import get_brain_manager, get_web_app

    app = get_web_app()
    bus = getattr(getattr(app, "state", None), "bus", None)
    if bus is not None:
        return bus
    return getattr(get_brain_manager(), "_bus", None)


def _load_config():
    from jarvis.core.config import load_config  # noqa: PLC0415

    return load_config()


class AppshotTool:
    name = "take_appshot"
    description = (
        "Take an appshot: capture the user's front app window once, with the "
        "usual privacy filter, so you can see what they are working on. Use when "
        "the user asks for an appshot or asks you to look at their window. Set "
        "scope to 'screen' only when they explicitly ask for the whole or full "
        "screen; otherwise leave it at 'window'."
    )
    risk_tier = "monitor"
    schema = {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": list(SCOPES),
                "description": (
                    "'window' (default): the front app window. 'screen': the whole "
                    "monitor, only when the user explicitly asks for the full screen."
                ),
            }
        },
        "additionalProperties": False,
    }

    async def execute(self, args, ctx):
        from jarvis.core.protocols import ToolResult

        scope = str((args or {}).get("scope") or "window").strip().lower()
        if scope not in SCOPES:
            return ToolResult(
                False, None, f"Unknown appshot scope {scope!r}; use 'window' or 'screen'."
            )
        trace_id = getattr(ctx, "trace_id", None)
        if scope == "screen":
            return await _screen_appshot(trace_id, ctx)

        from jarvis.appshot.service import take_appshot

        result = await take_appshot(
            trigger="tool",
            bus=_app_bus(),
            deliver=False,
            trace_id=trace_id,
        )
        if not result.ok or result.shot is None:
            return ToolResult(False, None, result.message or "The appshot could not be taken.")
        return _picture(result.shot, ctx, await asyncio.to_thread(_load_config))


async def _screen_appshot(trace_id, ctx=None):
    """The whole monitor the cursor is on, recorded as an appshot."""
    import uuid
    from dataclasses import replace

    from jarvis.appshot.service import record_turn_capture, shot_from_context
    from jarvis.core.protocols import ToolResult
    from jarvis.screen_context.models import IntentVerdict, VisualIntent
    from jarvis.screen_context.turn import get_service, model_note

    config = await asyncio.to_thread(_load_config)
    if not config.screen_context.enabled:
        return ToolResult(
            False,
            None,
            "Appshots are switched off. Turn them on under Settings > Appshots.",
        )
    bus = _app_bus()
    service = get_service(bus=bus)
    outcome = await service.capture(
        verdict=IntentVerdict(intent=VisualIntent.SCREEN, evidence=("appshot-screen",)),
        trace_id=trace_id or uuid.uuid4(),
    )
    context = service.consume(outcome.handle_id) if outcome.handle_id else None
    if outcome.status != "captured" or context is None:
        return ToolResult(False, None, outcome.message or "The appshot could not be taken.")
    await record_turn_capture(context, bus=bus, trigger="tool")
    # The shared appshot preamble speaks of the front window; this one is not.
    shot = replace(
        shot_from_context(context, trigger="tool"),
        note=f"{_SCREEN_PREAMBLE}\n{model_note(context)}",
    )
    return _picture(shot, ctx, config)


def _picture(shot, ctx=None, config=None):
    import base64

    from jarvis.core.image_references import get_store, instruction, scope_for, ttl_for
    from jarvis.core.protocols import ToolResult

    scope = scope_for(getattr(ctx, "config", None), getattr(ctx, "trace_id", ""))
    ref = get_store().add(
        scope, shot.image, shot.mime, source="appshot", ttl_s=ttl_for(scope, config),
    )

    return ToolResult(
        True,
        {
            "description": f"Appshot of the {shot.label}, {shot.width}x{shot.height}.",
            "app": shot.app_name,
            "evidence": shot.note,
            "image_ref": ref,
            "handoff": instruction([ref]),
            "_image": {
                "mime": shot.mime,
                "data": base64.b64encode(shot.image).decode("ascii"),
            },
        },
    )
