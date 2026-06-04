"""screenshot-Tool: erfasst den aktiven Monitor als JPEG fuer Brain-Vision.

Risk-Tier: monitor — Screenshot liest Bildschirminhalt, also werden Aufrufe
standardmaessig geloggt. Whitelist per `[safety.whitelist].tools` erlaubt
Dauer-Einsatz ohne Confirm-Prompt.

Phase A1 (2026-04-25): Default-Format auf JPEG q85 umgestellt. Vision-LLMs
(Claude/Gemini/GPT) rechnen Token-Kosten in Pixel-Area, nicht Bytes — JPEG
schrumpft die Payload um ~8x bei identischen Tokens. Quality 85 ist der
Sweet-Spot: visuell verlustfrei fuer Text + UI-Elements, deutlich kleiner
als PNG. Iteratives Shrinking entfaellt — quality-Param erledigt das.

Multi-Monitor-Fix: per Default folgt der Capture dem Foreground-Window
(``select_capture_monitor`` aus ``jarvis.vision.screenshot``). Damit sieht
das Brain den Bildschirm, auf dem der User gerade aktiv ist — nicht
hardcoded den mss-Primary, der auf Multi-Monitor-Setups oft leer ist.

Ablauf in `execute`:
1. ``mss.mss().monitors`` enumerieren -> ``select_capture_monitor`` waehlt
   den richtigen Monitor (Foreground-Window-Lookup).
2. `PIL.Image.frombytes("RGB", size, raw.rgb)` baut ein Pillow-Image.
3. JPEG q85 — typisch 100-300 KB fuer 4K-Screenshots. Falls > _MAX_BYTES
   (Edge-Case bei sehr detail-dichten Screens), Quality runter bis es passt.
4. Result: `artifacts=[{"type": "image", "mime": "image/jpeg", "data":
   <base64>}]` damit das Vision-capable Brain den Screenshot direkt
   konsumieren kann.
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
# Vision-LLMs (Claude/Gemini/GPT) skalieren intern auf ~1568 px Laengsseite
# zur Token-Berechnung. 4K-Captures (3840x2160) sind reine Verschwendung —
# sie kosten ~3x Bytes und Tokens ohne sichtbaren Vision-Vorteil. 2048 px
# Laengsseite ist der Sweet-Spot: deutlich groesser als das interne LLM-
# Resampling-Ziel (kein Detail-Verlust), aber hart genug um das _MAX_BYTES-
# Budget bei 4K-Multi-Monitor zuverlaessig zu treffen.
_MAX_DIMENSION = 2048


def _resize_for_budget(image: Any, max_dim: int) -> Any:
    """Skaliert ein Pillow-Image proportional auf maximal ``max_dim`` Laengsseite.

    Identitaet, wenn das Bild bereits kleiner ist. Damit kostet der Aufruf
    auf 1440p-Captures nichts.
    """
    w, h = image.size
    longest = max(w, h)
    if longest <= max_dim:
        return image
    scale = max_dim / longest
    new_size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    # Late-Import damit das Modul auch ohne PIL importierbar bleibt;
    # _encode_with_budget wird ohnehin nur aus Pfaden gerufen, die PIL haben.
    from PIL import Image  # noqa: PLC0415

    return image.resize(new_size, resample=Image.Resampling.LANCZOS)


def _encode_jpeg(image: Any, quality: int) -> bytes:
    """Serialisiert ein Pillow-Image als JPEG mit gegebener Qualitaet."""
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _encode_with_budget(image: Any, max_bytes: int) -> bytes:
    """JPEG q85 zuerst; bei Overshoot Quality reduzieren bis _MIN_JPEG_QUALITY.

    Vor dem Encode wird auf ``_MAX_DIMENSION`` Laengsseite runtergescaled,
    damit 4K-Captures (Multi-Monitor) das Byte-Budget zuverlaessig treffen.
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
                "description": "Warum der Screenshot gebraucht wird (fuer Log)",
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
                error=f"Dependency fehlt: {exc.name or exc}",
            )

        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                if len(monitors) < 2:
                    return ToolResult(
                        success=False,
                        output=None,
                        error="Kein Primaer-Monitor gefunden",
                    )
                target = select_capture_monitor(monitors, strategy="foreground")
                raw = sct.grab(target)
                image = Image.frombytes("RGB", raw.size, raw.rgb)
        except Exception as exc:  # noqa: BLE001 — mss/Display-Fehler sind divers
            return ToolResult(
                success=False,
                output=None,
                error=f"Screenshot fehlgeschlagen: {exc}",
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
