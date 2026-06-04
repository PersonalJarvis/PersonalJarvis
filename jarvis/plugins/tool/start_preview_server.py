"""StartPreviewServerTool — registriert einen laufenden Dev-Server in der Preview-Registry.

Sub-Agent ruft dieses Tool auf sobald sein Dev-Server laeuft.
Das Tool publisht ``PreviewServerStarted`` auf den Bus → die PreviewRegistry
aktualisiert die Liste → das Frontend zeigt den iframe in der Previews-View.
"""
from __future__ import annotations

from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


class StartPreviewServerTool:
    name = "start_preview_server"
    description = (
        "Registriert einen laufenden localhost-Dev-Server in der Jarvis-UI "
        "(Previews-Sidebar-View). Aufruf NACHDEM der Server laeuft."
    )
    risk_tier = "safe"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "port": {"type": "integer", "description": "Port des Dev-Servers."},
            "title": {
                "type": "string",
                "description": "Anzeige-Name in der UI.",
                "default": "",
            },
            "kind": {
                "type": "string",
                "description": "Server-Typ: 'vite' | 'flask' | 'django' | 'static' | ...",
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
            return ToolResult(success=False, error="port ist 0 oder leer")
        title = (args.get("title") or f"Dev-Server :{port}").strip()
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
                output=f"Dev-Server '{title}' auf {url} in Previews registriert.",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"Preview-Registrierung fehlgeschlagen: {exc}")
