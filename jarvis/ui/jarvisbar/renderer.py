"""Canonical Jarvis Bar: the Pet control strip in the native bar hosts.

Geometry and painting come from ui.orb.controls. The platform hosts retain
their lifecycle, saved placement and display scaling without another design.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

from jarvis.ui.jarvisbar.modes import DICTATION_MODES, MODES, NOTICE_MODES  # noqa: F401
from ui.orb import controls

COLOR_KEY_RGB = (255, 0, 255)
REFERENCE_SCREEN_W = 1920
REFERENCE_SCREEN_H = 1080
MIN_DISPLAY_SCALE = 0.55
BASE_DISPLAY_SCALE = 0.85
MAX_DISPLAY_SCALE = 1.6
REFERENCE_RAW_DPI = 154.0
PHYSICAL_DPI_MIN = 60.0
PHYSICAL_DPI_MAX = 350.0
USER_SIZE_MIN = 0.5
USER_SIZE_MAX = 2.0
USER_SIZE_DEFAULT = 1.0
USER_SIZE_SCALE = 1.0
DISPLAY_SCALE = 1.0
LEVEL_STALE_S = 0.35
DROP_STATE_NONE = "none"
DROP_STATE_ARMED = "armed"
DROP_STATE_OK = "ok"
DROP_STATE_REJECTED = "rejected"
DROP_STATES = (DROP_STATE_NONE, DROP_STATE_ARMED, DROP_STATE_OK, DROP_STATE_REJECTED)
DROP_CONFIRM_TOTAL_S = 1.4
NOTICE_ALPHA_MIN = 0.55


def notice_alpha(t: float) -> float:
    return NOTICE_ALPHA_MIN + (1.0 - NOTICE_ALPHA_MIN) * (0.5 + 0.5 * math.sin(t * 3.4))


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


def resolve_screen_scale(screen_w: int, screen_h: int, raw_dpi: float | None = None) -> float:
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


def effective_ext_level(
    ext_level: float, seconds_since_level_rx: float, *, stale_s: float = LEVEL_STALE_S
) -> float:
    """The level the frame loop should render: the live sample while fresh,
    dead zero once the feed has stopped. Pure — shared by the Tk and Qt
    surfaces so both decay identically."""
    return float(ext_level) if seconds_since_level_rx <= stale_s else 0.0


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

    The sweep (the animated "indicator") belongs ONLY to active thinking.
    Three distinct looks:

    - ``idle`` → ``idle`` (the standby pill). Silence here is not "thinking".
    - Real sound — ``playback_active`` (TTS audio on the device right now) OR a
      recent level within ``hold_s`` (your live mic) → the equalizer (``"speak"``
      → bars that move with the sound). ``playback_active`` is the player's
      authoritative signal, needed because the level tap only fires at
      buffer-write time (a brief instant per sentence) while the player then
      blocks for the whole multi-second playback with no further level.
    - Silent + ``coarse_mode == "think"`` (the THINKING state, and the silent
      TTS-synthesis lead-in which the bridge also shows as ``"think"``) → the
      sweep. This is the only place an indicator animates.
    - Silent + any OTHER active state (``"listen"`` — waiting after "Hey Jarvis"
      with no speech) → ``"speak"`` too, but with no level the equalizer renders
      flat and STILL: bars that just stand there, no indicator. "When nothing
      happens, nothing happens."

    ``hold_s`` bridges the short gaps between words/sentences so the bars don't
    flap back on every micro-pause.

    Dictation runs outside the voice state machine entirely and has two modes
    of its own, both resolved BEFORE the audio-activity branches so neither can
    be overruled by a stale level sample:

    - ``dictate`` (the user is speaking into the dictation mic) always renders
      as the equalizer. A SPEAKING dictation must never show the sweep:
      the mic level is being fed, so a silent pause mid-sentence shows still
      bars rather than falling through and pretending to think.
    - ``dictate_transcribing`` (the key was released, the transcription is
      running) renders as the sweep. Here there genuinely IS work in
      flight to represent, and the mic feed has stopped — showing the equalizer
      would claim the bar is still listening when it is not.

    ``notice`` is resolved first and passes through unchanged. It is the one
    look that must survive EVERY other signal: it is raised precisely when
    something did not happen, and a stale level sample or an in-flight playback
    must never be able to repaint it as listening or speaking — that would
    replace the answer to the user's key press with a lie about the microphone.
    """
    if coarse_mode in NOTICE_MODES:
        return coarse_mode
    if coarse_mode == "idle":
        return "idle"
    if coarse_mode == "dictate":
        return "speak"
    if coarse_mode == "dictate_transcribing":
        return "think"
    if playback_active or seconds_since_audible < hold_s:
        return "speak"
    if coarse_mode == "think":
        return "think"
    return "speak"


def apply_display_scale(scale: float, user_size: float | None = None) -> None:
    """Keep the existing size preference and monitor calibration."""
    global DISPLAY_SCALE, USER_SIZE_SCALE, WIN_W, WIN_H
    DISPLAY_SCALE = max(MIN_DISPLAY_SCALE, min(MAX_DISPLAY_SCALE, float(scale)))
    if user_size is not None:
        USER_SIZE_SCALE = clamp_user_size(user_size)
    WIN_W, WIN_H = controls.pet_strip_size(strip_scale())


def strip_scale() -> float:
    return DISPLAY_SCALE * USER_SIZE_SCALE


def target_pill_size(mode: str, **_kwargs) -> tuple[int, int]:
    """The strip keeps all of its controls visible in every mode."""
    return WIN_W, WIN_H


def pill_center_y(_height: float) -> float:
    return WIN_H / 2.0


class JarvisBarRenderer:
    def __init__(self, accent: str = "#e7c46e") -> None:
        # Retained for saved-config compatibility; the authored sphere is blue.
        try:
            value = accent.lstrip("#")
            if len(value) != 6:
                raise ValueError("Expected an RGB color")
            self.accent = tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))
        except (AttributeError, ValueError):  # An invalid optional accent retains the standard visible highlight.
            self.accent = controls.PET_ORB_HIGHLIGHT

    def render(
        self,
        t: float,
        mode: str,
        ext_level: float | None = None,
        *,
        hovered: bool = False,
        muted: bool = False,
        speaker_muted: bool = False,
        hovered_action: str | None = None,
        call_ring: int = 0,
        prompt_mode: bool = False,
        prompt_mode_paused: bool = False,
        drop_state: str = DROP_STATE_NONE,
        drop_elapsed: float = 0.0,
        surface_mode: str | None = None,
    ) -> Image.Image:
        actual = surface_mode or mode
        motion = (
            "think"
            if mode in ("think", "dictate_transcribing")
            else "voice"
            if mode in ("listen", "speak", "dictate")
            else "rest"
        )
        state = controls.PetStripState(
            jarvis_bar=True,
            mic_muted=muted,
            speaker_muted=speaker_muted,
            active=actual in ("listen", "think", "speak"),
            level=controls.quantize_level(ext_level),
            motion=motion,
            phase=controls.indicator_phase(motion, t),
            hovered=hovered_action,
            call_ring=call_ring,
        )
        # Cached frames belong to the shared component; never mutate them.
        frame = controls.render_pet_strip(state, strip_scale()).copy()
        layout = controls.pet_strip_layout(strip_scale())
        _, x0, x1 = next(slot for slot in layout.slots if slot[0] == "orb")
        cx, cy = (x0 + x1) / 2, WIN_H / 2
        r = max(2, (layout.pill[3] - layout.pill[1]) * 0.16)
        draw = ImageDraw.Draw(frame)
        color = controls.PET_ICON
        width = max(1, round(strip_scale() * 2))
        if actual in DICTATION_MODES:
            draw.rounded_rectangle((cx - r, cy - r, cx + r, cy + r), radius=1, fill=color)
        elif prompt_mode:
            color = self.accent
            draw.line(
                [(cx - r, cy), (cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)],
                fill=color,
                width=width,
            )
            if prompt_mode_paused:
                draw.line(
                    (cx - r, cy - r, cx + r, cy + r), fill=controls.PET_ICON_MUTED, width=width
                )
        if (
            drop_state in (DROP_STATE_OK, DROP_STATE_REJECTED)
            and drop_elapsed >= DROP_CONFIRM_TOTAL_S
        ):
            drop_state = DROP_STATE_NONE
        if actual in NOTICE_MODES or drop_state == DROP_STATE_REJECTED:
            red = tuple(round(c * notice_alpha(t)) for c in controls.PET_ICON_MUTED)
            draw.line((cx - r, cy - r, cx + r, cy + r), fill=red, width=width)
            draw.line((cx - r, cy + r, cx + r, cy - r), fill=red, width=width)
        elif drop_state == DROP_STATE_OK:
            draw.line(
                [(cx - r, cy), (cx, cy + r), (cx + r, cy - r)], fill=(126, 200, 133), width=width
            )
        elif drop_state == DROP_STATE_ARMED:
            draw.rounded_rectangle(
                layout.pill, radius=WIN_H / 2, outline=controls.PET_ORB_HIGHLIGHT, width=width
            )
        return frame


apply_display_scale(1.0)
