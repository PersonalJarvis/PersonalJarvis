"""Dev probe: run the real JarvisBarOverlay (Tk window, color-key + alpha)
standalone in THINK mode and capture it from the live desktop.

The position loader is patched to None so the probe bar anchors at the
default bottom-center spot instead of stacking on the app's bar (both read
the same persisted drag position). PIL ImageGrab is used because the bar is
a layered window invisible to plain BitBlt captures.
"""
import ctypes
import sys
import time
from pathlib import Path

if sys.platform != "win32":
    sys.exit(
        "jarvisbar_live_probe.py drives Windows-only APIs (ctypes.windll / "
        "layered-window capture); run it on Windows."
    )

# Per-monitor DPI awareness BEFORE Tk boots, so the window coordinates Tk
# reports match the physical pixels ImageGrab captures (display scaling).
ctypes.windll.shcore.SetProcessDpiAwareness(2)

from PIL import Image, ImageGrab  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.ui.jarvisbar import interaction, renderer  # noqa: E402
from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "screenshots"

# Detach from the persisted drag position — the running app's bar sits there.
interaction.load_jarvisbar_position = lambda *_a, **_k: None  # type: ignore


def main() -> None:
    bar = JarvisBarOverlay(persistent=True)
    bar.start_in_thread()
    time.sleep(2.5)  # Tk boot
    bar.show(mode="think")
    time.sleep(1.0)

    st = bar._renderer._st
    print(
        f"mode={bar._mode} pill={st.pw:.0f}x{st.ph:.0f} "
        f"target_active={renderer.ACTIVE_W}x{renderer.ACTIVE_H} "
        f"pos=({bar._x},{bar._y})",
        flush=True,
    )

    pad = 50
    bx, by = bar._x, bar._y
    for i in (1, 2, 3):
        time.sleep(1.3)
        img = ImageGrab.grab(all_screens=True)
        crop = img.crop(
            (
                max(0, bx - pad),
                max(0, by - pad),
                min(img.width, bx + renderer.WIN_W + pad),
                min(img.height, by + renderer.WIN_H + pad),
            )
        )
        crop = crop.resize((crop.width * 5, crop.height * 5), Image.LANCZOS)
        crop.save(OUT / f"bar-probe-crop-{i}.png")
        print("saved", i, flush=True)

    st = bar._renderer._st
    print(f"final mode={bar._mode} pill={st.pw:.0f}x{st.ph:.0f}", flush=True)
    bar.hide()
    time.sleep(0.5)
    print("done", flush=True)


if __name__ == "__main__":
    main()
