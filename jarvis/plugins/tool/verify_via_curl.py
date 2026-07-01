"""VerifyViaCurlTool — HTTP check for Jarvis-Agent worker verification."""
from __future__ import annotations

from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


class VerifyViaCurlTool:
    name = "verify_via_curl"
    description = (
        "Checks a URL via HTTP GET. Returns success if status 200 "
        "and an optional substring is found in the body."
    )
    risk_tier = "safe"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to check."},
            "expected_substring": {
                "type": "string",
                "description": "Optional substring expected in the body.",
                "default": "",
            },
            "timeout_s": {
                "type": "number",
                "description": "Timeout in seconds (default 5).",
                "default": 5.0,
            },
        },
        "required": ["url"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        url = (args.get("url") or "").strip()
        if not url:
            return ToolResult(success=False, error="url is empty")
        substring = (args.get("expected_substring") or "").strip()
        timeout = float(args.get("timeout_s") or 5.0)

        try:
            import httpx

            r = httpx.get(url, timeout=timeout, follow_redirects=True)
            if r.status_code != 200:
                return ToolResult(
                    success=False,
                    output=f"HTTP {r.status_code} for {url}",
                )
            if substring and substring not in r.text:
                return ToolResult(
                    success=False,
                    output=f"Substring '{substring}' not found in body of {url}",
                )
            return ToolResult(
                success=True,
                output=f"HTTP 200 OK ({url})" + (f" — '{substring}' found" if substring else ""),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"Request failed: {exc}")
