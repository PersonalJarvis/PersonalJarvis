"""An explicitly requested, privacy-filtered frame for a live vision model.

The capture runs through the shared Screen Context service, so the user sees
and hears it like every other look: the appshot shutter flash, the
click-clack, and a receipt in the Appshots history.
"""

from __future__ import annotations


class LiveScreenTool:
    name = "screen_snapshot"
    description = (
        "Capture the current screen once, respecting screen privacy settings. "
        "Use for visual questions and before desktop actions."
    )
    risk_tier = "monitor"
    schema = {"type": "object", "properties": {}, "additionalProperties": False}

    async def execute(self, args, ctx):
        import base64

        from jarvis.appshot.service import record_turn_capture
        from jarvis.core.protocols import ToolResult
        from jarvis.plugins.tool.appshot import _app_bus
        from jarvis.screen_context.turn import get_service

        del args
        bus = _app_bus()
        service = get_service(bus=bus)
        outcome = await service.capture(trace_id=ctx.trace_id)
        image = service.consume(outcome.handle_id) if outcome.handle_id else None
        if image is None:
            return ToolResult(False, None, outcome.message or "Screen capture unavailable.")
        await record_turn_capture(image, bus=bus, trigger="tool")
        return ToolResult(
            True,
            {
                "description": image.describe(),
                "ui_text": image.ui_text,
                "_image": {
                    "mime": image.mime,
                    "data": base64.b64encode(image.image).decode("ascii"),
                },
            },
        )
