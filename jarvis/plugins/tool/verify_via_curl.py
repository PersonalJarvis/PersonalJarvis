"""VerifyViaCurlTool — HTTP-Check fuer Sub-Agent-Verifikation."""
from __future__ import annotations

from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


class VerifyViaCurlTool:
    name = "verify_via_curl"
    description = (
        "Prueft eine URL per HTTP-GET. Gibt Erfolg zurueck wenn Status 200 "
        "und optionaler Substring im Body gefunden."
    )
    risk_tier = "safe"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL die geprueft werden soll."},
            "expected_substring": {
                "type": "string",
                "description": "Optionaler Substring der im Body erwartet wird.",
                "default": "",
            },
            "timeout_s": {
                "type": "number",
                "description": "Timeout in Sekunden (Default 5).",
                "default": 5.0,
            },
        },
        "required": ["url"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        url = (args.get("url") or "").strip()
        if not url:
            return ToolResult(success=False, error="url ist leer")
        substring = (args.get("expected_substring") or "").strip()
        timeout = float(args.get("timeout_s") or 5.0)

        try:
            import httpx

            r = httpx.get(url, timeout=timeout, follow_redirects=True)
            if r.status_code != 200:
                return ToolResult(
                    success=False,
                    output=f"HTTP {r.status_code} fuer {url}",
                )
            if substring and substring not in r.text:
                return ToolResult(
                    success=False,
                    output=f"Substring '{substring}' nicht im Body von {url}",
                )
            return ToolResult(
                success=True,
                output=f"HTTP 200 OK ({url})" + (f" — '{substring}' gefunden" if substring else ""),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"Request fehlgeschlagen: {exc}")
