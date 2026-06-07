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
_SCALE = 0.30  # overall size (was 0.336; -10% per Wispr feedback)
_W = 0.8       # 20% narrower than the uniform _SCALE
_H = 1.2       # taller than the uniform _SCALE (was 1.5; -20% per feedback)
_IDLE_W = 1.2  # standby pill length — a touch longer so the standby dots fit
_IDLE_H = 0.7  # standby pill is slimmer (less "fat") than the active height
_SW = _SCALE * _W  # combined horizontal factor
_SH = _SCALE * _H  # combined vertical factor

# Three pill sizes (width, height), eased between as the state changes:
# - COLLAPSED: the slim idle standby pill (unchanged).
# - OPEN:      the hover pill that reveals the X / dictation-square controls.
# - ACTIVE:    the conversation pill — DOUBLE the open size, shown the whole
#              time a voice session is live (listen/speak/think). This is the
#              "make the bar much bigger while talking" feature.
COLLAPSED_W = round(168 * _SW * _IDLE_W)  # standby pill (no dots, slightly longer)
COLLAPSED_H = round(30 * _SH * _IDLE_H)  # standby pill (slim)
OPEN_W = round(284 * _SW)  # hover/controls pill (the former "expanded" size)
OPEN_H = round(52 * _SH)
# Conversation pill: 2x the open pill, then trimmed so it doesn't read as bulky.
# Width loses 15% off EACH side (→ 0.70 of 2x = 30% narrower); height loses 5%
# off top AND bottom (→ 0.90 of 2x = 10% shorter). The pill stays centred, so
# the idle bar keeps its middle resting spot.
_ACTIVE_SIDE_TRIM = 0.15  # fraction removed from each side of the 2x width
_ACTIVE_VERT_TRIM = 0.05  # fraction removed from top and bottom of the 2x height
ACTIVE_W = round(2 * OPEN_W * (1.0 - 2 * _ACTIVE_SIDE_TRIM))  # 2x * 0.70
ACTIVE_H = round(2 * OPEN_H * (1.0 - 2 * _ACTIVE_VERT_TRIM))  # 2x * 0.90

# The pill is anchored by its BOTTOM edge this many px above the window bottom,
# so the idle pill keeps its usual resting spot and the active pill grows
# UPWARD (never down into the taskbar). Tune to nudge the resting height.
_BOTTOM_PAD = 10

# The fixed Tk window must contain the largest (ACTIVE) pill + its 2px outline
# and the flanking hover controls. overlay.py reads these dynamically, so the
# window auto-resizes when the pill sizes change.
WIN_W = ACTIVE_W + 12
WIN_H = ACTIVE_H + _BOTTOM_PAD + 4

N_BARS = 10  # slim strokes, count matched to Wispr (was 15 = too many)
# Inner animation geometry is expressed as fractions of the LIVE pill size, so
# the equalizer bars / wave grow together with the pill instead of staying a
# fixed size and looking lost in the big active bar.
_BAR_MAX_FRAC = 0.66  # equalizer max height / pill height
_BAR_MIN_FRAC = 0.10
_BARS_SPAN_FRAC = 0.62  # equalizer span / pill width (wider → room for more bars)
_BAR_HALF_W_FRAC = 0.008  # half bar thickness / pill width (slim Wispr strokes)
_WAVE_W_FRAC = 0.855  # thinking-wave width / pill width (was (284-40)/284)
_WAVE_W = max(2, round(3.0 * _SCALE))  # wave / control stroke thickness (px)

# Standby dots: when nothing is said the pill shows a quiet row of dots
# (Wispr-style) instead of an empty pill. Muted so they read as "at rest".
DOT_COLOR = (150, 140, 120)
_N_DOTS = 7  # dots in the standby row
_DOT_R_FRAC = 0.16  # dot radius / pill height (small round dots, not chunky)
_DOTS_SPAN_FRAC = 0.62  # dots span / pill width (matches the bars)

MODES = ("idle", "listen", "speak", "think")


def pill_center_y(ph: float) -> float:
    """Vertical centre that keeps the pill's BOTTOM edge anchored, so the pill
    grows upward and the idle pill never moves."""
    return WIN_H - _BOTTOM_PAD - ph / 2.0


def bar_max_for(ph: float) -> float:
    """Equalizer max bar height for the given live pill height."""
    return ph * _BAR_MAX_FRAC


def bar_min_for(ph: float) -> float:
    return max(2.0, ph * _BAR_MIN_FRAC)


def bars_span_for(pw: float) -> float:
    """Total equalizer span for the given live pill width."""
    return pw * _BARS_SPAN_FRAC


def bar_half_w_for(pw: float) -> float:
    return max(1.0, pw * _BAR_HALF_W_FRAC)


def evenly_spaced(cx: float, span: float, n: int) -> list[float]:
    """X-positions of ``n`` items centred on ``cx`` across ``span``.

    Shared by the equalizer bars and the standby dots so both rows line up.
    ``n == 1`` returns a single item exactly on ``cx``.
    """
    if n <= 1:
        return [cx]
    x0 = cx - span / 2.0
    step = span / (n - 1)
    return [x0 + i * step for i in range(n)]


def wave_width_for(pw: float) -> float:
    return pw * _WAVE_W_FRAC


def target_pill_size(mode: str, hovered: bool) -> tuple[int, int]:
    """Pick the pill's target (w, h): ACTIVE while a session is live, OPEN on
    hover (to show controls), COLLAPSED at rest. Only a live session is 2x —
    matching 'bigger only while in the conversation'."""
    if mode in ("listen", "speak", "think"):
        return ACTIVE_W, ACTIVE_H
    if hovered:
        return OPEN_W, OPEN_H
    return COLLAPSED_W, COLLAPSED_H


def _hex_to_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def ease(current: float, target: float, factor: float) -> float:
    """Exponential ease of ``current`` toward ``target``. factor in (0, 1]."""
    return current + (target - current) * factor


def visual_mode(
    coarse_mode: str,
    seconds_since_audible: float,
    *,
    hold_s: float,
    playback_active: bool = False,
) -> str:
    """Derive the rendered look from the coarse mode + actual audio activity.

    The bar's look is driven by ACTUAL audio, not by the supervisor state: the
    supervisor flips LISTENING/THINKING/SPEAKING in ways that don't line up with
    when sound is audible (TTS synthesis is silent for 0.5–20 s after the
    SPEAKING transition; continue-listening flips back to LISTENING mid-playback
    while Jarvis is still talking). So:

    The wave (the animated "indicator") belongs ONLY to active thinking. Three
    distinct looks:

    - ``idle`` → ``idle`` (the standby pill). Silence here is not "thinking".
    - Real sound — ``playback_active`` (TTS audio on the device right now) OR a
      recent level within ``hold_s`` (your live mic) → the equalizer (``"speak"``
      → bars that move with the sound). ``playback_active`` is the player's
      authoritative signal, needed because the level tap only fires at
      buffer-write time (a brief instant per sentence) while the player then
      blocks for the whole multi-second playback with no further level.
    - Silent + ``coarse_mode == "think"`` (the THINKING state, and the silent
      TTS-synthesis lead-in which the bridge also shows as ``"think"``) → the
      wave. This is the only place an indicator animates.
    - Silent + any OTHER active state (``"listen"`` — waiting after "Hey Jarvis"
      with no speech) → ``"speak"`` too, but with no level the equalizer renders
      flat and STILL: bars that just stand there, no wave. "When nothing
      happens, nothing happens."

    ``hold_s`` bridges the short gaps between words/sentences so the bars don't
    flap back on every micro-pause.
    """
    if coarse_mode == "idle":
        return "idle"
    if playback_active or seconds_since_audible < hold_s:
        return "speak"
    if coarse_mode == "think":
        return "think"
    return "speak"


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
    pw: float = float(COLLAPSED_W)  # live pill width, eased toward the target
    ph: float = float(COLLAPSED_H)  # live pill height, eased toward the target


class WhisperBarRenderer:
    def __init__(self, accent: str = "#e7c46e") -> None:
        self._accent = _hex_to_rgb(accent)
        self._st = _RenderState()

    def render(
        self, t: float, mode: str, ext_level: float, hovered: bool = False
    ) -> Image.Image:
        active = mode in ("listen", "speak")
        # Ease the pill toward its target size: ACTIVE (2x) while a session is
        # live, OPEN on hover (controls), COLLAPSED at rest.
        tw, th = target_pill_size(mode, hovered)
        # Snappy grow/shrink: 0.5 reaches the target in ~4 frames (~70 ms) so the
        # bar pops to full size almost immediately on "Hey Jarvis" instead of
        # crawling there over a third of a second.
        self._st.pw = ease(self._st.pw, tw, 0.5)
        self._st.ph = ease(self._st.ph, th, 0.5)
        self._st.display_level = ease(
            self._st.display_level, ext_level if active else 0.0, 0.35
        )
        pw, ph = self._st.pw, self._st.ph

        frame = np.empty((WIN_H, WIN_W, 3), dtype=np.uint8)
        frame[:, :] = COLOR_KEY_RGB
        img = Image.fromarray(frame)  # uint8 (H,W,3) → mode "RGB"
        d = ImageDraw.Draw(img)

        cx = WIN_W / 2.0
        cy = pill_center_y(ph)  # bottom-anchored: grows upward, idle stays put
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
                self._draw_bars(d, t, cx, cy, pw, ph, span=bars_span_for(pw) * 0.5, n=5)
            if active_sess:
                self._draw_close_x(d, x_left, cy, ph)
            self._draw_square(d, x_right, cy, ph)
        elif mode == "think":
            self._draw_wave(d, t, wave_width_for(pw), ph, cx, cy)
        elif mode in ("listen", "speak"):
            self._draw_bars(d, t, cx, cy, pw, ph)
        # idle / standby (not hovered): a clean EMPTY pill — no dots, no bars.
        # "When nothing is happening, nothing is in the bar."
        return img

    def _draw_dots(
        self, img: Image.Image, cx: float, cy: float, pw: float, ph: float
    ) -> None:
        # Supersample the dots: at this tiny resolution a 3 px circle drawn
        # directly renders as a cross. Draw at 4x on a transparent layer, then
        # downscale with antialiasing → clean round dots.
        ss = 4
        r = max(1.5, ph * _DOT_R_FRAC) * ss
        layer = Image.new("RGBA", (img.width * ss, img.height * ss), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for x in evenly_spaced(cx, _DOTS_SPAN_FRAC * pw, _N_DOTS):
            px, py = x * ss, cy * ss
            ld.ellipse([px - r, py - r, px + r, py + r], fill=(*DOT_COLOR, 255))
        small = layer.resize(img.size, Image.Resampling.LANCZOS)
        img.paste(small, (0, 0), small)

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
        pw: float,
        ph: float,
        span: float | None = None,
        n: int | None = None,
    ) -> None:
        n = n or N_BARS
        span = bars_span_for(pw) if span is None else span
        half_w = bar_half_w_for(pw)
        hs = bar_heights(
            t, self._st.display_level, n, max_h=bar_max_for(ph), min_h=bar_min_for(ph)
        )
        for x, h in zip(evenly_spaced(cx, span, n), hs, strict=True):
            d.rounded_rectangle(
                [x - half_w, cy - h / 2, x + half_w, cy + h / 2],
                radius=half_w,
                fill=self._accent,
            )

    def _draw_wave(
        self, d: ImageDraw.ImageDraw, t: float, width: float, ph: float, cx: float, cy: float
    ) -> None:
        pts = wave_points(t, int(width), int(ph), cx, cy, n=48)
        d.line(pts, fill=self._accent, width=_WAVE_W, joint="curve")
