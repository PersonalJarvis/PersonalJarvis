"""``jarvis.platform.layered_window`` — per-pixel alpha for Tk windows."""

from __future__ import annotations

import sys

import pytest
from PIL import Image

from jarvis.platform import layered_window


def test_premultiplied_bgra_orders_and_scales_the_channels() -> None:
    img = Image.new("RGBA", (1, 1), (200, 100, 50, 128))
    b, g, r, a = layered_window.premultiplied_bgra(img)
    assert a == 128
    assert (b, g, r) == (round(50 * 128 / 255), round(100 * 128 / 255), round(200 * 128 / 255))


def test_a_transparent_pixel_is_all_zero() -> None:
    assert layered_window.premultiplied_bgra(
        Image.new("RGBA", (2, 1), (255, 255, 255, 0))
    ) == bytes(8)


@pytest.mark.skipif(sys.platform == "win32", reason="the no-op path is for other platforms")
def test_every_call_is_a_quiet_no_op_off_windows() -> None:
    assert layered_window.per_pixel_alpha_supported() is False
    assert layered_window.tk_toplevel_hwnd(object()) == 0
    assert layered_window.enable_per_pixel_alpha(123) is False
    assert layered_window.update_layered(123, Image.new("RGBA", (1, 1))) is False


def test_a_missing_window_is_refused_without_raising() -> None:
    assert layered_window.enable_per_pixel_alpha(0) is False
    assert layered_window.update_layered(0, Image.new("RGBA", (1, 1))) is False
