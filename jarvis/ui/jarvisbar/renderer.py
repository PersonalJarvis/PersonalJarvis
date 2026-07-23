"""Pure rendering math + drawing for the whisper bar.

No tkinter, no I/O — every function is deterministic given its inputs, so the
visual behaviour is unit-testable. ``JarvisBarRenderer.render`` returns a PIL
image with a magenta color-key background that the Tk surface keys out.

State → look:
- ``idle``   → muted grey dots in a collapsed pill
- ``listen`` → gold equalizer bars, height driven by the live mic level
- ``speak``  → gold equalizer bars, height driven by the live TTS level
- ``think``  → the "orbital core": a breathing gold core with two comet
               sparks counter-orbiting on tilted ellipses (synthetic,
               ignores level). Replaced the old travelling sine wave,
               which read as a generic-AI visual.

Gold only appears during activity; idle dots stay muted.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

COLOR_KEY_RGB = (255, 0, 255)


def key_to_alpha(img: Image.Image) -> Image.Image:
    """RGB frame → RGBA with the magenta color key mapped to full transparency.

    Windows keys the magenta out natively (layered-window color key); macOS
    has no color-key concept, so the Tk surface there shows RGBA frames on a
    ``-transparent`` root instead. Exact-match keying mirrors the Windows
    contract: only pure ``COLOR_KEY_RGB`` pixels vanish.
    """
    arr = np.asarray(img, dtype=np.uint8)
    alpha = np.where(
        (arr == COLOR_KEY_RGB).all(axis=-1), 0, 255
    ).astype(np.uint8)
    return Image.fromarray(np.dstack((arr, alpha)), "RGBA")


PILL_BG = (14, 13, 12)
# Bright gold rim: the only thing that reads when the pill is slim AND
# semi-transparent (window -alpha). Dark fill + glowing gold edge = glass look.
PILL_BORDER = (215, 182, 105)
# Hover-to-hang-up close cross (soft red = "close").
CLOSE_X = (228, 110, 96)
# Muted-state rim + slashed-mic disc: a clear-but-soft red so the user can tell
# at a glance they are muted (the pill border turns this colour whenever the
# voice mic is muted FOR JARVIS, even at rest). Tune freely — purely cosmetic.
MUTED_RED = (220, 80, 72)

# Size factors. ``_SCALE`` is the overall shrink (1.0 was the original, far too
# big). ``_W`` / ``_H`` then stretch width / height independently on top of it:
# _W < 1 narrows, _H > 1 makes it taller. Tune these three numbers to resize.
_SCALE = 0.30  # overall size (was 0.336; -10% per maintainer feedback)
_W = 0.8       # 20% narrower than the uniform _SCALE
_H = 1.2       # taller than the uniform _SCALE (was 1.5; -20% per feedback)
_IDLE_W = 1.08  # standby pill length (was 1.2; -5% per side, maintainer 2026-07-21)
_IDLE_H = 0.7  # standby pill is slimmer (less "fat") than the active height
_SW = _SCALE * _W  # combined horizontal factor
_SH = _SCALE * _H  # combined vertical factor

# --- screen-adaptive display scale (screen-relative sizing) ------------------
# The pill sizes below were tuned on a desktop monitor and are RAW pixels
# (Tk points on macOS). On a small laptop screen (a 14" MacBook is ~1512 Tk
# points wide) the same fixed size occupies nearly twice the relative width
# and reads as clunky. ``DISPLAY_SCALE`` adapts the whole geometry to the
# screen the bar actually lives on: BASE_DISPLAY_SCALE (the maintainer-
# approved look) on anything at least REFERENCE_SCREEN_W x
# REFERENCE_SCREEN_H, proportionally smaller below that, never under
# MIN_DISPLAY_SCALE so the controls stay clickable. Scaling happens at
# RENDER time — the frame is drawn crisply at the scaled size. This is NOT
# the blurry DPI bitmap upscaling that was explicitly rejected (see
# overlay.start()'s DPI notes); the DPI strategy there is untouched.
REFERENCE_SCREEN_W = 1920
REFERENCE_SCREEN_H = 1080
MIN_DISPLAY_SCALE = 0.55
# The signed-off size ceiling. The historical constants (scale 1.0) render
# the idle pill 47 px long on the maintainer's 2560x1440 monitor — judged
# "too big" against a 40 px good-example screenshot (2026-07-21), while a
# physical-mm experiment at 0.595 (~29 px) was "too small". 40/47 = 0.85
# lands exactly on the good example, and the 14" laptop's proportional
# 0.79 (independently signed off) sits in the same zone — so 0.85 is the
# approved look on every screen at least the reference size.
BASE_DISPLAY_SCALE = 0.85
DISPLAY_SCALE = 1.0

# --- physical-size-consistent scaling (true per-monitor DPI) ------------------
# The screen-adaptive scale above is RESOLUTION-relative, so two monitors of the
# SAME resolution but DIFFERENT physical size render the bar at the same pixel
# size — physically bigger on the bigger monitor. To make the bar look the SAME
# PHYSICAL SIZE on the glass everywhere, ``compute_physical_scale`` scales by the
# monitor's TRUE physical DPI instead (from EDID via GetDpiForMonitor(MDT_RAW_DPI)
# on Windows / xrandr mm on X11 — NOT the OS scaling, and NOT Tk's winfo_screenmm
# which returns a 96-DPI-derived FAKE on Windows). A physical-mm model was tried
# and reverted once as "too small" (2026-07-21) because it anchored to a 14"
# laptop; the fix is anchoring to the maintainer's monitor DPI below, so THAT
# monitor keeps its exact current look and only physically different monitors
# adjust. When the true DPI is unavailable/implausible (macOS, Wayland, headless,
# missing EDID) the caller falls back to the resolution-relative scale.
REFERENCE_RAW_DPI = 154.0  # calibrated reference physical DPI (~28in 4K desktop);
# compute_physical_scale returns exactly BASE_DISPLAY_SCALE at this DPI, so the
# reference monitor is unchanged. Measured live via GetDpiForMonitor(MDT_RAW_DPI).
MAX_DISPLAY_SCALE = 1.6  # physical sizing may exceed 1.0 on dense (4K/Retina)
# monitors; bounded so a very dense or MISREPORTED monitor can't produce an absurd
# bar. The resolution path is separately capped at BASE_DISPLAY_SCALE, so raising
# this ceiling only affects the physical path.
PHYSICAL_DPI_MIN = 60.0   # plausibility gate: below → treat DPI as unknown
PHYSICAL_DPI_MAX = 350.0  # (fail-closed to the resolution fallback)


def compute_physical_scale(raw_dpi: float) -> float | None:
    """Screen scale that holds the bar's PHYSICAL size constant across monitors.

    ``raw_dpi`` is the monitor's TRUE physical dots-per-inch (EDID), NOT the OS
    display-scaling. Returns ``BASE_DISPLAY_SCALE * raw_dpi / REFERENCE_RAW_DPI``
    so a denser monitor draws MORE pixels (same physical size) and a coarser one
    FEWER, clamped to ``[MIN_DISPLAY_SCALE, MAX_DISPLAY_SCALE]``. At
    ``REFERENCE_RAW_DPI`` it returns exactly ``BASE_DISPLAY_SCALE`` (the reference
    monitor is unchanged). ``None`` when ``raw_dpi`` is missing / non-finite /
    implausible, so the caller falls back to ``compute_display_scale``.
    """
    try:
        d = float(raw_dpi)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(d) or not (PHYSICAL_DPI_MIN <= d <= PHYSICAL_DPI_MAX):
        return None
    raw = BASE_DISPLAY_SCALE * d / REFERENCE_RAW_DPI
    return max(MIN_DISPLAY_SCALE, min(MAX_DISPLAY_SCALE, round(raw, 4)))


def resolve_screen_scale(
    screen_w: int, screen_h: int, raw_dpi: float | None = None
) -> float:
    """The bar's base screen scale: physical-size-consistent when the monitor's
    true DPI is known + plausible, else the resolution-relative fallback.

    This is the single entry point the surfaces call — Windows/X11 pass the real
    per-monitor ``raw_dpi``; macOS (and any host that can't read it) passes
    ``None`` and gets today's resolution-relative behaviour unchanged.
    """
    if raw_dpi is not None:
        phys = compute_physical_scale(raw_dpi)
        if phys is not None:
            return phys
    return compute_display_scale(screen_w, screen_h)


# --- user size preference (the "Bar size" slider) ----------------------------
# A multiplier applied ON TOP of the screen-adaptive DISPLAY_SCALE, chosen by
# the user in Settings → "Bar size". 1.0 reproduces the signed-off default
# look byte-identically; below shrinks, above enlarges. Unlike DISPLAY_SCALE
# (which never enlarges past the approved ceiling because the maintainer's look
# is the ceiling), THIS axis is the user's explicit choice, so it may exceed
# 1.0. The frame is still drawn CRISPLY at the larger geometry — every constant
# is recomputed and the pill is redrawn at the scaled size, exactly like the
# screen-adaptive path. This is NOT the blurry DPI bitmap upscaling that was
# rejected (see overlay.start()'s DPI notes); the DPI strategy is untouched.
# Width AND height scale together (the whole geometry multiplies by one factor),
# so the pill's shape is preserved and only its size changes.
USER_SIZE_MIN = 0.5
USER_SIZE_MAX = 2.0
USER_SIZE_DEFAULT = 1.0
USER_SIZE_SCALE = USER_SIZE_DEFAULT


def clamp_user_size(user_size: float) -> float:
    """Clamp a user size multiplier into ``[USER_SIZE_MIN, USER_SIZE_MAX]``.

    Non-numeric / non-finite input degrades to ``USER_SIZE_DEFAULT`` so a
    corrupt persisted value can never brick the bar geometry.
    """
    try:
        u = float(user_size)
    except (TypeError, ValueError):
        return USER_SIZE_DEFAULT
    if not math.isfinite(u):
        return USER_SIZE_DEFAULT
    return max(USER_SIZE_MIN, min(USER_SIZE_MAX, u))


def compute_display_scale(screen_w: int, screen_h: int) -> float:
    """Scale factor for the screen the bar lives on (pure, unit-testable).

    Never enlarges beyond ``BASE_DISPLAY_SCALE`` (big monitors keep the
    approved look); shrinks proportionally on screens smaller than the
    reference in either axis; clamps at ``MIN_DISPLAY_SCALE``. Invalid input
    degrades to ``BASE_DISPLAY_SCALE``.
    """
    try:
        sw, sh = int(screen_w), int(screen_h)
    except (TypeError, ValueError):
        return BASE_DISPLAY_SCALE
    if sw <= 0 or sh <= 0:
        return BASE_DISPLAY_SCALE
    s = min(BASE_DISPLAY_SCALE, sw / REFERENCE_SCREEN_W, sh / REFERENCE_SCREEN_H)
    return max(MIN_DISPLAY_SCALE, round(s, 3))


# Three pill sizes (width, height), eased between as the state changes:
# - COLLAPSED: the slim idle standby pill (unchanged).
# - OPEN:      the hover pill that reveals the X / dictation-square controls.
# - ACTIVE:    the conversation pill — DOUBLE the open size, shown the whole
#              time a voice session is live (listen/speak/think). This is the
#              "make the bar much bigger while talking" feature.
# Conversation pill: 2x the open pill, then trimmed so it doesn't read as bulky.
# Width keeps 0.518 of 2x; height keeps 0.56 of 2x — the live pill stays slim
# and only moderately longer than the hover pill. Calibrated in two maintainer
# rounds (2026-07-21): the height trim killed the "way too big" 29 px
# thickness (target example: 18 px); the width then still read "much too
# wide" at 86 px on the 2560x1440 monitor, and the maintainer asked for 15%
# off each side → 0.74 * 0.70 = 0.518 of 2x (~60 px there). The pill stays
# centred, so the idle bar keeps its middle resting spot.
_ACTIVE_SIDE_TRIM = 0.241  # fraction removed from each side of the 2x width
_ACTIVE_VERT_TRIM = 0.22  # fraction removed from top and bottom of the 2x height

# The pill is anchored by its BOTTOM edge this many px above the window bottom,
# so the idle pill keeps its usual resting spot and the active pill grows
# UPWARD (never down into the taskbar). Tune _BASE_BOTTOM_PAD to nudge the
# resting height.
_BASE_BOTTOM_PAD = 10


def apply_display_scale(scale: float, user_size: float | None = None) -> None:
    """Recompute every derived geometry constant for ``scale``.

    Called once by ``overlay.start()`` (one bar per process) before any
    window geometry or renderer state derives from these values; module load
    applies 1.0, which reproduces the historical constants byte-identically.
    ``overlay.py`` reads the module attributes dynamically, so the window
    follows the recomputed sizes.

    ``scale`` is the SCREEN-adaptive factor (clamped to ``[MIN_DISPLAY_SCALE,
    1.0]``). ``user_size`` is the user's "Bar size" preference multiplied on
    top; ``None`` keeps the current ``USER_SIZE_SCALE`` (so the old single-arg
    call sites and the module-load call are byte-identical when the user has
    not changed the size). The effective geometry factor is
    ``DISPLAY_SCALE * USER_SIZE_SCALE`` — one number multiplies width, height,
    padding and window alike, so the bar's SHAPE is preserved and only its
    SIZE changes. The live "Bar size" slider re-invokes this with the same
    screen scale and a new ``user_size`` (surfaces call it via
    ``set_size_scale``).
    """
    global DISPLAY_SCALE, USER_SIZE_SCALE, COLLAPSED_W, COLLAPSED_H, OPEN_W, OPEN_H
    global ACTIVE_W, ACTIVE_H, _BOTTOM_PAD, WIN_W, WIN_H
    # Upper clamp is MAX_DISPLAY_SCALE (not 1.0): the physical-size path may
    # legitimately exceed 1.0 on a dense (4K/Retina) monitor. The resolution
    # path is separately capped at BASE_DISPLAY_SCALE by compute_display_scale,
    # so this wider ceiling only ever admits a physical scale.
    DISPLAY_SCALE = s = max(MIN_DISPLAY_SCALE, min(MAX_DISPLAY_SCALE, float(scale)))
    if user_size is not None:
        USER_SIZE_SCALE = clamp_user_size(user_size)
    g = s * USER_SIZE_SCALE  # effective geometry factor (screen × user size)
    COLLAPSED_W = round(168 * _SW * _IDLE_W * g)  # standby pill (slightly longer)
    COLLAPSED_H = round(30 * _SH * _IDLE_H * g)  # standby pill (slim)
    OPEN_W = round(284 * _SW * g)  # hover/controls pill (the former "expanded")
    OPEN_H = round(52 * _SH * g)
    ACTIVE_W = round(2 * OPEN_W * (1.0 - 2 * _ACTIVE_SIDE_TRIM))  # 2x * 0.518
    ACTIVE_H = round(2 * OPEN_H * (1.0 - 2 * _ACTIVE_VERT_TRIM))  # 2x * 0.56
    _BOTTOM_PAD = max(4, round(_BASE_BOTTOM_PAD * g))
    # The fixed Tk window must contain the largest (ACTIVE) pill + its 2px
    # outline and the flanking hover controls.
    WIN_W = ACTIVE_W + 12
    WIN_H = ACTIVE_H + _BOTTOM_PAD + 4


apply_display_scale(1.0)

N_BARS = 10  # slim strokes (was 15 = too many)
# Inner animation geometry is expressed as fractions of the LIVE pill size, so
# the equalizer bars / wave grow together with the pill instead of staying a
# fixed size and looking lost in the big active bar.
_BAR_MAX_FRAC = 0.66  # equalizer max height / pill height
_BAR_MIN_FRAC = 0.10
_BARS_SPAN_FRAC = 0.62  # equalizer span / pill width (wider → room for more bars)
_BAR_HALF_W_FRAC = 0.008  # half bar thickness / pill width (slim strokes)
_STROKE_W = max(2, round(3.0 * _SCALE))  # control stroke thickness (px)

# Standby dots: when nothing is said the pill shows a quiet row of dots
# instead of an empty pill. Muted so they read as "at rest".
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


def target_pill_size(mode: str, hovered: bool, muted: bool = False) -> tuple[int, int]:
    """Pick the pill's target (w, h): ACTIVE while a session is live, OPEN on
    hover (to show controls), COLLAPSED at rest. Only a live session is 2x —
    matching 'bigger only while in the conversation'.

    Muted standby is the exception: instead of collapsing to the tiny empty
    pill (where the red rim is a hairline and the mic glyph is hidden), the
    muted bar stays at the OPEN size so the slashed-mic + red rim stay visible.
    A muted user is otherwise trapped — they can't unmute by voice (Jarvis is
    deaf while muted), so the click target must always be on screen."""
    if mode in ("listen", "speak", "think"):
        return ACTIVE_W, ACTIVE_H
    if hovered or muted:
        return OPEN_W, OPEN_H
    return COLLAPSED_W, COLLAPSED_H


def _hex_to_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _lerp_rgb(
    a: tuple[int, int, int], b: tuple[int, int, int], u: float
) -> tuple[int, int, int]:
    return (
        round(a[0] + (b[0] - a[0]) * u),
        round(a[1] + (b[1] - a[1]) * u),
        round(a[2] + (b[2] - a[2]) * u),
    )


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

    The orbital core (the animated "indicator") belongs ONLY to active
    thinking. Three distinct looks:

    - ``idle`` → ``idle`` (the standby pill). Silence here is not "thinking".
    - Real sound — ``playback_active`` (TTS audio on the device right now) OR a
      recent level within ``hold_s`` (your live mic) → the equalizer (``"speak"``
      → bars that move with the sound). ``playback_active`` is the player's
      authoritative signal, needed because the level tap only fires at
      buffer-write time (a brief instant per sentence) while the player then
      blocks for the whole multi-second playback with no further level.
    - Silent + ``coarse_mode == "think"`` (the THINKING state, and the silent
      TTS-synthesis lead-in which the bridge also shows as ``"think"``) → the
      orbital core. This is the only place an indicator animates.
    - Silent + any OTHER active state (``"listen"`` — waiting after "Hey Jarvis"
      with no speech) → ``"speak"`` too, but with no level the equalizer renders
      flat and STILL: bars that just stand there, no indicator. "When nothing
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


# --- thinking: the "orbital core" --------------------------------------------
# A breathing gold core with two thought-sparks counter-orbiting on tilted
# ellipses. The periods are deliberately incommensurate so the composite
# figure never visibly repeats — it reads as churning thought, not a loop.
# Sparks carry a depth value: on the far half of their orbit they render
# smaller/dimmer and BEHIND the core, on the near half bigger/brighter and in
# front — a pseudo-3D gyroscope inside a 34 px pill.


@dataclass(frozen=True)
class OrbitSpec:
    period_s: float  # seconds per revolution; the sign is the spin direction
    rx_frac: float   # ellipse semi-axis (own x) / pill width
    ry_frac: float   # ellipse semi-axis (own y) / pill height
    tilt_rad: float  # rotation of the ellipse within the pill plane
    phase: float     # angular offset so the sparks never start aligned


ORBITS: tuple[OrbitSpec, ...] = (
    OrbitSpec(period_s=3.1, rx_frac=0.40, ry_frac=0.34, tilt_rad=-0.26, phase=0.7),
    OrbitSpec(period_s=-1.95, rx_frac=0.43, ry_frac=0.24, tilt_rad=0.42, phase=2.4),
)

TRAIL_N = 12        # samples per comet tail
TRAIL_SPAN_S = 0.50  # how far back in time the tail reaches

_CORE_R_FRAC = 0.115      # core radius / pill height (slimmer: the ring needs room)
_CORE_BREATH = 0.20       # breathing amplitude as a fraction of the base radius
_CORE_BREATH_RAD_S = 3.2  # breathing speed (rad/s ≈ one breath every 2 s)
_CORE_BREATH2 = 0.07      # slower second breath layered in — kills the metronome feel
_CORE_BREATH2_RAD_S = 1.3

# The whole reactor FLOATS: the core (and with it the ring, highlight and the
# spark orbits) drifts on a slow two-frequency Lissajous path plus a faint
# faster wobble. A position-pinned core read as "starr" — twice — even with
# the ring glint; motion of the body itself is what reads as alive.
DRIFT_AX_FRAC = 0.062     # max |dx| / pill width  (incl. the micro wobble)
DRIFT_AY_FRAC = 0.090     # max |dy| / pill height
_DRIFT_WX = 0.80          # rad/s — horizontal float
_DRIFT_WY = 1.27          # rad/s — vertical float (incommensurate with WX)
_DRIFT_W_MICRO = 1.9      # rad/s — faint quick wobble on top
_DRIFT_MICRO_FRAC = 0.012  # micro wobble amplitude / pill width
_SPARK_R_FRAC = 0.058     # spark radius / pill height (clearly below the core)
_SPARK_DEPTH_SIZE = 0.30  # spark size swing between far and near orbit half

# The core's saturn ring: a perspective-flattened ellipse hugging the sphere,
# with a glint of light travelling around it. This is what keeps the centre
# alive — a bare static dot read as dead (user feedback 2026-06-10).
_RING_RX = 2.05           # ring semi-axis x / core radius
_RING_RY = 0.62           # ring semi-axis y / core radius (perspective squash)
_RING_TILT_RAD = -0.18    # slight tilt so the ring reads as 3D, not as an "0"
_RING_N = 28              # polyline samples around the ring
_RING_GLINT_RAD_S = 3.0   # how fast the light runs around the ring (rad/s)
_HILITE_SWING_RAD_S = 0.9  # specular drift speed — a slowly turning sphere


def core_radius(t: float, ph: float) -> float:
    """Breathing radius of the thinking core — always well inside the pill.

    Two layered sine rhythms so the pulse feels organic, not metronomic.
    """
    base = ph * _CORE_R_FRAC
    breath = _CORE_BREATH * math.sin(t * _CORE_BREATH_RAD_S)
    breath += _CORE_BREATH2 * math.sin(t * _CORE_BREATH2_RAD_S)
    return base * (1.0 + breath)


def core_drift(t: float, pw: float, ph: float) -> tuple[float, float]:
    """Floating offset of the whole reactor relative to the pill centre.

    Slow two-frequency Lissajous plus a faint quicker wobble — visible
    within a ~3 s thinking phase, bounded by DRIFT_A*_FRAC, never looping.
    """
    main = (DRIFT_AX_FRAC - _DRIFT_MICRO_FRAC) * pw
    dx = math.sin(t * _DRIFT_WX) * main
    dx += math.sin(t * _DRIFT_W_MICRO + 0.8) * _DRIFT_MICRO_FRAC * pw
    dy = math.sin(t * _DRIFT_WY + 1.1) * DRIFT_AY_FRAC * ph
    return (dx, dy)


def core_ring_points(
    t: float, r: float, n: int = _RING_N
) -> list[tuple[float, float, float, float]]:
    """Saturn-ring samples around the core: ``(dx, dy, depth, glint)``.

    ``depth`` < 0 marks the half that passes BEHIND the sphere; ``glint`` is
    the 0..1 brightness of the travelling light at that point. Coordinates
    are relative to the core centre.
    """
    ct, st = math.cos(_RING_TILT_RAD), math.sin(_RING_TILT_RAD)
    rx, ry = r * _RING_RX, r * _RING_RY
    out: list[tuple[float, float, float, float]] = []
    for k in range(n):
        a = 2.0 * math.pi * k / n
        ex, ey = math.cos(a) * rx, math.sin(a) * ry
        # Light position runs around the ring; cosine falloff either side.
        glint = 0.5 + 0.5 * math.cos(a - t * _RING_GLINT_RAD_S)
        out.append((ex * ct - ey * st, ex * st + ey * ct, math.sin(a), glint**2))
    return out


def core_highlight_offset(t: float, r: float) -> tuple[float, float]:
    """Specular highlight position on the sphere — drifts slowly sideways so
    the core reads as a turning ball instead of a flat disc."""
    return (math.sin(t * _HILITE_SWING_RAD_S) * 0.32 * r, -0.30 * r)


def _spark_margin(ph: float) -> float:
    """Clearance a spark needs from the pill edge (its core + glow halo)."""
    return max(2.5, ph * _SPARK_R_FRAC * 2.2 + 1.0)


def orbit_point(
    t: float, spec: OrbitSpec, pw: float, ph: float
) -> tuple[float, float, float]:
    """One spark's ``(dx, dy, depth)`` relative to the pill centre.

    ``depth`` runs -1..+1 over the revolution: negative = far half (drawn
    behind the core, smaller/dimmer), positive = near half. The tilted
    ellipse is uniformly scaled down so the spark INCLUDING its glow stays
    inside every pill size the ease-in passes through.
    """
    a = 2.0 * math.pi * (t / spec.period_s) + spec.phase
    rx = spec.rx_frac * pw
    ry = spec.ry_frac * ph
    ct, st = math.cos(spec.tilt_rad), math.sin(spec.tilt_rad)
    # Extremes of the rotated ellipse, then one shared scale factor so the
    # orbit shape is preserved while honouring both axis budgets.
    max_x = math.hypot(rx * ct, ry * st)
    max_y = math.hypot(rx * st, ry * ct)
    # Reserve room for the reactor's float so orbit + drift can never poke
    # outside the pill (the orbits ride on the drifting core).
    m = _spark_margin(ph)
    bx = max(1.0, pw / 2.0 - m - pw * DRIFT_AX_FRAC)
    by = max(1.0, ph / 2.0 - m - ph * DRIFT_AY_FRAC)
    s = min(1.0, bx / max_x if max_x > 0 else 1.0, by / max_y if max_y > 0 else 1.0)
    ex = math.cos(a) * rx * s
    ey = math.sin(a) * ry * s
    return (ex * ct - ey * st, ex * st + ey * ct, math.sin(a))


def orbit_trail(
    t: float,
    spec: OrbitSpec,
    pw: float,
    ph: float,
    n: int = TRAIL_N,
    span_s: float = TRAIL_SPAN_S,
) -> list[tuple[float, float, float]]:
    """Comet-tail positions for one spark — head (current position) first."""
    dt = span_s / n
    return [orbit_point(t - k * dt, spec, pw, ph) for k in range(n + 1)]


@dataclass
class _RenderState:
    # default_factory (not a plain default): the collapsed size must be read
    # at INSTANTIATION time, after apply_display_scale() may have rescaled
    # the module geometry — a plain default would freeze the import-time value.
    display_level: float = 0.0
    # live pill width/height, eased toward the target
    pw: float = field(default_factory=lambda: float(COLLAPSED_W))
    ph: float = field(default_factory=lambda: float(COLLAPSED_H))


class JarvisBarRenderer:
    def __init__(self, accent: str = "#e7c46e") -> None:
        self._accent = _hex_to_rgb(accent)
        self._st = _RenderState()

    def render(
        self,
        t: float,
        mode: str,
        ext_level: float,
        hovered: bool = False,
        muted: bool = False,
    ) -> Image.Image:
        active = mode in ("listen", "speak")
        # Ease the pill toward its target size: ACTIVE (2x) while a session is
        # live, OPEN on hover (controls) OR while muted (keep the mute cue +
        # unmute target visible), COLLAPSED at rest.
        tw, th = target_pill_size(mode, hovered, muted)
        # Snappy grow/shrink: 0.5 reaches the target in ~4 frames (~70 ms) so the
        # bar pops to full size almost immediately on "Hey Jarvis" instead of
        # crawling there over a third of a second.
        self._st.pw = ease(self._st.pw, tw, 0.5)
        self._st.ph = ease(self._st.ph, th, 0.5)
        # Asymmetric level easing: rise almost instantly so the bars move in
        # sync with the voice, fall fast and snap to dead zero — a lingering
        # sub-visible tail otherwise keeps the equalizer wiggling in silence.
        level_target = ext_level if active else 0.0
        rising = level_target > self._st.display_level
        self._st.display_level = ease(
            self._st.display_level, level_target, 0.6 if rising else 0.5
        )
        if not rising and level_target <= 0.0 and self._st.display_level < 0.02:
            self._st.display_level = 0.0
        pw, ph = self._st.pw, self._st.ph

        frame = np.empty((WIN_H, WIN_W, 3), dtype=np.uint8)
        frame[:, :] = COLOR_KEY_RGB
        img = Image.fromarray(frame)  # uint8 (H,W,3) → mode "RGB"
        d = ImageDraw.Draw(img)

        cx = WIN_W / 2.0
        cy = pill_center_y(ph)  # bottom-anchored: grows upward, idle stays put
        # The rim turns red whenever the mic is muted FOR JARVIS — drawn on
        # EVERY frame (even idle/standby, no hover) so the muted cue is visible
        # at a glance without having to reveal the controls.
        outline_color = MUTED_RED if muted else PILL_BORDER
        d.rounded_rectangle(
            [cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2],
            radius=ph / 2,
            fill=PILL_BG,
            outline=outline_color,
            width=2,
        )

        # Hover splits the bar into controls: LEFT X (hang up, only while a
        # session is live) + RIGHT mic (toggle voice mute for Jarvis).
        x_right = cx + 0.33 * pw  # pulled in so the mic glyph never clips the rim
        if hovered:
            x_left = cx - 0.42 * pw
            active_sess = mode in ("listen", "speak", "think")
            # Keep the speech indicator VISIBLE while interacting — narrow bars
            # in the centre so you can see the voice is live, controls flanking.
            if mode in ("listen", "speak"):
                self._draw_bars(d, t, cx, cy, pw, ph, span=bars_span_for(pw) * 0.5, n=5)
            if active_sess:
                self._draw_close_x(d, x_left, cy, ph)
            self._draw_mic(img, x_right, cy, ph, muted)
        elif mode == "think":
            self._draw_thinking(img, t, cx, cy, pw, ph)
        elif mode in ("listen", "speak"):
            self._draw_bars(d, t, cx, cy, pw, ph)
        elif muted:
            # Muted standby (idle, not hovered): always show the slashed mic so
            # the user sees at a glance they're muted AND where to click to
            # unmute (they can't unmute by voice — Jarvis is deaf while muted).
            self._draw_mic(img, x_right, cy, ph, muted=True)
        # idle / standby (not hovered, not muted): a clean EMPTY pill — no dots,
        # no bars. "When nothing is happening, nothing is in the bar."
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
        w = max(2, _STROKE_W)
        d.line([(cx - r, cy - r), (cx + r, cy + r)], fill=CLOSE_X, width=w)
        d.line([(cx - r, cy + r), (cx + r, cy - r)], fill=CLOSE_X, width=w)

    def _draw_mic(
        self, img: Image.Image, cx: float, cy: float, ph: float, muted: bool
    ) -> None:
        """Right-hand control: the voice-mute toggle, drawn as a clean OUTLINE
        microphone (capsule head + cradle bow + stand), in the spirit of the
        reference glyph — line art, no enclosing box. Replaced the dictation
        square (maintainer request 2026-06-28).

        Supersampled (4x → LANCZOS) like the standby dots, because the thin
        curves alias badly drawn directly at ~30 px. Gold while live; red with
        a diagonal slash when muted (mirrors the red pill rim — the universal
        "mic off" mark).
        """
        ss = 4
        layer = Image.new("RGBA", (img.width * ss, img.height * ss), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        color = (*(MUTED_RED if muted else self._accent), 255)
        w = max(1, round(_STROKE_W * 0.85 * ss))

        x = cx * ss
        y = cy * ss
        p = ph * ss

        # Capsule head (outline rounded rect), sitting in the upper half.
        hw = p * 0.115
        head_top = y - p * 0.34
        head_bot = y + p * 0.02
        ld.rounded_rectangle(
            [x - hw, head_top, x + hw, head_bot], radius=hw, outline=color, width=w
        )
        # Cradle bow: a U-arc cupping the capsule from below (wider than it).
        bw = p * 0.21
        ld.arc(
            [x - bw, y - p * 0.16, x + bw, y + p * 0.20],
            start=15, end=165, fill=color, width=w,
        )
        # Stand: short stem from the bow down to a small foot.
        stem_bot = y + p * 0.32
        ld.line([(x, y + p * 0.18), (x, stem_bot)], fill=color, width=w)
        foot_hw = p * 0.12
        ld.line(
            [(x - foot_hw, stem_bot), (x + foot_hw, stem_bot)], fill=color, width=w
        )
        # Muted: a diagonal slash across the whole glyph ("mic off"). Kept short
        # enough that its top-right tip stays inside the rounded pill (else the
        # color-key shows through as a pink fleck at the rim).
        if muted:
            s = p * 0.28
            ld.line([(x - s, y + s), (x + s, y - s)], fill=color, width=w)

        small = layer.resize(img.size, Image.Resampling.LANCZOS)
        img.paste(small, (0, 0), small)

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

    def _draw_thinking(
        self,
        img: Image.Image,
        t: float,
        cx: float,
        cy: float,
        pw: float,
        ph: float,
    ) -> None:
        """Render the orbital core (THINKING) onto the frame.

        Drawn at 3x on an RGBA layer and LANCZOS-downscaled (same trick as
        the standby dots) — at 34 px pill height, direct drawing aliases
        badly. ImageDraw on RGBA REPLACES pixels rather than compositing, so
        everything is painted strictly back-to-front: trails, far sparks,
        core (glow → body → highlight), near sparks.
        """
        ss = 3
        layer = Image.new("RGBA", (img.width * ss, img.height * ss), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        accent = self._accent
        bright = _lerp_rgb(accent, (255, 255, 255), 0.40)
        spark_r = max(1.1, ph * _SPARK_R_FRAC)

        # The whole reactor floats: core, ring, highlight AND the spark
        # orbits all ride on this drifting centre.
        ddx, ddy = core_drift(t, pw, ph)
        ccx, ccy = cx + ddx, cy + ddy

        def dot(dx: float, dy: float, r: float, color: tuple[int, int, int], alpha: int) -> None:
            x, y = (ccx + dx) * ss, (ccy + dy) * ss
            rr = r * ss
            ld.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(*color, alpha))

        # 1. Comet trails — fade and thin toward the past.
        trails = [orbit_trail(t, spec, pw, ph) for spec in ORBITS]
        for trail in trails:
            for k in range(len(trail) - 1):
                u = k / (len(trail) - 1)  # 0 at the head → 1 at the tail tip
                alpha = int(150 * (1.0 - u) ** 1.3)
                if alpha <= 4:
                    continue
                x0, y0, _ = trail[k]
                x1, y1, _ = trail[k + 1]
                w = max(1, round(spark_r * ss * (1.0 - 0.65 * u)))
                ld.line(
                    [((ccx + x0) * ss, (ccy + y0) * ss), ((ccx + x1) * ss, (ccy + y1) * ss)],
                    fill=(*accent, alpha),
                    width=w,
                )

        def spark(dx: float, dy: float, depth: float) -> None:
            r = spark_r * (1.0 + _SPARK_DEPTH_SIZE * depth)
            dot(dx, dy, r * 1.6, accent, max(0, int(45 + 25 * depth)))
            dot(dx, dy, r, bright, min(255, int(195 + 60 * depth)))

        heads = [trail[0] for trail in trails]

        # 2. Far-half sparks pass BEHIND the core.
        for dx, dy, depth in heads:
            if depth < 0:
                spark(dx, dy, depth)

        # 3. The breathing core "reactor": pulsing halo → back ring arc →
        #    sphere body → drifting specular highlight → front ring arc.
        #    The saturn ring with its travelling glint is what keeps the
        #    centre alive — a bare static dot read as dead.
        r = core_radius(t, ph)
        breath = math.sin(t * _CORE_BREATH_RAD_S)  # in step with the radius
        ring = core_ring_points(t, r)

        def ring_arc(front: bool) -> None:
            w = max(1, round(r * 0.22 * ss))
            for k in range(len(ring)):
                x0, y0, d0, g0 = ring[k]
                x1, y1, d1, g1 = ring[(k + 1) % len(ring)]
                mid_depth = (d0 + d1) / 2.0
                if (mid_depth >= 0) != front:
                    continue
                g = (g0 + g1) / 2.0
                alpha = int(80 + 160 * g)
                color = _lerp_rgb(accent, bright, g)
                ld.line(
                    [((ccx + x0) * ss, (ccy + y0) * ss), ((ccx + x1) * ss, (ccy + y1) * ss)],
                    fill=(*color, min(255, alpha)),
                    width=w,
                )

        dot(0, 0, r * 2.3, accent, int(34 + 16 * breath))  # halo breathes visibly
        dot(0, 0, r * 1.5, accent, 70)  # kept soft so the ring doesn't drown
        ring_arc(front=False)  # the half passing behind the sphere
        dot(0, 0, r, accent, 255)
        hx, hy = core_highlight_offset(t, r)
        dot(hx, hy, r * 0.40, _lerp_rgb(accent, (255, 255, 255), 0.65), 235)
        ring_arc(front=True)  # the half passing in front

        # 4. Near-half sparks pass IN FRONT of the core.
        for dx, dy, depth in heads:
            if depth >= 0:
                spark(dx, dy, depth)

        small = layer.resize(img.size, Image.Resampling.LANCZOS)
        img.paste(small, (0, 0), small)
