"""VerifyViaCurlTool — HTTP check for Jarvis-Agent worker verification."""
from __future__ import annotations

import asyncio
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
            return ToolResult(success=False, output=None, error="url is empty")
        substring = (args.get("expected_substring") or "").strip()
        # Clamp the model-controlled timeout to a sane range: a 0/negative
        # value would hang forever, an unbounded huge one lets a single call
        # wedge the caller for arbitrarily long.
        timeout = min(max(float(args.get("timeout_s") or 5.0), 0.5), 30.0)

        try:
            import httpx

            # httpx.get is synchronous — run it off the event loop so a slow/
            # unreachable host cannot block every other in-flight task.
            r = await asyncio.to_thread(
                httpx.get, url, timeout=timeout, follow_redirects=True
            )
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
            return ToolResult(success=False, output=None, error=f"Request failed: {exc}")
