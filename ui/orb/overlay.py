"""Jarvis-Orb als natives Desktop-Overlay (Tkinter + Pillow + numpy).

Historie der Ansaetze und warum wir bei Tkinter gelandet sind:
    - pywebview + WebView2 Canvas: WebView2 rendert ueber DirectComposition,
      SetWindowRgn/LWA_COLORKEY greifen nicht → rechteckiger Kasten blieb.
    - PySide6 + WA_TranslucentBackground: Qt6+DWM auf Windows 11 liefert
      haeufig nur einen opaken schwarzen Backing-Buffer + DropShadow-Rahmen
      statt echter Transparenz. Dutzende bekannter Qt-Bugs, kein robuster Fix.
    - Tkinter + wm_attributes('-transparentcolor'): nutzt die klassische
      Win32 SetLayeredWindowAttributes-API mit LWA_COLORKEY. Stabil seit
      Windows 2000, unabhaengig von DWM-Compositor-Quirks. Magenta (#FF00FF)
      wird pixel-perfect transparent — Windows routet diese Pixel direkt
      zum Desktop durch.

Rendering-Pipeline:
    numpy berechnet pro Frame einen 108x108 RGB-Puffer. Radiale Gradienten,
    additive Swirls und der helle Core werden als Vektor-Operationen auf
    den Distance-Arrays ausgefuehrt (einmalig precomputed im Konstruktor).
    Harter Kreis-Rand, kein Alpha-Fade nach aussen — sonst entstuenden
    pinke Anti-Aliasing-Pixel an der Color-Key-Grenze. Pillow verpackt
    das Array in ein PhotoImage, das Tkinter in einen Canvas rendert.

Public-API:
    overlay = OrbOverlay(style="mascot")    # SWG/Gigi-PNG
    overlay.start()                         # blockt bis mainloop Exit
    overlay.show(mode="listen")
    overlay.show(mode="speak")
    overlay.hide()
    overlay.set_level(0.42)
    overlay.set_style("mascot")             # Runtime-Switch ohne Restart

ENV-Overrides:
    JARVIS_ORB_STYLE=mascot                 # legacy "orb" requests are ignored
    JARVIS_ORB_MASCOT_PATH=<pfad.png>       # alternativer Mascot-Pfad

Standalone-Test:
    python -m ui.orb.overlay                    # Demo-Sequenz (Mascot)
    python -m ui.orb.overlay --sticky           # dauerhaft sichtbar (Preview)
    python -m ui.orb.overlay --sticky --mascot  # SWG-Maskottchen statisch
    python -m ui.orb.overlay --mic --mascot     # SWG + Mic-reaktiv
"""

from __future__ import annotations

import argparse
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


@dataclass
class _DragState:
    """In-flight drag tracking. ``moved`` flips True once the threshold is crossed."""

    start_root_x: int
    start_root_y: int
    offset_x: int  # event.x_root - mascot_x at press time
    offset_y: int
    moved: bool = False

# 108x108 — rund 1/3 kleiner als die alte 160er-Groesse
WIN_W = 108
WIN_H = 108
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

# Magenta als Color-Key — Tkinter macht diese Farbe pixel-perfect transparent
COLOR_KEY_HEX = "#FF00FF"
COLOR_KEY_RGB = np.array([255, 0, 255], dtype=np.uint8)

_GWL_EXSTYLE = -20
_WS_EX_APPWINDOW = 0x00040000
_WS_EX_TOOLWINDOW = 0x00000080

TAU = math.tau

# Default-Pfad fuer das SWG/Gigi-Maskottchen. Wird von MascotRenderer gesucht,
# wenn kein expliziter Pfad uebergeben wird. Relativ zum Projekt-Root aufgeloest.
DEFAULT_MASCOT_REL = "assets/icons/jarvis-gigi-256.png"


def _transcript_visible_line_count(text_height: int, line_height: int) -> int:
    """Return how many transcript lines should shape the bubble height."""
    safe_line_height = max(1, line_height)
    lines = max(1, math.ceil(max(1, text_height) / safe_line_height))
    return min(TRANSCRIPT_MAX_VISIBLE_LINES, lines)


def _transcript_body_height(line_count: int, line_height: int) -> int:
    visible_lines = max(1, min(TRANSCRIPT_MAX_VISIBLE_LINES, line_count))
    return (BUBBLE_PADDING_Y * 2) + (max(1, line_height) * visible_lines)


def _resolve_mascot_path(path_str: str | None) -> Path | None:
    """Sucht das Maskottchen-PNG in gaengigen Ablagen.

    Reihenfolge: expliziter Pfad → ENV ``JARVIS_ORB_MASCOT_PATH`` → Projekt-Root
    (vom Modul hochlaufend). Returns None when no asset is available; callers
    keep the overlay invisible instead of falling back to the removed legacy orb.
    """
    candidates: list[Path] = []
    if path_str:
        candidates.append(Path(path_str))
    env_path = os.environ.get("JARVIS_ORB_MASCOT_PATH")
    if env_path:
        candidates.append(Path(env_path))
    # Projekt-Root finden: diese Datei liegt in <root>/ui/orb/overlay.py
    here = Path(__file__).resolve()
    for parent in [here.parent, here.parent.parent, here.parent.parent.parent]:
        candidates.append(parent / DEFAULT_MASCOT_REL)
    for c in candidates:
        if c.is_file():
            return c
    return None


def _apply_jarvis_icon_to_tk_root(root: tk.Tk) -> None:
    """Set the Jarvis taskbar/titlebar icon on this Tk root.

    Tkinter on Windows registers a window class without a class icon slot, so
    Windows falls back to the process icon (``pythonw.exe`` → Python logo).
    We override on three levels:
      1. ``SetCurrentProcessExplicitAppUserModelID`` — gives this process a
         stable taskbar grouping identity (idempotent across processes).
      2. ``Tk.iconbitmap(default=...)`` — Tk's own way to set an icon for all
         windows in this interpreter.
      3. ``WM_SETICON`` + ``SetClassLongPtrW`` — the only reliable override
         for the taskbar icon, because the taskbar reads the *class* icon.

    Each step is best-effort; any failure is silent (the orb is cosmetic).
    """
    if sys.platform != "win32":
        return
    try:
        from jarvis.ui.icon_utils import (
            ensure_windows_app_identity,
            project_icon_path,
            set_window_icon_by_hwnd,
        )
    except Exception:  # noqa: BLE001
        return

    ensure_windows_app_identity()

    ico_path = project_icon_path()
    if not ico_path.is_file():
        return

    # Step 2: Tk-level. ``default=`` applies to all current and future
    # windows of this interpreter (including hidden toplevels Tk creates
    # internally), preventing the brief Python-logo flash on first show.
    try:
        root.iconbitmap(default=str(ico_path))
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("jarvis.orb").debug(
            "Tk iconbitmap setup failed; continuing without it.",
            exc_info=True,
        )

    # Step 3: Win32-level. winfo_id() returns the HWND of the Tk root.
    try:
        hwnd = int(root.winfo_id())
    except Exception:  # noqa: BLE001
        return
    try:
        set_window_icon_by_hwnd(hwnd, ico_path)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("jarvis.orb").debug(
            "Win32 Tk icon setup failed; continuing without it.",
            exc_info=True,
        )


def _hide_tk_window_from_task_switcher(root: tk.Tk) -> None:
    """Markiert das Orb-Fenster als Toolwindow, damit es nicht als App zaehlt."""
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
        # Reine Desktop-Kosmetik. Wenn Win32 nicht greift, bleibt der Orb
        # funktional und wird nur evtl. als zusaetzliches Fenster angezeigt.
        return


class MascotRenderer:
    """Renderer for the SWG/Gigi mascot.

    Implements the render interface ``render(t, mode, ext_level) -> Image.Image``
    used by ``OrbOverlay``.

    Besonderheiten:
        - Alpha-Threshold (binär): Antialiasing-Kanten wuerden beim Composite
          auf Magenta rosa Fringes erzeugen. Fuer den pixeligen Gigi-Stil ist
          binäre Alpha ohnehin stimmig.
        - Weicher Glow: Ein gaussian-blur'ed Alpha-Mask-Derivat liefert einen
          warmen Halo um den Mascot, der mit ``energy`` pulsiert.
        - Breathing-Scale: Der Mascot atmet (±3%) und skaliert mit Energie auf
          bis ~107%. Skaliert wird mit NEAREST — das passt zum pixel-artigen
          Look und ist per-frame fast kostenlos.
    """

    def __init__(self, image_path: Path) -> None:
        raw = Image.open(image_path).convert("RGBA")
        # Einmal auf Zielgroesse; fuer per-frame Scale reicht NEAREST.
        base = raw.resize((WIN_W, WIN_H), Image.LANCZOS)
        self._base_rgba = np.asarray(base).copy()  # (H,W,4) uint8
        self._base_pil = base
        self._image_path = image_path

        # Weicher Glow-Mask aus gaussian-geblurrter Alpha. Werte 0..1.
        # Radius 6 gibt einen deutlich sichtbaren, aber tightt Halo.
        alpha_only = base.split()[3]
        blurred = alpha_only.filter(ImageFilter.GaussianBlur(radius=6))
        self._glow_mask = np.asarray(blurred).astype(np.float32) / 255.0
        # Alpha-Threshold fuer den Mascot selbst (binär).
        self._solid_mask = np.asarray(alpha_only) >= 128

        # Body-Part-Decomposition: extrahiere die echten Arm-Stummel als
        # eigene Sprites mit Pivot-Punkt. So koennen Animationen den
        # ECHTEN Arm rotieren statt einen zweiten daneben zu zeichnen.
        # _arm_left_sprite, _arm_right_sprite: kleine RGBA-Bilder mit Arm + transparent.
        # _arm_left_pivot, _arm_right_pivot: (x, y) im Frame-Koord-System,
        #     Punkt um den die Rotation laeuft (Schulter zum Body).
        # _body_no_arms_pil: Mascot-PIL ohne die Arm-Pixel (Alpha=0 dort).
        # _arm_left_local_pivot: Pivot relativ zum Sprite-Rechteck (in Sprite-Pixeln).
        decomp = self._decompose_arms(base)
        self._arm_left_sprite: Image.Image = decomp["left_sprite"]
        self._arm_right_sprite: Image.Image = decomp["right_sprite"]
        self._arm_left_pivot: tuple[int, int] = decomp["left_pivot"]
        self._arm_right_pivot: tuple[int, int] = decomp["right_pivot"]
        self._arm_left_local_pivot: tuple[int, int] = decomp["left_local_pivot"]
        self._arm_right_local_pivot: tuple[int, int] = decomp["right_local_pivot"]
        self._body_no_arms_pil: Image.Image = decomp["body_no_arms"]

        self._level: float = 0.0

        # Aktive Animationen — werden pro Frame gefiltert (is_finished).
        # Liste statt Set, damit FIFO-Reihenfolge fuer Layering deterministisch
        # ist (frueh hinzugefuegte Animationen zeichnen unter spaeteren).
        self._animations: list[Animation] = []

        # Mouth-anim deadline (in render-time `t` seconds). Mouth runs only
        # while `t < self._mouth_anim_until_t`. Outside that window the
        # original PNG mouth shows through unchanged.
        self._mouth_anim_until_t: float = -1.0

    # Hard-coded Arm-Bounding-Boxes (gemessen am 108x108-Render des
    # jarvis-gigi-256.png 2026-04-25). Heuristic-Detection war unzuverlaessig
    # weil die Body-Outline auch gelbe Pixel enthaelt (Cluster ueberlappen).
    # Diese BBoxes erfassen NUR den echten Stummel — saubere Trennung.
    # Wenn das Asset wechselt: nochmal manuell ausmessen + hier eintragen.
    # Sprite-BBox: gross genug um den Stummel + alle Outline-Spitzen zu erfassen.
    # Body-Erase-BBox: enger, exakt am Stummel — sonst entstehen "Loecher" im
    # Body bei Default-Pose (rot=0), weil das Sprite den geleerten Bereich
    # nicht voll fuellt.
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
        """Trennt die Arm-Stummel des Mascots vom Body als separate Sprites.

        Verwendet hard-coded Bounding-Boxes (ARM_LEFT_BBOX/ARM_RIGHT_BBOX)
        statt heuristischer Yellow-Cluster-Detection — letztere erfasste auch
        die Body-Outline-Pixel und produzierte unsaubere Sprites mit fremden
        Pixeln, die nach Rotation den Arm verzogen.

        Body-Erase: BBox + 2px Padding wird komplett auf alpha=0 gesetzt,
        damit der rotierte Arm freie Bahn hat (keine Outline-Reste in der
        Schulter-Region, die den hochgehobenen Arm verdecken).
        """
        arr = np.asarray(base)  # (H,W,4)

        def _extract_from_bbox(
            sprite_bbox: tuple[int, int, int, int],
            pivot_abs: tuple[int, int],
            erase_bbox: tuple[int, int, int, int],
        ) -> dict:
            x0, y0, x1, y1 = sprite_bbox
            # Sprite-Crop: bbox + Padding fuer Rotation-Headroom
            pad = 3
            sprite_x0 = max(0, x0 - pad)
            sprite_y0 = max(0, y0 - pad)
            sprite_x1 = min(arr.shape[1], x1 + 1 + pad)
            sprite_y1 = min(arr.shape[0], y1 + 1 + pad)
            crop = arr[sprite_y0:sprite_y1, sprite_x0:sprite_x1].copy()
            # Sprite-Maske: nur Pixel innerhalb des sprite_bbox behalten
            local_x0 = x0 - sprite_x0
            local_y0 = y0 - sprite_y0
            local_x1 = x1 + 1 - sprite_x0
            local_y1 = y1 + 1 - sprite_y0
            mask = np.zeros(crop.shape[:2], dtype=bool)
            mask[local_y0:local_y1, local_x0:local_x1] = True
            crop[~mask, 3] = 0
            sprite = Image.fromarray(crop, mode="RGBA")

            # Body-Erase: enger als sprite_bbox, exakt am Stummel.
            # So fuellt die Default-Pose (rot=0) das Erase-Loch komplett aus
            # und der Body sieht im Idle normal aus.
            ex0, ey0, ex1, ey1 = erase_bbox
            body_erase = np.zeros(arr.shape[:2], dtype=bool)
            body_erase[ey0 : ey1 + 1, ex0 : ex1 + 1] = True

            # Lokaler Pivot relativ zum Sprite-Crop
            pivot_local = (pivot_abs[0] - sprite_x0, pivot_abs[1] - sprite_y0)
            return {
                "sprite": sprite,
                "pivot": pivot_abs,
                "local_pivot": pivot_local,
                "body_erase_mask": body_erase,
            }

        left = _extract_from_bbox(
            cls.ARM_LEFT_BBOX, cls.ARM_LEFT_PIVOT, cls.ARM_LEFT_ERASE_BBOX,
        )
        right = _extract_from_bbox(
            cls.ARM_RIGHT_BBOX, cls.ARM_RIGHT_PIVOT, cls.ARM_RIGHT_ERASE_BBOX,
        )

        # Body ohne Arme: alle Pixel innerhalb der dilatierten Arm-Region
        # auf alpha=0 setzen — entfernt sowohl die gelben Stummel als auch
        # angrenzende Outline-Konturen, die sonst den hochgehobenen Arm
        # ueberdecken wuerden.
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
    # Animation-API (vom OrbOverlay aufgerufen, Tk-Main-Thread)
    # ------------------------------------------------------------------

    def add_animation(self, animation: Animation) -> None:
        """Fuegt eine laufende Animation hinzu. Threading: Caller stellt sicher
        dass das im Tk-Main-Thread passiert (OrbOverlay queued via root.after).
        """
        self._animations.append(animation)

    def stop_animation(self, name: str) -> int:
        """Entfernt alle laufenden Animationen mit dem gegebenen Namen.
        Gibt Anzahl gestoppter Instanzen zurueck."""
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
                [big_cx - inner_rx, big_cy - inner_ry,
                 big_cx + inner_rx, big_cy + inner_ry],
                fill=self.MOUTH_INNER_COLOR,
            )

        small = big.resize((sub_w, sub_h), Image.LANCZOS)
        frame[y0:y1, x0:x1] = np.asarray(small)

    def _aggregate_transform(self, t: float) -> Transform:
        """Faltet alle aktiven Animations-Transforms in eine kombinierte Transform."""
        result = identity_transform()
        for anim in self._animations:
            result = result.combine(anim.transform(t))
        return result

    def _aggregate_arm_transforms(self, t: float) -> tuple[ArmTransform, ArmTransform]:
        """Faltet linke und rechte Arm-Transforms aus allen aktiven Animationen."""
        left = identity_arm()
        right = identity_arm()
        for anim in self._animations:
            left = left.combine(anim.arm_left_transform(t))
            right = right.combine(anim.arm_right_transform(t))
        return left, right

    # ------------------------------------------------------------------
    # Render-Pipeline
    # ------------------------------------------------------------------

    def render(self, t: float, mode: str, ext_level: float | None) -> Image.Image:
        # Level smoothing mirrors the previous voice-reactive dynamics.
        if ext_level is not None:
            raw = max(0.0, min(1.0, ext_level))
        elif mode == "speak":
            raw = 0.35 + 0.25 * math.sin(t * 1.8) + 0.1 * math.sin(t * 3.3)
        elif mode == "think":
            raw = 0.2 + 0.12 * math.sin(t * 2.5)
        elif mode == "listen":
            raw = 0.25 + 0.18 * math.sin(t * 1.4) + 0.08 * math.sin(t * 2.7)
        else:
            raw = 0.0
        self._level += (raw - self._level) * 0.08
        breath = 0.5 + 0.5 * math.sin(t * 0.9)
        energy = max(self._level, breath * 0.1)

        # --- Animations-Lifecycle: finished-Animationen jetzt entfernen,
        # damit ihre Transforms und Overlays keinen "letzten Frame" mehr
        # einbringen. Wichtig vor dem aggregate_transform-Call.
        if self._animations:
            self._animations = [a for a in self._animations if not a.is_finished(t)]

        anim_transform = self._aggregate_transform(t)

        # Magenta-Hintergrund (Color-Key → transparent)
        frame = np.empty((WIN_H, WIN_W, 3), dtype=np.uint8)
        frame[:] = COLOR_KEY_RGB

        # --- 1. Warmer Halo um den Mascot (gaussian-blurrte Alpha als Maske)
        # Color-Key ist magenta — ein Weichblend mit Magenta erzeugt rosa Fringes.
        # Darum Halo HART: Pixel entweder sichtbar (volle Gold-Mischung) oder
        # magenta (transparent). Die Threshold-Grenze wird mit energy dynamisch
        # verschoben — bei hoher Energie wird mehr vom Blur-Gradient sichtbar
        # und der Halo wirkt "atmend/expandierend".
        halo_threshold = 0.55 - energy * 0.35  # 0.55 ruhig → 0.20 laut
        halo_mask = (self._glow_mask > halo_threshold) & ~self._solid_mask
        if halo_mask.any():
            # Innerhalb des Halos: Intensity steigt mit glow_mask-Wert
            # (dicht am Mascot = heller, Aussenkante = dunkler Gold).
            intensity = np.clip(
                (self._glow_mask[halo_mask] - halo_threshold) / (1.0 - halo_threshold),
                0.0, 1.0,
            )
            # Gold-Gradient: dunkel-warm (80,50,0) → hell-gold (255,210,80)
            frame[halo_mask, 0] = (80 + 175 * intensity).astype(np.uint8)
            frame[halo_mask, 1] = (50 + 160 * intensity).astype(np.uint8)
            frame[halo_mask, 2] = (0 + 80 * intensity).astype(np.uint8)

        # --- 2. PRE-Layer aus Animationen (z.B. Wind-Striche hinter dem Ghost)
        if self._animations:
            pre_layer = self._make_overlay_layer(t, which="pre")
            if pre_layer is not None:
                self._composite_layer(frame, pre_layer)

        # --- 3. Body (ohne Arme) + Arme separat mit Pivot-Rotation
        # Animations-Transform (scale, rotation, dx/dy, brightness) wird hier
        # ON TOP der Atem-Skalierung addiert/multipliziert.
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

        # Body ohne Arme rendern (Body-Pixel + Augen + Mund + zackiger Boden,
        # NUR die Arm-Stummel sind ge-erased). Damit ueberlappen rotierte
        # Arme nicht mit dem statischen Stub und es entstehen keine
        # "Doppelarm"-Artefakte.
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

        # Arm-Transforms aus den aktiven Animationen aggregieren
        arm_left_t, arm_right_t = self._aggregate_arm_transforms(t)

        # Linker Arm — Pivot ist der Body-naechste Punkt des Stummels.
        # Bei body-Skalierung skaliert auch der Pivot mit (Body-Scale-Aware).
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

        # --- 4. POST-Layer aus Animationen (Hand, Gedankenblase, Phone, Z-Z-Z…)
        if self._animations:
            post_layer = self._make_overlay_layer(t, which="post")
            if post_layer is not None:
                self._composite_layer(frame, post_layer)

        return Image.fromarray(frame, mode="RGB")

    # ------------------------------------------------------------------
    # Overlay-Helper
    # ------------------------------------------------------------------

    def _make_overlay_layer(self, t: float, which: str) -> Image.Image | None:
        """Bittet alle Animationen, ihre Overlays in einen RGBA-Layer zu malen.
        Gibt None zurueck wenn keine Animation den Layer beruehrt hat
        (Pixel-Check ueber Alpha-Kanal-Sum, billig).
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
        # Fast-Path: wenn die ganze Layer leer (alpha=0) → None statt composite.
        # Wir koennen das pruefen ohne extra-Walk: Pillow hat getbbox().
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
        """Skaliert und rotiert ein Sprite, plaziert es zentriert + offset im Frame.

        Genutzt fuer den Body (gesamtes Mascot ohne Arme). Der Sprite wird auf
        target_w x target_h skaliert, dann optional rotiert (around center),
        dann mit binaerer Alpha (>= 128) auf den Frame gesetzt.
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
        """Rendert ein Arm-Sprite mit Pivot-Rotation um die Schulter.

        Mathematik:
            1. Sprite hat einen lokalen Pivot (Schulter im Sprite-Koord-System).
            2. Welt-Pivot ist die Schulter im Frame (108x108-System).
            3. Body-Transform (Scale + Body-Rotation um Body-Center) verschiebt
               den Welt-Pivot — wir muessen die Schulter mitbewegen.
            4. Arm-Eigenrotation rotiert das Sprite UM seinen lokalen Pivot.
            5. Sprite wird so plaziert, dass der lokale Pivot auf dem
               (verschobenen) Welt-Pivot landet.

        PIL.rotate(center=...) rotiert ein Bild um einen frei waehlbaren Punkt
        — wir nutzen das fuer die Pivot-Rotation. expand=True vergroessert die
        Bounds, damit der rotierte Arm nicht clippt.
        """
        if arm_sprite.size == (1, 1):
            return  # Decomposition lieferte leeres Sprite (Asset-Mismatch)

        rot_deg_arm = -math.degrees(arm_t.rotation)
        if abs(rot_deg_arm) < 0.05:
            rotated = arm_sprite
            new_pivot = local_pivot
        else:
            # Standard-Trick: Sprite in eine groessere Leinwand padden, so dass
            # der Pivot exakt im Zentrum liegt. Dann mit center=Zentrum rotieren —
            # das verhaelt sich vorhersehbar (PIL.rotate ohne center, expand=False
            # rotiert pixel-stabil um die Bildmitte).
            sw, sh = arm_sprite.size
            px, py = local_pivot
            # Wie weit ist der Pivot vom rechten/unteren Rand entfernt?
            # Die neue Leinwand muss so gross sein, dass die maximale Distanz
            # vom Pivot zu jeder Sprite-Ecke in alle Richtungen Platz hat
            # (sonst clippt die Rotation Sprite-Pixel).
            max_dist = int(math.ceil(math.hypot(
                max(px, sw - px), max(py, sh - py)
            )))
            canvas_size = 2 * max_dist + 4
            canvas = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
            # Pivot des Sprites soll im Zentrum der Leinwand landen
            paste_x = canvas_size // 2 - px
            paste_y = canvas_size // 2 - py
            canvas.paste(arm_sprite, (paste_x, paste_y))
            # Rotation um Bildmitte — center-Default greift, expand=False
            rotated = canvas.rotate(rot_deg_arm, resample=Image.NEAREST, fillcolor=(0, 0, 0, 0))
            # Neue Pivot-Position ist die Bildmitte
            new_pivot = (canvas_size // 2, canvas_size // 2)

        # Welt-Pivot der Schulter, abgeleitet vom Body-Center + Mascot-relativer
        # Position. Der ungescalte Welt-Pivot ist `world_pivot` im 108er Frame.
        # Bei Skalierung muss er relativ zum Body-Center skaliert werden.
        cx_body = WIN_W / 2.0
        cy_body = WIN_H / 2.0
        # Body-relative Schulter-Position (vor Skalierung)
        rel_x = world_pivot[0] - cx_body
        rel_y = world_pivot[1] - cy_body
        # Skalieren
        rel_x *= body_scale_x
        rel_y *= body_scale_y
        # Body-Rotation: rotiere die Schulter mit dem Body
        if abs(body_rotation_rad) >= 1e-4:
            cr = math.cos(body_rotation_rad)
            sr = math.sin(body_rotation_rad)
            rel_x_r = rel_x * cr - rel_y * sr
            rel_y_r = rel_x * sr + rel_y * cr
            rel_x, rel_y = rel_x_r, rel_y_r
        # Welt-Pivot nach Body-Transform
        world_pivot_x = cx_body + rel_x + body_offset_dx + arm_t.dx
        world_pivot_y = cy_body + rel_y + body_offset_dy + arm_t.dy

        # Plaziere Sprite so, dass new_pivot auf world_pivot landet
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
        """Blittet ein RGBA-Sprite an Position (x0, y0) in den 108er-Frame.

        Binaere Alpha-Threshold (>= 128) damit Color-Key-Magenta nicht angefasst
        wird. Brightness multipliziert die RGB-Werte (clip auf 255).
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
        """In-place RGBA-over-RGB Composite, Color-Key-sicher (Magenta).

        Strategie:
            - Layer-Pixel mit alpha < 64 → ignorieren (nicht zeichnen).
            - Pixel mit alpha >= 64:
                * Wenn Frame-Pixel = Magenta (Color-Key, transparent) → HARTE
                  Setzung. Sonst entstuenden rosa Misch-Pixel die im Tk-Fenster
                  als Fringes durchscheinen.
                * Wenn Frame-Pixel = nicht-magenta (innerhalb Halo/Mascot) →
                  Soft-Blend ueber Alpha. Hier ist der Frame ohnehin opak,
                  Misch-Pixel bleiben sichtbar als sauberer Uebergang.
        """
        layer_arr = np.asarray(layer)  # (H,W,4)
        alpha = layer_arr[:, :, 3]
        draw_mask = alpha >= 64
        if not draw_mask.any():
            return
        src_rgb = layer_arr[:, :, :3].astype(np.float32)

        # Color-Key-Detection: Frame-Pixel die genau magenta sind → "transparent"
        is_magenta = (
            (frame[:, :, 0] == COLOR_KEY_RGB[0])
            & (frame[:, :, 1] == COLOR_KEY_RGB[1])
            & (frame[:, :, 2] == COLOR_KEY_RGB[2])
        )

        # 1) Hard-set ueber Magenta (jeder draw_mask-Pixel ueber Magenta wird
        #    1:1 mit der Layer-Farbe ueberschrieben — kein Blend).
        hard_mask = draw_mask & is_magenta
        if hard_mask.any():
            frame[hard_mask] = src_rgb[hard_mask].astype(np.uint8)

        # 2) Soft-Blend ueber bereits-belegten Pixeln (Halo, Mascot)
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

    def __init__(self, parent: tk.Tk, orb_x: int, orb_y: int, orb_w: int, screen_w: int) -> None:
        self._parent = parent
        self._orb_x = orb_x
        self._orb_y = orb_y
        self._orb_w = orb_w
        self._screen_w = screen_w
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

    def _build(self) -> None:
        top = tk.Toplevel(self._parent)
        top.overrideredirect(True)
        top.wm_attributes("-topmost", True)
        top.wm_attributes("-transparentcolor", COLOR_KEY_HEX)
        top.configure(bg=COLOR_KEY_HEX)
        _hide_tk_window_from_task_switcher(top)
        top.withdraw()

        canvas = tk.Canvas(top, bg=COLOR_KEY_HEX, highlightthickness=0, borderwidth=0)
        canvas.pack(fill="both", expand=True)

        self._top = top
        self._canvas = canvas
        self._font = tkfont.Font(
            family=BUBBLE_FONT_FAMILY, size=BUBBLE_FONT_SIZE, weight="bold"
        )
        self._transcript_font = tkfont.Font(
            family=BUBBLE_FONT_FAMILY, size=TRANSCRIPT_BUBBLE_FONT_SIZE, weight="bold"
        )

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

        # Probe text size by creating a throwaway text item and reading bbox.
        probe_id = self._canvas.create_text(
            0, 0, text=text, font=self._font, anchor="nw",
            width=BUBBLE_MAX_WIDTH, fill=BUBBLE_TEXT_HEX,
        )
        bbox = self._canvas.bbox(probe_id)
        self._canvas.delete(probe_id)
        if bbox is None:
            return
        text_w = max(BUBBLE_MIN_WIDTH, bbox[2] - bbox[0])
        text_h = bbox[3] - bbox[1]
        bubble_w = text_w + 2 * BUBBLE_PADDING_X
        body_h = text_h + 2 * BUBBLE_PADDING_Y
        total_h = body_h + BUBBLE_TAIL_H

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
            1, 1, bubble_w - 1, body_h - 1, BUBBLE_CORNER_RADIUS,
            fill=BUBBLE_BG_HEX, outline=BUBBLE_BORDER_HEX, width=BUBBLE_BORDER_WIDTH,
        )

        tail_base_y = body_h - 1
        tail_points = [
            tail_tip_x - tail_half, tail_base_y,
            tail_tip_x + tail_half, tail_base_y,
            tail_tip_x, tail_base_y + BUBBLE_TAIL_H,
        ]
        self._canvas.create_polygon(
            tail_points,
            fill=BUBBLE_BG_HEX, outline=BUBBLE_BORDER_HEX, width=BUBBLE_BORDER_WIDTH,
        )
        # Hide the seam between body bottom and tail base.
        self._canvas.create_line(
            tail_tip_x - tail_half + 1, tail_base_y,
            tail_tip_x + tail_half - 1, tail_base_y,
            fill=BUBBLE_BG_HEX, width=BUBBLE_BORDER_WIDTH + 1,
        )

        self._canvas.create_text(
            BUBBLE_PADDING_X, BUBBLE_PADDING_Y,
            text=text, font=self._font, anchor="nw",
            width=BUBBLE_MAX_WIDTH, fill=BUBBLE_TEXT_HEX,
        )

        self._top.deiconify()
        try:
            self._top.lift()
        except tk.TclError:
            pass

        self._show_until_t = time.perf_counter() + min(
            BUBBLE_MIN_SHOW_S, duration_ms / 1000.0
        )
        self._dismiss_after_id = self._top.after(duration_ms, self.hide)

    def _show_transcript(self, text: str, duration_ms: int) -> None:
        if (
            self._top is None
            or self._canvas is None
            or self._transcript_font is None
        ):
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
            fill=BUBBLE_TEXT_HEX,
        )
        bbox = self._canvas.bbox(probe_id)
        self._canvas.delete(probe_id)
        text_h = line_height if bbox is None else max(line_height, bbox[3] - bbox[1])
        visible_lines = _transcript_visible_line_count(text_h, line_height)
        body_h = _transcript_body_height(visible_lines, line_height)
        total_h = body_h + BUBBLE_TAIL_H

        mascot_center_x = self._orb_x + self._orb_w // 2
        bubble_x = mascot_center_x - bubble_w + 30
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
            1, 1, bubble_w - 1, body_h - 1, BUBBLE_CORNER_RADIUS,
            fill=BUBBLE_BG_HEX, outline=BUBBLE_BORDER_HEX, width=BUBBLE_BORDER_WIDTH,
        )

        tail_base_y = body_h - 1
        tail_points = [
            tail_tip_x - tail_half, tail_base_y,
            tail_tip_x + tail_half, tail_base_y,
            tail_tip_x, tail_base_y + BUBBLE_TAIL_H,
        ]
        self._canvas.create_polygon(
            tail_points,
            fill=BUBBLE_BG_HEX, outline=BUBBLE_BORDER_HEX, width=BUBBLE_BORDER_WIDTH,
        )
        self._canvas.create_line(
            tail_tip_x - tail_half + 1, tail_base_y,
            tail_tip_x + tail_half - 1, tail_base_y,
            fill=BUBBLE_BG_HEX, width=BUBBLE_BORDER_WIDTH + 1,
        )

        self._canvas.create_text(
            BUBBLE_PADDING_X,
            body_h - BUBBLE_PADDING_Y,
            text=display_text,
            font=self._transcript_font,
            anchor="sw",
            width=text_width,
            fill=BUBBLE_TEXT_HEX,
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
            x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1,
            x2, y1, x2, y1 + r, x2, y1 + r,
            x2, y2 - r, x2, y2 - r, x2, y2,
            x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y2 - r,
            x1, y1 + r, x1, y1 + r, x1, y1,
        ]
        return self._canvas.create_polygon(points, smooth=True, **kwargs)


class OrbOverlay:
    """Public Facade — Tkinter-basiert, Thread-safe via root.after(0, ...).

    tkinter selbst ist nicht thread-safe, aber `root.after(0, fn)` schedult
    fn sicher in den Tk-Main-Loop. Daher wickeln wir alle Aufrufe aus
    fremden Threads (Jarvis-Core, Demo-Thread) darueber ab.
    """

    def __init__(
        self,
        sticky: bool = False,
        mic_reactive: bool = False,
        style: str | None = None,
        mascot_path: str | Path | None = None,
    ) -> None:
        """
        style: only ``"mascot"`` is accepted. Legacy ``"orb"`` requests are
        coerced to ``"mascot"``.
        mascot_path: optionaler expliziter Pfad, sonst via ENV oder Default-Asset.
        ENV-Override: ``JARVIS_ORB_STYLE=mascot`` may request mascot explicitly;
        legacy ``orb`` values are ignored.
        """
        self._sticky = sticky
        self._mic_reactive = mic_reactive
        self._root: tk.Tk | None = None
        self._canvas: tk.Canvas | None = None
        self._comment_bubble: OrbCommentBubble | None = None
        self._renderer: MascotRenderer | None = None
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
        # Lazy-Import, damit der Orb auch ohne sounddevice startbar bleibt
        # (Mic-Reactive ist optional).
        self._mic = None

        # Style resolution: mascot-only. Keep accepting the old knobs so
        # existing launchers do not crash, but never render the legacy orb.
        env_style = (os.environ.get("JARVIS_ORB_STYLE") or "").strip().lower()
        requested_style = env_style or (style or "mascot").lower()
        if requested_style != "mascot":
            import logging

            logging.getLogger("jarvis.orb").warning(
                "Ignoring legacy orb style request %r; using mascot.",
                requested_style,
            )
        self._style = "mascot"
        self._mascot_path_hint = str(mascot_path) if mascot_path else None

    # --- Public API ----------------------------------------------------

    def start(self, auto_demo: bool = False) -> None:
        self._tk_thread_id = threading.get_ident()
        # DPI awareness MUST be set before Win32 GetWindowRect calls, else
        # taskbar coords come back DPI-virtualised and the mascot ends up
        # misplaced on 125%/150% scaled displays.
        _ensure_dpi_awareness()
        self._root = tk.Tk()
        self._root.title("JarvisOrb")
        self._root.overrideredirect(True)  # Frameless, kein DropShadow
        self._root.wm_attributes("-topmost", True)
        self._root.wm_attributes("-transparentcolor", COLOR_KEY_HEX)
        self._root.configure(bg=COLOR_KEY_HEX)
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
                mascot_size_px=WIN_W,
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
                resolved_screen = next(
                    (s for s in screens if s.name == placement.monitor), None
                )
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
                        f"{WIN_W}x{WIN_H}+{primary_anchor.x}+{primary_anchor.y}"
                    )
                else:
                    self._root.geometry(
                        f"{WIN_W}x{WIN_H}+{placement.abs_x}+{placement.abs_y}"
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
                self._root.geometry(f"{WIN_W}x{WIN_H}+{anchor.x}+{anchor.y}")
        else:
            anchor = self._resolve_anchor(screen_w, screen_h)
            self._mascot_x = anchor.x
            self._mascot_y = anchor.y
            self._root.geometry(f"{WIN_W}x{WIN_H}+{anchor.x}+{anchor.y}")

        self._canvas = tk.Canvas(
            self._root,
            width=WIN_W,
            height=WIN_H,
            bg=COLOR_KEY_HEX,
            highlightthickness=0,
            borderwidth=0,
        )
        self._canvas.pack(fill="both", expand=True)

        # Drag + interaction bindings. Tk dispatch:
        #   <Button-1> → drag-start (always fires, even on a double-click)
        #   <B1-Motion> → drag-update (only fires while LMB held)
        #   <ButtonRelease-1> → drag-finish (or no-op if it was a click)
        #   <Double-Button-1> → mute toggle (fires after Button-1+Release)
        #   <Button-3>       → raise the main desktop window (spec 2026-06-02)
        #   <Button-2>       → reset position (moved off the old right-click menu)
        # User spec 2026-05-17: double-click on the orb mutes Jarvis.
        # Spec 2026-06-02: right-click now opens the Jarvis window (same as the
        # whisper-bar). "Reset position" moved from the old right-click menu to
        # middle-click; mute stays on the double-double-click gesture. Drag-start
        # does not commit any geometry change until the threshold is crossed, so
        # a fast double-click stays harmless.
        self._canvas.bind("<ButtonPress-1>", self._on_drag_press)
        self._canvas.bind("<B1-Motion>", self._on_drag_motion)
        self._canvas.bind("<ButtonRelease-1>", self._on_drag_release)
        self._canvas.bind("<Double-Button-1>", self._on_mute_double_click)
        self._canvas.bind("<Button-3>", self._on_right_click)
        self._canvas.bind("<Button-2>", self._on_reset_double_click)

        self._comment_bubble = OrbCommentBubble(
            parent=self._root,
            orb_x=anchor.x,
            orb_y=anchor.y,
            orb_w=WIN_W,
            screen_w=screen_w,
        )

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

        # Mic-Listener erst nach Window-Creation starten, damit eventuelle
        # sounddevice-Fehler (kein Mic, PortAudio-Init) erst nach sichtbarem
        # Orb auftreten und nicht stumm beim Start verschluckt werden.
        if self._mic_reactive:
            try:
                from ui.orb.mic_listener import MicListener
                self._mic = MicListener(on_level=self.set_level)
                self._mic.start()
            except Exception as exc:
                print(f"[orb] Mic-Reactive konnte nicht starten: {exc}")

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
            screen_w, screen_h,
            mascot_size=WIN_W,
            taskbar=taskbar, tray_rect=tray_rect,
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
                    self._mascot_x, self._mascot_y, monitor_geo, mascot_size_px=WIN_W
                )
                if (clamped_x, clamped_y) != (self._mascot_x, self._mascot_y):
                    self._mascot_x = clamped_x
                    self._mascot_y = clamped_y
                    self._root.geometry(f"{WIN_W}x{WIN_H}+{clamped_x}+{clamped_y}")
                    if self._comment_bubble is not None:
                        self._comment_bubble.update_anchor(clamped_x, clamped_y, screen_w)
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
                    self._root.geometry(f"{WIN_W}x{WIN_H}+{anchor.x}+{anchor.y}")
                    if self._comment_bubble is not None:
                        self._comment_bubble.update_anchor(anchor.x, anchor.y, screen_w)
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
            self._root.geometry(f"{WIN_W}x{WIN_H}+{new_x}+{new_y}")
        except tk.TclError:
            return
        if self._comment_bubble is not None:
            screen_w = self._root.winfo_screenwidth()
            self._comment_bubble.update_anchor(new_x, new_y, screen_w)

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
            self._mascot_x, self._mascot_y, monitor_geo, mascot_size_px=WIN_W
        )
        if (clamped_x, clamped_y) != (self._mascot_x, self._mascot_y):
            self._mascot_x = clamped_x
            self._mascot_y = clamped_y
            try:
                self._root.geometry(f"{WIN_W}x{WIN_H}+{clamped_x}+{clamped_y}")
            except tk.TclError:
                pass

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

            logging.getLogger("jarvis.orb").warning(
                "orb show-window callback raised: %r", exc
            )

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
            self._root.geometry(f"{WIN_W}x{WIN_H}+{anchor.x}+{anchor.y}")
        except tk.TclError:
            return
        if self._comment_bubble is not None:
            self._comment_bubble.update_anchor(anchor.x, anchor.y, screen_w)

    def _monitor_at_orb_center(
        self, screens: list
    ) -> tuple[tuple[int, int, int, int], str]:
        """Return (geometry, device_name) of the monitor containing the orb center."""
        cx = self._mascot_x + WIN_W // 2
        cy = self._mascot_y + WIN_H // 2
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
        def _show() -> None:
            self._cancel_pending_hide()
            self._set_mode(mode)
            if self._root:
                self._root.deiconify()
                # ADR-0016 visible-feedback contract: schedule a post-frame
                # visibility snapshot. 50 ms gives Tk time to actually map
                # the window before we sample winfo_viewable / winfo_x.
                # We must NOT block the deiconify path — publish is fire-
                # and-forget; failures are swallowed by the publisher.
                self._root.after(50, self._publish_visibility_feedback, mode)
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
            self._root.geometry(f"{WIN_W}x{WIN_H}+{target_x}+{target_y}")
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
            geometry = f"{WIN_W}x{WIN_H}+{x}+{y}"
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
                self._pending_hide_after_id = self._root.after(
                    wait_ms, self._actually_hide
                )
            else:
                self._actually_hide()

        self._enqueue_ui(_hide)

    def _actually_hide(self) -> None:
        self._pending_hide_after_id = None
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

    def show_comment(self, text: str, duration_ms: int = 3500) -> None:
        """Pop a speech bubble above the orb. Thread-safe.

        Does NOT trigger the mouth animation — call start_mouth_animation()
        separately so the mouth only moves while audio is actually playing.
        """
        bubble = self._comment_bubble
        if bubble is None or not text:
            return
        self._enqueue_ui(lambda: bubble.show(text, duration_ms))

    def show_listening_transcript(
        self, text: str = "", duration_ms: int = 30000
    ) -> None:
        """Show the larger live transcript bubble used while the user speaks."""
        bubble = self._comment_bubble
        if bubble is None:
            return
        self._enqueue_ui(
            lambda: bubble.show(text, duration_ms, variant="transcript")
        )

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
        self._enqueue_ui(lambda: self._set_mode(mode))

    def set_level(self, level: float) -> None:
        self._ext_level = max(0.0, min(1.0, float(level)))

    # --- Animations-API ------------------------------------------------

    def play_animation(self, name: str, **params) -> None:
        """Startet eine benannte Animation (z.B. 'wave', 'salute', 'think').

        Thread-safe: queued via ``root.after(0, ...)`` in den Tk-Mainloop.
        Funktioniert nur am MascotRenderer.

        Stack-Verhalten: mehrere Animationen koennen gleichzeitig laufen.
        Eine erneute play_animation('wave') waehrend ein 'wave' noch lebt
        addiert eine zweite Instanz — das ist gewollt (mehrfaches Winken).
        Fuer "ersetzen" zuerst stop_animation('wave') aufrufen.
        """
        if not isinstance(self._renderer, MascotRenderer):
            return
        if name not in ANIMATION_REGISTRY:
            import logging
            logging.getLogger("jarvis.orb").warning(
                "play_animation(%r) unbekannt — verfuegbar: %s",
                name, sorted(ANIMATION_REGISTRY),
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
                    "play_animation(%r) failed", name,
                )

        self._enqueue_ui(_start)

    def stop_animation(self, name: str) -> None:
        """Stoppt alle laufenden Instanzen einer Animation (z.B. 'think').

        No-op wenn keine Instanz existiert oder Renderer kein Mascot ist.
        Wichtig fuer endlos-loopende Animationen wie 'think', 'sleep'.
        """
        if not isinstance(self._renderer, MascotRenderer):
            return
        renderer = self._renderer
        self._enqueue_ui(lambda: renderer.stop_animation(name))

    def clear_animations(self) -> None:
        """Beendet alle aktiven Animationen sofort."""
        if not isinstance(self._renderer, MascotRenderer):
            return
        renderer = self._renderer
        self._enqueue_ui(renderer.clear_animations)

    def active_animations(self) -> list[str]:
        """Snapshot der aktuell laufenden Animations-Namen.

        NICHT Thread-safe (Read ohne Lock); nur fuer Tests/Debug gedacht.
        """
        if not isinstance(self._renderer, MascotRenderer):
            return []
        return self._renderer.active_animation_names()

    def set_style(self, style: str) -> None:
        """Wechselt den Renderer zur Laufzeit (``"orb"`` oder ``"mascot"``).

        Thread-safe — queued via root.after. Wenn ``"mascot"`` angefordert aber
        das PNG nicht gefunden wird, bleibt der aktuelle Renderer unveraendert
        (der Caller bekommt darueber den Logger-Warn-Eintrag).
        """
        style = (style or "").lower()
        if style == "orb":
            import logging

            logging.getLogger("jarvis.orb").warning(
                "Ignoring legacy orb style request %r; using mascot.",
                style,
            )
            style = "mascot"
        if style != "mascot":
            raise ValueError(f"Unbekannter Style: {style!r} (erlaubt: mascot)")
        if self._root is None:
            # Noch nicht gestartet → Style merken, start() picks it up
            self._style = style
            return
        self._enqueue_ui(lambda: self._apply_style(style))

    def _apply_style(self, style: str) -> None:
        new_renderer = self._build_renderer(style)
        if new_renderer is None:
            return  # Fallback-Fall wurde bereits in _build_renderer geloggt
        self._renderer = new_renderer
        self._style = style

    def _build_renderer(self, style: str) -> MascotRenderer | None:
        if style == "mascot":
            mascot_path = _resolve_mascot_path(self._mascot_path_hint)
            if mascot_path is None:
                import logging
                logging.getLogger("jarvis.orb").warning(
                    "Mascot-Style angefordert, aber PNG nicht gefunden "
                    "(gesucht: %s, ENV JARVIS_ORB_MASCOT_PATH, %s) — "
                    "overlay will stay hidden.",
                    self._mascot_path_hint, DEFAULT_MASCOT_REL,
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

    # --- Intern --------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        if mode not in ("idle", "listen", "speak", "think"):
            raise ValueError(f"Unbekannter Modus: {mode}")
        self._mode = mode

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

    # --- Thread-Lifecycle fuer Integration in async/asyncio-Welten -----

    def start_in_thread(self, auto_demo: bool = False, timeout: float = 3.0) -> None:
        """Startet den Tk-Mainloop in einem Daemon-Thread und kehrt zurueck.

        Auf Windows ist Tk im Background-Thread stabil, solange alle
        UI-Mutationen ueber `root.after(0, fn)` gequeued werden — das tun
        unsere show/hide/set_mode/set_level-Methoden bereits.

        Blockt bis das Fenster wirklich existiert (self._started-Event),
        damit der Caller danach sicher show()/hide() aufrufen kann ohne
        in ein None-root zu laufen.
        """
        import logging
        _log = logging.getLogger("jarvis.orb")

        def _run():
            try:
                self.start(auto_demo=auto_demo)
            except Exception as exc:
                # pythonw.exe hat kein stdout → print geht ins Leere. Logger
                # schreibt ins Watchdog-Log und macht silent-deaths sichtbar.
                _log.exception("Orb-Thread-Start fehlgeschlagen: %s", exc)
        t = threading.Thread(target=_run, name="orb-tk-mainloop", daemon=True)
        t.start()
        ok = self._started.wait(timeout=timeout)
        if not ok:
            _log.error(
                "Orb-Fenster nicht innerhalb %.1fs initialisiert — UI wird nicht poppen.",
                timeout,
            )
        else:
            _log.info("Orb-Overlay Tk-Mainloop läuft (Fenster initialisiert).")

    def _schedule_frame(self) -> None:
        if not self._running or not self._root or not self._canvas or not self._renderer:
            return
        t = time.perf_counter() - self._t0
        img = self._renderer.render(t, self._mode, self._ext_level)

        # PhotoImage muss als self._photo gehalten werden — sonst GC'd
        # Tkinter das Image weg bevor es gerendert wird.
        self._photo = ImageTk.PhotoImage(img)
        if self._image_id is None:
            self._image_id = self._canvas.create_image(
                0, 0, anchor="nw", image=self._photo
            )
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
        help="Fenster dauerhaft sichtbar (Design-Preview)",
    )
    parser.add_argument(
        "--mic",
        action="store_true",
        help="Mic-reaktiv: pulsiert mit Mikrofon-Lautstaerke + Shockwave-Ringe",
    )
    parser.add_argument(
        "--mascot",
        action="store_true",
        help="Verwendet das SWG/Gigi-Maskottchen.",
    )
    parser.add_argument(
        "--mascot-path",
        type=str,
        default=None,
        help=(
            "Optional: expliziter Pfad zu einem Mascot-PNG "
            "(sonst assets/icons/jarvis-gigi-256.png)."
        ),
    )
    parser.add_argument(
        "--animation",
        type=str,
        default=None,
        help=(
            "Visual-QA: spielt eine bestimmte Animation in Endlosschleife "
            "(impliziert --mascot --sticky). Verfuegbar: "
            + ", ".join(sorted(ANIMATION_REGISTRY))
        ),
    )
    parser.add_argument(
        "--all-animations",
        action="store_true",
        help="Spielt alle Animationen nacheinander durch (impliziert --mascot --sticky).",
    )
    args = parser.parse_args()

    if args.animation and args.animation not in ANIMATION_REGISTRY:
        print(
            f"[orb] Unbekannte Animation {args.animation!r}. "
            f"Verfuegbar: {', '.join(sorted(ANIMATION_REGISTRY))}"
        )
        return 2

    use_mascot = args.mascot or bool(args.animation) or args.all_animations
    sticky = args.sticky or args.mic or bool(args.animation) or args.all_animations
    style = "mascot" if use_mascot else "orb"

    overlay = OrbOverlay(
        sticky=sticky,
        mic_reactive=args.mic,
        style=style,
        mascot_path=args.mascot_path,
    )

    # Demo-Threads je nach CLI-Argument
    auto_demo = False
    if args.animation:
        # Loopt die gewuenschte Animation alle 2.5s neu — fuer Visual-QA
        def _loop_anim() -> None:
            overlay._started.wait(timeout=5.0)
            overlay.show(mode="listen")
            while True:
                overlay.play_animation(args.animation)
                # Sleep so lange wie die Animation dauert + 0.6s Pause; loopende
                # Animationen (duration=0) loopen wir alle 4s neu (Re-trigger).
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
        # Klassisches Demo (zeigt listen/speak-Wechsel) wenn nichts spezifiziert.
        # Mit --mic kein auto_demo (sonst wuerde das Demo-Skript den Orb
        # ausblenden, genau waehrend man reinspricht).
        auto_demo = not (args.sticky or args.mic)

    overlay.start(auto_demo=auto_demo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
