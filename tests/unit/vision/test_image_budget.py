"""Tests for image-budget capping (Wave 1 — cap the uncapped vision payload).

The permanent router-vision feed attaches the on-disk screenshot verbatim
(`_read_observation_image_b64`), so a 4K PNG ships at 100-400 KB every turn
(``max_image_kb`` was dead config). ``cap_image_b64`` enforces the budget:
no-op when already small, otherwise downscale + JPEG-encode toward the budget.
"""
from __future__ import annotations

import base64
import io
import os

from jarvis.vision.image_budget import cap_image_b64


def _png_b64(width: int, height: int) -> tuple[str, str]:
    """A random-pixel PNG (incompressible → reliably large)."""
    from PIL import Image

    img = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "image/png", base64.b64encode(buf.getvalue()).decode("ascii")


def test_small_image_under_budget_is_unchanged() -> None:
    mime, b64 = _png_b64(64, 64)
    assert cap_image_b64(mime, b64, max_bytes=500_000) == (mime, b64)


def test_oversize_image_is_downscaled_to_jpeg() -> None:
    mime, b64 = _png_b64(2200, 1500)  # > 2048 longest, incompressible
    in_bytes = len(b64) * 3 // 4
    out_mime, out_b64 = cap_image_b64(mime, b64, max_bytes=200_000)
    out_bytes = len(out_b64) * 3 // 4

    assert out_mime == "image/jpeg"
    assert out_bytes < in_bytes
    from PIL import Image

    img = Image.open(io.BytesIO(base64.b64decode(out_b64)))
    assert max(img.size) <= 2048  # longest side capped


def test_undecodable_input_falls_back_to_original() -> None:
    # Telemetry/robustness: a bad image must never break the vision path.
    bad = ("image/png", "not-valid-base64-image!!")
    assert cap_image_b64(*bad, max_bytes=10) == bad


def test_custom_max_dimension_downscales_further() -> None:
    # L7 (CU speed): a smaller longest-side cap shrinks the payload more. Vision
    # models resample to ~1568 px internally, so 2048 ships pixels they discard.
    mime, b64 = _png_b64(2200, 1500)
    out_mime, out_b64 = cap_image_b64(
        mime, b64, max_bytes=200_000, max_dimension=1280,
    )
    from PIL import Image

    img = Image.open(io.BytesIO(base64.b64decode(out_b64)))
    assert out_mime == "image/jpeg"
    assert max(img.size) <= 1280  # honours the custom longest-side cap


def test_default_max_dimension_is_2048() -> None:
    # No-regression: the default longest-side cap is unchanged (2048 px).
    mime, b64 = _png_b64(2600, 1700)
    _, out_b64 = cap_image_b64(mime, b64, max_bytes=200_000)
    from PIL import Image

    img = Image.open(io.BytesIO(base64.b64decode(out_b64)))
    assert 2048 - 2 <= max(img.size) <= 2048  # still capped at the legacy 2048
