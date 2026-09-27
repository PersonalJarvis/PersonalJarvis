"""An explicitly requested, privacy-filtered frame for a live vision model."""

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

        from jarvis.core.config import load_config
        from jarvis.core.protocols import ToolResult
        from jarvis.screen_context.service import ScreenContextService, settings_from_config

        service = ScreenContextService(settings=settings_from_config(load_config()))
        try:
            outcome = await service.capture(trace_id=ctx.trace_id)
            image = service.consume(outcome.handle_id) if outcome.handle_id else None
            if image is None:
                return ToolResult(False, None, outcome.message or "Screen capture unavailable.")
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
        finally:
            service.close()
