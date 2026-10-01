"""``take_appshot`` — the live voice model's way to honour "take an appshot".

A running voice call does not pass through the brain's turn pipeline, so a
spoken "take an appshot" reaches the live model as words. This tool gives it
the same capture the global shortcut takes — the front window, privacy
filtered, with the shutter effect and sound — and returns the picture as an
image input for the answer.
"""

from __future__ import annotations


def _app_bus():
    from jarvis.core.runtime_refs import get_brain_manager, get_web_app

    app = get_web_app()
    bus = getattr(getattr(app, "state", None), "bus", None)
    if bus is not None:
        return bus
    return getattr(get_brain_manager(), "_bus", None)


class AppshotTool:
    name = "take_appshot"
    description = (
        "Take an appshot: capture the user's front window once, with the usual "
        "privacy filter, so you can see what they are working on. Use when the "
        "user asks for an appshot or asks you to look at their window or screen."
    )
    risk_tier = "monitor"
    schema = {"type": "object", "properties": {}, "additionalProperties": False}

    async def execute(self, args, ctx):
        import base64

        from jarvis.appshot.service import take_appshot
        from jarvis.core.protocols import ToolResult

        del args
        result = await take_appshot(
            trigger="tool",
            bus=_app_bus(),
            deliver=False,
            trace_id=getattr(ctx, "trace_id", None),
        )
        if not result.ok or result.shot is None:
            return ToolResult(False, None, result.message or "The appshot could not be taken.")
        shot = result.shot
        return ToolResult(
            True,
            {
                "description": f"Appshot of the {shot.label}, {shot.width}x{shot.height}.",
                "app": shot.app_name,
                "evidence": shot.note,
                "_image": {
                    "mime": shot.mime,
                    "data": base64.b64encode(shot.image).decode("ascii"),
                },
            },
        )
