"""Image-budget capping for the router-vision payload (Wave 1 — omni-latency).

The permanent vision feed ships the on-disk screenshot verbatim
(``_read_observation_image_b64``), so an uncapped 4K PNG costs 100-400 KB
upload + image tokens every turn (``max_image_kb`` was dead config). This helper
enforces the budget: a no-op when the image is already small, otherwise it
downscales to a vision-friendly longest side and JPEG-encodes toward the budget.
Any decode/encode failure falls back to the original image so the vision path is
never broken. PIL is imported lazily — a headless VPS without vision never hits
this code (``_collect_vision_images`` returns early when no provider is wired).
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Vision LLMs internally resample to ~1568px; 2048px longest side keeps full
# detail while bounding 4K multi-monitor captures. Mirrors screen_snapshot.py.
_MAX_DIMENSION = 2048
_DEFAULT_JPEG_QUALITY = 85
_MIN_JPEG_QUALITY = 35


def cap_image_b64(
    mime: str,
    data_b64: str,
    max_bytes: int,
    max_dimension: int = _MAX_DIMENSION,
) -> tuple[str, str]:
    """Return ``(mime, base64)`` capped to roughly ``max_bytes``.

    No-op when the encoded image is already within budget. Otherwise the image
    is downscaled to ``max_dimension`` longest side (default ``_MAX_DIMENSION``,
    2048 px) and JPEG-encoded, reducing quality toward ``_MIN_JPEG_QUALITY`` to
    approach the budget (best-effort). On any failure the original
    ``(mime, data_b64)`` is returned unchanged.

    ``max_dimension`` (L7 CU-speed lever) is tunable: vision models resample to
    ~1568 px internally, so a smaller longest side ships fewer pixels for faster
    encode + upload + ingest. ``<= 0`` disables the dimension cap (byte budget
    only). The default keeps the legacy 2048 px, so existing callers are
    byte-for-byte unchanged.
    """
    if max_bytes <= 0:
        return mime, data_b64
    approx_bytes = (len(data_b64) * 3) // 4
    if approx_bytes <= max_bytes:
        return mime, data_b64
    try:
        from PIL import Image  # noqa: PLC0415 — lazy; VPS without vision never hits this

        raw = base64.b64decode(data_b64, validate=True)
        img = Image.open(io.BytesIO(raw))
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        longest = max(w, h)
        if max_dimension > 0 and longest > max_dimension:
            scale = max_dimension / longest
            img = img.resize(
                (max(1, round(w * scale)), max(1, round(h * scale))),
                resample=Image.Resampling.LANCZOS,
            )
        quality = _DEFAULT_JPEG_QUALITY
        out = _encode_jpeg(img, quality)
        while len(out) > max_bytes and quality > _MIN_JPEG_QUALITY:
            quality -= 10
            out = _encode_jpeg(img, quality)
        return "image/jpeg", base64.b64encode(out).decode("ascii")
    except Exception:  # noqa: BLE001 — never break the vision path over a cap
        logger.debug("image cap failed; using original", exc_info=True)
        return mime, data_b64


def _encode_jpeg(img: Any, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()
