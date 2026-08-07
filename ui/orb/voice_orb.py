"""Procedural "voice orb" renderer for the free-floating desktop overlay.

This is the desktop twin of the in-app orb (``components/agentic/VoiceOrb.tsx``):
a luminous, cloud-filled sphere made of softly evolving procedural weather
rather than a glossy gradient ball. A low-resolution fractal field is colour
mapped through the product's ivory / gold / amber palette and enlarged with
interpolation, which is what produces organic depth without visible bands,
outlines, rotating particles or image assets. Voice states change pace,
breathing, turbulence and highlight energy; the colour identity stays stable.

Why a port instead of hosting the web canvas: the browser orb can only ever
live inside the app window. A user who wants the orb ON the desktop — dragged
to whichever monitor they like, above every other window — needs the same
frameless, always-on-top surface the mascot and the Jarvis Bar already use, and
that surface is fed by ``render(t, mode, ext_level) -> PIL.Image``. So the field
is reproduced here in numpy against the SAME constants as the TypeScript
version; the two are meant to look alike, and the guard test pins the palette
and the per-mode motion table rather than a pixel hash.

Two properties are load-bearing for the overlay, not cosmetic:

* **Hard circular edge.** The Windows surface keys out one exact colour
  (magenta) to make a pixel transparent. Any pixel that is a *blend* of orb and
  key colour survives as a pink fringe, so the silhouette is a binary mask — no
  alpha feathering outward. macOS derives its alpha from the same exact-match
  key (``overlay.key_to_alpha``), so the rule holds there too.
* **Capped field cadence.** The overlay's frame loop runs at ~60 fps; the
  procedural field is recomputed at 20 fps (the web orb's rate) and re-composed
  cheaply in between. Breathing still moves every frame, because that is a
  geometry change, not a field change.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

# --- Palette (identical to the in-app orb) ---------------------------------
IVORY = (255.0, 250.0, 235.0)
PALE_GOLD = (248.0, 226.0, 151.0)
SIGNAL_GOLD = (231.0, 196.0, 110.0)
AMBER = (210.0, 147.0, 24.0)
DEEP_AMBER = (126.0, 66.0, 2.0)
CLOUD_LIGHT = (255.0, 249.0, 216.0)

#: Field resolution. 48² samples is what keeps the whole renderer affordable
#: next to speech; the sphere gets its softness from the upscale, not from
#: resolution.
TEXTURE_SIZE = 48
#: Side length of the tiled pseudo-random grid the value noise samples.
NOISE_SIZE = 64
#: Recompute cadence of the procedural field (the web orb's 20 fps).
FIELD_INTERVAL_S = 1.0 / 20.0


class Motion:
    """How one coarse mode moves. Mirrors the ``Motion`` record of the web orb."""

    __slots__ = (
        "flow_x",
        "flow_y",
        "breath_amp",
        "breath_hz",
        "turbulence",
        "energy",
        "voice_impact",
        "color_churn",
    )

    def __init__(
        self,
        flow_x: float,
        flow_y: float,
        breath_amp: float,
        breath_hz: float,
        turbulence: float,
        energy: float,
        voice_impact: float,
        color_churn: float = 0.28,
    ) -> None:
        self.flow_x = flow_x
        self.flow_y = flow_y
        self.breath_amp = breath_amp
        self.breath_hz = breath_hz
        self.turbulence = turbulence
        self.energy = energy
        self.voice_impact = voice_impact
        #: How far the cloud field is allowed to drag the COLOUR ramp out of
        #: its vertical order. At rest it only softens the horizon; while
        #: thinking it is what makes the ivory, gold and amber visibly churn
        #: through each other instead of sitting in bands.
        self.color_churn = color_churn

    def copy(self) -> Motion:
        return Motion(
            self.flow_x,
            self.flow_y,
            self.breath_amp,
            self.breath_hz,
            self.turbulence,
            self.energy,
            self.voice_impact,
            self.color_churn,
        )


#: Per-mode motion, keyed by the surface's coarse modes
#: (``jarvis.ui.jarvisbar.modes.MODES``) rather than the web orb's voice states.
#:
#: * ``dictate`` reuses the listening weather: dictation feeds the very same live
#:   microphone level, so the orb reacts to the user's voice exactly as it does
#:   in a conversation.
#: * ``dictate_transcribing`` is the thinking weather: the key is released, the
#:   microphone feed has stopped, and a stale level must not keep the orb
#:   shimmering as though it were still hearing something.
#: * ``notice`` — something the user asked for did not happen. Quiet and dim,
#:   clearly awake but clearly not listening.
MOTIONS: dict[str, Motion] = {
    "idle": Motion(0.025, 0.008, 0.004, 0.14, 0.8, 0.86, 0.0, 0.28),
    "listen": Motion(0.11, 0.035, 0.016, 0.72, 1.02, 0.98, 0.0, 0.34),
    # Thinking is the one state with nothing to hear, so it has to be the most
    # visibly BUSY: the field races, and the colour ramp churns hard enough
    # that ivory, gold and amber run through each other (maintainer, 2026-08-06
    # — "the colours mix and that is how it shows it is thinking").
    "think": Motion(0.95, -0.44, 0.010, 0.34, 1.85, 1.08, 0.0, 1.45),
    "speak": Motion(0.34, 0.085, 0.014, 0.82, 1.06, 1.0, 0.11, 0.45),
    "dictate": Motion(0.11, 0.035, 0.016, 0.72, 1.02, 0.98, 0.0, 0.34),
    "dictate_transcribing": Motion(0.95, -0.44, 0.010, 0.34, 1.85, 1.08, 0.0, 1.45),
    "notice": Motion(0.012, 0.0, 0.003, 0.12, 0.7, 0.72, 0.0, 0.28),
}

#: Modes that react to a live level. ``speak`` is here because the bridge
#: forwards the REAL TTS loudness through ``set_level`` while Jarvis talks
#: (``OrbBridge._note_tts_level``); using it means the orb swells on the actual
#: voice instead of a synthetic cadence that only looks like speech. Every
#: other mode ignores ``ext_level`` outright, so a stale sample cannot animate
#: the orb while nothing is being heard.
_LEVEL_REACTIVE = frozenset({"listen", "dictate", "speak"})

#: Floor under the dictation level: a quiet moment while recording must still
#: read as "I am listening", never as a dead orb.
_DICTATE_LEVEL_FLOOR = 0.18

#: Resting size as a fraction of the window. Well below 1.0 ON PURPOSE, for two
#: reasons: the swell has to have somewhere to go (at 1.0 every beat hit the
#: ``min(1.0, ...)`` ceiling and the bump was invisible), and the aura needs the
#: margin outside the sphere to live in.
_BASE_SCALE = 0.75

#: How much of the remaining headroom a full-volume moment claims.
_LEVEL_SWELL = 0.10

# --- Aura -------------------------------------------------------------------
# The energy the sphere throws off: a warm corona that widens with loudness,
# plus waves that leave it and fade outward. What the in-app orb does with two
# CSS rings and an opacity animation — but the desktop window keys ONE exact
# colour out to become transparent, so a partly-transparent pixel is impossible.
#
# So the falloff is carried by DENSITY, not by alpha: each pixel is tested
# against a fine ordered-dither pattern, and the fraction that survives is the
# intensity. Half-strength paints half the pixels; from a step back the eye
# integrates that into a soft glow. A solid band with only its colour dimming
# was tried first and read as a machined brass ring around the sphere — the
# eye needs the AIR between the pixels to see gas rather than metal.

#: Widest the corona reaches, as a fraction of the half-window.
_AURA_REACH = 0.99
#: How quickly the corona thins with distance. Higher = tighter to the sphere.
_AURA_FALLOFF = 4.2
#: Corona density right at the sphere's edge, at full energy.
_AURA_MAX = 1.05
#: A wave leaves the sphere this often (seconds) and travels for this long.
_WAVE_PERIOD_S = 1.35
_WAVE_TRAVEL_S = 1.05
#: Wave ring thickness, as a fraction of the half-window.
_WAVE_WIDTH = 0.055
#: Ordered-dither cell. 8x8 is fine enough that the pattern reads as texture
#: rather than as a grid, and cheap enough to tile across a 216² window.
_DITHER_N = 8


def _mix(a, b, amount):
    return a + (b - a) * amount


def _clamp(value, lo=0.0, hi=1.0):
    return np.clip(value, lo, hi)


def _smoothstep(edge0: float, edge1: float, value):
    position = np.clip((value - edge0) / (edge1 - edge0), 0.0, 1.0)
    return position * position * (3.0 - 2.0 * position)


def _smoothstep_scalar(edge0: float, edge1: float, value: float) -> float:
    position = min(1.0, max(0.0, (value - edge0) / (edge1 - edge0)))
    return position * position * (3.0 - 2.0 * position)


def speech_envelope(elapsed: float) -> float:
    """Irregular syllable-like movement without pretending it is measured audio."""
    cadence = 0.5 + 0.5 * math.sin(elapsed * math.pi * 5.4 + math.sin(elapsed * 1.7) * 0.8)
    articulation = 0.5 + 0.5 * math.sin(elapsed * math.pi * 9.2 + 1.3)
    return _smoothstep_scalar(0.38, 0.86, cadence * 0.74 + articulation * 0.26)


def speaking_onset(age: float) -> float:
    """One soft outward beat when assistant speech begins."""
    if age < 0 or age >= 0.24:
        return 0.0
    return math.sin((age / 0.24) * math.pi) * math.exp(-age * 2.4)


def _dither_tile(size: int) -> np.ndarray:
    """The aura's threshold field (values 0..1) — its stand-in for alpha.

    A pixel is painted when its strength beats the threshold under it, so
    strength becomes DENSITY. The field is FIXED, never regenerated per frame:
    a field that changed every frame would make the corona boil.

    Ordered dither alone (a plain Bayer matrix) laid visible diagonals across
    the glow — the eye finds the grid instantly. Mixing it with a fixed random
    field breaks the lattice while keeping the even coverage a pure random
    field lacks, and the result reads as embers instead of as a screen door.
    """
    base = np.array(
        [
            [0, 32, 8, 40, 2, 34, 10, 42],
            [48, 16, 56, 24, 50, 18, 58, 26],
            [12, 44, 4, 36, 14, 46, 6, 38],
            [60, 28, 52, 20, 62, 30, 54, 22],
            [3, 35, 11, 43, 1, 33, 9, 41],
            [51, 19, 59, 27, 49, 17, 57, 25],
            [15, 47, 7, 39, 13, 45, 5, 37],
            [63, 31, 55, 23, 61, 29, 53, 21],
        ],
        dtype=np.float32,
    )
    normalized = (base + 0.5) / 64.0
    repeats = int(np.ceil(size / _DITHER_N))
    ordered = np.tile(normalized, (repeats, repeats))[:size, :size]
    scatter = np.random.default_rng(0x0A11A).random((size, size)).astype(np.float32)
    return np.clip(ordered * 0.45 + scatter * 0.55, 0.0, 1.0)


def _build_noise_grid() -> np.ndarray:
    """A stable tile of pseudo-random values (same LCG stream as the web orb)."""
    grid = np.empty(NOISE_SIZE * NOISE_SIZE, dtype=np.float64)
    seed = np.uint32(0x51F15E)
    mul = np.uint32(1_664_525)
    add = np.uint32(1_013_904_223)
    with np.errstate(over="ignore"):
        for index in range(grid.size):
            seed = np.uint32(seed * mul + add)
            grid[index] = float(seed) / float(0xFFFFFFFF)
    return grid.reshape(NOISE_SIZE, NOISE_SIZE)


_NOISE_GRID = _build_noise_grid()


def _grid_value(ix: np.ndarray, iy: np.ndarray) -> np.ndarray:
    return _NOISE_GRID[iy & (NOISE_SIZE - 1), ix & (NOISE_SIZE - 1)]


def _noise_at(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    left = np.floor(x)
    top = np.floor(y)
    tx = x - left
    ty = y - top
    sx = tx * tx * (3.0 - 2.0 * tx)
    sy = ty * ty * (3.0 - 2.0 * ty)
    li = left.astype(np.int64)
    ti = top.astype(np.int64)
    upper = _mix(_grid_value(li, ti), _grid_value(li + 1, ti), sx)
    lower = _mix(_grid_value(li, ti + 1), _grid_value(li + 1, ti + 1), sx)
    return _mix(upper, lower, sy)


def _fractal_noise(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    value = np.zeros_like(x)
    amplitude = 0.54
    normalizer = 0.0
    for _ in range(3):
        value += _noise_at(x, y) * amplitude
        normalizer += amplitude
        x = x * 2.03 + 11.7
        y = y * 2.01 - 7.9
        amplitude *= 0.5
    return value / normalizer


class VoiceOrbRenderer:
    """Renders the procedural voice orb as a colour-keyed RGB frame.

    Implements the overlay's render seam — ``render(t, mode, ext_level) ->
    Image`` — so it drops into the same Tk surface the mascot uses and inherits
    its window: frameless, always on top, draggable to any monitor.
    """

    def __init__(
        self,
        *,
        size: int = 108,
        color_key: tuple[int, int, int] = (255, 0, 255),
    ) -> None:
        self._size = int(size)
        self._color_key = (int(color_key[0]), int(color_key[1]), int(color_key[2]))

        axis = (np.arange(TEXTURE_SIZE, dtype=np.float64) / (TEXTURE_SIZE - 1)) * 2.0 - 1.0
        self._ny, self._nx = np.meshgrid(axis, axis, indexing="ij")
        self._radius_field = np.sqrt(self._nx * self._nx + self._ny * self._ny)
        # Deterministic sub-LSB dither prevents broad 8-bit colour contours.
        xs, ys = np.meshgrid(np.arange(TEXTURE_SIZE), np.arange(TEXTURE_SIZE), indexing="xy")
        self._dither = ((((xs * 73 + ys * 151) & 255) / 255.0) - 0.5) * 0.8

        self._live = MOTIONS["idle"].copy()
        self._active_mode = "idle"
        self._last_t: float | None = None
        self._phase_x = 0.0
        self._phase_y = 0.0
        self._breath_phase = 0.0
        self._activity_elapsed = 0.0
        self._speaking_age = math.inf
        self._live_impact = 0.0
        self._live_input = 0.0
        self._field_due_t = -math.inf
        self._texture: Image.Image | None = None
        self._masks: dict[int, Image.Image] = {}

        # Distance of every window pixel from the centre, in half-window units
        # (1.0 = the window edge). Built once; the aura is a handful of numpy
        # comparisons against it per frame.
        half = max(1.0, self._size / 2.0)
        span = (np.arange(self._size, dtype=np.float32) + 0.5) - half
        yy, xx = np.meshgrid(span, span, indexing="ij")
        self._pixel_radius = np.sqrt(xx * xx + yy * yy) / half
        # Angle of every pixel, so the corona can breathe unevenly instead of
        # sitting around the sphere as a perfect ring.
        self._pixel_angle = np.arctan2(yy, xx).astype(np.float32)
        self._dither_field = _dither_tile(self._size)
        self._aura_elapsed = 0.0

    # -- public seam ---------------------------------------------------

    def render(self, t: float, mode: str, ext_level: float | None) -> Image.Image:
        motion = MOTIONS.get(mode) or MOTIONS["idle"]
        dt = 0.0 if self._last_t is None else min(0.1, max(0.0, t - self._last_t))
        self._last_t = t

        if mode != self._active_mode:
            self._active_mode = mode
            self._speaking_age = 0.0 if mode == "speak" else math.inf

        ease = 1.0 - math.exp(-dt * 3.2)
        live = self._live
        live.flow_x = _mix(live.flow_x, motion.flow_x, ease)
        live.flow_y = _mix(live.flow_y, motion.flow_y, ease)
        live.breath_amp = _mix(live.breath_amp, motion.breath_amp, ease)
        live.breath_hz = _mix(live.breath_hz, motion.breath_hz, ease)
        live.turbulence = _mix(live.turbulence, motion.turbulence, ease)
        live.energy = _mix(live.energy, motion.energy, ease)
        live.voice_impact = _mix(live.voice_impact, motion.voice_impact, ease)
        live.color_churn = _mix(live.color_churn, motion.color_churn, ease)

        self._phase_x += dt * live.flow_x
        self._phase_y += dt * live.flow_y
        self._breath_phase += dt * live.breath_hz * math.pi * 2.0
        self._activity_elapsed += dt
        if math.isfinite(self._speaking_age):
            self._speaking_age += dt

        # Speech choreography. When the host forwards a REAL output level the
        # sphere follows that; the synthetic cadence is only the fallback for a
        # host that cannot measure its own playback, so the orb never stands
        # still while Jarvis is audibly talking.
        input_target = self._input_level(mode, ext_level)
        has_live_voice = mode == "speak" and ext_level is not None
        speech = (
            0.0 if (mode != "speak" or has_live_voice) else speech_envelope(self._activity_elapsed)
        )
        onset = speaking_onset(self._speaking_age) if mode == "speak" else 0.0
        impact_target = live.voice_impact * speech + 0.032 * onset
        impact_ease = 1.0 - math.exp(-dt * (22.0 if impact_target > self._live_impact else 8.0))
        self._live_impact = _mix(self._live_impact, impact_target, impact_ease)

        # A quick attack makes consonants feel immediate; the softer release
        # prevents a nervous flicker between syllables.
        input_ease = 1.0 - math.exp(-dt * (24.0 if input_target > self._live_input else 9.0))
        self._live_input = _mix(self._live_input, input_target, input_ease)
        visual_impact = self._live_impact + self._live_input * _LEVEL_SWELL

        if t >= self._field_due_t or self._texture is None:
            self._texture = self._paint_weather(visual_impact)
            self._field_due_t = t + FIELD_INTERVAL_S

        breath = (
            _BASE_SCALE
            - live.breath_amp * 0.65
            + live.breath_amp * 0.52 * math.sin(self._breath_phase)
            + live.breath_amp * 0.13 * math.sin(self._breath_phase * 2.05 + 1.4)
            + visual_impact
        )
        half = self._size / 2.0
        radius = (half - 1.0) * min(1.0, breath)

        # How much energy the orb is throwing off right now (0..1). The live
        # level dominates when there is one — that is the honest signal — and
        # thinking keeps a steady work pulse of its own, because it has no
        # sound to ride and still has to look busy.
        self._aura_elapsed += dt
        energy = self._live_input
        if mode in ("think", "dictate_transcribing"):
            energy = max(energy, 0.42 + 0.30 * math.sin(self._aura_elapsed * 3.1))
        elif mode == "speak":
            energy = max(energy, self._live_impact * 6.0)
        return self._compose(self._texture, radius, min(1.0, max(0.0, energy)))

    # Compatibility no-ops: the overlay drives mascot-only expressions through
    # these. Answering them here (instead of leaving them to fail) keeps the
    # shared surface free of per-renderer special cases.
    def start_mouth_anim(self, duration_s: float, t_now: float) -> None:
        """The orb has no mouth — speaking is expressed by the field itself."""

    def stop_mouth_anim(self) -> None:
        """Counterpart to :meth:`start_mouth_anim`; nothing to stop."""

    # -- internals -----------------------------------------------------

    @staticmethod
    def _input_level(mode: str, ext_level: float | None) -> float:
        """The live level this mode is allowed to react to (0..1)."""
        if mode not in _LEVEL_REACTIVE:
            return 0.0
        level = 0.0 if ext_level is None else min(1.0, max(0.0, float(ext_level)))
        if mode == "dictate":
            return max(level, _DICTATE_LEVEL_FLOOR)
        return level

    def _paint_weather(self, impact: float) -> Image.Image:
        motion = self._live
        # Speech briefly compresses the material while the silhouette expands.
        sample_x = self._nx * (1.0 + impact * 0.055)
        sample_y = self._ny * (1.0 - impact * 0.035)

        warp_x = _fractal_noise(
            sample_x * 1.3 + self._phase_x + 8.2,
            sample_y * 1.22 + self._phase_y + 3.4,
        )
        warp_y = _fractal_noise(
            sample_x * 1.18 - self._phase_x * 0.55 + 19.7,
            sample_y * 1.35 + self._phase_y * 0.8 + 12.1,
        )
        cloud_field = _fractal_noise(
            sample_x * 1.85 + warp_x * motion.turbulence * 1.35 + self._phase_x,
            sample_y * 1.72 + warp_y * motion.turbulence * 1.2 + self._phase_y,
        )
        detail = _fractal_noise(
            sample_x * 3.15 - self._phase_x * 0.45 + 31.2,
            sample_y * 2.9 + self._phase_y * 0.35,
        )
        weather = cloud_field * 0.76 + detail * 0.24

        # Where a pixel sits on the ivory→amber ramp. Warping it by the cloud
        # field removes the synthetic horizon band at rest; opening the warp up
        # (``color_churn``) is what makes the palette visibly MIX rather than
        # sit in stripes, and the slow drift keeps that mixing in motion so a
        # thinking orb never settles into a still picture.
        churn = motion.color_churn
        drift = math.sin(self._phase_x * 2.4 + self._phase_y * 1.7) * churn * 0.20
        vertical = _clamp((self._ny + 1.0) * 0.5 + (weather - 0.5) * churn + drift)
        red, green, blue = self._palette(vertical)

        shadow = _smoothstep(0.48, 0.66, 1.0 - weather) * _smoothstep(0.28, 0.95, vertical) * 0.18
        red = _mix(red, DEEP_AMBER[0], shadow)
        green = _mix(green, DEEP_AMBER[1], shadow)
        blue = _mix(blue, DEEP_AMBER[2], shadow)

        # Large cream masses form the soft, irregular clouds.
        cloud = _smoothstep(0.46, 0.64, weather) * (1.0 - vertical * 0.32)
        cloud_mix = cloud * 0.78 * motion.energy
        red = _mix(red, CLOUD_LIGHT[0], cloud_mix)
        green = _mix(green, CLOUD_LIGHT[1], cloud_mix)
        blue = _mix(blue, CLOUD_LIGHT[2], cloud_mix)

        # A second, quieter field breaks up any remaining uniform areas.
        shimmer = _smoothstep(0.58, 0.76, warp_x * 0.55 + detail * 0.45)
        shimmer_mix = shimmer * 0.24 * motion.energy
        red = _mix(red, PALE_GOLD[0], shimmer_mix)
        green = _mix(green, PALE_GOLD[1], shimmer_mix)
        blue = _mix(blue, PALE_GOLD[2], shimmer_mix)

        # Restrained spherical shading, with no dark outline or glossy rim.
        edge_shade = _smoothstep(0.7, 1.0, self._radius_field) * 0.1
        volume_light = 1.02 + (1.0 - np.minimum(1.0, self._radius_field)) * 0.05 - edge_shade

        stacked = np.stack(
            (
                red * volume_light + self._dither,
                green * volume_light + self._dither,
                blue * volume_light + self._dither,
            ),
            axis=-1,
        )
        return Image.fromarray(np.clip(np.round(stacked), 0, 255).astype(np.uint8), "RGB")

    @staticmethod
    def _palette(vertical: np.ndarray):
        """Four-stop vertical ramp: ivory → pale gold → signal gold → amber → deep."""
        stops = (
            (0.0, 0.22, IVORY, PALE_GOLD),
            (0.22, 0.5, PALE_GOLD, SIGNAL_GOLD),
            (0.5, 0.76, SIGNAL_GOLD, AMBER),
            (0.76, 1.0, AMBER, DEEP_AMBER),
        )
        red = np.empty_like(vertical)
        green = np.empty_like(vertical)
        blue = np.empty_like(vertical)
        remaining = np.ones_like(vertical, dtype=bool)
        for lo, hi, start, end in stops:
            band = remaining & (vertical < hi) if hi < 1.0 else remaining
            if not band.any():
                continue
            amount = (vertical[band] - lo) / (hi - lo)
            red[band] = _mix(start[0], end[0], amount)
            green[band] = _mix(start[1], end[1], amount)
            blue[band] = _mix(start[2], end[2], amount)
            remaining &= ~band
        return red, green, blue

    def _circle_mask(self, diameter: int) -> Image.Image:
        """Binary disc mask, cached per diameter.

        Binary — never anti-aliased: a half-blended edge pixel would survive the
        colour key as a pink fringe around the orb (see the module docstring).
        """
        mask = self._masks.get(diameter)
        if mask is None:
            axis = np.arange(diameter, dtype=np.float64) - (diameter - 1) / 2.0
            yy, xx = np.meshgrid(axis, axis, indexing="ij")
            inside = (xx * xx + yy * yy) <= ((diameter / 2.0) ** 2)
            mask = Image.fromarray((inside * 255).astype(np.uint8), "L")
            self._masks[diameter] = mask
        return mask

    def _aura_layer(self, radius: float, energy: float) -> np.ndarray | None:
        """The corona + travelling waves, as an RGB array over the key colour.

        Returns ``None`` when there is nothing to draw, so an idle orb costs
        exactly what it did before this existed.

        Every pixel returned is FULLY opaque — the falloff lives in the colour,
        not in an alpha channel the window cannot carry. See the module notes
        on ``_AURA_REACH``.
        """
        if energy <= 0.01:
            return None
        half = max(1.0, self._size / 2.0)
        inner = radius / half
        if inner >= _AURA_REACH:
            return None

        field = self._pixel_radius
        band = (field > inner) & (field <= _AURA_REACH)
        if not band.any():
            return None

        # Corona: densest against the sphere, thinning outward. An exponential
        # falloff, so it reads as gas leaving a surface rather than as a band
        # with an edge.
        distance_out = np.clip((field - inner) / max(1e-6, _AURA_REACH - inner), 0.0, 1.0)
        strength = np.exp(-distance_out * _AURA_FALLOFF) * _AURA_MAX * energy
        # Three slow lobes turning around the sphere: without this the corona is
        # a geometrically perfect annulus, which reads as a drawn ring rather
        # than as something the sphere is giving off.
        lobes = 1.0 + 0.34 * np.sin(self._pixel_angle * 3.0 - self._aura_elapsed * 1.15) * np.sin(
            self._pixel_angle * 2.0 + self._aura_elapsed * 0.7
        )
        strength = strength * lobes

        # Waves: rings that leave the sphere and thin as they travel, which is
        # what the in-app orb animates with two expanding CSS circles.
        phase = (self._aura_elapsed % _WAVE_PERIOD_S) / _WAVE_TRAVEL_S
        for offset in (0.0, 0.5):
            travel = phase - offset
            if not 0.0 <= travel <= 1.0:
                continue
            ring_r = inner + (_AURA_REACH - inner) * travel
            ring = np.clip(1.0 - np.abs(field - ring_r) / _WAVE_WIDTH, 0.0, 1.0)
            strength = np.maximum(strength, ring * (1.0 - travel) * energy * 0.9)

        # Density, not alpha: the dither decides which pixels survive, so a
        # half-strength glow paints half of them and the eye fills in the rest.
        visible = band & (strength > self._dither_field)
        if not visible.any():
            return None

        layer = np.empty((self._size, self._size, 3), dtype=np.uint8)
        layer[..., 0] = self._color_key[0]
        layer[..., 1] = self._color_key[1]
        layer[..., 2] = self._color_key[2]
        # Surviving pixels still carry a little of the falloff in their colour,
        # so the thin outer reaches read as embers rather than as gold confetti.
        amount = np.clip(strength[visible] / max(_AURA_MAX, 1e-6), 0.35, 1.0)
        layer[visible, 0] = (AMBER[0] + (SIGNAL_GOLD[0] - AMBER[0]) * amount).astype(np.uint8)
        layer[visible, 1] = (AMBER[1] + (SIGNAL_GOLD[1] - AMBER[1]) * amount).astype(np.uint8)
        layer[visible, 2] = (AMBER[2] + (SIGNAL_GOLD[2] - AMBER[2]) * amount).astype(np.uint8)
        return layer

    def _compose(self, texture: Image.Image, radius: float, energy: float = 0.0) -> Image.Image:
        diameter = max(2, int(round(radius * 2.0)))
        aura = self._aura_layer(radius, energy)
        if aura is None:
            frame = Image.new("RGB", (self._size, self._size), self._color_key)
        else:
            frame = Image.fromarray(aura, "RGB")
        scaled = texture.resize((diameter, diameter), Image.Resampling.BICUBIC)
        offset = (self._size - diameter) // 2
        frame.paste(scaled, (offset, offset), self._circle_mask(diameter))
        return frame
