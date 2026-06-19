"""VerifyLocalhostTool — HTTP-Check gegen localhost mit optionalem Screenshot."""
from __future__ import annotations

from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


class VerifyLocalhostTool:
    name = "verify_localhost"
    description = (
        "Prueft einen laufenden localhost-Server per HTTP. "
        "Optionaler Screenshot via mss fuer visuelle Verifikation."
    )
    risk_tier = "safe"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "port": {"type": "integer", "description": "Localhost-Port."},
            "path": {
                "type": "string",
                "description": "URL-Pfad (Default: '/').",
                "default": "/",
            },
            "expected_substring": {
                "type": "string",
                "description": "Optionaler Substring im Body.",
                "default": "",
            },
            "take_screenshot": {
                "type": "boolean",
                "description": "Screenshot des Browsers machen (via mss, nur sichtbarer Screen).",
                "default": False,
            },
        },
        "required": ["port"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        port = int(args.get("port") or 0)
        if not port:
            return ToolResult(success=False, error="port ist 0 oder leer")
        path = (args.get("path") or "/").strip()
        if not path.startswith("/"):
            path = "/" + path
        substring = (args.get("expected_substring") or "").strip()
        take_screenshot = bool(args.get("take_screenshot", False))

        url = f"http://localhost:{port}{path}"
        artifacts: list[Any] = []

        try:
            import httpx

            r = httpx.get(url, timeout=5.0, follow_redirects=True)
            ok = r.status_code == 200
            if ok and substring and substring not in r.text:
                ok = False
                msg = f"HTTP 200 aber Substring '{substring}' fehlt ({url})"
            elif not ok:
                msg = f"HTTP {r.status_code} ({url})"
            else:
                msg = f"HTTP 200 OK ({url})"
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"Verbindung zu {url} fehlgeschlagen: {exc}")

        if take_screenshot:
            try:
                import mss  # type: ignore[import-not-found]
                import mss.tools

                with mss.mss() as sct:
                    sct_img = sct.shot()
                    artifacts.append({"screenshot_path": sct_img})
            except Exception as exc:  # noqa: BLE001
                artifacts.append({"screenshot_error": str(exc)})

        return ToolResult(
            success=ok,
            output=msg,
            artifacts=tuple(artifacts) if artifacts else None,
        )
