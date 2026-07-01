"""screenshot tool: captures the active monitor as JPEG for Brain vision.

Risk tier: monitor — a screenshot reads screen content, so calls are logged
by default. A whitelist entry under `[safety.whitelist].tools` allows
continuous use without a confirm prompt.

Phase A1 (2026-04-25): switched the default format to JPEG q85. Vision LLMs
(Claude/Gemini/GPT) charge token cost by pixel area, not bytes — JPEG
shrinks the payload by ~8x for identical tokens. Quality 85 is the
sweet spot: visually lossless for text + UI elements, noticeably smaller
than PNG. Iterative shrinking is no longer needed — the quality param
handles that.

Multi-monitor fix: by default the capture follows the foreground window
(``select_capture_monitor`` from ``jarvis.vision.screenshot``). That way
the brain sees the screen the user is actually active on — instead of
hardcoding the mss primary, which is often blank on multi-monitor setups.

Flow in `execute`:
1. Enumerate ``mss.mss().monitors`` -> ``select_capture_monitor`` picks
   the right monitor (foreground-window lookup).
2. `PIL.Image.frombytes("RGB", size, raw.rgb)` builds a Pillow image.
3. JPEG q85 — typically 100-300 KB for 4K screenshots. If it exceeds
   _MAX_BYTES (edge case on very detail-dense screens), quality is lowered
   until it fits.
4. Result: `artifacts=[{"type": "image", "mime": "image/jpeg", "data":
   <base64>}]` so the vision-capable brain can consume the screenshot
   directly.
"""
from __future__ import annotations

import base64
import io
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.vision.screenshot import select_capture_monitor

_MAX_BYTES = 500_000
_DEFAULT_JPEG_QUALITY = 85
_MIN_JPEG_QUALITY = 50
# Vision LLMs (Claude/Gemini/GPT) internally downscale to ~1568 px on the
# longest side for token accounting. 4K captures (3840x2160) are pure
# waste — they cost ~3x the bytes and tokens with no visible vision benefit.
# 2048 px on the longest side is the sweet spot: noticeably larger than the
# internal LLM resampling target (no detail loss), but tight enough to
# reliably hit the _MAX_BYTES budget on 4K multi-monitor captures.
_MAX_DIMENSION = 2048


def _resize_for_budget(image: Any, max_dim: int) -> Any:
    """Proportionally scales a Pillow image to at most ``max_dim`` on its
    longest side.

    Identity when the image is already smaller. So the call costs nothing
    on 1440p captures.
    """
    w, h = image.size
    longest = max(w, h)
    if longest <= max_dim:
        return image
    scale = max_dim / longest
    new_size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    # Late import so the module stays importable without PIL; _encode_with_budget
    # is only ever called from paths that have PIL anyway.
    from PIL import Image  # noqa: PLC0415

    return image.resize(new_size, resample=Image.Resampling.LANCZOS)


def _encode_jpeg(image: Any, quality: int) -> bytes:
    """Serializes a Pillow image as JPEG at the given quality."""
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _encode_with_budget(image: Any, max_bytes: int) -> bytes:
    """JPEG q85 first; on overshoot, lower quality down to _MIN_JPEG_QUALITY.

    Before encoding, the image is downscaled to ``_MAX_DIMENSION`` on its
    longest side so 4K captures (multi-monitor) reliably hit the byte budget.
    """
    image = _resize_for_budget(image, _MAX_DIMENSION)
    quality = _DEFAULT_JPEG_QUALITY
    data = _encode_jpeg(image, quality)
    while len(data) > max_bytes and quality > _MIN_JPEG_QUALITY:
        quality -= 10
        data = _encode_jpeg(image, quality)
    return data


class ScreenSnapshotTool:
    name: str = "screenshot"
    risk_tier: str = "monitor"
    description: str = (
        "Captures primary monitor as JPEG, returns image artifact for Brain vision analysis."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Why the screenshot is needed (for the log)",
            }
        },
        "required": [],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        reason = (args or {}).get("reason") or ""

        try:
            import mss
            from PIL import Image
        except ImportError as exc:
            return ToolResult(
                success=False,
                output=None,
                error=f"Missing dependency: {exc.name or exc}",
            )

        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                if len(monitors) < 2:
                    return ToolResult(
                        success=False,
                        output=None,
                        error="No primary monitor found",
                    )
                target = select_capture_monitor(monitors, strategy="foreground")
                raw = sct.grab(target)
                image = Image.frombytes("RGB", raw.size, raw.rgb)
        except Exception as exc:  # noqa: BLE001 — mss/display errors are varied
            return ToolResult(
                success=False,
                output=None,
                error=f"Screenshot failed: {exc}",
            )

        jpeg_bytes = _encode_with_budget(image, _MAX_BYTES)
        data_b64 = base64.b64encode(jpeg_bytes).decode("ascii")

        output_msg = "Screenshot captured"
        if reason:
            output_msg = f"Screenshot captured ({reason})"

        return ToolResult(
            success=True,
            output=output_msg,
            artifacts=({"type": "image", "mime": "image/jpeg", "data": data_b64},),
        )
