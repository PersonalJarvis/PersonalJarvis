"""End-to-end live verification for the orb drag feature.

Spawns a sticky-mode OrbOverlay in its own thread, then dispatches
synthetic <ButtonPress-1>, <B1-Motion>, <ButtonRelease-1>, and
<Double-Button-1> events directly into Tk via the orb's UI queue.
This bypasses the OS event layer (pyautogui is unreliable against
layered/topmost windows) and deterministically tests the
drag-handler code path in a REAL Tk loop.

Uses a temp TOML so the real jarvis.toml is untouched.

Run: python scripts/verify_orb_drag.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

# Make project root importable when run as `python scripts/verify_orb_drag.py`.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Use a temp TOML so we don't mutate real jarvis.toml.
TEMP_DIR = Path(tempfile.mkdtemp(prefix="orb-drag-verify-"))
TEMP_TOML = TEMP_DIR / "jarvis.toml"
TEMP_TOML.write_text("[overlay]\nenabled = true\n", encoding="utf-8")

# Monkey-patch the JARVIS_TOML_PATH constant before OrbOverlay reads it.
import ui.orb.overlay as overlay_mod

overlay_mod.JARVIS_TOML_PATH = TEMP_TOML
print(f"[verify] using temp TOML at: {TEMP_TOML}")

from ui.orb.overlay import OrbOverlay

orb = OrbOverlay(sticky=True)  # always-visible
t = threading.Thread(target=orb.start, daemon=True)
t.start()

# Wait for window to materialise.
time.sleep(2.5)
print(f"[verify] orb position: ({orb._mascot_x}, {orb._mascot_y})")
print(f"[verify] orb manual_pinned: {orb._manual_pinned}")


def _on_tk(fn):
    """Schedule fn on the Tk main thread + wait for it to finish."""
    done = threading.Event()
    result: list = [None]

    def wrapper():
        try:
            result[0] = fn()
        finally:
            done.set()

    orb._enqueue_ui(wrapper)
    done.wait(timeout=5.0)
    return result[0]


# --- Step 1: synthetic drag from current position to (500, 500) -------------
src_x, src_y = orb._mascot_x + 54, orb._mascot_y + 54  # orb center
dst_x, dst_y = 500, 500
print(f"[verify] synthetic drag ({src_x},{src_y}) -> ({dst_x},{dst_y})")


def _press():
    orb._canvas.event_generate(
        "<ButtonPress-1>", rootx=src_x, rooty=src_y, x=54, y=54, when="now"
    )


def _motion_to(rx, ry):
    orb._canvas.event_generate(
        "<B1-Motion>", rootx=rx, rooty=ry, x=54, y=54, when="now"
    )


def _release(rx, ry):
    orb._canvas.event_generate(
        "<ButtonRelease-1>", rootx=rx, rooty=ry, x=54, y=54, when="now"
    )


_on_tk(_press)
# Walk in steps so the motion handler crosses the 5 px threshold properly.
for i in range(1, 21):
    rx = src_x + (dst_x - src_x) * i // 20
    ry = src_y + (dst_y - src_y) * i // 20
    _on_tk(lambda rx=rx, ry=ry: _motion_to(rx, ry))
    time.sleep(0.01)
_on_tk(lambda: _release(dst_x, dst_y))
time.sleep(0.5)

print(f"[verify] orb after drag: ({orb._mascot_x}, {orb._mascot_y})")
print(f"[verify] orb manual_pinned after drag: {orb._manual_pinned}")

toml_text = TEMP_TOML.read_text(encoding="utf-8")
print("\n[verify] --- TOML contents after drag ---")
print(toml_text)
print("[verify] --- end TOML ---\n")

if "[overlay.mascot]" not in toml_text:
    print("FAIL: no [overlay.mascot] section in TOML")
    sys.exit(1)
if "position_monitor" not in toml_text:
    print("FAIL: position_monitor not in TOML")
    sys.exit(1)
if not orb._manual_pinned:
    print("FAIL: _manual_pinned should be True after a real drag")
    sys.exit(1)
print("PASS: drag persisted to TOML + manual_pinned True")


# --- Step 2: synthetic double-click for reset -------------------------------
new_cx = orb._mascot_x + 54
new_cy = orb._mascot_y + 54
print(f"[verify] synthetic double-click for reset @ ({new_cx},{new_cy})")


def _double_click():
    orb._canvas.event_generate(
        "<Double-Button-1>",
        rootx=new_cx,
        rooty=new_cy,
        x=54,
        y=54,
        when="now",
    )


_on_tk(_double_click)
time.sleep(0.6)

print(f"[verify] orb after reset: ({orb._mascot_x}, {orb._mascot_y})")
print(f"[verify] orb manual_pinned after reset: {orb._manual_pinned}")

toml_text = TEMP_TOML.read_text(encoding="utf-8")
print("\n[verify] --- TOML after reset ---")
print(toml_text)
print("[verify] --- end ---\n")

if "position_monitor" in toml_text:
    print("FAIL: position_monitor still in TOML after reset")
    sys.exit(1)
if orb._manual_pinned:
    print("FAIL: _manual_pinned should be False after reset")
    sys.exit(1)
print("PASS: reset cleared TOML + manual_pinned False")


# --- Step 3: click-not-drag — sub-threshold motion must NOT persist ---------
print("\n[verify] sub-threshold (3px) motion must NOT register as drag")
sx2 = orb._mascot_x + 54
sy2 = orb._mascot_y + 54


def _press2():
    orb._canvas.event_generate(
        "<ButtonPress-1>", rootx=sx2, rooty=sy2, x=54, y=54, when="now"
    )


def _release2():
    orb._canvas.event_generate(
        "<ButtonRelease-1>", rootx=sx2 + 3, rooty=sy2 + 1, x=57, y=55, when="now"
    )


_on_tk(_press2)
# 3 px motion only — below 5 px threshold.
_on_tk(lambda: orb._canvas.event_generate(
    "<B1-Motion>", rootx=sx2 + 3, rooty=sy2 + 1, x=57, y=55, when="now"
))
_on_tk(_release2)
time.sleep(0.5)

toml_text = TEMP_TOML.read_text(encoding="utf-8")
if "position_monitor" in toml_text:
    print("FAIL: sub-threshold click persisted to TOML")
    sys.exit(1)
print("PASS: sub-threshold click did NOT persist")


print(f"\n✓ ALL CHECKS PASSED. Artifacts in {TEMP_DIR}")
