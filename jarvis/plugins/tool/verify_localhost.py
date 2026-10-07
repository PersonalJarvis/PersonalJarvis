"""VerifyLocalhostTool — HTTP check against localhost with an optional screenshot."""
from __future__ import annotations

import asyncio
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


def _take_checked_screenshot() -> str:
    """Save the primary monitor to a PNG and return its path (blocking: a worker thread).

    The screenshot is an optional extra of this check, not a gesture of its own: the
    state is read silently (never asks) and a missing grant degrades with the honest,
    prohibitive permission text. The grabbed pixels are checked too, because macOS
    hands back the wallpaper, not an error, for a capture it does not allow.
    """
    import mss  # type: ignore[import-not-found]  # noqa: PLC0415
    import mss.tools  # noqa: PLC0415

    from jarvis.platform import screen_access  # noqa: PLC0415

    blocked_state = screen_access.screen_recording_state()
    if not screen_access.state_allows_capture(blocked_state):
        raise screen_access.refusal_for_state(blocked_state)

    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[1])
    screen_access.verify_frame_is_real(
        (int(raw.size[0]), int(raw.size[1])),
        raw.rgb,
        feature="screen_context",
        interactive=False,
    )
    path = "monitor-1.png"  # what ``mss.shot()`` writes for the primary monitor
    mss.tools.to_png(raw.rgb, raw.size, output=path)
    return path


class VerifyLocalhostTool:
    name = "verify_localhost"
    description = (
        "Checks a running localhost server via HTTP. "
        "Optional screenshot via mss for visual verification."
    )
    risk_tier = "safe"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "port": {"type": "integer", "description": "Localhost port."},
            "path": {
                "type": "string",
                "description": "URL path (default: '/').",
                "default": "/",
            },
            "expected_substring": {
                "type": "string",
                "description": "Optional substring expected in the body.",
                "default": "",
            },
            "take_screenshot": {
                "type": "boolean",
                "description": "Take a screenshot of the browser (via mss, visible screen only).",
                "default": False,
            },
        },
        "required": ["port"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        port = int(args.get("port") or 0)
        if not port:
            return ToolResult(success=False, output=None, error="port is 0 or empty")
        path = (args.get("path") or "/").strip()
        if not path.startswith("/"):
            path = "/" + path
        substring = (args.get("expected_substring") or "").strip()
        take_screenshot = bool(args.get("take_screenshot", False))

        url = f"http://localhost:{port}{path}"
        artifacts: list[Any] = []

        try:
            import httpx

            # httpx.get is synchronous — run it off the event loop so a slow/
            # hung localhost server cannot block every other in-flight task.
            r = await asyncio.to_thread(
                httpx.get, url, timeout=5.0, follow_redirects=True
            )
            ok = r.status_code == 200
            if ok and substring and substring not in r.text:
                ok = False
                msg = f"HTTP 200 but substring '{substring}' missing ({url})"
            elif not ok:
                msg = f"HTTP {r.status_code} ({url})"
            else:
                msg = f"HTTP 200 OK ({url})"
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False, output=None, error=f"Connection to {url} failed: {exc}"
            )

        if take_screenshot:
            try:
                # One worker-thread hop for the state read (it may enumerate
                # windows), the grab and the pixel check: none of it on the loop.
                path = await asyncio.to_thread(_take_checked_screenshot)
                artifacts.append({"screenshot_path": path})
            except Exception as exc:  # noqa: BLE001
                artifacts.append({"screenshot_error": str(exc)})

        return ToolResult(
            success=ok,
            output=msg,
            artifacts=tuple(artifacts) if artifacts else None,
        )
