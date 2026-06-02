"""Pure rendering math + drawing for the whisper bar.

No tkinter, no I/O — every function is deterministic given its inputs, so the
visual behaviour is unit-testable. ``WhisperBarRenderer.render`` returns a PIL
image with a magenta color-key background that the Tk surface keys out.

State → look:
- ``idle``   → muted grey dots in a collapsed pill
- ``listen`` → gold equalizer bars, height driven by the live mic level
- ``speak``  → gold equalizer bars, height driven by the live TTS level
- ``think``  → a flowing gold sine wave (synthetic, ignores level)

Gold only appears during activity; idle dots stay muted.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

COLOR_KEY_RGB = (255, 0, 255)
PILL_BG = (14, 13, 12)
# Bright gold rim: the only thing that reads when the pill is slim AND
# semi-transparent (window -alpha). Dark fill + glowing gold edge = glass look.
PILL_BORDER = (215, 182, 105)
# Hover-to-hang-up close cross (soft red = "close").
CLOSE_X = (228, 110, 96)

# Size factors. ``_SCALE`` is the overall shrink (1.0 was the original, far too
# big). ``_W`` / ``_H`` then stretch width / height independently on top of it:
# _W < 1 narrows, _H > 1 makes it taller. Tune these three numbers to resize.
_SCALE = 0.336  # overall size (was 0.42; -20% per feedback)
_W = 0.8       # 20% narrower than the uniform _SCALE
_H = 1.2       # taller than the uniform _SCALE (was 1.5; -20% per feedback)
_IDLE_W = 1.08  # standby pill length (was 1.2; -10% per feedback: 5% off each side)
_IDLE_H = 0.7  # standby pill is slimmer (less "fat") than the active height
_SW = _SCALE * _W  # combined horizontal factor
_SH = _SCALE * _H  # combined vertical factor

WIN_W = round(300 * _SW)
WIN_H = round(72 * _SH)
COLLAPSED_W = round(168 * _SW * _IDLE_W)  # standby pill (no dots, slightly longer)
COLLAPSED_H = round(30 * _SH * _IDLE_H)  # standby pill (slim)
EXPANDED_W = round(284 * _SW)
EXPANDED_H = round(52 * _SH)
N_BARS = 7
BAR_MIN_H = max(2.0, 4.0 * _SH)
BAR_MAX_H = 38.0 * _SH
# Derived stroke geometry (min-clamped so the animation stays visible when tiny).
_BARS_SPAN = round(150 * _SW)
_BAR_HALF_W = max(1.5, 3.0 * _SW)
_WAVE_W = max(2, round(3.0 * _SCALE))
_WAVE_MARGIN = round(40 * _SW)
MODES = ("idle", "listen", "speak", "think")


def _hex_to_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def ease(current: float, target: float, factor: float) -> float:
    """Exponential ease of ``current`` toward ``target``. factor in (0, 1]."""
    return current + (target - current) * factor


def bar_heights(
    t: float, level: float, n: int, *, max_h: float, min_h: float
) -> list[float]:
    """Equalizer bar heights, deterministic in (t, level).

    ``level <= 0`` → all bars at ``min_h``. Height grows with level. Each bar
    has a distinct phase so the row never moves in lockstep. Bounded by
    ``[min_h, max_h]``.
    """
    level = 0.0 if level < 0.0 else 1.0 if level > 1.0 else level
    out: list[float] = []
    for i in range(n):
        phase = i * 0.9
        osc = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * 9.0 + phase))  # 0.55..1.0
        out.append(min_h + (max_h - min_h) * level * osc)
    return out


def wave_points(
    t: float, width: int, height: int, cx: float, cy: float, n: int = 48
) -> list[tuple[float, float]]:
    """Travelling sine polyline for THINKING, tapered to stay inside the pill."""
    pts: list[tuple[float, float]] = []
    half = width / 2.0
    amp = height * 0.32
    for k in range(n + 1):
        u = k / n
        x = cx - half + u * width
        envelope = math.sin(u * math.pi)
        y = cy + math.sin(u * math.pi * 3.0 - t * 4.0) * amp * envelope
        pts.append((x, y))
    return pts


@dataclass
class _RenderState:
    display_level: float = 0.0
    expand: float = 0.0  # 0 collapsed .. 1 expanded


class WhisperBarRenderer:
    def __init__(self, accent: str = "#e7c46e") -> None:
        self._accent = _hex_to_rgb(accent)
        self._st = _RenderState()

    def render(
        self, t: float, mode: str, ext_level: float, hovered: bool = False
    ) -> Image.Image:
        active = mode in ("listen", "speak")
        # Hovering OPENS the bar (expands it) so the left X + right square
        # controls have room, even from the collapsed idle pill.
        target_expand = 0.0 if (mode == "idle" and not hovered) else 1.0
        self._st.expand = ease(self._st.expand, target_expand, 0.25)
        self._st.display_level = ease(
            self._st.display_level, ext_level if active else 0.0, 0.35
        )

        frame = np.empty((WIN_H, WIN_W, 3), dtype=np.uint8)
        frame[:, :] = COLOR_KEY_RGB
        img = Image.fromarray(frame)  # uint8 (H,W,3) → mode "RGB"
        d = ImageDraw.Draw(img)

        pw = COLLAPSED_W + (EXPANDED_W - COLLAPSED_W) * self._st.expand
        ph = COLLAPSED_H + (EXPANDED_H - COLLAPSED_H) * self._st.expand
        cx, cy = WIN_W / 2.0, WIN_H / 2.0
        d.rounded_rectangle(
            [cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2],
            radius=ph / 2,
            fill=PILL_BG,
            outline=PILL_BORDER,
            width=2,
        )

        # Hover splits the bar into controls: LEFT X (hang up, only while a
        # session is live) + RIGHT square (toggle endpoint-free dictation).
        if hovered:
            x_left = cx - 0.42 * pw
            x_right = cx + 0.35 * pw  # pulled left off the right edge (no clip)
            active_sess = mode in ("listen", "speak", "think")
            # Keep the speech indicator VISIBLE while interacting — narrow bars
            # in the centre so you can see the voice is live, controls flanking.
            if mode in ("listen", "speak"):
                self._draw_bars(d, t, cx, cy, span=_BARS_SPAN * 0.5, n=5)
            if active_sess:
                self._draw_close_x(d, x_left, cy, ph)
            self._draw_square(d, x_right, cy, ph)
        elif mode == "think":
            self._draw_wave(d, t, EXPANDED_W - _WAVE_MARGIN, ph, cx, cy)
        elif mode in ("listen", "speak"):
            self._draw_bars(d, t, cx, cy)
        # idle / standby (not hovered): a clean pill, nothing in the middle
        return img

    def _draw_close_x(self, d: ImageDraw.ImageDraw, cx: float, cy: float, ph: float) -> None:
        r = max(3.0, ph * 0.26)  # half-diagonal of the cross
        w = max(2, _WAVE_W)
        d.line([(cx - r, cy - r), (cx + r, cy + r)], fill=CLOSE_X, width=w)
        d.line([(cx - r, cy + r), (cx + r, cy - r)], fill=CLOSE_X, width=w)

    def _draw_square(self, d: ImageDraw.ImageDraw, cx: float, cy: float, ph: float) -> None:
        r = max(2.5, ph * 0.21)  # half-side of the dictation square
        w = max(2, _WAVE_W)
        d.rounded_rectangle(
            [cx - r, cy - r, cx + r, cy + r], radius=max(1.0, r * 0.25),
            outline=self._accent, width=w,
        )

    def _draw_bars(
        self,
        d: ImageDraw.ImageDraw,
        t: float,
        cx: float,
        cy: float,
        span: float | None = None,
        n: int | None = None,
    ) -> None:
        n = n or N_BARS
        span = _BARS_SPAN if span is None else span
        hs = bar_heights(
            t, self._st.display_level, n, max_h=BAR_MAX_H, min_h=BAR_MIN_H
        )
        x0 = cx - span / 2.0
        step = span / max(1, n - 1)
        for i, h in enumerate(hs):
            x = x0 + i * step
            d.rounded_rectangle(
                [x - _BAR_HALF_W, cy - h / 2, x + _BAR_HALF_W, cy + h / 2],
                radius=_BAR_HALF_W,
                fill=self._accent,
            )

    def _draw_wave(
        self, d: ImageDraw.ImageDraw, t: float, width: int, ph: float, cx: float, cy: float
    ) -> None:
        pts = wave_points(t, width, int(ph), cx, cy, n=48)
        d.line(pts, fill=self._accent, width=_WAVE_W, joint="curve")
