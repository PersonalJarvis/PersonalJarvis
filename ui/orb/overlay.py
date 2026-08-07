"""Jarvis-Orb as a native desktop overlay (Tkinter + Pillow + numpy).

History of approaches tried and why we landed on Tkinter:
    - pywebview + WebView2 Canvas: WebView2 renders via DirectComposition,
      so SetWindowRgn/LWA_COLORKEY have no effect → a rectangular box remained.
    - PySide6 + WA_TranslucentBackground: Qt6+DWM on Windows 11 frequently
      delivers only an opaque black backing buffer + a DropShadow frame
      instead of real transparency. Dozens of known Qt bugs, no robust fix.
    - Tkinter + wm_attributes('-transparentcolor'): uses the classic
      Win32 SetLayeredWindowAttributes API with LWA_COLORKEY. Stable since
      Windows 2000, independent of DWM compositor quirks. Magenta (#FF00FF)
      becomes pixel-perfect transparent — Windows routes those pixels
      straight through to the desktop.

Rendering pipeline:
    numpy computes a 108x108 RGB buffer per frame. Radial gradients,
    additive swirls, and the bright core are executed as vector operations
    on the distance arrays (precomputed once in the constructor).
    Hard circle edge, no alpha fade outward — otherwise pink anti-aliasing
    pixels would appear at the color-key boundary. Pillow wraps the array
    into a PhotoImage that Tkinter renders onto a Canvas.

Public API:
    overlay = OrbOverlay(style="mascot")    # SWG/Gigi PNG
    overlay = OrbOverlay(style="voice_orb") # procedural weather sphere
    overlay.start()                         # blocks until mainloop exit
    overlay.show(mode="listen")             # voice modes: idle/listen/speak/think
    overlay.show(mode="speak")
    overlay.show(mode="dictate")            # dictation modes: recording ...
    overlay.show(mode="dictate_transcribing")  # ... and transcribing
    overlay.hide()
    overlay.set_level(0.42)
    overlay.set_style("mascot")             # runtime switch, no restart

ENV overrides:
    JARVIS_ORB_STYLE=mascot|voice_orb       # legacy "orb" requests are ignored
    JARVIS_ORB_MASCOT_PATH=<path.png>       # alternative mascot path

Standalone test:
    python -m ui.orb.overlay                    # demo sequence (mascot)
    python -m ui.orb.overlay --sticky           # permanently visible (preview)
    python -m ui.orb.overlay --sticky --mascot  # SWG mascot, static
    python -m ui.orb.overlay --sticky --voice-orb  # procedural voice orb
    python -m ui.orb.overlay --mic --mascot     # SWG + mic-reactive
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageTk

from jarvis.core.config import DEFAULT_CONFIG_FILE as JARVIS_TOML_PATH
from jarvis.core.win32_dpi import ensure_dpi_awareness as _ensure_dpi_awareness
from jarvis.ui.jarvisbar.modes import MODES
from jarvis.ui.overlay_styles import LEGACY_STYLE_ALIASES, ORB_STYLES
from ui.orb import controls as orb_controls
from ui.orb.animations import (
    ANIMATION_REGISTRY,
    Animation,
    ArmTransform,
    Transform,
    identity_arm,
    identity_transform,
    make_animation,
)
from ui.orb.drag_persistence import (
    MascotPosition,
    clamp_to_work_area,
    clear_position_in_toml,
    load_allow_secondary_monitor_pin,
    load_position_from_toml,
    resolve_placement,
    save_position_to_toml,
    screens_from_tk,
)
from ui.orb.taskbar import (
    MascotAnchor,
    compute_mascot_position,
    get_taskbar_info,
    get_tray_notify_rect,
)
from ui.orb.voice_orb import VoiceOrbRenderer

#: The looks this window can wear (ONE definition, in jarvis.ui.overlay_styles):
#: ``mascot`` is the Gigi ghost, ``voice_orb`` the procedural weather sphere —
#: the desktop twin of the in-app orb. Both share this window, so both are
#: always on top and draggable to any monitor. The removed legacy procedural
#: "orb" style is coerced to the mascot.
DEFAULT_ORB_STYLE = "mascot"


@dataclass
class _DragState:
    """In-flight drag tracking. ``moved`` flips True once the threshold is crossed."""

    start_root_x: int
    start_root_y: int
    offset_x: int  # event.x_root - mascot_x at press time
    offset_y: int
    moved: bool = False


# 108x108 — roughly 1/3 smaller than the old 160-px size
WIN_W = 108
WIN_H = 108

#: The voice orb's window. Twice the mascot's edge, which is NOT the same as
#: twice the sphere: the sphere rests at 70 % of the window (162 px — the 50 %
#: larger sphere the maintainer asked for on 2026-08-06), and the margin that
#: leaves is where the aura lives. The corona and its outgoing waves have to be
#: drawn INSIDE this window like everything else, so the window has to be
#: bigger than the thing it draws. The mascot keeps 108: its artwork, taskbar
#: anchor and animations are all drawn for that size.
VOICE_ORB_WIN_W = 216
VOICE_ORB_WIN_H = 216


def window_size_for_style(style: str) -> tuple[int, int]:
    """Window edge length for one orb style."""
    if style == "voice_orb":
        return VOICE_ORB_WIN_W, VOICE_ORB_WIN_H
    return WIN_W, WIN_H


MARGIN_RIGHT = 24
MARGIN_TOP = 28

# Anchor margins fed to ui.orb.taskbar.compute_mascot_position so the mascot
# stands on the real Windows taskbar top edge (Shell_TrayWnd, read live).
TRAY_SAFE_MARGIN_PX = 14
RIGHT_EDGE_MARGIN_PX = 24
TASKBAR_OVERLAP_PX = 1
AUTOHIDE_BOTTOM_MARGIN_PX = 16
POSITION_RECHECK_MS = 1500

# Drag-and-pin threshold (manhattan distance, px). Below this we treat
# the gesture as a click (no movement, no persistence). Raised from 5 to
# 16 on 2026-05-18 (BUG-027): an over-eager 5 px threshold meant a casual
# mouse twitch during a double-click could commit a pin, which silently
# moved the orb onto a secondary monitor where the user could not see it.
DRAG_THRESHOLD_PX = 16

# Mute gesture window (ms). After one ``<Double-Button-1>`` event we wait
# this long for a second double-click; only then does the mute toggle
# actually fire. The 2026-05-17 single-double-click implementation
# muted Jarvis whenever the user clicked the freshly popped-up orb,
# silently locking the wake-loop. Two double-clicks (four clicks total)
# in under 600 ms is intentional, easy to perform when wanted, and
# practically impossible to trigger by accident on an aware-popup hit.
MUTE_GESTURE_WINDOW_MS = 600

# Comment bubble — mirrors the gigi-bubble look from the Desktop App.
BUBBLE_BG_HEX = "#0F0F0F"
BUBBLE_BORDER_HEX = "#FFE500"
BUBBLE_TEXT_HEX = "#FFF200"
BUBBLE_PADDING_X = 14
BUBBLE_PADDING_Y = 8
BUBBLE_CORNER_RADIUS = 14
BUBBLE_GAP_FROM_ORB = 6
BUBBLE_MAX_WIDTH = 240
BUBBLE_MIN_WIDTH = 60
BUBBLE_FONT_FAMILY = "Segoe UI"
BUBBLE_FONT_SIZE = 11
BUBBLE_BORDER_WIDTH = 2
BUBBLE_TAIL_W = 18
BUBBLE_TAIL_H = 10
TRANSCRIPT_BUBBLE_WIDTH = 370
TRANSCRIPT_BUBBLE_TEXT_WIDTH = 332
TRANSCRIPT_BUBBLE_FONT_SIZE = 12
TRANSCRIPT_MAX_VISIBLE_LINES = 4
# Min on-screen time so fast LISTENING→THINKING bounces are still readable.
BUBBLE_MIN_SHOW_S = 1.1


@dataclass(frozen=True)
class BubbleTheme:
    """How the speech bubble is painted for one orb style.

    The bubble used to be the mascot's bubble, full stop: black body, thick
    yellow border, bold yellow text — the comic look that belongs to the Gigi
    ghost. Beside the procedural sphere that reads as a different program's
    window sitting on the same desktop, which is exactly what the maintainer
    reported (2026-08-06). So the look travels with the style instead of being
    baked into the widget: the ghost keeps its bubble, the orb gets the app's
    own quiet surface (``bg-background/85`` + ``border-border/50`` +
    ``text-muted-foreground``, flattened — a layered Tk window has no partial
    alpha), and neither one has to compromise for the other.
    """

    bg: str
    border: str
    text: str
    border_width: int
    #: Bold is the mascot's voice. The orb's transcript is read, not shouted.
    bold: bool
    #: A tail points at a character that is speaking. The orb is not a mouth,
    #: and the in-app bubble it copies has no tail either.
    tail: bool
    corner_radius: int


#: The Gigi bubble, unchanged — the ghost's own voice.
MASCOT_BUBBLE_THEME = BubbleTheme(
    bg=BUBBLE_BG_HEX,
    border=BUBBLE_BORDER_HEX,
    text=BUBBLE_TEXT_HEX,
    border_width=BUBBLE_BORDER_WIDTH,
    bold=True,
    tail=True,
    corner_radius=BUBBLE_CORNER_RADIUS,
)

#: The app's surface, so the desktop orb and the in-app orb read as one thing.
#: The border is a shade above the app's ``--border``: in the app a shadow
#: separates the bubble from what is behind it, and a colour-keyed window
#: cannot cast one without fringing.
ORB_BUBBLE_THEME = BubbleTheme(
    bg="#121212",
    border="#333333",
    text="#B4B4B4",
    border_width=1,
    bold=False,
    tail=False,
    corner_radius=16,
)


def bubble_theme_for_style(style: str) -> BubbleTheme:
    """The bubble look one orb style wears."""
    return ORB_BUBBLE_THEME if style == "voice_orb" else MASCOT_BUBBLE_THEME


# Magenta color key — Tkinter renders this color pixel-perfect transparent
COLOR_KEY_HEX = "#FF00FF"
COLOR_KEY_RGB = np.array([255, 0, 255], dtype=np.uint8)


def key_to_alpha(img: Image.Image) -> Image.Image:
    """RGB frame → RGBA with the magenta color key mapped to full transparency.

    Windows keys the magenta out natively (layered-window color key); macOS
    has no color-key concept, so the Tk surface there shows RGBA frames on a
    ``-transparent`` root instead. Exact-match keying mirrors the Windows
    contract: only pure ``COLOR_KEY_RGB`` pixels vanish.
    """
    arr = np.asarray(img, dtype=np.uint8)
    alpha = np.where((arr == COLOR_KEY_RGB).all(axis=-1), 0, 255).astype(np.uint8)
    return Image.fromarray(np.dstack((arr, alpha)), "RGBA")


# Warn-once latch: the clear-backing pass runs on every reveal, but a missing
# pyobjc should produce ONE actionable warning, not a log storm.
_WARNED_NO_APPKIT = False


def apply_macos_clear_backing() -> None:
    """Make every NSWindow of THIS process paint a clear backing (BUG-075).

    Tk 8.6 aqua turned the ``systemTransparent`` background into true
    per-pixel transparency; Tk 9 (bundled by uv's python-build-standalone)
    paints it as an opaque appearance color instead — the surface showed a
    solid grey box around its artwork. A non-opaque window backing with a
    clear background is the native, Tk-version-independent equivalent.
    Runs only inside the overlay host process, whose every window wants a
    clear backing. Safe post-BUG-067: Tk owns ``NSApp`` before any window
    exists here. Best-effort, never raises.
    """
    if sys.platform != "darwin":
        return
    import logging  # noqa: PLC0415

    orb_log = logging.getLogger("jarvis.orb")
    try:
        from AppKit import NSApp, NSColor  # type: ignore[import-not-found] # noqa: PLC0415
    except Exception:  # noqa: BLE001
        # Without this pass Tk 9 paints the window backing as an OPAQUE grey
        # box (BUG-075) — the single most visible macOS defect. Say so loudly
        # once instead of hiding the cause in a debug line.
        global _WARNED_NO_APPKIT
        if not _WARNED_NO_APPKIT:
            _WARNED_NO_APPKIT = True
            orb_log.warning(
                "pyobjc (AppKit) unavailable — Tk 9 paints the mascot backing "
                "as an opaque grey box. Install the [desktop-macos] extra "
                "(pip install 'personal-jarvis[desktop-macos]') to fix it."
            )
        return
    try:
        wins = list(NSApp.windows())
        for win in wins:
            win.setOpaque_(False)
            win.setBackgroundColor_(NSColor.clearColor())
            win.setHasShadow_(False)
        orb_log.debug("macOS clear-backing applied to %d window(s)", len(wins))
    except Exception:  # noqa: BLE001 — cosmetic; the grey box is the degrade
        orb_log.warning("macOS clear-backing pass failed", exc_info=True)


_GWL_EXSTYLE = -20
_WS_EX_APPWINDOW = 0x00040000
_WS_EX_TOOLWINDOW = 0x00000080

TAU = math.tau

# Default path for the SWG/Gigi mascot. Looked up by MascotRenderer when no
# explicit path is passed in. Resolved relative to the project root.
DEFAULT_MASCOT_REL = "assets/icons/jarvis-gigi-256.png"


def _transcript_visible_line_count(text_height: int, line_height: int) -> int:
    """Return how many transcript lines should shape the bubble height."""
    safe_line_height = max(1, line_height)
    lines = max(1, math.ceil(max(1, text_height) / safe_line_height))
    return min(TRANSCRIPT_MAX_VISIBLE_LINES, lines)


def _transcript_body_height(line_count: int, line_height: int) -> int:
    visible_lines = max(1, min(TRANSCRIPT_MAX_VISIBLE_LINES, line_count))
    return (BUBBLE_PADDING_Y * 2) + (max(1, line_height) * visible_lines)


def mode_energy(mode: str, t: float, ext_level: float | None) -> float:
    """Return the raw drive level (0..1) the mascot renders ``mode`` with.

    The mascot has exactly one expressive channel: energy. It feeds the halo
    threshold, the breathing scale and the brightness, so "what a mode looks
    like" on this surface *is* the curve this function returns. Pulled out of
    :meth:`MascotRenderer.render` so the per-mode contract is directly testable
    without a Tk root, a PNG asset or a frame buffer.

    Voice modes keep their historical behaviour to the letter: a live level
    (mic or TTS loudness) always wins, and each mode has its own synthetic
    fallback curve.

    The two dictation modes are deliberately NOT variations of ``listen``:

    * ``dictate`` — recording. The dictation session feeds the very same mic
      level channel, so the live signal drives the halo exactly like the bar's
      equalizer. It is clamped up from a floor, because a quiet moment while
      recording must still read as "I am listening", never as a dead mascot.
    * ``dictate_transcribing`` — the key is released and the mic feed has
      stopped, so the last sample would otherwise freeze the mascot at whatever
      loudness the user happened to end on. This mode therefore ignores the
      stale level outright and runs a steady work pulse instead — the mascot's
      equivalent of the bar's orbital core.

    ``notice`` — something the user asked for did not happen. Like
    ``dictate_transcribing`` it IGNORES the live level: the mascot must not
    shimmer along with a microphone it is not using while it says the opposite.
    It runs a low, slow breath — clearly awake (a dead-flat mascot reads as a
    crashed one) and clearly not listening. On this surface the reason itself
    is legible in the speech bubble, so the energy only has to avoid
    contradicting it.
    """
    if mode == "notice":
        return max(0.0, min(1.0, 0.16 + 0.08 * math.sin(t * 1.1)))
    if mode == "dictate":
        live = 0.0 if ext_level is None else max(0.0, min(1.0, ext_level))
        floor = 0.22 + 0.14 * math.sin(t * 1.6)
        return max(0.0, min(1.0, max(live, floor)))
    if mode == "dictate_transcribing":
        return max(0.0, min(1.0, 0.30 + 0.16 * math.sin(t * 2.2)))
    if ext_level is not None:
        return max(0.0, min(1.0, ext_level))
    if mode == "speak":
        return 0.35 + 0.25 * math.sin(t * 1.8) + 0.1 * math.sin(t * 3.3)
    if mode == "think":
        return 0.2 + 0.12 * math.sin(t * 2.5)
    if mode == "listen":
        return 0.25 + 0.18 * math.sin(t * 1.4) + 0.08 * math.sin(t * 2.7)
    return 0.0


def _resolve_mascot_path(path_str: str | None) -> Path | None:
    """Looks for the mascot PNG in the common locations.

    Order: explicit path → ENV ``JARVIS_ORB_MASCOT_PATH`` → project root
    (walking up from the module). Returns None when no asset is available; callers
    keep the overlay invisible instead of falling back to the removed legacy orb.
    """
    candidates: list[Path] = []
    if path_str:
        candidates.append(Path(path_str))
    env_path = os.environ.get("JARVIS_ORB_MASCOT_PATH")
    if env_path:
        candidates.append(Path(env_path))
    # Find the project root: this file lives at <root>/ui/orb/overlay.py
    here = Path(__file__).resolve()
    for parent in [here.parent, here.parent.parent, here.parent.parent.parent]:
        candidates.append(parent / DEFAULT_MASCOT_REL)
    for c in candidates:
        if c.is_file():
            return c
    return None


def _apply_jarvis_icon_to_tk_root(root: tk.Tk) -> None:
    """Set the Jarvis taskbar/titlebar icon on this Tk root.

    Thin wrapper over the canonical, cross-platform
    :func:`jarvis.ui.icon_utils.apply_tk_window_icon` so the orb and the
    JarvisBar share ONE implementation — the two used to drift apart, which is
    how the JarvisBar regressed to the Python logo (BUG #UI-Pin-2026-05-05).
    Best-effort; any failure is silent (the orb is cosmetic).
    """
    try:
        from jarvis.ui.icon_utils import apply_tk_window_icon

        apply_tk_window_icon(root)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("jarvis.orb").debug(
            "Tk icon setup failed; continuing without it.",
            exc_info=True,
        )


def _coerce_style(style: str | None) -> str:
    """Return a style this window can actually render.

    Unknown values (the removed legacy ``"orb"``, a typo, a value written by a
    newer build into an older ``jarvis.toml``) fall back to the mascot with one
    warning rather than leaving the desktop without any overlay at all.
    """
    candidate = (style or "").strip().lower()
    candidate = LEGACY_STYLE_ALIASES.get(candidate, candidate)
    if candidate in ORB_STYLES:
        return candidate
    import logging

    logging.getLogger("jarvis.orb").warning(
        "Unknown orb style %r — falling back to %r (known: %s).",
        style,
        DEFAULT_ORB_STYLE,
        ", ".join(ORB_STYLES),
    )
    return DEFAULT_ORB_STYLE


def _apply_color_key(root: tk.Tk) -> bool:
    """Make the key colour transparent on this Tk root. False = not supported.

    Windows keys the magenta out natively on the layered window. On Linux the
    same attribute is accepted only under a compositing window manager; under a
    bare X11 session (and on Wayland) Tk raises, and the honest outcome is "no
    overlay" rather than an opaque magenta square parked on the desktop. The
    caller degrades; this never raises.
    """
    try:
        root.wm_attributes("-transparentcolor", COLOR_KEY_HEX)
        root.configure(bg=COLOR_KEY_HEX)
        return True
    except tk.TclError:
        import logging

        logging.getLogger("jarvis.orb").warning(
            "This desktop session does not support Tk's transparent colour key "
            "(typical for Wayland or X11 without a compositor) — the on-screen "
            "orb stays hidden. Voice, the tray icon and the app window are "
            "unaffected; pick 'None (hidden)' as the display style to silence "
            "this."
        )
        return False


def _hide_tk_window_from_task_switcher(root: tk.Tk) -> None:
    """Marks the orb window as a toolwindow so it doesn't count as an app."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(root.winfo_id())
        user32 = ctypes.windll.user32
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = int(get_long(hwnd, _GWL_EXSTYLE))
        style = (style | _WS_EX_TOOLWINDOW) & ~_WS_EX_APPWINDOW
        set_long(hwnd, _GWL_EXSTYLE, style)
    except Exception:
        # Pure desktop cosmetics. If Win32 doesn't take effect, the orb
        # stays functional and may just show up as an extra window.
        return


class MascotRenderer:
    """Renderer for the SWG/Gigi mascot.

    Implements the render interface ``render(t, mode, ext_level) -> Image.Image``
    used by ``OrbOverlay``.

    Notable details:
        - Alpha threshold (binary): antialiasing edges would create pink
          fringes on magenta during compositing. For the pixel-art Gigi
          style, binary alpha is a good fit anyway.
        - Soft glow: a gaussian-blurred alpha-mask derivative provides a
          warm halo around the mascot that pulses with ``energy``.
        - Breathing scale: the mascot breathes (±3%) and scales with
          energy up to ~107%. Scaling uses NEAREST — this matches the
          pixel-art look and is nearly free per frame.
    """

    def __init__(self, image_path: Path) -> None:
        raw = Image.open(image_path).convert("RGBA")
        # Once to target size; NEAREST is enough for per-frame scaling.
        base = raw.resize((WIN_W, WIN_H), Image.LANCZOS)
        self._base_rgba = np.asarray(base).copy()  # (H,W,4) uint8
        self._base_pil = base
        self._image_path = image_path

        # Soft glow mask from a gaussian-blurred alpha. Values 0..1.
        # Radius 6 gives a clearly visible but tight halo.
        alpha_only = base.split()[3]
        blurred = alpha_only.filter(ImageFilter.GaussianBlur(radius=6))
        self._glow_mask = np.asarray(blurred).astype(np.float32) / 255.0
        # Alpha threshold for the mascot itself (binary).
        self._solid_mask = np.asarray(alpha_only) >= 128

        # Body-part decomposition: extract the actual arm stubs as their
        # own sprites with a pivot point. This lets animations rotate the
        # REAL arm instead of drawing a second one next to it.
        # _arm_left_sprite, _arm_right_sprite: small RGBA images with arm + transparent.
        # _arm_left_pivot, _arm_right_pivot: (x, y) in the frame coordinate system,
        #     the point the rotation runs around (shoulder toward the body).
        # _body_no_arms_pil: mascot PIL without the arm pixels (alpha=0 there).
        # _arm_left_local_pivot: pivot relative to the sprite rectangle (in sprite pixels).
        decomp = self._decompose_arms(base)
        self._arm_left_sprite: Image.Image = decomp["left_sprite"]
        self._arm_right_sprite: Image.Image = decomp["right_sprite"]
        self._arm_left_pivot: tuple[int, int] = decomp["left_pivot"]
        self._arm_right_pivot: tuple[int, int] = decomp["right_pivot"]
        self._arm_left_local_pivot: tuple[int, int] = decomp["left_local_pivot"]
        self._arm_right_local_pivot: tuple[int, int] = decomp["right_local_pivot"]
        self._body_no_arms_pil: Image.Image = decomp["body_no_arms"]

        self._level: float = 0.0

        # Active animations — filtered per frame (is_finished).
        # A list rather than a set so FIFO order stays deterministic for
        # layering (animations added earlier draw beneath later ones).
        self._animations: list[Animation] = []

        # Mouth-anim deadline (in render-time `t` seconds). Mouth runs only
        # while `t < self._mouth_anim_until_t`. Outside that window the
        # original PNG mouth shows through unchanged.
        self._mouth_anim_until_t: float = -1.0

    # Hard-coded arm bounding boxes (measured on the 108x108 render of
    # jarvis-gigi-256.png, 2026-04-25). Heuristic detection was unreliable
    # because the body outline also contains yellow pixels (clusters overlap).
    # These bboxes capture ONLY the actual stub — a clean separation.
    # If the asset changes: re-measure manually and update these here.
    # Sprite bbox: large enough to capture the stub + all outline tips.
    # Body-erase bbox: tighter, exactly on the stub — otherwise "holes"
    # appear in the body at the default pose (rot=0), because the sprite
    # doesn't fully fill the cleared area.
    ARM_LEFT_BBOX = (10, 55, 22, 79)
    ARM_LEFT_PIVOT = (20, 62)
    ARM_LEFT_ERASE_BBOX = (12, 58, 21, 76)
    ARM_RIGHT_BBOX = (86, 57, 99, 79)
    ARM_RIGHT_PIVOT = (87, 62)
    ARM_RIGHT_ERASE_BBOX = (87, 60, 98, 76)

    # Mouth overlay constants. Center mirrors the SVG ellipse cx=128 cy=146 in
    # the 256px source; rx/ry mirror the SVG (rx=7 ry=10), scaled to 108px:
    # rx≈3 ry=1..7. Animation runs only while _mouth_anim_until_t > t.
    MOUTH_CENTER = (54, 62)
    MOUTH_RX = 3.2
    MOUTH_RY_CLOSED = 0.6
    MOUTH_RY_OPEN = 7.0
    MOUTH_ERASE_RX = 5.5
    MOUTH_ERASE_RY = 9.0
    MOUTH_BG_COLOR = (14, 14, 14)
    MOUTH_FG_COLOR = (255, 229, 0)
    MOUTH_INNER_COLOR = (5, 5, 5)
    MOUTH_ANIM_HZ = 1.5
    MOUTH_SUPERSAMPLE = 4

    @classmethod
    def _decompose_arms(cls, base: Image.Image) -> dict:
        """Separates the mascot's arm stubs from the body as separate sprites.

        Uses hard-coded bounding boxes (ARM_LEFT_BBOX/ARM_RIGHT_BBOX)
        instead of heuristic yellow-cluster detection — the latter also
        picked up the body-outline pixels and produced unclean sprites with
        stray pixels that distorted the arm after rotation.

        Body erase: bbox + 2px padding is set entirely to alpha=0, so the
        rotated arm has a clear path (no leftover outline in the shoulder
        region that would otherwise cover the raised arm).
        """
        arr = np.asarray(base)  # (H,W,4)

        def _extract_from_bbox(
            sprite_bbox: tuple[int, int, int, int],
            pivot_abs: tuple[int, int],
            erase_bbox: tuple[int, int, int, int],
        ) -> dict:
            x0, y0, x1, y1 = sprite_bbox
            # Sprite crop: bbox + padding for rotation headroom
            pad = 3
            sprite_x0 = max(0, x0 - pad)
            sprite_y0 = max(0, y0 - pad)
            sprite_x1 = min(arr.shape[1], x1 + 1 + pad)
            sprite_y1 = min(arr.shape[0], y1 + 1 + pad)
            crop = arr[sprite_y0:sprite_y1, sprite_x0:sprite_x1].copy()
            # Sprite mask: keep only pixels inside sprite_bbox
            local_x0 = x0 - sprite_x0
            local_y0 = y0 - sprite_y0
            local_x1 = x1 + 1 - sprite_x0
            local_y1 = y1 + 1 - sprite_y0
            mask = np.zeros(crop.shape[:2], dtype=bool)
            mask[local_y0:local_y1, local_x0:local_x1] = True
            crop[~mask, 3] = 0
            sprite = Image.fromarray(crop, mode="RGBA")

            # Body erase: tighter than sprite_bbox, exactly on the stub.
            # This way the default pose (rot=0) fully fills the erase hole
            # and the body looks normal at idle.
            ex0, ey0, ex1, ey1 = erase_bbox
            body_erase = np.zeros(arr.shape[:2], dtype=bool)
            body_erase[ey0 : ey1 + 1, ex0 : ex1 + 1] = True

            # Local pivot relative to the sprite crop
            pivot_local = (pivot_abs[0] - sprite_x0, pivot_abs[1] - sprite_y0)
            return {
                "sprite": sprite,
                "pivot": pivot_abs,
                "local_pivot": pivot_local,
                "body_erase_mask": body_erase,
            }

        left = _extract_from_bbox(
            cls.ARM_LEFT_BBOX,
            cls.ARM_LEFT_PIVOT,
            cls.ARM_LEFT_ERASE_BBOX,
        )
        right = _extract_from_bbox(
            cls.ARM_RIGHT_BBOX,
            cls.ARM_RIGHT_PIVOT,
            cls.ARM_RIGHT_ERASE_BBOX,
        )

        # Body without arms: set all pixels inside the dilated arm region
        # to alpha=0 — removes both the yellow stubs and the adjacent
        # outline contours that would otherwise cover the raised arm.
        body_arr = arr.copy()
        for entry in (left, right):
            mask = entry.get("body_erase_mask")
            if mask is None:
                continue
            body_arr[mask, 3] = 0
        body_no_arms = Image.fromarray(body_arr, mode="RGBA")

        return {
            "left_sprite": left["sprite"],
            "right_sprite": right["sprite"],
            "left_pivot": left["pivot"],
            "right_pivot": right["pivot"],
            "left_local_pivot": left["local_pivot"],
            "right_local_pivot": right["local_pivot"],
            "body_no_arms": body_no_arms,
        }

    # ------------------------------------------------------------------
    # Animation API (called by OrbOverlay, Tk main thread)
    # ------------------------------------------------------------------

    def add_animation(self, animation: Animation) -> None:
        """Adds a running animation. Threading: the caller ensures this
        happens on the Tk main thread (OrbOverlay queues via root.after).
        """
        self._animations.append(animation)

    def stop_animation(self, name: str) -> int:
        """Removes all running animations with the given name.
        Returns the number of stopped instances."""
        before = len(self._animations)
        self._animations = [a for a in self._animations if a.name != name]
        return before - len(self._animations)

    def clear_animations(self) -> None:
        self._animations.clear()

    def active_animation_names(self) -> list[str]:
        return [a.name for a in self._animations]

    def start_mouth_anim(self, duration_s: float, t_now: float) -> None:
        """Schedule the mouth-talk overlay to run for `duration_s` seconds."""
        if duration_s <= 0:
            return
        self._mouth_anim_until_t = t_now + duration_s

    def stop_mouth_anim(self) -> None:
        """Immediately end the mouth-talk overlay."""
        self._mouth_anim_until_t = -1.0

    def _overlay_mouth(self, frame: np.ndarray, t: float) -> None:
        """Replace the mouth area with a smoothly animated open/close ellipse.

        Renders into a 4× super-sampled PIL buffer and LANCZOS-downscales,
        so the mouth opens with sub-pixel-smooth anti-aliased edges instead
        of jumping between integer pixel rows.
        """
        if t >= self._mouth_anim_until_t:
            return

        cx, cy = self.MOUTH_CENTER
        openness = 0.5 - 0.5 * math.cos(t * self.MOUTH_ANIM_HZ * TAU)
        ry = self.MOUTH_RY_CLOSED + openness * (self.MOUTH_RY_OPEN - self.MOUTH_RY_CLOSED)
        rx = self.MOUTH_RX

        sub_pad = 4
        sub_w_half = int(math.ceil(self.MOUTH_ERASE_RX)) + sub_pad
        sub_h_half = int(math.ceil(self.MOUTH_ERASE_RY)) + sub_pad
        x0 = max(0, cx - sub_w_half)
        y0 = max(0, cy - sub_h_half)
        x1 = min(frame.shape[1], cx + sub_w_half + 1)
        y1 = min(frame.shape[0], cy + sub_h_half + 1)
        if x1 <= x0 or y1 <= y0:
            return
        sub_w = x1 - x0
        sub_h = y1 - y0

        ss = self.MOUTH_SUPERSAMPLE
        sub_pil = Image.fromarray(frame[y0:y1, x0:x1], mode="RGB")
        big = sub_pil.resize((sub_w * ss, sub_h * ss), Image.NEAREST)
        draw = ImageDraw.Draw(big)

        big_cx = (cx - x0) * ss
        big_cy = (cy - y0) * ss

        erx = self.MOUTH_ERASE_RX * ss
        ery = self.MOUTH_ERASE_RY * ss
        draw.ellipse(
            [big_cx - erx, big_cy - ery, big_cx + erx, big_cy + ery],
            fill=self.MOUTH_BG_COLOR,
        )

        rx_b = rx * ss
        ry_b = ry * ss
        draw.ellipse(
            [big_cx - rx_b, big_cy - ry_b, big_cx + rx_b, big_cy + ry_b],
            fill=self.MOUTH_FG_COLOR,
        )

        if ry >= 1.6:
            inner_rx = max(0.6, rx - 1.4) * ss
            inner_ry = max(0.4, ry - 1.4) * ss
            draw.ellipse(
                [big_cx - inner_rx, big_cy - inner_ry, big_cx + inner_rx, big_cy + inner_ry],
                fill=self.MOUTH_INNER_COLOR,
            )

        small = big.resize((sub_w, sub_h), Image.LANCZOS)
        frame[y0:y1, x0:x1] = np.asarray(small)

    def _aggregate_transform(self, t: float) -> Transform:
        """Folds all active animation transforms into one combined transform."""
        result = identity_transform()
        for anim in self._animations:
            result = result.combine(anim.transform(t))
        return result

    def _aggregate_arm_transforms(self, t: float) -> tuple[ArmTransform, ArmTransform]:
        """Folds the left and right arm transforms from all active animations."""
        left = identity_arm()
        right = identity_arm()
        for anim in self._animations:
            left = left.combine(anim.arm_left_transform(t))
            right = right.combine(anim.arm_right_transform(t))
        return left, right

    # ------------------------------------------------------------------
    # Render pipeline
    # ------------------------------------------------------------------

    def render(self, t: float, mode: str, ext_level: float | None) -> Image.Image:
        # Level smoothing mirrors the previous voice-reactive dynamics; the
        # per-mode curve itself lives in ``mode_energy`` so it stays testable.
        raw = mode_energy(mode, t, ext_level)
        self._level += (raw - self._level) * 0.08
        breath = 0.5 + 0.5 * math.sin(t * 0.9)
        energy = max(self._level, breath * 0.1)

        # --- Animation lifecycle: remove finished animations now, so their
        # transforms and overlays no longer contribute a "last frame".
        # Important before the aggregate_transform call.
        if self._animations:
            self._animations = [a for a in self._animations if not a.is_finished(t)]

        anim_transform = self._aggregate_transform(t)

        # Magenta background (color key → transparent)
        frame = np.empty((WIN_H, WIN_W, 3), dtype=np.uint8)
        frame[:] = COLOR_KEY_RGB

        # --- 1. Warm halo around the mascot (gaussian-blurred alpha as mask)
        # The color key is magenta — soft-blending with magenta creates pink
        # fringes. So the halo is HARD: a pixel is either visible (full gold
        # mix) or magenta (transparent). The threshold boundary shifts
        # dynamically with energy — at high energy more of the blur gradient
        # becomes visible and the halo feels "breathing/expanding".
        halo_threshold = 0.55 - energy * 0.35  # 0.55 calm → 0.20 loud
        halo_mask = (self._glow_mask > halo_threshold) & ~self._solid_mask
        if halo_mask.any():
            # Inside the halo: intensity rises with the glow_mask value
            # (close to the mascot = brighter, outer edge = darker gold).
            intensity = np.clip(
                (self._glow_mask[halo_mask] - halo_threshold) / (1.0 - halo_threshold),
                0.0,
                1.0,
            )
            # Gold gradient: dark warm (80, 50, 0) → bright gold (255, 210, 80)
            frame[halo_mask, 0] = (80 + 175 * intensity).astype(np.uint8)
            frame[halo_mask, 1] = (50 + 160 * intensity).astype(np.uint8)
            frame[halo_mask, 2] = (0 + 80 * intensity).astype(np.uint8)

        # --- 2. PRE layer from animations (e.g. wind streaks behind the ghost)
        if self._animations:
            pre_layer = self._make_overlay_layer(t, which="pre")
            if pre_layer is not None:
                self._composite_layer(frame, pre_layer)

        # --- 3. Body (without arms) + arms rendered separately with pivot rotation
        # The animation transform (scale, rotation, dx/dy, brightness) is
        # added/multiplied ON TOP of the breathing scale here.
        breath_scale = 1.0 + 0.04 * math.sin(t * 0.9) + 0.03 * energy
        total_scale = breath_scale * anim_transform.scale
        sx = total_scale * anim_transform.skew_x
        sy = total_scale * anim_transform.skew_y
        target_w = max(16, int(round(WIN_W * sx)))
        target_h = max(16, int(round(WIN_H * sy)))
        body_dx = int(round(anim_transform.dx))
        body_dy = int(round(anim_transform.dy))
        body_rot_deg = -math.degrees(anim_transform.rotation)
        brightness_factor = (1.0 + 0.08 * energy) * anim_transform.brightness

        # Render the body without arms (body pixels + eyes + mouth + jagged
        # base, ONLY the arm stubs are erased). This way rotated arms don't
        # overlap the static stub and no "double-arm" artifacts appear.
        self._composite_sprite_centered(
            frame=frame,
            sprite_pil=self._body_no_arms_pil,
            target_w=target_w,
            target_h=target_h,
            rot_deg=body_rot_deg,
            offset_dx=body_dx,
            offset_dy=body_dy,
            brightness=brightness_factor,
        )

        # Aggregate arm transforms from the active animations
        arm_left_t, arm_right_t = self._aggregate_arm_transforms(t)

        # Left arm — the pivot is the point of the stub closest to the body.
        # When the body scales, the pivot scales with it (body-scale-aware).
        if arm_left_t.visible:
            self._composite_arm(
                frame=frame,
                arm_sprite=self._arm_left_sprite,
                local_pivot=self._arm_left_local_pivot,
                world_pivot=self._arm_left_pivot,
                arm_t=arm_left_t,
                body_scale_x=sx,
                body_scale_y=sy,
                body_offset_dx=body_dx,
                body_offset_dy=body_dy,
                body_rotation_rad=anim_transform.rotation,
                brightness=brightness_factor,
            )
        if arm_right_t.visible:
            self._composite_arm(
                frame=frame,
                arm_sprite=self._arm_right_sprite,
                local_pivot=self._arm_right_local_pivot,
                world_pivot=self._arm_right_pivot,
                arm_t=arm_right_t,
                body_scale_x=sx,
                body_scale_y=sy,
                body_offset_dx=body_dx,
                body_offset_dy=body_dy,
                body_rotation_rad=anim_transform.rotation,
                brightness=brightness_factor,
            )

        # --- 3b. Talking-mouth overlay. Only repaints a small ellipse, only
        # while a SPEAKING session is active (toggled by start/stop_mouth_anim
        # from OrbBusBridge on AudioOutFirst events).
        self._overlay_mouth(frame, t)

        # --- 4. POST layer from animations (hand, thought bubble, phone, Z-Z-Z…)
        if self._animations:
            post_layer = self._make_overlay_layer(t, which="post")
            if post_layer is not None:
                self._composite_layer(frame, post_layer)

        return Image.fromarray(frame, mode="RGB")

    # ------------------------------------------------------------------
    # Overlay helpers
    # ------------------------------------------------------------------

    def _make_overlay_layer(self, t: float, which: str) -> Image.Image | None:
        """Asks all animations to paint their overlays into an RGBA layer.
        Returns None if no animation touched the layer
        (a cheap pixel check via the alpha-channel sum).
        """
        layer = Image.new("RGBA", (WIN_W, WIN_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        any_drawn = False
        for anim in self._animations:
            if which == "pre":
                anim.draw_pre(draw, t)
            else:
                anim.draw_post(draw, t)
            any_drawn = True
        if not any_drawn:
            return None
        # Fast path: if the whole layer is empty (alpha=0) → None instead of compositing.
        # We can check this without an extra walk: Pillow has getbbox().
        if layer.getbbox() is None:
            return None
        return layer

    def _composite_sprite_centered(
        self,
        frame: np.ndarray,
        sprite_pil: Image.Image,
        target_w: int,
        target_h: int,
        rot_deg: float,
        offset_dx: int,
        offset_dy: int,
        brightness: float,
    ) -> None:
        """Scales and rotates a sprite, placing it centered + offset in the frame.

        Used for the body (the whole mascot minus arms). The sprite is
        scaled to target_w x target_h, then optionally rotated (around
        center), then placed onto the frame using binary alpha (>= 128).
        """
        if target_w == WIN_W and target_h == WIN_H and abs(rot_deg) < 0.05:
            tmp = sprite_pil
        else:
            tmp = sprite_pil.resize((target_w, target_h), Image.NEAREST)
            if abs(rot_deg) >= 0.05:
                tmp = tmp.rotate(rot_deg, resample=Image.NEAREST, fillcolor=(0, 0, 0, 0))
        arr = np.asarray(tmp)
        ph, pw = arr.shape[:2]
        cx_target = WIN_W // 2 + offset_dx
        cy_target = WIN_H // 2 + offset_dy
        x0 = cx_target - pw // 2
        y0 = cy_target - ph // 2
        self._blit_rgba_into_frame(frame, arr, x0, y0, brightness)

    def _composite_arm(
        self,
        frame: np.ndarray,
        arm_sprite: Image.Image,
        local_pivot: tuple[int, int],
        world_pivot: tuple[int, int],
        arm_t: ArmTransform,
        body_scale_x: float,
        body_scale_y: float,
        body_offset_dx: int,
        body_offset_dy: int,
        body_rotation_rad: float,
        brightness: float,
    ) -> None:
        """Renders an arm sprite with pivot rotation around the shoulder.

        Math:
            1. The sprite has a local pivot (shoulder in the sprite coordinate system).
            2. The world pivot is the shoulder in the frame (108x108 system).
            3. The body transform (scale + body rotation around the body center)
               moves the world pivot — we have to move the shoulder along with it.
            4. The arm's own rotation rotates the sprite AROUND its local pivot.
            5. The sprite is placed so that the local pivot lands on the
               (shifted) world pivot.

        PIL.rotate(center=...) rotates an image around a freely chosen point
        — we use that for the pivot rotation. expand=True enlarges the
        bounds so the rotated arm doesn't clip.
        """
        if arm_sprite.size == (1, 1):
            return  # Decomposition produced an empty sprite (asset mismatch)

        rot_deg_arm = -math.degrees(arm_t.rotation)
        if abs(rot_deg_arm) < 0.05:
            rotated = arm_sprite
            new_pivot = local_pivot
        else:
            # Standard trick: pad the sprite into a larger canvas so the
            # pivot sits exactly in the center. Then rotate with center=center —
            # this behaves predictably (PIL.rotate without center, expand=False,
            # rotates pixel-stably around the image midpoint).
            sw, sh = arm_sprite.size
            px, py = local_pivot
            # How far is the pivot from the right/bottom edge?
            # The new canvas must be large enough that the maximum distance
            # from the pivot to any sprite corner has room in every direction
            # (otherwise the rotation clips sprite pixels).
            max_dist = int(math.ceil(math.hypot(max(px, sw - px), max(py, sh - py))))
            canvas_size = 2 * max_dist + 4
            canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
            # The sprite's pivot should land at the canvas center
            paste_x = canvas_size // 2 - px
            paste_y = canvas_size // 2 - py
            canvas.paste(arm_sprite, (paste_x, paste_y))
            # Rotation around the image midpoint — the center default applies, expand=False
            rotated = canvas.rotate(rot_deg_arm, resample=Image.NEAREST, fillcolor=(0, 0, 0, 0))
            # The new pivot position is the image midpoint
            new_pivot = (canvas_size // 2, canvas_size // 2)

        # World pivot of the shoulder, derived from the body center + the
        # mascot-relative position. The unscaled world pivot is `world_pivot`
        # in the 108-frame. When scaling, it must be scaled relative to the body center.
        cx_body = WIN_W / 2.0
        cy_body = WIN_H / 2.0
        # Body-relative shoulder position (before scaling)
        rel_x = world_pivot[0] - cx_body
        rel_y = world_pivot[1] - cy_body
        # Scale
        rel_x *= body_scale_x
        rel_y *= body_scale_y
        # Body rotation: rotate the shoulder with the body
        if abs(body_rotation_rad) >= 1e-4:
            cr = math.cos(body_rotation_rad)
            sr = math.sin(body_rotation_rad)
            rel_x_r = rel_x * cr - rel_y * sr
            rel_y_r = rel_x * sr + rel_y * cr
            rel_x, rel_y = rel_x_r, rel_y_r
        # World pivot after the body transform
        world_pivot_x = cx_body + rel_x + body_offset_dx + arm_t.dx
        world_pivot_y = cy_body + rel_y + body_offset_dy + arm_t.dy

        # Place the sprite so that new_pivot lands on world_pivot
        x0 = int(round(world_pivot_x - new_pivot[0]))
        y0 = int(round(world_pivot_y - new_pivot[1]))

        arr = np.asarray(rotated)
        self._blit_rgba_into_frame(frame, arr, x0, y0, brightness)

    @staticmethod
    def _blit_rgba_into_frame(
        frame: np.ndarray,
        rgba_arr: np.ndarray,
        x0: int,
        y0: int,
        brightness: float,
    ) -> None:
        """Blits an RGBA sprite at position (x0, y0) into the 108-px frame.

        Binary alpha threshold (>= 128) so the color-key magenta is never
        touched. Brightness multiplies the RGB values (clipped to 255).
        """
        ph, pw = rgba_arr.shape[:2]
        src_x0 = max(0, -x0)
        src_y0 = max(0, -y0)
        src_x1 = pw - max(0, (x0 + pw) - WIN_W)
        src_y1 = ph - max(0, (y0 + ph) - WIN_H)
        dst_x0 = max(0, x0)
        dst_y0 = max(0, y0)
        dst_x1 = dst_x0 + (src_x1 - src_x0)
        dst_y1 = dst_y0 + (src_y1 - src_y0)
        if src_x1 <= src_x0 or src_y1 <= src_y0:
            return
        placed = rgba_arr[src_y0:src_y1, src_x0:src_x1]
        solid = placed[:, :, 3] >= 128
        if not solid.any():
            return
        rgb = placed[:, :, :3].astype(np.float32) * brightness
        frame_slice = frame[dst_y0:dst_y1, dst_x0:dst_x1]
        frame_slice[solid] = np.clip(rgb[solid], 0, 255).astype(np.uint8)

    @staticmethod
    def _composite_layer(frame: np.ndarray, layer: Image.Image) -> None:
        """In-place RGBA-over-RGB composite, color-key-safe (magenta).

        Strategy:
            - Layer pixels with alpha < 64 → ignore (don't draw).
            - Pixels with alpha >= 64:
                * If the frame pixel is magenta (color key, transparent) → HARD
                  set. Otherwise pink mixed pixels would appear as fringes
                  showing through in the Tk window.
                * If the frame pixel is non-magenta (inside the halo/mascot) →
                  soft-blend over alpha. Here the frame is opaque anyway,
                  so mixed pixels stay visible as a clean transition.
        """
        layer_arr = np.asarray(layer)  # (H,W,4)
        alpha = layer_arr[:, :, 3]
        draw_mask = alpha >= 64
        if not draw_mask.any():
            return
        src_rgb = layer_arr[:, :, :3].astype(np.float32)

        # Color-key detection: frame pixels that are exactly magenta → "transparent"
        is_magenta = (
            (frame[:, :, 0] == COLOR_KEY_RGB[0])
            & (frame[:, :, 1] == COLOR_KEY_RGB[1])
            & (frame[:, :, 2] == COLOR_KEY_RGB[2])
        )

        # 1) Hard-set over magenta (every draw_mask pixel over magenta is
        #    overwritten 1:1 with the layer color — no blending).
        hard_mask = draw_mask & is_magenta
        if hard_mask.any():
            frame[hard_mask] = src_rgb[hard_mask].astype(np.uint8)

        # 2) Soft-blend over already-occupied pixels (halo, mascot)
        soft_mask = draw_mask & ~is_magenta
        if soft_mask.any():
            a = (alpha[soft_mask].astype(np.float32) / 255.0)[:, None]
            dst = frame[soft_mask].astype(np.float32)
            blended = src_rgb[soft_mask] * a + dst * (1.0 - a)
            frame[soft_mask] = np.clip(blended, 0, 255).astype(np.uint8)


class OrbCommentBubble:
    """Speech bubble Toplevel that floats above the orb.

    Mirrors the look of MascotGigi.gigi-bubble from the Desktop App
    (dark body, yellow border, bold yellow text), rendered on a Tk Canvas
    with rounded corners + a tail pointer aimed at the mascot. Same
    LWA_COLORKEY transparency as the main orb.
    """

    def __init__(
        self,
        parent: tk.Tk,
        orb_x: int,
        orb_y: int,
        orb_w: int,
        screen_w: int,
        theme: BubbleTheme = MASCOT_BUBBLE_THEME,
    ) -> None:
        self._parent = parent
        self._orb_x = orb_x
        self._orb_y = orb_y
        self._orb_w = orb_w
        self._screen_w = screen_w
        self._theme = theme
        self._top: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._dismiss_after_id: str | None = None
        self._font: tkfont.Font | None = None
        self._transcript_font: tkfont.Font | None = None
        # Min-show + queue. Older queued entries are discarded; only the
        # most recent (text, duration) survives so the freshest line wins.
        self._show_until_t: float = 0.0
        self._queued_text: str | None = None
        self._queued_duration_ms: int = 0
        self._queue_after_id: str | None = None
        self._build()

    def update_anchor(self, orb_x: int, orb_y: int, screen_w: int) -> None:
        """Reposition reference for the next show() call (used after the
        underlying mascot moves due to taskbar resize / monitor change)."""
        self._orb_x = orb_x
        self._orb_y = orb_y
        self._screen_w = screen_w
        self.hide()

    def set_orb_width(self, orb_w: int) -> None:
        """Re-centre over an orb that changed size (a live style swap)."""
        self._orb_w = orb_w

    def _build(self) -> None:
        top = tk.Toplevel(self._parent)
        top.overrideredirect(True)
        top.wm_attributes("-topmost", True)
        # Same per-platform transparency split as the main orb root: macOS
        # has no color key, so the bubble Toplevel itself goes transparent;
        # a TclError degrades to an opaque (key-coloured) bubble.
        self._mac_transparent = False
        if sys.platform == "darwin":
            try:
                top.wm_attributes("-transparent", True)
                top.configure(bg="systemTransparent")
                self._mac_transparent = True
            except tk.TclError:
                import logging

                logging.getLogger("jarvis.orb").warning(
                    "macOS -transparent unsupported — the comment bubble renders opaque"
                )
                top.configure(bg=COLOR_KEY_HEX)
        else:
            top.wm_attributes("-transparentcolor", COLOR_KEY_HEX)
            top.configure(bg=COLOR_KEY_HEX)
        _hide_tk_window_from_task_switcher(top)
        top.withdraw()

        canvas = tk.Canvas(
            top,
            bg="systemTransparent" if self._mac_transparent else COLOR_KEY_HEX,
            highlightthickness=0,
            borderwidth=0,
        )
        canvas.pack(fill="both", expand=True)
        if self._mac_transparent:
            # New Toplevel = new NSWindow — give it the clear backing too
            # (BUG-075).
            top.update_idletasks()
            apply_macos_clear_backing()

        self._top = top
        self._canvas = canvas
        # Bind named fonts to this Toplevel's interpreter explicitly.  The
        # macOS host keeps a separate bootstrap Tk root alive before creating
        # the mascot root; relying on tkinter's process-global default root
        # would create the fonts in the wrong interpreter and make the bubble
        # fail as soon as it tries to draw text.
        weight = "bold" if self._theme.bold else "normal"
        self._font = tkfont.Font(
            root=top,
            family=BUBBLE_FONT_FAMILY,
            size=BUBBLE_FONT_SIZE,
            weight=weight,
        )
        self._transcript_font = tkfont.Font(
            root=top,
            family=BUBBLE_FONT_FAMILY,
            size=TRANSCRIPT_BUBBLE_FONT_SIZE,
            weight=weight,
        )

    def set_theme(self, theme: BubbleTheme) -> None:
        """Repaint in another style's colours from the next show() onwards.

        Called on a live style swap (mascot <-> voice orb). The bubble on
        screen right now is hidden rather than converted: a half-second of the
        old colours is nothing, a half-drawn bubble is a bug.
        """
        if theme == self._theme:
            return
        self._theme = theme
        weight = "bold" if theme.bold else "normal"
        for font in (self._font, self._transcript_font):
            if font is not None:
                try:
                    font.configure(weight=weight)
                except tk.TclError:
                    pass
        self.hide()

    def show(
        self,
        text: str,
        duration_ms: int = 3500,
        *,
        variant: str = "comment",
    ) -> None:
        if not self._top or not self._canvas or not self._font:
            return
        if variant == "transcript":
            self._show_transcript(text, duration_ms)
            return
        if not text:
            return

        # Min-show guard: if the current bubble has not been on screen long
        # enough yet, queue this one and let `_show_queued` fire it later.
        now = time.perf_counter()
        if now < self._show_until_t:
            self._queued_text = text
            self._queued_duration_ms = duration_ms
            if self._queue_after_id is None:
                wait_ms = max(1, int(round((self._show_until_t - now) * 1000)))
                self._queue_after_id = self._top.after(wait_ms, self._show_queued)
            return

        if self._dismiss_after_id is not None:
            try:
                self._top.after_cancel(self._dismiss_after_id)
            except tk.TclError:
                pass
            self._dismiss_after_id = None

        theme = self._theme
        # Probe text size by creating a throwaway text item and reading bbox.
        probe_id = self._canvas.create_text(
            0,
            0,
            text=text,
            font=self._font,
            anchor="nw",
            width=BUBBLE_MAX_WIDTH,
            fill=theme.text,
        )
        bbox = self._canvas.bbox(probe_id)
        self._canvas.delete(probe_id)
        if bbox is None:
            return
        text_w = max(BUBBLE_MIN_WIDTH, bbox[2] - bbox[0])
        text_h = bbox[3] - bbox[1]
        bubble_w = text_w + 2 * BUBBLE_PADDING_X
        body_h = text_h + 2 * BUBBLE_PADDING_Y
        tail_h = BUBBLE_TAIL_H if theme.tail else 0
        total_h = body_h + tail_h

        bubble_x = self._orb_x + self._orb_w // 2 - bubble_w // 2
        bubble_x = max(8, min(bubble_x, self._screen_w - bubble_w - 8))
        bubble_y = max(8, self._orb_y - total_h - BUBBLE_GAP_FROM_ORB)

        # Tail tip aligned to mascot center, clamped inside the bubble body.
        mascot_center_x = self._orb_x + self._orb_w // 2
        tail_tip_x = mascot_center_x - bubble_x
        tail_half = BUBBLE_TAIL_W // 2
        min_tip = BUBBLE_CORNER_RADIUS + tail_half + 2
        max_tip = bubble_w - BUBBLE_CORNER_RADIUS - tail_half - 2
        if min_tip <= max_tip:
            tail_tip_x = max(min_tip, min(tail_tip_x, max_tip))
        else:
            tail_tip_x = bubble_w // 2

        self._top.geometry(f"{bubble_w}x{total_h}+{bubble_x}+{bubble_y}")
        self._canvas.configure(width=bubble_w, height=total_h)
        self._canvas.delete("all")

        self._draw_rounded_rect(
            1,
            1,
            bubble_w - 1,
            body_h - 1,
            theme.corner_radius,
            fill=theme.bg,
            outline=theme.border,
            width=theme.border_width,
        )

        if theme.tail:
            tail_base_y = body_h - 1
            tail_points = [
                tail_tip_x - tail_half,
                tail_base_y,
                tail_tip_x + tail_half,
                tail_base_y,
                tail_tip_x,
                tail_base_y + BUBBLE_TAIL_H,
            ]
            self._canvas.create_polygon(
                tail_points,
                fill=theme.bg,
                outline=theme.border,
                width=theme.border_width,
            )
            # Hide the seam between body bottom and tail base.
            self._canvas.create_line(
                tail_tip_x - tail_half + 1,
                tail_base_y,
                tail_tip_x + tail_half - 1,
                tail_base_y,
                fill=theme.bg,
                width=theme.border_width + 1,
            )

        self._canvas.create_text(
            BUBBLE_PADDING_X,
            BUBBLE_PADDING_Y,
            text=text,
            font=self._font,
            anchor="nw",
            width=BUBBLE_MAX_WIDTH,
            fill=theme.text,
        )

        self._top.deiconify()
        try:
            self._top.lift()
        except tk.TclError:
            pass

        self._show_until_t = time.perf_counter() + min(BUBBLE_MIN_SHOW_S, duration_ms / 1000.0)
        self._dismiss_after_id = self._top.after(duration_ms, self.hide)

    def _show_transcript(self, text: str, duration_ms: int) -> None:
        if self._top is None or self._canvas is None or self._transcript_font is None:
            return

        if self._dismiss_after_id is not None:
            try:
                self._top.after_cancel(self._dismiss_after_id)
            except tk.TclError:
                pass
            self._dismiss_after_id = None
        if self._queue_after_id is not None:
            try:
                self._top.after_cancel(self._queue_after_id)
            except tk.TclError:
                pass
            self._queue_after_id = None
        self._queued_text = None

        theme = self._theme
        display_text = text.strip() or "..."
        bubble_w = min(TRANSCRIPT_BUBBLE_WIDTH, max(220, self._screen_w - 16))
        text_width = min(
            TRANSCRIPT_BUBBLE_TEXT_WIDTH,
            max(120, bubble_w - 2 * BUBBLE_PADDING_X),
        )
        line_height = max(1, self._transcript_font.metrics("linespace"))
        probe_id = self._canvas.create_text(
            0,
            0,
            text=display_text,
            font=self._transcript_font,
            anchor="nw",
            width=text_width,
            fill=theme.text,
        )
        bbox = self._canvas.bbox(probe_id)
        self._canvas.delete(probe_id)
        text_h = line_height if bbox is None else max(line_height, bbox[3] - bbox[1])
        visible_lines = _transcript_visible_line_count(text_h, line_height)
        body_h = _transcript_body_height(visible_lines, line_height)
        tail_h = BUBBLE_TAIL_H if theme.tail else 0
        total_h = body_h + tail_h

        mascot_center_x = self._orb_x + self._orb_w // 2
        # With a tail the bubble hangs off to one side so the pointer can aim at
        # the mascot's head; without one there is nothing to aim, and anything
        # but centred reads as a misplaced window.
        bubble_x = (
            mascot_center_x - bubble_w + 30 if theme.tail else mascot_center_x - bubble_w // 2
        )
        bubble_x = max(8, min(bubble_x, self._screen_w - bubble_w - 8))
        bubble_y = max(8, self._orb_y - total_h - BUBBLE_GAP_FROM_ORB)

        tail_tip_x = mascot_center_x - bubble_x
        tail_half = BUBBLE_TAIL_W // 2
        min_tip = BUBBLE_CORNER_RADIUS + tail_half + 2
        max_tip = bubble_w - BUBBLE_CORNER_RADIUS - tail_half - 2
        if min_tip <= max_tip:
            tail_tip_x = max(min_tip, min(tail_tip_x, max_tip))
        else:
            tail_tip_x = bubble_w // 2

        self._top.geometry(f"{bubble_w}x{total_h}+{bubble_x}+{bubble_y}")
        self._canvas.configure(width=bubble_w, height=total_h)
        self._canvas.delete("all")

        self._draw_rounded_rect(
            1,
            1,
            bubble_w - 1,
            body_h - 1,
            theme.corner_radius,
            fill=theme.bg,
            outline=theme.border,
            width=theme.border_width,
        )

        if theme.tail:
            tail_base_y = body_h - 1
            tail_points = [
                tail_tip_x - tail_half,
                tail_base_y,
                tail_tip_x + tail_half,
                tail_base_y,
                tail_tip_x,
                tail_base_y + BUBBLE_TAIL_H,
            ]
            self._canvas.create_polygon(
                tail_points,
                fill=theme.bg,
                outline=theme.border,
                width=theme.border_width,
            )
            self._canvas.create_line(
                tail_tip_x - tail_half + 1,
                tail_base_y,
                tail_tip_x + tail_half - 1,
                tail_base_y,
                fill=theme.bg,
                width=theme.border_width + 1,
            )

        self._canvas.create_text(
            BUBBLE_PADDING_X,
            body_h - BUBBLE_PADDING_Y,
            text=display_text,
            font=self._transcript_font,
            anchor="sw",
            width=text_width,
            fill=theme.text,
        )

        self._top.deiconify()
        try:
            self._top.lift()
        except tk.TclError:
            pass

        self._show_until_t = time.perf_counter()
        self._dismiss_after_id = self._top.after(duration_ms, self.hide)

    def _show_queued(self) -> None:
        self._queue_after_id = None
        text = self._queued_text
        duration_ms = self._queued_duration_ms
        self._queued_text = None
        self._queued_duration_ms = 0
        if text:
            self.show(text, duration_ms)

    def hide(self) -> None:
        if self._top is None:
            return
        if self._dismiss_after_id is not None:
            try:
                self._top.after_cancel(self._dismiss_after_id)
            except tk.TclError:
                pass
            self._dismiss_after_id = None
        if self._queue_after_id is not None:
            try:
                self._top.after_cancel(self._queue_after_id)
            except tk.TclError:
                pass
            self._queue_after_id = None
        self._queued_text = None
        try:
            self._top.withdraw()
        except tk.TclError:
            pass

    def _draw_rounded_rect(self, x1, y1, x2, y2, r, **kwargs) -> int:
        if self._canvas is None:
            return 0
        points = [
            x1 + r,
            y1,
            x1 + r,
            y1,
            x2 - r,
            y1,
            x2 - r,
            y1,
            x2,
            y1,
            x2,
            y1 + r,
            x2,
            y1 + r,
            x2,
            y2 - r,
            x2,
            y2 - r,
            x2,
            y2,
            x2 - r,
            y2,
            x2 - r,
            y2,
            x1 + r,
            y2,
            x1 + r,
            y2,
            x1,
            y2,
            x1,
            y2 - r,
            x1,
            y2 - r,
            x1,
            y1 + r,
            x1,
            y1 + r,
            x1,
            y1,
        ]
        return self._canvas.create_polygon(points, smooth=True, **kwargs)


class OrbControlRow:
    """The four round controls under the voice orb — see :mod:`ui.orb.controls`.

    Its own frameless, always-on-top Toplevel rather than extra pixels on the
    orb's window, for three reasons that all point the same way: the orb window
    is exactly the sphere (a taller window would put an invisible click-eating
    rectangle over whatever is underneath), the row has to appear and disappear
    on its own schedule, and the orb's frame loop must not repaint four static
    discs sixty times a second.

    Visibility is deliberately conditional (maintainer decision 2026-08-06): at
    rest the desktop shows a sphere and nothing else. The row arrives when the
    pointer does, or when a conversation is running — the two moments where a
    control is something the user is reaching for rather than clutter they did
    not ask for.
    """

    #: Grace period between losing the pointer and hiding, in ms. The row is a
    #: separate window a few pixels below the orb, so travelling from one to
    #: the other means genuinely leaving both — without this the row would
    #: vanish under the pointer on its way to being clicked.
    HOVER_GRACE_MS = 260

    #: After a hang-up, ignore clicks for this long. Same guard as the Jarvis
    #: Bar's close-X: the optimistic state flip must not let a second click
    #: land as "start a new session" (the request_hangup storm, 2026-06-19).
    CLICK_GUARD_S = 0.6

    def __init__(self, parent: tk.Tk, orb_x: int, orb_y: int, orb_w: int, orb_h: int) -> None:
        self._parent = parent
        self._orb_x = orb_x
        self._orb_y = orb_y
        self._orb_w = orb_w
        self._orb_h = orb_h
        self._top: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._image_id: int | None = None
        self._state = orb_controls.ControlState()
        self._visible = False
        self._pointer_inside = False
        self._hide_after_id: str | None = None
        self._pressed_action: str | None = None
        self._click_block_until = 0.0
        self._on_action: Callable[[str], None] | None = None
        self._width, self._height = orb_controls.row_size()
        self._build()

    # -- wiring ---------------------------------------------------------

    def set_on_action(self, callback: Callable[[str], None] | None) -> None:
        """Route a resolved button press ("attach"/"mic"/"close"/"speaker")."""
        self._on_action = callback

    def _build(self) -> None:
        top = tk.Toplevel(self._parent)
        top.overrideredirect(True)
        top.wm_attributes("-topmost", True)
        self._mac_transparent = False
        if sys.platform == "darwin":
            try:
                top.wm_attributes("-transparent", True)
                top.configure(bg="systemTransparent")
                self._mac_transparent = True
            except tk.TclError:
                logging.getLogger("jarvis.orb").warning(
                    "macOS -transparent unsupported — the orb controls render opaque"
                )
                top.configure(bg=COLOR_KEY_HEX)
        else:
            top.wm_attributes("-transparentcolor", COLOR_KEY_HEX)
            top.configure(bg=COLOR_KEY_HEX)
        _hide_tk_window_from_task_switcher(top)
        top.withdraw()

        canvas = tk.Canvas(
            top,
            width=self._width,
            height=self._height,
            bg="systemTransparent" if self._mac_transparent else COLOR_KEY_HEX,
            highlightthickness=0,
            borderwidth=0,
        )
        canvas.pack(fill="both", expand=True)
        if self._mac_transparent:
            top.update_idletasks()
            apply_macos_clear_backing()

        canvas.bind("<Motion>", self._on_motion)
        canvas.bind("<Enter>", self._on_enter)
        canvas.bind("<Leave>", self._on_leave)
        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<ButtonRelease-1>", self._on_release)

        self._top = top
        self._canvas = canvas
        self._repaint()

    # -- geometry -------------------------------------------------------

    def update_anchor(self, orb_x: int, orb_y: int) -> None:
        """Follow the orb after a drag / monitor change."""
        self._orb_x = orb_x
        self._orb_y = orb_y
        if self._visible:
            self._place()

    def set_orb_size(self, orb_w: int, orb_h: int) -> None:
        """Re-centre under an orb that changed size (a live style swap)."""
        self._orb_w = orb_w
        self._orb_h = orb_h
        if self._visible:
            self._place()

    def _place(self) -> None:
        if self._top is None:
            return
        x = self._orb_x + self._orb_w // 2 - self._width // 2
        y = self._orb_y + self._orb_h + orb_controls.ROW_GAP_FROM_ORB
        try:
            self._top.geometry(f"{self._width}x{self._height}+{x}+{y}")
        except tk.TclError:
            pass

    # -- state ----------------------------------------------------------

    def set_state(
        self,
        *,
        active: bool | None = None,
        speaker_muted: bool | None = None,
        can_attach: bool | None = None,
    ) -> None:
        """Update what the discs say, repainting only on a real change."""
        state = orb_controls.ControlState(
            active=self._state.active if active is None else bool(active),
            speaker_muted=(
                self._state.speaker_muted if speaker_muted is None else bool(speaker_muted)
            ),
            hovered=self._state.hovered,
            can_attach=self._state.can_attach if can_attach is None else bool(can_attach),
        )
        if state == self._state:
            return
        self._state = state
        self._repaint()

    def _repaint(self) -> None:
        if self._canvas is None:
            return
        try:
            frame = orb_controls.render_row(self._state, color_key=tuple(COLOR_KEY_RGB.tolist()))
            image = key_to_alpha(frame) if self._mac_transparent else frame
            self._photo = ImageTk.PhotoImage(image, master=self._canvas)
            if self._image_id is None:
                self._image_id = self._canvas.create_image(0, 0, anchor="nw", image=self._photo)
            else:
                self._canvas.itemconfigure(self._image_id, image=self._photo)
        except tk.TclError:
            pass

    # -- visibility -----------------------------------------------------

    def show(self) -> None:
        if self._top is None or self._visible:
            return
        self._cancel_pending_hide()
        self._place()
        try:
            self._top.deiconify()
            if self._mac_transparent:
                apply_macos_clear_backing()
            self._top.lift()
        except tk.TclError:
            return
        self._visible = True

    def hide(self) -> None:
        self._cancel_pending_hide()
        self._pointer_inside = False
        if self._state.hovered is not None:
            self._state = orb_controls.ControlState(
                active=self._state.active,
                speaker_muted=self._state.speaker_muted,
                hovered=None,
                can_attach=self._state.can_attach,
            )
            self._repaint()
        if self._top is None or not self._visible:
            return
        try:
            self._top.withdraw()
        except tk.TclError:
            pass
        self._visible = False

    def hide_after_grace(self) -> None:
        """Hide unless the pointer comes back within :attr:`HOVER_GRACE_MS`."""
        if self._top is None or not self._visible:
            return
        self._cancel_pending_hide()
        try:
            self._hide_after_id = self._top.after(self.HOVER_GRACE_MS, self._grace_elapsed)
        except tk.TclError:
            self.hide()

    def _grace_elapsed(self) -> None:
        self._hide_after_id = None
        if self._pointer_inside:
            return
        self.hide()

    def _cancel_pending_hide(self) -> None:
        if self._hide_after_id is None or self._top is None:
            self._hide_after_id = None
            return
        try:
            self._top.after_cancel(self._hide_after_id)
        except tk.TclError:
            pass
        self._hide_after_id = None

    @property
    def pointer_inside(self) -> bool:
        return self._pointer_inside

    @property
    def visible(self) -> bool:
        return self._visible

    def destroy(self) -> None:
        self._cancel_pending_hide()
        top = self._top
        self._top = None
        self._canvas = None
        self._photo = None
        if top is None:
            return
        try:
            top.destroy()
        except tk.TclError:
            pass

    # -- pointer --------------------------------------------------------

    def _on_enter(self, event: tk.Event) -> None:
        self._pointer_inside = True
        self._cancel_pending_hide()
        self._on_motion(event)

    def _on_leave(self, _event: tk.Event) -> None:
        self._pointer_inside = False
        self._set_hovered(None)
        self.hide_after_grace()

    def _on_motion(self, event: tk.Event) -> None:
        self._set_hovered(orb_controls.hit_test(event.x, event.y))

    def _set_hovered(self, action: str | None) -> None:
        if action == self._state.hovered:
            return
        self._state = orb_controls.ControlState(
            active=self._state.active,
            speaker_muted=self._state.speaker_muted,
            hovered=action,
            can_attach=self._state.can_attach,
        )
        self._repaint()
        if self._canvas is not None:
            try:
                self._canvas.configure(cursor="hand2" if action else "")
            except tk.TclError:
                pass

    def _on_press(self, event: tk.Event) -> None:
        self._pressed_action = orb_controls.hit_test(event.x, event.y)

    def _on_release(self, event: tk.Event) -> None:
        pressed = self._pressed_action
        self._pressed_action = None
        if pressed is None:
            return
        # Press and release must agree: a pointer that slid off the disc did
        # not click it, and every action here is one the user would rather not
        # trigger by accident.
        if orb_controls.hit_test(event.x, event.y) != pressed:
            return
        if time.monotonic() < self._click_block_until:
            return
        if pressed in ("mic", "close") and self._state.active:
            self._click_block_until = time.monotonic() + self.CLICK_GUARD_S
        callback = self._on_action
        if callback is None:
            return
        try:
            callback(pressed)
        except Exception:  # noqa: BLE001 — a bad action must not kill the Tk loop
            logging.getLogger("jarvis.orb").debug(
                "orb control action %r failed", pressed, exc_info=True
            )


class OrbOverlay:
    """Public facade — Tkinter-based, thread-safe via root.after(0, ...).

    tkinter itself is not thread-safe, but `root.after(0, fn)` schedules
    fn safely onto the Tk main loop. So we route every call from other
    threads (Jarvis-Core, demo thread) through it.
    """

    def __init__(
        self,
        sticky: bool = False,
        mic_reactive: bool = False,
        style: str | None = None,
        mascot_path: str | Path | None = None,
    ) -> None:
        """
        style: one of :data:`ORB_STYLES` — ``"mascot"`` (the Gigi ghost) or
        ``"voice_orb"`` (the procedural weather sphere). Anything else,
        including the legacy ``"orb"``, is coerced to ``"mascot"``.
        mascot_path: optional explicit path, otherwise via ENV or the default asset.
        ENV-Override: ``JARVIS_ORB_STYLE=mascot|voice_orb`` selects the look;
        unknown values are ignored.
        """
        self._sticky = sticky
        self._mic_reactive = mic_reactive
        self._root: tk.Tk | None = None
        self._canvas: tk.Canvas | None = None
        self._comment_bubble: OrbCommentBubble | None = None
        self._renderer: MascotRenderer | VoiceOrbRenderer | None = None
        # Cached mascot anchor + min-show-time guard.
        self._mascot_x: int = 0
        self._mascot_y: int = 0
        # Drag-and-pin state. _manual_pinned switches the 1500 ms recheck
        # loop into clamp-only mode and tells boot to skip the taskbar
        # anchor.
        self._manual_pinned: bool = False
        self._drag_state: _DragState | None = None
        # Double-double-click on the orb fires this callback (user spec
        # 2026-05-17 + revision 2026-05-18: two double-clicks within
        # MUTE_GESTURE_WINDOW_MS toggle the Jarvis-wide mute). The orb
        # stays decoupled from the bus — OrbBusBridge injects a
        # callable that publishes ``VoiceMuteToggleRequested``.
        self._mute_toggle_callback: Callable[[], None] | None = None
        # Right-click on the orb raises the main desktop window. OrbBusBridge
        # injects a callable that publishes ``ShowWindowRequested``; the orb
        # itself stays bus-agnostic (same contract as the mute toggle).
        self._on_show_window: Callable[[], None] | None = None
        # The control row's actions. Each is optional: wired, the callback
        # decides (the macOS host forwards it to the parent process, which is
        # the only place a SpeechPipeline exists); unwired, the row falls back
        # to the live pipeline in THIS process — the same two-step the Jarvis
        # Bar uses for its own talk / hang-up buttons.
        self._on_talk: Callable[[], None] | None = None
        self._on_hangup: Callable[[], None] | None = None
        self._on_speaker_toggle: Callable[[], None] | None = None
        #: The four round controls under the sphere. Only the voice orb wears
        #: them — the mascot has its own arms and expressions, and a row of
        #: discs under the ghost would read as a toolbar bolted to a cartoon.
        self._controls: OrbControlRow | None = None
        self._pointer_over_orb = False
        self._speaker_muted = False
        # Counter + Tk timer-id for the two-double-click gesture. The
        # earlier single-double-click implementation muted Jarvis as soon
        # as the user clicked twice on the popup logo, which fired
        # accidentally whenever the user clicked the freshly appeared
        # orb. Two double-clicks (four rapid clicks) is intentional.
        self._mute_click_count: int = 0
        self._mute_click_reset_id: str | None = None
        # ADR-0016 visible-feedback contract: bridge injects a callable
        # that publishes ``UserVisibleFeedback`` from the bus side. The
        # orb stays bus-agnostic; this lets unit tests run without a bus.
        # Signature: ``(mode: str, observed: dict[str, Any]) -> None``.
        self._feedback_publisher: Callable[[str, dict[str, Any]], None] | None = None
        self._show_until_t: float = 0.0
        self._pending_hide_after_id: str | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._image_id: int | None = None
        self._mode: str = "idle"
        self._ext_level: float | None = None
        self._t0: float = 0.0
        self._running: bool = False
        self._started = threading.Event()
        self._ui_queue: queue.Queue = queue.Queue()
        self._tk_thread_id: int | None = None
        # Lazy import so the orb stays startable even without sounddevice
        # (mic-reactive mode is optional).
        self._mic = None

        # Style resolution. Keep accepting the old knobs so existing launchers
        # do not crash, but never render the removed legacy orb.
        env_style = (os.environ.get("JARVIS_ORB_STYLE") or "").strip().lower()
        requested_style = env_style or (style or DEFAULT_ORB_STYLE).lower()
        self._style = _coerce_style(requested_style)
        self._mascot_path_hint = str(mascot_path) if mascot_path else None
        # Window size belongs to the style, not to the module: the voice orb is
        # half again as large as the mascot (see VOICE_ORB_WIN_W).
        self._win_w, self._win_h = window_size_for_style(self._style)

    # --- Public API ----------------------------------------------------

    def start(self, auto_demo: bool = False) -> None:
        self._tk_thread_id = threading.get_ident()
        # Test-safety guard: under pytest, never put a REAL mascot window on the
        # developer's desktop unless explicitly opted in (JARVIS_GUI_TESTS=1).
        # A routine `pytest tests/unit/` run (and parallel runs across worktrees)
        # would otherwise leave a live 'JarvisOrb' Tk window on screen until the
        # test process exits — users saw several mascots stacked beside their
        # chosen overlay style even though the desktop app was rendering only the
        # selected one. No production effect: pytest is never imported in the
        # live app, so this branch is dead there. ``_started`` is still set so a
        # ``start_in_thread`` caller unblocks immediately instead of timing out.
        if "pytest" in sys.modules and not os.environ.get("JARVIS_GUI_TESTS"):
            self._started.set()
            return
        # DPI awareness MUST be set before Win32 GetWindowRect calls, else
        # taskbar coords come back DPI-virtualised and the mascot ends up
        # misplaced on 125%/150% scaled displays.
        _ensure_dpi_awareness()
        self._root = tk.Tk()
        self._root.title("JarvisOrb")
        self._root.overrideredirect(True)  # Frameless, no drop shadow
        self._root.wm_attributes("-topmost", True)
        # Per-pixel transparency, per platform: Windows keys out the magenta
        # color key on the layered window; macOS has no color key, so there
        # the WINDOW itself becomes transparent (Aqua-Tk's "-transparent" +
        # the systemTransparent background) and every frame carries a real
        # alpha channel instead (key_to_alpha in the frame loop).
        self._mac_transparent = False
        if sys.platform == "darwin":
            try:
                self._root.wm_attributes("-transparent", True)
                self._root.configure(bg="systemTransparent")
                self._mac_transparent = True
            except tk.TclError:
                import logging

                logging.getLogger("jarvis.orb").warning(
                    "macOS -transparent unsupported — the mascot will show its key colour"
                )
                self._root.configure(bg=COLOR_KEY_HEX)
        else:
            if not _apply_color_key(self._root):
                # No per-pixel transparency on this session: showing the window
                # would put an opaque magenta square on the user's desktop.
                # Tear the root down and report started, so the caller's
                # start_in_thread() unblocks instead of timing out.
                try:
                    self._root.destroy()
                except tk.TclError:
                    pass
                self._root = None
                self._started.set()
                return
            # The colour key belongs to THIS window only. Once this Tk loop
            # stops pumping — any long GIL-holding stretch on another thread
            # does it — Windows paints an unlayered ``Ghost`` stand-in over the
            # same rectangle, and the keyed-out padding arrives as an opaque
            # black box around the mascot. Same defect as the bar's.
            try:
                from jarvis.core.process_utils import disable_windows_app_ghosting

                disable_windows_app_ghosting()
            except Exception:  # noqa: BLE001 — cosmetic; the orb must still boot
                import logging

                logging.getLogger("jarvis.orb").debug(
                    "window-ghosting suppression unavailable — a stalled orb may "
                    "show an opaque box",
                    exc_info=True,
                )
        # Apply the Jarvis icon BEFORE hiding from the task switcher: Windows
        # caches the taskbar entry on first show, so even a brief flash with
        # the default pythonw icon sticks. Belt-and-suspenders: stable AppID
        # so this process doesn't share Python.exe's taskbar group, then Tk's
        # own iconbitmap, then Win32 WM_SETICON + SetClassLongPtrW (the only
        # way to override the class-default that drives the taskbar icon).
        _apply_jarvis_icon_to_tk_root(self._root)
        _hide_tk_window_from_task_switcher(self._root)

        # Resolve mascot anchor. If the user has manually pinned the orb
        # in a prior session, restore that position; otherwise compute
        # the live Windows-taskbar-aligned default.
        #
        # BUG-027 defense: when the persisted pin lives on a non-primary
        # monitor, resolve_placement reports recovered=True so we clear the
        # stale entry and fall back to the primary anchor. The user can opt
        # out via ``[overlay.mascot] allow_secondary_monitor_pin = true``
        # in jarvis.toml.
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        persisted = load_position_from_toml(JARVIS_TOML_PATH)
        # ADR-0016 L1 — boot-flash state. Populated only when the pin
        # honours a secondary monitor (Power-User escape hatch). In that
        # case we deiconify the orb at the PRIMARY anchor for ~800 ms
        # before migrating to the pin, so the user always SEES the orb on
        # boot regardless of where it was last dragged.
        self._boot_flash_target_xy: tuple[int, int] | None = None
        if persisted is not None and persisted.monitor:
            screens = screens_from_tk(self._root)
            allow_secondary = load_allow_secondary_monitor_pin(JARVIS_TOML_PATH)
            placement = resolve_placement(
                persisted,
                screens,
                mascot_size_px=self._win_w,
                require_primary=not allow_secondary,
            )
            if not placement.recovered:
                # Persisted monitor still present — honour user's pin.
                self._manual_pinned = True
                self._mascot_x = placement.abs_x
                self._mascot_y = placement.abs_y
                anchor = MascotAnchor(
                    x=placement.abs_x,
                    y=placement.abs_y,
                    taskbar_aligned=False,
                )
                # L1 gate (BUG-027 / ADR-0016): when the honored pin is on
                # a NON-primary monitor in a multi-monitor topology, start
                # the orb at the primary anchor and migrate after a brief
                # visible flash. Single-monitor and primary-pin boots
                # skip the flash entirely (no visual noise in 99% case).
                resolved_screen = next((s for s in screens if s.name == placement.monitor), None)
                if (
                    len(screens) > 1
                    and resolved_screen is not None
                    and not resolved_screen.is_primary
                ):
                    primary_anchor = self._resolve_anchor(screen_w, screen_h)
                    self._boot_flash_target_xy = (
                        placement.abs_x,
                        placement.abs_y,
                    )
                    self._root.geometry(
                        f"{self._win_w}x{self._win_h}+{primary_anchor.x}+{primary_anchor.y}"
                    )
                else:
                    self._root.geometry(
                        f"{self._win_w}x{self._win_h}+{placement.abs_x}+{placement.abs_y}"
                    )
            else:
                # Monitor gone, no screens, or pin on non-primary monitor
                # while require_primary is set — fall back to default and
                # clear the stale entry so next boot starts clean.
                try:
                    clear_position_in_toml(JARVIS_TOML_PATH)
                except OSError:
                    pass
                anchor = self._resolve_anchor(screen_w, screen_h)
                self._mascot_x = anchor.x
                self._mascot_y = anchor.y
                self._root.geometry(f"{self._win_w}x{self._win_h}+{anchor.x}+{anchor.y}")
        else:
            anchor = self._resolve_anchor(screen_w, screen_h)
            self._mascot_x = anchor.x
            self._mascot_y = anchor.y
            self._root.geometry(f"{self._win_w}x{self._win_h}+{anchor.x}+{anchor.y}")

        self._canvas = tk.Canvas(
            self._root,
            width=self._win_w,
            height=self._win_h,
            bg="systemTransparent" if self._mac_transparent else COLOR_KEY_HEX,
            highlightthickness=0,
            borderwidth=0,
        )
        self._canvas.pack(fill="both", expand=True)
        if self._mac_transparent:
            # Tk 9 needs the native backing cleared too (BUG-075); flush
            # geometry so the NSWindow exists before the AppKit pass.
            self._root.update_idletasks()
            apply_macos_clear_backing()

        # Drag + interaction bindings. Tk dispatch:
        #   <Button-1> → drag-start (always fires, even on a double-click)
        #   <B1-Motion> → drag-update (only fires while LMB held)
        #   <ButtonRelease-1> → drag-finish (or no-op if it was a click)
        #   <Double-Button-1> → mute toggle (fires after Button-1+Release)
        #   <Button-3>       → raise the main desktop window (spec 2026-06-02)
        #   <Button-2>       → reset position (moved off the old right-click menu)
        # User spec 2026-05-17: double-click on the orb mutes Jarvis.
        # Spec 2026-06-02: right-click now opens the Jarvis window (same as the
        # jarvis-bar). "Reset position" moved from the old right-click menu to
        # middle-click; mute stays on the double-double-click gesture. Drag-start
        # does not commit any geometry change until the threshold is crossed, so
        # a fast double-click stays harmless.
        self._canvas.bind("<ButtonPress-1>", self._on_drag_press)
        self._canvas.bind("<B1-Motion>", self._on_drag_motion)
        self._canvas.bind("<ButtonRelease-1>", self._on_drag_release)
        self._canvas.bind("<Double-Button-1>", self._on_mute_double_click)
        self._canvas.bind("<Button-3>", self._on_right_click)
        self._canvas.bind("<Button-2>", self._on_reset_double_click)
        # Pointer presence drives the control row (see OrbControlRow): reaching
        # for the orb is the moment its controls become useful.
        self._canvas.bind("<Enter>", self._on_orb_pointer_enter)
        self._canvas.bind("<Leave>", self._on_orb_pointer_leave)

        # Drag-drop onto the mascot (desktop extra, cross-platform via tkdnd).
        # Pure addition, fully guarded: a no-op when tkinterdnd2 is absent
        # (NullDropTarget) so the live overlay is never destabilised. Dropped
        # paths/text go to the process-global bridge, which the desktop app
        # marshals to the brain intake (jarvis/brain/drop_context.ingest_drop).
        try:
            from jarvis.overlay.drop_bridge import dispatch_drop
            from jarvis.overlay.drop_target import make_drop_target

            make_drop_target().register(self._canvas, dispatch_drop)
        except Exception:  # noqa: BLE001 — drop is optional; never block orb boot.
            logging.getLogger("jarvis.orb").debug(
                "mascot drop target registration skipped", exc_info=True
            )

        self._comment_bubble = OrbCommentBubble(
            parent=self._root,
            orb_x=anchor.x,
            orb_y=anchor.y,
            orb_w=self._win_w,
            screen_w=screen_w,
            theme=bubble_theme_for_style(self._style),
        )
        self._ensure_controls()

        self._renderer = self._build_renderer(self._style)
        self._t0 = time.perf_counter()
        self._running = True

        # ADR-0016 L1 — selective boot flash. When the persisted pin is
        # honored on a secondary monitor (Power-User mode), show the orb
        # at the primary anchor for 800 ms BEFORE migrating to the pin.
        # Skipped in single-monitor / primary-pin / default-anchor boots.
        if self._boot_flash_target_xy is not None and not self._sticky:
            target_x, target_y = self._boot_flash_target_xy
            self._root.deiconify()
            self._root.after(800, lambda: self._finish_boot_flash(target_x, target_y))
        elif not self._sticky:
            self._root.withdraw()

        self._started.set()

        # Start the mic listener only after window creation, so any
        # sounddevice errors (no mic, PortAudio init) surface only after the
        # orb is visible and aren't silently swallowed at startup.
        if self._mic_reactive:
            try:
                from ui.orb.mic_listener import MicListener

                self._mic = MicListener(on_level=self.set_level)
                self._mic.start()
            except Exception as exc:
                print(f"[orb] Mic-reactive mode failed to start: {exc}")

        if auto_demo:
            threading.Thread(target=self._run_demo, daemon=True).start()

        self._schedule_ui_queue()
        self._schedule_frame()
        self._schedule_position_recheck()
        self._root.mainloop()

    def _resolve_anchor(self, screen_w: int, screen_h: int) -> MascotAnchor:
        """Read live taskbar+tray rects from Win32 and compute mascot anchor."""
        taskbar = get_taskbar_info()
        tray_rect = get_tray_notify_rect()
        return compute_mascot_position(
            screen_w,
            screen_h,
            mascot_size=self._win_w,
            taskbar=taskbar,
            tray_rect=tray_rect,
            tray_safe_margin_px=TRAY_SAFE_MARGIN_PX,
            right_edge_margin_px=RIGHT_EDGE_MARGIN_PX,
            overlap_px=TASKBAR_OVERLAP_PX,
            autohide_bottom_margin_px=AUTOHIDE_BOTTOM_MARGIN_PX,
        )

    def _schedule_position_recheck(self) -> None:
        """Re-resolve the mascot anchor periodically.

        Two paths:
        - manual_pinned=False: existing taskbar-anchor recompute (DPI /
          monitor / taskbar resize tracking).
        - manual_pinned=True: clamp-only — keep orb on the visible work
          area if monitors changed; otherwise leave it alone.
        """
        if not self._running or not self._root:
            return
        try:
            screen_w = self._root.winfo_screenwidth()
            screen_h = self._root.winfo_screenheight()
            if self._manual_pinned:
                screens = screens_from_tk(self._root)
                monitor_geo, monitor_name = self._monitor_at_orb_center(screens)
                clamped_x, clamped_y = clamp_to_work_area(
                    self._mascot_x, self._mascot_y, monitor_geo, mascot_size_px=self._win_w
                )
                if (clamped_x, clamped_y) != (self._mascot_x, self._mascot_y):
                    self._mascot_x = clamped_x
                    self._mascot_y = clamped_y
                    self._root.geometry(f"{self._win_w}x{self._win_h}+{clamped_x}+{clamped_y}")
                    if self._comment_bubble is not None:
                        self._comment_bubble.update_anchor(clamped_x, clamped_y, screen_w)
                    if self._controls is not None:
                        self._controls.update_anchor(clamped_x, clamped_y)
                    try:
                        save_position_to_toml(
                            JARVIS_TOML_PATH,
                            MascotPosition(
                                monitor=monitor_name,
                                x_relative=clamped_x - monitor_geo[0],
                                y_relative=clamped_y - monitor_geo[1],
                            ),
                        )
                    except OSError:
                        pass
            else:
                anchor = self._resolve_anchor(screen_w, screen_h)
                if (anchor.x, anchor.y) != (self._mascot_x, self._mascot_y):
                    self._mascot_x = anchor.x
                    self._mascot_y = anchor.y
                    self._root.geometry(f"{self._win_w}x{self._win_h}+{anchor.x}+{anchor.y}")
                    if self._comment_bubble is not None:
                        self._comment_bubble.update_anchor(anchor.x, anchor.y, screen_w)
                    if self._controls is not None:
                        self._controls.update_anchor(anchor.x, anchor.y)
        except (tk.TclError, OSError):
            pass
        if self._root is not None:
            self._root.after(POSITION_RECHECK_MS, self._schedule_position_recheck)

    # ------------------------------------------------------------------
    # Drag-and-pin handlers (Spec: docs/superpowers/specs/2026-05-17-orb-drag-design.md)
    # ------------------------------------------------------------------

    def _on_drag_press(self, event: tk.Event) -> None:
        if self._root is None:
            return
        self._drag_state = _DragState(
            start_root_x=event.x_root,
            start_root_y=event.y_root,
            offset_x=event.x_root - self._mascot_x,
            offset_y=event.y_root - self._mascot_y,
            moved=False,
        )
        try:
            self._root.configure(cursor="fleur")
        except tk.TclError:
            pass

    def _on_drag_motion(self, event: tk.Event) -> None:
        if self._drag_state is None or self._root is None:
            return
        dx = event.x_root - self._drag_state.start_root_x
        dy = event.y_root - self._drag_state.start_root_y
        if not self._drag_state.moved and (abs(dx) + abs(dy)) < DRAG_THRESHOLD_PX:
            return
        self._drag_state.moved = True
        new_x = event.x_root - self._drag_state.offset_x
        new_y = event.y_root - self._drag_state.offset_y
        self._mascot_x = new_x
        self._mascot_y = new_y
        try:
            self._root.geometry(f"{self._win_w}x{self._win_h}+{new_x}+{new_y}")
        except tk.TclError:
            return
        if self._comment_bubble is not None:
            screen_w = self._root.winfo_screenwidth()
            self._comment_bubble.update_anchor(new_x, new_y, screen_w)
        if self._controls is not None:
            self._controls.update_anchor(new_x, new_y)

    def _on_drag_release(self, _event: tk.Event) -> None:
        if self._root is not None:
            try:
                self._root.configure(cursor="")
            except tk.TclError:
                pass
        state = self._drag_state
        self._drag_state = None
        if state is None or not state.moved:
            return  # click, not drag

        screens = screens_from_tk(self._root)
        monitor_geo, monitor_name = self._monitor_at_orb_center(screens)
        clamped_x, clamped_y = clamp_to_work_area(
            self._mascot_x, self._mascot_y, monitor_geo, mascot_size_px=self._win_w
        )
        if (clamped_x, clamped_y) != (self._mascot_x, self._mascot_y):
            self._mascot_x = clamped_x
            self._mascot_y = clamped_y
            try:
                self._root.geometry(f"{self._win_w}x{self._win_h}+{clamped_x}+{clamped_y}")
            except tk.TclError:
                pass
        if self._controls is not None:
            self._controls.update_anchor(self._mascot_x, self._mascot_y)

        self._manual_pinned = True
        try:
            save_position_to_toml(
                JARVIS_TOML_PATH,
                MascotPosition(
                    monitor=monitor_name,
                    x_relative=self._mascot_x - monitor_geo[0],
                    y_relative=self._mascot_y - monitor_geo[1],
                ),
            )
        except OSError as exc:
            # Persistence failure is non-fatal — orb stays at new position
            # for this session; next restart falls back to default.
            print(f"[orb] save_position_to_toml failed: {exc}")

    def set_on_mute_toggle(self, callback: Callable[[], None] | None) -> None:
        """Inject the Jarvis-wide mute toggle action.

        Fired when the user double-clicks the orb. The callback is
        responsible for the actual mute logic — this layer just
        translates the gesture. Pass ``None`` to detach.

        Architectural note: keeping this as an opaque callback preserves
        the rule from ``ui/orb/bus_bridge.py`` that the orb knows nothing
        about Jarvis-Core, EventBus, or Supervisor states. The bridge in
        the upper layer is the one that publishes events.
        """
        self._mute_toggle_callback = callback

    # ------------------------------------------------------------------
    # Control row (voice-orb style only)
    # ------------------------------------------------------------------

    def _controls_wanted(self) -> bool:
        """Does this style show the control row at all?"""
        return self._style == "voice_orb"

    def _ensure_controls(self) -> None:
        """Create or tear down the row so it matches the current style."""
        if self._root is None:
            return
        if not self._controls_wanted():
            if self._controls is not None:
                self._controls.destroy()
                self._controls = None
            return
        if self._controls is not None:
            return
        try:
            row = OrbControlRow(
                parent=self._root,
                orb_x=self._mascot_x,
                orb_y=self._mascot_y,
                orb_w=self._win_w,
                orb_h=self._win_h,
            )
        except tk.TclError:
            logging.getLogger("jarvis.orb").debug("orb control row unavailable", exc_info=True)
            return
        row.set_on_action(self._on_control_action)
        row.set_state(
            active=self._mode in ("listen", "think", "speak"),
            speaker_muted=self._speaker_muted,
        )
        self._controls = row

    def _on_orb_pointer_enter(self, _event: tk.Event | None = None) -> None:
        self._pointer_over_orb = True
        row = self._controls
        if row is not None:
            row.show()

    def _on_orb_pointer_leave(self, _event: tk.Event | None = None) -> None:
        self._pointer_over_orb = False
        row = self._controls
        if row is None:
            return
        # A live conversation keeps the controls out: hanging up is the one
        # thing the user reaches for mid-call, and hunting for a row that hides
        # itself is worse than a row that stays.
        if self._mode in ("listen", "think", "speak"):
            return
        row.hide_after_grace()

    def _sync_controls_visibility(self) -> None:
        """Reconcile the row with mode + pointer after a state change."""
        row = self._controls
        if row is None:
            return
        active = self._mode in ("listen", "think", "speak")
        row.set_state(active=active)
        # Never on its own: the row is the orb's controls, so a mode change
        # arriving while the sphere is withdrawn must not float four buttons
        # over an empty desktop.
        if not self._orb_is_visible():
            row.hide()
            return
        if active or self._pointer_over_orb or row.pointer_inside:
            row.show()
        else:
            row.hide_after_grace()

    def _reveal_controls(self) -> None:
        """Post-deiconify pass: refresh the speaker disc, then place the row."""
        if self._controls is None:
            return
        self._refresh_speaker_state()
        self._sync_controls_visibility()

    def _orb_is_visible(self) -> bool:
        if self._root is None:
            return False
        try:
            return bool(self._root.winfo_viewable())
        except tk.TclError:
            return False

    def _on_control_action(self, action: str) -> None:
        """Run one control-row button. Always on the Tk thread."""
        if action == "attach":
            self._do_attach()
        elif action == "mic":
            self._do_talk_or_hangup()
        elif action == "close":
            self._do_close()
        elif action == "speaker":
            self._do_speaker_toggle()

    def _do_attach(self) -> None:
        """Pick files and hand them to the conversation (same path as a drop)."""
        log = logging.getLogger("jarvis.orb")
        count = orb_controls.pick_and_dispatch_files(
            self._root, on_error=lambda message: log.warning("orb attach: %s", message)
        )
        if count:
            log.info("orb attach: handed %d file(s) to the conversation", count)

    def _do_talk_or_hangup(self) -> None:
        """The mic disc: start a conversation, or end the running one."""
        if self._mode in ("listen", "think", "speak"):
            self._do_hangup()
            return
        callback = self._on_talk
        if callback is not None:
            callback()
            return
        pipeline = self._speech_pipeline()
        starter = getattr(pipeline, "request_voice_session", None)
        if callable(starter):
            starter()

    def _do_hangup(self) -> None:
        callback = self._on_hangup
        if callback is not None:
            callback()
        else:
            pipeline = self._speech_pipeline()
            hangup = getattr(pipeline, "request_hangup", None)
            if callable(hangup):
                hangup()
        # Local feedback now instead of waiting for microphone teardown plus
        # the EventBus round-trip; the authoritative state reconciles it.
        self._set_mode("idle")
        self._sync_controls_visibility()

    def _do_close(self) -> None:
        """X: end the conversation and put the orb away, like the in-app X."""
        if self._mode in ("listen", "think", "speak"):
            self._do_hangup()
        row = self._controls
        if row is not None:
            row.hide()
        self._pointer_over_orb = False
        self._show_until_t = 0.0
        self._actually_hide()

    def _do_speaker_toggle(self) -> None:
        """Silence / restore the assistant's voice for this session only."""
        callback = self._on_speaker_toggle
        if callback is not None:
            callback()
            # The host process cannot see the pipeline, so it cannot report the
            # resulting state either. Mirror optimistically — the next reveal
            # re-reads the truth via _refresh_speaker_state.
            self._speaker_muted = not self._speaker_muted
        else:
            result = orb_controls.toggle_speaker_mute()
            if result is None:
                logging.getLogger("jarvis.orb").info(
                    "orb speaker toggle ignored — no live speech pipeline"
                )
                return
            self._speaker_muted = result
        row = self._controls
        if row is not None:
            row.set_state(speaker_muted=self._speaker_muted)

    def _refresh_speaker_state(self) -> None:
        """Re-read the real TTS mute so the disc cannot drift out of sync."""
        if self._on_speaker_toggle is not None:
            return  # hosted: this process has no pipeline to ask
        muted = orb_controls.speaker_is_muted()
        if muted == self._speaker_muted:
            return
        self._speaker_muted = muted
        row = self._controls
        if row is not None:
            row.set_state(speaker_muted=muted)

    @staticmethod
    def _speech_pipeline():
        """The live pipeline, or ``None`` off the desktop process."""
        try:
            from jarvis.core.runtime_refs import get_speech_pipeline
        except Exception:  # noqa: BLE001 — importable in practice; never fatal
            return None
        return get_speech_pipeline()

    def set_on_talk(self, callback: Callable[[], None] | None) -> None:
        """Inject "start a voice session" (the mic disc while idle).

        Wired by ``jarvis.ui.jarvisbar.host`` when the orb runs in the macOS
        companion process, where no SpeechPipeline exists; the parent applies
        it. In-process the orb calls the pipeline itself.
        """
        self._on_talk = callback

    def set_on_hangup(self, callback: Callable[[], None] | None) -> None:
        """Inject "end the running session" (the mic disc while active, and X)."""
        self._on_hangup = callback

    def set_on_speaker_toggle(self, callback: Callable[[], None] | None) -> None:
        """Inject the assistant-voice mute toggle (the speaker disc)."""
        self._on_speaker_toggle = callback

    def set_on_show_window(self, callback: Callable[[], None] | None) -> None:
        """Inject the right-click → raise-main-window action.

        Fired on a right-click of the orb. Same bus-agnostic contract as
        ``set_on_mute_toggle``: OrbBusBridge passes a callable that publishes
        ``ShowWindowRequested``. Pass ``None`` to detach.
        """
        self._on_show_window = callback

    def _on_right_click(self, _event: tk.Event | None = None) -> None:
        """Right-click → raise the main desktop window via the injected
        callback. Replaces the old Reset/Mute context menu (spec 2026-06-02):
        "Reset position" now lives on middle-click, mute stays on the
        double-double-click gesture. No callback wired → safe no-op."""
        callback = self._on_show_window
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:  # noqa: BLE001
            # The Tk thread must not crash on a downstream bus hiccup.
            import logging

            logging.getLogger("jarvis.orb").warning("orb show-window callback raised: %r", exc)

    def _on_mute_double_click(self, _event: tk.Event) -> None:
        """First half of the mute gesture: increment a counter and arm a
        reset timer. Mute toggles only after a *second* ``<Double-Button-1>``
        within ``MUTE_GESTURE_WINDOW_MS`` ms (i.e. four clicks in quick
        succession). One stray double-click — e.g. when the user clicks
        the orb the moment it pops up — leaves the counter at 1 and
        expires harmlessly.

        Tk fires ``<Double-Button-1>`` *in addition* to the regular
        ``<Button-1>`` press/release pair. The press already entered
        ``_on_drag_press`` and primed the drag state; we abort it here so
        a cursor twitch between the second press and release does not
        commit a move.
        """
        self._drag_state = None
        self._mute_click_count += 1
        if self._mute_click_count >= 2:
            # Second double-click inside the window — fire the toggle.
            # Reset book-keeping first so a downstream callback exception
            # cannot strand the counter in the "armed" state.
            self._mute_click_count = 0
            self._cancel_mute_click_reset()
            self._fire_mute_toggle()
            return
        # First double-click. Cancel any previous timer (defensive) and
        # arm a fresh one that resets the counter if no second gesture
        # follows.
        self._cancel_mute_click_reset()
        if self._root is None:
            # Tests / headless harness: no timer available. Without the
            # reset timer the second click ought to still toggle, so
            # this branch is a graceful no-op for the timer-arm step.
            return
        try:
            self._mute_click_reset_id = self._root.after(
                MUTE_GESTURE_WINDOW_MS, self._reset_mute_click_count
            )
        except tk.TclError as exc:
            # Root went away mid-gesture — treat as graceful no-op so the
            # next legitimate gesture still works after the orb is rebuilt.
            print(f"[orb] mute gesture timer-arm failed: {exc}")

    def _reset_mute_click_count(self) -> None:
        """Tk-timer callback: clear the counter after ``MUTE_GESTURE_WINDOW_MS``
        with no second double-click. Called from the Tk main-thread, so no
        synchronisation needed.
        """
        self._mute_click_count = 0
        self._mute_click_reset_id = None

    def _cancel_mute_click_reset(self) -> None:
        """Cancel the pending Tk-after timer, if any. Safe to call when
        no timer is armed (e.g. before the first click of a gesture).
        """
        if self._mute_click_reset_id is None or self._root is None:
            self._mute_click_reset_id = None
            return
        try:
            self._root.after_cancel(self._mute_click_reset_id)
        except tk.TclError:
            # Timer already fired or root disposed — both are harmless.
            pass
        self._mute_click_reset_id = None

    def _fire_mute_toggle(self) -> None:
        """Invoke the registered mute-toggle callback exactly once.

        Used by the two-double-click gesture path *and* by the right-
        click context menu, which is the always-on recovery path: it
        must NOT require a four-click ritual to undo an accidental mute.
        """
        callback = self._mute_toggle_callback
        if callback is None:
            print("[orb] mute toggle gesture fired without registered callback")
            return
        try:
            callback()
        except Exception as exc:  # noqa: BLE001
            # Tk thread must not crash on a downstream bus hiccup — the
            # gesture is supposed to silence Jarvis, not blow up the orb.
            print(f"[orb] mute toggle callback raised: {exc!r}")

    def _on_reset_double_click(self, _event: tk.Event | None = None) -> None:
        """Reset the orb to its default anchor position.

        Historically bound to ``<Double-Button-1>`` (moved to mute on
        2026-05-17) and then reachable via the right-click menu. Spec
        2026-06-02 binds it to middle-click (``<Button-2>``) since right-click
        now raises the main window. Still reachable via the voice
        ``OrbResetRequested`` path and kept callable by the same name so the
        existing reset-coverage stays valid.
        """
        if self._root is None:
            return
        self._manual_pinned = False
        self._drag_state = None
        try:
            clear_position_in_toml(JARVIS_TOML_PATH)
        except OSError as exc:
            print(f"[orb] clear_position_in_toml failed: {exc}")
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        anchor = self._resolve_anchor(screen_w, screen_h)
        self._mascot_x = anchor.x
        self._mascot_y = anchor.y
        try:
            self._root.geometry(f"{self._win_w}x{self._win_h}+{anchor.x}+{anchor.y}")
        except tk.TclError:
            return
        if self._comment_bubble is not None:
            self._comment_bubble.update_anchor(anchor.x, anchor.y, screen_w)
        if self._controls is not None:
            self._controls.update_anchor(anchor.x, anchor.y)

    def _monitor_at_orb_center(self, screens: list) -> tuple[tuple[int, int, int, int], str]:
        """Return (geometry, device_name) of the monitor containing the orb center."""
        cx = self._mascot_x + self._win_w // 2
        cy = self._mascot_y + self._win_h // 2
        for s in screens:
            sx, sy, sw, sh = s.geometry
            if sx <= cx < sx + sw and sy <= cy < sy + sh:
                return s.geometry, s.name
        # Fallback: primary monitor.
        primary = next((s for s in screens if s.is_primary), None)
        if primary is not None:
            return primary.geometry, primary.name
        if screens:
            return screens[0].geometry, screens[0].name
        # Last resort: single-screen guess.
        if self._root is not None:
            sw = self._root.winfo_screenwidth()
            sh = self._root.winfo_screenheight()
            return (0, 0, sw, sh), ""
        return (0, 0, 1920, 1080), ""

    #: Min on-screen duration after a show() call. Even if hide() is invoked
    #: immediately afterwards, the mascot stays visible for at least this long.
    SHOW_MIN_DURATION_S: float = 2.5

    def show(self, mode: str = "listen") -> None:
        """Reveal the mascot in ``mode``. Thread-safe.

        The mode is validated SYNCHRONOUSLY, on the caller's thread, before the
        reveal is queued. That ordering is the contract, not a detail: the work
        itself is handed to the Tk thread, so a mode rejected inside ``_show``
        would be dropped later, on another thread, where no exception can ever
        reach the caller — the call would return "successfully" and the user
        would simply see nothing. Callers that keep a fallback look for a
        surface which cannot render a mode depend on hearing about the
        rejection here.

        Raises ``ValueError`` for a mode outside the shared vocabulary.
        """
        if mode not in MODES:
            raise ValueError(f"Unknown mode: {mode!r} (allowed: {', '.join(MODES)})")

        def _show() -> None:
            self._cancel_pending_hide()
            self._set_mode(mode)
            if self._root:
                self._root.deiconify()
                if self._mac_transparent:
                    # Tk 9 can (re)materialize the NSWindow on mapping after a
                    # withdraw; a construction-time clear-backing pass does not
                    # survive that, leaving the opaque grey box (BUG-075).
                    # Re-assert on every reveal — idempotent and cheap.
                    apply_macos_clear_backing()
                # ADR-0016 visible-feedback contract: schedule a post-frame
                # visibility snapshot. 50 ms gives Tk time to actually map
                # the window before we sample winfo_viewable / winfo_x.
                # We must NOT block the deiconify path — publish is fire-
                # and-forget; failures are swallowed by the publisher.
                self._root.after(50, self._publish_visibility_feedback, mode)
                # Same 50 ms reason: the row asks whether the orb is mapped,
                # and right after deiconify Tk still says it is not. The
                # speaker disc re-reads the real TTS mute here too, so a mute
                # set elsewhere (settings slider, voice command) is reflected
                # rather than remembered wrong.
                self._root.after(50, self._reveal_controls)
            now = time.perf_counter() - self._t0
            self._show_until_t = max(self._show_until_t, now + self.SHOW_MIN_DURATION_S)

        self._enqueue_ui(_show)

    def _finish_boot_flash(self, target_x: int, target_y: int) -> None:
        """ADR-0016 L1: end the boot-flash phase by migrating the orb to
        the persisted pin and hiding it again. Runs on the Tk thread.

        Defensive: if a wake-word arrived during the 800 ms flash and the
        orb is already in a visible state for LISTENING etc., the
        ``_show_until_t`` guard keeps it on-screen at the target position
        rather than withdrawing immediately. The user sees the orb slide
        from primary anchor to the secondary pin — that is the intended
        UX so they always know where the orb is.
        """
        if self._root is None:
            return
        self._mascot_x = target_x
        self._mascot_y = target_y
        try:
            self._root.geometry(f"{self._win_w}x{self._win_h}+{target_x}+{target_y}")
        except tk.TclError:
            return
        # Only withdraw if no active LISTENING/THINKING/SPEAKING state has
        # claimed the orb during the flash window. ``_show_until_t`` is
        # set by ``show()`` and stays in the future while the orb is
        # actively requested visible.
        now = time.perf_counter() - self._t0
        if now >= self._show_until_t:
            try:
                self._root.withdraw()
            except tk.TclError:
                pass
        self._boot_flash_target_xy = None

    def set_feedback_publisher(
        self, callback: Callable[[str, dict[str, Any]], None] | None
    ) -> None:
        """Inject the ADR-0016 visible-feedback publisher.

        Called from ``OrbBusBridge.attach`` so the bridge owns the bus
        reference and the orb stays bus-agnostic. Set to ``None`` to
        detach (useful in unit tests).
        """
        self._feedback_publisher = callback

    def _publish_visibility_feedback(self, mode: str) -> None:
        """Sample the live Tk geometry and forward to the registered
        publisher (if any). Runs on the Tk main thread via ``root.after``.

        Errors must NOT propagate — a bad bus or a stale root would
        otherwise crash the Tk event loop and freeze the orb.
        """
        publisher = self._feedback_publisher
        if publisher is None or self._root is None:
            return
        try:
            viewable = int(self._root.winfo_viewable())
            x = int(self._root.winfo_x())
            y = int(self._root.winfo_y())
            geometry = f"{self._win_w}x{self._win_h}+{x}+{y}"
        except tk.TclError:
            return
        observed = {
            "viewable": viewable,
            "geometry": geometry,
            "x": x,
            "y": y,
            "monitor": self._mascot_x and "" or "",  # placeholder, see below
        }
        # We deliberately leave ``monitor`` empty here — the bridge owns
        # monitor naming because it has access to ``screens_from_tk`` via
        # drag_persistence; injecting that lookup into the orb would
        # break the bus-agnostic boundary.
        observed.pop("monitor", None)
        try:
            publisher(mode, observed)
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger("jarvis.orb").debug(
                "feedback publisher raised; suppressed", exc_info=True
            )

    def hide(self) -> None:
        def _hide() -> None:
            self._cancel_pending_hide()
            if not self._root:
                return
            now = time.perf_counter() - self._t0
            remaining_s = self._show_until_t - now
            if remaining_s > 0.001:
                wait_ms = max(1, int(round(remaining_s * 1000)))
                self._pending_hide_after_id = self._root.after(wait_ms, self._actually_hide)
            else:
                self._actually_hide()

        self._enqueue_ui(_hide)

    def _actually_hide(self) -> None:
        self._pending_hide_after_id = None
        # The controls belong to the orb, not to the desktop: a row of buttons
        # left floating where the sphere used to be is a widget with nothing
        # behind it.
        row = self._controls
        if row is not None:
            row.hide()
        self._pointer_over_orb = False
        if self._root is not None:
            try:
                self._root.withdraw()
            except tk.TclError:
                pass

    def stop(self) -> None:
        """Tear the mascot overlay down at runtime (live display-style swap).

        Additive — not on the normal voice path. Clears ``_running`` so the
        after-loops (``_schedule_frame`` / ``_schedule_ui_queue`` /
        ``_schedule_position_recheck``) all stop rescheduling on their next
        tick (each guards on ``self._running`` first), then hides the comment
        bubble and destroys the root on the Tk thread. Fully guarded so a
        teardown hiccup never propagates.
        """
        self._running = False
        root = self._root
        if root is None:
            return

        def _teardown() -> None:
            try:
                bubble = self._comment_bubble
                if bubble is not None and hasattr(bubble, "hide"):
                    bubble.hide()
            except Exception:  # noqa: BLE001
                pass
            try:
                row = self._controls
                self._controls = None
                if row is not None:
                    row.destroy()
            except Exception:  # noqa: BLE001 — teardown must reach root.destroy
                logging.getLogger("jarvis.orb").debug(
                    "orb control row teardown failed", exc_info=True
                )
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass

        try:
            root.after(0, _teardown)
        except Exception:  # noqa: BLE001
            pass

    def _cancel_pending_hide(self) -> None:
        if self._pending_hide_after_id is not None and self._root is not None:
            try:
                self._root.after_cancel(self._pending_hide_after_id)
            except tk.TclError:
                pass
            self._pending_hide_after_id = None

    def _bubble_wanted(self) -> bool:
        """Does this style speak through a bubble at all?

        The mascot does — a character with a face says things in a bubble. The
        voice orb does NOT (maintainer, 2026-08-06): the sphere carries the
        whole state itself, and a panel of text floating over the desktop was
        the part that read as clutter. Everything the bubble used to say is now
        in how the orb moves — it swells on the voice, and its colours churn
        while it thinks.
        """
        return self._style != "voice_orb"

    def show_comment(self, text: str, duration_ms: int = 3500) -> None:
        """Pop a speech bubble above the orb. Thread-safe.

        Does NOT trigger the mouth animation — call start_mouth_animation()
        separately so the mouth only moves while audio is actually playing.
        """
        bubble = self._comment_bubble
        if bubble is None or not text or not self._bubble_wanted():
            return
        self._enqueue_ui(lambda: bubble.show(text, duration_ms))

    def show_listening_transcript(self, text: str = "", duration_ms: int = 30000) -> None:
        """Show the larger live transcript bubble used while the user speaks."""
        bubble = self._comment_bubble
        if bubble is None or not self._bubble_wanted():
            return
        self._enqueue_ui(lambda: bubble.show(text, duration_ms, variant="transcript"))

    def hide_comment(self) -> None:
        """Hide the speech bubble immediately. Thread-safe."""
        bubble = self._comment_bubble
        if bubble is None:
            return
        self._enqueue_ui(bubble.hide)

    def start_mouth_animation(self, duration_ms: int = 60000) -> None:
        """Run the mascot's open/close mouth animation for `duration_ms`."""

        def _start() -> None:
            renderer = self._renderer
            if renderer is not None and hasattr(renderer, "start_mouth_anim"):
                t_now = time.perf_counter() - self._t0
                renderer.start_mouth_anim(duration_ms / 1000.0, t_now)

        self._enqueue_ui(_start)

    def stop_mouth_animation(self) -> None:
        """Immediately stop the mouth animation. Thread-safe."""

        def _stop() -> None:
            renderer = self._renderer
            if renderer is not None and hasattr(renderer, "stop_mouth_anim"):
                renderer.stop_mouth_anim()

        self._enqueue_ui(_stop)

    def set_mode(self, mode: str) -> None:
        """Repaint the mascot in ``mode`` without touching visibility.

        Validated synchronously for the same reason as :meth:`show` — see there.
        """
        if mode not in MODES:
            raise ValueError(f"Unknown mode: {mode!r} (allowed: {', '.join(MODES)})")
        self._enqueue_ui(lambda: self._set_mode(mode))

    def set_level(self, level: float) -> None:
        self._ext_level = max(0.0, min(1.0, float(level)))

    # --- Animation API ------------------------------------------------

    def play_animation(self, name: str, **params) -> None:
        """Starts a named animation (e.g. 'wave', 'salute', 'think').

        Thread-safe: queued via ``root.after(0, ...)`` onto the Tk mainloop.
        Only works with the MascotRenderer.

        Stacking behavior: several animations can run at the same time.
        Calling play_animation('wave') again while a 'wave' is still active
        adds a second instance — this is intentional (multiple waves).
        To "replace" it, call stop_animation('wave') first.
        """
        if not isinstance(self._renderer, MascotRenderer):
            return
        if name not in ANIMATION_REGISTRY:
            import logging

            logging.getLogger("jarvis.orb").warning(
                "play_animation(%r) unknown — available: %s",
                name,
                sorted(ANIMATION_REGISTRY),
            )
            return
        renderer = self._renderer

        def _start() -> None:
            t_now = time.perf_counter() - self._t0
            try:
                anim = make_animation(name, t_start=t_now, **params)
                renderer.add_animation(anim)
            except Exception:
                import logging

                logging.getLogger("jarvis.orb").exception(
                    "play_animation(%r) failed",
                    name,
                )

        self._enqueue_ui(_start)

    def stop_animation(self, name: str) -> None:
        """Stops all running instances of an animation (e.g. 'think').

        No-op if no instance exists or the renderer isn't the mascot.
        Important for endlessly-looping animations like 'think', 'sleep'.
        """
        if not isinstance(self._renderer, MascotRenderer):
            return
        renderer = self._renderer
        self._enqueue_ui(lambda: renderer.stop_animation(name))

    def clear_animations(self) -> None:
        """Ends all active animations immediately."""
        if not isinstance(self._renderer, MascotRenderer):
            return
        renderer = self._renderer
        self._enqueue_ui(renderer.clear_animations)

    def active_animations(self) -> list[str]:
        """Snapshot of the currently running animation names.

        NOT thread-safe (read without a lock); intended for tests/debugging only.
        """
        if not isinstance(self._renderer, MascotRenderer):
            return []
        return self._renderer.active_animation_names()

    def set_style(self, style: str) -> None:
        """Switches the renderer at runtime (see :data:`ORB_STYLES`).

        Thread-safe — queued via root.after. If ``"mascot"`` is requested but
        the PNG is not found, the current renderer stays unchanged
        (the caller learns about it via the logger warning entry).
        """
        style = _coerce_style(style)
        if self._root is None:
            # Not started yet → remember the style, start() picks it up
            self._style = style
            return
        self._enqueue_ui(lambda: self._apply_style(style))

    def _apply_style(self, style: str) -> None:
        # Size first: _build_renderer paints at the window's edge length, so a
        # renderer built before the resize would be drawn at the old scale.
        self._win_w, self._win_h = window_size_for_style(style)
        new_renderer = self._build_renderer(style)
        if new_renderer is None:
            # Undo the resize — the old renderer is still the one painting.
            self._win_w, self._win_h = window_size_for_style(self._style)
            return  # The fallback case was already logged in _build_renderer
        self._renderer = new_renderer
        self._style = style
        self._resize_window()
        # The bubble's look and the control row both belong to the style, so a
        # live swap has to carry them across or the new sphere inherits the
        # ghost's yellow bubble (and vice versa).
        if self._comment_bubble is not None:
            self._comment_bubble.set_theme(bubble_theme_for_style(style))
        self._ensure_controls()
        self._sync_controls_visibility()

    def _resize_window(self) -> None:
        """Re-fit root, canvas and the attached surfaces after a style swap."""
        if self._root is None:
            return
        try:
            self._root.geometry(f"{self._win_w}x{self._win_h}+{self._mascot_x}+{self._mascot_y}")
            if self._canvas is not None:
                self._canvas.configure(width=self._win_w, height=self._win_h)
        except tk.TclError:
            logging.getLogger("jarvis.orb").debug("orb resize failed", exc_info=True)
            return
        if self._comment_bubble is not None:
            try:
                screen_w = self._root.winfo_screenwidth()
            except tk.TclError:
                screen_w = self._win_w
            self._comment_bubble.update_anchor(self._mascot_x, self._mascot_y, screen_w)
            self._comment_bubble.set_orb_width(self._win_w)
        if self._controls is not None:
            self._controls.set_orb_size(self._win_w, self._win_h)
            self._controls.update_anchor(self._mascot_x, self._mascot_y)

    def _build_renderer(self, style: str) -> MascotRenderer | VoiceOrbRenderer | None:
        if style == "voice_orb":
            try:
                return VoiceOrbRenderer(
                    size=self._win_w,
                    color_key=(int(COLOR_KEY_RGB[0]), int(COLOR_KEY_RGB[1]), int(COLOR_KEY_RGB[2])),
                )
            except Exception as exc:  # noqa: BLE001
                import logging

                logging.getLogger("jarvis.orb").warning(
                    "VoiceOrbRenderer init failed (%s); overlay will stay hidden.",
                    exc,
                )
                return None
        if style == "mascot":
            mascot_path = _resolve_mascot_path(self._mascot_path_hint)
            if mascot_path is None:
                import logging

                logging.getLogger("jarvis.orb").warning(
                    "Mascot style requested but PNG not found "
                    "(looked in: %s, ENV JARVIS_ORB_MASCOT_PATH, %s) — "
                    "overlay will stay hidden.",
                    self._mascot_path_hint,
                    DEFAULT_MASCOT_REL,
                )
                return None
            try:
                return MascotRenderer(mascot_path)
            except Exception as exc:  # noqa: BLE001
                import logging

                logging.getLogger("jarvis.orb").warning(
                    "MascotRenderer init failed (%s); overlay will stay hidden.",
                    exc,
                )
                return None
        return None

    # --- Internal --------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        """Apply ``mode`` on the Tk thread. The public entry points already
        validated it; this stays a guard for the internal call sites."""
        if mode not in MODES:
            raise ValueError(f"Unknown mode: {mode!r} (allowed: {', '.join(MODES)})")
        changed = mode != self._mode
        self._mode = mode
        if changed:
            self._sync_controls_visibility()

    def _enqueue_ui(self, fn) -> None:
        """Queue UI work for the Tk thread.

        Direct ``root.after`` calls from the backend asyncio thread are
        unreliable on Windows and can raise ``RuntimeError: main thread is not
        in main loop``. The Tk event loop drains this queue itself.
        """
        if self._root is None:
            return
        if self._tk_thread_id == threading.get_ident():
            fn()
            return
        self._ui_queue.put(fn)

    def _schedule_ui_queue(self) -> None:
        if not self._running or self._root is None:
            return
        while True:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                import logging

                logging.getLogger("jarvis.orb").exception("Orb UI command failed")
        self._root.after(20, self._schedule_ui_queue)

    # --- Thread lifecycle for integration into async/asyncio worlds -----

    def start_in_thread(self, auto_demo: bool = False, timeout: float = 3.0) -> None:
        """Starts the Tk mainloop on a daemon thread and returns.

        On Windows, Tk is stable on a background thread as long as all
        UI mutations are queued via `root.after(0, fn)` — our
        show/hide/set_mode/set_level methods already do this.

        On macOS this is a logged no-op: Aqua-Tk (like AppKit) is
        main-thread-only, and a Tk root on a worker thread aborts the whole
        process natively (BUG-057, same class as the BUG-056 tray).

        Blocks until the window actually exists (self._started event),
        so the caller can safely call show()/hide() afterward without
        running into a None root.
        """
        import logging

        _log = logging.getLogger("jarvis.orb")

        if sys.platform == "darwin":
            _log.info(
                "Orb overlay not started: macOS allows Tk windows on the "
                "main thread only — running without the on-screen orb."
            )
            return

        def _run():
            try:
                self.start(auto_demo=auto_demo)
            except Exception as exc:
                # pythonw.exe has no stdout → print goes nowhere. The logger
                # writes to the watchdog log and makes silent deaths visible.
                _log.exception("Orb thread start failed: %s", exc)

        t = threading.Thread(target=_run, name="orb-tk-mainloop", daemon=True)
        t.start()
        ok = self._started.wait(timeout=timeout)
        if not ok:
            _log.error(
                "Orb window not initialized within %.1fs — UI will not pop up.",
                timeout,
            )
        else:
            _log.info("Orb overlay Tk mainloop is running (window initialized).")

    def _schedule_frame(self) -> None:
        if not self._running or not self._root or not self._canvas or not self._renderer:
            return
        t = time.perf_counter() - self._t0
        img = self._renderer.render(t, self._mode, self._ext_level)
        if self._mac_transparent:
            # macOS has no color key — the frame carries a real alpha
            # channel instead (magenta → fully transparent).
            img = key_to_alpha(img)

        # The PhotoImage must be kept as self._photo — otherwise Tkinter
        # garbage-collects the image before it gets rendered.  It must also be
        # created in this root's Tcl interpreter: the macOS companion process
        # keeps a separate bootstrap Tk root alive to initialize NSApp, so an
        # unqualified PhotoImage lands in that other interpreter and the mascot
        # canvas reports ``image \"pyimageN\" does not exist``.
        self._photo = ImageTk.PhotoImage(img, master=self._root)
        if self._image_id is None:
            self._image_id = self._canvas.create_image(0, 0, anchor="nw", image=self._photo)
        else:
            self._canvas.itemconfig(self._image_id, image=self._photo)

        self._root.after(16, self._schedule_frame)  # ~60 FPS

    def _run_demo(self) -> None:
        self._started.wait(timeout=5.0)
        time.sleep(0.5)

        self.show(mode="listen")
        time.sleep(8.0)

        self.set_mode("speak")
        time.sleep(5.0)

        self.hide()
        time.sleep(1.5)

        self.show(mode="listen")
        time.sleep(4.0)
        self.hide()


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis-Orb Overlay")
    parser.add_argument(
        "--sticky",
        action="store_true",
        help="Window permanently visible (design preview)",
    )
    parser.add_argument(
        "--mic",
        action="store_true",
        help="Mic-reactive: pulses with microphone volume + shockwave rings",
    )
    parser.add_argument(
        "--mascot",
        action="store_true",
        help="Uses the SWG/Gigi mascot.",
    )
    parser.add_argument(
        "--voice-orb",
        action="store_true",
        help="Uses the procedural voice orb (the desktop twin of the in-app orb).",
    )
    parser.add_argument(
        "--mascot-path",
        type=str,
        default=None,
        help=(
            "Optional: explicit path to a mascot PNG (otherwise assets/icons/jarvis-gigi-256.png)."
        ),
    )
    parser.add_argument(
        "--animation",
        type=str,
        default=None,
        help=(
            "Visual QA: plays a specific animation in an endless loop "
            "(implies --mascot --sticky). Available: " + ", ".join(sorted(ANIMATION_REGISTRY))
        ),
    )
    parser.add_argument(
        "--all-animations",
        action="store_true",
        help="Plays all animations one after another (implies --mascot --sticky).",
    )
    args = parser.parse_args()

    if args.animation and args.animation not in ANIMATION_REGISTRY:
        print(
            f"[orb] Unknown animation {args.animation!r}. "
            f"Available: {', '.join(sorted(ANIMATION_REGISTRY))}"
        )
        return 2

    use_mascot = args.mascot or bool(args.animation) or args.all_animations
    sticky = args.sticky or args.mic or bool(args.animation) or args.all_animations
    style = "voice_orb" if args.voice_orb and not use_mascot else "mascot"

    overlay = OrbOverlay(
        sticky=sticky,
        mic_reactive=args.mic,
        style=style,
        mascot_path=args.mascot_path,
    )

    # Demo threads depending on the CLI argument
    auto_demo = False
    if args.animation:
        # Re-loops the requested animation every 2.5s — for visual QA
        def _loop_anim() -> None:
            overlay._started.wait(timeout=5.0)
            overlay.show(mode="listen")
            while True:
                overlay.play_animation(args.animation)
                # Sleep as long as the animation takes + a 0.6s pause; looping
                # animations (duration=0) we re-loop every 4s (re-trigger).
                cls = ANIMATION_REGISTRY[args.animation]
                wait = cls.duration if cls.duration > 0 else 4.0
                time.sleep(wait + 0.6)
                if cls.duration <= 0:
                    overlay.stop_animation(args.animation)

        threading.Thread(target=_loop_anim, daemon=True).start()
    elif args.all_animations:

        def _all_demo() -> None:
            overlay._started.wait(timeout=5.0)
            overlay.show(mode="listen")
            time.sleep(0.5)
            for name, cls in ANIMATION_REGISTRY.items():
                print(f"[orb] >>> {name}")
                overlay.play_animation(name)
                wait = cls.duration if cls.duration > 0 else 3.0
                time.sleep(wait + 0.5)
                if cls.duration <= 0:
                    overlay.stop_animation(name)
                    time.sleep(0.3)

        threading.Thread(target=_all_demo, daemon=True).start()
    else:
        # Classic demo (shows the listen/speak switch) when nothing is specified.
        # No auto_demo with --mic (otherwise the demo script would hide the
        # orb exactly while the user is speaking into the mic).
        auto_demo = not (args.sticky or args.mic)

    overlay.start(auto_demo=auto_demo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
