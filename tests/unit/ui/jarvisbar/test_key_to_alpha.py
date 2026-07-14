"""renderer.key_to_alpha — the macOS per-pixel transparency translation.

macOS has no layered-window color key, so the bar's frames must carry a real
alpha channel there: exactly the magenta key pixels become fully transparent,
everything else stays fully opaque (mirroring the Windows color-key contract).
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from jarvis.ui.jarvisbar.renderer import COLOR_KEY_RGB, key_to_alpha


def test_key_pixels_become_transparent_and_content_stays_opaque() -> None:
    arr = np.zeros((4, 4, 3), dtype=np.uint8)
    arr[:, :] = COLOR_KEY_RGB
    arr[1, 2] = (10, 20, 30)  # one content pixel
    arr[3, 0] = (255, 0, 254)  # near-key must NOT be keyed (exact match only)

    out = key_to_alpha(Image.fromarray(arr, "RGB"))
    assert out.mode == "RGBA"
    result = np.asarray(out)

    assert result[0, 0, 3] == 0  # key pixel → fully transparent
    assert result[1, 2, 3] == 255  # content pixel → fully opaque
    assert tuple(result[1, 2, :3]) == (10, 20, 30)  # colors untouched
    assert result[3, 0, 3] == 255  # near-key stays visible


def test_real_rendered_frame_keys_out_only_the_background() -> None:
    from jarvis.ui.jarvisbar.renderer import JarvisBarRenderer

    frame = JarvisBarRenderer(accent="#e7c46e").render(0.5, "listen", 0.8)
    out = np.asarray(key_to_alpha(frame))
    # A real frame has both: transparent background and an opaque pill.
    assert (out[..., 3] == 0).any()
    assert (out[..., 3] == 255).any()
