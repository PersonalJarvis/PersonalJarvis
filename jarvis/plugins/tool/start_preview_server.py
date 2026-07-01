"""StartPreviewServerTool — registers a running dev server in the preview registry.

The Jarvis-Agent worker calls this tool as soon as its dev server is running.
The tool publishes ``PreviewServerStarted`` on the bus → the PreviewRegistry
updates its list → the frontend shows the iframe in the Previews view.
"""
from __future__ import annotations

from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


class StartPreviewServerTool:
    name = "start_preview_server"
    description = (
        "Registers a running localhost dev server in the Jarvis UI "
        "(Previews sidebar view). Call AFTER the server is running."
    )
    risk_tier = "safe"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "port": {"type": "integer", "description": "Port of the dev server."},
            "title": {
                "type": "string",
                "description": "Display name in the UI.",
                "default": "",
            },
            "kind": {
                "type": "string",
                "description": "Server type: 'vite' | 'flask' | 'django' | 'static' | ...",
                "default": "unknown",
            },
        },
        "required": ["port"],
    }

    def __init__(self, bus: Any) -> None:
        self._bus = bus

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        port = int(args.get("port") or 0)
        if not port:
            return ToolResult(success=False, error="port is 0 or empty")
        title = (args.get("title") or f"Dev server :{port}").strip()
        kind = (args.get("kind") or "unknown").strip()
        url = f"http://localhost:{port}"

        try:
            from jarvis.preview.registry import PreviewServerStarted

            await self._bus.publish(
                PreviewServerStarted(
                    trace_id=ctx.trace_id,
                    port=port,
                    title=title,
                    kind=kind,
                    url=url,
                )
            )
            return ToolResult(
                success=True,
                output=f"Dev server '{title}' registered at {url} in Previews.",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"Preview registration failed: {exc}")
